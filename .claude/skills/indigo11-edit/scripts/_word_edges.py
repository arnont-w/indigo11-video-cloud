#!/usr/bin/env python3
"""_word_edges.py - where the WORD boundaries are, from the STT's own timing.

One helper, used by both `compute_edl.py` (which warns at cut time) and `qc_edit.py`
(which fails an edit). They disagreed before, so a correct cut could be reported as a
defect - and an agent told to iterate until the check passes will happily undo it.

Why this exists at all: Thai has no spaces, so a CHARACTER edge is not a word edge.
Two boundaries measured on real footage look identical in the character stream:

    5.159s   "...ก็คือ|มันจะต้องเอาไป"   between words - a clean cut
    7.279s   "...เอาไปคลุ|กกับอาหาร"     inside คลุก  - chops the word

Six attempts to tell those apart with character heuristics failed. The answer was
already in the kit: nlpo3 (rule #12) segments Thai, and every character carries its
own ElevenLabs timing, so a word's start is simply its first character's start.
That is the same path `indigo11-transcribe/word_segment.align_words` uses for karaoke.

Deliberately NOT a trust marker on the segment. A caller could set "I verified this",
and an agent looping until the checker passes would set it on anything. The checker
computes the answer itself or admits it cannot.

CLI (so non-Python hosts run the SAME code instead of reimplementing it):

    python _word_edges.py --transcript t.json --at 7.28 --at 26.10 [--window a b]

(`python`, not `python3` - on Windows `python3` is the Microsoft Store stub and a buyer
copy-pasting a doc line gets "Python was not found". Node callers use `pythonExe()`.)

prints {"engine": "nlpo3", "results": [{"t_s": 7.28, "at_edge": false}, ...]}.
`at_edge` is true / false / null, where null means "could not determine" - a host
must surface that as a WARN, never as a defect (rule #41: an agent told to iterate
until PASS will undo a correct edit to satisfy an invented failure).

The NLE host skills MUST invoke this with `pythonExe()`, never `vadPython()`:
the Silero venv has no nlpo3/pythainlp, so the VAD interpreter answers null to
everything and silently disables the guard (audit A risk 3).
"""
from __future__ import annotations
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)

_ENGINE = None


def _engine() -> str:
    """'nlpo3' when available (rule #12 prefers it), else newmm, else '' if neither."""
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    try:
        import nlpo3  # noqa: F401
        import pythainlp
        from pathlib import Path
        try:
            nlpo3.load_dict(
                str(Path(pythainlp.__file__).parent / "corpus" / "words_th.txt"), "default")
        except Exception:
            pass  # already loaded
        _ENGINE = "nlpo3"
    except Exception:
        try:
            import pythainlp  # noqa: F401
            _ENGINE = "newmm"
        except Exception:
            _ENGINE = ""
    return _ENGINE


def _is_spoken(tok) -> bool:
    """A character that was actually voiced - not an audio-event tag or whitespace."""
    if tok.get("type") == "audio_event":
        return False
    t = (tok.get("text") or "").strip()
    return bool(t) and not t.startswith("[")


def word_edges(transcript: dict, win_start_s: float, win_end_s: float) -> list:
    """Timestamps where a word begins or ends, inside [win_start_s, win_end_s].

    Returns [] when the transcript has no character stream or no segmenter is
    installed - callers must treat that as "unknown", never as "bad".
    """
    chars = [c for c in (transcript.get("words_raw") or [])
             if _is_spoken(c) and c.get("start") is not None and c.get("end") is not None
             and c["start"] >= win_start_s - 0.05 and c["end"] <= win_end_s + 0.05]
    if not chars:
        return []
    eng = _engine()
    if not eng:
        return []
    from pythainlp.tokenize import word_tokenize
    text = "".join(c["text"] for c in chars)
    try:
        words = word_tokenize(text, engine=eng, keep_whitespace=False)
    except Exception:
        return []

    # Map each word back to the character stream by CHARACTER COUNT, not by entry
    # count: one ElevenLabs entry can hold several Python characters, because a Thai
    # consonant arrives with its vowel and tone marks attached ('สิ่' is one entry and
    # three characters). Counting entries drifts and loses boundaries - it put the
    # 'ก็คือ|มัน' edge at 5.42 instead of 5.159.
    #
    # So walk the entries, tracking how many characters have been consumed, and take
    # each word's start from the entry the word BEGINS in.
    starts, consumed = [], 0
    for c in chars:
        starts.append((consumed, c["start"], c["end"]))
        consumed += len(c["text"])

    edges, pos = [], 0
    for w in words:
        entry = next((e for e in starts if e[0] <= pos < e[0] + 1 or e[0] == pos), None)
        if entry is None:
            entry = min(starts, key=lambda e: abs(e[0] - pos))
        edges.append(entry[1])
        pos += len(w)
        # the word's end is the start of whatever follows it
        nxt = next((e for e in starts if e[0] >= pos), None)
        edges.append(nxt[1] if nxt else chars[-1]["end"])
    return sorted(set(edges))


