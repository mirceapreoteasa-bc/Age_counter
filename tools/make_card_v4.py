#!/usr/bin/env python3
"""Generate the AGE CORE PHOTO CARD v4 - LANDSCAPE, for long cores.

A4 rotated: 297 mm across, 210 mm tall. This exists because v3's strip is
188 mm long and will not take a core of 250 mm, which a large cottonwood,
veteran Douglas-fir or bigleaf maple routinely exceeds.

Design decisions carried forward from measured field results, not preference:

* **The ruler is the instrument, not the card.** The v2 card's 100 mm bar
  printed 93 mm, and the printed bar, the bay pitch and the ID-1 outline were
  all wrong by the *same* 7%, so they can never cross-check one another. Only
  something that did not come out of the printer can. A physical ruler lying in
  the calibration lane is the reference; the card is target layout plus a drift
  report.

* **Graduations, never digits.** Printed cm digits on a glossy curved caliper
  body could not be segmented (residuals swung +-60 px). A tick ladder is
  regular and high contrast. The ruler's 0 mark sitting 7 mm in from a rounded
  edge is irrelevant, because the *pitch* is fitted and an unknown origin
  cancels exactly - which is why these combs carry no origin mark at all.

* **Two pitches, and they must agree.** 10 mm and 50 mm combs. Losing marks to
  occlusion still leaves a fit, and if the two disagree then print or lens is
  at fault. The 50 mm marks are an exact subset of the 10 mm marks, so the
  relationship is provable rather than hoped for.

* **The calibration lane is clear of cores.** On v2 the cores lay over the
  ticks and hid all but three of five, which is why automatic scale detection
  failed on real photos. Here the lane is a band above the core strip that no
  core can reach.

* **Corner fiducials for a homography**, on a non-square 271 x 184 mm grid so
  perspective is corrected rather than assumed away and a 90 degree rotation is
  unambiguous.

* **Strokes >= 1.2 mm, black on white**, so a weak toner cartridge still
  binarises cleanly.

* **Bay pitch is 50.00 mm, matching the 50 mm calibration comb.** A 250 mm core
  then fills the five bays exactly, and the same physical interval serves both
  measuring and layout. Fewer distinct pitches on the page also means fewer
  harmonic confusions when scale is recovered from a photo.

* **The 5 mm core diameter is printed as a reference ring**, used as a sanity
  band and never as an authority: the photographed silhouette includes the
  shadowed rim and measured 5.6-6.2 mm against a nominal 5 mm.

* **An opaque ruler is specified on purpose.** A transparent overlay's printed
  graduations had too little contrast to recover any scale from at all.

PRINT LANDSCAPE AT 100%. Do not use "fit to page" or "shrink to fit". Matte
paper only.
"""

import os
import shutil
import subprocess

# --- page, A4 rotated -------------------------------------------------------
W, H = 297.0, 210.0

BLACK = "#000000"
WHITE = "#FFFFFF"
GREY_TEXT = "#404040"
FAINT = "#BFBFBF"
MID = "#D8D8D8"

STROKE = 1.2
FIDUCIAL = 7.0
FID_CENTRES = ((13.0, 13.0), (284.0, 13.0), (13.0, 197.0), (284.0, 197.0))

# --- calibration lane: no core can reach this band --------------------------
LANE_X, LANE_Y, LANE_W, LANE_H = 9.0, 24.0, 279.0, 40.0
COMB10_X0, COMB10_N, COMB10_PITCH = 20.0, 24, 10.0
COMB10_Y, COMB10_H = LANE_Y + 6.0, 7.0
COMB50_X0, COMB50_N, COMB50_PITCH = 20.0, 5, 50.0
COMB50_Y, COMB50_H = LANE_Y + 26.0, 6.0

# --- core strip: 5 bays x 50.00 mm = 250.00 mm ------------------------------
STRIP_X, STRIP_Y, STRIP_W, STRIP_H = 18.0, 74.0, 261.0, 30.0
BAYS = 5
BAY_PITCH = 50.0
BAY_X0 = 20.0
AXIS_Y = STRIP_Y + STRIP_H / 2.0
TICK_LABELS = "ABCDE"

# --- below the strip --------------------------------------------------------
BAR_X0, BAR_LEN, BAR_Y, BAR_H = 20.0, 100.0, 114.0, 4.0
WEDGE_X0, WEDGE_Y, WEDGE_W, WEDGE_H = 20.0, 134.0, 90.0, 7.0
BAR_PERIODS = (2.0, 1.0, 0.7, 0.5, 0.35)
GREY_X0, GREY_Y, GREY_W, GREY_H = 20.0, 156.0, 42.0, 12.0
WHITE_X0, WHITE_Y, WHITE_W, WHITE_H = 70.0, 156.0, 42.0, 12.0
DIA_CX, DIA_CY, DIA_R = 130.0, 162.0, 11.0

