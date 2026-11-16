#!/usr/bin/env python3
"""add_overlay.py - Place image overlay overlays on a CapCut timeline.

Like add_sfx.py - the image must be REGISTERED in the Media panel first, which
`import_media.py` does with three JSON writes (edit-rules #25; no manual drag).
This script places
them on the timeline with position + scale + duration + rotation.

Image overlays in CapCut live in materials.videos[] with type="photo", on a video
track with render_index > 0 (overlay above the main video).

Buyer flow:
  1. python3 import_media.py --project <p> --files <img.jpg> ...   (CapCut QUIT)
  2. (CapCut must be quit: Cmd+Q on mac, Ctrl+Q / Alt+F4 on Windows)
  3. Run this script with timing+position JSON

Timing JSON shape:
  {
    "overlays": [
      {
        "file": "angry-emoji.webp",
        "at_ms": 5000,
        "duration_ms": 1500,
        "x": 0.0, // -1..+1, 0=center (default)
        "y": -0.6, // -1..+1, 0=center, -1=top, +1=bottom
        "scale": 0.4, // 1.0 = native size (default 0.5)
        "rotation": 0, // degrees (default 0)
        "alpha": 1.0 // 0..1 (default 1.0)
      }
    ]
  }

`file` matches by basename against materials.videos[].material_name (the name
shown in Media panel after drag).

VIDEO overlays (B-roll on top of the main clip) work the same way: a Media-panel
entry with metetype "video" is placed as a video segment on the overlay track.
Extra keys for a video entry:
  "source_in_ms": 1200   where in the B-roll file to start (default 0)
  "volume": 0.0          B-roll sound (default 0 = muted; the main audio carries)
A video overlay is capped at what the file holds after source_in_ms.

MOTION (new): an image overlay is no longer a static picture. Each one gets real CapCut keyframes:
  "motion": "pop+float"   (default for small/medium images; full-frame images, scale >= 0.85, default to "push": slow push-in + soft fade, no sway so edges never show)
                          pop = scale 0.6 -> 1.0 with an overshoot + fade-in over 0.35 s and a short fade-out at the end;
                          float = constant gentle motion: slow push-in, breathing scale, small sway and bob
           "pop" | "float" | "none"   ("none" = the old static overlay; video / B-roll overlays default to "none")
  "motion_strength": 1.0  0.5 = calmer, 2.0 = stronger
  or for the whole run:  --motion pop+float --motion-strength 1.0
The values are written as CapCut keyframes (KFTypeScaleX / PositionX / PositionY / Rotation / Alpha), so they show on the timeline and can be edited.

Position semantics:
  - x=0 y=0 → center of canvas
  - x=-1 → left edge, x=+1 → right edge
  - y=-1 → top edge, y=+1 → bottom edge
  - scale=0.5 → overlay appears at half its native pixel size

Usage:
  python3 add_overlay.py --project 0521 --overlay-timing overlays.json
"""

from __future__ import annotations
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _capcut_lib import (
    CAPCUT_DRAFTS_DIR, ensure_capcut_closed, new_uuid,
    load_template, deep_copy, emit_ok, emit_err, US, MS, S,
    draft_state_path, load_draft, mirror_9_places, atomic_write_text,
    begin_op, assert_preserved,
)


def find_overlay_material(draft: dict, overlay_file: str, project_dir: Path) -> tuple[str, int, int, int]:
    """Find a photo material that buyer dragged into Media panel.

    Returns (material_id, width, height, native_duration_us).
    Looks first in draft_info.materials.videos[type="photo"], falls back to
    Media panel (draft_meta_info.draft_materials[type=0] metetype="photo") and
    clones into draft if found there only.
    """
    videos = draft.setdefault("materials", {}).setdefault("videos", [])
    target = os.path.basename(overlay_file)
    abs_path_in = str(Path(overlay_file).expanduser().resolve())

    # Already in draft's videos?
    for v in videos:
        if v.get("type") != "photo":
            continue
        if v.get("material_name") == target or v.get("path") == abs_path_in:
            return v["id"], v.get("width", 0), v.get("height", 0), v.get("duration", 10800000000)

    # Look in Media panel
    meta_path = project_dir / "draft_meta_info.json"
    if meta_path.exists():
        meta = json.load(open(meta_path, encoding="utf-8"))
        for g in meta.get("draft_materials", []):
            if g.get("type") != 0:
                continue
            for v in g.get("value", []):
                if v.get("metetype") != "photo":
                    continue
                if v.get("extra_info") == target or v.get("file_Path") == abs_path_in:
                    abs_path = v.get("file_Path", abs_path_in)
                    width = v.get("width", 0)
                    height = v.get("height", 0)

                    tpl = load_template("photo/photo_material.json")
                    mat = deep_copy(tpl)
                    new_id = new_uuid()
                    mat["id"] = new_id
                    mat["material_name"] = target
                    mat["path"] = abs_path
                    mat["width"] = width
                    mat["height"] = height
                    # native duration_us - photos use a large default (~3 hours)
                    duration_us = mat.get("duration", 10800000000) or 10800000000
                    videos.append(mat)
                    return new_id, width, height, duration_us

    raise FileNotFoundError(
        f"Image overlay '{target}' ยังไม่อยู่ใน Media panel - ปิด CapCut แล้วรัน "
        f"import_media.py --project <project> --files <file> ก่อน แล้วลองใหม่ "
        f"(overlay not registered - quit CapCut, run import_media.py to register it, "
        f"then retry; no manual drag needed - rule #25)"
    )


