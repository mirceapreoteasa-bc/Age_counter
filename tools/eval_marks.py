"""How many of the forester's own marks does the detector offer?

This is the metric that matters for an assistive tool. Count agreement is the
wrong metric: it mixes together rings the forester missed, false rings he
correctly marked, and rings the app missed. Recall does not. The app is
useful when it *offers* the ring the forester already knows is there; the
forester then rejects the ones that are not.

Two numbers are produced for every frame:

  **recall**  the fraction of the forester's marks that have a detected
              boundary within a quarter of a ring of them.

  **extras**  the detected boundaries that no mark accounts for. These are
              either rings the forester did not mark, or the same ring found
              twice - which is a different problem and needs a different fix.

Usage:
    python3 -m tools.eval_marks --image PHOTOS/PXL_ACT_327.jpg --band 1386 1477
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import chain as CH               # noqa: E402
from engine import consolidate as C          # noqa: E402
from engine import detect as D               # noqa: E402
from engine import pipeline as P             # noqa: E402
from tools.extract_marks import extract      # noqa: E402


def strip_from_band(rgb, band):
    y0, y1 = band
    grey = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    return np.ascontiguousarray(grey[y0:y1 + 1, :].T)


def match(marks: list[float], found: list[float], tol: float):
    """Pair marks with detected boundaries.

    A mark is matched by the nearest unused detected boundary within `tol`.
    """
    used = set()
    pairs = []
    misses = []
    for m in marks:
        best = None
        bestd = None
        for i, f in enumerate(found):
            if i in used:
                continue
            d = abs(f - m)
            if bestd is None or d < bestd:
                bestd, best = d, i
        if best is not None and bestd is not None and bestd <= tol:
            used.add(best)
            pairs.append((m, found[best], bestd))
        else:
            misses.append(m)
    extras = [f for i, f in enumerate(found) if i not in used]
    return pairs, misses, extras


def evaluate(image_path: str, band=None, px_per_mm: float | None = None,
             keep_fraction: float = 0.20, merge_fraction: float = 0.30,
             tol_frac: float = 0.25, span_to_marks: bool = True,
             method: str | None = None) -> dict:
    marks_rep = extract(image_path, band, method=method)
    marks = marks_rep["centres_px"]
    with Image.open(image_path) as im:
        rgb = np.asarray(im.convert("RGB"), dtype=np.float64)
    if band is not None:
        strip = strip_from_band(rgb, band)
    else:
        core, _ = P.find_core(rgb)
        if core is None:
            return {"error": "no core found", "marks": marks_rep}
        strip, _ = P.rectify(rgb, core)
    L, W = strip.shape

    spacing = marks_rep["spacing_median_px"]
    tol = max(4.0, tol_frac * spacing)

    prof = P._texture_profile(strip, win=max(3, int(round(0.05 * spacing))))
    cands = C.candidates_from_profile(prof)
    con = C.consolidate(cands, expected_ring_px=spacing,
                         keep_fraction=keep_fraction,
                         merge_fraction=merge_fraction)
    found = [b.pos for b in con.boundaries]
    pairs, misses, extras = match(marks, found, tol)

    # the chain, which is the counter the app now uses
    kept = CH.non_max_suppression(cands, P.CH_NMS_FRACTION * spacing)
    ch = CH.find_chain(kept, spacing, beta=P.CH_BETA)

    # Only the stretch the forester actually marked can be scored. On
    # 646_543rings the marks stop around ring 44 while the core continues and
    # the decades are only numbered, so counting boundaries beyond the last
    # mark as "extras" would charge the detector for rings nobody marked.
    lo, hi = (min(marks), max(marks)) if (span_to_marks and marks) else (0.0, float(L))
    def in_span(x: float) -> bool:
        return lo - tol <= x <= hi + tol

    chain_found = [p for p in ch.positions if in_span(p)]
    found = [p for p in found if in_span(p)]
    c_pairs, c_misses, c_extras = match(marks, chain_found, tol)

    # the raw candidate field, as a control: how good is the signal before
    # anything decides what a boundary is
    cand_pos = sorted(c.pos for c in cands if in_span(c.pos))
    cand_pairs, cand_misses, _ = match(marks, cand_pos, tol)

    out = {
        "image": os.path.basename(image_path),
        "n_marks": len(marks),
        "strip_px": [int(L), int(W)],
        "scored_span_px": [round(lo, 1), round(hi, 1)],
        "mark_spacing_median_px": spacing,
        "tolerance_px": round(tol, 1),
        "candidates": {
            "n": len(cand_pos),
            "recall": round(len(cand_pairs) / max(1, len(marks)), 3),
            "misses": [round(m, 1) for m in cand_misses],
        },
        "chain": {
            "n_candidates": len(kept),
            "n_boundaries": len(chain_found),
            "count": max(0, len(chain_found) - 1),
            "count_whole_strip": ch.count,
            "recall": round(len(c_pairs) / max(1, len(marks)), 3),
            "matched": len(c_pairs),
            "misses": [round(m, 1) for m in c_misses],
            "extras": len(c_extras),
            "median_error_px": round(
                float(np.median([d for _, _, d in c_pairs])), 1) if c_pairs else 0.0,
        },
        "consolidated": {
            "n_boundaries": len(found),
            "recall": round(len(pairs) / max(1, len(marks)), 3),
            "matched": len(pairs),
            "misses": [round(m, 1) for m in misses],
            "extras": len(extras),
            "median_error_px": round(
                float(np.median([d for _, _, d in pairs])), 1) if pairs else 0.0,
            "worst_error_px": round(
                float(max([d for _, _, d in pairs])), 1) if pairs else 0.0,
        },
    }
    # where the misses are, in terms of the local ring spacing: a miss in a
    # tight stretch of wood is a resolution limit, a miss in an easy stretch
    # is a detector problem
    if misses:
        local = []
        for m in misses:
            near = sorted(abs(m - x) for x in marks)[:2]
            local.append(round(near[1] if len(near) > 1 else spacing, 1))
        out["consolidated"]["miss_local_spacing_px"] = local
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--band", nargs=2, type=int, default=None,
                    metavar=("Y0", "Y1"))
    ap.add_argument("--keep-fraction", type=float, default=0.20)
    ap.add_argument("--merge-fraction", type=float, default=0.30)
    ap.add_argument("--tol-frac", type=float, default=0.25)
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    band = tuple(a.band) if a.band else None
    rep = evaluate(a.image, band,
                   keep_fraction=a.keep_fraction,
                   merge_fraction=a.merge_fraction,
                   tol_frac=a.tol_frac)
    print(json.dumps(rep, indent=2))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
