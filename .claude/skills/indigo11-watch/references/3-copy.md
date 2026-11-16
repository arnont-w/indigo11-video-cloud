# Level 3: COPY (3A style transfer · 3B frame-locked remake)

Both start from a Level 2 SPEC. Never start building from vibes.

## Which one
| | 3A style transfer (default) | 3B frame-locked remake |
|---|---|---|
| Output | our story, told in their look, pacing and sound | their film, shot for shot, with our brand swapped in |
| Use for | our own reels, explainers, launch videos | practice; proving the engine; a client asking for "this exact video, our brand" |
| Risk | low (styles and techniques are not owned) | copying another brand's creative 1:1 and publishing it commercially can infringe. Use it to learn, or with the rights holder's permission |
| Worked examples in this pack | none shipped (there is no `templates/` folder). The base is `_system/engine/animation-starter.html` + `_system/engine/render_frames.mjs` | none shipped; use the same base and the 3B steps below |

## MOTION RULE (never skip): a copy must MOVE
A style copy or remake is an ANIMATED video. Still images with a voice-over and captions on top is NOT a deliverable (it happened once: all the motion was missing).
- **Build** with `_system/engine/animation-starter.html` and render with `node _system/engine/render_frames.mjs` (Chrome + ffmpeg, no npm packages). The page has one pure function `render(t)`; the starter already has entrances (pop with overshoot), constant motion (bob, pulse, drift), drawn-on effects (bars growing, a line drawing itself, a clock hand, a counting number) and scene fades.
- **Every effect the SPEC lists must exist** in the copy: if the reference has bars growing, a line chart or a clock hand, ours has them too. Report any that are missing; never ship without saying so.
- **Time entrances to the spoken words** (start of the word in `words.json` or the voice alignment), not to a fixed rhythm.
- **Prove it moves**: look at 4 stills spread across the video, then run `python3 _system/engine/motion_check.py final.mp4` and report its line. `STATIC` means not finished: add motion and run it again.
- This pack does not ship `render.mjs`, `new_project.py`, `mix.py`, `qa.py` or a `templates/` folder. Never rely on them.

---

## 3A: style transfer → a style pack
1. **Extract the pack from the SPEC** and save it as a template in `assets/templates/<name>/` (`python3 _system/engine/save_template.py <name> <files...> --source "<reference>"`; it makes the folder and a README):
   - `style.json`
     - palette tokens
     - fonts and sizes
     - texture, line and grain
     - motion profile: springs (ω, ζ) or easing, stagger, hold times, frame rate or on-twos, camera push-in
     - transition set
     - pacing: shot length, words/s, picture lead
   - `sfx/palette.json`
     - role → sound via `_system/engine/sfx_roles.py` (packs in `assets/sound-effects/`, roles: pop click tick swish whoosh whoosh_small impact riser sparkle success notify coin) or your own licensed files
     - gains, and peak or start alignment
   - `README.md`
     - the style in 5 lines
     - which story frameworks it suits
     - 4 reference stills
2. **Write our script** in the same story shape as the reference (hook → problem → reveal → proof → CTA, or the list / before-after shape the SPEC found). Tag each sentence with its job.
3. **Shotbook**: map each sentence to a shot. The visual comes from the style's vocabulary (one clear metaphor per sentence).
4. **Build the animated page**: copy `_system/engine/animation-starter.html` to `projects/_work/<name>/anim.html` and replace its scenes with the shotbook (pop-ins, constant motion and the drawn-on effects the SPEC found; every `at` = the start of its spoken word). Voice, music, sound effects and captions are made separately (`make_short.py`, `mix_bgm.py`, ElevenLabs) and mixed in with ffmpeg.
   - Every frame is a pure function of t.
5. **Show 4 stills first** (hook frame 0, mechanism, payoff, CTA): `node _system/engine/render_frames.mjs --page=anim.html --times=0.5,6,12,20 --frames-dir=stills`. Then render: `node _system/engine/render_frames.mjs --page=anim.html --dur=<seconds> --out=silent.mp4` (about 3 s of work per second of video), mix sound and captions in with ffmpeg, and run `python3 _system/engine/motion_check.py final.mp4` (`STATIC` = not finished). `popscan.py` finds loops in the reference.

