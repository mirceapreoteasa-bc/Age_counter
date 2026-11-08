#!/usr/bin/env python3
"""Measure what actually separates age-core wood from its backgrounds, and turn the
requested ring-width limits into a scale-free pixel rule.

Writes CORE_COLOR_SPEC.json and CORE_COLOR_SPEC.md into ~/ForestAgeCounter.
"""
import os, glob, json
import cv2, numpy as np

ROOT = os.path.expanduser('~/ForestAgeCounter')
HUG = os.path.join(ROOT, 'crop_overlaysHUG')
SET = os.path.join(ROOT, 'segset')

RING_MIN_MM = 0.25
RING_MAX_MM = 9.0
CORE_MM = 5.0
MIN_PX_PER_FEATURE = 4.0     # a ring must span this many px to be measurable


def lab(im):
    return cv2.cvtColor(im, cv2.COLOR_BGR2LAB).astype(np.float32)


def pct(x):
    p = np.percentile(x, [0.5, 1, 5, 50, 95, 99, 99.5], axis=0)
    return {'L': [round(float(p[0, 0]), 1), round(float(p[3, 0]), 1), round(float(p[6, 0]), 1)],
            'a': [round(float(p[0, 1]), 1), round(float(p[3, 1]), 1), round(float(p[6, 1]), 1)],
            'b': [round(float(p[0, 2]), 1), round(float(p[3, 2]), 1), round(float(p[6, 2]), 1)]}


def kmeans_labels(x, k=6):
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 1.0)
    _, lab, cen = cv2.kmeans(x.astype(np.float32), k, None, crit, 4, cv2.KMEANS_PP_CENTERS)
    return lab.ravel(), cen


