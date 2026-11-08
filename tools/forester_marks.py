"""Forester ground truth from annotated core photos, and scoring of ring detections.

A forester draws a red line at each ring boundary on an annotated copy of a core
photo (e.g. 646_543rings.jpg). extract_red_lines() turns those lines into x positions
along the core; score() compares a detector's positions with them.

  python3 tools/forester_marks.py ANNOTATED.jpg [--tol 17]
"""
from __future__ import annotations

import argparse

import numpy as np
from PIL import Image
from scipy import ndimage as ndi


def extract_red_lines(path: str, thr: int = 150, min_h: int = 8, max_w: int = 25,
                      merge_px: int = 8) -> np.ndarray:
    """x positions of red ring lines in an annotated photo, sorted left to right."""
    im = np.asarray(Image.open(path).convert("RGB")).astype(int)
    r, g, b = im[..., 0], im[..., 1], im[..., 2]
    red = (r > thr) & (r - g > 60) & (r - b > 60)
    lab, _ = ndi.label(red)
    xs = []
    for sl in ndi.find_objects(lab):
        h = sl[0].stop - sl[0].start
        w = sl[1].stop - sl[1].start
        if h >= min_h and w <= max_w:
            xs.append((sl[1].start + sl[1].stop) / 2)
    merged: list[list[float]] = []
    for x in sorted(xs):
        if merged and x - merged[-1][-1] < merge_px:
            merged[-1].append(x)
        else:
            merged.append([x])
    return np.array([float(np.mean(m)) for m in merged])


def score(detected: np.ndarray, truth: np.ndarray, tol: float = 17.0) -> dict:
    """Recall (truth found) and precision (detections on a truth line) within tol px."""
    detected = np.asarray(detected, dtype=float)
    truth = np.asarray(truth, dtype=float)
    if len(truth) == 0 or len(detected) == 0:
        return {"recall": 0.0, "precision": 0.0, "truth": len(truth), "detected": len(detected),
                "hits_truth": 0, "hits_detected": 0}
    hits_t = int(sum(np.min(np.abs(detected - t)) <= tol for t in truth))
    hits_d = int(sum(np.min(np.abs(truth - d)) <= tol for d in detected))
    return {"recall": hits_t / len(truth), "precision": hits_d / len(detected),
            "truth": len(truth), "detected": len(detected),
            "hits_truth": hits_t, "hits_detected": hits_d}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("--tol", type=float, default=17.0)
    args = ap.parse_args()
    xs = extract_red_lines(args.image)
    print(f"{args.image}: {len(xs)} forester ring lines, x {xs[0]:.0f}..{xs[-1]:.0f} px")
    if len(xs) > 2:
        d = np.diff(xs)
        print(f"spacing median {np.median(d):.1f} px (min {d.min():.1f}, max {d.max():.1f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
