"""Recover absolute scale from a photo, and refuse when the references conflict.

This is the last blocker. Everything downstream - ring width in mm, the
morphology element sizes, the count itself - depends on px/mm, and a wrong
px/mm is silently catastrophic: it rescales every structuring element and
every threshold, so the detector still returns a plausible count, just the
wrong one. Measured on a real frame, accepting a single dominant period
overstated ring width by 74%.

## Why a single period cannot work

A frame of a core on the v3 card contains, at exact integer ratios:

    the ruler's 1 mm graduations
    the card's 10 mm comb          = 10 x
    the card's 50 mm comb          = 50 x

plus a 100 mm cross-check bar at 100 x. Any of them can be the strongest
signal in the frame, and reporting the strongest one as "1 mm" lands on
whichever octave the comb fit happened to lock onto. Observed on one real
frame: 9.6, 9.375, 18.5, 23.5 and 40 px/mm, all from the same image, all
mutually inconsistent, only one of them right.

## The fix: require mutual consistency

A candidate period p is only accepted as 1 mm if the frame *also* shows 10p
and 50p. The smallest period satisfying that triple is the fundamental, and it
is fixed by construction rather than by picking the loudest peak.

If the triple cannot be satisfied, the frame does not get a scale. It gets a
reported conflict. That is the whole point: the dangerous case is not a frame
we fail on, it is a frame we confidently mis-scale.

## References, in order of trust

1. **Physical ruler graduations**, read jointly with the card combs.
2. **Card bay pitch** (5 bays, 37.6 mm) - printed, so it carries the printer's
   scale error, but it is opaque, high contrast and never occluded by the
   core, so it is a dependable second opinion.
3. **Core diameter** (~5 mm) - the only non-printed constant, and so immune to
   print shrink, but measured to about +-20% because the photographed
   silhouette includes the shadowed rim. Fallback and sanity band only.

A core diameter is *never* allowed to override a card or ruler measurement.
"""

from __future__ import annotations

import numpy as np

BAY_PITCH_MM = 37.60
BAYS = 5
COMB10_MM = 10.0
COMB50_MM = 50.0
BAR100_MM = 100.0
CORE_DIAMETER_MM = 5.0

# Mutual-consistency tolerance. 1 mm graduations on a phone photo of an A4
# sheet are well inside this; print shrink is 7% and must NOT be absorbed, so
# the tolerance has to stay well under it or the check approves a wrong octave.
CONSISTENCY_TOL = 0.05
# Two independent scales may differ by this much before we call it a conflict.
SCALE_AGREEMENT_TOL = 0.06
# Core diameter is coarse, so it only gates a plausible range.
CORE_DIA_BAND_MM = (3.5, 6.5)


def _band_periods(gray: np.ndarray, band: int = 340, step: int = 10,
                  min_period: float = 4.0, max_period: float | None = None,
                  min_contrast: float = 9.0) -> dict[float, float]:
    """Significant periods present in the frame, with a strength each.

    A sliding band is detrended and FFT'd, so local contrast is measured
    without the frame's overall brightness dominating the spectrum. Results are
    accumulated over bands and keyed on period.
    """
    from scipy import ndimage as ndi
    dark = gray < 125
    H, W = dark.shape
    # The period ceiling must not be an absolute pixel number. A 50 mm comb can
    # be several hundred pixels across even in a thumbnail, and a fixed 700 px
    # cap silently truncated exactly the partner needed to confirm the octave -
    # which is how the check reported "no 10x and 50x partner" on a frame that
    # had them. Cap relative to the frame instead.
    if max_period is None:
        max_period = 0.9 * max(H, W)
    acc: dict[float, float] = {}
    for y in range(0, max(1, H - band), step):
        prof = dark[y:y + band].sum(0).astype(float)
        p = prof - ndi.uniform_filter1d(prof, 251)
        p -= p.mean()
        if p.std() < min_contrast:
            continue
        n = len(p)
        kmin = max(1, int(n / max_period))
        kmax = int(n / min_period)
        if kmax <= kmin + 2:
            continue
        F = np.abs(np.fft.rfft(p * np.hanning(n)))
        seg = F[kmin:kmax]
        if not len(seg):
            continue
        # the strongest few peaks in this band
        for o in np.argsort(seg)[::-1][:4]:
            j = int(o) + kmin
            per = n / j
            if not (min_period <= per <= max_period):
                continue
            acc[round(per, 1)] = acc.get(round(per, 1), 0.0) + float(seg[o]) / p.std()
    return acc


def _has_period(periods: dict[float, float], target: float,
                tol: float = CONSISTENCY_TOL) -> bool:
    for k in periods:
        if abs(k - target) <= tol * target:
            return True
    return False


def _nearest_period(periods: dict[float, float], target: float,
                    tol: float = CONSISTENCY_TOL) -> float | None:
    best, bestd = None, None
    for k in periods:
        d = abs(k - target) / target
        if d <= tol and (bestd is None or d < bestd):
            best, bestd = k, d
    return best


