#!/usr/bin/env python3
"""indigo11-video project tool: keep every video in one tidy place. Run from the pack folder.

    python3 .claude/skills/indigo11-video/scripts/project.py new <slug> [--style promo] [--title "..."] [--date YYMMDD (Buddhist Era year, e.g. 691109)]
    python3 .claude/skills/indigo11-video/scripts/project.py done <project> [--title "..."] [--caption-file f.txt]
    python3 .claude/skills/indigo11-video/scripts/project.py where [words ...]          (no words = the latest 10)
    python3 .claude/skills/indigo11-video/scripts/project.py tidy [--yes]               (plan first; --yes = move. Never deletes)
    python3 .claude/skills/indigo11-video/scripts/project.py index [--quiet]            rebuild projects/INDEX.md
    python3 .claude/skills/indigo11-video/scripts/project.py check                      one line for the doctor: OK ... | WARN ...

Layout (working folder per video; the finished files are delivered to projects/results/):
  projects/<YYMMDD>-<name up to 32 chars>/  project.json BRIEF.md NOTES.md script/ assets/ src/ result/ (render output while working)
  projects/results/video-creation/<YYMMDD>-<name up to 32 chars>/  <slug>.mp4 + caption.txt   (`done` MOVES them here)
  projects/INDEX.md  date | style | title | folder | video | status   (built from every project.json in projects/)
  assets/brand/BRAND.md (+ brand.json) = the user's brand
Windows: use `python` instead of `python3`.
"""
import datetime, json, os, re, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]   # this file is <pack>/.claude/skills/indigo11-video/scripts/project.py
sys.path.insert(0, str(ROOT / "_system" / "engine"))
import _env  # noqa: E402  (UTF-8 console on Windows)

PROJ = ROOT / "projects"
INDEX, LOG, BRAND = PROJ / "INDEX.md", PROJ / "TIDY-LOG.md", ROOT / "assets" / "brand"
DELIVER = PROJ / "results" / "video-creation"   # finished videos of this skill; other job types: _system/engine/save_result.py
PACK_DIRS = ("raw-videos", "results")           # the pack's own folders in projects/ (not video projects)
DATED = re.compile(r"(\d{6}|\d{4}-\d{2}-\d{2})-([a-z0-9][a-z0-9-]*)")   # new form 691113-name, old form 2569-11-13-name
TOOL = ".claude/skills/indigo11-video/scripts/project.py"
PY = "python" if _env.WIN else "python3"


def be_date(d=None):
    """Today (or date d) as YYYY-MM-DD in the Buddhist Era: 2026 -> 2569."""
    d = d or datetime.date.today()
    return f"{d.year + 543}-{d.month:02d}-{d.day:02d}"


def rel(p): return Path(p).resolve().relative_to(ROOT).as_posix() if ROOT in Path(p).resolve().parents else str(p)


def opts(av):
    args, o, i = [], {}, 0
    while i < len(av):
        a = av[i]
        if a.startswith("--"):
            k, eq, v = a[2:].partition("=")
            if not eq and i + 1 < len(av) and not av[i + 1].startswith("--"): v = av[i + 1]; i += 1
            o[k] = v if (eq or v) else True
        else: args.append(a)
        i += 1
    return args, o


def meta(d):
    try: return json.loads((d / "project.json").read_text(encoding="utf-8"))
    except Exception: return {}


