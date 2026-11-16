#!/usr/bin/env python3
"""analyse_context.py - universal video context builder.

Takes 1+ local clips OR a URL and produces context.json - the base data layer
every other skill consumes. Replaces the old analyze-mode split.

This script handles DETERMINISTIC work only:
  - ffprobe (duration, dimensions, fps, channels)
  - frame extraction (sparse: scene boundaries + start/mid/end + silence boundaries)
  - scene-change detection (ffmpeg scene filter)
  - audio extract (mono 16k for transcribe + VAD)
  - call indigo11-transcribe for word-level text
  - call Silero VAD for speech_ranges → invert → silence_ranges
  - compute pacing statistics

The AGENT fills (after this script):
  - frames[].visual_description / has_speaker / on_screen_text
  - per_clip[].role_hint / quality_hint / summary / notable_moments
  - take_comparison[] (duplicate-phrase detection + ranking)
  - notes[]
  - video_metadata.overall_mood / pacing_speed
  - blueprint (when url input)

The script prints a `next_step` block telling the calling agent what to fill.

Usage:
  # Single clip
  python3 analyse_context.py --clips /path/to/clip.mp4

  # Multi clip
  python3 analyse_context.py --clips a.mov b.mov c.mov

  # URL (yt-dlp downloads, then treated as single-clip with blueprint mode)
  python3 analyse_context.py --url https://www.tiktok.com/@user/video/123

  # Force keep intermediates (frames + audio wav)
  python3 analyse_context.py --clips clip.mp4 --keep-intermediates

  # Output dir override
  python3 analyse_context.py --clips clip.mp4 --output-dir /path/out
"""

from __future__ import annotations
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path


def detect_repeat_candidates(words, gap_ms=700, min_chars=8, min_ratio=0.66, window=3):
    """Deterministic WITHIN-clip retake/false-start candidates (codex C5206 gap).

    Groups transcript words into utterances at >=gap_ms pauses, then compares each
    utterance's normalized text with the next `window` utterances (difflib ratio).
    High-similarity nearby pairs are exactly what a re-take/false-start looks like
    ("วันนี้ผมจะ... / วันนี้ผมจะมา..."). This emits CANDIDATES with timing + score;
    deciding which take to keep stays with the agent (rule #18).
    """
    import difflib
    utterances = []
    current = []
    for w in words:
        try:
            start = float(w.get("start_ms", w.get("start", 0) * 1000 if isinstance(w.get("start"), float) else w.get("start", 0)))
        except (TypeError, ValueError):
            continue
        if current and start - current[-1]["end"] >= gap_ms:
            utterances.append(current)
            current = []
        try:
            end = float(w.get("end_ms", w.get("end", 0) * 1000 if isinstance(w.get("end"), float) else w.get("end", 0)))
        except (TypeError, ValueError):
            end = start
        current.append({"text": str(w.get("text", "")), "start": start, "end": end})
    if current:
        utterances.append(current)
    norm = lambda u: re.sub(r"\s+", "", "".join(x["text"] for x in u))
    candidates = []
    for i, a in enumerate(utterances):
        ta = norm(a)
        if len(ta) < min_chars:
            continue
        for j in range(i + 1, min(i + 1 + window, len(utterances))):
            b = utterances[j]
            tb = norm(b)
            if len(tb) < min_chars:
                continue
            # prefix-similarity catches false starts (short A restarted as longer B)
            head = min(len(ta), len(tb))
            ratio = difflib.SequenceMatcher(None, ta[:head], tb[:head]).ratio()
            if ratio >= min_ratio:
                candidates.append({
                    "a_start_ms": int(a[0]["start"]), "a_end_ms": int(a[-1]["end"]),
                    "b_start_ms": int(b[0]["start"]), "b_end_ms": int(b[-1]["end"]),
                    "similarity": round(ratio, 3),
                    "text_a": "".join(x["text"] for x in a)[:120],
                    "text_b": "".join(x["text"] for x in b)[:120],
                })
    return candidates

from typing import Optional

if os.name == "nt":  # cp874 console crashes printing Thai (rule #32)
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")


def load_env_file() -> None:
    """Load .env from the nearest ancestor (kit root) so keys placed there (not just shell
    env) are visible. Real env wins (rule #17). Without this, a buyer who set keys in .env via
    ~/.indigo11/config.json would have analyze miss them: the Gemini-key check for --understand and the
    transcribe sub-call both read os.environ."""
    cwd = Path.cwd().resolve()
    for parent in [cwd] + list(cwd.parents):
        env_path = parent / ".env"
        if env_path.exists():
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k and k not in os.environ:
                        os.environ[k] = v
            except Exception:
                pass
            return


load_env_file()


# Interpreter to re-invoke for our own python sub-scripts (transcribe.py, detect_text.py,
# detect_motion.py). On Windows a bare "python3" is the Microsoft Store stub and errors out
# ("Python was not found"); sys.executable is the real interpreter currently running. Fall
# back to "python3" only if sys.executable is somehow empty (frozen/embedded edge case).
PYTHON = sys.executable or "python3"


