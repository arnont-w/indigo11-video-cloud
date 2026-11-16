# SPEC: <video name>

- source: <path/url> · <W>×<H> · <fps> fps · <duration> s · <frames> frames · audio: VO? music? SFX?
- purpose of this breakdown: <learn story / measure motion / style transfer / remake>

## 1. Identity and format
One paragraph: what it is, who it's for, the promise, and the platform format (9:16 reel, 16:9 launch …).

## 2. Shot table (no gaps)
| # | f0–f1 | t0–t1 | content | transition in / out | camera | host / assets |
|---|---|---|---|---|---|---|

## 3. Story beats (+ semantic tags)
| t0–t1 | beat (HOOK / CONTEXT / PIVOT / MECHANISM / HOW / RESULT / CTA …) | exact VO or super | on-screen label | visual metaphor | semantic tags |
|---|---|---|---|---|---|
Framework match: V? (playbook) / F? (story-frameworks); what is new.

## 4. On-screen text in order (verbatim)

## 5. Style fingerprint
- palette (hex + role): …
- fonts (family / weight / size px, from cap height): …
- texture / line / grain / shadows: …
- frame cadence (on ones / twos, boil): …
- global grade per frame: background tint, haze, fades, exposure curve (`track.py grade`), often the largest-area effect
- 4 key frames: <paths>

## 6. Motion numbers (per recurring move)
| move | frames | duration | easing / spring (ω, ζ, overshoot) | stagger | measured by |
|---|---|---|---|---|---|

## 6b. Chrome geometry (card-style reels, UI)
| element | x | y | w | h | radius | measured by (`track.py edge` / `ink` / `bands`) |
|---|---|---|---|---|---|---|

## 6c. Caption timing
| chunk / word | first active frame | first spoken frame | transcript start | lead (ms) |
|---|---|---|---|---|

## 7. Camera
Push-in rate, punches, zoom hard-cuts, shake, and parallax.

## 8. Transitions catalogue
| t | type | frames | how it's built |
|---|---|---|---|

## 9. Sound map
- tempo / beat period; strong cues (t, intensity); where the picture locks to the music
- SFX per event: | t | event | sound role | notes |
- music arc (builds, drops, low-pass, silence); loudness (LUFS, TP)

## 10. Pacing numbers
words/s, sentence length, shot length (median / min / max), picture lead over speech, events per second.

## 11. What makes it work (≤10 rules, each with evidence: frame/time)

## 12. Reusable parts + honest caveats (what was guessed, what could not be measured)
