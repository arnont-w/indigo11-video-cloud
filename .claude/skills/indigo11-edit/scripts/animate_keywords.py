#!/usr/bin/env python3
"""animate_keywords.py - give every key-message highlight box its "in" animation (default: Tiny Jump).

  python3 animate_keywords.py --project "1109 (1)" [--animation "Tiny Jump"] [--source fxlib] [--dry-run] [--force-kill]

Why: my-rules/sound-and-music.md says every keyword box uses the CapCut text animation "Tiny Jump" plus the pop sound.
add_keywords.py runs this for you (and sync_keyword_sfx.py adds the pop); run it alone to repair a project.

How (the same version-proof way as capcut_apply_resource.py): CapCut's animations come from an online library whose ids
change, so nothing is hard-coded. The animation is COPIED from the user's own library project (default "fxlib", where
they added it once): its material_animations entry is found by name or resource id and put on every box of the
track indigo11-keyword-highlight. Nothing else in the project changes.
Safe like the other ops: CapCut closed (or --force-kill), backup first, nothing outside the plan may change, undo with restore_project.py.
"""
from __future__ import annotations
import _starter  # noqa: F401,E402
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _capcut_lib import (  # noqa: E402
    CAPCUT_DRAFTS_DIR, ensure_capcut_closed, new_uuid, deep_copy, emit_ok, emit_err,
    load_draft, mirror_9_places, atomic_write_text, begin_op, assert_preserved, material_refcount,
)


def find_animation(src_draft, name, rid):
    for m in (src_draft.get("materials") or {}).get("material_animations", []):
        for an in m.get("animations") or []:
            if (rid and str(an.get("resource_id")) == str(rid)) or (name and an.get("name", "").lower() == name.lower()):
                return m
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--animation", default="Tiny Jump", help="animation name in the library project (default Tiny Jump)")
    ap.add_argument("--resource-id", default="7563492361758690577", help="its resource id (default: Tiny Jump); the name is tried too")
    ap.add_argument("--source", default="fxlib", help="library project to copy the animation from (default fxlib)")
    ap.add_argument("--highlight-track", default="indigo11-keyword-highlight")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--drafts-dir")
    ap.add_argument("--force-kill", action="store_true")
    a = ap.parse_args()

    drafts = Path(a.drafts_dir) if a.drafts_dir else CAPCUT_DRAFTS_DIR
    project_dir = drafts / a.project
    src_dir = drafts / a.source
    if not project_dir.exists():
        emit_err(op="animate-keywords", error=f"Project not found: {project_dir}")
    if not src_dir.exists():
        emit_err(op="animate-keywords", error=f"Library project '{a.source}' not found: add the animation once in a CapCut project named {a.source}, then run this again")
    if a.dry_run:
        close_detail = "dry-run"
    else:
        ok, close_detail = ensure_capcut_closed(a.force_kill)
        if not ok:
            emit_err(op="animate-keywords", error=close_detail)

    src, _p, _t = load_draft(src_dir)
    tpl = find_animation(src, a.animation, a.resource_id)
    if not tpl:
        emit_err(op="animate-keywords", error=f"animation '{a.animation}' not found in '{a.source}': add it to a text there once (CapCut > Text > Animation > In)")
    path = (tpl["animations"][0] or {}).get("path", "")
    warn = None if (not path or os.path.exists(path)) else "the animation's cache file is missing on this computer: CapCut will download it when the project opens"

    draft, draft_path, _tl = load_draft(project_dir)
    before = deep_copy(draft)
    tracks = draft.get("tracks") or []
    hl = next((t for t in tracks if t.get("type") == "text" and t.get("name") == a.highlight_track), None)
    if not hl or not hl.get("segments"):
        emit_err(op="animate-keywords", error=f"no highlight track '{a.highlight_track}' with boxes: run add_keywords.py first")

    mats = draft["materials"]
    anims = mats.setdefault("material_animations", [])
    by_id = {m.get("id"): m for m in anims}
    old_ids, new_ids = [], []
    for seg in hl["segments"]:
        refs = list(seg.get("extra_material_refs") or [])
        idx = next((i for i, r in enumerate(refs) if r in by_id), None)
        new = deep_copy(tpl)
        new["id"] = new_uuid()
        dur = min(500_000, int((seg.get("target_timerange") or {}).get("duration", 500_000)) // 2)
        for an in new.get("animations") or []:
            an["duration"] = dur
        anims.append(new)
        new_ids.append(new["id"])
        if idx is None:
            refs.append(new["id"])
        else:
            old_ids.append(refs[idx]); refs[idx] = new["id"]
        seg["extra_material_refs"] = refs
    gone = [m for m in old_ids if material_refcount(draft, m, exclude_tracks=(hl["id"],)) == 0]
    mats["material_animations"] = [m for m in anims if m.get("id") not in set(gone)]

    report = {"boxes": len(hl["segments"]), "animation": a.animation, "from": a.source}
    if a.dry_run:
        emit_ok(op="animate-keywords", dry_run=True, plan=report, warning=warn)
        return
    plan = {"add": {"tracks": [], "materials": new_ids},
            "replace": {"tracks": [hl["id"]]},
            "delete": {"segments": [], "materials": gone}}
    try:
        assert_preserved(before, draft, plan)
    except ValueError as e:
        emit_err(op="animate-keywords", error=str(e))
    with begin_op(project_dir, "animate-keywords", plan=plan) as op:
        atomic_write_text(draft_path, json.dumps(draft, indent=2, ensure_ascii=False))
        mirror = mirror_9_places(project_dir)
        op.check_mirror(mirror)
    emit_ok(op="animate-keywords", project=a.project, **report, warning=warn, capcut_close=close_detail,
            mirror_paths=len(mirror.get("copies", [])), **op.info())


if __name__ == "__main__":
    main()
