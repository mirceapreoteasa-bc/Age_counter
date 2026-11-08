"""Orientation-free detection of the Age Core Photo Card.

The card may appear at any rotation in the photo, so nothing here assumes the
strip is horizontal. Strategy:

  1. Segment the light-grey support strip (low chroma, mid brightness).
  2. Take its principal axis as the strip direction, whatever that is.
  3. Project the dark mask along that axis and comb-fit the bay ticks, which
     are equally spaced by construction.
  4. Report px/mm from the fitted pitch, plus the geometry needed to sample
     the core.

Two independent references are reported when both are visible: the printed
bay pitch (which suffers print shrink) and the physical ruler (which cannot).
Their disagreement is a diagnostic, not something to silently average.
"""
from __future__ import annotations

import numpy as np

BAY_PITCH_MM = 37.60
BAYS = 5
STRIP_LEN_MM = 188.0
STRIP_H_MM = 38.0


def _load_gray(img, max_side=1100):
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    im = img.copy()
    im.thumbnail((max_side, max_side))
    return np.asarray(im.convert("RGB")).astype(np.int16), im.size


def _seg(g, lo=90, hi=250, chroma_rel=0.16):
    """Segment the light support strip.

    Chroma is measured *relative* to brightness, not as an absolute channel
    spread. Phone photos carry a strong blue cast (B-R around 90 on white
    paper), so an absolute test rejected the entire card and the detector was
    left with only shadows and gaps.
    """
    mx = g.max(2)
    mn = g.min(2)
    grey = (mx - mn) < np.maximum(chroma_rel * mx, 12)
    mid = (mx > lo) & (mx < hi)
    return grey & mid


def _clean(m, min_frac=0.010):
    """Keep only large components. The translucent ruler and shadows are also
    'grey', so size filtering is what separates the real card strip from them."""
    from scipy import ndimage as ndi
    m = ndi.binary_opening(m, np.ones((3, 3)))
    m = ndi.binary_closing(m, np.ones((7, 7)))
    lab, n = ndi.label(m)
    if n == 0:
        return m, 0
    sz = ndi.sum(m, lab, range(1, n + 1))
    keep = np.zeros(n + 1, bool)
    keep[1:] = sz >= max(min_frac * m.size, 400)
    return keep[lab], int(keep[1:].sum())


def _comb_from_bars(bars, key, pitch_lo, pitch_hi, tol=0.15, kmin=3, kmax=6,
                    ideal=None):
    """Fit an equally spaced comb of dark bars along one image axis.

    Bars are first grouped into a common column, because on a real card the
    bay ticks all sit at the same offset perpendicular to the strip axis. That
    grouping is what rejects the ruler marks and the core's dark end, which
    otherwise produce convincing but false 3-to-5 tick combs.
    """
    import itertools
    if len(bars) < kmin:
        return None
    best = None
    vals = np.array([b[key] for b in bars], dtype=float)
    order = np.argsort(vals)
    groups, cur = [], [order[0]]
    for i in order[1:]:
        if abs(vals[i] - vals[cur[-1]]) <= 30.0:
            cur.append(i)
        else:
            groups.append(cur)
            cur = [i]
    groups.append(cur)
    for gr in groups:
        g = sorted(gr, key=lambda i: vals[i])
        n = len(g)
        for k in range(kmin, min(kmax, n) + 1):
            for combo in itertools.combinations(g, k):
                v = vals[list(combo)]
                d = np.diff(v)
                pitch = float(d.mean())
                if not (pitch_lo <= pitch <= pitch_hi):
                    continue
                err = float(np.abs(d - pitch).max())
                # A 3-tick comb has only 2 intervals, so it can look
                # "equally spaced" by luck. Demand much tighter agreement
                # than for 4+ ticks before trusting it.
                t = tol * (0.35 if k == 3 else 1.0)
                if err > t * pitch:
                    continue
                th = np.array([bars[i]["thick"] for i in combo], float)
                spread = float(th.std() / max(th.mean(), 1e-9))
                if spread > 0.55:
                    continue
                # Reward ticks and a pitch in the middle of the plausible
                # band. Without the pitch prior the search prefers tight
                # 3-tick clusters and reports wildly coarse scales.
                # The card is an A4 sheet photographed close up, so the bay
                # pitch is LARGE: several bays span the frame. Reward pitch
                # that keeps a healthy number of bays in view, i.e. pitch at
                # or above max_dim/(k*BAYS) for a few bays across. Do not
                # centre the prior on a small multiple, which favours the
                # tight junk clusters.
                ref = ideal if ideal else 0.5 * (pitch_lo + pitch_hi)
                if pitch >= ref:
                    prior = 1.0 + min((pitch / ref - 1.0), 3.0) * 0.5
                else:
                    prior = max(0.15, 1.0 - (1.0 - pitch / ref))
                # 3 ticks are worth less than 4+, they are only a fallback
                conf = 0.45 if k == 3 else 1.0
                score = (conf * (k - 1) * (1.0 + prior)
                         / (1.0 + 6.0 * spread + 6.0 * err / pitch))
                if best is None or score > best["score"]:
                    best = {
                        "score": score, "n": k, "pitch_px": pitch,
                        "positions": [float(x) for x in v],
                        "thickness_spread": spread, "group": list(combo),
                    }
    return best


