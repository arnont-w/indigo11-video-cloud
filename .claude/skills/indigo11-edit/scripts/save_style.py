#!/usr/bin/env python3
"""save_style.py - capture the editing style of a finished CapCut project as rule lines.

Replaces the old indigo11-video-handoff `save_pattern.py`. Instead of a pattern.json it writes
`my-rules/style-<name>.md` (one `- date: rule (why)` line per fact) and adds one line to
`my-rules/INDEX.md`, marked "only when I name it". Stdlib only.

Usage (run from the pack folder; Windows: python):
  python3 .claude/skills/indigo11-edit/scripts/save_style.py --source <capcut-project> --name <style-name> [--description "..."]
  python3 .claude/skills/indigo11-edit/scripts/save_style.py --source-state <state.json> --name <style-name>

The agent then asks the user only for gaps (description, target length, story arc, CTA) and edits the file.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

LIMIT = 160


def pack_root() -> Path:
    for p in Path(__file__).resolve().parents:
        if (p / "my-rules").is_dir():
            return p
    return Path.cwd()


def drafts_dir() -> Path:
    env = os.environ.get("CAPCUT_DRAFTS_DIR")
    if env:
        return Path(env)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    return Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"


def load_state(root: Path, source, source_state):
    if source_state:
        p = Path(source_state).expanduser()
    elif source:
        p = root / "projects" / "_work" / "capcut" / source / "state.json"
    else:
        raise ValueError("Need --source or --source-state")
    if not p.exists():
        raise FileNotFoundError(f"No state.json at {p}")
    return json.loads(p.read_text(encoding="utf-8")), p


def ops_of(state: dict) -> list:
    if isinstance(state.get("ops"), list):
        return state["ops"]
    return [state] if state.get("op") else []


def infer(state: dict, source) -> list[tuple[str, str]]:
    """Return [(rule, why)] inferred from observable data. All are guesses: the agent confirms them."""
    out: list[tuple[str, str]] = []
    ops = ops_of(state)
    cap = next((o for o in ops if o.get("op") == "add-captions"), None)
    if cap:
        mode = cap.get("mode", "plain")
        params = cap.get("params") or cap
        out.append((f"Captions use mode `{mode}`", "copied from the finished project"))
        if params.get("base_size"):
            out.append((f"Caption size {int(params['base_size'])}, colour {params.get('color', '#FFFFFF')}", "copied from the finished project"))
        if params.get("highlight_size") and params.get("base_size"):
            mult = round(params["highlight_size"] / params["base_size"], 2)
            out.append((f"Highlighted word x{mult} size, colour {params.get('highlight_color', '#F4C70F')}", "copied from the finished project"))
        n_in, n_out = cap.get("caption_count_in") or 0, cap.get("caption_count_out") or 0
        if mode != "plain" and n_in and n_out:
            out.append((f"About {max(1, round(n_out / n_in))} words per caption group", "inferred from caption counts"))
        out.append((f"Captions at {cap.get('vertical_pct', 78)}% from the top", "copied from the finished project"))
    edl = next((o for o in ops if o.get("op") == "inject-edl"), None)
    total = (edl or {}).get("total_duration_s") or 0
    clips = (edl or {}).get("materials_added") or 1
    if not total and source:   # fall back to the CapCut draft meta
        meta = drafts_dir() / source / "draft_meta_info.json"
        try:
            total = (json.loads(meta.read_text(encoding="utf-8")).get("tm_duration") or 0) / 1_000_000
        except Exception:
            total = 0
    if total:
        out.append((f"Target length {max(10, int(total * 0.75))}-{int(total * 1.25)} s", "plus or minus 25% of the finished project"))
    if edl or total:
        out.append(("Story arc: " + ("hook, b-roll, setup, main, CTA, outro" if clips > 1 else "single take"), "guessed from the clip count"))
    return out


def main():
    if os.name == "nt":
        for s in (sys.stdout, sys.stderr):
            try:
                s.reconfigure(encoding="utf-8")
            except Exception:
                pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--source")
    ap.add_argument("--source-state")
    ap.add_argument("--name", required=True, help="style name, becomes my-rules/style-<name>.md")
    ap.add_argument("--description", default="")
    ap.add_argument("--root", help="override the pack folder")
    a = ap.parse_args()

    name = re.sub(r"[^a-z0-9]+", "-", a.name.lower()).strip("-")
    if not name:
        print(json.dumps({"ok": False, "error": "empty style name"}))
        sys.exit(1)
    root = Path(a.root).expanduser() if a.root else pack_root()
    try:
        state, state_path = load_state(root, a.source, a.source_state)
    except (FileNotFoundError, ValueError) as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)

    rules = infer(state, a.source)
    today = f"{date.today().year + 543}-{date.today():%m-%d}"   # Buddhist Era year: 2026 -> 2569
    desc = a.description or "(add a one-line description: what this style is for)"
    body = [f"# Style: {name}", "", desc, "",
            "Use only when I name it (for example \"do it like " + name + "\"). My rules win over the defaults.",
            "One line per rule: `- date: what to do or not do (why)`.", ""]
    body += [f"- {today}: {r} ({w})" for r, w in rules]
    out = root / "my-rules" / f"style-{name}.md"
    if out.exists():
        print(json.dumps({"ok": False, "error": f"{out.name} already exists; edit it instead"}))
        sys.exit(1)
    out.write_text("\n".join(body) + "\n", encoding="utf-8")

    idx = root / "my-rules" / "INDEX.md"
    line = f"- style-{name}.md: style \"{name}\", only when I name it ({a.description or 'saved from ' + (a.source or state_path.name)})"
    idx_text = idx.read_text(encoding="utf-8")
    if f"style-{name}.md" not in idx_text:
        idx.write_text(idx_text.rstrip("\n") + "\n" + line + "\n", encoding="utf-8")

    print(json.dumps({"ok": True, "op": "save-style", "file": str(out), "rules": len(rules),
                      "next": "Ask the user for description, target length, story arc and CTA; edit the file; keep it under %d lines." % LIMIT},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
