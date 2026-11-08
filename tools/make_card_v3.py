#!/usr/bin/env python3
"""Generate the AGE CORE PHOTO CARD v3 as print-ready A4 vector SVG.

v3 inverts the roles of card and ruler, and that inversion is the whole point.

Why: the v2 card's 100 mm bar was measured at 93 mm after printing. Every
dimension on a printed card inherits that 7% error, so no redraw of the card
can fix it - a card that prints 7% small still prints 7% small. Worse, the
printed bar, the bay pitch and the ID-1 outline are all wrong by the *same*
7%, so they are not independent references and can never cross-check each
other. Only something that did not come out of the printer can.

So in v3:

  * The forester's plastic ruler is the INSTRUMENT. It cannot shrink. The app
    reads its millimetre graduations, never its digits: graduations are regular
    and high contrast, while digits on a glossy curved caliper body could not
    be segmented reliably (residuals swung +-60 px in testing).
  * The card is the TARGET LAYOUT plus a cross-check. It says where to put the
    core and confirms nothing has drifted. It is no longer trusted to measure.

The ruler's zero mark sits ~7 mm in from its edge, and the edge is rounded.
That does not matter: the app fits the *pitch* of the graduation comb over a
long baseline, never the absolute position of a zero mark, so an unknown or
offset origin cancels out exactly.

Card features, each answering a measured failure:

  * Calibration lane along the top edge that no core can cover, carrying
    graduated scales at two pitches (10 mm and 50 mm combs). Losing marks to
    occlusion still leaves a fit, and if the two pitches disagree we know
    something is wrong instead of quietly trusting one.
  * Four solid corner fiducials for a homography, so perspective is corrected
    rather than assumed away, and so the strip centreline follows from card
    geometry instead of from image detection.
  * Strokes >= 1.2 mm, black on white, generous spacing: chosen to binarise
    cleanly from a weak toner cartridge.
  * The 100 mm bar is retained but demoted to a drift report.

PRINT AT 100%. Do not use "fit to page" or "shrink to fit". Matte paper only.
"""

import math
import os
import shutil
import subprocess

W, H = 210.0, 297.0            # A4 in millimetres
BLACK = "#000000"
WHITE = "#FFFFFF"
GREY_TEXT = "#404040"
FAINT = "#BFBFBF"

STROKE = 1.2                   # minimum stroke weight, mm. Survives low toner.
FIDUCIAL = 7.0                 # corner fiducial size, mm
FID_CENTRES = ((13.0, 13.0), (W - 13.0, 13.0), (13.0, H - 13.0), (W - 13.0, H - 13.0))

# --- calibration lane: the part of the card no core can ever cover -----------
LANE_Y, LANE_H = 24.0, 40.0
LANE_TOP = LANE_Y

# 10 mm comb: 20 marks, 190 mm span, so a 20x longer baseline than a single
# interval. Pitch is what we fit, so a 7 mm ruler-origin offset is irrelevant.
COMB10_X0, COMB10_N, COMB10_PITCH = 20.0, 17, 10.0   # 160 mm span, ends at 180 mm
COMB10_Y, COMB10_TICK_H = LANE_Y + 7.0, 9.0
# 50 mm comb: independent pitch. If 10 mm and 50 mm disagree, say so.
COMB50_X0, COMB50_N, COMB50_PITCH = 14.0, 4, 50.0
COMB50_Y, COMB50_TICK_H = LANE_Y + 26.0, 7.0

# --- working strip -----------------------------------------------------------
STRIP_X, STRIP_Y, STRIP_W, STRIP_H = 11.0, 80.0, 188.0, 38.0
AXIS_Y = STRIP_Y + 23.0
BAYS = 5
BAY_W = STRIP_W / BAYS
BAY_CX = [STRIP_X + BAY_W * (i + 0.5) for i in range(BAYS)]
TICK_LABELS = "ABCDE"

# --- cross-check only: reports print drift, is not trusted for scale ---------
BAR_X0, BAR_LEN, BAR_Y, BAR_H = 20.0, 100.0, 134.0, 4.0
BAR_LABEL = "printed cross-check only - not used for scale"

# --- resolution target: reports true optical px/mm at the core plane ---------
WEDGE_X0, WEDGE_Y, WEDGE_W, WEDGE_H = 20.0, 160.0, 88.0, 7.0
BAR_PERIODS = (2.0, 1.0, 0.7, 0.5, 0.35)
# grey/white flat-field patches, for white balance and exposure checks
GREY_X0, GREY_Y, GREY_W, GREY_H = 20.0, 180.0, 40.0, 14.0
WHITE_X0, WHITE_Y, WHITE_W, WHITE_H = 66.0, 180.0, 42.0, 14.0

