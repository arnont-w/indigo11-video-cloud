# /// script
# dependencies = ["numpy"]
# ///
"""Frame-difference scan: hard cuts and single-frame pops (a frame that differs from BOTH neighbours).

    python3 .claude/skills/indigo11-watch/scripts/popscan.py <video> [--ratio 3] [--min 6] [--exclude x,y,w,h ...] [--period 100:400]

A pop = diff(f-1→f) and diff(f→f+1) both > ratio × the local median and > min (mean abs diff, 0-255 on a 64-px-wide gray
copy). Use it on our own renders before delivery (a flicker/glitch frame), and on references to find flashes and cuts.
--exclude drops regions (full-res px) such as embedded footage whose own cuts are not structural.
--period A:B finds a loop: for every P in [A, B] the mean diff between frame f and f+P; prints the best P (always run it before
trusting a "the clip loops every N frames" claim from a Level 2 analysis).
"""
import _starter  # indigo11 Video: runtime PATH + the Python the installer prepared
_starter.needs("numpy")
import argparse, subprocess, json
import numpy as np


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("video"); ap.add_argument("--ratio", type=float, default=3); ap.add_argument("--min", type=float, default=6)
    ap.add_argument("--exclude", action="append", default=[]); ap.add_argument("--period", default=None)
    a = ap.parse_args()
    pr = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate", "-of", "json", a.video],
                                   capture_output=True, text=True).stdout)["streams"][0]
    num, den = (int(v) for v in pr["r_frame_rate"].split("/")); fps = num / den
    W = 64; H = max(2, round(pr["height"] * W / pr["width"] / 2) * 2)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", a.video, "-vf", f"scale={W}:{H},format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, H, W).astype(np.float32)
    keep = np.ones((H, W), bool); sx, sy = W / pr["width"], H / pr["height"]
    for r in a.exclude:
        x, y, w, h = (float(v) for v in r.split(",")); keep[int(y * sy):int(np.ceil((y + h) * sy)), int(x * sx):int(np.ceil((x + w) * sx))] = False
    fr = fr[:, keep][:, :, None] if not keep.all() else fr
    d = np.abs(np.diff(fr, axis=0)).mean(axis=(1, 2))                    # d[i] = change i → i+1
    med = np.array([np.median(d[max(0, i - 8): i + 9]) for i in range(len(d))]) + 0.5
    cuts = [i + 1 for i in range(len(d)) if d[i] > a.ratio * med[i] and d[i] > a.min]
    pops = [f for f in range(1, len(fr) - 1) if d[f - 1] > a.ratio * med[f - 1] and d[f] > a.ratio * med[f] and min(d[f - 1], d[f]) > a.min]
    print(f"{len(fr)} frames @ {fps:.2f} fps · cuts {len(cuts)} · single-frame pops {len(pops)}")
    print("cuts :", ", ".join(f"f{f}({f / fps:.2f}s)" for f in cuts[:80]))
    print("pops :", ", ".join(f"f{f}({f / fps:.2f}s)" for f in pops) or "none")
    if a.period:
        lo, hi = (int(v) for v in a.period.split(":")); hi = min(hi, len(fr) - 2)
        sc = {P: float(np.abs(fr[P:] - fr[:-P]).mean()) for P in range(max(1, lo), hi + 1)}
        best = sorted(sc, key=sc.get)[:3]
        print("loop :", ", ".join(f"P={P} ({P / fps:.3f}s) diff {sc[P]:.2f}" for P in best), f"· median over range {np.median(list(sc.values())):.2f}")


if __name__ == "__main__":
    main()
