#!/usr/bin/env python3
"""Put a CapCut project back to how it was before one of the kit's ops.

Every indigo11-capcut write runs inside `begin_op()` (see _capcut_lib.py), which
snapshots the exact files that op is about to touch into
`projects/_work/capcut/<project>/backups/<op-id>/` with a manifest carrying each file's
sha256. This script is the other half: it reads a manifest and puts those bytes
back.

    restore_project.py --project 0902 --list
    restore_project.py --project 0902 --op 20260918-143012-add-captions

Why the op runs FIRST, before anything is written
-------------------------------------------------
A restore is itself a write, so it snapshots the CURRENT state as a new op named
`restore-from-<op-id>` before touching a byte. Undoing a restore is then the same
command with the new op id. Without it the buyer's only way back from a wrong
restore would be re-cutting from scratch.

Why sha256 is verified before ANY file is written
-------------------------------------------------
A half-applied restore is the worst outcome: the 9 mirror targets would disagree
with each other, and CapCut self-heals a disagreeing project to whichever copy it
reads first (rule #6) - silently discarding the rest. So every backup file is
verified against its manifest hash up front, and a single mismatch aborts before
the first write.

Failure modes an agent will actually hit
----------------------------------------
- CapCut is open. Refused (rule #7): a live CapCut auto-saves its in-memory
  timeline over restored files within ~30s, so the restore would appear to work
  and then vanish. `--force-kill` exists but only after asking the buyer, because
  it SIGKILLs unsaved work.
- No backups directory. The project has never been through a kit op, or the buyer
  deleted `projects/_work/`. Nothing to restore - reported as such, not as a crash.
- A manifest entry with `existed_before: false`. The op CREATED that file, so
  restoring means DELETING it. A file left behind (a half-written state.json, a
  draft in a project that had none) makes the next run believe an op completed
  that never did.
- The 9 mirror targets are all in the snapshot set, so restoring them reproduces
  the mirrored state directly. This script deliberately does NOT call
  mirror_9_places() afterwards - that would copy one restored file over the other
  eight, which is exactly what the snapshot already captured correctly.
"""
from __future__ import annotations
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _capcut_lib import (  # noqa: E402
    CAPCUT_DRAFTS_DIR, SKILL_DIR, KIT_ROOT, begin_op, emit_err, emit_ok,
    ensure_capcut_closed, op_seq, resolve_project, sha256_file,
)


def backups_root(project_dir: Path) -> Path:
    kit_root = KIT_ROOT
    return kit_root / "projects" / "_work" / "capcut" / project_dir.name / "backups"


