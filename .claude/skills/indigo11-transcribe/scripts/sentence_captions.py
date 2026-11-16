#!/usr/bin/env python3
"""sentence_captions.py - แบ่งซับตามประโยคที่ STT ให้มา ไม่ใช่นับ N คำแล้วตัด

WHY THIS EXISTS (measured on a real buyer clip, 0902 (3)):
`word_segment.py --words-per-group N` flattens the transcript into tokens and cuts
every N. On Thai that produces captions the buyer rejected on sight:

  'ครับบ้านก็ไม่'      <- the politeness particle ครับ ripped off the sentence it
                          ends and glued to the NEXT sentence
  'ก็คือแบรน' / 'ด์เอนะ'      <- a brand name split across two captions
  'มาแท' / 'ะมาขี้'    <- split mid-word, and mid-CHARACTER-CLUSTER

ElevenLabs already returns real sentence boundaries in `words[]` (19 phrases for
that clip, every นะครับ present, timing accurate to 0.02s). The transcript was never
the problem - throwing its structure away was. So: start from those phrases, and only
split one when it is too long to read, at a boundary that respects Thai.

Rules, in order:
  1. Build lines from WORDS (nlpo3, the same segmenter the kit already uses) - never
     from character indices, which is what split mid-word.
  2. Protect ATOMIC units (brand names, นะครับ) at the TEXT level, before tokenizing.
     Necessary because nlpo3 segments '...แล้วนะครับผมก็มี...' as
     ['แล้ว','นะ','ครับผม',...] - it swallows ครับ into ครับผม, so the unit can never
     be recovered from the tokenizer's output.
  3. A sentence-ending particle (นะครับ/ครับ/นะคะ/ค่ะ) may never START a line. It is
     the tail of the sentence before it.
  4. A trailing fragment shorter than MINC merges back.
  5. Gap-close the result (edit-rules #11b).

Usage:
  python3 sentence_captions.py <transcript.json> <out-words.json> [--max-chars N]
         [--atomic "name1,name2"]

Input is a kit transcript with `words[]` (phrases) + `words_raw[]` (char timing).
Brand names are DATA, never code (edit-rules #38): units that must not be split
come from the transcript's `proper_nouns[]` (canonical + variants, written by the
post-process pass after reading the packaging) and/or `--atomic`. Only the Thai
politeness particles are built in.
Output is the standard words.json shape that indigo11-edit/add_captions.py consumes.
"""
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)
import json, sys, re

MAXC = 24          # ตัวอักษรไทยต่อบรรทัด (9:16 อ่านสบาย)
ENDERS = ["นะครับ", "นะคะ", "ครับ", "ค่ะ"]
CONNECT = ["แต่ว่า", "แต่", "ก็คือ", "เพื่อที่จะ", "หลังจาก", "สำหรับ", "และ", "หรือ", "ซึ่ง", "ถ้า", "เวลา"]
# หน่วยที่ห้ามผ่า - เฉพาะคำลงท้ายที่ตัดแล้วอ่านไม่รู้เรื่อง. ชื่อแบรนด์ถูกเติมตอนรันจาก
# proper_nouns[] ของ transcript หรือ --atomic (ห้าม hard-code ชื่อสินค้าของผู้ซื้อคนใดในโค้ด)
ATOMIC = ["นะครับ", "นะคะ", "ครับ", "ค่ะ"]


def char_map(raw):
    """สร้าง map: index ตัวอักษร -> (start,end) จาก char stream"""
    out, pos = [], 0
    for c in raw:
        txt = c.get("text") or ""
        if not txt:
            continue
        for _ in txt:
            out.append((c["start"], c["end"]))
        pos += len(txt)
    return out


def protect(text):
    """คืน set ของ index ที่ห้ามตัด (อยู่กลาง atomic unit)"""
    banned = set()
    for a in ATOMIC:
        for m in re.finditer(re.escape(a), text):
            for i in range(m.start() + 1, m.end()):
                banned.add(i)
    return banned


def cut_points(text):
    """คืนจุดตัดที่ดี เรียงตามความสำคัญ"""
    banned = protect(text)
    pts = []
    for e in ENDERS:                       # a) หลัง นะครับ
        for m in re.finditer(re.escape(e), text):
            p = m.end()
            if p not in banned and 0 < p < len(text):
                pts.append((0, p))
    for c in CONNECT:                      # b) หน้าคำเชื่อม
        for m in re.finditer(re.escape(c), text):
            p = m.start()
            if p not in banned and 0 < p < len(text):
                pts.append((1, p))
    return sorted(set(pts), key=lambda x: (x[0], x[1])), banned


MINC = 8    # หางที่สั้นกว่านี้ห้ามยืนเดี่ยว

_ENGINE = "newmm"
try:
    import nlpo3, pythainlp
    from pathlib import Path as _P
    try:
        nlpo3.load_dict(str(_P(pythainlp.__file__).parent / "corpus" / "words_th.txt"), "default")
    except Exception:
        pass
    _ENGINE = "nlpo3"
except Exception:
    pass
from pythainlp.tokenize import word_tokenize as _wt