def at_word_edge(transcript: dict, t_s: float, win_start_s: float, win_end_s: float,
                 tol_s: float = 0.06):
    """Is t_s at a word boundary?  True / False / None when it cannot be determined.

    None means no character stream or no segmenter - report that honestly rather than
    guessing, since both answers are wrong in a different way.
    """
    edges = word_edges(transcript, win_start_s, win_end_s)
    if not edges:
        return None
    return any(abs(t_s - e) <= tol_s for e in edges)


def _phrase_window(transcript: dict, t_s: float):
    """The phrase containing t_s, so a CLI caller need not know the phrase list.

    Falls back to a ±3s window when no phrase contains it - enough context for the
    segmenter without pulling the whole transcript through the tokenizer.
    """
    for p in transcript.get("words") or []:
        s, e = p.get("start"), p.get("end")
        if s is not None and e is not None and s < t_s < e:
            return s, e
    return t_s - 3.0, t_s + 3.0


def main(argv=None) -> int:
    """CLI seam for the non-Python NLE hosts. See the module docstring."""
    import argparse
    import json
    import os
    import sys

    ap = argparse.ArgumentParser(description="word-boundary check for cut points")
    ap.add_argument("--transcript", required=True,
                    help="transcript json with words[] + words_raw[]")
    ap.add_argument("--at", action="append", type=float, default=[],
                    help="boundary time in SECONDS (repeatable)")
    ap.add_argument("--window", nargs=2, type=float, metavar=("START_S", "END_S"),
                    help="force the segmenter window (default: the containing phrase)")
    ap.add_argument("--tol-ms", type=float, default=60.0,
                    help="how close to an edge still counts (default 60ms)")
    args = ap.parse_args(argv)

    if os.name == "nt":  # rule #32: Thai text on a cp874 console raises otherwise
        for st in (sys.stdout, sys.stderr):
            if hasattr(st, "reconfigure"):
                st.reconfigure(encoding="utf-8", errors="replace")

    try:
        with open(args.transcript, encoding="utf-8") as fh:
            tr = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        print(json.dumps({"ok": False, "error": f"cannot read transcript: {e}"},
                         ensure_ascii=False))
        return 1

    results = []
    for t in args.at:
        w = tuple(args.window) if args.window else _phrase_window(tr, t)
        results.append({"t_s": t, "window": [w[0], w[1]],
                        "at_edge": at_word_edge(tr, t, w[0], w[1],
                                                tol_s=args.tol_ms / 1000.0)})
    print(json.dumps({"ok": True, "engine": _engine() or None, "results": results},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())


def in_silence(tr: dict, t: float, min_gap_s: float = 0.25) -> bool:
    """True when `t` sits in a pause of at least `min_gap_s` between two spoken
    characters. A character's end is capped at 300 ms (rule #36), because the STT
    stretches the last one across the silence that follows it."""
    chars = [(c["start"], min(c["end"], c["start"] + 0.30)) for c in (tr.get("words_raw") or [])
             if (c.get("text") or "").strip() and not (c.get("text") or "").strip().startswith("[")
             and c.get("type", "word") == "word"]
    if not chars:
        return False
    before = [e for s0, e in chars if e <= t + 0.02]
    after = [s0 for s0, e in chars if s0 >= t - 0.02]
    if any(s0 + 0.02 < t < e - 0.02 for s0, e in chars):
        return False
    return (min(after) if after else float("inf")) - (max(before) if before else float("-inf")) >= min_gap_s
