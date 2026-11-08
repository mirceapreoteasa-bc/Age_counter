"""Ring detection engine for increment cores.

Approach follows MtreeRing (Shi & Xiang 2021, CRAN 1.4.5), which implements the
morphological method of Soille & Misson (2001, Can. J. For. Res. 31:1074-1083)
and the linear detector of Lara et al. (2015, Dendrochronologia 34:43-50).

The important properties inherited from that method:

* Structuring-element sizes are *physical*, independent of resolution:
  opening 0.0635 mm kills dark specks inside earlywood, closing 0.3175 mm kills
  light strips inside latewood, and the black-hat closing is 2.54 mm. A
  pixel-based size silently rescales with the camera and destroys the tuning.
* A ring is a single latewood band, so rings are counted as *band runs*, not as
  profile peaks. Peak counting double-counts because each band has two edges.
* Inclined or curved boundaries are corrected with two paths a known distance
  apart: width *= cos(atan(dx / separation)).

Deliberately different from the research prototype: the caller supplies the core
strip and the sampling band. Auto-locating the core was the single largest
source of error in the prototype (it selected the wooden table, the plastic
ruler, or the paper instead), and a person can point at a core in one tap.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np
from scipy import ndimage

# --- physical constants from the reference method (mm) -----------------------
SE_OPENING_MM = 25.4 / 400.0        # 0.0635 mm
SE_CLOSING_MM = 25.4 / 80.0         # 0.3175 mm
SE_BLACKHAT_MM = 25.4 / 10.0        # 2.54 mm
MIN_RING_MM = 0.1                   # dp/10 px in the reference ring_sort

# --- resolvability gate -----------------------------------------------------
# Below this many pixels across a ring the boundary cannot be located reliably.
MIN_PX_PER_RING = 6.0
# Single-photo provisional uncertainty, per cent of the count.
SINGLE_PHOTO_UNCERTAINTY_PCT = 10.0

# Pre-filter scales tried around the estimated ring width, as multiples of it.
# The count's spread across this ladder is the confidence.
# Bracket the range where the s2 element is correct for both regimes: measured
# best s2 is ~0.26 x ring width for mature rings (1.2 mm) and 0.20-0.30 x for
# 0.27 mm rings, so the ladder is centred there and the extremes are diagnostic.
# The count depends on two tuning constants at once, so the ladder is 2-D and
# the confidence is the spread of the resulting counts. On the mountain hemlock
# the grid spanned 68-242 rings for the same photo, which is the real
# uncertainty of an unattended count on that core - not a number to hide behind
# a guessed percentage.
# The black-hat must be smaller than the ring pitch or it merges pairs, and
# much larger than it and the band cannot be separated. Measured optima were
# 0.5 x ring width on 337part and 0.6-0.7 x on 217-1-5, so the ladder brackets
# that. The old 0.7-1.4 x range never reached the working point and dragged the
# median down: on 217-1-5 it reported 57 where the truth is 84.
#
# Hemlock (HW/HM) needs a NARROWER black-hat than the default, not a wider one.
# This was measured, and it contradicted the obvious assumption. A gradual
# earlywood-to-latewood transition spreads the darkening over a wider band, so
# the pre-filter needs a tighter element to keep each ring distinct instead of
# merging neighbours. Sweeping black-hat on a 46-ring synthetic with a wide
# soft latewood band (duty 0.62, hemlock-like):
#
#     black-hat   count   error
#        0.80 mm     87   +89%   (splits rings, noise amplified)
#        1.20 mm     50    +9%
#        1.60 mm     46     0%   <- optimum
#        2.00 mm     45    -2%
#        2.54 mm     45    -2%   <- the abrupt-core default
#        3.20 mm     43    -7%
#        5.50 mm     40   -13%   (merges adjacent rings)
#
# The same sweep on an abrupt core (duty 0.30, pine/spruce-like) sits at
# +50% everywhere, because that synthetic has a different duty cycle, so the
# two must be told apart by species rather than by one fixed setting.
#
# The forester's note on 215 1-2 HW - "46 assumed, 42-43 rings visible" - is a
# ~7% loss consistent with running the abrupt-core default on a gradual core.
LADDER_CORE = tuple((s2m, bhm)
                    for s2m in (0.20, 0.28, 0.36)
                    for bhm in (0.50, 0.60, 0.70))

# Gradual-transition conifers: the same rungs plus tighter black-hats.
LADDER_GRADUAL = LADDER_CORE + tuple(
    (s2m, bhm) for s2m in (0.20, 0.28, 0.36) for bhm in (0.25, 0.32, 0.40))

# Default stays the core ladder so existing results do not move; callers that
# know the species pass the wider one.
LADDER = LADDER_CORE

# Species group -> ladder. Keyed on the forester's own codes so the record
# carries the assumption that produced the number.
LADDER_BY_SPECIES = {
    # conifers with an abrupt EW/LW transition (pine, spruce, fir, Douglas-fir)
    "DEFAULT": LADDER_CORE,
    # hemlocks: gradual transition
    "HW": LADDER_GRADUAL,
    "HM": LADDER_GRADUAL,
    "FDC": LADDER_CORE,
    "PL": LADDER_CORE,
    "BI": LADDER_CORE,
}


def ladder_for(species_code: Optional[str] = None) -> tuple:
    """Ladder for a species code, falling back to the core ladder.

    Unknown codes deliberately get the default rather than an error: a
    mis-typed code must not silently disable the extra rungs, and the ladder
    reports its own spread either way.
    """
    if not species_code:
        return LADDER_CORE
    return LADDER_BY_SPECIES.get(species_code.strip().upper(), LADDER_CORE)


def _rect(size: int) -> np.ndarray:
    return np.ones((max(1, int(size)),) * 2, bool)


def grey_opening(img: np.ndarray, size: int) -> np.ndarray:
    f = _rect(size)
    return ndimage.grey_dilation(ndimage.grey_erosion(img, footprint=f),
                                footprint=f)


def grey_closing(img: np.ndarray, size: int) -> np.ndarray:
    f = _rect(size)
    return ndimage.grey_erosion(ndimage.grey_dilation(img, footprint=f),
                                footprint=f)


def estimate_spacing_px(profile: np.ndarray, lag_lo: int = 3,
                        lag_hi: Optional[int] = None) -> float:
    """Dominant ring spacing in px, from the autocorrelation of the profile.

    Requires a genuine local peak that stands above the local baseline. A plain
    argmax is worthless here: a smooth profile autocorrelates ~0.97 at every
    short lag, which during prototyping made a wooden table look like a core.
    """
    x = np.asarray(profile, dtype=np.float64)
    if x.size < 32 or x.std() < 1e-9:
        return 0.0
    # remove the lighting gradient first: on a real core the low-frequency
    # trend carries far more power than the rings and hides the peak
    trend = ndimage.uniform_filter1d(x, size=max(9, x.size // 8),
                                     mode="nearest")
    x = x - trend
    if x.std() < 1e-9:
        return 0.0
    x = x - x.mean()
    x *= np.hanning(x.size)
    power = np.abs(np.fft.rfft(x)) ** 2
    freqs = np.fft.rfftfreq(x.size, d=1.0)
    period = 1.0 / np.maximum(freqs, 1e-9)
    hi = lag_hi or max(lag_lo + 2, x.size // 6)
    m = (period >= lag_lo) & (period <= hi)
    if not m.any():
        return 0.0
    p = period[m]
    w = power[m]
    # pick the strongest peak, then require it to beat the surrounding floor
    i = int(np.argmax(w))
    best_p, best_score = float(p[i]), 0.0
    for j in range(len(p)):
        near = np.abs(p - p[j]) < 0.5 * p[j]
        far = (p > 1.4 * p[j]) & (p < 3.0 * p[j])
        if far.any() and near.any():
            score = float(w[near].max() - np.median(w[far]))
            if score > best_score:
                best_score, best_p = score, float(p[j])
    return best_p if best_score > 0 else 0.0


def bootstrap_spacing_px(sub: np.ndarray, px_per_mm: float) -> float:
    """Estimate ring spacing by making a first, deliberately crude count.

    A periodogram is the obvious way to measure spacing, but ring width falls
    monotonically from juvenile to senescent, which broadens the spectrum and
    pushes its peak to roughly twice the median spacing. Overestimating the
    spacing then makes the s2 element too large, which merges rings - the
    failure that lost 98% of a 480-ring count. A crude run count is biased the
    safe way instead: it can over-count and underestimate the spacing, and a
    small element never destroys structure.
    """
    sizes = {"s1": 1, "s2": 1, "blackhat": max(3, round(0.8 * px_per_mm))}
    mor = morphology_process(sub, px_per_mm, sizes)
    bh = grey_closing(mor, sizes["blackhat"]) - mor
    mask = bh > otsu(bh)
    if not mask.any():
        return 0.0
    signal = mask.mean(axis=1)
    lab, n = ndimage.label(signal > 0.5)
    if n < 3:
        return 0.0
    centres = np.array([np.where(lab == i)[0].mean() for i in range(1, n + 1)])
    return float(np.median(np.diff(centres)))


def element_sizes(px_per_mm: float, spacing_px: float = 0.0,
                  s2_mm: Optional[float] = None,
                  blackhat_mm: Optional[float] = None) -> dict:
    """Structuring-element sizes, defaulting to the reference physical
    constants but never larger than the structure being measured.

    The reference constants assume mature rings. On a core with 0.27 mm rings
    the 0.3175 mm opening is wider than the earlywood it is meant to protect,
    and it erases the ring structure entirely - measured as 22 bands detected
    where 480 were present. So each element is additionally capped at a
    fraction of the measured ring spacing.
    """
    s1 = SE_OPENING_MM * px_per_mm
    s2 = (SE_CLOSING_MM if s2_mm is None else s2_mm) * px_per_mm
    bh = (SE_BLACKHAT_MM if blackhat_mm is None else blackhat_mm) * px_per_mm
    if spacing_px > 0:
        s1 = min(s1, 0.09 * spacing_px)
        s2 = min(s2, 0.45 * spacing_px)
        # the 2.54 mm black-hat suits mature rings; on a 0.27 mm core it spans
        # nine ring widths, so bh becomes a smooth background estimate and
        # adjacent rings merge (measured: 344 bands where 480 were present).
        # Keep it between 1.6 and 4 ring spacings.
        bh = min(max(bh, 1.6 * spacing_px), 4.0 * spacing_px)
    return {"s1": max(1, round(s1)), "s2": max(1, round(s2)),
            "blackhat": max(3, round(bh))}


def morphology_process(img: np.ndarray, px_per_mm: float,
                       sizes: Optional[dict] = None) -> np.ndarray:
    """opening(s1) -> closing(s1) -> opening(s2), exactly as the reference does:

        erode(s1); dilate(s1); dilate(s1); erode(s1); erode(s2); dilate(s2)

    i.e. opening with the small element, closing with it again to restore the
    dark specks, then opening with the larger element to remove light strips
    narrower than 0.3175 mm from the latewood. Note the second operation is a
    *closing* with s1 and the third an *opening* with s2; using a closing with
    s2 instead fills in rings up to 0.32 mm wide and merges narrow ones.
    """
    if sizes is None:
        s1 = max(1, round(SE_OPENING_MM * px_per_mm))
        s2 = max(1, round(SE_CLOSING_MM * px_per_mm))
    else:
        s1, s2 = int(sizes["s1"]), int(sizes["s2"])
    img = grey_opening(img, s1)
    img = grey_closing(img, s1)
    img = grey_opening(img, s2)
    return img


def otsu(values: np.ndarray, nbins: int = 256) -> float:
    """Otsu's threshold. Returns the intensity that maximises between-class
    variance. Implemented locally to avoid an OpenCV/skimage dependency."""
    v = np.asarray(values, dtype=np.float64).ravel()
    lo, hi = float(v.min()), float(v.max())
    if hi <= lo:
        return hi
    hist, edges = np.histogram(v, bins=nbins, range=(lo, hi))
    p = hist.astype(np.float64) / max(1, hist.sum())
    centres = (edges[:-1] + edges[1:]) / 2.0
    w0 = np.cumsum(p)
    w1 = 1.0 - w0
    csum = np.cumsum(p * centres)
    total = csum[-1]
    with np.errstate(invalid="ignore", divide="ignore"):
        m0 = csum / np.maximum(w0, 1e-12)
        between = w0 * w1 * (m0 - total) ** 2
    between[~np.isfinite(between)] = 0.0
    return float(centres[int(np.argmax(between))])


@dataclass
class RingBand:
    """One detected ring. The rectangle spans the band's real extent so a
    reviewer can see exactly what the detector used."""
    start_px: int          # along the core axis
    end_px: int
    centre_px: float
    width_px: float
    width_mm: float
    cross_lo: int          # extent across the core, in strip columns
    cross_hi: int
    strength: float = 0.0  # mean black-hat response inside the band

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DetectionResult:
    count: int
    bands: list[RingBand] = field(default_factory=list)
    px_per_mm: float = 0.0
    spacing_px_median: float = 0.0
    spacing_mm_median: float = 0.0
    px_per_ring: float = 0.0
    contrast: float = 0.0
    resolved: bool = False
    warnings: list[str] = field(default_factory=list)
    confidence: Optional[int] = None
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["bands"] = [b.to_dict() for b in self.bands]
        return d


def _band_runs(signal: np.ndarray, mask2d: np.ndarray, threshold: float,
               min_run_px: int) -> list[tuple[int, int, float]]:
    """Runs where the ring mask covers more than `threshold` of the band."""
    is_ring = signal > threshold
    if not is_ring.any():
        return []
    lab, n = ndimage.label(is_ring)
    out = []
    for i in range(1, n + 1):
        idx = np.where(lab == i)[0]
        a, b = int(idx[0]), int(idx[-1])
        if b - a + 1 < min_run_px:
            continue                      # speck, not a ring
        strength = float(signal[a:b + 1].mean())
        out.append((a, b, strength))
    return out


def _rect_for_band(mask2d: np.ndarray, a: int, b: int) -> tuple[int, int]:
    """Cross-axis extent of the ring's mask pixels between rows a and b."""
    cols = np.where(mask2d[a:b + 1].any(axis=0))[0]
    if cols.size == 0:
        return 0, mask2d.shape[1] - 1
    return int(cols[0]), int(cols[-1])