def save_meta(d, m): (d / "project.json").write_text(json.dumps(m, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def projects():
    if not PROJ.is_dir(): return []
    return [d for d in sorted(PROJ.iterdir()) if d.is_dir() and (d / "project.json").exists()]


def final_of(d, m):
    if m.get("final"):
        fp = (d / m["final"]).resolve()      # resolve first: an unresolved "..\\results\\..." path can pass Windows' 260-character limit and look missing
        if fp.exists(): return fp
    slug = m.get("slug") or d.name
    for sub in ("result", "out"):   # out/ = projects made before 2026-10-04
        for c in (d / sub / f"{slug}.mp4", d / sub / f"{d.name}.mp4"):
            if c.exists(): return c
    mp4 = [p for sub in ("result", "out") if (d / sub).is_dir() for p in (d / sub).glob("*.mp4") if not p.name.startswith("silent")]
    mp4.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return mp4[0] if mp4 else None


def build_index(quiet=False):
    rows = []
    for d in projects():
        m = meta(d); f = final_of(d, m)
        rows.append((m.get("date", ""), m.get("style", ""), m.get("title") or m.get("slug") or d.name,
                     rel(d), rel(f) if f else "", m.get("status", "draft")))
    rows.sort(key=lambda r: (r[0], r[3]), reverse=True)
    esc = lambda s: str(s).replace("|", "/")
    lines = ["# INDEX: all your videos", "",
             "1 video = 1 folder here. A finished video and its caption are in `projects/results/video-creation/<same folder name>/`. Ready-made looks are in `templates/`.", "",
             "Built automatically from every project.json (do not edit by hand: `" + PY + " " + TOOL + " index`)", "",
             "| date | template | title | folder | video | status |", "|---|---|---|---|---|---|"]
    lines += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]
    if not rows: lines.append("| | | (no videos yet: tell the AI \"make a new clip\") | | | |")
    PROJ.mkdir(exist_ok=True)
    INDEX.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not quiet: print(f"projects/INDEX.md: {len(rows)} projects")
    return rows


def find(q):
    p = Path(q)
    for c in (p, ROOT / q, PROJ / q):
        if c.is_dir() and (c / "project.json").exists(): return c.resolve()
        if c.is_dir() and c.name == "src" and (c.parent / "project.json").exists(): return c.parent.resolve()
    hits = [d for d in projects() if q.lower() in d.name.lower()]
    if len(hits) == 1: return hits[0]
    sys.exit(f"project '{q}' not found" + (f" (several match: {', '.join(h.name for h in hits)})" if hits else ""))


def cmd_new(av):
    """The pack ships one project type: the short video (new_short.py). --style is accepted for compatibility."""
    clean, i = [], 0
    while i < len(av):
        if av[i] == "--style":
            if av[i + 1:i + 2] and av[i + 1] not in ("topic-short", "indigo11-video"):
                sys.exit("this pack only creates topic-short projects (templates/ is not included)")
            i += 2
            continue
        if av[i].startswith("--style="):
            i += 1
            continue
        clean.append(av[i]); i += 1
    r = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "new_short.py"), *clean])
    sys.exit(r.returncode)


