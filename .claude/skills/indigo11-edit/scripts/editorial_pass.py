#!/usr/bin/env python3
"""Validate and refine an editorial EDL before rendering.

The editorial agent produces an EDL (which take ranges to keep). This script:
  1. Verifies no segment overlap
  2. Verifies end-pad doesn't bleed into the next dropped take
     (this is the bug that causes 'ขั้น ขั้น' double-hear - speaker said the
      word twice across two takes, and a too-generous end-pad on the earlier
      kept segment can include the start of the NEXT dropped take.)
  3. Adjusts end_ms to be just before the next dropped take starts
  4. Trims each segment's end back to the last REAL character + 150ms, when the
     transcript carries a char-level stream (`words_raw`). ElevenLabs stretches the
     FINAL character of a phrase across the trailing silence - measured up to 2.1s on
     one char - so an end taken from the phrase's own timing drags dead air into every
     kept segment. This never extends a segment, only pulls a too-late end earlier.

Usage:
  python3 editorial_pass.py <edl.json> <transcript.json> [-o <fixed-edl.json>]

Both inputs:
  edl.json: {"segments": [{"start_ms", "end_ms"}, ...]}
  transcript.json: {"words": [{"start", "end", "text"}, ...]} (ElevenLabs shape)
"""
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)

import argparse
import json
import sys

