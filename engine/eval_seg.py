import os
import glob, os, json, numpy as np, cv2, torch
torch.set_num_threads(8)
from ultralytics import YOLO

SET = os.path.expanduser('~/ForestAgeCounter/segset')
m = YOLO(f'{SET}/train1/weights/best.pt')
stems = [os.path.basename(p)[:-4] for p in sorted(glob.glob(f'{SET}/labels/*.txt'))]
src = [os.path.expanduser(f'~/ForestAgeCounter/NEW_PHOTOS/{s}.jpg') for s in stems]


def gt_mask(stem):
    v = open(f'{SET}/labels/{stem}.txt').read().split()[1:]
    im = cv2.imread(f'{SET}/images/{stem}.jpg')
    H, W = im.shape[:2]
    pts = np.array([[float(v[i]) * W, float(v[i + 1]) * H] for i in range(0, len(v), 2)], np.int32)
    msk = np.zeros((H, W), np.uint8)
    cv2.fillPoly(msk, [pts], 255)
    return msk


rows = []
print('%-26s %4s %4s %6s %7s %7s %7s %8s' % ('stem', 'ndet', 'rk', 'ctr', 'IoUbox', 'lenR', 'maskA%', 'conf'))
for r in m.predict(src, imgsz=960, conf=0.20, device='cpu', verbose=False):
    stem = os.path.basename(r.path)[:-4]
    g = gt_mask(stem)
    H, W = g.shape
    ys, xs = np.where(g > 0)
    gx0, gx1, gy0, gy1 = xs.min(), xs.max(), ys.min(), ys.max()
    glen = max(gx1 - gx0, gy1 - gy0)
    conf = r.boxes.conf.cpu().numpy()
    order = np.argsort(-conf)
    md = r.masks.data.cpu().numpy() if r.masks is not None else None
    best = None
    for rank, i in enumerate(order[:3], 1):
        x1, y1, x2, y2 = [float(v) for v in r.boxes.xyxy[i]]
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        cin = 1 if g[int(np.clip(cy, 0, H - 1)), int(np.clip(cx, 0, W - 1))] > 0 else 0
        pb = np.zeros((H, W), np.uint8)
        pb[max(0, int(y1)):int(y2), max(0, int(x1)):int(x2)] = 1
        pb = pb.astype(bool); gb = g > 0
        iou_b = (pb & gb).sum() / max(1, (pb | gb).sum())
        mask_a = 0.0
        if md is not None and i < len(md):
            mm = (cv2.resize(md[i], (W, H)) > 0.5).astype(np.uint8)
            mask_a = 100.0 * mm.sum() / max(1, gb.sum())
        plen = max(x2 - x1, y2 - y1)
        lr = plen / glen if glen else 0
        if rank == 1:
            best = (cin, iou_b, lr, mask_a, float(conf[i]))
        print('%-26s %4d %4d %6s %7.3f %7.3f %7.2f %8.3f'
              % (stem, len(order), rank, 'yes' if cin else 'NO', iou_b, lr, mask_a, conf[i]))
    rows.append(dict(stem=stem, ndet=len(order), cin=best[0], iou=best[1],
                     lenr=best[2], maskA=best[3], conf=best[4]))

print()
print('rank-1 centre inside ground truth : %d/%d' % (sum(r['cin'] for r in rows), len(rows)))
print('rank-1 IoU (box vs GT rect)       : median %.3f  min %.3f' %
      (np.median([r['iou'] for r in rows]), min(r['iou'] for r in rows)))
print('rank-1 length ratio (pred/GT)     : median %.3f  range %.3f-%.3f' %
      (np.median([r['lenr'] for r in rows]), min(r['lenr'] for r in rows), max(r['lenr'] for r in rows)))
print('rank-1 mask area / GT area        : median %.0f%%' % np.median([r['maskA'] for r in rows]))
print('detections per photo              : median %.1f  max %d' %
      (np.median([r['ndet'] for r in rows]), max(r['ndet'] for r in rows)))
json.dump(rows, open(f'{SET}/eval_rank1.json', 'w'), indent=1)
print('wrote', f'{SET}/eval_rank1.json')
