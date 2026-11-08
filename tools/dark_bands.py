"""Count rings as dark latewood bands along a horizontal core photo.

Each ring is one light earlywood band followed by one dark latewood band, so counting
the dark bands counts the rings. Brightness is averaged across the core's height,
normalised locally, smoothed over about half a ring, and its deep minima are counted.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi
from scipy.signal import find_peaks

from tools.ridge_rings import load_clean_luminance, local_normalise


def brightness_profile(path: str, rows: slice | None = None, norm_sigma: float = 25.0) -> np.ndarray:
    lum = load_clean_luminance(path)
    H = lum.shape[0]
    rows = rows or slice(int(H * 0.2), int(H * 0.8))
    z = local_normalise(lum, norm_sigma)
    return z[rows].mean(axis=0)


def dark_band_positions(profile: np.ndarray, smooth_px: float = 8.0,
                        min_distance_px: float = 14.0, min_prominence: float = 0.1) -> np.ndarray:
    """x positions of the dark latewood bands (minima of the smoothed brightness)."""
    smooth = ndi.gaussian_filter1d(profile, smooth_px)
    minima, _ = find_peaks(-smooth, distance=int(min_distance_px), prominence=min_prominence)
    return minima.astype(float)


def count_between(positions: np.ndarray, x_lo: float, x_hi: float) -> int:
    return int(((positions > x_lo) & (positions <= x_hi)).sum())


# Results (2026-10-27), settings: smooth 12 px, min distance 10 px, prominence 0.05
#   D71_8010 start -> ring 80: 76 bands vs 80 counted (-5%)
#   646_543 pith -> ring 48:   33 bands vs 48 counted (-31%); numerals 1-27 matched 14/27
# Not adopted: on 646 the dark bands are not separated enough from the surrounding wood.
