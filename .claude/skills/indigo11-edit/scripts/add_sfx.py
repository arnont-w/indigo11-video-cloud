#!/usr/bin/env python3
"""add_sfx.py - Place SFX (sound effects) onto a CapCut project timeline.

PREREQUISITE: the SFX file must be registered in CapCut's Media panel. This script
only PLACES already-registered media on the timeline - it does not register new
files. Register first with `import_media.py` (see below); no manual drag needed.

Registration is THREE PLAIN JSON WRITES and it works (edit-rules #25):
draft_meta_info.json draft_materials[type=0], a draft_virtual_store.json child_id
link, then the 9-place mirror. `import_media.py` does all three - verified on 81
real files in one pass. (An earlier version of this note claimed a hidden
SQLite/binary registry in the macOS sandbox made JSON-only registration
impossible. That was WRONG: the container holds only WebKit and crash-handler
DBs, nothing about media. Do not reintroduce the manual-drag instruction.)

Buyer flow:
  1. python3 import_media.py --project <p> --files <sfx.mp3> ...   (CapCut QUIT)
  2. Run this script with timing JSON

Reads:
  - <project>/Timelines/<UUID>/draft_info.json
  - SFX timing JSON: {"sfx": [{"file": "<name>", "at_ms": int, "volume": float}]}

To get the `at_ms` placements WITHOUT motion analysis or any cost, run the beat helper
first and build this JSON from its output:
  python3 .claude/skills/indigo11-find-assets/scripts/suggest_sfx_beats.py --context <ctx.json>
  # or --audio <media>: one ffmpeg pass catching phrase ends AND loud transients in the
  #   source (mouse click / slap / hit) that the transcript can't see.
It returns beats[].at_ms; pair each with a downloaded `file` and volume here.

`file` matches by basename against materials.audios[].name (= name shown in
Media panel). Absolute paths also work - we match by path too.

Writes:
  - patched draft_info.json with new audio segments at given timeline positions
  - 9-place mirror (rule #6)

Usage:
  python3 add_sfx.py --project 0521 --sfx-timing sfx.json
"""

from __future__ import annotations
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _capcut_lib import (
    CAPCUT_DRAFTS_DIR, ensure_capcut_closed, new_uuid,
    load_template, deep_copy, emit_ok, emit_err, US, MS, S,
    draft_state_path, load_draft, mirror_9_places, atomic_write_text,
    begin_op, assert_preserved,
)


def ffprobe_duration_us(path: str) -> int:
    import subprocess
    res = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "csv=p=0", path,
    ], capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return int(float(res.stdout.strip()) * 1_000_000)
    except (ValueError, AttributeError):
        return 0


def find_audio_material(draft: dict, sfx_file: str, project_dir: Path) -> tuple[str, int]:
    """Find an audio that buyer already dragged into Media panel.

    Two locations to check:
      1. draft_info.materials.audios[] - already-used materials in this timeline
      2. draft_meta_info.draft_materials[type=0] (metetype="music") - Media panel only

    If found only in meta (#2), register it into draft_info.materials.audios so
    timeline segments can reference it. Returns (material_id, duration_us).

    Raises FileNotFoundError with clear Thai+English message if not in either.
    """
    audios = draft.setdefault("materials", {}).setdefault("audios", [])
    target = os.path.basename(sfx_file)
    abs_path_in = str(Path(sfx_file).expanduser().resolve())

    # Already in this draft's audios?
    for a in audios:
        if a.get("name") == target or a.get("path") == abs_path_in:
            return a["id"], a.get("duration", 0)

    # Look in Media panel (draft_meta_info.json)
    meta_path = project_dir / "draft_meta_info.json"
    if meta_path.exists():
        meta = json.load(open(meta_path, encoding="utf-8"))
        for g in meta.get("draft_materials", []):
            if g.get("type") != 0:
                continue
            for v in g.get("value", []):
                if v.get("metetype") != "music":
                    continue
                if v.get("extra_info") == target or v.get("file_Path") == abs_path_in:
                    # Found in Media panel - clone into draft's materials.audios
                    duration_us = v.get("duration", 0)
                    abs_path = v.get("file_Path", abs_path_in)

                    tpl = load_template("audio/audio_material.json")
                    mat = deep_copy(tpl)
                    new_id = new_uuid() # draft_info uses UPPERCASE
                    mat["id"] = new_id
                    mat["name"] = target
                    mat["path"] = abs_path
                    mat["duration"] = duration_us
                    audios.append(mat)
                    return new_id, duration_us

    raise FileNotFoundError(
        f"SFX '{target}' ยังไม่อยู่ใน CapCut Media panel - ปิด CapCut แล้วรัน "
        f"import_media.py --project <project> --files <file> ก่อน แล้วลองใหม่ "
        f"(SFX not registered - quit CapCut, run import_media.py to register it, "
        f"then retry; no manual drag needed - rule #25)"
    )


