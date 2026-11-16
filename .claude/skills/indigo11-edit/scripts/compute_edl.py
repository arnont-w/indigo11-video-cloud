#!/usr/bin/env python3
"""Deterministic EDL computation from agent decisions.

Agent decides (semantic):
  - assembly_order: which clip in which slot
  - rough_decisions: word_range per clip to keep

Script computes (math):
  - source_ms ranges per clip
  - intersect with VAD speech ranges
  - timeline_ms cursor

This is the "AI decides, script computes" boundary per edit-rules #1.

Usage:
  python3 compute_edl.py --stage stage.json --decisions decisions.json -o final-edl.json

decisions.json shape:
  {
    "assembly_order": [5, 1, 0, 2, 3, 4],          # clip indices in slot order
    "rough_decisions": {
      "0": [[0, 0, "single phrase ok"]],            # [word_start_idx, word_end_idx, reason]
      "1": [[0, 2, "intro full"]],
      "3": [[0, 11, "main demo"]],
      ...
    },
    "do_silent": true                                # apply VAD intersect
  }

Word index is into the transcript's `words[]` array.
"""
from __future__ import annotations
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _capcut_lib import emit_err, emit_ok  # noqa: E402
from _word_edges import at_word_edge, in_silence  # noqa: E402


def intersect(a_start, a_end, b_start, b_end):
    s = max(a_start, b_start)
    e = min(a_end, b_end)
    return (s, e) if e > s else None


# edit-rules #14: char-level `start`/`end` are SECONDS (floats), as ElevenLabs returns them.
TAIL_PAD_MS = 150   # after real speech ends; measured against 12 human-made cuts (0902)
MAX_CHAR_MS = 300   # a spoken char above this is padded, not held (p95 = 200ms, p50 = 79ms)


def _is_real_char(tok) -> bool:
    """A character that was actually SPOKEN - not an audio-event tag or whitespace.

    ElevenLabs emits "[เสียงลม]" / "[MUSIC]" / "[เสียงเครื่องโกนหนวด]" tokens that carry
    timing but no speech, and they land at phrase ends - exactly where a tail anchor goes.
    """
    if tok.get("type") == "audio_event":
        return False  # authoritative flag from the STT (transcribe.py branches on it)
    t = (tok.get("text") or "").strip()
    return bool(t) and not t.startswith("[")  # fallback: bracketed convention


def _char_speech_end_s(tok) -> float:
    """When this character actually stopped sounding.

    ElevenLabs stretches the LAST character of a phrase across the trailing silence
    rather than emitting a separate gap: measured 'า' at 33.46 -> 36.68 (3.22s) closing
    "ซึ่งจะโกนขนได้เนียนกว่า". The character is real, so filtering by text cannot catch it -
    the tell is the DURATION. Across 969 chars of Thai speech: p50 79ms, p90 140ms,
    p95 200ms. Anything past MAX_CHAR_MS is padding, so we count only its first
    MAX_CHAR_MS as sound.
    """
    start, end = tok["start"], tok["end"]
    return min(end, start + MAX_CHAR_MS / 1000.0)


def real_speech_end_ms(words_raw, win_start_s: float, win_end_s: float):
    """End of real speech inside the window, in ms (already includes TAIL_PAD_MS).

    None when there is no char-level stream, so callers keep their existing end.

    Why not the phrase's own `end`: see _char_speech_end_s - it inherits the stretch.
    Why not VAD end minus a constant: VAD hears breath, room tone and the product itself
    (a running trimmer). On the 0902 clip a fixed -250ms from VAD end ate 3 real Thai
    characters and -400ms ate 13; this anchor ate none.
    """
    if not words_raw:
        return None
    ends = [
        _char_speech_end_s(c) for c in words_raw
        if _is_real_char(c) and c.get("start") is not None and c.get("end") is not None
        # Upper bound is EXCLUSIVE with no slack: a +0.05 tolerance let the first
        # character of the NEXT, DROPPED take become the anchor - the pad-bleed that
        # rule #4 exists to prevent (measured 100ms of a retake bleeding in).
        and c["start"] >= win_start_s - 0.05 and c["start"] < win_end_s
    ]
    if not ends:
        return None
    return int(max(ends) * 1000) + TAIL_PAD_MS


def true_speech_end_ms(transcript: dict, win_start_s: float, win_end_s: float):
    """Wrapper: pull the char stream off a transcript dict."""
    return real_speech_end_ms(transcript.get("words_raw") or [], win_start_s, win_end_s)


