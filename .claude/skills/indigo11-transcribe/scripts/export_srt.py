#!/usr/bin/env python3
"""export_srt.py - write an SRT (or WebVTT) subtitle file from a captions file.

Reads any caption JSON this skill makes and finds the cue list by itself:
  words.json      -> groups[]        (N-word captions)
  captions.json   -> groups[] or captions[] if present, else words[]
                     (in captions.json, words[] is the PHRASE list; words_raw[] is never used)
  phrases.json    -> a bare list of {text, start, end}
Each cue needs text + start + end in seconds (or start_ms + end_ms).

  python3 export_srt.py <captions.json|words.json> [-o out.srt] [--vtt] [--bridge 1.0]

  -o / --out   output file. Default: the input name with .srt (or .vtt) in the same folder.
               An output name ending in .vtt switches to WebVTT by itself.
  --vtt        write WebVTT instead of SRT.
  --bridge     close gaps shorter than this many seconds by holding the cue until the
               next one starts (default 1.0; 0 = leave gaps as they are).

Cues are sorted by start; empty cues are dropped; an end that runs past the next start
is clipped to it. Timing is otherwise untouched. Prints one JSON line with the result.
Deterministic, standard library only, no API call and no cost.
"""
import argparse
import json
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

MIN_DUR = 0.2  # seconds: a cue that would end at or before its start gets at least this


def fail(msg):
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    sys.exit(1)


def find_cues(data):
    """Return (container name, list of cue dicts)."""
    if isinstance(data, list):
        return "list", data
    if isinstance(data, dict):
        for key in ("groups", "captions", "words"):
            v = data.get(key)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return key, v
    return "", []


def seconds(cue, name):
    if name in cue and cue[name] is not None:
        return float(cue[name])
    ms = cue.get(name + "_ms")
    if ms is not None:
        return float(ms) / 1000.0
    raise KeyError(name)


def stamp(t, sep):
    ms = max(0, int(round(t * 1000)))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def main():
    ap = argparse.ArgumentParser(description="Write an SRT or WebVTT file from captions.json / words.json.")
    ap.add_argument("captions", help="captions.json, words.json or a bare list of cues")
    ap.add_argument("-o", "--out", help="output file (default: input name with .srt or .vtt)")
    ap.add_argument("--vtt", action="store_true", help="write WebVTT instead of SRT")
    ap.add_argument("--bridge", type=float, default=1.0,
                    help="close gaps shorter than this many seconds (default 1.0, 0 = off)")
    a = ap.parse_args()

    src = Path(a.captions)
    if not src.is_file():
        fail(f"file not found: {src}")
    try:
        data = json.loads(src.read_text(encoding="utf-8-sig"))
    except Exception as e:
        fail(f"not valid JSON: {e}")

    container, raw = find_cues(data)
    if not raw:
        fail("no cue list found (looked for groups[], captions[], words[] or a bare list)")

    cues, skipped = [], 0
    for c in raw:
        if not isinstance(c, dict):
            skipped += 1
            continue
        # tidy spaces, keep intended line breaks, never leave a blank line inside a cue
        text = "\n".join(" ".join(ln.split()) for ln in str(c.get("text", "")).splitlines() if ln.strip())
        try:
            start, end = seconds(c, "start"), seconds(c, "end")
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        if not text:
            skipped += 1
            continue
        cues.append([start, end, text])
    if not cues:
        fail(f"{container}[] has no cue with text + start + end")
    if max(c[1] for c in cues) > 100000:
        fail("times look like milliseconds in start/end; expected seconds (or start_ms/end_ms)")

    cues.sort(key=lambda c: (c[0], c[1]))
    bridged = clipped = 0
    for i, c in enumerate(cues):
        nxt = cues[i + 1][0] if i + 1 < len(cues) else None
        if c[1] <= c[0]:
            c[1] = c[0] + MIN_DUR
        if nxt is not None:
            if c[1] > nxt > c[0]:
                c[1] = nxt
                clipped += 1
            elif a.bridge > 0 and 0 < nxt - c[1] < a.bridge:
                c[1] = nxt
                bridged += 1

    vtt = a.vtt or (a.out or "").lower().endswith(".vtt")
    out = Path(a.out) if a.out else src.with_suffix(".vtt" if vtt else ".srt")
    sep = "." if vtt else ","
    lines = ["WEBVTT", ""] if vtt else []
    for n, (start, end, text) in enumerate(cues, 1):
        if not vtt:
            lines.append(str(n))
        lines += [f"{stamp(start, sep)} --> {stamp(end, sep)}", text, ""]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8", newline="\n")

    print(json.dumps({"ok": True, "out": str(out), "format": "vtt" if vtt else "srt", "source": container + "[]",
                      "cues": len(cues), "bridged": bridged, "clipped": clipped, "skipped": skipped,
                      "first": cues[0][2][:40], "duration": round(cues[-1][1], 3)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
