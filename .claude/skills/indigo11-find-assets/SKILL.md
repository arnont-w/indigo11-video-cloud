---
name: indigo11-find-assets
description: 'Find, download or generate editing assets: sound effects, background music, memes/GIFs and B-roll (stock clips or images). Use when an edit needs a stinger, BGM, meme, B-roll insert or an AI-generated image, clip or track. Triggers: "find sfx", "find music", "find a meme/gif", "stock footage", "generate b-roll", "vine boom", "where should the sound effects go", "หา sound effect", "หาเพลง". Free sources need no key; AI generation is multi-provider (Gemini, xAI, OpenRouter, ElevenLabs) and COSTS MONEY, so always show the estimate first. Licences differ per source; surface them before a commercial edit.'
allowed-tools: Bash Read AskUserQuestion
---

# indigo11-find-assets

Fetches candidate SFX, music, memes/GIFs and B-roll by search query, with a fallback chain per type so one dead source never blocks an edit. "Free to download" is not "free to reuse": every result is tagged with its licence.

This skill is plumbing. Which asset fits which beat is the editor's judgement and stays with you. CapCut's built-in library (stickers, effects, filters) is a different thing, handled by `indigo11-edit`.

`$SKILL_DIR` = `.claude/skills/indigo11-find-assets` in the pack folder. Run from the pack folder; on Windows use `python` (bare `python3` may be the Microsoft Store stub). Scripts are stdlib-only. Keys are read from `.env`. Each asset used in a video gets a row (source + licence) in the project's sources note.

## Placing sound effects: quick, not deep

When asked for "sound effects that fit", do not run frame or motion analysis. Get beats from the spoken structure (free, deterministic):

```bash
python3 $SKILL_DIR/scripts/suggest_sfx_beats.py --context projects/_work/analyze/<name>/context.json --max 3
python3 $SKILL_DIR/scripts/suggest_sfx_beats.py --words projects/_work/transcribe/<name>/words.json
python3 $SKILL_DIR/scripts/suggest_sfx_beats.py --audio clip.mp4     # one ffmpeg energy pass, no prior analysis
```

Output: `{"beats":[{"at_ms":4200,"kind":"transition","query":"whoosh","reason":"phrase end"}]}`.

- Override each beat's generic `query` as the edit needs (punchline: `vine boom`, slide: `whoosh`, highlight: `ding`).
- Default to 1 query and 1-3 placements. If `beats` is empty, place nothing and say so.
- Build the `--sfx-timing` JSON for CapCut's `add_sfx.py`.
- Do detailed sound design only if the user explicitly asks.

## Search order for EVERY asset: images, video, icons, fonts, music, sound effects, templates (the user's rule: lawful and free first, then generating, then buying)

