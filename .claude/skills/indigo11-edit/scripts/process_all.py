#!/usr/bin/env python3
"""Orchestrator: read media → analyze → compute EDL → write to /tmp.

Does NOT inject. Agent calls inject_edl.py + add_captions.py after this,
reading the EDL files this writes.

Reason: this script is the "deterministic computation" - analyze + intersect
math. Inject is a separate concern with its own kill_capcut + mirror.

Pipeline:
  1. read_media → manifest (filter ghosts)
  2. extract audio per clip
  3. transcribe per clip (via indigo11-transcribe/transcribe.py)
  4. VAD per clip (via indigo11-clean-cut/vad_detect.py)
  5. agent decides: assembly order (interactive - read transcripts, prompt for ORDER)
                   rough cut keep_word_ranges (interactive - read transcripts)
  6. script intersects rough × VAD → final-edl.json
  7. (optional) word-segment + post-process for captions → words.json

Usage:
  python3 process_all.py --project my-promo \
    --do-assembly --do-rough --do-silent \
    [--do-captions --words-per-group 2]

  This script writes EDL files only; agent runs subagents for:
  - Assembly order decision (read transcripts, decide order)
  - Rough-cut decisions (read transcripts, decide keep_word_ranges)
  - Caption post-process pass 2 (read words.json, fix boundary)

  Then agent runs inject_edl.py + add_captions.py.

  Pure-deterministic mode (skip AI decisions - keep all clips full, no rough):
  python3 process_all.py --project my-promo --do-silent # silent only, no rough/assembly choice
"""
from __future__ import annotations
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _capcut_lib import ( # noqa: E402
    CAPCUT_DRAFTS_DIR, MS, S, emit_err, emit_ok, kill_capcut, read_media_panel,
    resolve_project,
)


