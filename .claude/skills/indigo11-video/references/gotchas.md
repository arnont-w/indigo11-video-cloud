# indigo11-video: lessons from real jobs (gotchas)

Everything here was **measured or hit for real** and cannot be worked out from the code. Do not reverse these fixes.

## B-roll sources: stock may be posted, YouTube may not

Measured with the keywords beer, coffee and city (50 clips each) and `wolf` (7 each):

| | Pexels | Pixabay | YouTube |
|---|---|---|---|
| Post / earn from it | **free** | **free** | **no** (other people's copyright) |
| Usable clips (`wolf`) | 6/7 | **7/7** | 5/10 (a film, Minecraft, "subscribe" clips) |
| Watermarks / burned-in captions | none | none | often |
| Sharpness | 720-1080p | **2560x1440** | 1080p |
| Real brands / events | no | no | **yes** |
| Time for 6 clips | **about 9 s** | about 2 min (big files) | about 49 s |

- **Do not filter for portrait.** The render centre-crops to 9:16 anyway; forcing portrait throws good clips away (Pixabay keeps only 6%). Choose files by "still sharp after enlarging" (cover factor 1.34 or less) rather than orientation.
- **Pixabay files are huge** (4K, about 36 MB a clip), so 7 clips take over 2 minutes: run in the background if the terminal has a timeout.
- **Pexels 403 `error code: 1010` with a correct key** means Cloudflare blocks the `Python-urllib` user agent. The script sends its own `User-Agent` (constant `UA`). Do not chase the key.
- Real limits from headers: Pexels `x-ratelimit-limit: 25000` a month, Pixabay 100 requests per 60 s.
- Keep YouTube for real brands and events the stock libraries lack, and only for viewing and prototypes.

## The contact sheet must crop like the render, or it lies

`make_short.py` uses `scale=...:force_original_aspect_ratio=increase,crop=1080:1920` (centre crop). The old `verify.py` shrank the whole frame, so a landscape clip showed full width although the render keeps only the central **31.6%** (measured on 1280x720). Clips were chosen from pictures nobody would ever see. The `FIT` constant in `verify.py` **must always match make_short.py**: change one, change the other.

## The shade behind captions: do not reject a clip for text in the caption zone

A shade behind the captions (y 1090-1570) covers old text so the captions stay readable (tested with screenshots). It helps **only the caption zone**; text mid-screen must still be rejected. `ink` measures only the caption strip and cannot see text higher up, so **never trust `ink` alone: always look at the sheet.**

## Do not make the picking rules so strict that no work gets done

- Small text in a corner that does not overlap the captions passes. Reject only text that overlaps the captions, a large mid-screen watermark, or captions on every frame.
- Four rounds were once wasted chasing clips with small text in the lower corner: **shipping a video beats perfection; once you have 5 good clips, move on.**
- The picture need not match the voice exactly. It stops people scrolling; the voice tells the story.
- Burned-in captions in a source clip can sit anywhere (once at 70% of the height), so **scan the whole frame**.
- Soft mix guide: process about 45%, people using or eating about 30%, still beauty shots about 17%. Do not loop for new clips just to hit it.

## Search terms

- **Search by the topic, not a narrow term from the script.** For beer, `beer` matches about 9/12 but `guinness` about 5/12 (it returned a 33M-view Uber clip), because small tags are applied at random. This is counter-intuitive.
- No extra words at first (`giraffe`, not `giraffe safari asmr`, which returned crafts). Add them as a fallback basket: `--q "beer" "beer pour"`.
- For animals and everyday things avoid `funny`, `cute`, `best` (compilation channels, 11/11 with banners). Use activity words: `cat sleeping`, `cat eating`.
- Spell out names with special characters: `m&m's` becomes `#mms` = MMA (a UFC clip); `m and m` = the real M&M's, 6/6.
- Titles containing `ranking`, `compilation`, `moments`, `best of`, `funniest`, `top 5`, `countdown`, `tier list` are filtered out before download (`BAD_TITLE`).

## YouTube (prototype use only)

- Do not fetch metadata one clip at a time with `-J` (5.7 s a clip); `--flat-playlist -J` for the whole set takes 1.7 s. `/shorts/` URLs are already vertical.
- **Many parallel requests get you rate-limited** (6-8 lanes gave 0/8 downloads). The script locks 2 lanes, a 1.5 s gap and automatic back-off. Never raise `YTDLP_JOBS` above 3 or lower `YTDLP_GAP` below 1.
- Bot checks happen now and then: set `YTDLP_BROWSER=chrome`; do not chase keywords. Read the error message first.
- 50-60 s clips make a contact sheet spill over to 3 pages: keep `--max-dur 30`.

## Voice and captions

- **ElevenLabs gives almost no pause marks** (2 in 27 s measured), so captions cannot follow the voice. The script's **line breaks and punctuation** are the boundaries; a tokeniser (Thai) or word grouping (English) is only a fallback.
- English: `english_captions.py` breaks at line ends, sentence ends and ellipses, then splits long phrases into the fewest pieces (at most 36 characters and 6 words) at commas or before conjunctions, never ending a caption on "the", "of", "is" and similar.
- Thai: two sentences merged (`อาจถึงตายได้ปี2011`) was fixed with `SENT_GAP = 0.12` (a longer pause = a new sentence, never merge). Blank lines in the script do not help: they collapse into spaces first. A connector in `SPLIT_AT` must sit on a real word edge (`มา` in the middle of `หมาป่า` once cut it in half; fixed with `word_bounds`). If no safe cut exists, leave the phrase long. Spoken numbers become digits from 10 up; a lone scale word (`แสนอร่อย`) is not converted.
- The voice is never cut (the B-roll is cut to the voice). An abrupt end means **a weak script ending**, not a cutting fault.

## Caption rendering

- **English:** ffmpeg burns an ASS file with libass (Arial bold 80, black outline, soft shadow, pop-in, lower third at y 1330). The ffmpeg build must have the `subtitles` filter: Windows `winget install Gyan.FFmpeg`; macOS `brew install ffmpeg-full` (the plain Homebrew build lacks libass).
- **Thai:** needs a good shaper for vowels and tone marks, so it uses Chrome (`render_caps.mjs` + puppeteer-core, Noto Sans Thai 800, 82 px, 10 px black stroke, back-out pop-in over 0.17 s).
- Numbers on the contact sheet are drawn with Pillow because many ffmpeg builds lack `drawtext`.

## `check_story()` thresholds come from real data: do not guess and adjust

| Check | Value | Why |
|---|---|---|
| Pivot position | 25-75% | covers 47/48 reference clips (40-60% warned on 8 good scripts) |
| Finding the pivot | the one nearest 50% | "but" is also an ordinary conjunction |
| Numbers | number words count too | a script may say "two hundred and seventy thousand", not `270000` |

Warnings only, never blocking (11/48 real clips have no number and answer with a mechanism).

## Fixed bugs (do not revert)

| Symptom | Real cause |
|---|---|
| Thai `แสนอร่อย` became `100000อร่อย` | a lone scale word was converted to a number |
| `one hundred million` became 101,000,000 | `max(cur,1)` in the million branch |
| Caption cut mid-word | a blind cut when no connector was found |
| Caption overflowed 2 lines | `MAX_CHARS` too high |
| Video looped A-B-A-B | `clips[i % len]` with no minimum |
| Frame extraction very slow | one ffmpeg per frame (about 130 spawns) |
| Search for `m&m's` returned MMA | `&` stripped, giving `#mms` |
| 0/8 downloads | 6-8 parallel lanes |
| B-roll search took about 9 minutes | per-clip metadata (`-J`) |
| 4/6 clips opened with "did you know" | the template sat only in the reference file, not in SKILL.md |
| Check sheet differed from the render | `verify.py` squeezed the frame, the render centre-crops |
| Last sheet row bright green | `xstack` had no `fill=black` |
| Pexels 403 with a correct key | Cloudflare blocking the `Python-urllib` agent |

## Limits to tell the user plainly

- Picking clips still needs a human eye; text recognition misses faint text. Do not claim it is fully automatic.
- YouTube clips belong to others: for viewing and prototypes only, never posted.
- Facts in the script must be checked: use WebSearch/WebFetch for sources, never write from memory.
- ElevenLabs voices are commercial-use only on a paid plan. A v3 voice's accent follows the voice, so pick a British voice for British English.
