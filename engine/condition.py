"""Make a photo usable for ring counting, whatever phone took it.

The problem this solves is measured, not hypothetical. Across the two device
classes already photographed:

    device              white balance (R-B on paper)   ring/grain anisotropy
    BV5200 class  13MP            -6.5                       0.55
    Active 8 Pro  12MP           -81.9                       0.17

Two independent problems fall out of that table.

**Uncontrolled white balance.** The Active 8 Pro runs a systematic blue cast
that also *varies* between frames (-62 to -113 R-B on the same card). Any
absolute grey threshold therefore means something different in every frame.
Worse, the frames that failed during development did so precisely here: a
"find the neutral grey strip" test rejected the entire card because on white
paper B-R was about 90.

The fix is to stop reasoning in absolute grey. Estimate the illuminant from
the card's own reference patches, or from the brightest paper in the frame
when the patches are occluded, and rescale each channel so that paper is
neutral. Chroma is then only ever compared *relative to brightness*, which is
scale-free and cast-free.

**Unknown image quality.** The two devices differ threefold in ring-to-grain
anisotropy, and the forester's own autofocus hunting means a frame can be
soft. A count from a bad frame is worse than no count, because it looks
authoritative. So every frame is measured and gated: ring contrast, focus,
exposure, and scale agreement. A frame that fails is reported as failed, with
the reason, rather than silently producing a number.

Everything here is a pure function of pixels, so it is testable without any
device, and it runs before ring detection so the detector always sees a
neutral, well-exposed image.
"""

from __future__ import annotations

import numpy as np

# --- tunables, all chosen from the measured data ---------------------------
# Anisotropy is (across-core variance) / (total variance) on the wood mask.
# Below MIN_ANISOTROPY the ring signal is buried in grain and the count is
# meaningless; above MIN_ANISOTROPY the phone class is comfortable.
MIN_ANISOTROPY = 0.12
# A 99.5th-percentile Laplacian proxy. Below this the frame is soft, which is
# the autofocus-hunting failure mode.
# Sharpness gate. The threshold is from the measurements, not from taste:
# the softest real frame that was counted measures 34 (D71_7984, a DSLR
# frame), the frames the forester rejected for autofocus hunting measure
# under 25, and a deliberately blurred frame of the synthetic core measures
# 23. So 30 catches the soft frames and leaves every real frame alone.
# The old value of 6 was below anything measured and never fired.
MIN_SHARPNESS = 30.0

# The brightest surface in a frame has to be at least this bright before the
# illuminant can be measured on it. A darker frame has no surface to measure
# it on, and the answer is reported as unknown rather than guessed.
MIN_PAPER_LEVEL = 100.0
# Paper should land near this after normalisation.
TARGET_PAPER = 232.0
# Chroma is only ever compared relative to brightness.
CHROMA_REL = 0.16
# Patches on the v3 card, page millimetres. Used when the card is visible.
GREY_PATCH_MM = (20.0, 180.0, 60.0, 14.0)     # x, y, w, h
WHITE_PATCH_MM = (66.0, 180.0, 48.0, 14.0)
# A4 page, for the printed-reference fallback path.
PAGE_W_MM, PAGE_H_MM = 210.0, 297.0


def _percentiles(x: np.ndarray) -> tuple[float, float, float]:
    return (float(np.percentile(x, 1)), float(np.percentile(x, 50)),
            float(np.percentile(x, 99)))


def channel_gains(rgb: np.ndarray) -> np.ndarray:
    """Per-channel gains that neutralise the illuminant.

    Uses the bright (paper) pixels only, and works on channel *ratios* so a
    global exposure difference cancels. Falls back to the whole frame when
    there is not enough bright paper, which happens in a tight core crop.
    """
    a = rgb.astype(np.float32)
    mx = a.max(2)
    paper = mx > np.percentile(mx, 88)
    if paper.sum() < 200:
        paper = mx > np.percentile(mx, 75)
    if paper.sum() < 100:
        paper = np.ones(mx.shape, bool)
    means = np.array([a[..., c][paper].mean() for c in range(3)], np.float32)
    if not np.all(np.isfinite(means)) or means.min() <= 1:
        return np.ones(3, np.float32)
    # Reference illuminant: equal-energy, normalised to the green channel.
    ref = means[1]
    gains = ref / means
    gains = gains / gains[1]
    # Never apply an extreme correction: a factor beyond this means the
    # estimator has locked onto something that is not paper.
    np.clip(gains, 0.55, 1.8, out=gains)
    return gains.astype(np.float32)


