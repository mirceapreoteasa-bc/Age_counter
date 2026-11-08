#!/usr/bin/env python3
"""Generate the AGE CORE PHOTO CARD as print-ready A4 vector SVG.

Designed against four measured failures, not just a missing ruler:

1. Scale extraction failed on three separate photo sets (ruler ticks,
   printed digits, and an LLM-free heuristic all gave inconsistent px/mm).
   -> a 100 mm bar with 10 mm divisions, printed ON the support strip so it
      lies in the same plane as the core.
2. Path placement turned out to be the dominant error source: the same core
   counted 73 or 103 depending on a 100 px shift of the sampling line.
   -> a printed axis line plus five lettered section ticks, so the app can
      derive the sampling path from the card instead of from image heuristics.
3. Core localisation kept latching onto the wooden table, the plastic ruler
   and tan paper, all of which are tan. -> a high-chroma blue support strip:
      wood is warm (R>B>G), the strip is cool, so the core segments on colour
      alone regardless of thickness.
4. Grooves from the increment borer are helicoidal at ~30 degrees to the
   rings, so they drift across the core while rings do not.
   -> the axis line gives a reference direction to measure that drift.

Plus a Siemens star, because the best microscope images come from the core's
lateral edges and those are exactly where a photo falls out of focus: the star
sits in the same plane as the core and lets the app report the true optical
resolution instead of assuming the nominal pixel count.

PRINT AT 100%. Do not use "fit to page" or "shrink to fit". Matte paper only -
gloss stock throws a specular highlight off the raised grain, which is the very
thing the app has to see past.
"""

import math
import os

W, H = 210.0, 297.0            # A4 in millimetres
STRIP = "#D8D8D8"              # light grey, printed with BLACK toner only.
                               # A colour cartridge is not required: the core is
                               # the only warm, mid-tone object on the page.
BLACK = "#000000"
WHITE = "#FFFFFF"
GREY_TEXT = "#404040"
FAINT = "#BFBFBF"

STRIP_X, STRIP_Y, STRIP_W, STRIP_H = 11.0, 36.0, 188.0, 38.0
AXIS_Y = STRIP_Y + 23.0
BAYS = 5
BAY_W = STRIP_W / BAYS
TICK_X = [STRIP_X + BAY_W * (i + 1) for i in range(BAYS - 1)]
TICK_LABELS = "ABCDE"
BAY_CX = [STRIP_X + BAY_W * (i + 0.5) for i in range(BAYS)]
SCALE_X0, SCALE_LEN, SCALE_Y = 25.0, 100.0, STRIP_Y + 30.0

BAR_X, BAR_Y, BAR_W, BAR_H = 14.0, 96.0, 40.0, 12.0
ID1_W, ID1_H = 85.60, 53.98        # ISO/IEC 7810 ID-1 (bank card)
BAR_PERIODS = (2.0, 1.0, 0.7, 0.5, 0.35)   # mm per black/white pair
WEDGE_X0, WEDGE_Y, WEDGE_W, WEDGE_H = 60.0, 96.0, 110.0, 9.0
WHITE_X0, WHITE_Y, WHITE_W, WHITE_H = 60.0, 110.0, 110.0, 9.0

GR = 150.0 / 25.4              # user units per mm


def mm(v: float) -> str:
    return f"{v * GR:.3f}"


def rect(x, y, w, h, fill, extra=""):
    return (f'<rect x="{mm(x)}" y="{mm(y)}" width="{mm(w)}" '
            f'height="{mm(h)}" fill="{fill}" {extra}/>')


def text(x, y, s, size, fill=BLACK, weight="normal", anchor="start"):
    return (f'<text x="{mm(x)}" y="{mm(y)}" font-family="DejaVu Sans, '
            f'Helvetica, Arial, sans-serif" font-size="{mm(size)}" '
            f'fill="{fill}" font-weight="{weight}" text-anchor="{anchor}">'
            f'{s}</text>')


