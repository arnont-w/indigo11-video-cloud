# Caption pass 2: where each caption breaks

Read before running pass 2 on a transcript that will become N-word captions. You decide **where each caption breaks** and which words show differently from the STT spelling, by reading the Thai. Scripts do every number. You never write a timestamp: hand-edited times are how karaoke drifted. On a 14-minute clip, counting N tokens broke 32-37% of captions, rule-based `word_segment.py` 15-17%, breaks written this way 6%.

| You (meaning) | `apply_breaks.py` (arithmetic) |
|---|---|
| Split each phrase into natural chunks | Checks no source character went missing |
| Mark chunks whose on-screen text differs | Derives every start/end from character timing; builds `tokens[]` for karaoke |
| | Joins captions under 200 ms to a neighbour, gap-closes, writes `words.json` |

## Steps (Windows: `python`)

```bash
# 1. phrase list
python3 $TRANSCRIBE_DIR/scripts/word_segment.py <raw-elevenlabs.json> --phrases-out <dir>/phrases.json
# 2. YOU write <dir>/breaks.json
# 3. timing
python3 $TRANSCRIBE_DIR/scripts/apply_breaks.py <raw-elevenlabs.json> <dir>/breaks.json -o <dir>/words.json
# 4. fix ONLY ids listed under `fallback` or `too_long`, rerun step 3. Nothing listed = done
# 5. validate
python3 $TRANSCRIBE_DIR/scripts/validate_captions.py <dir>/words.json --require-tokens
```

`$TRANSCRIBE_DIR` is the sibling `indigo11-transcribe` skill. `phrases.json` is `[{"id":0,"text":"โอเค","start":0.94,"end":1.24}, ...]` (seconds, SOURCE media time). A phrase is speech without a stop: it ends at a silence of 0.3 s or more or where the STT wrote a space between clauses, so one-word phrases and false starts are normal. The rules below govern where YOU break; a phrase is written as it stands.

## breaks.json: exact shape

```json
{"words_per_group": 2,
 "chunks": {
  "0": ["โอเค"],
  "1": ["ผมเพิ่ง", "เดินทาง", "จากเชียงใหม่", "มากรุงเทพนะครับ"],
  "2": ["ผมไม่", {"src": "รักพลู", "text": "rug pull"}, "หรอกนะครับ"]
 }}
```

`words_per_group` is the N the user asked for. These fail **silently** (the phrase falls back to the rule-based split and your work is discarded):
- Keys are phrase `id` **as strings**. Every id must be present, unless you use `--partial`.
- **Source characters are sacred.** Joined chunks (a `{src,text}` chunk counts as its `src`), spaces removed, must equal the phrase text, spaces removed. No dropped stutter, added punctuation or reordering. Cut-off marks (`--`, a hyphen in `นะ-นี้`) are characters: keep them on the word they cut.
- Spaces inside a chunk are kept (`"Claude Code"`, `"22 ถึง 24"`); none are needed at chunk edges.
- Never merge a phrase with another.

## Text fixes: `{"src","text"}`

The caption shows `text`, timed by `src`, which must be the transcript's exact characters.

```json
{"src": "รักพลู", "text": "rug pull"}      corrected name
{"src": "ห้าสิบห้า", "text": "55"}         number as the user wants it
{"src": "ผมผม", "text": "ผม"}              stutter collapsed
{"src": "เอ่อ", "text": ""}                filler dropped from screen
```

Carry over every fix pass 1 made in `captions.json`, or the N-word captions ship the STT spelling. **Change a proper noun only after looking at the frames for that moment**; if they do not show the name, keep the STT spelling and report it unverified. Keep a fixed chunk to the word or name, not a clause (it is one karaoke token).

## How to break (priority order)

1. **Never split inside a word** (`อะไร`, `บอกว่า`, a name, an English word).
2. **Particles stay with the word before and do not count toward N**: `ครับ`, `ครับผม`, `นะครับ`, `ค่ะ`, `นะคะ`, `นะ`, `อะ`, `เนี่ย`, `ไหม`, `หรอก`, `ๆ`, and sentence-final `เลย`, `แล้ว`, `ด้วย`, `บ้าง` (`เสร็จแล้ว`). Never alone, never first in a chunk; a chunk ends after `ครับ` / `ครับผม` / `ค่ะ`.
3. **Do not END a chunk on a connector that leans forward**: `แต่`, `เพราะว่า`, `ซึ่ง`, `ถ้า`, `ที่`, `ของ`, `เพื่อ`, `จาก`, `สำหรับ`, `เกี่ยวกับ`, `และ`, `หรือ`, and `แล้ว` opening a clause. The list is examples; judge by whether the word leans forward. Allowed: a discourse opener as its own chunk when the alternative tears a verb from its object (`ก็เดี๋ยว | รอดูนะครับ`, `ก็คือ | ทำเว็บไซต์`); a subject pronoun or auxiliary (`ผม`, `จะ`, `ต้อง`, `อยาก`, `ได้`) may end a chunk; `ว่า` after a verb of saying or thinking stays with it (`บอกว่า`), but opens the next chunk if a particle sits between.
4. **Numbers stay with their unit** (`ห้าทุ่มครึ่ง`, `55 นาที`, `22 ถึง 24`); a number with unit counts as one word.
5. **Names and compounds are one piece** (`Claude Code`, `Sub Agent`, `Post-processing`).
6. **Break where meaning breaks, near the size asked.** Aim for N; N-1 is fine; N+1 when a break would fall badly. **Hard ceiling N+1**: a user who asked for 2 words and gets 4-word captions has not been given what they ordered. No floor. `apply_breaks.py` counts dictionary words; trust its `too_long` over your own tally.
7. **No stranded one-syllable word** (`เรา`, `คน`, `ก่อน`, `ใหม่`) at the end of a phrase: attach it to the chunk before. A leading interjection (`โอเค`) stands alone when more than a couple of words follow.

