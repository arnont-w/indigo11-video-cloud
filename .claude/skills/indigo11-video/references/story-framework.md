# Story framework for short-form video

Derived from an analysis of 48 shorts from one explainer channel (every clip transcribed, July 2026). It works for any topic, not only food, and is written so a script can be checked against it. Pivot words are given in English; for Thai scripts the pivot is "แต่" and the checks are in `make_short.py`.

## 1. Why it transfers to other topics

The core is a three-step psychological mechanism, not a subject:
1. The viewer already holds a belief (prior belief).
2. The clip first reinforces that belief (reinforce).
3. Then it breaks it with evidence, mid-clip (reversal).

Any topic with "something most people get wrong" fits: money, health, history, technology, law, sport, cars, animals, psychology.
**One condition:** you must find a real misbelief and a fact that really disproves it. If you cannot, do not force the framework: it becomes clickbait that under-delivers. Use MECHANISM instead.

## 2. The five beats

Times are for the median 26.5 s clip; scale them to the real length (up to 59 s: the pivot stays at about 50%, so a longer SETUP needs a longer PAYOFF).

| # | Beat | Position | Job |
|---|---|---|---|
| 1 | **HOOK** | 0-3 s (0-11%) | Throw a conclusion that contradicts common sense. No greeting, no run-up |
| 2 | **SETUP** | 3-12 s (11-45%) | Confirm what the viewer already believes |
| 3 | **PIVOT** | **about 50%** (40-60%) | Turn it with "but" |
| 4 | **PAYOFF** | 50-85% | Answer with something solid |
| 5 | **CUT** | end | Stop at once. No call to action |

Evidence: 48/48 clips have a pivot sentence starting with "but"; the pivot sits at a median of 50% (mean 49%, standard deviation 15 points); 0/48 have a CTA; 0/48 open with a greeting; 40/48 run 20-35 s.

| Pivot position | Clips | Share |
|---|---|---|
| 47-53% | 24/48 | 50% |
| 40-60% | 36/48 | 75% |
| 30-70% | 39/48 | 81% |

Treat 50% as the centre, not an iron rule. About 19% of clips pivot outside 30-70% on purpose, mostly RANKING (which must pivot early to start the countdown). **For automation use 40-60% as the pass range and aim for 50%**; the checker in `make_short.py` warns outside 25-75% so it never rejects a good script.

## 3. The eight templates

| Template | Clips | Average views | Pivot at | Opening formula |
|---|---|---|---|---|
| **MECHANISM** | 5 | 2,490,000 | 32% | "why does {X} {phenomenon}" |
| **PRICE_JUSTIFY** | 5 | 2,045,000 | 52% | "what makes people pay {price} for {X}" |
| **RANKING** | 5 | 1,950,000 | 18% | "ever wondered which {unit} {verb}s the most in the world" |
| **WARNING** | 8 | 1,507,000 | 49% | "don't {verb} {X} until you know this" |
| **MYTH_BUST** | 11 | 1,453,000 | 50% | "the world may be wrong that {X} belongs to {Y}" |
| **ORIGIN_STORY** | 4 | 1,387,000 | 52% | "in {year} everyone thought {X} was terrible" |
| **BIZARRE_EVENT** | 6 | 859,000 | 47% | "someone {did an odd thing}, but what came back..." |
| **REVEAL_TRUE_NATURE** | 4 | 454,000 | 49% | "if you knew what {X} really is, you might never {verb} it again" |

The most-used template (MYTH_BUST, 11 clips) is not the best performer: MECHANISM and PRICE_JUSTIFY have about double the average views from only 5 clips each, an under-used gap. The pivot is not always central: MECHANISM (32%) and RANKING (18%) pivot early because they go straight to the explanation or countdown.

## 4. Each template, with cross-topic examples

