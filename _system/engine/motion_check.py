#!/usr/bin/env python3
"""Does this video actually MOVE? Catches "a slideshow of still images with a voice-over on top".

    python3 _system/engine/motion_check.py video.mp4 [--top 0.62] [--fail-static 0.5] [--json]

How: the video is sampled at 6 frames per second, shrunk to 96 px and turned to grey. For every 1-second window the mean
brightness change between neighbouring samples is measured, in the TOP part of the picture only (default top 62%), so burned-in
captions at the bottom cannot make a still picture look alive. A window with almost no change is "still".

Verdict:  MOVING            fewer than 25% of the windows are still
          SOME STILL PARTS  25% to --fail-static (default 50%)
          STATIC            half the video or more is still: it is NOT finished, add motion (see _system/engine/animation-starter.html)
Exit code 0 = MOVING or SOME STILL PARTS, 1 = STATIC, 2 = could not analyse.
Windows: use `python` instead of `python3`.
"""
import json, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env  # noqa: F401,E402  (puts the runtime ffmpeg on PATH, UTF-8 console)

SW, SH, RATE = 96, 170, 6        # sample size (9:16) and sample rate
STILL_BELOW = 0.35               # mean abs change (0-255) under which a 1-second window counts as still


def main(av):
    if not av or av[0] in ("-h", "--help"): sys.exit(__doc__)
    f = av[0]; top = 0.62; fail = 0.5; as_json = "--json" in av
    for i, a in enumerate(av):
        if a == "--top" and i + 1 < len(av): top = float(av[i + 1])
        if a == "--fail-static" and i + 1 < len(av): fail = float(av[i + 1])
    if not Path(f).is_file(): print(f"not found: {f}"); sys.exit(2)
    cmd = ["ffmpeg", "-v", "error", "-i", f, "-an", "-vf", f"fps={RATE},scale={SW}:{SH}:flags=area,format=gray", "-f", "rawvideo", "-"]
    try: raw = subprocess.run(cmd, capture_output=True, timeout=1800).stdout
    except Exception as e: print("cannot run ffmpeg:", e); sys.exit(2)
    n = len(raw) // (SW * SH)
    if n < RATE * 2: print("video too short to judge"); sys.exit(2)
    rows = max(1, int(SH * top))
    frames = [raw[i * SW * SH:(i * SW * SH) + SW * rows] for i in range(n)]      # top part only
    diffs = [sum(abs(a - b) for a, b in zip(frames[i], frames[i + 1])) / len(frames[i]) for i in range(n - 1)]
    win = []
    for s in range(0, len(diffs) - RATE + 1, RATE):
        w = diffs[s:s + RATE]; win.append(sum(w) / len(w))
    still = [m < STILL_BELOW for m in win]
    share = sum(still) / len(still)
    # longest still stretch in seconds
    best = cur = 0
    for s in still:
        cur = cur + 1 if s else 0; best = max(best, cur)
    verdict = "STATIC" if share >= fail else ("SOME STILL PARTS" if share >= 0.25 else "MOVING")
    res = {"file": f, "seconds": len(win), "still_share": round(share, 2), "longest_still_s": best,
           "mean_change": round(sum(win) / len(win), 2), "verdict": verdict,
           "still_windows": [i for i, s in enumerate(still) if s][:40]}
    if as_json: print(json.dumps(res))
    else:
        print(f"{verdict}: {share:.0%} of {len(win)} one-second windows are still (longest still stretch {best} s, mean change {res['mean_change']}/255, top {top:.0%} of the picture)")
        if verdict == "STATIC": print("  -> this looks like still images with sound. Do not deliver: add entrances, constant motion and drawn-on effects.")
        elif verdict == "SOME STILL PARTS" and res["still_windows"]: print("  still at seconds: " + ", ".join(map(str, res["still_windows"])))
    sys.exit(1 if verdict == "STATIC" else 0)


if __name__ == "__main__":
    main(sys.argv[1:])
