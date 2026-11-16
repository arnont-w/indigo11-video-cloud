#!/usr/bin/env python3
from __future__ import annotations
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any


def kit_root() -> Path:
    configured = os.environ.get("INDIGO11_KIT_ROOT")
    if configured and Path(configured).expanduser().is_dir():
        return Path(configured).expanduser().resolve()
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "CLAUDE.md").exists() and (parent / ".claude" / "skills").is_dir():
            return parent
    return here.parent


def media_fingerprint(paths: list[str]) -> str:
    h = hashlib.sha256()
    for raw in paths:
        p = Path(raw).expanduser().resolve()
        st = p.stat()
        h.update(str(p).encode())
        h.update(str(st.st_size).encode())
        h.update(str(st.st_mtime_ns).encode())
    return h.hexdigest()


def parse_map(values: list[str], cast_value=str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw in values:
        for pair in raw.split(","):
            if not pair.strip():
                continue
            key, sep, value = pair.partition("=")
            if not sep or not key.strip() or not value.strip():
                raise ValueError(f"invalid mapping: {pair!r}")
            result[key.strip()] = cast_value(value.strip())
    return result


def participant_id(word: dict[str, Any]) -> str | None:
    if word.get("speaker_id") is not None:
        return str(word["speaker_id"])
    if word.get("channel_index") is not None:
        return f"channel_{int(word['channel_index'])}"
    return None


def transcript_words(data: dict[str, Any]) -> list[dict[str, Any]]:
    source = data.get("words_raw") or data.get("words") or []
    words = []
    for item in source:
        if item.get("type", "word") != "word":
            continue
        who = participant_id(item)
        if who is None:
            continue
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            continue
        words.append({
            "speaker_id": who,
            "start_ms": round(start * 1000),
            "end_ms": round(end * 1000),
            "text": str(item.get("text", "")),
            "logprob": item.get("logprob"),
        })
    words.sort(key=lambda w: (w["start_ms"], w["end_ms"]))
    return words


def build_utterances(words: list[dict[str, Any]], bridge_gap_ms: int) -> list[dict[str, Any]]:
    by_speaker: dict[str, list[dict[str, Any]]] = {}
    for word in words:
        utterances = by_speaker.setdefault(word["speaker_id"], [])
        if utterances and word["start_ms"] - utterances[-1]["end_ms"] <= bridge_gap_ms:
            cur = utterances[-1]
            cur["end_ms"] = max(cur["end_ms"], word["end_ms"])
            cur["text"] += word["text"]
            if isinstance(word.get("logprob"), (int, float)):
                cur["logprobs"].append(float(word["logprob"]))
        else:
            utterances.append({
                "speaker_id": word["speaker_id"],
                "start_ms": word["start_ms"],
                "end_ms": word["end_ms"],
                "text": word["text"],
                "logprobs": [float(word["logprob"])] if isinstance(word.get("logprob"), (int, float)) else [],
            })
    utterances = [utterance for speaker in by_speaker.values() for utterance in speaker]
    utterances.sort(key=lambda item: (item["start_ms"], item["end_ms"], item["speaker_id"]))
    for utterance in utterances:
        probs = utterance.pop("logprobs")
        utterance["confidence"] = round(math.exp(sum(probs) / len(probs)), 4) if probs else None
    return utterances


def build_segments(utterances: list[dict[str, Any]], speaker_map: dict[str, str],
                   duration_ms: int, min_hold_ms: int, lead_ms: int,
                   wide_camera: str | None) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    boundaries = sorted({0, duration_ms, *(u["start_ms"] for u in utterances), *(u["end_ms"] for u in utterances)})
    raw: list[dict[str, Any]] = []
    for start_ms, end_ms in zip(boundaries, boundaries[1:]):
        if end_ms <= start_ms:
            continue
        active = [u for u in utterances if u["start_ms"] < end_ms and u["end_ms"] > start_ms]
        active_speakers = list(dict.fromkeys(u["speaker_id"] for u in active))
        overlap = len(active_speakers) > 1
        if overlap and wide_camera is not None:
            camera = wide_camera
            speaker_id = "+".join(active_speakers)
            confidence = min((u["confidence"] for u in active if u["confidence"] is not None), default=None)
        elif active:
            chosen = max(active, key=lambda u: (u["start_ms"], u["end_ms"]))
            speaker_id = chosen["speaker_id"]
            camera = speaker_map.get(speaker_id)
            confidence = chosen["confidence"]
            if overlap:
                warnings.append(f"overlap at {start_ms}ms has no wide camera; using {speaker_id}")
            if camera is None:
                if wide_camera is None:
                    raise ValueError(f"missing camera mapping for {speaker_id}")
                camera = wide_camera
                warnings.append(f"{speaker_id} is unmapped; using {wide_camera}")
        elif raw:
            camera = raw[-1]["camera_id"]
            speaker_id = raw[-1]["speaker_id"]
            confidence = raw[-1]["confidence"]
        else:
            first = utterances[0]
            speaker_id = first["speaker_id"]
            camera = speaker_map.get(speaker_id) or wide_camera
            confidence = first["confidence"]
            if camera is None:
                raise ValueError(f"missing camera mapping for {speaker_id}")
        if end_ms - start_ms < min_hold_ms and raw and camera != raw[-1]["camera_id"]:
            if wide_camera is not None:
                camera = wide_camera
            else:
                warnings.append(f"short turn by {speaker_id} at {start_ms}ms kept on previous camera")
                camera = raw[-1]["camera_id"]
        if raw and raw[-1]["camera_id"] == camera:
            raw[-1]["end_ms"] = end_ms
        else:
            raw.append({"start_ms": start_ms, "end_ms": end_ms, "camera_id": camera,
                        "speaker_id": speaker_id, "confidence": confidence, "overlap": overlap})
    if not raw:
        raise ValueError("no speaker switches could be built")
    for index in range(1, len(raw)):
        shifted = max(raw[index - 1]["start_ms"] + 1, raw[index]["start_ms"] - lead_ms)
        if shifted < raw[index]["end_ms"]:
            raw[index - 1]["end_ms"] = shifted
            raw[index]["start_ms"] = shifted
    segments: list[dict[str, Any]] = []
    for segment in raw:
        if segments and segments[-1]["camera_id"] == segment["camera_id"]:
            segments[-1]["end_ms"] = segment["end_ms"]
        elif segment["end_ms"] > segment["start_ms"]:
            segments.append(segment)
    for segment in segments:
        if segment["confidence"] is not None and segment["confidence"] < 0.6:
            warnings.append(
                f"low confidence {segment['speaker_id']} at {segment['start_ms']}ms"
            )
    return segments, list(dict.fromkeys(warnings))


def probe_duration_ms(path: str) -> int | None:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    try:
        seconds = float(result.stdout.strip())
    except (TypeError, ValueError):
        return None
    return round(seconds * 1000) if math.isfinite(seconds) and seconds > 0 else None


def ensure_transcript(args: argparse.Namespace, root: Path, output_dir: Path) -> Path:
    if args.transcript:
        return Path(args.transcript).expanduser().resolve()
    transcript_dir = output_dir / "transcript"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    raw_path = transcript_dir / "raw-elevenlabs.json"
    if raw_path.exists():
        try:
            cached = json.loads(raw_path.read_text(encoding="utf-8"))
            if cached.get("diarized") or cached.get("multi_channel"):
                return raw_path
        except (OSError, ValueError):
            pass
    transcribe = root / ".claude/skills/indigo11-transcribe/scripts/transcribe.py"
    if not transcribe.exists():
        transcribe = Path(__file__).resolve().with_name("transcribe.py")
    if not transcribe.exists():
        raise RuntimeError("transcribe.py not found")
    cmd = [sys.executable, str(transcribe), args.audio, "--provider", "elevenlabs",
           "--lang", args.lang, "--save-all", str(transcript_dir)]
    if args.use_multi_channel:
        cmd.append("--use-multi-channel")
    else:
        cmd.append("--diarize")
        if args.num_speakers:
            cmd += ["--num-speakers", str(args.num_speakers)]
    for term in args.keyterm:
        cmd += ["--keyterm", term]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0 or not raw_path.exists():
        raise RuntimeError((result.stderr or result.stdout or "transcription failed")[-800:])
    return raw_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a host-neutral speaker-to-camera Podcast edit plan")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--audio", help="Mixed master audio/video for diarization")
    source.add_argument("--transcript", help="Existing diarized raw-elevenlabs.json")
    parser.add_argument("--clips", required=True, help="Comma-separated camera files in camera order")
    parser.add_argument("--speaker-map", action="append", default=[],
                        help="Required mapping, e.g. speaker_0=0,speaker_1=1")
    parser.add_argument("--assume-order", action="store_true",
                        help="Explicitly map first-seen speaker order to camera order")
    parser.add_argument("--sync-offset", action="append", default=[],
                        help="Camera sync offset, e.g. 0=0,1=842 (milliseconds)")
    parser.add_argument("--wide-angle", help="Camera index used for short/unknown/overlap turns")
    parser.add_argument("--num-speakers", type=int)
    parser.add_argument("--use-multi-channel", action="store_true")
    parser.add_argument("--keyterm", action="append", default=[])
    parser.add_argument("--lang", default="th")
    parser.add_argument("--min-hold-ms", type=int, default=1500)
    parser.add_argument("--bridge-gap-ms", type=int, default=350)
    parser.add_argument("--lead-ms", type=int, default=180)
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
    if args.audio and not Path(args.audio).expanduser().is_file():
        parser.error(f"audio file not found: {args.audio}")
    if args.transcript and not Path(args.transcript).expanduser().is_file():
        parser.error(f"transcript not found: {args.transcript}")
    if args.num_speakers is not None and not 1 <= args.num_speakers <= 32:
        parser.error("--num-speakers must be 1-32")

    root = kit_root()
    stem = Path(args.audio or args.transcript).stem
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else root / "projects/_work/analyze" / f"{stem}-podcast"
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
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
        segments, warnings = build_segments(
            utterances, speaker_map, duration_ms, max(1, args.min_hold_ms),
            max(0, args.lead_ms), wide_camera,
        )
        fps_num, sep, fps_den = args.fps.partition("/")
        if not sep:
            fps_den = "1"
        if int(fps_num) <= 0 or int(fps_den) <= 0:
            raise ValueError("fps must be positive")
        sources = []
        for i, clip in enumerate(clips):
            source_duration_ms = probe_duration_ms(clip)
            sources.append({
                "id": f"cam_{i}",
                "path": clip,
                "sync_offset_ms": int(offsets.get(str(i), 0)),
                "duration_ms": source_duration_ms,
            })
        by_id = {source["id"]: source for source in sources}
        for segment in segments:
            source_info = by_id[segment["camera_id"]]
            source_start = segment["start_ms"] + source_info["sync_offset_ms"]
            source_end = segment["end_ms"] + source_info["sync_offset_ms"]
            if source_start < 0:
                raise ValueError(f"sync offset makes {segment['camera_id']} start before source media")
            if source_info["duration_ms"] and source_end > source_info["duration_ms"]:
                raise ValueError(f"{segment['camera_id']} is too short for the requested sync offset")
        input_paths = clips + ([str(Path(args.audio).expanduser().resolve())] if args.audio else [str(transcript_path)])
        plan = {
            "schema_version": "1.0",
            "plan_id": str(uuid.uuid4()),
            "feature": "podcast",
            "source_fingerprint": media_fingerprint(input_paths),
            "timebase": {"fps_num": int(fps_num), "fps_den": int(fps_den), "drop_frame": False},
            "sources": sources,
            "analysis": {
                "transcript": str(transcript_path),
                "mode": "multi_channel" if transcript.get("multi_channel") else "diarization",
                "speakers": speakers,
                "speaker_map": speaker_map,
                "utterances": utterances,
                "sync_offset_convention": "source_ms = timeline_ms + sync_offset_ms",
            },
            "operations": [{
                "id": "podcast-switches",
                "type": "switch_angles",
                "master_audio": str(Path(args.audio).expanduser().resolve()) if args.audio else None,
                "segments": segments,
            }],
            "review": {"required": True, "warnings": warnings},
            "safety": {"mode": "new_timeline", "expected_source": None},
        }
        out_path = Path(args.out).expanduser().resolve() if args.out else output_dir / "edit-plan.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "ok": True,
            "plan": str(out_path),
            "speakers": speakers,
            "speaker_map": speaker_map,
            "segments": len(segments),
            "review_required": True,
            "warnings": warnings,
        }, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