def detect_path(strip: np.ndarray, px_per_mm: float, col_lo: int, col_hi: int,
                blackhat_mm: Optional[float] = None,
                otsu_adjust: float = 1.0,
                band_cover: float = 0.5,
                min_ring_mm: float = MIN_RING_MM,
                sizes: Optional[dict] = None,
                s2_mm: Optional[float] = None,
                auto_scale: bool = False) -> tuple[list[RingBand], dict]:
    """Detect ring bands on one path (one cross-axis band of the strip).

    `strip` is oriented so the core runs along axis 0. Returns bands plus
    diagnostics used to decide whether the result is trustworthy.
    """
    sub = strip[:, col_lo:col_hi]
    if sub.shape[0] < 8 or sub.shape[1] < 3:
        return [], {"contrast": 0.0, "otsu": 0.0, "cover": 0.0}

    if sizes is None:
        spacing_px = 0.0
        if auto_scale:
            # opt-in: crude first count -> measured spacing -> element sizes.
            # Off by default: the bootstrap over-counts on noisy surfaces and
            # the resulting undersized elements then over-count badly (150
            # rings read as 397), so the reference constants are safer.
            spacing_px = bootstrap_spacing_px(sub, px_per_mm)
            if spacing_px <= 0:
                spacing_px = estimate_spacing_px(sub.mean(axis=1))
        sizes = element_sizes(px_per_mm, spacing_px,
                              s2_mm=s2_mm, blackhat_mm=blackhat_mm)
    else:
        spacing_px = 0.0

    mor = morphology_process(sub, px_per_mm, sizes)
    if blackhat_mm is None:
        bh_size = int(sizes["blackhat"])
    else:
        bh_size = max(3, round(blackhat_mm * px_per_mm))
    bh = grey_closing(mor, bh_size) - mor
    thr = otsu(bh) * otsu_adjust
    mask = bh > thr
    if not mask.any():
        return [], {"contrast": 0.0, "otsu": float(thr), "cover": 0.0,
                    "sizes": dict(sizes)}

    signal = mask.mean(axis=1)                 # ring coverage along the core
    min_run_px = max(1, round(min_ring_mm * px_per_mm))
    runs = _band_runs(signal, mask, band_cover, min_run_px)

    bands: list[RingBand] = []
    for a, b, strength in runs:
        clo, chi = _rect_for_band(mask, a, b)
        width_px = float(b - a + 1)
        bands.append(RingBand(
            start_px=a, end_px=b, centre_px=0.5 * (a + b),
            width_px=width_px, width_mm=width_px / px_per_mm,
            cross_lo=clo + col_lo, cross_hi=chi + col_lo,
            strength=round(strength, 4),
        ))

    in_band = signal[[i for i in range(len(signal)) if signal[i] > band_cover]]
    out_band = signal[signal <= band_cover]
    contrast = float(in_band.mean() - out_band.mean()) if in_band.size and out_band.size else 0.0
    diag = {"contrast": round(contrast, 4), "otsu": round(float(thr), 3),
            "cover": round(float(mask.mean()), 4),
            "s1": sizes["s1"], "s2": sizes["s2"], "blackhat": bh_size,
            "est_spacing_px": round(float(spacing_px), 2)}
    return bands, diag


