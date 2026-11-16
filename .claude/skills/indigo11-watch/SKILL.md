---
name: indigo11-watch
description: 'Watch, break down or copy any reference video, local file or URL, with or without speech. Level 1 WATCH: shot index, frame grids, transcript and OCR to summarise, find a moment or cull clips. Level 2 BREAKDOWN: measure cuts, pacing, beats, SFX hits, colours, fonts and motion into a SPEC. Level 3 COPY: turn the SPEC into your own style pack or a frame-locked remake. Triggers: "watch this video", "summarise this clip", "what happens in this video", "what did they say at 3:20", "break down this video", "copy this style", "remake this clip", or any pasted video path or URL with a question about it. Not for plain editing with no reference video.'
---

# indigo11-watch: watch, break down, copy

Level 1 is the complete video-understanding method (scripts in `scripts/`, full guide in `references/1-watch.md`). Levels 2-3 add measurement tools. `SKILL_DIR` = `.claude/skills/indigo11-watch` in the pack folder; run commands from the pack folder with `python3` (Windows: `python`). If a package is missing, install what the script header lists.

## Step 0: pick the level by purpose (ask one question if unclear)

| Level | The user wants | Cost | Read |
|---|---|---|---|
| **1 WATCH** | to know what happens: summary, recap, finding a moment, choosing clips | cheap | `references/1-watch.md` |
| **2 BREAKDOWN** | to learn how it is made: structure, pacing, motion numbers, sound map, style. Output is `SPEC.md` | high (about 300-500k tokens per clip) | `references/2-breakdown.md` |
| **3 COPY** | to make their own from it. **3A** style transfer (same look, new story). **3B** frame-locked remake | very high | `references/3-copy.md` |

- Default to the lowest level that answers the request.
- If unclear, ask: "What is it for: just understanding it, working out how it is made, or making your own version?"
- A URL must be downloaded first: `python3 $SKILL_DIR/scripts/download.py "<url>" --out <work>` (prints `VIDEO: <path>`). A single Instagram reel downloads without login; YouTube may give only 360p, enough for WATCH.

## Scripts (`$SKILL_DIR/scripts/`)

| Script | Level | What it does |
|---|---|---|
| `index.py`, `frames.py`, `transcript.py`, `ocr.swift`, `cull.py`, `text_check.py` | 1 | shot index (motion, light, sound hits, silences, `--ocr` text), one-frame-per-shot grids, transcript, clip culling, burned-in-text check |
| `breakdown.py` | 2 | grids with exact frame labels, `shots.jpg`, `signals.json` (cuts, motion, audio onsets), `words.txt` |
| `strip.py` | 2 | every frame of a short window, labelled with its frame number |
| `overview.py` | 2 | one frame every N seconds (long videos) |
| `extract.py` | 2-3 | all frames 0-based (`fNNNNN.jpg`) plus `audio.wav` and `info.json` |
| `track.py` | 2-3 | per-frame numbers: `template`, `ink`, `bands`, `edge`, `grade`, `color`, `crop` |
| `audio_cues.py` | 2-3 | tempo, beat grid, musical cues, percussive hits (SFX), silences, loudness arc |
| `pacing.py` | 2 | cuts versus sentence starts |
| `vtt_compact.py` | 1-2 | de-duplicate rolling captions |
| `popscan.py` | 2-3 | hard cuts and single-frame pops, loop period; also QA on your own renders |
| `compare.py` | 3 | reference versus yours: side-by-side mp4, frame-exact pair sheet, drift score |
| `download.py` | all | URL to mp4 |

Run each as `python3 $SKILL_DIR/scripts/<name>.py`.

## Rules at every level

1. **Transcript whenever there is speech.** A grid never shows what was said. With no speech, `index.py --ocr` carries the words.
2. **Measure, do not eyeball** (level 2 up): cuts from frame differences, positions from `track.py`, colours from pixels, timing from `audio_cues.py`. Frames are the truth.
3. **Cite frames and times** (`f123 / 4.10s`) for every claim, and say when something is a guess.
4. **Working files** (frames, sheets, numbers) live in `projects/_work/watch/<video-id>/`. **Deliver** `SPEC.md` and the summary to `projects/results/reverse-engineering/<YYMMDD>-<video-id>/` (`python3 _system/engine/save_result.py reverse-engineering <video-id> <files...> --copy`). The reusable **style pack** (style.json, sfx/palette.json, README, stills, anim.html) goes to `assets/templates/<name>/` (`python3 _system/engine/save_template.py <name> <files...> --source "<reference>"`), so it can be used again. Never create `projects/<name>/watch/` or `projects/_watch/`.
5. **Agents:** never spawn nested sub-agents. Tell each agent to write in small appends (120 lines or fewer per write). Run three or fewer in parallel.
6. **Shell:** on macOS wrap multi-step snippets in `bash <<'EOF' ... EOF`. On Windows use `python` or Git Bash.
7. **A copy (level 3) must move.** Never deliver still images with a voice-over and captions. Build the animated page, render it with `_system/engine/render_frames.mjs` and prove the motion with `_system/engine/motion_check.py` (rules: `references/3-copy.md`, "MOTION RULE").

## Files in this skill

- `references/spec-template.md`: the `SPEC.md` layout for level 2.
- Copying rule: styles and techniques are free to learn; a 1:1 remake of someone else's video is for practice or with the owner's permission, never to publish as your own.
