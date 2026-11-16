#!/usr/bin/env python3
"""ElevenLabs alignment -> caption cues in English (Standard British English).

  python3 english_captions.py voice.json cues.json [script.txt]

Same input and output as thai_captions.py, so make_short.py can use either:
  voice.json  = the ElevenLabs with-timestamps response (needs "alignment")
  cues.json   = [{"t": start_s, "e": end_s, "n": words, "txt": "caption text"}, ...]

Method: with a script, captions break at the phrase boundaries the writer chose (line breaks, sentence
ends, commas, ellipses) because the writer already knows where each phrase ends. A phrase that is too long
is split at its best joint (after a comma, before a conjunction, near the middle). Each phrase is then
matched to the real time of its letters in the ElevenLabs alignment. Without a script it falls back to
grouping spoken words.

English needs no word tokeniser: words are separated by spaces, so there is no extra Python package.
"""
import json, re, sys, pathlib

# ============ tune here ============
MAX_CHARS = 36     # a caption longer than this would overflow two lines on a 9:16 screen
MAX_WORDS = 6      # a caption holds at most this many words (reads comfortably in about 2 seconds)
MIN_CHARS = 10     # shorter than this = merge into its neighbour
SENT_GAP = 0.12    # a pause longer than this (seconds) = a new sentence, never merge across it

TAG_RE = re.compile(r"\[[^\]]+\]")                     # ElevenLabs audio tags such as [curious]
PAUSE_RE = re.compile(r"\.{2,}|…+")                    # "..." = a pause, not shown
END_RE = re.compile(r"[.!?]['\")\]]*$")                # ends a sentence
SOFT_END_RE = re.compile(r"[,;:–—-]$")        # a comma-like joint
CONNECT = {"and", "but", "because", "which", "that", "so", "or", "when", "while", "with", "until",
           "than", "if", "as", "where", "who", "whose", "before", "after", "since", "yet", "then"}


def clean_alignment(align):
    """Drop audio tags; return (clean text, start time per character, end time per character)."""
    ch = align["characters"]
    st = align["character_start_times_seconds"]
    en = align["character_end_times_seconds"]
    full = "".join(ch)
    drop = {i for m in TAG_RE.finditer(full) for i in range(*m.span())}
    keep = [i for i in range(len(ch)) if i not in drop]
    return "".join(ch[i] for i in keep), [st[i] for i in keep], [en[i] for i in keep]


def _norm(s):
    """Letters and digits only, lower case: tolerant of punctuation and spacing differences."""
    return re.sub(r"[^0-9a-zÀ-ɏ]", "", s.lower())


def _fits(words):
    return len(" ".join(words)) <= MAX_CHARS and len(words) <= MAX_WORDS


_WEAK_END = {"a", "an", "the", "of", "to", "in", "on", "at", "for", "my", "your", "our", "their", "his", "her", "its", "is", "are", "was", "were", "and", "or", "but"}


def _joint(left_last, right_first):
    """How good a place to break between two words (higher is better)."""
    s = 0.0
    if SOFT_END_RE.search(left_last):
        s += 1.5                               # after a comma / dash
    if right_first.lower().strip(",;:") in CONNECT:
        s += 1.0                               # before a conjunction
    if left_last.lower().strip(",;:") in _WEAK_END:
        s -= 2.0                               # never end a caption on an article, preposition or "is"
    return s


def split_phrase(words):
    """Split a word list that is too long into the fewest pieces that fit, choosing the best joints.

    Dynamic programming over the break positions: first minimise the number of captions, then prefer
    joints after commas or before conjunctions, then prefer pieces of similar length."""
    n = len(words)
    if _fits(words) or n < 2:
        return [words]
    INF = (10 ** 9, 0.0)
    best = [INF] * (n + 1)
    prev = [0] * (n + 1)
    best[0] = (0, 0.0)
    for i in range(1, n + 1):
        for j in range(i - 1, -1, -1):
            piece = words[j:i]
            if not _fits(piece):
                break                          # longer pieces cannot fit either
            if best[j] == INF:
                continue
            cost = _joint(words[j - 1], words[j]) if j > 0 else 0.0
            if len(piece) == 1 and n > 1:
                cost -= 1.0                    # avoid a one-word caption
            bal = (len(" ".join(piece)) / MAX_CHARS) ** 2 * 0.5
            cand = (best[j][0] + 1, best[j][1] - cost + bal)
            if cand < best[i]:
                best[i], prev[i] = cand, j
    if best[n] == INF:                         # one very long word: keep it whole
        return [words[:1]] + split_phrase(words[1:]) if n > 1 else [words]
    pieces, i = [], n
    while i > 0:
        pieces.append(words[prev[i]:i])
        i = prev[i]
    return pieces[::-1]