def incline_correct(upper: list[RingBand], lower: list[RingBand],
                    separation_px: float) -> Optional[np.ndarray]:
    """Ring widths corrected for inclined boundaries.

    A boundary inclined to the core axis appears displaced by dx between two
    paths separated by `separation_px`; the true width is the mean observed
    width scaled by cos(atan(dx / separation)). Requires matching ring counts.
    """
    if len(upper) != len(lower) or len(upper) < 2 or separation_px <= 0:
        return None
    w_up = np.diff([b.centre_px for b in upper])
    w_lo = np.diff([b.centre_px for b in lower])
    dx = np.abs(np.array([b.centre_px for b in lower])
                - np.array([b.centre_px for b in upper]))[:-1]
    return (w_up + w_lo) / 2.0 * np.cos(np.arctan2(dx, separation_px))


def _need_ring_width(strip: np.ndarray, px_per_mm: float, col_lo: int,
                     col_hi: int) -> DetectionResult:
    """Unresolved result asking for the one input the image cannot supply."""
    sub = strip[:, col_lo:col_hi]
    probe = detect(strip, px_per_mm, col_lo, col_hi)
    res = DetectionResult(
        count=0, bands=[], px_per_mm=float(px_per_mm),
        spacing_px_median=0.0, spacing_mm_median=0.0, px_per_ring=0.0,
        contrast=probe.contrast, resolved=False, confidence=None,
        params={"needs_ring_width": True,
                "core_length_px": int(sub.shape[0]),
                "core_length_mm": round(sub.shape[0] / px_per_mm, 1) if px_per_mm else 0,
                "probe_spacing_mm": probe.spacing_mm_median},
    )
    res.warnings.append(
        "not_resolved: ring width required. Enter the expected ring count for "
        "this core (or the ring width in mm) so the pre-filter can be sized. "
        "The image alone cannot distinguish real rings from every 2nd or 3rd "
        "ring of a finer series - both are equally periodic.")
    return res