def line_pair_groups(x, y, w, h, periods):
    """Resolution target.

    The app measures which group is the finest one it can still resolve. That
    gives the true optical resolution in px/mm at the core's own distance,
    which the nominal sensor resolution does not tell you - and it is how we
    find out whether the core's edges fell out of focus.
    """
    out = []
    gw = w / len(periods)
    for i, per in enumerate(periods):
        gx = x + i * gw
        pitch = per / 2.0                      # one black bar per pitch
        n = max(1, int(round((gw - 1.0) / pitch)))
        if n * pitch > (gw - 1.0):
            n = max(1, n - 1)
        used = n * pitch
        for k in range(n):
            out.append(rect(gx + 0.5 + k * pitch, y, pitch, h, BLACK))
        out.append(text(gx + gw / 2.0, y + h + 2.4, f"{per:g}", 2.2, GREY_TEXT,
                        "normal", "middle"))
    return out


def build() -> str:
    p = []
    # page
    p.append(rect(0, 0, W, H, WHITE))

    # --- registration marks: rotation and perspective, and a page-present test
    for cx, cy in ((11, 11), (192, 11), (11, 279), (192, 279)):
        p.append(rect(cx, cy, 7, 7, BLACK))

    # --- header
    p.append(text(11, 28, "AGE CORE PHOTO CARD", 6.0, BLACK, "bold"))
    p.append(text(11, 33.2,
                  "Print at 100% - do NOT scale to fit - matte paper - "
                  "black only is fine - "
                  "align the ruler edge of this sheet with the table edge",
                  2.5, GREY_TEXT))

    # --- the support strip
    p.append(rect(STRIP_X, STRIP_Y, STRIP_W, STRIP_H, STRIP))
    # section ticks divide the strip into five lettered bays; lay one section
    # of the core in each bay so the app knows the sampling path
    for x in TICK_X:
        p.append(rect(x - 1.3, STRIP_Y, 2.6, STRIP_H, BLACK))
    for i, cx in enumerate(BAY_CX):
        p.append(text(cx, STRIP_Y + 7.0, TICK_LABELS[i], 5.0, BLACK, "bold"))
    # axis line: the sampling direction the app should use
    p.append(f'<line x1="{mm(STRIP_X + 2)}" y1="{mm(AXIS_Y)}" '
             f'x2="{mm(STRIP_X + STRIP_W - 2)}" y2="{mm(AXIS_Y)}" '
             f'stroke="{BLACK}" stroke-width="{mm(0.5)}"/>')
    # scale bar, printed in the core plane
    p.append(rect(SCALE_X0, SCALE_Y, SCALE_LEN, 3.0, BLACK))
    for i in range(0, 11):
        x = SCALE_X0 + i * 10.0
        p.append(rect(x, SCALE_Y, (0.5 if i % 5 else 1.4), 3.0, WHITE))
        p.append(text(x, SCALE_Y - 0.8, str(i * 10), 2.1, BLACK, "normal",
                      "middle"))
    p.append(text(SCALE_X0 + SCALE_LEN + 3, SCALE_Y + 2.6, "100 mm", 3.0,
                  BLACK, "bold"))
    p.append(text(STRIP_X + 3, AXIS_Y - 1.6,
                  "lay the core along this line", 2.3, BLACK))

    # --- optical calibration, same plane as the core
    p.append(text(11, 90, "OPTICAL CALIBRATION - same plane as the core",
                  3.0, BLACK, "bold"))
    p.append(text(BAR_X, BAR_Y - 1.4, "finest resolvable bar group", 2.4,
                  GREY_TEXT))
    p += line_pair_groups(BAR_X, BAR_Y, BAR_W, BAR_H, BAR_PERIODS)
    p.append(text(BAR_X, BAR_Y + BAR_H + 6.0,
                  "bar period, mm - the finest group that is", 2.2, GREY_TEXT))
    p.append(text(BAR_X, BAR_Y + BAR_H + 9.0,
                  "still sharp tells the app the real limit", 2.2, GREY_TEXT))

    # grey step wedge for tone response
    steps = 5
    sw = WEDGE_W / steps
    for i in range(steps):
        v = int(round(255 * (1 - i / (steps - 1))))
        p.append(rect(WEDGE_X0 + i * sw, WEDGE_Y, sw, WEDGE_H,
                      f"rgb({v},{v},{v})", 'stroke="#808080" stroke-width="0.2"'))
    p.append(text(WEDGE_X0, WEDGE_Y - 1.2, "grey step wedge", 2.4, GREY_TEXT))
    # pure white reference
    p.append(rect(WHITE_X0, WHITE_Y, WHITE_W, WHITE_H, WHITE,
                  'stroke="#000000" stroke-width="0.3"'))
    p.append(text(WHITE_X0, WHITE_Y - 1.2, "white reference", 2.4, GREY_TEXT))

    # --- absolute scale check: a bank card outline. A uniform print shrink
    #     cannot be detected from anything printed on the same sheet, because
    #     the whole sheet shrinks together. A real card laid on the outline can
    #     be, so this is the one check that catches a printer set to 95%.
    p.append(f'<rect x="{mm(120)}" y="{mm(150)}" width="{mm(ID1_W)}" '
             f'height="{mm(ID1_H)}" fill="none" stroke="{GREY_TEXT}" '
             f'stroke-width="{mm(0.3)}" stroke-dasharray="2 1.5"/>')
    p.append(text(120, 147, "put a bank card here to check the print scale",
                  2.4, GREY_TEXT))
    p.append(text(120, 150 + ID1_H + 3.0,
                  "ID-1 is 85.60 x 53.98 mm. If your print shrank, this",
                  2.2, GREY_TEXT))
    p.append(text(120, 150 + ID1_H + 6.0,
                  "outline will be smaller than the card and you will see it.",
                  2.2, GREY_TEXT))

    # --- how to use
    p.append(text(11, 136, "HOW TO USE", 3.0, BLACK, "bold"))
    for i, line in enumerate([
        "1  Place the core in ONE bay, along the black axis line. One photo per bay.",
        "2  Keep the core, the 100 mm bar and the bar target in one plane -",
        "     same height off the table. That is what tells the app the real focus.",
        "3  For a broken or short core, photograph only the bays you can fill.",
        "4  Use diffuse light. If you can, cross-polarise: a polarising filter on the",
        "     lens and the lamp turned 90 degrees away. It cancels the sheen off the",
        "     raised grain, which is what hides the ring boundaries.",
        "5  Fill the frame with the core. Unused width costs you pixels per ring.",
        "6  Note the bay letter, so a partial count can be tied to a section.",
        "7  Measure the 100 mm bar with a ruler after the first print and enter the",
        "     TRUE length in the app. Printers commonly shrink by 5-10% and the app",
        "     cannot detect that from anything printed on the same sheet.",
        "8  Shoot RAW if you can. It has 64x the tonal depth of a JPEG, and the ring",
        "     boundaries are close to the JPEG noise floor.",
    ]):
        p.append(text(11, 140 + i * 3.5, line, 2.5, GREY_TEXT))

    # --- notes
    p.append(text(11, 190, "NOTES", 3.0, BLACK, "bold"))
    y = 196.0
    while y < 274:
        p.append(f'<line x1="{mm(11)}" y1="{mm(y)}" x2="{mm(199)}" y2="{mm(y)}" '
                 f'stroke="{FAINT}" stroke-width="{mm(0.25)}"/>')
        y += 6.0
    for i, lab in enumerate(("Date", "Plot / Tree", "Forester", "Species")):
        p.append(text(11 + i * 47, 193, lab, 2.4, GREY_TEXT, "bold"))

    # width/height are the *physical* page size in mm; the viewBox is in user
    # units. Getting these mixed up prints a 1.24 m wide sheet, so they are
    # deliberately written from different sources.
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{W}mm" height="{H}mm" '
            f'viewBox="0 0 {mm(W)} {mm(H)}">\n' + "\n".join(p) + "\n</svg>\n")


if __name__ == "__main__":
    out_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "card")
    os.makedirs(out_dir, exist_ok=True)
    svg = os.path.join(out_dir, "age_core_photo_card_A4.svg")
    with open(svg, "w", encoding="utf-8") as fh:
        fh.write(build())
    print(svg)
