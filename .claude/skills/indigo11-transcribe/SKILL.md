---
name: indigo11-transcribe
description: 'Transcribe audio or video into a clean, segmented captions.json for any downstream use: SRT export for CapCut, caption rendering, or input to indigo11-edit. Supports ElevenLabs Scribe v2 (best word timing for Thai, the default), Gemini, Grok (xAI) and OpenAI Whisper (English fallback). A mandatory post-process pass fixes brand names, splits long phrases and adds emphasis. Use when the user wants to transcribe, make captions or subtitles, or get an SRT file. Also makes stand-alone text-to-speech files (tts.py). Not for studying a reference clip made by someone else (use indigo11-watch).'
allowed-tools: Bash Read Write Task AskUserQuestion
---

# indigo11-transcribe

Reply in Standard British English (Thai is fine if the user writes Thai). Thai text stays only where it is the content, a trigger, or a literal token.

`$SKILL_DIR` = `.claude/skills/indigo11-transcribe` in the pack folder. Run from the pack folder. On Windows use `python` instead of `python3` (bare `python3` is the Microsoft Store stub). Edit-rule numbers (#N) refer to `edit-rules.md` in the pack.

## Why a post-process pass is mandatory

Raw speech-to-text (STT) is not usable as captions on its own, and each provider fails differently: ElevenLabs is accurate but writes one entry per character; Grok stretches a tone mark across seconds of silence; OpenAI drops stretches of speech and mangles loanwords; Gemini rewrites what was said (`ห้าทุ่มครึ่ง` becomes `23:30น.`). Two kinds of bug, two passes:

- **Text bugs** (brand spelled phonetically, number words, missing hyphens, stutters): pass 1, the agent reads the phrase-level transcript and checks the frames (#38).
- **Break bugs** (a caption ending on `แต่`, a short word stranded alone): pass 2, the agent writes where phrases break and `apply_breaks.py` computes every timestamp.

Thai has no spaces between words, so every provider handles it differently. Steps 2 and 3 below are not optional; skipping them ships wrong captions.

## Provider selection

| Provider (model) | Timed unit | Speech onset vs VAD (p90) | Speech landing in silence | Use it for |
|---|---|---|---|---|
| **ElevenLabs** (`scribe_v2`) | character | 40 ms | 0.8% | **Thai, always.** The default. |
| **Gemini** (`gemini-3.5-transcribe`) | word | 76 ms | 0.7% | Opt-in. Good boundaries but rewrites numbers and loanwords. 30-minute limit. |
| **Grok** (`grok-voice-transcribe-2.0`) | code point | about 1 s | 14.5% | Cheapest. Fine for a draft; loose edges. |
| **OpenAI** (`whisper-1`) | sub-word token | about 1.2 s | 33.9% | English only. `gpt-transcribe` has no timestamps, so it cannot make captions. |

Measured on one 14-minute Thai vlog (a ranking, not a spec). Default to ElevenLabs for Thai: a bad caption costs more than the API call (about $0.004 per minute). Follow an explicit provider request; otherwise ask once with `AskUserQuestion` (provider, language, words per caption group). With `--provider auto` the script uses `providers.stt.chain` from the optional config file, filtered by keys present; with no config it uses a language-aware order (Thai: elevenlabs, grok, openai).

## Modes

**A: single output**
```bash
python3 $SKILL_DIR/scripts/transcribe.py <file> --provider elevenlabs --lang th --output captions.json
```

**B: three outputs (recommended for CapCut)**
```bash
python3 $SKILL_DIR/scripts/transcribe.py <file> --provider elevenlabs --lang th \
  --save-all projects/_work/transcribe/<basename>/ --words-per-group 2
```
One API call (one STT cost) writes:

1. `raw-elevenlabs.json`: character-level raw. **Keep forever**; re-segment to any group size without paying again. Never gap-closed. The name is fixed whichever provider made it; `timing_unit` (`char`, `word`, `token`) says what `words_raw` holds.
2. `captions.json`: phrase-level, gap-closed. Post-process pass 1 before shipping. Used by `indigo11-edit` and SRT export.
3. `words.json`: captions of about N words, cut by timing first, then by Thai words. Post-process pass 2 makes it read like an editor broke it. Used by CapCut's `add_captions.py`.

Also `meta.json`. Video is converted to mono AAC before upload.

**Gap-close** (default, #11b): each group's `end` is extended to the next `start`, so the line does not blink off between phrases. Token timing inside `tokens[]` is never touched (the karaoke beat stays on the audio). `--no-gap-close` opts out (rare).

## Pipeline

1. Run `transcribe.py` (load `.env` so keys are in the environment).
2. Spawn a post-process subagent (`Task`, `general-purpose`). If `Task` is unavailable, run the protocol inline and note it in `captions.json` `notes[]`.
3. Review the diff with the user; save the final `captions.json`.

Default output folder: `projects/_work/transcribe/<video-basename>/`. Write `captions.srt` only if asked. **Deliver** the final `captions.json` / `captions.srt` / transcript to `projects/results/transcription/<YYMMDD>-<project>/` (`python3 _system/engine/save_result.py transcription <project> <files...> --copy`; `--copy` keeps `_work/` intact). Use the same project name as the video it belongs to.
## Step 2: subagent prompt (fill in the `{{...}}` parts)

```
You are post-processing an STT transcript for a CapCut editing project.

Inputs:
- Raw transcript: {{abs path to raw.json}}
- Source audio: {{abs path}}
- Source video (to check proper nouns against the picture): {{abs path, or "audio-only"}}
- Frame grids (if indigo11-edit analysis already ran): {{abs path to context.json, or "none"}}
- Topic/context: {{one line or "unknown"}}
- Target language: {{th | en | mixed}}
- Caption max words per group: {{N; default 4 Thai / 5 English}}

Read $SKILL_DIR/references/post-process-protocol.md. If the language is en or mixed,
also read $SKILL_DIR/references/caption-rules-en.md. Write captions.json to {{abs path}}.

Hard requirements:
0. Before changing any PROPER NOUN (brand, product, place), look at the picture: read the
   frame grid for the moment it is spoken, or generate one from the video. If the frames
   confirm the spelling, use it; if not, KEEP the STT spelling and flag it unverified in
   notes[]. Never ship a confident guess. Skip only for audio-only sources.
1. Every spoken phrase from raw.json must appear in groups[]. No silent drops. Mark
   unintelligible phrases "[unintelligible]" with the original timing.
2. Thai: split chunks longer than 4 seconds into 2-4-word groups at particles and
   conjunctions (ครับ, นะ, แต่, ก็, และ, ที่, หรือ), interpolating times by character count.
3. Mixed Thai and English: keep loanwords with their Thai context (`GPU ได้` is one chunk);
   restore Latin script for brands and technical terms (`จีพียู` to `GPU`).
4. Brand names: picture first, then topic context, then web search. Record the choice and
   how it was verified in notes[].
5. Output strict JSON:
   {"duration":<float>,"language":"<th|en|...>","source_provider":"<...>",
    "post_processed_at":"<ISO 8601>","gap_closed":true,
    "groups":[{"start":<f>,"end":<f>,"text":"<str>","emphasis":["<substr>"]}],
    "emphasis_words":[{"word":"<str>","start":<f>,"duration":<f>,"color_hint":"<...>",
      "size_hint":"<small|normal|large>","entrance":"<drop|slam|slide-l|slide-r|pop>"}],
    "notes":["<one line per non-trivial decision>"]}
   gap_closed is REQUIRED: adjacent groups must abut (groups[i+1].start == groups[i].end),
   including seams you create when splitting. Set false only for a deliberate pause and
   explain it in notes[]. emphasis_words is optional. groups[] and notes[] are required.
6. Finish with a short summary: provider and language, group count, special handling
   (timestamp and reason), brand/loanword corrections (before and after).

Do not modify raw.json.
```

If the result looks wrong (too few groups, missing brands, odd timing), push back with another `Task` call.

## Step 3: review before saving

Present: provider and language, group count, the first three groups, brand/loanword corrections from `notes[]`, and anything flagged low-confidence. Wait for "ok" or edits; silence is not consent. Apply edits to `captions.json` directly. Do not re-transcribe unless the audio itself was wrong.

**Downstream formats:** SRT/VTT with `python3 $SKILL_DIR/scripts/export_srt.py <captions.json|words.json> [-o out.srt] [--vtt] [--bridge 1.0]` (never build one by hand: it reads `groups[]`, or the phrase list `words[]` of a `captions.json` that has no `groups[]`, bridges gaps under 1 s, free and deterministic; CapCut imports SRT directly), Markdown notes (`**MM:SS** text`), or pass `captions.json` straight to a caption renderer.

## N-word captions (1-4 words, Submagic style)

`word_segment.py` builds `words.json` from `raw-elevenlabs.json`. **Timing first, words second** (counting N dictionary tokens broke about one caption in three on a real clip):

1. **Phrases from timing:** cut where the speaker stopped (a silence of 0.3 s or more, or a space between clauses). A space next to English or digits stays inside the phrase.
2. **Words inside each phrase** (nlpo3, or the provider's own words). The tokenizer never sees across a pause.
3. **Never split:** a particle from its word (`นะครับ`), a number from its unit (`55 นาที`), consecutive English words (`Claude Code`).
4. **About N words per caption**, inside a phrase only. A caption never ends on `แต่` / `เพราะว่า`, a short orphan joins its neighbour, and a caption on screen under 200 ms is merged. N is a target, not a hard count.

Each word keeps its real ElevenLabs timing, never interpolated.

Setup check and install (about 65 MB, once; only needed for word-level captions):
```bash
python3 -c "import pythainlp, nlpo3" 2>/dev/null && echo ready || python3 -m pip install --user pythainlp nlpo3
```
Ask before installing. Use `nlpo3`, not `attacut`: attacut pulls in 343 MB of PyTorch and over-segments informal Thai (`ตัวเอง` becomes `ตัว` + `เอง`).

```bash
python3 $SKILL_DIR/scripts/word_segment.py <dir>/raw-elevenlabs.json -o <dir>/words.json --words-per-group 2
```
Re-run with any N at no STT cost. For sentence-style lines use `--max-chars 24` (about right for 9:16). Protect names the tokenizer would split: `--custom-dict "แอนดริว,Mark Zuckerberg,GPU"` (the transcript's `proper_nouns[]` is honoured automatically).

**Pass 2 (editor-quality breaks):** get phrases with `word_segment.py ... --phrases-out <dir>/phrases.json`, write `<dir>/breaks.json` per the protocol, then:
```bash
python3 $SKILL_DIR/scripts/apply_breaks.py <dir>/raw-elevenlabs.json <dir>/breaks.json -o <dir>/words.json
```

Recommend a size when asking: 1 word (kinetic), 2-3 (fast phrase), 4-5 (ordinary subtitles).

`words.json` shape: `{duration, language, engine, segmenter, words_per_group, gap_closed, groups:[{start, end, text, tokens:[{text, char_start, char_end, start, end}]}]}`. `segmenter` is `timing-first` or `agent-breaks`. A group's outer `end` is gap-closed; token times are the real karaoke beat. All times are seconds.

## Text to speech (tts.py)

```bash
python3 $SKILL_DIR/scripts/tts.py --text "สวัสดีครับ" --out voice.mp3 [--provider auto] [--voice <id|name>] [--lang th]
```

Providers: elevenlabs (`eleven_v4`, the only family besides v3 that speaks Thai; mp3), openai (`gpt-4o-mini-tts`, shuts down 2027-01-06; mp3), gemini (`gemini-3.8-flash-tts`; wav). Each uses its own key.

`--provider auto` uses `providers.tts.chain` from the optional config, else elevenlabs, openai, gemini by available key. Prints `{ok, file, bytes, provider, model, voice}`.

## API keys and setup

Keys come from the environment first, then `.env` in the pack folder: `ELEVENLABS_API_KEY` (default for Thai, also TTS), `XAI_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`. At least one is required. If the requested provider's key is missing, say so and offer another; if Grok is used for Thai, tell the subagent its phrase-splitting workload is heavy. Also needed: Python 3.10+ (base script is stdlib-only) and `ffmpeg` + `ffprobe` on PATH.
Full rules: `references/post-process-protocol.md`.