def detect_stable(strip: np.ndarray, px_per_mm: float, col_lo: int,
                  col_hi: int, ring_width_mm: Optional[float] = None,
                  second_path_mm: float = 0.0,
                  otsu_adjust: float = 1.0,
                  band_cover: float = 0.5) -> DetectionResult:
    """Detect with a ladder of pre-filter scales and report stability.

    A single fixed pre-filter scale hides how much the answer depends on a
    tuning constant. Measured on synthetic 0.27 mm rings, the count swung
    263 -> 436 as the s2 element went 0.15 -> 0.07 mm. So instead of asserting a
    confidence, vary the scale around the estimated (or user-supplied) ring
    width and use the *spread of the resulting counts* as the uncertainty. A
    core whose rings are genuinely well resolved gives the same answer at every
    scale; one that is not gives a wide spread and is reported as unresolved.
    """
    if ring_width_mm is None or ring_width_mm <= 0:
        # No ring width, no count. Nothing in the image can distinguish "true
        # 0.59 mm rings" from "every third of a 0.2 mm series": both look
        # equally periodic, and no stability test separates them because both
        # are stable. Measured on the mountain hemlock, letting the engine infer
        # the width gave 1.44 mm where the truth was 0.59 mm, and a count of
        # 80 against 262. The forester knows roughly how old the tree is, so
        # that estimate is an input, not something to guess.
        return _need_ring_width(strip, px_per_mm, col_lo, col_hi)

    ladder_counts: list[int] = []
    best: Optional[DetectionResult] = None
    for s2_mult, bh_mult in LADDER:
        r = detect(strip, px_per_mm, col_lo, col_hi,
                   second_path_mm=second_path_mm,
                   s2_mm=s2_mult * ring_width_mm,
                   blackhat_mm=bh_mult * ring_width_mm,
                   otsu_adjust=otsu_adjust, band_cover=band_cover)
        if r.count >= 2:
            ladder_counts.append(r.count)
            if best is None or r.contrast > best.contrast:
                best = r
    if best is None:
        best = detect(strip, px_per_mm, col_lo, col_hi,
                      s2_mm=0.45 * ring_width_mm, blackhat_mm=blackhat_mm)

    res = best
    res.params["ring_width_mm"] = round(float(ring_width_mm), 4)
    res.params["ladder"] = [{"s2_mm": round(a * ring_width_mm, 4),
                             "bh_mm": round(b * ring_width_mm, 4)}
                            for a, b in LADDER]
    res.params["ladder_counts"] = list(ladder_counts)

    if ladder_counts:
        arr = np.array(sorted(ladder_counts), dtype=float)
        lo, hi = int(arr.min()), int(arr.max())
        median = int(round(float(np.median(arr))))
        res.count = median
        # robust spread: p80 - p20, so one wild grid corner cannot define the
        # uncertainty, but a genuinely unstable result still shows it
        res.confidence = max(0, min(99, int(round(
            float(np.percentile(arr, 80) - np.percentile(arr, 20))))))
        res.params["ladder_min"], res.params["ladder_max"] = lo, hi
        # stable means most of the ladder agreed within a couple of rings
        agree = sum(1 for c in ladder_counts if abs(c - median) <= 2)
        res.resolved = agree >= max(3, len(ladder_counts) - 1) and median >= 2
        res.warnings = [w for w in res.warnings if not w.startswith("not_resolved")]
        if not res.resolved:
            res.warnings.append(
                f"not_resolved: count varies {lo}-{hi} with the pre-filter "
                f"scale, so the ring spacing is not reliably determined. "
                f"Try a closer photo, raking light, or set the ring width "
                f"manually (currently {ring_width_mm:.3f} mm).")
        elif res.confidence > 25:
            res.warnings.append(
                f"pre-filter sensitivity {res.confidence} rings "
                f"({lo}-{hi}); treat the count as provisional.")
    return res


