#!/usr/bin/env python3
"""find_local.py - STEP 1 of the asset search order: look in the user's OWN files before anything else.

  python3 find_local.py --type image|video|icon|music|sfx|font|template --query "city night market" [--count 5]

Order of the whole search (the user's rule): 1 local files -> 2 free sources (Pexels, Pixabay, Mixkit, Coverr; YouTube = prototype only)
-> 3 draw it yourself (not a real scene) or generate it (real photo/footage needed) -> 4 buying, last, and only with the user's yes.

Folders searched (the first four are the user's; the last holds earlier downloads):
  images/video: assets/images  assets/videos  projects/raw-videos  assets/templates  projects/_work/assets (earlier downloads)
  icons: assets/images  assets/brand  assets/templates   |   music: assets/music  projects/raw-videos  the video skill's music/ folder   |   sfx: assets/sound-effects (incl. its sfx/ pack)
  fonts: assets/fonts  assets/brand   |   templates: the folders in assets/templates (matched on the folder name and README.md)
A file matches when the words of the query appear in its path (file name or folder name), case-insensitive; the more words match, the
higher it ranks. Queries may be English or Thai. Prints JSON: {"ok", "count", "files":[{"file","score"}]}. Nothing is copied or changed.
Tip for the user: name files and folders after what is in them ("tokyo-street-night.mp4", "products/serum-bottle.png") so they can be found.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys

IMG = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
VID = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
ICO = {".svg", ".png", ".webp", ".jpg", ".jpeg"}
AUD = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}
FNT = {".ttf", ".otf", ".woff", ".woff2"}
EXTS = {"image": IMG, "video": VID, "icon": ICO, "music": AUD, "sfx": AUD, "font": FNT}
ROOTS_BY = {      # the user's own folders for each kind, then earlier downloads (projects/_work/assets)
    "image": ["assets/images", "assets/videos", "projects/raw-videos", "assets/templates", "projects/_work/assets"],
    "video": ["assets/videos", "assets/images", "projects/raw-videos", "assets/templates", "projects/_work/assets"],
    "icon": ["assets/images", "assets/brand", "assets/templates", "projects/raw-videos", "projects/_work/assets"],
    "music": ["assets/music", "projects/raw-videos", ".claude/skills/indigo11-video/music", "projects/_work/assets"],
    "sfx": ["assets/sound-effects", "projects/raw-videos", "projects/_work/assets"],
    "font": ["assets/fonts", "assets/brand", "projects/_work/assets"],
}


def kit_root(start=None):
    d = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.exists(os.path.join(d, "CLAUDE.md")):
            return d
        p = os.path.dirname(d)
        if p == d:
            return os.path.abspath(start or os.getcwd())
        d = p


def words(q):
    return [w for w in re.split(r"[\s\-_/.,]+", q.lower()) if len(w) >= 2]


def find_templates(query, count=5, root=None):
    """templates are FOLDERS in assets/templates/<name>/: match the folder name (weight 3) and the words in its README.md"""
    root = root or kit_root()
    base = os.path.join(root, "assets", "templates")
    ws = words(query)
    hits = []
    if not os.path.isdir(base) or not ws:
        return hits
    for name in sorted(os.listdir(base)):
        d = os.path.join(base, name)
        if not os.path.isdir(d):
            continue
        txt = ""
        try:
            txt = open(os.path.join(d, "README.md"), encoding="utf-8", errors="replace").read(3000).lower()
        except Exception:
            pass
        score = sum(3 for w in ws if w in name.lower()) + sum(1 for w in ws if w in txt)
        if score:
            hits.append({"file": d, "score": score, "rel": os.path.relpath(d, root).replace("\\", "/")})
    hits.sort(key=lambda h: -h["score"])
    return hits[:count]


def find_local(kind, query, count=5, root=None):
    if kind == "template":
        return find_templates(query, count, root)
    root = root or kit_root()
    exts = EXTS[kind]
    ws = words(query)
    if not ws:
        return []
    hits = []
    for r in ROOTS_BY[kind]:
        base = os.path.join(root, r)
        if not os.path.isdir(base):
            continue
        for dp, _dn, fns in os.walk(base):
            for fn in fns:
                if os.path.splitext(fn)[1].lower() not in exts:
                    continue
                rel = os.path.relpath(os.path.join(dp, fn), root).replace("\\", "/")
                hay = rel.lower()
                score = sum(1 for w in ws if w in hay)
                if score:
                    hits.append({"file": os.path.join(root, rel), "score": score, "rel": rel})
    hits.sort(key=lambda h: (-h["score"], len(h["rel"])))
    return hits[:count]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--type", required=True, choices=["image", "video", "icon", "music", "sfx", "font", "template"])
    ap.add_argument("--query", required=True)
    ap.add_argument("--count", type=int, default=5)
    a = ap.parse_args()
    hits = find_local(a.type, a.query, a.count)
    print(json.dumps({"ok": bool(hits), "type": a.type, "query": a.query, "count": len(hits),
                      "files": [{"file": h["file"], "score": h["score"]} for h in hits],
                      "next": None if hits else "nothing local: go to step 2 (free sources)"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
