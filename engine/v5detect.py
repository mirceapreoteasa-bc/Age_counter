#!/usr/bin/env python3
"""The v5 detection path: separate the core, take scale from the 5 mm core.

This is the implementation of card v5's promise, tested against a synthetic v5
image built by tools/make_v5_test_image.py so that the scale is known.

The order matters and follows the card, not convenience:

  1. Read the printed black and white patches, so the threshold comes from
     this print rather than from a constant. A faded print therefore
     self-corrects instead of silently inverting the decision.
  2. Find the channel and, inside it, the core as a bright object on black.
     Nothing outside the core is ever measured, which is what removes the
     card's printed halftone from the count.
  3. Take the scale from the core's own diameter. The print never enters the
     count, so shrinkage and fading cannot move the answer.
  4. Refuse anything under MIN_RATIO length to width, because a sliver of core
     cannot carry a count worth trusting.

Usage:
    python3 engine/v5detect.py IMAGE.png [--debug OUT.png]
"""

import argparse
import math

import numpy as np
from PIL import Image, ImageDraw

CORE_DIA_MM = 5.0
MIN_RATIO = 10.0          # a core fragment must be at least this long/width
MIN_DARK_PATCH_FRAC = 0.0008   # of the frame, for the calibration patches

# Ring contrast, as a fraction of the print's own black-to-white spread.
#
# Measured across nine photos of one real core, the photos the eye could count
# gave a de-trended profile sd of 10 to 14 against a 255-level print, and the
# two that were soft and out of focus gave 2.5 to 3.1. Anything under about a
# twentieth of the print's range has no ring signal worth counting, and the
# correct answer is to decline rather than to return the two or three bands
# that survive the noise.
MIN_CONTRAST = 0.020


class Report(dict):
    pass


def _otsu(v):
    """Split a 1-D signal into two levels. No absolute threshold anywhere."""
    hist, edges = np.histogram(v, bins=256)
    c = np.cumsum(hist)
    tot = c[-1]
    centres = edges[:-1] + (edges[1] - edges[0]) / 2
    w0 = c
    w1 = tot - c
    m0 = (w0 * centres).sum() / max(1.0, w0[-1])
    m1 = (w1 * centres).sum() / max(1.0, w1[-1])
    return float(centres[int(np.argmax(w0 * w1 * (m0 - m1) ** 2))])


def _longest_run_below(sig, thr):
    idx = np.where(sig < thr)[0]
    if len(idx) == 0:
        return None
    blocks = []
    start = idx[0]
    prev = idx[0]
    for i in idx[1:]:
        if i - prev > 2:
            blocks.append((start, prev))
            start = i
        prev = i
    blocks.append((start, prev))
    return max(blocks, key=lambda b: b[1] - b[0])


def _runs(mask):
    """Contiguous True runs as (start, end_inclusive) pairs."""
    out = []
    x = 0
    n = len(mask)
    while x < n:
        if mask[x]:
            s = x
            while x < n and mask[x]:
                x += 1
            out.append((s, x - 1))
        else:
            x += 1
    return out


def _largest_box(mask):
    """Biggest solid rectangle region described by a coarse mask.

    Returns (y0, y1, x0, x1) of the longest run of rows whose masked fraction
    is high, then the widest run of columns inside it. Good enough for the
    rectangular features on the card and cheap.
    """
    rowfrac = mask.mean(axis=1)
    rows = np.where(rowfrac > 0.5)[0]
    if len(rows) == 0:
        return None
    blocks = []
    y = rows[0]
    s = rows[0]
    for v in rows[1:]:
        if v - y > 8:
            blocks.append((s, y))
            s = v
        y = v
    blocks.append((s, y))
    y0, y1 = max(blocks, key=lambda b: (b[1] - b[0]) * mask.shape[1])
    inner = mask[y0:y1 + 1]
    colfrac = inner.mean(axis=0)
    cols = np.where(colfrac > 0.5)[0]
    if len(cols) == 0:
        return None
    x0, x1 = cols.min(), cols.max()
    return int(y0), int(y1), int(x0), int(x1)


def detect(path, debug_out=None, min_ratio=MIN_RATIO):
    """Detect on the photo as shot, trying both core orientations.

    The card is landscape but it gets photographed in portrait half the time,
    and then the core runs vertically. Every step below assumes the core runs
    across the frame, so the whole detection is run twice: once as shot, once
    transposed, and whichever produces the more core-like answer wins. Guessing
    the rotation instead would be wrong half the time.
    """
    best = None
    for transpose in (False, True):
        r = _detect_oriented(path, transpose, min_ratio)
        if r is None:
            continue
        r["orientation"] = "transposed" if transpose else "as shot"
        score = r.pop("_score", 0.0)
        if r.get("_box") is None:
            score = -1.0
        if best is None or score > best[0]:
            best = (score, r)
    if best is None:
        out = Report()
        out["error"] = "no core found in either orientation"
        return out
    rep = best[1]
    if debug_out:
        if rep.get("_box") is None:
            return rep
        im = Image.open(path).convert("RGB")
        d = ImageDraw.Draw(im)
        x0, x1, y0, y1 = rep["_box"]
        if rep["orientation"] == "transposed":
            x0, x1, y0, y1 = y0, y1, x0, x1
        d.rectangle([x0, y0, x1, y1], outline=(255, 0, 255), width=6)
        im.save(debug_out)
        rep["debug"] = debug_out
    rep.pop("_box", None)
    return rep


