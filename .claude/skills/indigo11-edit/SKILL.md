---
name: indigo11-edit
description: 'Edit the user''s OWN raw footage end to end. (1) Analyse clips before cutting (transcript, silences, scenes, frame grids, retakes -> context.json). (2) Clean-cut to an MP4: assemble, rough cut (retakes), jump cut (silence), normalise loudness, mix music. (3) Write edits INTO a CapCut Desktop project: assembly, voice-over-driven assembly, captions (1-4 words, karaoke, bilingual), SFX, overlays, stickers, effects, undo, render. (4) Cut out a subject to a transparent video. Triggers: "analyse clips", "rough cut", "jump cut", "remove silence", "merge clips", "normalise audio", "add captions to CapCut", "edit project 0517", "remove background", "cutout", "undo".'
allowed-tools: Bash Read Write Task AskUserQuestion
---

# indigo11-edit

One skill, four jobs. Pick the job from the request, run only that part.
| Job | When | Output |
|---|---|---|
| **A. Analyse** | raw clips arrive, before any cut | `context.json` |
| **B. Clean-cut** | the user wants a cleaned MP4 (or an EDL) | `cleaned.mp4` / EDL |
| **C. CapCut** | the user wants the edit written INTO a CapCut project | patched project |
| **D. Cutout** | the user wants a subject on a transparent background | alpha `.webm` + CFR `.mp4` |

`$SKILL_DIR` = this folder; scripts live in `$SKILL_DIR/scripts/`. Windows: use `python`, not `python3` (the Store stub fails); run `.sh` files from Git Bash. Sibling skills: `indigo11-transcribe` (STT, captions) and `indigo11-find-assets` (SFX, music, B-roll).

## Global rules (all jobs)
- **Scripts are deterministic helpers; you make the editorial decisions** (order, which take, caption phrasing, which effect fits). Never put judgment into a script.
- **Ask only what the conversation has not answered.** Batch the missing questions into one `AskUserQuestion` (plain text if the tool is missing). Skip anything already stated.
- **Look at the picture.** Read the frame-grid PNGs, never infer visuals from the transcript. If you cannot view images, leave the field `null`, set `visual_unverified: true` and say so. A made-up description is worse than an empty one.
- **Cost and downloads: tell first.** Silero VAD (~450 MB), rembg + u2net (~400 MB) and any paid API (Gemini, ElevenLabs) are installed or used on first use only, after the user says yes.
- **Report outcomes faithfully**: failed checks, skipped steps, warnings. Do not hide a `FAIL` behind a note.
- **Deliver into `projects/results/editing/<YYMMDD>-<project>/`** (cleaned mp4, rendered mp4, SRT, cutout files the user keeps). Working files stay in `projects/_work/`. Command: `python3 _system/engine/save_result.py editing <project> <files...>` (use `--copy` if the file is still needed in `_work/`). Same project = same folder.
---
## A. Analyse (context before cutting)

Own footage only. For someone else's or a viral clip, use a watch/breakdown skill instead. Skip for clips under 5 s or already-clean clips that only need captions. Do not analyse more than 10 minutes in one pass; split first.
```bash
python3 $SKILL_DIR/scripts/analyse_context.py --clips a.mov b.mov [--lang th] [--output-dir DIR] \
  [--detect-motion] [--detect-text] [--understand] [--fast] [--keep-intermediates]
```

Default output: `projects/_work/analyze/<basename>/` with `context.json`, `frame-grids/` (keep) and `transcripts/`.

It runs: ffprobe, scene changes, Silero VAD silence ranges (ffmpeg `silencedetect` fallback, with a warning), sparse frames, word-level transcript, pacing stats. Optional passes: `--detect-motion` (any OS, free: motion peaks, frozen spans), `--detect-text` (macOS OCR only), `--understand` (Gemini watches the video; cloud, needs `GEMINI_API_KEY`, costs tokens).

**Then you fill the empty fields** in `context.json` and save it back:
1. `video_metadata.overall_mood` and `pacing_speed`.
2. **Read every `frame_grids[]` PNG** (about 36 frames per image; one Read replaces ~7 single frames). For each frame: `visual_description` (5-15 words, name any visual device: before/after split, hand demo, arrow), `has_speaker`, `on_screen_text`. Watch long silences closely: that is usually the demo, which words never show.
3. Multi-clip only, per `per_clip[]`: `role_hint` (hook, setup, main, b-roll, cta, outro, discard), `quality_hint` tags (clean, has-false-start, has-retake, has-stumble, no-speech), `summary`, 2-5 `notable_moments_ms`. Use `motion_peaks_ms` for those when present.
4. `take_comparison[]` when the same 4+ word phrase repeats: list each take, a one-line verdict, pick `best_idx` and say why.
5. `notes[]`: one line per low-confidence call.

