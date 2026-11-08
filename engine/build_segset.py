#!/usr/bin/env python3
"""Turn the user's hand annotations into YOLO segmentation labels on the ORIGINAL photos.

Pipeline per annotated photo:
  1. hand-draw mask = diff(annotated, base_overlay), largest connected component
  2. fill it:  h -> axis-aligned bounding box   c -> enclosed region of the drawn contour
  3. register base_overlay -> original photo with SIFT + affine (try 4 rotations)
  4. warp the filled mask into original coordinates
  5. write original image + YOLO-seg polygon label
"""
import os, re, sys, json
import cv2, numpy as np

ROOT = os.path.expanduser('~/ForestAgeCounter')
HUG  = os.path.join(ROOT, 'crop_overlaysHUG')
ORIG = os.path.join(ROOT, 'NEW_PHOTOS')
OUT  = os.path.join(ROOT, 'segset')
CHK  = os.path.join(ROOT, 'seg_check')
IMGD = os.path.join(OUT, 'images')
LBLD = os.path.join(OUT, 'labels')
for d in (IMGD, LBLD, CHK):
    os.makedirs(d, exist_ok=True)

PAT = re.compile(r'^(IMG_\d{8}_\d{6}_\d+?)([a-zA-Z]*)\.jpg$')


def hand_mask(base, ann, kind):
    """Largest connected component of the user's marks, filled."""
    d = cv2.absdiff(base, ann)
    g = cv2.cvtColor(d, cv2.COLOR_BGR2GRAY)
    m = (g > 28).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        return None, 0
    k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    area = int(stats[k, cv2.CC_STAT_AREA])

    if kind.lower() == 'h':
        x, y, w, hh = cv2.boundingRect((lab == k).astype(np.uint8) * 255)
        out = np.zeros_like(m)
        cv2.rectangle(out, (x, y), (x + w - 1, y + hh - 1), 255, -1)
        return out, area

    # A contour is drawn as a freehand stroke, so it arrives as several
    # fragments. Keep them all and bridge them, then fill the vertical span.
    allm = (m > 0).astype(np.uint8) * 255
    allm = cv2.morphologyEx(allm, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    cols = np.where(allm.any(axis=0))[0]
    if len(cols) < 20:
        return None, area
    out = np.zeros_like(m)
    for x in cols:
        yy = np.where(allm[:, x])[0]
        out[yy.min(), x] = 255
        out[yy.max(), x] = 255
    out = cv2.morphologyEx(out, cv2.MORPH_CLOSE, np.ones((1, 11), np.uint8))
    return out, area


def photo_valid_mask(bgr):
    """True where the pixel looks like real photo (wood/background), not drawn graphics.

    My overlays are saturated cyan/green/magenta line art; the scene is desaturated
    browns, greys and whites. Excluding saturated pixels keeps SIFT off the graphics.
    """
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]
    return (sat < 70).astype(np.uint8) * 255


def _grad(x):
    x = x.astype(np.float32)
    gx = cv2.Sobel(x, cv2.CV_32F, 1, 0, 3)
    gy = cv2.Sobel(x, cv2.CV_32F, 0, 1, 3)
    return np.sqrt(gx * gx + gy * gy)


def locate_band(orig, overlay_gray):
    """Find which rotation/axis/offset of the original the overlay was cut from.

    The overlay is the same centred 25% band as AGE_COUNTS/25_percent_MIDDLE, just
    downscaled, and the original may be stored rotated 90 degrees. Both facts are
    searched and scored by gradient correlation, so no feature matching is needed.
    """
    best = None
    for k in range(4):
        cand = np.rot90(orig, k)
        ch, cw = cand.shape[:2]
        for axis in (0, 1):
            span = ch if axis == 0 else cw
            b = max(8, int(round(0.25 * span)))
            o0 = (span - b) // 2
            band = cand[o0:o0 + b, :] if axis == 0 else cand[:, o0:o0 + b]
            if band.size == 0:
                continue
            small = cv2.cvtColor(
                cv2.resize(band, (overlay_gray.shape[1], overlay_gray.shape[0]),
                           interpolation=cv2.INTER_AREA),
                cv2.COLOR_BGR2GRAY)
            sg, og = _grad(small), _grad(overlay_gray)
            sg = sg - sg.mean(); og = og - og.mean()
            d = float(np.sqrt((sg * sg).sum()) * np.sqrt((og * og).sum()))
            corr = float((sg * og).sum() / d) if d > 1e-9 else -1.0
            if best is None or corr > best[0]:
                best = (corr, k, axis, o0, b, band.shape[:2])
    return best