def _loudest_within(periods: dict[float, float], lo: float, hi: float) -> float | None:
    cands = [(v, k) for k, v in periods.items() if lo <= k <= hi]
    if not cands:
        return None
    return max(cands)[1]


def resolve_1mm(periods: dict[float, float]) -> dict:
    """Pick the 1 mm fundamental from a set of observed periods.

    Returns the smallest period p for which 10p and 50p are also present.
    Taking the *smallest* such p is what pins the octave: a harmonic
    2p would also satisfy the test if 20p and 100p happened to appear, so the
    candidates are ranked by size and the smallest consistent one wins.
    """
    keys = sorted(periods)
    accepted = []
    for p in keys:
        p10 = _nearest_period(periods, COMB10_MM * p)
        p50 = _nearest_period(periods, COMB50_MM * p)
        if p10 is None or p50 is None:
            continue
        # strength of the triple, used to break ties between equals
        strength = periods[p] + periods[p10] + periods[p50]
        accepted.append((p, p10, p50, strength, len(periods)))
    if not accepted:
        return {"found": False, "reason": "no period has both its 10x and 50x partner"}
    accepted.sort(key=lambda t: (t[0], -t[3]))
    p, p10, p50, strength, n_obs = accepted[0]
    # the 10x and 50x must agree with each other too, or the frame is
    # internally inconsistent and we should not pretend otherwise
    e10 = abs(p10 - COMB10_MM * p) / (COMB10_MM * p)
    e50 = abs(p50 - COMB50_MM * p) / (COMB50_MM * p)
    return {
        "found": True,
        "one_mm_px": float(p),
        "ten_mm_px": float(p10),
        "fifty_mm_px": float(p50),
        "px_per_mm": float(p),
        "err_10x": float(e10),
        "err_50x": float(e50),
        "err_worst": float(max(e10, e50)),
        "strength": float(strength),
        "n_periods_observed": int(n_obs),
        "n_consistent_candidates": len(accepted),
    }


def scale_from_bays(periods: dict[float, float],
                    plausible: tuple[float, float] = (3.0, 90.0)) -> float | None:
    """px/mm from the card's 37.6 mm bay pitch.

    The bay pitch is *known geometry*, not a period to be discovered, so the
    search is constrained to what is plausible: 37.6 mm must land inside the
    frame, which bounds the candidate period. Searching for "the loudest period
    in a wide range" instead picked up a 140 px sub-pattern of the core and
    returned 3.7 px/mm, an 82% error.
    """
    lo = BAY_PITCH_MM * plausible[0]
    hi = BAY_PITCH_MM * plausible[1]
    d = _loudest_within(periods, lo, hi)
    return None if d is None else float(d) / BAY_PITCH_MM


def measure_labelled_bar(gray: np.ndarray, y0: int, y1: int,
                         x_lo: int = 0, x_hi: int | None = None) -> float | None:
    """px/mm from a printed bar with known end-to-end length.

    Uses the bar's own printed extent, which is unambiguous: the solid dark run
    starts at the zero tick and ends at the hundred. This is the most reliable
    single reference available when the card is in frame, and on one real frame
    it returned 22.04 px/mm where the decade *labels* returned 20.39 - an 8%
    disagreement, which is the same order as the printer's scale error and is
    exactly the kind of conflict that must be reported rather than averaged.
    """
    a = np.asarray(gray, dtype=np.float32)
    if a.ndim == 3:
        a = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    H, W = a.shape
    hi = x_hi if x_hi is not None else W
    band = a[y0:y1, x_lo:hi]
    if band.size == 0:
        return None
    # longest run of "dark" columns across the band = the bar body
    dark = (band < 130).sum(0) > 0.35 * band.shape[0]
    best = cur = 0
    start = best_start = -1
    for i, v in enumerate(dark):
        if v:
            if cur == 0:
                start = i
            cur += 1
            if cur > best:
                best, best_start = cur, start
        else:
            cur = 0
    if best < 200:
        return None
    # bar length in px; caller supplies the mm via the returned length
    return float(best)