# --- instructions ------------------------------------------------------------
NOTE_Y = 210.0
NOTES = (
    "1  Lay the plastic ruler across the top lane. Any straight ruler with 1 mm graduations works.",
    "2  The app measures the ruler's millimetre graduations, not this card, for scale.",
    "3  Ruler origin does not matter: only the graduation pitch is used. Edge and 0 mark may differ.",
    "4  Place one core per bay, A to E. Keep the core off the top lane entirely.",
    "5  Align the ruler's long edge with the table edge so every photo is shot the same way.",
    "6  This card's 100 mm bar is a drift check only. It is printed, so it may be inaccurate.",
)

GR = 150.0 / 25.4              # user units per mm


def mm(v: float) -> str:
    return f"{v * GR:.3f}"


def rect(x, y, w, h, fill, extra=""):
    return (f'<rect x="{mm(x)}" y="{mm(y)}" width="{mm(w)}" '
            f'height="{mm(h)}" fill="{fill}" {extra}/>')


def text(x, y, s, size, fill=BLACK, weight="normal", anchor="start"):
    safe = (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    return (f'<text x="{mm(x)}" y="{mm(y)}" font-family="DejaVu Sans, '
            f'Helvetica, Arial, sans-serif" font-size="{mm(size)}" '
            f'fill="{fill}" font-weight="{weight}" text-anchor="{anchor}">'
            f'{safe}</text>')


def comb(x0, y, n, pitch, tick_h, label, sub=""):
    """A graduated tick comb of known pitch.

    Fitted by pitch over the whole span, so it needs no origin and no zero
    mark. Every tenth mark is taller, which gives the app a coarse second
    reference even if the short marks binarise poorly.
    """
    out = []
    for k in range(n):
        h = tick_h * (1.6 if (k + 1) % 5 == 0 else 1.0)
        out.append(rect(x0 + k * pitch, y, STROKE, h, BLACK))
    out.append(rect(x0 - 1.0, y + tick_h + 1.0,
                    (n - 1) * pitch + STROKE + 2.0, 0.6, BLACK))
    # label sits to the right of the comb baseline, on the tick row, so it can
    # never collide with the comb or with the next comb's label
    # Label goes ABOVE the ticks, right-aligned to the comb end, so long
    # text can never run off the right edge of the page.
    out.append(text(x0 + (n - 1) * pitch, y - 1.6, label, 3.0, BLACK,
                    "bold", "end"))
    if sub:
        out.append(text(x0 - 2.0, y - 1.6, sub, 2.2, GREY_TEXT))
    return out


def build() -> str:
    p = []
    p.append(rect(0, 0, W, H, WHITE))

    # --- corner fiducials: homography for perspective, and a page-present test
    for cx, cy in FID_CENTRES:
        p.append(rect(cx - FIDUCIAL / 2, cy - FIDUCIAL / 2, FIDUCIAL, FIDUCIAL, BLACK))
    # a broken ring of mid-grey around each fiducial: helps a human confirm the
    # page is fully in frame, and gives the app a contrast-normalised reference
    for cx, cy in FID_CENTRES:
        r = FIDUCIAL / 2 + 2.2
        p.append(f'<circle cx="{mm(cx)}" cy="{mm(cy)}" r="{mm(r)}" fill="none" '
                 f'stroke="{FAINT}" stroke-width="{mm(0.5)}"/>')

    # --- title
    p.append(text(30, 15.0, "AGE CORE PHOTO CARD  v3", 5.0, BLACK, "bold"))
    p.append(text(180, 15.0, "scale comes from your ruler, not from this page", 2.4,
                  GREY_TEXT, anchor="end"))

    # --- calibration lane -------------------------------------------------
    p.append(rect(9.0, LANE_Y - 3.0, 192.0, LANE_H + 6.0, FAINT))
    p.append(text(11.0, LANE_TOP - 1.4, "CALIBRATION LANE  -  keep clear of cores",
                  2.8, BLACK, "bold"))
    p.extend(comb(COMB10_X0, COMB10_Y, COMB10_N, COMB10_PITCH, COMB10_TICK_H,
                  "10 mm", "160 mm baseline"))
    p.extend(comb(COMB50_X0, COMB50_Y, COMB50_N, COMB50_PITCH, COMB50_TICK_H,
                  "50 mm", "second pitch"))

    p.append(text(20.0, LANE_Y + LANE_H + 5.6,
                  "10 mm comb: 17 marks, 160 mm baseline.  50 mm comb is a second "
                  "pitch - if the two disagree, print or lens is at fault.",
                  2.2, GREY_TEXT))

    # --- working strip ----------------------------------------------------
    p.append(rect(STRIP_X, STRIP_Y, STRIP_W, STRIP_H, FAINT))
    p.append(text(STRIP_X, STRIP_Y - 2.0, "CORE BAYS", 2.8, BLACK, "bold"))
    for i, cx in enumerate(BAY_CX):
        p.append(text(cx, STRIP_Y + 8.0, TICK_LABELS[i], 5.0, GREY_TEXT,
                      "normal", "middle"))
        if i:
            p.append(rect(STRIP_X + BAY_W * i, STRIP_Y, 0.6, STRIP_H, BLACK))
    p.append(rect(STRIP_X, AXIS_Y, STRIP_W, STROKE, BLACK))
    for i, cx in enumerate(BAY_CX):
        p.append(rect(cx - 0.5, AXIS_Y - 3.0, STROKE, 6.0, BLACK))
    p.append(text(STRIP_X, AXIS_Y + 5.0,
                  "rings run across this line", 2.2, GREY_TEXT))

    # --- printed cross-check bar (drift report only) ----------------------
    p.append(text(BAR_X0, BAR_Y - 2.0, "PRINTED CROSS-CHECK - not used for scale",
                  2.6, BLACK, "bold"))
    p.append(rect(BAR_X0, BAR_Y, BAR_LEN, BAR_H, BLACK))
    for k in range(11):
        p.append(rect(BAR_X0 + k * 10.0 - 0.4, BAR_Y - 2.2, 0.8, 2.2, BLACK))
    p.append(text(BAR_X0 + BAR_LEN + 2.0, BAR_Y + BAR_H - 0.8,
                  "nominal 100.00 mm", 2.4, GREY_TEXT))
    p.append(text(BAR_X0, BAR_Y + BAR_H + 4.0, BAR_LABEL, 2.2, GREY_TEXT))

    # --- resolution target ----------------------------------------------
    p.append(text(WEDGE_X0, WEDGE_Y - 2.0, "OPTICAL RESOLUTION TARGET", 2.6, BLACK, "bold"))
    gw = WEDGE_W / len(BAR_PERIODS)
    for i, per in enumerate(BAR_PERIODS):
        gx = WEDGE_X0 + i * gw
        pitch = per / 2.0
        n = max(1, int(round((gw - 1.5) / pitch)))
        if n * pitch > (gw - 1.5):
            n = max(1, n - 1)
        for k in range(n):
            p.append(rect(gx + 0.75 + k * pitch, WEDGE_Y, pitch, WEDGE_H, BLACK))
        p.append(text(gx + gw / 2.0, WEDGE_Y + WEDGE_H + 3.2, f"{per:g}", 2.2,
                      GREY_TEXT, "normal", "middle"))
    p.append(text(WEDGE_X0, WEDGE_Y + WEDGE_H + 7.0,
                  "mm per black/white pair - the finest group the app can still "
                  "resolve is the true limit at the core's distance", 2.2, GREY_TEXT))

    # --- grey and white patches -----------------------------------------
    p.append(rect(GREY_X0, GREY_Y, GREY_W, GREY_H, FAINT))
    p.append(rect(WHITE_X0, WHITE_Y, WHITE_W, WHITE_H, WHITE,
                  f'stroke="{FAINT}" stroke-width="{mm(0.4)}"'))
    p.append(text(GREY_X0, GREY_Y + GREY_H + 4.0, "grey ref", 2.2, GREY_TEXT))
    p.append(text(WHITE_X0, WHITE_Y + WHITE_H + 4.0, "white ref", 2.2, GREY_TEXT))

    # --- instructions ----------------------------------------------------
    y = NOTE_Y
    p.append(text(11, y, "HOW TO USE", 3.2, BLACK, "bold"))
    y += 6.0
    for line in NOTES:
        p.append(text(11, y, line, 2.5, BLACK))
        y += 5.0

    y += 3.0
    p.append(text(11, y, "Bar period, mm - do NOT scale to fit. Matte paper only.",
                  2.4, GREY_TEXT))

    p.append(text(11, H - 8.0,
                  "v3: scale is measured from a physical ruler. A printed card "
                  "cannot be its own reference - this one printed 7% small.",
                  2.3, GREY_TEXT))

    # Physical size MUST be declared in mm. Emitting bare user-unit numbers
    # makes renderers assume 96 dpi px, which silently produced a 328x464 mm
    # page instead of A4. The viewBox carries the internal coordinate scale.
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}mm" '
            f'height="{H}mm" viewBox="0 0 {mm(W)} {mm(H)}">\n'
            + "\n".join(p) + "\n</svg>\n")


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "..", "card")
    os.makedirs(out, exist_ok=True)
    svg = build()
    svg_path = os.path.join(out, "age_core_photo_card_v3.svg")
    with open(svg_path, "w") as f:
        f.write(svg)
    print(f"wrote {svg_path}")

    pdf_path = os.path.join(out, "age_core_photo_card_v3.pdf")
    png_path = os.path.join(out, "preview_v3.png")
    for cmd in (
        ["inkscape", svg_path, "--export-type=pdf",
         f"--export-filename={pdf_path}", "--export-dpi=600"],
        ["inkscape", svg_path, "--export-type=png",
         f"--export-filename={png_path}", "--export-dpi=150"],
    ):
        if shutil.which(cmd[0]):
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0:
                print(f"wrote {cmd[-1].split('=')[-1]}")
            else:
                print(f"{cmd[0]} failed: {r.stderr.strip()[:200]}")
        else:
            print(f"{cmd[0]} not available, skipped")


if __name__ == "__main__":
    main()
