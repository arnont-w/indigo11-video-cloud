"""indigo11 Video shim, imported first by every indigo11-transcribe script (same idea as .claude/skills/indigo11-watch/scripts/_starter.py).

- finds the starter folder (the parent with _system/engine/_env.py) and loads _system/engine/_env.py: the portable ffmpeg / node / python
  the installer may have put in ~/.indigo11/runtime go on PATH, UTF-8 console on Windows, HTTPS certificates
- if this Python is older than 3.10 or lacks pythainlp, re-runs the script with the Python the installer prepared
  (_system/setup/local.json, usually ~/.indigo11/venv); if none fits, prints the Thai fix and stops
- puts this interpreter's own bin/Scripts folder on PATH
Run from anywhere: python3 .claude/skills/indigo11-transcribe/scripts/<script>.py (Windows: python).
"""
import os, sys
from pathlib import Path

ROOT = next((p for p in Path(__file__).resolve().parents if (p / "_system" / "engine" / "_env.py").exists()), None)
if ROOT is not None:
    sys.path.append(str(ROOT / "_system" / "engine"))   # appended: never shadows this folder's own modules
    import _env  # noqa: E402
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    _env.needs('pythainlp')