def neutralise(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Return (neutralised rgb, gains applied, mean paper level after)."""
    g = channel_gains(rgb)
    out = np.clip(rgb.astype(np.float32) * g, 0, 255)
    mx = out.max(2)
    paper = mx > np.percentile(mx, 88)
    level = float(mx[paper].mean()) if paper.sum() > 50 else float(mx.mean())
    return out, g, level


def to_gray(rgb: np.ndarray) -> np.ndarray:
    a = rgb.astype(np.float32)
    return 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]


def exposure(rgb: np.ndarray) -> tuple[float, float, float]:
    p1, p50, p99 = _percentiles(rgb.max(2))
    return p1, p50, p99


def sharpness(gray: np.ndarray) -> float:
    """Robust focus proxy: high percentile of the 4-neighbour gradient."""
    g = gray.astype(np.float32)
    if g.shape[0] < 8 or g.shape[1] < 8:
        return 0.0
    lap = (np.abs(np.diff(g, axis=0)[:, :-1])
           + np.abs(np.diff(g, axis=1)[:-1, :]))
    return float(np.percentile(lap, 99.5))


def wood_mask(rgb: np.ndarray, min_rb: float | None = None) -> np.ndarray:
    """Core material, by warm hue against neutral paper.

    Hue, not brightness: a blue cast changes brightness but leaves R>B on
    wood, and a blue cast on the paper side makes the test *easier*, since
    the paper's own R-B goes strongly negative.

    The R-B threshold is **adaptive**, because a fixed one fails on oxidised
    or weathered wood. A fixed test of R-B > 22 works on fresh hemlock and
    cottonwood but on greyed, degraded cores the separation falls below 20,
    the mask empties, and the frame is then refused as "no core found" - a
    silent failure that looks identical to a photo with no core in it.

    The adaptive rule keeps only the pixels that are warm *relative to this
    frame's own paper*, so it survives both a heavy colour cast and a
    desaturated core. min_rb is an absolute floor, not the target.
    """
    a = rgb.astype(np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    rb = r - b
    mx = a.max(2)
    if min_rb is None:
        paper = mx > np.percentile(mx, 88)
        if paper.sum() >= 200:
            # paper is the reference: anything warmer than it is wood
            floor = max(6.0, float(np.percentile(rb[paper], 90)) + 6.0)
        else:
            floor = 8.0
    else:
        floor = float(min_rb)
    return (rb > floor) & (r > 100.0) & (r < 225.0) & (rb < 0.45 * r)


def _nanmean_rows(a: np.ndarray, axis: int) -> np.ndarray:
    """Mean over the valid entries, dropping slices that have none.

    np.nanmean emits a RuntimeWarning for all-NaN slices *before* the caller
    can drop them, which is fatal under -W error. The counts are computed
    first so empty slices are simply never divided.
    """
    ok = np.isfinite(a)
    cnt = ok.sum(axis=axis)
    tot = np.where(ok, a, 0.0).sum(axis=axis)
    good = cnt > 0
    return tot[good] / cnt[good]


def ring_anisotropy(rgb: np.ndarray) -> tuple[float, int]:
    """Ring-to-grain contrast on the largest wood region.

    Rings run across the core, so the signal of interest is the variation of
    row means (each row is a cross-section of the core). Grain runs along the
    core and dominates the column means. The ratio of the two variances says
    how much of the structure is ring rather than texture.
    """
    from scipy import ndimage as ndi
    m = wood_mask(rgb)
    if m.sum() < 4000:
        # Retry with a floor derived from this frame's own distribution. A
        # degraded or heavily cast core can sit below the paper-relative test
        # while still being clearly the warmest thing in the frame.
        a = rgb.astype(np.float32)
        rb = a[..., 0] - a[..., 2]
        warm = np.percentile(rb, 97)
        if warm > 5.0:
            m = wood_mask(rgb, min_rb=max(4.0, warm * 0.45))
    if m.sum() < 4000:
        return 0.0, 0
    lab, n = ndi.label(ndi.binary_closing(m, np.ones((9, 9))))
    if n == 0:
        return 0.0, 0
    sizes = ndi.sum(m, lab, range(1, n + 1))
    i = int(np.argmax(sizes)) + 1
    core = lab == i
    ys, xs = np.nonzero(core)
    if len(xs) < 4000:
        return 0.0, 0
    g = to_gray(rgb)[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    cm = core[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    # erode so the core's own dark edge does not dominate the statistics
    inner = ndi.binary_erosion(cm, np.ones((15, 15)))
    if inner.sum() < 2000:
        inner = cm
    gz = np.where(inner, g, np.nan)
    # Rows/columns with no core pixels at all would produce NaN-only means.
    # Drop them explicitly rather than letting them warn and pollute the fit.
    rows = _nanmean_rows(gz, axis=1)
    cols = _nanmean_rows(gz, axis=0)
    if rows.size < 16 or cols.size < 16:
        return 0.0, 0
    vr = float(np.var(rows))
    vc = float(np.var(cols))
    if vr + vc <= 1e-6:
        return 0.0, 0
    return vc / (vr + vc), int(inner.sum())


def neutral_chroma(rgb: np.ndarray) -> float:
    """Median relative chroma of the frame. Low means grey, cast-free."""
    a = rgb.astype(np.float32)
    mx = a.max(2)
    mn = a.min(2)
    rel = np.where(mx > 40, (mx - mn) / np.maximum(mx, 1), 0.0)
    return float(np.median(rel))


def assess(rgb: np.ndarray, px_per_mm: float | None = None,
           px_per_mm_alt: float | None = None) -> dict:
    """Measure a frame and decide whether it is safe to count.

    px_per_mm_alt, when given, is a second independent scale estimate. If the
    two disagree by more than 6% the frame is not counted: that is the
    harmonic-aliasing failure, and it is exactly the case where a single
    dominant period lies about the octave. Silence there is how ring width
    gets overstated by 74%.
    """
    out = {}
    g = gains = None
    p1, p50, p99 = exposure(rgb)
    out["exposure_p1_p50_p99"] = (p1, p50, p99)
    out["cast_rb_raw"] = float(_rb_on_paper(rgb))

    neutral, gains, level = neutralise(rgb)
    out["gains"] = tuple(float(v) for v in gains)
    out["paper_level_after"] = level
    out["cast_rb_neutral"] = float(_rb_on_paper(neutral))

    gray = to_gray(neutral)
    out["sharpness"] = sharpness(gray)
    aniso, npx = ring_anisotropy(neutral)
    out["ring_anisotropy"] = aniso
    out["core_px"] = npx
    out["neutral_chroma"] = neutral_chroma(neutral)

    if px_per_mm:
        out["px_per_mm"] = float(px_per_mm)
    warnings = []
    if np.isfinite(out["cast_rb_raw"]) and abs(out["cast_rb_raw"]) > 40:
        warnings.append(
            f"strong white-balance cast (R-B {out['cast_rb_raw']:+.0f} before "
            f"normalisation); corrected, but check exposure")
    # Judge clipping AFTER normalisation: gains can push a frame over 255.
    pn1, pn50, pn99 = exposure(neutral)
    out["exposure_neutral_p99"] = pn99
    if pn99 > 253.0 or pn1 < 3.0:
        warnings.append("highlight or shadow clipping; some rings may be lost")
    if out["sharpness"] < MIN_SHARPNESS:
        warnings.append(
            f"soft frame (sharpness {out['sharpness']:.1f} < {MIN_SHARPNESS}); "
            "likely autofocus hunting - use tap-to-focus")
    if not np.isfinite(out["cast_rb_neutral"]) and npx:
        warnings.append("no paper in frame; white balance estimated from the "
                        "core itself, so scale may be slightly off")
    if aniso < MIN_ANISOTROPY:
        if npx == 0:
            warnings.append("no core found in frame")
        else:
            warnings.append(
                f"ring signal too weak (anisotropy {aniso:.3f} < "
                f"{MIN_ANISOTROPY}); ring boundaries are not separable here")
    if px_per_mm and px_per_mm_alt:
        rel = abs(px_per_mm - px_per_mm_alt) / max(px_per_mm, 1e-9)
        out["scale_disagreement"] = float(rel)
        if rel > 0.06:
            warnings.append(
                f"scale references disagree by {rel*100:.1f}% "
                f"({px_per_mm:.2f} vs {px_per_mm_alt:.2f} px/mm) - likely an "
                "octave alias; refusing to count")

    blocking = [w for w in warnings
                if "refusing" in w or "too weak" in w or "no core" in w
                or "clipped" in w]
    out["warnings"] = warnings
    out["ok"] = not blocking
    return out


def _rb_on_paper(rgb: np.ndarray, fallback: float | None = None) -> float:
    """R-B on the brightest 12% of the frame.

    The frame may be a tight core crop with no paper in it at all. That is a
    different situation from "no cast", so it is reported as NaN rather than
    silently as 0.0, which would read as perfectly neutral.
    """
    a = rgb.astype(np.float32)
    mx = a.max(2)
    level = float(np.percentile(mx, 88))
    # `>=`, not `>`: with a strictly greater test the threshold sits exactly
    # on the paper and the paper itself is excluded, so a frame that is mostly
    # paper returned NaN - "no paper in frame" for a frame that is all paper.
    paper = mx >= level
    # A frame this dark cannot say what the illuminant was: the illuminant is
    # measured on the brightest surface in the frame, and if that surface is
    # dark there is no surface to measure it on. Reporting "unknown" is
    # honest; reporting a number from the shadow of the core is not.
    if paper.sum() < 100 or level < MIN_PAPER_LEVEL:
        return float("nan") if fallback is None else fallback
    return float(a[..., 0][paper].mean() - a[..., 2][paper].mean())