def find_video_material(draft: dict, overlay_file: str, project_dir: Path):
    """A VIDEO (B-roll) material for an overlay, or None when the file is not a video.

    Same two places as photos: an existing materials.videos[type=video] entry, else
    the Media panel (metetype "video"), cloned from the harvested video template.
    Returns (material_id, width, height, duration_us) or None.
    """
    videos = draft.setdefault("materials", {}).setdefault("videos", [])
    target = os.path.basename(overlay_file)
    abs_path_in = str(Path(overlay_file).expanduser().resolve())
    for v in videos:
        if v.get("type") != "video":
            continue
        if v.get("material_name") == target or v.get("path") == abs_path_in:
            return v["id"], v.get("width", 0), v.get("height", 0), v.get("duration", 0)
    meta_path = project_dir / "draft_meta_info.json"
    if not meta_path.exists():
        return None
    meta = json.load(open(meta_path, encoding="utf-8"))
    for g in meta.get("draft_materials", []):
        if g.get("type") != 0:
            continue
        for v in g.get("value", []):
            if str(v.get("metetype", "")).lower() not in ("video", "movie"):
                continue
            if v.get("extra_info") == target or v.get("file_Path") == abs_path_in:
                mat = deep_copy(load_template("video_material.json"))
                mat["id"] = new_uuid()
                mat["path"] = v.get("file_Path", abs_path_in)
                mat["material_name"] = target
                mat["duration"] = int(v.get("duration", 0))
                if v.get("width") and v.get("height"):
                    mat["width"], mat["height"] = v["width"], v["height"]
                videos.append(mat)
                return mat["id"], mat.get("width", 0), mat.get("height", 0), mat["duration"]
    return None


def _clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def _out_back(p, s=1.9):
    p -= 1.0
    return p * p * ((s + 1.0) * p + s) + 1.0


def kf_group(prop: str, pts: list) -> dict:
    """One CapCut keyframe group (same shape CapCut itself writes): pts = [(time_us_from_segment_start, value), ...]."""
    return {
        "id": new_uuid(),
        "keyframe_list": [{
            "curveType": "Line", "graphID": "", "id": new_uuid(),
            "left_control": {"x": 0.0, "y": 0.0}, "right_control": {"x": 0.0, "y": 0.0},
            "string_value": "", "time_offset": int(tu), "values": [round(float(v), 6)],
        } for tu, v in pts],
        "material_id": "",
        "property_type": prop,
    }


