---
name: indigo11-video
description: 'Make a whole short video (9:16, up to 59 s) from one topic, in Standard British English by default (Thai is an option): research real facts with sources, pick a story template, write the script, ElevenLabs v3 voice, captions, stock B-roll, render and check the file; plus keep the project folders organised (start, close, tidy, find). Triggers: "make a clip about X", "short / reels / tiktok about X", "new project", "done", "tidy", "where is my video about X". NOT for raw footage (use indigo11-edit) or someone else''s clip.'
allowed-tools: Bash Read Write Edit WebSearch WebFetch AskUserQuestion
---

# indigo11-video: topic to short video, workflows and project folders

**Language.** Every video is in **Standard British English** (colour, organise, licence) unless the user asks for Thai. Pass `--lang en` (default) or `--lang th` to `make_short.py` and `new_short.py`. Script, on-screen captions, `caption.txt`, voice language code and the reply to the user all follow the chosen language. The Thai rules in this file apply only with `--lang th` (section "Thai option" below).
Run everything from the pack folder (Windows: `python`, Mac: `python3`). `S=.claude/skills/indigo11-video/scripts`.
`P` = the project folder `projects/<YYMMDD>-<name up to 32 chars>/`; while working, renders land in `P/result/`; `project.py done` then MOVES the video + `caption.txt` to `projects/results/video-creation/<YYMMDD>-<name up to 32 chars>/`.
Scripts use the pack's Python, ffmpeg and the keys in its `.env`; never print a key. Workflows and looks (`workflows/`, `templates/`) are not in this pack.
Brand first: if `assets/brand/BRAND.md` exists, read it (colours, tone, CTA, voice id, `lang`) before writing any script; if not, ask the user for tone and CTA.
## Part 1. Short: one topic, one finished clip
A topic is a complete order: **do everything until there is an .mp4, then report.** Choose the angle and story template
yourself and say afterwards what you chose and why. Ask back only if the topic is too vague to research (e.g. "the mountain").
Ask about the brand only if unknown (default: the brand in BRAND.md).
```bash
python3 $S/project.py new <slug> --title "<title>" [--lang en|th]          # creates P (default en)
# 1 research -> Facts table in P/assets/SOURCES.md      2 story template -> P/BRIEF.md      3 script -> P/script/script.txt
python3 $S/scan_broll.py --q "<one-word topic>" --out P/assets/broll       # 4 find footage
#   open P/assets/broll/02-frames/_sheets/all.jpg and look (one sheet; do not re-run verify.py on raw clips)
python3 $S/scan_broll.py --cut P/assets/broll --pick "c0:2-8,c3:1-6,..."   # 5 cut the chosen ranges
python3 $S/make_short.py --lang en --script P/script/script.txt --broll P/assets/broll/03-selected --out P   # 6 render
python3 $S/verify.py P/result/<slug>.mp4                                   # 7 check the real file, open the sheet
python3 $S/project.py done P                                               # 8 caption + projects/INDEX.md
```
Report: file path, length, story template, sources, anything dropped because it could not be verified, B-roll source and whether it may be posted.
### Iron rules (never skip)
1. **Every number, name and year in the script has a source** row in `P/assets/SOURCES.md` (claim | value | URL + date checked). Never write from memory. If you cannot verify it, cut it, even if it is more interesting.
2. **Posted B-roll = stock libraries only** (Pexels, Pixabay: free for commercial use). **YouTube = research and prototype only, never posted.**
   `scan_broll.py --source auto` uses Pexels/Pixabay when a key is in `.env`, otherwise YouTube plus a warning. `make_short.py` then writes
   `result/<slug>.NOT-FOR-PUBLISHING.txt`: do **not** run `done` as a publishable job; tell the user to add a free stock key.
