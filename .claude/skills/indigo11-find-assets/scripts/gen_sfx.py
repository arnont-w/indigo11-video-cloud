#!/usr/bin/env python3
"""gen_sfx.py - STEP 3 of the search order for a SOUND EFFECT: generate it (ElevenLabs sound generation). It costs money.

  python3 gen_sfx.py --prompt "soft pop, short, bright" --duration 1 --out pop.mp3 --estimate-only
  python3 gen_sfx.py --prompt "soft pop, short, bright" --duration 1 --out pop.mp3 --confirm-cost

Order for any sound effect: 1 own files (assets/sound-effects) > 2 free sources (Mixkit, myinstants) > 3 THIS (generate; drawing does not apply to sound)
> 4 buying, last. Estimate first, ask the user, then run with --confirm-cost (without it nothing is generated and nothing is charged).
Cost: ElevenLabs bills credits for sound generation (roughly 40 credits per second of the duration you ask for; check your plan). Key: ELEVENLABS_API_KEY in .env.
Afterwards the new sound still needs a row in _system/sound-guide/SOUND-GUIDE.md (CLAUDE.md "Sound effects: Claude cannot hear") before it is used.
"""
from __future__ import annotations
import _starter  # noqa: F401,E402
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

EL_API = "https://api.elevenlabs.io/v1/sound-generation"
CREDITS_PER_SECOND = 40


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prompt", required=True, help="what the sound is, in English (for example: soft UI pop, short, bright)")
    ap.add_argument("--duration", type=float, default=1.0, help="seconds, 0.5-30 (default 1)")
    ap.add_argument("--out", required=True, help="output file (.mp3); save it in assets/sound-effects/ to keep it")
    ap.add_argument("--estimate-only", action="store_true")
    ap.add_argument("--confirm-cost", action="store_true", help="REQUIRED to generate (it uses credits that cost money)")
    a = ap.parse_args()

    from find_assets import load_env, _utf8_stdio
    _utf8_stdio()
    load_env()
    dur = max(0.5, min(30.0, a.duration))
    est = {"credits": int(dur * CREDITS_PER_SECOND), "note": "rough; ElevenLabs bills credits, check your plan's price per credit"}
    if a.estimate_only:
        print(json.dumps({"ok": True, "estimate": est, "charged": False}))
        return
    if not a.confirm_cost:
        print(json.dumps({"ok": False, "needs_confirmation": True, "estimate": est, "charged": False,
                          "message_en": "Sound generation uses paid credits. Show the user the estimate, then re-run with --confirm-cost."}))
        sys.exit(2)
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        print(json.dumps({"ok": False, "error": "no ELEVENLABS_API_KEY in .env", "charged": False}))
        sys.exit(1)
    out = os.path.expanduser(a.out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    req = urllib.request.Request(EL_API + "?output_format=mp3_44100_128",
                                 data=json.dumps({"text": a.prompt, "duration_seconds": dur}).encode(),
                                 headers={"xi-api-key": key, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            Path(out).write_bytes(r.read())
    except urllib.error.HTTPError as e:
        print(json.dumps({"ok": False, "http": e.code, "error": e.read().decode(errors="ignore")[:300], "charged": False}))
        sys.exit(1)
    print(json.dumps({"ok": True, "file": out, "bytes": os.path.getsize(out), "estimate": est,
                      "license": {"tier": "generated", "note": "AI-generated; commercial use depends on your ElevenLabs plan"},
                      "next": "add a row for it in _system/sound-guide/SOUND-GUIDE.md before using it"}))


if __name__ == "__main__":
    main()
