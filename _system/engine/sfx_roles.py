"""indigo11 Video · _system/engine/sfx_roles.py: which sound file plays for a sound-effect role.

Used by .claude/skills/indigo11-edit/scripts/vo_assemble.py and .claude/skills/indigo11-video/scripts/_starter.py.
A role (pop, whoosh, paper_flip, impact_big, ...) resolves in this order, first tier with a file wins:
  1. Optional role map botanica-v4.roles.json (in a sfx-roles/ folder next to this file), when that map exists and assets/sound-effects/Botanica V4/ exists
  2. Optional role map all-packs.roles.json (same folder) for whichever pack folders are in assets/sound-effects/; a file is found by
     its path, else by its file name anywhere inside that pack folder. Entries listed as copyrighted in that map are never picked.
  3. the user's own flat file assets/sound-effects/<role>.wav (+ variants <role>-2.wav ...; any audio type; old <role>/ folders too)
  4. optional CC0 sounds in assets/sound-effects/starter-cc0/, when that folder exists
If nothing matches, the caller makes a synthetic sound. Tiers 1-2 also try the role's aliases (below); tiers 3-4 also try the
caller's fallback role. The two role maps are NOT shipped in the hand-over pack (a missing map counts as empty), and packs are never
shipped: the user adds their own files to assets/sound-effects/.

    python3 _system/engine/sfx_roles.py            which packs are installed + what every engine role resolves to
"""
import fnmatch, json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SFX = ROOT / "assets" / "sound-effects"
CC0 = SFX / "starter-cc0"
MAPS = HERE / "sfx-roles"
AUD = (".wav", ".mp3", ".flac", ".ogg", ".aif", ".aiff", ".m4a")
BOTANICA = "Botanica V4"
PACKS = [BOTANICA, "Sfx", "Free SFX pack by Z7", "The Complete Sound FX Pack", "peen_s SFX", "SFX 2", "SFX Sound Effects", "sfx-simple"]
OPT_KEYS = ("gain_db", "align", "trim_start", "max_len", "fade_out")
# engine / channel role -> the owner's role names to try in tiers 1-2 (after the role itself)
ALIAS = {
    "whoosh": ["whoosh_medium", "whoosh_1s"], "whoosh_small": ["whoosh_fast", "whoosh_short"], "swish": ["swipe_ui", "whoosh_fast", "whoosh_short"],
    "impact": ["impact_big", "thud_heavy"], "stamp": ["cartoon_thump", "land_drop"], "land": ["land_drop", "cartoon_thump"],
    "type": ["typing"], "count": ["data_count"], "notify": ["notify", "phone_ping"], "notification": ["notify", "phone_ping"],
    "coin": ["success"], "riser_short": ["riser"], "sparkle": ["sparkle"], "list_up": ["list_up"], "whoosh_fast": ["whoosh_short"],
    "whoosh_medium": ["whoosh_1s"], "cartoon_thump": ["thud_heavy"], "impact_big": ["thud_heavy"], "notify_ping": ["phone_ping"],
}


def _load(name):
    try: return json.loads((MAPS / name).read_text(encoding="utf-8"))
    except Exception: return {}


_BOT, _ALL = _load("botanica-v4.roles.json"), _load("all-packs.roles.json")
_BLOCK = list(_ALL.get("copyrighted_do_not_use_for_clients", []))
_names = {}


def pack_dir(name):
    """the folder of a pack inside assets/sound-effects/ (also the doubled 'X/X/' an unzip makes), else None"""
    d = SFX / name
    if not d.is_dir():
        low = {p.name.lower(): p for p in SFX.iterdir() if p.is_dir()} if SFX.is_dir() else {}
        d = low.get(name.lower())
        if d is None: return None
    return d / d.name if (d / d.name).is_dir() and not any(f.suffix.lower() in AUD for f in d.iterdir() if f.is_file()) else d