import os as _os_utf8, sys as _sys_utf8
if _os_utf8.name == "nt":  # cp874 console crashes printing Thai (rule #32)
    for _s in (_sys_utf8.stdout, _sys_utf8.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")


def validate_no_overlap(segments):
    """Check editorial segments are chronological and non-overlapping."""
    issues = []
    for i in range(1, len(segments)):
        if segments[i]["start_ms"] < segments[i-1]["end_ms"]:
            issues.append(
                f"seg {i} starts at {segments[i]['start_ms']}ms but seg {i-1} ends at "
                f"{segments[i-1]['end_ms']}ms - overlap of "
                f"{segments[i-1]['end_ms'] - segments[i]['start_ms']}ms"
            )
    return issues


def find_dropped_words(segments, words):
    """Return list of word dicts that fall ENTIRELY inside a dropped range."""
    kept_ranges = [(s["start_ms"]/1000, s["end_ms"]/1000) for s in segments]

    def in_kept(t_sec):
        for ks, ke in kept_ranges:
            if ks <= t_sec < ke:
                return True
        return False

    dropped = []
    for w in words:
        # word is dropped if its midpoint is not in any kept range
        mid = (w["start"] + w["end"]) / 2
        if not in_kept(mid):
            dropped.append(w)
    return dropped


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


def trim_stretched_tails(segments, words_raw, pad_ms=TAIL_PAD_MS):
    """Pull each segment's end back to (last real character + pad_ms).

    Returns (segments, fixes). No-op when words_raw is empty. Never extends a segment.
    Char timings are SECONDS per edit-rules #14.
    """
    if not words_raw:
        return segments, []
    out, fixes = [], []
    for i, seg in enumerate(segments):
        a_s, b_s = seg["start_ms"] / 1000.0, seg["end_ms"] / 1000.0
        anchored = real_speech_end_ms(words_raw, a_s, b_s)
        if anchored is None:
            out.append(seg)
            continue
        new_end = anchored - TAIL_PAD_MS + pad_ms
        if new_end < seg["end_ms"]:
            new_end = max(seg["start_ms"] + 1, new_end)
            fixes.append({"seg_index": i, "old_end_ms": seg["end_ms"],
                          "new_end_ms": new_end, "saved_ms": seg["end_ms"] - new_end})
            out.append({**seg, "end_ms": new_end})
        else:
            out.append(seg)
    return out, fixes


def trim_end_pads(segments, dropped_words, min_gap_ms=20):
    """For each segment, if its end_ms reaches into a dropped word, trim it
    back to just before the dropped word starts (minus min_gap_ms safety)."""
    fixed = []
    fixes = []
    for i, seg in enumerate(segments):
        new_end = seg["end_ms"]
        # next dropped word that starts AFTER segment start and could be touched
        for w in dropped_words:
            w_start_ms = int(w["start"] * 1000)
            # only care about dropped words that START between seg.end and seg.end + 500ms
            # (i.e. ones we might have accidentally padded into)
            if seg["start_ms"] < w_start_ms < new_end + 500:
                # if our end is past this dropped word's start, trim
                if new_end > w_start_ms:
                    new_end = w_start_ms - min_gap_ms
                    fixes.append({
                        "seg_index": i,
                        "old_end_ms": seg["end_ms"],
                        "new_end_ms": new_end,
                        "trimmed_ms": seg["end_ms"] - new_end,
                        "dropped_word": w["text"],
                        "word_start_ms": w_start_ms,
                    })
        # Preserve every other field the subagent emitted - `beat`, `reason`,
        # `clip_index`, ... A 2-key rebuild silently deletes the rule #36 narrative
        # audit trail on its way to edl-rough-safe.json, which is the file downstream
        # skills read.
        fixed.append({**seg, "end_ms": new_end})
    return fixed, fixes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("edl", help="editorial EDL JSON (from subagent)")
    ap.add_argument("transcript", help="transcript JSON with word timings")
    ap.add_argument("-o", "--output", help="write fixed EDL (default: stdout)")
    ap.add_argument("--min-gap-ms", type=int, default=20,
                    help="minimum gap between seg end and next dropped word (default 20ms)")
    args = ap.parse_args()

    with open(args.edl, encoding="utf-8") as f:
        edl = json.load(f)
    with open(args.transcript, encoding="utf-8") as f:
        trans = json.load(f)

    segments = edl["segments"]
    words = trans["words"]

    # 1. Check overlap
    overlap_issues = validate_no_overlap(segments)
    if overlap_issues:
        print("WARNING: segment overlap issues:", file=sys.stderr)
        for issue in overlap_issues:
            print(f" {issue}", file=sys.stderr)
        print(" (these will cause audio duplication when rendered)\n", file=sys.stderr)

    # 2. Find dropped words
    dropped = find_dropped_words(segments, words)
    print(f"editorial decision: keep {len(words) - len(dropped)} words, drop {len(dropped)}",
          file=sys.stderr)

    # 3. Trim end pads that bleed into dropped takes
    fixed_segs, fixes = trim_end_pads(segments, dropped, args.min_gap_ms)
    if fixes:
        print(f"\nFIXED {len(fixes)} end-pad bleed issue(s):", file=sys.stderr)
        for fix in fixes:
            print(f" seg {fix['seg_index']}: end {fix['old_end_ms']} → {fix['new_end_ms']} "
                  f"(was bleeding into dropped word '{fix['dropped_word']}' "
                  f"at {fix['word_start_ms']}ms)", file=sys.stderr)
    else:
        print("\nNo end-pad bleed issues found.", file=sys.stderr)

    # 4. Trim tails stretched by the STT's final character
    fixed_segs, tail_fixes = trim_stretched_tails(fixed_segs, trans.get("words_raw") or [])
    if tail_fixes:
        saved = sum(f["saved_ms"] for f in tail_fixes)
        print(f"\nTRIMMED {len(tail_fixes)} stretched tail(s), recovered {saved/1000:.2f}s "
              f"of dead air:", file=sys.stderr)
        for fix in tail_fixes:
            print(f" seg {fix['seg_index']}: end {fix['old_end_ms']} → {fix['new_end_ms']} "
                  f"(-{fix['saved_ms']}ms)", file=sys.stderr)
    elif trans.get("words_raw"):
        print("\nNo stretched tails found.", file=sys.stderr)
    else:
        print("\nWARNING: transcript has no char-level `words_raw`, so the stretched-tail "
              "trim (kit rule #36) did NOT run. Segment ends keep whatever the STT reported, "
              "which on some providers includes up to seconds of trailing silence. "
              "ElevenLabs supplies words_raw; re-transcribe with it if the cut feels loose.",
              file=sys.stderr)

    # Output fixed EDL
    out = {**edl, "segments": fixed_segs}
    out_json = json.dumps(out, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out_json)
        print(f"\nwrote fixed EDL to {args.output}", file=sys.stderr)
    else:
        print(out_json)


if __name__ == "__main__":
    main()
