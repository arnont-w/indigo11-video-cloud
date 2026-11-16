#!/usr/bin/env python3
"""Read CapCut Media panel - enumerate valid clips ready for editing.

Reads <project>/draft_meta_info.json. Filters ghost entries per edit-rules #13.

Usage:
  python3 read_media.py --project <name>
  python3 read_media.py --project <name> --count-only # just print integer

Output JSON: {"ok": true, "project": "...", "clips": [...]}
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
    CAPCUT_DRAFTS_DIR, emit_err, emit_ok, read_media_panel, resolve_project,
)


# Save+quit keystrokes differ per OS (edit-rules #7/#13). The kit sells on both,
# so never hardcode the macOS chord in a buyer-facing message.
_SAVE_QUIT_HINT = "Ctrl+S → Ctrl+Q (หรือ Alt+F4)" if os.name == "nt" else "Cmd+S → Cmd+Q"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True, help="Project name (folder under CapCut drafts)")
    ap.add_argument("--count-only", action="store_true", help="Print just the integer count")
    ap.add_argument("--drafts-dir", default=str(CAPCUT_DRAFTS_DIR))
    args = ap.parse_args()

    project_dir = resolve_project(args.project, args.drafts_dir)
    if not project_dir.exists():
        if args.count_only:
            print(0)
            return 0
        emit_err(f"ไม่พบ project: {args.project} (project not found)",
                 fix=f"เปิด CapCut → สร้าง project ชื่อ '{args.project}' → ลาก video ลง Media panel → "
                     f"{_SAVE_QUIT_HINT}")

    clips = read_media_panel(project_dir)
    if args.count_only:
        print(len(clips))
        return 0

    if not clips:
        emit_err(
            f"Media panel ใน {args.project} ว่างเปล่า (Media panel empty)",
            fix=f"เปิด CapCut → ลาก video ลง Media panel → {_SAVE_QUIT_HINT}",
            project=args.project,
            project_dir=str(project_dir),
        )

    total_dur_s = round(sum(c["duration_s"] for c in clips), 2)
    emit_ok(
        op="read-media",
        project=args.project,
        project_dir=str(project_dir),
        clips=clips,
        clip_count=len(clips),
        total_duration_s=total_dur_s,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
