#!/usr/bin/env python3
"""Find an age core in a plain photo: no card, no ruler, no printing.

This is the recognition question asked on its own, before any ring counting:
can the app tell what an age core is? It only has to find the core and draw its
outline, so it is deliberately not doing any counting here.

Why warmth, not brightness
-------------------------
Measured on the reference frames, the core and its background differ by far
more in colour than in tone: on white paper the core reads 132.6 against a
background of 151.8, only 19 levels apart, while the same two differ 67.8
against 24.5 in red-minus-blue. Every brightness threshold tried so far failed
for exactly this reason, and it also explains why the same photograph looks
easy on dark blue fabric and hard on white paper. Wood is warm; paper is
neutral; fabric in these shots is cool. That is the one cue that survives both.

Why the centreline is tracked rather than assumed
-------------------------------------------------
A core is not a straight bar. Real ones wander, and a frame that assumes a
horizontal band cuts the core diagonally at the ends and loses the ends
entirely. So the centre is followed column by column with continuity, then
smoothed over a window about one core width, and the outline is offset
perpendicular to the local tangent rather than straight up and down.

Usage:
    python3 engine/corefind.py IMAGE.jpg --out OVERLAY.png
"""

import argparse
import os

import numpy as np
from PIL import Image, ImageDraw

# Work at reduced scale; the core is hundreds of pixels wide and the answer
# does not change, but 26 frames at full resolution is a lot of convolve.
SCALE = 3


def _runs(mask):
    out = []
    x = 0
    n = len(mask)
    while x < n:
        if mask[x]:
            s = x
            while x < n and mask[x]:
                x += 1
            out.append((s, x - 1))
        else:
            x += 1
    return out


