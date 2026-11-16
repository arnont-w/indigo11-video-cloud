# Level 1: WATCH (understand, summarize, find, choose)

This is the complete `video-understand` method. Its scripts are byte-identical copies in `$SKILL_DIR/scripts/` (checked 2026-09-27), and this file carries its full guide, so indigo11-watch works without the original skill.

Starter note: the scripts are run with `python3` (Windows `python`) from the starter folder. They switch to the Python the installer prepared by themselves (`scripts/_starter.py`). Extra packages: `python3 _system/setup/common/watchdeps.py`.

Read a video the way a person does: see every shot, hear the words, notice what moves and what goes quiet, and tie it all to the clock.
- `index.py`: one cheap pass over every shot (motion, light, sharpest moment), loudness hits and silences, plus on-screen text over time with `--ocr`. Its output is text you can read and grep.
- `frames.py`: one frame per shot (never skips a shot), plus extra frames through long takes, packed into timestamp-labelled grids.
- `transcript.py`: a timestamped transcript (ElevenLabs Scribe v2 by default).

**The transcript is required whenever there is speech.** A grid shows what is on screen, never what was said. On a 24-minute episode the grid alone gives the arc, but every fact that matters (what was stolen, the gadget's name and rule, how it ends) lives in the audio. Skipping the transcript produces confident, plausible, wrong summaries. Skip it only when there is no speech or the user asks for visuals only. Without speech, `index.py --ocr` carries the words: captions, signs and title cards.

## Before you start
- `SKILL_DIR` = the folder holding indigo11-watch's `SKILL.md`. Scripts are in `$SKILL_DIR/scripts/`.
- **URLs: download first**, then pass the local file:
  `python3 "$SKILL_DIR/scripts/download.py" "<url>" --out <work>` (yt-dlp from the starter; prints `VIDEO: <path>`; a single IG reel works without login; YouTube may give only 360p, enough for WATCH)
- **ElevenLabs key**: `transcript.py` reads `ELEVENLABS_API_KEY` from the shell or the pack's `.env` (never print it). No key: it falls back to captions (URLs) or local whisper if installed (`pip install faster-whisper` into `~/.indigo11/venv`), else says "no transcript" and you continue with frames + `--ocr`.

## Recipe by job
| Job | Run |
|---|---|
| Short clip (< ~5 min): what happens, explain it | transcript (background) + `index.py --ocr` + `frames.py --index` |
| Film with burned-in subtitles | `index.py --ocr --ocr-lang <lang>`. The OCR track IS the transcript, so skip the paid one |
| Silent: sight gags, room tours, b-roll, animation without VO | `index.py --ocr` + `frames.py --index`; transcript only if there is speech |
| Long video / film: recap, spoiler, pick scenes | transcript (background) + `index.py` + `frames.py --index`; read every grid, then zoom |
| One moment ("what happens at 1:02:10") | `frames.py --start --end --cells 16` over that window |
| Choosing footage from many clips | `cull.py --text`: one ranked table + one contact sheet |
| Which clips have burned-in text | `text_check.py` |

## Step 1: transcript FIRST, in the background
The transcript dominates wall-clock time and shares nothing with steps 2–3, so launch it with `run_in_background: true` and build the index and grids while it runs.
```bash
python3 "$SKILL_DIR/scripts/transcript.py" "<video>" --out <work>/transcript.json [--lang th] [--source auto|elevenlabs|captions|whisper]
```
- It prints `TRANSCRIPT: <path>`. The JSON is `{source, language, duration_s, segments:[{t_ms, end_ms, t_label, text}], words:[…]}`, and the words carry word-level timing.
- **Sources, tried in order**:
  1. elevenlabs: best for Thai
  2. captions: URLs only, free
  3. whisper: offline, weaker on Thai
- Omit `--lang` to auto-detect.
- **Don't try to speed it up**: chunking into parallel calls was 2.2× slower (the API rate-limits per key), and a smaller Opus upload was 3× slower. One whole-file call is the fast path.
- **Exit codes**: 2 = no usable source, 3 = the source returned nothing. If it fails, say so plainly and continue with frames only. Never invent dialogue.

## Step 2: index
```bash
python3 "$SKILL_DIR/scripts/index.py" "<video>" --out-dir <work> [--ocr --ocr-lang th]
```
It prints `INDEX: <path>/index.md`. **Read `index.md` WHOLE.** There is one line per shot: `#n start-end (dur) | motion, light, sound; sound hit 00:12.3`. The bands are relative to this video ("high motion" = the busiest quarter of its shots). Text spans (with `--ocr`) and silences come below the shots.
- **Cuts**: rapid short shots = action, argument, montage. One long shot = walkthrough, take, speech.
- **Sound without words**: a `sound hit` is a sudden loudness jump (slam, laugh, music sting). In a silent comedy the hits are the punchlines, so check the frames just before each one.
- **Silence** after a line is often the joke landing, or a beat meant to be felt. Look at the frames inside it.
- **`--ocr-lang`** names the script to read (default `zh,en`). Use `th` for Thai subtitles, or `ja`, `ko` and so on. On a Thai film the default returned zero Thai lines, while `--ocr-lang th` returned the dialogue verbatim.
- **Measured cost**:
  - 2h05m film: 69 s (1,727 shots); with `--ocr --ocr-lang th` 356 s (1,278 subtitle lines)
  - 2:45 phone clip with `--ocr`: 7 s
  - `--ocr` needs macOS + `swiftc`, or `tesseract`

## Step 3: frames
```bash
python3 "$SKILL_DIR/scripts/frames.py" "<video>" --out-dir <work> --index <work>/index.json [--cells 64]
```
- **Output**: it prints `MANIFEST: <path>`. `--index` reuses the shot list instead of decoding again.
- **Coverage**: every shot gets its sharpest frame, never deduplicated away. The remaining budget adds frames through long takes (up to 4/s under 1 min, 2/s under 5 min, 1/s beyond). The budget rises to the shot count (cap 2048), so a 1,727-shot film is covered 100% in 27 grids.
- **Timestamps**: every cell has its time burned in top-left (`MM:SS.d`). Cells are NOT evenly spaced, so read the stamp (or the manifest's `t_ms`) and never infer time from grid position.
- **Pick `--cells` by the QUESTION, not the video length**:

| `--cells` | px per cell | Use for |
|---|---|---|
| 16 | ~392 | small text: UI labels, player time, small subtitles |
| 36 | ~261 | facial expression, fine action, motion beats |
| **64** | ~196 | **plot, scene inventory, recaps, spoilers (default)**; big captions and title cards read fine |
| 100 | ~157 | rough skim of very long footage; small text is lost |

(The px figures are for landscape. Portrait video automatically gets more columns so the grid stays near-square.)

**Flags**:
- `--start` / `--end` (`SS`, `MM:SS`, `HH:MM:SS`): focus one section. Prefer this over a sparse scan when the user asks about one moment.
- `--budget N`: cap the total number of frames (thinned evenly)
- `--max-grids N`
- `--no-labels`
- `--force keyframe|uniform`

**Zoom, don't re-read.** For a long video:
1. Read the index, the transcript and every grid once.
2. Zoom into the scenes the question hinges on with `--start/--end --cells 16|36`.

Don't crop single stills to "look closer".

**Cue frames.** If the speaker points at something ("look here", "ตรงนี้") and the sampling missed it, grab exactly those instants:
```bash
python3 "$SKILL_DIR/scripts/frames.py" "<video>" --out-dir <work>/cues --at 4:32,7:10 --cells 16
```

## Step 4: read everything
1. Read `index.md` whole.
2. Read every `grids[].path` from the manifest **in one message** (parallel reads).
3. Read `transcript.json` **WHOLE**, in sequential chunks if it is large.
   - Never sample the transcript: no scripts that print "interesting windows", no grepping a few timestamps, no reading the first N segments.
   - Sampling means you choose what matters from the pictures, which is exactly the guess the transcript exists to prevent.
   - A 24-minute episode is ~170 segments, only a few thousand tokens.

Cell *i* of grid *g* is `grids[g].cells[i]`, with its `t_ms` and `t_label`.

## Step 5: answer
- **Combine the streams, tied together by time, and cite timestamps**:
  - frames = what is shown
  - transcript = what is said
  - index = cuts, motion, sound hits and on-screen words
- **Scene selection** (spoiler cut, highlights, b-roll pulls): answer as `start–end | what happens | why it matters`, with start/end taken from the index's shot boundaries.
- **Specific question**: answer it directly. Otherwise summarize the structure, key beats, notable visuals and what is actually said. Never paste the raw transcript back.
- **The failure mode**: pictures let you assemble a story that hangs together and is wrong, and it feels like understanding.
  - Real case: the grids showed a gadget, a boy turning into a black shadow, a fox and a falling monkey. "A gadget with shadow-creature side effects" was confident and false.
  - One line at 14:00 explained it: the gadget makes *figures of speech literal*.
  - So when frames suggest a causal story, treat it as a hypothesis and find the line that confirms it. If none does, say what you saw and that the reason is not stated.
- Delete the work dir when a follow-up is unlikely.

## Culling: the best clips from many
```bash
python3 "$SKILL_DIR/scripts/cull.py" <clip> [<clip> ...] --out-dir <work> --text [--top N] [--cells N]
```
- **Output**: it prints `CULL: <path>/cull.md`, a ranked table (sharpness, jitter, exposure, burned-in text, length) plus a contact sheet of each clip's sharpest frame labelled with its rank.
- **Reading the ranks**: sharpness and jitter are ranked within the batch, so treat the score as an ordering, then look.
- **Measured**: 20 clips in 48 s.

## Screening for burned-in text
When picking reusable footage from many downloads, screen first rather than building grids for every file:
```bash
python3 "$SKILL_DIR/scripts/text_check.py" <video> [<video> ...] --out <work>/text_check.json [--samples 6] [--max-chars 8]
```
- **How it works**: it OCRs 6 full-resolution frames per video and marks each one `clean` or `TEXT` (~2 s per clip).
- **Limit**: a subtitle shown for one second between samples can slip through, so run `frames.py` on the clean survivors before promising "no text anywhere".
- **Exit 2** = no OCR backend. Say so.

## What level 1 cannot do (say so plainly, or go to level 2)
- **Continuous motion**: easing, velocity, bounce, overshoot. Grids give positions, not the curve; a 250 ms ease is one frame at 4 fps. For that, use level 2 (`extract.py` + `track.py`).
- **Frame-exact timing**: it is only as precise as the sampled frame. Use level 2 (`strip.py`, `breakdown.py`).
- **Anything inaudible and off-screen.**

## Cost
- **Grids are ~7× cheaper**: 64 frames read one by one ≈ 12.5k tokens, while the same 64 in one grid ≈ 1.9k.
- **Past a 1568 px long edge** every grid costs ~1.9k regardless of cell count. Extra cells are free in tokens but paid for in resolution.
- **Requirements**:
  - `ffmpeg` + `ffprobe`; Python 3.10+ (the starter's)
  - OCR: macOS Vision via `swiftc` (Command Line Tools), or `tesseract`; Windows: OCR is skipped unless tesseract is installed
  - optional: yt-dlp, faster-whisper, `ELEVENLABS_API_KEY`

## When to go up a level
If the user then asks "how is this made", "what's the structure" or "I want one like this", switch to Level 2 and reuse the same work folder. The transcript and index carry over.