def detect(strip: np.ndarray, px_per_mm: float, col_lo: int, col_hi: int,
           second_path_mm: float = 0.0,
           blackhat_mm: Optional[float] = None,
           s2_mm: Optional[float] = None,
           auto_scale: bool = False,
           otsu_adjust: float = 1.0,
           band_cover: float = 0.5,
           min_ring_mm: float = MIN_RING_MM) -> DetectionResult:
    """Full detection on a core strip, with the confidence model.

    strip        grey image, oriented with the core along axis 0
    col_lo/hi    the cross-axis band to sample (one path)
    second_path_mm  if > 0, run a second path this far away and correct widths
    """
    bands, diag = detect_path(strip, px_per_mm, col_lo, col_hi,
                              blackhat_mm, otsu_adjust, band_cover, min_ring_mm,
                              s2_mm=s2_mm, auto_scale=auto_scale)
    sizes = {k: diag.get(k) for k in ("s1", "s2", "blackhat")
             if diag.get(k)}

    corrected = None
    if second_path_mm > 0:
        sep_px = second_path_mm * px_per_mm
        c2_lo = int(round(col_lo + sep_px))
        c2_hi = int(round(col_hi + sep_px))
        if c2_hi <= strip.shape[1]:
            bands2, _ = detect_path(strip, px_per_mm, c2_lo, c2_hi,
                                    blackhat_mm, otsu_adjust, band_cover,
                                    min_ring_mm, sizes=sizes or None,
                                    auto_scale=auto_scale)
            corrected = incline_correct(bands, bands2, sep_px)
            if corrected is not None and len(corrected) == len(bands) - 1:
                for i, w in enumerate(corrected):
                    if px_per_mm > 0:
                        bands[i].width_px = float(w)
                        bands[i].width_mm = float(w) / px_per_mm

    spacing = np.diff([b.centre_px for b in bands]) if len(bands) > 1 else np.array([])
    spacing_med = float(np.median(spacing)) if spacing.size else 0.0
    spacing_mm = spacing_med / px_per_mm if px_per_mm > 0 else 0.0

    res = DetectionResult(
        count=len(bands), bands=bands, px_per_mm=float(px_per_mm),
        spacing_px_median=round(spacing_med, 2),
        spacing_mm_median=round(spacing_mm, 4),
        px_per_ring=round(spacing_med, 2),
        contrast=diag.get("contrast", 0.0),
        params={"otsu_adjust": otsu_adjust, "band_cover": band_cover,
                "min_ring_mm": min_ring_mm, "s2_mm": s2_mm,
                "blackhat_mm": blackhat_mm, "auto_scale": auto_scale,
                "second_path_mm": second_path_mm, "col_lo": col_lo,
                "col_hi": col_hi, **{k: v for k, v in diag.items()}},
    )
    res.warnings.extend(_assess(res))
    res.resolved = not any(w.startswith("not_resolved") for w in res.warnings)
    if res.resolved:
        res.confidence = max(2, min(99, round(
            SINGLE_PHOTO_UNCERTAINTY_PCT / 100.0 * res.count)))
    return res


