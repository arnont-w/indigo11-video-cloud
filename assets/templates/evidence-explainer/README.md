# Style pack: evidence explainer

- Saved: 2569-11-11
- Origin: reverse-engineered
- Source: a reel studied with indigo11-watch (level 3); the source file is not included

Full numbers: SPEC.md. Stills: stills/.

Example: `anim.html` + `timing.js` come from an English test clip about insulin resistance and brain health.
`anim.html` + `timing.js` = the animated page from that test (9 scenes, time-driven pop-ins, bobbing brain, drifting confetti). It is topic-specific: copy it, replace the scene blocks and `timing.js` (scene start times from the voice), then render with `_system/engine/render_frames.mjs`.

## The style in 5 lines
1. Warm cream paper background, drifting confetti, faint diagonal streaks.
2. One idea per scene, named by a coloured pill label at the top; colour = meaning.
3. Flat vector illustration, thick black outlines, one clear metaphor per sentence.
4. Tiny citation under each claim; big keyword; bottom karaoke caption (white, outlined, yellow highlight word).
5. Scenes fade through the bare background, never hard-cut; music bed ducks under the voice, 1 s silence at the end.

## Suits these story frameworks
Myth-check ("true or fake?"), evidence summary, then-vs-now, 3-takeaways, safety-first health explainers. Shape used: hook, verdict teaser, evidence, caveat, better evidence, mechanism, nuance, conclusion, takeaway, safety + CTA.

## How to use
Tell Claude: "make a clip about X in the evidence explainer style". Claude reads style.json and sfx/palette.json, writes the script in the shape above, tags each sentence with its job, plans one visual metaphor per scene, shows 4 stills first (hook, mechanism, payoff, CTA), then builds.

## Reference stills
stills/hook.jpg (1.5 s), stills/evidence.jpg (12.5 s), stills/mechanism.jpg (34.2 s)

## Caveats
Colours and easing are estimates. Fonts not identified (use Prompt/Kanit from assets/fonts). Do not reuse the source's music or voice.
