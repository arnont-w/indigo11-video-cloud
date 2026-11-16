#!/usr/bin/env python3
"""Keep an asset (music, sound effect, image, video clip, icon, font) in the pack's EXISTING assets/ folder.

    python3 _system/engine/save_asset.py <kind> <file-or-folder> [<file> ...] [--copy]
    python3 _system/engine/save_asset.py --list

kind -> folder:
  music            assets/music/            background music
  sound-effects    assets/sound-effects/    sound effects (a new sound still needs a row in SOUND-GUIDE.md before use)
  images           assets/images/           pictures
  icons            assets/images/icons/     icons (SVG or PNG)
  videos           assets/videos/           video clips / B-roll
  fonts            assets/fonts/            fonts (keep the font's own folder, then write its name in assets/brand/BRAND.md)

WHY this exists: kept assets used to be filed under projects/results/assets/<project>/, a second "assets" folder next to the real one. Results are
finished deliverables (projects/results/); anything you keep to USE again goes into assets/ so the search order (my own files first) finds it.
Rules: files are MOVED by default (--copy keeps the original); never overwrites (same name and size = skipped as already saved; same name but different = _2, _3 ...).
Name files after what they are (calm-piano.mp3, tokyo-street-night.mp4): the search matches those words. Windows: use `python`.
"""
import shutil
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8")
    except Exception: pass

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "assets"
KINDS = {"music": "music", "sound-effects": "sound-effects", "sfx": "sound-effects", "images": "images", "icons": "images/icons",
         "videos": "videos", "fonts": "fonts"}


def main():
    av = sys.argv[1:]
    if not av or av[0] in ("-h", "--help"):
        print(__doc__); return
    if av[0] == "--list":
        for k, v in KINDS.items():
            d = ASSETS / v
            print(f"{k:14} -> assets/{v}/  ({sum(1 for _ in d.rglob('*') if _.is_file()) if d.exists() else 0} files)")
        return
    copy = "--copy" in av
    av = [a for a in av if a != "--copy"]
    kind, files = av[0], av[1:]
    if kind == "assets" or kind not in KINDS:
        sys.exit(f"kind must be one of: {', '.join(KINDS)} (got '{kind}')")
    if not files:
        sys.exit("give at least one file or folder")
    dest = ASSETS / KINDS[kind]
    dest.mkdir(parents=True, exist_ok=True)
    for f in files:
        src = Path(f).expanduser()
        if not src.exists():
            print(f"missing: {f}"); continue
        t = dest / src.name
        if t.exists() and src.is_file() and t.is_file() and t.stat().st_size == src.stat().st_size:
            print(f"already saved: {t.relative_to(ROOT).as_posix()} (same name and size, nothing copied)")
            if not copy and src.resolve() != t.resolve():
                pass          # leave the source alone: the user can delete the duplicate
            continue
        n = 2
        while t.exists():
            t = dest / f"{src.stem}_{n}{src.suffix}"; n += 1
        (shutil.copytree if src.is_dir() else shutil.copy2)(str(src), str(t))
        if not copy:
            shutil.rmtree(src) if src.is_dir() else src.unlink()
        print(f"{'copied' if copy else 'moved'}: {t.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