NOTES = (
    "1  Landscape card, cores to 250 mm. Bays A-E are 50.00 mm each, so a 250 mm core fills them exactly.",
    "2  Lay an opaque ruler across the top lane. The app measures its millimetre graduations, not this page.",
    "3  Ruler origin does not matter: only the graduation pitch is used. Edge and 0 mark may differ.",
    "4  Place the core in the strip and keep it clear of the calibration lane above.",
    "5  A core from a 5 mm increment drill is about 5 mm across. The ring at right is a rough check only.",
    "6  The printed 100 mm bar is a drift check. It is printed, so do not scale to it.",
)

GR = 150.0 / 25.4


def mm(v: float) -> str:
    return f"{v * GR:.3f}"


def rect(x, y, w, h, fill, extra=""):
    return (f'<rect x="{mm(x)}" y="{mm(y)}" width="{mm(w)}" '
            f'height="{mm(h)}" fill="{fill}" {extra}/>')


def circle(cx, cy, r, fill="none", stroke=BLACK, sw=1.2):
    return (f'<circle cx="{mm(cx)}" cy="{mm(cy)}" r="{mm(r)}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{mm(sw)}"/>')


def text(x, y, s, size, fill=BLACK, weight="normal", anchor="start"):
    safe = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (f'<text x="{mm(x)}" y="{mm(y)}" font-family="DejaVu Sans, '
            f'Helvetica, Arial, sans-serif" font-size="{mm(size)}" '
            f'fill="{fill}" font-weight="{weight}" text-anchor="{anchor}">'
            f'{safe}</text>')


def comb(x0, y, n, pitch, tick_h, label, sub=""):
    """Graduated tick comb of known pitch, fitted by pitch not by origin."""
    out = []
    for k in range(n):
        h = tick_h * (1.6 if (k + 1) % 5 == 0 else 1.0)
        out.append(rect(x0 + k * pitch, y, STROKE, h, BLACK))
    out.append(rect(x0 - 1.0, y + tick_h + 1.0,
                    (n - 1) * pitch + STROKE + 2.0, 0.6, BLACK))
    out.append(text(x0 + (n - 1) * pitch, y - 1.2, label, 2.6, BLACK,
                    "bold", "end"))
    return out


