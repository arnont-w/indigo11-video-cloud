#!/usr/bin/env python3
"""Inject captions into a CapCut project's text track.

Reads:
  - <project>/Timelines/<UUID>/draft_info.json
  - words.json - captions with {timeline_start_ms, timeline_end_ms, duration_ms, text}
                  Optionally each caption may also have a "tokens" field:
                    [{"text": "ชื่อแอนดริว", "char_start": 0, "char_end": 11,
                      "start": 0.62, "end": 1.06}, ...]
                  char_start/char_end index into `text`. token start/end are each
                  token's REAL spoken time in SECONDS (the caption window uses ms via
                  timeline_*_ms; tokens use seconds - see references/post-process-
                  protocol-v2.md unit contract). When start/end are present on ALL
                  tokens, --karaoke highlights each word on its real beat instead of
                  splitting the caption duration evenly. If absent, the script falls
                  back to splitting `text` on whitespace + an even time split.

Modes:
  --plain - 1 text segment per caption, single color (default white). The default.
  --karaoke - split each caption into N sub-captions (N = number of tokens). All show
               the FULL caption text, but each sub-caption highlights one token in turn
               with a gold color (same size by default; pass --highlight-size for the
               grow effect). Result: word-by-word "active highlight" (Submagic style).
  --accumulate - typewriter build-up: sub-caption i shows the caption text UP TO token i
               (prefix grows word by word). Text box is left-aligned with a per-caption
               fixed_width sized to the FULL sentence, so already-shown words never move
               while new words appear to the right. Combine with --karaoke to also
               highlight the newest token (gold + bigger). Spacing of the original text
               is preserved (no Submagic space-strip) - mixed Thai/English stays readable.

Honoring post-process: when tokenizing, prefer the `tokens` field from words.json
(emitted by post-process pass 2 - see references/post-process-protocol.md).
If missing, split by space. This means "ชื่อแอนดริว" stays one token (post-process
merged the proper noun); "55 นาที" is two tokens (post-process kept the space).

Usage:
  python3 add_captions.py --project my-promo --words words.json
  python3 add_captions.py --project my-promo --words words.json --karaoke
  python3 add_captions.py --project my-promo --words words.json --karaoke \
    --highlight-size 18 --base-size 15 --highlight-color "#F4C70F"

  # Re-granulate from raw-elevenlabs.json without re-STT:
  python3 add_captions.py --project my-promo --raw raw-elevenlabs.json --words-per-group 2

  # The buyer already cut the project by hand - caption the POST-CUT timeline:
  python3 add_captions.py --project my-promo --from-timeline
  python3 add_captions.py --project my-promo --from-timeline --audio-track 1

--from-timeline reads the source<->target map off the track that carries the
SPEECH (capcut_to_edl.py) and remaps the transcript onto it (remap_captions.py) -
the kit's one implementation of that math, rule #11c. Trims and reorders come
free from that map. Multi-clip and duplicated-clip timelines work because the
remap runs PER CLIP and PER PLACEMENT: every distinct media file on the track is
transcribed (cached per basename, rule #2) and remapped through its own segments,
then the per-clip results are merged by timeline start. A clip dropped in twice
gets captions at both placements.

It REFUSES rather than guessing or half-finishing:
  * a speed-changed clip - no EDL shape can express a speed ramp;
  * a separate audio track whose layout differs from the video track's - which
    one carries the speech is the buyer's call (pass --audio-track <i>, or
    --audio-track video to use the audio baked into the clips);
  * any clip whose media is missing or cannot be transcribed - captions that
    cover clip A and stop where clip B starts look finished and are not.
"""
from __future__ import annotations
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _capcut_lib import ( # noqa: E402
    CAPCUT_DRAFTS_DIR, MS, SKILL_DIR, KIT_ROOT, deep_copy, emit_err, emit_ok, find_timeline_uuid,
    load_template, mirror_9_places, new_uuid, resolve_project, write_state,
    draft_state_path, load_draft, atomic_write_text, begin_op, ensure_capcut_closed,
    assert_preserved, material_refcount,
)


# --------------------------- track ownership (rule #42) ---------------------
#
# The 2026-09-18 refund case: "add captions" deleted EVERY text track, including
# the titles the buyer had typed by hand. --append-track was the only escape and
# nobody knew to pass it, so the destructive path was the default.
#
# Ownership inverts that. We record the track we wrote (per role) plus a hash of
# its contents AS WE WROTE THEM. On the next run we update that exact track and
# nothing else; a track we do not own is never touched, and our own track that
# the buyer has since hand-edited is refused rather than silently overwritten.
# A track's `name` is a UI hint only - a buyer can rename anything, so a name
# match is never a licence to delete.

def _style_fingerprint(styles) -> str:
    """The style-bearing fields of a text material's `content.styles[]`, ordered.

    A buyer's colour / size / outline / shadow edits on OUR caption track are
    real work, and hashing only text+time silently overwrote them while the
    refusal message claimed to detect "hand-edited". Only fields the kit itself
    writes are read, so a CapCut version that adds a key of its own does not
    invalidate an untouched track.
    """
    if not isinstance(styles, list):
        return ""
    out = []
    for st in styles:
        if not isinstance(st, dict):
            continue
        fill = ((st.get("fill") or {}).get("content") or {}).get("solid") or {}
        strokes = st.get("strokes") or []
        shadows = st.get("shadows") or []

        def _solid(items):
            vals = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                s = ((it.get("content") or {}).get("solid") or {})
                vals.append(",".join(str(v) for v in (
                    s.get("color"), it.get("width"), it.get("alpha"),
                    it.get("distance"), it.get("angle"))))
            return ";".join(vals)

        out.append("|".join([
            str(fill.get("color")),
            str(st.get("range")),
            str(st.get("size")),
            str(st.get("useLetterColor")),
            str((st.get("font") or {}).get("id")),
            _solid(strokes),
            _solid(shadows),
        ]))
    return "~".join(out)


# CapCut snaps every segment to its frame grid when it saves: a caption we wrote as
# 0.340 s came back as 0.3667 s (11 frames) after the buyer merely OPENED the project.
# A hash that includes raw microseconds therefore reported every viewed project as
# "hand-edited" and refused the re-run. Timing is compared with this tolerance instead
# (one frame at 24 fps on each edge, with margin); text and styling stay exact.
TIMING_TOLERANCE_US = 50_000


def owned_timing(track: dict) -> list:
    return [[int((seg.get("target_timerange") or {}).get("start") or 0),
             int((seg.get("target_timerange") or {}).get("duration") or 0)]
            for seg in (track.get("segments") or [])]


def same_as_written(track: dict, draft: dict, rec: dict) -> bool:
    """Is this owned track still what we wrote, allowing for CapCut's frame snap?

    A record from before the tolerant check carries only `content_hash` (timing
    included); it is compared exactly, as it always was."""
    if not rec.get("style_hash") or not isinstance(rec.get("timing_us"), list):
        return track_hash_from_draft(track, draft) == rec.get("content_hash")
    if track_hash_from_draft(track, draft, timing=False) != rec["style_hash"]:
        return False
    now, then = owned_timing(track), rec["timing_us"]
    if len(now) != len(then):
        return False
    return all(abs(a[0] - b[0]) <= TIMING_TOLERANCE_US
               and abs((a[0] + a[1]) - (b[0] + b[1])) <= TIMING_TOLERANCE_US
               for a, b in zip(sorted(now), sorted(map(list, then))))


def owned_hash(track: dict, timing: bool = True) -> str:
    """Fingerprint an owned caption track from the fields WE author.

    Per segment: id, text, target_timerange, the text material's style-bearing
    fields (content.styles[], font_size, text_color, alignment, fixed_width) and
    the segment's own placement (clip transform / scale / rotation / alpha).
    Computed identically on write and on read so it round-trips.

    DELIBERATELY EXCLUDED: anything CapCut normalises on save - render_index,
    visible, volume, the extra_material_refs order, and any key the kit never
    writes. A false "buyer edited this" refusal undoes a correct edit, which is
    exactly as bad as the silent overwrite this hash exists to prevent (rule #41
    on warns).
    """
    import hashlib
    parts = []
    for seg in (track.get("segments") or []):
        tr = seg.get("target_timerange") or {}
        clip = seg.get("clip") or {}
        tf = clip.get("transform") or {}
        sc = clip.get("scale") or {}
        parts.append("|".join([
            str(seg.get("id") or ""),
            str(seg.get("_indigo11_text") or ""),
            str(tr.get("start") or 0) if timing else "",
            str(tr.get("duration") or 0) if timing else "",
            _style_fingerprint(seg.get("_indigo11_styles")),
            str(seg.get("_indigo11_font_size")),
            str(seg.get("_indigo11_text_color")),
            str(seg.get("_indigo11_alignment")),
            str(seg.get("_indigo11_fixed_width")),
            str(tf.get("x")), str(tf.get("y")),
            str(sc.get("x")), str(sc.get("y")),
            str(clip.get("rotation")), str(clip.get("alpha")),
        ]))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def track_hash_from_draft(track: dict, draft: dict, timing: bool = True) -> str:
    """owned_hash() for a track read back off disk.

    The segment carries neither text nor styling - both live in its text material
    - so resolve each segment's material first and stage the same `_indigo11_*` probe
    fields the write path stages, then hash the identical shape.
    """
    texts = {m.get("id"): m for m in
             ((draft.get("materials") or {}).get("texts") or []) if isinstance(m, dict)}
    probe = {"segments": []}
    for seg in (track.get("segments") or []):
        mat = texts.get(seg.get("material_id")) or {}
        try:
            content = json.loads(mat.get("content") or "")
        except (ValueError, TypeError):
            content = {}
        if not isinstance(content, dict):
            content = {}
        s = dict(seg)
        s["_indigo11_text"] = content.get("text", "")
        s["_indigo11_styles"] = content.get("styles")
        s["_indigo11_font_size"] = mat.get("font_size")
        s["_indigo11_text_color"] = mat.get("text_color")
        s["_indigo11_alignment"] = mat.get("alignment")
        s["_indigo11_fixed_width"] = mat.get("fixed_width")
        probe["segments"].append(s)
    return owned_hash(probe, timing)


def _state_ops(project_dir: Path) -> list:
    """state.json's op history, oldest first. A pre-history flat file is one op."""
    kit_root = KIT_ROOT
    state_path = kit_root / "projects" / "_work" / "capcut" / project_dir.name / "state.json"
    if not state_path.exists():
        return []
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    except (ValueError, OSError):
        return []
    ops = state.get("ops")
    if not isinstance(ops, list):
        ops = [state] if isinstance(state, dict) else []
    return [o for o in ops if isinstance(o, dict)]


