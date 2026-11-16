#!/usr/bin/env python3
"""capcut_apply_resource.py - apply ANY CapCut resource (sticker / text-template /
sound / effect / filter / ... ) to a project, VERSION-PROOF, on macOS or Windows.

How it works (the only approach that survives CapCut version drift):
  CapCut's stickers/effects/filters/fancy-text/sounds come from an online library
  whose resource_ids change per app version & region, with NO public search API.
  So we never hard-code ids. The buyer adds the items they want in a "library"
  CapCut project once (default name: fxlib); then this script COPIES the buyer's
  OWN material object + the exact segment CapCut authored for it - guaranteed to
  render on THIS machine's CapCut - into the target project on a new track, at the
  time/position you ask. Pure additive JSON patch + the 9-place mirror (rule #6);
  it never rewrites existing tracks.

Discover what's available first:
  python extract_resources.py --project fxlib --json catalog.json
  (or just --project fxlib to list resource_id + name)

Apply:
  python capcut_apply_resource.py --target 0616 --source fxlib \
      --resource-id 7647460459821550869 --start-ms 1000 --dur-ms 3000 \
      --x 0.3 --y -0.4 --scale 0.6
  # or match by name (case-insensitive substring), e.g. --name "Maxim GOAL"

Times are MILLISECONDS (converted to CapCut microseconds internally, rule #14).

Transitions (--transition):
  A CapCut transition is not a track segment. It is a `materials.transitions[]`
  entry referenced from the extra_material_refs of the clip BEFORE the cut, and it
  does not move any clip on the timeline (checked on real drafts). So:
      python capcut_apply_resource.py --target 1004 --source fxlib \
          --transition --name "Light Flare" --start-ms 4800
  copies the buyer's own transition material and attaches it to the main-video
  segment that ENDS at --start-ms (the cut, +-1 frame). It refuses when there is no
  cut there, when the cut is the last clip, or when that clip already carries a
  transition - it never replaces one silently.
"""
from __future__ import annotations
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _capcut_lib import (  # noqa: E402
    CAPCUT_DRAFTS_DIR, MS, S, deep_copy, draft_state_path, emit_err, emit_ok,
    find_timeline_uuid, ensure_capcut_closed, mirror_9_places, new_uuid,
    resolve_project, write_state, atomic_write_text, begin_op, assert_preserved,
)

_ID_FIELDS = ("resource_id", "effect_id", "sticker_id", "filter_id", "font_id")
# segment fields that hold material-id references (besides material_id)
_REF_LIST_FIELDS = ("extra_material_refs", "keyframe_refs")


def _material_index(draft):
    """id -> (category, material dict) over every materials list."""
    idx = {}
    for cat, items in draft.get("materials", {}).items():
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and it.get("id"):
                    idx[it["id"]] = (cat, it)
    return idx


def _resource_id_of(mat):
    return next((mat[f] for f in _ID_FIELDS if mat.get(f)), None)


def _find_segment(draft, mat_index, *, resource_id=None, name=None):
    """Find a (segment, track_type) in the source whose MAIN material matches."""
    name_l = name.lower() if name else None
    for track in draft.get("tracks", []):
        for seg in track.get("segments", []):
            mid = seg.get("material_id")
            ent = mat_index.get(mid)
            if not ent:
                continue
            _cat, mat = ent
            if resource_id and _resource_id_of(mat) == resource_id:
                return seg, track.get("type")
            if name_l and name_l in str(mat.get("name", "")).lower():
                return seg, track.get("type")
    return None, None


# Material categories that are applied as their OWN track segment (clip-attached
# in a source draft, so no standalone segment to copy - we build one).
_TRACK_FOR_CATEGORY = {
    "video_effects": "effect", "effects": "effect", "filters": "filter",
}