def build() -> str:
    p = [rect(0, 0, W, H, WHITE)]

    for cx, cy in FID_CENTRES:
        p.append(rect(cx - FIDUCIAL / 2, cy - FIDUCIAL / 2,
                      FIDUCIAL, FIDUCIAL, BLACK))
        p.append(circle(cx, cy, FIDUCIAL / 2 + 2.2, stroke=FAINT, sw=0.5))

    p.append(text(26, 16.0, "AGE CORE PHOTO CARD  v4  -  LANDSCAPE", 4.6,
                  BLACK, "bold"))
    p.append(text(271, 16.0, "cores to 250 mm  -  scale from your ruler",
                  2.3, GREY_TEXT, anchor="end"))

    # calibration lane
    p.append(rect(LANE_X, LANE_Y, LANE_W, LANE_H, MID))
    p.append(text(11.0, LANE_Y - 1.4,
                  "CALIBRATION LANE  -  keep clear of cores", 2.6, BLACK, "bold"))
    p.extend(comb(COMB10_X0, COMB10_Y, COMB10_N, COMB10_PITCH, COMB10_H, "10 mm"))
    p.extend(comb(COMB50_X0, COMB50_Y, COMB50_N, COMB50_PITCH, COMB50_H, "50 mm"))
    p.append(text(20.0, LANE_Y + LANE_H + 4.0,
                  "10 mm comb: 24 marks, 230 mm baseline.   50 mm comb is a "
                  "second pitch - the two must agree, or print or lens is at fault.",
                  2.1, GREY_TEXT))

    # core strip
    p.append(rect(STRIP_X, STRIP_Y, STRIP_W, STRIP_H, MID))
    p.append(text(STRIP_X, STRIP_Y - 2.0,
                  "CORE STRIP  -  5 bays x 50.00 mm  =  250.00 mm",
                  2.6, BLACK, "bold"))
    for i in range(BAYS + 1):
        x = BAY_X0 + BAY_PITCH * i
        p.append(rect(x - 0.3, STRIP_Y, 0.6, STRIP_H, BLACK))
    for i in range(BAYS):
        cx = BAY_X0 + BAY_PITCH * (i + 0.5)
        p.append(text(cx, STRIP_Y + 7.5, TICK_LABELS[i], 4.4, GREY_TEXT,
                      "normal", "middle"))
    p.append(rect(BAY_X0, AXIS_Y, BAY_PITCH * BAYS, STROKE, BLACK))
    for i in range(BAYS):
        cx = BAY_X0 + BAY_PITCH * (i + 0.5)
        p.append(rect(cx - 0.4, AXIS_Y - 2.5, STROKE, 5.0, BLACK))
    p.append(text(BAY_X0 + 2.0, STRIP_Y + STRIP_H - 2.0,
                  "rings run across this line", 2.0, GREY_TEXT))

    # printed cross-check bar
    p.append(text(BAR_X0, BAR_Y - 2.0,
                  "PRINTED CROSS-CHECK  -  not used for scale", 2.5,
                  BLACK, "bold"))
    p.append(rect(BAR_X0, BAR_Y, BAR_LEN, BAR_H, BLACK))
    for k in range(11):
        p.append(rect(BAR_X0 + k * 10.0 - 0.4, BAR_Y - 2.0, 0.8, 2.0, BLACK))
    p.append(text(BAR_X0 + BAR_LEN + 2.0, BAR_Y + BAR_H - 0.8,
                  "nominal 100.00 mm", 2.2, GREY_TEXT))

    # optical resolution target
    p.append(text(WEDGE_X0, WEDGE_Y - 2.0, "OPTICAL RESOLUTION TARGET",
                  2.5, BLACK, "bold"))
    gw = WEDGE_W / len(BAR_PERIODS)
    for i, per in enumerate(BAR_PERIODS):
        gx = WEDGE_X0 + i * gw
        pitch = per / 2.0
        n = max(1, int(round((gw - 1.5) / pitch)))
        if n * pitch > (gw - 1.5):
            n = max(1, n - 1)
        for k in range(n):
            p.append(rect(gx + 0.75 + k * pitch, WEDGE_Y, pitch, WEDGE_H, BLACK))
        p.append(text(gx + gw / 2.0, WEDGE_Y + WEDGE_H + 3.0, f"{per:g}",
                      2.1, GREY_TEXT, "normal", "middle"))
    p.append(text(WEDGE_X0, WEDGE_Y + WEDGE_H + 6.4,
                  "mm per black/white pair - the finest group still resolved "
                  "is the real limit at the core's distance", 2.1, GREY_TEXT))

    # grey and white reference patches
    p.append(rect(GREY_X0, GREY_Y, GREY_W, GREY_H, FAINT))
    p.append(rect(WHITE_X0, WHITE_Y, WHITE_W, WHITE_H, WHITE,
                  f'stroke="{FAINT}" stroke-width="{mm(0.4)}"'))
    p.append(text(GREY_X0, GREY_Y + GREY_H + 3.6, "grey ref", 2.1, GREY_TEXT))
    p.append(text(WHITE_X0, WHITE_Y + WHITE_H + 3.6, "white ref", 2.1, GREY_TEXT))

    # 5 mm core diameter reference ring
    p.append(circle(DIA_CX, DIA_CY, 2.5, fill=BLACK))
    p.append(circle(DIA_CX, DIA_CY, DIA_R, stroke=BLACK, sw=1.0))
    p.append(text(DIA_CX, DIA_CY + DIA_R + 4.0, "5 mm core dia.", 2.1,
                  GREY_TEXT, "normal", "middle"))
    p.append(text(DIA_CX, DIA_CY - DIA_R - 2.0, "sanity check only", 1.9,
                  GREY_TEXT, "normal", "middle"))

    # instructions
    y = 140.0
    p.append(text(NOTE_X := 150.0, y, "HOW TO USE", 2.8, BLACK, "bold"))
    y += 5.2
    for line in NOTES:
        p.append(text(150.0, y, line, 2.1, BLACK))
        y += 4.6

    p.append(text(20.0, H - 8.0,
                  "v4 landscape: scale is measured from a physical ruler. A "
                  "printed card cannot be its own reference - v2 printed 7% small.",
                  2.2, GREY_TEXT))
    p.append(text(20.0, H - 4.4,
                  "PRINT LANDSCAPE AT 100% - do NOT scale to fit. Matte paper only.",
                  2.2, GREY_TEXT, "bold"))

    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}mm" '
            f'height="{H}mm" viewBox="0 0 {mm(W)} {mm(H)}">\n'
            + "\n".join(p) + "\n</svg>\n")


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "..", "card")
    os.makedirs(out, exist_ok=True)
    svg = build()
    svg_path = os.path.join(out, "age_core_photo_card_v4_landscape.svg")
    with open(svg_path, "w") as f:
        f.write(svg)
    print(f"wrote {svg_path}")

    pdf_path = os.path.join(out, "age_core_photo_card_v4_landscape.pdf")
    png_path = os.path.join(out, "preview_v4.png")
    for cmd in (
        ["inkscape", svg_path, "--export-type=pdf",
         f"--export-filename={pdf_path}", "--export-dpi=600"],
        ["inkscape", svg_path, "--export-type=png",
         f"--export-filename={png_path}", "--export-dpi=150"],
    ):
        if not shutil.which(cmd[0]):
            print(f"{cmd[0]} not available, skipped")
            continue
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            print(f"wrote {cmd[-1].split('=')[-1]}")
        else:
            print(f"{cmd[0]} failed: {r.stderr.strip()[:200]}")


if __name__ == "__main__":
    main()