def read_owned_tracks(project_dir: Path) -> dict:
    """Latest `owned_tracks` across state.json's op history.

    write_state APPENDS (rule #9 / history file), so the newest ownership record
    is the last op that carried one - not necessarily the last op at all, since
    add-sfx or apply-resource run in between and record none.
    """
    owned = {}
    for op in _state_ops(project_dir):
        if isinstance(op.get("owned_tracks"), dict):
            owned.update(op["owned_tracks"])
    return owned


def last_caption_op_without_ownership(project_dir: Path) -> dict | None:
    """The newest add-captions op from BEFORE ownership records existed.

    Returns None when any add-captions op carries `owned_tracks` - ownership is
    then authoritative and there is nothing to adopt.
    """
    found = None
    for op in _state_ops(project_dir):
        if op.get("op") != "add-captions":
            continue
        if isinstance(op.get("owned_tracks"), dict):
            return None
        found = op
    return found


def adopt_pre_ownership_track(draft: dict, prior_op: dict, role: str):
    """Find the ONE text track a pre-2.4.0 run of this kit unambiguously wrote.

    A project captioned before 2.4.0 has an add-captions op but no owned_tracks,
    so a re-run used to APPEND a second caption track and merely warn - the buyer
    ends up with two stacked caption tracks and has to delete one by hand.

    Adoption is allowed only on unmistakable evidence, because adopting the
    buyer's own title track would DELETE their work on the next replace:
      * exactly one candidate text track, and
      * it has no `name` (pre-2.4.0 runs never set one; the buyer's track in
        CapCut's UI does, and a buyer who renamed ours is also excluded - that
        rename is a signal we cannot distinguish from their own track), and
      * its segment count matches that op's recorded `caption_count_out`, and
      * every one of its text materials carries the signature this kit writes
        (`use_effect_default_color: False` plus a `content.styles[]` array).

    Returns (track, reason) on adoption, or (None, reason) when it must not.
    """
    want = prior_op.get("caption_count_out")
    if not isinstance(want, int) or want <= 0:
        return None, "the prior add-captions op recorded no caption_count_out"

    texts = {m.get("id"): m for m in
             ((draft.get("materials") or {}).get("texts") or []) if isinstance(m, dict)}
    text_tracks = [t for t in (draft.get("tracks") or []) if t.get("type") == "text"]
    if not text_tracks:
        return None, "the project has no text track left to adopt"

    candidates = []
    for t in text_tracks:
        if (t.get("name") or ""):
            continue  # named = the buyer's, or ours-but-renamed: not unmistakable
        segs = t.get("segments") or []
        if len(segs) != want:
            continue
        signed = True
        for s in segs:
            mat = texts.get(s.get("material_id")) or {}
            if mat.get("use_effect_default_color") is not False:
                signed = False
                break
            try:
                content = json.loads(mat.get("content") or "")
            except (ValueError, TypeError):
                signed = False
                break
            if not isinstance(content, dict) or not isinstance(content.get("styles"), list):
                signed = False
                break
        if signed:
            candidates.append(t)

    if not candidates:
        return None, (f"no unnamed text track carries this kit's signature with "
                      f"{want} segment(s)")
    if len(candidates) > 1:
        return None, (f"{len(candidates)} text tracks match the prior run equally - "
                      f"adopting either could delete the buyer's own titles")
    return candidates[0], (f"adopted the pre-2.4.0 caption track "
                           f"{candidates[0].get('id')} for role '{role}' "
                           f"({want} segment(s), kit signature present)")


# --- Text-width estimation for --accumulate fixed_width -----------------------
#
# Calibrated against a CapCut-UI-authored sample (project 0611, canvas 1920x1080,
# font_size 15, CapCut system th.ttf):
# * rendered glyph size: font_size 15 -> ~109.2pt => PX_PER_FONTSIZE = 7.281
# canvas px per font_size unit (scales with canvas height / 1080).
# * fixed_width unit: 1 unit = 2.5195 canvas px on a 1920-wide canvas
# (box drawn at fixed_width=527.17 rendered 1328.2 canvas px wide; scales
# with canvas width / 1920).
# Measurement prefers PIL + CapCut's own bundled fonts (exact advance widths);
# falls back to a per-script character heuristic when PIL is unavailable.
PX_PER_FONTSIZE = 7.281
FIXED_WIDTH_UNIT_PX = 2.5195


def _capcut_font_dir() -> Path:
    """Where CapCut keeps its bundled system fonts (th.ttf/en.ttf).

    Used ONLY for PIL advance-width calibration in --accumulate fixed_width mode;
    when the dir/font is absent, _pil_font() returns None and the caller falls
    back to a per-script character heuristic, so a wrong path is never fatal.

    macOS:   /Applications/CapCut.app/Contents/Resources/Font/SystemFont
    Windows: %LOCALAPPDATA%/CapCut/Apps/<version>/Resources/Font/SystemFont
             (version dir varies; pick the newest that has SystemFont/).
    """
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", "")) / "CapCut" / "Apps"
        if base.exists():
            for d in sorted((p for p in base.iterdir() if p.is_dir()), reverse=True):
                cand = d / "Resources" / "Font" / "SystemFont"
                if cand.exists():
                    return cand
        return base  # likely absent -> heuristic fallback (non-fatal)
    return Path("/Applications/CapCut.app/Contents/Resources/Font/SystemFont")


_CAPCUT_FONT_DIR = _capcut_font_dir()

_FONT_CACHE = {}


def _pil_font(name: str, pt: int):
    try:
        from PIL import ImageFont
    except ImportError:
        return None
    path = _CAPCUT_FONT_DIR / name
    if not path.exists():
        return None
    key = (name, pt)
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = ImageFont.truetype(str(path), pt)
    return _FONT_CACHE[key]


def _is_thai(ch: str) -> bool:
    return "฀" <= ch <= "๿"


def measure_text_px(text: str, font_size: float, canvas_h: int = 1080) -> float:
    """Estimated rendered width in canvas px for `text` at CapCut font_size."""
    pt = max(8, int(round(font_size * PX_PER_FONTSIZE * canvas_h / 1080)))
    th = _pil_font("th.ttf", pt)
    en = _pil_font("en.ttf", pt) or th
    if th is not None:
        # split into Thai / non-Thai runs, measure each with the matching font
        width = 0.0
        run, run_thai = "", None
        for ch in text + "\x00": # sentinel flushes last run
            is_t = _is_thai(ch)
            if run and (is_t != run_thai or ch == "\x00"):
                width += (th if run_thai else en).getlength(run)
                run = ""
            if ch != "\x00":
                run, run_thai = run + ch, is_t
        return width
    # Heuristic fallback (no PIL): em-relative average advance per char class.
    em = font_size * PX_PER_FONTSIZE * canvas_h / 1080
    width = 0.0
    for ch in text:
        if "ั" <= ch <= "ฺ" or "็" <= ch <= "๎":
            continue # Thai combining marks: zero advance
        if _is_thai(ch):
            width += 0.56 * em
        elif ch == " ":
            width += 0.27 * em
        elif ch.isupper():
            width += 0.62 * em
        else:
            width += 0.50 * em
    return width


def accumulate_fixed_width(text: str, tokens: list, *, karaoke: bool,
                           base_size: float, hl_size: float,
                           canvas_w: int = 1920, canvas_h: int = 1080) -> float:
    """fixed_width (CapCut units) sized to the widest accumulate frame + margin.

    Widest frame is almost always the last (full text, last token highlighted),
    but a long penultimate token at highlight size can beat it - so check every
    frame. 8% + 0.5em margin guards against PIL-vs-CapCut advance differences;
    too small a box makes CapCut WRAP the line, which is the one failure mode
    that ruins the static build-up effect.
    """
    widest = measure_text_px(text, base_size, canvas_h)
    if karaoke:
        for tok in tokens:
            prefix = text[:tok[2]]
            w = (measure_text_px(prefix, base_size, canvas_h)
                 + measure_text_px(tok[0], hl_size, canvas_h)
                 - measure_text_px(tok[0], base_size, canvas_h))
            widest = max(widest, w)
    margin = widest * 0.08 + 0.5 * base_size * PX_PER_FONTSIZE * canvas_h / 1080
    px = min(widest + margin, canvas_w * 0.92)
    return px / (FIXED_WIDTH_UNIT_PX * canvas_w / 1920)


