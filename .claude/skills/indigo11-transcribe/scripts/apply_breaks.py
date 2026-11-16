#!/usr/bin/env python3
"""
apply_breaks.py - The agent decides WHERE a caption breaks; this script does the timing.

Pass 2 used to ask the agent to hand-edit a words.json: move `timeline_end_ms`, recompute
token `start`/`end`, keep captions abutting. That is arithmetic, and an agent doing
arithmetic on 700 captions drops timing or drifts the karaoke beat. So the split is now
(edit-rules #18):

  agent  - reads each phrase and writes the chunks, as plain text. Meaning only.
  script - checks that not one character went missing, then derives every start/end from
           the same char-level timing word_segment.py uses.

Measured on IMG_6894 (blind review, 2 judges): fixed-N 32-37% broken captions,
rule-based word_segment 15-17%, agent breaks through this script 6%.

Usage:
  python3 word_segment.py raw.json --phrases-out phrases.json      # the agent's input
  python3 apply_breaks.py raw.json breaks.json -o words.json [--words-per-group N]

breaks.json (the agent writes this; ids are the phrase ids from phrases.json):
  {"words_per_group": 2, "chunks": {
     "0": ["โอเค"],
     "1": ["ผมเพิ่ง", "เดินทาง", "จากเชียงใหม่"],
     "2": ["ผมไม่", {"src": "รักพลู", "text": "rug pull"}, "หรอกนะครับ"]}}

A chunk is a string, or {"src", "text"} when the caption must SHOW something other than
what the transcript holds: `src` is the transcript's characters (that is where the timing
comes from), `text` is what goes on screen. `text: ""` drops the chunk (a stutter).

The contract: for every phrase, the chunks' source characters joined with spaces removed
must equal the phrase text with spaces removed. A phrase that fails it is NOT guessed at:
it falls back to word_segment's rule-based captions and is listed under `fallback`, so
the output is always complete and the caller can see exactly what went wrong. Exit code
2 when more than half the phrases fell back - a bad breaks file, not a few slips.
"""
from __future__ import annotations
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

import word_segment as ws


def _src(chunk) -> Optional[str]:
    if isinstance(chunk, str):
        return chunk
    if isinstance(chunk, dict) and isinstance(chunk.get("src"), str) and isinstance(chunk.get("text"), str):
        return chunk["src"]
    return None