## 3B: frame-locked remake (adapted from the public frame-locked remake process shared by @notdwd on X)
- **Swap only** brand, logo, palette, platform UI and faces. Everything else stays identical: layout, sizes, positions, timing, easing, camera, cuts, blur, cursor path, typing cadence and beat grid.
- **Acceptance**:
  - stacked, the ref and the remake hold the same pose on every frame
  - cuts are 0 frames off
  - position and size error is ≤1% of the frame
  - differences are allowed only where the swap forces them, and each one is reported

1. **Phase 0 = Level 2**, at full depth. Treat any earlier Level 2 prose as hints: 6 fps grids hid a wrong loop period, a missing card and a font-morph wave in the r12 pilot.
   - re-derive the beat list from full-rate strips (`strip.py`, every frame or every 2nd) before building
   - loops: `popscan.py --period`; global tint / haze: `track.py grade`
   - `extract.py` on ALL frames
   - `track.py` arrays for every major move (store the CSVs, because they are the truth)
   - `audio_cues.py`
   - the transcript table for VO
2. **Phase 1, the engine (the lead does this, not agents)**:
   - one HTML page at the reference's resolution (start from `_system/engine/animation-starter.html`: its `window.render(t)` is the `seek`; render with `_system/engine/render_frames.mjs`)
   - `seek(F)` is pure: no timers, no Date, no Math.random, no CSS transitions
   - a shot registry `SHOT({id,f0,f1,render(lf,F)})`
   - `core.js`: easing, `kf(F, keys, ease)`, `samples(F, f0, measuredArray)`, camera, motion blur, measured cursor, text reveal, logo as mask
   - palette swap only if the brief swaps the palette: for canvas, swap the colour-token table (a filter over HTML does not apply)
   - `ready` only after fonts and images load
3. **Phase 2, the build**:
   - ≤3–4 agents, each owning a contiguous group of shots in `shots/<G>.js` (they never edit core)
   - verify loop per shot: `compare.py sheet ref.mp4 ours.mp4 out.png <keyframes>` until it is within tolerance
   - ≤15 frames per render call
4. **Phase 3, integrate**:
   - render in full, then run `compare.py video ref.mp4 ours.mp4 sync.mp4 --diff --audio ref` (`--layout h` for square/landscape, `v` for 9:16) and `compare.py score`
   - score = mean abs gray diff at 96 px, area-downsampled (same metric for mp4 and PNG-folder inputs). Report it with the blank-frame baseline it prints, per-beat means (`--ranges`), and a second score with swapped regions removed (`--exclude x,y,w,h` / `--mask`)
   - captions: check per-word state (ghost / active / spoken) by colour inside the word boxes the engine exports, on ref and ours, per frame. (the ref lit each word a median 360 ms before the ElevenLabs word start)
   - check seams at group boundaries
   - scan for leftover old-brand colour
   - `popscan.py`
   - sound: a new royalty-free track (or ElevenLabs Music) cut to the same beat grid, SFX from `assets/sound-effects/` on the reference's hit times. Never reuse the reference's music or voice.
5. **Deliver**: `remake.mp4`, `sync-check.mp4`, `SPEC.md`, the source, and a list of remaining differences with frame numbers.

**Pitfalls, from @notdwd and our own work**:
- Measure text only after fonts load; instance variable fonts (fontTools) because canvas 2D can't set axes.
- Per-letter effects with a different brand length: time them by normalised letter position u∈[0,1], so the wave still crosses the word in the same frames.
- Canvas engine: call `ctx.reset()` (or save/restore) on pooled layers, or a stray `clip()` leaks into later frames; never name a top-level function the same as a `window.*` export (it recurses).
- Brand-name length shifts centred layouts: keep shared elements where they were and absorb the difference in the word.
- Random particles won't stack, so drive the visible ones from measured tracks.
- Whips in the reference may be crisp.
- Never say "matches" without the ref|ours sheet for that shot.
