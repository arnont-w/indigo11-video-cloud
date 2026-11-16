#!/usr/bin/env python3
"""
word_segment.py - Turn a char-level (or word-level) STT transcript into Thai captions
of about N words each, with real per-word timing.

TIMING FIRST, WORDS SECOND. The order matters and the old version had it backwards:
it stripped every space and pause, ran the tokenizer over one long string, then cut
every N tokens. Measured on a real 14-minute Thai clip (IMG_6894) that produced:

  'ชื่อ' | 'อะ' | 'ไรผม' | 'อยาก'   <- 13 s of silence sat between อะไร and ผม, but the
                                     tokenizer never saw it and matched ไรผม (hairline)
  'นะ' | 'ครับผม' | 'มีสอน'        <- ครับ + the next sentence's ผม, 10 times
  'ครับแต่', 'บ้างผม'              <- the end of one sentence glued to the next
  'ก็ห้า' | 'ทุ่มครึ่ง'              <- a number torn from its unit

About one caption in three was broken (blind review, 2 judges). So now:

  1. PHRASES from timing: cut the char stream where the speaker actually stopped -
     a silence >= PAUSE_S, or a space the STT wrote between two Thai clauses. A space
     next to Latin/digits with no pause is just orthography ("ใช้ Claude Code") and
     stays inside the phrase.
  2. WORDS inside each phrase: nlpo3 (or the provider's own words when it returns
     real word-level units, e.g. Gemini). Words the dictionary gets wrong on a tie
     (บอกว่า -> บอ|กว่า) are protected at the text level before tokenizing.
  3. UNITS that never split: particle glued to the word before it (นะครับ), number
     with its classifier (ห้าทุ่มครึ่ง, 55 นาที).
  4. CAPTIONS of ~N words, packed inside a phrase only. Particles are free. A caption
     never ends on a clause starter (แต่, เพราะว่า), a short orphan rides with its
     neighbour, and N is a target, not a law.

The same phrase list is what the agent chunks by meaning in pass 2 - see
apply_breaks.py and indigo11-edit/references/post-process-protocol-v2.md.

Usage:
  python3 word_segment.py raw.json --output words.json
                                   [--words-per-group N]
                                   [--engine nlpo3|newmm]
                                   [--custom-dict word1,word2,brand1]
                                   [--phrases-out phrases.json]

Output (words.json):
  {
    "words_per_group": 2, "gap_closed": true, "segmenter": "timing-first",
    "groups": [
      {"start": 0.94, "end": 1.28, "text": "โอเค",
       "tokens": [{"text": "โอเค", "char_start": 0, "char_end": 4, "start": 0.94, "end": 1.24}]},
      ...
    ]
  }
"""
from __future__ import annotations
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)

import argparse
import json
import re
import sys
from pathlib import Path
from typing import List, Tuple, Dict, Optional

