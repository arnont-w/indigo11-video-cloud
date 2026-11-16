# Caption rules for English videos (Standard British English)

The English counterpart of the Thai caption rules in `post-process-protocol.md` (§3) and `edit-rules.md` (#11, #11b, #12). Use it when the target language is `en`, or for the English parts of a mixed video. Timing limits are the same ones the scripts already enforce; the text and break rules below are for the agent's post-process pass.

Status: written for the seminar pack. The timing limits come from the existing scripts. The spelling, line and reading-speed rules are standard subtitle practice. Test them on one real English clip before you rely on them.

## 1. Size and breaks
- **3-5 words per caption** (default 5). For karaoke-style (word-by-word highlighting) use 2-3 words.
- On a vertical 9:16 frame keep a line to about **24 characters** (`word_segment.py --max-chars 24`) and at most **2 lines**.
- Break at a natural pause: the end of a clause, before a preposition (`in`, `on`, `with`), or before a conjunction (`and`, `but`, `because`, `so`).
- Never end a caption on an article or a small word (`a`, `the`, `to`, `of`, `and`). That word belongs with the next caption.
- Keep together: a name (`Claude Code`), a number and its unit (`55 minutes`), a phrasal verb (`set up`, `turn off`), an auxiliary and its verb (`will be`, `can't do`).
- A break at a breath (a pause of 0.3 s or more in the audio) beats any grammar rule.

## 2. Timing (same limits as the Thai rules)
- A caption stays on screen for at least **0.2 s** (0.1 s for one-word captions) and at most **4 s**.
- Aim for a reading speed of **17 characters per second or less**. If a caption is too dense, split it or shorten it.
- Captions **abut**: each one starts when the previous one ends, with no blink. A gap up to 1.2 s is bridged; a longer silence clears the screen (edit-rules #11b).
- Never write timestamps by hand. Let the scripts derive them from the word timing.

## 3. Wording
- Write exactly what is said. Do not translate, paraphrase or "improve" the speaker.
- Remove fillers and stutters (`um`, `er`, `you know`, repeated words) unless they carry meaning or the speaker's character.
- Keep the speaker's contractions (`don't`, `it's`).
- Brand and product names keep their official spelling and capitals (`CapCut`, `ElevenLabs`, `ChatGPT`). If the speech-to-text turned a brand into a common word, fix it, but only after checking the picture or flagging it as unverified (post-process protocol §1).

## 4. Spelling and style (Standard British English)
- British spelling: colour, organise, analyse, centre, licence (noun), programme (not a computer program), grey.
- Use `-ise`, not `-ize`, unless the speaker is quoting a name (`Organization` inside a brand).
- Numbers: write one to nine in words, 10 and over as numerals. Always use numerals with units, times and percentages (`5 minutes`, `9:30`, `20%`). Never start a caption with a numeral if you can avoid it.
- Dates: `7 November 2026`, day first.
- Quotation marks: single (`'like this'`); double only for a quote inside a quote.
- Capitals: sentence case. Do not use ALL CAPITALS except for acronyms (`CTA`, `SFX`) or one deliberate emphasis word.
- Punctuation: keep `?` and `!`. Drop the full stop at the end of a caption in short-form video; keep it when a caption holds two sentences.
- Do not add an emoji unless the speaker asked for it.

## 5. Emphasis
- At most **one highlighted word per caption**, chosen from the words that carry the meaning (the number, the verb, the surprise). Never highlight small words.
- The highlighted word must appear exactly in the caption text, otherwise the build cannot find it (quality bar in the post-process protocol).

## 6. Quick self-check before you save
- [ ] No caption ends on `a`, `the`, `to`, `of`, `and`.
- [ ] No caption is longer than 2 lines or 4 s, or shorter than 0.2 s.
- [ ] British spelling throughout, numbers as in section 4.
- [ ] Names and brands spelled correctly and checked.
- [ ] Captions abut, with `gap_closed: true` in the file.
- [ ] Every spoken word is present (nothing silently dropped).
