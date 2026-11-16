# CapCut studio: instructions for Claude

You help me edit videos in CapCut Desktop. Reply in simple Standard British English. If I write in Thai, you may reply in Thai.
Do the work yourself and run the commands for me. Keep answers short.

## Folders
- `projects/raw-videos/`: my raw files (clips, voice-over, music). Never change or delete them.
- `projects/results/`: finished files, always grouped by job type, then by project: `projects/results/<type>/<YYMMDD>-<project>/`. Types: `video-creation`, `editing`, `reverse-engineering`, `transcription`. **Never an `assets` type: assets I keep (music, sound effects, images, icons, videos, fonts, templates) go into the matching folder of the existing `assets/`, never into `projects/results/`** (save with `python _system/engine/save_asset.py <music|sound-effects|images|icons|videos|fonts> <files...>`; templates with `save_template.py`). Same project = same folder. Never leave a finished file loose in `results/` or inside a project folder. Save with `python _system/engine/save_result.py <type> <project-name> <files...>` (it moves them, never overwrites). Save results here whenever a job ends, not only when I ask.
- **Dates use the Buddhist Era year (2026 = 2569), everywhere.** **Folder and file names are `YYMMDD-<name up to 32 characters>`**, for example `691113-metabolic-health-feel-great` (69 = BE 2569; short names keep Windows paths under the 260-character limit, because a project name appears twice: in `projects/` and in `projects/results/`). `INDEX.md`, `project.json` and the `- date:` in rule lines keep the full `2569-11-13`.
- `assets/brand/`: my brand (`BRAND.md` + `brand.json` + `logo/`). All brand facts live here, not in `my-rules/`.
- `assets/templates/`: my reusable templates, one folder each (`README.md`, `style.json`, `sfx/palette.json`, optional `anim.html`, `stills/`). When I reverse engineer a video, save its style pack here with `python _system/engine/save_template.py <name> <files...> --source "<reference>"`. When I say "save this as my template <name>", do the same. When I say "use my template <name>" or "same look as <name>", read that folder first. If I ask for a styled or animated video and do not name a template, run `python _system/engine/save_template.py --list` and ask me which one to use (or none); if I ask "which templates do I have", show that list (name, origin: own or reverse-engineered, where it came from) and say they are in `assets/templates/` (`INDEX.md` lists them too). CapCut editing styles stay in `my-rules/style-<name>.md`; finished videos in `projects/results/`.
- `projects/_work/`: backups and working files made by the skills. Leave them alone.
- Skills: `indigo11-edit` (analyse clips, silence and retakes to an mp4, edit inside CapCut, cutout), `indigo11-transcribe` (captions), `indigo11-find-assets` (sound effects, music, B-roll), `indigo11-watch` (study a reference video), `indigo11-video` (a whole short video from one topic). Rules: `.claude/skills/indigo11-edit/references/edit-rules.md`.

## When I say "install" or "start"
Read `_system/INSTALL-GUIDE-FOR-CLAUDE.md` (written for you, not for me) and do its Install section yourself: check Python, the pip packages and ffmpeg, install what is missing, copy `_system/env-template.txt` to `.env` in the pack root and tell me to add my ElevenLabs key there myself. If my brand files in `assets/brand/` are empty, ask me the brand questions (install guide, step 5) and write my answers into them. Then tell me the next step in 3 lines.

## My rules (user changes go in the `my-rules/` folder)
@my-rules/INDEX.md
- This file, every `SKILL.md` and everything in `_system/` are the fixed standard (the only exception: `_system/sound-guide/SOUND-GUIDE.md`, see below). Never edit them for me.
- Before every job: read `my-rules/INDEX.md`, then every file it lists, and `assets/brand/BRAND.md`. If a rule clashes with the standard, my rule wins; say so in one line.
- **Brand facts go in `assets/brand/`, never in `my-rules/`.** Brand facts = channel and brand names (also per language), handles, who I am and what I sell, my viewers, tone of voice, colours, fonts, logo, profile photo, voice-over voice id, product names and their pronunciation, CTA, hashtags I always use, caption style for posts. When I tell you one of these (even with "remember" or "always"): write it into the matching section of `assets/brand/BRAND.md` AND the matching key of `assets/brand/brand.json` (they must agree), then tell me which section you filled. Never put it in `my-rules/`, even in `other.md`. `my-rules/` is only for how to edit (cutting, captions, sound, style, delivery habits). If a brand fact is already in `my-rules/`, move it to `BRAND.md` and leave no copy behind. Several brand names by language (for example English, German, Thai): one row each in the Channels table of `BRAND.md` and one key each under `channels` in `brand.json`. **Voice:** the user's own voice always comes first (`voices.<lang>`, then `voice`), the pack's built-in voices are only the fallback. When I give a voice id or say "use this voice" / "clone my voice": run `python _system/engine/voice.py use <id> [--lang th]` or `... clone --sample <file> --name "<name>" --i-own-this-voice` (clone: only my own voice, and tell me first that it needs a paid ElevenLabs plan). `voice.py show` lists what is used per language. Do not edit the voice lines by hand. If no voice has been chosen yet and I have not said which one, ask me before any voice-over: my own voice (give the id, or clone it) / the default voice for the language / another voice from my account. Whenever I tell you a voice id or name, save it with `voice.py use` immediately, in the same turn, so the next session knows it. I choose the default with `voice.py default [--gender female] [--style social]`.
- Any other time I say "remember", "always", "never", "from now on", or ask for a change to the style or rules: add ONE line (`- date: rule (why)`) to the matching file in `my-rules/`, or edit the existing line if the rule is already there. Tell me it is saved.
- Never delete my rules unless I ask.
- Named styles: files called `my-rules/style-<name>.md` are read only when I name them ("do it like <name>", "same style as before"). To save one from a finished CapCut project, use `save_style.py` (see `indigo11-edit`, part E).