def measure_core_diameter_px(gray_or_rgb, axis: str = "auto") -> float | None:
    """Median core thickness in px - a proxy for the 5 mm drill diameter.

    Deliberately coarse. The photographed silhouette includes the shadowed rim
    and the darkest ring boundaries, which inflate it: measured 5.6-6.2 mm
    against a nominal 5 mm on frames whose card scale was known. Used as a
    sanity band and a fallback, never as an authority.
    """
    a = np.asarray(gray_or_rgb, dtype=np.float32)
    if a.ndim == 3:
        r, b = a[..., 0], a[..., 2]
        warm = ((r - b) > 16) & (r > 90)
    else:
        warm = a < 190  # not meaningful; caller should pass RGB
        return None
    from scipy import ndimage as ndi
    warm = ndi.binary_closing(warm, np.ones((5, 5)))
    rows = warm.mean(1)
    cols = warm.mean(0)
    horiz = rows.max() >= cols.max()
    widths = []
    if horiz:
        idx = np.nonzero(rows > 0.4)[0]
        if idx.size < 5:
            return None
        for x in range(0, warm.shape[1], max(1, warm.shape[1] // 60)):
            col = np.nonzero(warm[:, x])[0]
            if col.size > 10:
                d = np.diff(col)
                segs = np.split(col, np.nonzero(d > 3)[0] + 1)
                L = max(len(s) for s in segs)
                if L > 15:
                    widths.append(L)
    else:
        idx = np.nonzero(cols > 0.4)[0]
        if idx.size < 5:
            return None
        for y in range(0, warm.shape[0], max(1, warm.shape[0] // 60)):
            row = np.nonzero(warm[y, :])[0]
            if row.size > 10:
                d = np.diff(row)
                segs = np.split(row, np.nonzero(d > 3)[0] + 1)
                L = max(len(s) for s in segs)
                if L > 15:
                    widths.append(L)
    if len(widths) < 5:
        return None
    return float(np.median(widths))


def resolve_scale(rgb: np.ndarray, core_px: float | None = None) -> dict:
    """Full scale resolution with an explicit verdict.

    Returns a dict with px_per_mm (or None), the reference that produced it,
    and a list of conflicts. `ok` is False whenever the references disagree -
    the frame is then not safe to count, which the caller enforces.
    """
    a = np.asarray(rgb, dtype=np.float32)
    if a.ndim == 3:
        gray = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    else:
        gray = a
    periods = _band_periods(gray)

    out: dict = {
        "periods_found": len(periods),
        "conflicts": [],
        "references": {},
        "px_per_mm": None,
        "ok": False,
    }
    if not periods:
        out["reason"] = "no periodic structure found; is the card or ruler in frame?"
        return out

    comb = resolve_1mm(periods)
    out["comb"] = comb
    if comb["found"]:
        out["references"]["ruler_plus_card"] = comb["px_per_mm"]
    else:
        out["conflicts"].append(
            "ruler 1 mm comb not confirmed: " + comb.get("reason", ""))

    bay = scale_from_bays(periods)
    if bay:
        out["references"]["card_bays"] = bay

    if core_px:
        out["references"]["core_diameter"] = core_px / CORE_DIAMETER_MM

    # choose the authority: ruler+card wins, then card bays, then diameter
    if "ruler_plus_card" in out["references"]:
        out["px_per_mm"] = out["references"]["ruler_plus_card"]
        out["reference"] = "ruler+card combs (1 mm pitch, confirmed at 10x and 50x)"
    elif "card_bays" in out["references"]:
        out["px_per_mm"] = out["references"]["card_bays"]
        out["reference"] = "card bay pitch (printed, may carry print scale error)"
    elif "core_diameter" in out["references"]:
        out["px_per_mm"] = out["references"]["core_diameter"]
        out["reference"] = ("core diameter (coarse, +-20%; the photographed "
                            "silhouette includes the shadowed rim)")
        out["conflicts"].append(
            "scale from core diameter only; treat as provisional")
    else:
        out["reason"] = "no usable scale reference in frame"
        return out

    # cross-check every other reference against the chosen one
    px = out["px_per_mm"]
    for name, val in out["references"].items():
        if name == out.get("reference", "")[:0] + name and abs(val - px) < 1e-9:
            continue
        rel = abs(val - px) / px
        out.setdefault("agreement", {})[name] = float(rel)
        if rel > SCALE_AGREEMENT_TOL:
            # the diameter is expected to be this loose, so it is a band, not
            # a conflict
            if name == "core_diameter":
                implied = val * 1.0  # px_per_mm -> implied diameter below
                continue
            out["conflicts"].append(
                f"scale references disagree: {name} {val:.2f} vs "
                f"{px:.2f} px/mm ({rel*100:.1f}%)")

    # sanity band on the core diameter
    if core_px:
        implied_mm = core_px / px
        out["implied_core_diameter_mm"] = float(implied_mm)
        if not (CORE_DIA_BAND_MM[0] <= implied_mm <= CORE_DIA_BAND_MM[1]):
            out["conflicts"].append(
                f"core measures {implied_mm:.2f} mm across at this scale, "
                f"outside the plausible {CORE_DIA_BAND_MM[0]}-"
                f"{CORE_DIA_BAND_MM[1]} mm band for a 5 mm increment core")

    # a conflict that is not the expected diameter looseness blocks the count
    hard = [c for c in out["conflicts"] if "provisional" not in c
            and "out of frame" not in c]
    out["ok"] = not hard
    if hard:
        out["reason"] = "scale references conflict; refusing to count"
    return out
