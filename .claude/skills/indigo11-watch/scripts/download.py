#!/usr/bin/env python3
"""Download a video URL (YouTube, TikTok, IG reel, X, ...) as mp4 with yt-dlp, for indigo11-watch.

    python3 .claude/skills/indigo11-watch/scripts/download.py "<url>" [--max 1080] [--out <dir>]     (Windows: python)

- yt-dlp comes from the starter (`_system/setup/common/watchdeps.py` installs it into ~/.indigo11/venv); ffmpeg merges video + audio.
- Output: <out>/<title>_<height>p.mp4 (default out = <starter>/projects/_watch/downloads). Last line: `VIDEO: <path>`.
- YouTube sometimes serves only 360p to plain yt-dlp (its SABR / PO-token gate). This script asks yt-dlp to solve YouTube's
  JS challenge with the starter's Node.js, which usually unlocks HD. If you still get 360p, 360p is enough for WATCH;
  for a frame-exact BREAKDOWN/COPY get the original file from the creator or download it in a browser.
- Only download clips you are allowed to study. Never re-upload someone else's video.
"""
import _starter  # indigo11 Video: runtime PATH + the Python the installer prepared
import argparse, os, shutil, subprocess, sys
from pathlib import Path


def ytdlp_cmd():
    try:
        import yt_dlp  # noqa: F401
        return [sys.executable, "-m", "yt_dlp"]
    except Exception:
        exe = shutil.which("yt-dlp")
        if exe: return [exe]
    sys.exit(f"✗ ไม่พบ yt-dlp - รัน: {_starter.PY} _system/setup/common/watchdeps.py")


ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("url")
ap.add_argument("--max", type=int, default=1080, help="max height (default 1080)")
ap.add_argument("--out", default=str(_starter.ROOT / "projects" / "_watch" / "downloads"))
a = ap.parse_args()
out = Path(a.out).expanduser().resolve(); out.mkdir(parents=True, exist_ok=True)
H = a.max
cmd = ytdlp_cmd() + [
    "-f", f"bestvideo[height<={H}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={H}]+bestaudio/best[height<={H}]/best",
    "--merge-output-format", "mp4", "--no-playlist", "--restrict-filenames",
    "-o", str(out / "%(title).80s_%(height)sp.%(ext)s"), "--print", "after_move:filepath",
]
node = shutil.which("node")
if node:   # let yt-dlp run YouTube's JS challenge with our Node.js (newer yt-dlp needs a JS runtime for HD YouTube)
    help_txt = subprocess.run(ytdlp_cmd() + ["--help"], capture_output=True, text=True).stdout
    if "--js-runtimes" in help_txt:
        cmd += ["--js-runtimes", f"node:{node}"]
cmd.append(a.url)
env = {k: v for k, v in os.environ.items() if k != "NODE_OPTIONS"}
r = subprocess.run(cmd, stdout=subprocess.PIPE, text=True, env=env)
paths = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip().endswith(".mp4") and Path(ln.strip()).exists()]
if r.returncode != 0 or not paths:
    sys.exit("✗ โหลดไม่สำเร็จ (ลิงก์ส่วนตัว/ต้องล็อกอิน/โดนบล็อก?) ลองดาวน์โหลดเองแล้วส่ง path ไฟล์แทน")
p = paths[-1]
h = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height", "-of", "csv=p=0", p],
                   capture_output=True, text=True).stdout.strip()
if h and h.isdigit() and int(h) < 480 and "youtu" in a.url:
    print(f"  ! ได้แค่ {h}p (YouTube จำกัด) พอสำหรับ WATCH; ถ้าจะแกะละเอียดให้หาไฟล์ต้นฉบับ")
print(f"VIDEO: {p}")
