"""Photo in, count out: find the core, isolate it, find the rings, count them.

The four steps of the field workflow, in the order they have to happen:

1. **find the core** in the photo - the core is a warm, long, straight object
   on a background that is not (fabric, denim, a table) or a bright sheet
   (paper, clean card). Both backgrounds are handled, because the field kit
   uses both, and neither cue works on the other.
2. **rectify** it, so the core runs along axis 0 of a strip and the rings
   become a one-dimensional problem.
3. **find the ring boundaries** on that strip.
4. **count** them, and say plainly when the count is not safe to report.

This is a thin layer over parts that were measured on their own -
`engine/sausage.py` for the core object, `engine/ensemble.py` and
`engine/consolidate.py` for the boundaries - and the point of the layer is
that it does not quietly invent a scale. The 5 mm core diameter is used as a
*ratio* check and is reported as an assumption, and every count carries the
scale it was measured with, so a later measurement can contradict it.

Nothing here raises the quality of the underlying signals; the honest limits
are the ones already recorded in `docs/DESIGN.md` and in the module
docstrings these parts were written with.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from engine import annotate as A
from engine import chain as CH
from engine import consolidate as C
from engine import detect as D
from engine import ensemble as E
from engine import sausage as S

# The core is 5 mm across. Used as a *ratio* and reported as an assumption:
# a 5 mm borer core photographed at 15 px/mm gives 75 px of width.
CORE_DIA_MM = 5.0

# Shape gate: a core is much longer than it is wide. Below this it is a
# fragment, a sleeve, a rule or a shadow.
MIN_ASPECT = 12.0
# The forester's floor for a core worth counting.
MIN_LENGTH_MM = 30.0

# Working scale for the core search. The full-resolution image is used for
# the ring work, because ring detail is the thing that gets lost.
WORK_SCALE = 3

# Ring-chain parameters, tuned on the four real cores with known counts
# (382, 592, D71_7984, PXL_ACT_327) and checked on the marked fixture.
#
# The NMS window is a fraction of the ring pitch: it decides how many
# candidates per ring survive to the chain. Too small and the sub-features of
# one ring all survive; too large and real rings are suppressed. Measured:
# 0.25-0.45 of the pitch is the flat part of the curve.
CH_NMS_FRACTION = 0.35
# How much of the last measured spacing the chain carries forward. Zero is a
# fixed pitch, which the design notes call the wrong model: ring width varies
# by a factor of two inside one core.
CH_BETA = 0.4


# --------------------------------------------------------------------- core

@dataclass
class Core:
    """A core found in a photo, in full-resolution image coordinates."""

    source: str                       # "paper" | "fabric"
    top: dict[int, float] = field(default_factory=dict)   # x -> upper edge y
    bot: dict[int, float] = field(default_factory=dict)   # x -> lower edge y
    axis_a: tuple[float, float] = (0.0, 0.0)              # pith end
    axis_b: tuple[float, float] = (0.0, 0.0)              # bark end
    width_px: float = 0.0
    length_px: int = 0
    px_per_mm: float = 0.0            # from the 5 mm assumption
    score: float = 0.0
    n_candidates: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "axis_a": list(self.axis_a),
            "axis_b": list(self.axis_b),
            "width_px": round(self.width_px, 1),
            "length_px": self.length_px,
            "px_per_mm_assumed": round(self.px_per_mm, 3),
            "score": round(self.score, 4),
            "n_candidates": self.n_candidates,
            "notes": list(self.notes),
        }


def _local_std(img: np.ndarray, win: int) -> np.ndarray:
    """Local standard deviation, computed with box filters.

    `generic_filter` with a 25x25 window costs hundreds of millions of
    operations on a phone-sized image, which is minutes per photo. Two box
    filters give the same thing in linear time.
    """
    m = ndimage.uniform_filter(img, win)
    m2 = ndimage.uniform_filter(img * img, win)
    return np.sqrt(np.maximum(m2 - m * m, 0.0))


def _paper_mask(lum: np.ndarray, cut_frac: float = 0.04,
                tex_win: int = 25) -> tuple[np.ndarray, float, float]:
    """Mask the core on a light background.

    Measured on clean card: the sheet sits at 192.9 with sd 4.3, the core at
    120.7 with sd 29.5 - a separation of d' = 3.4. A cut a few percent below
    the sheet's own median keeps 91% of the paper and 0.7% of the core, so the
    segmentation is essentially one threshold.

    On a dark fabric the margins are dark, the "paper level" is low, the cut
    lands below almost everything and the mask comes back empty. That is the
    desired behaviour: the paper path must not fire on a fabric photo.
    """
    h = lum.shape[0]
    m = max(4, int(h * 0.06))
    margins = np.concatenate([lum[:m, :].ravel(), lum[-m:, :].ravel()])
    paper = float(np.median(margins))
    cut = paper * (1.0 - cut_frac)
    mask = lum < cut
    tex = _local_std(lum, tex_win)
    paper_tex = float(np.median(tex[:max(4, h // 12), :]))
    if paper_tex < 1.0 and paper > 60.0:
        # a clean sheet: the wood really is the textured part
        mask &= tex > paper_tex * 1.6
    mask = ndimage.binary_opening(mask, np.ones((9, 25)))
    mask = ndimage.binary_closing(mask, np.ones((25, 25)))
    return mask, paper, paper_tex


def _fabric_mask(rgb_small: np.ndarray) -> tuple[np.ndarray, float]:
    """Mask the core on a dark or coloured background, by warmth.

    Red minus blue, normalised by brightness, is the cue that separates wood
    from denim, a table or a plastic sleeve. It is not used on white paper:
    paper is neutral, so there it reads warm and the mask swallows the sheet.
    """
    r, g, b = rgb_small[..., 0], rgb_small[..., 1], rgb_small[..., 2]
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    warmth = (r - b) / (lum + 20.0)
    thr = S.auto_thr(warmth)
    mask = warmth > thr
    return mask, float(thr)


def _smooth_contour(d: dict[int, float], win: int = 9) -> dict[int, float]:
    """Running median along the core, which suppresses edge texture.

    The wood/background boundary is coherent along the core while the
    background's own texture is not, so a median along the core's direction
    keeps the edge and removes the fabric weave.
    """
    if len(d) < win:
        return dict(d)
    xs = np.array(sorted(d), float)
    v = np.array([d[int(x)] for x in xs], float)
    pad = np.pad(v, (win // 2, win // 2), mode="edge")
    win_v = np.lib.stride_tricks.sliding_window_view(pad, win)
    med = np.median(win_v, axis=1)
    return {int(x): float(m) for x, m in zip(xs, med)}


def _is_highlighter(rgb: np.ndarray, mk: np.ndarray) -> bool:
    """True for a fluorescent-yellow marker lane, which is not a core.

    The photo card draws a highlighted lane over the ruler. It is long, warm
    and straight, so it wins the shape score against the core underneath it -
    and a strip of marker ink has no rings at all. Yellow ink is much brighter
    than any wood in these photographs and has its green channel close to red
    with little blue (measured 252,233,152; the cores are 204,175,145 at the
    palest and 132,123,125 at the greyest).
    """
    px = rgb[mk]
    r, g, b = (float(v) for v in px.mean(axis=0))
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    return lum > 210.0 and g > 0.88 * r and b < 0.72 * r


def _candidates_from_mask(mask: np.ndarray,
                          rgb: Optional[np.ndarray] = None) -> list[S.Candidate]:
    lab, n = S.label(mask)
    out = []
    for i in range(1, n + 1):
        mk = lab == i
        if mk.sum() < 150:
            continue
        if rgb is not None and _is_highlighter(rgb, mk):
            continue
        c = S.Candidate(mk)
        if c.measure() and c.aspect >= 6.0:
            c.score_it()
            if c.score > 0:
                out.append(c)
    return out


def find_core(rgb: np.ndarray, work_scale: int = WORK_SCALE,
              crop: float = 0.20) -> tuple[Optional[Core], list[dict]]:
    """Find the core in a photo. Returns (core, all candidates).

    Both background paths are tried and the best-shaped object wins, because
    which cue works depends on the background and not on the core.
    """
    H0, W0 = rgb.shape[:2]
    small_im = Image.fromarray(rgb.astype(np.uint8)).resize(
        (max(1, W0 // work_scale), max(1, H0 // work_scale)), Image.LANCZOS)
    small = np.asarray(small_im, dtype=np.float64)
    lum = 0.299 * small[..., 0] + 0.587 * small[..., 1] + 0.114 * small[..., 2]

    found: list[tuple[str, S.Candidate, list[S.Candidate]]] = []

    # --- fabric path -------------------------------------------------------
    try:
        mask_f, thr = _fabric_mask(small)
        # centre crop, so clutter at the frame edge cannot bridge two objects
        h, w = mask_f.shape
        m = np.zeros_like(mask_f)
        m[int(h * crop):int(h * (1 - crop))] = mask_f[int(h * crop):int(h * (1 - crop))]
        m = ndimage.binary_opening(m, np.ones((5, 9)))
        cands_f = _candidates_from_mask(m, small)
        if cands_f:
            found.append(("fabric", max(cands_f, key=lambda c: c.score), cands_f))
    except Exception as exc:                                # pragma: no cover
        found.append(("fabric", None, []))                  # type: ignore[arg-type]
        _ = exc

    # --- paper path --------------------------------------------------------
    try:
        mask_p, paper, paper_tex = _paper_mask(lum)
        cands_p = _candidates_from_mask(mask_p, small)
        if cands_p:
            found.append(("paper", max(cands_p, key=lambda c: c.score), cands_p))
    except Exception:                                       # pragma: no cover
        found.append(("paper", None, []))                  # type: ignore[arg-type]

    all_cands = []
    for source, best, cands in found:
        for c in cands:
            all_cands.append({
                "source": source,
                "x0": c.x0, "x1": c.x1,
                "length_px": int(c.length_px * work_scale),
                "width_px": round(c.width_px * work_scale, 1),
                "aspect": round(c.aspect, 1),
                "bend": round(c.straight_px / max(c.length_px, 1), 5),
                "score": round(c.score, 4),
            })

    best_entry = None
    for source, best, cands in found:
        if best is None:
            continue
        if best.aspect < MIN_ASPECT:
            continue
        if best_entry is None or best.score > best_entry[1].score:
            best_entry = (source, best, cands)
    if best_entry is None:
        return None, all_cands

    source, cand, cands = best_entry
    top = _smooth_contour(cand.top)
    bot = _smooth_contour(cand.bot)
    if len(top) < 20:
        return None, all_cands

    xs = np.array(sorted(top), float)
    mid = np.array([(top[int(x)] + bot[int(x)]) / 2.0 for x in xs], float)
    widths = np.array([bot[int(x)] - top[int(x)] for x in xs], float)

    # The width is measured over the middle of the core: the ends taper, and a
    # 5 mm core is a nominal figure, so a tapered end would bias it low.
    lo, hi = int(len(xs) * 0.2), int(len(xs) * 0.8)
    core_w = float(np.median(widths[lo:hi])) if hi > lo else float(np.median(widths))

    # A straight axis through the middle of the object. The forester's own
    # measurements show a core that is not pressed flat wanders by about a
    # third of its width, and a straight sampling path is more stable than the
    # wandering midline.
    if xs[-1] - xs[0] < 2:
        return None, all_cands
    fit = np.polyfit(xs, mid, 1)
    x_a, x_b = float(xs[0]), float(xs[-1])
    axis_a = (x_a * work_scale, float(np.polyval(fit, x_a)) * work_scale)
    axis_b = (x_b * work_scale, float(np.polyval(fit, x_b)) * work_scale)

    core = Core(
        source=source,
        top={int(x * work_scale): float(v * work_scale) for x, v in top.items()},
        bot={int(x * work_scale): float(v * work_scale) for x, v in bot.items()},
        axis_a=axis_a,
        axis_b=axis_b,
        width_px=core_w * work_scale,
        length_px=int(round((x_b - x_a) * work_scale)),
        px_per_mm=(core_w * work_scale) / CORE_DIA_MM,
        score=cand.score,
        n_candidates=len(all_cands),
    )
    if core.length_px / max(core.px_per_mm, 1e-9) < MIN_LENGTH_MM:
        core.notes.append(
            f"core is only {core.length_px / max(core.px_per_mm, 1e-9):.0f} mm "
            f"long at the 5 mm assumption, below the {MIN_LENGTH_MM:.0f} mm "
            "the forester counts")
    core.notes.append(
        "scale is from the 5 mm core diameter, which is a nominal figure: it "
        "is a ratio check, not an absolute measurement")
    return core, all_cands


# ----------------------------------------------------------------- rectify

def rectify(rgb: np.ndarray, core: Core, band_frac: float = 0.60
            ) -> tuple[np.ndarray, A.Path]:
    """Sample the core into a strip with the core along axis 0.

    The strip is taken from the **full-resolution** image: the core search
    runs on a reduced image because that is fast, but ring detail is the thing
    that is easily lost, so the count is done on every pixel that is there.
    """
    grey = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    width_px = max(16, int(round(core.width_px * band_frac)))
    path = A.Path(a=core.axis_a, b=core.axis_b, width_px=width_px)
    strip = path.sample(grey)
    return strip, path


# ------------------------------------------------------------------ rings

def _texture_profile(strip: np.ndarray, win: int = 5) -> np.ndarray:
    """Local standard deviation along the core: the boundary signal.

    Texture rather than brightness, because the lower half of a cylindrical
    core is in self-shadow and a brightness threshold reads that shadow as
    card. On the reference conifer core every one of the forester's 24 marked
    boundaries was located by this signal to within about a millimetre.
    """
    band = strip
    if band.shape[0] < 2 * win + 2:
        return np.zeros(band.shape[0])
    out = np.zeros(band.shape[0])
    for i in range(win, band.shape[0] - win):
        out[i] = band[i - win:i + win, :].std(axis=0).mean()
    return out


def estimate_ring_px(prof: np.ndarray) -> tuple[float, float]:
    """Dominant ring pitch in px from the profile's autocorrelation.

    Returns (pitch_px, ambiguity). The ambiguity is the ratio of the second
    strongest peak to the strongest one when the second is at twice or half
    the period: that is the octave case, where "every ring" and "every second
    ring" look identical, and a count cannot be asserted.

    A single period is the wrong model for a real core - ring width varies by
    a factor of two inside one core - so this is used to *size* the detection,
    never to produce a count on its own.
    """
    x = np.asarray(prof, dtype=np.float64)
    if x.size < 32 or x.std() < 1e-9:
        return 0.0, 1.0
    trend = ndimage.uniform_filter1d(x, size=max(9, x.size // 8), mode="nearest")
    y = x - trend
    y = y - y.mean()
    y *= np.hanning(y.size)
    spec = np.abs(np.fft.rfft(y)) ** 2
    ac = np.fft.irfft(spec, n=y.size)[: y.size // 2]
    ac = ac / max(ac[0], 1e-9)
    if ac.size < 8:
        return 0.0, 1.0
    lo = 4
    hi = max(lo + 2, ac.size // 2)
    seg = ac[lo:hi]
    if seg.size == 0:
        return 0.0, 1.0
    # local maxima only, so a monotonic tail cannot be read as a peak
    peaks = [i + lo for i in range(1, seg.size - 1)
             if seg[i] > seg[i - 1] and seg[i] >= seg[i + 1] and seg[i] > 0.05]
    if not peaks:
        return 0.0, 1.0
    best = max(peaks, key=lambda i: ac[i])
    pitch = float(best)
    amb = 1.0
    for p in peaks:
        if p == best:
            continue
        r = p / pitch
        if abs(r - 2.0) < 0.15 or abs(r - 0.5) < 0.08:
            amb = max(amb, ac[p] / max(ac[best], 1e-9))
    return pitch, amb


def count_rings(strip: np.ndarray, px_per_mm: float,
                ring_width_mm: Optional[float] = None,
                expected_ring_px: Optional[float] = None) -> dict:
    """Count rings on a rectified strip, with every method's answer kept.

    Two boundary-based counters are used, because they fail in opposite
    directions and the truth is between them:

    * **consolidated** - the strongest candidates are kept and the rest merged
      into them. It is precise: on the marked conifer core it found 25
      boundaries where the forester marked 23, and it missed 3.
    * **chain** - the candidates are walked in order with a spacing that
      follows the wood. It is sensitive: it found every one of the forester's
      marks, but it also proposed 12 extra boundaries.

    The morphological ladder (what the Android app runs) and the other three
    estimators are reported alongside as cross-checks. The disagreement between
    all of them is the uncertainty, and it is shown rather than hidden.
    """
    L, W = strip.shape
    out: dict = {
        "strip_px": [int(L), int(W)],
        "px_per_mm": round(float(px_per_mm), 3),
        "methods": {},
        "warnings": [],
        "boundaries_px": [],
        "count": 0,
        "resolved": False,
    }
    if L < 32 or W < 4:
        out["warnings"].append("the core strip is too short to count")
        return out

    win = max(3, int(round(0.05 * (expected_ring_px or 30))))
    prof = _texture_profile(strip, win=win)
    pitch_px, ambiguity = estimate_ring_px(prof)
    out["estimated_ring_px"] = round(pitch_px, 1)
    out["octave_ambiguity"] = round(ambiguity, 3)

    if ring_width_mm is None and expected_ring_px is None:
        expected_ring_px = pitch_px if pitch_px > 0 else None
    if ring_width_mm is not None and expected_ring_px is None:
        expected_ring_px = ring_width_mm * px_per_mm
    if expected_ring_px is None:
        out["warnings"].append(
            "the ring width could not be estimated, so the ring period is "
            "unknown and no count can be offered")
        return out

    # --- the boundary-based counts -----------------------------------------
    # The chain is the primary counter: measured on four real cores with
    # known counts it lands within 5% where the alternatives are 35-65% out.
    # The consolidation is kept as a second opinion, because it fails in the
    # opposite direction and the two together bound the answer.
    cands = C.candidates_from_profile(prof)
    kept = CH.non_max_suppression(cands, CH_NMS_FRACTION * expected_ring_px)
    ch = CH.find_chain(kept, expected_ring_px, beta=CH_BETA)
    out["methods"]["chain"] = {
        "candidates": len(kept),
        "boundaries": len(ch.positions),
        "count": ch.count,
        "skipped": ch.skipped,
        "spacing_median_px": round(ch.spacing_median_px, 1),
        "resolved": ch.count >= 2,
    }
    out["boundaries_px"] = [round(p, 1) for p in ch.positions]

    con = C.consolidate(cands, expected_ring_px=expected_ring_px)
    n_bounds = len(con.boundaries)
    # A count is the number of *intervals* between accepted boundaries: a
    # section with one boundary has no year in it at all.
    n_intervals = max(0, n_bounds - 1)
    out["methods"]["consolidated"] = {
        "candidates": con.n_candidates,
        "kept": con.n_kept,
        "boundaries": n_bounds,
        "count": n_intervals,
        "resolved": con.resolved,
        "warnings": list(con.warnings),
    }

    out["count"] = ch.count
    out["count_low"] = min(ch.count, n_intervals)
    out["count_high"] = max(ch.count, n_intervals)
    out["resolved"] = bool(ch.count >= 2 and not con.warnings)
    for w in con.warnings:
        out["warnings"].append(w)
    if n_intervals and abs(ch.count - n_intervals) / max(ch.count, 1) > 0.2:
        out["warnings"].append(
            f"the two boundary counters differ ({ch.count} against "
            f"{n_intervals} rings); the count is provisional")

    # --- cross-checks ------------------------------------------------------
    # The morphological ladder is what the Android app runs, and it is the
    # only estimator here that reports its own stability.
    ladder = D.detect_stable(strip, px_per_mm, 0, W, ring_width_mm=ring_width_mm)
    out["methods"]["ladder"] = {
        "count": ladder.count,
        "resolved": ladder.resolved,
        "confidence": ladder.confidence,
        "ladder_counts": ladder.params.get("ladder_counts"),
    }
    res = E.run_all(strip, px_per_mm, 0, W, ring_width_mm=ring_width_mm)
    for r in res.readings:
        out["methods"][r.method] = {
            "count": r.count if r.resolved else 0,
            "resolved": r.resolved,
            "ring_mm": round(r.ring_mm, 4),
            "px_per_ring": round(r.px_per_ring, 1),
            "note": r.note,
        }
    out["warnings"].extend(res.notes[:2])

    counts = [m["count"] for m in out["methods"].values()
              if m.get("resolved") and m.get("count", 0) >= 2]
    if counts:
        out["method_spread"] = {"min": min(counts), "max": max(counts),
                                "median": int(np.median(counts))}
        spread = (max(counts) - min(counts)) / max(max(counts), 1)
        out["method_spread"]["pct"] = round(spread * 100, 1)
        if spread > 0.35:
            out["warnings"].append(
                f"the counting methods disagree by {spread*100:.0f}% "
                f"({min(counts)} to {max(counts)} rings), so the count is "
                "provisional")
            out["resolved"] = False
    if ambiguity > 0.75:
        out["warnings"].append(
            "the ring pattern repeats at about half the detected period, so "
            "the count could be twice what is reported; give the expected ring "
            "width to settle it")
    if out["resolved"] and out["count"] < 2:
        out["resolved"] = False
    return out


# ----------------------------------------------------------------- overlay

def render_overlay(rgb: np.ndarray, core: Core, path: A.Path,
                   boundaries: list[float], out_png: str,
                   label: str = "") -> str:
    """Draw the core outline and the counted boundaries on the photo."""
    im = Image.fromarray(rgb.astype(np.uint8))
    d = ImageDraw.Draw(im)
    xs = sorted(core.top)
    if len(xs) > 1:
        d.line([(x, core.top[x]) for x in xs], fill=(255, 140, 0), width=3)
        d.line([(x, core.bot[x]) for x in xs], fill=(255, 140, 0), width=3)
    half = path.width_px / 2.0
    for b in boundaries:
        # a line across the core at the boundary's position, so the proof
        # shows where each counted boundary is
        corners = path.strip_rect_to_image(b, b, 0, path.width_px)
        d.line([corners[0][0], corners[0][1], corners[3][0], corners[3][1]],
               fill=(255, 40, 40), width=5)
    if label:
        d.text((12, 12), label, fill=(0, 0, 0))
    im.save(out_png)
    return out_png


# ------------------------------------------------------------------ facade

def analyse(image_path: str, ring_width_mm: Optional[float] = None,
            band_frac: float = 0.60,
            out_png: Optional[str] = None) -> dict:
    """The whole thing on one photo. Returns a report dictionary."""
    with Image.open(image_path) as im:
        rgb = np.asarray(im.convert("RGB"), dtype=np.float64)
    core, cands = find_core(rgb)
    report: dict = {
        "image": os.path.basename(image_path),
        "size": [int(rgb.shape[1]), int(rgb.shape[0])],
        "candidates": cands,
    }
    if core is None:
        report["error"] = "no core found in this photo"
        report["resolved"] = False
        return report
    report["core"] = core.to_dict()
    strip, path = rectify(rgb, core, band_frac=band_frac)
    report["ring_count"] = count_rings(strip, core.px_per_mm, ring_width_mm)
    report["resolved"] = report["ring_count"]["resolved"]
    if out_png:
        report["overlay"] = render_overlay(
            rgb, core, path, report["ring_count"]["boundaries_px"], out_png,
            label=f"{report['ring_count']['count']} rings "
                  f"({core.source} core)")
    return report


def main(argv=None) -> int:                                 # pragma: no cover
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("image")
    ap.add_argument("--ring-width-mm", type=float, default=None,
                    help="expected ring width; settle the octave ambiguity")
    ap.add_argument("--out", default=None, help="write an overlay PNG here")
    ap.add_argument("--json", default=None, help="write the report here")
    a = ap.parse_args(argv)
    rep = analyse(a.image, a.ring_width_mm, out_png=a.out)
    print(json.dumps(rep, indent=2, default=float))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=2, default=float)
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    raise SystemExit(main())