def anchor_ranges(transcript: dict, ranges: list) -> list:
    """Pull each range's END back to real speech + TAIL_PAD_MS. The tail-anchor seam.

    The non-Python NLE hosts need this exact arithmetic - it is what stops
    1-3s of dead air riding into every segment, because ElevenLabs stretches a phrase's
    FINAL character across the trailing silence instead of emitting a gap (measured: 'า'
    at 33.46 -> 36.68, 3.22s). Reimplementing it in JS would be a second set of answers to
    the same question; this is the same function `compute_edl` uses on the CapCut path.

    `ranges` is [{start_s, end_s}, ...]. Each result carries the anchored end plus what
    changed, so a caller can report the trim rather than silently moving a boundary:

        {start_s, end_s, original_end_s, trimmed_ms, anchored: bool}

    An end is only ever pulled EARLIER, never extended, and a transcript with no character
    stream leaves every end untouched (`anchored: false`) - the honest answer when the
    evidence to move it does not exist.
    """
    out = []
    for r in ranges:
        a = float(r.get("start_s", r.get("start", 0)))
        b = float(r.get("end_s", r.get("end", 0)))
        end_ms = int(b * 1000)
        true_end = true_speech_end_ms(transcript, a, b)
        anchored = true_end is not None and true_end < end_ms
        new_end_ms = max(int(a * 1000) + 1, true_end) if anchored else end_ms
        out.append({
            "start_s": a,
            "end_s": new_end_ms / 1000.0,
            "original_end_s": b,
            "trimmed_ms": end_ms - new_end_ms,
            "anchored": bool(anchored),
        })
    return out


