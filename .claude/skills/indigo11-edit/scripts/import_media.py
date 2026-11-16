#!/usr/bin/env python3
"""import_media.py - Register the buyer's OWN media files into a CapCut project's
Media panel by patching JSON, so they never have to drag-import by hand.

WHAT THIS IS NOT: it does NOT fetch anything from CapCut's online library. CapCut's
sticker/effect/sound catalog is served by private ByteDance endpoints whose requests
carry a runtime x-gorgon/x-argus signature; a synthetic client is the clearest possible
bot signal and can BAN the buyer's account. Never call those endpoints. Library
resources come from the buyer's own browse-cache instead (scan_capcut_cache.py) and
are applied with capcut_apply_resource.py - see edit-rules #33.

This script only handles LOCAL files the buyer already owns (SFX, memes, images, GIFs,
B-roll, music - including anything indigo11-find-assets downloaded). Nothing leaves the
machine.

Registration is three JSON writes - there is NO hidden SQLite/binary registry (the
sandbox container holds only WebKit/crash-handler DBs, which are unrelated to media):
  1. draft_meta_info.json      -> draft_materials[type=0].value[]  (the Media panel list)
  2. draft_virtual_store.json  -> type=1 value[] {child_id, parent_id}
  3. the 9-place mirror        -> so CapCut does not self-heal the change away

UUID case matters (rule #26): draft_meta_info + draft_virtual_store use LOWERCASE ids.

After import the files behave exactly like drag-imported ones, so add_sfx.py /
add_overlay.py / inject_edl.py can place them on the timeline.

Usage:
  python3 import_media.py --project 0824 --files a.mp3 b.jpg
  python3 import_media.py --project 0824 --dir ~/assets --types audio,image
  python3 import_media.py --project 0824 --dir ~/assets --recursive --dry-run
"""
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _capcut_lib import (  # noqa: E402
    new_uuid_lower, mirror_9_places, resolve_project, ensure_capcut_closed,
    emit_err, _force_utf8_stdio, begin_op,
)

# CapCut's metetype per media kind. Photos get a default on-timeline duration
# because a still has no intrinsic length.
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".aiff", ".aif"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic", ".tiff"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
PHOTO_DEFAULT_US = 5_000_000


def kind_of(path: Path):
    e = path.suffix.lower()
    if e in AUDIO_EXT:
        return "music"
    if e in IMAGE_EXT:
        return "photo"
    if e in VIDEO_EXT:
        return "video"
    return None


