#!/usr/bin/env python3
"""Voice-aware BGM bed prep - measure, gain, duck, master. EBU R128 throughout.

THE DEFAULT CONTRACT (edit-rules #30): whenever music goes under speech,
do NOT guess a volume slider. Measure both sides in LUFS and place the music
bed a fixed offset BELOW the voice, then (optionally) sidechain-duck it so it
breathes with the speech. "ลด volume 90%" by feel is the anti-pattern this
script replaces.

Why LUFS, not dB peak: LUFS is perceived loudness (ITU-R BS.1770). Two tracks
with identical peaks can differ wildly in loudness - mastered stock music is
heavily compressed and will bury speech at the same peak level.

The numbers:
  voice (already normalized) -14 LUFS (social target; normalise_audio.py)
  music bed under speech voice - 20 LU (industry range -18..-25)
  duck on top of that ~6 dB when voice present (sidechaincompress)
  final master -14 LUFS / TP -1.0 (loudnorm, before upload)

Modes:
  # 1) measure any file's integrated loudness
  python3 mix_bgm.py measure voice.mp4

  # 2) prep a music bed: gain music so it sits <offset> LU below the voice,
  # then duck it against the voice (omit --duck to skip ducking)
  python3 mix_bgm.py prep --voice cleaned.mp4 --music track.mp3 -o bed.m4a \
      [--offset 20] [--duck 6]

  # 3) master a finished mix back to the social target
  python3 mix_bgm.py master final.mp4 -o final-mastered.mp4

Output bed is AAC 192k. Use it at data-volume/volume = 1.0 downstream (HyperFrames,
CapCut, ffmpeg amix) - the level is already correct by construction.
Deterministic plumbing (rule #18): WHAT track fits the content stays with the agent.
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


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False))


def err(msg, fix=""):
    emit({"ok": False, "error": msg, "fix": fix})
    sys.exit(1)


def measure_lufs(path: str) -> dict:
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", path,
         "-af", "loudnorm=I=-14:TP=-1:LRA=11:print_format=json",
         "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", p.stderr, re.S)
    if not m:
        err(f"loudnorm measurement failed for {path}",
            "check the file has an audio track (ffprobe <file>)")
    d = json.loads(m.group(0))
    return {"I": float(d["input_i"]), "TP": float(d["input_tp"]),
            "LRA": float(d["input_lra"])}


def cmd_measure(args):
    m = measure_lufs(args.input)
    emit({"ok": True, "op": "measure", "input": args.input,
          "I_lufs": m["I"], "TP_dbtp": m["TP"], "LRA": m["LRA"]})


def cmd_prep(args):
    voice = measure_lufs(args.voice)
    music = measure_lufs(args.music)
    target_music_I = voice["I"] - args.offset
    gain_db = target_music_I - music["I"]
    out = args.output or str(Path(args.music).with_name(
        Path(args.music).stem + "-bed.m4a"))

    if args.duck > 0:
        # gain the music, then duck it against the voice. sidechaincompress:
        # threshold low enough that speech always engages it; ratio sized so
        # reduction ≈ --duck dB; slow-ish release so the bed swells back
        # naturally in pauses instead of pumping.
        ratio = max(1.5, args.duck / 2.0)
        fc = (f"[1:a]volume={gain_db:.2f}dB[m];"
              f"[m][0:a]sidechaincompress=threshold=0.015:ratio={ratio:.1f}"
              f":attack=20:release=400:makeup=1[out]")
        cmd = ["ffmpeg", "-y", "-hide_banner",
               "-i", args.voice, "-i", args.music,
               "-filter_complex", fc, "-map", "[out]",
               "-c:a", "aac", "-b:a", "192k", out]
    else:
        cmd = ["ffmpeg", "-y", "-hide_banner", "-i", args.music,
               "-af", f"volume={gain_db:.2f}dB",
               "-c:a", "aac", "-b:a", "192k", out]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0 or not Path(out).exists():
        err("ffmpeg bed prep failed",
            p.stderr.strip().splitlines()[-1] if p.stderr else "")
    emit({"ok": True, "op": "prep-bgm", "output": out,
          "voice_I_lufs": voice["I"], "music_I_lufs": music["I"],
          "offset_lu": args.offset, "gain_applied_db": round(gain_db, 2),
          "duck_db": args.duck,
          "note": "use at volume=1.0 downstream - level already correct"})


def cmd_master(args):
    out = args.output or str(Path(args.input).with_stem(
        Path(args.input).stem + "-mastered"))
    meas = measure_lufs(args.input)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-i", args.input,
           "-af", "loudnorm=I=-14:TP=-1:LRA=11:linear=true",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", out]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0 or not Path(out).exists():
        err("ffmpeg master failed",
            p.stderr.strip().splitlines()[-1] if p.stderr else "")
    emit({"ok": True, "op": "master", "input": args.input, "output": out,
          "before_I_lufs": meas["I"], "target_I_lufs": -14.0})


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)

    m = sub.add_parser("measure", help="print integrated LUFS of a file")
    m.add_argument("input")
    m.set_defaults(fn=cmd_measure)

    pr = sub.add_parser("prep", help="gain+duck music into a voice-relative bed")
    pr.add_argument("--voice", required=True, help="video/audio with the speech")
    pr.add_argument("--music", required=True)
    pr.add_argument("-o", "--output")
    pr.add_argument("--offset", type=float, default=20.0,
                    help="LU below voice for the bed (default 20; range 18-25)")
    pr.add_argument("--duck", type=float, default=6.0,
                    help="extra dB of sidechain ducking while voice present "
                         "(default 6; 0 = no duck)")
    pr.set_defaults(fn=cmd_prep)

    ma = sub.add_parser("master", help="loudnorm a finished mix to -14 LUFS")
    ma.add_argument("input")
    ma.add_argument("-o", "--output")
    ma.set_defaults(fn=cmd_master)

    args = ap.parse_args()
    for attr in ("input", "voice", "music"):
        v = getattr(args, attr, None)
        if v and not Path(v).exists():
            err(f"not found: {v}")
    args.fn(args)


if __name__ == "__main__":
    main()
