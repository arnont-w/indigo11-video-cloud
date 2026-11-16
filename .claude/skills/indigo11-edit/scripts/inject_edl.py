#!/usr/bin/env python3
"""Inject a final EDL into a CapCut project as video timeline segments.

Reads:
  - <project>/draft_meta_info.json → buyer's Media panel assets
  - <project>/Timelines/<UUID>/draft_info.json → existing draft state
  - edl.json → segments to inject (raw timestamps in ms)

Writes:
  - <project>/Timelines/<UUID>/draft_info.json (patched)
  - 9-place mirror (edit-rules #6)

EDL shape (one of):
  {"segments": [{"clip_index": int, "source_start_ms": int, "source_end_ms": int}, ...]}
  {"segments": [{"clip_path": str, "source_start_ms": int, "source_end_ms": int}, ...]}

Timeline timing is computed by the script (cumulative cursor).

Optional per-segment keys (used by vo_assemble.py, voice-over-driven mode):
  "volume": 0.0     mute the clip's own sound (the voice-over carries the audio)
  "scale":  1.15    static punch-in zoom (clip.scale + uniform_scale)

Usage:
  python3 inject_edl.py --project my-promo --edl edl-final.json
"""
from __future__ import annotations
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _capcut_lib import ( # noqa: E402
    CAPCUT_DRAFTS_DIR, IS_WINDOWS, S, MS, assert_preserved, atomic_write_text,
    begin_op, deep_copy, draft_state_path, emit_err, emit_ok,
    ensure_capcut_closed, find_timeline_uuid, load_template, material_refcount,
    mirror_9_places, new_uuid, read_media_panel, resolve_project, write_state,
)


def main_video_track(draft: dict):
    """The A-roll track this op rebuilds: the FIRST video track in `tracks`.

    That is the one an earlier inject wrote (it inserts at index 0), and the one
    CapCut treats as the base layer. Everything above it - a PiP, an overlay, a
    logo or image track - is also type "video" but is NOT A-roll, and rebuilding
    "every video track" silently deleted those (the buyer's cutaway vanished with
    no warning, because the refusal counted its segments as if they were A-roll).
    """
    for t in (draft.get("tracks") or []):
        if t.get("type") == "video":
            return t
    return None


def count_video_segments(draft: dict) -> int:
    """Segments on the MAIN video track only - what this op would actually destroy.

    inject_edl REBUILDS that track from the EDL, so a non-zero count means the
    buyer (or an earlier op) already has cuts there that this run would destroy.
    That is the 2026-09-18 refund case: captions were requested on a hand-made cut
    and the timeline was rebuilt instead. Overlay/PiP tracks are deliberately NOT
    counted here - they survive the rebuild, so counting them would overstate the
    loss in the refusal message.
    """
    t = main_video_track(draft)
    return len(t.get("segments") or []) if t else 0


def other_video_tracks(draft: dict) -> list:
    """Video tracks this op must leave alone (overlay / PiP / image layers)."""
    vids = [t for t in (draft.get("tracks") or []) if t.get("type") == "video"]
    return vids[1:]


