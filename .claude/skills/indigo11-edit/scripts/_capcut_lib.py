#!/usr/bin/env python3
"""Shared helpers for indigo11-capcut. NOT a CLI.

Owns:
  - kill_capcut() - pgrep + pkill -9 safety net (edit-rules #7)
  - read_media_panel() - parse draft_meta_info.json, filter ghosts (rule #13)
  - load_templates() - read harvested CapCut JSON snippets
  - mirror_9_places() - sync draft_info.json to 8 siblings + parent catalog (rule #6)
  - emit_ok/emit_err() - single-line JSON stdout contract
  - timeline_us/ms helpers (rule #14)
"""
from __future__ import annotations
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)

import glob
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = SKILL_DIR / "templates"


def _find_kit_root(start: Path) -> Path:
    """The starter folder: the nearest parent with _system/engine/_env.py (.claude/skills/<name>/ -> two levels up).
    Works for the real skill folder and for a Windows copy under .claude/skills/<name>/."""
    for p in [start, *start.parents]:
        if (p / "_system" / "engine" / "_env.py").exists():
            return p
    return start.parent.parent


KIT_ROOT = _find_kit_root(SKILL_DIR)

IS_WINDOWS = os.name == "nt"


# --------------------------- atomic write + lock ---------------------------

def atomic_write_text(path, text: str) -> None:
    """Write text to `path` atomically: temp file in the same dir, fsync, os.replace.

    A crash mid-write can otherwise leave a half-written draft_info.json, which
    mirror_9_places then copies over all 8 mirrors -> total project loss
    (P0-08/H10). os.replace is atomic on both POSIX and Windows (same volume).
    """
    path = Path(path)
    tmp = path.with_name(path.name + ".indigo11tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass  # some filesystems / Windows handles don't support fsync
    os.replace(tmp, path)


def atomic_copy(src, dst) -> None:
    """Copy src -> dst atomically (read src, atomic-write dst). Text-mode, UTF-8."""
    text = Path(src).read_text(encoding="utf-8")
    atomic_write_text(dst, text)


class ProjectLock:
    """Best-effort per-project write lock so two writers don't interleave.

    Uses an exclusive-create lockfile in the project dir. Not a hard kernel lock
    (CapCut itself doesn't honor it), but it stops two indigo11 script runs from
    racing on the same draft. Stale locks (>120s) are reclaimed.
    """
    def __init__(self, project_dir, timeout_s: float = 20.0):
        self.path = Path(project_dir) / ".indigo11-write.lock"
        self.timeout_s = timeout_s
        self.acquired = False

    def __enter__(self):
        deadline = time.time() + self.timeout_s
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                self.acquired = True
                return self
            except FileExistsError:
                # reclaim a stale lock
                try:
                    age = time.time() - self.path.stat().st_mtime
                    if age > 120:
                        self.path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    raise TimeoutError(
                        f"another indigo11 write is in progress on {self.path.parent.name} "
                        f"(lock held >{self.timeout_s}s). If nothing is running, delete {self.path}")
                time.sleep(0.2)

    def __exit__(self, *exc):
        if self.acquired:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass


def _force_utf8_stdio() -> None:
    """Reconfigure stdout/stderr to UTF-8 on Windows.

    Every capcut script prints Thai via `print(json.dumps(..., ensure_ascii=False))`.
    On a Windows Thai console (cp874) that raises UnicodeEncodeError. No-op on
    macOS/Linux (already UTF-8). Imported by every script through this module.
    """
    if not IS_WINDOWS:
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


_force_utf8_stdio()


_TAIL = ("User Data", "Projects", "com.lveditor.draft")


def candidate_drafts_dirs() -> list:
    """All known CapCut/JianYing drafts-folder locations for this OS, in priority
    order. The folder location varies by install (CapCut vs JianYing, default vs
    relocated), so we probe rather than assume one path. CAPCUT_DRAFTS_DIR always
    wins when set.
    """
    cands = []
    env = os.environ.get("CAPCUT_DRAFTS_DIR")
    if env:
        cands.append(Path(env))
    if IS_WINDOWS:
        bases = [
            os.environ.get("LOCALAPPDATA"),
            str(Path.home() / "AppData" / "Local"),
            os.environ.get("APPDATA"),
            str(Path.home() / "AppData" / "Roaming"),
        ]
        for b in bases:
            if b:
                cands.append(Path(b) / "CapCut" / Path(*_TAIL))
    else:
        home = Path.home()
        cands.append(home / "Movies" / "CapCut" / Path(*_TAIL))
        cands.append(home / "Movies" / "JianyingPro" / Path(*_TAIL))
    # de-dupe, preserve order
    seen, out = set(), []
    for c in cands:
        s = str(c)
        if s not in seen:
            seen.add(s)
            out.append(c)
    return out


def default_drafts_dir() -> Path:
    """Best CapCut drafts folder: CAPCUT_DRAFTS_DIR if set, else the first probed
    candidate that actually exists, else the canonical per-OS path.

    macOS:   ~/Movies/CapCut/User Data/Projects/com.lveditor.draft
    Windows: %LOCALAPPDATA%/CapCut/User Data/Projects/com.lveditor.draft
    """
    cands = candidate_drafts_dirs()
    for c in cands:
        if c.exists():
            return c
    return cands[0]


CAPCUT_DRAFTS_DIR = default_drafts_dir()

# CapCut stores the timeline JSON under different filenames per platform:
#   Windows -> draft_content.json     macOS -> draft_info.json
# (see references/capcut-cli-schema/05-version-differences.md). Detect, don't guess.
DRAFT_STATE_NAMES = ("draft_content.json", "draft_info.json")


def draft_state_name(timeline_dir) -> str:
    """Return the draft-state filename actually present in a dir.

    Used for BOTH the project-root copy and the Timelines/<UUID>/ copy - they
    share the same basename on a given platform. When neither exists yet (a
    freshly created empty draft), default to the per-OS convention.
    """
    d = Path(timeline_dir)
    for name in DRAFT_STATE_NAMES:
        if (d / name).exists():
            return name
    return "draft_content.json" if IS_WINDOWS else "draft_info.json"

# Time-unit constants (rule #14)
US = 1
MS = 1_000
S = 1_000_000


# ----------------------------- CapCut process safety -----------------------

def is_capcut_running() -> bool:
    """Detect ANY CapCut-related process (cross-platform).

    macOS: CapCut spawns CEF helpers (Renderer, GPU, utility). The main process
    has command name "CapCut" but the binary path is /Applications/CapCut.app/...
    so `pgrep -fx CapCut` (exact full cmdline) returns empty. We check both
    `pgrep -x CapCut` (process name) and `pgrep -f /CapCut.app/` (binary path).

    Windows: all CapCut processes (main + GPU/renderer helpers) share the image
    name CapCut.exe, so a single `tasklist` filter catches them.
    """
    try:
        if IS_WINDOWS:
            res = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq CapCut.exe", "/NH"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            return "CapCut.exe" in (res.stdout or "")
        for args in (["pgrep", "-x", "CapCut"], ["pgrep", "-f", "/CapCut.app/"]):
            res = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if res.returncode == 0 and res.stdout.strip():
                return True
        return False
    except FileNotFoundError:
        return False


def is_capcut_main_running() -> bool:
    """True when the MAIN CapCut GUI process is alive (not just a stray helper).

    The distinction matters because kill_capcut() used to SIGKILL the editor out
    from under a buyer who had unsaved work open - the 2026-09-18 refund case.
    A helper with no main process is a leftover daemon and is safe to reap; the
    main process holds the buyer's unsaved timeline and must never be killed
    without an explicit confirmation (--force-kill).

    macOS: the main process is the one whose *name* is exactly "CapCut"
    (`pgrep -x`). The CEF Renderer/GPU/Utility helpers match `pgrep -f
    /CapCut.app/` but NOT `-x`, which is what separates them.
    Windows: every CapCut process shares the image name CapCut.exe, so name
    alone cannot tell main from helper. The main window-owning process is the
    one with no CapCut.exe parent; `wmic`/tasklist give us the parent pid, so we
    treat "a CapCut.exe whose parent is not CapCut.exe" as the main process.
    """
    try:
        if IS_WINDOWS:
            res = subprocess.run(
                ["wmic", "process", "where", "name='CapCut.exe'",
                 "get", "ProcessId,ParentProcessId", "/FORMAT:CSV"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            rows = []
            for line in (res.stdout or "").splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3 and parts[-1].isdigit() and parts[-2].isdigit():
                    rows.append((int(parts[-1]), int(parts[-2])))  # (pid, ppid)
            if rows:
                pids = {pid for pid, _ in rows}
                return any(ppid not in pids for pid, ppid in rows)
            # wmic is absent on newer Windows builds; fall back to "any CapCut.exe
            # is the main one" - the SAFE direction (refuse rather than kill).
            return is_capcut_running()
        res = subprocess.run(["pgrep", "-x", "CapCut"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace")
        return res.returncode == 0 and bool(res.stdout.strip())
    except FileNotFoundError:
        return False


CAPCUT_OPEN_MSG = (
    "CapCut เปิดอยู่ - กด Cmd+S แล้ว Cmd+Q ก่อน (CapCut is open - save + quit first). "
    "ถ้าแน่ใจว่าไม่มีงานค้าง ใช้ --force-kill (use --force-kill only after confirming "
    "with the buyer that nothing is unsaved)"
)


def ensure_capcut_closed(force: bool = False, timeout_s: int = 5) -> tuple:
    """The non-destructive front door every write op should call instead of kill_capcut().

    Returns (ok: bool, detail: str).

    - Main GUI alive and not `force` -> (False, CAPCUT_OPEN_MSG). Nothing is
      killed; the buyer's unsaved timeline survives.
    - Only helpers/daemons lingering (rule #7's real target: lvoverseas, the crash
      handler, orphaned CEF helpers that still auto-save over our files) -> reap
      them and return (True, ...).
    - `force` -> full kill_capcut(), the pre-2.4.0 behaviour, gated on an explicit
      buyer confirmation at the SKILL.md layer.
    """
    if not is_capcut_running():
        return True, "capcut not running"
    if is_capcut_main_running() and not force:
        return False, CAPCUT_OPEN_MSG
    dead = kill_capcut(timeout_s=timeout_s)
    if not dead:
        return False, ("ปิด CapCut ไม่ได้ - Cmd+Q เอง (could not close CapCut - quit it manually)")
    return True, ("force-killed CapCut (main GUI)" if force else
                  "reaped leftover CapCut helpers (no main GUI was running)")


def kill_capcut(timeout_s: int = 5) -> bool:
    """Kill ALL CapCut processes (main + helpers). Returns True if dead.

    Per edit-rules #7 this is the SAFETY NET that runs after the buyer has
    quit, not a substitute for them quitting. Callers must go through
    ensure_capcut_closed() so a live GUI with unsaved work is refused instead of
    SIGKILLed; this function itself is unconditional by design (--force-kill and
    the kill_capcut.py CLI are its only direct callers).

    macOS strategy: pkill -9 by name + by /CapCut.app/ path + lvoverseas daemon
    + crash handler.
    Windows strategy: taskkill /F /T /IM CapCut.exe (kills the process tree, so
    GPU/renderer helpers go too).
    """
    if not is_capcut_running():
        return True
    print("WARN: CapCut running - killing (CapCut เปิดอยู่ - กำลังปิด)", file=sys.stderr)
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/F", "/T", "/IM", "CapCut.exe"], capture_output=True)
    else:
        # Kill main process by exact name
        subprocess.run(["pkill", "-9", "-x", "CapCut"], capture_output=True)
        # Kill all CEF helper processes whose binary path contains /CapCut.app/
        subprocess.run(["pkill", "-9", "-f", "/CapCut.app/"], capture_output=True)
        # Container daemon
        subprocess.run(["pkill", "-9", "-f", "lvoverseas"], capture_output=True)
        # Crash handler
        subprocess.run(["pkill", "-9", "-f", "parfait_crash_handler"], capture_output=True)
    time.sleep(timeout_s)
    return not is_capcut_running()


# ----------------------------- Media panel read ----------------------------

def read_media_panel(project_dir: Path) -> list:
    """Return a list of valid video clips from CapCut's Media panel.

    Reads <project>/draft_meta_info.json's draft_materials[type=0].value[].
    Filters ghost entries per rule #13.

    Returns: [{"index": int, "id": str, "path": str, "name": str,
               "duration_us": int, "duration_s": float, "width": int, "height": int}]
    """
    meta_path = project_dir / "draft_meta_info.json"
    if not meta_path.exists():
        return []
    meta = json.load(open(meta_path, encoding="utf-8"))
    clips = []
    idx = 0
    for entry in meta.get("draft_materials", []):
        if entry.get("type") != 0:
            continue
        for v in entry.get("value", []):
            path = v.get("file_Path", "") or v.get("file_path", "")
            dur_us = v.get("duration", 0)
            if not path or not os.path.exists(path) or dur_us < 100_000:
                continue
            # A type==0 group can hold image/audio assets (metetype "photo"/"music")
            # the buyer dragged in. Those must NOT be returned as video clips or
            # inject_edl builds a broken video segment from them (M-CAP2). Accept
            # only video metetypes (empty metetype = legacy/unknown -> allow).
            mt = str(v.get("metetype", "")).lower()
            if mt and mt not in ("video", "movie"):
                continue
            clips.append({
                "index": idx,
                "id": v.get("id", ""),
                "path": path,
                "name": v.get("extra_info", "") or os.path.basename(path),
                "duration_us": dur_us,
                "duration_s": round(dur_us / S, 3),
                "width": v.get("width", 0),
                "height": v.get("height", 0),
            })
            idx += 1
    return clips


# ----------------------------- Templates -----------------------------------

def load_template(name: str) -> dict:
    """Load a harvested CapCut JSON snippet from templates/."""
    path = TEMPLATES_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Template missing: {path}")
    return json.load(open(path, encoding="utf-8"))


def deep_copy(obj):
    return json.loads(json.dumps(obj))


def new_uuid() -> str:
    """Generate UPPERCASE UUID for draft_info.json (matches CapCut's convention there).

    NOTE: draft_meta_info.json AND draft_virtual_store.json use LOWERCASE UUIDs.
    For those files, use new_uuid_lower() instead. Mixing cases causes CapCut to
    show "File not accessible" because lookups are case-sensitive within each file.
    """
    return str(uuid.uuid4()).upper()


def new_uuid_lower() -> str:
    """Generate LOWERCASE UUID for draft_meta_info.json and draft_virtual_store.json."""
    return str(uuid.uuid4()).lower()


# ----------------------------- 9-place mirror ------------------------------

def find_timeline_uuid(project_dir: Path) -> str:
    """Return the UUID of the Timelines/<UUID>/ subfolder.

    Only directories named like a UUID count - Timelines/ also holds
    project.json / .bak files. A valid draft has exactly ONE such dir; more than
    one means a botched clone (the bug that made CapCut reject the draft), so we
    fail loudly rather than guess.
    """
    tdir = project_dir / "Timelines"
    if not tdir.exists():
        raise FileNotFoundError(f"Timelines/ missing: {tdir}")
    uuid_dirs = [c.name for c in tdir.iterdir()
                 if c.is_dir() and len(c.name) == 36 and c.name.count("-") == 4]
    if len(uuid_dirs) == 1:
        return uuid_dirs[0]
    if not uuid_dirs:
        raise FileNotFoundError(f"No timeline UUID dir under {tdir}")
    raise RuntimeError(f"{len(uuid_dirs)} timeline UUID dirs under {tdir} "
                       f"(expected 1): {uuid_dirs} - clone is malformed")


def draft_state_path(project_dir, tl_uuid: str = None) -> Path:
    """Absolute path to the canonical timeline JSON (the file CapCut reads on open).

    Resolves the per-platform basename (draft_content.json on Windows,
    draft_info.json on macOS). This is what every write op should patch.
    """
    project_dir = Path(project_dir)
    if tl_uuid is None:
        tl_uuid = find_timeline_uuid(project_dir)
    tl_dir = project_dir / "Timelines" / tl_uuid
    return tl_dir / draft_state_name(tl_dir)


def mirror_9_places(project_dir: Path) -> dict:
    """Sync Timelines/<UUID>/<state>.json into the 8 sibling files + parent catalog.

    Per edit-rules #6. CapCut self-heals to old state otherwise. The state
    basename is platform-dependent (draft_content.json on Windows,
    draft_info.json on macOS) and detected automatically.
    Returns a dict of paths touched.
    """
    project_dir = Path(project_dir)
    tl_uuid = find_timeline_uuid(project_dir)
    tl_dir = project_dir / "Timelines" / tl_uuid
    state = draft_state_name(tl_dir)
    source = tl_dir / state
    if not source.exists():
        raise FileNotFoundError(f"Source {state} missing: {source}")

    # Get duration for catalog update
    draft = json.load(open(source, encoding="utf-8"))
    total_us = int(draft.get("duration", 0))

    touched = {"copies": [], "catalog_updated": False, "warnings": [], "failed": []}

    # Every copy is atomic (temp+replace) and guarded: a failure on ANY mirror is
    # collected in `failed` rather than aborting half-mirrored (P0-08/H11). Callers
    # must treat a non-empty `failed` as an error - a partial mirror lets CapCut
    # self-heal to the OLD state (rule #6), silently discarding the edit.
    def _mirror(dst):
        try:
            atomic_copy(source, dst)
            touched["copies"].append(str(dst))
        except Exception as e:
            touched["failed"].append({"path": str(dst), "error": str(e)})

    # 1-3: project-root mirrors
    for name in (state, state + ".bak", "template-2.tmp"):
        _mirror(project_dir / name)

    # 4: draft_meta_info.json - patch tm_duration. CapCut writes these files
    # MINIFIED (no newlines/indent); match that exactly so the draft is
    # byte-shaped like one CapCut produced (see capcut-schema-master.md).
    meta_path = project_dir / "draft_meta_info.json"
    if meta_path.exists():
        try:
            meta = json.load(open(meta_path, encoding="utf-8"))
            meta["tm_duration"] = total_us
            atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False, separators=(",", ":")))
            touched["copies"].append(str(meta_path) + " (tm_duration patched)")
        except Exception as e:
            touched["failed"].append({"path": str(meta_path), "error": str(e)})

    # 5-8: Timelines/<UUID>/* siblings
    for name in (state + ".bak", "template-2.tmp", "template.tmp"):
        _mirror(tl_dir / name)

    # 9: parent catalog. draft_fold_path uses forward slashes on every platform,
    # but match on basename so a Windows backslash path still resolves.
    root_meta = project_dir.parent / "root_meta_info.json"
    if root_meta.exists():
        try:
            root = json.load(open(root_meta, encoding="utf-8"))
            for entry in root.get("all_draft_store", []):
                fold = entry.get("draft_fold_path", "")
                if fold and Path(fold.replace("\\", "/")).name == project_dir.name:
                    entry["tm_duration"] = total_us
                    touched["catalog_updated"] = True
                    break
            if touched["catalog_updated"]:
                atomic_write_text(root_meta, json.dumps(root, ensure_ascii=False, separators=(",", ":")))
            else:
                touched["warnings"].append(
                    f"{project_dir.name} not in root_meta_info.json - catalog stale until CapCut reopen")
        except Exception as e:
            touched["failed"].append({"path": str(root_meta), "error": str(e)})
    else:
        touched["warnings"].append("root_meta_info.json missing")

    return touched


def assert_mirror_ok(touched: dict) -> None:
    """Raise if any of the 9 sync points failed to write.

    Callers must invoke this after mirror_9_places so a partial mirror is a hard
    error, not a silent "CapCut reverts to old state" (P0-08/H11).
    """
    failed = touched.get("failed") or []
    if failed:
        paths = ", ".join(f["path"] for f in failed)
        raise RuntimeError(
            f"9-place mirror incomplete - {len(failed)} file(s) failed: {paths}. "
            f"CapCut may revert the edit. Close CapCut and re-run. "
            f"(mirror ไม่ครบ {len(failed)} ไฟล์ - ปิด CapCut แล้วรันใหม่)")


# ----------------------------- I/O contract --------------------------------

def emit_ok(**kv) -> None:
    print(json.dumps({"ok": True, **kv}, ensure_ascii=False))


def emit_err(error: str, **kv) -> None:
    print(json.dumps({"ok": False, "error": error, **kv}, ensure_ascii=False))
    sys.exit(1)


def resolve_project(name_or_path: str, drafts_dir=None) -> Path:
    """Accept a bare project name or an absolute path. For a bare name, look it up under
    drafts_dir (a script's --drafts-dir, when passed) and fall back to the global
    CAPCUT_DRAFTS_DIR. Passing drafts_dir is what makes --drafts-dir actually take effect."""
    p = Path(name_or_path)
    if p.is_absolute() and p.exists():
        return p
    base = Path(drafts_dir) if drafts_dir else CAPCUT_DRAFTS_DIR
    return base / name_or_path


def load_draft(project_dir: Path, tl_uuid: str = None):
    """Load the platform-correct timeline draft JSON, failing CLEANLY when the project is blank.
    Returns (draft_dict, draft_path, tl_uuid) so callers don't re-resolve the path/uuid (and so the
    resolve happens INSIDE this guard).

    A blank-but-saved CapCut project (rule #23) either has no Timelines/ dir yet (find_timeline_uuid
    raises) or a 0-byte draft file that EXISTS but JSONDecodeErrors. This helper treats missing /
    empty / malformed all the same: a bilingual emit_err telling the user to drag clips into Media
    and save first. Every script that reads a draft BEFORE writing should use this, so a blank
    project never produces a stack trace."""
    try:
        if tl_uuid is None:
            tl_uuid = find_timeline_uuid(project_dir)
        path = draft_state_path(project_dir, tl_uuid)
        draft = json.load(open(path, encoding="utf-8"))
        return draft, path, tl_uuid
    except (FileNotFoundError, RuntimeError, ValueError) as e:
        emit_err(
            f"{project_dir.name} ยังว่าง - เปิด CapCut ลากคลิปเข้า Media panel + Cmd+S ก่อน "
            f"({project_dir.name} is empty - open it, drag clips into the Media panel, and save first)",
            detail=str(e),
        )


def write_state(project_dir: Path, state: dict) -> Path:
    """APPEND this op to projects/_work/capcut/<project>/state.json (edit-rules #9).

    History, not a snapshot. A normal session runs several ops on one project
    (inject-edl, then add-captions, then maybe apply-resource); overwriting meant only
    the LAST one survived, so `save_style.py` - which reads the
    inject-edl op to recover the editorial half of a pattern - found nothing and fell
    back to counting clips. The saved pattern silently lost its cuts.

    Shape written:
        {skill, project, project_dir, ops: [ {op, ran_at, ...}, ... ], latest_op}

    Back-compat both ways: a pre-existing FLAT single-op file (the old format) is
    migrated into ops[0] on the next write, and readers that still expect flat keys
    keep working because the latest op's fields are also mirrored at the top level.
    """
    kit_root = KIT_ROOT
    out_dir = kit_root / "projects" / "_work" / "capcut" / project_dir.name
    out_dir.mkdir(parents=True, exist_ok=True)
    state_path = out_dir / "state.json"

    op = dict(state)
    op["ran_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    ops = []
    if state_path.exists():
        try:
            with open(state_path, encoding="utf-8") as f:
                prev = json.load(f)
            if isinstance(prev.get("ops"), list):
                ops = prev["ops"]
            elif isinstance(prev.get("op"), str):
                # migrate the old flat single-op file into history
                legacy = {k: v for k, v in prev.items()
                          if k not in ("skill", "project", "project_dir")}
                ops = [legacy]
        except (json.JSONDecodeError, OSError):
            ops = []  # corrupt/unreadable history must never block the write

    ops.append(op)

    out = dict(op)  # mirror latest op at top level for older readers
    out["skill"] = "indigo11-capcut"
    out["project"] = project_dir.name
    out["project_dir"] = str(project_dir)
    out["ops"] = ops
    out["latest_op"] = op.get("op")
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return state_path


# ----------------------------- non-destructive ops (rule #42) ---------------
#
# The 2026-09-18 refund case: a buyer asked for captions on a project they had
# already cut by hand. The kit rebuilt the timeline and their edits were gone,
# and because mirror_9_places() overwrites CapCut's OWN .bak with the new state,
# even CapCut's backup was the destroyed version. Nothing on disk could undo it.
#
# begin_op() snapshots the exact files an op will write BEFORE it writes any of
# them, so every write is reversible; assert_preserved() makes an op declare what
# it intends to change and refuses it if anything else moved.

def sha256_file(path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def op_write_targets(project_dir, op_name: str) -> list:
    """The ACTUAL file list a given op writes - built per op, never a hardcoded count.

    The 9 mirror targets already include draft_meta_info.json and the parent
    ../root_meta_info.json, so a snapshot of them covers everything
    mirror_9_places() touches. import_media additionally rewrites
    draft_virtual_store.json (rule #25), which no other op does.
    """
    project_dir = Path(project_dir)
    targets = []
    try:
        tl_uuid = find_timeline_uuid(project_dir)
        tl_dir = project_dir / "Timelines" / tl_uuid
        state = draft_state_name(tl_dir)
        targets.append(tl_dir / state)
        for name in (state + ".bak", "template-2.tmp", "template.tmp"):
            targets.append(tl_dir / name)
        for name in (state, state + ".bak", "template-2.tmp"):
            targets.append(project_dir / name)
    except (FileNotFoundError, RuntimeError):
        pass  # blank project: nothing to snapshot yet, recorded as existed_before:false
    targets.append(project_dir / "draft_meta_info.json")
    targets.append(project_dir.parent / "root_meta_info.json")
    if op_name == "import-media":
        targets.append(project_dir / "draft_virtual_store.json")
    kit_root = KIT_ROOT
    targets.append(kit_root / "projects" / "_work" / "capcut" / project_dir.name / "state.json")
    seen, out = set(), []
    for t in targets:
        s = str(t)
        if s not in seen:
            seen.add(s)
            out.append(t)
    return out


def project_tree_targets(project_dir) -> list:
    """EVERY file in the project, plus the parent catalog - for a whole-dir op.

    `init_project.py --force` does `shutil.rmtree(project_dir)`: the most
    destructive line in the kit, and the per-op write list does not describe it
    (it deletes sidecars, Timelines/, cover images - not just the 9 mirror
    targets). Snapshotting the whole tree is what makes that rmtree undoable.
    """
    project_dir = Path(project_dir)
    # The write lock is this op's own transient file, not project data. Restoring
    # it would hand the buyer back a locked project (until the 120s stale reclaim).
    out = [p for p in sorted(project_dir.rglob("*"))
           if p.is_file() and p.name != ".indigo11-write.lock"]
    parent_catalog = project_dir.parent / "root_meta_info.json"
    if parent_catalog.exists():
        out.append(parent_catalog)
    kit_root = KIT_ROOT
    out.append(kit_root / "projects" / "_work" / "capcut" / project_dir.name / "state.json")
    return out


def op_seq(op_id: str) -> int:
    """The monotonic seq embedded in an op_id, or 0 for a pre-seq id.

    Shape: <YYYYmmdd-HHMMSS>-<NNNN>-<op>-<rand>. Pre-seq ids (no numeric 3rd field)
    sort before every seq'd op, which is correct: they are older.
    """
    parts = (op_id or "").split("-")
    if len(parts) >= 3 and parts[2].isdigit():
        return int(parts[2])
    return 0


class OpContext:
    """A reversible CapCut write. Holds the project lock for the whole op.

    Usage:
        with begin_op(project_dir, "add-captions") as op:
            ...mutate + write + mirror...
            op.check_mirror(mirror_result)   # auto-rolls back on partial mirror

    On an exception inside the block the op rolls back automatically. The backup
    directory is NEVER deleted, in any outcome - a restore must stay possible
    even after a rollback itself half-failed.
    """

    def __init__(self, project_dir, op_name: str, files_to_write=None, plan=None,
                 scope: str = "files"):
        self.project_dir = Path(project_dir)
        self.op = op_name
        # "files" (default) = this op snapshotted only its own write list.
        # "whole-tree" = the snapshot lists EVERY file in the project, which is
        # the ONLY case where a restore may delete files the manifest does not
        # list. It must be declared by the op, never inferred: the 9-place mirror
        # puts the root draft file in every manifest, so a filename-based guess
        # read EVERY per-op snapshot as whole-tree and a restore deleted 21 of a
        # real project's 29 files, draft_virtual_store.json (the Media panel
        # links) among them.
        self.scope = scope if scope in ("files", "whole-tree") else "files"
        # The op's declared change plan, in assert_preserved()'s shape. It is
        # recorded in manifest.json so the READ-BACK check (_preserved.py) can tell
        # a legitimate replace from a wiped buyer track. Without it a manifest says
        # only "a track disappeared", which is indistinguishable from the refund
        # case - so an op that does not declare one is reported as unverifiable,
        # never as a pass.
        self.plan = plan
        # The suffix is not decoration: strftime resolves to ONE SECOND, and two ops
        # on the same project inside that second produced the SAME op_id, hence the
        # same backup_dir. The second snapshot then overwrote the first one's files
        # and manifest, so restoring the first op returned the state left by the
        # second - the pre-op bytes were gone. A reversible op whose backup can be
        # silently replaced is not reversible.
        # seq is allocated under the lock in __enter__ (a timestamp resolves to one
        # SECOND, so several ops share it and the uuid suffix orders them RANDOMLY).
        # Until then the id is provisional; nothing reads it before __enter__.
        self.seq = None
        self._stamp = time.strftime("%Y%m%d-%H%M%S")
        self._rand = uuid.uuid4().hex[:8]
        self.op_id = f"{self._stamp}-{op_name}-{self._rand}"
        self.files = [Path(f) for f in (files_to_write
                                        if files_to_write is not None
                                        else op_write_targets(project_dir, op_name))]
        kit_root = KIT_ROOT
        self.backups_root = (kit_root / "projects" / "_work" / "capcut" / self.project_dir.name
                             / "backups")
        self.backup_dir = self.backups_root / self.op_id
        self.manifest_path = self.backup_dir / "manifest.json"
        self.manifest = None
        self.rolled_back = False
        self._lock = ProjectLock(self.project_dir)

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self):
        self._lock.__enter__()          # lock BEFORE snapshot, so nobody writes between
        try:
            self.snapshot()
        except Exception:
            self._lock.__exit__()
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is not None and not self.rolled_back:
                self.rollback(reason=f"{exc_type.__name__}: {exc}")
            elif exc_type is None and not self.rolled_back:
                self._record_after()
        finally:
            self._lock.__exit__()
        return False                     # never swallow the original error

    def _record_after(self) -> None:
        """Hash every written file as this op LEFT it.

        The read-back compares the draft on disk with the pre-op snapshot. Once the
        buyer opens the project, CapCut re-saves it (and the buyer may edit), so a
        later read-back saw those changes and blamed the op for them. With the
        after-hash it can tell "the op changed this" from "the draft was saved
        since" and say which."""
        try:
            for e in self.manifest.get("files", []):
                p = Path(e["path"])
                e["after_sha256"] = sha256_file(p) if p.exists() else None
            atomic_write_text(self.manifest_path,
                              json.dumps(self.manifest, ensure_ascii=False, indent=2))
        except Exception:
            pass                         # a missing after-hash only makes the read-back stricter

    def _assign_seq(self) -> None:
        """Allocate this op's monotonic sequence number, under the project lock.

        A timestamp resolves to ONE SECOND, so ops made inside it sorted by their
        random uuid suffix: `--list` showed them in an order unrelated to when they
        ran, and "restore the first one listed" could hand back a LATER state. seq
        goes in the id as a zero-padded field right after the timestamp, so lexical
        order IS chronological order for every consumer, including a plain `ls`.
        """
        self.seq = 1 + max([op_seq(d.name) for d in self.backups_root.iterdir()
                            if d.is_dir()] or [0]) if self.backups_root.exists() else 1
        self.op_id = f"{self._stamp}-{self.seq:04d}-{self.op}-{self._rand}"
        self.backup_dir = self.backups_root / self.op_id
        self.manifest_path = self.backup_dir / "manifest.json"

    # -- snapshot ----------------------------------------------------------
    def snapshot(self) -> dict:
        # exist_ok=False on purpose: reusing a backup dir means overwriting an
        # earlier op's snapshot + manifest, which destroys the only copy of the
        # pre-op bytes. Fail BEFORE anything is copied rather than silently
        # replacing a restore point.
        if self.seq is None:
            self._assign_seq()
        self.backup_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.backup_dir.mkdir(exist_ok=False)
        except FileExistsError:
            raise FileExistsError(
                f"backup dir already exists: {self.backup_dir} - refusing to overwrite "
                f"an existing snapshot (op_id {self.op_id}). "
                f"(มี backup ของ op นี้อยู่แล้ว - ไม่เขียนทับ)")
        entries = []
        for i, src in enumerate(self.files):
            # A flat "NN-basename" is unique for the usual write list, but a
            # WHOLE-PROJECT snapshot holds many files with the same basename
            # (draft_info.json sits at the root AND under Timelines/<uuid>/), and a
            # flat name would have one silently overwrite the other - the restore
            # would then hand back the wrong bytes. Index-prefixing the RELATIVE
            # path keeps both unique and human-readable.
            try:
                sub = Path(src).relative_to(self.project_dir)
                rel = str(Path(f"{i:02d}") / sub)
            except ValueError:
                rel = f"{i:02d}-{src.name}"   # outside the project (root_meta_info, state.json)
            existed = src.exists()
            entry = {"path": str(src), "existed_before": existed,
                     "sha256": None, "backup_rel": rel if existed else None}
            if existed:
                dst = self.backup_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst))
                entry["sha256"] = sha256_file(src)
            entries.append(entry)
        self.manifest = {
            "op_id": self.op_id, "op": self.op,
            "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "project_dir": str(self.project_dir),
            "seq": self.seq,
            "scope": self.scope,
            "plan": normalize_plan(self.plan) if self.plan is not None else None,
            "files": entries,
        }
        atomic_write_text(self.manifest_path,
                          json.dumps(self.manifest, ensure_ascii=False, indent=2))
        return self.manifest

    def set_plan(self, plan: dict) -> dict:
        """Record the op's change plan, rewriting manifest.json in place.

        For ops that can only build their plan after the snapshot (they need the
        pre-op draft to compute it). It must land BEFORE the op reports success,
        because a manifest with no plan makes the read-back check unverifiable -
        so call it inside the `with` block, not after it.
        """
        self.plan = plan
        if self.manifest is not None:
            self.manifest["plan"] = normalize_plan(plan) if plan is not None else None
            atomic_write_text(self.manifest_path,
                              json.dumps(self.manifest, ensure_ascii=False, indent=2))
        return self.manifest

    # -- rollback ----------------------------------------------------------
    def rollback(self, reason: str = "") -> dict:
        """Restore every snapshotted file. 3 states: ok / partial / failed.

        A file that did NOT exist before the op is DELETED on rollback - leaving
        a half-created state.json or draft file behind would make the next run
        think the project had been through an op it never completed.
        """
        self.rolled_back = True
        restored, failed = [], []
        for e in (self.manifest or {}).get("files", []):
            dst = Path(e["path"])
            try:
                if e["existed_before"]:
                    src = self.backup_dir / e["backup_rel"]
                    if sha256_file(src) != e["sha256"]:
                        raise RuntimeError("backup file corrupt (sha256 mismatch)")
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    # BINARY copy, not a text round-trip. read_text+write_text
                    # rewrites line endings on Windows (\n -> \r\n), so a "restored"
                    # file was not byte-identical to its own sha256 in the manifest,
                    # and any non-UTF-8 file landed in `failed` instead of coming
                    # back. restore_project.py uses copy2 for the same reason.
                    shutil.copy2(str(src), str(dst))
                else:
                    if dst.exists():
                        dst.unlink()
                restored.append(str(dst))
            except Exception as ex:
                failed.append({"path": str(dst), "error": str(ex)})
        if not failed:
            status = "ok"
        elif restored:
            status = "partial"
        else:
            status = "failed"
        return {
            "status": status, "op_id": self.op_id, "reason": reason,
            "restored": restored, "failed": failed,
            "backup_dir": str(self.backup_dir),
            "restore_command": self.restore_command(),
        }

    def restore_command(self) -> str:
        return (f"python3 $CAPCUT_DIR/scripts/restore_project.py "
                f"--project {self.project_dir.name} --op {self.op_id}")

    def check_mirror(self, mirror: dict) -> None:
        """Roll back when mirror_9_places reported failures, then raise.

        A partial mirror is exactly the state rule #6 warns about: CapCut
        self-heals to the OLD timeline and silently discards the edit. Rolling
        back makes that outcome consistent instead of half-applied.
        """
        if not (mirror or {}).get("failed"):
            return
        rb = self.rollback(reason="9-place mirror incomplete")
        raise MirrorRollbackError(
            f"9-place mirror incomplete ({len(mirror['failed'])} file(s)) - "
            f"rolled back [{rb['status']}]. op_id={self.op_id}. "
            f"backup: {rb['backup_dir']}. restore: {rb['restore_command']} "
            f"(mirror ไม่ครบ - ย้อนกลับแล้ว)", rollback=rb, op_id=self.op_id)

    def info(self) -> dict:
        """The op_id + backup_dir every emit_ok/emit_err must carry."""
        return {"op_id": self.op_id, "backup_dir": str(self.backup_dir),
                "restore_command": self.restore_command()}


class MirrorRollbackError(RuntimeError):
    def __init__(self, msg, rollback=None, op_id=None):
        super().__init__(msg)
        self.rollback = rollback
        self.op_id = op_id


def normalize_plan(plan) -> dict:
    """The change plan in one canonical shape, so a reader never has to guess.

    assert_preserved() tolerates missing keys; the manifest must not, because
    _preserved.check_preserved() decides a FAIL from what the plan does not list.
    A key absent from a stored plan would then read as "nothing was declared here"
    - the same as a wiped track - so every bucket is filled in explicitly.
    """
    plan = plan or {}
    add = plan.get("add") or {}
    rep = plan.get("replace") or {}
    dele = plan.get("delete") or {}
    return {
        "add": {"tracks": sorted(str(x) for x in (add.get("tracks") or [])),
                "materials": sorted(str(x) for x in (add.get("materials") or []))},
        # replace.materials = an id whose entry this op REWRITES IN PLACE (same id,
        # different content). No op in the kit does that today and the bucket is
        # empty everywhere - it exists because the read-back has to be able to tell
        # a declared in-place rewrite from an undeclared one, and without the bucket
        # every rewrite was unjudgeable and therefore passed. Changing a buyer's
        # title text or colour while keeping its id is the refund case with the ids
        # intact: nothing is deleted, so a deletion-only check sees nothing.
        "replace": {"tracks": sorted(str(x) for x in (rep.get("tracks") or [])),
                    "materials": sorted(str(x) for x in (rep.get("materials") or []))},
        "delete": {"segments": sorted(str(x) for x in (dele.get("segments") or [])),
                   "materials": sorted(str(x) for x in (dele.get("materials") or []))},
    }


def begin_op(project_dir, op_name: str, files_to_write=None, plan=None,
             scope: str = "files") -> OpContext:
    """Start a reversible op: acquire the project lock, snapshot the write list.

    `plan` is the op's declared change set (assert_preserved's shape). It is stored
    in the manifest so the read-back check can separate a declared replace from an
    undeclared wipe. Pass it here when it is known up front, or call
    `op.set_plan(...)` inside the block once it is computed.

    `scope` must be "whole-tree" ONLY when files_to_write really is every file in
    the project (init_project --force). A restore may delete unlisted files for
    that scope alone, so an op that mislabels itself turns its own restore into
    the most destructive operation in the kit.
    """
    return OpContext(project_dir, op_name, files_to_write, plan, scope)


# ----------------------------- change plan enforcement (A2) ----------------

def _track_index(draft: dict) -> dict:
    return {t.get("id"): t for t in (draft.get("tracks") or [])}


def _material_index(draft: dict) -> dict:
    """{material_id: (category, entry)} across every materials.* list."""
    out = {}
    for cat, items in (draft.get("materials") or {}).items():
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and it.get("id"):
                    out[it["id"]] = (cat, it)
    return out


def material_refcount(draft: dict, material_id: str, exclude_tracks=(),
                      exclude_segments=()) -> int:
    """How many segments still reference this material, ignoring the ones the op
    is itself removing (`exclude_tracks` whole tracks, `exclude_segments` individual
    segment ids).

    A material may only be deleted at refcount 0. Without this, replacing our own
    caption track could delete a text material that a buyer's own title segment
    also pointed at - their title would render blank.

    Both exclusions are needed: an op that deletes SEGMENTS without replacing the
    whole track (a partial caption trim) otherwise still counts its own doomed
    segments as live references, so its declared material delete is refused.
    """
    exclude = set(exclude_tracks or ())
    exclude_segs = set(exclude_segments or ())
    n = 0
    for t in (draft.get("tracks") or []):
        if t.get("id") in exclude:
            continue
        for s in (t.get("segments") or []):
            if s.get("id") in exclude_segs:
                continue
            if s.get("material_id") == material_id:
                n += 1
            for r in (s.get("extra_material_refs") or []):
                if r == material_id:
                    n += 1
    return n


def assert_preserved(before: dict, after: dict, plan: dict) -> None:
    """Refuse the write if anything outside `plan` changed.

    plan = {"add":     {"tracks": [ids], "materials": [ids]},
            "replace": {"tracks": [ids]},
            "delete":  {"segments": [ids], "materials": [ids]}}

    Method: reduce BOTH drafts to a canonical form with the planned changes
    removed, then compare deeply. Added tracks/materials are dropped from
    `after`; replaced tracks are dropped from both (their contents are the op's
    business); deleted ids are dropped from `before`. Whatever is left must be
    identical - every other track, segment, materials.* entry,
    extra_material_refs, canvas_config and duration.

    Raises ValueError naming the JSON path(s) that differ. Deliberately
    deterministic (rule #18): no heuristics, no "probably fine".
    """
    plan = plan or {}
    add_tracks = set((plan.get("add") or {}).get("tracks") or [])
    add_mats = set((plan.get("add") or {}).get("materials") or [])
    rep_tracks = set((plan.get("replace") or {}).get("tracks") or [])
    del_segs = set((plan.get("delete") or {}).get("segments") or [])
    del_mats = set((plan.get("delete") or {}).get("materials") or [])

    # a material may only be deleted when nothing else still points at it
    for mid in del_mats:
        n = material_refcount(before, mid, exclude_tracks=rep_tracks,
                              exclude_segments=del_segs)
        if n > 0:
            raise ValueError(
                f"plan deletes material {mid} but {n} segment(s) on other tracks "
                f"still reference it (materials.{mid}) - refusing "
                f"(ลบ material ที่ track อื่นยังใช้อยู่ไม่ได้)")

    def canon(draft, *, drop_tracks, drop_mats, drop_segs):
        tracks = []
        for t in (draft.get("tracks") or []):
            if t.get("id") in drop_tracks:
                continue
            t2 = deep_copy(t)
            t2["segments"] = [s for s in (t2.get("segments") or [])
                              if s.get("id") not in drop_segs]
            tracks.append(t2)
        tracks.sort(key=lambda t: str(t.get("id")))
        mats = {}
        for cat, items in (draft.get("materials") or {}).items():
            if isinstance(items, list):
                kept = [deep_copy(i) for i in items
                        if not (isinstance(i, dict) and i.get("id") in drop_mats)]
                kept.sort(key=lambda i: str(i.get("id")) if isinstance(i, dict) else str(i))
                mats[cat] = kept
            else:
                mats[cat] = deep_copy(items)
        return {"tracks": tracks, "materials": mats,
                "canvas_config": draft.get("canvas_config"),
                "duration": draft.get("duration")}

    a = canon(before, drop_tracks=rep_tracks, drop_mats=del_mats, drop_segs=del_segs)
    b = canon(after, drop_tracks=rep_tracks | add_tracks, drop_mats=add_mats, drop_segs=set())

    diffs = []

    def walk(x, y, path):
        if len(diffs) >= 8:
            return
        if type(x) is not type(y) and not (isinstance(x, (int, float))
                                           and isinstance(y, (int, float))):
            diffs.append(f"{path}: type {type(x).__name__} -> {type(y).__name__}")
        elif isinstance(x, dict):
            for k in sorted(set(x) | set(y)):
                if k not in x:
                    diffs.append(f"{path}.{k}: added unexpectedly")
                elif k not in y:
                    diffs.append(f"{path}.{k}: removed unexpectedly")
                else:
                    walk(x[k], y[k], f"{path}.{k}")
        elif isinstance(x, list):
            if len(x) != len(y):
                diffs.append(f"{path}: {len(x)} -> {len(y)} entries")
            else:
                for i, (xi, yi) in enumerate(zip(x, y)):
                    walk(xi, yi, f"{path}[{i}]")
        elif x != y:
            diffs.append(f"{path}: {x!r} -> {y!r}")

    walk(a, b, "draft")
    if diffs:
        raise ValueError(
            "unplanned_change - this op would modify things it did not declare: "
            + "; ".join(diffs)
            + " (แก้ของที่ไม่ได้อยู่ในแผน - ยกเลิกการเขียน)")
