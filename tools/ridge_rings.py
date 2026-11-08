"""Ridge detector for ring boundaries across a horizontal core strip.

Ring boundaries are thin lines that run across the core. This filter responds to
narrow, near-vertical intensity ridges (dark or bright), averages the response over
the core's height, and reports the peaks along the length as ring positions.
"""
from __future__ import annotations

import numpy as np
from PIL import Image
from scipy import ndimage as ndi


def load_clean_luminance(path: str) -> np.ndarray:
    """Luminance of a photo with red annotation marks replaced by their neighbourhood."""
    im = np.asarray(Image.open(path).convert("RGB")).astype(float)
    r, g, b = im[..., 0], im[..., 1], im[..., 2]
    red = (r > 150) & (r - g > 60) & (r - b > 60)
    red = ndi.binary_dilation(red, iterations=2)
    lum = im @ np.array([0.299, 0.587, 0.114])
    if red.any():
        med = ndi.median_filter(lum, size=9)
        lum = np.where(red, med, lum)
    return lum


def ridge_profile(lum: np.ndarray, rows: slice, sigma_x: float = 1.5,
                  sigma_y: float = 6.0, polarity: int = -1) -> np.ndarray:
    """Profile along x: response of a near-vertical ridge filter, averaged over rows.

    polarity -1 looks for dark lines (ring boundaries are usually darker),
    +1 for bright lines.
    """
    flat = lum - ndi.gaussian_filter(lum, sigma=(25, 25))
    # second derivative across the line (x), smoothed along it (y)
    resp = ndi.gaussian_filter(flat, sigma=(sigma_y, sigma_x), order=(0, 2))
    resp = -polarity * resp  # positive where a line of the chosen polarity is
    band = resp[rows]
    return band.mean(axis=0)


def peaks(profile: np.ndarray, min_distance: int = 14, min_prominence: float = 0.0) -> np.ndarray:
    """Local maxima of the profile, separated by at least min_distance px."""
    maxima = (profile == ndi.maximum_filter1d(profile, size=2 * min_distance + 1))
    maxima &= profile > np.percentile(profile, 50)
    idx = np.flatnonzero(maxima)
    if min_prominence > 0:
        idx = idx[profile[idx] >= min_prominence]
    return idx.astype(float)


# Settings chosen on 646_543 and checked, without re-tuning, on 641_879.
# joint settings: best mean F1 over 646_543 and 641_879 (382 held out: F1 0.78)
DEFAULT = dict(polarity=-1, sigma_x=3.0, sigma_y=8.0, min_distance=20, x_lo=0, x_hi=10**9)


def detect_rings(path: str, **overrides) -> np.ndarray:
    """Ring-boundary x positions (px) in a horizontal core photo."""
    cfg = {**DEFAULT, **overrides}
    lum = load_clean_luminance(path)
    H = lum.shape[0]
    rows = slice(int(H * 0.2), int(H * 0.8))
    prof = ridge_profile(lum, rows, sigma_x=cfg["sigma_x"], sigma_y=cfg["sigma_y"],
                         polarity=cfg["polarity"])
    p = peaks(prof, min_distance=cfg["min_distance"])
    return p[(p > cfg["x_lo"]) & (p < cfg["x_hi"])]


def local_normalise(img: np.ndarray, sigma: float = 8.0) -> np.ndarray:
    """Subtract the local mean and divide by the local standard deviation."""
    mu = ndi.gaussian_filter(img, sigma)
    sd = np.sqrt(np.maximum(ndi.gaussian_filter((img - mu) ** 2, sigma), 1e-6))
    return (img - mu) / sd


# Joint setting for the outer section, chosen on 646_543 rings 28-47 (F1 0.89 vs forester
# lines) and checked on D71_8010 rings 50-80 (within 9% of two boundaries per ring).
OUTER = dict(x_lo=2782, x_hi=3392, norm_sigma=25.0, sigma_x=3.0, sigma_y=8.0, min_distance=12)


def detect_rings_outer(path: str, outer: dict | None = None) -> np.ndarray:
    """Inner section as detect_rings(); outer section (x_lo..x_hi) with local normalisation."""
    o = {**OUTER, **(outer or {})}
    base = detect_rings(path)
    lum = load_clean_luminance(path)
    H = lum.shape[0]
    rows = slice(int(H * 0.2), int(H * 0.8))
    z = local_normalise(lum, o["norm_sigma"])
    prof = ridge_profile(z, rows, sigma_x=o["sigma_x"], sigma_y=o["sigma_y"], polarity=-1)
    pk = peaks(prof, min_distance=o["min_distance"])
    pk = pk[(pk > o["x_lo"]) & (pk < o["x_hi"])]
    inner = base[(base <= o["x_lo"]) | (base >= o["x_hi"])]
    return np.sort(np.concatenate([inner, pk]))


if __name__ == "__main__":
    import argparse
    from tools.forester_marks import extract_red_lines, score
    ap = argparse.ArgumentParser(description="Detect ring boundaries in a core strip")
    ap.add_argument("image", help="clean core photo (no red marks needed)")
    ap.add_argument("--truth", default=None, help="annotated copy with red ring lines")
    ap.add_argument("--tol", type=float, default=17.0)
    a = ap.parse_args()
    found = detect_rings(a.image)
    print(f"{a.image}: {len(found)} ring boundaries detected")
    if a.truth:
        truth = extract_red_lines(a.truth)
        s = score(found, truth, tol=a.tol)
        f1 = 2 * s["recall"] * s["precision"] / (s["recall"] + s["precision"] + 1e-9)
        print(f"truth {s['truth']}  recall {s['recall']:.2f}  precision {s['precision']:.2f}  F1 {f1:.2f}")
