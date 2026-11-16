TEMPLATES

Looks you want to use again. One folder per template: assets/templates/<name>/

Search order: Claude looks here FIRST (`save_template.py --list`), then free CapCut library items, then makes one (reverse-engineering a video or building it from scratch, then saves it here), and buying a template pack is last, only with your yes.

Two ways to get one:
- You reverse engineer a video (indigo11-watch, level 3): Claude saves the style pack here.
- You have your own: put its folder here, or tell Claude "save this as my template <name>".

Inside one template folder:
- README.md          the look in 5 lines, what it suits, where it came from, the date
- style.json         colours, fonts, motion, transitions, pacing
- sfx/palette.json   which sound effect for which role
- anim.html          optional: the animated page (see _system/engine/animation-starter.html)
- stills/            optional: 3-4 reference pictures

To use one, say: "use my template <name>". To see the names: open assets/templates/INDEX.md, or ask Claude "which templates do I have?" (it runs python _system/engine/save_template.py --list).
Each template shows its origin: "own" (made or added by you) or "reverse-engineered" (taken from a video you studied; its source is listed).
Not here: CapCut editing styles (my-rules/style-<name>.md) and finished videos (projects/results/).