def motion_keyframes(motion: str, dur_ms: int, scale: float, x: float, y: float, rot: float,
                     alpha: float, strength: float = 1.0) -> list:
    """Keyframes that make an overlay MOVE. Sampled every 0.4 s (and densely during the pop) with straight lines between,
    which CapCut shows as keyframe diamonds. Values are absolute (the same units as clip.scale / transform / rotation / alpha).
    Only symmetric movement is used (sway, bob, breathing), so the sign convention of the y axis never matters."""
    import math
    motion = (motion or "none").lower()
    dur = dur_ms / 1000.0
    if "push" in motion and dur >= 0.3:
        # FULL-FRAME picture (a scene image that fills the screen): only a slow push-in plus a soft fade in and out.
        # No sway, rotation or shrinking, because those would show the picture's edges.
        edge = min(0.2, dur / 3)
        ts = sorted({0.0, dur, edge, dur - edge} | {round(k * 0.5, 3) for k in range(1, int(dur / 0.5) + 1) if k * 0.5 < dur})
        sc, al = [], []
        for tt in ts:
            tu = int(round(tt * 1_000_000))
            sc.append((tu, scale * (1.0 + 0.08 * strength * (tt / dur))))
            al.append((tu, alpha * _clamp(tt / edge) * _clamp((dur - tt) / edge)))
        return [kf_group("KFTypeScaleX", sc), kf_group("KFTypeAlpha", al)]
    pop, flo = "pop" in motion, "float" in motion
    if not (pop or flo) or dur < 0.3:
        return []
    POP = min(0.35, dur * 0.4)
    OUT = min(0.15, dur * 0.2)
    ts = {0.0, dur}
    if pop:
        ts |= {POP * k / 6 for k in range(7)} | {dur - OUT, dur - OUT / 2}
    if flo:
        k = 0.4
        while k < dur:
            ts.add(round(k, 3)); k += 0.4
    ts = sorted(v for v in ts if 0 <= v <= dur)
    s = strength
    sc, al, px, py, rt = [], [], [], [], []
    for tt in ts:
        f, a = 1.0, 1.0
        if pop:
            f *= 0.6 + 0.4 * _out_back(_clamp(tt / POP))
            a *= _clamp(tt / (POP * 0.6))
            q = _clamp((tt - (dur - OUT)) / OUT)
            f *= 1.0 - 0.08 * q
            a *= 1.0 - q
        dx = dy = dr = 0.0
        if flo:
            f *= 1.0 + 0.025 * s * math.sin(2 * math.pi * tt / 2.2) + 0.04 * s * (tt / dur)
            dy = 0.012 * s * math.sin(2 * math.pi * tt / 1.7)
            dx = 0.008 * s * math.sin(2 * math.pi * tt / 2.6 + 1.0)
            dr = 1.2 * s * math.sin(2 * math.pi * tt / 3.1)
        tu = int(round(tt * 1_000_000))
        sc.append((tu, scale * f)); al.append((tu, alpha * a))
        px.append((tu, x + dx)); py.append((tu, y + dy)); rt.append((tu, rot + dr))
    groups = [kf_group("KFTypeScaleX", sc)]
    if flo:
        groups += [kf_group("KFTypePositionX", px), kf_group("KFTypePositionY", py), kf_group("KFTypeRotation", rt)]
    if pop:
        groups.append(kf_group("KFTypeAlpha", al))
    return groups


def build_video_overlay_segment(draft: dict, material_id: str, at_ms: int, duration_ms: int,
                                source_in_ms: int, volume: float, x: float, y: float,
                                scale: float, rotation: float, alpha: float) -> dict:
    """A video segment for the overlay track, helpers cloned the way inject_edl does."""
    tpl_helpers = load_template("segment_helpers.json")
    id_map = {}
    for cat, items in tpl_helpers.items():
        for item in items:
            h = deep_copy(item)
            h["id"] = new_uuid()
            id_map[item["id"]] = h["id"]
            draft.setdefault("materials", {}).setdefault(cat, []).append(h)
    seg = deep_copy(load_template("video_segment.json"))
    seg["id"] = new_uuid()
    seg["material_id"] = material_id
    seg["extra_material_refs"] = [id_map[r] for r in seg["extra_material_refs"] if r in id_map]
    seg["source_timerange"] = {"start": source_in_ms * MS, "duration": duration_ms * MS}
    seg["target_timerange"] = {"start": at_ms * MS, "duration": duration_ms * MS}
    seg["volume"] = volume
    seg["last_nonzero_volume"] = 1.0
    seg["clip"] = {
        "scale": {"x": scale, "y": scale},
        "rotation": rotation,
        "transform": {"x": x, "y": y},
        "flip": {"horizontal": False, "vertical": False},
        "alpha": alpha,
    }
    seg["uniform_scale"] = {"on": True, "value": scale}
    return seg


def build_extra_refs(draft: dict) -> list[str]:
    """Mint the 7 refs a photo segment needs.

    From inspection of CapCut UI-built segment: speeds, placeholder_infos,
    canvases, material_animations, sound_channel_mappings, material_colors,
    vocal_separations.
    """
    refs = []
    for category in ("speeds", "placeholder_infos", "canvases",
                     "material_animations", "sound_channel_mappings",
                     "material_colors", "vocal_separations"):
        tpl = load_template(f"photo/{category}.json")
        obj = deep_copy(tpl)
        obj["id"] = new_uuid()
        draft.setdefault("materials", {}).setdefault(category, []).append(obj)
        refs.append(obj["id"])
    return refs


