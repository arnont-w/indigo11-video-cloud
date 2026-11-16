# /// script
# dependencies = ["numpy", "opencv-python-headless"]
# ///
"""Measure how something moves, frame by frame, on frames from .claude/skills/indigo11-watch/scripts/extract.py (numbers, not eyeballing).

  crop     save a template from one frame        track.py crop <frames_dir> F x y w h out.png
  template follow a template (cursor tip, logo)  track.py template <frames_dir> tmpl.png --from F0 --to F1 [--roi x,y,w,h] [--anchor ax,ay]
  ink      bbox of pixels unlike the background  track.py ink <frames_dir> --from F0 --to F1 --roi x,y,w,h [--thresh 28] [--bg auto|r,g,b]
  color    colour at a point / dominant colours   track.py color <frames_dir> F [--at x,y] [--k 6]
  bands    text lines per frame (kinetic type)    track.py bands <frames_dir> --from F0 --to F1 [--roi x,y,w,h] [--thresh 40] [--bg auto|r,g,b]
  edge     sub-pixel edge along a line            track.py edge <frames_dir> --from F0 --to F1 --line x0,y0,x1,y1 [--first|--last]
  grade    global colour per frame (tint, haze)   track.py grade <frames_dir> --from F0 --to F1 [--pts x,y;x,y;...]

template/ink print CSV (frame, x, y, w, h, score) plus velocity columns; pipe to a file and plot or fit springs.
template: --min-score 0.7 blanks low-confidence rows (blurred entry frames give confident-looking wrong positions), --max-step PX
rejects jumps, --subpixel refines the peak with a parabola. ink/bands: pass --bg r,g,b when the ROI border touches other elements.
"""
import _starter  # indigo11 Video: runtime PATH + the Python the installer prepared
_starter.needs("numpy", "cv2")
import argparse, sys
from pathlib import Path
import numpy as np, cv2


def frame(d, f):
    for ext in ("jpg", "png"):
        p = Path(d) / f"f{f:05d}.{ext}"
        if p.exists():
            return cv2.imread(str(p))
    sys.exit(f"missing frame {f} in {d}")


def roi_of(s, img):
    if not s:
        return 0, 0, img.shape[1], img.shape[0]
    return tuple(int(v) for v in s.split(","))


