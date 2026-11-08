"""Run several independent ring-counting methods and let disagreement be seen.

## Why multiple methods, shown side by side

The measurements so far make the case for this rather than argue it.

Ring boundaries are not one kind of object. On a conifer they are a latewood
band; on black cottonwood they are a row of large earlywood vessels; the
strength of each signal depends on species, on the core's condition, on the
camera, and on the lighting. No single method is right for all of those, and
a program that picks one silently will produce a confident wrong number on
whatever does not suit it.

The failure this design exists to prevent is specifically:

    a plausible count, presented as authoritative, that is wrong

because a wrong count is worse than no count - it gets recorded, it gets
crossdated against, and nobody knows to distrust it.

## The four estimators

Each is deliberately built on a *different physical signal*, not a different
threshold on the same one. If they all failed together the frame would be
hopeless; if they disagree, the disagreement is itself the information.

1. **conifer_latewood** - dark latewood bands via black-hat morphology. The
   reference method, validated to 0.2-5% on synthetic conifer cores. Fails on
   diffuse-porous wood because there is no latewood band to threshold.

2. **vessel_rows** - the diffuse-porous boundary. Each ring closes with a row
   of large earlywood vessels, which is *lighter* and *higher frequency* than
   the surrounding wood, not darker and banded. Detected as peaks in local
   texture variance. This is the physically correct signal for alder, cottonwood,
   poplar, birch and maple.

3. **profile_peaks** - plain 1-D peak picking on the smoothed cross-core
   profile, with no morphology. Crude, almost no assumptions, and therefore a
   useful control: if it agrees with a fancier method, the method is probably
   not adding much.

4. **comb_fit** - fits an explicitly periodic model rather than counting
   events. Robust to a missing ring, which event counting cannot be, and it is
   the one method that can say "I found 118 boundaries, and the best-fitting
   period implies 120 rings". It fails when ring width genuinely varies, which
   on the cottonwood is 24%, so it is expected to disagree there - and that
   disagreement is worth showing.

## What the user sees

- every method's count, its own confidence, and *why* it might be wrong
- the spread between them, which is the honest uncertainty
- a recommended single figure only when the methods agree
- specific questions when they do not, rather than a bare disagreement
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Callable, Optional

import numpy as np

from engine import detect as D


# --- helpers shared by the estimators ---------------------------------------

def _profile(strip: np.ndarray, col_lo: int, col_hi: int,
             absolute: bool = False) -> np.ndarray:
    """Collapse the core across its width to one profile along its length."""
    band = strip[:, col_lo:col_hi]
    if band.size == 0:
        return np.zeros(strip.shape[0])
    if absolute:
        return band.min(axis=1)          # darkest point: latewood band
    return band.mean(axis=1)             # average across the path


def _texture_profile(strip: np.ndarray, col_lo: int, col_hi: int,
                     win: int = 5) -> np.ndarray:
    """Local standard deviation along the core: the vessel-row signal.

    Texture, not brightness, because the lower half of a cylindrical core is
    in self-shadow, and a brightness threshold reads that shadow as card. That
    mistake produced a factor-of-two error in the core diameter and had to be
    corrected before any scale was believable.
    """
    band = strip[:, col_lo:col_hi]
    if band.shape[0] < 2 * win + 2:
        return np.zeros(band.shape[0])
    out = np.zeros(band.shape[0])
    for i in range(win, band.shape[0] - win):
        out[i] = band[i - win:i + win, :].std(axis=0).mean()
    return out


def _bands_from(peaks: list[int], prof: np.ndarray, px_per_mm: float,
                col_lo: int, col_hi: int) -> list:
    """Turn peak positions into RingBand records with real extents.

    Each band spans from one peak to the next, so a reviewer looking at the
    proof image sees exactly the interval the detector counted rather than a
    hairline at an arbitrary offset.
    """
    out = []
    if len(peaks) < 2:
        return out
    for a, b in zip(peaks, peaks[1:]):
        w = float(b - a)
        seg = prof[a:b] if b > a else np.zeros(1)
        out.append(D.RingBand(
            start_px=int(a), end_px=int(b), centre_px=(a + b) / 2.0,
            width_px=w, width_mm=w / max(px_per_mm, 1e-9),
            cross_lo=int(col_lo), cross_hi=int(col_hi),
            strength=float(seg.max()) if seg.size else 0.0))
    return out


def _peak_positions(prof: np.ndarray, min_sep: int = 8,
                    rel_thresh: float = 1.15) -> list[int]:
    """Local maxima above a robust baseline, thinned by minimum separation."""
    if prof.size < 5:
        return []
    from scipy import ndimage as ndi
    sm = ndi.uniform_filter1d(prof, 3)
    base = float(np.percentile(sm, 30))
    if base <= 0:
        base = float(sm.mean()) * 0.5
    if base <= 0:
        return []
    out: list[int] = []
    i = 1
    n = len(sm)
    while i < n - 1:
        if sm[i] > base * rel_thresh and sm[i] >= sm[i - 1] and sm[i] > sm[i + 1]:
            if not out or i - out[-1] >= min_sep:
                out.append(i)
                i += max(2, min_sep)
                continue
            if sm[i] > sm[out[-1]]:
                out[-1] = i
        i += 1
    return out


# --- the estimators ---------------------------------------------------------

def est_conifer_latewood(strip: np.ndarray, px_per_mm: float, col_lo: int,
                         col_hi: int, **kw) -> D.DetectionResult:
    """Dark latewood bands, by black-hat morphology."""
    return D.detect(strip, px_per_mm, col_lo, col_hi,
                    second_path_mm=kw.get("second_path_mm", 0.0))


def est_vessel_rows(strip: np.ndarray, px_per_mm: float, col_lo: int,
                    col_hi: int, **kw) -> D.DetectionResult:
    """Vessel rows, by local texture. The diffuse-porous boundary.

    Each ring in cottonwood, alder, poplar and maple closes with a row of
    large earlywood vessels. It is a *pale, high-frequency* line, so the sign
    of the signal is opposite to a conifer band and it is measured as texture
    rather than as intensity.

    The window has to span a vessel row. A 5 px window sees a single pore and
    averages it away; on the cottonwood the rows are 10-25 px tall at 41.6
    px/mm, so the window defaults to a fraction of the ring spacing.
    """
    ring_mm = kw.get("ring_width_mm")
    if not ring_mm:
        return D.DetectionResult(
            count=0, px_per_mm=px_per_mm, resolved=False,
            warnings=["vessel_rows: needs ring width (mm) to size the "
                      "expected spacing. The image alone cannot distinguish a "
                      "vessel row from every 2nd or 3rd of a finer series."])
    sep_px = max(4, int(round(ring_mm * px_per_mm)))
    win = kw.get("tex_win") or max(5, min(25, int(round(sep_px * 0.18))))
    prof = _texture_profile(strip, col_lo, col_hi, win=int(win))
    pk = _peak_positions(prof, min_sep=max(4, sep_px // 3),
                         rel_thresh=kw.get("rel_thresh", 1.12))
    n = len(pk)
    bands = _bands_from(pk, prof, px_per_mm, col_lo, col_hi)
    contrast = float((np.percentile(prof, 95) - np.percentile(prof, 30))
                     / max(np.percentile(prof, 95), 1e-6)) if prof.size else 0.0
    resolved = n >= 3 and contrast > 0.15
    warn = []
    if not resolved:
        if n < 3:
            warn.append("vessel_rows: fewer than 3 texture peaks found")
        if contrast <= 0.15:
            warn.append(
                f"vessel_rows: boundary contrast too low ({contrast:.2f}); "
                "the core may be too soft or too fine to resolve")
    sp = np.diff(pk) if n > 1 else np.array([0.0])
    return D.DetectionResult(
        count=n, bands=bands, px_per_mm=px_per_mm,
        spacing_px_median=float(np.median(sp)) if n > 1 else 0.0,
        spacing_mm_median=float(np.median(sp) / px_per_mm) if n > 1 else 0.0,
        px_per_ring=float(np.median(sp)) if n > 1 else 0.0,
        contrast=contrast, resolved=resolved, warnings=warn,
        params={"expected_sep_px": sep_px})


def est_profile_peaks(strip: np.ndarray, px_per_mm: float, col_lo: int,
                      col_hi: int, **kw) -> D.DetectionResult:
    """Plain peak picking on the intensity profile. Deliberately naive."""
    prof = _profile(strip, col_lo, col_hi)
    pk = _peak_positions(prof, min_sep=kw.get("min_sep", 8),
                         rel_thresh=kw.get("rel_thresh", 1.10))
    n = len(pk)
    bands = _bands_from(pk, prof, px_per_mm, col_lo, col_hi)
    sp = np.diff(pk) if n > 1 else np.array([0.0])
    return D.DetectionResult(
        count=n, bands=bands, px_per_mm=px_per_mm,
        spacing_px_median=float(np.median(sp)) if n > 1 else 0.0,
        spacing_mm_median=float(np.median(sp) / px_per_mm) if n > 1 else 0.0,
        px_per_ring=float(np.median(sp)) if n > 1 else 0.0,
        resolved=n >= 3,
        warnings=[] if n >= 3 else
        ["profile_peaks: fewer than 3 peaks found"],
        params={"naive": True})


def est_comb_fit(strip: np.ndarray, px_per_mm: float, col_lo: int,
                 col_hi: int, **kw) -> D.DetectionResult:
    """Fit an explicit period, then decide whether events or period is truer.

    Counts peaks first, then tests whether a single period explains them
    better. When the two agree the count is well determined; when they differ
    by a factor near 2 or 3 the frame is an octave-ambiguity case and the
    result is reported unresolved rather than guessed.
    """
    ring_mm = kw.get("ring_width_mm")
    use_texture = kw.get("texture", True)
    prof = (_texture_profile(strip, col_lo, col_hi) if use_texture
            else _profile(strip, col_lo, col_hi, absolute=True))
    pk = _peak_positions(prof, min_sep=kw.get("min_sep", 8),
                         rel_thresh=kw.get("rel_thresh", 1.10))
    n = len(pk)
    if n < 3:
        return D.DetectionResult(
            count=0, px_per_mm=px_per_mm, resolved=False,
            warnings=["comb_fit: too few peaks to fit a period"])

    p = np.array(pk, float)
    best = None
    for period in np.arange(4.0, max(12.0, (p[-1] - p[0]) / 2.0), 0.25):
        k = (p - p[0]) / period
        kk = np.round(k)
        if np.any(kk <= 0):
            continue
        resid = np.abs(k - kk)
        inl = resid < 0.18
        if inl.sum() < 3:
            continue
        score = inl.sum() / (1.0 + 3.0 * float(resid[inl].mean()))
        if best is None or score > best[0]:
            best = (score, float(period), int(inl.sum()))
    if best is None:
        return D.DetectionResult(
            count=n, px_per_mm=px_per_mm, resolved=False,
            warnings=["comb_fit: no consistent period fits the peaks"])

    _, period, inliers = best
    n_period = int(round((p[-1] - p[0]) / period)) + 1
    warn = []
    ratio = n_period / max(n, 1)
    resolved = True
    if 1.8 <= ratio <= 2.2 or 2.8 <= ratio <= 3.2:
        resolved = False
        warn.append(
            f"comb_fit: period implies {n_period} rings but {n} events were "
            f"found (ratio {ratio:.2f}) - this is an octave ambiguity, and a "
            "count is not safe until the ring width is known")
    if inliers < 0.7 * n:
        warn.append(
            f"comb_fit: only {inliers} of {n} peaks fit one period; ring "
            "width varies, so a single-period model is a poor description")
    return D.DetectionResult(
        count=n_period, px_per_mm=px_per_mm,
        spacing_px_median=period,
        spacing_mm_median=period / px_per_mm,
        px_per_ring=period, resolved=resolved, warnings=warn,
        params={"events": n, "period_px": period, "inliers": inliers,
                "signal": "texture" if use_texture else "intensity"})


ESTIMATORS: dict[str, Callable] = {
    "conifer_latewood": est_conifer_latewood,
    "vessel_rows": est_vessel_rows,
    "profile_peaks": est_profile_peaks,
    "comb_fit": est_comb_fit,
}


# --- the comparison and the dialogue ----------------------------------------

@dataclass
class Reading:
    """One method's answer, in a form a person can be shown."""
    method: str
    count: int
    resolved: bool
    confidence: str                 # low | medium | high
    px_per_ring: float = 0.0
    ring_mm: float = 0.0
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Comparison:
    """All readings, whether they agree, and what to ask the user."""
    readings: list[Reading] = field(default_factory=list)
    recommended: Optional[int] = None
    spread_pct: float = 0.0
    agreement: str = "none"         # none | partial | strong
    questions: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["readings"] = [r.to_dict() for r in self.readings]
        return d

    def summary_line(self) -> str:
        if not self.readings:
            return "no readings"
        parts = [f"{r.method}={r.count}" if r.resolved
                 else f"{r.method}=unresolved" for r in self.readings]
        s = ", ".join(parts)
        if self.recommended is not None:
            s += f"  -> best estimate {self.recommended}"
        else:
            s += "  -> no single estimate"
        return s