def phrases_from_script(script_text):
    """Phrases from the script: line breaks, sentence ends and ellipses are boundaries; long ones are split."""
    s = TAG_RE.sub("", script_text)
    out = []
    for block in re.split(r"[\n\r]+|\.{2,}|…", s):
        for sent in re.split(r"(?<=[.!?])\s+", block.strip()):
            words = sent.split()
            if not words:
                continue
            for piece in split_phrase(words):
                out.append(" ".join(piece))
    return out


def _display(txt):
    """What is shown on screen: no trailing full stop, comma, semicolon or colon (keep ? and !)."""
    return re.sub(r"[.,;:]+$", "", txt.strip())


def cues_from_script(align, script_text):
    """Map each script phrase back to the real timestamps in the alignment."""
    clean, kst, ken = clean_alignment(align)
    idx_map = [i for i, c in enumerate(clean) if _norm(c)]
    flat = "".join(_norm(clean[i]) for i in idx_map)
    cues, pos = [], 0
    for ph in phrases_from_script(script_text):
        key = _norm(PAUSE_RE.sub("", ph))
        if not key:
            continue
        j = flat.find(key, pos)
        if j < 0:                                  # not found: skip this phrase
            continue
        a, b = idx_map[j], idx_map[min(j + len(key) - 1, len(idx_map) - 1)]
        cues.append({"t": round(kst[a], 3), "e": round(ken[b], 3), "n": len(ph.split()), "raw": ph.strip()})
        pos = j + len(key)

    # merge very short phrases into a neighbour, but never across a sentence boundary
    out = []
    for c in cues:
        gap = c["t"] - out[-1]["e"] if out else 0
        if out and len(_display(c["raw"])) < MIN_CHARS and gap < SENT_GAP and not END_RE.search(out[-1]["raw"]) \
                and len(_display(out[-1]["raw"])) + 1 + len(_display(c["raw"])) <= MAX_CHARS \
                and out[-1]["n"] + c["n"] <= MAX_WORDS:
            out[-1] = {"t": out[-1]["t"], "e": c["e"], "n": out[-1]["n"] + c["n"], "raw": out[-1]["raw"] + " " + c["raw"]}
        else:
            out.append(c)

    for i in range(len(out) - 1):                  # gap-close so captions do not flicker
        if out[i + 1]["t"] > out[i]["e"]:
            out[i]["e"] = out[i + 1]["t"]
    return [{"t": c["t"], "e": c["e"], "n": c["n"], "txt": _display(c["raw"])} for c in out]


def cues_from_alignment(align, script_text=None):
    if script_text:
        c = cues_from_script(align, script_text)
        if c:
            return c
    return _fallback(align)


def _fallback(align):
    """No script: group the spoken words, breaking after sentence ends and at the size limits."""
    clean, kst, ken = clean_alignment(align)
    words, pos = [], 0
    for m in re.finditer(r"\S+", clean):
        words.append({"text": m.group(), "start": kst[m.start()], "end": ken[m.end() - 1]})
    cues, cur = [], []

    def flush():
        if cur:
            txt = " ".join(w["text"] for w in cur)
            cues.append({"t": round(cur[0]["start"], 3), "e": round(cur[-1]["end"], 3), "n": len(cur), "txt": _display(txt)})
            cur.clear()

    for w in words:
        if cur and (len(" ".join(x["text"] for x in cur)) + 1 + len(w["text"]) > MAX_CHARS or len(cur) >= MAX_WORDS):
            flush()
        cur.append(w)
        if END_RE.search(w["text"]):
            flush()
    flush()
    for i in range(len(cues) - 1):
        if cues[i + 1]["t"] > cues[i]["e"]:
            cues[i]["e"] = cues[i + 1]["t"]
    return cues


def main(src, dst, script=None):
    align = json.loads(pathlib.Path(src).read_text(encoding="utf-8"))["alignment"]
    txt = pathlib.Path(script).read_text(encoding="utf-8") if script else None
    cues = cues_from_alignment(align, txt)
    pathlib.Path(dst).write_text(json.dumps(cues, ensure_ascii=False, indent=1), encoding="utf-8")
    over = [c for c in cues if len(c["txt"]) > MAX_CHARS]
    print(f"{len(cues)} captions | longer than {MAX_CHARS} characters: {len(over)}"
          f" | {'from the script' if txt else 'from spoken words (no script)'}")
    for c in cues:
        print(f"  [{c['t']:5.2f}-{c['e']:5.2f}] {c['txt']}")
    return cues


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
