#!/usr/bin/env python3
"""indigo11-video: start a short video in the standard project folder (run from the pack folder).

    python3 .claude/skills/indigo11-video/scripts/new_short.py <slug> [--title "Clip title"] [--date YYMMDD (Buddhist Era year, e.g. 691109)] [--brand <name>] [--lang en|th]
    (same as: python3 .claude/skills/indigo11-video/scripts/project.py new <slug> --style topic-short)

Creates projects/<YYMMDD>-<name up to 32 chars>/
  project.json (style indigo11-video, lang) . BRIEF.md . NOTES.md
  script/script.txt   the script sent to the voice (the WHOLE file is spoken, so no notes in it)
  assets/SOURCES.md   Facts (every number/name/year in the script + link) and Files (every clip used + licence)
  assets/broll/       where B-roll goes (scan_broll.py --out)   src/ working files   result/<slug>.mp4 the finished video

--lang en (default) = Standard British English. --lang th = Thai.
"""
import datetime, json, re, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _starter import ROOT, PY  # noqa: E402

args, opt, av, i = [], {}, sys.argv[1:], 0
while i < len(av):
    a = av[i]
    if a.startswith("--"):
        k, eq, v = a[2:].partition("=")
        if not eq and i + 1 < len(av) and not av[i + 1].startswith("--"): v = av[i + 1]; i += 1
        opt[k] = v if (eq or v) else True
    else: args.append(a)
    i += 1
if not args: sys.exit(__doc__)
try: bj = json.loads((ROOT / "assets" / "brand" / "brand.json").read_text(encoding="utf-8"))
except Exception: bj = {}
slug, brand = args[0].strip().lower(), str(opt.get("brand") or bj.get("name") or "my-brand").strip().lower()
if len(slug) > 32:      # names up to 32 characters keep Windows paths short (the folder name appears twice: projects/ and results/)
    print(f"[!] the name is {len(slug)} characters: shortened to 32 -> {slug[:32].rstrip('-')}")
    slug = slug[:32].rstrip("-")
lang = str(opt.get("lang") or bj.get("lang") or "en").lower()
if lang not in ("en", "th"): sys.exit("--lang must be en or th")
SLUG = re.compile(r"[a-z0-9][a-z0-9-]*")
if not SLUG.fullmatch(slug): sys.exit("the slug may only use a-z 0-9 and - (for example why-cats-purr)")
if not SLUG.fullmatch(brand): sys.exit("the brand name may only use a-z 0-9 and - (for example indigo11, my-shop)")
date = str(opt.get("date") or f"{datetime.date.today().year + 543}-{datetime.date.today():%m-%d}")   # Buddhist Era year: 2026 -> 2569
if re.fullmatch(r"\d{6}", date): date = f"25{date[:2]}-{date[2:4]}-{date[4:]}"                          # 691113 -> 2569-11-13
if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date): sys.exit("--date must be YYMMDD (691113) or YYYY-MM-DD (2569-11-13)")
STAMP = date[2:4] + date[5:7] + date[8:10]       # the folder name uses YYMMDD (Buddhist Era): 2569-11-13 -> 691113
title = str(opt.get("title") or slug.replace("-", " "))
P = ROOT / "projects" / f"{STAMP}-{slug}"
if (P / "project.json").exists() and not opt.get("force"):
    sys.exit(f"{P.relative_to(ROOT).as_posix()} already exists: use another slug")
for d in ("script", "assets/broll", "src", "result"): (P / d).mkdir(parents=True, exist_ok=True)


def keep(path, text):
    if not path.exists(): path.write_text(text, encoding="utf-8")


language = "Standard British English" if lang == "en" else "Thai"
size = "about 145 words at 59 s (75 at 30 s)" if lang == "en" else "about 840 characters at 59 s (427 at 30 s)"
keep(P / "script" / "script.txt", "")
keep(P / "assets" / "SOURCES.md", "# Sources\n\n## Facts (every number on screen)\n| claim | value | source (URL, date checked) |\n|---|---|---|\n\n## Files in assets/\n| file | source | licence | may be posted? |\n|---|---|---|---|\n")
keep(P / "BRIEF.md", f"""# {title}

- **Brand:** {brand} (assets/brand/BRAND.md) . **template:** topic-short (skill indigo11-video) . **date:** {date}
- **Language:** {language}
- **Topic:**
- **Story template (references/story-framework.md):**
- **Question the hook asks / sentence at the end that answers it:**
- **Length:** up to 59 seconds ({size}) . 9:16 . TikTok / Reels / Shorts
- **B-roll:** pexels / pixabay (may be posted) . youtube = viewing and prototypes only, never post
""")
keep(P / "NOTES.md", f"# Notes . {title}\n\n| Date | Topic | Decision / feedback |\n|---|---|---|\n| {date} | Project created | style indigo11-video, {language} |\n")
pj = P / "project.json"
meta = {"date": date, "brand": brand, "style": "indigo11-video", "slug": slug, "title": title, "lang": lang, "status": "draft"}
pj.write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
if not (ROOT / "assets" / "brand" / "BRAND.md").exists():
    print("[!] assets/brand/BRAND.md does not exist yet (write your brand details there)")
tool = Path(__file__).resolve().parent / "project.py"
if tool.exists(): subprocess.run([sys.executable, str(tool), "index", "--quiet"])

rel = P.relative_to(ROOT).as_posix()
S = ".claude/skills/indigo11-video/scripts"
words = "up to about 145 words, 10-14 words per sentence" if lang == "en" else "up to about 840 characters; a space = a pause + a caption break"
print(f"""Created {rel}/ (template topic-short, skill indigo11-video, language {language})
Next steps (details: .claude/skills/indigo11-video/SKILL.md):
  1) Research real facts (WebSearch/WebFetch): every number/name/year goes in the Facts table in {rel}/assets/SOURCES.md
  2) Pick the story template from .claude/skills/indigo11-video/references/story-framework.md and note it in BRIEF.md
  3) Write the script {rel}/script/script.txt (up to 59 s, {words})
  4) {PY} {S}/scan_broll.py --q "<one-word topic>" --out {rel}/assets/broll      (with a Pexels/Pixabay key = stock libraries you may post)
     open {rel}/assets/broll/02-frames/_sheets/all.jpg and look, then
     {PY} {S}/scan_broll.py --cut {rel}/assets/broll --pick "c0:2-8,c1:..."
  5) {PY} {S}/make_short.py --lang {lang} --script {rel}/script/script.txt --broll {rel}/assets/broll/03-selected --out {rel}
  6) {PY} {S}/verify.py {rel}/result/{slug}.mp4   and look at the picture
  7) {PY} {S}/project.py done {rel}   -> caption + projects/INDEX.md""")