def _find_material(draft, mat_index, *, resource_id=None, name=None):
    """Find a material directly (for effect/filter that are clip-attached in the
    source, so they have no standalone segment). Returns (material, track_type)."""
    name_l = name.lower() if name else None
    for mid, (cat, mat) in mat_index.items():
        tt = _TRACK_FOR_CATEGORY.get(cat)
        if not tt:
            continue
        if resource_id and _resource_id_of(mat) == resource_id:
            return mat, tt
        if name_l and name_l in str(mat.get("name", "")).lower():
            return mat, tt
    return None, None


def _overlaps(track, start_us, dur_us):
    """True if a segment placed at start_us for dur_us would collide with one already on the track."""
    end_us = start_us + dur_us
    for s in track.get("segments") or []:
        tr = s.get("target_timerange") or {}
        a = int(tr.get("start", 0)); b = a + int(tr.get("duration", 0))
        if start_us < b and a < end_us:
            return True
    return False


def _fit_audio(track, seg, min_us=80_000):
    """Make a sound fit on a shared SFX track WITHOUT a new track: a CapCut track cannot hold two overlapping segments, so
    when sounds are close together the earlier one is shortened to end where the next one starts (or the new one is
    shortened to end where a later one starts). Returns False if that would leave a sound under 80 ms (then a new track is used).
    Nothing is changed unless every adjustment is possible."""
    tr = seg["target_timerange"]
    ns, nd = int(tr["start"]), int(tr["duration"])
    edits = []
    for s in sorted(track.get("segments") or [], key=lambda x: int((x.get("target_timerange") or {}).get("start", 0))):
        st = s.get("target_timerange") or {}
        a, b = int(st.get("start", 0)), int(st.get("start", 0)) + int(st.get("duration", 0))
        if not (a < ns + nd and ns < b):
            continue
        if a < ns:                       # the earlier sound is cut where the new one starts
            if ns - a < min_us: return False
            edits.append((s, ns - a))
        else:                            # a later sound starts inside the new one: the new one is cut there
            if a - ns < min_us: return False
            nd = a - ns
    for s, d in edits:
        s["target_timerange"]["duration"] = d
        sr = s.get("source_timerange")
        if sr: sr["duration"] = min(int(sr.get("duration", d)), d)
    tr["duration"] = nd
    sr = seg.get("source_timerange")
    if sr: sr["duration"] = min(int(sr.get("duration", nd)), nd)
    return True


def _build_effect_segment(material_id, start_us, dur_us):
    """Minimal valid effect/filter-track segment referencing a copied material
    (shape harvested from pyCapCut's add_effect output)."""
    return {
        "id": new_uuid(), "material_id": material_id,
        "target_timerange": {"start": start_us, "duration": dur_us},
        "render_index": 0, "track_attribute": 0, "track_render_index": 0,
        "visible": True, "reverse": False, "last_nonzero_volume": 1.0,
        "common_keyframes": [], "keyframe_refs": [],
        "enable_adjust": False, "enable_color_correct_adjust": False,
        "enable_color_curves": False, "enable_color_match_adjust": False,
        "enable_color_wheels": False, "enable_lut": False,
        "enable_smart_color_adjust": False,
    }


def _collect_ref_ids(seg):
    ids = []
    if seg.get("material_id"):
        ids.append(seg["material_id"])
    for f in _REF_LIST_FIELDS:
        for r in seg.get(f, []) or []:
            if r:
                ids.append(r)
    return ids


CUT_TOLERANCE_US = 40_000   # one frame at 25 fps


def _find_transition(mat_index, *, resource_id=None, name=None):
    name_l = name.lower() if name else None
    for _mid, (cat, mat) in mat_index.items():
        if cat != "transitions":
            continue
        if resource_id and _resource_id_of(mat) == resource_id:
            return mat
        if name_l and name_l in str(mat.get("name", "")).lower():
            return mat
    return None


