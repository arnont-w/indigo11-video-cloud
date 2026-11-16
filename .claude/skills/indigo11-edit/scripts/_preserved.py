#!/usr/bin/env python3
"""Compare a project's CURRENT draft against the last op's snapshot.

`assert_preserved()` in _capcut_lib.py stops an op writing something it did not
declare. This is the read-back half: AFTER the write, does the draft on disk
differ from the pre-op snapshot in only the ways that op was supposed to change?
It answers a question the buyer actually asks - "did the kit touch my other
tracks?" - which structural checks (refs resolve, mirror matches) cannot.

Used by verify.py and qc_edit.py. Deterministic, no judgment (rule #18).

Three outcomes, and the difference between the last two matters (rule #41):
  ok       every difference is a change the op DECLARED in its plan
  fail     something else changed -> "unplanned_change"
  skip     there is no backup manifest to compare against, or the manifest carries
           no plan - the check COULD NOT RUN. This is NOT a pass. A project the
           buyer has never run a kit op on has nothing to compare to, and reporting
           that as "verified good" is the exact failure the kit's
           verified/could-not-tell split exists to stop.

Why the plan is the whole mechanism
-----------------------------------
Until 2026-09-18 this file counted a deleted or replaced track as `ok` merely by
REPORTING it. Deleting the buyer's text track and its material therefore passed -
the same shape as the refund case. A diff alone cannot tell "the op replaced the
caption track it owns" from "the op wiped the buyer's titles"; only the op's own
declaration can. So `begin_op(..., plan=...)` records what the op intended, and
anything deleted or replaced that the plan does not name is a FAIL. An op that
recorded no plan (a pre-fix manifest) is reported as unverifiable, never a pass.
"""
from __future__ import annotations
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _capcut_lib import op_seq  # noqa: E402

SKIP_UNKNOWN = "no_backup_manifest"
SKIP_NO_PLAN = "no_plan_in_manifest"


def _index_tracks(draft: dict) -> dict:
    return {t.get("id"): t for t in (draft.get("tracks") or []) if isinstance(t, dict)}


def _index_materials(draft: dict) -> dict:
    """{material_id: (category, entry)} across every materials.* list."""
    out = {}
    for cat, items in (draft.get("materials") or {}).items():
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and it.get("id"):
                    out[it["id"]] = (cat, it)
    return out


def _canon(x, key: str = ""):
    """The draft as the BUYER would see it, so CapCut's own re-save is not a change.

    Opening a project in CapCut after a kit op rewrote untouched video segments by a
    microsecond (266667 -> 266666 us) and filled a material's empty `unique_id`; the
    read-back then reported the buyer's own cut as overwritten. Timeranges are
    compared to the millisecond and a `unique_id` CapCut fills in is not content."""
    if isinstance(x, dict):
        if key.endswith("timerange") and {"start", "duration"} <= set(x):
            return {"start": round((x.get("start") or 0) / 1000),
                    "duration": round((x.get("duration") or 0) / 1000)}
        return {k: _canon(v, k) for k, v in x.items() if k != "unique_id"}
    if isinstance(x, list):
        return [_canon(v, key) for v in x]
    return x


def _same(a, b) -> bool:
    return json.dumps(_canon(a), sort_keys=True, ensure_ascii=False) == \
        json.dumps(_canon(b), sort_keys=True, ensure_ascii=False)


def _track_label(t: dict) -> str:
    return f"{t.get('type', '?')}[{len(t.get('segments') or [])} seg]"