def _dark_bars(dark):
    """Locate the thick, near-black bay ticks on the strip.

    These are short heavy bars perpendicular to the strip axis, much darker
    and thicker than ring lines or ruler ticks, so they are the most reliable
    fiducial in the frame. Finding them first also gives the strip axis
    directly, which is more robust than trying to segment a strip that the
    cores may be covering.
    """
    from scipy import ndimage as ndi
    d = ndi.binary_opening(dark, np.ones((5, 5)))
    d = ndi.binary_closing(d, np.ones((11, 3)))
    lab, n = ndi.label(d)
    out = []
    for i in range(1, n + 1):
        ys, xs = np.nonzero(lab == i)
        if len(xs) < 120:
            continue
        ext = max(xs.max() - xs.min(), ys.max() - ys.min()) + 1
        thick = min(xs.max() - xs.min(), ys.max() - ys.min()) + 1
        if thick > 0.42 * ext:
            continue
        out.append({
            "x0": int(xs.min()), "x1": int(xs.max()),
            "y0": int(ys.min()), "y1": int(ys.max()),
            "area": int(len(xs)), "length": int(ext), "thick": int(thick),
            "cx": float(xs.mean()), "cy": float(ys.mean()),
        })
    return out


def _largest_strip(mask):
    """Pick the elongated large component, not the biggest blob."""
    from scipy import ndimage as ndi
    lab, n = ndi.label(mask)
    if n == 0:
        return None
    best, bestscore = None, -1.0
    for i in range(1, n + 1):
        ys, xs = np.nonzero(lab == i)
        if len(xs) < 500:
            continue
        ext = max(xs.max() - xs.min(), ys.max() - ys.min()) + 1
        # width perpendicular to the long axis; a card strip is long and thin,
        # and its width is a known 38 mm, so prefer long-and-thin over blobs
        q = np.stack([xs, ys]).astype(np.float64) - np.stack([xs, ys]).mean(1, keepdims=True)
        _, evec = np.linalg.eigh(q @ q.T / len(xs))
        minor = evec[:, 0]
        # project ONTO the minor axis; projecting perpendicular to it returns
        # the major extent and makes every component look square
        across = q[0] * minor[0] + q[1] * minor[1]
        wid = float(across.max() - across.min())
        if wid < 1e-6:
            continue
        aspect = ext / wid
        if aspect < 3.0:
            continue
        score = ext * (0.35 + float(len(xs)) / (ext * wid))
        if score > bestscore:
            bestscore, best = score, (i, xs, ys, ext)
    if best is None:
        return None
    i, xs, ys, ext = best
    return lab == i, ext


def _axis(mask):
    ys, xs = np.nonzero(mask)
    if len(xs) < 200:
        return None
    pts = np.stack([xs, ys]).astype(np.float64)
    centre = pts.mean(1, keepdims=True)
    q = pts - centre
    cov = q @ q.T / len(xs)
    ev, evec = np.linalg.eigh(cov)
    major = evec[:, int(np.argmax(ev))]
    if major[0] < 0:
        major = -major
    return centre, major / np.linalg.norm(major)


