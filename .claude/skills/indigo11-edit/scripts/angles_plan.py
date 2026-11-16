#!/usr/bin/env python3
"""angles_plan.py - host-neutral multi-camera ANGLES rule engine.

Angles is NOT Podcast speaker-switching. Podcast asks "who is talking" and cuts to
that camera. Angles layers editorial camera RULES on top of the same diarized
transcript:

  - active-speaker switching (base, same data as Podcast)
  - wide on overlap (two people talking at once)
  - reaction cutaways during long monologues (cut to a listener camera)
  - minimum hold (no camera chatter)
  - no-repeat (avoid A->B->A flapping right after switching away)
  - manual locks (reviewer forces a camera over a time range)

Every switch in the output carries a `reason` + `confidence`, and the plan is
review-first: the engine also writes `angles-switches.json` (a flat reviewable
switch list). A reviewer corrects switches by re-running with `--lock` ranges
(or a `--corrections` JSON file of the same shape) - locks always win.

The output uses the SAME versioned plan envelope as podcast_plan.py
(`operations[0].type == "switch_angles"`), so the existing Premiere and Resolve
podcast adapters can apply an Angles plan without new host code.

Deterministic by design (edit-rules #18): every rule here is arithmetic on
timestamps - no LLM. Semantic judgment (e.g. "is this beat emotionally a reaction
moment") stays with the calling agent, which can encode its decision as locks.
"""
from __future__ import annotations
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from podcast_plan import (  # noqa: E402  (same-skill helper module)
    build_utterances,
    ensure_transcript,
    kit_root,
    media_fingerprint,
    parse_map,
    probe_duration_ms,
    transcript_words,
)


def parse_locks(values: list[str]) -> list[dict[str, int]]:
    """--lock 12000-15000=2  ->  {"start_ms": 12000, "end_ms": 15000, "camera_index": 2}"""
    locks: list[dict[str, int]] = []
    for raw in values:
        for item in raw.split(","):
            if not item.strip():
                continue
            span, sep, camera = item.partition("=")
            start, sep2, end = span.partition("-")
            if not sep or not sep2:
                raise ValueError(f"invalid lock (want start-end=camera): {item!r}")
            start_ms, end_ms, cam = int(start), int(end), int(camera)
            if end_ms <= start_ms:
                raise ValueError(f"lock range is empty: {item!r}")
            locks.append({"start_ms": start_ms, "end_ms": end_ms, "camera_index": cam})
    locks.sort(key=lambda lock: lock["start_ms"])
    for a, b in zip(locks, locks[1:]):
        if b["start_ms"] < a["end_ms"]:
            raise ValueError(f"overlapping locks at {b['start_ms']}ms")
    return locks


def active_speaker_segments(utterances: list[dict[str, Any]], speaker_map: dict[str, str],
                            duration_ms: int, wide_camera: str | None) -> list[dict[str, Any]]:
    """Base pass: one segment per active-speaker span, with reason + confidence."""
    boundaries = sorted({0, duration_ms,
                         *(u["start_ms"] for u in utterances),
                         *(u["end_ms"] for u in utterances)})
    segments: list[dict[str, Any]] = []
    for start_ms, end_ms in zip(boundaries, boundaries[1:]):
        if end_ms <= start_ms:
            continue
        active = [u for u in utterances if u["start_ms"] < end_ms and u["end_ms"] > start_ms]
        speakers = list(dict.fromkeys(u["speaker_id"] for u in active))
        if len(speakers) > 1 and wide_camera is not None:
            seg = {"camera_id": wide_camera, "speaker_id": "+".join(speakers),
                   "reason": "wide-on-overlap",
                   "confidence": min((u["confidence"] for u in active
                                      if u["confidence"] is not None), default=None)}
        elif active:
            chosen = max(active, key=lambda u: (u["start_ms"], u["end_ms"]))
            camera = speaker_map.get(chosen["speaker_id"]) or wide_camera
            if camera is None:
                raise ValueError(f"missing camera mapping for {chosen['speaker_id']}")
            seg = {"camera_id": camera, "speaker_id": chosen["speaker_id"],
                   "reason": "active-speaker", "confidence": chosen["confidence"]}
        elif segments:
            prev = segments[-1]
            seg = {"camera_id": prev["camera_id"], "speaker_id": prev["speaker_id"],
                   "reason": "hold-through-silence", "confidence": prev["confidence"]}
        else:
            first = utterances[0]
            camera = speaker_map.get(first["speaker_id"]) or wide_camera
            if camera is None:
                raise ValueError(f"missing camera mapping for {first['speaker_id']}")
            seg = {"camera_id": camera, "speaker_id": first["speaker_id"],
                   "reason": "opening", "confidence": first["confidence"]}
        seg.update(start_ms=start_ms, end_ms=end_ms)
        if segments and segments[-1]["camera_id"] == seg["camera_id"]:
            segments[-1]["end_ms"] = end_ms
        else:
            segments.append(seg)
    if not segments:
        raise ValueError("no angle segments could be built")
    return segments


