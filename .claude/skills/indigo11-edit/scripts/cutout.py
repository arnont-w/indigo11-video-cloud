#!/usr/bin/env python3
"""Remove a video's background AND guarantee the cutout stays frame-synced to the source.

Why this exists
---------------
A background matter that re-encodes at the container's *declared* frame rate (not the
source's *actual* one) makes the cutout drift.
Phone/screen-capture clips are very often VFR (variable frame rate): they declare
60fps but really run ~57fps with 843 frames. The matter then writes 887 frames at
a true 60fps - same content, stretched ~5% longer. Layered on top of the
original in any editor, the cutout drifts behind the source and you get a
ghost "double head", worst at the end of the clip. It is NOT a matte-quality bug
and NOT the model being slow - it is a frame-count mismatch.

The fix is to normalize the SOURCE to CFR first, then matte that. Once the source
is CFR, the matter's output lands frame-for-frame on the same timeline and the
overlay is pixel-perfect for the whole clip - no post-hoc resync needed.

Pipeline
--------
1. Probe source: r_frame_rate vs avg_frame_rate vs counted frames.
2. If VFR (or --force-cfr), transcode source → CFR mp4 at the target fps.
3. Matte every frame of the CFR source with rembg (u2net_human_seg, on this computer) → alpha webm
   (VP9 + alpha) at the same fps, so frame N of the cutout = frame N of the source.
4. Verify cutout frame count + duration match the CFR source. Warn if not.

Outputs (kit convention: projects/_work/cutout/<basename>/)
  <basename>-cfr.mp4 the CFR-normalized source (use THIS as the bg layer)
  <basename>-cutout.webm alpha cutout, frame-synced to <basename>-cfr.mp4
  meta.json

The agent does NO judgment here - this is a deterministic ffmpeg/ffprobe +
remove-background wrapper. All "where does the cutout go in the composition"
decisions live in the SKILL.md / downstream skills.

Backend (no HyperFrames needed): rembg in its own venv ~/.indigo11/cutout-env (Windows %USERPROFILE%/.indigo11/cutout-env),
installed on FIRST USE only (~400 MB with the u2net model). Without it the script prints the Thai message and exits:
  python3 .claude/skills/indigo11-edit/scripts/cutout.py --install      (ask the user first: it downloads ~400 MB)
A working `rembg` CLI already on PATH is used as is. INDIGO11_CUTOUT_ENV=<venv dir> points at another rembg venv (tests).

Usage (from the starter folder; Windows: python):
  python3 .claude/skills/indigo11-edit/scripts/cutout.py <input-video> [--output-dir DIR] [--fps N]
                    [--force-cfr] [--quality fast|balanced|best] [--model u2net_human_seg|u2net|isnet-general-use]
"""
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

# Thai text in notes[] is printed to stdout with ensure_ascii=False. On a Windows
# cp874/cp1252 console that raises UnicodeEncodeError; force UTF-8 on both OSes so
# the JSON (and any Thai) always prints. No-op effect on macOS/Linux (already UTF-8).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def find_kit_root(start):
    """The starter folder (has _system/engine/_env.py): outputs land in <starter>/projects/_work/cutout/.
    Tries the script's own location first, then cwd."""
    here = os.path.dirname(os.path.realpath(__file__))
    for s0 in (here, os.path.abspath(start)):
        d = s0
        while True:
            if os.path.exists(os.path.join(d, "_system", "engine", "_env.py")):
                return d
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    d = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(d, "CLAUDE.md")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return os.path.abspath(start)
        d = parent


def ffprobe_json(path):
    r = run([
        "ffprobe", "-v", "error", "-select_streams", "v",
        "-count_frames",
        "-show_entries", "stream=r_frame_rate,avg_frame_rate,nb_read_frames",
        "-show_entries", "format=duration",
        "-of", "json", path,
    ])
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {r.stderr.strip()}")
    j = json.loads(r.stdout)
    st = j["streams"][0]

    def rate(s):
        if not s or s in ("0/0", "N/A"):
            return None
        num, den = s.split("/")
        den = float(den)
        return float(num) / den if den else None

    return {
        "r_frame_rate": rate(st.get("r_frame_rate")),
        "avg_frame_rate": rate(st.get("avg_frame_rate")),
        "frames": int(st.get("nb_read_frames") or 0),
        "duration": float(j["format"]["duration"]),
    }