def _assess(res: DetectionResult) -> list[str]:
    """Resolvability gate. Refusing to count is a feature: this app's output is
    the field record, and a confident wrong number corrupts it silently."""
    warn: list[str] = []
    if res.count < 2:
        warn.append("not_resolved: fewer than 2 ring bands detected")
        return warn
    if res.px_per_ring < MIN_PX_PER_RING:
        warn.append(
            f"not_resolved: only {res.px_per_ring:.1f} px per ring "
            f"(need >= {MIN_PX_PER_RING:.0f}). Move closer or use a better "
            f"camera. At {res.px_per_mm:.1f} px/mm a {MIN_RING_MM:.2f} mm ring "
            f"is only {MIN_RING_MM * res.px_per_mm:.1f} px.")
    if res.contrast < 0.08:
        warn.append(
            f"weak signal: ring/earlywood contrast {res.contrast:.3f}. "
            f"The surface may not be sanded, or lighting is flat.")
    if res.spacing_mm_median and res.spacing_mm_median > 8.0:
        warn.append(
            f"unusually wide: median ring {res.spacing_mm_median:.1f} mm. "
            f"Check the px/mm calibration.")
    return warn


def combine_session(counts: list[int],
                    resolved: list[bool]) -> tuple[int, int, int, str]:
    """Combine repeat photos of one core.

    Returns (count, confidence, n_photos, status). The confidence is the measured
    spread between photos, in years, not a heuristic: on the mountain hemlock
    three comparable framings gave 261/263/261, while the Abies core on the
    Pixel gave 187-283. That spread is the uncertainty.
    """
    usable = [c for c, r in zip(counts, resolved) if r and c >= 2]
    if not usable:
        return 0, 0, len(counts), "unresolved"
    median = int(round(float(np.median(usable))))
    confidence = max(0, min(99, max(usable) - min(usable)))
    status = "counted" if all(resolved) else "partial"
    return median, confidence, len(counts), status


