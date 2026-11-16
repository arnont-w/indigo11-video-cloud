#!/usr/bin/env python3
"""Choose, create and save the user's OWN ElevenLabs voice (instead of the pack's built-in voices).

    python3 _system/engine/voice.py show                                  what is saved, and which voice each language will use
    python3 _system/engine/voice.py list                                  the voices in the user's ElevenLabs account (My Voices)
    python3 _system/engine/voice.py use <voice_id> [--lang en] [--name "My voice"]
    python3 _system/engine/voice.py clone --sample a.mp3 [b.wav ...] --name "My voice" [--lang en] [--description "..."] --i-own-this-voice
    python3 _system/engine/voice.py default [--gender male|female] [--style narration|social]   the user chose the built-in voices (remembered)

Order used when a video is voiced (first match wins):
  1. --voice on the command line
  2. the user's own voice for that language   brand.json  voices.<lang>   (for example voices.th)
  3. the user's own voice for every language  brand.json  voice
  4. the pack's built-in voice for that language (make_short.py DEFAULT_VOICES)

`use` and `clone` save the id in assets/brand/brand.json AND in the Voice-over section of assets/brand/BRAND.md,
so the two always agree. With --lang the id is saved for that language only; without it, for every language.

clone = ElevenLabs "Instant Voice Clone": needs a paid plan that includes voice cloning and a clean sample
(about 1 to 3 minutes, one speaker, no music or noise). It refuses to run without --i-own-this-voice: clone only your
own voice, or one you have written permission for. The key is read from .env (ELEVENLABS_API_KEY) and never printed.
Windows: use `python` instead of `python3`.
"""
import json, mimetypes, re, sys, uuid
import urllib.error, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env  # noqa: E402  (UTF-8 console, key reader)

ROOT = Path(__file__).resolve().parents[2]
BRAND_DIR = ROOT / "assets" / "brand"
API = "https://api.elevenlabs.io"
BEGIN = "<!-- voice:begin (written by _system/engine/voice.py: change it with that script, not by hand) -->"
END = "<!-- voice:end -->"
DEFAULTS = {"en": "built-in: Oliver Silk (British male, default) or Karina (British female, --gender female)",
            "th": "built-in: Songkran (male, narration, default), PapaAndy (male, social media, --style social) or Mali (female, --gender female)"}


def opts(av):
    args, o, i = [], {}, 0
    while i < len(av):
        a = av[i]
        if a.startswith("--"):
            k, eq, v = a[2:].partition("=")
            if eq: o[k] = v
            elif k == "sample":   # one or more files
                files = []
                while i + 1 < len(av) and not av[i + 1].startswith("--"): files.append(av[i + 1]); i += 1
                o[k] = files
            elif i + 1 < len(av) and not av[i + 1].startswith("--") and k not in ("i-own-this-voice",):
                o[k] = av[i + 1]; i += 1
            else: o[k] = True
        else: args.append(a)
        i += 1
    return args, o


def need_key():
    k = _env.key("ELEVENLABS_API_KEY")
    if not k: sys.exit("No ElevenLabs key: add it after ELEVENLABS_API_KEY= in the .env file of this folder (create one at https://elevenlabs.io/app/settings/api-keys).")
    return k


def call(method, path, key, body=None, headers=None, timeout=120):
    h = {"xi-api-key": key, **(headers or {})}
    req = urllib.request.Request(API + path, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "replace")
        try: d = json.loads(txt).get("detail"); txt = d.get("message", d) if isinstance(d, dict) else d
        except Exception: pass
        raise SystemExit(f"ElevenLabs said {e.code}: {str(txt)[:300]}")
    except urllib.error.URLError as e:
        raise SystemExit(f"Cannot reach ElevenLabs: {e.reason}")