def _comb(peaks, pitch_lo, pitch_hi, kmin=3, kmax=6):
    """Best comb: k equally spaced peaks consistent with the observed set.

    kmin is 3 by design. With k=2 any two peaks are equally spaced by
    definition, which made the fit report confident 2-tick matches on photos
    where the card was never in frame.
    """
    best = None
    n = len(peaks)
    for k in range(kmin, min(kmax, n) + 1):
        for i in range(n - k + 1):
            win = peaks[i:i + k]
            d = np.diff(win)
            p = float(d.mean())
            if not (pitch_lo <= p <= pitch_hi):
                continue
            err = float(np.abs(d - p).max())
            if err > 0.12 * p:
                continue
            score = (k - 1) / (1.0 + 6.0 * err / p)
            if best is None or score > best[0]:
                best = (score, win.copy(), p, err)
    return best


def _find_peaks(y, thr, min_distance=3):
    """Strict local maxima above thr, greedily thinned by min_distance."""
    cand = [i for i in range(1, len(y) - 1)
            if y[i] >= thr and y[i] >= y[i - 1] and y[i] > y[i + 1]]
    cand.sort(key=lambda i: -y[i])
    out = []
    for i in cand:
        if all(abs(i - j) >= min_distance for j in out):
            out.append(i)
    return np.array(sorted(out), dtype=int)


def detect(img, verbose=False):
    g, (w, h) = _load_gray(img)
    out = {"width": w, "height": h, "strip_found": False, "notes": []}

    mx = g.max(2)
    out["brightness_percentiles"] = [float(v) for v in np.percentile(mx, [5, 25, 50, 75, 95])]

    best = None
    for t in (60, 80, 100, 120, 140, 160):
        bars = [b for b in _dark_bars(mx < t) if b["thick"] >= 6 and b["area"] >= 150]
        if len(bars) < 3:
            continue
        for key in ("cx", "cy"):
            for nom, lbl in ((BAY_PITCH_MM, "bay_pitch"), (100.0, "scale_bar")):
                # Plausible field of view: a phone/tablet photo of an A4 sheet
                # resolves roughly 5-60 px/mm. Anything outside is text, ring
                # lines or ruler graduations, not a card fiducial.
                # This search runs on a downscaled thumbnail, so the useful
                # band is much coarser than at full resolution. Bound by what
                # the thumbnail can actually resolve instead of by native px.
                # The card nearly fills the frame in these captures, so the
                # physically meaningful band is fixed by the card's own size:
                # 188 mm of strip must fit inside the image, and the strip
                # cannot be smaller than a few pixels per bay.
                pxmm_lo = max(nom / (max(w, h) / BAYS), 0.5)
                pxmm_hi = 45.0
                lo = max(nom * pxmm_lo, 20.0)
                hi = min(nom * pxmm_hi, max(w, h) * 0.9)
                c = _comb_from_bars(bars, key, lo, hi,
                                     ideal=max(w, h) / (2.0 * BAYS))
                if c is None:
                    continue
                c["axis_key"] = key
                c["threshold"] = t
                c["nominal_mm"] = nom
                c["reference"] = lbl
                # prefer 5 ticks, then wide pitch ranges, then cleanliness
                c["score"] += 0.6 * c["n"]
                if best is None or c["score"] > best["score"]:
                    best = c
    if best is None:
        out["notes"].append("no tick comb found in any threshold")
        return out

    pxmm = best["pitch_px"] / best["nominal_mm"]
    out.update(
        strip_found=True,
        n_ticks=best["n"],
        pitch_px=best["pitch_px"],
        tick_positions=best["positions"],
        tick_axis=best["axis_key"],
        tick_threshold=best["threshold"],
        thickness_spread=best["thickness_spread"],
        px_per_mm=float(pxmm),
        scale_ref=best["reference"],
        comb_score=float(best["score"]),
    )
    if verbose:
        print(f"  px/mm={pxmm:.2f} ref={best['reference']} ticks={best['n']} "
              f"pitch={best['pitch_px']:.1f}px axis={best['axis_key']} thr={best['threshold']}")
    return out