def main():
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    c = sp.add_parser("crop"); c.add_argument("dir"); c.add_argument("f", type=int); [c.add_argument(k, type=int) for k in "xywh"]; c.add_argument("out")
    t = sp.add_parser("template"); t.add_argument("dir"); t.add_argument("tmpl"); t.add_argument("--from", dest="f0", type=int, required=True)
    t.add_argument("--to", dest="f1", type=int, required=True); t.add_argument("--roi"); t.add_argument("--anchor", default="0,0")
    t.add_argument("--min-score", type=float, default=0); t.add_argument("--max-step", type=float, default=0); t.add_argument("--subpixel", action="store_true")
    b = sp.add_parser("bands"); b.add_argument("dir"); b.add_argument("--from", dest="f0", type=int, required=True); b.add_argument("--to", dest="f1", type=int, required=True)
    b.add_argument("--roi"); b.add_argument("--thresh", type=float, default=40); b.add_argument("--bg", default="auto"); b.add_argument("--gap", type=int, default=6)
    e = sp.add_parser("edge"); e.add_argument("dir"); e.add_argument("--from", dest="f0", type=int, required=True); e.add_argument("--to", dest="f1", type=int, required=True)
    e.add_argument("--line", required=True); e.add_argument("--last", action="store_true")
    q = sp.add_parser("grade"); q.add_argument("dir"); q.add_argument("--from", dest="f0", type=int, required=True); q.add_argument("--to", dest="f1", type=int, required=True)
    q.add_argument("--pts", default="")
    k = sp.add_parser("ink"); k.add_argument("dir"); k.add_argument("--from", dest="f0", type=int, required=True)
    k.add_argument("--to", dest="f1", type=int, required=True); k.add_argument("--roi"); k.add_argument("--thresh", type=float, default=28); k.add_argument("--bg", default="auto")
    o = sp.add_parser("color"); o.add_argument("dir"); o.add_argument("f", type=int); o.add_argument("--at"); o.add_argument("--k", type=int, default=6)
    a = ap.parse_args()
    if a.cmd == "crop":
        im = frame(a.dir, a.f); cv2.imwrite(a.out, im[a.y:a.y + a.h, a.x:a.x + a.w]); print("saved", a.out); return
    if a.cmd == "color":
        im = frame(a.dir, a.f)
        if a.at:
            x, y = (int(v) for v in a.at.split(",")); b, g, r = im[y, x]; print(f"#{r:02X}{g:02X}{b:02X}"); return
        px = im.reshape(-1, 3).astype(np.float32)[:: max(1, im.size // 3 // 60000)]
        _, lab, cen = cv2.kmeans(px, a.k, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1), 3, cv2.KMEANS_PP_CENTERS)
        cnt = np.bincount(lab.ravel(), minlength=a.k)
        for i in np.argsort(-cnt):
            b, g, r = (int(v) for v in cen[i]); print(f"#{r:02X}{g:02X}{b:02X}  {cnt[i] / cnt.sum():.1%}")
        return
    if a.cmd == "bands": return bands(a)
    if a.cmd == "edge": return edge(a)
    if a.cmd == "grade": return grade(a)
    rows, prev = [], None
    print("frame,x,y,w,h,score,vx,vy")
    if a.cmd == "template":
        tm = cv2.imread(a.tmpl); th, tw = tm.shape[:2]; ax, ay = (float(v) for v in a.anchor.split(","))
        for f in range(a.f0, a.f1 + 1):
            im = frame(a.dir, f); x0, y0, w, h = roi_of(a.roi, im)
            res = cv2.matchTemplate(im[y0:y0 + h, x0:x0 + w], tm, cv2.TM_CCOEFF_NORMED)
            _, sc, _, loc = cv2.minMaxLoc(res)
            px, py = float(loc[0]), float(loc[1])
            if a.subpixel and 0 < loc[0] < res.shape[1] - 1 and 0 < loc[1] < res.shape[0] - 1:
                l, c, r = res[loc[1], loc[0] - 1:loc[0] + 2]; px += .5 * (l - r) / (l - 2 * c + r + 1e-9)
                u, c, dn = res[loc[1] - 1:loc[1] + 2, loc[0]]; py += .5 * (u - dn) / (u - 2 * c + dn + 1e-9)
            x, y = x0 + px + ax, y0 + py + ay
            if sc < a.min_score or (a.max_step and prev and np.hypot(x - prev[0], y - prev[1]) > a.max_step):
                print(f"{f},,,{tw},{th},{sc:.3f},,"); continue
            vx, vy = (x - prev[0], y - prev[1]) if prev else (0, 0); prev = (x, y)
            print(f"{f},{x:.2f},{y:.2f},{tw},{th},{sc:.3f},{vx:.2f},{vy:.2f}")
    else:
        for f in range(a.f0, a.f1 + 1):
            im = frame(a.dir, f); x0, y0, w, h = roi_of(a.roi, im); r = im[y0:y0 + h, x0:x0 + w].astype(np.int16)
            if a.bg == "auto":
                border = np.concatenate([r[0], r[-1], r[:, 0], r[:, -1]]); bg = np.median(border, axis=0)
            else:
                rr, gg, bb = (int(v) for v in a.bg.split(",")); bg = np.array([bb, gg, rr])
            mask = np.abs(r - bg).max(axis=2) > a.thresh
            ys, xs = np.nonzero(mask)
            if len(xs) == 0:
                print(f"{f},,,,,0,,"); prev = None; continue
            bx0, bx1, by0, by1 = xs.min(), xs.max(), ys.min(), ys.max()
            cx, cy = x0 + (bx0 + bx1) / 2, y0 + (by0 + by1) / 2
            vx, vy = (cx - prev[0], cy - prev[1]) if prev else (0, 0); prev = (cx, cy)
            print(f"{f},{cx:.1f},{cy:.1f},{bx1 - bx0 + 1},{by1 - by0 + 1},{mask.mean():.3f},{vx:.1f},{vy:.1f}")


def bg_of(r, spec):
    if spec != "auto":
        rr, gg, bb = (int(v) for v in spec.split(",")); return np.array([bb, gg, rr])
    return np.median(np.concatenate([r[0], r[-1], r[:, 0], r[:, -1]]), axis=0)


def bands(a):                                     # each text line: y-band, x-extent, centre, ink count, darkest-10% colour
    print("frame,line,y0,y1,x0,x1,cx,ink,dark_hex")
    for f in range(a.f0, a.f1 + 1):
        im = frame(a.dir, f); x0, y0, w, h = roi_of(a.roi, im); r = im[y0:y0 + h, x0:x0 + w].astype(np.int16)
        m = np.abs(r - bg_of(r, a.bg)).max(axis=2) > a.thresh
        ys = np.nonzero(m.sum(axis=1) > 1)[0]; runs = []
        for yy in ys:
            if runs and yy - runs[-1][1] <= a.gap: runs[-1][1] = yy
            else: runs.append([yy, yy])
        for i, (r0, r1) in enumerate([q for q in runs if q[1] - q[0] >= 3]):
            sub = m[r0:r1 + 1]; xs = np.nonzero(sub.sum(axis=0))[0]; px = r[r0:r1 + 1][sub]
            dk = px[np.argsort(px.sum(axis=1))[:max(1, len(px) // 10)]].mean(axis=0)
            print(f"{f},{i},{y0 + r0},{y0 + r1},{x0 + xs[0]},{x0 + xs[-1]},{x0 + (xs[0] + xs[-1]) / 2:.1f},{int(sub.sum())},#{int(dk[2]):02X}{int(dk[1]):02X}{int(dk[0]):02X}")


def edge(a):                                      # sub-pixel crossing of the mid level between the line's two ends
    x0, y0, x1, y1 = (float(v) for v in a.line.split(",")); n = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
    xs, ys = np.linspace(x0, x1, n), np.linspace(y0, y1, n)
    print("frame,pos,x,y")
    for f in range(a.f0, a.f1 + 1):
        L = frame(a.dir, f).astype(np.float32).mean(axis=2)
        v = cv2.remap(L, xs.astype(np.float32)[None], ys.astype(np.float32)[None], cv2.INTER_LINEAR)[0]
        k = max(1, min(4, n // 4)); mid = (v[:k].mean() + v[-k:].mean()) / 2; sg = np.sign(v - mid); idx = np.nonzero(np.diff(sg) != 0)[0]
        if not len(idx): print(f"{f},,,"); continue
        i = idx[-1] if a.last else idx[0]; t = i + (mid - v[i]) / (v[i + 1] - v[i] + 1e-9); u = t / max(1, n - 1)
        print(f"{f},{t:.2f},{x0 + (x1 - x0) * u:.2f},{y0 + (y1 - y0) * u:.2f}")


def grade(a):                                     # median colour of sample patches: tint, haze, fades, exposure over time
    print("frame,r,g,b,luma")
    for f in range(a.f0, a.f1 + 1):
        im = frame(a.dir, f); H, W = im.shape[:2]
        pts = [tuple(int(v) for v in p.split(",")) for p in a.pts.split(";") if p] or \
              [(8, 8), (W - 9, 8), (8, H - 9), (W - 9, H - 9), (W // 2, 8), (W // 2, H - 9), (8, H // 2), (W - 9, H // 2)]
        cs = np.array([im[max(0, y - 4):y + 4, max(0, x - 4):x + 4].reshape(-1, 3).mean(axis=0) for x, y in pts])
        b, g, r = np.median(cs, axis=0); print(f"{f},{r:.1f},{g:.1f},{b:.1f},{.2126 * r + .7152 * g + .0722 * b:.1f}")


if __name__ == "__main__":
    main()