def _by_name(d):
    if d not in _names: _names[d] = {f.name.lower(): f for f in sorted(d.rglob("*")) if f.is_file() and f.suffix.lower() in AUD}
    return _names[d]


def _find(base, rel):
    if base is None: return None
    p = base / rel
    return p if p.is_file() else _by_name(base).get(Path(rel).name.lower())


def _blocked(rel): return any(fnmatch.fnmatch(rel, pat) for pat in _BLOCK)


def _opts(entry): return {k: entry[k] for k in OPT_KEYS if k in entry}


def _from_map(roles, names, locate):
    for n in names:
        e = roles.get(n)
        if not isinstance(e, dict): continue
        out = []
        for rel in [e.get("main")] + list(e.get("alt") or []):
            if not rel or _blocked(rel): continue
            f = locate(rel)
            if f: out.append((f, _opts(e) if rel == e.get("main") else {k: v for k, v in _opts(e).items() if k not in ("trim_start",)}, n))
        if out: return out
    return []


def _flat(d, role):
    if d is None or not d.is_dir(): return []
    flat = sorted((f for f in d.iterdir() if f.is_file() and f.suffix.lower() in AUD and (f.stem == role or f.stem.startswith(role + "-"))),
                  key=lambda f: (f.stem != role, f.name))
    old = sorted(f for f in (d / role).iterdir() if f.is_file() and f.suffix.lower() in AUD) if (d / role).is_dir() and role not in PACKS else []
    return flat + old


def resolve(role, fallback=None):
    """[(file, opts, source)] for a role: opts = gain_db / align / trim_start / max_len / fade_out from the owner's map (tiers 1-2 only);
    source = 'botanica:<role>' | 'pack:<role>' | 'own' | 'starter-cc0'. [] when nothing exists (caller synthesises)."""
    names = [role] + [a for a in ALIAS.get(role, []) if a != role]
    bot = pack_dir(BOTANICA)
    if bot is not None:
        hit = _from_map(_BOT.get("roles", {}), names, lambda rel: _find(bot, rel))
        if hit: return [(f, o, "botanica:" + n) for f, o, n in hit]
    def pack_locate(rel):
        parts = Path(rel).parts
        return _find(pack_dir(parts[0]), str(Path(*parts[1:]))) if len(parts) > 1 else None
    hit = _from_map(_ALL.get("roles", {}), names, pack_locate)
    if hit: return [(f, o, "pack:" + n) for f, o, n in hit]
    for r in [role] + ([fallback] if fallback and fallback != role else []):
        own = _flat(SFX, r)
        if own: return [(f, {}, "own") for f in own]
    for r in [role] + ([fallback] if fallback and fallback != role else []):
        cc = _flat(CC0, r)
        if cc: return [(f, {}, "starter-cc0") for f in cc]
    return []


def packs():
    """[(pack name, folder or None)] for the known packs, then any other folder the user added"""
    out = [(n, pack_dir(n)) for n in PACKS]
    known = {n.lower() for n in PACKS} | {"starter-cc0"}
    if SFX.is_dir():
        out += [(p.name, p) for p in sorted(SFX.iterdir()) if p.is_dir() and p.name.lower() not in known and not p.name.startswith((".", "_"))]
    return out


if __name__ == "__main__":
    import sys
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Windows console (cp1252) cannot print the check marks
    except Exception: pass
    for n, d in packs(): print(("✓ " if d else "○ ") + n + (f"  ({d.relative_to(ROOT).as_posix()})" if d else "  (not downloaded, optional)"))
    for r in ("pop", "click", "tick", "swish", "whoosh", "whoosh_small", "impact", "riser", "sparkle", "success", "notify", "coin", "paper_flip", "cartoon_thump"):
        h = resolve(r)
        print(f"{r:14s} -> {h[0][2] + ': ' + h[0][0].relative_to(ROOT).as_posix() if h else '(synth)'}")
