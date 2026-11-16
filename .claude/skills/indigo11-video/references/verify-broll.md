# B-roll picking criteria

Use when you open the contact sheet before cutting. **Never skip it.**

## The idea

The picture exists to **stop people scrolling**; the voice tells the story. A clip need not match the voice exactly: a child playing with a giraffe works even if the voice talks about humming.

## Reject only two things

| # | Reject when | Why |
|---|---|---|
| 1 | A watermark or big text **mid-screen** (outside the red frame) | the dark shade does not reach it |
| 2 | So dark or blurred it cannot be understood | nothing to see |

**Text inside the caption zone (the red frame on the sheet) passes: do not reject it.** The render lays a translucent dark shade over that zone (tested: the old text becomes unreadable, the new captions stay clear).

"Not on topic" is no longer a rejection reason: the same vibe works. For a beer video, bars, people clinking glasses and pouring drinks are all fine. Reject only what is truly unrelated (an empty road, a building, a corridor).

> **Scan the whole frame, not one strip.** A check once missed captions because it looked only at the bottom strip, while the captions sat at 70% of the height.

## Mix of clip types

Counted from 24 frames of a real reference channel:

| Type | Share |
|---|---|
| Process (making, preparing, close-up hands) | about 45% |
| People using or eating | about 30% |
| Beautiful stills (people optional) | about 17% |

Do not use one type for 100%: search both angles (keyword table below).

## How many to prepare

| Needed | Download |
|---|---|
| 5 clips (minimum) | 10-12 clips |

Only about 30-50% pass, depending on the topic.

## Keywords

**Spell out brands with special characters**: `&`, `'` and `.` are stripped when tags are made.

| Written as | Becomes tag | Real result |
|---|---|---|
| `m&m's` | `#mms` | bad: MMS/MMA and news clips |
| `m and m` | `#mandms` | good: the real M&M's, 6/6 |

**Search the plain topic first** (`giraffe`, `kimchi`). Do not add extra words at the start (`mukbang`, `safari asmr`): the results drift to something else. If you get too few, add one word at a time:

| You want | Add |
|---|---|
| Process | `making`, `cooking`, `recipe` |
| People using or eating | `eating`, `asmr`, `mukbang` |
| Original language | a native-language term can find the real thing where English finds toys |

**Avoid** `commercial`, `stock footage`, `4k`: they return adverts and stock reels.

## Clip sources (read before choosing)

- **A video you will post = stock libraries only** (`--source pexels` / `pixabay`, or `auto` when a key is in `.env`). Free for commercial use.
- **YouTube = research and prototypes only, never post** (the owner's copyright). The script writes `_source.json` so `make_short.py` warns, and places `<slug>.NOT-FOR-PUBLISHING.txt` beside the file.
- Animals, nature and everyday subjects: stock is clearly better (a YouTube `wolf` search returned films, games and "subscribe" clips for half the results).

## Tool limits (YouTube)

- Shorts search uses InnerTube (the web page's own API, no key): vertical clips plus real titles, so titles are filtered before download.
- If that fails it falls back to `ytsearch:` (landscape, but the render centre-crops to 9:16).
- `yt-dlp` cannot search Shorts by itself: `ytsearch` returned landscape 30/30, and the `sp=EgIYAQ` filter landscape 12/12.
- The `/hashtag/<word>/shorts` page returns `duration=None` / `title=None` for nearly all clips, so nothing can be filtered before download.
- `--shorts-only` refuses landscape clips entirely.
- Results differ from a signed-in browser (the web page uses a personal algorithm).

## When YouTube asks you to confirm you are not a bot

Usually **no cookies are needed**; search and download both passed in tests. If you see `Sign in to confirm you're not a bot` (it happens now and then after many requests):

```bash
export YTDLP_BROWSER=chrome     # or safari / firefox (Windows: set YTDLP_BROWSER=chrome)
```

The script warns by itself when it meets this.

## Cut rhythm

- **3.6 seconds** a segment: shorter tires the eye, longer bores.
- **Switch clip every segment**; never the same clip twice in a row (`make_short.py` enforces it).
- A 26 s video is about 7-8 segments; a 59 s video is about 16 (that needs at least 5 different clips, so download 10-12 as usual and rotate them).
