> FOR CLAUDE, not for attendees. When the user says "install" or "start", do the Install section yourself (the pack is already in their folder). Personal changes go in the `my-rules/` folder only (see CLAUDE.md); never edit CLAUDE.md, SKILL.md or `_system/` for the user. Attendees read `README.txt` and `QUICK-START.txt`.

# indigo11 video pack: install guide

## What is inside
| Skill | What it does for the user |
|---|---|
| `indigo11-edit` | Analyses clips; cuts silence and retakes to an mp4 (ffmpeg); writes into a CapCut project (Thai captions, sound effects, stickers, transitions, voice-over assembly, plan first); cutout; saves named styles. Can undo. |
| `indigo11-transcribe` | Speech to text: captions, SRT files, text to speech. |
| `indigo11-find-assets` | Finds sound effects, music and B-roll, with licence notes. |
| `indigo11-watch` | Studies a reference video (summary, breakdown, copy the style). |
| `indigo11-video` | Makes a whole short video from one topic. |

Rules the skills quote: `.claude/skills/indigo11-edit/references/edit-rules.md`. Helper that finds Python, ffmpeg and `.env`: `_system/engine/_env.py`.
Skills live only in `.claude/skills/<name>` (scripts run from there).

## Install (once)
1. Short folder for the pack, for example `C:\studio` (Windows) or `~/studio` (Mac).
   Windows has a 260-character path limit. A deep OneDrive path makes the skills fail when they make their safety backup.
2. Everything from the pack is in that folder: `.claude`, `_system`, `my-rules`, `projects`, `assets` and `CLAUDE.md`.
3. **Check, then install only what is missing.** Run `python _system/engine/check_setup.py` (Mac `python3`). It reports Python, ffmpeg, bash, the pip packages, `.env` and key status (never prints a key) and ends with `Ready.` or what is missing; `--json` gives a machine-readable result. Install what it lists, then run it again. Manual equivalents:
   - Python 3.10 or newer: `python --version` (Windows) or `python3 --version` (Mac). Missing or older: install it.
   - ffmpeg and ffprobe: `ffmpeg -version` and `ffprobe -version`. Missing: install (Windows `winget install Gyan.FFmpeg`, Mac `brew install ffmpeg`). Needed for cutting to mp4, silence detection and frame checks, not for CapCut itself.
   - bash (Windows: Git Bash from Git for Windows): `bash -c "echo ok"` must print `ok`. Missing: `winget install Git.Git`, then ask the user to reopen the terminal. The skills run their preflight `.sh` files with it; Mac already has bash.
   - Python packages: `python -m pip install pythainlp nlpo3 numpy Pillow requests certifi` (pip skips what is installed).
   - Only for animated videos and Thai captions (`indigo11-watch` copies, `indigo11-video --lang th`): Node 22 or newer (`node --version`; Windows `winget install OpenJS.NodeJS.LTS`, Mac `brew install node`) and Google Chrome (the animation renderer drives it). Skip both for plain CapCut editing.
   - CapCut Desktop, a current version. Very old projects have no `Timelines` folder and are refused.
4. Keys: if `.env` does not exist yet, copy `_system/env-template.txt` to `.env` in the pack root yourself (not in `_system/`; the scripts read `<pack>/.env`). Never overwrite an existing `.env`. Then open it for the user (Windows `notepad .env`, Mac `open -e .env`) and tell them, in plain words:
   - The file is `<full path>\.env`. Paste the key after the `=` of `ELEVENLABS_API_KEY=` (no spaces, no quotes), save, close, and type `check`. ElevenLabs is the one required key (captions and transcription; Gemini, OpenAI or xAI can replace it). Get it at elevenlabs.io (Profile, API keys).
   - Optional keys, only when they want these jobs (each AI job costs money; you show the price first): `GEMINI_API_KEY` for AI images, AI B-roll video and music (one key covers all three); `OPENAI_API_KEY` or `XAI_API_KEY` as image alternatives; `PEXELS_API_KEY` and `PIXABAY_API_KEY` for free stock B-roll.
   - Never ask for a key in chat, never print it. On "check", run `check_setup.py` and look only at set or empty.