def find_skill(skill_name: str, script: str) -> Path:
    """Locate a sibling skill script (global or project-local install)."""
    here = Path(__file__).resolve().parents[4]  # <starter>/.claude/skills/indigo11-capcut/scripts/ -> <starter>
    candidates = [
        Path(__file__).resolve().parent / script,
        here / ".claude" / "skills" / skill_name / "scripts" / script,
        here / ".agents" / "skills" / skill_name / "scripts" / script,
        here / ".claude" / "skills" / skill_name / "scripts" / script,
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(f"{skill_name}/{script} not found")


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def extract_audio(video: str, out_wav: str) -> bool:
    if os.path.exists(out_wav):
        return True
    r = run(["ffmpeg", "-y", "-i", video, "-ac", "1", "-ar", "16000", "-vn", out_wav])
    return r.returncode == 0


def _clip_fingerprint(path: str) -> str:
    """Short stable id from the source file's path+size+mtime, for cache keying."""
    try:
        st = os.stat(path)
        raw = f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        raw = os.path.abspath(path)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _json_ok(p) -> bool:
    """True if p is a non-empty, parseable JSON file (a cached artifact is only
    reusable if it actually validates - a 0-byte or truncated file is a miss)."""
    try:
        if os.path.getsize(p) < 2:
            return False
        json.load(open(p, encoding="utf-8"))
        return True
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--do-assembly", action="store_true", help="Enable assembly ordering (agent decides order)")
    ap.add_argument("--no-frame-grid", action="store_true",
                    help="Skip frame grids. They are LOCAL and FREE and are how the agent sees "
                         "the footage - only skip for an audio-only decision.")
    ap.add_argument("--grid-cells", type=int, default=36,
                    help="Frames per grid image (default 36 ~261px/cell).")
    ap.add_argument("--do-rough", action="store_true", help="Enable rough cut (agent decides keep_word_ranges)")
    ap.add_argument("--do-silent", action="store_true", help="Enable silent cut (VAD intersect)")
    ap.add_argument("--workspace", default=str(Path(tempfile.gettempdir()) / "indigo11-capcut-work"),
                    help="Where to stage audio/transcripts/vad")
    ap.add_argument("--drafts-dir", default=str(CAPCUT_DRAFTS_DIR))
    ap.add_argument("--transcribe-provider", default="elevenlabs", choices=["elevenlabs", "grok", "openai", "auto"])
    ap.add_argument("--lang", default="th")
    args = ap.parse_args()

    project_dir = resolve_project(args.project, args.drafts_dir)
    if not project_dir.exists():
        emit_err(f"Project not found: {project_dir}")

    clips = read_media_panel(project_dir)
    if not clips:
        emit_err(f"Media panel empty in {args.project}", fix="ลาก video ลง Media panel ก่อน + Cmd+S + Cmd+Q")

    workspace = Path(args.workspace) / args.project
    (workspace / "audio").mkdir(parents=True, exist_ok=True)
    (workspace / "transcripts").mkdir(parents=True, exist_ok=True)
    (workspace / "vad").mkdir(parents=True, exist_ok=True)
    (workspace / "edl").mkdir(parents=True, exist_ok=True)

    # Save manifest
    manifest_path = workspace / "manifest.json"
    json.dump({"project": args.project, "project_dir": str(project_dir), "clips": clips},
              open(manifest_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    transcribe_script = find_skill("indigo11-transcribe", "transcribe.py")
    vad_script = find_skill("indigo11-clean-cut", "vad_detect.py")
    # VAD venv interpreter is per-OS: Scripts\python.exe (Windows) vs bin/python3 (POSIX)
    vad_env = Path.home() / ".indigo11" / "vad-env"
    vad_py = vad_env / ("Scripts/python.exe" if os.name == "nt" else "bin/python3")

    results = []
    for i, c in enumerate(clips):
        clip_dir = workspace / f"clip-{i:02d}"
        clip_dir.mkdir(exist_ok=True)
        # Content-address the cache by the SOURCE file identity (path+size+mtime),
        # not just the clip index. Otherwise swapping/reordering clips silently
        # reuses another file's transcript/VAD (P0-07). fp changes -> cache miss.
        fp = _clip_fingerprint(c["path"])
        wav = workspace / "audio" / f"{i:02d}.wav"
        if not extract_audio(c["path"], str(wav)):
            emit_err(f"ffmpeg failed on clip {c['path']}")
        tr_out = workspace / "transcripts" / f"{fp}.json"
        if not (tr_out.exists() and _json_ok(tr_out)):
            r = run([sys.executable, str(transcribe_script), str(wav),
                     "--provider", args.transcribe_provider, "--lang", args.lang,
                     "--output", str(tr_out)])
            # A failed STT (bad key / quota / network) must NOT be recorded as a
            # usable transcript - compute_edl would then crash on an empty/absent
            # file with an opaque traceback (P0-07/H12). Fail loudly + bilingually.
            if r.returncode != 0 or not (tr_out.exists() and _json_ok(tr_out)):
                emit_err(f"transcribe failed on clip {i} ({Path(c['path']).name}): "
                         f"{(r.stderr or r.stdout or '')[-300:]}",
                         fix="เช็ค key ของ STT / เน็ต แล้วรันใหม่ (check STT key / network)")
        vad_out = workspace / "vad" / f"{fp}.json"
        if not (vad_out.exists() and _json_ok(vad_out)) and args.do_silent:
            if not vad_py.exists():
                emit_err(f"VAD env missing: {vad_py}", fix=f"bash {find_skill('indigo11-clean-cut','install_vad.sh').parent / 'install_vad.sh'}")
            r = run([str(vad_py), str(vad_script), str(wav),
                     "--min-silence-ms", "300", "--pad-ms", "150",
                     "--output", str(vad_out)])
            if r.returncode != 0 or not (vad_out.exists() and _json_ok(vad_out)):
                emit_err(f"VAD failed on clip {i} ({Path(c['path']).name}): "
                         f"{(r.stderr or r.stdout or '')[-300:]}",
                         fix="เช็ค VAD env (~/.indigo11/vad-env) แล้วรันใหม่")
        results.append({"clip_index": i, "audio": str(wav),
                        "transcript": str(tr_out),
                        "vad": str(vad_out) if vad_out.exists() else None})

    # Frame grids so the deciding agent can SEE the footage (rule #37). Local ffmpeg tiling,
    # no API key, nothing leaves the machine. Without this the CapCut path is transcript-only:
    # the agent cannot tell which product is in frame, and cannot spot a silent demo it might
    # want to keep via `visual_keeps` (rule #37) - it would be deciding blind.
    frame_grids = []
    grid_err = None
    if not args.no_frame_grid:
        try:
            fg = find_skill("indigo11-analyze", "frame_grid.py")
        except Exception:
            fg = None
        if fg and fg.exists():
            for c in clips:
                gdir = workspace / "frame-grids" / Path(c["path"]).stem
                r = subprocess.run(
                    [sys.executable, str(fg), c["path"], "--out-dir", str(gdir),
                     "--cells", str(args.grid_cells)],
                    capture_output=True, text=True, encoding="utf-8", errors="replace")
                man = gdir / "manifest.json"
                if r.returncode != 0 or not man.exists():
                    grid_err = (r.stderr or "").strip()[:200] or "frame_grid failed"
                    continue
                try:
                    data = json.loads(man.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError) as e:
                    grid_err = f"unreadable manifest: {e}"
                    continue
                for g in data.get("grids", []):
                    frame_grids.append({"clip": Path(c["path"]).stem, "path": g.get("path"),
                                        "cols": g.get("cols"), "rows": g.get("rows"),
                                        "cells": g.get("cells", [])})
        else:
            grid_err = "indigo11-analyze/scripts/frame_grid.py not found"

    # Save stage manifest. Agent reads this to decide assembly order + rough keep_word_ranges,
    # then calls compute_edl.py with the decisions to produce final-edl.json.
    stage_path = workspace / "stage.json"
    stage = {
        "project": args.project, "project_dir": str(project_dir),
        "workspace": str(workspace),
        "clips": clips, "per_clip": results,
        # READ THESE before deciding cuts (rule #37) - grid PNGs, ~36 frames each with
        # per-cell timestamps. Empty when --no-frame-grid or generation failed (see error).
        "frame_grids": frame_grids, "frame_grids_error": grid_err,
        "do_assembly": args.do_assembly, "do_rough": args.do_rough, "do_silent": args.do_silent,
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    json.dump(stage, open(stage_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    emit_ok(
        op="process-all-stage1",
        project=args.project,
        workspace=str(workspace),
        clips=len(clips),
        stage_file=str(stage_path),
        next_step=(
            f"Agent reads {stage_path} + transcripts/* + vad/* to decide assembly order + rough keep_word_ranges, "
            f"then calls compute_edl.py to write final-edl.json, then inject_edl.py."
        ),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
