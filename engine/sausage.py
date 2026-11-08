#!/usr/bin/env python3
"""Intermediary stage between "a warm blob" and "a core": find the sausage.

`corefind.py` aggregates warm pixels into the longest horizontal band and walks
outwards from it. That works on a clean frame and fails on a real field photo:
when a ruler, a sleeve and a second core are in shot, the longest warm band is
whatever they add up to. On three of the six October frames it either returned
nothing or returned an object two to three times too wide.

This stage asks a different question. Instead of "what is the warmest band", it
finds every warm *object* and asks which ones could physically be a 5 mm
increment-borer core:

  1. work in a centre crop, so clutter at the frame edge cannot join two objects
  2. label the warm mask into separate objects
  3. keep the long, thin, straight ones - a core is a sausage
  4. score them and take the best
  5. fit its axis, then re-draw the outline from a narrow band around that axis

Step 5 is the part that matters for scale. The first pass is deliberately coarse
and, on these cores, cuts 20-25% inside the real edge because the warmth
threshold walks into the ring crests. Once an axis exists the edges can be found
again within a couple of millimetres of it, using the intensity step at the wood
boundary rather than a global threshold. That is also where the ring crests - the
small bulges the borer leaves standing proud of the nominal 5 mm - end up on the
right side of the line instead of being lost.

The width of the chosen object also sets the scale, since a core is 5 mm across.
That makes the refinement self-calibrating: the coarse pass says roughly how wide
the core is, and the 3 mm refinement band is derived from it.

Usage:
    python3 engine/sausage.py IMAGE.png [--out OVERLAY.png] [--crop 0.20]
"""

from __future__ import annotations

import argparse

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

SCALE = 3                      # corefind works here; match it so results compare
CORE_DIA_MM = 5.0
MIN_ASPECT = 12.0              # length / width
MIN_LENGTH_MM = 30.0           # the user's floor for a countable core
REFINE_MM = 3.0                # half-height of the band re-examined for edges


# ---------------------------------------------------------------- warmth

