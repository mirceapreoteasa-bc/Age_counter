#!/usr/bin/env python3
"""Build a synthetic v5-card photo from a real core, to test the v5 path.

Card v5 cannot be validated on real photos until it is printed, but the hard
part of the v5 pipeline - separating a core from a black channel and taking the
scale from the core's own 5 mm diameter - can be tested now by compositing a
real core cutout onto a card whose geometry we choose and therefore know.

Because the composite scale is known, a band count found on the synthetic image
can be compared directly against the count already validated by eye on the
original photo. Nothing here is a substitute for a real print; it is a way to
test the detector against ground truth before spending a print run on it.

Usage:
    python3 tools/make_v5_test_image.py SRC.jpg OUT.png [--px-per-mm 30]
"""

import argparse
import os
import sys

import numpy as np
from PIL import Image

# Card v5 layout, millimetres. Must match tools/make_card_v5.py.
PAGE_W, PAGE_H = 297.0, 210.0
FIDUCIAL = 8.0
FID_CENTRES = ((14.0, 14.0), (283.0, 14.0), (14.0, 196.0), (283.0, 196.0))
PATCH = 25.0
PATCH_BLACK = (196.0, 8.0)
PATCH_WHITE = (228.0, 8.0)
CH_A = (12.0, 78.0, 273.0, 16.0)
CH_B = (12.0, 110.0, 273.0, 16.0)
BAR100 = (12.0, 136.0, 100.0, 5.0)
BAR5 = (12.0, 150.0, 40.0, 5.0)
LANE = (12.0, 50.0, 273.0, 18.0)

CORE_DIA_MM = 5.0


def load_core(src: str):
    """Return the core cutout and the pixel-per-mm implied by its own width.

    The core is taken as the warm, horizontally elongated region found from the
    red-minus-blue channel, which is how the wood was separated from the card
    in the field photos. The width it measures here is what v5 would use as
    scale, so no separate truth is assumed.
    """
    im = Image.open(src).convert("RGB")
    a = np.asarray(im, dtype=float)
    H, W, _ = a.shape
    chroma = a[:, :, 0] - a[:, :, 2]
    rm = chroma.mean(axis=1)
    warm = rm > rm.mean() + 0.9 * rm.std()
    best = (0, 0, 0)
    y = 0
    while y < H:
        if warm[y]:
            y0 = y
            while y < H and warm[y]:
                y += 1
            if y - y0 > best[0]:
                best = (y - y0, y0, y)
        else:
            y += 1
    _, sy0, sy1 = best
    cm = np.convolve(chroma[sy0:sy1].mean(axis=0), np.ones(81) / 81, mode="same")
    wood = cm > np.percentile(cm, 40)
    runs = []
    x = 0
    while x < W:
        if wood[x]:
            s = x
            while x < W and wood[x]:
                x += 1
            runs.append([s, x - 1])
        else:
            x += 1
    merged = []
    for rr in runs:
        if merged and rr[0] - merged[-1][1] <= 120:
            merged[-1][1] = rr[1]
        else:
            merged.append(rr)
    merged = [r for r in merged if r[1] - r[0] > 300]
    if not merged:
        raise SystemExit("no wood found in " + src)
    merged.sort(key=lambda r: -(r[1] - r[0]))
    x0, x1 = merged[0]
    pad = 6
    cut = im.crop((max(0, x0 - pad), max(0, sy0 - pad),
                   min(W, x1 + pad), min(H, sy1 + pad)))
    return cut, (x0, x1, sy0, sy1)


def compose(cut: Image.Image, out: str, px_per_mm: float, channel: str = "A"):
    """Paste the core cutout onto a card v5 at a known scale."""
    W = int(round(PAGE_W * px_per_mm))
    H = int(round(PAGE_H * px_per_mm))
    card = Image.new("RGB", (W, H), "white")
    from PIL import ImageDraw
    d = ImageDraw.Draw(card)

    def mm(v):
        return int(round(v * px_per_mm))

    for cx, cy in FID_CENTRES:
        r = mm(FIDUCIAL / 2)
        d.ellipse([mm(cx) - r, mm(cy) - r, mm(cx) + r, mm(cy) + r], fill="black")

    bx, by = PATCH_BLACK
    d.rectangle([mm(bx), mm(by), mm(bx + PATCH), mm(by + PATCH)], fill="black")
    wx, wy = PATCH_WHITE
    d.rectangle([mm(wx), mm(wy), mm(wx + PATCH), mm(by + PATCH)], fill="white",
                outline="black", width=max(1, mm(1.2)))

    lx, ly, lw, lh = LANE
    d.rectangle([mm(lx), mm(ly), mm(lx + lw), mm(ly + lh)], outline="black",
                width=max(1, mm(1.2)))

    cx, cy, cw, ch = (CH_A if channel == "A" else CH_B)
    if channel == "A":
        d.rectangle([mm(cx), mm(cy), mm(cx + cw), mm(cy + ch)], fill="black")
    else:
        d.rectangle([mm(cx), mm(cy), mm(cx + cw), mm(cy + ch)], fill="white",
                    outline="black", width=max(1, mm(1.6)))

    for bx, by, bw, bh in (BAR100, BAR5):
        d.rectangle([mm(bx), mm(by), mm(bx + bw), mm(by + bh)], fill="black")

    # Scale the cutout so the core measures CORE_DIA_MM across, then lay it in
    # the channel. Scaling the cutout by its own width is what makes the
    # composite's scale self-consistent: a 5 mm core in the channel.
    core_px = cut.height
    want_px = CORE_DIA_MM * px_per_mm
    scale = want_px / core_px
    new_w = max(1, int(round(cut.width * scale)))
    new_h = max(1, int(round(cut.height * scale)))
    core = cut.resize((new_w, new_h), Image.LANCZOS)

    # Longest core that fits the channel with a little margin at each end.
    max_w = int(cw * px_per_mm) - 2 * mm(4)
    if new_w > max_w:
        k = max_w / new_w
        core = core.resize((max_w, max(1, int(round(new_h * k)))), Image.LANCZOS)
        new_h = core.height

    px = mm(cx + 4)
    py = int(round(cy * px_per_mm + (ch * px_per_mm - new_h) / 2))
    card.paste(core, (px, py))

    card.save(out)
    return {
        "px_per_mm": px_per_mm,
        "core_w_px": core.width,
        "core_h_px": core.height,
        "core_mm_measured": core.height / px_per_mm,
        "core_len_mm": core.width / px_per_mm,
        "ratio": core.width / core.height,
        "px": px, "py": py,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--px-per-mm", type=float, default=30.0)
    ap.add_argument("--channel", default="A")
    a = ap.parse_args()
    if not os.path.exists(a.src):
        sys.exit("missing " + a.src)
    cut, box = load_core(a.src)
    info = compose(cut, a.out, a.px_per_mm, a.channel)
    print("source core box (x0,x1,y0,y1) =", box, " cutout", cut.size)
    for k, v in info.items():
        print("  %-18s %s" % (k, v))
    print("wrote", a.out)


if __name__ == "__main__":
    main()
