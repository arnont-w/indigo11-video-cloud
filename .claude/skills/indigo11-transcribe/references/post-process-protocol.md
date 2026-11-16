# Post-process protocol

Detailed rules the post-process subagent applies to clean up a raw STT response into a buyer-ready `captions.json`. The main `SKILL.md` references this file; the subagent reads it (or has it inlined into its spawn prompt) before producing output.

**Input file depends on transcribe.py mode:**
- **Single-output mode** (`transcribe.py <file> --output captions.json`): input = the raw STT response written to that path. No gap-close has happened yet. Below, this is what the doc means when it says "read `raw.json`".
- **3-output mode** (`transcribe.py <file> --save-all <dir>`): the kit emits three files - `raw-elevenlabs.json` (untouched STT, never gap-closed; the re-segmentation source), `captions.json` (phrase-level, **already gap-closed** by default - see edit-rules #11b), and `words.json` (nlpo3-segmented, also gap-closed). When the subagent runs in 3-output mode, the input is `captions.json` (gap-closed). The text-level fixes in this doc all still apply unchanged; what changes is the gap-preserving rule in §3 ("Preserving gap-close") - adjacent captions are already abutting, so don't re-introduce a gap when you split/merge.

In either mode: never touch `raw-elevenlabs.json` (downstream re-segmentation needs the actual word boundaries); only edit/write `captions.json`.

## Table of contents

1. [Read before you write](#1-read-before-you-write)
2. [Fix what the model got wrong](#2-fix-what-the-model-got-wrong)
3. [Re-segment for the language](#3-re-segment-for-the-language)
4. [Propose emphasis words](#4-propose-emphasis-words)
5. [Build hook + audio beats](#5-build-hook--audio-beats)
6. [Emit captions.json](#6-emit-captionsjson)
7. [Quality bar - never ship without these](#7-quality-bar)
8. [Examples](#8-examples)

---

## 1. Read before you write

Read `raw.json.text` end to end before touching anything. Identify:

- **Domain** - tech, finance, health, lifestyle. Shapes which loanwords to expect.
- **Speaker register** - formal / casual / hype. Affects whether to keep stutters and fillers.
- **Brands and proper nouns** - list them mentally before checking spellings. Missed brands are the #1 cause of "wrong but plausible" captions.
- **Audience language** - Thai with Latin loanwords? English with code names? Both reads of the script will show up here.

### Language-mismatch sanity check

Before anything else, compare the language flag the script was called with against what's actually in `raw.json.text`. Two patterns that come up:

- **Thai audio transcribed as English** - sometimes happens when the user passes `--lang en` to OpenAI Whisper. Whisper's English mode silently falls back to the *translate* task: it produces English text from Thai audio rather than transcribing the Thai. The transcript reads natural but it has no relation to what's actually on screen, and word timing won't match the spoken Thai. Detect this by counting Thai characters (U+0E00–U+0E7F) in `raw.json.text`: if `--lang` was `en` but Thai chars > 5%, flag it in the user-facing summary, ask whether to re-transcribe with `--lang th`, and **don't ship captions built from translated text** - they'll be stylistically fine but useless as captions because they don't match the audio the viewer is hearing.
- **English audio mis-flagged as Thai** - rare, but the symptom is `notes[]` entries about "no loanwords needed" alongside captions that read fluent English. Same fix: surface to the user before saving.

The post-process pass cannot fix this kind of mismatch on its own - it has to be re-transcribed. Surface the issue, don't paper over it.

### Look at the video before guessing a proper noun

The transcript is audio only, and Thai STT renders unfamiliar brands phonetically - a spelling
that is *plausible* and *wrong* reads exactly like a correct one, so you cannot catch it by
reading. When the clip is a product/affiliate video, the thing being named is usually **on
screen**, so look before you decide.

Where the pictures are: if `indigo11-edit` has run, `context.json` has `frame_grids[]` - grid
PNGs holding ~36 frames each with per-cell timestamps (kit rule #37). Open the grid with
whatever your runtime provides (Codex CLI: `view_image`); if it cannot show images, keep the
STT's spelling and flag it unverified - never guess (edit-rules.md "Agent-neutral execution").
Read the grid whose
`cells[].t_ms` covers the moment the term is spoken. If analyze has not run, generate grids
directly - local, free, no API key:

```bash
# $ANALYZE_DIR = the sibling indigo11-edit skill: <starter>/skills/indigo11-edit
python3 $ANALYZE_DIR/scripts/frame_grid.py <video> --out-dir <dir> \
  --start <sec-before> --end <sec-after> --cells 16 --cell-width 900
```

Use `--cells 16 --cell-width 900` (~392px per cell) when the answer is small on-screen text
such as a logo; the 36-cell default is for identifying an object, not reading print off it.

What this settles, and what it does not:

- **Settles:** which of two similar products is in hand right now (a clip may show an old model
  and a new one), whether an on-screen label confirms the spelling, what a silent stretch is
  demonstrating, whether a number you heard matches a number printed on the box.
- **Does NOT settle:** an unbranded object. Measured case - a Thai clip said `แบตเตอเลียน`
  twice; the device is held to camera for 24 seconds from several angles and carries **no
  visible logo**. The frames prove the object exists and what it looks like, and prove nothing
  about its name.

So: **look first; if the frames do not answer it, say so and ask the buyer - never invent a
brand spelling.** Flag it in `notes[]` as unverified and leave the STT's spelling rather than
substituting a confident guess. A wrong brand in a caption is worse than a phonetic one,
because the buyer will not notice it before publishing.

Then skim `raw.json.words[]` to see the granularity you're working with:

- 1–3 token entries per second → likely word-level (ElevenLabs, OpenAI English)
- 1 token per 5–15 seconds with high character count → phrase-level (Grok Thai)

The granularity you have decides whether you mostly **split** (phrase → words) or mostly **merge** (words → phrases). Don't fight the source; transform it.

---

## 2. Fix what the model got wrong

These four failure modes account for almost every caption bug.

### Transliterated loanwords

When the audience reads brand names in Latin script, restore them:

| What STT returns | What it should be |
| --------------------- | ----------------- |
| `จีพียู` | `GPU` |
| `ทีเอสเอ็มซี` | `TSMC` |
| `เอไอ` | `AI` |
| `เคอร์เซอร์` / `คัวร์เซอร์` | `Cursor` |
| `คล็อด` | `Claude` |
| `เอ็นวิเดีย` | `NVIDIA` |
| `โอเพ่นเอไอ` | `OpenAI` |

Keep `start`/`end` from the original entry - only edit `text`.

### Misheard brand names

This is the most dangerous failure: STT returns a real-sounding word that's the wrong product. Examples seen in production:

- `เคิร์เซอร์` could be `Cursor` OR `Claude Code, Cursor` (two adjacent brands collapsed into one mishearing)
- `เกรน` could be `Grain` (audio plugin) OR `Grok` (xAI model) depending on topic
- `แลม่า` could be `LLaMA` (Meta) OR `Llama 3` (Meta) - version matters

Disambiguation rules in order:

0. **THE PICTURE** - read the frame grid covering the moment the name is spoken (see
   [§1](#look-at-the-video-before-guessing-a-proper-noun)). If the product is in shot, its
   packaging, on-device print, or an on-screen caption may simply *say* the name. Also check
   `text_events[]` when `--detect-text` ran - that is OCR of the actual screen. **Direct visual
   evidence outranks every inference below**, including a web search: the frames show what THIS
   video is about, a search shows what is popular. Use it when the term names something
   physical or on-screen; skip straight to rule 1 for a term that is purely spoken (a person's
   name, an abstract concept).
1. **Topic context** - if the speaker just mentioned Anthropic, `เคิร์เซอร์` is probably bridging from Cursor to Claude. If they mentioned Meta/open source, `แลม่า` is LLaMA.
2. **Sentence neighbours** - words just before/after constrain meaning. "ใช้ ___ เขียนโค้ด" is a coding tool.
3. **Audio duration** - `Cursor` (2 syllables) vs `Claude Code, Cursor` (5+ syllables) often shows up in the timing window.
4. **Web search** - when confidence is genuinely low, web-search the topic + the candidate. Take the more likely answer, log both options in `notes[]`. Never let a search override what the frames plainly show.

If still unsure, mark as `[uncertain: A or B]` in text and put the question in `notes[]`. Never silently guess.

### Wrong Thai word boundaries

Thai has no spaces. STT often segments oddly: `กระโดดลงหน้าผา` might come back as `กระโดด ลง หน้าผา` (good) or `กระ โดดล งหน้าผ า` (bad).

When you re-split, **keep the original `start`/`end` of the source entry** and only edit `text`. If you split one entry into two, interpolate timestamps proportionally to character count (see [§3](#3-re-segment-for-the-language)).

### Stutters and filler

Thai filler: `เออ`, `อ่ะ`, `แบบ`, `คือว่า`. English: `uh`, `um`, `like`, `you know`.

- **Casual register** → keep them, they're part of the voice.
- **Polished/professional** → drop them. When dropping, merge the surrounding entries' timing - don't leave a hole.

If the speaker stutters a real word (`GP- GPU`), keep the final intended word and use the full timing window.

---

## 3. Re-segment for the language

The goal is caption groups that actually fit in a 9:16 frame and read in one breath.

### Thai

- Target: **2–4 short words** per caption, or one short phrase.
- Break on:
 - End-of-sentence particles: `ครับ`, `นะ`, `ค่ะ`, `จ้า`, `เลย`
 - Conjunctions: `แต่`, `ก็`, `และ`, `หรือ`, `ที่`, `เพราะ`, `ถ้า`
 - Breath pauses (visible as a gap > 200ms in `words[]` if you have word-level timing)
- Don't break in the middle of a noun-modifier pair (`บ้าน ใหญ่` stays together, not `บ้าน` + `ใหญ่`)

### English

- Target: **3–5 words** per caption.
- Break on clauses, prepositions, or coordinating conjunctions.
- No orphan articles or particles (`a`, `the`, `to` should never be the last word in a caption).
- Full English rules (spelling, numbers, line length, reading speed): `references/caption-rules-en.md`.

### Mixed Thai + English

- Keep loanwords with their Thai context: `GPU ได้`, `ใช้ Cursor`, `Claude เขียนโค้ด`. These are one chunk.
- If the speaker code-switches mid-sentence, break at the switch - the visual pacing aligns with the audio.

### Splitting a long phrase chunk (Grok Thai case)

When `raw.json.words[]` gives you `[18.09–33.08] "เขาเลยต้องเลยใช้คำว่ากระโดดลงหน้าผาเพราะมันคือความรู้สึกของช่วงเวลานั้น"` (15 seconds, one chunk):

1. Count characters in the phrase. Identify natural break points using the rules above.
2. For each sub-phrase, compute its character count as a fraction of the total.
3. Multiply that fraction by the total span (`33.08 - 18.09 = 14.99s`) to get its duration.
4. Walk timestamps forward from `start`.

Worked example:

```
Total: 60 chars, 14.99s, starts at 18.09

split candidates by particles:
 "เขาเลยต้องเลยใช้คำว่า" (20 chars) - 33.3% - 4.99s - [18.09 → 23.08]
 "กระโดดลงหน้าผา" (14 chars) - 23.3% - 3.49s - [23.08 → 26.57]
 "เพราะมันคือความรู้สึก" (18 chars) - 30.0% - 4.50s - [26.57 → 31.07]
 "ของช่วงเวลานั้น" ( 8 chars) - 13.3% - 1.99s - [31.07 → 33.08] wait, 8 chars?

(re-count chars; the example numbers are illustrative, not arithmetic-perfect)
```

Splits stay inside the original window - no drift, no overlap. If a split feels visually awkward (e.g. "และ" alone for 0.4s), merge it with the next chunk.

### Merging when timing is too tight

If two adjacent word entries are both <0.4s and read as one phrase, merge them. Use `start` of the first and `end` of the last.

### Preserving gap-close (don't re-introduce blink between captions)

The input arrives **gap-closed** (`captions.json` has `gap_closed: true`) - every caption's `end` already extends to the next caption's `start`, so caption renderers (CapCut / HyperFrames / SRT) never blink off-screen between phrases. This is the kit's default since rule #11b in CLAUDE.md.

When you re-segment in this pass (splitting a long phrase, merging two tight ones, dropping a stutter), preserve the gap-closed property:

- **Splitting** a caption into two halves at time T → first half ends at T, second half starts at T (no gap).
- **Merging** two adjacent captions → the merged caption's `end` is the second's old `end`. The next caption (after the merged one) already abuts at that point; nothing to do.
- **Dropping** a caption entirely → extend the previous caption's `end` to where the dropped one would have ended, so the timeline still abuts the caption that follows.
- **Shifting** a boundary → move both `caption[i].end` and `caption[i+1].start` to the new point together. Never leave them apart.

What that looks like:

```
Input (gap-closed):
 caption A: start=2.10s end=4.46s "ยุคนี้คือยุคแห่งผักผลไม้"
 caption B: start=4.46s end=6.24s "ละครไทยพีเรียด..."

If you split A at 3.20s (because it's two thoughts):
 ✅ A1: start=2.10s end=3.20s
 A2: start=3.20s end=4.46s
 B: start=4.46s end=6.24s ← still abuts A2
 ❌ A1: start=2.10s end=3.10s
 A2: start=3.25s end=4.46s ← introduced a 150ms blink
 B: start=4.46s end=6.24s
```

If you genuinely need a silent gap (rare - e.g. a deliberate dramatic pause where the previous line should NOT linger), say so in `notes[]` so the next reader knows it's intentional. By default, keep adjacent captions abutting.

Per-token timing inside `tokens[]` is never touched here - those are the karaoke beat from ElevenLabs char-level timestamps and must stay accurate. Gap-close only operates on the caption's outer `start`/`end`.

---

## 4. Propose emphasis words

Pick **3–8 words** across the whole transcript that deserve visual punch. Categories:

- **Brands and products** - `Claude`, `GPU`, `Cursor`
- **Stats and numbers** - `90%`, `1.2 trillion`, `3 ปี`
- **Contrast words** - `but`, `แต่`, `however`, `จริงๆ`
- **Payoffs and reveals** - `ที่สุด`, `winner`, `ฟรี`, `secret`
- **CTAs** - `subscribe`, `like`, `link in bio`, `กดติดตาม`

For each emphasis word, propose:

- `start` and `duration` (the word's actual timing from `groups[]`)
- `color_hint` - one of `accent` / `green` / `red` / `yellow` / `blue`. Pick by meaning: green = positive payoff, red = negative/contrast, accent = brand. Don't apply a colour the user didn't approve at the build stage; this is a **suggestion** for the build step.
- `size_hint` - `small` / `normal` / `large`. Use `large` sparingly (1–2 per video).
- `entrance` - `drop`, `slam`, `slide-l`, `slide-r`, `pop`. `slam` for hard reveals; `pop` for quick hits; `drop` is the safe default.

Don't apply styling - just recommend in `emphasis_words[]`. The build skill or the user picks final styling.

You can also mark substrings inside `groups[].emphasis[]` to colour them inline within the caption pill (`"text": "GPU ทำได้", "emphasis": ["GPU"]`). Use this for words you don't want to also pull out as a standalone punch - it's the lighter weight emphasis.

---

## 5. Build hook + audio beats

### Hook

The first 2–3 seconds of the rendered video. Two-line structure:

```json
"hook": {
 "line1_text": "<setup - what's the topic / question / contrast>",
 "line2_text": "<punch - the stat / claim / surprise>",
 "duration_sec": 2.5
}
```

The hook draft has to come from content that lives *inside* the first 2.5 seconds of the source audio. That's the window the hero text actually overlays on screen - pulling a line from second 11 and putting it on second 0 desyncs the visual punch from what the viewer is hearing.

So:

1. Read `groups[]` entries that fall inside `[0, 2.5]`.
2. If the speaker has already delivered a clear setup/punch in those 2.5 seconds, use those lines directly.
3. If they haven't (the first 2.5s are still throat-clearing or topic intro), draft a 2-line hook *about* the setup - keep it short, keep it tied to what the viewer will hear in the same window. Do not copy a powerful line from later in the clip and pretend it belongs at the start.
4. If the audio simply doesn't support a hook in its first 2.5s, leave `line1_text` and `line2_text` short and topic-only, and note in `notes[]` that the user might want to re-cut the source so the hook material lands earlier.

### Audio beats

`audio_beats[]` are SFX cues. Each cue:

```json
{ "at": <float seconds>, "suggested_sfx": "<id>", "volume": <0–1> }
```

`audio_beats[]` is optional in this kit version - fill it only if the user explicitly asked for SFX suggestions. The shape is documented here so downstream tools that DO render captions (HyperFrames compositions, custom pipelines) have a contract to consume. Use generic onomatopoeia for `suggested_sfx` (`"whoosh"`, `"ding"`, `"riser"`, `"pop"`, `"beat-drop"`) - the rendering tool maps those to actual sound files.

SFX budget rule (when you DO emit cues):

- 5–7 cues per 60 seconds of video. Total. Not per caption.
- Minimum 3 seconds between cues.
- Each cue must answer "would this moment land worse without this sound?" If answer is "same" or "marginal", don't include it.

When in doubt, include fewer cues, or omit `audio_beats[]` entirely. The downstream tool can always add SFX later.

---

## 6. Emit captions.json

Final structure (must validate as valid JSON):

```json
{
 "duration": 56.4,
 "language": "th",
 "source_provider": "elevenlabs",
 "gap_closed": true,
 "post_processed_at": "2026-05-10T14:30:22+07:00",
 "groups": [
 { "start": 0.36, "end": 1.62, "text": "ไม่ใช่ทุกคนทำ", "emphasis": [] },
 { "start": 1.62, "end": 2.38, "text": "GPU ได้", "emphasis": ["GPU"] }
 ],
 "emphasis_words": [
 { "word": "GPU", "start": 1.62, "duration": 0.6,
 "color_hint": "accent", "size_hint": "normal", "entrance": "drop" }
 ],
 "audio_beats": [
 { "at": 0.05, "suggested_sfx": "click", "volume": 0.6 }
 ],
 "hook": {
 "line1_text": "ข้อจำกัดใหม่ของโลก",
 "line2_text": "คือ ฮาร์ดแวร์",
 "duration_sec": 2.5
 },
 "notes": [
 "Brand 'GPU': raw said 'จีพียู', restored to Latin (audience reads English).",
 "Phrase at 18.09–33.08 split into 4 groups by particles."
 ]
}
```

`notes[]` is the audit trail. Every non-trivial decision goes here. The reviewer (main agent or user) reads `notes[]` to know what changed from `raw.json`.

---

## 7. Quality bar

Before saving, self-check against these. If any fail, fix and re-emit:

- [ ] Every spoken phrase from `raw.json` appears in `groups[]`. No silent drops. Unintelligible portions are marked, not deleted.
- [ ] No `groups[]` entry is longer than 4 seconds. (Beyond ~4s a single caption stops feeling tied to the audio.)
- [ ] No `groups[]` entry has 0 or negative duration.
- [ ] Adjacent groups don't overlap in time.
- [ ] **Adjacent groups abut (no silent gaps)** - `groups[i+1].start == groups[i].end` for every pair, unless you explicitly noted an intentional dramatic pause in `notes[]`. This preserves the gap-closed input contract so caption renderers don't blink between phrases. See [§3 Preserving gap-close](#preserving-gap-close-dont-re-introduce-blink-between-captions) and edit-rules #11b.
- [ ] `gap_closed: true` is set at the top level (unless you intentionally re-introduced gaps, in which case set it to `false` and explain in `notes[]`).
- [ ] Total `duration` matches `raw.json.duration` within 0.5s.
- [ ] All `emphasis_words[].word` strings appear inside one of the `groups[].text` (otherwise the build can't anchor them).
- [ ] If `audio_beats[]` is present, every `suggested_sfx` is a generic onomatopoeia string (`"whoosh"`, `"ding"`, `"riser"`, etc) - downstream tools map those to real files.
- [ ] `language` matches what was used at `--lang`. Not auto-detected per group.
- [ ] **Language-content match:** if `language` is `th`, `text` fields contain Thai characters; if `en`, they contain mostly Latin. A Thai-flagged transcript with all-English text means the provider silently translated and you must surface this (see [§1 Language-mismatch sanity check](#language-mismatch-sanity-check)).
- [ ] **Hook is grounded:** `hook.line1_text` and `hook.line2_text` describe content that's actually delivered in the first 2.5 seconds of audio. Pulling later content into the hook desyncs the visual from what the viewer hears.
- [ ] **Every proper noun you CHANGED was verified against the picture or is flagged unverified.** If you rewrote a brand/product/place name, either you read the frames covering that timestamp (see [§1](#look-at-the-video-before-guessing-a-proper-noun)) and they confirm it, or `notes[]` says it is an unverified phonetic guess. Never silently substitute a confident spelling you did not check - a wrong brand ships because it looks right.
- [ ] `notes[]` has at least one entry per non-trivial decision (brand pick, phrase split, dropped filler, etc.). Empty notes on a Thai transcript is suspicious.

---

## 8. Examples

### Example 1: Grok Thai phrase-level → split

**Input** (`raw.json.words[]` excerpt):
```json
{ "text": "เขาเลยต้องเลยใช้คำว่ากระโดดลงหน้าผาเพราะรู้สึกแบบนั้น", "start": 18.09, "end": 33.08 }
```

**Output** (`captions.json.groups[]` excerpt):
```json
[
 { "start": 18.09, "end": 22.50, "text": "เขาเลยต้องใช้คำว่า", "emphasis": [] },
 { "start": 22.50, "end": 26.10, "text": "กระโดดลงหน้าผา", "emphasis": ["กระโดดลงหน้าผา"] },
 { "start": 26.10, "end": 30.20, "text": "เพราะรู้สึก", "emphasis": [] },
 { "start": 30.20, "end": 33.08, "text": "แบบนั้น", "emphasis": [] }
]
```

`notes[]`:
- "Phrase 18.09–33.08 split into 4 groups using conjunctions (เลย, เพราะ) and particles (แบบนั้น). Timing interpolated by character count."
- "Dropped filler 'เลย' that appeared twice in original."

### Example 2: ElevenLabs Thai with brand transliteration

**Input** (`raw.json.words[]` excerpt):
```json
{ "text": "ใช้", "start": 12.10, "end": 12.32 },
{ "text": "เคอร์เซอร์", "start": 12.32, "end": 12.95 },
{ "text": "เขียน", "start": 12.95, "end": 13.22 },
{ "text": "โค้ด", "start": 13.22, "end": 13.55 }
```

**Output** (`captions.json.groups[]`):
```json
{ "start": 12.10, "end": 13.55, "text": "ใช้ Cursor เขียนโค้ด", "emphasis": ["Cursor"] }
```

`notes[]`:
- "Loanword 'เคอร์เซอร์' → 'Cursor' (audience reads Latin)."

`emphasis_words[]`:
```json
{ "word": "Cursor", "start": 12.32, "duration": 0.63,
 "color_hint": "accent", "size_hint": "normal", "entrance": "drop" }
```

### Example 3: brand disambiguation

**Input**:
```json
{ "text": "ผมใช้ เคิร์ส เขียนโค้ดทุกวัน", "start": 5.0, "end": 7.5 }
```

**Topic**: "AI coding tools for developers" (from user)

`เคิร์ส` is ambiguous: could be `Cursor`, could be `cursor` (the actual cursor on screen). Topic narrows it: in an AI-coding-tools video, `Cursor` (the editor) is the more likely meaning.

**Output**:
```json
{ "start": 5.0, "end": 7.5, "text": "ผมใช้ Cursor เขียนโค้ดทุกวัน", "emphasis": ["Cursor"] }
```

`notes[]`:
- "Brand pick: 'เคิร์ส' → 'Cursor' (the AI editor) based on topic 'AI coding tools'. Other candidates considered: 'cursor' (UI element) - rejected as low-content."

---

## When this protocol fails to produce good output

If after a careful pass the output still looks wrong, the right move is **not** to ship it. Either:

1. Re-spawn the post-process subagent with a fix prompt that calls out the specific problem (e.g., "the phrase at 18s is still a single 15s chunk - split it by particles per §3").
2. If the audio itself is the problem (overlapping speakers, too much background noise), tell the user. Don't paper over bad audio with cleverness.

The goal is a transcript that, when read alongside the audio, never makes you think "wait, that's not what they said." Anything less and the captions break trust the moment they hit screen.
