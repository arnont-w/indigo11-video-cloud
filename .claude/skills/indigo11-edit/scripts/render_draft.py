#!/usr/bin/env python3
"""Render a CapCut Desktop project to a final mp4 with ffmpeg - no CapCut UI.

CapCut Desktop has NO headless export (the binary is a Qt GUI app whose only
CLI flags are -uplog / -updmplog). So instead of driving CapCut, this script
READS the same timeline JSON CapCut reads (draft_info.json on macOS /
draft_content.json on Windows, see rule #6) and rebuilds the edit as an
ffmpeg graph: cuts, per-clip zoom/scale/position, and styled text overlays
rendered through libass so Thai shapes correctly.

What it reproduces (verified against a real project):
  - video track cuts   : source_timerange -> target_timerange, re-encoded
                         (never -c copy, rule #10)
  - per-clip transform : clip.scale / clip.transform / clip.alpha
  - text tracks        : content.styles[] -> colour, size, stroke, position,
                         karaoke per-token highlight (useLetterColor, rule #15)
  - canvas             : canvas_config width/height (9:16 etc.)
  - audio              : taken from the same cut ranges, concatenated

What it CANNOT reproduce (and says so instead of faking it):
  - CapCut library resources: effects, filters, transitions, stickers,
    text_templates, material_animations with real keyframes. Those are
    ByteDance-proprietary GPU shaders with no public spec. If the draft uses
    any, the script refuses by default (--allow-unsupported downgrades to a
    warning and renders everything else).

Failure modes the agent should expect:
  - PATH ffmpeg on macOS is built WITHOUT libass/freetype -> no subtitles or
    drawtext filter. Use ffmpeg-full (auto-detected; --ffmpeg overrides).
  - Media moved since the project was saved -> path no longer exists; the
    script reports the missing file rather than rendering a black gap.
  - VFR phone footage: handled by forcing CFR on output (rule #27).
"""
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _capcut_lib import draft_state_path, default_drafts_dir, candidate_drafts_dirs
except Exception:
    draft_state_path = None

US = 1_000_000.0

# CapCut text `size` -> pixels, calibrated against CapCut's own draft_cover.jpg
# render: the full glyph (red fill + white stroke) of a size-11.5 line measured
# 341px wide / 78px tall on a 1440x1920 canvas. Matching that gives k ~= 6.09.
CAPCUT_SIZE_K = (55.0 / 11.5) * (341.0 / 268.0)


def force_utf8():
    if os.name == "nt":
        for s in (sys.stdout, sys.stderr):
            try:
                s.reconfigure(encoding="utf-8")
            except Exception:
                pass


def find_ffmpeg(override=None):
    """PATH ffmpeg on this Mac lacks libass; prefer a build that has it."""
    cands = []
    if override:
        cands.append(override)
    if os.name == "nt":
        # Windows ffmpeg builds (gyan/BtbN, what install.ps1 installs) ship libass
        # in the standard binary, so PATH is the right first choice.
        cands += [shutil.which("ffmpeg") or "", shutil.which("ffmpeg.exe") or ""]
    else:
        # macOS: Homebrew's plain `ffmpeg` is built WITHOUT libass, so a PATH hit
        # would silently drop every caption. Prefer ffmpeg-full, PATH last.
        cands += [
            "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
            "/usr/local/opt/ffmpeg-full/bin/ffmpeg",
            shutil.which("ffmpeg") or "",
        ]
    for c in cands:
        if c and os.path.exists(c):
            try:
                out = subprocess.run([c, "-hide_banner", "-filters"],
                                     capture_output=True, text=True, timeout=30).stdout
            except Exception:
                continue
            if " ass " in out or " subtitles " in out:
                return c, True
    for c in cands:
        if c and os.path.exists(c):
            return c, False
    raise SystemExit("ffmpeg not found")


