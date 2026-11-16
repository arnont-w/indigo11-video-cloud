# /// script
# dependencies = ["numpy", "pillow"]
# ///
"""Compare a reference with our remake, frame-locked. REF and OURS can each be an mp4 or a folder of numbered frames (fNNNN.png/jpg).

  video  side-by-side (or stacked) mp4, optional diff panel
         python3 .claude/skills/indigo11-watch/scripts/compare.py video ref.mp4 ours.mp4 out.mp4 [--layout h|v] [--diff] [--offset N] [--audio ref|ours|none]
  sheet  labelled ref|ours pairs for chosen frames (the "never claim a shot matches without looking" check)
         python3 .claude/skills/indigo11-watch/scripts/compare.py sheet ref.mp4 ours.mp4 out.png 0 30 61 90 [--offset N] [--crop x,y,w,h] [--width 360]
  score  mean abs gray difference per frame (0-255) → where the remake drifts
         python3 .claude/skills/indigo11-watch/scripts/compare.py score ref.mp4 ours/ [--res 96] [--ranges A:0-21,B:22-52] [--exclude x,y,w,h ...]
                [--mask keep.png] [--csv out.csv] [--offset N]
--offset shifts ours by N frames (positive = ours starts later). Frame F is fetched by index, so F is exact for every frame incl. the last.
score downsamples both sides with area averaging (same metric for mp4 and PNG inputs) and prints a blank-frame baseline for scale;
--exclude/--mask drop regions that differ on purpose (swapped logo, replaced scene): a low-res pixel counts only if ≥99% of it is kept.
"""
import _starter  # indigo11 Video: runtime PATH + the Python the installer prepared
_starter.needs("numpy", "PIL")
import argparse, json, re, subprocess, sys
from io import BytesIO
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont

IMG = (".png", ".jpg", ".jpeg")


def frames_of(d):
    fs = [p for p in Path(d).iterdir() if p.suffix.lower() in IMG and re.search(r"\d+", p.stem)]
    return sorted(fs, key=lambda p: int(re.findall(r"\d+", p.stem)[-1]))


def probe(v, fps_default=30.0):
    if Path(v).is_dir():
        im = Image.open(frames_of(v)[0]); return im.width, im.height, fps_default
    s = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate",
                                   "-of", "json", v], capture_output=True, text=True).stdout)["streams"][0]
    n, d = (int(x) for x in s["r_frame_rate"].split("/")); return s["width"], s["height"], n / d


def grab(v, f, fps):
    if Path(v).is_dir():
        fs = frames_of(v)
        if not 0 <= f < len(fs): sys.exit(f"frame {f} not in {v} ({len(fs)} frames)")
        return Image.open(fs[f]).convert("RGB")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, (f - .5) / fps):.6f}", "-i", v, "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
                         capture_output=True).stdout
    if not raw: sys.exit(f"frame {f} not decodable from {v}")
    return Image.open(BytesIO(raw)).convert("RGB")


def ff_input(v, fps):
    if not Path(v).is_dir(): return ["-i", v]
    p = frames_of(v)[0]; m = re.match(r"(.*?)(\d+)$", p.stem)
    return ["-framerate", str(fps), "-start_number", str(int(m.group(2))), "-i", str(p.parent / f"{m.group(1)}%0{len(m.group(2))}d{p.suffix}")]


def has_audio(v):
    if Path(v).is_dir(): return False
    return bool(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", v],
                               capture_output=True, text=True).stdout.strip())


def gray_stack(v, W, H, fps):
    if Path(v).is_dir():
        return np.stack([np.asarray(Image.open(p).convert("L").resize((W, H), Image.BOX), np.float32) for p in frames_of(v)])
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", v, "-vf", f"fps={fps},scale={W}:{H}:flags=area,format=gray", "-f", "rawvideo", "-"],
                         capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W).astype(np.float32)