SCHEMA_VERSION = "4" # v4 adds frame_grids[] (local, free - the agent SEES the footage)


def load_config() -> dict:
    """Read ~/.indigo11/config.json (~/.indigo11/config.json). {} on error so absent config = old behaviour."""
    try:
        p = Path.home() / ".indigo11" / "config.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        return {}


def understand_enabled_in_config() -> bool:
    """True when ~/.indigo11/config.json turned on video understanding AND chose a usable provider.

    The UI writes providers.video_understand.chain (e.g. ["gemini"]) + an explicit
    enable flag. We require BOTH an enable and a Gemini key, so a buyer who never set
    it up gets the exact old behaviour (no cloud call, no cost).
    """
    cfg = load_config()
    vu = (cfg.get("providers") or {}).get("video_understand") or {}
    if not vu.get("enabled"):
        return False
    chain = vu.get("chain") or []
    return "gemini" in chain and bool(os.environ.get("GEMINI_API_KEY", "").strip())


def find_kit_root(start: Path) -> Path:
    p = start.resolve()
    while p != p.parent:
        cm = p / "CLAUDE.md"
        if cm.exists():
            try:
                if any(m in cm.read_text(encoding="utf-8") for m in ("indigo11-edit-kit", "Indigo11 pack")):
                    return p
            except Exception:
                pass
        p = p.parent
    for q in Path(__file__).resolve().parents:   # cwd outside the starter: use where this script lives
        if (q / "_system" / "engine" / "_env.py").exists():
            return q
    return Path.cwd()


def kit_skill(kit_root: Path, name: str) -> Path:
    if name in ("indigo11-clean-cut", "indigo11-analyze", "indigo11-edit"):
        return Path(__file__).resolve().parent.parent   # merged skill: all scripts live in indigo11-edit/scripts
    # <starter>/skills is the real source; .claude/.agents/.agent .claude/skills/<name> only link to it
    # (a Windows unzip can turn those links into tiny text files, so require is_dir()).
    for base in (kit_root / ".claude" / "skills",
                 Path(__file__).resolve().parents[2],
                 kit_root / ".agents" / "skills",
                 kit_root / ".claude" / "skills"):
        cand = base / name
        if cand.is_dir():
            return cand
    return kit_root / ".claude" / "skills" / name


def ffprobe(path: str) -> dict:
    """Return duration_s, fps, width, height, audio_channels."""
    res = subprocess.run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", path
    ], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {res.stderr}")
    d = json.loads(res.stdout)
    fmt = d.get("format", {})
    streams = d.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = next((s for s in streams if s.get("codec_type") == "audio"), {})

    fps_str = v.get("r_frame_rate", "30/1")
    try:
        num, den = map(int, fps_str.split("/"))
        fps = round(num / den) if den else 30
    except Exception:
        fps = 30

    return {
        "duration_s": float(fmt.get("duration", 0)),
        "fps": fps,
        "width": int(v.get("width", 0)),
        "height": int(v.get("height", 0)),
        "audio_channels": int(a.get("channels", 0)),
    }


def aspect_ratio(w: int, h: int) -> str:
    if not w or not h:
        return "unknown"
    if h > w * 1.5:
        return "9:16"
    if w > h * 1.5:
        return "16:9"
    if abs(w - h) < max(w, h) * 0.1:
        return "1:1"
    if abs(w * 5 - h * 4) < max(w, h) * 0.1:
        return "4:5"
    return f"{w}x{h}"


