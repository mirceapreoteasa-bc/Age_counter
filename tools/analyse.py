"""Command-line analysis: photo + path -> count, confidence, proof image, record.

This is the batch form of what the desktop tool does interactively, and the
same three calls the Android app will make. Everything the detector needs is
explicit on the command line, because automatic core finding was the single
largest source of error in the prototype.

Example:
  python3 -m tools.analyse \\
      --image PHOTOS/D71_7984.JPG \\
      --path 40 240 5900 240 --band 80 \\
      --px-per-mm 45.9 --ring-width-mm 0.27 \\
      --name XGV237P2T6 --out out/AGE_COUNT --save-record
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
from engine import annotate as A        # noqa: E402
from engine import records as R         # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Count rings in a core photo")
    ap.add_argument("--image", required=True)
    ap.add_argument("--path", nargs=4, type=float, required=True,
                    metavar=("X0", "Y0", "X1", "Y1"),
                    help="sampling path, pith end first")
    ap.add_argument("--band", type=int, default=80,
                    help="cross-axis sampling width in pixels")
    ap.add_argument("--px-per-mm", type=float, required=True)
    ap.add_argument("--ring-width-mm", type=float, default=None,
                    help="known ring width; supply it for fine-ring cores")
    ap.add_argument("--second-path-mm", type=float, default=0.0,
                    help="run a second path this far away and correct widths")
    ap.add_argument("--segment", default="")
    ap.add_argument("--seg-from-mm", type=float, default=None)
    ap.add_argument("--seg-to-mm", type=float, default=None)
    ap.add_argument("--name", default="", help="age name, e.g. XGV237P2T6")
    ap.add_argument("--out", default="out", help="output folder")
    ap.add_argument("--save-record", action="store_true",
                    help="append to the CSV and rebuild ODS/XLSX")
    ap.add_argument("--store-folder", default=None,
                    help="AGE_COUNT folder; defaults to <out>/AGE_COUNT")
    ap.add_argument("--json", default=None, help="also dump the result here")
    args = ap.parse_args(argv)

    with Image.open(args.image) as im:
        rgb = np.asarray(im.convert("RGB"), dtype=np.float64)
    lum = rgb @ [0.299, 0.587, 0.114]

    path = A.Path(a=(args.path[0], args.path[1]),
                  b=(args.path[2], args.path[3]),
                  width_px=args.band)
    strip = path.sample(lum)
    res = D.detect_stable(strip, args.px_per_mm, 0, args.band,
                          ring_width_mm=args.ring_width_mm,
                          second_path_mm=args.second_path_mm)

    core_mm = path.length_px / args.px_per_mm
    print(f"image        {os.path.basename(args.image)}  {lum.shape[1]}x{lum.shape[0]}")
    print(f"path         ({path.a[0]:.0f},{path.a[1]:.0f}) -> "
          f"({path.b[0]:.0f},{path.b[1]:.0f})  band {args.band}px")
    print(f"scale        {args.px_per_mm:.1f} px/mm   core {core_mm:.0f} mm")
    print(f"ring width   {res.params.get('ring_width_mm')} mm "
          f"(given: {args.ring_width_mm})")
    print(f"count        {res.count} rings")
    print(f"confidence   +/- {res.confidence}   (pre-filter ladder "
          f"{res.params.get('ladder_counts')})")
    print(f"spacing      {res.spacing_mm_median} mm median  "
          f"({res.px_per_ring} px/ring)")
    print(f"status       {'counted' if res.resolved else 'UNRESOLVED'}")
    for w in res.warnings:
        print(f"  ! {w}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(res.to_dict(), fh, indent=2, default=float)

    if args.name:
        folder = args.store_folder or os.path.join(args.out, "AGE_COUNT")
        os.makedirs(folder, exist_ok=True)
        png = os.path.join(folder, f"{args.name}.png")
        rec = A.render_proof(args.image, path, res.bands, png, args.name,
                             res.count, res.confidence, args.px_per_mm,
                             warnings=res.warnings,
                             status="counted" if res.resolved else "unresolved",
                             segment=args.segment)
        print(f"proof        {png}")
        print(f"sidecar      {rec['_json_path']}")
        if args.save_record:
            store = R.CountStore(folder)
            row = R.record_from_count(
                args.name, res.count, res.confidence,
                status="counted" if res.resolved else "unresolved",
                segment=args.segment, proof_png=png,
                record_json=rec["_json_path"],
                width_median_mm=res.spacing_mm_median,
                seg_from_mm=args.seg_from_mm, seg_to_mm=args.seg_to_mm,
                px_per_mm=args.px_per_mm, source_image=args.image)
            info = store.add(row)
            print(f"records      {info['rows']} rows  -> {info['csv']}")
            print(f"              {info['ods']}")
            print(f"              {info['xlsx']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
