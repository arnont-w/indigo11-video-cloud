#!/usr/bin/env python3
"""detect_motion.py - local, free MOTION / freeze timeline for indigo11-analyze.

Why this exists
---------------
Sparse keyframe sampling (scene boundaries + start/mid/end) is blind to motion
that happens INSIDE a shot: a hand gesture, a product reveal, a head turn - and
to the opposite, a B-roll shot that's frozen/dead. Knowing "where is this clip
busy vs still" helps the agent place cuts on motion beats and flag dead air that
isn't silence (a held shot with sound).

This needs ZERO new dependencies - it's pure ffmpeg (already required by the kit):
  - `scdet` emits per-frame `lavfi.scd.mafd` (mean absolute frame difference) - 
    a cheap 0..~50 motion proxy for every frame, not just at cuts.
  - `freezedetect` flags spans where the picture is essentially static.

We bin mafd into a per-second motion_score[] (0..1 normalized) and surface
freeze spans. Deterministic plumbing (rule #18): reports the timeline; the agent
decides what the beats mean.

Usage
  python3 detect_motion.py <video> [--bin-ms 1000] [--freeze-noise 0.003]
                                   [--freeze-duration 0.5] [--output motion.json]

Output (stdout JSON + optional --output):
  {"ok": true,
   "motion_score": [{"t_ms": 0, "score": 0.02}, {"t_ms": 1000, "score": 0.78}, ...],
   "motion_peaks_ms": [3000, 7000], # local maxima - candidate gesture/reveal beats
   "freeze_spans_ms": [{"start_ms": 12000, "end_ms": 14000}],
   "bin_ms": 1000}
"""
from __future__ import annotations
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def run_scdet(video: str) -> list:
    """Return [(t_ms, mafd), ...] per frame via ffmpeg scdet metadata."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "info", "-i", video,
           "-vf", "scdet=s=0:t=14,metadata=print", "-an", "-f", "null", "-"]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = []
    cur_t = None
    for line in res.stderr.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            cur_t = int(float(m.group(1)) * 1000)
        m2 = re.search(r"lavfi\.scd\.mafd=([\d.]+)", line)
        if m2 and cur_t is not None:
            out.append((cur_t, float(m2.group(1))))
    return out


def run_freezedetect(video: str, noise: float, duration: float) -> list:
    """Return freeze spans [{start_ms, end_ms}] via ffmpeg freezedetect."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "info", "-i", video,
           "-vf", f"freezedetect=n={noise}:d={duration}", "-an", "-f", "null", "-"]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    spans = []
    start = None
    for line in res.stderr.splitlines():
        m = re.search(r"freeze_start:\s*([\d.]+)", line)
        if m:
            start = int(float(m.group(1)) * 1000)
        m2 = re.search(r"freeze_end:\s*([\d.]+)", line)
        if m2 and start is not None:
            spans.append({"start_ms": start, "end_ms": int(float(m2.group(1)) * 1000)})
            start = None
    return spans


def bin_motion(per_frame: list, bin_ms: int) -> list:
    """Average mafd per time bin, normalized 0..1 across the clip."""
    if not per_frame:
        return []
    bins = {}
    for t_ms, mafd in per_frame:
        b = (t_ms // bin_ms) * bin_ms
        bins.setdefault(b, []).append(mafd)
    raw = [(b, sum(v) / len(v)) for b, v in sorted(bins.items())]
    peak = max((v for _, v in raw), default=0.0) or 1.0
    return [{"t_ms": b, "score": round(v / peak, 3)} for b, v in raw]


def find_peaks(scores: list, min_score: float = 0.45) -> list:
    """Local maxima above min_score - candidate motion beats (gesture/reveal)."""
    peaks = []
    for i in range(1, len(scores) - 1):
        s = scores[i]["score"]
        if s >= min_score and s >= scores[i - 1]["score"] and s >= scores[i + 1]["score"]:
            peaks.append(scores[i]["t_ms"])
    # also catch a strong first/last bin
    if scores and scores[0]["score"] >= min_score and (len(scores) == 1 or scores[0]["score"] >= scores[1]["score"]):
        peaks.insert(0, scores[0]["t_ms"])
    return sorted(set(peaks))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--bin-ms", type=int, default=1000,
                    help="Motion-score time bin (default 1000 = per second)")
    ap.add_argument("--freeze-noise", type=float, default=0.003,
                    help="freezedetect noise tolerance (default 0.003)")
    ap.add_argument("--freeze-duration", type=float, default=0.5,
                    help="Min freeze span seconds to flag (default 0.5)")
    ap.add_argument("--output", help="Also write motion JSON here")
    args = ap.parse_args()

    if not Path(args.video).exists():
        print(json.dumps({"ok": False, "reason": f"video not found: {args.video}"}))
        return

    per_frame = run_scdet(args.video)
    motion_score = bin_motion(per_frame, args.bin_ms)
    peaks = find_peaks(motion_score)
    freezes = run_freezedetect(args.video, args.freeze_noise, args.freeze_duration)

    result = {
        "ok": True,
        "engine": "ffmpeg-scdet",
        "bin_ms": args.bin_ms,
        "motion_score": motion_score,
        "motion_peaks_ms": peaks,
        "freeze_spans_ms": freezes,
    }
    if args.output:
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
