#!/usr/bin/env python3
"""add_keywords.py - KEY-MESSAGE highlight boxes on a CapCut timeline (one important message at a time).

  python3 add_keywords.py --project P --keywords keywords.json [--y 1000] [--dry-run]

keywords.json = the key messages YOU chose (an editorial decision, never a script's), each with the time it is spoken:
  [ {"text": "อัลไซเมอร์เชื่อมโยงกับระบบเผาผลาญ", "start_ms": 5500, "end_ms": 7800}, ... ]
  (start_ms / end_ms may also be called timeline_start_ms / timeline_end_ms; take the times from words.json / phrases.json)

What a key message is: ONE complete claim the viewer must remember (subject + verb + object, 10-20 visible characters, so short and punchy), for example
"อัลไซเมอร์เชื่อมโยงกับระบบเผาผลาญ", not a fragment such as "คิดว่าอัลไซเมอร์" or a bare noun such as "ระบบเผาผลาญ".

Rules enforced here (so they hold for every project):
  - at most ONE highlight per period: two starts are at least --min-gap-s apart (default 3.0 s; aim for one per 3-5 s at most,
    and skip a period that has nothing important). The later one is dropped and reported.
  - 10-20 visible characters PER LINE (--min-chars / --max-chars), at most 2 lines: at size 22 a line of more than 20 runs off the screen. If a message cannot be shortened and still read well, break it into two lines yourself with "\\n" in the text at a word boundary (a text with spaces is split at the middle space automatically). Thai vowel and tone marks do not count (they sit above/below), spaces do not count.
  - it must not start with a linking word (คิดว่า / แต่ / และ / ที่ / เพราะ ...): that is a fragment (--allow-weak overrides).
  - each box shows for its spoken time, held 1.5-3.0 s, and never overlaps the next one.
Look (defaults, change with the options): yellow fill #FFD400 with a black stroke, size 22, centred, at CapCut Y = 1000
(the subtitles are at Y = 1350). Y is the CapCut position number (up is positive; Y / 1920 = the value the draft stores), the same scale as the subtitle rule Y = 1350,
so Y = 1000 sits UNDER subtitles at 1350.

It uses add_captions.py underneath (own track "indigo11-keyword-highlight": an earlier highlight track of this tool is REPLACED, so old
fragment boxes disappear; a backup is made first), so backup, undo
(restore_project.py), and the "owned track" safety all work the same. CapCut must be closed (or --force-kill).
"""
from __future__ import annotations
import _starter  # noqa: F401,E402
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
import unicodedata


def vis_len(s):
    """characters that take room on screen: spaces and Thai vowel/tone marks (which sit above or below) do not count"""
    return sum(1 for c in s if not c.isspace() and unicodedata.category(c) != "Mn")


WEAK_STARTS = ("แต่", "และ", "ที่", "คิดว่า", "เพราะ", "แล้ว", "ก็", "ซึ่ง", "หรือ")   # a key message does not start by leaning on a previous word


def load(path):
    items = json.load(open(path, encoding="utf-8"))
    if isinstance(items, dict):
        items = items.get("keywords") or items.get("items") or []
    out = []
    for it in items:
        s = it.get("start_ms", it.get("timeline_start_ms"))
        e = it.get("end_ms", it.get("timeline_end_ms"))
        if s is None or not it.get("text"):
            continue
        out.append({"text": str(it["text"]).strip(), "start_ms": int(s), "end_ms": int(e if e is not None else int(s) + 2000)})
    out.sort(key=lambda x: x["start_ms"])
    return out


def auto_split(text, max_chars):
    """break a text that has spaces into two lines at the space nearest the middle"""
    best, bd = None, 10 ** 9
    for i, c in enumerate(text):
        if c == " ":
            d = abs(vis_len(text[:i]) - vis_len(text[i + 1:]))
            if d < bd: best, bd = i, d
    return [text[:best].strip(), text[best + 1:].strip()] if best is not None else [text]