def read_existing_draft(project_dir: Path):
    """Load the timeline draft, tolerating the blank-project shapes (rule #23).

    A blank-but-saved project has a 0-byte timeline file that EXISTS but
    JSONDecodeErrors; here an empty timeline is EXPECTED (media lives in the
    Media panel), so missing/empty/malformed all read as {}.
    """
    try:
        tl_uuid = find_timeline_uuid(project_dir)
    except (FileNotFoundError, RuntimeError):
        return {}, None, None
    path = draft_state_path(project_dir, tl_uuid)
    try:
        draft = json.load(open(path, encoding="utf-8")) if path.exists() else {}
    except ValueError:
        draft = {}
    return draft, path, tl_uuid


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--edl", required=True, help="EDL JSON path")
    ap.add_argument("--drafts-dir", default=str(CAPCUT_DRAFTS_DIR))
    ap.add_argument("--replace-timeline", action="store_true",
                    help="Rebuild the video track even though the project already has "
                         "cuts on it. Destroys the existing arrangement - only pass this "
                         "after the buyer has confirmed.")
    ap.add_argument("--force-kill", action="store_true",
                    help="Kill a running CapCut instead of refusing. Unsaved work is lost.")
    args = ap.parse_args()

    project_dir = resolve_project(args.project, args.drafts_dir)
    if not project_dir.exists():
        emit_err(f"Project not found: {project_dir} (ไม่พบ project)")

    edl_path = Path(args.edl)
    if not edl_path.exists():
        emit_err(f"EDL not found: {edl_path}")

    edl = json.load(open(edl_path, encoding="utf-8"))
    segments = edl.get("segments", [])
    if not segments:
        emit_err("EDL has no segments")

    # The guard runs BEFORE ensure_capcut_closed and BEFORE begin_op: a refusal must
    # leave the machine exactly as it was - no killed helpers, no backup directory,
    # nothing written. Reading the draft here is safe (read-only).
    draft, draft_info_path, tl_uuid = read_existing_draft(project_dir)
    old_main = main_video_track(draft)
    old_main_id = old_main.get("id") if old_main else None
    existing_video_segments = count_video_segments(draft)
    kept_video_tracks = other_video_tracks(draft)
    kept_note = ""
    if kept_video_tracks:
        kept_note = (
            f" (video track อื่นอีก {len(kept_video_tracks)} track "
            f"[{', '.join(str(t.get('id')) for t in kept_video_tracks)}] "
            f"จะไม่ถูกแตะ / {len(kept_video_tracks)} other video track(s) are kept)")
    if existing_video_segments and not args.replace_timeline:
        emit_err(
            f"{args.project}: video track หลัก {old_main_id} มีคลิปอยู่แล้ว "
            f"{existing_video_segments} ท่อน - inject_edl จะสร้างทับเฉพาะ track นี้ "
            f"จึงไม่เขียนให้{kept_note} "
            f"(main video track {old_main_id} already has {existing_video_segments} "
            f"segment(s); inject_edl rebuilds THAT track only, so nothing was written)",
            main_video_track_id=old_main_id,
            existing_video_segments=existing_video_segments,
            kept_video_tracks=[{"id": t.get("id"),
                                "segments": len(t.get("segments") or [])}
                               for t in kept_video_tracks],
            options=[
                "ถ้าจะใส่ซับบนคัตที่ตัดไว้แล้ว: add_captions.py --from-timeline "
                "(captions on the existing cut - nothing on the timeline is touched)",
                f"ถ้าจะทิ้งคัตเดิมแล้วตัดใหม่จริงๆ: รันซ้ำด้วย --replace-timeline "
                f"(จะลบ {existing_video_segments} ท่อนบน track {old_main_id} - "
                f"rebuild that track from this EDL, deleting those "
                f"{existing_video_segments} segment(s) - only after the buyer confirms)",
            ],
        )

    ok, detail = ensure_capcut_closed(args.force_kill)
    if not ok:
        emit_err(detail)

    # Map clip identifiers to Media panel entries
    clips = read_media_panel(project_dir)
    by_index = {c["index"]: c for c in clips}
    by_path = {c["path"]: c for c in clips}
    by_name = {c["name"]: c for c in clips}

    # Resolve each EDL segment to a Media panel clip
    resolved = []
    for s in segments:
        clip = None
        if "clip_index" in s and s["clip_index"] in by_index:
            clip = by_index[s["clip_index"]]
        elif "clip_path" in s and s["clip_path"] in by_path:
            clip = by_path[s["clip_path"]]
        elif "clip_name" in s and s["clip_name"] in by_name:
            clip = by_name[s["clip_name"]]
        if not clip:
            emit_err(f"EDL segment refers to clip not in Media panel: {s}",
                     fix="ลาก clip นี้ลง Media panel ก่อน")
        resolved.append({"clip": clip, "src_start_ms": s["source_start_ms"],
                         "src_end_ms": s["source_end_ms"],
                         "volume": s.get("volume"), "scale": s.get("scale")})

    # The draft + its platform-correct path were resolved by the guard above. A
    # blank project has no Timelines/ dir yet, so re-resolve now that the media
    # read has confirmed the project is real.
    if draft_info_path is None:
        tl_uuid = find_timeline_uuid(project_dir)
        draft_info_path = draft_state_path(project_dir, tl_uuid)

    # Load harvested templates
    tpl_video_material = load_template("video_material.json")
    tpl_video_segment = load_template("video_segment.json")
    tpl_video_track = load_template("video_track_empty.json")
    tpl_helpers = load_template("segment_helpers.json")

    # Materials: 1 per unique clip used
    new_video_materials = []
    clip_to_mat_id = {}
    for r in resolved:
        ci = r["clip"]["index"]
        if ci in clip_to_mat_id:
            continue
        mat = deep_copy(tpl_video_material)
        mat["id"] = new_uuid()
        mat["path"] = r["clip"]["path"]
        mat["material_name"] = r["clip"]["name"]
        mat["duration"] = r["clip"]["duration_us"]
        if r["clip"]["width"]:
            mat["width"] = r["clip"]["width"]
            mat["height"] = r["clip"]["height"]
        clip_to_mat_id[ci] = mat["id"]
        new_video_materials.append(mat)

    # Segments: 1 per EDL entry, each with cloned helpers
    new_segments = []
    new_helpers_by_cat = {cat: [] for cat in tpl_helpers.keys()}
    timeline_cursor_us = 0
    for r in resolved:
        helper_id_map = {}
        for cat, items in tpl_helpers.items():
            for item in items:
                h = deep_copy(item)
                old_id = item["id"]
                new_id = new_uuid()
                helper_id_map[old_id] = new_id
                h["id"] = new_id
                new_helpers_by_cat[cat].append(h)

        seg = deep_copy(tpl_video_segment)
        seg["id"] = new_uuid()
        seg["material_id"] = clip_to_mat_id[r["clip"]["index"]]
        seg["extra_material_refs"] = [helper_id_map[old] for old in seg["extra_material_refs"]]

        dur_us = (r["src_end_ms"] - r["src_start_ms"]) * MS
        seg["source_timerange"] = {"start": r["src_start_ms"] * MS, "duration": dur_us}
        seg["target_timerange"] = {"start": timeline_cursor_us, "duration": dur_us}
        if r.get("volume") is not None:
            seg["volume"] = max(0.0, float(r["volume"]))
        if r.get("scale"):
            sc = float(r["scale"])
            seg.setdefault("clip", {})["scale"] = {"x": sc, "y": sc}
            seg["uniform_scale"] = {"on": True, "value": sc}
        new_segments.append(seg)
        timeline_cursor_us += dur_us

    # Apply to draft.
    #
    # Only the MAIN video track is rebuilt. `mats["videos"] = new_video_materials`
    # used to drop every video material in the project, which blanked the clips an
    # overlay/PiP track still pointed at - the track survived as empty frames. So a
    # material is dropped only when NOTHING outside the track being replaced still
    # references it (material_refcount, rule #42 invariant 2).
    before_draft = deep_copy(draft)
    mats = draft.setdefault("materials", {})
    survivors, dropped_material_ids = [], []
    for m in (mats.get("videos") or []):
        mid = m.get("id")
        if mid and material_refcount(draft, mid, exclude_tracks=(old_main_id,)) > 0:
            survivors.append(m)
        else:
            dropped_material_ids.append(mid)
    mats["videos"] = survivors + new_video_materials
    for cat, items in new_helpers_by_cat.items():
        existing = mats.get(cat, [])
        # Keep existing helpers (in case other tracks use them), append new ones
        mats[cat] = existing + items

    # Rebuild the main video track IN PLACE, keeping its id so the change reads as a
    # replace (what the plan declares) rather than a delete plus an add. Every other
    # track - text, audio, effect, and any overlay/PiP video track - is untouched.
    video_track = deep_copy(tpl_video_track)
    video_track["id"] = old_main_id or new_uuid()
    video_track["segments"] = new_segments
    if old_main is not None:
        # The buyer may have renamed or muted the track; those are their settings,
        # not ours to reset - only the segments are this op's business.
        for k in ("name", "is_default_name", "attribute", "flag"):
            if k in old_main:
                video_track[k] = old_main[k]
        draft["tracks"] = [video_track if t.get("id") == old_main_id else t
                           for t in draft.get("tracks", [])]
    else:
        draft.setdefault("tracks", []).insert(0, video_track)

    draft["duration"] = timeline_cursor_us
    draft["draft_type"] = "video"

    # Canvas must match the FOOTAGE, not whatever the buyer's blank project defaulted
    # to. A phone/DJI vertical clip (e.g. 1728x3072) dropped into a project still set
    # to 1920x1080 renders pillarboxed - big black bars either side - and the buyer
    # only finds out on playback. CapCut does not re-derive this on open, and nothing
    # else in the pipeline sets it, so do it here from the media we just injected.
    #
    # Only ever CORRECT a mismatch in orientation: if the buyer deliberately chose a
    # different-but-same-orientation canvas (1080x1350 4:5 for a vertical clip), leave
    # it alone - that is a real editorial choice, not an accident.
    canvas = draft.setdefault("canvas_config", {})
    src_w = src_h = 0
    for m in draft.get("materials", {}).get("videos", []) or []:
        if m.get("width") and m.get("height"):
            src_w, src_h = int(m["width"]), int(m["height"])
            break
    if src_w and src_h:
        cur_w, cur_h = int(canvas.get("width") or 0), int(canvas.get("height") or 0)
        # Three-way, not a boolean: square footage is neither portrait nor landscape,
        # and a boolean test reads 1080x1080 as "landscape" - the same as a 1920x1080
        # canvas - so a square clip would keep a 16:9 canvas and get pillarboxed.
        def _orient(w, h):
            return "square" if w == h else ("portrait" if h > w else "landscape")

        src_o = _orient(src_w, src_h)
        cur_o = _orient(cur_w, cur_h) if (cur_w and cur_h) else None
        src_portrait = src_o == "portrait"
        if cur_o is None or src_o != cur_o:
            if src_portrait:
                canvas.update({"width": 1080, "height": 1920, "ratio": "9:16"})
            elif src_w == src_h:
                canvas.update({"width": 1080, "height": 1080, "ratio": "1:1"})
            else:
                canvas.update({"width": 1920, "height": 1080, "ratio": "16:9"})
            canvas_fixed = {"from": [cur_w, cur_h],
                            "to": [canvas["width"], canvas["height"]],
                            "source": [src_w, src_h]}
        else:
            canvas_fixed = None
    else:
        canvas_fixed = None
    # Preserve the project's OWN schema version. CapCut rejects ("file not
    # accessible" / self-heals) a draft whose version is OLDER than the app that
    # wrote it, so setdefault only fills these on a BLANK draft. Read the real
    # values from the sibling draft_meta_info.json first (the blank timeline still
    # has them), so we never stamp a stale/foreign version. Fall back to current-
    # CapCut defaults only when meta has nothing.
    meta_sibling = {}
    _meta_p = project_dir / "draft_meta_info.json"
    if _meta_p.exists():
        try:
            meta_sibling = json.load(open(_meta_p, encoding="utf-8"))
        except Exception:
            meta_sibling = {}
    draft.setdefault("new_version", meta_sibling.get("cloud_package_version")
                     or meta_sibling.get("draft_new_version") or "175.0.0")
    draft.setdefault("version", 360000)

    # Ensure required modern-CapCut fields aren't null
    for k, v in [("is_drop_frame_timecode", False), ("lyrics_effects", []), ("path", ""),
                 ("smart_ads_info", {"page_from": "", "routine": "", "draft_url": ""}),
                 ("uneven_animation_template_info", {"composition": "", "content": "",
                  "order": "", "sub_template_info_list": []}),
                 ("function_assistant_info", {"smart_rec_applied": False, "fixed_rec_applied": False})]:
        if draft.get(k) is None:
            draft[k] = v

    # Preserve the project's real platform block. Only synthesize when the draft
    # has NONE (a blank timeline). Prefer the sibling meta's platform; else build
    # an OS-CORRECT fingerprint - NEVER stamp os="mac" onto a Windows project
    # (M-CAP3), which is exactly the corruption the old hardcoded mac block caused.
    if not draft.get("platform"):
        if meta_sibling.get("platform"):
            draft["platform"] = dict(meta_sibling["platform"])
        elif IS_WINDOWS:
            draft["platform"] = {
                "os": "windows", "os_version": "10.0", "app_id": 359289,
                "app_version": "8.5.0", "app_source": "cc",
                "device_id": "", "hard_disk_id": "", "mac_address": "",
            }
        else:
            draft["platform"] = {
                "os": "mac", "os_version": "15.5", "app_id": 359289,
                "app_version": "8.5.0", "app_source": "cc",
                "device_id": "", "hard_disk_id": "", "mac_address": "",
            }
    if not draft.get("last_modified_platform"):
        draft["last_modified_platform"] = dict(draft["platform"])

    # Declare exactly what this op changes, then refuse the write if anything else
    # moved. The op rebuilds ONE track and adds its materials; a PiP track or a
    # buyer title disappearing here is a bug, and this is what turns it into an
    # error instead of a discovery on playback (rule #42).
    #
    # assert_preserved compares materials category-by-category, so a helper category
    # a blank draft never had reads as "added unexpectedly" though every entry in it
    # is planned. Seed the missing keys as empty lists on the BEFORE side.
    for _cat in (draft.get("materials") or {}):
        if isinstance(draft["materials"][_cat], list):
            before_draft.setdefault("materials", {}).setdefault(_cat, [])
    # `duration` and `canvas_config` ARE this op's to set - the timeline length
    # follows the EDL, and the canvas follows the footage (rule #40). The plan
    # shape has no bucket for them, and _preserved.diff_draft exempts the same two
    # keys for the same reason, so align the before side rather than weakening the
    # track/material comparison that actually protects the buyer's work.
    before_draft["duration"] = draft.get("duration")
    before_draft["canvas_config"] = deep_copy(draft.get("canvas_config"))
    _before_mat_ids = {i.get("id") for items in (before_draft.get("materials") or {}).values()
                       if isinstance(items, list) for i in items if isinstance(i, dict)}
    _after_mat_ids = {i.get("id") for items in (draft.get("materials") or {}).values()
                      if isinstance(items, list) for i in items if isinstance(i, dict)}
    plan = {
        "add": {"tracks": [] if old_main is not None else [video_track["id"]],
                "materials": sorted(x for x in (_after_mat_ids - _before_mat_ids) if x)},
        "replace": {"tracks": [old_main_id] if old_main is not None else []},
        "delete": {"segments": [], "materials": sorted(x for x in dropped_material_ids if x)},
    }
    try:
        assert_preserved(before_draft, draft, plan)
    except ValueError as e:
        emit_err(str(e), op="inject-edl", project=args.project,
                 main_video_track_id=old_main_id)

    # Everything above was in-memory. begin_op snapshots the exact files this op
    # writes BEFORE the first byte lands, so a partial mirror (or any exception)
    # rolls the project back instead of leaving CapCut to self-heal to a mix.
    with begin_op(project_dir, "inject-edl", plan=plan) as op:
        # Atomic write so a crash mid-write can't leave a half-written draft that the
        # mirror then propagates (P0-08/H10).
        atomic_write_text(draft_info_path,
                          json.dumps(draft, ensure_ascii=False, indent=2))

        # Mirror to 9 places - a partial mirror rolls back (else CapCut self-heals
        # to the pre-inject state and the edit vanishes).
        mirror = mirror_9_places(project_dir)
        op.check_mirror(mirror)

        # Write state.json
        state_path = write_state(project_dir, {
            "skill": "indigo11-capcut",
            "op": "inject-edl",
            "op_id": op.op_id,
            "edl_source": str(edl_path),
            "segments_injected": len(new_segments),
            "materials_added": len(new_video_materials),
            "replaced_timeline": bool(existing_video_segments),
            "total_duration_us": timeline_cursor_us,
            "total_duration_s": round(timeline_cursor_us / S, 3),
            "mirror_result": mirror,
        })

    emit_ok(
        op="inject-edl",
        project=args.project,
        project_dir=str(project_dir),
        segments_injected=len(new_segments),
        materials_added=len(new_video_materials),
        replaced_video_segments=existing_video_segments,
        canvas_fixed=canvas_fixed,
        total_duration_s=round(timeline_cursor_us / S, 3),
        state_path=str(state_path),
        capcut_close=detail,
        next_step=f"เปิด CapCut → {args.project} → ดู timeline",
        **op.info(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
