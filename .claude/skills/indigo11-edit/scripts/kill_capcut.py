#!/usr/bin/env python3
"""Force-close CapCut Desktop. Single-purpose script.

Usage: python3 kill_capcut.py
Outputs JSON with status.
"""
from __future__ import annotations
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _capcut_lib import kill_capcut, is_capcut_running, emit_ok, emit_err # noqa: E402


def main() -> int:
    was_running = is_capcut_running()
    if not was_running:
        emit_ok(status="not_running", message="CapCut ปิดอยู่แล้ว (CapCut wasn't running)")
        return 0
    success = kill_capcut(timeout_s=5)
    if success:
        emit_ok(status="killed", message="ปิด CapCut แล้ว (CapCut killed)")
        return 0
    emit_err(
        "ปิด CapCut ไม่ได้ - กรุณา Cmd+Q เอง (Cannot kill CapCut - Cmd+Q manually)",
        fix="Open CapCut, save (Cmd+S), quit (Cmd+Q), then retry",
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
