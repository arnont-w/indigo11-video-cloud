#!/usr/bin/env python3
"""Save finished files into projects/results/ grouped by job type, then by project.

    python3 _system/engine/save_result.py <type> <project-name> <file> [<file> ...] [--copy] [--date YYMMDD]
    python3 _system/engine/save_result.py --list

Layout (one folder per job, same project = same folder):
  projects/results/<type>/<YYMMDD>-<project-name up to 32 characters>/<files>

Types (the only allowed folder names under results/):
  video-creation        a whole video made from a topic (indigo11-video): <slug>.mp4 + caption.txt
  editing               cleaned / cut / captioned footage (indigo11-edit): cleaned.mp4, final.mp4, SRT
  reverse-engineering   a reference video studied or copied (indigo11-watch): SPEC.md, sheets, style pack
  transcription         captions and transcripts (indigo11-transcribe): captions.srt, captions.json, words.json

Not a type: assets you keep (music, sound effects, images, videos, fonts) go into the matching folder of assets/ with save_asset.py, not under results/.

Rules: files are MOVED by default (no double copies of big videos); --copy keeps the original.
Never overwrites: if a file with the same name exists, the new one gets a _2, _3 ... suffix.
Same project, later job of the same type: pass the SAME project-name, the date of the first folder is reused.
Folder names are YYMMDD with the Buddhist Era year: 2026-11-13 -> 691113-<name>. --date takes YYMMDD (or 2569-11-13). The name is cut to 32 characters.
Windows: use `python` instead of `python3`.
"""
import datetime, re, shutil, sys, unicodedata
from pathlib import Path

for _s in (sys.stdout, sys.stderr):   # Thai / German file names must print on a Windows console
    try: _s.reconfigure(encoding="utf-8")
    except Exception: pass

ROOT = Path(__file__).resolve().parents[2]   # <pack>/_system/engine/save_result.py
RESULTS = ROOT / "projects" / "results"
TYPES = ("video-creation", "editing", "reverse-engineering", "transcription")
DATED = re.compile(r"(?:\d{6}|\d{4}-\d{2}-\d{2})-(.+)")   # 691113-name (new) or 2569-11-13-name (old)
MAXNAME = 32                                                    # names up to 32 characters keep Windows paths short


def slugify(s):
    """Keep letters of any language (Thai, German), lower-case, hyphens only."""
    s = "".join(c if (c.isalnum() or unicodedata.category(c).startswith("M")) else "-" for c in s.strip().lower())   # Thai vowel/tone marks are category M: keep them
    return re.sub(r"-+", "-", s).strip("-") or "untitled"


def project_folder(kind, name, date):
    """Reuse an existing folder of the same project (any date), else make <date>-<slug>."""
    slug = DATED.fullmatch(name).group(1) if DATED.fullmatch(name) else slugify(name)
    slug = slug[:MAXNAME].rstrip("-")
    base = RESULTS / kind
    if base.is_dir():
        for d in sorted(base.iterdir()):
            m = DATED.fullmatch(d.name)
            if d.is_dir() and m and m.group(1)[:MAXNAME].rstrip("-") == slug: return d
    return base / f"{date}-{slug}"


def unique(p):
    if not p.exists(): return p
    i = 2
    while (p.with_name(f"{p.stem}_{i}{p.suffix}")).exists(): i += 1
    return p.with_name(f"{p.stem}_{i}{p.suffix}")


def main(av):
    if not av or av[0] in ("-h", "--help"): sys.exit(__doc__)
    if av[0] == "--list":
        for k in TYPES:
            d = RESULTS / k
            n = [x.name for x in sorted(d.iterdir()) if x.is_dir()] if d.is_dir() else []
            print(f"{k}/  ({len(n)})" + "".join(f"\n    {x}" for x in n))
        return
    copy = "--copy" in av
    t0 = datetime.date.today()
    date = f"{(t0.year + 543) % 100:02d}{t0.month:02d}{t0.day:02d}"   # YYMMDD with the Buddhist Era year: 2026-11-13 -> 691113
    args, i = [], 0
    while i < len(av):
        if av[i] == "--copy": i += 1; continue
        if av[i] == "--date":
            date = av[i + 1]
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date): date = date[2:4] + date[5:7] + date[8:10]   # 2569-11-13 -> 691113
            if not re.fullmatch(r"\d{6}", date): sys.exit("--date must be YYMMDD (691113) or YYYY-MM-DD (2569-11-13)")
            i += 2; continue
        args.append(av[i]); i += 1
    if len(args) < 3: sys.exit("usage: save_result.py <type> <project-name> <file> [<file> ...]")
    kind, name, files = args[0], args[1], [Path(f) for f in args[2:]]
    if kind == "assets": sys.exit("there is no results type 'assets': keep music, sound effects, images, videos and fonts in the matching folder of assets/ with:  python _system/engine/save_asset.py <music|sound-effects|images|icons|videos|fonts> <files...>")
    if kind not in TYPES: sys.exit(f"type must be one of: {', '.join(TYPES)} (got '{kind}')")
    miss = [str(f) for f in files if not f.exists()]
    if miss: sys.exit("not found: " + ", ".join(miss))
    dest = project_folder(kind, name, date)
    dest.mkdir(parents=True, exist_ok=True)
    locked = []
    for f in files:
        t = dest / f.name
        if not copy and f.is_file() and t.is_file() and t.stat().st_size == f.stat().st_size:
            # an earlier run already copied it but the original was in use: finish the move now
            try: f.unlink(); print(f"moved {f.name}  ->  {t.relative_to(ROOT).as_posix()}  (finished earlier copy)")
            except OSError: locked.append(f.name)
            continue
        t = unique(t)
        if copy or f.is_dir():
            shutil.copytree(f, t) if f.is_dir() else shutil.copy2(f, t)
            if f.is_dir() and not copy: shutil.rmtree(f)
        else:
            try: shutil.move(str(f), str(t))
            except OSError:   # Windows: the original is open in another program (CapCut, a player, OneDrive sync)
                if f.exists() and t.exists() and t.stat().st_size == f.stat().st_size:
                    locked.append(f.name)   # the copy is complete; only the original could not be removed
                    print(f"copied {f.name}  ->  {t.relative_to(ROOT).as_posix()}  (original is in use, not removed yet)"); continue
                if f.exists() and t.exists(): t.unlink()   # partial copy: remove it, the original is untouched
                sys.exit(f"cannot move {f.name}: it is in use by another program. Close it and run the same command again.")
        print(("copied " if copy else "moved ") + f"{f.name}  ->  {t.relative_to(ROOT).as_posix()}")
    print(f"RESULT FOLDER: {dest.relative_to(ROOT).as_posix()}")
    if locked: print("STILL IN USE (close the program using them, then run the same command again to finish the move): " + ", ".join(locked))


if __name__ == "__main__":
    main(sys.argv[1:])