**MYTH_BUST** (the origin or owner is not what people think). HOOK: the world may be wrong that {X} belongs to {Y}. SETUP: many grew up picturing... it is everywhere... it became a symbol. PIVOT: but the more {X} became the familiar image, the more its true origin was forgotten. PAYOFF: in fact {X} has roots in **{Z}**, before {Y} developed into what it is today. Examples: "the world may be wrong that credit cards were invented for convenience"; "that QR codes are Japanese"; "that you must drink 8 glasses of water a day".

**WARNING** (looks safe, hides a danger). HOOK: don't {verb} {X} until you know this. SETUP: for years {X} was seen as... PIVOT: but the truth may not be that. PAYOFF: {X} may contain **{named germ / substance / mechanism}**, which can... CLOSE: for safety, {prevention} first (a safety note, not a CTA; the only template with a closing line). Examples: "don't sign a 0% instalment contract until you know this"; "don't join public Wi-Fi until you know this".

**RANKING** (a ranking that overturns the guess). Ask a question, point out that people guess wrong, count down 5 to 2, tease, reveal number 1. HOOK: ever wondered which {unit} {verb}s the most in the world. SETUP: most would guess {obvious choice} because... PIVOT: but measured **{per-person unit}**, the result flips. PAYOFF: number five... four... three... two... TEASE: but the {unit} that {verb}s even more than number two... CUT: **number one: {answer}**, then stop at once with no summary. This is why these run longer (55-66 s) yet still average high. Examples: which country works hardest; which company pays most; which language is hardest.

**PRICE_JUSTIFY** (why something is so expensive). HOOK: what makes people pay {price} for {X}, when it is only {dismissive word}. SETUP: many cannot understand it; some say... PIVOT: but behind the price is something most people never learn. PAYOFF: to get one {X} you need **{hidden cost}**. CLOSE: so what you pay for is not just {X} but **{something intangible}**. The closing line must turn the price into something you cannot hold ("decades of time", "a fisherman's life"): that is why this template averages highest. Examples: why a watch costs a million; why medical school fees are so high; why a GPU costs as much as a car.

**ORIGIN_STORY** (the past saw it the opposite way). HOOK: in **{year}** everyone thought **{X}** was **{something terrible}**. SETUP: rumours spread until almost nobody dared... PIVOT: but the hidden truth was more shocking than anyone thought. PAYOFF: the cause was not {X} at all but **{real cause}**. Examples: coffee was once banned; video games were once blamed; trains were once believed to drive people mad.

**MECHANISM** (highest average views). A phenomenon people notice but cannot explain, then the mechanism behind it. HOOK: why does **{X}** **{unexpected phenomenon}**. SETUP: many think it is because... PIVOT: **but the truth is...** (early, about 32%, because the viewer wants the answer fast and the question already did the setup). PAYOFF: because of **{system-level mechanism}**. Examples: why flight prices keep changing; why a free app can survive; why a meeting room echoes.

**BIZARRE_EVENT** (a person or brand did something odd). HOOK: someone **{did an odd thing}**, but what came back **{was astonishing}**. SETUP: tell the origin with year, brand and person's name. PIVOT: but a few months later... / but when someone asked... PAYOFF: the real outcome, with details that can be checked. It must contain a specific, searchable name (brand, year, person) or it sounds invented.

**REVEAL_TRUE_NATURE** (it is not what you think it is). HOOK: if you knew what **{X}** is, some people might never **{verb}** it again. SETUP: many know {X} as... a premium product that... PIVOT: but it is not **{what people assume}** at all. PAYOFF: what we eat as {X} is really **{true nature}**. Unlike MYTH_BUST (origin or owner), this argues about the thing's own identity. Examples: imitation crab contains no crab; some honey is syrup; some "leather" is pressed offcuts.

## 5. Rules not to break

| Rule | Evidence |
|---|---|
| No greeting | 0/48 clips have one |
| No call to action | 0/48 ask for likes, follows or shares |
| One clip, one fact | none packs in two topics |
| The answer needs something solid | 35/48 have a year, name or number; the rest explain a **mechanism** |
| The pivot is mid-clip | median 50%; 75% fall in 40-60% |
| 20-35 seconds | 40/48 (RANKING excepted) |