Do not fill `blueprint` for raw clips. Do not write code to fill judgment fields. Downstream jobs reuse an existing `context.json`; delete it to force a fresh run.

**Multi-camera plans** (deterministic, always show the switch list to the user before applying): `podcast_plan.py` (speaker switching, needs `--speaker-map`, `--sync-offset`, `--min-hold-ms`) and `angles_plan.py` (adds no-repeat suppression, reaction cutaways, `--lock start-end=camera`; locks always win).
---
## B. Clean-cut (MP4 or EDL)

Modes: **assembly** (concatenate clips), **rough** (remove retakes and content errors), **jump** (remove silence and fillers), **full** (assembly, then rough, then jump). Modes produce an **EDL** (JSON list of kept segments); rendering is a separate, asked-for step.

**Ask**: video path, mode, language, and for rough the register (formal, casual, hype). After the EDL, ask delivery: render MP4 / inject into CapCut (Job C) / both / EDL only. Rendering costs 30-90 s, so never render by default. Ask about loudness normalisation only when rendering.

Preflight first: `bash $SKILL_DIR/scripts/preflight_clean_cut.sh`. Needs `ffmpeg`, Python 3.9+, and Silero VAD for rough, jump and full (install with `bash $SKILL_DIR/scripts/install_vad.sh` after the user agrees). Resolve the VAD interpreter per OS: `VAD_PY="$HOME/.indigo11/vad-env/bin/python3"; [ -x "$VAD_PY" ] || VAD_PY="$HOME/.indigo11/vad-env/Scripts/python.exe"` (`install_vad.sh` creates that folder). Use VAD, not `silencedetect`, for Thai: amplitude thresholds cut soft final particles.

Output folder: `projects/_work/clean-cut/<basename>/` (`edl-*.json`, `cleaned.mp4`, `meta.json`).

**Assembly**: `assembly.py clip1.mp4 clip2.mp4 -o assembled.mp4` (or `--from-list clips.txt`). Stream-copies when codecs match, else re-encodes at 30 fps with keyframes every 30 frames.

**Rough**: (1) run Job A. (2) Decide the cuts, in a subagent if available, otherwise yourself, reading `context.json` fully plus `references/edit-rules.md` rule #36. **Narrative first**: name the beats (product pitch: hook, product, proof/demo, benefits, CTA), assign every line to one, and cut any line that adds no beat even if delivered perfectly (empty promise, dangling reference, duplicate beat, throat-clearing). Check that no beat is empty. Then take quality: follow `take_comparison.best_idx`, cut at silence boundaries, drop false starts and fillers, scrutinise the last 20%. Write segments `{start_ms, end_ms, beat}`, chronological, non-overlapping, plus `beats`, `narrative_cuts` and `context_used`. (3) **Never skip** the pad-bleed fix: `editorial_pass.py edl-rough.json transcript/raw.json -o edl-rough-safe.json`. It re-anchors each end on the last real character and stops a kept pad from reaching into a dropped word (the listener would hear "word word"). Use the `-safe` file from here on.

**Jump**: `ffmpeg -y -i IN -ac 1 -ar 16000 audio.wav`, then `"$VAD_PY" vad_detect.py audio.wav --min-silence-ms 300 --pad-ms 200 --output speech.json`, then `speech_to_edl.py speech.json -o edl-jump.json`.
| Content | min-silence-ms | pad-ms | merge-gap-ms |
|---|---|---|---|
| TikTok/Reels | 300 | 150 | 0 |
| Talking head | 300 | 200 | 0 |
| Podcast | 800 | 300 | 0 |
| Long-form over 30 min | 500 | 250 | 300 |

Use `--merge-gap-ms` on long videos: CapCut lags near 500 segments and may crash near 1000.

**Full**: assembly, rough, render the rough result with `apply_edits.py`, then run VAD on that render (not on the original audio, which still contains dropped takes) with `--min-silence-ms 600`.

**Render**: `apply_edits.py IN edl-<mode>-safe.json -o cleaned.mp4`. Never cut a sub-range with `-c copy` (the first seconds freeze); the script re-encodes with `-r 30 -g 30 -keyint_min 30 -movflags +faststart`.

