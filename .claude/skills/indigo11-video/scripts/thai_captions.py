#!/usr/bin/env python3
"""ElevenLabs alignment -> Thai caption cues

  python3 thai_captions.py voice.json cues.json [script.txt]

Method: with a script.txt, captions break at the PHRASE BOUNDARIES of the script (spaces / line breaks / ellipses),
because the writer already knows where each phrase ends; this is far more accurate than a tokenizer.
ElevenLabs gives very few pause marks (2 in 27 seconds once measured), so the voice alone is not enough: the sentence structure is needed.
A phrase longer than MAX_CHARS is split at a conjunction (never in the middle of a word or a number).
Without a script.txt it falls back to the pythainlp tokenizer (worse results).
For English use english_captions.py (same interface).
"""
import json, re, sys, pathlib

if __name__ == "__main__":   # run directly: use the starter's Python (pythainlp) like the other scripts
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from _starter import _env  # noqa: E402
    _env.needs("pythainlp")

MAX_CHARS = 20
MIN_PIECE = 8
MIN_CHARS = 6
SENT_GAP  = 0.12
# ===================================

STRIP_RE = re.compile(r"[\.]{2,}|…+|[\[\]\(\)]|[ฯ]")

SPLIT_AT = ["เพราะ", "แต่", "แล้ว", "จน", "ที่ไม่", "ที่", "ซึ่ง", "และ",
            "เลย", "ไป", "มา", "ให้", "จาก", "ของ", "กับ", "ใน", "ก็",
            "ต้อง", "จะ", "คือ", "เป็น"]

UNITS = {"ศูนย์": 0, "หนึ่ง": 1, "เอ็ด": 1, "สอง": 2, "ยี่": 2, "สาม": 3, "สี่": 4,
         "ห้า": 5, "หก": 6, "เจ็ด": 7, "แปด": 8, "เก้า": 9}
SCALES = {"สิบ": 10, "ร้อย": 100, "พัน": 1000, "หมื่น": 10000,
          "แสน": 100000, "ล้าน": 1000000}
NUMWORD = re.compile("(?:" + "|".join(
    sorted(list(UNITS) + list(SCALES), key=len, reverse=True)) + ")+")
_BARE_SCALE = set(SCALES)
_NOT_NUMBER = {"ร้อยเอ็ด"}


def thai_num_to_int(s):
    total, cur, i = 0, 0, 0
    order = sorted(list(UNITS) + list(SCALES), key=len, reverse=True)
    while i < len(s):
        for w in order:
            if s.startswith(w, i):
                if w in UNITS:
                    cur = UNITS[w]
                else:
                    sc = SCALES[w]
                    if sc == 1000000:
                        total = (total + cur) * sc if (total or cur) else sc
                    else:
                        total += max(cur, 1) * sc
                    cur = 0
                i += len(w)
                break
        else:
            return None
    return total + cur


def convert_numbers(text, min_value=10):
    """Convert spoken Thai number words in a phrase to digits (values from min_value up)."""
    def rep(m):
        g = m.group()
        if g in _BARE_SCALE or g in _NOT_NUMBER:
            return g                                  # "แสนอร่อย" = คำขยาย
        if not any(g.startswith(u) for u in UNITS):
            return g
        v = thai_num_to_int(g)
        return str(v) if (v is not None and v >= min_value) else g
    return NUMWORD.sub(rep, text)


def clean_alignment(align):
    """Drop audio tags; return (clean text, start and end time per character)."""
    ch = align["characters"]
    st = align["character_start_times_seconds"]
    en = align["character_end_times_seconds"]
    full = "".join(ch)
    drop = {i for m in re.finditer(r"\[[^\]]+\]", full) for i in range(*m.span())}
    keep = [i for i in range(len(ch)) if i not in drop]
    return ("".join(ch[i] for i in keep),
            [st[i] for i in keep], [en[i] for i in keep])


def _num_spans(p):
    """Character spans that are spoken numbers: never split inside them."""
    return [m.span() for m in NUMWORD.finditer(p) if len(m.group()) > 3]


def _inside_number(p, i):
    return any(a < i < b for a, b in _num_spans(p))


def word_bounds(p):
    """Word boundaries of a Thai phrase (pythainlp)."""
    try:
        from pythainlp.tokenize import word_tokenize
        out, acc = set(), 0
        for w in word_tokenize(p, engine="newmm"):
            acc += len(w)
            out.add(acc)
        return out
    except Exception:
        return set()


