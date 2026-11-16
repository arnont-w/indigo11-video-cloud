"""indigo11 Video shim, imported first by every indigo11-watch script.

- finds the starter root (.claude/skills/indigo11-watch/scripts -> 3 levels up) and loads _system/engine/_env.py:
  runtime ffmpeg / node / python on PATH, UTF-8 console on Windows, HTTPS certificates
- needs(*modules): if this Python lacks a package (or is < 3.10), re-runs the script with the Python the installer
  prepared (_system/setup/local.json, usually ~/.indigo11/venv); if none has it, prints the Thai fix (run watchdeps.py)
- puts this interpreter's own bin/Scripts folder on PATH (yt-dlp installed by pip lives there)
"""
import os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]   # .claude/skills/indigo11-watch/scripts -> the starter folder
sys.path.insert(0, str(ROOT / "_system" / "engine"))
import _env  # noqa: E402

os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
PY = "python" if _env.WIN else "python3"


def needs(*mods):
    miss = _env.needs(*mods, soft=True)
    if miss:
        sys.exit(f"✗ indigo11-watch ขาด: {', '.join(miss)} - รัน: {PY} _system/setup/common/watchdeps.py  (จากโฟลเดอร์ starter)")


def key(name):
    """API key from the environment or the starter's .env (never printed)."""
    return _env.key(name)
needs()   # Python >= 3.10 (re-runs with the prepared Python when this one is older)