def warmth(path, scale=SCALE):
    im = Image.open(path).convert("RGB")
    W0, H0 = im.size
    im = im.resize((W0 // scale, H0 // scale), Image.LANCZOS)
    a = np.asarray(im, dtype=float)
    R, G, B = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    lum = 0.299 * R + 0.587 * G + 0.114 * B
    w = (R - B) / (lum + 20.0)
    return w, lum, scale, (W0, H0)


def auto_thr(w):
    """Otsu on the warmth image, so a faded frame self-corrects."""
    v = w.ravel()
    lo, hi = np.percentile(v, 1), np.percentile(v, 99)
    if hi <= lo:
        return float(hi)
    hist, edges = np.histogram(v, bins=256, range=(lo, hi))
    hist = hist.astype(float)
    tot = hist.sum()
    if tot <= 0:
        return float(hi)
    wsum = np.cumsum(hist)
    tsum = np.cumsum(hist * np.arange(256))
    m0 = np.cumsum(hist * np.arange(256) ** 2)
    w0 = wsum[:-1]
    w1 = tot - w0
    valid = (w0 > 0) & (w1 > 0)
    if not valid.any():
        return float((lo + hi) / 2)
    between = np.zeros_like(w0)
    between[valid] = (
        tsum[-1] * w0[valid] - tsum[:-1][valid] * tot
    ) ** 2 / (w0[valid] * w1[valid] * tot * tot)
    i = int(np.argmax(between))
    return float(edges[i] + (edges[i + 1] - edges[i]) / 2.0)


# ------------------------------------------------------------ components

def label(mask):
    """4-connected labelling, via scipy: a pure-python flood fill takes minutes
    on a 1386x1040 working image and there are >120 frames to get through."""
    from scipy import ndimage as _ndi
    lab, n = _ndi.label(mask)
    return lab.astype(np.int32), int(n)

def _label_reference(mask):

    """The straightforward version, kept so the scipy call can be checked."""
    H, W = mask.shape
    lab = np.zeros((H, W), np.int32)
    cur = 0
    for sy in range(H):
        row = mask[sy]
        for sx in range(W):
            if not row[sx] or lab[sy, sx]:
                continue
            cur += 1
            stack = [(sy, sx)]
            lab[sy, sx] = cur
            while stack:
                y, x = stack.pop()
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < H and 0 <= nx < W and mask[ny, nx] and not lab[ny, nx]:
                        lab[ny, nx] = cur
                        stack.append((ny, nx))
    return lab, cur


class Candidate:
    """One warm object, with the measurements used to judge it."""

    __slots__ = ("mask", "area", "x0", "x1", "top", "bot", "cx", "cy",
                 "length_px", "width_px", "aspect", "straight_px", "score",
                 "ncols", "paper", "tex_floor")

    def __init__(self, mask):
        self.mask = mask
        self.area = int(mask.sum())
        ys, xs = np.nonzero(mask)
        self.x0, self.x1 = int(xs.min()), int(xs.max())
        self.cx = (self.x0 + self.x1) / 2.0
        self.cy = float(ys.mean())
        self.length_px = float(self.x1 - self.x0 + 1)

    def measure(self, min_cols=20):
        """Centreline per column, plus width and straightness."""
        H, W = self.mask.shape
        top, bot, cxs, cys = {}, {}, [], []
        for x in range(self.x0, self.x1 + 1):
            col = np.nonzero(self.mask[:, x])[0]
            if col.size == 0:
                continue
            # longest contiguous run in this column
            best_s, best_e, s = col[0], col[0], col[0]
            for y in col[1:]:
                if y > best_e + 1:
                    if y - 1 - best_e > best_e - best_s:
                        best_s, best_e = s, y - 1
                    s = y
                best_e = y
            if col[-1] - 1 - best_e > best_e - best_s:
                best_s, best_e = s, int(col[-1])
            top[x] = float(best_s)
            bot[x] = float(best_e)
            cxs.append(x)
            cys.append((best_s + best_e) / 2.0)
        self.top, self.bot = top, bot
        self.ncols = len(cxs)
        if self.ncols < min_cols:
            return False
        w = np.array([bot[x] - top[x] for x in cxs], float)
        self.width_px = float(np.median(w))
        self.aspect = self.length_px / max(self.width_px, 1e-6)
        # straightness: rms residual of the centreline from a straight fit.
        # A sausage is straight; a sleeve, a bag or a pair of jeans is not.
        cxs_a = np.asarray(cxs, float)
        cys_a = np.asarray(cys, float)
        if cxs_a.ptp() < 2:
            return False
        p = np.polyfit(cxs_a, cys_a, 1)
        resid = cys_a - np.polyval(p, cxs_a)
        self.straight_px = float(np.sqrt((resid ** 2).mean()))
        return True

    def score_it(self, px_per_mm_guess=0.0):
        """Higher is better. Straightness dominates; aspect gates."""
        if self.aspect < MIN_ASPECT:
            self.score = -1.0
            return
        # straightness as a fraction of length; a core sits well under 0.05
        bend = self.straight_px / max(self.length_px, 1.0)
        self.score = (self.ncols / 1000.0) - 8.0 * bend
        return


def candidates(w, thr=None, crop=0.20, min_area=200):
    """Every warm object in the centre crop that could be a core."""
    H, W = w.shape
    m = w > (auto_thr(w) if thr is None else thr)
    # centre crop: keeps clutter at the frame edge from bridging two objects
    cy0, cy1 = int(H * crop), int(H * (1 - crop))
    sub = np.zeros_like(m)
    sub[cy0:cy1] = m[cy0:cy1]
    # drop specks and anything not wide enough to be a core
    from scipy import ndimage as ndi
    sub = ndi.binary_opening(sub, np.ones((5, 9)))
    lab, n = label(sub)
    out = []
    for i in range(1, n + 1):
        mk = lab == i
        if mk.sum() < min_area:
            continue
        c = Candidate(mk)
        if c.measure():
            c.score_it()
            if c.score > 0:
                out.append(c)
    return out, (cy0, cy1)


# ----------------------------------------------------------------- refine

def refine(lum, cand, band_mm=REFINE_MM, core_dia_mm=CORE_DIA_MM):
    """Re-draw the outline from a narrow band around the fitted axis.

    The coarse mask used one global warmth threshold, which on these cores sits
    inside the ring crests and loses 20-25% of the width. Here the axis is
    already known, so each column is examined only within `band_mm` of it and the
    edge is taken at the steepest fall in luminance - the step from wood to
    denim - rather than at wherever warmth happens to cross a constant.

    Returns (top, bot, axis), all keyed by column. `axis` is the refined
    centreline, which is also what the caller should use for scale.
    """
    if cand.width_px <= 0:
        return None, None, None
    # Denim is a woven fabric and carries gradients comparable to the wood/denim
    # step itself, so the strongest gradient in a window is as likely to be fabric
    # weave as the edge. Taking a running median *along* the core suppresses the
    # per-column texture while leaving an edge - which is coherent along the core -
    # intact. A mean would not do: it would smear the edge as well.
    k = 9
    lum_s = _col_median(lum, k)
    px_per_mm = cand.width_px / core_dia_mm
    band = max(2, int(round(band_mm * px_per_mm)))
    # a 3 mm band on a 5 mm core is most of the core; cap it so the window
    # cannot reach across to the opposite edge or into the fabric
    band = int(min(band, 0.30 * cand.width_px))
    H = lum.shape[0]
    top, bot, axis = {}, {}, {}
    for x in range(cand.x0, cand.x1 + 1):
        if x not in cand.top:
            continue
        # Search only within `band` of the *coarse edge*, never from the axis
        # outward. A core's interior carries strong gradients at every ring
        # boundary, so a window spanning the half-width finds those instead of
        # the wood/background step and the contour ends up following the rings.
        edges = []
        for coarse, up in ((cand.top[x], True), (cand.bot[x], False)):
            lo = max(0, int(round(coarse - band)))
            hi = min(H, int(round(coarse + band)) + 1)
            if hi - lo < 4:
                edges.append(None)
                continue
            seg = lum_s[lo:hi, x]
            d = np.abs(np.diff(seg))
            if d.size < 3:
                edges.append(None)
                continue
            i = int(np.argmax(d))
            # the step lies between seg[i] and seg[i+1]
            edges.append(float(lo + i + 0.5) if up else float(lo + i + 0.5))
        if edges[0] is None or edges[1] is None:
            continue
        t, b = edges
        if b - t < 3 or t < 1 or b > H - 1:
            continue
        top[x] = t
        bot[x] = b
        axis[x] = (t + b) / 2.0
    if len(top) < 20:
        return None, None, None
    return top, bot, axis


def _col_median(lum, k=9):
    """Running median along x (axis 1), which is the core's direction."""
    if k < 3:
        return lum
    h = k // 2
    pad = np.pad(lum, ((0, 0), (h, h)), mode="edge")
    win = np.lib.stride_tricks.sliding_window_view(pad, k, axis=1)
    return np.median(win, axis=2)


def refine_widths(top, bot):
    w = np.array([bot[x] - top[x] for x in sorted(top)], float)
    return float(np.median(w))


# -------------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--out")
    ap.add_argument("--thr", type=float, default=None)
    ap.add_argument("--crop", type=float, default=0.20)
    a = ap.parse_args()

    w, lum, sc, (W0, H0) = warmth(a.image)
    cands, band = candidates(w, a.thr, a.crop)
    print(f"image {a.image}  warmth threshold {auto_thr(w):.4f}  crop {a.crop:.0%}")
    print(f"core-like candidates: {len(cands)}")
    for c in sorted(cands, key=lambda c: -c.score):
        print(f"  x{c.x0}..{c.x1}  length {c.length_px*sc:5.0f}px  width {c.width_px*sc:4.0f}px  "
              f"aspect {c.aspect:5.1f}  bend {c.straight_px/c.length_px:.4f}  score {c.score:.3f}")
    if not cands:
        print("no core-like object found")
        return
    best = max(cands, key=lambda c: c.score)
    print(f"\nchosen: x{best.x0}..{best.x1}  {best.length_px*sc:.0f}px long  "
          f"{best.width_px*sc:.0f}px wide  aspect {best.aspect:.1f}")
    if best.length_px / (best.width_px / CORE_DIA_MM) < MIN_LENGTH_MM:
        print(f"  WARNING shorter than {MIN_LENGTH_MM:.0f} mm; not a countable core")

    coarse_px = best.width_px * sc
    rtop, rbot, raxis = refine(lum, best)
    if rtop:
        fine_px = refine_widths(rtop, rbot) * sc
        print(f"  coarse width {coarse_px:5.1f}px -> refined {fine_px:5.1f}px "
              f"({(fine_px/coarse_px-1)*100:+.1f}%)")
        # the coarse width assumes 5 mm, so the ratio is what the true diameter would be
        print(f"  if the coarse width is 5 mm, the refined one implies {5.0*fine_px/coarse_px:.2f} mm")
        print(f"  scale from refined width: {fine_px/5.0:.2f} px/mm")
    else:
        print("  refinement produced nothing")

    if a.out:
        im = Image.open(a.image).convert("RGB")
        d = ImageDraw.Draw(im)
        s = sc
        for x in sorted(best.top):
            d.line([(x * s, best.top[x] * s), (x * s, best.bot[x] * s)], fill=(255, 140, 0), width=1)
        if rtop:
            d.line([(x * s, rtop[x] * s) for x in sorted(rtop)], fill=(0, 255, 0), width=3)
            d.line([(x * s, rbot[x] * s) for x in sorted(rbot)], fill=(0, 255, 0), width=3)
            d.line([(x * s, raxis[x] * s) for x in sorted(raxis)], fill=(255, 0, 255), width=2)
        im.save(a.out)
        print(f"wrote {a.out}  (orange=coarse, green=refined, magenta=axis)")


if __name__ == "__main__":
    main()

# ------------------------------------------------------- white paper support

def paper_level(lum, margin=0.06):
    """Brightness of the paper, taken from the frame margins.

    The paper is the brightest large smooth region, so the median of a border
    band estimates it without assuming where the core lies. Both margins are
    used because the sheet is often lit unevenly and the core often sits low.
    """
    h = lum.shape[0]
    m = max(4, int(h * margin))
    top = lum[:m, :].ravel()
    bot = lum[-m:, :].ravel()
    both = np.concatenate([top, bot])
    # median, not a high percentile: the sheet has a lighting gradient and the
    # frame margins catch its shadowed edge, so a 92nd-percentile estimate sits
    # well above typical paper and a cut taken from it swallows half the frame.
    return float(np.median(both))


def texture(lum, win=9):
    """Local standard deviation: the discriminator between wood and paper.

    Wood carries ring structure, grain and tool marks; paper is smooth to within
    a couple of grey levels. On a dark fabric the colour cue (red minus blue)
    works and this is unnecessary, but paper is neutral - it is neither warm
    against cool fabric nor cool against warm wood - so on white the only
    reliable separation is that wood is *textured* and paper is not.

    The window has to be small enough to resolve ring spacing (tens of pixels at
    this scale) and large enough to be robust to paper's own faint texture.
    """
    return ndimage.generic_filter(lum, np.std, size=win)


def wood_mask_paper(lum, tex_win=25, cut_frac=0.04, tex_gain=1.6,
                    open_px=(9, 25)):
    """Mask the core on a light background.

    Measured on clean card (`IMG_20261005_140641_879`): the sheet sits at
    192.9 with sd 4.3, the core at 120.7 with sd 29.5 - a separation of
    d' = 3.4. A cut at 3% below the sheet's own mean keeps 91% of the paper and
    0.7% of the core, so the segmentation is essentially a single threshold.

    The cut is a *percentage* below the paper, not a fraction of it. An earlier
    version used `paper * 0.65`, which was never justified and cut 700 px into
    what is a 150 px core.

    Local standard deviation is used only as a confirmation, not as the primary
    cue. On clean card the core runs 20x the paper's texture at a 25 px window;
    on a textured sheet the relationship inverts and the paper reads rougher
    than the wood, so a texture test is switched off whenever the sheet's own
    texture is high rather than being allowed to reject a good frame.

    Colour is deliberately not used. Red-minus-blue is the cue that fails here:
    paper is neutral, so on white it reads warm relative to cool fabric and the
    mask swallows the whole sheet.
    """
    paper = paper_level(lum)
    tex = texture(lum, tex_win)
    paper_tex = float(np.median(tex[:max(4, tex.shape[0] // 12), :]))
    cut = paper * (1.0 - cut_frac)
    m = lum < cut
    if tex_gain > 1.0 and paper_tex < 1.0:
        # clean sheet: the core really is the textured part, so use that
        floor = paper_tex * tex_gain
        m &= tex > floor
    m = ndimage.binary_opening(m, np.ones(open_px))
    m = ndimage.binary_closing(m, np.ones((25, 25)))
    return m, paper, paper_tex, cut


def candidates_paper(lum, crop=0.20, min_area=3000, **kw):
    """Core-like candidates on a light background, same scoring as `candidates`.

    The centre crop is not applied here: on a paper sheet the core often lies low
    in the frame, and trimming 20% off each edge would cut it. Instead the shape
    tests do the work.
    """
    m, paper, tex_floor, tex = wood_mask_paper(lum, **kw)
    lab, n = label(m)
    out = []
    for i in range(1, n + 1):
        mk = lab == i
        if mk.sum() < min_area:
            continue
        c = Candidate(mk)
        if c.measure():
            c.score_it()
            if c.score > 0:
                c.paper = paper
                c.tex_floor = tex_floor
                out.append(c)
    return out, m