def select(items, min_gap_ms, min_chars, max_chars, hold_min=1500, hold_max=3000, allow_weak=False, max_lines=2):
    kept, dropped, warn = [], [], []
    for it in items:
        lines = [l.strip() for l in it["text"].split("\n") if l.strip()]
        if len(lines) == 1 and vis_len(lines[0]) > max_chars and " " in lines[0]:
            lines = auto_split(lines[0], max_chars)          # a text with spaces: break it at the space nearest the middle
        it = dict(it, text="\n".join(lines))
        n = vis_len(it["text"])
        if n < min_chars:
            dropped.append((it, f"fragment: {n} characters, a key message needs at least {min_chars}")); continue
        if len(lines) > max_lines:
            dropped.append((it, f"{len(lines)} lines: at most {max_lines} fit under the subtitles (shorten it)")); continue
        too = [vis_len(l) for l in lines if vis_len(l) > max_chars]
        if too:
            dropped.append((it, f"a line has {max(too)} characters, at most {max_chars} fit one line at this size. Shorten it, or break it into two lines yourself with a line break (\\n) at a word boundary")); continue
        if it["text"].startswith(WEAK_STARTS) and not allow_weak:
            dropped.append((it, "starts with a linking word (e.g. คิดว่า / แต่ / และ): it leans on the previous words, so it is a fragment. Start at the real subject, or pass --allow-weak")); continue
        if kept and it["start_ms"] - kept[-1]["start_ms"] < min_gap_ms:
            dropped.append((it, f"too close to the previous highlight ({(it['start_ms'] - kept[-1]['start_ms']) / 1000:.1f} s, need {min_gap_ms / 1000:.1f} s)")); continue
        kept.append(dict(it))
    for i, it in enumerate(kept):          # hold time, no overlap with the next box
        dur = max(hold_min, min(hold_max, it["end_ms"] - it["start_ms"]))
        if i + 1 < len(kept):
            dur = min(dur, kept[i + 1]["start_ms"] - it["start_ms"] - 100)
        it["end_ms"] = it["start_ms"] + max(600, dur)
    return kept, dropped, warn


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--keywords", required=True, help="keywords.json: the key messages with their spoken times")
    ap.add_argument("--y", type=float, default=1000.0, help="CapCut Y position of the boxes (default 1000; the subtitles are at 1350)")
    ap.add_argument("--canvas-h", type=float, default=1920.0)
    ap.add_argument("--color", default="#FFD400", help="text colour (default yellow)")
    ap.add_argument("--stroke-color", default="#000000")
    ap.add_argument("--stroke-width", type=float, default=0.08)
    ap.add_argument("--size", type=float, default=22.0)
    ap.add_argument("--min-gap-s", type=float, default=3.0)
    ap.add_argument("--min-chars", type=int, default=10)
    ap.add_argument("--max-chars", type=int, default=20)
    ap.add_argument("--allow-weak", action="store_true", help="keep messages that start with a linking word")
    ap.add_argument("--no-sfx", action="store_true", help="do not move the pop sounds to the new boxes")
    ap.add_argument("--no-animation", action="store_true", help="do not give the boxes the Tiny Jump animation")
    ap.add_argument("--animation", default="Tiny Jump", help="in-animation copied from the fxlib library project (default Tiny Jump)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--drafts-dir")
    ap.add_argument("--force-kill", action="store_true")
    a = ap.parse_args()

    items = load(a.keywords)
    if not items:
        sys.exit("keywords.json has no usable entries (each needs text and start_ms)")
    kept, dropped, warn = select(items, int(a.min_gap_s * 1000), a.min_chars, a.max_chars, allow_weak=a.allow_weak)
    print(f"{len(kept)} key messages kept, {len(dropped)} dropped, from {len(items)} given:")
    for it in kept:
        print(f"  KEEP {it['start_ms'] / 1000:7.2f}-{it['end_ms'] / 1000:6.2f}s  {it['text'].replace(chr(10), '  /  ')}")
    for it, why in dropped:
        print(f"  DROP {it['start_ms'] / 1000:7.2f}s  {it['text']}   ({why})")
    for it, why in warn:
        print(f"  WARN {it['start_ms'] / 1000:7.2f}s  {it['text']}   ({why})")
    if not kept:
        sys.exit("nothing left to place")
    if a.dry_run:
        print("dry run: nothing written")
        return

    vertical_pct = 50.0 + 50.0 * (a.y / a.canvas_h)      # add_captions: y = (pct - 50) / 50, and CapCut Y / canvas height = y
    caps = [{"timeline_start_ms": it["start_ms"], "timeline_end_ms": it["end_ms"],
             "duration_ms": it["end_ms"] - it["start_ms"], "text": it["text"]} for it in kept]
    with tempfile.TemporaryDirectory() as td:
        wp = Path(td) / "keyword-words.json"
        wp.write_text(json.dumps(caps, ensure_ascii=False, indent=1), encoding="utf-8")
        cmd = [sys.executable, str(HERE / "add_captions.py"), "--project", a.project, "--words", str(wp),
               "--role", "keyword-highlight", "--overwrite-owned",
               "--color", a.color, "--border-color", a.stroke_color, "--border-width", str(a.stroke_width),
               "--base-size", str(a.size), "--vertical-pct", f"{vertical_pct:.4f}"]
        if a.drafts_dir: cmd += ["--drafts-dir", a.drafts_dir]
        if a.force_kill: cmd.append("--force-kill")
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    sys.stdout.write(r.stdout[-1800:])
    if r.returncode != 0:
        sys.stderr.write(r.stderr[-1200:])
        sys.exit(r.returncode)
    if not a.no_animation:      # my-rules: every keyword box has the Tiny Jump animation
        cmd = [sys.executable, str(HERE / "animate_keywords.py"), "--project", a.project, "--animation", a.animation]
        if a.drafts_dir: cmd += ["--drafts-dir", a.drafts_dir]
        if a.force_kill: cmd.append("--force-kill")
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        sys.stdout.write(r.stdout[-700:])
        if r.returncode != 0:
            sys.stderr.write(r.stderr[-800:])
            print("NOTE: highlights were placed, but the animation was not added (see above); run animate_keywords.py after fixing")
    if not a.no_sfx:      # one pop at the start of every box; old pops that belonged to the old boxes are moved or removed
        cmd = [sys.executable, str(HERE / "sync_keyword_sfx.py"), "--project", a.project]
        if a.drafts_dir: cmd += ["--drafts-dir", a.drafts_dir]
        if a.force_kill: cmd.append("--force-kill")
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        sys.stdout.write(r.stdout[-1500:])
        if r.returncode != 0:
            sys.stderr.write(r.stderr[-800:])
            print("NOTE: highlights were placed, but the pop sounds were not synced (see above); run sync_keyword_sfx.py after fixing")


if __name__ == "__main__":
    main()