Examples (N = 2):
```
ผมจะมาพูดเพิ่มเติมเกี่ยวกับ platform ของผมนะครับ -> ผมจะมา | พูดเพิ่มเติม | เกี่ยวกับ platform | ของผมนะครับ
ตอนนี้ก็ห้าทุ่มครึ่งแล้ว -> ตอนนี้ | ก็ห้าทุ่มครึ่งแล้ว
เสร็จแล้วก็จะได้โกนหนวดนะครับ -> เสร็จแล้ว | ก็จะได้ | โกนหนวดนะครับ
คนที่บอกว่าผมจะยกเลิกก่อน -> คนที่ | บอกว่าผม | จะยกเลิกก่อน
```
Wrong: `ครับแต่ | ตอนนี้ผม` (ครับ ends a sentence, แต่ starts the next); `ก็ห้า | ทุ่มครึ่ง` (number torn from unit); `ที่บอ | กว่าผม` (inside a word); `ผมไม่รักพลูหรอกนะครับ` on a 2-word order (split: `ผมไม่ | รักพลูหรอกนะครับ`).

## What apply_breaks.py reports

```json
{"ok": true, "phrases": 206, "groups": 560, "fallback_count": 0, "fallback": [],
 "too_long_count": 1, "too_long": [{"id": 139, "chunk": "ผมไม่รักพลูหรอกนะครับ", "words": 4}],
 "text_fixes": 3, "merged_short": 4, "output": ".../words.json"}
```

- `fallback[].reason = "text changed"`: your chunks do not rebuild the phrase. Re-chunk from the `text` shown, character for character (or use `{src,text}` for a fix). `"missing"`: the id is absent or a chunk is neither a string nor a `{src,text}` object. Fallback phrases are still in the output, so the file is complete either way; fix them when you can and say so when you could not.
- `too_long`: over N+1 words, or over `8N+6` characters (22 at N=2). A single number or name is never reported.
- `merged_short`: chunks joined to a neighbour for being on screen under 200 ms (100 ms when N = 1). Nothing to fix.
- Exit code 2: more than half the phrases fell back (usually ids offset by one, or breaks for a different transcript).
- If the validator still fails, do not hand-edit `words.json`; report the error text (a script bug).

## Output shape

```json
{"words_per_group": 2, "gap_closed": true, "segmenter": "agent-breaks",
 "groups": [{"start": 3.52, "end": 4.36, "text": "55 นาที",
   "tokens": [{"text": "55", "char_start": 0, "char_end": 2, "start": 3.52, "end": 3.88},
              {"text": "นาที", "char_start": 3, "char_end": 7, "start": 3.88, "end": 4.36}]}]}
```

Times are **seconds**; `add_captions.py` converts to ms once at load. `text[char_start:char_end]` equals `token.text`. Token times are real spoken times, so karaoke follows the voice. Group `end` is gap-closed to the next `start`; token times never are. The older `{"captions":[{timeline_start_ms,...}]}` shape is still accepted but do not produce it.

## Cut timelines

Break on the **source** transcript, then remap: `add_captions.py --from-timeline` does it from `projects/_work/transcribe/<basename>/words.json`, so pass 2 writes there. Never re-break on the cut timeline (the same line would appear once per fragment). Chunk only phrases inside kept source ranges (`capcut_to_edl.py --project <name>` prints them) and run `apply_breaks.py ... --partial`: omitted phrases get the rule-based cut and count under `rule_based`, not as failures.

## When to run it

Before injecting N-word captions whenever quality matters (`word_segment.py` alone is the floor and needs no model). Re-run when N changes (no new STT call). Skip for phrase-level `captions.json`. A runtime that cannot do the pass skips it and says so, and never ships a breaks file it did not read.