5. Brand: if `assets/brand/brand.json` has an empty `name`, or `assets/brand/BRAND.md` still has empty fields, interview the user and write the answers into both files (they must match). Ask in one batch (`AskUserQuestion`, or plain text if it is missing), and let the user skip any question ("later"); never invent an answer, leave skipped fields empty:
   1. Who are you and what do you offer or sell? What must you never claim or teach?
   2. Channel name(s), handle(s), language(s) and a one-line bio per channel.
   3. Who are your viewers (age, language, problems)?
   4. Tone: "I" or "we", style, what you never do.
   5. Colours (hex codes; if they have none, offer to pick a palette and ask for a yes) and fonts.
   6. Logo and profile photo: ask them to put the files in `assets/brand/logo/` and give the file names.
   7. Does the user have their OWN voice? Own voice always comes first. Yes, in ElevenLabs: `python _system/engine/voice.py list`, then `voice.py use <id> [--lang th]`. No, but they can record a clean sample (1-3 min, one speaker): `voice.py clone --sample f.mp3 --name "<name>" --i-own-this-voice` (paid plan; ask first, only their own voice). Neither: leave empty, the built-in voices are used. Never edit the voice lines in BRAND.md or brand.json by hand: voice.py writes both.
   8. Words to use, words never to use, how numbers and percentages are written, product names and how they are pronounced.
   9. Caption style (bold highlight or colour boxes) and the usual call to action (CTA), plus anything that must not appear on screen.
   10. Hashtags they always use.
   Write the answers into `BRAND.md` (keep its headings, and keep the line `**CTA:** ...`, because the caption script reads it) and into `brand.json` (`name`, `lang`, `channels`, `voice`, `colors`, `fonts`, `logo`, `profile_photo`). Show the user a 5-line summary and ask if anything is wrong. The user can change the brand later by saying "update my brand".
6. Tell the user the next step in 3 lines.

## Optional: provider order
`~/.indigo11/config.json` can set the order of AI providers (`providers.<task>.chain`). It holds no keys and is not needed; skip it for new users.

## Working folders
- `projects/raw-videos/`: the user's raw files (clips, voice-over, music, images). Never change or delete them.
- `projects/results/`: finished files, always grouped by job type, then by project: `projects/results/<type>/<YYMMDD>-<project>/`. Types: `video-creation`, `editing`, `reverse-engineering`, `transcription`. **Never an `assets` type: assets I keep (music, sound effects, images, icons, videos, fonts, templates) go into the matching folder of the existing `assets/`, never into `projects/results/`** (save with `python _system/engine/save_asset.py <music|sound-effects|images|icons|videos|fonts> <files...>`; templates with `save_template.py`). Same project = same folder. Never leave a finished file loose in `results/` or inside a project folder. Save with `python _system/engine/save_result.py <type> <project-name> <files...>` (it moves them, never overwrites).
- Dates use the Buddhist Era year (2026 = 2569) in folder names, INDEX.md, project.json and rule lines: `2569-11-09-<slug>`. `YYYY` means that year.
- `assets/brand/`: the user's brand (BRAND.md + brand.json). Every brand fact the user tells you later (channel name, colours, voice id, CTA ...) goes here, never into `my-rules/`.
- `projects/_work/`: backups and working files made by the skills.
- The skills do not use these automatically; the user names the folder in the request.

## Every edit
1. CapCut: new project, clips in the Media panel, save (Ctrl+S / Cmd+S), quit CapCut.
2. User asks, for example: "add 2-word captions to project 0521".
3. CapCut reopened to see the result. "undo" restores the backup.

## Language
Reply in Standard British English (Thai if the user writes Thai). Thai captions and Thai word splitting stay for Thai videos. English captions: `.claude/skills/indigo11-transcribe/references/caption-rules-en.md`.

## Known limits
- The skills never rebuild a timeline the user already cut by hand. To add captions to their own cut: "add captions, do not re-cut".
- Silence detection can download about 450 MB the first time (ask first).