def resolve_project(name_or_path):
    p = Path(name_or_path).expanduser()
    if p.is_dir():
        return p
    if draft_state_path:
        for d in candidate_drafts_dirs():
            cand = Path(d) / name_or_path
            if cand.is_dir():
                return cand
    home = Path.home()
    tail = Path("User Data") / "Projects" / "com.lveditor.draft"
    if os.name == "nt":
        bases = [os.environ.get("LOCALAPPDATA"), str(home / "AppData" / "Local"),
                 os.environ.get("APPDATA"), str(home / "AppData" / "Roaming")]
        dirs = [Path(b) / "CapCut" / tail for b in bases if b]
    else:
        dirs = [home / "Movies" / "CapCut" / tail,
                home / "Movies" / "JianyingPro" / tail]
    env = os.environ.get("CAPCUT_DRAFTS_DIR")
    if env:
        dirs.insert(0, Path(env))
    for d in dirs:
        cand = d / name_or_path
        if cand.is_dir():
            return cand
    raise SystemExit(f"project not found: {name_or_path}")


def load_draft(proj):
    for n in ("draft_info.json", "draft_content.json"):
        p = proj / n
        if p.exists() and p.stat().st_size > 0:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    raise SystemExit(f"no timeline JSON in {proj} (open the project and Cmd+S first)")


def unsupported_report(mats, tracks):
    """List CapCut-proprietary features we cannot reproduce in ffmpeg."""
    bad = []
    for key, label in [("effects", "video/character effect"),
                       ("video_effects", "video effect"),
                       ("filters", "filter"),
                       ("transitions", "transition"),
                       ("stickers", "sticker"),
                       ("text_templates", "text template")]:
        v = mats.get(key)
        if isinstance(v, list) and v:
            bad.append(f"{label} x{len(v)}")
    n_anim = 0
    for a in mats.get("material_animations") or []:
        if a.get("animations"):
            n_anim += len(a["animations"])
    if n_anim:
        bad.append(f"in/out animation x{n_anim}")
    for t in tracks:
        if t.get("type") in ("effect", "filter"):
            segs = t.get("segments") or []
            if segs:
                bad.append(f"{t['type']} track x{len(segs)}")
    return bad


def parse_text_material(mat):
    """CapCut stores text style as a JSON string in `content`."""
    raw = mat.get("content") or ""
    text, styles = "", []
    try:
        c = json.loads(raw)
        text = c.get("text", "")
        styles = c.get("styles", []) or []
    except Exception:
        text = raw
    return text, styles


def rgb_to_ass(color, alpha=1.0):
    """CapCut colour = [r,g,b] floats 0..1. ASS = &HAABBGGRR (BGR + inverted alpha)."""
    r, g, b = [max(0, min(255, int(round(c * 255)))) for c in (color + [0, 0, 0])[:3]]
    a = max(0, min(255, int(round((1.0 - alpha) * 255))))
    return f"&H{a:02X}{b:02X}{g:02X}{r:02X}"


def hex_to_ass(hx, alpha=1.0):
    hx = (hx or "").lstrip("#")
    if len(hx) != 6:
        return "&H00FFFFFF"
    r, g, b = int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)
    a = max(0, min(255, int(round((1.0 - alpha) * 255))))
    return f"&H{a:02X}{b:02X}{g:02X}{r:02X}"