def build_overlay_segment(material_id: str, at_ms: int, duration_ms: int,
                       x: float, y: float, scale: float, rotation: float,
                       alpha: float, extra_refs: list[str]) -> dict:
    """Build a photo segment placing the overlay at given position + scale + duration."""
    tpl = load_template("photo/photo_segment.json")
    seg = deep_copy(tpl)
    seg["id"] = new_uuid()
    seg["material_id"] = material_id
    seg["extra_material_refs"] = extra_refs
    seg["source_timerange"] = {"start": 0, "duration": duration_ms * MS}
    seg["target_timerange"] = {"start": at_ms * MS, "duration": duration_ms * MS}
    seg["volume"] = 1.0
    seg["last_nonzero_volume"] = 1.0
    seg["clip"] = {
        "scale": {"x": scale, "y": scale},
        "rotation": rotation,
        "transform": {"x": x, "y": y},
        "flip": {"horizontal": False, "vertical": False},
        "alpha": alpha,
    }
    seg["uniform_scale"] = {"on": True, "value": scale}
    return seg


def get_or_create_overlay_video_track(draft: dict) -> dict:
    """Find an existing overlay video track (render_index > 0) or create new.

    Overlays live ABOVE the main video, so render_index must be > 0. We append to
    the highest existing overlay track, or create a new one.
    """
    tracks = draft.setdefault("tracks", [])
    video_tracks = [t for t in tracks if t.get("type") == "video"]
    # Tracks beyond index 0 are overlays
    overlay_tracks = [t for t in video_tracks if any(
        (s.get("track_render_index") or 0) > 0 for s in t.get("segments", [])
    )]
    if overlay_tracks:
        return overlay_tracks[0]
    # Create new overlay track - copy attribute shape from the main video track
    main = video_tracks[0] if video_tracks else None
    new_track = {
        "attribute": 0 if not main else main.get("attribute", 0),
        "flag": 0,
        "id": new_uuid(),
        "is_default_name": True,
        "name": "",
        "segments": [],
        "type": "video",
    }
    tracks.append(new_track)
    return new_track


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="CapCut project name")
    ap.add_argument("--overlay-timing", required=True, help="Path to overlay timing JSON")
    ap.add_argument("--drafts-dir", help="Override CapCut drafts dir")
    ap.add_argument("--force-kill", action="store_true",
                    help="Kill a running CapCut instead of refusing. Unsaved work is lost.")
    ap.add_argument("--motion", choices=("none", "pop", "float", "pop+float", "push"), default=None,
                    help="movement for the overlays (default: pop+float for images, none for video/B-roll; a per-entry \"motion\" wins)")
    ap.add_argument("--motion-strength", type=float, default=1.0, help="0.5 = calmer, 2.0 = stronger (default 1.0)")
    args = ap.parse_args()

    drafts = Path(args.drafts_dir) if args.drafts_dir else CAPCUT_DRAFTS_DIR
    project_dir = drafts / args.project
    if not project_dir.exists():
        emit_err(op="add-overlay", error=f"Project not found: {project_dir}")

    timing_path = Path(args.overlay_timing).expanduser()
    if not timing_path.exists():
        emit_err(op="add-overlay", error=f"Timing JSON not found: {timing_path}")

    overlays = json.load(open(timing_path, encoding="utf-8")).get("overlays", [])
    if not overlays:
        emit_err(op="add-overlay", error="No 'overlays' entries in timing JSON")

    # Rule #7: a live CapCut auto-saves over our write. Refuse rather than SIGKILL
    # a GUI holding unsaved work unless the buyer explicitly passed --force-kill.
    ok, close_detail = ensure_capcut_closed(args.force_kill)
    if not ok:
        emit_err(op="add-overlay", error=close_detail)

    # blank project -> clean error (rule #23)
    draft, draft_path, _tl_uuid = load_draft(project_dir)
    before_draft = deep_copy(draft)

    existing_track_ids = {t.get("id") for t in (draft.get("tracks") or [])}
    overlay_track = get_or_create_overlay_video_track(draft)
    track_is_new = overlay_track.get("id") not in existing_track_ids
    materials_before = set()
    for _cat, _items in (draft.get("materials") or {}).items():
        if isinstance(_items, list):
            for _i in _items:
                if isinstance(_i, dict) and _i.get("id"):
                    materials_before.add(_i["id"])
    # Compute next render_index - highest existing + 1
    max_render = 0
    for t in draft.get("tracks", []):
        for s in t.get("segments", []):
            ri = s.get("render_index", 0) or 0
            if ri > max_render:
                max_render = ri
    next_render_idx = max(1, max_render + 1)

    added = []
    for i, entry in enumerate(overlays):
        overlay_file = entry["file"]
        at_ms = int(entry["at_ms"])
        duration_ms = int(entry.get("duration_ms", 1500))
        x = float(entry.get("x", 0.0))
        y = float(entry.get("y", 0.0))
        scale = float(entry.get("scale", 0.5))
        rotation = float(entry.get("rotation", 0))
        alpha = float(entry.get("alpha", 1.0))

        if not os.path.isabs(overlay_file) and os.path.sep not in overlay_file:
            pass
        else:
            overlay_file = str(Path(overlay_file).expanduser().resolve())

        vid = find_video_material(draft, overlay_file, project_dir)
        if vid is not None:
            mat_id, w, h, native_dur = vid
            src_in = int(entry.get("source_in_ms", 0))
            room_ms = max(0, native_dur // MS - src_in)
            if room_ms < 100:
                emit_err(op="add-overlay",
                         error=f"{os.path.basename(overlay_file)}: source_in_ms {src_in} "
                               f"อยู่เลยท้ายคลิป (past the end of the clip, {native_dur // MS} ms)")
            duration_ms = min(duration_ms, room_ms)
            seg = build_video_overlay_segment(
                draft, mat_id, at_ms, duration_ms, src_in,
                float(entry.get("volume", 0.0)), x, y, scale, rotation, alpha)
        else:
            mat_id, w, h, native_dur = find_overlay_material(draft, overlay_file, project_dir)
            refs = build_extra_refs(draft)
            seg = build_overlay_segment(mat_id, at_ms, duration_ms,
                                      x, y, scale, rotation, alpha, refs)
        seg["render_index"] = next_render_idx + i
        seg["track_render_index"] = 2 # overlay layer
        motion = str(entry.get("motion") or args.motion or ("none" if vid is not None else ("push" if scale >= 0.85 else "pop+float")))
        kfs = motion_keyframes(motion, duration_ms, scale, x, y, rotation, alpha,
                               float(entry.get("motion_strength", args.motion_strength)))
        if kfs:
            seg["common_keyframes"] = kfs
            seg.setdefault("keyframe_refs", [])
        overlay_track["segments"].append(seg)

        added.append({
            "file": os.path.basename(overlay_file),
            "at_ms": at_ms,
            "duration_ms": duration_ms,
            "x": x, "y": y, "scale": scale,
            "size": f"{w}x{h}",
            "kind": "video" if vid is not None else "photo",
            "motion": motion if kfs else "none",
            "keyframe_groups": len(kfs),
        })

    # This op only ADDS overlay segments (plus any material they needed) onto one
    # track. Declaring that and checking it stops an add from silently rewriting a
    # clip or dropping a material another segment still uses.
    materials_after = set()
    for _cat, _items in (draft.get("materials") or {}).items():
        if isinstance(_items, list):
            for _i in _items:
                if isinstance(_i, dict) and _i.get("id"):
                    materials_after.add(_i["id"])
    # assert_preserved compares materials category-by-category, so a category this
    # op CREATES (canvases/material_colors/... a text-only draft never had) reads as
    # "added unexpectedly" even though every entry in it is planned. Seed the missing
    # keys as empty lists on the BEFORE side so the diff is [] -> [planned ids].
    for _cat in (draft.get("materials") or {}):
        if isinstance(draft["materials"][_cat], list):
            before_draft.setdefault("materials", {}).setdefault(_cat, [])
    plan = {
        "add": {"tracks": [overlay_track["id"]] if track_is_new else [],
                "materials": sorted(materials_after - materials_before)},
        "replace": {"tracks": [] if track_is_new else [overlay_track["id"]]},
        "delete": {"segments": [], "materials": []},
    }
    try:
        assert_preserved(before_draft, draft, plan)
    except ValueError as e:
        emit_err(op="add-overlay", error=str(e))

    with begin_op(project_dir, "add-overlay", plan=plan) as op:
        atomic_write_text(draft_path, json.dumps(draft, indent=2, ensure_ascii=False))
        mirror = mirror_9_places(project_dir)
        op.check_mirror(mirror)

    emit_ok(
        op="add-overlay",
        project=args.project,
        overlays_added=len(added),
        details=added,
        mirror_paths=len(mirror.get("copies", [])),
        capcut_close=close_detail,
        next_step="เปิด CapCut → " + args.project + " → ดู timeline ว่า overlay อยู่ตรงตำแหน่งที่กำหนด",
        **op.info(),
    )


if __name__ == "__main__":
    main()