def latest_manifest(project_dir: Path, kit_root: Path) -> dict | None:
    """The newest readable op manifest for this project, or None.

    Newest by ran_at then op_id - op ids are timestamp-prefixed, so the pair sorts
    chronologically even when two ops land inside the same second.
    """
    root = Path(kit_root) / "projects" / "_work" / "capcut" / Path(project_dir).name / "backups"
    if not root.is_dir():
        return None
    best = None
    for d in sorted(root.iterdir()):
        mp = d / "manifest.json"
        if not d.is_dir() or not mp.exists():
            continue
        try:
            m = json.loads(mp.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        m["_backup_dir"] = str(d)
        # seq, not (ran_at, op_id): several ops share a one-second ran_at and the
        # id's random suffix then ordered them arbitrarily, so the "latest"
        # manifest could be an EARLIER op and the read-back would compare against
        # the wrong snapshot.
        key = (m.get("seq") or op_seq(m.get("op_id") or ""), m.get("ran_at") or "")
        if best is None or key > best[0]:
            best = (key, m)
    return best[1] if best else None


def snapshot_draft(manifest: dict, draft_path: Path) -> dict | None:
    """Load the pre-op copy of `draft_path` out of the manifest's backup dir."""
    backup_dir = Path(manifest["_backup_dir"])
    target = str(Path(draft_path))
    for e in manifest.get("files", []):
        if str(Path(e.get("path", ""))) != target:
            continue
        if not e.get("existed_before") or not e.get("backup_rel"):
            return None
        p = backup_dir / e["backup_rel"]
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None
    return None


def diff_draft(before: dict, after: dict) -> dict:
    """What changed between two drafts, by track and by material.

    A track present in both but whose serialized content differs counts as
    `replaced` - that covers a track whose segments were re-cut in place, which is
    what inject_edl does to the video track.
    """
    tb, ta = _index_tracks(before), _index_tracks(after)
    mb, ma = _index_materials(before), _index_materials(after)

    added_tracks = [{"id": k, "track": _track_label(ta[k])} for k in ta if k not in tb]
    deleted_tracks = [{"id": k, "track": _track_label(tb[k])} for k in tb if k not in ta]
    replaced_tracks = []
    for k in ta:
        if k in tb and not _same(tb[k], ta[k]):
            replaced_tracks.append({
                "id": k, "track": _track_label(ta[k]),
                "segments_before": len(tb[k].get("segments") or []),
                "segments_after": len(ta[k].get("segments") or []),
            })

    added_materials = [{"id": k, "category": ma[k][0]} for k in ma if k not in mb]
    deleted_materials = [{"id": k, "category": mb[k][0]} for k in mb if k not in ma]
    replaced_materials = [
        {"id": k, "category": ma[k][0]} for k in ma
        if k in mb and not _same(mb[k][1], ma[k][1])
    ]

    # Everything outside tracks/materials that an op has no business changing.
    # duration and canvas_config are legitimately op-owned (inject_edl re-derives
    # the canvas from the injected media, rule #40), so they are reported as
    # context rather than counted as unplanned.
    other = {}
    for key in ("duration", "canvas_config"):
        if before.get(key) != after.get(key):
            other[key] = {"before": before.get(key), "after": after.get(key)}
    unplanned = []
    for key in sorted(set(before) | set(after)):
        if key in ("tracks", "materials", "duration", "canvas_config"):
            continue
        if before.get(key) != after.get(key):
            unplanned.append(key)

    return {
        "added_tracks": added_tracks,
        "replaced_tracks": replaced_tracks,
        "deleted_tracks": deleted_tracks,
        "added_materials": added_materials,
        "replaced_materials": replaced_materials,
        "deleted_materials": deleted_materials,
        "changed_other": other,
        "unplanned_keys": unplanned,
    }


def unplanned_changes(diff: dict, plan: dict | None) -> list:
    """Every destructive change the plan did not declare.

    Judged: deleted tracks, replaced tracks, deleted materials, and materials
    REWRITTEN IN PLACE. An added track or material is the op's own new work - it
    cannot take anything away from the buyer - and `unplanned_keys` already covers
    top-level keys nobody should touch.

    `replaced_materials` was collected by diff_draft and then ignored, so an op that
    rewrote a buyer's material without touching its id - their title's text, colour
    or font changed, every reference still resolving - reported `ok`. It is the
    refund case with the ids left intact: nothing is deleted, nothing is added, so
    every other bucket is empty and the diff looks clean. No op in the kit rewrites
    a material in place, so `replace.materials` is empty in every stored plan and
    any entry here is by definition undeclared.
    """
    plan = plan or {}
    planned_replace = set((plan.get("replace") or {}).get("tracks") or [])
    planned_rep_mats = set((plan.get("replace") or {}).get("materials") or [])
    planned_del_mats = set((plan.get("delete") or {}).get("materials") or [])
    # A track the op declared as deleted is legitimate too. assert_preserved's plan
    # shape has no delete.tracks bucket (a replace covers it), so accept either.
    planned_del_tracks = set((plan.get("delete") or {}).get("tracks") or [])

    out = []
    for t in diff.get("deleted_tracks") or []:
        if t.get("id") not in (planned_replace | planned_del_tracks):
            out.append({"kind": "deleted_track", "id": t.get("id"),
                        "track": t.get("track")})
    for t in diff.get("replaced_tracks") or []:
        if t.get("id") not in planned_replace:
            out.append({"kind": "replaced_track", "id": t.get("id"),
                        "track": t.get("track"),
                        "segments_before": t.get("segments_before"),
                        "segments_after": t.get("segments_after")})
    for m in diff.get("deleted_materials") or []:
        if m.get("id") not in planned_del_mats:
            out.append({"kind": "deleted_material", "id": m.get("id"),
                        "category": m.get("category")})
    # A material rewritten in place is legitimate ONLY when the op declared that id
    # in replace.materials. A replaced TRACK does not license it: the track's
    # contents are the op's business, its materials are not (a buyer's title can
    # share a text material with a segment on a track we own).
    for m in diff.get("replaced_materials") or []:
        if m.get("id") not in planned_rep_mats:
            out.append({"kind": "replaced_material", "id": m.get("id"),
                        "category": m.get("category")})
    return out


def check_preserved(project_dir: Path, draft_path: Path, kit_root: Path,
                    draft: dict | None = None) -> dict:
    """The preserved_tracks check. Returns a dict with `status` in ok/fail/skip.

    `status == "skip"` means the check could not run (no manifest, the manifest
    predates this draft file, or the op recorded no plan). Callers must NOT treat
    that as a pass.
    """
    manifest = latest_manifest(project_dir, kit_root)
    if manifest is None:
        return {"check": "preserved_tracks", "status": "skip", "reason": SKIP_UNKNOWN,
                "detail": ("ยังไม่มี backup ให้เทียบ - ตรวจไม่ได้ (no op snapshot to "
                           "compare against - check could not run, not a pass)")}
    before = snapshot_draft(manifest, draft_path)
    if before is None:
        return {"check": "preserved_tracks", "status": "skip", "reason": "no_draft_in_manifest",
                "detail": (f"op {manifest.get('op_id')} ไม่ได้ snapshot ไฟล์ draft นี้ - ตรวจไม่ได้ "
                           f"(that op did not snapshot this draft file - check could not run)"),
                "op_id": manifest.get("op_id")}
    entry = next((e for e in manifest.get("files", [])
                  if str(Path(e.get("path", ""))) == str(Path(draft_path))), {})
    if entry.get("after_sha256"):
        from _capcut_lib import sha256_file
        try:
            now = sha256_file(Path(draft_path))
        except OSError:
            now = None
        if now != entry["after_sha256"]:
            return {"check": "preserved_tracks", "status": "skip", "reason": "saved_since_op",
                    "op_id": manifest.get("op_id"),
                    "detail": (f"project ถูกเซฟใน CapCut หลัง op {manifest.get('op_id')} - "
                               f"สิ่งที่เปลี่ยนหลังจากนั้นแยกไม่ได้ว่าใครทำ (the project was saved in "
                               f"CapCut after that op, so later changes cannot be attributed to it; "
                               f"the op's own read-back ran when it wrote)")}
    if draft is None:
        try:
            draft = json.loads(Path(draft_path).read_text(encoding="utf-8"))
        except (ValueError, OSError) as ex:
            return {"check": "preserved_tracks", "status": "skip", "reason": "draft_unreadable",
                    "detail": f"อ่าน draft ไม่ได้ (could not read draft): {ex}"}

    d = diff_draft(before, draft)
    plan = manifest.get("plan")
    out = {
        "check": "preserved_tracks",
        "op_id": manifest.get("op_id"),
        "op": manifest.get("op"),
        "ran_at": manifest.get("ran_at"),
        "plan": plan,
    }
    out.update(d)

    # A manifest with no plan cannot be judged: every deleted track looks the same
    # as a declared one. Report that honestly instead of passing it.
    if plan is None:
        has_destructive = bool(d["deleted_tracks"] or d["replaced_tracks"]
                               or d["deleted_materials"] or d["replaced_materials"])
        out["status"] = "skip"
        out["reason"] = SKIP_NO_PLAN
        out["detail"] = (
            f"op {manifest.get('op_id')} ไม่ได้บันทึกแผนการแก้ไข - เทียบไม่ได้ว่าอะไรตั้งใจ "
            f"(that op recorded no change plan, so a declared replace cannot be told "
            f"apart from a wipe - check could not run, not a pass)")
        out["destructive_changes_seen"] = has_destructive
        return out

    unplanned = unplanned_changes(d, plan)
    status = "fail" if (d["unplanned_keys"] or unplanned) else "ok"
    out["status"] = status
    out["unplanned_changes"] = unplanned
    if status == "fail":
        out["reason"] = "unplanned_change"
        parts = []
        if unplanned:
            parts.append(", ".join(
                f"{u['kind']} {u['id']}" for u in unplanned[:6]))
        if d["unplanned_keys"]:
            parts.append("top-level: " + ", ".join(d["unplanned_keys"]))
        out["detail"] = (
            "op แก้/ลบสิ่งที่ไม่ได้อยู่ในแผน: " + " ; ".join(parts) +
            " (the op deleted or replaced things it did not declare - the buyer's "
            "own work may have been overwritten)")
    else:
        out["detail"] = (
            f"เทียบกับ op {manifest.get('op_id')}: "
            f"+{len(d['added_tracks'])} / ~{len(d['replaced_tracks'])} / "
            f"-{len(d['deleted_tracks'])} tracks, "
            f"+{len(d['added_materials'])} / ~{len(d['replaced_materials'])} / "
            f"-{len(d['deleted_materials'])} materials")
    return out