def is_vfr(info, tol=0.01):
    """VFR when declared rate and measured average rate disagree, OR when the
    COUNTED frames imply an effective rate that differs from the declared rate.

    A phone/screen-capture clip can report r==avg==60 yet actually contain fewer
    frames (~57fps effective). u2net then re-times it to a true 60fps -> the
    ghost double-head (rule #27). The old check only compared r vs avg and missed
    this 'labeled-CFR-but-really-VFR' case (H14). Use nb_read_frames/duration too.
    """
    r, a = info["r_frame_rate"], info["avg_frame_rate"]
    if not r or not a:
        return True  # can't confirm CFR -> treat as VFR to be safe
    if abs(r - a) / r > tol:
        return True
    frames, dur = info.get("frames") or 0, info.get("duration") or 0
    if frames and dur:
        effective = frames / dur
        if abs(effective - r) / r > tol:
            return True
    return False


def has_audio(path) -> bool:
    """True if the file has at least one audio stream (so CFR transcode doesn't
    force -c:a aac on a silent screen-capture and error out - M-CUT1)."""
    r = run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
             "stream=index", "-of", "csv=p=0", path])
    return r.returncode == 0 and bool(r.stdout.strip())


WIN = os.name == "nt"
ENV_DIR = os.environ.get("INDIGO11_CUTOUT_ENV") or os.path.join(os.path.expanduser("~"), ".indigo11", "cutout-env")
ENV_PY = os.path.join(ENV_DIR, "Scripts", "python.exe") if WIN else os.path.join(ENV_DIR, "bin", "python3")
INSTALL_MSG = ("ลบพื้นหลังวิดีโอต้องใช้ rembg + โมเดล u2net (~400 MB, โหลดครั้งแรกครั้งเดียว ลงใน ~/.indigo11/cutout-env "
               "ไม่แตะ Python ของระบบ). ถามผู้ใช้ก่อน แล้วรัน: "
               + ("python" if WIN else "python3") + " .claude/skills/indigo11-edit/scripts/cutout.py --install")

# runs inside the rembg Python: matte every PNG of a folder, keep names (frame order) -> RGBA PNGs
_MATTE = r"""
import sys, os
from rembg import new_session, remove
from PIL import Image
src, dst, model = sys.argv[1], sys.argv[2], sys.argv[3]
sess = new_session(model)
names = sorted(n for n in os.listdir(src) if n.endswith('.png'))
for i, n in enumerate(names):
    remove(Image.open(os.path.join(src, n)), session=sess).save(os.path.join(dst, n))
    if i % 30 == 0: print(f'  matte {i + 1}/{len(names)}', file=sys.stderr, flush=True)
print(len(names))
"""


def find_matter():
    """-> ('py', python) for our venv, ('cli', rembg) for a working rembg CLI, or None (not installed)."""
    if os.path.exists(ENV_PY) and run([ENV_PY, "-c", "import rembg, onnxruntime"]).returncode == 0:
        return ("py", ENV_PY)
    cli = shutil.which("rembg")
    if cli and run([cli, "--help"]).returncode == 0:
        return ("cli", cli)
    return None


def install_matter():
    """First-use install: ~/.indigo11/cutout-env with rembg[cpu] (the model downloads on the first matte)."""
    base = None
    for cand in (os.path.join(os.path.expanduser("~"), ".indigo11", "venv", "Scripts" if WIN else "bin", "python.exe" if WIN else "python3"),
                 sys.executable):
        if cand and os.path.exists(cand):
            base = cand; break
    print("กำลังลง rembg ลง ~/.indigo11/cutout-env (~400 MB รวมโมเดล ครั้งเดียว) ...", file=sys.stderr, flush=True)
    if not os.path.exists(ENV_PY):
        r = subprocess.run([base, "-m", "venv", ENV_DIR])
        if r.returncode != 0 or not os.path.exists(ENV_PY):
            return False
    r = subprocess.run([ENV_PY, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "rembg[cpu]", "pillow"])
    return r.returncode == 0 and find_matter() is not None