**Captions already made on the original?** Remap, no new transcription: `remap_captions.py --edl EDL --in words.json --out words-remapped.json` (also works on `captions.json`). If the user cut by hand in CapCut instead, read the cuts back with `capcut_to_edl.py --project P --out edl-from-capcut.json`, remap, then re-add captions (Job C). Speed changes are not remapped; the script warns.

**Loudness** (EBU R128, same as CapCut's Normalise loudness): `normalise_audio.py cleaned.mp4 -o cleaned-norm.mp4` (default -14 LUFS; `--target broadcast|podcast`, `--measure-only`). Video is stream-copied. When takes differ a lot, normalise each source clip before assembly.

**Music under speech** (do not guess slider values; mastered stock music can measure louder than speech): normalise the voice, then `mix_bgm.py prep --voice cleaned-norm.mp4 --music track.mp3 -o bed.m4a [--offset N] [--duck 6]` (music at voice minus 20 LU, 6 dB sidechain duck), use the bed at volume 1.0, then `mix_bgm.py master final.mp4 -o final-mastered.mp4`. `mix_bgm.py measure FILE` prints LUFS. SFX one-shots stay outside this chain; judge them by ear.

**Delivery to CapCut**: `inject_edl.py --project P --edl edl-<mode>-safe.json` (Job C rules apply).
---
## C. CapCut project editing

Patches the user's own project JSON in place (pure JSON, no CapCut library, so no version mismatch). Works on macOS and Windows; paths and the timeline filename (`draft_info.json` mac, `draft_content.json` Windows) are auto-resolved by `_capcut_lib.py` (override with `CAPCUT_DRAFTS_DIR`). Short date-style names (`0517`) mean a CapCut project.

**User prepares**: new project, drag clips (and any voice-over) into the **Media panel only**, save and quit (Cmd+S, Cmd+Q / Ctrl+S, Ctrl+Q), tell you the project name.

**Safety rules (non-negotiable)**
1. **CapCut must be quit.** `capcut_running: yes` in preflight is a STOP: ask the user to save and quit. Never kill it. `--force-kill` only after the user says nothing is unsaved.
2. **The request decides the mode, never permission to rebuild.** "Add captions" means captions only, on every timeline state. Cutting happens only when asked.
3. **Never destroy the user's cut.** `inject_edl.py` refuses a non-empty video track. `--replace-timeline` needs an explicit yes after you state the loss in numbers ("this deletes N segments you cut"). Offer "keep my edit, add a track" first.
4. **Own only your tracks.** `state.json` `owned_tracks` lists tracks you may replace. Never delete the user's titles or hand-typed captions. If your own track holds their edits, `--overwrite-owned` needs asking.
5. **Mirror to all 9 places** (the scripts do it; never skip it, CapCut self-heals to the old timeline otherwise). Never generate CapCut JSON from scratch.
6. **Every write is reversible.** Each op prints `op_id` and `backup_dir` (`projects/_work/capcut/<project>/backups/<op_id>/`). Always hand over the undo line. Rollback reports ok, partial or failed; relay which.
7. **No CapCut cloud API, no CLI export.** Calling its catalogue API risks banning the account; the binary has no export flag.

**Start every job with** `bash $SKILL_DIR/scripts/preflight_capcut.sh <project>` (checks ffmpeg, VAD, `capcut_running`, `valid_clips_count`; 0 clips means the user did not drag any), `read_media.py --project P` (report "found N clips, total Xs"), and `verify.py --project P --json` (`video_segments` above 0 means **Flow B**, text tracks, `owned_tracks`). Ghost Media entries (empty path or duration 33000 us) are ignored.
### Flow A: blank timeline
`process_all.py --project P [--do-assembly] [--do-rough] [--do-silent]` stages audio, transcripts, VAD and frame grids in `<temp>/indigo11-capcut-work/<project>/`. Read the frame grids, then write `decisions.json`:
```json
{"assembly_order":[0,3,1,0],"do_silent":true,
 "rough_decisions":{"0":[[1,5,"HOOK: pain"]],"3":[[9,13,"PROOF: demo"]],"0#3":[[17,18,"CTA"]]}}
```

Silent gotchas: keys are **strings** (`"0"`, an int key means "keep the whole clip"); `[word_start, word_end, reason]` with `word_end` **inclusive**, indexing phrase entries in `transcript.words`; put the story beat in the reason; **cut only on phrase boundaries** (a mid-phrase cut splits words); a clip may repeat, and `"clip#slot"` uses the **position in `assembly_order`**, not an occurrence count; read `warnings[]` from `compute_edl.py` and re-pick any flagged cut. Use the same two-layer rough logic as Job B (narrative, then take quality). A silent visual beat (demo, before/after) over ~2 s is dropped by VAD: name the range, **ask the user** before keeping it, then add `visual_keeps` `{clip_index,start_ms,end_ms,reason}`.

Then: `compute_edl.py --stage stage.json --decisions decisions.json -o edl-final.json`, then `inject_edl.py --project P --edl edl-final.json`.
### Flow A-VO: footage cut to a separate voice-over (plan first)
The voice-over is the master clock; one plan row per voice-over phrase; the voice-over is never cut.
1. `vo_assemble.py prep --project P [--vo file]` transcribes the voice-over (needs `ELEVENLABS_API_KEY`, or pass `--transcript DIR`), builds frame grids of all footage (`frame_grid.py`), and writes `PLAN.md`. Read the watch output and every grid yourself, then fill the table. Columns (names are read literally by the script): `เวลา VO` (`0.00-2.40`, rows contiguous from 0 to the end), `ฟุตเทจ` (file), `เริ่มที่` (start second in the clip), `B-roll` (`-`, `file.mp4@2.5`, or `ต้องหา: <english query>`), `เอฟเฟกต์` (`zoom 1.15`, `transition NAME`, `effect NAME`, `filter NAME`), `SFX` (`whoosh@cut`, `pop@word`, `impact@3.2`), `ซับ` (`✓` or `-`). Settings under `## ตั้งค่า`: `captions: karaoke 2` (plain N, karaoke N, accumulate, none), `footage_audio: mute`, `fx_source: fxlib`, `sfx_volume`, `approved: no`.
2. `vo_assemble.py build --plan PLAN.md` validates everything and writes nothing on error. Fix and repeat.
3. **STOP.** Show the user a short table and the path. Set `approved: yes` only after they agree.
4. `vo_assemble.py apply --plan PLAN.md` (refuses without approval, on a non-empty timeline, or while CapCut runs). It imports media, injects cuts, adds the voice-over, B-roll, transitions, effects, SFX, captions, and QC; `undo_all` in the result reverts the whole set.

Limits: transitions, effects and filters must exist in the user's `fx_source` project; keyframes only on image overlays (pop-in + constant motion, see `add_overlay.py`); no speed ramps, masks, colour grading or ducking; zoom is a fixed scale per row; BGM is added afterwards; `render_draft.py` omits overlays, transitions and effects.
### Flow B: the user already cut it (add tracks only)
Captions: `add_captions.py --project P --from-timeline --words-per-group N`. It reads each segment's `source_timerange` and `target_timerange`, remaps the cached word timings, and never touches the video track. It refuses on speed changes or an ambiguous audio track (ask which track carries the edit; `--video-track N`, `--audio-track N`). Do not run `process_all`, `compute_edl` or `inject_edl` here. Ask only how many words per caption. In your report say: "your cut is untouched, captions went on a new track."
### Captions
Modes: **plain** (default), **`--karaoke`** (one token highlighted at a time; colour only unless `--highlight-size`), **`--accumulate`** (typewriter build-up, left-aligned fixed-width box). Karaoke text is merged without spaces by default; `--keep-spaces` restores them. Word counts: 1 (kinetic), 2 (balanced, default), 3-4 (long-form).

Styling: `--color`, `--base-size 15`, `--highlight-color "#F4C70F"`, `--vertical-pct 78`, `--horizontal-pct 50`, `--border-width 0.06` (0 = none), `--border-color`, `--shadow-alpha 0.8`, `--no-shadow`. **Bilingual**: run twice with different `--role` (`captions-th`, `captions-en`), colour and position; translate the first run's remapped words file (reported under `from_timeline.words`). Re-running a role updates only its own track.

**Pass 2 (default for N-word captions; skip only if the user says to hurry, and say so):** per clip with `D=projects/_work/transcribe/<basename>`: `word_segment.py $D/raw-elevenlabs.json --phrases-out $D/phrases.json`, write `$D/breaks.json` (text chunks only, kept phrases only; see `references/post-process-protocol.md`), `apply_breaks.py $D/raw-elevenlabs.json $D/breaks.json -o $D/words.json --words-per-group N --partial`, fix anything listed under `fallback` or `too_long`, rerun, then `add_captions.py`. Use the same N in both. Never hand-edit timings; add split names to `proper_nouns[]`. Gap-close is on by default (`--no-gap-close` to disable).
### Decorate and assets
- **Own files** (SFX, memes, images, B-roll, music): `import_media.py --project P --files a.mp3 b.jpg` or `--dir D --recursive --types audio,image`. No manual drag. Safe to repeat; `--dry-run` previews.
- **SFX/B-roll placement**: `add_sfx.py --sfx-timing T.json [--track auto]` (auto avoids overlapping the voice-over), `add_overlay.py` (images or video, muted by default, position/scale/rotation). **Image overlays MOVE by default** (`"motion": "pop+float"`: pop-in with overshoot and fade, then constant push-in, breathing, sway and bob, all as real CapCut keyframes; `"pop"`, `"float"`, `"none"` and `"motion_strength"` change it). Never leave a picture static; time each overlay to the word it illustrates. For "find a fitting sound", search with `indigo11-find-assets` (English keyword, audition 3), get beats from its `suggest_sfx_beats.py` (free, no motion pass), then import and place. Do not run Job A just for SFX.
- **Key-message highlights** (text boxes that stress what matters): `add_keywords.py --project P --keywords keywords.json [--y 1000] [--dry-run]`. Look: yellow `#FFD400` fill, black stroke, at CapCut Y = 1000 (under the subtitles at Y = 1350; change with `--y`, `--color`). **You choose the messages, not the script**: read `phrases.json` / `words.json`, pick ONE complete claim (subject + verb + object, 10-20 visible characters PER LINE, at most 2 lines: at size 22 a line of more than 20 runs off the screen; Thai vowel/tone marks and spaces do not count. If it cannot be shortened and still read well, break it into two lines yourself with `
` at a word boundary) for a period that has something worth remembering, for example "อัลไซเมอร์เชื่อมโยงกับระบบเผาผลาญ", never fragments like "คิดว่าอัลไซเมอร์" or bare nouns like "ระบบเผาผลาญ". At most one highlight per 3-5 seconds and skip periods with nothing important (the tool drops anything closer than 3 s, shorter than 10 characters, longer than 20, or starting with a linking word, and tells you why). Give each message its spoken `start_ms` / `end_ms`. Run `--dry-run` first, show the user the kept list, then run it for real: it replaces the earlier highlight track and backs up first, then `animate_keywords.py` gives every box the "Tiny Jump" in-animation copied from the user's `fxlib` library project (`--no-animation` skips it), then `sync_keyword_sfx.py` (run automatically; `--no-sfx` skips it) puts ONE pop sound (volume 0.56) at the start of every box, moving or removing the old pops and leaving other sounds alone. Run `sync_keyword_sfx.py --project P` by itself if you change the boxes some other way.
- **CapCut library items** (stickers, effects, filters, fancy text, sounds, transitions): ids differ per version and region, so never hard-code them. The user adds items once to a library project named `fxlib`; you run `extract_resources.py --project fxlib --json $SKILL_DIR/references/resource-catalog.json`, pick **by name** from the catalogue, and apply with `capcut_apply_resource.py --target P --source fxlib --name "Mouse click 1" --start-ms 4200 [--dur-ms --x --y --scale --rotation --alpha]` (`--resource-id` if names clash). **All sounds go on ONE shared track** named `indigo11-sfx` (every call with the same `--track-name` reuses it); when two sounds are close together the earlier one is shortened to end where the next starts, so a new track opens only if a sound would be under 80 ms. Never create one audio track per sound effect. A project made before this fix (many one-sound audio tracks, still images): `tidy_capcut.py --project P [--dry-run]` merges the sounds onto one track and gives static image overlays motion (full-frame images: slow push-in + soft fade; smaller ones: pop-in + float). It backs up first; `restore_project.py` undoes it. A `--transition` attaches to the clip ending at `--start-ms` and fails if there is no cut. New items cannot be pulled from the cloud; use `indigo11-find-assets` for unlimited external assets.
### Finish
1. `qc_edit.py --project P` (exit 1 on any failure). Checks: mid-word cuts, noise-only ranges, timeline gaps, caption gaps/orphans/duplicates/too-short, canvas ratio, split or varying brand names. **A `FAIL` is not shippable**: fix and rerun. A `warn` does not block; report it. Do not change a correct cut to silence a warn (`cut_unverified` is a gap in the check; `pip install nlpo3 pythainlp` resolves it).
2. Report: what changed, what you did not touch, `op_id`, backup path, and `restore_project.py --project P --op <op_id>` (`--list` shows snapshots).
3. Do not open CapCut or export automatically; the user keeps editing. Tell them to open the project and check.
4. **MP4 on request** ("render it", "no need to open CapCut"): `render_draft.py --project P` (last step; output `projects/_work/capcut-render/<project>/final.mp4`). It rebuilds cuts, zoom, speed and all text via ffmpeg + libass without CapCut. It refuses (exit 2, lists them) when the draft uses CapCut-library effects, filters, transitions, stickers, text templates or animations. Offer both options: export from CapCut's own dialog, or `--allow-unsupported` (cuts and text only). If the header says `libass=NO`, stop: captions would vanish. Flags: `--output`, `--crf 18` (use 22 for smaller files), `--fps`, `--font`, `--dry-run`.
---
## D. Cutout (transparent subject, frame-synced)

Phone and screen clips are usually **VFR**: they declare 60 fps but run about 57. A matte that re-encodes at the declared rate stretches the cutout ~5% and produces a ghost "double head" when stacked on the original. The fix is upstream: normalise the source to CFR first. `cutout.py` does it and verifies it.
```bash
bash $SKILL_DIR/scripts/preflight_cutout.sh               # ffmpeg, ffprobe, rembg?
python3 $SKILL_DIR/scripts/cutout.py --install              # only after the user agrees (~400 MB, once)
python3 $SKILL_DIR/scripts/cutout.py "IN.mp4" [--output-dir DIR] [--fps N] [--quality balanced] [--model u2net_human_seg]
```

If the JSON says `needs_install: true`, ask first. The matte runs on the CPU (about 0.2-1 s per 1080p frame; a 10 s clip at 30 fps takes minutes), so warn before long clips and trim first.

Outputs in `projects/_work/cutout/<basename>/`: `<base>-cfr.mp4` (use this, with its audio, as the **original layer**, never the raw input) and `<base>-cutout.webm` (VP9 alpha). If `synced` is `false`, tell the user and rerun before building on it. Compose in CapCut or Premiere by stacking the cutout above the CFR source. Text "behind the subject" goes between the two layers. ffmpeg: `ffmpeg -i base-cfr.mp4 -c:v libvpx-vp9 -i base-cutout.webm -filter_complex "[0][1]overlay"` (decode the webm with `libvpx-vp9` to keep alpha).

Quality: `u2net_human_seg` is fast and local but infers at 320x320, so edges fuzz and dark clothing on dark backgrounds can be cut. Use `--model u2net` for objects, `isnet-general-use` for a finer matte. A better-lit source helps most; heavier models are not bundled.
---
## E. Styles (replaces the old indigo11-video-handoff skill)

A saved style is a normal rules file: `my-rules/style-<name>.md`, listed in `my-rules/INDEX.md` as "only when I name it". It is loaded only when the user names it.
- **Save** ("save this as a style", "keep this look"): `python3 $SKILL_DIR/scripts/save_style.py --source <capcut-project> --name <name> [--description "..."]`. It infers caption mode, size, colour, words per group, position, target length and story arc, and writes them as `- date: rule (why)` lines. Then ask only for the gaps (description, length, story arc, CTA), edit the file and keep it under 160 lines.
- **Apply** ("do it like <name>", "same style as before"): read `my-rules/style-<name>.md`, then run the normal job (A, B, C). Its rules win over the defaults; say so in one line. Do not ask what the file already answers.
- **List**: read `my-rules/INDEX.md` and show the `style-*` lines.
- Never store session data (target path, output folder) in a style file.

---
## Don'ts
(1) Do not run VAD on the original audio in `full` mode, or skip the pad-bleed fix in rough mode. (2) Do not write to CapCut while it runs (it overwrites within ~30 s) or skip the 9-place mirror. (3) Do not treat "add captions" as permission to cut, and do not promise CapCut can export from the terminal. (4) Do not fabricate `visual_description`, hard-code resource ids, or call CapCut's online catalogue. (5) Do not report success without the undo line.

References: `references/edit-rules.md` (all numbered rules; rough cut = #36), `references/post-process-protocol.md` (caption pass 2), `references/capcut-schema.md` (project JSON, 9 sync points).