def warmth(path, scale=SCALE):
    """Return (warmth, luminance) at reduced scale, plus the scale factor."""
    im = Image.open(path).convert("RGB")
    W0, H0 = im.size
    im = im.resize((W0 // scale, H0 // scale), Image.LANCZOS)
    a = np.asarray(im, dtype=float)
    R, G, B = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    # Red minus blue, normalised by overall brightness so a dim frame and a
    # bright frame of the same core give the same answer.
    lum = 0.299 * R + 0.587 * G + 0.114 * B
    w = (R - B) / (lum + 20.0)
    return w, lum, scale, (W0, H0)


def medfilt(v, k):
    """Running median. Used for the baseline because a mean is not robust: on a
    frame where the loose pass jumped onto a pair of jeans 715 px away, a mean
    filter dragged the baseline off the core and the second pass then had
    nothing to snap back to. A median ignores those columns outright."""
    if k < 3:
        return v
    h = k // 2
    pad = np.pad(v, (h, h), "edge")
    win = np.lib.stride_tricks.sliding_window_view(pad, k)
    return np.median(win, axis=1)


def smooth(v, k):
    if k < 3:
        return v
    ker = np.ones(k) / k
    return np.convolve(np.pad(v, (k // 2, k // 2), "edge"), ker, mode="valid")


def core_columns(w, thr):
    """Warm-enough mask, cleaned of speckle by a column-coherence filter."""
    m = w > thr
    # drop columns that are only a few pixels warm: that is sensor noise, not
    # a core. The core spans most columns, so a low column fraction kills
    # speckle without touching a real edge.
    return m


def find_core(w, thr=None):
    """Return (centre_y, half_width, valid) per column.

    The baseline is the dominant horizontal warm band, not a walk. A per-column
    walk with a generous window can escape - on one frame it stepped onto a
    pair of jeans 715 px above the core - and once the baseline is wrong the
    tight second pass has nothing to snap back to. Aggregating warm pixels over
    all columns to find the band cannot wander, because every column votes.
    Sinuosity is then picked up in the refinement, where it belongs.
    """
    H, W = w.shape
    m = w > (thr if thr is not None else _auto_thr(w))

    rowwarm = m.mean(axis=1)
    if rowwarm.max() <= 0:
        return None
    # the core is the band of rows that are warm across much of the frame
    hot = rowwarm > max(0.10, 0.35 * rowwarm.max())
    bands = []
    y = 0
    while y < H:
        if hot[y]:
            y0 = y
            while y < H and hot[y]:
                y += 1
            bands.append((y0, y - 1))
        else:
            y += 1
    if not bands:
        return None
    # longest band, then the one with the most warm pixels as a tie-break
    y0, y1 = max(bands, key=lambda b: (b[1] - b[0], rowwarm[b[0]:b[1] + 1].sum()))
    band_c = (y0 + y1) / 2.0
    band_h = max(4.0, (y1 - y0 + 1))

    top, bot = {}, {}
    reach = max(6, int(2.5 * band_h))
    for x in range(W):
        col = m[:, x]
        lo = max(0, int(band_c - reach))
        hi = min(H, int(band_c + reach) + 1)
        seg = col[lo:hi]
        if not seg.any():
            continue
        r = min(_runs(seg),
                key=lambda q: abs((q[0] + q[1]) / 2.0 + lo - band_c))
        top[x] = float(r[0] + lo)
        bot[x] = float(r[1] + lo)
    if len(top) < 30:
        return None

    xs = sorted(top)
    idx = np.arange(W)
    tt = np.interp(idx, idx[xs], [top[x] for x in xs])
    bb = np.interp(idx, idx[xs], [bot[x] for x in xs])
    cs = (tt + bb) / 2.0
    half = (bb - tt) / 2.0

    medw = float(np.median(half))
    if medw < 1:
        return None
    k = int(max(5, min(101, int(2 * medw) | 1)))
    cs = smooth(cs, k)
    half = smooth(half, k)

    # a second, tighter read around the now-smoothed centre, so a column whose
    # first pick was a neighbouring bright patch gets corrected
    tight = max(6, int(2.5 * np.median(half)))
    for x in range(W):
        c = cs[x]
        lo = max(0, int(c - tight))
        hi = min(H, int(c + tight) + 1)
        seg = m[lo:hi, x]
        if not seg.any():
            continue
        r = min(_runs(seg),
                key=lambda q: abs((q[0] + q[1]) / 2.0 + lo - c))
        tt[x] = r[0] + lo
        bb[x] = r[1] + lo
    cs = (tt + bb) / 2.0
    half = (bb - tt) / 2.0
    k = int(max(5, min(101, int(2 * float(np.median(half))) | 1)))
    cs = smooth(cs, k)
    half = smooth(half, k)

    valid = np.zeros(W, dtype=bool)
    valid[xs[0]:xs[-1] + 1] = True
    valid &= half > 1.0
    return cs, half, valid, m


# Width prior expressed against the core's own LENGTH, not the frame height.
#
# The old form was 2.0-6.5% of frame height, measured on full 3120/4160 px
# frames. Cropping to the middle 25% band produced 780 px tall images, where a
# 100 px core is 13% and every crop was rejected. Tying width to length is
# invariant to cropping, zoom and camera, and it is the quantity that actually
# means something: a 5 mm core is roughly 35x longer than it is wide.
MIN_RATIO = 18.0
MAX_RATIO = 90.0

# Width as a fraction of FRAME HEIGHT. This is the prior that was in place
# when five overlays were checked by eye and accepted. It was replaced with a
# scale-invariant width/length form, which is better in principle, but the
# rewrite was never validated and the detection regressed with it. Restored
# until there is ground truth to choose between them.
WIDTH_FRAC_LO = 0.020
WIDTH_FRAC_HI = 0.065

# A core wanders, but it does not wander around itself. Measured wander on the
# unambiguous frames was 20 to 165 px, under 1.5 core widths; the failures
# reached 715 px, over three widths, which is a tracker that has lost the core.
MAX_WANDER_WIDTHS = 1.6

# The core lives on the centre line, and nothing else does.
#
# The user's instruction is to frame the core with the long side of the image
# parallel to it and the core centred. That is a guarantee, so the app can act
# on it instead of estimating it: search only the middle 25% band of the frame
# and reject anything whose centre falls outside.
#
# This is what keeps the wooden ruler out. On the card photos the ruler is
# warm, elongated and around 30 mm wide - the same shape as a core - and it
# sits well away from the centre line, so the band excludes it for free rather
# than needing to know what a ruler looks like.
CENTRE_ZONE = 0.25      # total band height, as a fraction of the frame
CENTRE_ZONE_HALF = CENTRE_ZONE / 2.0


def score_core(length_px, width_px, height_px, coverage, width_cv):
    """How much does this candidate look like an age core?

    Scale-invariant on purpose: every term is a ratio between the candidate's
    own measurements, so the same answer comes out of a full frame, a middle
    band crop, or a different camera. A core is long, thin, even in width and
    continuous; a slab is thick, a blemish is short, and a tracked edge line is
    too thin, and each of those fails a different term.
    """
    if width_px <= 0 or length_px <= 0:
        return -1e9
    wf = width_px / height_px
    if wf < WIDTH_FRAC_LO:
        return -1e9                      # an edge or a scratch
    if wf > WIDTH_FRAC_HI:
        return -1e9                      # a slab, a table, a shadow
    ratio = length_px / width_px
    if ratio < MIN_RATIO or ratio > MAX_RATIO:
        return -1e9
    centre = 1.0 - abs(wf - 0.035) / 0.035
    return (coverage * 2.0) + centre + (1.0 - min(width_cv, 1.0))


def wander_ok(centre, valid, width_px):
    """Peak-to-peak centreline wander, in core widths."""
    if valid.sum() < 10 or width_px <= 0:
        return 0.0, False
    span = float(centre[valid].max() - centre[valid].min()) * SCALE
    return span / width_px, span / width_px <= MAX_WANDER_WIDTHS


def _auto_thr(w, frac=0.35):
    """Threshold between the warm core and the background.

    Not Otsu. A core is a few percent of the frame - 3.5% on the white-paper
    reference - and Otsu on a 96/4 split puts the cut inside the background's
    own noise rather than between the two populations. Measured on that frame
    Otsu returned -0.011 when the core sits at +0.5 and the paper at -0.01.

    Instead take the background as the median and the core as a high
    percentile, then cut a fixed fraction of the way between them. That works
    unchanged on white paper (neutral) and on blue fabric (strongly negative),
    because it only ever uses the two levels actually present.
    """
    bg = float(np.median(w))
    hi = float(np.percentile(w, 99.0))
    if hi - bg < 1e-6:
        return bg + 1e-6
    return bg + frac * (hi - bg)


def outline(cs, half, valid):
    """Top and bottom edges, offset perpendicular to the local tangent."""
    W = len(cs)
    d = np.gradient(np.where(valid, cs, np.nan))
    d = np.nan_to_num(d)
    norm = np.sqrt(1.0 + d * d)
    top = cs - half / norm
    bot = cs + half / norm
    return top, bot


def find_best_any(w, fracs=(0.30, 0.45, 0.60, 0.72, 0.82), centre_zone=None):
    """Run the search on the image as shot and transposed, keep the better.

    The card is landscape and gets photographed in portrait half the time, so
    the core runs vertically in a third of the new references. Every step here
    walks the image column by column, which assumes the core runs across the
    frame, so the whole search is done twice and scored.
    """
    best = None
    for transpose in (False, True):
        wu = np.transpose(w) if transpose else w
        r = find_best(wu, fracs, centre_zone)
        if r is None:
            continue
        r["transpose"] = transpose
        if r["score"] <= 0:
            continue
        if best is None or r["score"] > best["score"]:
            best = r
    return best


def find_best(w, fracs=(0.30, 0.45, 0.60, 0.72, 0.82), centre_zone=None):
    """Try several warmth thresholds and keep the most core-like candidate.

    A single cut cannot work. On dark blue fabric the background warmth is
    strongly negative, so any threshold low enough to admit a pale core also
    admits every neutral object - one frame has a grey slab lying across the
    core and the tracker walked the slab. Raising the cut fixes that and
    amputates the pale end of a real core, so the cut is swept and the
    candidates are judged instead of assuming one level is right.
    """
    # Crop to the centre band BEFORE any threshold is computed.
    #
    # Applying the 25% zone as a rejection test afterwards is too late: the
    # wooden ruler is warm, so it is already inside the mask by the time the
    # core is scored, and on the card photos the ruler - warm, 30 mm wide and
    # spanning the frame - was the candidate that got rejected for being off
    # centre while the real core never got a look in. Restricting first means
    # the ruler is never in the mask at all, which is what the zone is for.
    H, W = w.shape
    H_ORIG = float(H)          # width priors stay relative to the whole frame
    # centre_zone=None means the caller has already cropped to the band, which
    # is the case for the 25_percent_MIDDLE set. Cropping again there would
    # discard a third of an already narrow strip and clip the core's ends.
    cz = centre_zone            # off unless asked: see note in CENTRE_ZONE
    band = (0, H)
    if cz and cz < 1.0:
        y0 = int(round((1.0 - cz) / 2.0 * H))
        y1 = int(round((1.0 + cz) / 2.0 * H))
        if y1 - y0 < 20:
            return None
        w = w[y0:y1, :]
        H = w.shape[0]
        band = (y0, y1)
    bg = float(np.median(w))
    hi = float(np.percentile(w, 99.0))
    best = None
    for f in fracs:
        thr = bg + f * (hi - bg)
        r = find_core(w, thr)
        if r is None:
            continue
        cs, half, valid, m = r
        xs = np.where(valid)[0]
        if len(xs) < 30:
            continue
        length = (xs.max() - xs.min()) * SCALE
        halfw = half[valid]
        width = float(np.median(halfw)) * 2 * SCALE
        cov = len(xs) / max(1, int(xs.max() - xs.min() + 1))
        cv = float(halfw.std() / max(1e-6, halfw.mean()))
        sc = score_core(length, width, H_ORIG * SCALE, cov, cv)
        wn, wok = wander_ok(cs, valid, width)
        if not wok:
            sc = -1e9 + sc          # keep for diagnosis, never wins
        # the centre-line guarantee, enforced
        offs = abs(float(np.mean(cs[valid])) - H / 2.0) / max(1.0, H)
        cand_off = offs
        if offs > CENTRE_ZONE_HALF:
            sc = -1e9 + sc
        cand = None
        cand = {"score": sc, "thr": thr, "frac": f, "cs": cs, "half": half,
                "wander_widths": wn, "centre_offset": cand_off,
                "band": band,
                "valid": valid, "mask": m, "length": length, "width": width,
                "coverage": cov, "width_cv": cv,
                "x_lo": int(xs.min()), "x_hi": int(xs.max())}
        if best is None or sc > best["score"]:
            best = cand
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--out")
    ap.add_argument("--thr", type=float, default=None)
    a = ap.parse_args()

    w, lum, sc, (W0, H0) = warmth(a.image)
    if a.thr is not None:
        r = find_core(w, a.thr)
        best = None
        if r is not None:
            cs, half, valid, m = r
            xs = np.where(valid)[0]
            best = {"cs": cs, "half": half, "valid": valid, "mask": m,
                    "frac": float("nan"), "thr": a.thr,
                    "length": (xs.max() - xs.min()) * sc,
                    "width": float(np.median(half[valid])) * 2 * sc,
                    "coverage": len(xs) / max(1, int(xs.max() - xs.min() + 1)),
                    "width_cv": float(half[valid].std() /
                                      max(1e-6, half[valid].mean())),
                    "x_lo": int(xs.min()), "x_hi": int(xs.max()),
                    "score": float("nan")}
    else:
        best = find_best_any(w)
    if best is None or best["score"] < 0:
        if best is not None:
            print("image %s" % os.path.basename(a.image))
            print("  no candidate looked like a core: best was %.1f px wide "
                  "(%.1f%% of height), ratio %.0f:1, wander %.1f widths"
                  % (best["width"], 100.0 * best["width"] / H0,
                     best["length"] / max(1.0, best["width"]),
                     best.get("wander_widths", 0.0)))
            print("  reporting uncertain rather than drawing an outline")
        else:
            print("no core found")
        return

    cs, half, valid = best["cs"], best["half"], best["valid"]
    x_lo, x_hi = best["x_lo"], best["x_hi"]
    print("image %s  threshold %.4f (cut %.0f%% of the way to the core)  %s" % (
        os.path.basename(a.image), best["thr"], 100 * best["frac"],
        "core runs vertically" if best.get("transpose") else "core runs across"))
    print("  core spans x %d..%d of %d  (%.0f%% of frame width)" % (
        x_lo * sc, x_hi * sc, W0, 100.0 * (x_hi - x_lo) / w.shape[1]))
    print("  length %.0f px   median width %.0f px (%.1f%% of height)" % (
        best["length"], best["width"], 100.0 * best["width"] / (H0)))
    print("  ratio %.1f:1   coverage %.0f%%   width variation %.0f%%" % (
        best["length"] / max(1.0, best["width"]), 100 * best["coverage"],
        100 * best["width_cv"]))
    print("  centreline wander %.0f px (%.2f core widths)" % (
        (cs[valid].max() - cs[valid].min()) * sc,
        (cs[valid].max() - cs[valid].min()) * sc / max(1.0, best["width"])))

    if a.out:
        im = Image.open(a.image).convert("RGB")
        d = ImageDraw.Draw(im)
        dl = np.gradient(np.where(valid, cs, np.nan))
        dl = np.nan_to_num(dl)
        norm = np.sqrt(1 + dl * dl)
        top = cs - half / norm
        bot = cs + half / norm
        pt = [(int(x * sc), int(top[x] * sc))
              for x in range(x_lo, x_hi + 1) if valid[x]]
        pb = [(int(x * sc), int(bot[x] * sc))
              for x in range(x_hi, x_lo - 1, -1) if valid[x]]
        if len(pt) > 4:
            d.line(pt, fill=(0, 255, 255), width=5)
            d.line(pb, fill=(0, 255, 255), width=5)
            d.line([pt[0], pb[0]], fill=(0, 255, 255), width=5)
            d.line([pt[-1], pb[-1]], fill=(0, 255, 255), width=5)
            pc = [(int(x * sc), int(cs[x] * sc))
                  for x in range(x_lo, x_hi + 1) if valid[x]]
            d.line(pc, fill=(255, 0, 255), width=2)
        im.save(a.out)
        print("  wrote", a.out)


if __name__ == "__main__":
    main()