def spacing_from_centres(centres, min_gap: float = 4.0,
                         max_gap: float = 400.0,
                         bin_px: float = 1.0) -> float:
    """Dominant ring spacing in px, from the distances between band centres.

    Why not the spectrum. `estimate_spacing_px` assumes one period for the whole
    core, and that is false here: measured along the reference core the ring
    width runs 1.13 mm at the pith, down to 0.53 mm, and back to 0.68 mm at the
    bark - a factor of two inside one core. A single FFT of a 3260-sample
    profile also puts its largest peak at the residual lighting drift, which is
    how it came to answer 326 px (about fourteen ring widths) on a core whose
    rings are 23 px apart.

    Pooling the distances between consecutive band centres instead does not
    assume stationarity. Every ring pair votes, the histogram accumulates a peak
    at the dominant local spacing, and the width of that peak reports the spread
    directly rather than hiding it.

    Returns 0.0 when fewer than three centres are available.
    """
    c = np.sort(np.asarray(centres, dtype=np.float64))
    c = c[np.isfinite(c)]
    if c.size < 3:
        return 0.0
    gaps = np.diff(c)
    gaps = gaps[(gaps >= min_gap) & (gaps <= max_gap)]
    if gaps.size < 2:
        return 0.0
    # a repeated ring can hide one or two periods inside one gap, so fold the
    # large gaps down before histogramming rather than discarding them
    hist, edges = np.histogram(gaps, bins=int((max_gap - min_gap) / bin_px) + 1,
                               range=(min_gap, max_gap))
    if hist.sum() < 2:
        return 0.0
    # smooth a little: the peak is several bins wide by construction
    k = np.ones(3)
    sm = np.convolve(hist.astype(float), k, mode="same")
    i = int(np.argmax(sm))
    if sm[i] <= 0:
        return 0.0
    # take the centroid of the peak, not its leading bin
    lo = max(0, i - 2)
    hi = min(len(sm), i + 3)
    w = sm[lo:hi]
    if w.sum() <= 0:
        return float(edges[i])
    centres_bin = 0.5 * (edges[:-1] + edges[1:])
    return float((centres_bin[lo:hi] * w).sum() / w.sum())