3. **Never use B-roll you have not looked at.** Open `_sheets/all.jpg` (red frame = caption zone; label = `c<clip> <second>s`).
4. **At least 5 different clips** per video (`make_short.py` enforces it; do not disable). Once you have 5 good ones, stop searching.
5. **Always pass `--script`.** Captions are split on the spaces in the script, not by a tokenizer.
6. **Length up to 59 s** (spoken English is roughly 2.5 words or 15 characters per second: 59 s is about 145 words, 30 s about 75; Thai is 14.25 characters per second: 30 s is about 427 characters, 59 s about 840). 59 s is the ceiling, not the target: keep one idea per clip, 20-40 s is typical, and go beyond 30 s only when the story needs it. Platform Shorts limits sit at 60 s, hence 59.
7. **Check the real file before delivery:** captions appear throughout, are readable, fit in two lines, and no watermark or foreign text sits mid-screen.
### 1. Research
- WebSearch for primary sources (studies, agencies, major media), then WebFetch and read them; snippets are not enough.
- Log every fact in the Facts table before writing the script.
- Find "the common mistaken belief + the fact that really disproves it" (the heart of the framework). If none exists, use MECHANISM instead.
### 2. Pick the story template (by the facts, not by habit)
| Facts look like | Template | Opening line |
|---|---|---|
| Why something happens | MECHANISM (best) | `why does {X} {phenomenon}` |
| A price that seems unreasonable | PRICE_JUSTIFY (best) | `what makes people pay {price} for {X}` |
| Comparison or a ranked list | RANKING (best) | `ever wondered which {unit} has the most...` |
| A danger people do not know | WARNING | `don't {verb} {X} until you know this` |
| Origin differs from belief | MYTH_BUST | `the world may be wrong that {X} belongs to {Y}` |
| Past view opposite to today's | ORIGIN_STORY | `in {year} everyone thought {X} was terrible` |
| A person or brand did something odd | BIZARRE_EVENT | `someone did {odd thing}, but what came back...` |
| It is not what you think | REVEAL_TRUE_NATURE | `if you knew what {X} really is...` |
Details, cross-topic examples and the decision tree: `references/story-framework.md`.
- Five beats: HOOK (0-3 s), SETUP, **PIVOT at about 50%** (25-75%), PAYOFF (hard proof: numbers, evidence), CUT (short knowledge clips have no CTA).
- Do not default to "Did you know that": the reference channel uses it in only 12% of clips. Avoid the opening the brand used in its previous clip.
- Write the chosen template into `P/BRIEF.md`.
### 3. Write the script (`P/script/script.txt`; the whole file is spoken, so no notes)
English (default): Standard British English, short spoken sentences (about 10-14 words), contractions allowed, one idea per sentence, and a line break ends a sentence. Write numbers as digits in the script (`1,614`, `40%`); the voice reads them aloud and the captions show them. Spell out units only where speech needs it. Open with something the viewer has seen themselves, not jargon; keep jargon until after the PIVOT, in plain words. Captions break at phrase boundaries (the script's own punctuation and line breaks), about 3-6 words per caption, at most two lines.
Keep for both languages:
- **The last sentence must answer the HOOK** (loop back), never drop new information and cut.
  Hook: "ever got goosebumps at a cartoon?" Ending: "that feeling is not only in us" + "it is millions of years older than we are".
- **ElevenLabs v3 audio tags in every script** (filtered out of captions), about one per 4-6 phrases; more makes the voice jump.
  `[curious]` opens the HOOK · `[pauses]` before the question leading to the PIVOT · `[whispers]` a small reveal · `[excited]` PAYOFF · `[calm]` closing.
  Others: `[nervous]` `[sighs]` `[laughs]` `[gasps]` `[hesitates]` `[deadpan]` `[playfully]`.
- `make_short.py` checks the script before spending credit (warns, never blocks): length, "Did you know" opening, pivot at 25-75%, any numbers, and for Thai the spacing.
#### Thai option (`--lang th` only)
Thai rules (the channel style guide is not in this pack). Key points:
- **No spaces between Thai words.** A space is a pause and a caption break; add one only where you want a breath.
- **No space between Thai and English in one phrase** (`ใช้Claude Codeทำ`): a space makes a wrong pause and a wrong caption break.
- A new line ends a sentence; `...` controls a pause (it is filtered out of the captions).
- Write numbers as spoken words (`หนึ่งพันหกร้อยสิบสี่`); the script turns numbers from 10 up into digits on screen.
### 4. Find B-roll
Search order (the user's rule): **1 your own files** (`scan_broll.py` lists matching videos from `assets/videos`, `assets/images`, `projects/raw-videos` first) > **2 free stock** Pexels > Pixabay (YouTube = prototype only, never posted) > **3 draw it** (not a real scene) or **generate it** (real photo/footage needed, show the price and wait for a yes, see `indigo11-find-assets`) > **4 buying, last, only with a yes**.
```bash
python3 $S/scan_broll.py --q "<topic>" --out P/assets/broll                                  # auto: pexels > pixabay > youtube
python3 $S/scan_broll.py --source pexels --q "<topic>" "<fallback>" --out P/assets/broll -n 10
```
| | Pexels | Pixabay | YouTube |
|---|---|---|---|
| May be posted | **yes, free** | **yes, free** | **no** |
| Key | `PEXELS_API_KEY` (free, pexels.com/api) | `PIXABAY_API_KEY` (free, pixabay.com/api/docs) | none |
| Good for | general, animals, nature, cities | like Pexels, sharper, bigger files | real brands/events (prototype only) |
- Put keys in the starter's `.env` (`_system/env-template.txt` shows the lines).
- **Search term = one word for the topic** (`beer`, not `guinness`); add fallbacks in the same command. For animals avoid `funny/cute/best`.
- Output: `01-raw-clips/`, `02-frames/_sheets/all.jpg`, `index.json`, `_source.json`, `SOURCES.md`, and rows in the Files table of `P/assets/SOURCES.md`.
- Landscape clips are fine; the render crops the centre to 9:16. YouTube "not a bot" error: set `YTDLP_BROWSER=chrome` and retry.
### 5. Look, then cut
Reject only for two reasons: a watermark or big text **mid-screen** (outside the red frame), or footage so dark/blurred it is unreadable.
Text inside the red frame passes (a shadow covers it at render). "Not exactly on topic" is not a reason: the same vibe is fine.
Detailed criteria: `references/verify-broll.md`. Then run the `--pick` command above.
### 5b. Voice: ask before the first voice-over
Run `python _system/engine/voice.py show`. If it names the user's own voice for the clip's language: use it, say its name in one line, do not ask. If it says built-in and the user has not said which voice to use, ask ONE question (`AskUserQuestion`): **my own voice** (they give the voice id and a name, or want it cloned from a recording) / **the default voice for this language** (en: Oliver Silk or Karina; th: Songkran narration, PapaAndy social media (paid plan) or Mali) / **another voice from my ElevenLabs account** (`voice.py list`, they pick). Then save the answer at once (`voice.py use`, `clone` or `default`), so the next session already knows. Whenever the user tells you a voice id or name in chat, run `voice.py use <id> --name "<name>"` before anything else. `make_short.py` stops with exit code 3 and the same four choices if no voice has been chosen, so nothing is spent on the wrong voice.

### 6. Render
`make_short.py` does it all: voice (eleven_v3 with timestamps), Thai captions, B-roll cuts of 3.6 s without repeats, ding + voice + soft music,
captions burned in by Chrome, output `P/result/<slug>.mp4`.
| flag | default | note |
|---|---|---|
| `--bgm` | `auto` | music from `assets/music/` (also `.claude/skills/indigo11-video/music/`) (only music you have the right to use; names start `calm-`, `tense-`, `upbeat-`); none = no music; or a path / `none` |
| `--ding` | `notify` | opening sound, role `notify` in `assets/sound-effects/`; path / `none` |
| `--voice` | the user's own voice first (`brand.json` `voices.<lang>`, else `voice`; set with `_system/engine/voice.py`); only if none, the built-in voice per language: en: Oliver Silk (British male, default) or Karina (British female, `--gender female`); th: Songkran (male, narration, default), PapaAndy (male, social media / selling, `--style social`, paid plans only) or Mali (female, `--gender female`) | ElevenLabs voice id (or the voice in `assets/brand/brand.json`). Alternatives: Karina `BcIZ7kngidiArTkRyUAp` (British female); Thai Mali `7XW5JhaNn90bnFojQ5DA` (female), PapaAndy `KhBRXgEQjvfdIyXOu7G3` (male, selling, paid plans only). All are public library voices: add each to My Voices first |
| `--seed` | 1614 | same voice every run |
| `--skip-tts` | - | reuse `P/src/.work/voice.json`, no credit (re-render after picture/caption fixes) |
| `--no-voice` | - | no ElevenLabs key: silent, estimated timing, for checking captions/picture only (gets NOT-FOR-PUBLISHING) |
Caption tuning: top of `scripts/english_captions.py` (`MAX_CHARS = 36`, `MAX_WORDS = 6`, `MIN_CHARS`, `SENT_GAP`); Thai: `scripts/thai_captions.py` (not in this pack).
Caption look: English = the ASS style at the top of `make_short.py` (`ASS_HEAD`: Arial bold 80, black outline, soft shadow, pop-in, lower third); Thai = `scripts/render_caps.mjs` (Noto Sans Thai 800, 82 px, shade at y 1090-1570).
Working files: `P/src/` (`.work/` can be deleted); `P/assets/broll/02-frames/` can be deleted after picking.
### 7. Check and close
`verify.py` makes a contact sheet of the real file, cropped like the render. Look at it yourself, then write `P/caption.txt`
(no markdown or emoji; one main source line is allowed) and run `project.py done P`.
If `result/<slug>.NOT-FOR-PUBLISHING.txt` exists, do not close it as a publishable job: tell the user to use stock footage or add a real voice first.
Needs: ffmpeg + ffprobe **built with libass** (English captions are burned in by ffmpeg: no Chrome), Python with Pillow, `ELEVENLABS_API_KEY`, optional free `PEXELS_API_KEY` / `PIXABAY_API_KEY`, yt-dlp (YouTube only). Thai (`--lang th`) also needs `pythainlp`, Node + puppeteer-core + Chrome and a Noto Sans Thai font in `assets/fonts/NotoSansThai/`.
Say plainly to the user:
- Picking clips still needs a human eye (OCR misses faint text); do not claim it is fully automatic.
- ElevenLabs gives very few pause marks, so the script drives the caption breaks: a badly spaced script gives bad captions.
- YouTube clips belong to others: for viewing and prototypes only. ElevenLabs voices are commercial-use only on a paid plan.

## Part 2. Workflows and templates (not in this pack)
The original skill could run saved recipes (`workflows/*.md`), list `templates/` and save a finished project as a recipe with `workflow.py`. This pack has no `workflows/` or `templates/` folder, so Part 1 (a short from one topic) is the supported route. Steps 1-3 of Part 1 (research with sources, story template, script) are the method every recipe would use.

## Part 3. Project: start, close, find, tidy
```
assets/brand/BRAND.md (+ brand.json, logo/)   the one brand: tone, colours, fonts, logo, voice id, caption style, CTA
projects/<YYMMDD>-<name up to 32 chars>/   project.json BRIEF.md NOTES.md script/ assets/(+SOURCES.md) src/ result/ (render output while working)
projects/results/video-creation/<YYMMDD>-<name up to 32 chars>/   <slug>.mp4 + caption.txt (delivered by `done`)
projects/INDEX.md               date | template | title | folder | video | status (built from every project.json)
```
**new:** ask only what is missing (a short slug a-z 0-9 -, the title, the language if not British English). Run `python3 $S/project.py new <slug> --title "<title>" [--lang th]`, fill `BRIEF.md`, then follow Part 1. Never copy an old project folder.
**done:** only after you have looked at the `verify.py` sheet of the real file and the user is happy.
Write `result/caption.txt` following BRAND.md (keep any music credit line from the mix report), then `python3 $S/project.py done <folder> [--title "<title>"]`:
it moves the video and caption to `projects/results/video-creation/<YYMMDD>-<name up to 32 chars>/`, sets status done, notes it in NOTES.md and rebuilds `projects/INDEX.md`. With no caption it drafts one from the voice script
and the BRAND.md CTA; tell the user to check it. Give the user both paths in that results folder and open the video.
**where:** `python3 $S/project.py where <words...>` (title, template, date, folder; no words = the latest 10). Answer with title, date and video path.
`projects/INDEX.md` is the source of truth.
**tidy:** `python3 $S/project.py tidy` prints a plan only (folders not named `<date>-<slug>`, loose files). Show it to the user, then
`tidy --yes` moves them. It never deletes; every move is a row in `projects/TIDY-LOG.md`. Never delete or overwrite the user's files yourself;
anything the tool misses, move by hand and log it. `projects/_work/` belongs to the footage skills: leave it.
**index / check:** `project.py index` rebuilds `projects/INDEX.md`; `project.py check` prints one line for the doctor (`OK ...` or `WARN ...`).