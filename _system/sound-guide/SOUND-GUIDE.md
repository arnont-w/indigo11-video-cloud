# Sound guide: what each sound effect is for

Claude cannot hear. This file is its ears. It lives in `_system/` so it is not edited by accident: only Claude writes here, after asking the user. Before placing any sound effect, Claude reads this file and picks sounds from it. Claude uses only sounds that have a row here.

How the file works:
- One row per sound file (first column = file name, a path inside `assets/sound-effects/` is fine).
- "Confirmed" says who wrote the row: `no` = Claude's draft from the file name and length, not heard by anyone; `yes (use)` = the "Use it for" column comes from you (the pre-installed list); the other columns are still drafts; `yes` = you checked the whole row.
- When a new sound appears, Claude asks you the questions at the bottom, then adds a row with `Confirmed: yes`.
- Claude never invents what a sound sounds like. If you have not told it, the row stays `no` and Claude says so.

| File | Sounds like | Use it for | How (timing, volume) | Avoid | Length s | Confirmed |
|---|---|---|---|---|---|---|
| click.wav | Tiny dry click | Button press; counting numbers | On the frame it appears. Low volume. | Big dramatic moments | 0.09 | yes (use) |
| click-2.wav | Extremely short tick, almost silent | Button press; counting numbers (very faint) | Layer with another sound. Very low. | Using it alone (too faint) | 0.01 | yes (use) |
| tick.wav | Soft tick | Button press; counting numbers | On each item, low volume | Long runs at high volume | 0.04 | yes (use) |
| tick-2.wav | Softer, shorter tick | Button press; counting numbers (variant of tick) | Alternate with tick.wav so repeats do not sound identical | - | 0.02 | yes (use) |
| pop.wav | Soft bubble pop | Things popping in: chips, stickers | At the start of the pop-in animation | Serious or sad moments | 0.19 | yes (use) |
| pop-2.wav | Slightly different pop | Things popping in: chips, stickers (variant of pop) | Alternate with pop.wav | - | 0.19 | yes (use) |
| impact.wav | Heavy punch | The peak moment, once or twice per video | On the exact beat, once or twice per video at most | Frequent use (loses power) | 0.55 | yes (use) |
| impact-2.wav | Softer heavy thud | Gentler emphasis, section change | On the beat, a little lower than impact.wav | - | 0.57 | no |
| cartoon_thump.wav | Short soft thump | Playful emphasis, small bounce, landing | On the landing frame | Serious topics | 0.18 | no |
| swish.wav | Card sliding | Short swipe; scene change | Start just before the cut or move | Static moments | 0.6 | yes (use) |
| swish-2.wav | Longer card shove | Short swipe; scene change (longer variant) | Start just before the move | - | 0.77 | yes (use) |
| sparkle.wav | Shiny glass ting | Something new | On the highlighted word | Warnings, bad news | 0.69 | yes (use) |
| success.wav | Rising confirmation | Success | When the tick or result appears | Wrong or neutral moments | 0.54 | yes (use) |
| notify.wav | Short question-like blip | Notification | On the notification or question word | - | 0.33 | yes (use) |
| error.wav | Low error buzz | Wrong answer, myth, warning, mistake | On the red cross or the myth word | Positive moments | 0.19 | no |
| coin.wav | Chips clinking | Price or money | When the number appears | Non-money topics | 0.2 | yes (use) |
| coin-2.wav | Single chip laid down | Variant of coin | Alternate with coin.wav | - | 0.17 | no |
| whoosh.wav | Synthetic air sweep (made in code, weak) | Scene change | Start just before the cut | Replace it with your own if you can | - | yes (use) |
| whoosh_small.wav | Shorter synthetic sweep (made in code, weak) | Light whoosh | Start just before the cut | Same as whoosh | - | yes (use) |
| riser.wav | Rising synthetic tone (made in code, weak) | Rising build-up before the answer or reveal | End it exactly on the reveal beat | Using it without a payoff | - | yes (use) |

## Questions Claude asks for every new sound
Ask in one message, one line per sound, and say short answers are fine.
- REQUIRED: When is it used? (which moment and beat, for example "on the cut", "when a price appears"). Without this answer Claude adds no row and does not use the sound.
- Optional (skip if the user does not answer; write `-` in that column):
  1. What does it sound like, in your own words?
  2. How loud, once or repeated?
  3. Anything it should NOT be used for?
  4. Where did it come from, and may you use it commercially?
