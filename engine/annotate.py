"""Proof rendering: draw the detected ring rectangles on the full-resolution
photo, and write the machine-readable record beside it.

Two rules that matter:

* Draw on the **full-resolution original**, never on the downsampled image the
  detector worked on. Reviewers zoom 20-50x; marks drawn on a reduced image
  drift off the rings and the whole point of the proof is lost.
* The ring geometry is stored in **image coordinates** in a JSON sidecar, so the
  record stays checkable and re-renderable without re-running detection. The
  proof is data, not just a picture.

Geometry is handled by sampling along the path rather than by rotating the
image, which keeps the mapping from strip coordinates back to image pixels
exact and invertible.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional, Sequence

import numpy as np
from PIL import Image, ImageDraw

# proof colours: latewood ring rectangle, path line, band edges
RING_COLOUR = (255, 40, 40)
PATH_COLOUR = (0, 200, 255)
BAND_COLOUR = (255, 220, 0)
MIN_LINE_W = 2          # so a rectangle is still visible at 50x zoom


@dataclass
class Path:
    """A sampling path along the core plus a cross-axis band.

    a, b      image pixel coordinates, a at the pith end
    width_px  cross-axis band width in image pixels
    """
    a: tuple[float, float]
    b: tuple[float, float]
    width_px: int

    def axis(self) -> tuple[tuple[float, float], tuple[float, float]]:
        ax, ay = self.a
        bx, by = self.b
        length = math.hypot(bx - ax, by - ay)
        ux, uy = (bx - ax) / length, (by - ay) / length
        return (ux, uy), (-uy, ux)

    @property
    def length_px(self) -> int:
        return int(round(math.hypot(self.b[0] - self.a[0], self.b[1] - self.a[1])))

    def sample(self, grey: np.ndarray) -> np.ndarray:
        """Resample the image into a strip with the core along axis 0.

        Returns shape (length_px, width_px) so every downstream coordinate is
        expressed in "along the core" and "across the core" terms.
        """
        from scipy import ndimage
        (ux, uy), (vx, vy) = self.axis()
        L = self.length_px
        W = max(1, int(self.width_px))
        t = np.arange(L, dtype=np.float64)
        s = np.arange(W, dtype=np.float64) - (W - 1) / 2.0
        xs = self.a[0] + ux * t[:, None] + vx * s[None, :]
        ys = self.a[1] + uy * t[:, None] + vy * s[None, :]
        return ndimage.map_coordinates(grey, [ys, xs], order=1, mode="nearest")

    def strip_rect_to_image(self, t0: int, t1: int, s0: int, s1: int
                            ) -> list[tuple[float, float]]:
        """Map a strip-space rectangle to its four image-space corners."""
        (ux, uy), (vx, vy) = self.axis()
        W = max(1, int(self.width_px))
        half = (W - 1) / 2.0
        corners = []
        for t, s in ((t0, s0), (t1, s0), (t1, s1), (t0, s1)):
            off = s - half
            corners.append((self.a[0] + ux * t + vx * off,
                            self.a[1] + uy * t + vy * off))
        return corners

    def point_to_strip(self, x: float, y: float) -> tuple[float, float]:
        (ux, uy), (vx, vy) = self.axis()
        W = max(1, int(self.width_px))
        half = (W - 1) / 2.0
        dx, dy = x - self.a[0], y - self.a[1]
        return dx * ux + dy * uy, dx * vx + dy * vy + half


def _poly_bounds(corners: Sequence[tuple[float, float]]
                 ) -> tuple[int, int, int, int]:
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return (int(round(min(xs))), int(round(min(ys))),
            int(round(max(xs))), int(round(max(ys))))


def render_proof(image_path: str, path: Path, bands: Iterable,
                 out_png: str, age_name: str, count: int,
                 confidence: Optional[int], px_per_mm: float,
                 warnings: Optional[list[str]] = None,
                 status: str = "counted",
                 segment: str = "", json_path: Optional[str] = None,
                 draw_path: bool = True, draw_bands: bool = True,
                 ring_index: bool = True) -> dict:
    """Write the annotated PNG and the JSON sidecar. Returns the record.

    `bands` are detect.RingBand objects expressed in strip coordinates.
    """
    with Image.open(image_path) as im:
        full = im.convert("RGB")
    W, H = full.size

    # Draw at full resolution. Only the rectangle edges are thickened, never the
    # interior, so a 50x zoom shows the actual boundary the detector used.
    draw = ImageDraw.Draw(full)
    records = []
    bands = list(bands)

    for i, b in enumerate(bands, start=1):
        corners = path.strip_rect_to_image(int(b.start_px), int(b.end_px),
                                           int(b.cross_lo), int(b.cross_hi))
        x0, y0, x1, y1 = _poly_bounds(corners)
        line = max(MIN_LINE_W, int(round(1.0 / 1.0)))
        draw.line(corners + [corners[0]], fill=RING_COLOUR, width=line)
        if ring_index and (i == 1 or i == len(bands) or i % 10 == 0):
            label = f"{i}"
            tx, ty = corners[2]
            draw.text((tx + 3, ty + 3), label, fill=RING_COLOUR)
        records.append({
            "index": i,
            "rect_image": [x0, y0, x1, y1],
            "corners_image": [[round(c[0], 1), round(c[1], 1)] for c in corners],
            "centre_px": round(float(b.centre_px), 2),
            "width_px": round(float(b.width_px), 2),
            "width_mm": round(float(b.width_mm), 4),
            "strength": round(float(b.strength), 4),
        })

    if draw_path:
        line = max(MIN_LINE_W, 1)
        draw.line([path.a, path.b], fill=PATH_COLOUR, width=line)
    if draw_bands:
        for s in (0, path.width_px - 1):
            corners = path.strip_rect_to_image(0, path.length_px, s, s)
            draw.line([corners[0], corners[1]], fill=BAND_COLOUR, width=1)

    os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
    full.save(out_png)          # PNG: lossless, so 50x zoom stays honest

    record = {
        "schema": "agecore.count/1",
        "age_name": age_name,
        "segment": segment,
        "ring_count": int(count),
        "confidence_plus_minus": (None if confidence is None else int(confidence)),
        "status": status,
        "px_per_mm": round(float(px_per_mm), 3),
        "source_image": os.path.basename(image_path),
        "proof_image": os.path.basename(out_png),
        "source_size": [W, H],
        "path": {"a": list(map(float, path.a)), "b": list(map(float, path.b)),
                 "width_px": int(path.width_px),
                 "length_px": int(path.length_px)},
        "rings": records,
        "warnings": warnings or [],
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    side = json_path or os.path.splitext(out_png)[0] + ".json"
    with open(side, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
    record["_json_path"] = side
    record["_png_path"] = out_png
    return record
