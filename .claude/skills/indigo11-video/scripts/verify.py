#!/usr/bin/env python3
"""indigo11-video: check work with one picture. Works for B-roll clips and for a finished video (run from the project folder).

  # check B-roll clips before rendering (finds text burned into the clips)
  python3 verify.py <folder of clips> [-o sheet.jpg] [--zone]

  # check a rendered video (do captions appear, do they overflow?)
  python3 verify.py <video.mp4>

Each thumbnail is labelled c<clip> <second>s (drawn with Pillow, because many ffmpeg builds lack drawtext).
Output is ONE contact sheet with the second on every frame, so one look is enough.
Why dense: sampling every 3 s once missed text that appeared in between; the default of 2 frames per second catches it all.
"""
import argparse, pathlib, shutil, subprocess, sys, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _starter import _env  # noqa: E402

_env.needs("PIL")

FPS = 2.0
TW, TH = 150, 267
COLS = 14


def sh(cmd, t=600):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=t, encoding="utf-8", errors="replace")


def dur(p):
    r = sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "csv=p=0", str(p)])
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


ZONE_LO, ZONE_HI = 0.615, 0.771

FIT = ("scale=1080:1920:force_original_aspect_ratio=increase,"
       "crop=1080:1920,setsar=1")


def label(imgs, fps, prefix=""):
    """Write 'c3 12s' in the top-left corner of every thumbnail (Pillow), so nobody has to count frames."""
    from PIL import Image, ImageDraw
    for n, f in enumerate(imgs):
        im = Image.open(f).convert("RGB"); d = ImageDraw.Draw(im)
        txt = f"{prefix}{n / fps:.0f}s" if fps <= 1 else f"{prefix}{n / fps:.1f}s"
        x0, y0, x1, y1 = d.textbbox((4, 3), txt)
        d.rectangle((x0 - 2, y0 - 2, x1 + 2, y1 + 2), fill=(0, 0, 0))
        d.text((4, 3), txt, fill=(255, 230, 0))
        im.save(f, quality=90)


def grab(src, out_dir, fps, label_base=0.0, zone=False, prefix=""):
    """Extract frames and write the second on each image.

    zone=True draws a red frame over the caption zone, so you can see at once whether text overlaps it
    (the render puts a dark shade on that zone, so text inside the frame is not a reason to reject).
    Crops exactly as the render does before shrinking, so you see what the viewer will see."""
    pat = str(out_dir / "%04d.jpg")
    box = ""
    if zone:
        y, h = int(TH * ZONE_LO), int(TH * (ZONE_HI - ZONE_LO))
        box = f",drawbox=x=0:y={y}:w={TW}:h={h}:color=red@0.85:t=2"
    sh(["ffmpeg", "-v", "error", "-i", str(src), "-vf",
        f"fps={fps},{FIT},scale={TW}:{TH}{box}", "-vsync", "0", pat, "-y"])
    imgs = sorted(out_dir.glob("*.jpg"))
    try:
        label(imgs, fps, prefix)
    except Exception as e:
        print(f"  (could not write the numbers on the images: {e})")
    return imgs


def sheet(imgs, dest, cols=COLS):
    """Join the thumbnails into one sheet; splits into several sheets above 140 frames."""
    if not imgs:
        return []
    made = []
    CHUNK = cols * 10
    for gi in range(0, len(imgs), CHUNK):
        part = imgs[gi:gi + CHUNK]
        n = len(part)
        args = []
        for p in part:
            args += ["-i", str(p)]
        fc = ("".join(f"[{i}:v]scale={TW}:{TH}[v{i}];" for i in range(n))
              + "".join(f"[v{i}]" for i in range(n))
              + f"xstack=inputs={n}:fill=black:layout="
              + "|".join(f"{(i % cols) * TW}_{(i // cols) * TH}" for i in range(n))
              + "[o]")
        out = dest if len(imgs) <= CHUNK else dest.with_name(
            f"{dest.stem}_{gi // CHUNK + 1}{dest.suffix}")
        if sh(["ffmpeg", "-v", "error", *args, "-filter_complex", fc,
               "-map", "[o]", str(out), "-y"]).returncode == 0:
            made.append(out)
    return made


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="an .mp4 file or a folder of clips")
    ap.add_argument("--fps", type=float, default=FPS)
    ap.add_argument("-o", "--out", help="where to save the contact sheet")
    ap.add_argument("--zone", action="store_true",
                    help="draw the red caption-zone frame (text inside the frame is not a reason to reject)")
    a = ap.parse_args()

    t = pathlib.Path(a.target)
    if not t.exists():
        sys.exit(f"not found: {t}")

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="verify-"))
    try:
        if t.is_dir():
            clips = sorted([p for p in t.iterdir()
                            if p.suffix.lower() in (".mp4", ".webm", ".mkv", ".mov")])
            if not clips:
                sys.exit(f"no clips in {t}")
            out_base = pathlib.Path(a.out) if a.out else t / "_verify.jpg"
            all_imgs = []
            print(f"checking {len(clips)} clips @ {a.fps} frames/s\n")
            for i, c in enumerate(clips):
                d = tmp / f"c{i}"
                d.mkdir()
                imgs = grab(c, d, a.fps, zone=a.zone, prefix=f"c{i} ")
                print(f"  {c.name:24s} {dur(c):5.1f}s  {len(imgs):3d} frames")
                all_imgs += imgs
            made = sheet(all_imgs, out_base)
        else:
            out_base = pathlib.Path(a.out) if a.out else t.with_name(
                t.stem + "_verify.jpg")
            d = tmp / "v"
            d.mkdir()
            imgs = grab(t, d, a.fps, zone=a.zone)
            print(f"checking {t.name}: {dur(t):.1f}s, {len(imgs)} frames @ {a.fps}/s")
            made = sheet(imgs, out_base)

        if not made:
            sys.exit("could not build the contact sheet")
        print("\nOpen this file:")
        for m in made:
            print(f"  {m}")
        print("\nLook for: text burned into the clip / watermarks / captions overflowing two lines")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