def apply_transition(args, src_index, target_dir) -> int:
    """Attach a harvested transition to the main-video clip that ends at --start-ms."""
    mat = _find_transition(src_index, resource_id=args.resource_id, name=args.name)
    if mat is None:
        emit_err(f"ไม่เจอ transition '{args.resource_id or args.name}' ใน {args.source} "
                 f"(transition not found - add it between two clips in {args.source}, save, retry)")
    ok, close_detail = ensure_capcut_closed(args.force_kill)
    if not ok:
        emit_err(close_detail)
    try:
        tgt = json.load(open(draft_state_path(target_dir), encoding="utf-8"))
    except (FileNotFoundError, RuntimeError, ValueError) as e:
        emit_err(f"{args.target} ยังว่างหรือ draft เพี้ยน ({args.target} is empty or malformed)",
                 detail=str(e))
    before = deep_copy(tgt)
    main = next((t for t in tgt.get("tracks", []) if t.get("type") == "video"), None)
    segs = sorted((main or {}).get("segments") or [],
                  key=lambda x: int(x.get("target_timerange", {}).get("start", 0)))
    cut_us = args.start_ms * MS
    hit = None
    for i, sg in enumerate(segs):
        tr = sg.get("target_timerange") or {}
        end = int(tr.get("start", 0)) + int(tr.get("duration", 0))
        if abs(end - cut_us) <= CUT_TOLERANCE_US:
            hit = i
            break
    if hit is None:
        emit_err(f"ไม่มีจุดตัดที่ {args.start_ms} ms บน video track หลัก "
                 f"(no cut on the main video track at {args.start_ms} ms)")
    if hit == len(segs) - 1:
        emit_err(f"{args.start_ms} ms คือท้ายคลิปสุดท้าย ไม่มีคลิปถัดไปให้ต่อ "
                 f"(that is the end of the last clip - a transition needs a next clip)")
    idx = {}
    for cat, items in tgt.get("materials", {}).items():
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and it.get("id"):
                    idx[it["id"]] = cat
    seg = segs[hit]
    if any(idx.get(r) == "transitions" for r in seg.get("extra_material_refs") or []):
        emit_err(f"คลิปที่ {args.start_ms} ms มี transition อยู่แล้ว - ไม่เขียนทับ "
                 f"(that clip already has a transition; remove it in CapCut first)")
    new_mat = deep_copy(mat)
    new_mat["id"] = new_uuid()
    # A transition longer than half the shorter neighbour eats the shot it joins.
    nxt = segs[hit + 1]
    room = min(int(seg["target_timerange"]["duration"]),
               int(nxt.get("target_timerange", {}).get("duration", 0))) // 2
    if room and int(new_mat.get("duration") or 0) > room:
        new_mat["duration"] = room
    tgt.setdefault("materials", {}).setdefault("transitions", []).append(new_mat)
    refs = list(seg.get("extra_material_refs") or [])
    pos = next((k + 1 for k, r in enumerate(refs) if idx.get(r) == "placeholder_infos"), len(refs))
    refs.insert(pos, new_mat["id"])
    seg["extra_material_refs"] = refs
    before.setdefault("materials", {}).setdefault("transitions", [])
    plan = {
        "add": {"tracks": [], "materials": [new_mat["id"]]},
        "replace": {"tracks": [main["id"]]},
        "delete": {"segments": [], "materials": []},
    }
    try:
        assert_preserved(before, tgt, plan)
    except ValueError as e:
        emit_err(str(e), op="apply-transition", target=args.target)
    with begin_op(target_dir, "apply-transition", plan=plan) as op:
        atomic_write_text(draft_state_path(target_dir), json.dumps(tgt, ensure_ascii=False, indent=2))
        mirror = mirror_9_places(target_dir)
        op.check_mirror(mirror)
        state_path = write_state(target_dir, {
            "skill": "indigo11-capcut", "op": "apply-transition", "op_id": op.op_id,
            "name": mat.get("name", ""), "resource_id": _resource_id_of(mat) or "",
            "cut_ms": args.start_ms, "duration_ms": int(new_mat.get("duration") or 0) // MS,
            "source": args.source, "mirror_result": mirror,
        })
    emit_ok(op="apply-transition", target=args.target, source=args.source,
            resource=mat.get("name", ""), cut_ms=args.start_ms,
            duration_ms=int(new_mat.get("duration") or 0) // MS,
            state_path=str(state_path), capcut_close=close_detail, **op.info())
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", required=True, help="project to add the resource to")
    ap.add_argument("--source", default="fxlib", help="library project to copy from (default fxlib)")
    ap.add_argument("--resource-id", help="resource_id to copy (from extract_resources)")
    ap.add_argument("--name", help="match the resource by name substring instead of id")
    ap.add_argument("--start-ms", type=int, default=0)
    ap.add_argument("--dur-ms", type=int, help="duration on the timeline (default: keep source duration)")
    ap.add_argument("--x", type=float, help="clip transform x (-1..1, screen-relative)")
    ap.add_argument("--y", type=float, help="clip transform y (-1..1)")
    ap.add_argument("--scale", type=float, help="uniform scale (e.g. 0.6)")
    ap.add_argument("--rotation", type=float, help="degrees")
    ap.add_argument("--alpha", type=float, help="opacity 0..1")
    ap.add_argument("--track-name", help="name of the track to put it on (default: indigo11-sfx for sounds, indigo11-<type> for the rest). "
                                          "Every call with the same name SHARES one track while the segments do not overlap")
    ap.add_argument("--drafts-dir", default=str(CAPCUT_DRAFTS_DIR))
    ap.add_argument("--force-kill", action="store_true",
                    help="Kill a running CapCut instead of refusing. Unsaved work is lost.")
    ap.add_argument("--transition", action="store_true",
                    help="the resource is a TRANSITION: attach it at the cut that --start-ms names")
    args = ap.parse_args()

    if not args.resource_id and not args.name:
        emit_err("ต้องระบุ --resource-id หรือ --name (give --resource-id or --name)")

    target_dir = resolve_project(args.target, args.drafts_dir)
    source_dir = resolve_project(args.source, args.drafts_dir)
    if not target_dir.exists():
        emit_err(f"Target project not found: {target_dir}")
    if not source_dir.exists():
        emit_err(f"Source/library project not found: {source_dir} (สร้าง fxlib แล้วใส่ resource ที่อยากได้)")

    # A freshly-made/saved library may have an empty or missing Timelines/ (rule #23);
    # draft_state_path raises there. Surface the JSON error contract, not a traceback.
    try:
        src = json.load(open(draft_state_path(source_dir), encoding="utf-8"))
    except (FileNotFoundError, RuntimeError, ValueError) as e:
        emit_err(
            f"{args.source} ยังว่างหรือยังไม่ได้ save - เปิด CapCut ใส่ resource แล้ว Cmd+S ก่อน "
            f"({args.source} is empty or unsaved - add resources and Cmd+S first)",
            detail=str(e),
        )
    src_index = _material_index(src)
    if args.transition:
        return apply_transition(args, src_index, target_dir)

    # First try an INDEPENDENT segment (sticker/text-template/sound/image - copy it
    # whole, cluster + all). Fall back to a clip-attached MATERIAL (effect/filter -
    # build a fresh effect/filter-track segment around the copied material).
    seg_src, track_type = _find_segment(src, src_index, resource_id=args.resource_id, name=args.name)
    attached_mat = None
    if not seg_src:
        attached_mat, track_type = _find_material(src, src_index, resource_id=args.resource_id, name=args.name)
    if not seg_src and not attached_mat:
        emit_err(f"Resource not found in {args.source}: {args.resource_id or args.name} "
                 f"(เปิด CapCut ใส่ลง {args.source} แล้ว save ก่อน)")

    ok, close_detail = ensure_capcut_closed(args.force_kill)
    if not ok:
        emit_err(close_detail)

    # CapCut is now closed; the target load is BEFORE any write, so a malformed/blank
    # target (missing or multi-UUID Timelines/) just fails cleanly with no half-write.
    try:
        tgt = json.load(open(draft_state_path(target_dir), encoding="utf-8"))
    except (FileNotFoundError, RuntimeError, ValueError) as e:
        emit_err(
            f"{args.target} ยังว่างหรือ draft เพี้ยน - เปิด CapCut ลากคลิปเข้า Media + save ก่อน "
            f"({args.target} is empty or has a malformed draft - open it, add media, and save first)",
            detail=str(e),
        )
    tgt.setdefault("materials", {})
    tgt.setdefault("tracks", [])
    before_tgt = deep_copy(tgt)

    start_us = args.start_ms * MS
    matched_name = ""

    if attached_mat is not None:
        # clip-attached resource (effect/filter): copy the material, build a segment.
        # CRITICAL: the source material was applied to a CLIP (apply_target_type=0,
        # with a clip-relative time_range). On its OWN effect/filter track it must be
        # a TRACK effect: apply_target_type=2 + time_range=None (matches what CapCut/
        # pyCapCut author for effect-track materials) - else CapCut won't apply it.
        cat = src_index[attached_mat["id"]][0]
        new_mat = deep_copy(attached_mat)
        new_mat["id"] = new_uuid()
        if "apply_target_type" in new_mat:
            new_mat["apply_target_type"] = 2
        if "time_range" in new_mat:
            new_mat["time_range"] = None
        tgt["materials"].setdefault(cat, []).append(new_mat)
        dur_us = (args.dur_ms * MS) if args.dur_ms else 3 * S
        seg = _build_effect_segment(new_mat["id"], start_us, dur_us)
        matched_name = attached_mat.get("name", "") or _resource_id_of(attached_mat)
        id_remap = {attached_mat["id"]: new_mat["id"]}
        clip = None
    else:
        # independent segment: copy its whole material cluster (main + helper refs)
        id_remap = {}
        for old_id in _collect_ref_ids(seg_src):
            ent = src_index.get(old_id)
            if not ent:
                continue  # ref to something not in materials (CapCut tolerates); skip
            cat, mat = ent
            new_mat = deep_copy(mat)
            nid = new_uuid()
            new_mat["id"] = nid
            id_remap[old_id] = nid
            tgt["materials"].setdefault(cat, []).append(new_mat)

        seg = deep_copy(seg_src)
        seg["id"] = new_uuid()
        if seg.get("material_id") in id_remap:
            seg["material_id"] = id_remap[seg["material_id"]]
        for f in _REF_LIST_FIELDS:
            if seg.get(f):
                seg[f] = [id_remap.get(r, r) for r in seg[f]]
        dur_us = (args.dur_ms * MS) if args.dur_ms else seg.get("target_timerange", {}).get("duration", 3 * S)
        seg["target_timerange"] = {"start": start_us, "duration": dur_us}
        matched_name = src_index[seg_src["material_id"]][1].get("name", "")

    # transform overrides only apply to visual segments (sticker/text/image), not
    # effect/filter-track segments (which have no clip box).
    clip = None if attached_mat is not None else seg.setdefault(
        "clip", {"scale": {"x": 1.0, "y": 1.0}, "rotation": 0.0,
                 "transform": {"x": 0.0, "y": 0.0},
                 "flip": {"vertical": False, "horizontal": False}, "alpha": 1.0})
    if clip is not None:
        if args.x is not None or args.y is not None:
            clip.setdefault("transform", {"x": 0.0, "y": 0.0})
            if args.x is not None:
                clip["transform"]["x"] = args.x
            if args.y is not None:
                clip["transform"]["y"] = args.y
        if args.scale is not None:
            clip["scale"] = {"x": args.scale, "y": args.scale}
            seg["uniform_scale"] = {"on": True, "value": args.scale}
        if args.rotation is not None:
            clip["rotation"] = args.rotation
        if args.alpha is not None:
            clip["alpha"] = args.alpha

    # 3) layer on top: render_index above everything in the target
    max_ri = 0
    for t in tgt["tracks"]:
        for s in t.get("segments", []):
            max_ri = max(max_ri, int(s.get("render_index", 0)))
    seg["render_index"] = max_ri + 1
    seg["track_render_index"] = 0

    # 4) put it on a track of the resource's type. Never on the user's own tracks (rule #6 spirit): only on a track THIS tool
    #    made earlier (named indigo11-sfx / indigo11-<type>), and only if the segment does not overlap anything there.
    #    So 30 "pop" sounds land on ONE audio track, not 30 tracks. A new track is made only when there is a real overlap.
    ttype = track_type or "sticker"
    tname = args.track_name or ("indigo11-sfx" if ttype == "audio" else f"indigo11-{ttype}")
    cands = [tr for tr in tgt["tracks"] if tr.get("type") == ttype and tr.get("name") == tname]
    new_track = next((tr for tr in cands if not _overlaps(tr, start_us, dur_us)), None)
    if new_track is None and ttype == "audio":     # sounds close together: shorten instead of opening a new track
        new_track = next((tr for tr in cands if _fit_audio(tr, seg)), None)
        if new_track is not None: dur_us = int(seg["target_timerange"]["duration"])
    reused = new_track is not None
    if reused:
        new_track["segments"].append(seg)
        new_track["segments"].sort(key=lambda s: int((s.get("target_timerange") or {}).get("start", 0)))
    else:
        new_track = {
            "attribute": 0, "flag": 0, "id": new_uuid(), "is_default_name": False,
            "name": tname, "segments": [seg], "type": ttype,
        }
        tgt["tracks"].append(new_track)

    # keep duration correct
    tgt["duration"] = max(int(tgt.get("duration", 0)), start_us + dur_us)

    # This op only ever ADDS: one new track plus the materials it copied in. The
    # `duration` bump is part of that add, so it is declared by replacing nothing
    # and adding the new ids - anything else moving is a bug, not a resource apply.
    # assert_preserved compares materials category-by-category, so a category this
    # copy CREATES in the target (a sticker's own category the target never had)
    # reads as "added unexpectedly" even though every entry in it is planned. Seed
    # the missing keys as empty lists on the BEFORE side.
    for _cat in (tgt.get("materials") or {}):
        if isinstance(tgt["materials"][_cat], list):
            before_tgt.setdefault("materials", {}).setdefault(_cat, [])
    plan = {
        "add": {"tracks": [] if reused else [new_track["id"]], "materials": sorted(set(id_remap.values()))},
        "replace": {"tracks": [new_track["id"]] if reused else []},
        "delete": {"segments": [], "materials": []},
    }
    try:
        assert_preserved(before_tgt, tgt, plan)
    except ValueError as e:
        emit_err(str(e), op="apply-resource", target=args.target)

    # 5) write + 9-place mirror (per-OS filename via draft_state_path), reversibly
    with begin_op(target_dir, "apply-resource", plan=plan) as op:
        atomic_write_text(draft_state_path(target_dir), json.dumps(tgt, ensure_ascii=False, indent=2))
        mirror = mirror_9_places(target_dir)
        op.check_mirror(mirror)

        state_path = write_state(target_dir, {
            "skill": "indigo11-capcut", "op": "apply-resource",
            "op_id": op.op_id,
            "resource_id": args.resource_id or "", "name": matched_name,
            "track_type": track_type, "track_name": tname, "track_reused": reused, "materials_copied": len(id_remap),
            "start_ms": args.start_ms, "dur_ms": dur_us // MS, "source": args.source,
            "mirror_result": mirror,
        })

    emit_ok(
        op="apply-resource", target=args.target, source=args.source,
        resource=matched_name or args.resource_id, track_type=track_type,
        materials_copied=len(id_remap), start_ms=args.start_ms, dur_ms=dur_us // MS,
        state_path=str(state_path), capcut_close=close_detail,
        next_step=f"เปิด CapCut → {args.target} → ดู track {track_type}",
        **op.info(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