def main():
    # ---------- gather core pixels: the 17 pure-core strips ----------
    strip_px = np.vstack([lab(cv2.imread(f)).reshape(-1, 3)
                          for f in sorted(glob.glob(os.path.join(HUG, 'c*.png')))])

    # ---------- gather core + background from the ground-truth polygons ----------
    gt_core, gt_bg = [], []
    for lf in sorted(glob.glob(os.path.join(SET, 'labels', '*.txt'))):
        stem = os.path.basename(lf)[:-4]
        im = cv2.imread(os.path.join(SET, 'images', stem + '.jpg'))
        if im is None:
            continue
        H, W = im.shape[:2]
        v = open(lf).read().split()[1:]
        pts = np.array([[float(v[i]) * W, float(v[i + 1]) * H]
                        for i in range(0, len(v), 2)], np.int32)
        m = np.zeros((H, W), np.uint8)
        cv2.fillPoly(m, [pts], 255)
        gt_core.append(lab(im)[m > 0])
        far = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (81, 81))) == 0
        if far.sum() > 5000:
            gt_bg.append(lab(im)[far][::29])
    gt_core = np.vstack(gt_core)
    gt_bg = np.vstack(gt_bg)
    core = np.vstack([strip_px, gt_core])

    print('pixels: strips=%d gt_core=%d background=%d' % (len(strip_px), len(gt_core), len(gt_bg)))
    print('core    ', pct(core))
    print('background', pct(gt_bg))

    # ---------- can a LAB box separate them? ----------
    print('\n--- global LAB box separability ---')
    for k in (0.5, 1.0, 2.0, 3.0, 5.0):
        lo, hi = np.percentile(core, [k, 100 - k], axis=0)
        def acc(x):
            m = np.all((x >= lo) & (x <= hi), axis=1)
            return 100.0 * m.mean()
        a, b = acc(core), acc(gt_bg)
        print('  core box %.1f-%.1f pct : core accepted %5.1f%%   background accepted %5.1f%%   -> %s'
              % (k, 100 - k, a, b, 'SEPARATES' if b < 50 else 'no separation'))

    # ---------- what are the background materials, and can they be rejected? ----------
    print('\n--- background materials (k-means on LAB) ---')
    lb, cen = kmeans_labels(gt_bg, 6)
    cnt = np.bincount(lb, minlength=6)
    mats = []
    for i in np.argsort(-cnt):
        c = cen[i]
        patch = np.uint8([[[c[0], c[1], c[2]]]])          # L,a,b -> BGR
        bgr = cv2.cvtColor(patch, cv2.COLOR_LAB2BGR)[0, 0]
        share = 100.0 * cnt[i] / cnt.sum()
        mats.append({'share_pct': round(share, 1),
                     'L': round(float(c[0]), 1), 'a': round(float(c[1]), 1), 'b': round(float(c[2]), 1),
                     'bgr': [int(v) for v in bgr]})
        print('  %5.1f%%  L=%6.1f a=%6.1f b=%6.1f  BGR=(%3d,%3d,%3d)'
              % (share, c[0], c[1], c[2], bgr[0], bgr[1], bgr[2]))

    # ---------- per-material rejection rules ----------
    print('\n--- how much of each background can simple rules reject? ---')

    def rule_reject(x):
        L, a, b = x[:, 0], x[:, 1], x[:, 2]
        grey = (np.abs(a - 128) < 6) & (np.abs(b - 128) < 10)
        dark = L < 45
        blown = L > 238
        return grey & (dark | blown)

    print('  neutral-and-either-dark-or-blown (paper/card/fabric): rejects %.1f%% of background'
          % (100 * rule_reject(gt_bg).mean()))
    print('  the same rule removes %.1f%% of CORE pixels (collateral damage)' % (100 * rule_reject(core).mean()))
    for i, m in enumerate(mats):
        sel = lb == np.argsort(-cnt)[i]
        print('    material %d (%4.1f%% of bg): rule rejects %5.1f%% of it'
              % (i, m['share_pct'], 100 * rule_reject(gt_bg[sel]).mean()))

    # ---------- ring width limits -> scale-free pixel rule ----------
    print('\n--- ring width limits as a function of measured core width ---')
    rows = []
    for w in (20, 30, 40, 60, 80, 100, 150, 200, 300):
        ppmm = w / CORE_MM
        lo_px = RING_MIN_MM * ppmm
        hi_px = RING_MAX_MM * ppmm
        ok = lo_px >= MIN_PX_PER_FEATURE
        rows.append({'core_width_px': w, 'px_per_mm': round(ppmm, 2),
                     'min_ring_px': round(lo_px, 2), 'max_ring_px': round(hi_px, 1),
                     'resolves_0p25mm': bool(ok)})
        print('  core %3dpx wide -> %5.1f px/mm | ring %.2fmm = %5.2fpx | 9mm = %6.1fpx | %s'
              % (w, ppmm, RING_MIN_MM, lo_px, hi_px,
                 'ok' if ok else 'TOO NARROW for 0.25mm'))

    min_w = CORE_MM * MIN_PX_PER_FEATURE / RING_MIN_MM
    print('\n  scale-free rule:  min_ring_px = core_width_px / %.0f     max_ring_px = %.1f x core_width_px'
          % (CORE_MM / RING_MIN_MM, RING_MAX_MM / CORE_MM))
    print('  a 0.25 mm ring needs >= %.0f px of core width (at %.0f px per feature)' % (min_w, MIN_PX_PER_FEATURE))

    # ---------- what our own crops can actually resolve ----------
    print('\n--- our existing core-only strips ---')
    hs = []
    for f in sorted(glob.glob(os.path.join(HUG, 'c*.png'))):
        h = cv2.imread(f).shape[0]
        hs.append(h)
    hs = np.array(hs)
    ppmm = hs / CORE_MM
    print('  strip heights (= 5 mm core): %d-%d px  ->  %.1f-%.1f px/mm' % (hs.min(), hs.max(), ppmm.min(), ppmm.max()))
    print('  a 0.25 mm ring measures %.2f-%.2f px here  -> %s'
          % (0.25 * ppmm.min(), 0.25 * ppmm.max(),
             'below the %0.0f px floor, so 0.25 mm is NOT resolvable in these crops'
             % MIN_PX_PER_FEATURE))

    spec = {
        'core_diameter_mm': CORE_MM,
        'colour_space': 'OpenCV LAB, 8-bit (L 0-255, a/b 128 = neutral)',
        'core_percentiles': pct(core),
        'background_percentiles': pct(gt_bg),
        'verdict_global_lab_box': 'does not separate; background lies inside the core range',
        'background_materials': mats,
        'rejection_rule_neutral_dark_or_blown': {
            'definition': 'abs(a-128)<6 and abs(b-128)<10 and (L<45 or L>238)',
            'rejects_background_pct': round(100 * rule_reject(gt_bg).mean(), 1),
            'removes_core_pct': round(100 * rule_reject(core).mean(), 1)},
        'ring_width_limits_mm': [RING_MIN_MM, RING_MAX_MM],
        'ring_width_rule': {
            'min_ring_px': 'core_width_px / %.0f' % (CORE_MM / RING_MIN_MM),
            'max_ring_px': '%.1f * core_width_px' % (RING_MAX_MM / CORE_MM),
            'px_per_mm': 'core_width_px / %.1f' % CORE_MM},
        'resolution_floor': {'min_px_per_feature': MIN_PX_PER_FEATURE,
                             'min_core_width_px': round(min_w, 1)},
        'core_width_sweep': rows,
        'existing_strips': {'heights_px': [int(hs.min()), int(hs.max())],
                            'px_per_mm': [round(float(ppmm.min()), 2), round(float(ppmm.max()), 2)],
                            'resolves_0p25mm': bool(0.25 * ppmm.min() >= MIN_PX_PER_FEATURE)},
    }
    p = os.path.join(ROOT, 'CORE_COLOR_SPEC.json')
    json.dump(spec, open(p, 'w'), indent=1)
    print('\nwrote ' + p)


if __name__ == '__main__':
    main()
