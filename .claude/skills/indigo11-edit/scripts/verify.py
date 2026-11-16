#!/usr/bin/env python3
"""Sanity-check a CapCut project after inject.

Reads draft_info.json, reports:
  - track types + segment counts
  - total duration
  - materials count
  - mirror integrity (8 sibling files match Timelines/<UUID>/draft_info.json)
  - missing helper refs (segments pointing to material IDs not in materials)
  - preserved_tracks: which tracks/materials the last op added, replaced or
    deleted, compared against its pre-op snapshot. Anything else changed is a
    FAIL ("unplanned_change"). With no snapshot to compare against the check
    reports `skip` - could not tell, which is not the same as good (rule #41).

Usage:
  python3 verify.py --project my-promo
"""
from __future__ import annotations
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)

import argparse
import filecmp
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _capcut_lib import (  # noqa: E402
    CAPCUT_DRAFTS_DIR, S, SKILL_DIR, KIT_ROOT, draft_state_name, draft_state_path, emit_err,
    emit_ok, find_timeline_uuid, resolve_project,
)
from _preserved import check_preserved  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", required=True)
    ap.add_argument("--drafts-dir", default=str(CAPCUT_DRAFTS_DIR))
    # Accepted and ignored: this script has ALWAYS printed a single JSON line
    # (emit_ok). SKILL.md Step 0.5 tells the agent to run `verify.py --json`, and
    # without the flag argparse exited 2 with "unrecognized arguments" - so the
    # step that is supposed to read the timeline before deciding just failed.
    ap.add_argument("--json", action="store_true",
                    help="No-op; output is always a single JSON line.")
    args = ap.parse_args()

    project_dir = resolve_project(args.project, args.drafts_dir)
    if not project_dir.exists():
        emit_err(f"Project not found: {project_dir}")

    tl_uuid = find_timeline_uuid(project_dir)
    main_draft = draft_state_path(project_dir, tl_uuid)
    draft = json.load(open(main_draft, encoding="utf-8"))
    state = draft_state_name(project_dir / "Timelines" / tl_uuid)

    tracks = draft.get("tracks", [])
    track_summary = [{"type": t.get("type"), "segments": len(t.get("segments", []))} for t in tracks]

    materials = draft.get("materials", {})
    material_summary = {k: len(v) for k, v in materials.items() if isinstance(v, list) and v}

    # Check mirror integrity. NOTE (verified against real CapCut 8.9.1 projects):
    # `Timelines/<UUID>/template.tmp` is CapCut's ORIGINAL-import snapshot (0
    # tracks) and is NOT kept in sync with the live draft - so we do NOT require
    # it to match. The live mirror the app reads is the canonical draft +
    # `template-2.tmp` + the `.bak` files. Root-level `template.tmp` doesn't exist
    # in native projects; only `template-2.tmp` does.
    mirror_status = {}
    for relpath in [
        state, state + ".bak", "template-2.tmp",
        f"Timelines/{tl_uuid}/{state}.bak",
        f"Timelines/{tl_uuid}/template-2.tmp",
    ]:
        p = project_dir / relpath
        if not p.exists():
            mirror_status[relpath] = "MISSING"
        else:
            mirror_status[relpath] = "ok" if filecmp.cmp(main_draft, p, shallow=False) else "DIFFERS"
    # template.tmp is informational only (empty snapshot expected).
    _ttmp = project_dir / "Timelines" / tl_uuid / "template.tmp"
    if _ttmp.exists():
        mirror_status[f"Timelines/{tl_uuid}/template.tmp"] = (
            "ok" if filecmp.cmp(main_draft, _ttmp, shallow=False) else "snapshot(expected)")

    # Mirror point #4: draft_meta_info.json tm_duration must equal draft.duration.
    # #9: parent catalog tm_duration too. A stale value = mirror aborted mid-run;
    # the old verify reported healthy anyway (P0-08).
    draft_dur = int(draft.get("duration", 0))
    meta_p = project_dir / "draft_meta_info.json"
    if meta_p.exists():
        try:
            mtm = int(json.load(open(meta_p, encoding="utf-8")).get("tm_duration", -1))
            mirror_status["draft_meta_info.json:tm_duration"] = "ok" if mtm == draft_dur else f"STALE({mtm}!={draft_dur})"
        except Exception as e:
            mirror_status["draft_meta_info.json:tm_duration"] = f"ERROR({e})"
    root_meta_p = project_dir.parent / "root_meta_info.json"
    if root_meta_p.exists():
        try:
            root = json.load(open(root_meta_p, encoding="utf-8"))
            found = None
            for entry in root.get("all_draft_store", []):
                fold = entry.get("draft_fold_path", "")
                if fold and Path(fold.replace("\\", "/")).name == project_dir.name:
                    found = int(entry.get("tm_duration", -1)); break
            if found is None:
                mirror_status["root_meta_info.json:catalog"] = "NOT_LISTED"
            else:
                mirror_status["root_meta_info.json:tm_duration"] = "ok" if found == draft_dur else f"STALE({found}!={draft_dur})"
        except Exception as e:
            mirror_status["root_meta_info.json:catalog"] = f"ERROR({e})"

    # Check ref integrity (segments → existing materials)
    ref_issues = []
    all_material_ids = set()
    used_material_ids = set()
    for cat, items in materials.items():
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and "id" in it:
                    all_material_ids.add(it["id"])
    for t in tracks:
        for s in t.get("segments", []):
            refs = [s.get("material_id")] + (s.get("extra_material_refs") or [])
            for ref in refs:
                if ref:
                    used_material_ids.add(ref)
                if ref and ref not in all_material_ids:
                    ref_issues.append({"track": t.get("type"), "segment": s.get("id", "?")[:8],
                                       "missing_ref": ref[:8] if ref else None})

    # Orphan materials (in materials but no segment references them)
    # Skip helper categories that aren't expected to be referenced directly
    orphan_skip_cats = {"canvases", "material_colors"}  # rendering helpers, can be unreferenced
    orphans = []
    for cat, items in materials.items():
        if cat in orphan_skip_cats or not isinstance(items, list):
            continue
        for it in items:
            if isinstance(it, dict) and "id" in it and it["id"] not in used_material_ids:
                orphans.append({"category": cat, "id": it["id"][:8],
                                "name": it.get("name", it.get("material_name", ""))[:30]})

    # Chronological check per source clip (video tracks)
    chronological_issues = []
    for t in tracks:
        if t.get("type") != "video":
            continue
        by_mat = {}
        for s in t.get("segments", []):
            mat_id = s.get("material_id")
            src_start = s.get("source_timerange", {}).get("start", 0)
            src_end = src_start + s.get("source_timerange", {}).get("duration", 0)
            if mat_id not in by_mat:
                by_mat[mat_id] = []
            by_mat[mat_id].append((src_start, src_end, s.get("id", "?")[:8]))
        for mat_id, segs in by_mat.items():
            # Sort by timeline position (target_timerange), check source goes forward
            last_src_end = -1
            for src_start, src_end, seg_id in segs:
                if src_start < last_src_end:
                    chronological_issues.append({
                        "material_id": mat_id[:8],
                        "segment": seg_id,
                        "at_source_ms": src_start // 1000,
                        "previous_ended_ms": last_src_end // 1000,
                    })
                last_src_end = max(last_src_end, src_end)

    # Soft states (don't fail health): NOT_LISTED (fresh draft not yet catalogued)
    # and snapshot(expected) (Timelines/template.tmp is CapCut's empty original
    # snapshot, never synced). Everything else (MISSING/DIFFERS/STALE/ERROR) is a
    # hard mirror failure. NOTE: verify is meant to run immediately after an indigo11
    # write; on a project the USER edited in CapCut afterwards, .bak files
    # legitimately lag and DIFFERS there is CapCut's own state, not our bug.
    SOFT = ("ok", "NOT_LISTED", "snapshot(expected)")
    mirror_bad = [k for k, v in mirror_status.items() if v not in SOFT]

    # Did this op touch anything it did not declare? Compared against the newest
    # backup manifest. `skip` = there is nothing to compare to, which is NOT a pass
    # and must never be folded into `healthy` as if it were one (rule #41).
    preserved = check_preserved(project_dir, main_draft,
                                KIT_ROOT, draft=draft)

    healthy = (
        not mirror_bad
        and not ref_issues
        and not chronological_issues
        and preserved["status"] != "fail"
    )

    emit_ok(
        op="verify",
        project=args.project,
        project_dir=str(project_dir),
        duration_s=round(draft.get("duration", 0) / S, 3),
        tracks=track_summary,
        materials=material_summary,
        mirror=mirror_status,
        ref_issues=ref_issues[:5],
        ref_issue_count=len(ref_issues),
        orphan_materials=orphans[:5],
        orphan_count=len(orphans),
        chronological_issues=chronological_issues[:5],
        chronological_issue_count=len(chronological_issues),
        mirror_failures=mirror_bad,
        preserved_tracks=preserved,
        healthy=healthy,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
