#!/usr/bin/env python3
"""tidy_capcut.py - repair a CapCut project made BEFORE the track / motion fixes.

  python3 tidy_capcut.py --project "1109 (1)" [--sfx] [--motion] [--dry-run] [--strength 1.0] [--force-kill]

--sfx     Gather the stray one-sound audio tracks (an audio track holding ONE short segment, 5 s or less, with no name or the
          name indigo11-sfx) onto ONE shared track named indigo11-sfx. Sounds that overlap are shortened (the earlier one ends
          where the next starts); a sound that would end up under 80 ms goes on a second shared track. The voice-over, music and
          every other track are never touched. No sound is deleted.
--motion  Give every STATIC image overlay (a photo on a video track above the main video, with no keyframes yet) real CapCut
          keyframes: full-frame images get a slow push-in with soft fades, smaller ones pop in and keep gentle motion.
Neither flag = both. --dry-run only prints the plan and writes nothing.

Safe like the other ops: CapCut must be closed (or --force-kill), a backup is made first, the write is checked so nothing
outside the plan changed, and `restore_project.py --project P --list` / `--op <id>` puts it back.
"""
from __future__ import annotations
import _starter  # noqa: F401,E402
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _capcut_lib import (  # noqa: E402
    CAPCUT_DRAFTS_DIR, ensure_capcut_closed, new_uuid, deep_copy, emit_ok, emit_err,
    load_draft, mirror_9_places, atomic_write_text, begin_op, assert_preserved,
)
from add_overlay import motion_keyframes  # noqa: E402

MIN_US = 80_000
SHORT_US = 5_000_000


def _st(s):
    tr = s.get("target_timerange") or {}
    return int(tr.get("start", 0)), int(tr.get("duration", 0))


def plan_sfx(draft):
    """-> (old_track_ids, lanes) : lanes = list of segment lists, each lane = one new track. Segments are modified in place."""
    old = []
    segs = []
    for t in draft.get("tracks") or []:
        if t.get("type") != "audio":
            continue
        ss = t.get("segments") or []
        if t.get("name") == "indigo11-sfx" or (not t.get("name") and len(ss) == 1 and _st(ss[0])[1] <= SHORT_US):
            old.append(t["id"]); segs += ss
    if len(old) < 2:          # one sound track (or none) is already tidy
        return [], []
    segs.sort(key=lambda s: _st(s)[0])
    lanes = []
    for s in segs:
        a, d = _st(s)
        placed = False
        for lane in lanes:
            la, ld = _st(lane[-1])
            if a >= la + ld:
                lane.append(s); placed = True; break
            if la < a and a - la >= MIN_US:     # shorten the earlier sound so this one can follow on the same track
                lane[-1]["target_timerange"]["duration"] = a - la
                sr = lane[-1].get("source_timerange")
                if sr: sr["duration"] = min(int(sr.get("duration", a - la)), a - la)
                lane.append(s); placed = True; break
        if not placed:
            lanes.append([s])
    return old, lanes


def plan_motion(draft, strength):
    """-> list of (track, segment, motion_name). Static photo segments on video tracks above the main video."""
    photos = {m["id"] for m in (draft.get("materials") or {}).get("videos", []) if m.get("type") == "photo"}
    out = []
    first_video = next((t for t in draft.get("tracks") or [] if t.get("type") == "video"), None)
    for t in draft.get("tracks") or []:
        if t.get("type") != "video" or t is first_video:
            continue
        for s in t.get("segments") or []:
            if s.get("material_id") in photos and not s.get("common_keyframes") and _st(s)[1] >= 300_000:
                c = s.get("clip") or {}
                sc = float((c.get("scale") or {}).get("x", 1.0))
                out.append((t, s, "push" if sc >= 0.85 else "pop+float"))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--sfx", action="store_true")
    ap.add_argument("--motion", action="store_true")
    ap.add_argument("--strength", type=float, default=1.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--drafts-dir")
    ap.add_argument("--force-kill", action="store_true")
    args = ap.parse_args()
    do_sfx = args.sfx or not args.motion
    do_motion = args.motion or not args.sfx

    drafts = Path(args.drafts_dir) if args.drafts_dir else CAPCUT_DRAFTS_DIR
    project_dir = drafts / args.project
    if not project_dir.exists():
        emit_err(op="tidy-capcut", error=f"Project not found: {project_dir}")
    if not args.dry_run:
        ok, close_detail = ensure_capcut_closed(args.force_kill)
        if not ok:
            emit_err(op="tidy-capcut", error=close_detail)
    else:
        close_detail = "dry-run"

    draft, draft_path, _tl = load_draft(project_dir)
    before = deep_copy(draft)

    replace_ids, add_ids, report = [], [], {}
    if do_sfx:
        old_ids, lanes = plan_sfx(draft)
        if old_ids:
            for lane in lanes:
                nt = {"attribute": 0, "flag": 0, "id": new_uuid(), "is_default_name": False,
                      "name": "indigo11-sfx", "segments": lane, "type": "audio"}
                add_ids.append(nt["id"])
                draft["tracks"].append(nt)
            gone = set(old_ids)
            draft["tracks"] = [t for t in draft["tracks"] if t.get("id") not in gone]
            replace_ids += old_ids
            report["sfx"] = {"audio_tracks_before": len(old_ids), "audio_tracks_after": len(lanes),
                             "sounds": sum(len(l) for l in lanes)}
    if do_motion:
        todo = plan_motion(draft, args.strength)
        touched = []
        det = []
        for tr_, s, mot in todo:
            c = s.get("clip") or {}
            sc = float((c.get("scale") or {}).get("x", 1.0))
            tr = c.get("transform") or {}
            kfs = motion_keyframes(mot, _st(s)[1] // 1000, sc, float(tr.get("x", 0.0)), float(tr.get("y", 0.0)),
                                   float(c.get("rotation", 0.0)), float(c.get("alpha", 1.0)), args.strength)
            if kfs:
                s["common_keyframes"] = kfs
                s.setdefault("keyframe_refs", [])
                det.append({"at_s": round(_st(s)[0] / 1e6, 2), "motion": mot})
                if tr_["id"] not in touched: touched.append(tr_["id"])
        if det:
            replace_ids += [i for i in touched if i not in replace_ids]
            report["motion"] = {"images": len(det), "details": det}

    if not report:
        emit_ok(op="tidy-capcut", project=args.project, changed=False,
                note="nothing to repair: no stray sound tracks and no static image overlays")
        return
    if args.dry_run:
        emit_ok(op="tidy-capcut", project=args.project, dry_run=True, plan=report)
        return

    plan = {"add": {"tracks": add_ids, "materials": []},
            "replace": {"tracks": replace_ids},
            "delete": {"segments": [], "materials": []}}
    try:
        assert_preserved(before, draft, plan)
    except ValueError as e:
        emit_err(op="tidy-capcut", error=str(e))

    with begin_op(project_dir, "tidy-capcut", plan=plan) as op:
        atomic_write_text(draft_path, json.dumps(draft, indent=2, ensure_ascii=False))
        mirror = mirror_9_places(project_dir)
        op.check_mirror(mirror)

    emit_ok(op="tidy-capcut", project=args.project, repaired=report, mirror_paths=len(mirror.get("copies", [])),
            capcut_close=close_detail,
            next_step="เปิด CapCut → " + args.project + " → ดู audio track (เสียงรวมอยู่ track เดียว) และ overlay รูป (มี keyframe)",
            **op.info())


if __name__ == "__main__":
    main()