def _confidence(res: D.DetectionResult) -> str:
    if not res.resolved:
        return "low"
    if res.warnings:
        return "low"
    if res.contrast and res.contrast < 0.25:
        return "medium"
    return "high"


def _octave_questions(counts: list[int]) -> list[dict]:
    """When two readings differ by ~2x or ~3x, ask rather than choose."""
    qs: list[dict] = []
    uniq = sorted(set(c for c in counts if c >= 2))
    for i, a in enumerate(uniq):
        for b in uniq[i + 1:]:
            r = b / a
            for k, why in ((2.0, "every 2nd ring counted, or every one"),
                           (3.0, "every 3rd ring counted, or every one")):
                if abs(r - k) < 0.12:
                    qs.append({
                        "id": f"octave_{a}_{b}",
                        "question": (
                            f"Two methods disagree by a factor of about {k:.0f}: "
                            f"{a} rings versus {b}. This usually means the "
                            f"boundary pattern repeats every {k:.0f} rings, so "
                            "the real answer is one of those two."),
                        "options": [a, b],
                        "why": why,
                        "severity": "high",
                    })
    return qs


def compare(results: dict[str, D.DetectionResult],
            expected_ring_mm: float | None = None) -> Comparison:
    """Turn a dict of estimator outputs into something worth reading."""
    c = Comparison()
    for name, res in results.items():
        c.readings.append(Reading(
            method=name,
            count=res.count if res.resolved else 0,
            resolved=res.resolved,
            confidence=_confidence(res),
            px_per_ring=res.px_per_ring,
            ring_mm=res.spacing_mm_median,
            note="; ".join(res.warnings[:2]),
        ))

    usable = [r for r in c.readings if r.resolved and r.count >= 2]
    counts = [r.count for r in usable]
    for r in c.readings:
        if not r.resolved and r.note:
            c.notes.append(f"{r.method}: {r.note}")

    if not usable:
        c.agreement = "none"
        c.questions.append({
            "id": "no_reading",
            "question": (
                "None of the counting methods could resolve this image. "
                "Before going further: is the core in focus, and is it "
                "filling enough of the frame?"),
            "options": ["retake the photo", "the core has no visible rings",
                        "this species is not supported yet"],
            "why": "every method declined, so the frame itself is the problem",
            "severity": "high",
        })
        return c

    if len(counts) == 1:
        c.agreement = "partial"
        c.recommended = counts[0]
        c.notes.append("only one method produced a usable count")
        c.questions.append({
            "id": "single_method",
            "question": (
                f"Only one method could count this ({counts[0]} rings). "
                "Can you see roughly that many rings yourself?"),
            "options": [f"about {counts[0]}", "clearly not", "clearly more"],
            "why": "a single reading has no cross-check",
            "severity": "medium",
        })
        return c

    lo, hi = min(counts), max(counts)
    c.spread_pct = (hi - lo) / max(hi, 1) * 100.0
    if c.spread_pct <= 5.0:
        c.agreement = "strong"
        c.recommended = int(round(float(np.median(counts))))
        c.notes.append(
            f"all {len(counts)} methods agree within {c.spread_pct:.1f}%")
        return c

    if c.spread_pct <= 20.0:
        c.agreement = "partial"
        c.recommended = int(round(float(np.median(counts))))
        c.notes.append(
            f"methods span {lo}-{hi} ({c.spread_pct:.1f}%); the median is shown "
            "but the range is the real uncertainty")
    else:
        c.agreement = "none"
        c.notes.append(
            f"methods disagree strongly: {lo}-{hi}. No single number is "
            "defensible from this image alone")

    c.questions.extend(_octave_questions(counts))

    if expected_ring_mm:
        implied = [(r.method, r.ring_mm) for r in c.readings
                   if r.resolved and r.ring_mm > 0]
        if implied:
            c.notes.append(
                "ring widths implied: "
                + ", ".join(f"{m} {v:.2f} mm" for m, v in implied)
                + f" (you indicated {expected_ring_mm:.2f} mm)")

    # a species question whenever the latewood method and the vessel method
    # disagree in a way that tracks the material rather than the arithmetic
    if "conifer_latewood" in results and "vessel_rows" in results:
        a = results["conifer_latewood"]
        b = results["vessel_rows"]
        if a.resolved and b.resolved and abs(a.count - b.count) > 0.15 * max(a.count, b.count, 1):
            c.questions.append({
                "id": "species",
                "question": (
                    f"The conifer method reads {a.count} and the vessel-row "
                    f"method reads {b.count}. Which describes the wood you "
                    "are looking at?"),
                "options": [
                    "dark bands closing each ring (conifer: fir, spruce, pine, hemlock)",
                    "a row of large pale vessels at each boundary (cottonwood, alder, poplar, maple, birch)",
                ],
                "why": "the two methods look for opposite things, so the "
                       "material decides which is even applicable",
                "severity": "high",
            })
    return c


def run_all(strip: np.ndarray, px_per_mm: float, col_lo: int, col_hi: int,
            ring_width_mm: float | None = None,
            methods: Optional[list[str]] = None,
            second_path_mm: float = 0.0) -> Comparison:
    """Run the requested estimators and compare them."""
    names = methods or list(ESTIMATORS)
    kw = {"ring_width_mm": ring_width_mm, "second_path_mm": second_path_mm}
    results: dict[str, D.DetectionResult] = {}
    for n in names:
        fn = ESTIMATORS.get(n)
        if fn is None:
            continue
        try:
            results[n] = fn(strip, px_per_mm, col_lo, col_hi, **kw)
        except Exception as e:                      # never let one kill the set
            results[n] = D.DetectionResult(
                count=0, resolved=False,
                warnings=[f"{n}: failed ({type(e).__name__}: {e})"])
    return compare(results, expected_ring_mm=ring_width_mm)
