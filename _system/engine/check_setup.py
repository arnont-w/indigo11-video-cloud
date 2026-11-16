"""indigo11 setup check: Python, ffmpeg, bash (Git Bash on Windows), packages and keys in one report.

Run from the pack folder:  python _system/engine/check_setup.py   (Mac: python3)
Add --json for a machine-readable result. Standard library only, so it runs before anything is installed.
Keys are never printed: only "set" or "empty". Exit code 0 = ready, 1 = something required is missing.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WIN = os.name == "nt"

# the same runtime folders _system/engine/_env.py puts on PATH
BASE = Path(os.environ.get("LOCALAPPDATA", Path.home())) if WIN else Path.home()
for d in ("indigo11", "ii23") if WIN else (".indigo11", ".ii23"):
    rt = BASE / d / "runtime"
    extra = [rt / "bin", rt / "node"] + ([rt / "python", rt / "python" / "Scripts"] if WIN else [])
    os.environ["PATH"] = os.pathsep.join([str(p) for p in extra if p.is_dir()] + [os.environ.get("PATH", "")])

PACKAGES = [("pythainlp", "pythainlp"), ("nlpo3", "nlpo3"), ("numpy", "numpy"),
            ("PIL", "Pillow"), ("requests", "requests"), ("certifi", "certifi")]
STT_KEYS = ["ELEVENLABS_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY"]
OPT_KEYS = ["PEXELS_API_KEY", "PIXABAY_API_KEY"]
rows = []  # (status, name, detail)  status: ok / MISSING / warn


def add(ok, name, detail="", required=True):
    rows.append(("ok" if ok else ("MISSING" if required else "warn"), name, detail))


def tool_version(cmd):
    try:
        out = subprocess.run([cmd, "-version"], capture_output=True, text=True, timeout=20).stdout
        return out.splitlines()[0] if out else ""
    except Exception:
        return ""


def read_env():
    found = {}
    p = ROOT / ".env"
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                found[k.strip()] = v.strip().strip("'\"")
    for k in STT_KEYS + OPT_KEYS:
        if os.environ.get(k, "").strip():
            found[k] = os.environ[k].strip()
    return found


def main():
    v = sys.version_info
    add(v >= (3, 10), "Python 3.10+", f"found {v.major}.{v.minor}.{v.micro} at {sys.executable}")
    for exe in ("ffmpeg", "ffprobe"):
        path = shutil.which(exe)
        add(bool(path), exe, (tool_version(exe) or path) if path else
            "not found. Windows: winget install Gyan.FFmpeg   Mac: brew install ffmpeg")
    # the skills run their preflight .sh files with bash; on Windows that is Git Bash (Git for Windows)
    bash = shutil.which("bash")
    works = False
    if bash:
        try:
            works = subprocess.run([bash, "-c", "echo ok"], capture_output=True, text=True, timeout=20).stdout.strip() == "ok"
        except Exception:
            works = False
    add(works, "bash (Git Bash)", bash if works else
        "not found or not working. Windows: winget install Git.Git, then reopen the terminal" if WIN else
        "not found. Install bash", required=WIN)
    miss = []
    for mod, pipname in PACKAGES:
        ok = importlib.util.find_spec(mod) is not None
        add(ok, f"package {pipname}", "installed" if ok else "not installed")
        if not ok:
            miss.append(pipname)
    if miss:
        rows.append(("fix", "pip", f"{Path(sys.executable).name} -m pip install {' '.join(miss)}"))

    env = read_env()
    has_env = (ROOT / ".env").is_file()
    stt = [k for k in STT_KEYS if env.get(k)]
    add(has_env, ".env file", str(ROOT / ".env") if has_env else
        "missing. Copy _system/env-template.txt to .env in the pack folder", required=not stt)
    add(bool(stt), "speech-to-text key", ("set: " + ", ".join(stt)) if stt else
        "none set. Fill in ONE of " + ", ".join(STT_KEYS) + " in .env")
    for k, what in (("GEMINI_API_KEY", "AI images, AI B-roll video, music"), ("OPENAI_API_KEY", "AI images"),
                    ("XAI_API_KEY", "AI images")):
        add(bool(env.get(k)), k + " (optional)", ("set: " if env.get(k) else "empty, unlocks ") + what, required=False)
    for k in OPT_KEYS:
        add(bool(env.get(k)), k + " (optional)", "set" if env.get(k) else "empty, free stock B-roll", required=False)

    bj = {}
    try:
        bj = json.loads((ROOT / "assets" / "brand" / "brand.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    add(bool(str(bj.get("name", "")).strip()), "brand files", "filled in" if str(bj.get("name", "")).strip() else
        "empty. Claude asks about your brand during install (assets/brand/BRAND.md and brand.json)", required=False)

    cap = [BASE / "CapCut"] if WIN else [Path.home() / "Movies" / "CapCut", Path("/Applications/CapCut.app")]
    found = [str(c) for c in cap if c.exists()]
    add(bool(found), "CapCut Desktop", found[0] if found else "not found (only needed to edit inside CapCut)",
        required=False)
    add((ROOT / "projects" / "raw-videos").is_dir(), "projects/raw-videos folder", "", required=False)

    bad = [r for r in rows if r[0] == "MISSING"]
    if "--json" in sys.argv:
        print(json.dumps({"ready": not bad, "checks": [dict(status=s, name=n, detail=d) for s, n, d in rows]},
                         ensure_ascii=False, indent=1))
    else:
        for s, n, d in rows:
            print(f"[{s:^7}] {n}" + (f": {d}" if d else ""))
        print("\nReady." if not bad else f"\n{len(bad)} required item(s) missing. Fix them, then run this check again.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