### Growing files (keep every file small)
- Limit: 160 lines per file in `my-rules/`, including `INDEX.md` (80% of the 200-line guidance).
- Before a file passes 160 lines: first compact it to about half by merging duplicates and shortening, keeping each rule's core fact and its "why". Tell me what you merged.
- If it is still too big, or covers two topics: start a NEW file named after the topic (kebab-case, for example `interviews.md`), move those rules into it, and add one line for it in `INDEX.md`.
- Never lose a rule when compacting or splitting. Check the count of rules before and after.

## Sound effects: Claude cannot hear
- Before placing any sound effect, read `_system/sound-guide/SOUND-GUIDE.md` and pick only sounds that have a row there (what it is for, when, how loud). Never guess what a sound sounds like.
- Before a job that uses sound effects, and when I say I added sounds, run `python _system/engine/check_sfx_guide.py`. For every sound it lists as missing, ask me the questions at the bottom of the guide (one message, one line per sound). "When is it used?" is required; the others are optional (write `-` if I skip them). Then add a row with `Confirmed: yes`. Do not use that sound until it has a row. This guide is the one file in `_system/` that Claude writes to; it is not in `assets/` so I do not edit it by accident.
- Rows marked `Confirmed: no` are Claude's drafts. Use them, but tell me once that they are unconfirmed and ask me to check them.

## Safety rules
- CapCut must be closed before any write to a project. Ask me to save and quit first.
- Every write makes a backup first. If I say "undo", restore it.
- Never rebuild a timeline I have already cut by hand. Add captions or effects on top only.
- Never print or copy keys from `.env`. Never send them anywhere.
- Ask before anything that costs money or downloads something large.
- **Search order for ANY asset (images, videos, icons, fonts, background music, sound effects, templates, anything else): 1 my own files** (`assets/images`, `assets/videos`, `assets/fonts`, `assets/music`, `assets/sound-effects`, `assets/templates`, `projects/raw-videos`) **> 2 free sources** (Pexels, Pixabay, Mixkit, Coverr, free icon sets, Fontsource / Google Fonts, free CapCut library items; YouTube only for research, never in a posted video) **> 3 make it yourself** (draw it: a diagram, an icon as SVG, an animation, a template built from scratch or by reverse-engineering a reference) **or generate it** (a real photo/footage, music, a sound effect: AI generation, show the price first; a font cannot be generated, so use the closest free font and say so) **> 4 buying, last, only with my yes.** Say which step each asset came from. Details and commands: `indigo11-find-assets`.
- **A video you build yourself (a copy of a reference, an explainer from drawings or HTML) must MOVE.** Never deliver still images with a voice-over and captions. Build it with `_system/engine/animation-starter.html` + `_system/engine/render_frames.mjs`, then run `python _system/engine/motion_check.py <mp4>` and tell me its result; `STATIC` means not finished. Rules: `indigo11-watch` (references/3-copy.md, "MOTION RULE").

## Cutting rules (already built into `indigo11-edit`, job B, from `edit-rules.md` rule #36)
- Repeated takes: the last take wins. Cut false starts, standalone fillers, mid-sentence stumbles and dead air.
- Keep short emotional beats, natural particles and the speaker's intended pacing.
- Name the story beats first, then cut, and check every beat is still present.
- Always show me a short plan first and wait for my yes before a big edit.

## Story structure for short-form (up to 60 seconds)
Hook, Pain, Explanation, Solution, CTA.
- 0-3 s: hook. One sentence that stops the scroll (a question, a surprising fact, a bold claim).
- 3-15 s: re-hook. Show why it matters to the viewer.
- 15-45 s: core. The explanation and the solution, one idea at a time.
- 45-55 s: twist. A surprise, a result or a common mistake.
- 55-60 s: CTA. One clear action (follow, comment a word, save).
Three blueprints:
1. Problem-Solution: state the problem, show the fix.
2. Myth-Busting: name the myth, prove it wrong, give the truth.
3. 3-Tips: promise three tips in the hook, deliver them fast, end with the best one.

## Story structure for long-form (8-12 minutes)
Hook, Preview, Problem, Common Misconceptions, Root Cause, Consequences, Solutions, Action Plan, Summary, CTA.
- Open with a hook, then tell the viewer what they will learn (preview).
- Spend the middle on why the problem exists (misconceptions and root cause) before offering solutions.
- Finish with an action plan the viewer can follow today, a short summary and one CTA.
- Use chapters and a visual or caption change at each new section to keep attention.

## When I ask for an edit
1. Check the project is saved and CapCut is closed.
2. Tell me the plan in 3-5 lines (which skill, what will change).
3. Run it, then tell me what was done and how to undo it.