def matte_video(matter, cfr_path, out_webm, fps, model, quality, work):
    """CFR mp4 -> numbered PNGs -> rembg -> VP9+alpha webm at the same fps (same frame count)."""
    fin, fout = os.path.join(work, "in"), os.path.join(work, "out")
    os.makedirs(fin, exist_ok=True); os.makedirs(fout, exist_ok=True)
    r = run(["ffmpeg", "-y", "-v", "error", "-i", cfr_path, "-vsync", "passthrough", os.path.join(fin, "%06d.png")])
    if r.returncode != 0:
        return f"frame extract failed: {r.stderr[-300:]}"
    kind, exe = matter
    if kind == "py":
        r = subprocess.run([exe, "-c", _MATTE, fin, fout, model], stdout=subprocess.PIPE, text=True)
    else:
        r = subprocess.run([exe, "p", "-m", model, fin, fout])
        for n in os.listdir(fout):   # rembg p may write .png next to the same name; keep only pngs
            if not n.endswith(".png"):
                os.remove(os.path.join(fout, n))
    if r.returncode != 0:
        return "rembg failed (see the lines above)"
    crf = {"fast": "40", "balanced": "32", "best": "24"}[quality]
    r = run(["ffmpeg", "-y", "-v", "error", "-framerate", str(fps), "-i", os.path.join(fout, "%06d.png"),
             "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", crf, "-auto-alt-ref", "0",
             "-row-mt", "1", out_webm])
    if r.returncode != 0:
        return f"webm encode failed: {r.stderr[-300:]}"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", nargs="?")
    ap.add_argument("--install", action="store_true",
                    help="First-use install of rembg into ~/.indigo11/cutout-env (~400 MB). Ask the user first.")
    ap.add_argument("--model", default="u2net_human_seg",
                    help="rembg model: u2net_human_seg (people, default), u2net (any object), isnet-general-use.")
    ap.add_argument("--output-dir", default=None)
    ap.add_argument("--fps", type=float, default=None,
                    help="Target CFR fps. Default: source's declared rate (rounded).")
    ap.add_argument("--force-cfr", action="store_true",
                    help="Always transcode to CFR even if source looks CFR already.")
    ap.add_argument("--allow-unsynced", action="store_true",
                    help="Return ok:true even if the cutout frame-count/duration doesn't match "
                         "the source. Default is FAIL CLOSED (ok:false) so a drifting cutout is "
                         "never silently shipped downstream (P0-14).")
    ap.add_argument("--quality", default="balanced",
                    choices=["fast", "balanced", "best"],
                    help="webm (VP9 + alpha) encoder quality.")
    args = ap.parse_args()
    # No --keep-intermediates: both outputs (cfr.mp4 + cutout.webm) are deliverables
    # used together downstream, and the script writes no throwaway intermediates.

    if args.install:
        ok = find_matter() is not None or install_matter()
        print(json.dumps({"ok": ok, "installed": ok, "env": ENV_DIR}, ensure_ascii=False))
        sys.exit(0 if ok else 1)
    if not args.input:
        ap.error("input video is required")
    matter = find_matter()
    if not matter:
        print(json.dumps({"ok": False, "needs_install": True, "error": INSTALL_MSG}, ensure_ascii=False))
        sys.exit(2)

    src = os.path.abspath(args.input)
    if not os.path.exists(src):
        print(json.dumps({"ok": False, "error": f"input not found: {src}"}))
        sys.exit(1)

    base = os.path.splitext(os.path.basename(src))[0]
    out_dir = args.output_dir or os.path.join(
        find_kit_root(os.getcwd()), "projects", "_work", "cutout", base)
    os.makedirs(out_dir, exist_ok=True)

    started = time.time()
    info = ffprobe_json(src)
    vfr = is_vfr(info)
    target_fps = args.fps or (round(info["r_frame_rate"]) if info["r_frame_rate"] else 30)
    notes = []

    # ---- Step 1: normalize source to CFR (the actual fix) ----
    cfr_path = os.path.join(out_dir, f"{base}-cfr.mp4")
    if vfr or args.force_cfr:
        reason = "VFR detected" if vfr else "--force-cfr"
        notes.append(
            f"{reason}: declared {info['r_frame_rate']}fps vs avg "
            f"{info['avg_frame_rate']}fps, {info['frames']} frames. "
            f"Transcoded source → CFR {target_fps}fps so the cutout stays in sync."
        )
        cmd = ["ffmpeg", "-y", "-i", src,
               "-vsync", "cfr", "-r", str(target_fps),
               "-c:v", "libx264", "-preset", "fast", "-crf", "18"]
        # Only touch audio if there IS an audio stream - forcing -c:a aac on a
        # silent screen-capture makes ffmpeg error out (M-CUT1).
        cmd += (["-c:a", "aac", "-b:a", "192k"] if has_audio(src) else ["-an"])
        cmd.append(cfr_path)
        r = run(cmd)
        if r.returncode != 0:
            print(json.dumps({"ok": False, "error": f"CFR transcode failed: {r.stderr[-500:]}"}))
            sys.exit(1)
    else:
        # Source is already CFR. If it's already an MP4, copy; otherwise REMUX into
        # an MP4 container (a byte-copy under a .mp4 name mislabels the container -
        # P0-14). -c copy is safe here (no re-encode, no sub-range) since we keep
        # every frame; only the container changes.
        if os.path.splitext(src)[1].lower() == ".mp4":
            notes.append(f"Source already CFR MP4 ({info['r_frame_rate']}fps); copied.")
            shutil.copy2(src, cfr_path)
        else:
            notes.append(f"Source already CFR ({info['r_frame_rate']}fps) but not MP4; remuxed to MP4.")
            r = run(["ffmpeg", "-y", "-i", src, "-c", "copy", "-movflags", "+faststart", cfr_path])
            if r.returncode != 0:
                # remux can fail if the codec isn't MP4-compatible - fall back to re-encode
                cmd = ["ffmpeg", "-y", "-i", src, "-c:v", "libx264", "-preset", "fast", "-crf", "18"]
                cmd += (["-c:a", "aac", "-b:a", "192k"] if has_audio(src) else ["-an"])
                cmd.append(cfr_path)
                r = run(cmd)
                if r.returncode != 0:
                    print(json.dumps({"ok": False, "error": f"remux/transcode failed: {r.stderr[-500:]}"}))
                    sys.exit(1)

    cfr_info = ffprobe_json(cfr_path)

    # ---- Step 2: matte the CFR source (rembg / u2net, on this computer) ----
    cutout_path = os.path.join(out_dir, f"{base}-cutout.webm")
    work = os.path.join(out_dir, "_frames")
    shutil.rmtree(work, ignore_errors=True)
    err = matte_video(matter, cfr_path, cutout_path, target_fps, args.model, args.quality, work)
    shutil.rmtree(work, ignore_errors=True)
    if err or not os.path.exists(cutout_path):
        print(json.dumps({"ok": False, "error": f"remove-background failed: {err}"}, ensure_ascii=False))
        sys.exit(1)

    # ---- Step 3: verify sync ----
    cut_info = ffprobe_json(cutout_path)
    frame_match = cut_info["frames"] == cfr_info["frames"]
    dur_match = abs(cut_info["duration"] - cfr_info["duration"]) < 0.05
    synced = frame_match and dur_match
    if synced:
        notes.append(
            f"Verified in sync: cutout {cut_info['frames']} frames / "
            f"{cut_info['duration']:.3f}s == source {cfr_info['frames']} / "
            f"{cfr_info['duration']:.3f}s."
        )
    else:
        notes.append(
            f"WARNING: cutout {cut_info['frames']}f/{cut_info['duration']:.3f}s "
            f"!= source {cfr_info['frames']}f/{cfr_info['duration']:.3f}s. "
            "Overlay may still drift - re-run, or report to maintainer."
        )

    meta = {
        "skill": "indigo11-cutout",
        "version": "1.0",
        "source_video": src,
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started)),
        "duration_seconds": round(time.time() - started, 1),
        "params": {
            "fps": target_fps,
            "quality": args.quality,
            "model": args.model,
            "backend": "rembg-" + matter[0],
            "source_was_vfr": vfr,
        },
        "source_probe": info,
        "cfr_probe": cfr_info,
        "cutout_probe": cut_info,
        "synced": synced,
        "outputs": [cfr_path, cutout_path],
        "notes": notes,
    }
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # Fail CLOSED on drift: an agent reading ok:true would otherwise ship a
    # drifting cutout (P0-14). --allow-unsynced overrides for the rare case the
    # user accepts a small mismatch.
    ok = synced or args.allow_unsynced
    out = {
        "ok": ok,
        "synced": synced,
        "cfr_source": cfr_path,
        "cutout": cutout_path,
        "meta": os.path.join(out_dir, "meta.json"),
        "notes": notes,
    }
    if not synced and not args.allow_unsynced:
        out["error"] = ("cutout is out of sync with the source (frame/duration mismatch). "
                        "Re-run, or pass --allow-unsynced to accept it. "
                        "(cutout ไม่ตรงกับต้นฉบับ - รันใหม่ หรือใส่ --allow-unsynced)")
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
