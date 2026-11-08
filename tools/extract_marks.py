"""Find the forester's own marks in a marked photo.

The forester's marks are the only ground truth that is not a number: they are
ink on the wood, at pixel positions, and they say *where* the rings are
rather than how many there are. That is the metric an assistive tool is
judged on - did the app offer the ring the forester had already found.

The marks are drawn in red ink. Ink is separated from wood by hue (red is
strongly red-dominant; wood is not) rather than by brightness, because the
marks run over both pale earlywood and dark latewood and a brightness cut
loses the ones on the dark bands.

Two mark styles occur, and they need different grouping:

* **rectangles** (``PXL_ACT_327``): a ring is boxed with two short vertical
  strokes joined by a thin top edge. A column of the band then carries either
  ~85 ink pixels (a stroke), ~8 (the top edge) or 0, so the marks are the
  runs of ink-bearing columns.
* **slanted lines** (``646_543rings``): one stroke per ring. Slanted strokes
  sweep a wide range of columns, so dense rings merge into a single column
  run. Connected components separate them, and the forester's written
  numbers (also red, but only ~20 px tall against a ~90 px stroke) are
  rejected by height.

With a band, components are used and marks must be tall enough to span the
core; without one, the column-run method is kept as before.

Usage:
    python3 -m tools.extract_marks IMAGE --band 1386 1477
    python3 -m tools.extract_marks IMAGE --band 80 210 --min-height 45
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def red_mask(rgb: np.ndarray, min_excess: int = 45,
             min_sat: int = 55) -> np.ndarray:
    """Pixels that are ink: strongly red-dominant and saturated enough."""
    r = rgb[..., 0].astype(int)
    g = rgb[..., 1].astype(int)
    b = rgb[..., 2].astype(int)
    excess = r - np.maximum(g, b)
    sat = rgb.max(2).astype(int) - rgb.min(2).astype(int)
    return (excess > min_excess) & (sat > min_sat)


def groups_from_columns(counts: np.ndarray, min_count: int = 3,
                        gap: int = 4) -> list[tuple[int, int]]:
    """Contiguous runs of columns that carry enough ink."""
    cols = np.nonzero(counts >= min_count)[0]
    if cols.size == 0:
        return []
    out = []
    s = p = int(cols[0])
    for c in cols[1:]:
        c = int(c)
        if c - p > gap:
            out.append((s, p))
            s = c
        p = c
    out.append((s, p))
    return out


def components_from_mask(mask: np.ndarray, min_height: int,
                         min_area: int = 80) -> list[tuple[int, int, int]]:
    """One mark per connected ink component, tall enough to cross the core.

    Returns ``(x0, x1, height)`` per component. Written numbers, ticks and
    specks are short and are dropped by the height gate; a stroke that spans
    the core is kept. Densely drawn rings stay separate because the strokes
    do not touch, which column runs cannot promise.
    """
    from scipy import ndimage
    lab, n = ndimage.label(mask, structure=np.ones((3, 3), dtype=int))
    out = []
    for i, sl in enumerate(ndimage.find_objects(lab), 1):
        if sl is None:
            continue
        ys, xs = sl
        h = ys.stop - ys.start
        if h < min_height:
            continue
        if int((lab[sl] == i).sum()) < min_area:
            continue
        out.append((int(xs.start), int(xs.stop - 1), int(h)))
    out.sort()
    return out


def _otsu(v: np.ndarray) -> float:
    """Otsu's threshold, on a 1-D signal."""
    x = np.asarray(v, dtype=np.float64)
    if x.size == 0:
        return 0.0
    lo, hi = float(x.min()), float(x.max())
    if hi <= lo:
        return hi
    hist, edges = np.histogram(x, bins=256, range=(lo, hi))
    p = hist.astype(np.float64) / hist.sum()
    centres = (edges[:-1] + edges[1:]) / 2.0
    w0 = np.cumsum(p)
    w1 = 1.0 - w0
    csum = np.cumsum(p * centres)
    m0 = csum / np.maximum(w0, 1e-12)
    total = csum[-1]
    with np.errstate(invalid="ignore", divide="ignore"):
        between = w0 * w1 * (m0 - total) ** 2
    between[~np.isfinite(between)] = 0.0
    return float(centres[int(np.argmax(between))])


def _two_level_threshold(nz: np.ndarray) -> float:
    """Split a two-level signal at the widest gap between its levels.

    The ink signal here is strongly bimodal - a broad stroke covers about 85
    pixels of a column, the connecting line about 7 - and the line covers far
    more columns than the strokes do, so Otsu (which optimises the *variance*
    between classes) lands on the line. The widest gap in the sorted values
    finds the actual separation.
    """
    if nz.size < 4:
        return float(nz.max() if nz.size else 0.0)
    s = np.sort(nz)
    gaps = np.diff(s)
    i = int(np.argmax(gaps))
    if gaps[i] <= 0:
        return float(s[-1])
    return float(s[i] + gaps[i] / 2.0)


def extract(image_path: str, band: tuple[int, int] | None = None,
            min_count: int = 3, gap: int = 4,
            method: str | None = None, min_height: int | None = None) -> dict:
    """Find the ink marks and their positions.

    band        (y0, y1) rows holding the core, or None to use the whole image.
    method      "components" (default when a band is given), "columns", or
                None to pick by whether a band was supplied.
    min_height  shortest stroke counted as a mark; default 35% of the band.

    Each mark's centre is the ring position, which is what the detector is
    compared against. For components that is the bounding box centre, which is
    the same point for a rectangle and for a straight slanted line.
    """
    with Image.open(image_path) as im:
        rgb = np.asarray(im.convert("RGB"))
    H, W = rgb.shape[:2]
    y0, y1 = (0, H - 1) if band is None else band
    sub = rgb[y0:y1 + 1, :, :]
    mask = red_mask(sub)
    if method is None:
        method = "components" if band is not None else "columns"
    if method == "components":
        if min_height is None:
            min_height = max(12, int(round(0.35 * (y1 - y0 + 1))))
        comps = components_from_mask(mask, min_height)
        spans = [(a, b) for a, b, _ in comps]
        heights = [h for _, _, h in comps]
        centres = [(a + b) / 2.0 for a, b in spans]
    else:
        counts = mask.sum(0)
        spans = groups_from_columns(counts, min_count=min_count, gap=gap)
        heights = []
        centres = [(a + b) / 2.0 for a, b in spans]
    widths = [b - a + 1 for a, b in spans]
    diffs = np.diff(centres) if len(centres) > 1 else np.array([])
    return {
        "image": os.path.basename(image_path),
        "band": [y0, y1],
        "method": method,
        "min_height_px": min_height,
        "n_marks": len(spans),
        "centres_px": [round(c, 1) for c in centres],
        "spans": [list(m) for m in spans],
        "widths_px": widths,
        "heights_px": heights,
        "spacing_px": [round(float(d), 1) for d in diffs],
        "spacing_median_px": round(float(np.median(diffs)), 1) if diffs.size else 0.0,
        "spacing_min_px": round(float(diffs.min()), 1) if diffs.size else 0.0,
        "spacing_max_px": round(float(diffs.max()), 1) if diffs.size else 0.0,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--band", nargs=2, type=int, default=None,
                    metavar=("Y0", "Y1"))
    ap.add_argument("--method", choices=("components", "columns"), default=None)
    ap.add_argument("--min-height", type=int, default=None)
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    band = tuple(a.band) if a.band else None
    rep = extract(a.image, band, method=a.method, min_height=a.min_height)
    print(json.dumps(rep, indent=2))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