def hex_to_rgb(hex_color: str) -> list:
    """'#F4C70F' → [0.957, 0.78, 0.06] (0–1 floats, CapCut convention)."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]


def tokenize_caption(cap: dict) -> list:
    """Return list of (text, char_start, char_end, start_ms, end_ms) for a caption.

    Honor `cap["tokens"]` from post-process if present. Otherwise split `text` by space.

    The 4th/5th tuple fields are each token's REAL spoken start/end in MILLISECONDS,
    taken from the token timing that word_segment.group_words / post-process pass 2
    preserves (which came from ElevenLabs char-level timestamps). The loader in
    main() has already normalized any seconds-valued source to ms, so this function
    does NOT guess units - token times are ms or they are None.

    They are None when the source has no per-token timing (e.g. a hand-written
    captions file or a space-split fallback). In that case the karaoke loop falls
    back to evenly splitting the caption duration (old behavior). When timing IS
    present, the karaoke highlight follows the audio instead of a flat average - 
    which is what keeps fast and slow words highlighting on the right beat.
    """
    text = cap["text"]

    def _num(v):
        return float(v) if v is not None else None

    if "tokens" in cap and isinstance(cap["tokens"], list) and cap["tokens"]:
        out = []
        for t in cap["tokens"]:
            if "char_start" in t and "char_end" in t:
                out.append((
                    t.get("text", text[t["char_start"]:t["char_end"]]),
                    t["char_start"], t["char_end"],
                    _num(t.get("start")), _num(t.get("end")),
                ))
        if out:
            return out
    # Fallback: split by space, preserve char offsets, no per-token timing
    out = []
    pos = 0
    for chunk in text.split(" "):
        if chunk:
            out.append((chunk, pos, pos + len(chunk), None, None))
        pos += len(chunk) + 1
    if not out:
        out = [(text, 0, len(text), None, None)]
    return out


# --- caption legibility: stroke (border) + drop shadow (exact CapCut/pyCapCut shape) ---
def _stroke(color: list, width: float) -> list:
    """A text outline. width is normalized (~0.03-0.08 looks good)."""
    return [{"content": {"solid": {"alpha": 1.0, "color": color}}, "width": width}]


def _shadow(color: list, alpha: float, distance: float, angle: float) -> list:
    return [{"diffuse": 0.025, "alpha": alpha, "distance": distance,
             "content": {"solid": {"color": color}}, "angle": angle}]


def _apply_legibility(style: dict, border, shadow) -> dict:
    """Inject strokes/shadows into a style dict. border/shadow are dicts or None."""
    if border:
        style["strokes"] = _stroke(border["color"], border["width"])
    if shadow:
        style["shadows"] = _shadow(shadow["color"], shadow["alpha"], shadow["distance"], shadow["angle"])
    return style


def build_styles_plain(text: str, color: list, size: float, border=None, shadow=None) -> list:
    return [_apply_legibility({
        "fill": {"content": {"solid": {"color": color}, "render_type": "solid"}},
        "range": [0, len(text)],
        "size": size,
        "font": {"path": "", "id": ""},
    }, border, shadow)]


def build_styles_karaoke(text: str, tokens: list, hl_idx: int,
                        base_color: list, hl_color: list,
                        base_size: float, hl_size: float,
                        border=None, shadow=None) -> list:
    """Multi-range styles: each token gets its own range, only hl_idx in highlight color/size.
    Gap chars (whitespace) fill with base style.
    """
    styles = []
    cursor = 0
    n = len(text)
    for i, tok in enumerate(tokens):
        s, e = tok[1], tok[2] # char_start, char_end (tok = text, cs, ce, [start_ms, end_ms])
        if s > cursor:
            styles.append({
                "fill": {"content": {"solid": {"color": base_color}, "render_type": "solid"}},
                "range": [cursor, s], "size": base_size,
                "font": {"path": "", "id": ""},
            })
        color = hl_color if i == hl_idx else base_color
        size = hl_size if i == hl_idx else base_size
        st = {
            "fill": {"content": {"solid": {"color": color}, "render_type": "solid"}},
            "range": [s, e], "size": size,
            "font": {"path": "", "id": ""},
        }
        if i == hl_idx:
            # This is the key CapCut needs to render multi-color text correctly.
            # Discovered by harvesting a CapCut-UI-authored sample.
            st["useLetterColor"] = True
        styles.append(st)
        cursor = e
    if cursor < n:
        styles.append({
            "fill": {"content": {"solid": {"color": base_color}, "render_type": "solid"}},
            "range": [cursor, n], "size": base_size,
            "font": {"path": "", "id": ""},
        })
    return [_apply_legibility(s, border, shadow) for s in styles]


# --------------------------------------------------------------------------
# --from-timeline: caption a project the buyer already cut by hand.
#
# The cuts are already in the CapCut timeline, so the words.json we transcribe
# from the SOURCE media is in the wrong coordinate system - every caption would
# land drifted right by however much was cut before it. The deterministic fix is
# the kit's existing pair (rule #11c): capcut_to_edl.py reads the project's
# source<->target map off the track that carries the speech, remap_captions.py
# translates the timestamps. Neither is reimplemented here - both are invoked as
# subprocesses so there stays exactly ONE implementation of that math in the kit.
# What lives here is only the ORCHESTRATION the per-clip case needs: which track,
# which clips, which placement batch, and merging the per-clip results.
# --------------------------------------------------------------------------

def _clean_cut_script(name: str) -> Path:
    """Resolve an indigo11-edit script without hardcoding an absolute path.

    Skills live in <starter>/skills/<skill>/ with links in .claude/.agents/.agent
    skills/ (made by _system/setup/common/link_skills.py). We are inside
    indigo11-capcut/scripts/, so the sibling skill is two levels up either way.
    """
    here = Path(__file__).resolve().parent.parent.parent
    for base in (here, KIT_ROOT / ".claude" / "skills", Path.cwd() / "skills"):
        p = base / "indigo11-edit" / "scripts" / name
        if p.exists():
            return p
    raise FileNotFoundError(
        f"หา {name} ของ indigo11-edit ไม่เจอ (could not locate indigo11-edit/scripts/{name})")


def _timeline_tracks(draft: dict, kind: str) -> list:
    return [t for t in (draft.get("tracks") or []) if t.get("type") == kind]


def _pick_track(tracks: list, selector, kind: str):
    """Select a track by integer index or by id. Returns (index, track).

    A selector that matches nothing is a hard error naming what IS available -
    silently falling back to track 0 would caption against the wrong timeline.
    """
    if selector is None:
        return 0, tracks[0]
    sel = str(selector)
    if sel.isdigit():
        i = int(sel)
        if i >= len(tracks):
            raise ValueError(
                f"{kind} track {i} ไม่มี - มีแค่ {len(tracks)} track "
                f"({kind} track {i} out of range; project has {len(tracks)})")
        return i, tracks[i]
    for i, t in enumerate(tracks):
        if t.get("id") == sel:
            return i, t
    raise ValueError(
        f"ไม่พบ {kind} track id {sel} (no {kind} track with that id). "
        f"available: {[t.get('id') for t in tracks]}")


def _seg_ranges_us(track: dict) -> list:
    out = []
    for s in (track.get("segments") or []):
        tr = s.get("target_timerange") or {}
        st = int(tr.get("start", 0) or 0)
        out.append((st, st + int(tr.get("duration", 0) or 0)))
    return sorted(out)


def _check_speed(track: dict) -> list:
    """Every segment whose speed != 1.0, as buyer-readable ranges.

    remap_captions translates timestamps by a constant per-segment offset; a speed
    ramp would need each token's time SCALED, which the EDL shape cannot express
    (rule #11c says so explicitly). So this stops the op rather than silently
    dropping the clip - a caption track that is right for 40s and wrong after is
    worse than a refusal, because playback is the only place it shows.
    """
    bad = []
    for i, s in enumerate(track.get("segments") or []):
        sp = s.get("speed", 1.0)
        sp = 1.0 if sp is None else float(sp)
        if abs(sp - 1.0) > 0.001:
            tr = s.get("target_timerange") or {}
            st = int(tr.get("start", 0) or 0)
            bad.append({
                "segment_index": i,
                "speed": sp,
                "timeline_start_ms": st // 1000,
                "timeline_end_ms": (st + int(tr.get("duration", 0) or 0)) // 1000,
            })
    return bad


def _speed_segments(draft: dict, track: dict) -> list:
    """_check_speed() plus, per segment, the media path and SOURCE range it plays.

    Whether a slowed segment blocks captions depends on what is said in it: a slow-mo
    cutaway the speaker is silent over has nothing to remap."""
    mats = {}
    for items in (draft.get("materials") or {}).values():
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and it.get("id"):
                    mats[it["id"]] = it
    segs = track.get("segments") or []
    out = []
    for b in _check_speed(track):
        s = segs[b["segment_index"]]
        m = mats.get(s.get("material_id")) or {}
        src = s.get("source_timerange") or {}
        st = int(src.get("start", 0) or 0)
        out.append(dict(b, path=m.get("path") or m.get("file_Path") or "",
                        source_start_s=st / 1_000_000,
                        source_end_s=(st + int(src.get("duration", 0) or 0)) / 1_000_000))
    return out


def _speech_in_range(raw_path: Path, start_s: float, end_s: float) -> str:
    """What the cached transcript holds between two source times ("" = nothing said)."""
    try:
        raw = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "?"                      # cannot tell -> treat as speech, never as silence
    units = raw.get("words_raw") or raw.get("words") or []
    return "".join(u.get("text") or "" for u in units
                   if u.get("type", "word") == "word"
                   and float(u.get("start", 0)) < end_s and float(u.get("end", 0)) > start_s).strip()


def _source_media_for_track(draft: dict, track: dict) -> list:
    """Absolute paths of the media the track's segments reference, in timeline order.

    Distinct paths, first-appearance order. A segment whose material carries no
    path contributes nothing here - `_unresolved_segments` is what reports those,
    because a timeline we can only partly resolve must stop, not caption half.
    """
    mats = {}
    for cat, items in (draft.get("materials") or {}).items():
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and it.get("id"):
                    mats[it["id"]] = it
    seen, out = set(), []
    for s in sorted((track.get("segments") or []),
                    key=lambda s: int((s.get("target_timerange") or {}).get("start", 0) or 0)):
        m = mats.get(s.get("material_id"))
        if not m:
            continue
        p = m.get("path") or m.get("file_Path") or ""
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _unresolved_segments(draft: dict, track: dict) -> list:
    """Segment ids on the track whose material has no media path."""
    mats = {}
    for items in (draft.get("materials") or {}).values():
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and it.get("id"):
                    mats[it["id"]] = it
    out = []
    for s in (track.get("segments") or []):
        m = mats.get(s.get("material_id")) or {}
        if not (m.get("path") or m.get("file_Path")):
            out.append(s.get("id") or "<no id>")
    return out


def _merge_caption_files(paths: list) -> dict:
    """Concatenate several remapped caption files into one, sorted by time.

    Each input covers ONE clip (or one placement of one clip) already in
    cut-timeline coordinates, so the merge is a sort - no arithmetic. Overlaps
    between two clips' captions are left as they are: they would mean the buyer
    stacked two speaking clips at the same moment, which is their edit, and
    silently deleting one clip's words would be the worse answer.

    remap_captions.py preserves its INPUT shape, and which shape that is depends
    on the cached words.json for that media - `{groups}` (transcribe/word_segment
    output, times in SECONDS) or `{captions}` (CapCut shape, timeline_*_ms). Both
    are emitted by the kit, so both are merged here; main() then loads whichever
    comes back through its existing shape detection. Assuming one shape produced
    an empty merge - the silent-empty failure mode rule #39 is about.
    """
    groups, caps, sizes = [], [], set()
    for p in paths:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        groups.extend(d.get("groups") or [])
        caps.extend(d.get("captions") or [])
        sizes.add(d.get("words_per_group"))
    # carried so qc_edit judges N-word captions by their own duration floor
    size = {"words_per_group": sizes.pop()} if len(sizes) == 1 else {}
    if groups and caps:
        raise ValueError(
            "ไฟล์ซับที่ remap มาคนละ shape กัน รวมไม่ได้ (per-clip remaps returned mixed "
            "shapes - {groups} and {captions} - which cannot be merged; re-run so every "
            "clip's cached words.json has the same shape)")
    if caps:
        caps.sort(key=lambda c: (c.get("timeline_start_ms", 0),
                                 c.get("timeline_end_ms", 0)))
        return {"captions": caps, "caption_count": len(caps), "remapped": True,
                "merged_from": [str(p) for p in paths], **size}
    groups.sort(key=lambda g: (g.get("start", 0), g.get("end", 0)))
    return {"groups": groups, "group_count": len(groups),
            "word_count": sum(len(g.get("tokens") or []) for g in groups),
            "remapped": True, "merged_from": [str(p) for p in paths], **size}


def _held_end(end_s: float, next_start_s: float) -> float:
    """Gap-close (rule #11b) with the long-pause cap - word_segment.held_end, mirrored
    here because skills share files, not imports."""
    gap = next_start_s - end_s
    if gap <= 0:
        return end_s
    if gap <= 1.2:
        return next_start_s
    return end_s + max(0.0, min(1.2, gap - 1.2 - 0.05))


def _stale_words(path: Path, words_per_group: int) -> str:
    """Why a cached words.json should be re-cut from raw, or "" to use it as it is.

    Kept as is: anything an agent or a person wrote (the pass-2 `captions[]` shape, a
    file carrying post-process markers). Re-cut: script output for a different N, or
    from the segmenter that predates timing-first cutting (no `segmenter` field)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict) or "groups" not in data or data.get("post_processed"):
        return ""
    if data.get("segmenter") not in ("timing-first", "agent-breaks"):
        return "cached file predates timing-first segmentation"
    if int(data.get("words_per_group") or 0) != int(words_per_group):
        return f"cached file is {data.get('words_per_group')}-word"
    return ""


def captions_from_timeline(project_dir: Path, draft: dict, *, video_track=None,
                           audio_track=None, words_per_group=2, no_gap_close=False,
                           kit_root: Path = None) -> dict:
    """Transcribe the timeline's source media and remap it onto the CUT timeline.

    Returns {"words_path": Path, "edl_paths": [Path], "media": [str], "notes": [...]}.
    Raises ValueError with a bilingual message on every refusal (speed ramp,
    ambiguous audio track, no media, a clip that cannot be transcribed).

    EVERY distinct media file on the chosen track is transcribed and remapped
    through ITS OWN segments. Reading one flat EDL for the whole track and
    captioning only media[0] was the silent bug: clip A's words got stretched over
    clip B's placements, and capcut_to_edl's duplicate-source guard then dropped
    clip B's ranges as if they were a duplicate of clip A's. Per-clip EDLs make a
    range from clip B unmistakable, and per-PLACEMENT batches mean a clip the
    buyer dropped in twice gets captions at both spots.
    """
    import subprocess
    notes = []
    vtracks = _timeline_tracks(draft, "video")
    if not vtracks:
        raise ValueError("โปรเจกต์นี้ไม่มี video track (project has no video track)")
    v_idx, vtrack = _pick_track(vtracks, video_track, "video")

    def _refuse_speed(t, label):
        """A speed-changed segment cannot be remapped (rule #11c) - but that only
        matters when something is SAID in it. An 81 s edit with one silent 0.5x
        cutaway used to get no captions at all. So: look in the clip's cached
        transcript; refuse when there is speech in the slowed range, or when there
        is no transcript yet to look in (unknown is never treated as silence)."""
        cache = KIT_ROOT / "projects" / "_work" / "transcribe"
        bad = []
        for sg in _speed_segments(draft, t):
            rng = f"{sg['timeline_start_ms']/1000:.2f}-{sg['timeline_end_ms']/1000:.2f}s"
            raw_p = cache / Path(sg["path"]).stem / "raw-elevenlabs.json"
            said = _speech_in_range(raw_p, sg["source_start_s"], sg["source_end_s"])
            if said == "?":
                bad.append(f"{rng} (speed {sg['speed']}x; {Path(sg['path']).name} is not transcribed "
                           f"yet, so whether anything is said there is unknown)")
            elif said:
                bad.append(f"{rng} (speed {sg['speed']}x, says {said[:40]!r})")
            else:
                notes.append(f"speed-changed segment {rng} ({sg['speed']}x) holds no speech - "
                             f"skipped, nothing to caption there")
        if not bad:
            return
        raise ValueError(
            "มีคลิปที่เปลี่ยนความเร็ว - remap ซับให้ตรงไม่ได้ ต้องเอาความเร็วออกก่อน "
            f"(speed-changed clips on {label} cannot be remapped; remove the speed "
            f"change first): {'; '.join(bad)}. ถ้าช่วงนั้นไม่มีคำพูด ให้ถอดเสียงคลิปนั้นก่อนแล้วรันใหม่ "
            "(if nothing is said in that range, transcribe the clip first - transcribe.py <clip> "
            "--save-all projects/_work/transcribe/<basename> - and re-run: a silent slowed segment is skipped)")

    # A speed ramp is unanswerable whichever track carries the speech, so it is
    # refused BEFORE the which-track question - otherwise a project with both a
    # speed ramp and a separate audio track asks the buyer to pick a track and
    # only then tells them the answer could never have worked.
    _refuse_speed(vtrack, f"video track {v_idx}")

    # Which track carries the SPEECH decides which track the EDL is built from.
    # Building it off the video track while transcribing the audio track's media
    # (the pre-fix behaviour) puts captions on the picture's cut points and the
    # words on the audio's - wrong timing, and nothing raises.
    atracks = _timeline_tracks(draft, "audio")
    track_type, t_idx, track = "video", v_idx, vtrack
    if audio_track is not None:
        # "video" (or the video track's own id) = "use the audio baked into the
        # clips, ignore the separate track". Without this a project whose separate
        # track is BGM had no way through: the refusal listed the video track as a
        # candidate but _pick_track could only ever select an audio one.
        sel = str(audio_track)
        if sel == "video" or sel == str(vtrack.get("id")):
            notes.append(f"--audio-track video: using the video track {v_idx}'s own clip audio; "
                         f"the separate audio track(s) are ignored")
        else:
            if not atracks:
                raise ValueError(
                    "ไม่มี audio track ในโปรเจกต์ - ถ้าจะใช้เสียงที่ติดมากับคลิป ใส่ --audio-track video "
                    "(project has no separate audio track; pass --audio-track video to use the "
                    "clips' own audio)")
            a_idx, atrack = _pick_track(atracks, audio_track, "audio")
            track_type, t_idx, track = "audio", a_idx, atrack
            notes.append(f"audio from audio track {a_idx} (explicit --audio-track); "
                         f"the EDL is built from THAT track's cuts")
    else:
        # An EMPTY audio track is not a fork in the road - it carries no speech, so
        # it cannot be the answer to "which track has the words". Treating it as a
        # candidate made every project with a leftover empty audio track refuse
        # with an option that could only ever produce zero captions.
        differing = [i for i, t in enumerate(atracks)
                     if (t.get("segments") or [])
                     and _seg_ranges_us(t) != _seg_ranges_us(vtrack)]
        if differing:
            # Only selectable options are offered. The video track is reachable as
            # the literal `video`, each differing audio track by its index.
            cand_v = (f"--audio-track video → ใช้เสียงที่ติดมากับคลิปบน video track {v_idx} "
                      f"(id {vtrack.get('id')}, {len(vtrack.get('segments') or [])} segment(s)) "
                      f"- use the clips' own audio")
            cand_a = "; ".join(
                f"--audio-track {i} → audio track {i} (id {atracks[i].get('id')}, "
                f"{len(atracks[i].get('segments') or [])} segment(s))" for i in differing)
            raise ValueError(
                "มี audio track แยกที่ไม่ตรงกับ video track - บอกก่อนว่าจะถอดเสียงจากอันไหน "
                "ด้วย --audio-track (a separate audio track does not line up with the video "
                "track; say which one carries the speech with --audio-track). "
                f"ตัวเลือก (candidates): [{cand_v}] | [{cand_a}]")
        notes.append(f"audio from the video track {v_idx}'s own clips (default)")

    # And again on the track the EDL actually comes from: an AUDIO track's own
    # speed ramp is invisible to the video-track check above, and remapping
    # through it would put every caption progressively out of sync.
    if track is not vtrack:
        _refuse_speed(track, f"{track_type} track {t_idx}")
    speed_segs = _speed_segments(draft, track)

    unresolved = _unresolved_segments(draft, track)
    if unresolved:
        raise ValueError(
            f"มี segment ที่หาไฟล์สื่อไม่ได้บน {track_type} track {t_idx} - ถอดเสียงไม่ครบ timeline "
            f"จึงไม่ใส่ซับให้ (segments on {track_type} track {t_idx} reference no media file, so "
            f"the timeline cannot be fully transcribed): {', '.join(unresolved)}")

    media = _source_media_for_track(draft, track)
    if not media:
        raise ValueError("ไม่พบไฟล์สื่อที่ timeline อ้างถึง (no media files referenced by the timeline)")

    missing = [m for m in media if not Path(m).exists()]
    if missing:
        raise ValueError(
            "ไฟล์สื่อหาย - ถอดเสียงไม่ได้ จึงไม่ใส่ซับให้ (media files referenced by the timeline "
            f"are missing, so no captions were written): {', '.join(missing)}")

    if kit_root is None:
        kit_root = KIT_ROOT
    edl_script = _clean_cut_script("capcut_to_edl.py")
    remap_script = _clean_cut_script("remap_captions.py")
    work_root = Path(kit_root) / "projects" / "_work" / "capcut" / project_dir.name / "from-timeline"
    work_root.mkdir(parents=True, exist_ok=True)

    edl_paths, remapped_paths, failures = [], [], []
    if len(media) > 1:
        notes.append(f"timeline references {len(media)} media files; "
                     f"every one is transcribed and remapped through its own segments")

    for m in media:
        basename = Path(m).stem
        cache_dir = Path(kit_root) / "projects" / "_work" / "transcribe" / basename
        raw_path = cache_dir / "raw-elevenlabs.json"
        words_src = cache_dir / "words.json"

        # Cache-once (rule #2), PER MEDIA: re-segmenting a cached raw response costs
        # nothing, re-calling STT costs the buyer money AND throws away the
        # post-process fixes already applied to that media.
        if words_src.exists():
            notes.append(f"reused cached STT for {basename} (no new API call)")
            # The cache is the TRANSCRIPT, not this caption size. A words.json cut for
            # another N - or by the earlier segmenter that counted tokens across pauses
            # - is re-cut from the raw response (free), into the work dir so an agent's
            # pass-2 file in the cache is never overwritten.
            stale = _stale_words(words_src, words_per_group)
            if stale and raw_path.exists():
                seg = Path(kit_root) / ".claude" / "skills" / "indigo11-transcribe" / "scripts" / "word_segment.py"
                if not seg.exists():
                    seg = Path(__file__).resolve().parent.parent.parent / "indigo11-transcribe" / "scripts" / "word_segment.py"
                recut = work_root / f"words-{basename}-n{words_per_group}.json"
                cmd = [sys.executable, str(seg), str(raw_path), "-o", str(recut),
                       "--words-per-group", str(words_per_group)]
                if no_gap_close:
                    cmd.append("--no-gap-close")
                r = subprocess.run(cmd, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace")
                if r.returncode == 0 and recut.exists():
                    words_src = recut
                    notes.append(f"re-cut {basename} to {words_per_group} words from the cached raw ({stale})")
                else:
                    notes.append(f"could not re-cut {basename} ({stale}); using the cached words.json as is")
        else:
            tr = Path(kit_root) / ".claude" / "skills" / "indigo11-transcribe" / "scripts" / "transcribe.py"
            if not tr.exists():
                tr = Path(__file__).resolve().parent.parent.parent / "indigo11-transcribe" / "scripts" / "transcribe.py"
            if not tr.exists():
                raise ValueError("หา transcribe.py ไม่เจอ (could not locate indigo11-transcribe/scripts/transcribe.py)")
            cache_dir.mkdir(parents=True, exist_ok=True)
            cmd = [sys.executable, str(tr), m, "--save-all", str(cache_dir),
                   "--words-per-group", str(words_per_group)]
            if no_gap_close:
                cmd.append("--no-gap-close")
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            if r.returncode != 0 or not words_src.exists():
                failures.append(f"{Path(m).name}: "
                                f"{(r.stderr or r.stdout or 'transcribe failed').strip()[-300:]}")
                continue
            notes.append(f"transcribed {basename} (cached at {cache_dir})")

        if raw_path.exists():
            notes.append(f"raw STT kept at {raw_path.name} for re-segmentation (rule #2)")


        # One EDL per PLACEMENT batch of this clip. A batch holds placements whose
        # source ranges do not overlap, which is what remap_captions requires; a
        # clip placed twice from the same source range therefore spans two batches
        # and gets captions at BOTH placements instead of one being dropped.
        r = subprocess.run(
            [sys.executable, str(edl_script), "--project", str(project_dir),
             "--track-type", track_type, "--track-index", str(t_idx),
             "--clip", m, "--list-clips"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        n_batches = 1
        try:
            listing = json.loads((r.stdout or "").strip().splitlines()[-1])
            for c in listing.get("clips") or []:
                if c.get("path") == m:
                    n_batches = max(1, int(c.get("placement_batches") or 1))
        except (ValueError, IndexError, TypeError):
            pass  # a listing we cannot parse means "assume one placement", never a crash

        for b in range(n_batches):
            tag = f"{basename}-{track_type}{t_idx}-p{b}"
            edl_path = work_root / f"edl-{tag}.json"
            r = subprocess.run(
                [sys.executable, str(edl_script), "--project", str(project_dir),
                 "--track-type", track_type, "--track-index", str(t_idx),
                 "--clip", m, "--placement", str(b), "--out", str(edl_path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0:
                failures.append(f"{Path(m).name} placement {b}: EDL build failed: "
                                f"{(r.stderr or r.stdout or '').strip()[-300:]}")
                continue
            if speed_segs:
                # Keep only 1.0x segments in the remap table: a slowed segment's source
                # and target lengths differ, which a constant offset cannot express.
                edl_doc = json.loads(edl_path.read_text(encoding="utf-8"))
                kept = [g for g in edl_doc.get("segments", [])
                        if abs((g["source_end_ms"] - g["source_start_ms"])
                               - (g["target_end_ms"] - g["target_start_ms"])) <= 2]
                if len(kept) != len(edl_doc.get("segments", [])):
                    edl_doc["segments"] = kept
                    edl_path.write_text(json.dumps(edl_doc, ensure_ascii=False, indent=2), encoding="utf-8")
            out_path = work_root / f"words-{tag}.json"
            r = subprocess.run(
                [sys.executable, str(remap_script), "--edl", str(edl_path),
                 "--in", str(words_src), "--out", str(out_path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            if not out_path.exists():
                failures.append(f"{Path(m).name} placement {b}: remap failed: "
                                f"{(r.stderr or r.stdout or '').strip()[-300:]}")
                continue
            edl_paths.append(edl_path)
            remapped_paths.append(out_path)
            notes.append(f"remapped {basename} placement {b} via {edl_path.name}")

    # A partial result is the failure this stops: captions that cover clip A and
    # silently stop where clip B begins look like a finished job on the timeline
    # and are only discovered on playback.
    if failures:
        raise ValueError(
            "ถอดเสียง/remap ไฟล์เหล่านี้ไม่สำเร็จ จึงไม่ใส่ซับให้เลย (ซับที่ครอบแค่บางคลิปดูเหมือนเสร็จแต่ผิด) "
            "(these files could not be transcribed/remapped, so NO captions were written - "
            "captions covering only part of the timeline look finished and are not): "
            + " | ".join(failures))
    if not remapped_paths:
        raise ValueError("remap ไม่ได้ผลลัพธ์เลย (remap produced no captions)")

    if len(remapped_paths) == 1:
        words_out = remapped_paths[0]
    else:
        words_out = work_root / f"words-merged-{track_type}{t_idx}.json"
        words_out.write_text(
            json.dumps(_merge_caption_files(remapped_paths),
                       ensure_ascii=False, indent=2), encoding="utf-8")
        notes.append(f"merged {len(remapped_paths)} per-clip caption files into {words_out.name}")

    return {"words_path": words_out, "edl_paths": edl_paths,
            "edl_path": edl_paths[0] if edl_paths else None,
            "media": media, "track_type": track_type, "track_index": t_idx,
            "notes": notes}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--words", help="words.json (ready-to-inject captions)")
    ap.add_argument("--raw", help="raw-elevenlabs.json (re-segment from raw)")
    ap.add_argument("--words-per-group", type=int, default=2, help="Used with --raw")
    ap.add_argument("--karaoke", action="store_true",
                    help="Per-token highlight (Submagic / karaoke style). In karaoke mode "
                         "we strip whitespace from text by default (compact Submagic look). "
                         "Pass --keep-spaces to preserve the spaces in tokens.")
    ap.add_argument("--keep-spaces", action="store_true",
                    help="Karaoke only: keep whitespace between tokens in the display text "
                         "(default is to merge tokens with no separator).")
    ap.add_argument("--accumulate", action="store_true",
                    help="Typewriter build-up: each sub-caption shows the sentence prefix "
                         "up to the current token. Left-aligned in a per-caption fixed-"
                         "width box so shown words never move. Add --karaoke to also "
                         "highlight the newest token. Original spacing is preserved.")
    ap.add_argument("--horizontal-pct", type=float, default=50.0,
                    help="Accumulate only: horizontal position of the text BOX center. "
                         "0=left edge, 50=center (default), 100=right edge.")
    ap.add_argument("--base-size", type=float, default=15.0, help="Non-highlight font size")
    ap.add_argument("--highlight-size", type=float, default=None,
                    help="Active-token font size (karaoke only). Default: same as "
                         "--base-size - highlight is color-only, no size bump. Pass a "
                         "larger value for the old Submagic grow effect.")
    ap.add_argument("--color", default="#FFFFFF", help="Base text color (hex)")
    ap.add_argument("--highlight-color", default="#F4C70F",
                    help="Active-token color (karaoke only). Default = CapCut UI gold.")
    # legibility: outline (border) + drop shadow - ON by default (pro caption look)
    ap.add_argument("--border-width", type=float, default=0.06,
                    help="Caption outline width (0 = no border; 0.04-0.08 typical). Default 0.06.")
    ap.add_argument("--border-color", default="#000000", help="Caption outline color (hex)")
    ap.add_argument("--no-shadow", action="store_true", help="Disable the drop shadow")
    ap.add_argument("--shadow-alpha", type=float, default=0.8, help="Drop-shadow opacity 0..1")
    ap.add_argument("--vertical-pct", type=float, default=78.0,
                    help="Caption vertical position. 0=top, 50=center, 100=bottom. "
                         "Default 78 (TikTok lower-third safe zone). Auto-flipped to top "
                         "(22) for 16:9 to avoid covering speaker's mouth.")
    ap.add_argument("--append-track", action="store_true",
                    help="Force a BRAND-NEW text track even when this role already owns one. "
                         "Rare - the default already updates our own track in place and never "
                         "touches the buyer's. Use for a second, parallel caption pass.")
    ap.add_argument("--role", default="captions-th",
                    help="Ownership role for the track this run writes (captions-th, "
                         "captions-en, ...). A bilingual pass uses a different role per "
                         "language so each one updates its own track. Default captions-th.")
    ap.add_argument("--adopt", metavar="TRACK_ID",
                    help="Take over a pre-2.4.0 caption track by id and replace it. "
                         "Only after the buyer confirms that track is the kit's old "
                         "captions and not a title they typed.")
    ap.add_argument("--overwrite-owned", action="store_true",
                    help="Replace our own caption track even though it has been hand-edited "
                         "since we wrote it. The buyer's changes to that track are lost.")
    ap.add_argument("--force-kill", action="store_true",
                    help="Kill a running CapCut instead of refusing. Unsaved work is lost.")
    ap.add_argument("--no-gap-close", action="store_true",
                    help="Don't auto-extend each caption to abut the next. Default OFF "
                         "(captions are gap-closed) so the line never blinks black "
                         "between phrases - the current text holds across short silences "
                         "until the next caption begins. Pass this flag to keep ElevenLabs' "
                         "raw gaps and trim manually in CapCut.")
    ap.add_argument("--from-timeline", action="store_true",
                    help="The buyer already cut this project by hand: transcribe the media "
                         "the timeline references, then remap the captions onto the POST-CUT "
                         "timeline (no re-transcribe, no re-cut). Reads the source<->target "
                         "map off the video track - handles trims, reorders and duplicated "
                         "clips. Refuses on speed-changed clips, which cannot be remapped.")
    ap.add_argument("--video-track",
                    help="--from-timeline: which video track carries the cut (index or track id). "
                         "Default 0 (the main A-roll).")
    ap.add_argument("--audio-track",
                    help="--from-timeline: which track carries the speech - an audio track "
                         "index/id, or the literal 'video' (or the video track's id) to use "
                         "the audio baked into the clips and ignore the separate track (the "
                         "way through when that track is BGM). The remap EDL is built from "
                         "whichever track is chosen. Default = the video track's own audio. "
                         "Required when a separate audio track's layout differs.")
    ap.add_argument("--drafts-dir", default=str(CAPCUT_DRAFTS_DIR))
    args = ap.parse_args()

    if not args.words and not args.raw and not args.from_timeline:
        emit_err("Pass --words, --raw or --from-timeline")

    project_dir = resolve_project(args.project, args.drafts_dir)
    if not project_dir.exists():
        emit_err(f"Project not found: {project_dir}")

    # --from-timeline resolves to a words.json in CUT-timeline coordinates; from
    # there it is an ordinary --words run, so every styling/karaoke/bilingual flag
    # behaves identically. Done BEFORE kill_capcut: transcribe + remap only read.
    from_timeline_info = None
    if args.from_timeline:
        if args.words or args.raw:
            emit_err("--from-timeline ใช้ร่วมกับ --words/--raw ไม่ได้ "
                     "(--from-timeline derives its own captions; drop --words/--raw)")
        _draft_ro, _, _ = load_draft(project_dir)
        try:
            from_timeline_info = captions_from_timeline(
                project_dir, _draft_ro,
                video_track=args.video_track, audio_track=args.audio_track,
                words_per_group=args.words_per_group, no_gap_close=args.no_gap_close)
        except ValueError as ex:
            emit_err(str(ex))
        except Exception as ex:
            emit_err(f"--from-timeline ล้มเหลว (from-timeline failed): {ex}")
        args.words = str(from_timeline_info["words_path"])

    # Load captions.
    #
    # Two source shapes are accepted, and BOTH may carry per-token timing:
    # A) CapCut shape: {"captions": [{timeline_start_ms, timeline_end_ms, text,
    # tokens:[{text,char_start,char_end,start,end}]}]}
    # Caption window = ms (the *_ms field names). token.start/end = SECONDS.
    # B) transcribe/word_segment shape: {"groups": [{start, end, text,
    # tokens:[{text,char_start,char_end,start,end}]}]}
    # Group start/end = SECONDS. token.start/end = SECONDS.
    #
    # UNIT CONTRACT (one rule, no guessing): every token.start/end in any words.json
    # is in SECONDS - regardless of shape. The only ms fields are the explicitly-named
    # timeline_*_ms / duration_ms. We convert token seconds -> ms exactly once here,
    # for every shape, so the karaoke path always sees ms. See post-process-protocol.md.
    # UNIT CONTRACT (single source of truth):
    # * Caption window: timeline_start_ms / timeline_end_ms / duration_ms - MILLISECONDS
    # (the field name says ms; that never changes).
    # * Token timing: token.start / token.end - SECONDS, everywhere a words.json
    # is written (transcribe, word_segment, post-process pass 2 all emit seconds).
    # We convert token seconds -> ms exactly ONCE here, for EVERY source shape, so the
    # karaoke path always sees ms and never guesses units. Tokens missing start/end
    # are kept (char offsets only) - they just won't drive timing (even-split fallback).
    def _normalize_tokens(text, toks):
        if not isinstance(toks, list) or not toks:
            return None
        out = []
        for t in toks:
            if "char_start" not in t or "char_end" not in t:
                continue
            nt = {
                "text": t.get("text", text[t["char_start"]:t["char_end"]]),
                "char_start": t["char_start"],
                "char_end": t["char_end"],
            }
            s, e = t.get("start"), t.get("end")
            if s is not None and e is not None:
                nt["start"] = int(round(float(s) * 1000))
                nt["end"] = int(round(float(e) * 1000))
            # else: omit start/end -> tokenize_caption returns None timing -> even split
            out.append(nt)
        return out or None

    def _normalize_caption(cap):
        """Caption already has timeline_*_ms (ms). Only token times need sec->ms."""
        toks = _normalize_tokens(cap["text"], cap.get("tokens"))
        if toks is not None:
            cap = dict(cap)
            cap["tokens"] = toks
        return cap

    def _groups_to_captions(groups):
        out = []
        for g in groups:
            cap = {
                "text": g["text"],
                "timeline_start_ms": int(round(g["start"] * 1000)),
                "timeline_end_ms": int(round(g["end"] * 1000)),
                "duration_ms": int(round((g["end"] - g["start"]) * 1000)),
            }
            toks = _normalize_tokens(g["text"], g.get("tokens"))
            if toks is not None:
                cap["tokens"] = toks
            out.append(cap)
        return out

    if args.words:
        loaded = json.load(open(args.words, encoding="utf-8"))
        if isinstance(loaded, list):
            captions = [_normalize_caption(c) for c in loaded]
        elif "captions" in loaded:
            # CapCut shape: timeline_*_ms in ms, token start/end in seconds -> normalize.
            captions = [_normalize_caption(c) for c in loaded["captions"]]
        elif "groups" in loaded:
            captions = _groups_to_captions(loaded["groups"]) # transcribe shape (seconds)
        else:
            captions = []
    else:
        import subprocess
        import tempfile
        seg_script = Path(__file__).parent.parent.parent / "indigo11-transcribe" / "scripts" / "word_segment.py"
        if not seg_script.exists():
            seg_script = KIT_ROOT / ".claude" / "skills" / "indigo11-transcribe" / "scripts" / "word_segment.py"
        # tempfile.gettempdir() resolves per-OS (/tmp on POSIX, %TEMP% on Windows);
        # a hardcoded "/tmp" would write to C:\tmp on Windows, which may not exist.
        tmp_out = Path(tempfile.gettempdir()) / f"words{args.words_per_group}-{Path(args.raw).stem}.json"
        seg_cmd = [
            sys.executable, str(seg_script), args.raw,
            "-o", str(tmp_out),
            "--engine", "nlpo3",
            "--words-per-group", str(args.words_per_group),
        ]
        # Honor --no-gap-close on the --raw path too: forward it to word_segment so
        # the user's intent ("keep ElevenLabs' raw inter-phrase gaps, I'll trim
        # manually") survives the re-segmentation step. Without this, --raw + --no-
        # gap-close silently still produced gap-closed groups, which contradicted
        # the flag's documented behavior.
        if args.no_gap_close:
            seg_cmd.append("--no-gap-close")
        subprocess.run(seg_cmd, check=True)
        # word_segment now emits per-token timing in groups[].tokens[]; carry it
        # through (seconds -> ms) so --raw karaoke is as accurate as --words.
        captions = _groups_to_captions(json.load(open(tmp_out, encoding="utf-8"))["groups"])

    if not captions:
        emit_err("No captions to inject")

    ok, close_detail = ensure_capcut_closed(args.force_kill)
    if not ok:
        emit_err(close_detail)

    draft, draft_info_path, tl_uuid = load_draft(project_dir)

    # Auto-flip caption position for 16:9: bottom-third covers nothing,
    # but mouth/face usually mid-frame, so prefer top safe-zone (~22%).
    # Only flip if buyer didn't explicitly override (= still at default 78).
    canvas = draft.get("canvas_config") or {}
    ratio = canvas.get("ratio", "")
    vertical_pct = args.vertical_pct
    if ratio == "16:9" and abs(vertical_pct - 78.0) < 0.01:
        vertical_pct = 22.0 # top safe zone for horizontal
        auto_flipped = True
    else:
        auto_flipped = False

    tpl_text_material = load_template("text_material.json")
    tpl_text_segment = load_template("text_segment.json")
    tpl_text_track = load_template("text_track_empty.json")

    # -- routing: update OUR track, never delete the buyer's (rule #42) ------
    #
    # Four outcomes, all non-destructive by default:
    #   own it + unchanged  -> replace that track's segments in place
    #   own it + hand-edited -> REFUSE (needs --overwrite-owned)
    #   own it + track gone  -> write a new one, re-record ownership (not an error)
    #   no ownership record  -> append, warn; nothing existing is removed
    draft.setdefault("materials", {})
    draft["materials"].setdefault("texts", [])
    draft["materials"].setdefault("material_animations", [])
    draft.setdefault("tracks", [])
    before_draft = deep_copy(draft)

    owned_all = read_owned_tracks(project_dir)
    owned_rec = owned_all.get(args.role) or {}
    owned_id = owned_rec.get("id")
    tracks_by_id = {t.get("id"): t for t in draft["tracks"]}
    owned_track = tracks_by_id.get(owned_id) if owned_id else None

    ownership_mode = "append-new"
    ownership_warning = None
    replaced_track_id = None
    adoption_detail = None

    # Pre-2.4.0 projects: an add-captions op exists but recorded no owned_tracks,
    # so without adoption a re-run stacks a SECOND caption track and only warns.
    # Adoption is attempted only when nothing is owned for this role yet, and it
    # refuses on any ambiguity (see adopt_pre_ownership_track).
    if (not args.append_track and owned_track is None and not owned_id):
        prior = last_caption_op_without_ownership(project_dir)
        if prior is not None:
            cand, adoption_detail = adopt_pre_ownership_track(draft, prior, args.role)
            if cand is not None and args.adopt and args.adopt == cand.get("id"):
                # The buyer named this exact track, so it is theirs to give.
                owned_track = cand
                owned_id = cand.get("id")
                # No recorded hash exists for an adopted track, so treat the track
                # as it stands on disk as the baseline: this run replaces it, and
                # the hash written now governs every later run.
                owned_rec = {"id": owned_id,
                             "content_hash": track_hash_from_draft(cand, draft)}
                tracks_by_id = {t.get("id"): t for t in draft["tracks"]}
            elif cand is not None and args.adopt:
                emit_err(
                    f"--adopt {args.adopt} ไม่ตรงกับ track ที่เข้าเกณฑ์ ({cand.get('id')}) - "
                    f"ไม่เขียนให้ (--adopt names a different track than the candidate; "
                    f"nothing was written)",
                    role=args.role, candidate_track_id=cand.get("id"),
                    requested_track_id=args.adopt)
            elif cand is not None:
                # REFUSE, never adopt on our own judgement. The old signature -
                # unnamed + use_effect_default_color False + a styles[] list +
                # a matching segment count - is not evidence this kit wrote the
                # track: templates/text_track_empty.json shows a UI-made track is
                # also name "" / is_default_name true, and a UI per-letter-colour
                # text carries use_effect_default_color false too (rule #15). So a
                # buyer's own title of the right length matched, and we replaced
                # it with captions - flagless destruction of a track we never
                # wrote, which is the 2026-09-18 refund all over again.
                first_texts = []
                for s in (cand.get("segments") or [])[:3]:
                    mat = {m.get("id"): m for m in
                           ((draft.get("materials") or {}).get("texts") or [])
                           }.get(s.get("material_id")) or {}
                    try:
                        first_texts.append(json.loads(mat.get("content") or "{}").get("text", ""))
                    except (ValueError, TypeError):
                        pass
                emit_err(
                    f"project นี้มีซับจากเวอร์ชันก่อน 2.4.0 - เจอ text track {cand.get('id')} "
                    f"ที่อาจเป็นของเรา แต่ยืนยันไม่ได้ว่าไม่ใช่ title ที่คุณพิมพ์เอง จึงไม่เขียนทับ "
                    f"(a pre-2.4.0 caption track MAY be {cand.get('id')}, but that cannot be "
                    f"told apart from a title you typed yourself, so nothing was written)",
                    role=args.role,
                    candidate_track_id=cand.get("id"),
                    candidate_first_texts=first_texts,
                    candidate_segments=len(cand.get("segments") or []),
                    options=[
                        f"ถ้า track {cand.get('id')} คือซับเก่าของ kit จริง: รันซ้ำด้วย "
                        f"--adopt {cand.get('id')} (replaces THAT track and records ownership)",
                        "ถ้าเป็น title ที่คุณพิมพ์เอง: รันซ้ำด้วย --append-track "
                        "(adds a new caption track and leaves yours alone)",
                    ])

    if args.append_track:
        ownership_mode = "append-forced"
    elif owned_track is not None:
        cur_hash = track_hash_from_draft(owned_track, draft)
        untouched = same_as_written(owned_track, draft, owned_rec)
        if untouched or args.overwrite_owned:
            ownership_mode = "replace-owned" if untouched else "replace-owned-forced"
            if adoption_detail and untouched:
                ownership_mode = "adopted-owned"
                ownership_warning = adoption_detail
            replaced_track_id = owned_id
        else:
            emit_err(
                f"track ซับของ role '{args.role}' ถูกแก้ด้วยมือหลังจากที่เราเขียนไว้ "
                f"({len(owned_track.get('segments') or [])} segment บน track {owned_id}) - "
                f"เขียนทับจะทำให้งานที่แก้ไว้หาย จึงไม่เขียนให้ "
                f"(the '{args.role}' caption track has been hand-edited since we wrote it; "
                f"overwriting would discard those changes, so nothing was written)",
                role=args.role,
                track_id=owned_id,
                expected_hash=owned_rec.get("content_hash"),
                actual_hash=cur_hash,
                options=[
                    "ถ้าจะทิ้งที่แก้ไว้แล้วเขียนใหม่: รันซ้ำด้วย --overwrite-owned "
                    "(discard the hand edits and rewrite the track)",
                    "ถ้าจะเก็บทั้งสองอัน: รันซ้ำด้วย --append-track "
                    "(keep the edited track and add a new one beside it)",
                ],
            )
    elif owned_id:
        ownership_mode = "recreate-owned"
        ownership_warning = (f"track เดิมของ role '{args.role}' ถูกลบไปแล้ว - สร้าง track ใหม่ให้ "
                             f"(the previously owned track is gone; a new one was created)")
    elif any(t.get("type") == "text" for t in draft["tracks"]):
        ownership_warning = (
            f"project นี้มี text track อยู่แล้วแต่ยังไม่มีบันทึกว่าเป็นของเรา - เพิ่ม track ใหม่ "
            f"ไม่ได้ลบของเดิม (this project already has text track(s) with no ownership record; "
            f"a new track was ADDED and nothing existing was removed)"
            + (f" [adoption not attempted: {adoption_detail}]" if adoption_detail else ""))

    # Color-only highlight by default: buyers found the size bump distracting,
    # so the active token changes color but not size unless explicitly asked.
    if args.highlight_size is None:
        args.highlight_size = args.base_size

    base_color = hex_to_rgb(args.color)
    hl_color = hex_to_rgb(args.highlight_color)
    # legibility (default ON): black outline + soft drop shadow so captions read on
    # any background. --border-width 0 turns the outline off; --no-shadow drops the shadow.
    border = None if args.border_width <= 0 else {"color": hex_to_rgb(args.border_color), "width": args.border_width}
    shadow = None if args.no_shadow else {"color": [0, 0, 0], "alpha": args.shadow_alpha, "distance": 8.0, "angle": -45.0}

    new_segments = []
    sub_caption_count = 0

    # Gap-close at the CAPTION level - applies to both --plain and --karaoke.
    #
    # Why: ElevenLabs char-level timing leaves small silences between phrases (breath,
    # sentence breaks - 20-500ms). If left as dead time on the timeline, the whole
    # caption line BLINKS off-screen between phrases, which reads as a flaw. Holding
    # the current caption until the next begins matches TikTok / Submagic / CapCut
    # auto-captions and feels natural.
    #
    # Defense in depth: indigo11-transcribe (--save-all) already gap-closes captions.json
    # and words.json by default at the point of output. This pass is the safety net - 
    # if a buyer hand-writes captions, generates words.json with a different tool, or
    # passes --no-gap-close to transcribe, we still close gaps here. Running gap-close
    # twice is a no-op on already-closed data; cheap insurance.
    #
    # Only extend (never shrink), only between adjacent captions. Captions are
    # chronological by contract.
    captions = [dict(c) for c in captions] # don't mutate caller's input
    if not args.no_gap_close:
        for i in range(len(captions) - 1):
            cur_e = captions[i]["timeline_end_ms"]
            nxt_s = captions[i + 1]["timeline_start_ms"]
            new_e = int(round(_held_end(cur_e / 1000.0, nxt_s / 1000.0) * 1000))
            if new_e > cur_e:
                captions[i]["timeline_end_ms"] = new_e
                captions[i]["duration_ms"] = new_e - captions[i]["timeline_start_ms"]

    for cap in captions:
        text = cap["text"]
        cs = cap["timeline_start_ms"]
        ce = cap["timeline_end_ms"]
        cap_dur = ce - cs

        if args.karaoke or args.accumulate:
            tokens = tokenize_caption(cap) # (text, cs, ce, start_ms|None, end_ms|None)
            # Karaoke default: strip spaces from display text and recompute char
            # offsets. Most Submagic-style buyers want a compact look without
            # spaces between tokens. Opt out with --keep-spaces.
            # IMPORTANT: carry each token's start_ms/end_ms through the re-merge - 
            # that real spoken timing is what drives the highlight beat below.
            # Accumulate mode always preserves the original spacing - the build-up
            # is a readable sentence, and stripping spaces would glue English words.
            if not args.accumulate and not args.keep_spaces and tokens:
                merged_text = ""
                new_tokens = []
                for tok in tokens:
                    tok_text = tok[0]
                    new_start = len(merged_text)
                    merged_text += tok_text
                    new_tokens.append((tok_text, new_start, len(merged_text), tok[3], tok[4]))
                text = merged_text
                tokens = new_tokens
            n_tokens = len(tokens)

            # Decide each sub-caption's [start, end] on the timeline.
            #
            # Preferred: use the token's REAL spoken start/end (from ElevenLabs
            # char-level timing, preserved through word_segment + post-process).
            # This makes the highlight land on each word exactly when it's said - 
            # fast words flash by, slow words linger - which is the whole point of
            # karaoke. Falling back to an even split (cap_dur / n) is what made the
            # highlight drift ahead of the audio.
            #
            # We use real timing only when EVERY token has it and the values are
            # monotonic and inside the caption window; otherwise we fall back to
            # the even split so a hand-written or partial captions file still works.
            have_timing = n_tokens > 0 and all(
                tok[3] is not None and tok[4] is not None for tok in tokens)
            if have_timing:
                # clamp + enforce monotonic non-overlap within the caption window
                bounds = []
                prev = cs
                for wi, tok in enumerate(tokens):
                    s_ms = int(round(tok[3]))
                    e_ms = int(round(tok[4]))
                    # first token starts at caption start; last ends at caption end
                    if wi == 0:
                        s_ms = cs
                    s_ms = max(s_ms, prev)
                    e_ms = max(e_ms, s_ms + 1)
                    if wi == n_tokens - 1:
                        e_ms = ce
                    e_ms = min(e_ms, ce)
                    s_ms = min(s_ms, e_ms - 1) if e_ms - 1 > cs else s_ms
                    bounds.append((s_ms, e_ms))
                    prev = e_ms
            else:
                # Fallback ONLY when token timing is absent (have_timing == False) - 
                # i.e. hand-written captions or third-party words.json without per-
                # token start/end. Even split is not great (highlight will drift on
                # fast/slow words), but it's the best we can do without timestamps,
                # and the buyer can switch to ElevenLabs transcribe.py to get real
                # timing. This branch is intentionally kept - do NOT remove without
                # also removing tokenize_caption's None-timing fallback shape.
                sub_dur = cap_dur // n_tokens if n_tokens else cap_dur
                bounds = []
                for wi in range(n_tokens):
                    sub_s = cs + wi * sub_dur
                    sub_e = cs + (wi + 1) * sub_dur if wi < n_tokens - 1 else ce
                    bounds.append((sub_s, sub_e))

            # Gap-close (default ON): hold each sub-caption visible until the next
            # one begins. ElevenLabs char timing has small silences between words
            # (e.g. 80ms between 'ไหม' and 'ผม'), which would otherwise make the
            # full caption line BLINK off-screen between every word. By extending
            # each sub-caption's end to the next sub's start, the current word
            # stays highlighted across the gap - the line never disappears mid-
            # sentence. The buyer can still trim manually if they want hard cuts.
            #
            # Honors --no-gap-close so the flag is consistent end-to-end: when the
            # buyer asks for raw boundaries, intra-token gaps stay as ElevenLabs
            # reported them. Otherwise (default) we close them. This is the same
            # rule the inter-caption pass (above, before the karaoke branch) follows.
            if not args.no_gap_close:
                for wi in range(n_tokens - 1):
                    s, _ = bounds[wi]
                    next_s, _ = bounds[wi + 1]
                    bounds[wi] = (s, next_s)

            # remember the LAST sub-caption's slot so we can extend it across
            # any gap before the NEXT caption begins (done after this loop).
            last_sub_idx = len(new_segments) + n_tokens - 1

            if args.accumulate:
                # One fixed_width per caption (sized to the widest frame) keeps
                # the box constant across all frames - the static-anchor property
                # that makes the build-up look like typing instead of re-layout.
                canvas_w = canvas.get("width") or 1920
                canvas_h = canvas.get("height") or 1080
                fixed_w = accumulate_fixed_width(
                    text, tokens, karaoke=args.karaoke,
                    base_size=args.base_size, hl_size=args.highlight_size,
                    canvas_w=canvas_w, canvas_h=canvas_h)
                transform_x = (args.horizontal_pct - 50.0) / 50.0
                for wi in range(n_tokens):
                    sub_s, sub_e = bounds[wi]
                    prefix = text[:tokens[wi][2]]
                    if args.karaoke:
                        styles = build_styles_karaoke(
                            prefix, tokens[:wi + 1], wi, base_color, hl_color,
                            args.base_size, args.highlight_size, border, shadow)
                    else:
                        styles = build_styles_plain(prefix, base_color, args.base_size, border, shadow)
                    _append_caption_segment(
                        draft, tpl_text_material, tpl_text_segment, new_segments,
                        text=prefix,
                        styles=styles,
                        timeline_start_ms=sub_s,
                        duration_ms=sub_e - sub_s,
                        vertical_pct=vertical_pct,
                        material_overrides={"alignment": 0, "fixed_width": fixed_w},
                        transform_x=transform_x,
                    )
                    sub_caption_count += 1
            else:
                for wi in range(n_tokens):
                    sub_s, sub_e = bounds[wi]
                    _append_caption_segment(
                        draft, tpl_text_material, tpl_text_segment, new_segments,
                        text=text,
                        styles=build_styles_karaoke(text, tokens, wi, base_color, hl_color,
                                                    args.base_size, args.highlight_size, border, shadow),
                        timeline_start_ms=sub_s,
                        duration_ms=sub_e - sub_s,
                        vertical_pct=vertical_pct,
                    )
                    sub_caption_count += 1
        else:
            _append_caption_segment(
                draft, tpl_text_material, tpl_text_segment, new_segments,
                text=text,
                styles=build_styles_plain(text, base_color, args.base_size, border, shadow),
                timeline_start_ms=cs,
                duration_ms=cap_dur,
                vertical_pct=vertical_pct,
                transform_x=(args.horizontal_pct - 50.0) / 50.0,
            )

    # Ownership hash covers the segments as WE wrote them, then _indigo11_text goes
    # away - it must never reach the file CapCut reads.
    new_track_hash = owned_hash({"segments": new_segments})
    new_style_hash = owned_hash({"segments": new_segments}, timing=False)
    new_timing = owned_timing({"segments": new_segments})
    for seg in new_segments:
        for k in [k for k in seg if k.startswith("_indigo11_")]:
            seg.pop(k, None)

    added_material_ids = set()
    for seg in new_segments:
        if seg.get("material_id"):
            added_material_ids.add(seg["material_id"])
        for r in (seg.get("extra_material_refs") or []):
            added_material_ids.add(r)

    deleted_segment_ids = []
    deleted_material_ids = []
    if replaced_track_id:
        old_track = tracks_by_id[replaced_track_id]
        old_segments = old_track.get("segments") or []
        deleted_segment_ids = [s.get("id") for s in old_segments if s.get("id")]
        # Remove ONLY the materials this track's own segments referenced, and only
        # at refcount 0. A text material a buyer's own title segment also points at
        # would render blank if we dropped it - never `materials["texts"] = []`.
        candidate_ids = set()
        for s in old_segments:
            if s.get("material_id"):
                candidate_ids.add(s["material_id"])
            for r in (s.get("extra_material_refs") or []):
                candidate_ids.add(r)
        for mid in candidate_ids:
            if material_refcount(draft, mid, exclude_tracks=(replaced_track_id,)) == 0:
                deleted_material_ids.append(mid)
        drop = set(deleted_material_ids)
        for cat, items in list(draft["materials"].items()):
            if isinstance(items, list):
                draft["materials"][cat] = [
                    i for i in items
                    if not (isinstance(i, dict) and i.get("id") in drop)]
        old_track["segments"] = new_segments
        text_track = old_track
    else:
        text_track = deep_copy(tpl_text_track)
        text_track["id"] = new_uuid()
        # A UI hint so the buyer can tell our track apart in CapCut's track list.
        # Ownership is decided by state.json (rule #42), never by this name - and
        # the replace-owned branch above deliberately leaves the name alone, so a
        # track the buyer renamed keeps their name.
        text_track["name"] = f"indigo11-{args.role}"
        text_track["is_default_name"] = False
        text_track["segments"] = new_segments
        draft["tracks"].append(text_track)

    plan = {
        "add": {"tracks": [] if replaced_track_id else [text_track["id"]],
                "materials": sorted(added_material_ids)},
        "replace": {"tracks": [replaced_track_id] if replaced_track_id else []},
        "delete": {"segments": deleted_segment_ids, "materials": deleted_material_ids},
    }
    # Refuse the whole write if this op moved anything it did not declare - the
    # check that turns "captions vanished the buyer's titles" into an error rather
    # than a discovery on playback.
    try:
        assert_preserved(before_draft, draft, plan)
    except ValueError as e:
        emit_err(str(e), role=args.role, ownership_mode=ownership_mode)

    owned_tracks = dict(owned_all)
    owned_tracks[args.role] = {"id": text_track["id"], "content_hash": new_track_hash,
                               "style_hash": new_style_hash, "timing_us": new_timing}

    with begin_op(project_dir, "add-captions", plan=plan) as op:
        atomic_write_text(draft_info_path, json.dumps(draft, ensure_ascii=False, indent=2))
        mirror = mirror_9_places(project_dir)
        op.check_mirror(mirror)

        state_path = write_state(project_dir, {
            "skill": "indigo11-capcut",
            "op": "add-captions",
            "op_id": op.op_id,
            "mode": ("accumulate-karaoke" if (args.accumulate and args.karaoke) else
                     "accumulate" if args.accumulate else
                     "karaoke" if args.karaoke else "plain"),
            "caption_count_in": len(captions),
            "caption_count_out": len(new_segments),
            "base_size": args.base_size,
            "highlight_size": args.highlight_size if args.karaoke else None,
            "highlight_color": args.highlight_color if args.karaoke else None,
            "vertical_pct": vertical_pct,
            "horizontal_pct": args.horizontal_pct if args.accumulate else None,
            "aspect_ratio": ratio or "unknown",
            "auto_flipped_vertical": auto_flipped,
            "append_track": args.append_track,
            "role": args.role,
            "ownership_mode": ownership_mode,
            "adoption": adoption_detail,
            "owned_tracks": owned_tracks,
            "replaced_track_id": replaced_track_id,
            "materials_removed": len(deleted_material_ids),
            "mirror_result": mirror,
        })

    emit_ok(
        op="add-captions",
        mode=("accumulate-karaoke" if (args.accumulate and args.karaoke) else
              "accumulate" if args.accumulate else
              "karaoke" if args.karaoke else "plain"),
        project=args.project,
        project_dir=str(project_dir),
        caption_count_in=len(captions),
        caption_count_out=len(new_segments),
        role=args.role,
        ownership_mode=ownership_mode,
        adoption=adoption_detail,
        track_id=text_track["id"],
        replaced_track_id=replaced_track_id,
        materials_removed=len(deleted_material_ids),
        warning=ownership_warning,
        capcut_close=close_detail,
        state_path=str(state_path),
        from_timeline=({
            "media": from_timeline_info["media"],
            "edls": [str(p) for p in from_timeline_info["edl_paths"]],
            "words": str(from_timeline_info["words_path"]),
            "speech_track": f"{from_timeline_info['track_type']} "
                            f"{from_timeline_info['track_index']}",
            "notes": from_timeline_info["notes"],
        } if from_timeline_info else None),
        **op.info(),
    )
    return 0


def _append_caption_segment(draft, tpl_text_material, tpl_text_segment, out_segments,
                            *, text: str, styles: list,
                            timeline_start_ms: int, duration_ms: int,
                            vertical_pct: float = 78.0,
                            material_overrides: dict | None = None,
                            transform_x: float = 0.0) -> None:
    """Build one text material + text segment, append to draft + out_segments."""
    text_id = new_uuid()
    mat = deep_copy(tpl_text_material)
    mat["id"] = text_id
    mat["content"] = json.dumps({"styles": styles, "text": text}, ensure_ascii=False)
    if material_overrides:
        # Used by --accumulate: alignment=0 (left) + fixed_width so the text box
        # stays constant while the prefix grows - already-shown words never move.
        mat.update(material_overrides)
    # When using multi-style ranges with useLetterColor: true on highlighted tokens,
    # the outer-level color/size fields must NOT override the inner styles. CapCut
    # honors the inner styles only when use_effect_default_color is FALSE - when
    # it's True (template default), CapCut falls back to outer text_color/font_size
    # and ignores the styles array. Discovered by inspecting a UI-authored sample.
    mat["use_effect_default_color"] = False
    # Some CapCut versions also peek at outer text_color/font_size. Sync them with
    # the FIRST style block to keep them consistent (won't visually override since
    # use_effect_default_color is False, but reduces risk of CapCut "fixing" the file).
    if styles:
        first = styles[0]
        color = first.get("fill", {}).get("content", {}).get("solid", {}).get("color")
        if color and len(color) == 3:
            mat["text_color"] = "#{:02X}{:02X}{:02X}".format(
                int(color[0] * 255), int(color[1] * 255), int(color[2] * 255))
        if "size" in first:
            mat["font_size"] = float(first["size"])
    draft["materials"]["texts"].append(mat)

    helper_id = new_uuid()
    draft["materials"]["material_animations"].append({
        "id": helper_id, "type": "sticker_animation",
        "animations": [], "multi_language_current": "none",
    })

    seg = deep_copy(tpl_text_segment)
    seg["id"] = new_uuid()
    seg["material_id"] = text_id
    seg["extra_material_refs"] = [helper_id]
    seg["target_timerange"] = {
        "start": timeline_start_ms * MS,
        "duration": duration_ms * MS,
    }
    seg["source_timerange"] = None
    # Caption vertical position: CapCut transform.y is normalized [-1..+1].
    # vertical_pct: 0=top, 50=center, 100=bottom. Map: y = (pct - 50) / 50
    # So vertical_pct=78 → y=+0.56 (lower-third, TikTok-friendly).
    y = (vertical_pct - 50.0) / 50.0
    if "clip" not in seg or seg["clip"] is None:
        seg["clip"] = {"scale": {"x": 1.0, "y": 1.0}, "rotation": 0.0,
                       "transform": {"x": 0.0, "y": 0.0},
                       "flip": {"horizontal": False, "vertical": False}, "alpha": 1.0}
    seg["clip"]["transform"]["y"] = y
    seg["clip"]["transform"]["x"] = transform_x
    # Carried only so owned_hash() can fingerprint what we wrote; stripped before
    # the draft is serialised (a stray key would make CapCut normalise the file
    # and the very next hash read would then look like a buyer edit). These mirror
    # exactly what track_hash_from_draft() reads back off the material, so the
    # hash round-trips and an untouched re-run is never mistaken for a buyer edit.
    seg["_indigo11_text"] = text
    seg["_indigo11_styles"] = styles
    seg["_indigo11_font_size"] = mat.get("font_size")
    seg["_indigo11_text_color"] = mat.get("text_color")
    seg["_indigo11_alignment"] = mat.get("alignment")
    seg["_indigo11_fixed_width"] = mat.get("fixed_width")
    out_segments.append(seg)


if __name__ == "__main__":
    sys.exit(main())