def _detect_oriented(path, transpose, min_ratio):
    rep = Report()
    im = Image.open(path).convert("RGB")
    a = np.asarray(im, dtype=float)
    H, W, _ = a.shape
    if transpose:
        a = np.transpose(a, (1, 0, 2))
        H, W = W, H
    lum = 0.299 * a[:, :, 0] + 0.587 * a[:, :, 1] + 0.114 * a[:, :, 2]
    rep["image"] = "%dx%d" % (W, H)
    rep["_box"] = None
    rep["_score"] = -1.0     # an early refusal must never win the vote

    # --- 1. printed black and white levels --------------------------------
    lo = np.percentile(lum, 0.5)
    hi = np.percentile(lum, 99.5)
    rep["coverage"] = 1.0
    black_patch = lum < lo + 0.35 * (np.percentile(lum, 50) - lo)
    wp = _largest_box(black_patch)
    # The white level is the paper itself. A printed white patch is redundant
    # on white paper and cannot be told apart from it, so the patch earns its
    # place by carrying a black border (locatable) rather than by being a level
    # reference. The black patch is the one that carries real information: it
    # tells the app how dark THIS print actually got.
    rep["black_level"] = round(float(lo), 1)
    rep["white_level"] = round(float(hi), 1)
    rep["black_patch_found"] = wp is not None
    # Two thresholds, both derived from this print.
    #
    # The core is a mid-tone object: the same wood measured 58 to 137 in the
    # field. A mid-grey threshold of 115 therefore erased the darker half of
    # the core entirely. Against black the core is simply "not black", so the
    # core threshold sits low. The channel threshold has to sit lower still, or
    # the printed text and fiducials get selected instead of the channel.
    drop = hi - lo
    thr_chan = lo + 0.12 * drop
    thr_core = lo + 0.25 * drop
    rep["threshold_channel"] = round(float(thr_chan), 1)
    rep["threshold_core"] = round(float(thr_core), 1)
    if drop < 25:
        rep["error"] = ("print has too little contrast: black/white spread only "
                        "%.0f levels, so no threshold can be trusted" % (hi - lo))
        return rep

    # --- 2. the channel, found from ROW MEANS -------------------------------
    #
    # Not from a per-pixel threshold. This print is striped and faded: the
    # channel's row mean is 52 against paper at 215, but individual pixels in
    # the same row run from 0 to 236, so any test asking for most pixels to be
    # below a fixed level fails on exactly the print that needs the card's
    # self-calibration. Thresholding the row means with Otsu asks only "which
    # rows are dark overall", which a faded fill still answers correctly.
    rowmean = lum.mean(axis=1)
    rep["paper_level"] = round(float(np.percentile(rowmean, 90)), 1)
    rep["darkest_rows"] = round(float(rowmean.min()), 1)
    blk = _longest_run_below(rowmean, _otsu(rowmean))
    if blk is None or blk[1] - blk[0] < 8:
        rep["error"] = "no channel: no run of dark rows"
        return rep
    cy0, cy1 = blk
    rep["channel"] = "rows %d..%d (%d px tall)" % (cy0, cy1, cy1 - cy0 + 1)

    # --- 3. the core: brighter than the channel it sits in ------------------
    #
    # Found by per-column bright runs, with the cut taken from the channel's
    # own darkness rather than a fixed number. The channel's floor and the
    # paper's level are both measured in this photo, so a faded print moves
    # both ends of the cut and the core is still found.
    chan_region = lum[cy0:cy1 + 1, :]
    dark_level = float(np.percentile(chan_region, 20))
    paper_level = float(np.percentile(lum, 90))
    core_thr = dark_level + 0.45 * (paper_level - dark_level)
    rep["channel_floor"] = round(dark_level, 1)
    rep["core_threshold"] = round(core_thr, 1)

    colmean = chan_region.mean(axis=0)
    hot_cols = np.where(colmean > core_thr)[0]
    if len(hot_cols) < 30:
        rep["error"] = "channel found but nothing brighter than the cut in it"
        return rep
    cx0, cx1 = int(hot_cols.min()), int(hot_cols.max())

    centres, extents = [], []
    for x in range(cx0, cx1 + 1):
        col = chan_region[:, x]
        bright = col > core_thr
        if not bright.any():
            continue
        rs = _runs(bright)
        if not rs:
            continue
        r = max(rs, key=lambda q: q[1] - q[0])
        if r[1] - r[0] + 1 < 3:
            continue
        centres.append(x)
        extents.append(r[0] + r[1])
    if len(centres) < 30:
        rep["error"] = "core columns found but no usable vertical extent"
        return rep
    dia_px = int(round(float(np.median(extents)) / 2.0)) * 2
    centre_row = float(np.median(extents)) / 2.0
    len_px = len(centres)
    # A core is much thinner than the channel that holds it. Without this, a
    # "core" that fills its whole channel scores a small positive ratio and can
    # beat the correct orientation, which is a different failure from the one
    # it was meant to prevent.
    chan_h = cy1 - cy0 + 1
    if dia_px > 0.60 * chan_h:
        rep["error"] = ("bright region is %d px in a %d px channel, so it is "
                        "the channel floor, not a core" % (dia_px, chan_h))
        return rep
    rep["core"] = "x %d..%d  %d x %d px" % (cx0, cx1, len_px, dia_px)
    rep["core_dia_px"] = dia_px
    rep["core_len_px"] = len_px
    rep["ratio"] = round(len_px / max(1, dia_px), 2)
    rep["_box"] = (cx0, cx1,
                   int(cy0 + centre_row - dia_px / 2),
                   int(cy0 + centre_row + dia_px / 2))

    # --- 4. scale from the core, and the ratio gate ------------------------
    px_per_mm = dia_px / CORE_DIA_MM
    rep["px_per_mm"] = round(px_per_mm, 3)
    rep["core_length_mm"] = round(len_px / px_per_mm, 2)
    if rep["ratio"] < min_ratio:
        rep["error"] = ("core is %.1f:1, under the %d:1 minimum, so it is "
                        "refused rather than counted" % (rep["ratio"], min_ratio))
        return rep

    # --- 5. bands on the core only ----------------------------------------
    # profile from the middle half of the core's own width, not from the
    # channel: the channel floor either side of the core is not wood
    c0 = int(cy0 + centre_row - dia_px / 4)
    h = max(4, dia_px // 2)
    prof = lum[c0:c0 + h, cx0:cx1 + 1].mean(axis=0)
    n = len(prof)
    Wn = max(40, int(0.10 * n))
    trend = np.convolve(np.pad(prof, Wn, "edge"),
                        np.ones(2 * Wn + 1) / (2 * Wn + 1), mode="valid")
    hp = prof - trend
    sm = np.convolve(hp, np.ones(5) / 5, mode="same")
    sd = hp.std()
    rep["profile_sd"] = round(float(sd), 2)
    rep["contrast_frac"] = round(float(sd) / max(1.0, drop), 4)
    if rep["contrast_frac"] < MIN_CONTRAST:
        rep["error"] = ("ring contrast is only %.1f%% of the print's range "
                        "(%.2f of %.0f levels), below the %.0f%% floor; this "
                        "frame is too soft or too dark to count"
                        % (100 * rep["contrast_frac"], sd, drop,
                           100 * MIN_CONTRAST))
        return rep
    d = sm[:n] < -0.30 * sd
    runs = []
    x = 0
    while x < n:
        if d[x]:
            s = x
            while x < n and d[x]:
                x += 1
            runs.append([s, x - 1])
        else:
            x += 1
    merged = []
    for rr in runs:
        if merged and rr[0] - merged[-1][1] <= 3:
            merged[-1][1] = rr[1]
        else:
            merged.append(rr)
    keep = [rr for rr in merged if (rr[1] - rr[0] + 1) >= max(8, int(0.010 * n))]
    rep["bands"] = len(keep)
    centres = [(s + e) / 2.0 for s, e in keep]
    g = np.diff(centres)
    if len(g):
        rep["ring_mm"] = round(float(np.median(g)) / px_per_mm, 2)
        rep["ring_cv"] = round(float(g.std() / g.mean()), 3)
        rep["rings_per_cm"] = round(float(len(keep) / rep["core_length_mm"] * 10.0), 2)

    # score: a core is long, thin and well covered. Used only to choose an
    # orientation, never to report a count.
    rep["_score"] = (rep["ratio"] / 100.0)
    rep["_box"] = (cx0, cx1, cy0, cy1)
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--debug")
    ap.add_argument("--min-ratio", type=float, default=MIN_RATIO)
    a = ap.parse_args()
    r = detect(a.image, a.debug, a.min_ratio)
    for k, v in r.items():
        print("%-20s %s" % (k, v))


if __name__ == "__main__":
    main()