def detect_scene_changes(clip_path: str, threshold: float = 0.15) -> list:
    """Return list of t_ms where ffmpeg detects scene changes (excluding start/end)."""
    cmd = [
        "ffmpeg", "-i", clip_path, "-filter:v",
        f"select='gt(scene,{threshold})',showinfo",
        "-f", "null", "-"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    times = []
    for line in res.stderr.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            t_s = float(m.group(1))
            times.append(int(t_s * 1000))
    return sorted(set(times))


def extract_audio(clip_path: str, out_wav: Path) -> bool:
    """Extract first audio track only. Returns True on success."""
    cmd = ["ffmpeg", "-i", clip_path, "-map", "0:a:0", "-vn",
           "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-y", str(out_wav)]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return res.returncode == 0


def detect_silence_vad(audio_wav: Path, media_duration_ms: int = 0) -> tuple:
    """Run Silero VAD. Returns (silence_ranges, status).

    status = {"status": "ok|unavailable|error", "error": <str|None>}. Critically,
    a FAILURE returns status != "ok" instead of an empty list that looks like
    "no silence" - otherwise a broken VAD env makes pacing read as 100% speech and
    downstream cuts blindly (P0-06). media_duration_ms (from ffprobe) is used as
    the trailing-silence bound, NOT the end of the last speech range.
    """
    # venv interpreter layout differs per OS: Windows = Scripts\python.exe, POSIX = bin/python3
    vad_dir = Path.home() / ".indigo11" / "vad-env"
    vad_python = (vad_dir / "Scripts" / "python.exe") if os.name == "nt" else (vad_dir / "bin" / "python3")
    if not vad_python.exists():
        return [], {"status": "unavailable",
                    "error": f"VAD env not installed at {vad_dir} - run install_vad.sh"}
    kit_root = find_kit_root(Path.cwd())
    vad_script = kit_skill(kit_root, "indigo11-clean-cut") / "scripts" / "vad_detect.py"
    if not vad_script.exists():
        return [], {"status": "unavailable", "error": "vad_detect.py not found"}
    fd, tmp_name = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    tmp_speech = Path(tmp_name)
    res = subprocess.run([
        str(vad_python), str(vad_script), str(audio_wav),
        "--min-silence-ms", "200", "--pad-ms", "0",
        "--output", str(tmp_speech),
    ], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        tmp_speech.unlink(missing_ok=True)
        return [], {"status": "error", "error": (res.stderr or res.stdout or "VAD failed")[-300:]}
    try:
        speech = json.loads(tmp_speech.read_text(encoding="utf-8"))
    except Exception as e:
        return [], {"status": "error", "error": f"VAD output not parseable: {e}"}
    finally:
        tmp_speech.unlink(missing_ok=True)

    # vad_detect.py returns a list of {start, end} in SECONDS (float). Normalize to ms.
    def to_ms_ranges(raw):
        result = []
        for r in raw:
            if "start_ms" in r:
                result.append({"start_ms": int(r["start_ms"]), "end_ms": int(r["end_ms"])})
            elif "start" in r:
                result.append({"start_ms": int(r["start"] * 1000), "end_ms": int(r["end"] * 1000)})
        return result

    if isinstance(speech, list):
        speech_ranges = to_ms_ranges(speech)
        total_ms = max((r["end_ms"] for r in speech_ranges), default=0)
    else:
        raw = speech.get("speech", []) or speech.get("ranges", [])
        speech_ranges = to_ms_ranges(raw)
        total_ms = int((speech.get("duration_ms") or speech.get("duration_s", 0) * 1000) or 0)
        if not total_ms and speech_ranges:
            total_ms = max(r["end_ms"] for r in speech_ranges)

    # Use the real MEDIA duration for the trailing bound - end-of-speech misses a
    # long silent tail (P0-06). Fall back to end-of-speech only if unknown.
    if media_duration_ms and media_duration_ms > total_ms:
        total_ms = media_duration_ms

    # Invert: silence = gaps between speech ranges
    silences = []
    cursor = 0
    for r in sorted(speech_ranges, key=lambda x: x["start_ms"]):
        if r["start_ms"] - cursor > 200:
            silences.append({"start_ms": cursor, "end_ms": r["start_ms"]})
        cursor = r["end_ms"]
    if total_ms > cursor + 200:
        silences.append({"start_ms": cursor, "end_ms": total_ms})
    return silences, {"status": "ok", "error": None}


def extract_frames(clip_path: str, frame_times_ms: list, out_dir: Path, width: int = 512) -> list:
    """Extract one frame per timestamp. Returns list of {t_ms, path}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for i, t_ms in enumerate(frame_times_ms):
        out = out_dir / f"frame-{i:04d}-t{t_ms}.jpg"
        cmd = [
            "ffmpeg", "-ss", str(t_ms / 1000.0), "-i", clip_path,
            "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "5", "-y", str(out)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if res.returncode == 0 and out.exists():
            frames.append({"t_ms": t_ms, "path": str(out)})
    return frames



def build_frame_grids(clips: list, out_dir: Path, cells: int, max_grids: int):
    """Tile each clip's frames into grid PNGs the agent can Read.

    Returns (grids[], error_or_None). Delegates to frame_grid.py (vendored, stdlib +
    ffmpeg only). One clip that fails does not stop the rest - the caller still gets
    whatever succeeded, because grids are an aid, never a hard dependency.
    """
    script = Path(__file__).parent / "frame_grid.py"
    if not script.exists():
        return [], "frame_grid.py missing"
    out_dir.mkdir(parents=True, exist_ok=True)
    grids, errors = [], []
    for clip in clips:
        name = Path(clip).stem
        cdir = out_dir / name
        cmd = [PYTHON, str(script), str(clip), "--out-dir", str(cdir),
               "--cells", str(cells)]
        if max_grids:
            cmd += ["--max-grids", str(max_grids)]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        man = cdir / "manifest.json"
        if r.returncode != 0 or not man.exists():
            errors.append(f"{name}: {(r.stderr or '').strip()[:200]}")
            continue
        try:
            data = json.loads(man.read_text(encoding="utf-8"))
        except Exception as e:
            errors.append(f"{name}: unreadable manifest ({e})")
            continue
        for g in data.get("grids", []):
            grids.append({
                "clip": name,
                "path": g.get("path"),
                "cols": g.get("cols"),
                "rows": g.get("rows"),
                # cells[] carries each tile's timestamp, in reading order
                # (left->right, top->bottom) so the agent can name a moment.
                "cells": g.get("cells", []),
            })
    return grids, ("; ".join(errors) if errors else None)


def pick_frame_times(duration_ms: int, scene_changes_ms: list, silence_ranges: list, blueprint_mode: bool) -> list:
    """Pick sparse but representative frame timestamps.

    Strategy:
    - Always: start, midpoint, near-end
    - Scene change boundaries (one frame just after each cut)
    - Long silences are sampled THROUGHOUT, not just at their start (see below)
    - For blueprint_mode (viral analysis), pack denser: ~1 frame/sec up to 30 total

    Why long silences get several frames: a silent stretch in a product clip is usually
    the DEMO - the seller stops talking and shows the thing working. Sampling only
    `start_ms` catches the moment before the demo begins and nothing of the demo itself,
    because a held shot has no scene change to trigger another frame. Measured on a Thai
    affiliate clip: a 12s silence covering the actual shaving demo produced exactly ONE
    frame, taken before the razor touched skin. Sampling every ~2s inside the gap fixes it.
    """
    times = set()
    times.add(min(500, duration_ms // 4)) # start (small offset to avoid black frame)
    times.add(duration_ms // 2)
    times.add(max(0, duration_ms - 500))

    for t in scene_changes_ms:
        times.add(t + 200) # 200ms after cut

    for r in silence_ranges:
        gap = r["end_ms"] - r["start_ms"]
        if gap >= 1000:
            times.add(r["start_ms"])
            # Walk the inside of the gap so a held demo shot is actually seen.
            step = 2000
            t = r["start_ms"] + step
            while t < r["end_ms"] - 300:
                times.add(t)
                t += step
            if gap >= 3000:
                times.add(max(r["start_ms"], r["end_ms"] - 300))

    if blueprint_mode:
        # Dense sampling for viral analyze - 1 fps up to 30 frames
        step = max(1000, duration_ms // 30)
        for t in range(0, duration_ms, step):
            times.add(t)

    return sorted(t for t in times if 0 <= t < duration_ms)


def transcribe_clip(kit_root: Path, clip_path: str, lang: str, out_json: Path) -> dict:
    """Run indigo11-transcribe. Falls back to track-0 extract on amix failure."""
    script = kit_skill(kit_root, "indigo11-transcribe") / "scripts" / "transcribe.py"
    res = subprocess.run([
        PYTHON, str(script), clip_path,
        "--provider", "auto", "--lang", lang,
        "--output", str(out_json),
    ], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode == 0 and out_json.exists():
        return json.loads(out_json.read_text(encoding="utf-8"))

    # Fallback: extract track-0 wav and retry
    _fd, _tmp = tempfile.mkstemp(suffix=".wav"); os.close(_fd)
    tmp_wav = Path(_tmp)
    if not extract_audio(clip_path, tmp_wav):
        raise RuntimeError(f"audio extract failed for {clip_path}")
    res2 = subprocess.run([
        PYTHON, str(script), str(tmp_wav),
        "--provider", "auto", "--lang", lang,
        "--output", str(out_json),
    ], capture_output=True, text=True, encoding="utf-8", errors="replace")
    tmp_wav.unlink(missing_ok=True)
    if res2.returncode == 0 and out_json.exists():
        return json.loads(out_json.read_text(encoding="utf-8"))
    raise RuntimeError(f"transcribe failed for {clip_path}: {res2.stderr[-500:]}")


def download_url(url: str, dest_dir: Path, audio_only: bool = False) -> str:
    """Download a video URL with yt-dlp. Returns local video path."""
    if not shutil.which("yt-dlp"):
        hint = "winget install yt-dlp (or: pip install -U yt-dlp)" if os.name == "nt" \
            else "brew install yt-dlp"
        raise RuntimeError(f"yt-dlp not installed. {hint}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(dest_dir / "source.%(ext)s")
    if audio_only:
        cmd = [
            "yt-dlp", url, "-o", out_template,
            "-f", "bestaudio",
            "-x", "--audio-format", "m4a",
            "--no-warnings",
        ]
    else:
        cmd = [
            "yt-dlp", url, "-o", out_template,
            # video+audio pair first - a lone best[ext=mp4] is often video-only (no audio)
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format", "mp4",
            "--no-warnings",
        ]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        raise RuntimeError(
            f"yt-dlp failed: {res.stderr[-400:]}\n"
            f"Tip: YouTube/TikTok change formats often - try 'pip3 install -U yt-dlp' first.")
    # Find the downloaded file
    exts = (".m4a", ".mp3", ".wav", ".aac", ".opus", ".webm") if audio_only else (".mp4", ".webm", ".mkv", ".mov")
    for f in dest_dir.iterdir():
        if f.is_file() and f.suffix.lower() in exts:
            return str(f)
    raise RuntimeError("yt-dlp completed but no media file found")


def detect_text_events(clip_path: str, fps: float = 3.0) -> dict:
    """Optional local OCR pass (macOS Vision). Returns {} if unavailable - never fatal.

    Closes the sparse-frame blind spot for burned-in text that flashes <1s between
    sampled frames. Runs entirely on-device (no cloud, no key). See detect_text.py.
    """
    script = Path(__file__).resolve().parent / "detect_text.py"
    res = subprocess.run([PYTHON, str(script), clip_path, "--fps", str(fps)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        return {}
    try:
        d = json.loads(res.stdout)
    except Exception:
        return {}
    return d if d.get("ok") else {"_skipped": d.get("reason"), "_install": d.get("install")}


def detect_motion_timeline(clip_path: str) -> dict:
    """Optional motion/freeze pass (ffmpeg scdet - zero new deps). Never fatal.

    Surfaces within-shot motion beats + frozen spans that sparse stills miss.
    See detect_motion.py.
    """
    script = Path(__file__).resolve().parent / "detect_motion.py"
    res = subprocess.run([PYTHON, str(script), clip_path],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        return {}
    try:
        d = json.loads(res.stdout)
    except Exception:
        return {}
    return d if d.get("ok") else {}


def process_clip(kit_root: Path, clip_path: str, frames_dir: Path,
                 transcripts_dir: Path, lang: str, blueprint_mode: bool,
                 want_text: bool = False, want_motion: bool = False,
                 fast_mode: bool = False) -> dict:
    """Run all deterministic prep for one clip. Returns per-clip data."""
    name = Path(clip_path).stem
    probe = ffprobe(clip_path)
    duration_ms = int(probe["duration_s"] * 1000)

    scene_changes_ms = [] if fast_mode else detect_scene_changes(clip_path)

    # Audio + VAD
    audio_wav = transcripts_dir / f"{name}.wav"
    silences = []
    vad_status = {"status": "unavailable", "error": "audio extraction failed"}
    if extract_audio(clip_path, audio_wav):
        silences, vad_status = detect_silence_vad(audio_wav, media_duration_ms=duration_ms)

    # Transcribe
    transcript_json = transcripts_dir / f"{name}.json"
    transcript = transcribe_clip(kit_root, clip_path, lang, transcript_json)

    # Frames
    frame_times = [] if fast_mode else pick_frame_times(duration_ms, scene_changes_ms, silences, blueprint_mode)
    frames = [] if fast_mode else extract_frames(clip_path, frame_times, frames_dir / name)

    # Optional local enrichment passes (off by default - see flags)
    text_pass = detect_text_events(clip_path) if want_text else None
    motion_pass = detect_motion_timeline(clip_path) if want_motion else None

    # Words → context shape
    words = [
        {"clip": name, "text": w["text"], "start_ms": int(w["start"] * 1000), "end_ms": int(w["end"] * 1000)}
        for w in transcript.get("words", [])
    ]

    return {
        "name": name,
        "path": clip_path,
        "probe": probe,
        "duration_ms": duration_ms,
        "scene_changes_ms": [{"clip": name, "t_ms": t, "kind": "hard-cut"} for t in scene_changes_ms],
        "silences": [{"clip": name, "start_ms": s["start_ms"], "end_ms": s["end_ms"]} for s in silences],
        "frames": [
            {"clip": name, "t_ms": f["t_ms"], "path": f["path"], "scene_id": 0,
             "has_speaker": None, "visual_description": None, "on_screen_text": None}
            for f in frames
        ],
        "words": words,
        "text": transcript.get("text", ""),
        "text_events": (text_pass or {}).get("text_events", []) if text_pass else [],
        "text_pass_meta": text_pass if (text_pass and "_skipped" in text_pass) else None,
        "motion": motion_pass or None,
        "vad_status": vad_status,
    }


def assign_scene_ids(frames: list, scene_changes: list) -> None:
    """Cluster frames by scene_id based on scene_changes_ms boundaries (per clip)."""
    by_clip = {}
    for sc in scene_changes:
        by_clip.setdefault(sc["clip"], []).append(sc["t_ms"])
    for f in frames:
        boundaries = sorted(by_clip.get(f["clip"], []))
        sid = sum(1 for b in boundaries if f["t_ms"] >= b)
        f["scene_id"] = sid


def compute_pacing(silences: list, scene_changes: list, total_ms: int) -> dict:
    if not total_ms:
        return {}
    total_silence = sum(s["end_ms"] - s["start_ms"] for s in silences)
    longest_silence = max((s["end_ms"] - s["start_ms"] for s in silences), default=0)
    silence_pct = round(total_silence / total_ms * 100, 1)
    speech_pct = round(100 - silence_pct, 1)
    return {
        "speech_pct": speech_pct,
        "silence_pct": silence_pct,
        "scene_change_count": len(scene_changes),
        "cuts_per_minute_estimate": round(len(scene_changes) / (total_ms / 60_000), 1) if total_ms else 0,
        "longest_silence_ms": longest_silence,
    }


def run_understanding(kit_root: Path, clips: list, fps: Optional[float]) -> list:
    """Gemini watches each clip. Returns video_understanding[] for context.json.

    Reuses indigo11-analyze/scripts/video_understand.py (the tested Files-API client). One
    short editorial prompt per clip. A clip that fails (timeout, bad key) records its
    error rather than aborting the whole analyze run.
    """
    vu_script = Path(__file__).resolve().parent / "video_understand.py"
    prompt = (
        "ดูวิดีโอนี้แล้วสรุปสั้นๆ เป็นภาษาไทย เพื่อช่วยตัดต่อ short-form: "
        "(1) คนในคลิปทำอะไร/พูดเรื่องอะไร (2) มี action หรือการโชว์ของช่วงไหน (บอกวินาทีคร่าวๆ) "
        "(3) มีข้อความบนจอไหม (4) เป็นคลิปแนวไหน hook/รีวิว/สอน/cta (5) ช่วงที่น่าตัดทิ้ง/น่าเก็บ. "
        "ตอบกระชับเป็น bullet."
    )

    def one(clip):
        try:
            cmd = [sys.executable, str(vu_script), "--video", clip, "--prompt", prompt]
            if fps is not None:
                cmd += ["--fps", str(fps)]
            r = subprocess.run(
                cmd,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
            )
            out = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
            if out.get("ok"):
                return {"clip": clip, "summary": out.get("text"), "model": out.get("model"),
                        "tokens": out.get("tokens"), "seconds": out.get("seconds")}
            return {"clip": clip, "summary": None, "error": out.get("error") or (r.stderr or "")[-300:]}
        except Exception as e:
            return {"clip": clip, "summary": None, "error": str(e)}

    fps_label = fps if fps is not None else "auto"
    print(f"[analyze] Gemini watching {len(clips)} clip(s) (fps={fps_label})...", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=min(4, len(clips))) as ex:
        return list(ex.map(one, clips))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", nargs="+", help="One or more local video paths")
    ap.add_argument("--url", help="Download a URL via yt-dlp and analyze it (single clip, blueprint mode)")
    ap.add_argument("--lang", default="th", help="Transcription language (default th)")
    ap.add_argument("--output-dir", help="Override output dir")
    ap.add_argument("--keep-intermediates", action="store_true", help="Keep frames + audio after run")
    ap.add_argument("--blueprint", action="store_true", help="Force blueprint mode (viral-style analysis stub)")
    ap.add_argument("--detect-text", action="store_true",
                    help="Local on-screen-text OCR pass (macOS Vision, no cloud). Catches "
                         "burned-in captions/prices that flash between sampled frames.")
    ap.add_argument("--detect-motion", action="store_true",
                    help="Local motion/freeze timeline (ffmpeg scdet, zero new deps). Surfaces "
                         "within-shot gesture/reveal beats + frozen B-roll.")
    ap.add_argument("--no-frame-grid", action="store_true",
                    help="Skip packing frames into grid images. Grids are LOCAL and FREE "
                         "(ffmpeg tile, no API key) and are how the agent sees the footage - "
                         "only skip when you truly need audio/transcript signal alone.")
    ap.add_argument("--grid-cells", type=int, default=36,
                    help="Frames per grid image (default 36 ~261px/cell - good for reading "
                         "an object in hand or a gesture). Use 16 for small on-screen text, "
                         "64 for a coarse plot skim.")
    ap.add_argument("--grid-max", type=int, default=0,
                    help="Cap grid images per clip (0 = no cap). Frames are thinned evenly "
                         "across the whole clip, never truncated at the tail.")
    ap.add_argument("--fast", action="store_true",
                    help="Fast profile: skip scene detection and frame extraction; keep STT, VAD, pacing.")
    ap.add_argument("--understand", action="store_true",
                    help="Gemini WATCHES each clip (cloud, needs GEMINI_API_KEY). Adds "
                         "video_understanding[] to context.json: a real read of on-screen action, "
                         "not just stills. Auto-on when ~/.indigo11/config.json enables it; this flag forces it.")
    ap.add_argument("--no-understand", action="store_true",
                    help="Force OFF even if ~/.indigo11/config.json enables video understanding.")
    ap.add_argument("--understand-fps", type=float, default=None,
                    help="Frame sampling for the Gemini watch pass. Default auto (long clips lower fps).")
    ap.add_argument("--template", action="store_true",
                    help="After building context.json, run extract_template.py: Gemini returns a "
                         "STRUCTURED edit template (overlays/camera/SFX/caption style) fused with "
                         "the exact signals -> edit-template.json. Cloud, needs GEMINI_API_KEY. "
                         "Single reference video only.")
    ap.add_argument("--kit-root", help="Override kit root")
    ap.add_argument("positional_clips", nargs="*", help=argparse.SUPPRESS)
    args = ap.parse_args()

    # video understanding: explicit --understand wins; else ~/.indigo11/config.json config; --no-understand vetoes.
    do_understand = (args.understand or understand_enabled_in_config()) and not args.no_understand

    if args.positional_clips:
        args.clips = (args.clips or []) + args.positional_clips

    if not args.clips and not args.url:
        print(json.dumps({"ok": False, "error": "Need --clips or --url"}), file=sys.stderr)
        sys.exit(1)

    kit_root = Path(args.kit_root).expanduser() if args.kit_root else find_kit_root(Path.cwd())

    # Resolve input
    blueprint_mode = bool(args.url) or args.blueprint
    if args.url:
        input_kind = "url"
        url_dest = kit_root / "projects" / "_work" / "analyze" / "_url_downloads" / re.sub(r"\W+", "_", args.url)[:40]
        clip_path = download_url(args.url, url_dest, audio_only=args.fast)
        clips = [clip_path]
        understand_inputs = [args.url]
    else:
        clips = [str(Path(c).expanduser().resolve()) for c in args.clips]
        for c in clips:
            if not Path(c).exists():
                print(json.dumps({"ok": False, "error": f"clip not found: {c}"}), file=sys.stderr)
                sys.exit(1)
        input_kind = "multi-clip" if len(clips) > 1 else "single-clip"
        understand_inputs = clips

    # Output dir
    if args.output_dir:
        out_dir = Path(args.output_dir).expanduser()
    else:
        if input_kind == "url":
            basename = re.sub(r"\W+", "_", args.url)[:40]
        else:
            basename = Path(clips[0]).stem if len(clips) == 1 else f"batch-{len(clips)}clips"
        out_dir = kit_root / "projects" / "_work" / "analyze" / basename
    out_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = out_dir / "frames"
    transcripts_dir = out_dir / "transcripts"
    transcripts_dir.mkdir(exist_ok=True)

    # Process clips in parallel
    print(f"[analyze] processing {len(clips)} clip(s)...", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=min(8, len(clips))) as ex:
        results = list(ex.map(
            lambda c: process_clip(kit_root, c, frames_dir, transcripts_dir, args.lang,
                                   blueprint_mode, args.detect_text, args.detect_motion, args.fast),
            clips
        ))

    # Frame grids - pack the extracted frames into a few readable images (rule #29).
    # Free and local: ffmpeg tiles frames; the agent Reads the grids instead of every
    # frame, which is ~7x fewer image reads for the same coverage. No key, no network.
    frame_grids = []
    grid_error = None
    if not args.fast and not args.no_frame_grid:
        try:
            frame_grids, grid_error = build_frame_grids(
                clips, out_dir / "frame-grids", args.grid_cells, args.grid_max)
        except Exception as e:  # never let a visual aid break the analysis
            grid_error = f"{type(e).__name__}: {e}"

    # Aggregate
    all_words = []
    all_silences = []
    all_scenes = []
    all_frames = []
    all_text_events = []
    all_motion = []
    text_skipped = None
    per_clip = []
    total_duration_s = 0
    aspect = "unknown"
    vad_statuses = []

    for r in results:
        all_words.extend(r["words"])
        all_silences.extend(r["silences"])
        all_scenes.extend(r["scene_changes_ms"])
        all_frames.extend(r["frames"])
        total_duration_s += r["probe"]["duration_s"]
        vs = r.get("vad_status") or {"status": "unknown", "error": None}
        vad_statuses.append({"clip": r["name"], **vs})
        if aspect == "unknown":
            aspect = aspect_ratio(r["probe"]["width"], r["probe"]["height"])

        for ev in r.get("text_events", []):
            all_text_events.append({"clip": r["name"], **ev})
        if r.get("text_pass_meta"):
            text_skipped = r["text_pass_meta"] # remember why OCR was skipped, to surface
        if r.get("motion"):
            all_motion.append({"clip": r["name"], **r["motion"]})

        per_clip.append({
            "name": r["name"],
            "path": r["path"],
            "duration_s": r["probe"]["duration_s"],
            "role_hint": None, # agent fills
            "quality_hint": None, # agent fills
            "speaker_visible": None, # agent fills
            "has_on_screen_text": None, # agent fills
            "notable_moments_ms": [], # agent fills
            "summary": None, # agent fills
        })

    # Scene ids on frames
    assign_scene_ids(all_frames, all_scenes)

    # Video understanding (Gemini watches each clip) - cloud, opt-in via --understand or ~/.indigo11/config.json.
    video_understanding = run_understanding(kit_root, understand_inputs, args.understand_fps) if do_understand else []

    # Pacing
    pacing = compute_pacing(all_silences, all_scenes, int(total_duration_s * 1000))

    # Get probe info for metadata
    first = results[0]["probe"]
    video_metadata = {
        "total_duration_s": round(total_duration_s, 2),
        "primary_language": args.lang,
        "fps": first["fps"],
        "aspect_ratio": aspect,
        "width": first["width"],
        "height": first["height"],
        "audio_channels": first["audio_channels"],
        "overall_mood": None, # agent fills
        "pacing_speed": None, # agent fills
    }

    context = {
        "_schema_version": SCHEMA_VERSION,
        "analyzed_at": datetime.now().isoformat(),
        "input_kind": input_kind,
        "kit_root": str(kit_root),
        "video_metadata": video_metadata,
        "transcript_words": all_words,
        # deterministic retake/false-start CANDIDATES (within-clip; single-clip incl.)
        # - the agent judges these, it must not re-derive them from raw text
        "repeat_candidates": detect_repeat_candidates(all_words),
        "silence_ranges_ms": all_silences,
        # vad[]: per-clip VAD run status {clip, status: ok|unavailable|error, error}.
        # status != "ok" means silence_ranges for that clip is UNRELIABLE (empty
        # because VAD failed, not because there was no silence) - downstream rough/
        # silent cut must treat it as degraded, not "100% speech" (P0-06).
        "vad": vad_statuses,
        "scene_changes_ms": all_scenes,
        "frames": all_frames,
        # frame_grids[]: the SAME frames tiled into a few PNGs - Read these, not frames[].
        # Each entry {clip, path, cols, rows, cells[{index,t_ms,t_label}]} in reading
        # order. This is how the agent SEES the footage without a cloud call: what is in
        # the speaker's hand, what a silent stretch is demonstrating, whether a caption
        # matches the screen. Empty when --fast or --no-frame-grid.
        "frame_grids": frame_grids,
        "frame_grids_error": grid_error,
        # Local enrichment passes (present only when --detect-text / --detect-motion).
        # text_events[]: burned-in on-screen text the sparse frames would miss
        # {clip, text, t_start_ms, t_end_ms, confidence} - macOS Vision, on-device.
        # motion[]: per-clip {motion_score[], motion_peaks_ms[], freeze_spans_ms[]} - ffmpeg.
        "text_events": all_text_events,
        "text_events_skipped": text_skipped, # null, or {_skipped: <why>, _install: <command>} if OCR unavailable
        "motion": all_motion,
        # video_understanding[]: Gemini's read of the MOVING video (rule #29) - present only when
        # --understand / ~/.indigo11/config.json enabled it. Per clip: {clip, summary, model, tokens, seconds}
        # or {clip, summary: null, error}. Empty [] = the pass didn't run (default, no cost).
        "video_understanding": video_understanding,
        "per_clip": per_clip if input_kind == "multi-clip" else [],
        "take_comparison": [], # agent fills if retakes detected
        "pacing": pacing,
        "blueprint": None, # agent fills when blueprint_mode
        "notes": [],
    }
    if args.fast:
        context["notes"].append("fast_mode: skipped scene detection and frame extraction")
    degraded = [v for v in vad_statuses if v.get("status") not in ("ok",)]
    if degraded:
        context["notes"].append(
            f"VAD DEGRADED on {len(degraded)}/{len(vad_statuses)} clip(s): "
            f"{degraded[0].get('error')}. silence_ranges may be empty/unreliable - "
            f"do NOT treat as 100% speech; install ~/.indigo11/vad-env or re-run.")

    context_path = out_dir / "context.json"
    context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")

    # Optional: structured edit-template pass (Gemini + signal fusion). One reference video only.
    template_path = None
    template_error = None
    if args.template:
        if len(clips) > 1:
            template_error = "template extraction needs a single reference video (got multi-clip)"
        else:
            # YouTube URLs go to Gemini directly (no upload, free in preview); local files
            # otherwise. --fast + non-YouTube URL downloaded audio-only -> nothing to watch.
            if args.url and ("youtube.com" in args.url or "youtu.be" in args.url):
                ref = args.url
            elif args.fast and args.url:
                template_error = "--template needs video; rerun without --fast (audio-only download)"
                ref = None
            else:
                ref = clips[0]
            if ref:
                tmpl_cmd = [sys.executable, str(Path(__file__).parent / "extract_template.py"),
                            "--video", ref, "--context", str(context_path),
                            "--out", str(out_dir / "edit-template.json")]
                print("[analyze] extracting edit template (Gemini structured)...", file=sys.stderr)
                tr = subprocess.run(tmpl_cmd, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=900)
                try:
                    tout = json.loads(tr.stdout.strip().splitlines()[-1]) if tr.stdout.strip() else {}
                except json.JSONDecodeError:
                    tout = {}
                if tout.get("ok"):
                    template_path = tout["output"]
                else:
                    template_error = tout.get("error") or (tr.stderr or "").strip()[-300:] or "unknown"

    # Identify which fields the agent needs to fill
    pending = {
        "frames_to_describe": len(all_frames),
        "per_clip_hints": len(per_clip) if input_kind == "multi-clip" else 0,
        "video_metadata_judgment": ["overall_mood", "pacing_speed"],
        "take_comparison": ("scan transcript_words for duplicate phrases" if input_kind == "multi-clip" else "judge repeat_candidates[] (within-clip retakes/false-starts) - do NOT report ไม่มี false-start unless repeat_candidates is empty"),
        "blueprint": "fill hook_analysis + timeline_events + technical_takeaways" if blueprint_mode else "n/a (raw-clip mode)",
        "notes": "one line per low-confidence call",
    }

    cleanup = []
    if not args.keep_intermediates:
        cleanup = ["frames/ and transcripts/*.wav will be removed after agent fills context.json"]

    print(json.dumps({
        "ok": True,
        "op": "analyze-context",
        "input_kind": input_kind,
        "context_path": str(context_path),
        "clips_processed": len(clips),
        "frame_count": len(all_frames),
        "scene_change_count": len(all_scenes),
        "silence_range_count": len(all_silences),
        "total_duration_s": total_duration_s,
        "pending_agent_judgment": pending,
        "cleanup_hint": cleanup,
        "template_path": template_path,
        "template_error": template_error,
        "next_step": (
            "Agent: read context.json. Read frames in batches. Fill: video_metadata.overall_mood / "
            "pacing_speed, per frame's visual_description / has_speaker / on_screen_text, "
            "per_clip[].role_hint / quality_hint / summary, take_comparison[] if retakes found, "
            "notes[]. Then save context.json back."
            + (" Also fill blueprint{} for viral analysis." if blueprint_mode else "")
        ),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