1. **Local files first.** `python3 $SKILL_DIR/scripts/find_local.py --type image|video --query "<words>"` looks in `assets/images`, `assets/videos`, `projects/raw-videos`, `assets/templates/*/stills` and `projects/_work/assets` (earlier downloads). `find_assets.py --type broll|image` does this step by itself before any website (`--skip-local` turns it off). A match: use it and stop.
2. **Free sources, only if nothing local fits.** Video: Pexels, Pixabay (the short-video skill's `scan_broll.py`), then Mixkit, Coverr (`find_assets.py --type broll`). Pictures: Pexels, then Pixabay (`find_assets.py --type image`; free keys in `.env`). **YouTube is for research and prototypes only, never in a posted video** (it has no commercial licence).
3. **Nothing found, or no access (for example no stock key): draw it or generate it.**
   - **Not a real scene** (diagram, explanation, title, illustration, chart): draw it yourself as an HTML animation (it must MOVE, see CLAUDE.md "MOTION RULE"). Free.
   - **A real photo or real footage is needed:** generate it with `gen_asset.py` (GPT Image / Gemini for a picture, preferably ONE still image with a slow zoom; AI video only when real movement is needed). **It costs money: show the estimate and wait for the user's yes first.** If it is AI-made, say so in the caption.
4. **Buying is last**, and only with the user's explicit yes (paid stock, a paid plan, anything with a price). Never pay on your own.
**The same four steps apply to every other asset (icons, music, sound effects, fonts, templates, anything new):**
   - **Icons:** 1 own files (`assets/images`, `assets/brand`; `find_local.py --type icon`) > 2 free open-source icon sets (`find_assets.py --type icon --query rocket [--color "#FFD400"]`: Lucide, Tabler, Phosphor, Material, as SVG; commercial OK, no credit) > 3 **draw it yourself as an SVG** (simple shapes; free) or generate an image only if a real picture is needed (price first) > 4 buying, last.
   - **Background music:** 1 own music (`assets/music`; `find_local.py --type music`, or `--bgm auto` in the short-video skill) > 2 free stock (`find_assets.py --type music`: Mixkit, Incompetech, ccMixter; credit lines go to the caption) > 3 generate with `gen_music.py` (drawing does not apply to music; **show the price and wait for the yes**) > 4 buying, last.
   - **Sound effects:** 1 own files (`assets/sound-effects`, including its `sfx/` pack of 20 CC0 sounds; `find_local.py --type sfx`) > 2 free (`find_assets.py --type sfx`: Mixkit, then myinstants, which is restricted) > 3 generate with `gen_sfx.py` (ElevenLabs; drawing does not apply to sound; **estimate, ask, then `--confirm-cost`**) > 4 buying, last. **Whatever you pick, the sound needs a row in `SOUND-GUIDE.md` first** (CLAUDE.md, "Sound effects: Claude cannot hear").
   - **Fonts:** 1 own fonts (`assets/fonts`, and the Fonts section of `assets/brand/BRAND.md`; `find_local.py --type font`) > 2 free open-source fonts (`find_assets.py --type font --query "Bebas Neue" [--subset thai]`: Fontsource / Google Fonts, SIL OFL, commercial OK, saves .ttf) > 3 a font cannot be drawn or generated: use the closest free font already in `assets/fonts` and say so > 4 a paid font, last, only with the user's yes. Keep a new font by copying it to `assets/fonts/<Family>/` and writing its name in BRAND.md.
   - **Templates:** 1 own templates (`find_local.py --type template --query "explainer"` or `save_template.py --list`; folders in `assets/templates`) > 2 free: CapCut's own free library items (effects, animations, text styles the user added to the `fxlib` project, applied with `capcut_apply_resource.py`) > 3 make one: reverse-engineer a reference video (`indigo11-watch`, level 3) or build it from scratch as an HTML animation, then save it with `save_template.py` so it is step 1 next time > 4 a paid template pack, last, only with the user's yes.
   - **B-roll** = the video rule above (own clips > Pexels, Pixabay, Mixkit, Coverr > draw an animation or generate > buying).
**Keeping what you found:** downloads land in `projects/_work/assets/` (working copies). Anything the user will reuse goes into the EXISTING `assets/` folder, never into `projects/results/` and never into a new folder: `python _system/engine/save_asset.py <music|sound-effects|images|icons|videos|fonts> <files...>` (name files after what they are, so step 1 finds them next time).
Say in one line which step each asset came from (for example "3 local, 4 Pexels, 1 drawn").

## Fallback chains (inside step 2)

| Type | Chain (first that works wins) | Key |
|---|---|---|
| SFX | Mixkit, then myinstants | none |
| Music | Mixkit (tag search), Incompetech, ccMixter | none |
| Meme/GIF | Klipy (if `KLIPY_API_KEY`), Tenor, Giphy, Imgflip (static template only) | Klipy optional |
| B-roll | your own files first, then Mixkit stock video (720p), Coverr (1080p); then draw or paid generation (see the search order above) | none |
| Image | your own files first, then Pexels, then Pixabay (`--type image`); then draw or paid generation | `PEXELS_API_KEY`, `PIXABAY_API_KEY` (free) |

## Licences: read before commercial use

- **Commercial, no credit:** Mixkit. Default to it for any monetised or branded edit.
- **CC-BY (credit required):** Incompetech, ccMixter. Credit lines are saved in `meta.json` `credits[]`; give them to the user verbatim for the post description.
- **Restricted:** myinstants, Klipy, Tenor, Giphy, Imgflip. Terms limit redistribution and commercial use; warn the user or swap for a Mixkit/CC-BY asset.

Every run prints `license_summary` (`all_commercial_ok`, `any_restricted`, `any_credit_required`). Read it; never guess.

## Run it

```bash
bash $SKILL_DIR/scripts/preflight.sh          # python, network, key check (Git Bash on Windows)
python3 $SKILL_DIR/scripts/find_assets.py --type sfx   --query "swoosh" [--count 3] [--output-dir DIR]   # your own sounds first, then Mixkit, myinstants
python3 $SKILL_DIR/scripts/gen_sfx.py --prompt "soft pop, short" --duration 1 --out pop.mp3 --estimate-only   # step 3, paid: ask first, then --confirm-cost
python3 $SKILL_DIR/scripts/find_assets.py --type music --query "upbeat corporate" [--count 1]   # your own music first, then free stock
python3 $SKILL_DIR/scripts/find_assets.py --type meme  --query "mind blown" [--count 2]
python3 $SKILL_DIR/scripts/find_assets.py --type broll --query "city night market" [--count 2]
python3 $SKILL_DIR/scripts/find_assets.py --type image --query "serum bottle" [--count 3]   # local first, then Pexels, Pixabay
python3 $SKILL_DIR/scripts/find_assets.py --type icon  --query "rocket" [--color "#FFD400"]   # local first, then free icon sets (SVG)
python3 $SKILL_DIR/scripts/find_assets.py --type font  --query "Bebas Neue" [--subset thai]     # local first, then Fontsource
python3 $SKILL_DIR/scripts/find_local.py --type image|video|icon|music|sfx|font|template --query "tokyo night"   # step 1 only
```

Output is JSON (`files`, `sources`, `credits`, `note`). Files land in `projects/_work/assets/<query-slug>/<type>/` with a `meta.json`.

- **Queries are English only** (stock sites index in English). Use concrete nouns plus one mood word. Non-Latin queries produce a `query_warning`.
- **Choosing queries:** SFX = the beat you are scoring; music = mood words (`calm`, `chill lofi`); meme = the reaction the line calls for.
- **Picking between candidates:** `--count 3 --inspect` adds a `probe` per file (`duration_s`, `mean_volume_db`, `max_volume_db`). Prefer SFX of 3 s or less. Match music energy to the edit's shape.
- **Never drop music into a mix at a guessed volume.** Run it through `indigo11-edit/scripts/mix_bgm.py prep` first (voice minus 20 LU, with ducking).

## AI generation (paid): estimate, ask, generate

This is step 3 (real photo or real footage needed) and step 4 of the search order: use it only when local files and the free chain returned nothing usable, or the user asks. `gen_asset.py` reads `providers.image.chain` / `providers.video_gen.chain` from the optional config file, picks the first provider that has its key in `.env`, and otherwise falls back to OpenRouter.

```bash
python3 $SKILL_DIR/scripts/gen_asset.py --type video --prompt "..." --duration 4 --estimate-only   # no charge
# Ask the user with the price: "Generate a 4 s clip for about 13 baht, OK?"
python3 $SKILL_DIR/scripts/gen_asset.py --type image --prompt "flat-lay of street food, top view"
python3 $SKILL_DIR/scripts/gen_asset.py --type video --prompt "crowded night market, neon" --duration 4 --resolution 720p --aspect 9:16
```

| Type | Provider id | Key | Notes |
|---|---|---|---|
| Image | `gemini_flash_image` | `GEMINI_API_KEY` | about $0.067, recommended |
| Image | `gemini_flash_lite_image` | `GEMINI_API_KEY` | about $0.034, cheapest |
| Image | `gemini_pro_image` | `GEMINI_API_KEY` | about $0.134, sharpest text in image |
| Image | `xai_grok_image` | `XAI_API_KEY` | about $0.05; needs the image endpoint enabled on the key |
| Image | `openai_image` | `OPENAI_API_KEY` | OpenAI GPT Image 2 (`gpt-image-2`) called directly, no OpenRouter; quality `high`; rough estimate about $0.13, check OpenAI's pricing; sizes 1024x1024, 1536x1024, 1024x1536; used automatically when it is the only image key |
| Image | `openai_gpt_image_2` (alias `openrouter`) | `OPENROUTER_API_KEY` | the same model (`openai/gpt-5.4-image-2`) via OpenRouter; about $0.13 at high quality; renders text well, good title cards |
| Video | `gemini_omni_flash` | `GEMINI_API_KEY` | about $0.10 per second at 720p, 3-10 s, audio built in |
| Video | `openrouter` (Grok Imagine) | `OPENROUTER_API_KEY` | $0.05-0.07 per second, cheapest |

Prices drift, so always re-run `--estimate-only`. Any OpenRouter image or video model slug (`gpt-5.4-image-2`, `flux.2`, `seedream`, `recraft`; for video `grok-imagine-video-1.5`, `kling-v3.0`, `seedance`) can be set in the config under `providers.<task>.models.openrouter`; an empty value uses the built-in default.

- A B-roll insert holds 1.5-3 s, so generate 3-4 s, not 10. Cheaper still: generate a still image and add a slow zoom in the editor (about five times cheaper). Use video generation only for real movement.
- With no usable key, generation fails with a message naming the keys that would unlock it; the free chain still works.
- Actual cost lands in `meta.json` `cost{}`. Generated assets have licence tier `generated`.

## Generated music (gen_music.py)

Step 3 of the search order for music (your own music and free stock come first); it costs money, so show the estimate and wait for a yes.

```bash
python3 $SKILL_DIR/scripts/gen_music.py --prompt "calm lofi for a cooking clip" --duration 20 --out bgm.mp3
python3 $SKILL_DIR/scripts/gen_music.py --prompt "..." --estimate-only
```

| Provider id | Key | Cost |
|---|---|---|
| `elevenmusic` (default) | `ELEVENLABS_API_KEY` | about $0.15 per minute (paid plan) |
| `gemini_lyria3_clip` | `GEMINI_API_KEY` | about $0.04 per track |
| `gemini_lyria35` | `GEMINI_API_KEY` | about $0.08 per track |

Same estimate, ask, generate contract.

## Klipy key (optional, memes only)

Register a free key at docs.klipy.com and add `KLIPY_API_KEY=...` to `.env`. Without it, memes fall back to Tenor, Giphy, then Imgflip.

## Output and questions

Default output: `projects/_work/assets/<query-slug>/<type>/` with `meta.json` (`skill, type, query, ran_at, results, credits, note`); override with `--output-dir`. When using `AskUserQuestion`, keep options short and plain, in Standard British English.