def multipart(fields, files):
    """fields: {name: str}; files: [(field, path)] -> (body, content_type)"""
    b = uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8"))
    for k, p in files:
        p = Path(p); ct = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        out.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{p.name}"\r\nContent-Type: {ct}\r\n\r\n'.encode("utf-8") + p.read_bytes() + b"\r\n")
    out.append(f"--{b}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={b}"


# ---------- saving ----------
def load_brand():
    try: return json.loads((BRAND_DIR / "brand.json").read_text(encoding="utf-8"))
    except Exception: return {}


def save_brand(m):
    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    (BRAND_DIR / "brand.json").write_text(json.dumps(m, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def block(m):
    lines = [BEGIN]
    if m.get("voice"): lines.append(f"- Voice for every language: {m.get('voice_name') or '(unnamed)'}, voice id `{m['voice']}`")
    for lang, vid in sorted((m.get("voices") or {}).items()):
        nm = (m.get("voice_names") or {}).get(lang) or "(unnamed)"
        lines.append(f"- Voice for {lang}: {nm}, voice id `{vid}`")
    if len(lines) == 1:
        if m.get("voice_mode") == "default":
            lines.append(f"- I chose the pack's built-in voices (gender: {m.get('default_gender', 'male')}, Thai male style: {m.get('default_style', 'narration')}).")
        else:
            lines.append("- No voice chosen yet: ask the user before the first voice-over.")
    lines.append(END)
    return "\n".join(lines)


def save_brand_md(m):
    f = BRAND_DIR / "BRAND.md"
    t = f.read_text(encoding="utf-8") if f.exists() else "# My brand\n"
    new = block(m)
    if BEGIN in t and END in t:
        t = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END), lambda _: new, t, flags=re.S)
    elif "## Voice-over" in t:
        head = re.search(r"^## Voice-over[^\n]*\n", t, flags=re.M)
        t = t[:head.end()] + new + "\n" + t[head.end():]
        t = re.sub(r"^- Voice id: *\n", "", t, flags=re.M)   # the empty template line
    else:
        t = t.rstrip("\n") + "\n\n## Voice-over (ElevenLabs)\n" + new + "\n"
    f.write_text(t, encoding="utf-8", newline="")


def save_voice(vid, name, lang):
    m = load_brand()
    if lang:
        m.setdefault("voices", {})[lang] = vid
        m.setdefault("voice_names", {})[lang] = name
    else:
        m["voice"] = vid; m["voice_name"] = name
    save_brand(m); save_brand_md(m)
    print(f"Saved in assets/brand/brand.json and assets/brand/BRAND.md: {name} ({vid}) for " + (f"language '{lang}'" if lang else "every language"))


# ---------- commands ----------
def cmd_show(o):
    m = load_brand()
    print("Voice saved for this brand (first match wins when a video is voiced):")
    if not m.get("voice") and not m.get("voices") and m.get("voice_mode") != "default":
        print("  NO VOICE CHOSEN YET: ask the user (own voice / default voice / another voice from their account) before the first voice-over.")
    elif m.get("voice_mode") == "default" and not m.get("voice") and not m.get("voices"):
        print(f"  the user chose the built-in voices (gender: {m.get('default_gender', 'male')}, Thai male style: {m.get('default_style', 'narration')})")
    for lang in ("en", "de", "th"):
        own = (m.get("voices") or {}).get(lang)
        if own: src, vid = f"own voice for '{lang}'", own; nm = (m.get("voice_names") or {}).get(lang, "")
        elif m.get("voice"): src, vid, nm = "own voice (all languages)", m["voice"], m.get("voice_name", "")
        else: src, vid, nm = "built-in", "", DEFAULTS.get(lang, "built-in voice of the pack, or the pack default for en")
        print(f"  {lang}: {src}  {nm} {('(' + vid + ')') if vid else ''}".rstrip())


def cmd_default(o):
    m = load_brand()
    m["voice_mode"] = "default"
    if o.get("gender"):
        if o["gender"] not in ("male", "female"): sys.exit("--gender must be male or female")
        m["default_gender"] = o["gender"]
    if o.get("style"):
        if o["style"] not in ("narration", "social"): sys.exit("--style must be narration or social")
        m["default_style"] = o["style"]
    save_brand(m); save_brand_md(m)
    print(f"Saved: the user chose the built-in voices (gender: {m.get('default_gender', 'male')}, Thai male style: {m.get('default_style', 'narration')}). An own voice, if saved later, always wins.")


def cmd_list(o):
    key = need_key()
    try: d = call("GET", "/v2/voices?page_size=100", key)
    except SystemExit: d = call("GET", "/v1/voices", key)
    vs = d.get("voices", [])
    if not vs: print("No voices in this account yet."); return
    print(f"{len(vs)} voices in your ElevenLabs account:")
    for v in vs: print(f"  {v.get('name', '?'):<28} {v.get('voice_id')}  [{v.get('category', '')}]")


def cmd_use(args, o):
    if not args: sys.exit("usage: voice.py use <voice_id> [--lang en] [--name \"...\"]")
    vid = args[0]; key = need_key()
    v = call("GET", f"/v1/voices/{vid}", key)   # fails clearly if the id is not in this account
    save_voice(vid, str(o.get("name") or v.get("name") or "my voice"), o.get("lang"))


def cmd_clone(o):
    if not o.get("i-own-this-voice"):
        sys.exit("Stopped: clone only your OWN voice, or a voice you have written permission to clone. If that is true, run again with --i-own-this-voice.")
    files = o.get("sample") or []
    if not files or not o.get("name"): sys.exit('usage: voice.py clone --sample a.mp3 [b.wav ...] --name "My voice" [--lang en] --i-own-this-voice')
    miss = [f for f in files if not Path(f).is_file()]
    if miss: sys.exit("sample file not found: " + ", ".join(miss))
    key = need_key()
    fields = {"name": str(o["name"]), "remove_background_noise": "false" if o.get("no-denoise") else "true"}
    if o.get("description"): fields["description"] = str(o["description"])
    body, ct = multipart(fields, [("files", f) for f in files])
    d = call("POST", "/v1/voices/add", key, body, {"Content-Type": ct}, timeout=300)
    vid = d.get("voice_id")
    if not vid: sys.exit("ElevenLabs gave no voice_id: " + json.dumps(d)[:200])
    save_voice(vid, str(o["name"]), o.get("lang"))
    print("Tip: say a test sentence with it before a real video, for example: python3 .claude/skills/indigo11-transcribe/scripts/tts.py --text \"Hello\" --out test.mp3 --voice " + vid)


def main(av):
    if not av or av[0] in ("-h", "--help"): sys.exit(__doc__)
    args, o = opts(av[1:])
    c = av[0]
    if c == "show": cmd_show(o)
    elif c == "list": cmd_list(o)
    elif c == "default": cmd_default(o)
    elif c == "use": cmd_use(args, o)
    elif c == "clone": cmd_clone(o)
    else: sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
