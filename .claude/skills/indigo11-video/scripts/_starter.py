"""indigo11-video: find the Indigo11 pack folder and load its _system/engine/_env.

_env puts the portable runtime (ffmpeg, node) on PATH, reads keys from <pack>/.env (never prints them) and sets a UTF-8 console on Windows.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = next((p for p in HERE.parents if (p / "_system" / "engine" / "_env.py").is_file()), None)
if ROOT is None:
    sys.exit("Cannot find the Indigo11 pack folder (it needs _system/engine/_env.py). Run from the pack folder.")
sys.path.insert(0, str(ROOT / "_system" / "engine"))
import _env  # noqa: E402,F401

SKILL = HERE.parent
FONT = ROOT / "assets" / "fonts" / "NotoSansThai" / "NotoSansThai-VF.ttf"
PY = "python" if _env.WIN else "python3"


def sound(role):
    """the sound for a role, the owner's way (_system/engine/sfx_roles.py): Botanica V4 / other downloaded packs -> your own
    assets/sound-effects/<role>.wav -> the starter's CC0 sound in assets/sound-effects/starter-cc0/"""
    import sfx_roles
    hits = sfx_roles.resolve(role)
    return hits[0][0] if hits else None