def keep_mask(w, h, W, H, excludes, mask_png):
    m = np.full((h, w), 255, np.uint8)
    for r in excludes:
        x, y, ew, eh = (int(float(v)) for v in r.split(",")); m[max(0, y):y + eh, max(0, x):x + ew] = 0
    if mask_png:
        mm = np.asarray(Image.open(mask_png).convert("L").resize((w, h), Image.NEAREST)); m[mm < 128] = 0
    return np.asarray(Image.fromarray(m).resize((W, H), Image.BOX)) >= 252


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("mode", choices=["video", "sheet", "score"]); ap.add_argument("ref"); ap.add_argument("ours")
    ap.add_argument("rest", nargs="*"); ap.add_argument("--layout", default="h"); ap.add_argument("--diff", action="store_true")
    ap.add_argument("--offset", type=int, default=0); ap.add_argument("--fps", type=float, default=30.0, help="fps for frame folders")
    ap.add_argument("--audio", choices=["auto", "ref", "ours", "none"], default="auto")
    ap.add_argument("--crop", default=None, help="sheet: x,y,w,h crop in full-res px"); ap.add_argument("--width", type=int, default=360)
    ap.add_argument("--res", type=int, default=96, help="score: width of the gray frames"); ap.add_argument("--ranges", default="")
    ap.add_argument("--exclude", action="append", default=[]); ap.add_argument("--mask", default=None); ap.add_argument("--csv", default=None)
    a = ap.parse_args()
    w, h, fps = probe(a.ref, a.fps)
    if Path(a.ref).is_dir() and not Path(a.ours).is_dir(): fps = probe(a.ours, a.fps)[2]
    if a.mode == "video":
        out = a.rest[0]; stack = "hstack" if a.layout == "h" else "vstack"
        sh = "setpts=PTS-STARTPTS" if a.offset == 0 else (f"trim=start_frame={a.offset},setpts=PTS-STARTPTS" if a.offset > 0 else f"tpad=start={-a.offset},setpts=PTS-STARTPTS")
        fc = f"[0:v]scale={w}:{h},fps={fps},setpts=PTS-STARTPTS[r];[1:v]scale={w}:{h},fps={fps},{sh}[o];"
        fc += (f"[r]split[r1][r2];[o]split[o1][o2];[r2][o2]blend=all_mode=difference[d];[r1][o1][d]{stack}=inputs=3[v]" if a.diff else f"[r][o]{stack}=inputs=2[v]")
        src = a.audio if a.audio != "auto" else ("ours" if has_audio(a.ours) else "ref" if has_audio(a.ref) else "none")
        amap = {"ref": ["-map", "0:a?"], "ours": ["-map", "1:a?"], "none": []}[src]
        subprocess.run(["ffmpeg", "-v", "error", "-y", *ff_input(a.ref, fps), *ff_input(a.ours, fps), "-filter_complex", fc, "-map", "[v]", *amap,
                        "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", "-shortest", out], check=True)
        print(f"wrote {out} (audio: {src})"); return
    if a.mode == "sheet":
        out, frames = a.rest[0], [int(x) for x in a.rest[1:]]
        cx, cy, cw, ch = (int(v) for v in a.crop.split(",")) if a.crop else (0, 0, w, h)
        tw = a.width; th = int(ch * tw / cw); font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 18)
        sheet = Image.new("RGB", (tw * 2 + 30, (th + 28) * len(frames)), (245, 245, 240)); dr = ImageDraw.Draw(sheet)
        for i, f in enumerate(frames):
            r = grab(a.ref, f, fps).crop((cx, cy, cx + cw, cy + ch)).resize((tw, th), Image.LANCZOS)
            o = grab(a.ours, f + a.offset, fps).crop((cx, cy, cx + cw, cy + ch)).resize((tw, th), Image.LANCZOS)
            y = i * (th + 28); sheet.paste(r, (0, y + 24)); sheet.paste(o, (tw + 30, y + 24))
            dr.text((4, y + 2), f"REF f{f} ({f / fps:.3f}s)", fill=(20, 20, 20), font=font); dr.text((tw + 34, y + 2), f"OURS f{f + a.offset}", fill=(20, 20, 20), font=font)
        sheet.save(out); print("wrote", out); return
    W = a.res; H = max(2, round(h * W / w / 2) * 2)
    R, O = gray_stack(a.ref, W, H, fps), gray_stack(a.ours, W, H, fps)
    if a.offset > 0: O = O[a.offset:]
    elif a.offset < 0: R = R[-a.offset:]
    n = min(len(R), len(O)); keep = keep_mask(w, h, W, H, a.exclude, a.mask)
    d = np.array([np.abs(R[i] - O[i])[keep].mean() for i in range(n)])
    blank = np.array([np.abs(R[i] - R[i][keep].mean())[keep].mean() for i in range(n)])
    tag = f" · kept {keep.mean() * 100:.0f}% of the frame" if keep.mean() < 1 else ""
    print(f"frames {n} @ {W}x{H}{tag} · mean {d.mean():.2f} · median {np.median(d):.2f} · p95 {np.percentile(d, 95):.2f} · blank-frame baseline {blank.mean():.2f}")
    print("worst: " + ", ".join(f"f{i}({d[i]:.1f})" for i in np.argsort(-d)[:10]))
    for part in filter(None, a.ranges.split(",")):
        name, span = part.split(":"); f0, f1 = (int(x) for x in span.split("-")); seg = d[f0:min(f1, n - 1) + 1]
        if len(seg): print(f"  {name} f{f0}-{f1}: mean {seg.mean():.2f} · max {seg.max():.1f}@f{f0 + int(seg.argmax())}")
    if a.csv:
        Path(a.csv).write_text("frame,diff\n" + "".join(f"{i},{v:.3f}\n" for i, v in enumerate(d))); print("wrote", a.csv)


if __name__ == "__main__":
    main()