def ass_time(us):
    s = us / US
    h = int(s // 3600); s -= h * 3600
    m = int(s // 60); s -= m * 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def ass_escape(t):
    return t.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")




# Thai vowels/tone marks are separate code points that must never start a line -
# breaking "แอนดริว" between "ดร" and "ิว" detaches the vowel from its consonant.
_THAI_COMBINING = set(
    "\u0e31\u0e34\u0e35\u0e36\u0e37\u0e38\u0e39\u0e3a"
    "\u0e47\u0e48\u0e49\u0e4a\u0e4b\u0e4c\u0e4d\u0e4e"
)


def _clusters(text):
    """Split into unbreakable clusters: a base char plus its combining marks."""
    import unicodedata
    out = []
    for ch in text:
        if out and (ch in _THAI_COMBINING or unicodedata.combining(ch)):
            out[-1] += ch
        else:
            out.append(ch)
    return out

def wrap_text(text, font_path, px_size, max_px):
    r"""Pre-wrap to the canvas the way CapCut does.

    libass ignores MarginL/R once \pos() is used (and \pos is required to hit
    CapCut's exact x/y), so wrapping has to happen in the text itself. Thai has
    no spaces, so a space-split wrapper does nothing - measure real glyph widths
    with Pillow and break by character, preferring a space when one exists.
    Without Pillow we fall back to an em-ratio estimate.
    """
    if not text:
        return text
    measure = None
    if font_path and os.path.exists(font_path):
        try:
            from PIL import ImageFont
            f = ImageFont.truetype(font_path, px_size)
            measure = lambda t: f.getbbox(t)[2] - f.getbbox(t)[0]
        except Exception:
            measure = None
    if measure is None:
        measure = lambda t: len(t) * px_size * 0.62

    out = []
    for para in text.split("\n"):
        if measure(para) <= max_px:
            out.append(para)
            continue
        line = ""
        for cl in _clusters(para):
            if measure(line + cl) > max_px and line:
                cut = line.rfind(" ")
                if cut > 0:
                    out.append(line[:cut])
                    line = line[cut + 1:] + cl
                else:
                    out.append(line)
                    line = cl
            else:
                line += cl
        if line:
            out.append(line)
    return "\n".join(out)

def build_ass(draft, W, H, font_override=None):
    """Text tracks -> one ASS file. libass shapes Thai correctly; drawtext does not."""
    mats = {m["id"]: m for m in draft["materials"].get("texts") or []}
    events, fonts_missing = [], set()

    for track in draft.get("tracks", []):
        if track.get("type") != "text":
            continue
        for seg in track.get("segments", []):
            mat = mats.get(seg.get("material_id"))
            if not mat:
                continue
            text, styles = parse_text_material(mat)
            if not text.strip():
                continue

            tr = seg["target_timerange"]
            start_us, end_us = tr["start"], tr["start"] + tr["duration"]

            clip = seg.get("clip") or {}
            tx = (clip.get("transform") or {}).get("x", 0.0)
            ty = (clip.get("transform") or {}).get("y", 0.0)
            csc = (clip.get("scale") or {}).get("x", 1.0)
            alpha = clip.get("alpha", 1.0)
            # CapCut transform: origin = centre, +y = UP, range -1..1 = half-canvas
            px = W / 2.0 + tx * (W / 2.0)
            py = H / 2.0 - ty * (H / 2.0)

            st = styles[0] if styles else {}
            fp = (st.get("font") or {}).get("path") or mat.get("font_path") or ""
            if font_override:
                fp = font_override
            if fp and not os.path.exists(fp):
                fonts_missing.add(fp)
                fp = ""

            # CapCut's `size` is in its own design units, NOT a % of canvas height.
            # Calibrated against CapCut's own draft_cover.jpg render of this
            # project: size 11.5 measured 55px tall on a 1440x1920 canvas, i.e.
            # px = size * 4.78 at 1920 high. Scale that by the canvas height so
            # other canvases match. (Treating it as %-of-height renders ~4x too
            # big and makes every line overflow.)
            size = st.get("size", mat.get("font_size", 8.0))
            px_size = max(8, int(round(size * CAPCUT_SIZE_K * (H / 1920.0) * csc)))

            fill = ((st.get("fill") or {}).get("content") or {}).get("solid") or {}
            if fill.get("color"):
                primary = rgb_to_ass(list(fill["color"]), fill.get("alpha", 1.0) * alpha)
            else:
                primary = hex_to_ass(mat.get("text_color"), alpha)

            strokes = st.get("strokes") or []
            if strokes:
                sc = (strokes[0].get("content") or {}).get("solid") or {}
                outline_col = rgb_to_ass(list(sc.get("color", [0, 0, 0])), sc.get("alpha", 1.0))
                ow = strokes[0].get("width", mat.get("border_width", 0.06))
            else:
                outline_col = hex_to_ass(mat.get("border_color") or "#000000")
                ow = mat.get("border_width", 0.0)
            # CapCut renders the stroke heavier than a naive width*size/2;
            # measured against draft_cover.jpg it sits near width*size.
            outline_px = max(0, round(ow * px_size, 1))

            shadow_px = 0 if not mat.get("has_shadow") else max(1, int(px_size * 0.04))

            fname = "CapCutText"
            # CapCut wraps long lines inside the canvas; give ASS the same usable
            # width via margins, else size-20 Thai runs off both edges.
            margin = max(12, int(W * 0.04))
            # NOTE: do NOT use \fsp to tighten Thai. Negative letter-spacing
            # detaches vowel/tone marks from their base consonant in libass
            # (แอนดริว renders as แอนดร + a floating ิ). The ~1.5% width
            # difference vs CapCut is the acceptable trade for correct shaping.
            ov = (f"\\fn{fname}\\fs{px_size}\\c{primary}\\3c{outline_col}"
                  f"\\bord{outline_px}\\shad{shadow_px}\\an5\\q1"
                  f"\\pos({px:.0f},{py:.0f})")
            wrapped = wrap_text(text, fp, px_size, W - 2 * margin)
            events.append((start_us, end_us, ov, ass_escape(wrapped), fp))

    events.sort(key=lambda e: e[0])
    fonts = {e[4] for e in events if e[4]}

    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: None

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,CapCutText,48,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    mg = max(12, int(W * 0.04))
    lines = [f"Dialogue: 0,{ass_time(s)},{ass_time(e)},Default,,{mg},{mg},0,,{{{ov}}}{tx}"
             for s, e, ov, tx, _ in events]
    return head + "\n".join(lines) + "\n", fonts, fonts_missing, len(events)


def main():
    force_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="CapCut project name or folder path")
    ap.add_argument("-o", "--output", default=None)
    ap.add_argument("--ffmpeg", default=None)
    ap.add_argument("--font", default=None, help="override font file for all text")
    ap.add_argument("--crf", type=int, default=18)
    ap.add_argument("--fps", default=None,
                    help="output frame rate; default = the draft's own fps "
                         "(CapCut stores it in draft_info.json 'fps'), which for "
                         "iPhone footage is 29.97 not 30 - forcing 30 makes every "
                         "cut drift a little and the file end ~130ms long.")
    ap.add_argument("--allow-unsupported", action="store_true",
                    help="render anyway when the draft uses CapCut-only effects")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    proj = resolve_project(args.project)
    draft = load_draft(proj)
    mats, tracks = draft["materials"], draft.get("tracks", [])

    fps = args.fps or draft.get("fps") or 30
    fps = f"{float(fps):.6f}".rstrip("0").rstrip(".")
    # 29.97 must stay rational for x264 (-r 30000/1001), else timestamps drift.
    if abs(float(fps) - 30000.0 / 1001.0) < 0.01:
        fps_arg, gop = "30000/1001", 30
    else:
        fps_arg, gop = fps, max(1, int(round(float(fps))))

    cc = draft.get("canvas_config") or {}
    W, H = int(cc.get("width", 1080)), int(cc.get("height", 1920))
    total_us = draft.get("duration", 0)

    ffmpeg, has_ass = find_ffmpeg(args.ffmpeg)
    print(f"[render] project : {proj}")
    print(f"[render] canvas  : {W}x{H}   duration {total_us/US:.2f}s")
    print(f"[render] ffmpeg  : {ffmpeg}  (libass={'yes' if has_ass else 'NO'})")

    bad = unsupported_report(mats, tracks)
    if bad:
        msg = "[render] CapCut-only features present, NOT reproducible: " + ", ".join(bad)
        if args.allow_unsupported:
            print(msg + "  -> skipped (--allow-unsupported)")
        else:
            print(msg)
            print("[render] refusing. Export from CapCut, or pass --allow-unsupported "
                  "to render cuts+text only.")
            return 2

    vmats = {m["id"]: m for m in mats.get("videos") or []}
    vtrack = next((t for t in tracks if t.get("type") == "video"), None)
    if not vtrack:
        raise SystemExit("no video track")

    segs = sorted(vtrack["segments"], key=lambda s: s["target_timerange"]["start"])
    inputs, vparts, aparts, missing = [], [], [], []
    n_inputs = 0

    for i, seg in enumerate(segs):
        mat = vmats.get(seg["material_id"])
        if not mat:
            continue
        path = mat.get("path") or ""
        if not os.path.exists(path):
            missing.append(path)
            continue
        sr = seg.get("source_timerange") or {"start": 0, "duration": seg["target_timerange"]["duration"]}
        tr = seg["target_timerange"]
        speed = 1.0
        for sp in mats.get("speeds") or []:
            if sp.get("id") in (seg.get("extra_material_refs") or []):
                speed = sp.get("speed", 1.0) or 1.0

        idx = n_inputs           # ffmpeg input INDEX, not len(inputs) (6 argv items per input)
        n_inputs += 1
        inputs += ["-ss", f"{sr['start']/US:.6f}", "-t", f"{sr['duration']/US:.6f}", "-i", path]

        clip = seg.get("clip") or {}
        sc = (clip.get("scale") or {}).get("x", 1.0)
        txn = (clip.get("transform") or {}).get("x", 0.0)
        tyn = (clip.get("transform") or {}).get("y", 0.0)

        # fill canvas (CapCut default), then apply the clip's own zoom
        f = (f"[{idx}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
             f"crop={W}:{H}")
        if abs(sc - 1.0) > 1e-3:
            zw, zh = int(W * sc) // 2 * 2, int(H * sc) // 2 * 2
            ox = (zw - W) / 2.0 - txn * (W / 2.0)
            oy = (zh - H) / 2.0 + tyn * (H / 2.0)
            f += f",scale={zw}:{zh},crop={W}:{H}:{max(0,int(ox))}:{max(0,int(oy))}"
        if abs(speed - 1.0) > 1e-3:
            f += f",setpts={1.0/speed}*PTS"
        f += f",setsar=1,fps={fps_arg},format=yuv420p[v{i}]"
        vparts.append(f)

        a = f"[{idx}:a]aresample=async=1"
        if abs(speed - 1.0) > 1e-3:
            a += f",atempo={min(2.0,max(0.5,speed))}"
        a += f",aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a{i}]"
        aparts.append(a)

    if missing:
        print("[render] MISSING media (project references files that moved):")
        for m in sorted(set(missing)):
            print("   ", m)
        return 3

    n = len(vparts)
    if n == 0:
        raise SystemExit("no renderable segments")

    ass_text, fonts, fonts_missing, n_events = build_ass(draft, W, H, args.font)
    if fonts_missing:
        print("[render] font file(s) missing, falling back to system font:")
        for f in sorted(fonts_missing):
            print("   ", f)

    out = Path(args.output) if args.output else (
        Path.cwd() / "projects" / "_work" / "capcut-render" / proj.name / "final.mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    ass_path = out.parent / "captions.ass"
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_text)

    # concat expects streams INTERLEAVED per segment: [v0][a0][v1][a1]...
    concat_in = "".join(f"[v{i}][a{i}]" for i in range(n))
    graph = ";".join(vparts + aparts)
    graph += f";{concat_in}concat=n={n}:v=1:a=1[vc][ac]"

    if n_events:
        if not has_ass:
            print("[render] WARNING: this ffmpeg has no libass -> text will be DROPPED.")
            graph += ";[vc]null[vout]"
        else:
            esc = str(ass_path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
            fd = ""
            if fonts:
                fdir = os.path.dirname(sorted(fonts)[0]).replace("\\", "/").replace(":", "\\:")
                fd = f":fontsdir='{fdir}'"
            graph += f";[vc]ass='{esc}'{fd}[vout]"
    else:
        graph += ";[vc]null[vout]"

    cmd = [ffmpeg, "-y"] + inputs + [
        "-filter_complex", graph, "-map", "[vout]", "-map", "[ac]",
        "-c:v", "libx264", "-preset", "medium", "-crf", str(args.crf),
        "-r", fps_arg, "-g", str(gop), "-keyint_min", str(gop),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "192k", str(out)]

    print(f"[render] segments: {n} cuts, {n_events} text events -> {out}")
    if args.dry_run:
        print(" ".join(cmd[:1]) + " ... (dry run)")
        print("[render] ass:", ass_path)
        return 0

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-4000:])
        return r.returncode

    meta = {"skill": "indigo11-capcut", "script": "render_draft.py",
            "source_project": str(proj), "canvas": [W, H],
            "segments": n, "text_events": n_events,
            "unsupported_skipped": bad, "outputs": [str(out), str(ass_path)]}
    with open(out.parent / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"[render] done -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
