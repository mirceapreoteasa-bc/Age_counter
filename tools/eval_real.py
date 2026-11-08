"""Measure the pipeline against real cores with known answers.

Two questions are being separated, because they fail for different reasons and
have different fixes:

  **isolation** - was the right object found, and is its width right? The
  width sets the scale, so a 10% width error moves every ring width with it.

  **counting** - given the right core region, how close is the count? This is
  measured with the *known* scale, because a count made with a wrong scale is
  not evidence about the detector.

Every case below is a real photo with a number the forester gave, and the
numbers are in the file's own units. `band` gives a known region (y0, y1 in
image pixels) for the photos whose core sits in a printed channel, where the
automatic search cannot work and the point of the test is the count.

Usage:
    python3 -m tools.eval_real [--only 382]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import detect as D          # noqa: E402
from engine import ensemble as E        # noqa: E402
from engine import pipeline as P        # noqa: E402

ROOT = os.path.expanduser("~/ForestAgeCounter")


class Case:
    def __init__(self, name, path, true_count, px_per_mm, ring_mm,
                 band=None, note=""):
        self.name = name
        self.path = os.path.join(ROOT, path)
        self.true_count = true_count
        self.px_per_mm = px_per_mm
        self.ring_mm = ring_mm
        self.band = band              # (y0, y1) in image pixels, or None
        self.note = note


CASES = [
    Case("382", "OCT3/382.jpg", 132, 29.2, 0.79,
         note="ruler in frame; 130 marked + 2 pith arcs = 132"),
    Case("592", "OCT3/091604_592.jpg", 60, 31.4, 2.37,
         note="forester 59-60; own ruler 31.4 px/mm"),
    Case("D71_7984", "PHOTOS/D71_7984.JPG", 480, 45.9, 0.27,
         note="3-4 BA core, forester ~480"),
    Case("PXL_ACT_327", "PHOTOS/PXL_ACT_327.jpg", 23, 14.55, 9.0,
         band=(1386, 1477),
         note="24 marked boundaries = 23 intervals; core in the printed strip"),
]


def strip_for_case(case, rgb):
    """The core strip, either from the automatic search or from a known band."""
    if case.band is not None:
        y0, y1 = case.band
        grey = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
        # the core runs across the frame here, so it is transposed to run
        # along axis 0 like every other strip in the engine
        return np.ascontiguousarray(grey[y0:y1 + 1, :].T), None
    core, _ = P.find_core(rgb)
    if core is None:
        return None, None
    strip, path = P.rectify(rgb, core)
    return strip, core


def evaluate(case, use_true_scale=True, use_true_ring=True) -> dict:
    with Image.open(case.path) as im:
        rgb = np.asarray(im.convert("RGB"), dtype=np.float64)
    strip, core = strip_for_case(case, rgb)
    if strip is None:
        return {"name": case.name, "error": "no core found"}

    px_per_mm = case.px_per_mm if use_true_scale else (
        core.px_per_mm if core else case.px_per_mm)
    ring_mm = case.ring_mm if use_true_ring else None
    W = strip.shape[1]

    out = {
        "name": case.name,
        "note": case.note,
        "true_count": case.true_count,
        "px_per_mm_used": round(px_per_mm, 2),
        "px_per_mm_true": case.px_per_mm,
        "ring_mm_used": ring_mm,
        "strip_px": [int(strip.shape[0]), int(strip.shape[1])],
    }
    if core is not None:
        out["core"] = {
            "source": core.source,
            "width_px": round(core.width_px, 1),
            "implied_px_per_mm": round(core.px_per_mm, 2),
            "width_error_pct": round(
                (core.width_px / (case.px_per_mm * 5.0) - 1) * 100, 1),
        }

    # 1. the app's own path: the pre-filter ladder
    res = D.detect_stable(strip, px_per_mm, 0, W, ring_width_mm=ring_mm)
    out["ladder"] = {
        "count": res.count,
        "error_pct": round((res.count - case.true_count) / case.true_count * 100, 1),
        "resolved": res.resolved,
        "confidence": res.confidence,
        "ladder_counts": res.params.get("ladder_counts"),
    }
    if res.params.get("ladder_counts"):
        out["ladder"]["spread_pct"] = round(
            (max(res.params["ladder_counts"]) - min(res.params["ladder_counts"]))
            / max(max(res.params["ladder_counts"]), 1) * 100, 1)

    # 2. the boundary consolidation, which is the pipeline's primary
    prof = P._texture_profile(strip, win=max(3, int(round(0.05 * case.ring_mm * px_per_mm))))
    from engine import consolidate as C
    from engine import chain as CH
    cands = C.candidates_from_profile(prof)
    con = C.consolidate(cands, expected_ring_px=case.ring_mm * px_per_mm)
    n_int = max(0, len(con.boundaries) - 1)
    out["consolidated"] = {
        "candidates": con.n_candidates,
        "boundaries": len(con.boundaries),
        "count": n_int,
        "error_pct": round((n_int - case.true_count) / case.true_count * 100, 1),
        "resolved": con.resolved,
    }

    # 3. the chain, which is the sensitive counter
    kept = CH.non_max_suppression(cands, P.CH_NMS_FRACTION * case.ring_mm * px_per_mm)
    ch = CH.find_chain(kept, case.ring_mm * px_per_mm, beta=P.CH_BETA)
    out["chain"] = {
        "candidates": len(kept),
        "boundaries": len(ch.positions),
        "count": ch.count,
        "error_pct": round((ch.count - case.true_count) / case.true_count * 100, 1),
        "skipped": ch.skipped,
    }

    # 3. the other estimators, as cross-checks
    cmp_ = E.run_all(strip, px_per_mm, 0, W, ring_width_mm=ring_mm)
    out["methods"] = {
        r.method: {"count": r.count, "resolved": r.resolved}
        for r in cmp_.readings
    }
    counts = [r.count for r in cmp_.readings if r.resolved and r.count >= 2]
    if counts:
        out["method_spread_pct"] = round(
            (max(counts) - min(counts)) / max(max(counts), 1) * 100, 1)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="case name substring")
    ap.add_argument("--auto-scale", action="store_true",
                    help="use the scale from the detected core, not the true one")
    ap.add_argument("--no-ring-hint", action="store_true",
                    help="do not give the engine the expected ring width")
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)

    results = []
    for case in CASES:
        if a.only and a.only not in case.name:
            continue
        r = evaluate(case, use_true_scale=not a.auto_scale,
                     use_true_ring=not a.no_ring_hint)
        results.append(r)
        print(json.dumps(r, indent=2, default=float))
        print()
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2, default=float)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
