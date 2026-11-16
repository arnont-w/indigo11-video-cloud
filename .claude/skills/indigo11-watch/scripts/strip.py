# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow"]
# ///
"""Every frame (or a chosen fps) of a short window as one labelled sheet, for studying a transition or an action.

  python3 .claude/skills/indigo11-watch/scripts/strip.py videos/ig1_free-apis.mp4 --from 4.8 --to 5.4 [--fps 0 = native] [--w 300] --out breakdown/ig1_free-apis/strips/cut_05.jpg
"""
import _starter  # indigo11 Video: runtime PATH + the Python the installer prepared
_starter.needs("PIL")
import argparse, json, subprocess, tempfile, shutil
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video"); ap.add_argument("--from", dest="t0", type=float, required=True); ap.add_argument("--to", dest="t1", type=float, required=True)
    ap.add_argument("--fps", type=float, default=0); ap.add_argument("--w", type=int, default=0); ap.add_argument("--cols", type=int, default=0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate", "-of", "json", a.video], capture_output=True, text=True)
    s = json.loads(r.stdout)["streams"][0]; W, H = s["width"], s["height"]; n, d = s["r_frame_rate"].split("/"); native = float(n) / float(d)
    step = max(1, round(native / (a.fps or native)))       # exact source frames, labelled by true frame index
    f0, f1 = int(round(a.t0 * native)), int(round(a.t1 * native))
    w = a.w or (240 if H > W else 300 if H == W else 400); h = int(round(w * H / W / 2)) * 2
    cols = a.cols or (8 if H > W else 6 if H == W else 5)
    tmp = Path(tempfile.mkdtemp())
    subprocess.run(["ffmpeg", "-v", "error", "-i", a.video, "-vf",
                    f"select='between(n\\,{f0}\\,{f1 - 1})*not(mod(n-{f0}\\,{step}))',scale={w}:{h}",
                    "-fps_mode", "vfr", "-q:v", "3", str(tmp / "f_%04d.jpg")])
    files = sorted(tmp.glob("f_*.jpg")); lab = 20
    try:
        f = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 14)
    except Exception:
        f = ImageFont.load_default(size=14)
    rows = (len(files) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (w + 4) + 4, rows * (h + lab + 4) + 4), (245, 245, 245)); dr = ImageDraw.Draw(sheet)
    for k, fp in enumerate(files):
        fr = f0 + k * step; t = fr / native; x = 4 + (k % cols) * (w + 4); y = 4 + (k // cols) * (h + lab + 4)
        dr.rectangle([x, y, x + w, y + lab], fill=(30, 30, 30)); dr.text((x + 3, y + 3), f"{t:6.3f}s f{fr}", fill="white", font=f)
        sheet.paste(Image.open(fp), (x, y + lab))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True); sheet.save(a.out, quality=88)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"{a.out}: {len(files)} frames f{f0}-f{f1 - 1} ({a.t0}-{a.t1}s) every {step} frame(s)")


if __name__ == "__main__":
    main()