def build_extra_refs(draft: dict) -> list[str]:
    """Mint 5 extra_material_refs (speeds, placeholder_infos, beats, sound_channel_mappings, vocal_separations).
    Append each to its category in materials.
    """
    refs = []
    for category in ("speeds", "placeholder_infos", "beats", "sound_channel_mappings", "vocal_separations"):
        tpl_name = f"audio/{category if category != 'sound_channel_mappings' else 'sound_channel_mappings'}.json"
        tpl = load_template(tpl_name)
        obj = deep_copy(tpl)
        obj["id"] = new_uuid()
        draft.setdefault("materials", {}).setdefault(category, []).append(obj)
        refs.append(obj["id"])
    return refs


def build_audio_segment(material_id: str, at_ms: int, duration_us: int,
                        volume: float, fade_in_ms: int, fade_out_ms: int,
                        extra_refs: list[str]) -> dict:
    """Build an audio track segment placing the SFX at given timeline position."""
    tpl = load_template("audio/audio_segment.json")
    seg = deep_copy(tpl)
    seg["id"] = new_uuid()
    seg["material_id"] = material_id
    seg["extra_material_refs"] = extra_refs
    seg["source_timerange"] = {"start": 0, "duration": duration_us}
    seg["target_timerange"] = {"start": at_ms * MS, "duration": duration_us}
    seg["volume"] = volume
    seg["last_nonzero_volume"] = volume
    # Fades are stored on segment as audio_fade_id refs typically; skip for v1
    # CapCut UI shows them as separate material refs - leaving fade_in/out_ms
    # for future extension. Volume is the main control.
    return seg


def get_or_create_audio_track(draft: dict) -> dict:
    """Find first audio track, or append a new one."""
    tracks = draft.setdefault("tracks", [])
    audio_tracks = [t for t in tracks if t.get("type") == "audio"]
    if audio_tracks:
        return audio_tracks[0]
    # No audio track yet - create a minimal one matching CapCut's shape
    new_track = {
        "attribute": 0,
        "flag": 0,
        "id": new_uuid(),
        "is_default_name": True,
        "name": "",
        "segments": [],
        "type": "audio",
    }
    tracks.append(new_track)
    return new_track


def _overlaps(track: dict, start_us: int, dur_us: int) -> bool:
    end_us = start_us + dur_us
    for s in track.get("segments") or []:
        tr = s.get("target_timerange") or {}
        a = int(tr.get("start", 0))
        b = a + int(tr.get("duration", 0))
        if start_us < b and a < end_us:
            return True
    return False


def _new_audio_track(draft: dict) -> dict:
    t = {"attribute": 0, "flag": 0, "id": new_uuid(), "is_default_name": True,
         "name": "", "segments": [], "type": "audio"}
    draft.setdefault("tracks", []).append(t)
    return t