def words_of(t):
    return _wt(t, engine=_ENGINE, keep_whitespace=False)


def _split_atomic_aware(text):
    """แบ่งคำโดยกัน ATOMIC ไว้ก่อน แล้วค่อยให้ nlpo3 แบ่งส่วนที่เหลือ

    ต้องทำแบบนี้เพราะ nlpo3 แบ่ง "...แล้วนะครับผมก็มี..." เป็น ['แล้ว','นะ','ครับผม',...]
    - คำว่า "ครับ" ถูกกลืนรวมกับ "ผม" เป็น token เดียว จึงไม่มีทางจับ "นะครับ" ได้
    จากผลลัพธ์ของ tokenizer เลย ต้องกันไว้ตั้งแต่ระดับข้อความ
    """
    spans = []
    for a in sorted(ATOMIC, key=len, reverse=True):
        start = 0
        while True:
            i = text.find(a, start)
            if i < 0:
                break
            if not any(i < e and s_ < i + len(a) for s_, e in spans):
                spans.append((i, i + len(a)))
            start = i + len(a)
    spans.sort()
    out, pos = [], 0
    for s_, e in spans:
        if s_ > pos:
            out += words_of(text[pos:s_])
        out.append(text[s_:e])
        pos = e
    if pos < len(text):
        out += words_of(text[pos:])
    return out


def _atomic_merge(ws):
    """รวม token ที่ประกอบกันเป็นหน่วยห้ามผ่า (ชื่อแบรนด์ / นะครับ) ให้เป็นชิ้นเดียว"""
    out, i = [], 0
    while i < len(ws):
        hit = None
        for a in sorted(ATOMIC, key=len, reverse=True):
            acc, j = "", i
            while j < len(ws) and len(acc) < len(a):
                acc += ws[j]; j += 1
            if acc == a:
                hit = (a, j); break
        if hit:
            out.append(hit[0]); i = hit[1]
        else:
            out.append(ws[i]); i += 1
    return out


def split_text(text, maxc=MAXC):
    """แบ่งข้อความเป็นบรรทัดซับ - ทำครั้งเดียวจบ ไม่ recurse

    กฎ (เรียงตามความสำคัญ):
      1. ประกอบจาก 'คำ' ที่ nlpo3 แบ่ง - ไม่เคยผ่ากลางคำ
      2. หน่วย ATOMIC (ชื่อแบรนด์, นะครับ) รวมเป็น token เดียวก่อน จึงไม่มีทางถูกผ่า
      3. ENDER ห้ามขึ้นต้นบรรทัดใหม่ - มันคือหางของประโยคก่อน ไม่ใช่หัวของประโยคใหม่
      4. หางสั้นกว่า MINC ผนวกกลับบรรทัดก่อนหน้า
    """
    if len(text) <= maxc:
        return [text]
    ws = _atomic_merge(_split_atomic_aware(text))
    parts, cur = [], ""
    for w in ws:
        if not cur:
            cur = w
            continue
        # ENDER ต่อท้ายเสมอ แม้จะทำให้ยาวเกินนิดหน่อย
        if w in ATOMIC and w in ENDERS:
            cur += w
            continue
        if len(cur) + len(w) > maxc:
            parts.append(cur); cur = w
        else:
            cur += w
    if cur:
        parts.append(cur)
    # หางสั้นเกินไป -> ผนวกกลับ
    out = []
    for pz in parts:
        if out and len(pz) < MINC:
            out[-1] += pz
        else:
            out.append(pz)
    return out or [text]


def load_vad(path):
    """ช่วงที่ VAD บอกว่ามีเสียงพูดจริง [(start,end),...] - ไม่มีไฟล์ก็คืน []"""
    if not path:
        return []
    try:
        return [(v["start"], v["end"]) for v in json.load(open(path, encoding="utf-8"))]
    except Exception:
        return []


def voice_end(speech, s, e):
    """เสียงพูดจริงจบเมื่อไหร่ภายในช่วง [s,e]

    STT ยืดตัวอักษรสุดท้ายคลุมความเงียบ - วัดจริงบนคลิปผู้ซื้อ: 'รั' ยาว 2.4s และ 'บ'
    ยาว 2.1s ปิดท้าย '...นะครับ' ทั้งที่ VAD บอกเสียงจบไปแล้ว 4 วินาทีก่อนหน้า. ถ้าไม่ตัด
    ตรงนี้ ซับจะค้างจอยาวผิดจังหวะ. คืน None เมื่อไม่มีข้อมูล VAD.
    """
    ends = [min(e, ve) for vs, ve in speech if vs < e and ve > s]
    return max(ends) if ends else None


def voice_gaps(speech, s, e, min_gap=0.25):
    """จุดที่ 'เสียงหยุด' ภายในประโยค = จังหวะแบ่งประโยคตามธรรมชาติของคนพูด"""
    runs = sorted([(max(s, vs), min(e, ve)) for vs, ve in speech if vs < e and ve > s])
    return [(runs[i][1], runs[i + 1][0]) for i in range(len(runs) - 1)
            if runs[i + 1][0] - runs[i][1] >= min_gap]


