# Cloud sessions (Claude Code on the web)

These rules apply only when the session runs in the cloud: `echo $CLAUDE_CODE_REMOTE` prints `true` (a Linux machine at Anthropic, not the user's computer). On a PC or Mac, ignore this file. Where a rule here clashes with CLAUDE.md or the install guide, this file wins in the cloud.

The user may have no computer, only a phone or tablet. They cannot open files, run commands or use CapCut Desktop. Do everything yourself. Raw clips come in through a Google Drive link; finished files go out as GitHub links.

## First job of every session
1. Run `bash _system/cloud/setup.sh` and read its report. It installs ffmpeg, the Python and Node packages and the Chrome wrapper, and is quick when already done. In the cloud this replaces the Install section of the install guide: do not look for CapCut, Git Bash or winget, do not make a `.env` file, and do not run `check_setup.py` (it looks for a `.env` file and PC tools).
2. Use `python3` in every command (the docs say `python`).
3. Keys come from environment variables. If the report says `ELEVENLABS_API_KEY: empty`, tell the user: open the environment settings of this cloud session, add the line `ELEVENLABS_API_KEY=<their key>` under Environment variables, save, and start a new session. Never ask for a key in chat, never print one, never write one into a file.
4. If the brand files are empty, do the brand interview (install guide, step 5) as usual.
5. The user's guides in this copy are `CLOUD-QUICK-START.txt` (the 7 steps) and `CLOUD-README.txt` (overview and help). The root `README.txt` and `QUICK-START.txt` named in other files do not exist here: point to the two cloud files instead.

## What does not work in the cloud
- Everything that writes into a CapCut project (`indigo11-edit`: captions, sound effects, overlays, stickers, effects, voice-over assembly, undo and render inside CapCut). Say so in one line and offer the cloud route: a finished mp4 with burned-in captions, or an SRT file the user imports into CapCut later.
- Cutout and VAD silence detection need large downloads (rembg, torch) and are slow without a graphics card: ask first.
- YouTube and TikTok downloads are often blocked from cloud machines. If one fails, do not retry in a loop: ask the user to put the file on Google Drive instead.
- On-screen text reading uses tesseract (`--backend tesseract`), not the Mac Vision helper.

## Network
The network is limited by default. If a request fails with a proxy, 403, "blocked" or "could not resolve" error, do not retry: name the domain and tell the user to add it under Allowed domains in the environment settings (the full list is in `CLOUD-README.txt`, part 11), then start a new session. If every host fails and the keys are empty too, the session was started in another environment: tell the user to start a new session and choose the environment they set up.

## Getting files in: Google Drive link, never GitHub
- The user uploads the raw clip to Google Drive, shares it as "Anyone with the link" and pastes the link. Download it with `python3 -m gdown "<link>" -O projects/raw-videos/<name>` (tested with gdown 6.5.1, which takes a share link as it is and has no `--fuzzy` flag; a folder link: add `--folder`). If a flag is refused, read `python3 -m gdown --help` instead of guessing.
- Do not download a video through the Google Drive connector: it returns the whole file into the conversation.
- A sign-in or permission page instead of the file means the sharing is not "Anyone with the link": ask the user to change it.
- When the job is done, remind the user to delete the raw clip on Drive or turn its sharing off.

## Delivering: finished files by GitHub link
Tested 2569-11-14: the Google Drive connector carries at most about 3 MB per call, so it cannot take videos. Do not try to upload a video to Drive.
1. Save results as usual (`save_result.py`).
2. GitHub refuses files over 100 MB: re-encode anything over 90 MB to a smaller file first and say so.
3. `git add -f <the finished files only>` (results are git-ignored, so `-f` is needed; never add raw clips), commit, `git push`.
4. Give one link per file: `https://github.com/<owner>/<repo>/raw/<branch>/<path>` (from `git remote get-url origin` and `git branch --show-current`). This direct link starts the download when the user is signed in to GitHub (untested for private repositories: if it fails, give the `/blob/` link instead). Tell the user: tap the link and keep the video on the phone or in Drive. On a `/blob/` page GitHub says "can't show files this big": that is normal, tap the download arrow. Show the post text in the chat as well.
5. Only when brand, `my-rules/`, the sound guide, templates or kept assets changed: `git add -A`, commit, `git push`, check `git status` shows no `.env`, then tell the user what was saved and to tap Create PR and then Merge. If nothing of this changed, do not ask for a merge.
- Do not use `gh` in the cloud (tested 2569-11-14: its token is not valid there, so releases and `gh pr` fail). Deliver with `git push` and links only.
- The machine is removed after the session: anything not pushed is lost.
- Commands stop after about 2 minutes: run long renders in the background and wait for them.

## GitHub space
- Finished videos stay in the repository's history for good, about 20 MB each. After each delivery run `du -sm .git | cut -f1` (a rough guide). Over 1000: tell the user in one line that a fresh copy will be needed before long. Over 4000: tell them to make a fresh copy now (`CLOUD-README.txt`, part 9).
- Fresh copy, in the old repository: when the user asks for "my brand and my rules as text", print `assets/brand/BRAND.md`, `assets/brand/brand.json` and every file in `my-rules/` in one code block, each under its file name. In the new repository: when the user pastes that text, write the files back exactly, push, and ask for the merge. Logo and photo files are not text: tell the user to upload them again.