**"Something solid" in the PAYOFF** is one of two kinds (either is enough): **specific data** (a year, a person, a substance, a number: "founded in 1920", "2.69 million Scoville") or **a mechanism** (how it works: "green tea is an infusion of the leaf, matcha is the whole ground leaf, drunk entire"). 13/48 clips have no number and are still tight. Never answer vaguely ("it depends", "there are many factors"): viewers feel cheated.

Extras seen in the channel: English titles with a local-language voice (reach through metadata); and contested national topics drive engagement but carry brand-safety risk, so stay strictly with historical evidence if you touch them.

## 6. Pivot bank

Frequency across the 48 clips: "but when" 11, "but the truth" 10, plain "but" 9, "but what" 5, "but there is" 4, "but the more" 3. Reusable full lines: "But when people actually looked back at the history, what they found left many speechless." / "But the truth may not be that." / "But the more {X} became the familiar image, the more its true origin was forgotten."

## 7. For automation

Pipeline: INPUT topic + facts -> SELECT template (tree below) -> FILL the slots -> VALIDATE (checklist) -> TTS -> TIMING (pivot at 40-60%, adjust SETUP length if not) -> RENDER.

```
What kind of fact do you have?
|- why a phenomenon happens                  -> MECHANISM          (2.49M/clip)
|- a price that seems unreasonable           -> PRICE_JUSTIFY      (2.05M/clip)
|- compare several options / a countdown     -> RANKING            (1.95M/clip)
|- a danger or risk people do not know       -> WARNING            (1.51M/clip)
|- origin or owner differs from belief       -> MYTH_BUST          (1.45M/clip)
|- the past saw it the opposite way          -> ORIGIN_STORY       (1.39M/clip)
|- an odd event a person or brand really did -> BIZARRE_EVENT      (0.86M/clip)
`- it is really not what people think        -> REVEAL_TRUE_NATURE (0.45M/clip)
```
Cannot choose? Ask what the viewer feels at the end: "ah, so that is how it works" = MECHANISM; "wow, I never thought so" = MYTH_BUST or REVEAL_TRUE_NATURE; "I had better be careful" = WARNING.

Validation checklist (before render): no greeting in the first sentence; no CTA words (like, follow, share, comment, subscribe); at least one pivot sentence starting with "but"; pivot at 40-60% (MECHANISM 20-45%, RANKING 10-35%); PAYOFF has specific data or a mechanism; no vague answer; about 20-35 s (RANKING 50-70 s); one main point only.

Pivot position: never use `script.find("but")` alone, because it finds an ordinary conjunction ("not only the roe but time"). Search for the specific pivot phrases first ("but the truth", "but when", "but the more", "but what", "but there is", then "but"), skipping the first 20 characters (the HOOK), and take `idx / len(script)`; if outside 0.40-0.60, adjust SETUP or PAYOFF. Measuring by character index gives a median of 48% against 50% from word timestamps (speech rate varies), so use characters when drafting and the post-TTS timestamps for precision.

## 8. Cautions

- View counts are rounded by YouTube (7.3M, 4.6M): good for proportions, not exact.
- Small samples: PRICE_JUSTIFY had only a few clips, so its average moves easily. It shows direction, not statistics.
- Other variables were not controlled (thumbnail, posting time, algorithm).
- The answer must be true: the framework works because it promises an answer. A weak or invented answer destroys trust quickly.
- Do not use it on a topic with no real misbelief: it becomes clickbait.

Sources: the list was pulled with `yt-dlp` from the channel's shorts page (48 clips), transcribed with ElevenLabs Scribe v1 with word timestamps (48/48 succeeded), pivot positions mapped from character index back to real word times; collected 27 July 2026.
