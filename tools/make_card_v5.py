#!/usr/bin/env python3
"""Generate the AGE CORE PHOTO CARD v5 - for faded prints and a 5 mm core scale.

A4 rotated: 297 mm across, 210 mm tall.

This card exists because of four measured failures on v4, not preference.

* **The printed scale was abandoned, because the print cannot be trusted.**
  v2's 100 mm bar printed 93 mm. Worse, the bar, the bay pitch and the outline
  were all wrong by the same 7%, so no two printed features can ever
  cross-check one another. And the field printer runs low on toner, so printed
  tone is not a reliable threshold either. So the primary scale is now a
  physical length that never came out of the printer: **the 5 mm increment
  core's own diameter.** Because v5 puts the core against solid black, that
  diameter is measurable to a few pixels, where on v4 the same core measured
  6.7 mm on one camera and 9.4 mm on another. The printed 100 mm bar survives
  only as a labelled drift report.

* **The contact shadow was the real reason core separation failed.** A
  vertical luminance slice through a core on v4 read: card ~190, core top edge
  152, core 127 to 85, highlight 73 to 58, then shadow 63 to 67. The shadow is
  as dark as the wood, so the core's lower edge does not exist as an edge and
  every threshold either swallowed the card's halftone or bled into the
  shadow. v5 inverts this: the channel is printed **solid black** and the pale
  core is the bright object. The contact shadow now falls on black where it
  cannot be seen, and the core is bounded by black on both sides, which is the
  highest-contrast boundary available and is unaffected by white balance.
  Channel B is the same idea inverted for genuinely dark cores.

* **The card calibrates its own threshold from the print in the photo.** Two
  25 mm patches, one solid black and one solid white, sit in frame. The app
  reads the actual black and white levels off the print it was given and sets
  its threshold from those, instead of from a constant tuned on one printer.
  A faded print is then self-correcting rather than fatal.

* **Length over width.** Core fragments run past 50 mm, so the channel is
  273 mm long against a 5 mm core: 54:1, and the app refuses any separated
  region under 10:1 rather than counting a sliver.

Faded-print rules, applied throughout: solid black only, no greys and no tints;
no stroke below 1.2 mm; no feature smaller than 4 mm except the position
ticks; digits avoided in favour of a regular tick ladder, because printed
cm digits could not be segmented reliably. The 5.00 mm bar is printed so that
the app can compare the printed 5 mm against the measured core and report the
disagreement instead of hiding it.

PRINT LANDSCAPE AT 100%. Do not use "fit to page" or "shrink to fit". Matte
paper only. Check the toner patches before trusting a run of photos.
"""

import os
import subprocess

# --- page, A4 rotated -------------------------------------------------------
W, H = 297.0, 210.0

BLACK = "#000000"
WHITE = "#FFFFFF"

# Every stroke at least this wide. v4 measured 1.2 mm as the floor for a weak
# cartridge to binarise cleanly; v5 does not go below it anywhere.
STROKE = 1.2
RULE = 1.6

FIDUCIAL = 8.0
FID_CENTRES = ((14.0, 14.0), (283.0, 14.0), (14.0, 196.0), (283.0, 196.0))

# --- self-calibration patches: the app reads the print's real black and white
PATCH = 25.0
PATCH_BLACK = (196.0, 8.0)
PATCH_WHITE = (228.0, 8.0)

# --- ruler lane: a core can never reach this band ---------------------------
LANE_X, LANE_Y, LANE_W, LANE_H = 12.0, 50.0, 273.0, 18.0
COMB50_X0, COMB50_N, COMB50_PITCH = 20.0, 5, 50.0
COMB50_Y, COMB50_H = LANE_Y + 3.0, 7.0

# --- channels ---------------------------------------------------------------
# A: solid black, for pale cores. The core is the bright object.
CH_A_X, CH_A_Y, CH_A_W, CH_A_H = 12.0, 78.0, 273.0, 16.0
# B: white with heavy black rules, for dark cores.
CH_B_X, CH_B_Y, CH_B_W, CH_B_H = 12.0, 110.0, 273.0, 16.0