def main():
    if len(sys.argv) < 3 or "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        return
    src, out_path = sys.argv[1], sys.argv[2]
    global MAXC
    if "--max-chars" in sys.argv:
        MAXC = int(sys.argv[sys.argv.index("--max-chars") + 1])
    vad_path = None
    if "--vad" in sys.argv:
        vad_path = sys.argv[sys.argv.index("--vad") + 1]
    t = json.load(open(src, encoding="utf-8"))
    # Proper nouns arrive as data: post-process wrote what it verified on camera.
    extra = []
    for e in t.get("proper_nouns") or []:
        extra += [n for n in [e.get("canonical")] + list(e.get("variants") or []) if n]
    if "--atomic" in sys.argv:
        extra += [a.strip() for a in sys.argv[sys.argv.index("--atomic") + 1].split(",")]
    for n in sorted(set(extra), key=len, reverse=True):
        if n and n not in ATOMIC:
            ATOMIC.insert(0, n)  # longest first, ahead of the particles
    speech = load_vad(vad_path)
    cmap = char_map(t["words_raw"])
    full = "".join(c.get("text") or "" for c in t["words_raw"])
    groups, cursor = [], 0
    for w in t["words"]:
        text = w["text"]
        idx = full.find(text, cursor)
        if idx < 0:
            idx = full.find(text)
        if idx < 0:
            continue
        cursor = idx + len(text)
        parts = split_text(text)
        off = idx
        for p in parts:
            s = cmap[off][0] if off < len(cmap) else w["start"]
            e_i = min(off + len(p) - 1, len(cmap) - 1)
            e = cmap[e_i][1] if e_i >= 0 else w["end"]
            # ตัดปลายกลับมาที่ "เสียงจริงหยุด" - ไม่ให้ซับค้างจอตามตัวอักษรที่ STT ยืด
            ve = voice_end(speech, s, e)
            if ve is not None and ve > s:
                e = min(e, ve + 0.12)
            # One token per WORD, not one per caption. remap_captions drops or keeps
            # whole tokens, so a single-token caption cannot be trimmed when the cut
            # removes part of the line - which left the doubled "ข้อดีข้อดี" on screen
            # after the audio had been fixed. Per-word tokens let the caption follow
            # the cut. Timings come from the char map, never an even split (rule #16).
            toks, tpos = [], 0
            for tw in (words_of(p) if len(p) > 1 else [p]):
                ts = cmap[off + tpos][0] if off + tpos < len(cmap) else s
                te_i = min(off + tpos + len(tw) - 1, len(cmap) - 1)
                te = cmap[te_i][1] if te_i >= 0 else e
                toks.append({"text": tw, "char_start": tpos, "char_end": tpos + len(tw),
                             "start": ts, "end": min(te, e)})
                tpos += len(tw)
            if not toks:
                toks = [{"text": p, "char_start": 0, "char_end": len(p), "start": s, "end": e}]
            groups.append({"start": s, "end": e, "text": p, "tokens": toks})
            off += len(p)
    # gap-close (rule #11b) แต่ไม่ยืดข้ามความเงียบยาว: ยืดให้ชนซับถัดไปเมื่อช่องว่าง
    # สั้น (หายใจ/เว้นวรรค) เท่านั้น ถ้าเงียบยาวกว่านี้แปลว่าคนพูดหยุดจริง - ปล่อยซับหาย
    # ไปแทนที่จะค้างจอ 6 วินาที
    HOLD = 1.2
    # ElevenLabs returns a code-switched brand as SEPARATE phrases - "Brand" / "Name"
    # / "นะครับ" arrive as three entries, so each became its own caption: three flashes
    # and a stranded particle. Merge short adjacent OUTPUT lines instead of merging the
    # input phrases - the input text is matched against the character stream by exact
    # substring, and a merged phrase (no separator) is not found there, which silently
    # dropped the brand and its particle from the captions entirely.
    MERGE_MAX = 14
    merged = []
    for g in groups:
        if (merged and len(g["text"]) <= MERGE_MAX
                and len(merged[-1]["text"]) + len(g["text"]) <= MAXC
                and g["start"] - merged[-1]["end"] <= 0.5):
            # Keep a space between Latin words ("Brand Name"); Thai never needs one.
            sep = " " if (merged[-1]["text"][-1:].isascii() and merged[-1]["text"][-1:].isalnum()
                          and g["text"][:1].isascii() and g["text"][:1].isalnum()) else ""
            merged[-1] = {**merged[-1], "text": merged[-1]["text"] + sep + g["text"],
                          "end": g["end"],
                          "tokens": merged[-1]["tokens"] + g["tokens"]}
            continue
        merged.append(g)
    groups = merged

    for i in range(len(groups) - 1):
        nxt = groups[i + 1]["start"]
        groups[i]["end"] = nxt if nxt - groups[i]["end"] <= HOLD else min(nxt, groups[i]["end"] + HOLD)
    json.dump({"duration": t.get("duration", 0), "language": "th", "engine": "sentence-aware",
               "gap_closed": True, "group_count": len(groups), "groups": groups},
              open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({"ok": True, "groups": len(groups),
                      "max_chars": max(len(g["text"]) for g in groups)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