def probe(path: Path):
    """(duration_us, width, height) via ffprobe. A still returns duration 0."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-select_streams", "v:0", "-show_entries", "stream=width,height",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60)
        d = json.loads(r.stdout or "{}")
        dur = float((d.get("format") or {}).get("duration") or 0)
        st = (d.get("streams") or [{}])[0]
        return int(dur * 1_000_000), int(st.get("width") or 0), int(st.get("height") or 0)
    except Exception:
        return 0, 0, 0


def build_entry(path: Path, metetype: str):
    dur, w, h = probe(path)
    if metetype == "photo":
        dur = PHOTO_DEFAULT_US
    if metetype != "photo" and dur <= 0:
        return None, "ffprobe could not read a duration (corrupt or unsupported file)"
    now = int(time.time())
    return {
        "ai_group_type": "",
        "create_time": now,
        "duration": dur,
        "enter_from": 0,
        "extra_info": path.name,          # the label shown in the Media panel
        "file_Path": str(path),           # native absolute path (rule #13)
        "height": h,
        "id": new_uuid_lower(),           # LOWERCASE here (rule #26)
        "import_time": now,
        "import_time_ms": int(time.time() * 1_000_000),
        "item_source": 1,
        "material_color_tag": "",
        "md5": "",
        "metetype": metetype,
        "roughcut_time_range": {"duration": dur, "start": 0},
        "sub_time_range": {"duration": -1, "start": -1},
        "type": 0,
        "width": w,
    }, None


def collect(args):
    files = []
    for f in args.files or []:
        files.append(Path(f).expanduser().resolve())
    if args.dir:
        base = Path(args.dir).expanduser().resolve()
        it = base.rglob("*") if args.recursive else base.glob("*")
        files += sorted(p for p in it if p.is_file())
    wanted = None
    if args.types:
        m = {"audio": "music", "image": "photo", "video": "video",
             "music": "music", "photo": "photo", "sfx": "music"}
        wanted = {m[t.strip().lower()] for t in args.types.split(",") if t.strip().lower() in m}
    out = []
    for p in files:
        if p.name.startswith("."):
            continue
        k = kind_of(p)
        if not k or (wanted and k not in wanted):
            continue
        out.append((p, k))
    return out


def main():
    _force_utf8_stdio()
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--files", nargs="*", help="explicit files to import")
    ap.add_argument("--dir", help="import every supported media file in this folder")
    ap.add_argument("--recursive", action="store_true")
    ap.add_argument("--types", help="filter: audio,image,video (default: all)")
    ap.add_argument("--drafts-dir")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force-kill", action="store_true",
                    help="Kill a running CapCut instead of refusing. Unsaved work is lost.")
    args = ap.parse_args()

    proj = resolve_project(args.project, args.drafts_dir)
    if not proj.is_dir():
        emit_err(f"ไม่เจอ project {args.project} (project not found)", project_dir=str(proj))

    meta_p = proj / "draft_meta_info.json"
    vs_p = proj / "draft_virtual_store.json"
    if not meta_p.exists():
        emit_err(f"{proj.name} ไม่มี draft_meta_info.json - เปิด CapCut แล้ว Cmd+S ก่อน "
                 f"({proj.name} has no draft_meta_info.json - open it in CapCut and save first)")

    items = collect(args)
    if not items:
        emit_err("ไม่เจอไฟล์ที่รองรับ (no supported media files found)",
                 hint="audio: mp3/wav/m4a · image: jpg/png/webp/gif · video: mp4/mov")

    meta = json.load(open(meta_p, encoding="utf-8"))
    vs = json.load(open(vs_p, encoding="utf-8")) if vs_p.exists() else \
        {"draft_materials": [], "draft_virtual_store": [{"type": 1, "value": []}]}

    grp0 = next((g for g in meta.setdefault("draft_materials", []) if g.get("type") == 0), None)
    if grp0 is None:
        grp0 = {"type": 0, "value": []}
        meta["draft_materials"].append(grp0)
    grp0.setdefault("value", [])
    existing = {e.get("file_Path") for e in grp0["value"]}

    vgrp = next((g for g in vs.setdefault("draft_virtual_store", []) if g.get("type") == 1), None)
    if vgrp is None:
        vgrp = {"type": 1, "value": []}
        vs["draft_virtual_store"].append(vgrp)
    vgrp.setdefault("value", [])

    added, skipped, failed = [], [], []
    for path, metetype in items:
        if str(path) in existing:
            skipped.append({"file": path.name, "why": "already in Media panel"})
            continue
        entry, err = build_entry(path, metetype)
        if err:
            failed.append({"file": path.name, "why": err})
            continue
        if not args.dry_run:
            grp0["value"].append(entry)
            vgrp["value"].append({"child_id": entry["id"], "parent_id": ""})
        existing.add(str(path))
        added.append({"file": path.name, "type": metetype,
                      "duration_s": round(entry["duration"] / 1_000_000, 2),
                      "size": [entry["width"], entry["height"]]})

    result = {"ok": True, "op": "import-media", "project": proj.name,
              "added": added, "skipped": skipped, "failed": failed,
              "added_count": len(added)}

    if args.dry_run:
        result["dry_run"] = True
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if added:
        # CapCut overwrites our files from memory within ~30s if it is alive (rule #7).
        ok, close_detail = ensure_capcut_closed(args.force_kill)
        if not ok:
            emit_err(close_detail)
        result["capcut_close"] = close_detail
        # begin_op's "import-media" target list includes draft_virtual_store.json,
        # which no other op writes - so a failed import is reversible too.
        # The plan is EMPTY on purpose: this op registers Media-panel entries in
        # draft_meta_info.json / draft_virtual_store.json and never touches a
        # timeline track or material, so any track change the read-back sees is a
        # real defect, not this op's doing.
        with begin_op(proj, "import-media", plan={}) as op:
            json.dump(meta, open(meta_p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            json.dump(vs, open(vs_p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            try:
                mirror = mirror_9_places(proj)
                op.check_mirror(mirror)
                result["mirrored"] = True
            except (FileNotFoundError, ValueError) as e:
                # A blank-but-saved project (rule #23) is TWO shapes, not one: no
                # Timelines/ dir yet (FileNotFoundError), or a 0-byte draft file that
                # EXISTS and makes json.load raise JSONDecodeError - a ValueError.
                # Catching only the first rolled back a perfectly valid import on the
                # commonest first-run project. The Media panel reads
                # draft_meta_info.json, so the import itself stands either way; a
                # PARTIAL mirror still rolls back through check_mirror.
                result["mirrored"] = False
                result["mirror_note"] = f"timeline not mirrored ({e}) - normal for a blank project"
        result.update(op.info())

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
