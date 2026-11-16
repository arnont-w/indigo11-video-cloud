"""indigo11 Video · _system/engine/check_sfx_guide.py: list sound files that have no row in SOUND-GUIDE.md yet.

    python _system/engine/check_sfx_guide.py

Scans assets/sound-effects/ (all sub-folders) for audio files, compares their names with the first column of the table in
_system/sound-guide/SOUND-GUIDE.md, and prints the files that are missing, with their length when ffprobe is available.
Exit code 0 = every sound is described, 1 = some are missing. Also lists guide rows whose file no longer exists.
Claude cannot hear, so each missing sound needs the user's description before Claude may use it.
"""
import re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SFX = ROOT / "assets" / "sound-effects"
GUIDE = ROOT / "_system" / "sound-guide" / "SOUND-GUIDE.md"
AUD = {".wav", ".mp3", ".flac", ".ogg", ".aif", ".aiff", ".m4a"}


def length(p):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                             capture_output=True, text=True, timeout=20).stdout.strip()
        return "%.2f s" % float(out)
    except Exception:
        return "? s"


def main():
    listed = set()
    if GUIDE.exists():
        for line in GUIDE.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\|\s*([^|]+?)\s*\|", line)
            if m and not m.group(1).startswith(("File", "---")):
                listed.add(m.group(1).replace("\\", "/").lower())
    files = sorted(p for p in SFX.rglob("*") if p.suffix.lower() in AUD)
    missing = []
    present = set()
    for p in files:
        rel = p.relative_to(SFX).as_posix().lower()
        present.add(rel); present.add(p.name.lower())
        if rel not in listed and p.name.lower() not in listed:
            missing.append(p)
    gone = sorted(x for x in listed if x not in present)
    print("Sound files: %d, described in SOUND-GUIDE.md: %d" % (len(files), len(files) - len(missing)))
    if missing:
        print("\nNo row in the guide yet (ask the user what each is for):")
        for p in missing:
            print("  - %s (%s)" % (p.relative_to(SFX).as_posix(), length(p)))
    if gone:
        print("\nGuide rows with no file (remove or fix):")
        for x in gone:
            print("  - " + x)
    if not missing and not gone:
        print("Every sound is described.")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
