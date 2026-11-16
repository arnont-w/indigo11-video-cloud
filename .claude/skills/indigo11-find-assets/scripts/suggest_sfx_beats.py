#!/usr/bin/env python3
"""suggest_sfx_beats.py - deterministic "where should SFX go" helper.

The download side (find_assets.py) already answers WHICH sound. This answers WHERE
to place it, WITHOUT visual/motion analysis, frame extraction, indigo11-edit, or any
paid generation. It reads signals the kit ALREADY produced:

  - silence_ranges_ms[]  (Silero VAD, from indigo11-edit context.json)   -> phrase edges
  - transcript_words[]   (word-level ms timing)                          -> punchline / last word
  - OR, if neither exists, an on-the-fly ffmpeg audio-energy pass on --audio

Each emitted beat is a candidate SFX placement with a suggested generic query, so the
agent can hand the list straight to indigo11-edit add_sfx.py (--sfx-timing) or to an engine scene as cues
without guessing timings. This is the indigo11 answer to HyperFrames' storyboard `sfx:` cues:
same idea (place from the spoken structure, not from watching pixels), but sourced from
the transcript+VAD the kit computes for free.

Beat kinds (all from audio/text, never from motion):
  transition  - a speech->silence edge (a phrase ends): natural whoosh/riser spot
  accent      - the last word before a pause / an emphasized token: ding/pop/impact
  impact      - a long-silence -> speech onset (a beat drops back in): boom/impact

Usage:
  # from an analyze context.json (preferred - has VAD + words)
  python3 suggest_sfx_beats.py --context projects/_work/analyze/<name>/context.json
  # from a transcribe words.json
  python3 suggest_sfx_beats.py --words projects/_work/transcribe/<name>/words.json
  # from raw audio/video (no prior analysis) - one ffmpeg energy pass
  python3 suggest_sfx_beats.py --audio clip.mp4
  # tune density (default: at most 3 beats)
  python3 suggest_sfx_beats.py --context ctx.json --max 3

Output (stdout JSON):
  {"beats": [{"at_ms": 4200, "kind": "transition", "query": "whoosh",
              "reason": "phrase end", "confidence": 0.7}], "source": "vad+words"}

The agent still decides the final query per beat (a punchline may want "vine boom",
a slide "whoosh") - `query` here is a safe generic default, not a mandate. Placement
is deterministic; the creative call stays with the agent (edit-rules #18).
"""
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# --- generic, safe query per beat kind (agent may override) ------------------
DEFAULT_QUERY = {
    "transition": "whoosh",
    "accent": "pop",
    "impact": "impact",
}
# a silence longer than this before speech resumes = a real "drop back in" beat
LONG_SILENCE_MS = 700
# ignore micro-gaps (breaths) as phrase edges
MIN_PHRASE_GAP_MS = 250


def _force_utf8_stdio():
    if os.name == "nt":
        for s in (sys.stdout, sys.stderr):
            try:
                s.reconfigure(encoding="utf-8")
            except Exception:
                pass


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False))


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def beats_from_silence(silence_ranges, total_ms):
    """speech<->silence edges. Each range is a silence {start_ms,end_ms}; the START of
    a silence is a phrase END (transition), the END of a LONG silence is a beat drop
    (impact)."""
    beats = []
    for r in silence_ranges:
        s, e = int(r["start_ms"]), int(r["end_ms"])
        dur = e - s
        if dur < MIN_PHRASE_GAP_MS:
            continue
        # phrase ended right as this silence began -> transition sits ON the edge
        if s > 0:
            beats.append({"at_ms": s, "kind": "transition", "reason": "phrase end",
                          "confidence": 0.7})
        # a long pause that then resumes -> impact on the resume (skip trailing silence)
        if dur >= LONG_SILENCE_MS and e < total_ms - 50:
            beats.append({"at_ms": e, "kind": "impact", "reason": "beat resumes after pause",
                          "confidence": 0.75})
    return beats


def beats_from_words(words):
    """Accent the last word before a >=MIN gap (a spoken clause lands)."""
    beats = []
    ws = [w for w in words if "start_ms" in w and "end_ms" in w]
    ws.sort(key=lambda w: w["start_ms"])
    for i, w in enumerate(ws):
        nxt = ws[i + 1] if i + 1 < len(ws) else None
        gap = (nxt["start_ms"] - w["end_ms"]) if nxt else LONG_SILENCE_MS
        if gap >= MIN_PHRASE_GAP_MS:
            beats.append({"at_ms": int(w["end_ms"]), "kind": "accent",
                          "reason": f'clause lands on "{w.get("text", "").strip()}"',
                          "confidence": 0.6})
    return beats


