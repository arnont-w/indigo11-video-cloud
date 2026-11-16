#!/usr/bin/env python3
"""detect_text.py - local, free on-screen TEXT detection for indigo11-analyze.

Why this exists
---------------
indigo11-analyze's main pass samples SPARSE frames (scene boundaries + start/mid/end).
That misses burned-in text that flashes for <1s between sampled frames - a price,
a product name, a caption, a lower-third. For a viral short-form edit, "does this
clip already have on-screen text, and what does it say, and WHEN" is a real
editorial question (do I add my own caption here or not?).

This closes that blind spot WITHOUT any cloud API, key, or upload:
  - dense-sample frames (default 3 fps) with ffmpeg
  - OCR each frame with macOS Vision (Apple's on-device engine, Thai-capable
    since macOS 14 / 2023) via the `ocrmac` wrapper
  - collapse consecutive frames with the same text into one event with
    {text, t_start_ms, t_end_ms, confidence}

Everything runs on the user's Mac. Nothing leaves the machine. Deterministic
(rule #18): this is plumbing - it reports WHAT text is where; the agent decides
what to DO about it.

Requirements (macOS only)
  - macOS 14+ (Vision Thai support). Verified at runtime.
  - ocrmac in a dedicated venv at ~/.indigo11/ocr-env (install_ocr.sh sets it up).
    We use a venv because Homebrew/system python are PEP-668 "externally managed"
    and refuse a bare `pip install` - same pattern as ~/.indigo11/vad-env for VAD.

Usage
  python3 detect_text.py <video> [--fps 3] [--langs th-TH,en-US] [--width 720]
                                 [--output text_events.json] [--min-conf 0.5]

Output (stdout JSON + optional --output file):
  {"ok": true, "text_events": [
     {"text": "ลด 50%", "t_start_ms": 1200, "t_end_ms": 2400, "confidence": 0.98}
  ], "frames_scanned": 246, "fps": 3, "engine": "macos-vision"}

If ocrmac/venv is missing, prints {"ok": false, "reason": "..."} with the exact
install command and exits 0 (non-fatal - the caller treats text detection as an
optional enrichment, never a hard dependency).
"""
from __future__ import annotations
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# OCR is macOS-only (Apple Vision), but resolve the venv interpreter per-OS layout anyway
# (Windows = Scripts\python.exe, POSIX = bin/python3) so this never hardcodes a bad path.
# On Windows main() returns early at the sys.platform guard, so this is belt-and-suspenders.
_OCR_ENV = Path.home() / ".indigo11" / "ocr-env"
OCR_PYTHON = (_OCR_ENV / "Scripts" / "python.exe") if os.name == "nt" \
    else (_OCR_ENV / "bin" / "python3")


def have_ocr() -> bool:
    return OCR_PYTHON.exists()


def install_hint() -> str:
    skill_dir = Path(__file__).resolve().parent
    return f"bash {skill_dir / 'install_ocr.sh'} # one-time, ~30MB, no PyTorch"