def cmd_done(av):
    args, o = opts(av)
    if not args: sys.exit("usage: project.py done <project>")
    d = find(args[0]); m = meta(d)
    if o.get("title"): m["title"] = str(o["title"])
    fresh = [p for p in (d / "result").glob("*.mp4") if not p.name.startswith("silent")] if (d / "result").is_dir() else []
    fresh.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    f = fresh[0] if fresh else final_of(d, m)   # a new render in result/ always wins over an older delivered file
    if not f: sys.exit(f"no video file in {rel(d)}/result/ yet: finish the render first")
    # caption: --caption-file, else the project's caption.txt, else a draft from the voice script + BRAND.md CTA
    cap = None
    for c in ([Path(o["caption-file"])] if o.get("caption-file") else []) + [f.parent / "caption.txt", DELIVER / d.name / "caption.txt", d / "caption.txt", d / "src" / "caption.txt"]:
        if c.exists(): cap = c.read_text(encoding="utf-8").strip(); break
    drafted = False
    if cap is None:
        drafted = True; lines = []
        for sj in (d / "script" / "vo.json", d / "src" / "vo" / "script.json", d / "vo" / "script.json"):
            if sj.exists():
                try: lines = json.loads(sj.read_text(encoding="utf-8")).get("lines", [])
                except Exception: lines = []
                break
        clean = lambda s: re.sub(r"\s+", " ", re.sub(r"\{([^}|]*)\|([^}]*)\}", r"\2", re.sub(r"\[[^\]]*\]", "", s)).replace("|", "")).strip()
        body = "\n\n".join(clean(x) for x in lines if clean(x)) or (m.get("title") or "")
        cta = ""
        bm = BRAND / "BRAND.md"
        if bm.exists():
            mm = re.search(r"\*\*CTA[^*]*:\*\*[ \t]*(.+)", bm.read_text(encoding="utf-8"))
            if mm and mm.group(1).strip(): cta = mm.group(1).strip()
        cap = "\n\n".join(x for x in (body, cta) if x)
    for rep in (d / "src" / "sound" / "mix.report.json", d / "sound" / "mix.report.json"):   # a CC BY music credit must be in the post
        try: cr = json.loads(rep.read_text(encoding="utf-8")).get("music_credit") if rep.exists() else None
        except Exception: cr = None
        if cr and cr not in cap and "Kevin MacLeod" not in cap: cap = cap.rstrip() + "\n\nMusic: " + cr
    capf = f.parent / "caption.txt"
    capf.write_text(cap + "\n", encoding="utf-8")
    # deliver: MOVE video + caption into projects/results/video-creation/<project>/ (no double copy of a big file)
    dest = DELIVER / d.name
    dest.mkdir(parents=True, exist_ok=True)
    for src in (f, capf):
        t = dest / src.name
        if src.exists() and src.resolve() != t.resolve():
            if t.exists(): t.unlink()
            shutil.move(str(src), str(t))
    f, capf = dest / f.name, dest / capf.name
    m["final"] = os.path.relpath(f, d).replace("\\", "/")
    m["status"] = "done"; m["done"] = be_date(); save_meta(d, m)
    nf = d / "NOTES.md"
    if nf.exists():
        t = nf.read_text(encoding="utf-8")
        if f"Closed | {m['final']}" not in t:
            row = f"| {m['done']} | Closed | {m['final']} |" if "| Date |" in t else f"- {m['done']} Closed -> {m['final']}"
            nf.write_text(t.rstrip("\n") + "\n" + row + "\n", encoding="utf-8")
    build_index(quiet=True)
    print(f"Done: {rel(f)} + {rel(capf)}" + (" (caption drafted automatically: check and edit before posting)" if drafted else ""))
    print("projects/INDEX.md updated")
    # 2026-10-06: a video without a voice-over must never be handed over silently. Tell the agent what to tell the user.
    has_key = bool(_env.key("ELEVENLABS_API_KEY"))
    voiced = any((d / sub / f"vo_final{e}").exists() for sub in ("src/vo", "vo") for e in (".wav", ".m4a", ".mp3"))
    if m.get("style") in ("topic-short", "indigo11-video"): voiced = has_key   # make_short.py: no key = silent track (--no-voice)
    if not voiced:
        print("\nThis clip has no voice-over. Tell the user in the final message (under a \"Voice-over\" heading):")
        if has_key: print("  1) Why there is no voice-over (an ElevenLabs key exists, but this clip was made without speech)")
        else:
            print("  1) There is no voice-over because there is no ElevenLabs key yet")
            print("  2) How to add the key: open the .env file in the pack folder, add the key after ELEVENLABS_API_KEY= and save"
                  "\n     (create a key at https://elevenlabs.io/app/settings/api-keys)")
        print(f"  {'2' if has_key else '3'}) To add a voice-over, say \"add a voice-over to this clip\" and rerun make_short.py without --no-voice (--skip-tts reuses an existing voice)")


def cmd_where(av):
    rows = build_index(quiet=True)
    words = [w.lower() for w in av if not w.startswith("--")]
    hit = [r for r in rows if all(w in " ".join(r).lower() for w in words)] if words else rows[:10]
    if not hit: print("not found in projects/INDEX.md" + (f" (searched: {' '.join(words)})" if words else "")); return
    for date, style, title, path, final, status in hit:
        print(f"{date}  {title}  ({style}, {status})\n    folder: {path}\n    video:  {final or '-'}")