def chunk_groups(tokens: List[dict], chunks: list) -> List[List[dict]]:
    """Cut one phrase's timed tokens at the agent's chunk boundaries.

    Offsets are counted in space-stripped characters. A boundary that lands inside a
    token (the tokenizer joined what the agent separated, or split a name the agent
    kept whole) splits that token there, timing interpolated by character count."""
    spans, pos = [], 0
    for t in tokens:
        n = len(t["text"].replace(" ", ""))
        spans.append((pos, pos + n, t))
        pos += n
    groups, a = [], 0
    for ch in chunks:
        b = a + len(_src(ch).replace(" ", ""))
        part = []
        for s, e, t in spans:
            lo, hi = max(a, s), min(b, e)
            if hi <= lo:
                continue
            if lo == s and hi == e:
                part.append(dict(t))
                continue
            dur = t["end"] - t["start"]
            part.append({"text": t["text"][lo - s:hi - s],
                         "start": t["start"] + dur * (lo - s) / (e - s),
                         "end": t["start"] + dur * (hi - s) / (e - s),
                         "sp": t.get("sp") and lo == s})
        a = b
        if not part:
            continue
        if isinstance(ch, dict):                 # the caption shows `text`, timed by `src`
            if not ch["text"].strip():
                continue
            part = [{"text": ch["text"].strip(), "start": part[0]["start"], "end": part[-1]["end"],
                     "sp": part[0].get("sp", False)}]
        groups.append(part)
    return groups


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("raw_json", help="raw-elevenlabs.json (the same file phrases.json was made from)")
    ap.add_argument("breaks_json", help="the agent's chunks per phrase id")
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--words-per-group", type=int,
                    help="target size, used for the rule-based fallback and the too-long check "
                         "(default: breaks.json words_per_group, else 2)")
    ap.add_argument("--engine", default="nlpo3", choices=["newmm", "nlpo3"])
    ap.add_argument("--custom-dict", default="")
    ap.add_argument("--no-gap-close", action="store_true")
    ap.add_argument("--partial", action="store_true",
                    help="breaks.json covers only SOME phrases on purpose (e.g. the ones that survive a "
                         "cut). A phrase with no entry gets the rule-based cut and is counted under "
                         "`rule_based`, not reported as a failure")
    args = ap.parse_args()

    raw = ws.load_raw(args.raw_json)
    breaks = json.loads(Path(args.breaks_json).read_text(encoding="utf-8"))
    chunks_by_id = breaks.get("chunks", breaks) if isinstance(breaks, dict) else {}
    n = args.words_per_group or (breaks.get("words_per_group") if isinstance(breaks, dict) else None) or 2
    custom = [w.strip() for w in args.custom_dict.split(",") if w.strip()] or None
    names = list(custom or []) + ws.proper_nouns(raw, args.raw_json)
    word_level = raw.get("timing_unit") == "word"

    caps: List[List[dict]] = []
    fallback: List[dict] = []
    rule_based = 0
    too_long: List[dict] = []
    max_chars = 8 * n + 6
    fixes: List[dict] = []
    phrases = ws.phrases_for(raw, args.engine, names, custom)
    for i, atoms in enumerate(phrases):
        tokens = ws.phrase_tokens(atoms, args.engine, names, custom, word_level)
        if not tokens:
            continue
        text = ws.phrase_text(atoms)
        chunks = chunks_by_id.get(str(i))
        reason = None
        if not isinstance(chunks, list) or any(_src(c) is None for c in chunks):
            reason = "missing"
        elif "".join(_src(c) for c in chunks).replace(" ", "") != text.replace(" ", ""):
            reason = "text changed"
        if reason:
            if args.partial and chunks is None:
                rule_based += 1
            else:
                fallback.append({"id": i, "reason": reason, "text": text})
            caps += ws.pack(ws.units_from(tokens), max(1, n))
            continue
        parts = chunk_groups(tokens, chunks)
        for part in parts:
            units = ws.units_from(part)
            words = sum(ws._cost(u) for u in units)
            shown = ws._join(part)
            # Over the word ceiling, or simply too much text for one flash. A single unit
            # (one long number, one name) cannot be split, so it is never reported.
            if len(units) > 1 and (words > n + 1 or len(shown) > max_chars):
                too_long.append({"id": i, "chunk": shown, "words": words, "chars": len(shown)})
        fixes += [{"id": i, "src": c["src"], "text": c["text"]} for c in chunks if isinstance(c, dict)]
        caps += parts

    floored = ws.enforce_floor(caps, ws.floor_for(n))
    groups = [ws._group(c) for c in floored]
    if not args.no_gap_close:
        groups = ws.gap_close(groups)
    out = {
        "duration": raw.get("audio_duration_secs") or raw.get("duration"),
        "language": raw.get("language") or raw.get("language_code"),
        "engine": args.engine,
        "segmenter": "agent-breaks",
        "words_per_group": n,
        "gap_closed": not args.no_gap_close,
        "phrase_count": len(phrases),
        "group_count": len(groups),
        "fallback": fallback,
        "rule_based": rule_based,
        "too_long": too_long,
        "text_fixes": fixes,
        "merged_short": len(caps) - len(floored),
        "groups": groups,
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    bad = len(fallback) * 2 > max(1, len(phrases))
    print(json.dumps({"ok": not bad, "phrases": len(phrases), "groups": len(groups),
                      "fallback_count": len(fallback), "fallback": fallback, "rule_based": rule_based,
                      "too_long_count": len(too_long), "too_long": too_long,
                      "text_fixes": len(fixes), "merged_short": len(caps) - len(floored),
                      "output": args.output}, ensure_ascii=False))
    return 2 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
