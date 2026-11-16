#!/usr/bin/env python3
"""Save, list and find reusable TEMPLATES (a look you want to use again) in assets/templates/.

    python3 _system/engine/save_template.py <name> <file-or-folder> [<more> ...] [--copy] [--source "<url or file of the reference>"] [--description "one line"] [--origin own|reverse-engineered]
    python3 _system/engine/save_template.py --list        names, origin (own or reverse-engineered), source; also rewrites assets/templates/INDEX.md

A template = one folder:  assets/templates/<name>/
    README.md          the look in 5 lines, what it suits, where it came from, the date (Buddhist Era year)   (made for you if missing)
    style.json         palette, fonts, motion profile, transitions, pacing   (from a reverse-engineered video, or your own)
    sfx/palette.json   which sound effect for which role
    anim.html          optional: the animated page (see _system/engine/animation-starter.html)
    stills/            optional: 3-4 reference pictures
Use it for: your own template, or the style pack of a video you reverse engineered (indigo11-watch, level 3).
Not for: CapCut editing styles (those are my-rules/style-<name>.md, made by save_style.py) and finished videos (projects/results/).

Files are MOVED by default; --copy keeps the originals. Nothing is overwritten: an existing file gets a _2, _3 ... name.
Windows: use `python` instead of `python3`.
"""
import datetime, shutil, sys, unicodedata
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8")
    except Exception: pass

ROOT = Path(__file__).resolve().parents[2]
TPL = ROOT / "assets" / "templates"


def slug(s):
    s = "".join(c if (c.isalnum() or unicodedata.category(c).startswith("M")) else "-" for c in s.strip().lower())
    return "-".join(x for x in s.split("-") if x) or "untitled"


def unique(p):
    if not p.exists(): return p
    i = 2
    while p.with_name(f"{p.stem}_{i}{p.suffix}").exists(): i += 1
    return p.with_name(f"{p.stem}_{i}{p.suffix}")


def first_line(d):
    try:
        for ln in (d / "README.md").read_text(encoding="utf-8").splitlines():
            if ln.strip() and not ln.startswith("#"): return ln.strip()[:100]
    except Exception: pass
    return ""


def meta(d):
    """description, origin, source, saved date from a template's README.md"""
    m = {"desc": first_line(d), "origin": "", "source": "", "saved": ""}
    try:
        for ln in (d / "README.md").read_text(encoding="utf-8").splitlines():
            for k, key in (("- Origin:", "origin"), ("- Source:", "source"), ("- Saved:", "saved")):
                if ln.startswith(k): m[key] = ln[len(k):].strip()
    except Exception: pass
    return m


def write_index():
    dirs = [d for d in sorted(TPL.iterdir()) if d.is_dir()] if TPL.is_dir() else []
    rows = ["# My templates", "",
            'To use one, tell Claude: "use my template <name>". Built automatically by `_system/engine/save_template.py` (do not edit by hand).', "",
            "| name | origin | what it is | source | saved |", "|---|---|---|---|---|"]
    esc = lambda s: str(s).replace("|", "/")
    for d in dirs:
        m = meta(d)
        rows.append(f"| {d.name} | {esc(m['origin'] or '?')} | {esc(m['desc'])} | {esc(m['source'])} | {esc(m['saved'])} |")
    if not dirs: rows.append("| | | (no templates yet) | | |")
    TPL.mkdir(parents=True, exist_ok=True)
    (TPL / "INDEX.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return dirs


def opts(av):
    args, o, i = [], {}, 0
    while i < len(av):
        a = av[i]
        if a in ("--copy", "--list"): o[a[2:]] = True
        elif a in ("--source", "--description", "--origin") and i + 1 < len(av): o[a[2:]] = av[i + 1]; i += 1
        else: args.append(a)
        i += 1
    return args, o


def main(av):
    if not av or av[0] in ("-h", "--help"): sys.exit(__doc__)
    args, o = opts(av)
    if o.get("list"):
        dirs = write_index()
        print(f"{len(dirs)} templates in assets/templates/" + (":" if dirs else " (none yet)"))
        for d in dirs:
            m = meta(d); print(f"  {d.name:<28} [{m['origin'] or '?':<19}] {m['desc']}")
        return
    if len(args) < 2: sys.exit("usage: save_template.py <name> <file-or-folder> [...] [--copy] [--source ...] [--description ...]")
    name, items = slug(args[0]), [Path(a) for a in args[1:]]
    miss = [str(p) for p in items if not p.exists()]
    if miss: sys.exit("not found: " + ", ".join(miss))
    dest = TPL / name; dest.mkdir(parents=True, exist_ok=True)
    for p in items:
        if p.is_dir():   # a folder's CONTENT goes in, so style/ -> assets/templates/<name>/
            for c in sorted(p.iterdir()):
                t = unique(dest / c.name)
                (shutil.copytree if c.is_dir() else shutil.copy2)(c, t)
                if not o.get("copy"): shutil.rmtree(c) if c.is_dir() else c.unlink()
                print(f"{'copied' if o.get('copy') else 'moved'} {c.name}  ->  {t.relative_to(ROOT).as_posix()}")
            if not o.get("copy"):
                try: p.rmdir()
                except OSError: pass
        else:
            t = unique(dest / p.name)
            shutil.copy2(p, t) if o.get("copy") else shutil.move(str(p), str(t))
            print(f"{'copied' if o.get('copy') else 'moved'} {p.name}  ->  {t.relative_to(ROOT).as_posix()}")
    rd = dest / "README.md"
    if not rd.exists():
        d = datetime.date.today(); be = f"{d.year + 543}-{d.month:02d}-{d.day:02d}"
        rd.write_text(f"# Template: {name}\n\n{o.get('description') or '(one line: what this look is and what it suits)'}\n\n"
                      f"- Saved: {be}\n- Origin: {o.get('origin') or ('reverse-engineered' if o.get('source') else 'own')}\n- Source: {o.get('source') or '(my own, or the reference video it was taken from)'}\n"
                      "- Files: style.json (look), sfx/palette.json (sounds), anim.html (animated page, optional), stills/ (pictures, optional)\n"
                      "- Use: say \"use my template " + name + "\" and Claude reads this folder first.\n", encoding="utf-8")
        print(f"made {rd.relative_to(ROOT).as_posix()} (edit the first line: say what the look is)")
    write_index()
    print(f"TEMPLATE FOLDER: {dest.relative_to(ROOT).as_posix()}  (list: assets/templates/INDEX.md)")


if __name__ == "__main__":
    main(sys.argv[1:])