def pick_track(draft: dict, mode: str, start_us: int, dur_us: int, run_tracks: list) -> dict:
    """Which audio track this segment lands on.

    first - the first audio track (the original behaviour; may stack on top of
            whatever is already there).
    auto  - the first audio track where it does not overlap anything, else a new
            one. A CapCut track cannot hold two overlapping segments, so a voice-over
            and an SFX placed on the same track collide; auto keeps them apart.
    new   - a track created by THIS run (spilling to another new one on overlap),
            never one that existed before.
    """
    if mode == "first":
        return get_or_create_audio_track(draft)
    pool = run_tracks if mode == "new" else \
        [t for t in (draft.get("tracks") or []) if t.get("type") == "audio"]
    for t in pool:
        if not _overlaps(t, start_us, dur_us):
            return t
    t = _new_audio_track(draft)
    run_tracks.append(t)
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="CapCut project name")
    ap.add_argument("--sfx-timing", required=True, help="Path to SFX timing JSON")
    ap.add_argument("--drafts-dir", help="Override CapCut drafts dir")
    ap.add_argument("--force-kill", action="store_true",
                    help="Kill a running CapCut instead of refusing. Unsaved work is lost.")
    ap.add_argument("--track", choices=("first", "auto", "new"), default="first",
                    help="first = first audio track (default, original behaviour); "
                         "auto = first track with no overlap, else a new one (voice-over + SFX); "
                         "new = only tracks this run creates")
    args = ap.parse_args()

    drafts = Path(args.drafts_dir) if args.drafts_dir else CAPCUT_DRAFTS_DIR
    project_dir = drafts / args.project
    if not project_dir.exists():
        emit_err(op="add-sfx", error=f"Project not found: {project_dir}")

    timing_path = Path(args.sfx_timing).expanduser()
    if not timing_path.exists():
        emit_err(op="add-sfx", error=f"Timing JSON not found: {timing_path}")

    sfx_list = json.load(open(timing_path, encoding="utf-8")).get("sfx", [])
    if not sfx_list:
        emit_err(op="add-sfx", error="No 'sfx' entries in timing JSON")

    # Rule #7: a live CapCut auto-saves over our write. Refuse rather than SIGKILL
    # a GUI holding unsaved work unless the buyer explicitly passed --force-kill.
    ok, close_detail = ensure_capcut_closed(args.force_kill)
    if not ok:
        emit_err(op="add-sfx", error=close_detail)

    # Load draft (platform-correct timeline filename); blank project -> clean error (rule #23)
    draft, draft_path, _tl_uuid = load_draft(project_dir)
    before_draft = deep_copy(draft)

    existing_track_ids = {t.get("id") for t in (draft.get("tracks") or [])}
    run_tracks: list = []
    touched_tracks: dict = {}
    materials_before = set()
    for cat, items in (draft.get("materials") or {}).items():
        if isinstance(items, list):
            for i in items:
                if isinstance(i, dict) and i.get("id"):
                    materials_before.add(i["id"])

    # Process each SFX
    added = []
    for entry in sfx_list:
        sfx_file = entry["file"]
        at_ms = int(entry["at_ms"])
        volume = float(entry.get("volume", 1.0))
        fade_in = int(entry.get("fade_in_ms", 0))
        fade_out = int(entry.get("fade_out_ms", 0))

        # Resolve to absolute path if relative path
        if not os.path.isabs(sfx_file) and os.path.sep not in sfx_file:
            # Just a name - must already be in materials.audios
            pass
        else:
            sfx_file = str(Path(sfx_file).expanduser().resolve())

        mat_id, duration_us = find_audio_material(draft, sfx_file, project_dir)
        if entry.get("max_ms"):
            duration_us = min(duration_us, int(entry["max_ms"]) * MS)
        refs = build_extra_refs(draft)
        seg = build_audio_segment(mat_id, at_ms, duration_us, volume, fade_in, fade_out, refs)
        audio_track = pick_track(draft, args.track, at_ms * MS, duration_us, run_tracks)
        audio_track["segments"].append(seg)
        touched_tracks[audio_track["id"]] = audio_track

        added.append({
            "file": os.path.basename(sfx_file),
            "at_ms": at_ms,
            "duration_ms": duration_us // MS,
            "volume": volume,
            "track_id": audio_track["id"],
        })

    # This op only ADDS: new audio segments (and any material they needed) onto one
    # track. Declaring that and checking it is what stops an add from quietly
    # rewriting a clip or dropping a material something else still uses.
    materials_after = set()
    for cat, items in (draft.get("materials") or {}).items():
        if isinstance(items, list):
            for i in items:
                if isinstance(i, dict) and i.get("id"):
                    materials_after.add(i["id"])
    # assert_preserved compares materials category-by-category, so a category this
    # op CREATES (audio helpers land in speeds/beats/... a video-only draft has
    # never had) reads as "materials.beats added unexpectedly" even though every
    # entry in it is planned. Seed the missing keys as empty lists on the BEFORE
    # side: the comparison then sees [] -> [planned ids], which the plan covers.
    for cat in (draft.get("materials") or {}):
        if isinstance(draft["materials"][cat], list):
            before_draft.setdefault("materials", {}).setdefault(cat, [])
    new_ids = [tid for tid in touched_tracks if tid not in existing_track_ids]
    old_ids = [tid for tid in touched_tracks if tid in existing_track_ids]
    plan = {
        "add": {"tracks": new_ids,
                "materials": sorted(materials_after - materials_before)},
        "replace": {"tracks": old_ids},
        "delete": {"segments": [], "materials": []},
    }
    try:
        assert_preserved(before_draft, draft, plan)
    except ValueError as e:
        emit_err(op="add-sfx", error=str(e))

    # Save + mirror (9-place, canonical), reversibly
    with begin_op(project_dir, "add-sfx", plan=plan) as op:
        atomic_write_text(draft_path, json.dumps(draft, indent=2, ensure_ascii=False))
        mirror = mirror_9_places(project_dir)
        op.check_mirror(mirror)

    emit_ok(
        op="add-sfx",
        project=args.project,
        sfx_added=len(added),
        details=added,
        track_mode=args.track,
        tracks_created=len(new_ids),
        mirror_paths=len(mirror.get("copies", [])),
        capcut_close=close_detail,
        next_step="เปิด CapCut → " + args.project + " → ดู audio track ว่า SFX อยู่ตรงเวลาที่กำหนด",
        **op.info(),
    )


if __name__ == "__main__":
    main()