def drift_corrected_profile(prof: np.ndarray, window: Optional[int] = None,
                            min_run_px: float = 3.0) -> np.ndarray:
    """Ring profile with its own slow lighting drift removed.

    Why. Every count in this session moved with the threshold, and the reason
    turned out not to be the rings. A point light close to the core puts a hot
    spot on it, so brightness slides along the length; on a threshold taken at a
    fixed percentile of the whole profile that drift decides what gets called
    "dark". Measured on the reference frames, moving the cut from the 20th to
    the 30th percentile changed the answer from 48 bands to 5, then back to 47
    at the 40th - the wood was clipping to white over part of its length.

    Subtracting a running median taken over several ring periods removes the
    drift while leaving the rings, because a median that spans a few rings
    follows the lighting and not the bands. A mean would not do: it would drag
    the baseline toward whichever rings are darkest, which is the same coupling
    in the other direction.

    `window` defaults to a fifth of the profile, which is wide enough to span a
    run of rings on these cores and narrow enough to follow the lighting.
    """
    x = np.asarray(prof, dtype=np.float64)
    if x.size < 12:
        return x - x.mean() if x.size else x
    w = window or max(15, x.size // 5)
    w = int(min(max(w, 5), x.size))
    base = ndimage.median_filter(x, size=w, mode="nearest")
    return x - base


def count_bands(prof: np.ndarray, x: np.ndarray, frac: float = 0.25,
                window: Optional[int] = None, min_run_px: float = 3.0
                ) -> tuple:
    """Count latewood bands on a drift-corrected profile.

    Returns (count, centres_px, threshold). The threshold is on the *corrected*
    profile, so it means the same thing everywhere along the core.
    """
    corr = drift_corrected_profile(prof, window)
    s = np.std(corr)
    if s < 1e-9:
        return 0, [], 0.0
    thr = -frac * s
    dark = corr < thr
    runs = 0
    centres = []
    x = np.asarray(x, dtype=np.float64)
    i = 0
    n = dark.size
    while i < n:
        if dark[i]:
            j = i
            while j < n and dark[j]:
                j += 1
            if x[j - 1] - x[i] >= min_run_px:
                runs += 1
                centres.append(float(0.5 * (x[i] + x[j - 1])))
            i = j
        else:
            i += 1
    return runs, centres, float(thr)
