#!/usr/bin/env python3
r"""Detect speech segments using Silero VAD.

Runs inside ~/.indigo11/vad-env (sibling venv created by install_vad.sh).
Outputs JSON list of speech ranges [{start, end}, ...] in seconds.

Usage:
  macOS/Linux: ~/.indigo11/vad-env/bin/python3 vad_detect.py <input.wav> [options]
  Windows: %USERPROFILE%\.indigo11\vad-env\Scripts\python.exe vad_detect.py <input.wav> [options]

Options:
  --min-silence-ms N Gaps shorter than this are NOT cut (default 300)
  --min-speech-ms N Speech blips shorter than this are dropped as noise (default 200)
  --pad-ms N Padding added to each speech range edge (default 200)
  --merge-gap-ms N After VAD, merge adjacent speech ranges that are this close
                       or closer. Default 0 (no merge). Use 300+ for long videos
                       (>30 min) to keep CapCut/NLE segment count manageable.
  --output PATH Write JSON to file (else stdout)

Tuning by content type:
  TikTok/Reels: --min-silence-ms 300 --pad-ms 150 --merge-gap-ms 0
  Talking head (default): --min-silence-ms 300 --pad-ms 200 --merge-gap-ms 0
  Long-form > 30min: --min-silence-ms 500 --pad-ms 250 --merge-gap-ms 300
  Podcast / interview: --min-silence-ms 800 --pad-ms 300 --merge-gap-ms 300

Why merge-gap-ms matters for long video:
  A 2hr talk can produce 1500+ raw speech ranges. CapCut starts lagging at ~500
  segments, may crash at ~1000+. Merging ranges <300ms apart cuts segment count
  by ~half without changing what audio is kept (the gaps were too short to be
  perceived as cuts anyway).

The Silero model loads from ~/.cache/torch on first run (~1.8MB download).
"""

import argparse
import json
import os
import sys
from typing import List, Dict

# Lazy-import silero_vad inside main() so --help works without the dep.
# Importing at module-level would make --help fail in CI / minimal envs.


def merge_close(ranges: List[Dict], merge_gap_s: float) -> List[Dict]:
    """Merge adjacent speech ranges whose gap is <= merge_gap_s."""
    if merge_gap_s <= 0 or not ranges:
        return ranges
    out = [dict(ranges[0])]
    for r in ranges[1:]:
        if r["start"] - out[-1]["end"] <= merge_gap_s:
            out[-1]["end"] = r["end"]
        else:
            out.append(dict(r))
    return out


def load_wav_16k(path: str):
    """Load an audio file as a mono float32 torch tensor at 16 kHz.

    Deliberately AVOIDS silero_vad.read_audio() / torchaudio.load(): recent torchaudio
    (2.x) routes .load() through torchcodec, which dlopen()s libtorchcodec_core*.dll and
    needs FFmpeg "full-shared" DLLs on PATH. On Windows those aren't found, so the whole
    VAD step dies with "Could not load libtorchcodec". soundfile (libsndfile, shipped
    inside the wheel) reads WAV/FLAC/OGG with no external DLLs and behaves identically on
    macOS/Linux/Windows. Resampling, when needed, uses pure-torch ops (no DLL).
    """
    import numpy as np
    import torch
    try:
        import soundfile as sf
    except ImportError:
        # last-resort fallback: silero's torchaudio reader (works on macOS/Linux)
        from silero_vad import read_audio
        return read_audio(path, sampling_rate=16000)

    data, sr = sf.read(path, dtype="float32", always_2d=False)
    if getattr(data, "ndim", 1) > 1: # stereo/multi -> mono
        data = data.mean(axis=1)
    data = np.ascontiguousarray(data, dtype=np.float32)
    if sr != 16000:
        try:
            import torchaudio # functional.resample is pure torch, no torchcodec/DLL
            return torchaudio.functional.resample(torch.from_numpy(data), sr, 16000)
        except Exception:
            # linear-interp resample fallback so we never hard-depend on torchaudio
            n_out = max(1, int(round(len(data) * 16000 / sr)))
            idx = np.linspace(0, len(data) - 1, n_out)
            lo = np.floor(idx).astype(np.int64)
            hi = np.minimum(lo + 1, len(data) - 1)
            frac = (idx - lo).astype(np.float32)
            data = (data[lo] * (1 - frac) + data[hi] * frac).astype(np.float32)
    return torch.from_numpy(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio", help="path to mono 16kHz WAV (extract with ffmpeg first)")
    ap.add_argument("--min-silence-ms", type=int, default=300)
    ap.add_argument("--min-speech-ms", type=int, default=200)
    ap.add_argument("--pad-ms", type=int, default=200)
    ap.add_argument("--merge-gap-ms", type=int, default=0,
                    help="post-merge ranges this close together (default 0 = no merge)")
    ap.add_argument("--output", help="write JSON here (default: stdout)")
    args = ap.parse_args()

    # Lazy import - keeps --help working without silero_vad installed
    try:
        from silero_vad import load_silero_vad, get_speech_timestamps
    except ImportError:
        # AUTO-REPAIR (live customer report 2026-07-19, Windows): the venv can exist
        # with silero_vad missing when the installer's pip step died mid-download.
        # We ARE the venv's interpreter here - one quiet pip retry usually heals it.
        import subprocess
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                            "silero-vad", "soundfile", "numpy"],
                           timeout=600, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            from silero_vad import load_silero_vad, get_speech_timestamps  # noqa: F811
            print("note: silero_vad was missing - auto-installed into the VAD env",
                  file=sys.stderr)
        except Exception:
            if os.name == "nt":
                fix = ("เปิด START HERE\\Windows.bat อีกครั้ง (ตัวติดตั้งจะซ่อม VAD ให้) "
                       "หรือรัน: %USERPROFILE%\\.indigo11\\vad-env\\Scripts\\pip install silero-vad soundfile numpy")
            else:
                fix = "run: bash indigo11-clean-cut/scripts/install_vad.sh"
            print(f"error: silero_vad not installed and auto-repair failed. {fix}",
                  file=sys.stderr)
            sys.exit(2)

    model = load_silero_vad()
    wav = load_wav_16k(args.audio)
    # Ask for SAMPLES and convert here. `return_seconds=True` rounds every edge to
    # 0.1 s (Silero's default time_resolution=1): measured on a 14-minute clip that
    # reported a range as 1.0-3.8 s when the model found 0.960-3.776 s, i.e. cut
    # points off by up to 50 ms before any padding was applied.
    speech = [{"start": round(t["start"] / 16000, 3), "end": round(t["end"] / 16000, 3)}
              for t in get_speech_timestamps(
                  wav, model,
                  sampling_rate=16000,
                  min_silence_duration_ms=args.min_silence_ms,
                  min_speech_duration_ms=args.min_speech_ms,
                  speech_pad_ms=args.pad_ms,
              )]

    raw_count = len(speech)
    if args.merge_gap_ms > 0:
        speech = merge_close(speech, args.merge_gap_ms / 1000.0)
        print(f"merged: {raw_count} raw → {len(speech)} ranges", file=sys.stderr)

    out = json.dumps(speech, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"wrote {len(speech)} speech ranges to {args.output}", file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()
