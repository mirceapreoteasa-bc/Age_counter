#!/usr/bin/env python3
"""Turn a detected core into a normalised, curvature-gated "sausage" strip.

This implements the agreed workflow, in order:

  1. identify the core                       (engine/corefind.py)
  2. contour and centreline attempt          (engine/corefind.py)
  3. analyse the contour width; where it swells by more than 20%, work out
     which side of the contour is the straighter one - larger radius of
     curvature - because that is the side that is telling the truth
  4. correct the side that has spilled outside the normalised shape
  5. redraw the centreline from the corrected contour
  6. accept only centreline curvatures gentler than a stated radius, and
     redraw the contour from what survives
  7. cut the selected core image at three nested half-widths, 2.2 / 2.0 /
     1.75 mm, giving 4.4 / 4.0 / 3.5 mm bands inside a 5 mm core

Why the half-widths step down rather than being one number: the first attempt
is generous and will occasionally take a shadow or a background sliver, and
the second and third are progressively tighter. Which attempt to trust is
decided later by looking for long narrow differently-coloured bands along an
edge, not by guessing here.

The centreline is the unit of work, not the image. Unrolling it into a
rectangle is what makes a sinuous core comparable with a straight one, and it
is also the form the ring analysis wants.

Usage:
    python3 engine/coreshape.py IMAGE.jpg --outdir DIR
"""

import argparse
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine.corefind as cf  # noqa: E402

CORE_DIA_MM = 5.0
EDGE_MM = CORE_DIA_MM / 2.0        # the core edge should sit ~2.5 mm out

# Nested half-widths, per the agreed workflow.
ATTEMPTS = (2.2, 2.0, 1.75)

# A swell of more than this fraction above the core's own median width is
# treated as the contour having spilled rather than the core being wider.
SWELL_FRAC = 0.20

# Radius gate for the centreline, measured rather than guessed.
#
# Pooling the radius of curvature over all 26 reference centrelines gives
# p1 = 4.2 mm, p2 = 6.4, p5 = 10.2, p10 = 18.6, p25 = 48.3, median 95.1 mm.
# So the great majority of a core is gently curved and a thin tail is tight.
#
# After trimming 3 mm off each end the pooled distribution is p1 = 4.7,
# p2 = 7.0, p5 = 13.6, p10 = 22.7, p25 = 51.4, median 98.5 mm. The user
# expects real deformation at 50 to 70 mm radius over 20 mm stretches near the
# bark side, and that lands between p25 and p50 here - so the measurement and
# the field expectation agree.
#
# Measured at the 6 mm window above, only 2% of the centreline reads tighter
# than 20 mm and the median minimum radius is 47.5 mm. So 20 mm is now a loose
# sanity gate rather than an aggressive one: it catches a centreline that has
# genuinely been thrown onto something else, and it leaves the 50 to 70 mm
# deformation the user describes on bent cores well alone.
#
# Worth revisiting as more cores arrive, but it should not need to go much
# lower: the noise floor is above 15 mm, so a gate below that would be
# rejecting the tracker rather than the core.
MIN_RADIUS_MM = 20.0

# Neither end of a core is 5 mm core: the tip tapers to nothing and the bark
# end is bark. Measuring curvature straight to the ends put the tightest
# radius on the frame at 0.9 mm, against 2.6 mm once 3 mm is trimmed from each
# end, and moved the median radius from 10 mm to 17 mm. The user reports real
# deformation at 50 to 70 mm radius over 20 mm stretches near the bark side, so
# sub-20 mm readings are tracking artefact, not bent wood.
TRIM_END_MM = 3.0


