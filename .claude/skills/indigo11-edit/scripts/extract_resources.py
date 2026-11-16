#!/usr/bin/env python3
"""extract_resources.py - harvest EVERY resource_id the buyer placed in a CapCut
project, straight from their own CapCut version (version-proof).

CapCut's stickers / fancy-text / bubbles / effects / filters / transitions /
animations come from an online library whose resource_ids change per version &
region. There is NO public search API. So the robust way to get valid ids is:
the buyer adds the items they want in CapCut once -> save -> we read them back
out of the draft. This walks the draft's `materials` and pulls every object that
carries a `resource_id` (and its human name + category), so you build a catalog
that is guaranteed to render on THIS machine's CapCut.

  python extract_resources.py --project <name>            # pretty report
  python extract_resources.py --project <name> --json out.json   # save catalog

Reads the canonical Timelines/<UUID>/draft_content.json (per-OS, via _capcut_lib).
"""
from __future__ import annotations
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _capcut_lib import (  # noqa: E402
    CAPCUT_DRAFTS_DIR, draft_state_path, emit_err, resolve_project,
)

# Fields that, when present on a material dict, identify a reusable library resource.
_ID_FIELDS = ("resource_id", "effect_id", "sticker_id", "font_id", "filter_id")
_NAME_FIELDS = ("name", "title", "category_name")


def _walk(obj, category, out):
    """Recursively collect any dict carrying a resource id (handles nested
    structures like material_animations[].animations[] and text content)."""
    if isinstance(obj, dict):
        rid = next((obj[f] for f in _ID_FIELDS if obj.get(f)), None)
        if rid:
            name = next((obj[f] for f in _NAME_FIELDS if obj.get(f)), "")
            out.append({
                "category": category,
                "resource_id": rid,
                "name": name,
                "type": obj.get("type") or obj.get("category") or "",
                "path": obj.get("path", ""),
            })
        for v in obj.values():
            _walk(v, category, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, category, out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True, help="CapCut project name (where you placed the resources)")
    ap.add_argument("--json", dest="out", help="write the full catalog to this JSON path")
    ap.add_argument("--drafts-dir", default=str(CAPCUT_DRAFTS_DIR))
    args = ap.parse_args()

    project_dir = resolve_project(args.project, args.drafts_dir)
    if not project_dir.exists():
        emit_err(f"Project not found: {project_dir} (ไม่พบ project)")

    # A freshly-created/saved library project may have an empty or missing Timelines/
    # (rule #23: blank-but-saved is the expected first state). draft_state_path ->
    # find_timeline_uuid raises in that case; turn it into the JSON error contract
    # instead of a traceback.
    try:
        draft = json.load(open(draft_state_path(project_dir), encoding="utf-8"))
    except (FileNotFoundError, RuntimeError, ValueError) as e:
        emit_err(
            f"{args.project} ยังว่างหรือยังไม่ได้ save - เปิด CapCut ใส่ resource แล้ว Cmd+S ก่อน "
            f"({args.project} is empty or unsaved - add resources in CapCut and Cmd+S first)",
            detail=str(e),
        )
    materials = draft.get("materials", {})

    found = []
    for cat, items in materials.items():
        _walk(items, cat, found)

    # de-dup by (category, resource_id)
    seen, catalog = set(), []
    for r in found:
        k = (r["category"], r["resource_id"])
        if k not in seen:
            seen.add(k)
            catalog.append(r)

    # Self-contained version-proof catalog: keep the FULL top-level material object
    # for each resource so a capability script can COPY the buyer's own material
    # (the exact resource_id their CapCut accepts) into another project - no reliance
    # on pyCapCut's bundled (older-version) enums. Keyed by resource_id.
    materials_raw = {}
    for cat, items in materials.items():
        if not isinstance(items, list):
            continue
        for it in items:
            if isinstance(it, dict):
                rid = next((it[f] for f in _ID_FIELDS if it.get(f)), None)
                if rid and rid not in materials_raw:
                    materials_raw[rid] = {"category": cat, "material": it}

    # group counts by category for the report
    by_cat = {}
    for r in catalog:
        by_cat.setdefault(r["category"], []).append(r)

    result = {
        "ok": True,
        "op": "extract-resources",
        "project": args.project,
        "total_resources": len(catalog),
        "categories": {c: len(v) for c, v in sorted(by_cat.items())},
        "catalog": catalog,
        # full material objects (only written to --json file, not the stdout report)
        "materials_raw": materials_raw,
    }

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # human report
    print(f"resources found: {result['total_resources']}  (project {args.project})")
    for c, items in sorted(by_cat.items()):
        print(f"\n[{c}]  x{len(items)}")
        for r in items[:30]:
            print(f"  - {r['resource_id']}   {r['name']}  {('('+r['type']+')') if r['type'] else ''}")
    if args.out:
        print(f"\ncatalog written: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