def extract_dense_frames(video: str, out_dir: Path, fps: float, width: int) -> list:
    """Extract frames at `fps`, scaled to `width`. Returns [(t_ms, path), ...]."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(out_dir / "f%05d.jpg")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", video,
           "-vf", f"fps={fps},scale={width}:-2", "-q:v", "4", "-y", pattern]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg frame extract failed: {res.stderr[-400:]}")
    frames = sorted(out_dir.glob("f*.jpg"))
    step_ms = 1000.0 / fps
    # ffmpeg fps filter emits frame N at t = N/fps (frame 1 ≈ 0ms)
    return [(int(round(i * step_ms)), str(p)) for i, p in enumerate(frames)]


# This block runs INSIDE the ocr-env python (Vision lives there). We hand it the
# frame list as JSON on argv so the heavy import (pyobjc/Vision) happens once.
OCR_WORKER = r'''
import sys, json
from ocrmac import ocrmac
frames = json.loads(sys.argv[1])
langs = sys.argv[2].split(",")
min_conf = float(sys.argv[3])
out = []
for t_ms, path in frames:
    try:
        res = ocrmac.OCR(path, language_preference=langs).recognize()
    except Exception as e:
        out.append({"t_ms": t_ms, "text": "", "conf": 0.0})
        continue
    # join all text boxes on the frame above the confidence floor, top-to-bottom
    lines = [(txt, conf) for (txt, conf, _box) in res if conf >= min_conf and txt.strip()]
    text = " ".join(t for t, _ in lines).strip()
    conf = round(min((c for _, c in lines), default=0.0), 3)
    out.append({"t_ms": t_ms, "text": text, "conf": conf})
print(json.dumps(out, ensure_ascii=False))
'''


def ocr_frames(frames: list, langs: str, min_conf: float) -> list:
    """Run OCR in the ocr-env. Returns [{t_ms, text, conf}, ...]."""
    payload = json.dumps(frames, ensure_ascii=False)
    res = subprocess.run(
        [str(OCR_PYTHON), "-c", OCR_WORKER, payload, langs, str(min_conf)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        raise RuntimeError(f"ocr worker failed: {res.stderr[-400:]}")
    return json.loads(res.stdout)


def collapse_events(ocr_rows: list, fps: float) -> list:
    """Merge consecutive same-text frames into one {text, t_start_ms, t_end_ms}.

    A text that persists across N frames becomes one event spanning them. Blank
    frames break a run. We compare on normalized text so tiny OCR jitter on the
    same caption doesn't split it.
    """
    def norm(s):
        return " ".join(s.split()).strip()

    frame_ms = 1000.0 / fps
    events = []
    cur = None
    for row in ocr_rows:
        t = row["t_ms"]
        txt = norm(row["text"])
        if not txt:
            if cur:
                events.append(cur)
                cur = None
            continue
        if cur and norm(cur["text"]) == txt:
            cur["t_end_ms"] = int(t + frame_ms)
            cur["confidence"] = max(cur["confidence"], row["conf"])
        else:
            if cur:
                events.append(cur)
            cur = {"text": row["text"], "t_start_ms": t,
                   "t_end_ms": int(t + frame_ms), "confidence": row["conf"]}
    if cur:
        events.append(cur)
    return events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--fps", type=float, default=3.0,
                    help="Dense sample rate (default 3 - catches sub-second text)")
    ap.add_argument("--langs", default="th-TH,en-US",
                    help="Vision language preference, comma-sep (default th-TH,en-US)")
    ap.add_argument("--width", type=int, default=720,
                    help="Frame width for OCR (default 720 - sharper than the 512 analyze uses)")
    ap.add_argument("--min-conf", type=float, default=0.5,
                    help="Drop OCR boxes below this confidence (default 0.5)")
    ap.add_argument("--output", help="Also write text_events JSON here")
    args = ap.parse_args()

    if sys.platform != "darwin":
        print(json.dumps({"ok": False, "reason": "macOS-only (uses Apple Vision OCR)"}))
        return
    if not have_ocr():
        print(json.dumps({"ok": False, "reason": "ocr-env not installed",
                          "install": install_hint()}, ensure_ascii=False))
        return
    if not Path(args.video).exists():
        print(json.dumps({"ok": False, "reason": f"video not found: {args.video}"}))
        return

    tmp = Path(tempfile.mkdtemp(prefix="indigo11-ocr-"))
    try:
        frames = extract_dense_frames(args.video, tmp, args.fps, args.width)
        rows = ocr_frames(frames, args.langs, args.min_conf)
        events = collapse_events(rows, args.fps)
    except Exception as e:
        # Non-fatal: the caller treats text detection as optional enrichment.
        print(json.dumps({"ok": False, "reason": str(e)}, ensure_ascii=False))
        return
    finally:
        for f in tmp.glob("*"):
            f.unlink(missing_ok=True)
        tmp.rmdir()

    result = {
        "ok": True,
        "engine": "macos-vision",
        "fps": args.fps,
        "frames_scanned": len(frames),
        "text_events": events,
    }
    if args.output:
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