# Curvature is the second derivative of the centreline, so it is dominated by
# whatever the finest wiggle in that line is. Measured raw, every one of the 26
# cores reported a radius of 0.0 mm and 100% of the line was "tighter than
# 25 mm", which is arithmetically true and physically meaningless. A core bends
# over centimetres, so the line is smoothed over a physical length before the
# derivative is taken. This is the window that decides what counts as a bend.
# Curvature is a second derivative, so its smallest resolvable feature is set
# by the noise in the centreline: one pixel is 0.045 mm here, and a wobble of
# that size over a 2 mm window reads as a radius of about 15 mm. Measured
# directly, the minimum radius grew 15 -> 28 -> 48 -> 62 -> 103 -> 173 mm as
# the window went 2 -> 4 -> 6 -> 8 -> 12 -> 24 mm. That is the signature of
# noise, not bent wood.
#
# At a 6 mm window the median minimum radius is 47.5 mm and the 25th
# percentile is 20.9 mm, which is where the field expectation of 50 to 70 mm
# deformation belongs. Below about 6 mm this measurement cannot resolve real
# curvature and should not be used to reject anything.
CURVATURE_SMOOTH_MM = 6.0


def _box_smooth(v, k):
    k = int(max(3, k))
    if k % 2 == 0:
        k += 1
    if k >= len(v):
        return v.copy()
    return np.convolve(np.pad(v, (k // 2, k // 2), "edge"),
                       np.ones(k) / k, mode="valid")


def curvature_radius(xs, ys, px_per_mm, smooth_mm=CURVATURE_SMOOTH_MM):
    """Radius of curvature in MILLIMETRES along a polyline.

    Two things were wrong here and both produced a radius of zero.

    num/den is the curvature in 1/mm, not a radius. It was being stored and
    gated as if it were a radius, so a straight stretch gave 0.000 and read as
    "infinitely tight" rather than "perfectly straight".

    And num/den is zero exactly where the line is straight, which after any
    real smoothing is most of the line. Taking a minimum over that therefore
    always returned zero. The radius is 1/curvature, and zero curvature means
    infinite radius, so those points must become inf and be excluded from the
    minimum rather than reported as zero.
    """
    n = len(xs)
    if n < 5:
        return np.full(n, np.inf)
    h = 1.0 / px_per_mm                 # mm of photo per column
    X = np.asarray(xs, dtype=float) * h
    Y = np.asarray(ys, dtype=float) / px_per_mm
    Y = _box_smooth(Y, int(max(5, round(smooth_mm / h))))
    dx = np.gradient(X)
    dy = np.gradient(Y)
    ddx = np.gradient(dx)
    ddy = np.gradient(dy)
    num = np.abs(dx * ddy - dy * ddx)
    den = np.power(dx * dx + dy * dy, 1.5)
    with np.errstate(divide="ignore", invalid="ignore"):
        kappa = np.where(den > 1e-15, num / den, 0.0)
    # zero curvature means a perfectly straight stretch, which is an infinite
    # radius, not a radius of zero
    R = np.full_like(kappa, np.inf)
    pos = kappa > 1e-9
    R[pos] = 1.0 / kappa[pos]
    R = cf.medfilt(R, 5)
    # one-sided differences at the ends make them unreliable; take the
    # strongest radius available nearby instead of the nearest
    edge = max(2, int(0.02 * n))
    for i in list(range(edge)) + list(range(n - edge, n)):
        lo = max(0, i - edge)
        hi = min(n, i + edge + 1)
        seg = R[lo:hi]
        seg = seg[np.isfinite(seg)]
        if len(seg):
            R[i] = float(np.min(seg))
    R[~np.isfinite(R)] = np.inf
    return R


def boundary_radius(ys, xs, px_per_mm):
    """Radius of curvature of one boundary curve, for the straighter-side test."""
    return curvature_radius(xs, ys, px_per_mm)


def correct_spill(xs, top, bot, px_per_mm):
    """Where the contour swells, keep the straighter side and mirror it.

    Returns (top2, bot2, n_fixed). A core is a near-constant-diameter rod, so
    a local bulge on one side only is the contour escaping onto something
    beside the core. Which side escaped is decided by which boundary is
    locally straighter, not by brightness, because the escape is usually onto
    something of similar tone.
    """
    n = len(xs)
    width = bot - top
    wmed = float(np.median(width))
    if wmed <= 0:
        return top, bot, 0
    swell = width > wmed * (1.0 + SWELL_FRAC)
    if not swell.any():
        return top, bot, 0

    top2 = top.copy()
    bot2 = bot.copy()
    fixed = 0
    win = max(5, int(0.04 * n))
    idx = np.where(swell)[0]
    # group contiguous spikes
    groups = []
    for i in idx:
        if groups and i - groups[-1][-1] <= 3:
            groups[-1].append(i)
        else:
            groups.append([i])

    for g in groups:
        lo = max(0, g[0] - win)
        hi = min(n, g[-1] + win + 1)
        sl = slice(lo, hi)
        if hi - lo < 7:
            continue
        # straightness of each boundary over the window, as median radius
        rt = float(np.median(boundary_radius(top[sl], xs[sl], px_per_mm)))
        rb = float(np.median(boundary_radius(bot[sl], xs[sl], px_per_mm)))
        top_spread = top[sl].max() - top[sl].min()
        bot_spread = bot[sl].max() - bot[sl].min()
        # the straighter side is the one with larger radius and less wander
        top_straight = (rt, -top_spread)
        bot_straight = (rb, -bot_spread)
        if top_straight >= bot_straight:
            # bottom spilled: replace it with the top mirrored about the top
            # boundary offset by the median half width
            half = wmed / 2.0
            bot2[sl] = top[sl] + 2.0 * half
        else:
            half = wmed / 2.0
            top2[sl] = bot[sl] - 2.0 * half
        fixed += 1
    return top2, bot2, fixed


def gate_curvature(xs, cs, px_per_mm, min_radius_mm=MIN_RADIUS_MM):
    # iterated, because resmoothing a gated span bends it again a little
    """Flag centreline segments tighter than the radius gate, and straighten.

    Returns (cs2, n_gated, min_radius_seen). The centreline is re-smoothed
    with a stronger kernel where the gate trips, because a real core is
    gently curved and a tight kink is a tracking artefact.
    """
    n = len(cs)
    cs2 = cs.copy()
    total = 0
    for _ in range(4):
        R = curvature_radius(xs, cs2, px_per_mm)
        finite = R[np.isfinite(R)]
        if not len(finite):
            return cs2, 0, float("inf")
        min_seen = float(np.min(finite))
        bad = R < min_radius_mm
        if not bad.any():
            break
        win = max(7, int(0.05 * n))
        groups = []
        for i in np.where(bad)[0]:
            if groups and i - groups[-1][-1] <= win:
                groups[-1].append(i)
            else:
                groups.append([i])
        total += len(groups)
        for g in groups:
            lo = max(0, g[0] - win)
            hi = min(n, g[-1] + win + 1)
            # A box fit, not a median. A median filter preserves a locally
            # linear signal almost exactly - that is what makes it good at
            # finding edges - so using one to straighten a bend changed
            # nothing and the gate could never converge. The minimum radius
            # came out bit-identical before and after on all 26 cores.
            seg = cs2[lo:hi]
            k = int(max(5, (2 * win) | 1))
            cs2[lo:hi] = _box_smooth(seg, k)
    return cs2, total, min_seen


def unroll(lum, xs_top, xs_bot, cs, half_px, n_out=None):
    """Cut the core out along its centreline and straighten it.

    Returns a 2-D array whose rows run across the core and whose columns run
    along it, so a sinuous core becomes a rectangle of even width.
    """
    n = len(cs)
    if n_out is None:
        n_out = n
    half = int(round(half_px))
    H, W = lum.shape
    out = np.zeros((2 * half + 1, n_out), dtype=float)
    t = np.linspace(0, n - 1, n_out)
    ti = np.clip(np.round(t).astype(int), 0, n - 1)
    # local tangent, to sample across the core rather than straight down
    dy = np.gradient(cs)
    for j, i in enumerate(ti):
        d = dy[i]
        norm = np.sqrt(1.0 + d * d)
        nx, ny = -d / norm, 1.0 / norm
        for k in range(-half, half + 1):
            xx = xs_bot[i] + nx * k
            yy = cs[i] + ny * k
            xi = int(round(xx))
            yi = int(round(yy))
            if 0 <= xi < W and 0 <= yi < H:
                out[k + half, j] = lum[yi, xi]
    return out


def process(path, outdir=None):
    w, lum_s, sc, (W0, H0) = cf.warmth(path)
    best = cf.find_best(w)
    rep = {"image": os.path.basename(path)}
    if best is None or best["score"] < 0:
        rep["verdict"] = "no core"
        return rep, None
    cs, half, valid = best["cs"], best["half"], best["valid"]
    xs_cols = np.where(valid)[0]
    x_lo, x_hi = int(xs_cols.min()), int(xs_cols.max())
    rep["trimmed_px"] = 0
    width_px = float(np.median(half[valid])) * 2.0 * sc
    px_per_mm = width_px / CORE_DIA_MM
    rep["px_per_mm"] = px_per_mm
    rep["core_width_px"] = width_px
    rep["length_px"] = best["length"]

    xs = np.arange(x_lo, x_hi + 1, dtype=float)
    c = cs[x_lo:x_hi + 1].copy()
    t = half[x_lo:x_hi + 1] * sc

    # trim the tip and the bark end before anything is measured or corrected
    ntrim = int(round(TRIM_END_MM * px_per_mm / sc))
    if 2 * ntrim < len(xs) // 4:
        xs, c, t = xs[ntrim:-ntrim], c[ntrim:-ntrim], t[ntrim:-ntrim]
        rep["trimmed_px"] = 2 * ntrim * sc
    top = c - t
    bot = c + t

    # step 3/4: width swells -> keep the straighter side
    top, bot, nfix = correct_spill(xs, top, bot, px_per_mm)
    rep["spills_corrected"] = nfix

    # step 5: redraw the centreline from the corrected contour
    c2 = (top + bot) / 2.0
    k = max(5, int(round(width_px * 0.5)) | 1)
    c2 = cf.smooth(c2, k)
    t2 = (bot - top) / 2.0
    t2 = cf.smooth(t2, k)
    rep["width_after_px"] = float(np.median(t2) * 2)
    rep["width_after_mm"] = rep["width_after_px"] / px_per_mm

    # step 6: curvature gate
    c3, ngate, min_seen = gate_curvature(xs, c2, px_per_mm)
    rep["curvature_spans_gated"] = ngate
    rep["min_radius_mm"] = min_seen
    rep["min_radius_gate_mm"] = MIN_RADIUS_MM
    R = curvature_radius(xs, c3, px_per_mm)
    finite = R[np.isfinite(R)]
    rep["min_radius_after_mm"] = float(np.min(finite)) if len(finite) else float("inf")

    # step 7: the three nested selections
    full = np.asarray(
        Image.open(path).convert("L").resize((W0 // sc, H0 // sc), Image.LANCZOS),
        dtype=float)
    attempts = {}
    for half_mm in ATTEMPTS:
        half_px = half_mm * px_per_mm / sc          # into reduced-scale pixels
        if half_px < 2:
            continue
        # `full` is at reduced scale, so the coordinates must be too. Passing
        # xs*sc indexed the downsampled image with full-resolution positions,
        # which ran off the array and produced a black strip.
        strip = unroll(full, xs, xs, c3, half_px)
        attempts[half_mm] = strip
    rep["attempts"] = list(attempts.keys())
    return rep, {"xs": xs, "cs": c3, "top": c3 - t2, "bot": c3 + t2,
                 "t2": t2, "attempts": attempts, "px_per_mm": px_per_mm,
                 "sc": sc, "x_lo": x_lo, "x_hi": x_hi}


def draw(path, geom, rep, out_png, out_strip=None):
    im = Image.open(path).convert("RGB")
    d = ImageDraw.Draw(im)
    sc = geom["sc"]
    top, bot = geom["top"] * sc, geom["bot"] * sc
    xs = geom["xs"] * sc
    pt = [(int(xs[i]), int(top[i])) for i in range(len(xs))]
    pb = [(int(xs[i]), int(bot[i])) for i in range(len(xs) - 1, -1, -1)]
    # one colour, one outline: the review overlay has to be unambiguous
    d.line(pt, fill=(255, 0, 255), width=6)
    d.line(pb, fill=(255, 0, 255), width=6)
    d.line([pt[0], pb[0]], fill=(255, 0, 255), width=6)
    d.line([pt[-1], pb[-1]], fill=(255, 0, 255), width=6)
    im.save(out_png)

    if out_strip and geom["attempts"]:
        tiles = []
        for half_mm, strip in geom["attempts"].items():
            s = strip.copy()
            # normalise for viewing only
            lo, hi = np.percentile(s, 2), np.percentile(s, 98)
            if hi > lo:
                s = np.clip((s - lo) / (hi - lo) * 255, 0, 255)
            tiles.append((half_mm, s))
        h = max(t.shape[0] for _, t in tiles)
        pad = 8
        W = sum(t.shape[1] for _, t in tiles) + pad * (len(tiles) + 1)
        canvas = Image.new("L", (W, h + 2 * pad), 255)
        xo = pad
        for half_mm, t in tiles:
            img = Image.fromarray(t.astype(np.uint8))
            canvas.paste(img, (xo, pad))
            xo += t.shape[1] + pad
        canvas.save(out_strip)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--outdir")
    a = ap.parse_args()
    rep, geom = process(a.image)
    for k, v in rep.items():
        print("  %-22s %s" % (k, v))
    if geom and a.outdir:
        os.makedirs(a.outdir, exist_ok=True)
        n = os.path.basename(a.image).rsplit(".", 1)[0]
        draw(a.image, geom, rep, os.path.join(a.outdir, n + "-shape.png"),
             os.path.join(a.outdir, n + "-sausage.png"))


if __name__ == "__main__":
    main()


# --- broken cores ----------------------------------------------------------
#
# A broken core is two pieces, not one bent piece. Forcing a single centreline
# through the break produces a step, and a step reads as a radius of a couple of
# millimetres: 10 of the 26 reference cores came out of the curvature gate
# still tighter than 20 mm, at 2.0 to 4.6 mm, which no bent 5 mm core does.
# Those are breaks.
#
# So a break is detected and the core is reported as segments rather than
# straightened through. Two signals, because they fail differently:
#   - a gap: columns with no core at all, which is a clean separation
#   - a step: the centreline jumps by a sizeable fraction of a core width,
#     which happens when the two halves touch or nearly touch
# A short chip is not a break, so gaps below MIN_SEGMENT_MM are bridged.
MIN_SEGMENT_MM = 12.0
STEP_FRACTION = 0.45          # a jump this fraction of a core width is a break


def find_segments(mask, cs, valid, half, px_per_mm, sc):
    """Split a tracked core at gaps and steps. Returns a list of column spans."""
    W = len(cs)
    colwarm = mask.any(axis=0)
    med_px = float(np.median(half[valid])) * 2.0 * sc
    min_gap_cols = max(1, int(round(MIN_SEGMENT_MM * px_per_mm / sc)))
    step_px = STEP_FRACTION * med_px

    breaks = []
    gap_run = 0
    for x in range(W):
        if not colwarm[x]:
            gap_run += 1
            continue
        if gap_run >= min_gap_cols:
            breaks.append(("gap", x - gap_run // 2))
        gap_run = 0
        if 0 < x < W and valid[x] and valid[x - 1]:
            if abs(cs[x] - cs[x - 1]) > step_px:
                breaks.append(("step", x))
    if not breaks:
        return [(0, W - 1, "whole")]

    # merge break points that land on top of each other
    merged = []
    for kind, x in breaks:
        if merged and x - merged[-1][1] < min_gap_cols:
            merged[-1] = (merged[-1][0], x, merged[-1][2] + "/" + kind)
        else:
            merged.append((x, x, kind))

    spans = []
    start = 0
    for x, _, kind in merged:
        if x - start >= min_gap_cols:
            spans.append((start, x - 1, kind))
            start = x
    if W - start >= min_gap_cols:
        spans.append((start, W - 1, "end"))
    return [s for s in spans if s[1] - s[0] >= min_gap_cols] or [(0, W - 1, "whole")]
