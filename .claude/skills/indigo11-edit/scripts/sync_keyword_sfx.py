#!/usr/bin/env python3
"""sync_keyword_sfx.py - put ONE pop sound at the start of every key-message highlight box, and nothing else.

  python3 sync_keyword_sfx.py --project "1109 (1)" [--volume 0.56] [--dry-run] [--force-kill]

Use it after add_keywords.py (which calls it for you). It reads the start of every box on the highlight track
(indigo11-keyword-highlight) and then on the shared sound track (indigo11-sfx):
  - the pop sounds (name contains "Pop-up_tap", the keyword pop of my-rules/sound-and-music.md) are re-used:
    as many as there are boxes are MOVED to the box starts (volume 0.56 = -5 dB by default), the surplus ones are removed
    together with their own materials, and missing ones are copied from an existing pop;
  - every other sound on that track (whooshes, hits, anything else) is left exactly where it is;
  - the voice-over, music, captions and all other tracks are never touched.
If the project has no pop sound yet there is nothing to copy: apply one first with capcut_apply_resource.py.
Safe like the other ops: CapCut closed (or --force-kill), backup first, nothing outside the plan may change,
undo with restore_project.py.
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
    load_draft, mirror_9_places, atomic_write_text, begin_op, assert_preserved, material_refcount,
)

POP_KEY = "Pop-up_tap"
POP_US = 500_000


def _start(s):
    return int((s.get("target_timerange") or {}).get("start", 0))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--volume", type=float, default=0.56, help="pop volume (default 0.56 = -5 dB, the keyword-pop rule)")
    ap.add_argument("--highlight-track", default="indigo11-keyword-highlight")
    ap.add_argument("--sfx-track", default="indigo11-sfx")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--drafts-dir")
    ap.add_argument("--force-kill", action="store_true")
    a = ap.parse_args()

    drafts = Path(a.drafts_dir) if a.drafts_dir else CAPCUT_DRAFTS_DIR
    project_dir = drafts / a.project
    if not project_dir.exists():
        emit_err(op="sync-keyword-sfx", error=f"Project not found: {project_dir}")
    if a.dry_run:
        close_detail = "dry-run"
    else:
        ok, close_detail = ensure_capcut_closed(a.force_kill)
        if not ok:
            emit_err(op="sync-keyword-sfx", error=close_detail)

    draft, draft_path, _tl = load_draft(project_dir)
    before = deep_copy(draft)
    tracks = draft.get("tracks") or []
    hl = next((t for t in tracks if t.get("type") == "text" and t.get("name") == a.highlight_track), None)
    if not hl or not hl.get("segments"):
        emit_err(op="sync-keyword-sfx", error=f"no highlight track '{a.highlight_track}' with boxes: run add_keywords.py first")
    starts = sorted(_start(s) for s in hl["segments"])

    sfx = next((t for t in tracks if t.get("type") == "audio" and t.get("name") == a.sfx_track), None)
    if not sfx:
        emit_err(op="sync-keyword-sfx", error=f"no sound track '{a.sfx_track}' found: apply a pop sound with capcut_apply_resource.py first")
    names = {m["id"]: m.get("name", "") for m in (draft.get("materials") or {}).get("audios", [])}
    pops = sorted([s for s in sfx["segments"] if POP_KEY in names.get(s.get("material_id"), "")], key=_start)
    others = [s for s in sfx["segments"] if s not in pops]
    if not pops:
        emit_err(op="sync-keyword-sfx", error="no pop sound on the track to re-use: apply one first with capcut_apply_resource.py")

    keep = pops[:len(starts)]
    drop = pops[len(starts):]
    mats_all = (draft.get("materials") or {})

    def add_copy(src):
        """copy a pop segment with its own audio material and extra materials; returns the new segment"""
        ns = deep_copy(src)
        ns["id"] = new_uuid()
        idmap = {}
        for mid in [src["material_id"]] + list(src.get("extra_material_refs") or []):
            for cat, items in mats_all.items():
                if not isinstance(items, list): continue
                for it in list(items):
                    if isinstance(it, dict) and it.get("id") == mid:
                        c = deep_copy(it); c["id"] = new_uuid(); idmap[mid] = c["id"]; items.append(c)
                        added_mats.append(c["id"])
        ns["material_id"] = idmap.get(src["material_id"], src["material_id"])
        ns["extra_material_refs"] = [idmap.get(r, r) for r in (src.get("extra_material_refs") or [])]
        return ns

    added_mats = []
    placed = list(keep)
    while len(placed) < len(starts):
        placed.append(add_copy(pops[0]))
    for seg, st in zip(placed, starts):
        seg["target_timerange"] = {"start": st, "duration": POP_US}
        sr = seg.get("source_timerange") or {}
        sr["duration"] = min(int(sr.get("duration", POP_US)), POP_US) if sr else POP_US
        seg["source_timerange"] = sr
        seg["volume"] = a.volume
        seg["last_nonzero_volume"] = a.volume

    gone_segs = [s["id"] for s in drop]
    cand = set()
    for s in drop:
        cand.add(s.get("material_id"))
        cand.update(s.get("extra_material_refs") or [])
    cand.discard(None)
    gone_mats = [m for m in cand if material_refcount(draft, m, exclude_tracks=(sfx["id"],)) == 0
                 and not any(m == k.get("material_id") or m in (k.get("extra_material_refs") or []) for k in placed + others)]
    dm = set(gone_mats)
    for cat, items in list(mats_all.items()):
        if isinstance(items, list):
            mats_all[cat] = [i for i in items if not (isinstance(i, dict) and i.get("id") in dm)]
    sfx["segments"] = sorted(placed + others, key=_start)

    report = {"boxes": len(starts), "pops_moved": len(keep), "pops_added": len(placed) - len(keep),
              "pops_removed": len(drop), "other_sounds_kept": len(others), "volume": a.volume,
              "pop_at_s": [round(s / 1e6, 2) for s in starts]}
    if a.dry_run:
        emit_ok(op="sync-keyword-sfx", dry_run=True, plan=report)
        return

    plan = {"add": {"tracks": [], "materials": added_mats},
            "replace": {"tracks": [sfx["id"]]},
            "delete": {"segments": gone_segs, "materials": gone_mats}}
    try:
        assert_preserved(before, draft, plan)
    except ValueError as e:
        emit_err(op="sync-keyword-sfx", error=str(e))
    with begin_op(project_dir, "sync-keyword-sfx", plan=plan) as op:
        atomic_write_text(draft_path, json.dumps(draft, indent=2, ensure_ascii=False))
        mirror = mirror_9_places(project_dir)
        op.check_mirror(mirror)
    emit_ok(op="sync-keyword-sfx", project=a.project, **report, capcut_close=close_detail,
            mirror_paths=len(mirror.get("copies", [])), **op.info())


if __name__ == "__main__":
    main()