def cmd_tidy(av):
    """Plan, then (with --yes) MOVE stray things into the layout. Never deletes. Every move is appended to projects/TIDY-LOG.md."""
    args, o = opts(av)
    plan = []   # (src, dst, why)
    if PROJ.is_dir():
        for p in sorted(PROJ.iterdir()):
            if p.name.startswith((".", "_")) or p.name in ("README.md", "README.txt", "INDEX.md", "TIDY-LOG.md", *PACK_DIRS): continue   # raw-videos/ and results/ are the pack's own folders
            if p.is_file():
                plan.append((p, PROJ / "_loose" / p.name, "loose file in projects/")); continue
            if DATED.fullmatch(p.name) and (p / "project.json").exists(): continue
            date = be_date(datetime.date.fromtimestamp(p.stat().st_mtime))
            slug = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", p.name.lower())).strip("-") or "untitled"
            m = DATED.fullmatch(p.name)
            new = p.name if m else f"{date.replace('-', '')[2:]}-{slug[:32].rstrip('-')}"
            plan.append((p, PROJ / new, "folder name does not follow the pattern" if not m else "no project.json"))
    if not plan: print("All tidy: nothing to move"); build_index(quiet=True); return
    print("Tidy plan (moves only, nothing is deleted):")
    for s, t, why in plan: print(f"  {rel(s)}  ->  {rel(t)}   ({why})")
    if not o.get("yes"): print("\nNothing moved yet. If this looks right, run again with --yes"); return
    stamp = be_date() + datetime.datetime.now().strftime(" %H:%M")
    if not LOG.exists(): LOG.write_text("# TIDY-LOG: every move made by project.py tidy (move back using these lines)\n\n| Time | From | To | Reason |\n|---|---|---|---|\n", encoding="utf-8")
    moved = 0
    for s, t, why in plan:
        if t.exists() and t.resolve() != s.resolve():
            print(f"  skipped {rel(s)}: {rel(t)} already exists"); continue
        t.parent.mkdir(parents=True, exist_ok=True)
        if t.resolve() != s.resolve(): shutil.move(str(s), str(t)); moved += 1
        with LOG.open("a", encoding="utf-8") as lg: lg.write(f"| {stamp} | {rel(s)} | {rel(t)} | {why} |\n")
        if t.is_dir() and not (t / "project.json").exists():
            mm = DATED.fullmatch(t.name)
            bj = {}
            try: bj = json.loads((BRAND / "brand.json").read_text(encoding="utf-8"))
            except Exception: pass
            save_meta(t, {"date": mm.group(1), "brand": bj.get("name", "my-brand"), "style": "?", "slug": mm.group(2), "title": mm.group(2).replace("-", " "),
                          "status": "done" if final_of(t, {}) else "draft", "tidied_from": rel(s)})
    build_index(quiet=True)
    print(f"Moved {moved} items. Logged in projects/TIDY-LOG.md. projects/INDEX.md updated")


def cmd_check(av):
    miss = [x for x in ("projects",) if not (ROOT / x).is_dir()] + ([] if INDEX.exists() else ["projects/INDEX.md"])
    if miss: print("WARN missing: " + ", ".join(miss)); return
    listed = set(re.findall(r"\|\s*(projects/[^|\s]+)\s*\|", INDEX.read_text(encoding="utf-8")))
    have = {rel(d) for d in projects()}
    stray = [p.name for p in PROJ.iterdir() if p.is_dir() and not p.name.startswith((".", "_")) and p.name not in PACK_DIRS and not (DATED.fullmatch(p.name) and (p / "project.json").exists())]
    if have != listed: print(f"WARN projects/INDEX.md does not match the folders ({len(have)} projects, INDEX lists {len(listed)}): run project.py index"); return
    if stray: print(f"WARN folders not following the pattern in projects/: {', '.join(stray[:3])} (tidy)"); return
    done = sum(1 for d in projects() if meta(d).get("status") == "done")
    print(f"OK {len(have)} projects ({done} done). projects/ and INDEX.md are present")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"): sys.exit(__doc__)
    c, rest = sys.argv[1], sys.argv[2:]
    if c == "new": cmd_new(rest)
    elif c == "done": cmd_done(rest)
    elif c == "where": cmd_where(rest)
    elif c == "tidy": cmd_tidy(rest)
    elif c == "index": build_index(quiet="--quiet" in rest)
    elif c == "check": cmd_check(rest)
    else: sys.exit(__doc__)
