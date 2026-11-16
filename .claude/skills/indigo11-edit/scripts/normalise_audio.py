#!/usr/bin/env python3
"""Normalize a clip's loudness to a target - the same thing CapCut's
"Normalize loudness" toggle does, done with ffmpeg's EBU R128 `loudnorm`.

Why this exists: when you assemble several takes/clips, each was recorded at a
different distance/gain, so one segment is loud and the next is quiet. CapCut's
"Normalize loudness" measures each clip's integrated loudness (LUFS) and applies
gain so they all sit at one target level. `ffmpeg loudnorm` is the exact same
EBU R128 standard.

Deterministic plumbing only (edit-rules #18) - no judgment, no AI. The agent
decides WHETHER to normalize (via AskUserQuestion); this script just measures and
applies. Two-pass for accuracy (CapCut does the same): pass 1 measures, pass 2
applies using the measured values so the result lands on target without pumping.

Targets (integrated loudness I, true peak TP):
  social I=-14 LUFS, TP=-1.0 dBTP ← default (TikTok/IG/YouTube spec, = CapCut)
  broadcast I=-23 LUFS, TP=-2.0 dBTP ← EBU R128 broadcast
  podcast I=-16 LUFS, TP=-1.5 dBTP

Usage:
  python3 normalise_audio.py in.mp4 -o out.mp4 # social default
  python3 normalise_audio.py in.mp4 -o out.mp4 --target broadcast
  python3 normalise_audio.py in.mp4 -o out.mp4 --i -14 --tp -1.0 --lra 11
  python3 normalise_audio.py in.mp4 --measure-only # just print LUFS

Video is stream-copied (`-c:v copy`) - only audio is re-encoded, so this is fast
and lossless on the picture. (Audio MUST be re-encoded; you can't gain-adjust a
copied stream. That's expected and does not trigger the rule-#10 keyframe issue,
which is about cutting video sub-ranges - we don't cut here.)
"""
from __future__ import annotations
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import os as _os_utf8, sys as _sys_utf8
if _os_utf8.name == "nt":  # cp874 console crashes printing Thai (rule #32)
    for _s in (_sys_utf8.stdout, _sys_utf8.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

TARGETS = {
    "social": (-14.0, -1.0, 11.0),
    "broadcast": (-23.0, -2.0, 11.0),
    "podcast": (-16.0, -1.5, 11.0),
}


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False))


def err(msg, fix=""):
    emit({"ok": False, "error": msg, "fix": fix})
    sys.exit(1)


def measure(inp: str, I: float, TP: float, LRA: float) -> dict:
    """Pass 1 - run loudnorm in analysis mode, parse the JSON it prints."""
    cmd = [
        "ffmpeg", "-hide_banner", "-i", inp,
        "-af", f"loudnorm=I={I}:TP={TP}:LRA={LRA}:print_format=json",
        "-f", "null", "-",
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    # loudnorm prints the JSON block to stderr, at the tail.
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", p.stderr, re.S)
    if not m:
        err("ffmpeg loudnorm measurement failed",
            "check the file has an audio track (ffprobe <file>)")
    return json.loads(m.group(0))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--target", choices=list(TARGETS), default="social",
                    help="Preset target. Default social (-14 LUFS = CapCut).")
    ap.add_argument("--i", type=float, help="Override integrated loudness LUFS")
    ap.add_argument("--tp", type=float, help="Override true peak dBTP")
    ap.add_argument("--lra", type=float, help="Override loudness range")
    ap.add_argument("--measure-only", action="store_true",
                    help="Print current loudness, don't write a file")
    args = ap.parse_args()

    inp = args.input
    if not Path(inp).exists():
        err(f"Input not found: {inp}")

    I, TP, LRA = TARGETS[args.target]
    if args.i is not None:
        I = args.i
    if args.tp is not None:
        TP = args.tp
    if args.lra is not None:
        LRA = args.lra

    meas = measure(inp, I, TP, LRA)

    if args.measure_only:
        emit({
            "ok": True, "op": "measure-loudness", "input": inp,
            "measured_I_lufs": float(meas["input_i"]),
            "measured_TP_dbtp": float(meas["input_tp"]),
            "measured_LRA": float(meas["input_lra"]),
            "target": args.target, "target_I_lufs": I,
        })
        return 0

    out = args.output or str(Path(inp).with_stem(Path(inp).stem + "-norm"))

    # Pass 2 - apply with measured values (the accurate, no-pump path).
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-i", inp,
        "-af",
        (f"loudnorm=I={I}:TP={TP}:LRA={LRA}"
         f":measured_I={meas['input_i']}"
         f":measured_TP={meas['input_tp']}"
         f":measured_LRA={meas['input_lra']}"
         f":measured_thresh={meas['input_thresh']}"
         f":offset={meas['target_offset']}"
         f":linear=true:print_format=summary"),
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        out,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0 or not Path(out).exists():
        err("ffmpeg loudnorm apply failed", p.stderr.strip().splitlines()[-1] if p.stderr else "")

    emit({
        "ok": True, "op": "normalize-loudness", "input": inp, "output": out,
        "target": args.target, "target_I_lufs": I, "target_TP_dbtp": TP,
        "measured_before_I_lufs": float(meas["input_i"]),
        "note": f"normalized to {I} LUFS (CapCut-style). video stream-copied; audio re-encoded.",
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
