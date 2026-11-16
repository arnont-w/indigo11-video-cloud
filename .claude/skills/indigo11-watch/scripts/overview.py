# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow"]
# ///
"""Overview sheets for long videos (tutorials): one frame every N seconds, labelled mm:ss, 4x4 per sheet.

  python3 .claude/skills/indigo11-watch/scripts/overview.py <video> --out <dir> [--every 5] [--w 480]
Writes <dir>/ov_XXX_mm-ss.jpg. Fast: ffmpeg seeks by keyframe-accurate -ss per frame batch via the fps filter.
"""
import _starter  # indigo11 Video: runtime PATH + the Python the installer prepared
_starter.needs("PIL")
import argparse, json, subprocess, tempfile, shutil
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video"); ap.add_argument("--out", required=True)
    ap.add_argument("--every", type=float, default=5); ap.add_argument("--w", type=int, default=480)
    ap.add_argument("--cols", type=int, default=4); ap.add_argument("--rows", type=int, default=4)
    a = ap.parse_args()
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration", "-of", "json", a.video], capture_output=True, text=True)
    j = json.loads(r.stdout); W, H = j["streams"][0]["width"], j["streams"][0]["height"]; dur = float(j["format"]["duration"])
    w = a.w; h = int(round(w * H / W / 2)) * 2
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    if list(out.glob("ov_*.jpg")):
        print(f"skip {a.video} (exists)"); return
    tmp = Path(tempfile.mkdtemp())
    subprocess.run(["ffmpeg", "-v", "error", "-skip_frame", "nokey", "-i", a.video, "-vf", f"fps=1/{a.every},scale={w}:{h}", "-vsync", "vfr", "-q:v", "4", str(tmp / "f_%05d.jpg")])
    files = sorted(tmp.glob("f_*.jpg"))
    if len(files) < dur / a.every * 0.5:        # keyframes too sparse: fall back to full decode
        for p in files: p.unlink()
        subprocess.run(["ffmpeg", "-v", "error", "-i", a.video, "-vf", f"fps=1/{a.every},scale={w}:{h}", "-q:v", "4", str(tmp / "f_%05d.jpg")])
        files = sorted(tmp.glob("f_*.jpg"))
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 16)
    except Exception:
        font = ImageFont.load_default(size=16)
    per = a.cols * a.rows; lab = 22
    for gi in range(0, len(files), per):
        sheet = Image.new("RGB", (a.cols * (w + 4) + 4, a.rows * (h + lab + 4) + 4), (245, 245, 245)); d = ImageDraw.Draw(sheet)
        for k, fp in enumerate(files[gi:gi + per]):
            t = (gi + k) * a.every; x = 4 + (k % a.cols) * (w + 4); y = 4 + (k // a.cols) * (h + lab + 4)
            d.rectangle([x, y, x + w, y + lab], fill=(30, 30, 30)); d.text((x + 4, y + 3), f"{int(t // 60):02d}:{int(t % 60):02d}", fill="white", font=font)
            sheet.paste(Image.open(fp), (x, y + lab))
        t0 = gi * a.every
        sheet.save(out / f"ov_{gi // per:03d}_{int(t0 // 60):02d}-{int(t0 % 60):02d}.jpg", quality=85)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"{Path(a.video).name}: {dur/60:.1f} min, {len(files)} frames, {(len(files) + per - 1) // per} sheets")


if __name__ == "__main__":
    main()