def warp_mask_to_original(mask_ov, orig, loc):
    """Inverse of the overlay->original crop, using the located band."""
    corr, k, axis, o0, b, bshape = loc
    cand = np.rot90(orig, k)
    ch, cw = cand.shape[:2]
    full = np.zeros((ch, cw), np.uint8)
    bh, bw = bshape
    piece = cv2.resize(mask_ov, (bw, bh), interpolation=cv2.INTER_NEAREST)
    if axis == 0:
        full[o0:o0 + bh, :] = piece
    else:
        full[:, o0:o0 + bw] = piece
    return np.rot90(full, -k)


def poly_from_mask(m, eps=0.004):
    """Largest external contour as a YOLO-seg polygon (normalised)."""
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    if cv2.contourArea(c) < 50:
        return None
    ap = cv2.approxPolyDP(c, eps * cv2.arcLength(c, True), True).reshape(-1, 2)
    if len(ap) < 4:
        return None
    H, W = m.shape
    return [(float(x) / W, float(y) / H) for x, y in ap]


def main():
    ann = []
    for f in sorted(os.listdir(HUG)):
        m = PAT.match(f)
        if m and m.group(2):
            ann.append((m.group(1), m.group(2).lower(), f))
    print(f'found {len(ann)} annotations')

    rows, failed = [], []
    for stem, kind, fn in ann:
        base = cv2.imread(os.path.join(HUG, stem + '.jpg'))
        apo  = cv2.imread(os.path.join(HUG, fn))
        org  = cv2.imread(os.path.join(ORIG, stem + '.jpg'))
        if base is None or apo is None or org is None:
            failed.append((stem, kind, 'read'))
            continue
        hm, raw = hand_mask(base, apo, kind)
        if hm is None or raw < 60:
            failed.append((stem, kind, 'nomask'))
            continue
        if hm.mean() < 12:                      # degenerate / empty fill
            failed.append((stem, kind, 'thinfill'))
            continue

        loc = locate_band(org, cv2.cvtColor(base, cv2.COLOR_BGR2GRAY))
        if loc is None or loc[0] < 0.25:
            failed.append((stem, kind, f'loc(corr={loc[0]:.2f})' if loc else 'loc'))
            continue
        corr = loc[0]
        H, W = org.shape[:2]
        warped = warp_mask_to_original(hm, org, loc)
        if warped.shape != (H, W):
            failed.append((stem, kind, 'shape'))
            continue
        # keep the largest blob after warping
        n, lab, stats, _ = cv2.connectedComponentsWithStats((warped > 127).astype(np.uint8), 8)
        if n <= 1:
            failed.append((stem, kind, 'warp'))
            continue
        k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        big = (lab == k).astype(np.uint8) * 255
        # the core should occupy a believable slice of the frame
        cover = 100.0 * big.mean() / 255.0
        if not (0.4 <= cover <= 30.0):
            failed.append((stem, kind, f'cover={cover:.2f}%'))
            continue
        poly = poly_from_mask(big)
        if poly is None:
            failed.append((stem, kind, 'poly'))
            continue

        cv2.imwrite(os.path.join(IMGD, stem + '.jpg'), org,
                    [cv2.IMWRITE_JPEG_QUALITY, 95])
        with open(os.path.join(LBLD, stem + '.txt'), 'w') as fh:
            fh.write('0 ' + ' '.join(f'{x:.6f} {y:.6f}' for x, y in poly) + '\n')
        vis = org.copy()
        cv2.polylines(vis, [np.array([(int(x * W), int(y * H)) for x, y in poly], np.int32)],
                      True, (0, 255, 255), 4)
        cv2.imwrite(os.path.join(CHK, f'{stem}_{kind}.jpg'), vis,
                    [cv2.IMWRITE_JPEG_QUALITY, 88])
        rows.append((stem, kind, round(corr, 3), len(poly), round(cover, 2)))

    print(f'\nOK {len(rows)} / {len(ann)}')
    print(f'{"stem":<32}{"k":<3}{"corr":>6}{"pts":>5}{"cover%":>8}')
    for s, k, i, p, c in rows:
        print(f'{s:<32}{k:<3}{i:>5}{p:>5}{c:>8}')
    if failed:
        print('\nFAILED:')
        for f in failed:
            print('  ', f)
    json.dump({'ok': [r[0] for r in rows], 'failed': failed},
              open(os.path.join(OUT, 'build_report.json'), 'w'), indent=1)
    print(f'\nwrote {OUT}')


if __name__ == '__main__':
    main()