def enforce_min_hold(segments: list[dict[str, Any]], min_hold_ms: int,
                     wide_camera: str | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for seg in segments:
        short = seg["end_ms"] - seg["start_ms"] < min_hold_ms
        if short and out and seg["camera_id"] != out[-1]["camera_id"]:
            if wide_camera is not None and out[-1]["camera_id"] != wide_camera:
                seg = {**seg, "camera_id": wide_camera, "reason": "min-hold-wide"}
            else:
                out[-1]["end_ms"] = seg["end_ms"]
                continue
        if out and out[-1]["camera_id"] == seg["camera_id"]:
            out[-1]["end_ms"] = seg["end_ms"]
        else:
            out.append(dict(seg))
    return out


def apply_no_repeat(segments: list[dict[str, Any]], no_repeat_ms: int,
                    wide_camera: str | None) -> list[dict[str, Any]]:
    """Suppress A->B->A flapping: a return to the camera we just left within
    no_repeat_ms extends the current camera instead (or goes wide)."""
    if no_repeat_ms <= 0:
        return segments
    out: list[dict[str, Any]] = []
    for seg in segments:
        if (len(out) >= 2
                and seg["camera_id"] == out[-2]["camera_id"]
                and seg["camera_id"] != out[-1]["camera_id"]
                and seg["start_ms"] - out[-2]["end_ms"] < no_repeat_ms
                and seg["end_ms"] - seg["start_ms"] < no_repeat_ms):
            if wide_camera is not None and out[-1]["camera_id"] != wide_camera:
                seg = {**seg, "camera_id": wide_camera, "reason": "no-repeat-wide"}
            else:
                out[-1]["end_ms"] = seg["end_ms"]
                continue
        if out and out[-1]["camera_id"] == seg["camera_id"]:
            out[-1]["end_ms"] = seg["end_ms"]
        else:
            out.append(dict(seg))
    return out


def insert_reactions(segments: list[dict[str, Any]], cameras: list[str],
                     speaker_map: dict[str, str], every_ms: int, dur_ms: int,
                     min_hold_ms: int, wide_camera: str | None) -> list[dict[str, Any]]:
    """During a monologue longer than every_ms, cut to a listener camera for dur_ms.
    Listener = a mapped camera that is not the active one (least recently used);
    falls back to wide. No candidate -> no reaction."""
    if every_ms <= 0:
        return segments
    last_seen = {camera: -1 for camera in cameras}
    clock = 0
    out: list[dict[str, Any]] = []
    for seg in segments:
        for camera in cameras:
            if camera == seg["camera_id"]:
                last_seen[camera] = clock
        clock += 1
        length = seg["end_ms"] - seg["start_ms"]
        if seg["reason"] != "active-speaker" or length < every_ms + dur_ms + min_hold_ms:
            out.append(seg)
            continue
        candidates = [c for c in dict.fromkeys(speaker_map.values()) if c != seg["camera_id"]]
        if wide_camera is not None and wide_camera not in candidates and wide_camera != seg["camera_id"]:
            candidates.append(wide_camera)
        if not candidates:
            out.append(seg)
            continue
        cursor = seg["start_ms"]
        while seg["end_ms"] - cursor >= every_ms + dur_ms + min_hold_ms:
            reaction_start = cursor + every_ms
            target = min(candidates, key=lambda c: last_seen.get(c, -1))
            out.append({**seg, "start_ms": cursor, "end_ms": reaction_start})
            out.append({"start_ms": reaction_start, "end_ms": reaction_start + dur_ms,
                        "camera_id": target, "speaker_id": seg["speaker_id"],
                        "reason": "reaction", "confidence": seg["confidence"]})
            last_seen[target] = clock
            clock += 1
            cursor = reaction_start + dur_ms
        out.append({**seg, "start_ms": cursor})
    return [seg for seg in out if seg["end_ms"] > seg["start_ms"]]


def apply_locks(segments: list[dict[str, Any]], locks: list[dict[str, int]],
                cameras: list[str], duration_ms: int) -> list[dict[str, Any]]:
    """Manual locks override every rule. Segments are split at lock boundaries."""
    if not locks:
        return segments
    for lock in locks:
        if not 0 <= lock["camera_index"] < len(cameras):
            raise ValueError(f"lock camera index out of range: {lock['camera_index']}")
        if lock["start_ms"] >= duration_ms:
            raise ValueError(f"lock starts after media ends: {lock['start_ms']}ms")
    out: list[dict[str, Any]] = []
    for seg in segments:
        spans = [dict(seg)]
        for lock in locks:
            next_spans: list[dict[str, Any]] = []
            for span in spans:
                lock_start = max(span["start_ms"], lock["start_ms"])
                lock_end = min(span["end_ms"], lock["end_ms"])
                if lock_end <= lock_start:
                    next_spans.append(span)
                    continue
                if span["start_ms"] < lock_start:
                    next_spans.append({**span, "end_ms": lock_start})
                next_spans.append({**span, "start_ms": lock_start, "end_ms": lock_end,
                                   "camera_id": cameras[lock["camera_index"]],
                                   "reason": "manual-lock", "confidence": None})
                if span["end_ms"] > lock_end:
                    next_spans.append({**span, "start_ms": lock_end})
            spans = next_spans
        out.extend(spans)
    out.sort(key=lambda span: span["start_ms"])
    merged: list[dict[str, Any]] = []
    for seg in out:
        if merged and merged[-1]["camera_id"] == seg["camera_id"] and merged[-1]["reason"] == seg["reason"]:
            merged[-1]["end_ms"] = seg["end_ms"]
        else:
            merged.append(seg)
    return merged


def build_angles(utterances: list[dict[str, Any]], speaker_map: dict[str, str],
                 cameras: list[str], duration_ms: int, wide_camera: str | None,
                 min_hold_ms: int, no_repeat_ms: int, reaction_every_ms: int,
                 reaction_dur_ms: int, locks: list[dict[str, int]]) -> list[dict[str, Any]]:
    segments = active_speaker_segments(utterances, speaker_map, duration_ms, wide_camera)
    segments = enforce_min_hold(segments, min_hold_ms, wide_camera)
    segments = apply_no_repeat(segments, no_repeat_ms, wide_camera)
    segments = insert_reactions(segments, cameras, speaker_map, reaction_every_ms,
                                reaction_dur_ms, min_hold_ms, wide_camera)
    segments = apply_locks(segments, locks, cameras, duration_ms)
    return segments


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a reviewable multi-camera Angles plan")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--audio", help="Mixed master audio/video for diarization")
    source.add_argument("--transcript", help="Existing diarized raw-elevenlabs.json")
    parser.add_argument("--clips", required=True, help="Comma-separated camera files in camera order")
    parser.add_argument("--speaker-map", action="append", default=[],
                        help="Required mapping, e.g. speaker_0=0,speaker_1=1")
    parser.add_argument("--assume-order", action="store_true")
    parser.add_argument("--sync-offset", action="append", default=[])
    parser.add_argument("--wide-angle", help="Camera index for overlap/short/unknown turns")
    parser.add_argument("--num-speakers", type=int)
    parser.add_argument("--use-multi-channel", action="store_true")
    parser.add_argument("--keyterm", action="append", default=[])
    parser.add_argument("--lang", default="th")
    parser.add_argument("--min-hold-ms", type=int, default=1500)
    parser.add_argument("--bridge-gap-ms", type=int, default=350)
    parser.add_argument("--no-repeat-ms", type=int, default=4000,
                        help="Suppress returning to the just-left camera within this window (0=off)")
    parser.add_argument("--reaction-every-ms", type=int, default=0,
                        help="Cut to a listener camera during monologues longer than this (0=off)")
    parser.add_argument("--reaction-dur-ms", type=int, default=1500)
    parser.add_argument("--lock", action="append", default=[],
                        help="Manual override: start-end=camera_index (repeatable)")
    parser.add_argument("--corrections", help="JSON file of review corrections: [{start_ms,end_ms,camera_index}]")
    parser.add_argument("--fps", default="30/1")
    parser.add_argument("--output-dir")
    parser.add_argument("--out")
    args = parser.parse_args()

    clips = [str(Path(c.strip()).expanduser().resolve()) for c in args.clips.split(",") if c.strip()]
    if len(clips) < 2:
        parser.error("--clips needs at least two camera files")
    for clip in clips:
        if not Path(clip).is_file():
            parser.error(f"camera file not found: {clip}")
    if args.num_speakers is not None and not 1 <= args.num_speakers <= 32:
        parser.error("--num-speakers must be 1-32")

    root = kit_root()
    stem = Path(args.audio or args.transcript).stem
    output_dir = (Path(args.output_dir).expanduser().resolve() if args.output_dir
                  else root / "projects/_work/analyze" / f"{stem}-angles")
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        locks = parse_locks(args.lock)
        if args.corrections:
            corrections = json.loads(Path(args.corrections).read_text(encoding="utf-8"))
            locks = parse_locks([f"{c['start_ms']}-{c['end_ms']}={c['camera_index']}"
                                 for c in corrections]) if corrections else locks

        transcript_path = ensure_transcript(args, root, output_dir)
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        words = transcript_words(transcript)
        if not words:
            raise ValueError("transcript has no speaker-tagged words")
        speakers = list(dict.fromkeys(w["speaker_id"] for w in words))
        mapping_raw = parse_map(args.speaker_map)
        if args.assume_order:
            for i, speaker in enumerate(speakers):
                mapping_raw.setdefault(speaker, str(min(i, len(clips) - 1)))
        missing = [speaker for speaker in speakers if speaker not in mapping_raw]
        if missing and args.wide_angle is None:
            raise ValueError("speaker-camera mapping required for: " + ", ".join(missing))
        speaker_map: dict[str, str] = {}
        for speaker, raw_camera in mapping_raw.items():
            idx = int(raw_camera)
            if idx < 0 or idx >= len(clips):
                raise ValueError(f"camera index out of range for {speaker}: {idx}")
            speaker_map[speaker] = f"cam_{idx}"
        cameras = [f"cam_{i}" for i in range(len(clips))]
        wide_camera = None
        if args.wide_angle is not None:
            wide_idx = int(args.wide_angle)
            if wide_idx < 0 or wide_idx >= len(clips):
                raise ValueError("--wide-angle camera index out of range")
            wide_camera = f"cam_{wide_idx}"

        offsets = parse_map(args.sync_offset, int)
        utterances = build_utterances(words, max(0, args.bridge_gap_ms))
        transcript_duration_ms = max(w["end_ms"] for w in words)
        master_duration_ms = probe_duration_ms(str(Path(args.audio).expanduser().resolve())) if args.audio else None
        duration_ms = max(transcript_duration_ms, master_duration_ms or 0)

        segments = build_angles(utterances, speaker_map, cameras, duration_ms, wide_camera,
                                max(1, args.min_hold_ms), max(0, args.no_repeat_ms),
                                max(0, args.reaction_every_ms), max(1, args.reaction_dur_ms), locks)

        fps_num, sep, fps_den = args.fps.partition("/")
        if not sep:
            fps_den = "1"
        if int(fps_num) <= 0 or int(fps_den) <= 0:
            raise ValueError("fps must be positive")
        sources = []
        for i, clip in enumerate(clips):
            sources.append({"id": f"cam_{i}", "path": clip,
                            "sync_offset_ms": int(offsets.get(str(i), 0)),
                            "duration_ms": probe_duration_ms(clip)})
        by_id = {source["id"]: source for source in sources}
        for segment in segments:
            info = by_id[segment["camera_id"]]
            if segment["start_ms"] + info["sync_offset_ms"] < 0:
                raise ValueError(f"sync offset makes {segment['camera_id']} start before source media")
            if info["duration_ms"] and segment["end_ms"] + info["sync_offset_ms"] > info["duration_ms"]:
                raise ValueError(f"{segment['camera_id']} is too short for the requested sync offset")

        switches = [{"index": i, "start_ms": seg["start_ms"], "end_ms": seg["end_ms"],
                     "camera_id": seg["camera_id"], "speaker_id": seg["speaker_id"],
                     "reason": seg["reason"], "confidence": seg["confidence"]}
                    for i, seg in enumerate(segments)]
        review_path = output_dir / "angles-switches.json"
        review_path.write_text(json.dumps({
            "hint": "review each switch; to correct, re-run with --lock start-end=camera_index "
                    "(or --corrections file of [{start_ms,end_ms,camera_index}]) - locks always win",
            "cameras": {source["id"]: source["path"] for source in sources},
            "switches": switches,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        input_paths = clips + ([str(Path(args.audio).expanduser().resolve())] if args.audio else [str(transcript_path)])
        plan = {
            "schema_version": "1.0",
            "plan_id": str(uuid.uuid4()),
            "feature": "angles",
            "source_fingerprint": media_fingerprint(input_paths),
            "timebase": {"fps_num": int(fps_num), "fps_den": int(fps_den), "drop_frame": False},
            "sources": sources,
            "analysis": {
                "transcript": str(transcript_path),
                "mode": "multi_channel" if transcript.get("multi_channel") else "diarization",
                "speakers": speakers,
                "speaker_map": speaker_map,
                "rules": {"min_hold_ms": args.min_hold_ms, "no_repeat_ms": args.no_repeat_ms,
                          "reaction_every_ms": args.reaction_every_ms,
                          "reaction_dur_ms": args.reaction_dur_ms,
                          "locks": locks, "wide_camera": wide_camera},
                "sync_offset_convention": "source_ms = timeline_ms + sync_offset_ms",
            },
            "operations": [{
                "id": "angle-switches",
                "type": "switch_angles",
                "master_audio": str(Path(args.audio).expanduser().resolve()) if args.audio else None,
                "segments": segments,
            }],
            "review": {"required": True, "warnings": [],
                       "switch_list": str(review_path)},
            "safety": {"mode": "new_timeline", "expected_source": None},
        }
        out_path = Path(args.out).expanduser().resolve() if args.out else output_dir / "edit-plan.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "plan": str(out_path), "switch_list": str(review_path),
                          "speakers": speakers, "segments": len(segments),
                          "reasons": sorted({seg["reason"] for seg in segments}),
                          "review_required": True}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
