#!/usr/bin/env python3
"""Read a CapCut project's video timeline and emit a kit-compatible EDL.

Why this exists
---------------
After a user transcribes a video and injects captions into a CapCut project,
they may cut/trim/rearrange clips inside CapCut (manual editing in the NLE).
Once that happens, the original timeline is no longer in sync with the source
audio - captions transcribed from the source will be at the wrong timestamps
relative to the cut video. Re-transcribing costs another STT call and loses
all hand-edits already done.

This script reads the CapCut project's video timeline (which CapCut maintains
as `source_timerange` ↔ `target_timerange` pairs) and produces an EDL that
matches the kit's standard shape. Combined with `remap_captions.py`, this gives
end-to-end deterministic sync between manual CapCut cuts and any caption file.

Input: a CapCut project directory (one that has been Cmd+S'd and Cmd+Q'd - 
CapCut only flushes timeline state to disk on save).

Output: EDL JSON in the source↔target schema that `remap_captions.py` accepts - 
each segment carries BOTH `source_start_ms`/`source_end_ms` (range in the
original media) AND `target_start_ms`/`target_end_ms` (where it plays on the
CapCut timeline). This dual mapping is what makes reorders work - if the user
rearranged clips so source order ≠ target order, the EDL preserves both views
and remap_captions sorts output by target. See edit-rules #11c.

Time units (edit-rules #14):
  - CapCut's draft_info.json uses MICROSECONDS (μs). 1_000_000 μs = 1 s.
  - kit EDL uses MILLISECONDS (ms).
  - We convert at the boundary, in this script.

Which track, and per-clip EDLs
------------------------------
`--track-type audio` reads an AUDIO track instead of a video track. This exists
because a caption remap must follow whichever track carries the SPEECH: on a
project whose lav-mic audio was cut separately from the picture, building the EDL
off the video track puts every caption at the video's cut points while the words
are at the audio's - wrong timing with no error anywhere (add_captions.py's
--audio-track routes here).

`--clip <path-or-stem>` restricts the EDL to the segments that reference ONE media
file, and `--list-clips` reports the files a track uses. A multi-clip timeline
needs this: each clip has its own transcript in its own coordinate system, so
"source 0-3s" means a different moment per clip, and one flat EDL cannot say which.
The caller builds one EDL per clip and remaps that clip's words through it.

Because `remap_captions.py` requires source ranges not to overlap (a source moment
cannot play at two target places at once), a clip the buyer placed TWICE from the
same source range needs one EDL per placement. `--placement <n>` emits the n-th
such batch and the summary reports `placement_batches`, so every placement gets
captions instead of the duplicate being dropped.

Limitations
-----------
- Single-track assumption: this script reads the FIRST video track. For multi-
  track projects (PiP, B-roll on a second track), the main A-roll is almost
  always track 0 - explicitly specify with --track-index if not.
- Splits-and-rearranges: CapCut can rearrange segments (clip B then clip A then
  clip C). We honor target_timerange order, so the EDL reflects the actual
  on-screen sequence. remap_captions.py handles non-monotonic EDLs by sorting.
- Speed changes (slow-mo / fast-forward): CapCut stores `speed` on each segment.
  If speed != 1.0, source_timerange.duration != target_timerange.duration. We
  detect this and warn - remap won't be accurate on sped-up segments because
  it'd need to scale token times, which is out of scope. Run normal-speed cuts
  only, or split the project at speed-change boundaries.

Usage
-----
  capcut_to_edl.py --project 0528 --out edl-from-capcut.json
  capcut_to_edl.py --project /full/path/to/project --out edl.json --track-index 0
  capcut_to_edl.py --project 0528 --track-type audio --track-index 0 --out edl.json
  capcut_to_edl.py --project 0528 --list-clips
  capcut_to_edl.py --project 0528 --clip IMG_1766 --placement 0 --out edl-a0.json

Then:
  remap_captions.py --edl edl.json --in words.json --out words-remapped.json
"""
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)
import argparse
import json
import os
import sys
from pathlib import Path

