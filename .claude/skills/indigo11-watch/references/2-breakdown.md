# Level 2: BREAKDOWN → SPEC.md

Goal: turn a reference into numbers and rules we can reuse. This is the method used on many reference reels (card reels, launch films, SaaS clips). Worked results are not included in this pack.

## 2.1 Always (cheap, one pass)
```bash
W=<work>/watch/<id>; mkdir -p $W
python3 "$SKILL_DIR/scripts/breakdown.py" <video> --out $W/bd --fps 6 [--transcript $W/transcript.json]   # 6 for reels/fast, 3 for 2–3 min, 2 for slow tours
python3 "$SKILL_DIR/scripts/audio_cues.py" <video> $W/audio                   # tempo, beats, strong cues, SFX hits, silences
python3 "$SKILL_DIR/scripts/popscan.py" <video> [--period 100:400]            # cuts + flash frames (+ loop period if it may loop)
python3 "$SKILL_DIR/scripts/transcript.py" <video> --out $W/transcript.json   # if there is speech (needs ELEVENLABS_API_KEY in the starter's .env)
```
- Read **every** grid in order, plus `shots.jpg`, `audio_cues.md` and the transcript.
- The cut detector is a heuristic. Confirm cuts on the grids; flashes and time-lapses fake cuts.

## 2.2 Measure what matters (only the moves you will reuse)
| Question | Tool |
|---|---|
| exact frames of a move or transition | `strip.py <video> --from 4.8 --to 5.6 --out $W/strips/x.jpg` |
| position, size or velocity over time (shape morph, card slide, zoom) | `extract.py <video> $W/ex --from F0 --to F1 --scale .5` then `track.py ink $W/ex/full --from F0 --to F1 --roi x,y,w,h` |
| cursor path / logo path | `track.py crop …` then `track.py template $W/ex/full tmpl.png --from F0 --to F1 --anchor ax,ay --subpixel --min-score 0.7 --max-step 60` (blurred frames give confident wrong matches without the gates) |
| palette | `track.py color $W/ex/full F --k 6`, or `--at x,y` for one pixel |
| global tint / haze / fades | `track.py grade $W/ex/full --from F0 --to F1` (median colour of edge patches per frame) |
| kinetic type: each text line per frame | `track.py bands $W/ex/full --from F0 --to F1 --roi x,y,w,h --bg r,g,b` |
| exact edges of static UI (card, sticker) | `track.py edge $W/ex/full --from F0 --to F1 --line x0,y0,x1,y1` (sub-pixel) |
| does it loop? | `popscan.py <video> --period 100:400` (never trust a loop claim without it) |
| spring / easing | fit the `track.py` CSV: overshoot %, settle time, ζ ≈ −ln(OS)/√(π²+ln²OS) where OS = overshoot fraction; `_system/engine/engine.js` K.spring(d, f, z) takes it directly |
| cut vs sentence timing | `pacing.py $W/bd/signals.json $W/transcript.json` (pairs; several clips at once) |
| font size | not cap height ÷ 0.7 (family-dependent). Compare x-height : ascender ratios across candidates, then fit size and letter-spacing to the measured ink widths of known strings with canvas `measureText` after fonts load. Canvas can't set variable-font axes: instance them first with `fontTools.varLib.instancer` |

## 2.3 Write SPEC.md (template: `references/spec-template.md`)
Sections:
1. identity and format
2. shot table (no gaps)
3. story beats + the 26 semantic tags
4. on-screen text in order
5. style fingerprint (palette hex, fonts, textures, line, frame rate / on-twos)
6. motion numbers (per recurring move: duration, easing or spring, stagger)
7. camera
8. transitions catalogue
9. sound map (beats, strong cues, SFX per event, music arc, loudness)
10. pacing numbers (words/s, shot length, lead of picture over speech)
11. what makes it work (≤10 rules)
12. reusable parts, with honest caveats

## 2.4 Compare against what we already know
- **Story**:
  - which framework is it? Common reel shapes: hook → problem → reveal → proof → CTA; list (3–5 items); before/after; myth vs fact; story with a twist. Name the one it uses and where each part starts (time)
  - what is new?
- **Visual / motion / sound**: add new numbers to the right knowledge doc, not only to the SPEC.

## 2.5 Big jobs with agents
- **Split the work**: use one agent per clip, or per batch of about 10 short clips for story-only work.
- **Brief template**: `references/spec-template.md`.
- **Hard rules to put in every brief**:
  - no sub-agents
  - append ≤120 lines per write
  - a STATUS line at the top
  - honest about guesses
- **Cost**: frame-level analysis is 320–500k tokens per clip on Opus. Story-only batches of about 10 reels ran about 250–450k on Sonnet. Run ≤3 agents in parallel.