import os as _os_utf8, sys as _sys_utf8
if _os_utf8.name == "nt":  # cp874 console crashes printing Thai (rule #32)
    for _s in (_sys_utf8.stdout, _sys_utf8.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")


# A silence this long is a breath: a phrase boundary even when the STT wrote no space.
PAUSE_S = 0.30
# ...but a closing particle still belongs to its sentence across a breath this short.
PARTICLE_REACH_S = 0.80
# A caption on screen for less than this cannot be read (edit-rules #11).
MIN_ON_SCREEN_S = 0.20
# Word-by-word is a different promise. Thai words are spoken in about 0.2 s (median on a
# real clip), so the 200 ms floor merged HALF of a "1 word" order into two- and three-word
# captions - the buyer asked for one word and saw "ตอนนี้ห้าทุ่ม". Only a flash under three
# frames is joined to its neighbour there.
MIN_ON_SCREEN_ONE_WORD_S = 0.10


def floor_for(n: int) -> float:
    return MIN_ON_SCREEN_ONE_WORD_S if n == 1 else MIN_ON_SCREEN_S
# ElevenLabs stretches a phrase's final character across the trailing silence
# (edit-rules #36) and Grok does it to a lone tone mark for up to 6 s. No real
# Thai character lasts this long, so anything beyond the cap is hidden silence.
STRETCH_CAP_S = 0.30

# Sentence-final / politeness particles: never alone, never first in a caption.
PARTICLES = {
    "ครับ", "ค่ะ", "คะ", "ค่า", "คับ", "ครับผม", "ฮะ", "จ้ะ", "จ้า", "จ๊ะ",
    "นะ", "น่ะ", "นะครับ", "นะคะ", "อะ", "อ่ะ", "เนอะ", "เนี่ย", "สิ", "ซิ",
    "ล่ะ", "ละ", "หรอก", "วะ", "ว่ะ", "เหรอ", "หรอ", "มั้ย", "ไหม", "ๆ",
}
# Politeness particles close a clause: nothing may follow one inside a caption.
ENDERS = {"ครับ", "ค่ะ", "คะ", "ค่า", "คับ", "ครับผม", "นะครับ", "นะคะ"}
# Clause starters: a caption must not END on one.
STRONG_LEADERS = {"แต่", "เพราะ", "เพราะว่า", "ซึ่ง", "ถ้า", "แล้วก็", "ส่วน"}
WEAK_LEADERS = {"แล้ว", "และ", "หรือ"}
LEADERS = STRONG_LEADERS | WEAK_LEADERS
# Words that lean on what follows: a caption ending on one reads as cut off.
FORWARD = {"ที่", "ของ", "ใน", "เพื่อ", "จาก", "สำหรับ", "เกี่ยวกับ", "กับ", "โดย", "ตอน", "ถึง", "แบบ"}
# Nominalisers the dictionary sometimes leaves detached: การ+โกน, ความ+ฝัน.
PREFIXES = {"การ", "ความ"}
NUMERAL = re.compile(
    r"^(ศูนย์|หนึ่ง|เอ็ด|ยี่|สอง|สาม|สี่|ห้า|หก|เจ็ด|แปด|เก้า|สิบ|ร้อย|พัน|หมื่น|แสน|ล้าน|[0-9.,]+)+$")
CLASSIFIERS = {
    "นาที", "วินาที", "ชั่วโมง", "วัน", "เดือน", "ปี", "บาท", "คน", "ครั้ง", "ตัว", "อัน",
    "ชิ้น", "เปอร์เซ็นต์", "เปอร์เซนต์", "เปอร์", "ทุ่ม", "โมง", "ล้าน", "พัน", "หมื่น",
    "แสน", "ร้อย", "ครึ่ง", "%",
    "ดอลลาร์", "เหรียญ", "ยูโร", "เยน", "สตางค์", "เท่า", "รอบ", "ข้อ", "ขวบ", "สัปดาห์", "อาทิตย์",
    "เมตร", "กิโลเมตร", "เซนติเมตร", "กิโล", "กิโลกรัม", "กรัม", "ลิตร", "เมก", "กิก", "จิ๊ก",
}
# Common spoken words the dictionary splits wrong when a longer rare word ties with
# them. These are closed-class function words, not buyer vocabulary - brand names
# come from --custom-dict / the transcript's proper_nouns[] (edit-rules #38).
PROTECTED = ["อะไร"]
# ...and pairs it mis-cuts by one letter: X+ก+ว่า read as X + กว่า ("than").
RESPLIT = {("บอ", "กว่า"): ("บอก", "ว่า"), ("หรอ", "กว่า"): ("หรอก", "ว่า"),
           ("เย", "อะนะ"): ("เยอะ", "นะ"), ("อี", "กรอบ"): ("อีก", "รอบ"),
           ("เทพ", "เจ้าของ"): ("เทพเจ้า", "ของ")}
# `ว่า` after one of these belongs to it (บอกว่า, คิดว่า) - one unit.
SAY_VERBS = {"บอก", "คิด", "รู้", "รู้สึก", "เห็น", "ถือ", "เชื่อ", "แน่ใจ", "หมายความ", "เข้าใจ"}

_LATIN_OR_DIGIT = re.compile(r"[A-Za-z0-9]")
_LATIN_WORD = re.compile(r"^[A-Za-z][A-Za-z'\-]*$")
_THAI_COMBINING = re.compile(r"[ัิ-ฺ็-๎]")


def load_raw(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------- 1. phrases from timing ----------

def _entries(raw: dict) -> List[dict]:
    """The finest-grained timed stream the transcript has.

    `words_raw` = the provider's own units (ElevenLabs/Grok: characters, Gemini:
    words). `words` = the merged phrase-level fallback for providers without it."""
    return raw.get("words_raw") or raw.get("words") or []


def build_phrases(raw: dict, pause_s: float = PAUSE_S) -> List[List[dict]]:
    """Cut the timed stream into phrases. A phrase is a list of atoms
    {text, start, end, sp, tb}:
      sp = a visible orthographic space sits before this atom ("ใช้ Claude")
      tb = an invisible word boundary sits before it - the STT wrote a space the caption
           should not show ("ยี่สิบสาม คน แรก นะครับ" is one breath, written with three)"""
    src = _entries(raw)
    phrases: List[List[dict]] = []
    cur: List[dict] = []
    pending = ""                                 # "sp" | "tb" | ""

    def is_space(e: dict) -> bool:
        return e.get("type") == "spacing" or (e.get("text") or "").isspace()

    def is_break(e: dict) -> bool:
        return is_space(e) or e.get("type") == "audio_event"

    def run_after(i: int) -> str:
        out = ""
        for e in src[i + 1:i + 16]:
            if is_break(e):
                break
            out += e.get("text") or ""
        return out

    def run_before() -> str:
        out = ""
        for a in reversed(cur):
            out = a["text"] + out
            if a["sp"] or a["tb"]:
                break
        return out

    for i, e in enumerate(src):
        text = e.get("text") or ""
        if e.get("type") == "audio_event":      # [เสียงลม] is not speech
            if cur:
                phrases.append(cur)
            cur, pending = [], ""
            continue
        if is_space(e):
            prv = src[i - 1] if i and not is_break(src[i - 1]) else None
            nxt = src[i + 1] if i + 1 < len(src) and not is_break(src[i + 1]) else None
            if cur and prv and nxt and float(nxt["start"]) - _capped_end(prv) < pause_s:
                before, after = run_before(), run_after(i)
                if "ๆ" in (prv["text"], nxt["text"]):
                    continue                     # "ต่อ ๆ ไป" -> ต่อๆไป
                if _LATIN_OR_DIGIT.search(prv["text"]) or _LATIN_OR_DIGIT.search(nxt["text"]):
                    pending = "sp"               # "ใช้ Claude Code" stays one phrase
                    continue
                if (NUMERAL.match(before) or NUMERAL.match(after) or before in CLASSIFIERS
                        or after in CLASSIFIERS or after in PARTICLES):
                    pending = "tb"               # a number, its unit, a trailing นะครับ
                    continue
            if cur:
                phrases.append(cur)
            cur, pending = [], ""
            continue
        if not text:
            continue
        start = float(e.get("start", 0.0))
        end = min(float(e.get("end", start)), start + max(STRETCH_CAP_S, 0.12 * len(text)))
        by_pause = False
        if cur and start - cur[-1]["end"] >= pause_s:
            phrases.append(cur)                  # silence with no space written
            cur, pending, by_pause = [], "", True
        cur.append({"text": text, "start": start, "end": end, "sp": bool(cur) and pending == "sp",
                    "tb": bool(cur) and pending == "tb", "by_pause": by_pause})
        pending = ""
    if cur:
        phrases.append(cur)
    return phrases


def _capped_end(e: dict) -> float:
    start = float(e.get("start", 0.0))
    return min(float(e.get("end", start)), start + max(STRETCH_CAP_S, 0.12 * len(e.get("text") or "")))


def phrase_text(atoms: List[dict]) -> str:
    return "".join((" " if a["sp"] else "") + a["text"] for a in atoms)


def _runs(atoms: List[dict]) -> List[List[dict]]:
    """Split a phrase at its sp/tb boundaries: each run is tokenized on its own, because
    a space the STT wrote is a word boundary the dictionary must not reach across."""
    runs: List[List[dict]] = []
    for a in atoms:
        if not runs or a["sp"] or a.get("tb"):
            runs.append([])
        runs[-1].append(a)
    return runs


# ---------- 2. words inside a phrase ----------

_NLPO3_READY = False
_CUSTOM_TOKENIZERS: Dict[tuple, object] = {}


def segment(text: str, engine: str, custom_words: Optional[List[str]] = None) -> List[str]:
    """
    Segment Thai text into words.

    engine='nlpo3' - Rust dict-based, ~3x faster than newmm, same accuracy (default)
    engine='newmm' - pure-Python fallback, same dictionary
    """
    global _NLPO3_READY
    from pythainlp.tokenize import word_tokenize

    if custom_words:
        key = tuple(sorted(custom_words))
        if key not in _CUSTOM_TOKENIZERS:   # building the 62k-word trie costs ~1 s
            from pythainlp.tokenize import Tokenizer
            from pythainlp.corpus.common import thai_words
            _CUSTOM_TOKENIZERS[key] = Tokenizer(custom_dict=set(thai_words()) | set(custom_words),
                                                engine="newmm")
        words = _CUSTOM_TOKENIZERS[key].word_tokenize(text)
    elif engine == "nlpo3":
        try:
            import nlpo3
            if not _NLPO3_READY:
                import pythainlp
                dict_path = Path(pythainlp.__file__).parent / "corpus" / "words_th.txt"
                try:
                    nlpo3.load_dict(str(dict_path), "default")
                except Exception:
                    pass                        # already loaded
                _NLPO3_READY = True
            words = word_tokenize(text, engine="nlpo3", keep_whitespace=False)
        except ImportError:
            print("⚠ nlpo3 not installed. Run: pip install nlpo3", file=sys.stderr)
            print(" Falling back to newmm.", file=sys.stderr)
            words = word_tokenize(text, engine="newmm", keep_whitespace=False)
    else:
        words = word_tokenize(text, engine=engine, keep_whitespace=False)

    words = [w for w in words if w and not w.isspace()]
    out: List[str] = []
    for w in words:
        if out and (out[-1], w) in RESPLIT:
            out[-1], w = RESPLIT[(out[-1], w)]
        out.append(w)
    return out


def segment_protected(text: str, engine: str, names: List[str],
                      custom_words: Optional[List[str]] = None) -> List[str]:
    """Tokenize, but cut the text around protected words first so each stays whole.

    A dictionary add-on cannot do this: 'อะ'+'ไรผม' and 'อะไร'+'ผม' are both two
    dictionary words, and maximal matching breaks the tie the wrong way.

    `names` (buyer vocabulary) are trusted as given. The built-in PROTECTED words are
    only honoured where the tokenizer itself starts a word - 'เยอะไร้สาระ' contains the
    letters อะไร but no word begins there."""
    plain = segment(text, engine, custom_words)
    starts, pos = set(), 0
    for w in plain:
        pos = text.find(w, pos)
        starts.add(pos)
        pos += len(w)
    spans = []
    for w, trusted in ([(n, True) for n in sorted(set(names), key=len, reverse=True) if n]
                       + [(p, False) for p in PROTECTED]):
        for m in re.finditer(re.escape(w), text):
            a, b = m.span()
            if b < len(text) and _THAI_COMBINING.match(text[b]):
                continue                        # would cut a grapheme cluster
            if not trusted and a not in starts:
                continue
            if any(a < e and s < b for s, e in spans):
                continue
            spans.append((a, b))
    if not spans:
        return plain
    out, pos = [], 0
    for a, b in sorted(spans):
        if a > pos:
            out += segment(text[pos:a], engine, custom_words)
        out.append(text[a:b])
        pos = b
    if pos < len(text):
        out += segment(text[pos:], engine, custom_words)
    return [w for w in out if w and not w.isspace()]


def phrase_tokens(atoms: List[dict], engine: str, names: List[str],
                  custom_words: Optional[List[str]] = None, word_level: bool = False) -> List[dict]:
    """[{text, start, end, sp}] for one phrase; timing is the real char/word timing."""
    if word_level:                               # provider already returned Thai words
        toks = merge_compounds([dict(a) for a in atoms], engine, names, custom_words)
    else:
        toks = []
        for run in _runs(atoms):
            text, tm = "", []
            for a in run:
                for ch in a["text"]:
                    text += ch
                    tm.append((a["start"], a["end"]))
            i = 0
            for w in segment_protected(text, engine, names, custom_words):
                j = text.find(w, i)
                if j < 0:
                    continue
                toks.append({"text": w, "start": tm[j][0], "end": tm[j + len(w) - 1][1],
                             "sp": j == 0 and run[0]["sp"]})
                i = j + len(w)
    out: List[dict] = []
    for t in toks:
        if t["text"] == "ๆ" and out:             # repetition mark belongs to the word
            out[-1]["text"] += "ๆ"
            out[-1]["end"] = t["end"]
        else:
            out.append(t)
    return out


_THAI = re.compile(r"[\u0E00-\u0E7F]")
_DICT: Optional[set] = None


def _is_word(tok: str) -> bool:
    global _DICT
    if not _THAI.search(tok):
        return True
    if _DICT is None:
        from pythainlp.corpus.common import thai_words
        _DICT = set(thai_words())
    return tok in _DICT or tok in PROTECTED or tok in ("บอก", "หรอก")


def heal_pause_cuts(phrases: List[List[dict]], engine: str, names: List[str],
                    custom_words: Optional[List[str]] = None, word_level: bool = False) -> List[List[dict]]:
    """Undo a pause cut that landed INSIDE a word.

    A provider's timing can open a gap in the middle of a word (Grok put 0.3 s between
    ครั and บ). A cut is kept only when both sides of it are real words; otherwise the
    two halves are rejoined so the tokenizer sees the word whole. The caption can still
    break at that silence - pack() does it between units, never inside one."""
    out: List[List[dict]] = []
    for atoms in phrases:
        if out and atoms[0].get("by_pause") and not word_level:
            left = segment_protected(phrase_text(out[-1]), engine, names, custom_words)
            right = segment_protected(phrase_text(atoms), engine, names, custom_words)
            whole = (left and right and _is_word(left[-1]) and _is_word(right[0])
                     and not _THAI_COMBINING.match(atoms[0]["text"][0]))
            if not whole:
                out[-1] = out[-1] + atoms
                continue
        if out and atoms[0]["start"] - out[-1][-1]["end"] < PARTICLE_REACH_S:
            # "...เปอร์เซนต์ <breath> ครับ แต่ตอนนี้": the particle that opens a phrase closes
            # the sentence before it, as long as the breath was short.
            lead = 0
            for w in segment_protected(phrase_text(_runs(atoms)[0]), engine, names, custom_words):
                if w not in PARTICLES:
                    break
                lead += len(w)
            k, seen = 0, 0
            while k < len(atoms) and seen < lead:
                seen += len(atoms[k]["text"])
                k += 1
            if lead and seen == lead:
                out[-1] = out[-1] + [dict(atoms[0], sp=False, tb=True)] + atoms[1:k]
                atoms = [dict(atoms[k], sp=False, tb=False)] + atoms[k + 1:] if k < len(atoms) else []
                if not atoms:
                    continue
        out.append(atoms)
    return out


def merge_compounds(words: List[dict], engine: str, names: List[str],
                    custom_words: Optional[List[str]] = None) -> List[dict]:
    """Word-level providers split finer than a reader counts (เพิ่ม|เติม, เกี่ยว|กับ).
    Join provider words into a dictionary compound only when the compound's edges sit
    exactly on provider word edges - so the provider still wins every disagreement
    (it heard อะไร|ผม; the dictionary alone would say อะ|ไรผม)."""
    text = "".join(w["text"] for w in words)
    edges, pos = [], 0
    for w in words:
        edges.append(pos)
        pos += len(w["text"])
    index = {e: i for i, e in enumerate(edges)}
    out, i, pos = [], 0, 0
    for tok in segment_protected(text, engine, names, custom_words):
        a = text.find(tok, pos)
        b = a + len(tok)
        pos = b
        while i < len(words) and edges[i] < a:
            out.append(words[i])
            i += 1
        j = index.get(b, len(words) if b == len(text) else None)
        if i < len(words) and edges[i] == a and j is not None and j - i >= 2 \
                and not any(w["text"] in PARTICLES for w in words[i:j]):
            out.append({"text": tok, "start": words[i]["start"], "end": words[j - 1]["end"],
                        "sp": words[i].get("sp", False)})
            i = j
    out += words[i:]
    return out


# ---------- 3. units that never split ----------

def units_from(tokens: List[dict], pause_s: float = PAUSE_S) -> List[List[dict]]:
    units: List[List[dict]] = []
    for k, t in enumerate(tokens):
        x = t["text"]
        prev = units[-1] if units else None
        glue = False
        gap = t["start"] - prev[-1]["end"] if prev else 0.0
        if prev and x in PARTICLES and gap < PARTICLE_REACH_S:
            glue = True                                       # นะครับ / อะ
        elif prev and gap < pause_s:
            ptxt = prev[-1]["text"]
            last = k == len(tokens) - 1 or tokens[k + 1]["text"] in PARTICLES
            if (t.get("sp") and len(prev) < 3 and _LATIN_WORD.match(ptxt) and _LATIN_WORD.match(x)):
                glue = True                                   # Claude Code, Sub Agent
            elif ptxt in PREFIXES and x not in PARTICLES:
                glue = True                                   # การ+โกน
            elif x == "ว่า" and ptxt in SAY_VERBS:
                glue = True                                   # บอกว่า
            elif x == "ถึง" and NUMERAL.match(ptxt) and k + 1 < len(tokens) and NUMERAL.match(tokens[k + 1]["text"]):
                glue = True                                   # 22 ถึง 24
            elif ptxt == "ถึง" and len(prev) > 1 and NUMERAL.match(x):
                glue = True
            elif x == "แล้ว" and last:
                glue = True                                   # "...ครึ่งแล้ว" = already
            elif NUMERAL.match(ptxt) and (NUMERAL.match(x) or x in CLASSIFIERS):
                glue = True                                   # ห้า+ทุ่ม, 55+นาที
            elif len(prev) > 1 and ptxt in CLASSIFIERS and x in {"ครึ่ง", "เซนต์", "เซ็นต์"}:
                glue = True                                   # ห้าทุ่ม+ครึ่ง
        if glue:
            prev.append(t)
        else:
            units.append([t])
    return units


# ---------- 4. captions of ~N words ----------

def _join(tokens: List[dict]) -> str:
    s = ""
    for t in tokens:
        s += (" " if s and t.get("sp") else "") + t["text"]
    return s


def _group(tokens: List[dict]) -> Dict:
    """One caption. tokens[] carries each word's REAL spoken start/end - karaoke must
    read these, never split the caption duration evenly (edit-rules #16).
    text[char_start:char_end] == token.text exactly."""
    text, toks = "", []
    for t in tokens:
        if text and t.get("sp"):
            text += " "
        cs = len(text)
        text += t["text"]
        toks.append({"text": t["text"], "char_start": cs, "char_end": len(text),
                     "start": round(t["start"], 3), "end": round(t["end"], 3)})
    return {"start": toks[0]["start"], "end": toks[-1]["end"], "text": text, "tokens": toks}


def _cost(unit: List[dict]) -> int:
    if any(NUMERAL.match(t["text"]) for t in unit):
        return 1                                 # ห้าทุ่มครึ่ง reads as one thing
    free = sum(1 for a, b in zip(unit, unit[1:]) if b["text"] == "ว่า" and a["text"] in SAY_VERBS)
    return max(1, sum(1 for t in unit if t["text"] not in PARTICLES and t["text"] not in PREFIXES) - free)


def pack(units: List[List[dict]], n: int, pause_s: float = PAUSE_S,
         max_chars: Optional[int] = None) -> List[List[dict]]:
    """Pack units into captions of ~n words; returns captions as token lists.

    With `max_chars` the size is a line length instead of a word count (sentence-style
    subtitles): the same breaks apply - pause, clause starter, closing particle - and
    a line simply fills until the next unit would not fit."""
    if max_chars:
        n = 10 ** 6
    else:
        max_chars = 9 * n + 8
    caps: List[List[List[dict]]] = []
    cur: List[List[dict]] = []
    cur_cost = 0
    for u in units:
        c = _cost(u)
        head = u[0]["text"]
        if cur:
            pause = u[0]["start"] - cur[-1][-1]["end"] >= pause_s
            leader = head in STRONG_LEADERS or (head in WEAK_LEADERS and cur_cost >= 2)
            too_long = len(_join([t for x in cur + [u] for t in x])) > max_chars
            closed = cur[-1][-1]["text"] in ENDERS
            # (not at n == 1: word-by-word means the connector is its own caption)
            dangling = n >= 2 and len(cur[-1]) == 1 and cur[-1][0]["text"] in FORWARD
            if pause or leader or too_long or closed:
                caps.append(cur)
                cur, cur_cost = [], 0
            elif cur_cost + c > n:
                if dangling and cur_cost + c == n + 1:
                    caps.append(cur + [u])       # "คนที่" + "สมัคร": one over beats a cut-off
                    cur, cur_cost = [], 0
                    continue
                if dangling and len(cur) >= 2:
                    carry = cur.pop()            # ...or let the connector open the next one
                    caps.append(cur)
                    cur, cur_cost = [carry], _cost(carry)
                else:
                    caps.append(cur)
                    cur, cur_cost = [], 0
        cur.append(u)
        cur_cost += c
    if cur:
        caps.append(cur)
    # A phrase's last caption left with one bare short word reads as a stutter.
    # (n == 1 is word-by-word: one word alone is the request, not an orphan.)
    by_length = n >= 10 ** 6
    if n >= 2 and len(caps) >= 2 and len(caps[-1]) == 1 and len(caps[-1][0]) == 1:
        tail, prev = caps[-1], caps[-2]
        word = tail[0][0]["text"]
        if (tail[0][0]["start"] - prev[-1][-1]["end"] < pause_s and word not in LEADERS
                and prev[-1][-1]["text"] not in ENDERS):
            if len(prev) >= 3:                   # 3+1 -> 2+2
                tail.insert(0, prev.pop())
                # a full line with a stub under it: keep moving words down until the
                # last line is worth a line of its own
                while by_length and len(prev) >= 3 and len(_join([t for x in tail for t in x])) < 8:
                    tail.insert(0, prev.pop())
            elif not by_length and len(word) <= 4 and len(_join([t for x in prev + tail for t in x])) <= max_chars:
                prev.extend(tail)                # short orphan rides along
                caps.pop()
    return [[t for u in cap for t in u] for cap in caps]


def build_groups(raw: dict, n: int, engine: str = "nlpo3",
                 custom_words: Optional[List[str]] = None, max_chars: Optional[int] = None,
                 raw_path: Optional[str] = None) -> List[Dict]:
    """raw transcript -> caption groups (not gap-closed)."""
    names = list(custom_words or []) + proper_nouns(raw, raw_path)
    word_level = raw.get("timing_unit") == "word"
    caps: List[List[dict]] = []
    for atoms in phrases_for(raw, engine, names, custom_words):
        toks = phrase_tokens(atoms, engine, names, custom_words, word_level)
        if toks:
            caps += pack(units_from(toks), max(1, n), max_chars=max_chars)
    return [_group(c) for c in enforce_floor(caps, MIN_ON_SCREEN_S if max_chars else floor_for(n))]


def enforce_floor(caps: List[List[dict]], min_s: float = MIN_ON_SCREEN_S) -> List[List[dict]]:
    """Join a caption that would flash for under `min_s` to the neighbour it is
    closest to in time. On-screen time runs to the NEXT caption's start (gap-close
    holds it that long), so only a word spoken faster than a reader can see is merged."""
    out = [list(c) for c in caps]
    i = 0
    while i < len(out) and len(out) > 1:
        cap = out[i]
        nxt = out[i + 1] if i + 1 < len(out) else None
        prv = out[i - 1] if i else None
        shown = (nxt[0]["start"] if nxt else cap[-1]["end"]) - cap[0]["start"]
        if round(shown * 1000) >= round(min_s * 1000):   # compare in ms, as the validator does
            i += 1
            continue
        gap_next = nxt[0]["start"] - cap[-1]["end"] if nxt else float("inf")
        gap_prev = cap[0]["start"] - prv[-1]["end"] if prv else float("inf")
        a = i if gap_next <= gap_prev else i - 1
        out[a] = out[a] + out[a + 1]
        del out[a + 1]
        i = max(a - 1, 0)
    return out


def phrases_for(raw: dict, engine: str = "nlpo3", names: Optional[List[str]] = None,
                custom_words: Optional[List[str]] = None) -> List[List[dict]]:
    """The phrase list every consumer must share (word_segment + apply_breaks): the ids
    the agent writes breaks against are positions in THIS list."""
    return heal_pause_cuts(build_phrases(raw), engine, names or [], custom_words,
                           word_level=raw.get("timing_unit") == "word")


def proper_nouns(raw: dict, raw_path: Optional[str] = None) -> List[str]:
    """Names the post-process pass recorded (canonical + variants) - never split them.

    Pass 1 writes `proper_nouns[]` into captions.json, not into the raw response, so
    when the raw file's path is known its siblings are read too."""
    found = list(raw.get("proper_nouns") or [])
    if raw_path:
        for name in ("captions.json", "transcript.json"):
            fp = Path(raw_path).parent / name
            try:
                found += json.loads(fp.read_text(encoding="utf-8")).get("proper_nouns") or []
            except (OSError, ValueError, AttributeError):
                continue
    out = []
    for p in found:
        if isinstance(p, str):
            out.append(p)
        elif isinstance(p, dict):
            out += [p.get("canonical") or p.get("text") or ""] + list(p.get("variants") or [])
    return [w for w in out if w]


def gap_close(groups: List[Dict]) -> List[Dict]:
    """Extend each caption's `end` to the next caption's `start`.

    ElevenLabs char-level timing has small silences between phrases (breath pauses,
    word boundaries - typically 20–300ms, sometimes more after a sentence end). If a
    caption renderer treats those gaps as dead time, the line BLINKS off-screen
    between phrases ('sentence' → black → 'next sentence'). That blink reads as
    a flaw, not as a natural pause.

    The natural-feeling default is: hold the current caption until the next one is
    about to start. The viewer always has something to read. The author can still
    re-introduce a hard cut in their NLE by trimming a caption's tail back. We do
    this once at the words.json layer so every downstream consumer (indigo11-edit,
    HyperFrames, any caption renderer reading words.json) gets gap-closed output
    by default - no per-tool fix needed.

    Operates on group `end` only. Tokens inside the group keep their real
    char-level timing (the karaoke beat must stay accurate). The group's `end` is
    purely a visibility hold-time.
    """
    out = [dict(g) for g in groups] # don't mutate caller's input
    for i in range(len(out) - 1):
        out[i]["end"] = held_end(out[i]["end"], out[i + 1]["start"])
    return out


# A pause longer than this is a real pause, not a breath: on uncut footage one caption
# sat on screen for 7 s of silence. Same 1.2 s as the NLE adapters' gap-close and
# the validator's --gap-tolerance-ms.
MAX_HOLD_S = 1.2


def held_end(end: float, next_start: float) -> float:
    """Where a caption should end, given when the next one starts (rule #11b).

    A breath (<= 1.2 s) is bridged completely. A longer pause keeps the line up for
    a moment and then clears it, always leaving more than 1.2 s of clear screen so
    the gap reads as intended, never as a blink."""
    gap = next_start - end
    if gap <= 0:
        return end
    if gap <= MAX_HOLD_S:
        return next_start
    return end + max(0.0, min(MAX_HOLD_S, gap - MAX_HOLD_S - 0.05))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("raw_json", help="Path to raw.json from transcribe.py")
    parser.add_argument("--output", "-o", help="Output captions JSON path")
    parser.add_argument("--engine", default="nlpo3", choices=["newmm", "nlpo3"],
                        help="Thai word segmenter (default: nlpo3 - 3x faster, same accuracy)")
    parser.add_argument("--words-per-group", type=int, default=1,
                        help="Target words per caption (1 = per-word, 2-4 = phrase-style). A target, "
                             "not a hard count: particles ride free and a caption never crosses a pause. Default: 1")
    parser.add_argument("--max-chars", type=int,
                        help="Sentence-style lines instead of N-word captions: fill each line up to this "
                             "many characters (24 reads well on 9:16). Overrides --words-per-group")
    parser.add_argument("--custom-dict", default="",
                        help="Comma-separated extra words that must stay whole (e.g. 'Mark Zuckerberg,GPU,แอนดริว')")
    parser.add_argument("--phrases-out",
                        help="Also write the phrase list [{id, text, start, end}] - the input the agent "
                             "chunks by meaning for apply_breaks.py (pass 2). start/end are source seconds, "
                             "so on a cut timeline the agent can skip phrases that were cut away")
    parser.add_argument("--no-gap-close", action="store_true",
                        help="Don't extend each caption to abut the next. Default OFF "
                             "(captions ARE gap-closed) so caption lines never blink "
                             "off-screen between phrases - the current text holds across "
                             "short silences until the next caption begins. Pass this flag "
                             "to keep ElevenLabs' raw inter-phrase gaps (rare - you'd want "
                             "this only when an NLE / downstream tool prefers exact word "
                             "boundaries and will trim manually).")
    args = parser.parse_args()
    if not args.output and not args.phrases_out:
        parser.error("give --output and/or --phrases-out")

    raw = load_raw(args.raw_json)
    custom = [w.strip() for w in args.custom_dict.split(",") if w.strip()] if args.custom_dict else None
    phrases = phrases_for(raw, args.engine, list(custom or []) + proper_nouns(raw, args.raw_json), custom)

    if args.phrases_out:
        Path(args.phrases_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.phrases_out).write_text(
            json.dumps([{"id": i, "text": phrase_text(p), "start": round(p[0]["start"], 3),
                         "end": round(p[-1]["end"], 3)} for i, p in enumerate(phrases)],
                       ensure_ascii=False, indent=1), encoding="utf-8")

    summary = {"ok": True, "phrases": len(phrases)}
    if args.output:
        groups = build_groups(raw, args.words_per_group, args.engine, custom, args.max_chars,
                              raw_path=args.raw_json)
        if not args.no_gap_close:
            groups = gap_close(groups)
        out = {
            "duration": raw.get("audio_duration_secs") or raw.get("duration"),
            "language": raw.get("language") or raw.get("language_code"),
            "engine": args.engine if not custom else f"{args.engine}+custom-dict",
            "segmenter": "timing-first",
            "words_per_group": None if args.max_chars else args.words_per_group,
            "max_chars": args.max_chars,
            "gap_closed": not args.no_gap_close,
            "phrase_count": len(phrases),
            "word_count": sum(len(g["tokens"]) for g in groups),
            "group_count": len(groups),
            "groups": groups,
        }
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        summary.update({"engine": out["engine"], "words_per_group": args.words_per_group,
                        "words": out["word_count"], "groups": len(groups), "output": args.output})
    if args.phrases_out:
        summary["phrases_out"] = args.phrases_out
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