import os as _os_utf8, sys as _sys_utf8
if _os_utf8.name == "nt":  # cp874 console crashes printing Thai (rule #32)
    for _s in (_sys_utf8.stdout, _sys_utf8.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")


# CapCut timeline JSON basename is per-OS: draft_content.json (Windows) /
# draft_info.json (macOS). See edit-rules #6/#32.
_DRAFT_STATE_NAMES = ("draft_content.json", "draft_info.json")


def _state_name(d):
    """Basename of the draft-state file present in dir d (per-OS), or the OS default."""
    for name in _DRAFT_STATE_NAMES:
        if (d / name).exists():
            return name
    return "draft_content.json" if os.name == "nt" else "draft_info.json"


def find_timeline_draft(project_dir):
    """Find the canonical timeline draft (the one CapCut reads on open).
    Per kit rule #6, it's at Timelines/<UUID>/<state>.json - usually one timeline
    subdir; we take it if unambiguous. <state> = draft_content.json on Windows,
    draft_info.json on macOS."""
    tl_dir = project_dir / "Timelines"
    if not tl_dir.is_dir():
        # Older / simpler projects keep the draft at the project root only.
        root = project_dir / _state_name(project_dir)
        if root.exists():
            return root
        raise SystemExit(f"no Timelines/ and no root draft state file in {project_dir}")
    timeline_dirs = [d for d in tl_dir.iterdir() if d.is_dir()]
    if not timeline_dirs:
        raise SystemExit(f"Timelines/ is empty in {project_dir}")
    if len(timeline_dirs) > 1:
        print(f"WARNING: multiple timelines found, using {timeline_dirs[0].name}", file=sys.stderr)
    return timeline_dirs[0] / _state_name(timeline_dirs[0])


def extract_video_segments(draft, track_index, track_type="video"):
    """Return list of segments (dicts) from the track_type track at given index.

    track_type is "video" (default, the main A-roll) or "audio". A caption remap
    has to read whichever track carries the SPEECH - reading the video track when
    the speech was cut on a separate audio track gives captions that follow the
    picture's cut points and the words' timing from somewhere else, with nothing
    raising. Kept named extract_video_segments for the existing callers.
    """
    if track_type not in ("video", "audio"):
        raise SystemExit(f"track_type must be video or audio, got {track_type!r}")
    tracks = [t for t in draft.get("tracks", []) if t.get("type") == track_type]
    if not tracks:
        raise SystemExit(f"no {track_type} track in project")
    if track_index >= len(tracks):
        raise SystemExit(
            f"track_index {track_index} out of range ({len(tracks)} {track_type} tracks)")
    track = tracks[track_index]
    segs = track.get("segments", [])
    if not segs:
        raise SystemExit(f"{track_type} track {track_index} has no segments")
    return segs


def material_paths(draft):
    """{material_id: absolute media path} over every materials category.

    A segment names only its material_id; the path lives on the material. Both
    `path` (draft_info/draft_content) and `file_Path` (draft_meta_info shape) are
    accepted so this works whichever file the caller loaded.
    """
    out = {}
    for items in (draft.get("materials") or {}).values():
        if not isinstance(items, list):
            continue
        for it in items:
            if isinstance(it, dict) and it.get("id"):
                p = it.get("path") or it.get("file_Path") or ""
                if p:
                    out[it["id"]] = p
    return out


def clip_key(path):
    """The stem a transcript cache is keyed by (rule #2: projects/_work/transcribe/<stem>/)."""
    return Path(path).stem


def segments_by_clip(segments, mat_paths):
    """Group segments by the media file they reference, in timeline order.

    Returns (groups, unresolved) where groups is a list of
    {"path", "key", "segments"} ordered by each clip's first appearance on the
    timeline, and unresolved lists segments whose material carries no path -
    the caller must HARD STOP on those rather than caption part of a timeline.
    """
    ordered = sorted(segments, key=lambda s: int(
        (s.get("target_timerange") or {}).get("start", 0) or 0))
    groups, index, unresolved = [], {}, []
    for s in ordered:
        p = mat_paths.get(s.get("material_id")) or ""
        if not p:
            unresolved.append(s)
            continue
        if p not in index:
            index[p] = len(groups)
            groups.append({"path": p, "key": clip_key(p), "segments": []})
        groups[index[p]]["segments"].append(s)
    return groups, unresolved


def placement_batches(segments):
    """Split one clip's segments into batches with NON-OVERLAPPING source ranges.

    remap_captions.py rejects overlapping source ranges, and rightly so: a single
    source↔target map cannot say that source 0-3s plays at BOTH target 0-3s and
    target 9-12s. A clip the buyer duplicated does exactly that. Batching greedily
    by target order puts the first placement of each source range in batch 0, the
    second in batch 1, and so on - so the caller remaps once per batch and every
    placement gets captions, instead of the duplicate being silently dropped.
    """
    ordered = sorted(segments, key=lambda s: int(
        (s.get("target_timerange") or {}).get("start", 0) or 0))
    batches = []
    for s in ordered:
        src = s.get("source_timerange") or {}
        ss = int(src.get("start", 0) or 0)
        se = ss + int(src.get("duration", 0) or 0)
        for b in batches:
            if all(se <= int((o.get("source_timerange") or {}).get("start", 0) or 0)
                   or ss >= int((o.get("source_timerange") or {}).get("start", 0) or 0)
                   + int((o.get("source_timerange") or {}).get("duration", 0) or 0)
                   for o in b):
                b.append(s)
                break
        else:
            batches.append([s])
    return batches


def segments_to_edl(segments, drop_source_overlaps=True):
    """Convert CapCut video segments → kit EDL with source↔target mapping.

    Each CapCut segment carries:
      source_timerange = {start, duration} (in original media, μs)
      target_timerange = {start, duration} (on CapCut timeline, μs)
      speed (optional) (1.0 default)

    We emit one EDL entry per surviving segment with BOTH coordinate systems
    preserved. This is essential when the user reordered clips in CapCut:
    source order is the chronological order of the original media, target
    order is what plays on the CapCut timeline. The two can differ. We DO NOT
    sort - order is preserved as CapCut wrote it (which is target order in the
    JSON; the actual ordering doesn't matter to remap_captions because the
    target field is explicit).

    Returns: (edl_segments_list, warnings_list)

    EDL segment shape (new - supports reorders):
      {source_start_ms, source_end_ms, target_start_ms, target_end_ms}

    drop_source_overlaps=False keeps every segment and only warns. Pass it when
    the caller has already split the input into placement batches whose source
    ranges cannot overlap (placement_batches) - dropping there would throw away
    a real placement, and the dropped one used to be the buyer's duplicated clip
    losing its captions entirely.
    """
    warnings = []
    keeps = []
    for i, s in enumerate(segments):
        src = s.get("source_timerange") or {}
        tgt = s.get("target_timerange") or {}
        speed = s.get("speed", 1.0) or 1.0
        if abs(speed - 1.0) > 0.001:
            warnings.append(f"segment {i} has speed={speed} (≠ 1.0); remap won't be accurate")
        # CapCut μs → kit ms
        src_start_us = int(src.get("start", 0) or 0)
        src_dur_us = int(src.get("duration", 0) or 0)
        if src_dur_us <= 0:
            warnings.append(f"segment {i} has zero/negative source duration; skipping")
            continue
        tgt_start_us = int(tgt.get("start", 0) or 0)
        tgt_dur_us = int(tgt.get("duration", 0) or src_dur_us)
        keeps.append({
            "source_start_ms": src_start_us // 1000,
            "source_end_ms": (src_start_us + src_dur_us) // 1000,
            "target_start_ms": tgt_start_us // 1000,
            "target_end_ms": (tgt_start_us + tgt_dur_us) // 1000,
            "_segment_index": i,
        })

    # Validate: no two segments use the SAME source range (CapCut "duplicate
    # clip" would do this - warn, keep first occurrence by target order).
    by_target = sorted(keeps, key=lambda k: k["target_start_ms"])
    seen_src = []
    for k in by_target:
        overlap = False
        for prev in seen_src:
            if not (k["source_end_ms"] <= prev["source_start_ms"] or
                    k["source_start_ms"] >= prev["source_end_ms"]):
                warnings.append(
                    f"source range overlap (segments {prev['_segment_index']} and "
                    f"{k['_segment_index']} share part of the source). CapCut duplicate-clip "
                    f"creates this - "
                    + ("keeping the first occurrence on target."
                       if drop_source_overlaps else
                       "kept anyway (caller batches placements).")
                )
                overlap = True
                break
        if not overlap or not drop_source_overlaps:
            seen_src.append(k)
    # Detect reorders for user info
    reordered = any(
        seen_src[i]["target_start_ms"] < seen_src[i - 1]["target_start_ms"]
        for i in range(1, len(seen_src))
    ) if seen_src else False
    # That should never be true after sort-by-target above, so detect from source:
    src_seq = [k["source_start_ms"] for k in by_target]
    reordered = any(src_seq[i] < src_seq[i - 1] for i in range(1, len(src_seq)))
    if reordered:
        warnings.append("clips reordered in CapCut (target order ≠ source order); "
                        "remap will honor target order")
    return seen_src, warnings


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True,
                    help="CapCut project name (under the per-OS drafts dir: "
                         "%%LOCALAPPDATA%%\\CapCut\\... on Windows, ~/Movies/CapCut/... on macOS; "
                         "override with CAPCUT_DRAFTS_DIR) or absolute path to the project directory.")
    ap.add_argument("--track-index", type=int, default=0,
                    help="Which track of --track-type to read (0 = first). Default 0 - "
                         "the main A-roll.")
    ap.add_argument("--track-type", choices=["video", "audio"], default="video",
                    help="Read a video track (default) or an AUDIO track. Use audio when "
                         "the speech was cut on its own track - the caption remap must "
                         "follow the track that carries the words, not the picture.")
    ap.add_argument("--clip",
                    help="Restrict the EDL to segments referencing this media file "
                         "(absolute path, basename or stem). A multi-clip timeline needs "
                         "one EDL per clip, because each clip's transcript has its own "
                         "coordinate system.")
    ap.add_argument("--placement", type=int, default=None,
                    help="With --clip: which placement batch to emit (0 = first). A clip "
                         "placed twice from the SAME source range needs one EDL per "
                         "placement, since remap rejects overlapping source ranges.")
    ap.add_argument("--list-clips", action="store_true",
                    help="Report the media files the chosen track uses (and each one's "
                         "placement-batch count) as JSON, and write no EDL.")
    ap.add_argument("--out", help="Output EDL JSON path (required unless --list-clips)")
    args = ap.parse_args()
    if not args.out and not args.list_clips:
        ap.error("--out is required (or pass --list-clips)")
    if args.placement is not None and not args.clip:
        ap.error("--placement needs --clip (placements are per media file)")

    # Resolve project path. Bare name -> per-OS CapCut drafts dir
    # (Windows %LOCALAPPDATA%\CapCut\... / macOS ~/Movies/CapCut\...), overridable
    # with CAPCUT_DRAFTS_DIR. Absolute / ~ paths are used as-is.
    if os.path.sep in args.project or (os.altsep and os.altsep in args.project) or args.project.startswith("~"):
        project_dir = Path(os.path.expanduser(args.project)).resolve()
    else:
        env = os.environ.get("CAPCUT_DRAFTS_DIR")
        if env:
            drafts = Path(os.path.expanduser(env))
        elif os.name == "nt":
            base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
            drafts = Path(base) / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
        else:
            drafts = Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
        project_dir = drafts / args.project
    if not project_dir.is_dir():
        raise SystemExit(f"project not found: {project_dir}")

    draft_path = find_timeline_draft(project_dir)
    with open(draft_path, encoding="utf-8") as f:
        draft = json.load(f)
    segments = extract_video_segments(draft, args.track_index, args.track_type)
    mat_paths = material_paths(draft)
    clip_groups, unresolved = segments_by_clip(segments, mat_paths)

    if args.list_clips:
        print(json.dumps({
            "ok": not unresolved,
            "project": str(project_dir),
            "track_type": args.track_type,
            "track_index": args.track_index,
            "clips": [{
                "path": g["path"], "key": g["key"],
                "segments": len(g["segments"]),
                "placement_batches": len(placement_batches(g["segments"])),
            } for g in clip_groups],
            "unresolved_segments": [s.get("id") for s in unresolved],
        }, ensure_ascii=False))
        return 0 if not unresolved else 1

    selected_clip = None
    if args.clip:
        want = str(args.clip)
        # An ABSOLUTE path is already unambiguous, so it matches on the full path
        # ALONE. Folding its basename/stem into the candidate set made two clips
        # that share a basename in different folders BOTH match, and the refusal
        # then said "ambiguous - pass the full path" although the full path was
        # exactly what had been passed: a dead end with no further argument to try.
        # A bare name still matches by name or stem (that is the convenient form).
        want_abs = os.path.isabs(os.path.expanduser(want))
        if want_abs:
            cands = {str(Path(os.path.expanduser(want)))}
            try:
                cands.add(str(Path(os.path.expanduser(want)).resolve()))
            except OSError:
                pass

            def _hit(g):
                gp = str(Path(g["path"]))
                if gp in cands:
                    return True
                try:
                    return str(Path(g["path"]).resolve()) in cands
                except OSError:
                    return False
        else:
            wants = {want, Path(want).name, Path(want).stem}

            def _hit(g):
                return (g["path"] in wants or Path(g["path"]).name in wants
                        or g["key"] in wants)

        matched = [g for g in clip_groups if _hit(g)]
        if not matched:
            raise SystemExit(
                f"--clip {want!r} matches no media on {args.track_type} track "
                f"{args.track_index}. available: "
                f"{[g['path'] if want_abs else g['key'] for g in clip_groups]}")
        if len(matched) > 1:
            # Only reachable for a BARE name now; an absolute path matches one
            # media or none, so the advice it gives is actionable.
            raise SystemExit(
                f"--clip {want!r} is ambiguous - matches "
                f"{[g['path'] for g in matched]}; pass the full path")
        selected_clip = matched[0]
        segments = selected_clip["segments"]
        if args.placement is not None:
            batches = placement_batches(segments)
            if args.placement >= len(batches):
                raise SystemExit(
                    f"--placement {args.placement} out of range: clip "
                    f"{selected_clip['key']} has {len(batches)} placement batch(es)")
            segments = batches[args.placement]

    # Per-batch callers have already guaranteed non-overlapping source ranges, so
    # the duplicate-drop would only ever discard a legitimate placement there.
    keeps, warnings = segments_to_edl(
        segments, drop_source_overlaps=(args.placement is None))

    if not keeps:
        raise SystemExit("no keepable segments found")

    # Compose EDL in kit's standard shape (source+target mapping - supports reorders)
    edl = {
        "segments": [{
            "source_start_ms": k["source_start_ms"],
            "source_end_ms": k["source_end_ms"],
            "target_start_ms": k["target_start_ms"],
            "target_end_ms": k["target_end_ms"],
        } for k in keeps],
        "source": "capcut",
        "source_project": args.project,
        "source_draft": str(draft_path),
        "track_index": args.track_index,
        "track_type": args.track_type,
        "clip": selected_clip["path"] if selected_clip else None,
        "clip_key": selected_clip["key"] if selected_clip else None,
        "placement": args.placement,
        "kept_seconds": round(sum(k["source_end_ms"] - k["source_start_ms"] for k in keeps) / 1000, 3),
        "notes": warnings if warnings else ["timeline parsed from CapCut without warnings"],
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(edl, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "ok": True,
        "project": str(project_dir),
        "track_index": args.track_index,
        "track_type": args.track_type,
        "clip": selected_clip["path"] if selected_clip else None,
        "clip_key": selected_clip["key"] if selected_clip else None,
        "placement": args.placement,
        "placement_batches": (len(placement_batches(selected_clip["segments"]))
                              if selected_clip else None),
        "clips_on_track": [g["key"] for g in clip_groups],
        "unresolved_segments": [s.get("id") for s in unresolved],
        "input_segments": len(segments),
        "edl_segments": len(keeps),
        "kept_seconds": edl["kept_seconds"],
        "warnings": warnings,
        "output": args.out,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