def _cli_anchor(argv) -> int:
    """`--anchor` seam: read a transcript + ranges, print anchored ranges as JSON.

        python compute_edl.py --anchor --transcript t.json --ranges r.json

    (`python`, not `python3` - on Windows `python3` is the Store stub, rule #32. Node
    callers must use pythonExe(), never vadPython(): the Silero venv has no pythainlp.)
    """
    ap = argparse.ArgumentParser(description="tail-anchor ranges against a transcript")
    ap.add_argument("--anchor", action="store_true")
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--ranges", required=True,
                    help='json file: [{"start_s":..,"end_s":..}, ...] (or {"keep":[...]})')
    args = ap.parse_args(argv)

    if os.name == "nt":  # rule #32
        for st in (sys.stdout, sys.stderr):
            if hasattr(st, "reconfigure"):
                st.reconfigure(encoding="utf-8", errors="replace")
    try:
        with open(args.transcript, encoding="utf-8") as fh:
            tr = json.load(fh)
        with open(args.ranges, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 1
    ranges = raw.get("keep", raw) if isinstance(raw, dict) else raw
    res = anchor_ranges(tr, ranges)
    print(json.dumps({
        "ok": True,
        "has_char_stream": bool(tr.get("words_raw")),
        "keep": res,
        "anchored": sum(1 for r in res if r["anchored"]),
        "trimmed_ms_total": sum(r["trimmed_ms"] for r in res),
    }, ensure_ascii=False))
    return 0


def main() -> int:
    if "--anchor" in sys.argv[1:]:
        return _cli_anchor(sys.argv[1:])
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", required=True, help="stage.json from process_all.py")
    ap.add_argument("--decisions", required=True, help="decisions.json (agent-authored)")
    ap.add_argument("-o", "--output", required=True, help="final-edl.json path")
    args = ap.parse_args()

    stage = json.load(open(args.stage, encoding="utf-8"))
    decisions = json.load(open(args.decisions, encoding="utf-8"))

    assembly_order = decisions.get("assembly_order", list(range(len(stage["clips"]))))
    rough = decisions.get("rough_decisions", {})
    do_silent = decisions.get("do_silent", stage.get("do_silent", False))

    # Build per-slot segments
    segments = []
    for slot, clip_idx in enumerate(assembly_order):
        clip = stage["clips"][clip_idx]
        per = next(p for p in stage["per_clip"] if p["clip_index"] == clip_idx)
        transcript = json.load(open(per["transcript"], encoding="utf-8"))
        words = transcript.get("words", [])

        # rough decisions: keep word_index ranges (or full clip if no decision).
        #
        # A clip may appear MORE THAN ONCE in assembly_order - a single take often holds
        # both the best hook and the best CTA, and a human editor uses it twice. Address
        # the second use as "<clip>#<slot>" (e.g. "0#4") to give that slot its own ranges;
        # a bare "<clip>" key applies to the FIRST occurrence only. Without this, repeating
        # a clip duplicated ALL of its ranges into every slot, so the only way to move its
        # tail to the end was to drop it - measured on a real 4-take clip, that discarded
        # the only take carrying the 7-day result claim and a CTA naming the buy button.
        slot_key = f"{clip_idx}#{slot}"
        if slot_key in rough:
            ranges = rough[slot_key]
        elif slot == assembly_order.index(clip_idx):
            ranges = rough.get(str(clip_idx)) or [[0, max(0, len(words) - 1), "full clip"]]
        else:
            ranges = []  # repeat occurrence with no explicit ranges contributes nothing
        for r in ranges:
            # A range is normally [start_phrase, end_phrase, reason]. It may instead be
            # an OBJECT, for the case phrase indices cannot express: the speaker restarts
            # INSIDE one phrase ("...ก็คือมันจะต้องเอาไป" then says it again) and only
            # part of the phrase is wanted.
            #   {"phrase": 1, "trim_end_ms": 5159, "reason": "drop the trailing restart"}
            # Use it ONLY at a word boundary you have verified in words_raw. compute_edl
            # still reports the boundary in warnings[] if it lands mid-phrase, and that
            # warning is expected here - confirm from the character timings that the
            # point sits between two words before accepting it.
            trim_s = trim_e = None
            if isinstance(r, dict):
                # "phrase" alone trims one phrase; add "end_phrase" to trim the START of
                # a RUN. Needed because splitting a run into "trimmed phrase" + "plain
                # range" puts a seam inside continuous speech, which is audible even
                # when only 140ms is removed (see qc_edit's seam_in_speech).
                wstart = int(r["phrase"])
                wend = int(r.get("end_phrase", r["phrase"]))
                trim_s, trim_e = r.get("trim_start_ms"), r.get("trim_end_ms")
                reason = r.get("reason", "")
            else:
                wstart, wend = r[0], r[1]
                reason = r[2] if len(r) > 2 else ""
            if wstart >= len(words) or wend >= len(words):
                continue
            src_start_ms = int(words[wstart]["start"] * 1000)
            src_end_ms = int(words[wend]["end"] * 1000)
            if trim_s is not None:
                src_start_ms = max(src_start_ms, int(trim_s))
            if trim_e is not None:
                # A trim_end past the phrase run's own end is a mistake, not a wish:
                # it means the author meant to cover MORE phrases and forgot
                # `end_phrase`. Silently clamping deleted a whole product name from a
                # shipped edit (trim_end 10619 on a run ending 7100 kept only 7100).
                if int(trim_e) > src_end_ms + 50:
                    emit_err(
                        f"trim_end_ms {int(trim_e)} is past the end of phrase "
                        f"{wend} ({src_end_ms}ms) - add \"end_phrase\" to cover the "
                        f"phrases you meant to keep (ระบุ end_phrase ให้ครบช่วง)",
                        fix="set end_phrase to the last phrase in the run")
                    return 1
                src_end_ms = min(src_end_ms, int(trim_e))
            # Trim the STT's stretched trailing character back to real speech + 150ms.
            # Never EXTENDS the range - only pulls a too-late end earlier.
            true_end = true_speech_end_ms(transcript, words[wstart]["start"], words[wend]["end"])
            if true_end is not None and true_end < src_end_ms:
                src_end_ms = max(src_start_ms + 1, true_end)
            segments.append({
                "slot": slot, "clip_index": clip_idx, "clip_name": clip["name"],
                "clip_path": clip["path"],
                "source_start_ms": src_start_ms,
                "source_end_ms": src_end_ms,
                "word_range": [wstart, wend],
                "reason": reason,
            })

    # Visual-only keeps: ranges the agent chose from the FRAME GRIDS, where the value is
    # the picture (a product demo, a before/after, a reveal) and the transcript is silent.
    # They are appended as segments and are exempt from the VAD intersect below - which
    # would otherwise drop them entirely, since `any_overlap` is False for pure silence.
    # Rule #37 gave the agent eyes; without this it had no way to act on what it saw.
    for v in decisions.get("visual_keeps", []):
        clip_idx = v.get("clip_index", 0)
        clip = stage["clips"][clip_idx]
        slot = assembly_order.index(clip_idx) if clip_idx in assembly_order else 0
        segments.append({
            "slot": slot, "clip_index": clip_idx, "clip_name": clip["name"],
            "clip_path": clip["path"],
            "source_start_ms": int(v["start_ms"]),
            "source_end_ms": int(v["end_ms"]),
            "word_range": None,
            "visual_only": True,
            "reason": v.get("reason", "visual-only keep (no speech)"),
        })

    # Silent intersect
    if do_silent:
        intersected = []
        for s in segments:
            if s.get("visual_only"):
                intersected.append(s)  # exempt: its value is the picture, not the audio
                continue
            per = next(p for p in stage["per_clip"] if p["clip_index"] == s["clip_index"])
            if not per.get("vad"):
                # no VAD available → keep as-is
                intersected.append(s)
                continue
            speech = json.load(open(per["vad"], encoding="utf-8"))
            speech_ms = [{"start": int(r["start"]*1000), "end": int(r["end"]*1000)} for r in speech]
            try:
                tr = json.load(open(per["transcript"], encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                tr = {}

            # VAD hears SOUND, not words: a bag rustling or the speaker shifting in
            # the chair reads as speech. Normally the transcript keeps that in check,
            # but when the STT stretches one character over the pause that follows a
            # phrase - measured: 'ล' closing "...ได้เลย" ran 54.16-61.18, seven
            # seconds - that non-speech noise sits INSIDE a character's span and the
            # intersect happily keeps it as its own segment. On playback it is a
            # second of the speaker silently reaching for something.
            #
            # So require each surviving piece to contain a real, normally-timed
            # character. `chars` excludes audio-event tokens; the duration cap is the
            # same MAX_CHAR_MS the tail anchor uses, because a stretched character is
            # padding, not evidence that words are being spoken here.
            chars = [c for c in (tr.get("words_raw") or [])
                     if _is_real_char(c) and c.get("start") is not None
                     and c.get("end") is not None
                     and (c["end"] - c["start"]) <= MAX_CHAR_MS / 1000.0]

            def _has_words(a_ms, b_ms):
                if not chars:
                    return True  # no char stream (non-ElevenLabs) - keep old behaviour
                return any(c["start"] * 1000 < b_ms and c["end"] * 1000 > a_ms
                           for c in chars)

            any_overlap = False
            for sp in speech_ms:
                ov = intersect(s["source_start_ms"], s["source_end_ms"], sp["start"], sp["end"])
                if ov:
                    if not _has_words(ov[0], ov[1]):
                        continue  # sound without speech - a rustle, not a sentence
                    intersected.append({**s, "source_start_ms": ov[0], "source_end_ms": ov[1]})
                    any_overlap = True
            if not any_overlap:
                pass  # drop this segment entirely
        segments = intersected

    # Sort by slot then source_start (preserve assembly order)
    segments.sort(key=lambda x: (x["slot"], x["source_start_ms"]))

    # Merge adjacent same-clip segments with small gap
    merged = []
    GAP_MS = 50
    for s in segments:
        if (merged and merged[-1]["clip_index"] == s["clip_index"]
                and s["source_start_ms"] - merged[-1]["source_end_ms"] <= GAP_MS):
            # Keep the visual-only marker when a held shot merges into adjacent speech,
            # so the EDL still records WHY this segment runs past the last spoken word.
            if s.get("visual_only"):
                merged[-1]["holds_visual"] = True
                merged[-1]["reason"] = (merged[-1].get("reason", "") + " + " + s.get("reason", "")).strip(" +")
            # UNION, not overwrite. Segments are sorted by START, so a later segment can
            # END EARLIER than the one it merges into - a visual_keep that overlaps speech
            # produces exactly that order (speech 45.0-47.5, visual 45.0-53.5, speech
            # 48.5-49.06). Assigning would shrink the merged range back to 49.06 and drop
            # the held footage silently, while leaving holds_visual set.
            merged[-1]["source_end_ms"] = max(merged[-1]["source_end_ms"], s["source_end_ms"])
        else:
            merged.append({**s})

    # --- Safety net: refuse a cut that would be audibly broken -------------------
    # Two failure modes a buyer sees immediately, both from ranges that were chosen
    # by TIME rather than by phrase boundary (visual_keeps, or a hand-written EDL):
    #   * a boundary landing mid-word -> the word is chopped in half on playback
    #   * a kept range with (almost) no speech in it -> dead air in the middle
    # Report them instead of shipping silently; the agent can then re-pick the range.
    warnings = []
    for s in merged:
        per = next((p for p in stage["per_clip"] if p["clip_index"] == s["clip_index"]), None)
        if not per:
            continue
        try:
            tr = json.load(open(per["transcript"], encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        # A boundary is safe when it sits at a PHRASE edge. Landing inside a phrase
        # chops a word in half - and checking characters is not enough: the cut that
        # broke "คลุก" into "คลุ|ก" fell exactly on a character edge, so only the
        # phrase-level check catches it.
        phrases = tr.get("words") or []
        for edge, t_ms in (("start", s["source_start_ms"]), ("end", s["source_end_ms"])):
            t = t_ms / 1000.0
            if any(abs(t - p_["start"]) < 0.25 or abs(t - p_["end"]) < 0.25 for p_ in phrases):
                continue  # at (or next to) a phrase edge - fine
            inside = next((p_ for p_ in phrases if p_["start"] < t < p_["end"]), None)
            # A boundary inside a phrase is FINE at a word edge - that is how a doubled
            # word ("ข้อดีข้อดี") or a restart gets cut. Ask the segmenter; None means
            # it could not tell, and an unknown must not be reported as a defect.
            if inside is not None:
                # NOT `edge = ...`: that rebound the loop variable holding "start" /
                # "end", so the warning below printed "segment False 7.28s" instead of
                # naming the boundary the agent has to move.
                verdict = at_word_edge(tr, t, inside["start"], inside["end"])
                if verdict is True:
                    continue
                # ...and it is fine in a real SILENCE. The STT does not always write a
                # space where the speaker stopped: one "phrase" on IMG_6894 ran
                # 31.56-39.68 s with 5.7 s of nothing in the middle, and all 26 VAD
                # edges that landed in such pauses were reported as mid-word chops.
                if in_silence(tr, t):
                    continue
            # Not every boundary inside a phrase is wrong: the tail anchor deliberately
            # ends at real speech, and the STT stretches a phrase's last character over
            # the trailing silence (rule #36) - so an end that sits after the last
            # spoken character is CORRECT, not a chop. Only warn when speech continues
            # past the boundary, which is what actually cuts a word in half.
            if inside is not None:
                speech_after = any(
                    (c.get("text") or "").strip()
                    and not (c.get("text") or "").strip().startswith("[")
                    and c["start"] > t + 0.05 and c["start"] < inside["end"] - 0.05
                    and (c["end"] - c["start"]) < 0.35
                    for c in (tr.get("words_raw") or []))
                if not speech_after:
                    continue
                near = "".join(
                    c.get("text") or "" for c in (tr.get("words_raw") or [])
                    if abs(c["start"] - t) < 0.45)
                warnings.append(
                    f"segment {edge} {t:.2f}s falls INSIDE a phrase "
                    f"({inside['start']:.2f}-{inside['end']:.2f}) near {near!r} - this chops "
                    f"a word mid-sound. Move it to a phrase boundary.")
        if per.get("vad"):
            try:
                sp = json.load(open(per["vad"], encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                sp = []
            a, b = s["source_start_ms"] / 1000.0, s["source_end_ms"] / 1000.0
            voiced = sum(max(0.0, min(b, v["end"]) - max(a, v["start"])) for v in sp)
            if (b - a) > 0.8 and voiced < (b - a) * 0.35 and not s.get("visual_only") \
                    and not s.get("holds_visual"):
                warnings.append(
                    f"segment {a:.2f}-{b:.2f}s is {voiced / (b - a) * 100:.0f}% speech - "
                    f"mostly dead air; drop it or mark it as a visual_keep")

    # Compute timeline cursor
    cursor = 0
    for s in merged:
        dur = s["source_end_ms"] - s["source_start_ms"]
        s["duration_ms"] = dur
        s["timeline_start_ms"] = cursor
        s["timeline_end_ms"] = cursor + dur
        cursor += dur

    out = {
        "type": "final-edl",
        "total_duration_ms": cursor,
        "segments": merged,
        "warnings": warnings,
    }
    json.dump(out, open(args.output, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    emit_ok(
        op="compute-edl",
        segments=len(merged),
        total_duration_s=round(cursor / 1000, 3),
        output=args.output,
        warnings=warnings,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