# Position ticks every 50 mm above channel A. Reference only; the count is
# taken over the whole core, not per bay.
TICK_X0, TICK_N, TICK_PITCH = 12.0, 5, 50.0
TICK_Y, TICK_H = 96.0, 4.0

# --- printed references: drift report only, never the scale ----------------
BAR100_X, BAR100_Y, BAR100_W, BAR100_H = 12.0, 136.0, 100.0, 5.0
BAR5_X, BAR5_Y, BAR5_W, BAR5_H = 12.0, 150.0, 40.0, 5.0

CORE_DIA_MM = 5.0


def esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x, y, s, size=4.0, anchor="start", weight="normal"):
    return (f'<text x="{x}" y="{y}" font-family="DejaVu Sans, Helvetica, Arial, sans-serif" '
            f'font-size="{size}" fill="{BLACK}" text-anchor="{anchor}" '
            f'font-weight="{weight}">{esc(s)}</text>')


def build() -> str:
    p = []
    p.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}mm" '
             f'height="{H}mm" viewBox="0 0 {W} {H}">')
    p.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="{WHITE}"/>')

    # --- fiducials, on a non-square grid so perspective is correctable -------
    for cx, cy in FID_CENTRES:
        p.append(f'<circle cx="{cx}" cy="{cy}" r="{FIDUCIAL/2:.2f}" fill="{BLACK}"/>')

    # --- self-calibration patches -------------------------------------------
    bx, by = PATCH_BLACK
    p.append(f'<rect x="{bx}" y="{by}" width="{PATCH}" height="{PATCH}" fill="{BLACK}"/>')
    wx, wy = PATCH_WHITE
    p.append(f'<rect x="{wx}" y="{wy}" width="{PATCH}" height="{PATCH}" fill="{WHITE}" '
             f'stroke="{BLACK}" stroke-width="{RULE}"/>')
    p.append(text(bx, by + PATCH + 5.0, "TONER CHECK", 3.6, weight="bold"))
    p.append(text(wx, wy + PATCH + 5.0, "WHITE REF", 3.6, weight="bold"))
    p.append(text(196.0, 42.0, "App reads these to set its own threshold.", 3.0))

    # --- title ---------------------------------------------------------------
    p.append(text(12.0, 28.0, "AGE CORE PHOTO CARD v5", 6.5, weight="bold"))
    p.append(text(12.0, 34.0,
                  "Scale is the 5 mm core itself. The print is a target, not a ruler.",
                  3.6))

    # --- ruler lane ----------------------------------------------------------
    p.append(f'<rect x="{LANE_X}" y="{LANE_Y}" width="{LANE_W}" height="{LANE_H}" '
             f'fill="{WHITE}" stroke="{BLACK}" stroke-width="{STROKE}"/>')
    for i in range(COMB50_N):
        x = COMB50_X0 + i * COMB50_PITCH
        p.append(f'<rect x="{x}" y="{COMB50_Y}" width="{RULE}" height="{COMB50_H}" fill="{BLACK}"/>')
    p.append(text(LANE_X, LANE_Y - 3.0,
                  "RULER LANE - lay an opaque ruler here, never over a core", 3.4, weight="bold"))
    p.append(text(LANE_X + 2.0, LANE_Y + LANE_H - 2.0,
                  "50 mm comb - check only, not used for scale", 3.0))

    # --- position ticks ------------------------------------------------------
    for i in range(TICK_N):
        x = TICK_X0 + i * TICK_PITCH
        p.append(f'<rect x="{x}" y="{TICK_Y}" width="{STROKE}" height="{TICK_H}" fill="{BLACK}"/>')

    # --- channel A: solid black, pale core is the bright object --------------
    p.append(f'<rect x="{CH_A_X}" y="{CH_A_Y}" width="{CH_A_W}" height="{CH_A_H}" fill="{BLACK}"/>')
    p.append(text(CH_A_X, CH_A_Y - 3.0,
                  "CHANNEL A - pale cores. Lay the core on the black, ends to both edges.",
                  3.4, weight="bold"))

    # --- channel B: white, for dark cores -----------------------------------
    p.append(f'<rect x="{CH_B_X}" y="{CH_B_Y}" width="{CH_B_W}" height="{CH_B_H}" fill="{WHITE}"/>')
    p.append(f'<rect x="{CH_B_X}" y="{CH_B_Y}" width="{CH_B_W}" height="{RULE}" fill="{BLACK}"/>')
    p.append(f'<rect x="{CH_B_X}" y="{CH_B_Y + CH_B_H - RULE}" width="{CH_B_W}" height="{RULE}" fill="{BLACK}"/>')
    p.append(f'<rect x="{CH_B_X}" y="{CH_B_Y}" width="{RULE}" height="{CH_B_H}" fill="{BLACK}"/>')
    p.append(f'<rect x="{CH_B_X + CH_B_W - RULE}" y="{CH_B_Y}" width="{RULE}" height="{CH_B_H}" fill="{BLACK}"/>')
    p.append(text(CH_B_X, CH_B_Y - 3.0,
                  "CHANNEL B - dark cores. Core must touch both black rules.",
                  3.4, weight="bold"))

    # --- printed references: drift report only -------------------------------
    p.append(f'<rect x="{BAR100_X}" y="{BAR100_Y}" width="{BAR100_W}" height="{BAR100_H}" fill="{BLACK}"/>')
    p.append(text(BAR100_X, BAR100_Y - 2.5,
                  "100.00 mm nominal - DRIFT CHECK ONLY, not used for scale", 3.2, weight="bold"))
    p.append(f'<rect x="{BAR5_X}" y="{BAR5_Y}" width="{BAR5_W}" height="{BAR5_H}" fill="{BLACK}"/>')
    p.append(text(BAR5_X, BAR5_Y - 2.5,
                  "5.00 mm nominal - app compares this with the measured core", 3.2, weight="bold"))

    # --- how to use ----------------------------------------------------------
    lines = [
        "1  Core scale: the app measures the core's own 5 mm diameter. Print size does not enter the count.",
        "2  Use CHANNEL A for a pale core, CHANNEL B for a dark one. The whole core should lie in one channel.",
        "3  Cores under 10:1 length to width are refused rather than counted.",
        "4  Ruler in the lane is an extra check. It is not needed for the count.",
        "5  One photo per channel. Overlapping shots are not stitched.",
        "6  Count the whole core at once. The 50 mm ticks are position reference only.",
        "7  The two patches at top right let the app set its own threshold from this print.",
    ]
    y = 162.0
    for ln in lines:
        p.append(text(24.0, y, ln, 3.4))
        y += 6.0

    p.append(text(24.0, 206.0,
                  f"Channel {CH_A_W:.0f} mm long against a {CORE_DIA_MM:.0f} mm core = "
                  f"{CH_A_W/CORE_DIA_MM:.0f}:1.  Solid black only, no greys, minimum stroke 1.2 mm.",
                  3.0))
    p.append("</svg>")
    return "\n".join(p) + "\n"


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(os.path.dirname(here), "card")
    os.makedirs(out, exist_ok=True)

    svg_path = os.path.join(out, "age_core_card_v5.svg")
    with open(svg_path, "w") as f:
        f.write(build())
    print(f"wrote {svg_path}")

    pdf_path = os.path.join(out, "age_core_card_v5.pdf")
    png_path = os.path.join(out, "preview_v5.png")
    for args, dest in (
        (["inkscape", svg_path, "--export-type=pdf",
          f"--export-filename={pdf_path}", "--export-dpi=600"], pdf_path),
        (["inkscape", svg_path, "--export-type=png",
          f"--export-filename={png_path}", "--export-width=2400"], png_path),
    ):
        try:
            subprocess.run(args, check=True, capture_output=True)
            print(f"wrote {dest}")
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            print(f"FAILED {dest}: {exc}")


if __name__ == "__main__":
    main()
