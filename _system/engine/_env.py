"""Shared setup for the engine's Python tools (imported first by vo.py, mix.py, qa.py, energy.py, media.py, doctor.py, new_project.py).

- puts the portable runtime (ffmpeg / node / python the installer may have downloaded) on PATH
- needs(...): if this Python is older than 3.10 or lacks a package, re-runs the script with the Python the installer
  prepared (recorded in _system/setup/local.json, usually ~/.indigo11/venv); otherwise prints the fix in Thai
- key(name): reads a key from the environment, then from <starter>/.env (never prints it)
- UTF-8 console on Windows; HTTPS certificates via certifi when a standalone Python has none
"""
import json, os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # the starter folder (this file is _system/engine/_env.py)
SYS = ROOT / "_system"
WIN = os.name == "nt"
_BASE = Path(os.environ.get("LOCALAPPDATA", Path.home())) if WIN else Path.home()
_D = "indigo11" if WIN else ".indigo11"
RT = _BASE / _D / "runtime"

if WIN:
    for _s in (sys.stdout, sys.stderr):
        try: _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass

_extra = [RT / "bin", RT / "node"] + ([RT / "python", RT / "python" / "Scripts"] if WIN else [])
os.environ["PATH"] = os.pathsep.join([str(p) for p in _extra if p.is_dir()] + [os.environ.get("PATH", "")])


def local():
    try: return json.loads((SYS / "setup" / "local.json").read_text(encoding="utf-8"))
    except Exception: return {}


def _venv_python():
    v = Path.home() / ".indigo11" / "venv"
    return v / ("Scripts/python.exe" if WIN else "bin/python3")


def needs(*mods, soft=False):
    """Make sure this interpreter is >= 3.10 and can import mods; else hand over to the prepared one.
    soft=True: when no better Python exists, return the list of problems instead of exiting."""
    missing = []
    for m in mods:
        try: __import__(m)
        except Exception: missing.append(m)
    if sys.version_info >= (3, 10) and not missing:
        _certs(); return []
    if os.environ.get("INDIGO11_REEXEC") != "1":
        for cand in [local().get("python"), str(_venv_python())]:
            if cand and Path(cand).exists() and os.path.abspath(cand) != os.path.abspath(sys.executable):
                env = dict(os.environ, INDIGO11_REEXEC="1")
                if WIN:
                    sys.exit(subprocess.run([cand] + sys.argv, env=env).returncode)
                os.execve(cand, [cand] + sys.argv, env)
    if soft:
        _certs(); return (["python<3.10"] if sys.version_info < (3, 10) else []) + missing
    why = f"Python {sys.version.split()[0]} is below 3.10" if sys.version_info < (3, 10) else "missing package(s): " + ", ".join(missing)
    sys.exit(f"✗ {why} - install the requirements again: python -m pip install pythainlp numpy Pillow requests")


def _certs():
    if os.environ.get("SSL_CERT_FILE"): return
    try:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()
    except Exception:
        pass


def key(name, project=None):
    if os.environ.get(name, "").strip(): return os.environ[name].strip()
    for f in ([Path(project) / ".env"] if project else []) + [ROOT / ".env"]:
        try:
            for ln in f.read_text(encoding="utf-8").splitlines():
                ln = ln.strip()
                if ln.startswith(name + "="):
                    v = ln.split("=", 1)[1].strip().strip('"').strip("'")
                    if v: return v
        except Exception:
            pass
    return ""


class Project:
    """Where a project's files live. Two layouts:
      convention  projects/<YYMMDD>-<name up to 32 chars>/ (project.json, BRIEF.md, script/, assets/, src/, result/, NOTES.md)
                  -> the engine page is src/; media + SOURCES.md in assets/; finished video result/<slug>.mp4
      flat        any folder with index.html (templates/<name>/, _system/setup/test-clip) -> everything inside it;
                  a template's finished video is templates/<name>/example.mp4, anything else <dir>/out/<slug>.mp4
    Accepts the project folder or its src/ folder."""

    def __init__(self, path):
        p = Path(path).resolve()
        if p.name == "src" and (p.parent / "project.json").exists(): p = p.parent
        self.meta = {}
        if (p / "project.json").exists() and (p / "src" / "index.html").exists():
            self.root, self.src = p, p / "src"
            try: self.meta = json.loads((p / "project.json").read_text(encoding="utf-8"))
            except Exception: pass
            self.assets = p / "assets"
            self.final_dir = p / "out" if (p / "out").is_dir() and not (p / "result").is_dir() else p / "result"   # out/ = older projects
            self.script = p / "script" / "vo.json"
        else:
            self.root = self.src = p
            self.assets, self.final_dir = p / "media", p / "out"
            self.script = p / "vo" / "script.json"
        self.slug = self.meta.get("slug") or self.root.name
        self.template = self.root == self.src and (ROOT / "templates") in self.root.parents
        if self.template: self.final_dir, self.final = self.root, self.root / "example.mp4"
        else: self.final = self.final_dir / f"{self.slug}.mp4"
        self.sources = self.assets / "SOURCES.md" if self.root != self.src else self.root / "SOURCES.md"