def beats_from_audio(audio_path):
    """No transcript/VAD: TWO ffmpeg passes over the real audio (no model, no motion):

      1. silencedetect - the END of each silence is a new-phrase onset (transition).
      2. per-frame RMS (astats) - a frame whose loudness spikes well above the running
         median is a LOUD TRANSIENT in the source itself: a mouse click, a slap, a prop
         hit, a clap. This is exactly the "line up the SFX with the loud moment in the
         video" case - the transcript can't see these (they aren't words).

    Both are audio-only and cheap. Transients score higher (0.65) than plain onsets
    (0.5) because a real loud hit is a stronger SFX cue than a phrase boundary."""
    beats = []
    devnull = os.devnull

    # pass 1: silence boundaries -> onsets
    try:
        p1 = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(audio_path),
             "-af", "silencedetect=noise=-30dB:d=0.35", "-f", "null", devnull],
            capture_output=True, text=True)
        for line in p1.stderr.splitlines():
            if "silence_end:" in line:
                try:
                    t = float(line.split("silence_end:")[1].split("|")[0].strip())
                    beats.append({"at_ms": int(t * 1000), "kind": "transition",
                                  "reason": "phrase onset after quiet", "confidence": 0.5})
                except (IndexError, ValueError):
                    continue
    except Exception:
        pass

    # pass 2: per-frame RMS -> loud transients (clicks / hits) above the median
    try:
        p2 = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(audio_path),
             "-af", "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
             "-f", "null", devnull],
            capture_output=True, text=True)
        import re
        import statistics
        t_cur = None
        rows = []
        for line in p2.stderr.splitlines():
            m = re.search(r"pts_time:([0-9.]+)", line)
            if m:
                t_cur = float(m.group(1))
            m = re.search(r"RMS_level=(-?[0-9.]+)", line)
            if m and t_cur is not None:
                try:
                    rows.append((t_cur, float(m.group(1))))
                except ValueError:
                    pass
        if len(rows) >= 4:
            vals = [v for _, v in rows if v != float("-inf")]
            if vals:
                med = statistics.median(vals)
                # a transient = RMS jumps >=6 dB over the median AND over the prior frame
                prev = None
                for t, v in rows:
                    if v > med + 6 and (prev is None or v > prev + 4):
                        beats.append({"at_ms": int(t * 1000), "kind": "accent",
                                      "reason": "loud transient in source (click/hit)",
                                      "confidence": 0.65})
                    prev = v
    except Exception:
        pass

    return beats


def dedupe_and_rank(beats, max_beats):
    """Merge beats within 300ms (keep highest confidence), sort by time, cap to max."""
    beats.sort(key=lambda b: (b["at_ms"], -b["confidence"]))
    merged = []
    for b in beats:
        if merged and abs(b["at_ms"] - merged[-1]["at_ms"]) < 300:
            if b["confidence"] > merged[-1]["confidence"]:
                merged[-1] = b
            continue
        merged.append(b)
    # rank by confidence to pick the strongest N, then re-sort by time for output
    merged.sort(key=lambda b: -b["confidence"])
    merged = merged[:max_beats]
    merged.sort(key=lambda b: b["at_ms"])
    for b in merged:
        b["query"] = DEFAULT_QUERY.get(b["kind"], "pop")
    return merged


def main():
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(description="Suggest SFX placement beats from transcript/VAD (no motion, no cost).")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--context", help="indigo11-edit context.json (has VAD + words)")
    src.add_argument("--words", help="indigo11-transcribe words.json")
    src.add_argument("--audio", help="audio/video file - ffmpeg energy pass fallback")
    ap.add_argument("--max", type=int, default=3, help="max beats to emit (default 3 - it's a garnish)")
    args = ap.parse_args()

    beats = []
    source = ""
    total_ms = 0

    if args.context:
        ctx = load_json(args.context)
        sil = ctx.get("silence_ranges_ms") or []
        words = ctx.get("transcript_words") or []
        total_ms = max([r["end_ms"] for r in sil] + [w.get("end_ms", 0) for w in words] + [0])
        if sil:
            beats += beats_from_silence(sil, total_ms)
        if words:
            beats += beats_from_words(words)
        source = "+".join(filter(None, ["vad" if sil else "", "words" if words else ""])) or "empty"
        if not beats:
            emit({"beats": [], "source": source,
                  "note": "context.json had no silence_ranges_ms or transcript_words - run analyze with VAD, or pass --audio"})
            return

    elif args.words:
        words = load_json(args.words)
        # words.json may be a list or {"words":[...]} / {"tokens":[...]}
        if isinstance(words, dict):
            words = words.get("words") or words.get("tokens") or []
        beats += beats_from_words(words)
        source = "words"

    else:  # --audio
        if not Path(args.audio).exists():
            emit({"beats": [], "source": "audio", "error": f"file not found: {args.audio}"})
            sys.exit(1)
        beats += beats_from_audio(args.audio)
        source = "audio-energy"

    beats = dedupe_and_rank(beats, args.max)
    emit({"beats": beats, "source": source,
          "hint": "hand at_ms to indigo11-edit add_sfx.py --sfx-timing (or engine scene cues); override query per beat as the edit calls for it"})


if __name__ == "__main__":
    main()