def split_phrase(p):
    """Split a phrase longer than MAX_CHARS at a conjunction or the nearest word boundary (never inside a number)."""
    if len(p) <= MAX_CHARS:
        return [p]
    bounds = word_bounds(p)
    best, best_score = None, -1
    for kw in SPLIT_AT:
        for m in re.finditer(re.escape(kw), p):
            i = m.start()
            if i < MIN_PIECE or len(p) - i < MIN_PIECE:
                continue
            if bounds and i not in bounds:
                continue
            if _inside_number(p, i):
                continue
            balance = 1 - abs(i - len(p) / 2) / (len(p) / 2)
            score = balance * 2 + (len(SPLIT_AT) - SPLIT_AT.index(kw)) / len(SPLIT_AT)
            if score > best_score:
                best, best_score = i, score
    if best is None:
        best = _word_boundary_near(p, len(p) // 2)
    if best is None or best <= 0 or best >= len(p) or _inside_number(p, best):
        return [p]
    return split_phrase(p[:best]) + split_phrase(p[best:])


def _word_boundary_near(p, target):
    """The word boundary nearest to a target position."""
    bounds = [b for b in sorted(word_bounds(p))
              if MIN_CHARS <= b <= len(p) - MIN_CHARS
              and not _inside_number(p, b)]
    if bounds:
        return min(bounds, key=lambda b: abs(b - target))
    return None if word_bounds(p) else target


def phrases_from_script(script_text):
    """Pull the phrases out of the script: spaces, line breaks and ellipses are phrase boundaries."""
    s = re.sub(r"\[[^\]]+\]", "", script_text)
    raw = [p.strip() for p in re.split(r"[\s\n]+|\.{2,}|…", s) if p.strip()]
    out = []
    for p in raw:
        if len(convert_numbers(p)) <= MAX_CHARS:
            out.append(p)
        else:
            out.extend(split_phrase(p))
    return out


def cues_from_script(align, script_text):
    """Map each phrase of the script back to the real timestamps of the alignment."""
    clean, kst, ken = clean_alignment(align)
    flat = re.sub(r"\s+", "", clean)
    idx_map = [i for i, c in enumerate(clean) if not c.isspace()]

    cues, pos = [], 0
    for ph in phrases_from_script(script_text):
        key = re.sub(r"\s+", "", STRIP_RE.sub("", ph))
        if not key:
            continue
        j = flat.find(key, pos)
        if j < 0:
            continue
        a, b = idx_map[j], idx_map[min(j + len(key) - 1, len(idx_map) - 1)]
        cues.append({"t": round(kst[a], 3), "e": round(ken[b], 3),
                     "n": 1, "txt": convert_numbers(ph.strip())})
        pos = j + len(key)

    out = []
    for c in cues:
        gap = c["t"] - out[-1]["e"] if out else 0
        if out and len(c["txt"]) < MIN_CHARS and gap < SENT_GAP and \
                len(out[-1]["txt"]) + len(c["txt"]) <= MAX_CHARS:
            out[-1] = {"t": out[-1]["t"], "e": c["e"], "n": 1,
                       "txt": out[-1]["txt"] + c["txt"]}
        else:
            out.append(c)

    for i in range(len(out) - 1):
        if out[i + 1]["t"] > out[i]["e"]:
            out[i]["e"] = out[i + 1]["t"]
    return out


def cues_from_alignment(align, script_text=None):
    if script_text:
        c = cues_from_script(align, script_text)
        if c:
            return c
    return _fallback(align)


def _fallback(align):
    """No script: use the tokenizer (worse results; use only when needed)."""
    from pythainlp.tokenize import word_tokenize
    clean, kst, ken = clean_alignment(align)
    words, pos, timed = word_tokenize(clean, engine="newmm"), 0, []
    for w in words:
        t = STRIP_RE.sub("", w).strip()
        if t:
            timed.append({"text": t, "start": kst[pos],
                          "end": ken[pos + len(w) - 1]})
        pos += len(w)
    cues, cur = [], []
    for w in timed:
        txt = "".join(x["text"] for x in cur)
        if cur and len(txt) + len(w["text"]) > MAX_CHARS:
            cues.append({"t": round(cur[0]["start"], 3),
                         "e": round(cur[-1]["end"], 3), "n": len(cur),
                         "txt": convert_numbers(txt)})
            cur = []
        cur.append(w)
    if cur:
        cues.append({"t": round(cur[0]["start"], 3), "e": round(cur[-1]["end"], 3),
                     "n": len(cur),
                     "txt": convert_numbers("".join(x["text"] for x in cur))})
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
          f" | {'from the script' if txt else 'from the tokenizer'}")
    for c in cues:
        print(f"  [{c['t']:5.2f}-{c['e']:5.2f}] {c['txt']}")
    return cues


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2],
         sys.argv[3] if len(sys.argv) > 3 else None)