def load_manifests(project_dir: Path) -> list:
    """Every readable manifest under backups/, newest first.

    An unreadable manifest is skipped rather than fatal - one corrupt op must not
    hide the other ops the buyer could still restore from.
    """
    root = backups_root(project_dir)
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir()):
        mp = d / "manifest.json"
        if not d.is_dir() or not mp.exists():
            continue
        try:
            m = json.loads(mp.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        m["_backup_dir"] = str(d)
        m["_manifest_path"] = str(mp)
        out.append(m)
    # seq first: ops inside one second share ran_at, and the id's random suffix
    # ordered them arbitrarily - so "the first one listed" was not the first one
    # that ran, and restoring it handed the buyer a LATER state.
    out.sort(key=lambda m: (m.get("seq") or op_seq(m.get("op_id") or ""),
                            m.get("ran_at") or ""), reverse=True)
    return out


def verify_backup(manifest: dict) -> list:
    """Check every backed-up file against its recorded sha256. Returns problems."""
    backup_dir = Path(manifest["_backup_dir"])
    problems = []
    for e in manifest.get("files", []):
        if not e.get("existed_before"):
            continue
        rel = e.get("backup_rel")
        if not rel:
            problems.append({"path": e.get("path"), "error": "manifest has no backup_rel"})
            continue
        src = backup_dir / rel
        if not src.exists():
            problems.append({"path": e.get("path"), "error": f"backup file missing: {rel}"})
            continue
        try:
            actual = sha256_file(src)
        except OSError as ex:
            problems.append({"path": e.get("path"), "error": f"unreadable backup: {ex}"})
            continue
        if actual != e.get("sha256"):
            problems.append({
                "path": e.get("path"),
                "error": "sha256 mismatch (backup corrupt)",
                "expected": e.get("sha256"), "actual": actual,
            })
    return problems


def prune_extra_files(manifest: dict, project_dir: Path) -> tuple:
    """Delete project files the snapshot does not list - only for a WHOLE-TREE op.

    Returns (removed, failed). A file that could NOT be removed is reported, never
    swallowed: on Windows a held or read-only file survives the unlink, and the one
    that matters is the re-clone's second `Timelines/<uuid>/` dir - find_timeline_uuid
    refuses anything but exactly one, so a restore that silently left it behind
    reported `ok` and handed back a project CapCut cannot open.

    A per-op manifest lists a handful of write targets, so anything else in the
    project is untouched buyer data and must stay. A whole-tree snapshot
    (init_project --force) lists EVERY file, so a file absent from it was created
    after the snapshot - and leaving it behind breaks the project rather than
    merely cluttering it: the re-clone mints a NEW Timelines/<uuid>/ dir, so the
    restored project would carry two, and find_timeline_uuid refuses anything but
    exactly one.

    Detected, never assumed: the op must have snapshotted the draft-state files at
    the project ROOT, which only the whole-tree list does.
    """
    # Compare RESOLVED paths on both sides. The containment check below resolves
    # each candidate, and on macOS a temp dir resolves /var -> /private/var, so an
    # unresolved manifest string matched nothing and every listed file - including
    # ones present at snapshot time - looked prunable.
    listed = {str(Path(e["path"]).resolve()) for e in manifest.get("files", [])}
    # DECLARED, never inferred. The previous test - "does the manifest list a root
    # draft file?" - is true of EVERY per-op manifest, because the 9-place mirror
    # writes the root draft on every op. On a real 29-file project that read a
    # plain add-captions snapshot as whole-tree and deleted 21 files, including
    # draft_virtual_store.json (the Media panel links): a restore that destroyed
    # the project it was restoring. Only an op that snapshotted the entire tree
    # may prune, and it says so in its own manifest.
    if manifest.get("scope") != "whole-tree":
        return [], []
    project_dir = Path(project_dir).resolve()
    removed, failed = [], []
    for p in sorted(project_dir.rglob("*"), reverse=True):
        # Never delete outside the project, whatever the manifest says.
        try:
            inside = p.resolve().is_relative_to(project_dir)
        except (OSError, AttributeError):
            inside = str(p.resolve()).startswith(str(project_dir) + os.sep)
        if not inside:
            continue
        if p.is_file() and str(p.resolve()) not in listed and p.name != ".indigo11-write.lock":
            try:
                p.unlink()
                removed.append(str(p))
            except OSError as ex:
                failed.append({"path": str(p), "action": "prune", "error": str(ex)})
    for p in sorted(project_dir.rglob("*"), reverse=True):
        if p.is_dir() and not any(p.iterdir()):
            try:
                p.rmdir()
            except OSError as ex:
                # An empty dir left behind is cosmetic on its own, BUT the one this
                # prune exists for is the re-clone's extra Timelines/<uuid>/ - so it
                # is reported too rather than assumed harmless.
                failed.append({"path": str(p), "action": "prune-dir", "error": str(ex)})
    return removed, failed


def apply_restore(manifest: dict) -> tuple:
    """Put the snapshotted files back. Returns (results, restored_n, failed_n).

    Binary-safe copy (shutil.copy2), not a text round-trip: a draft written on a
    Thai Windows console is UTF-8 on disk but would be re-encoded by a text read/
    write pair, and CapCut then fails to parse it.
    """
    backup_dir = Path(manifest["_backup_dir"])
    results = []
    for e in manifest.get("files", []):
        dst = Path(e["path"])
        try:
            if e.get("existed_before"):
                src = backup_dir / e["backup_rel"]
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst))
                results.append({"path": str(dst), "action": "restored", "status": "ok"})
            else:
                if dst.exists():
                    dst.unlink()
                    results.append({"path": str(dst), "action": "deleted", "status": "ok"})
                else:
                    results.append({"path": str(dst), "action": "absent", "status": "ok"})
        except Exception as ex:
            results.append({"path": str(dst), "action": "restore", "status": "failed",
                            "error": str(ex)})
    failed_n = sum(1 for r in results if r["status"] == "failed")
    return results, len(results) - failed_n, failed_n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--list", action="store_true",
                    help="List the ops that can be restored (newest first)")
    ap.add_argument("--op", help="op_id to restore (from --list)")
    ap.add_argument("--force-kill", action="store_true",
                    help="SIGKILL a running CapCut instead of refusing. Only after "
                         "confirming with the buyer that nothing is unsaved.")
    ap.add_argument("--json", action="store_true", help="print JSON only")
    ap.add_argument("--drafts-dir", default=str(CAPCUT_DRAFTS_DIR))
    args = ap.parse_args()

    if os.name == "nt":  # cp874 console crashes printing Thai (rule #32)
        for st in (sys.stdout, sys.stderr):
            if hasattr(st, "reconfigure"):
                st.reconfigure(encoding="utf-8", errors="replace")

    if not args.list and not args.op:
        emit_err("ต้องใส่ --list หรือ --op <op-id> (pass --list or --op <op-id>)")

    project_dir = resolve_project(args.project, args.drafts_dir)
    if not project_dir.exists():
        emit_err(f"Project not found: {project_dir}")

    manifests = load_manifests(project_dir)

    if args.list:
        ops = [{
            "op_id": m.get("op_id"),
            "op": m.get("op"),
            # The order the buyer reads. ran_at alone cannot order ops made in the
            # same second, so print the seq that actually decides the sort.
            "seq": m.get("seq") or op_seq(m.get("op_id") or ""),
            "ran_at": m.get("ran_at"),
            "file_count": len(m.get("files", [])),
            "files": [Path(e["path"]).name for e in m.get("files", [])],
            "backup_dir": m["_backup_dir"],
        } for m in manifests]
        if args.json:
            print(json.dumps({"ok": True, "op": "restore-list", "project": args.project,
                              "backups_dir": str(backups_root(project_dir)),
                              "count": len(ops), "ops": ops}, ensure_ascii=False))
            return 0
        if not ops:
            print(f"ไม่มี backup สำหรับ {args.project} (no restorable ops found)")
            print(f"  looked in: {backups_root(project_dir)}")
            return 0
        print(f"{len(ops)} op(s) ที่ย้อนกลับได้ (restorable) - {args.project}")
        for o in ops:
            print(f"  {o['op_id']:<40} {o['op']:<20} {o['ran_at']}  {o['file_count']} files")
        return 0

    chosen = next((m for m in manifests if m.get("op_id") == args.op), None)
    if chosen is None:
        emit_err(
            f"ไม่พบ op {args.op} (op not found). ดูรายการด้วย --list (list them with --list)",
            available=[m.get("op_id") for m in manifests][:20],
            backups_dir=str(backups_root(project_dir)),
        )

    ok, detail = ensure_capcut_closed(args.force_kill)
    if not ok:
        emit_err(detail)

    problems = verify_backup(chosen)
    if problems:
        emit_err(
            f"backup ของ {args.op} เสียหาย - ไม่เขียนอะไรเลย "
            f"(backup is corrupt - nothing was written)",
            op_id=args.op, backup_dir=chosen["_backup_dir"], problems=problems,
        )

    # Snapshot the CURRENT state first so the restore is itself undoable. The file
    # list comes from the manifest being restored, not from op_write_targets():
    # only the files this restore will actually touch need saving, and the op being
    # restored may have written a file (draft_virtual_store.json for import-media)
    # that a generic target list for "restore" would not include.
    files_to_write = [Path(e["path"]) for e in chosen.get("files", [])]
    # A whole-tree restore also PRUNES files the snapshot does not list (see
    # prune_extra_files). Those are deletions too, so they belong in this restore's
    # own snapshot - otherwise undoing the restore would bring back the old files
    # but not the ones it removed, which is a one-way trip in the other direction.
    #
    # ONLY for a whole-tree op. prune_extra_files prunes nothing for any other
    # scope, so a per-op restore deletes nothing and has nothing extra to save - yet
    # this swept the ENTIRE project into its snapshot regardless, so undoing one
    # caption run copied every sidecar, cover and Resources/ payload for no reason
    # and wrote a manifest that claimed a blast radius the restore never had.
    if chosen.get("scope") == "whole-tree":
        # RESOLVED on both sides, exactly as prune_extra_files compares: on macOS a
        # temp dir resolves /var -> /private/var, so an unresolved compare matched
        # nothing and every already-listed file was appended a second time.
        _listed = {str(p.resolve()) for p in files_to_write}
        for _p in sorted(project_dir.rglob("*")):
            if (_p.is_file() and str(_p.resolve()) not in _listed
                    and _p.name != ".indigo11-write.lock"):
                files_to_write.append(_p)
    try:
        with begin_op(project_dir, f"restore-from-{args.op}",
                      files_to_write=files_to_write) as op:
            results, restored_n, failed_n = apply_restore(chosen)
            if failed_n and restored_n == 0:
                raise RuntimeError(
                    "ไม่สามารถกู้คืนไฟล์ใดได้เลย (no file could be restored)")
            pruned, prune_failed = prune_extra_files(chosen, project_dir)
            # A prune failure is NOT cosmetic: the file it could not remove may be
            # the re-clone's second Timelines/<uuid>/ draft, and find_timeline_uuid
            # refuses a project carrying two. Reporting `ok` there hands the buyer a
            # project CapCut cannot open, from the command whose job is undoing
            # damage - so an unremoved file downgrades the whole restore.
            status = "ok" if not (failed_n or prune_failed) else "partial"
            payload = {
                "op": "restore-project",
                "project": args.project,
                "project_dir": str(project_dir),
                "restored_op": args.op,
                "restored_op_name": chosen.get("op"),
                "restored_op_ran_at": chosen.get("ran_at"),
                "status": status,
                "files_restored": restored_n,
                "files_failed": failed_n,
                "files": results,
                "files_pruned": pruned,
                "prune_failed": prune_failed,
                "undo_command": (f"python3 $CAPCUT_DIR/scripts/restore_project.py "
                                 f"--project {args.project} --op {op.op_id}"),
            }
            if prune_failed:
                payload["detail"] = (
                    "ลบไฟล์ส่วนเกินไม่สำเร็จ " + str(len(prune_failed)) + " ไฟล์: "
                    + ", ".join(Path(f["path"]).name for f in prune_failed[:6])
                    + " - project อาจมี Timelines ซ้ำและ CapCut เปิดไม่ขึ้น "
                    "(could not remove leftover file(s); the project may carry two "
                    "Timelines dirs, which CapCut refuses to open). ปิดโปรแกรมที่ถือไฟล์นี้ "
                    "แล้วลบเองหรือรัน restore ซ้ำ (close whatever holds them, then delete "
                    "them or re-run this restore)")
            payload.update(op.info())
    except Exception as ex:
        emit_err(f"restore ล้มเหลว (restore failed): {ex}", restored_op=args.op)

    if args.json or not sys.stdout.isatty():
        emit_ok(**payload)
        return 0 if status == "ok" else 1

    mark = "OK" if status == "ok" else "PARTIAL"
    print(f"{mark}  กู้คืน {args.op} แล้ว (restored)  "
          f"{restored_n} ok / {failed_n} failed")
    for r in results:
        print(f"  [{r['status']:<6}] {r['action']:<9} {r['path']}")
    for f in payload.get("prune_failed") or []:
        print(f"  [failed] {f['action']:<9} {f['path']}  {f['error']}")
    if payload.get("detail"):
        print(f"\n  {payload['detail']}")
    print(f"\n  ย้อนการกู้คืนนี้ (undo this restore): {payload['undo_command']}")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
