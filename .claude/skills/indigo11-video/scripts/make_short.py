#!/usr/bin/env python3
"""indigo11-video: make a short video from a script in one go (run from the pack folder; Windows: python).

  python3 .claude/skills/indigo11-video/scripts/make_short.py --script P/script/script.txt --broll P/assets/broll/03-selected --out P [--lang en|th]

  Automated steps:
    TTS (ElevenLabs eleven_v3 + a timestamp for every character) -> captions cut at the phrase boundaries of the script
    -> B-roll cut to length -> audio (ding + voice + soft music) -> captions burned in with the pack's Chrome (render_caps.mjs)
    -> P/result/<slug>.mp4

  --lang en (default) = Standard British English captions and voice.  --lang th = Thai (needs pythainlp and a Thai font).
  P = the work folder projects/<YYMMDD>-<name up to 32 chars>/ (made by new_short.py). Working files go in P/src/ (.work/ can be deleted).

Common options:
  --bgm auto|<name>|<path>|none   music from assets/music/ (or .claude/skills/indigo11-video/music/) (only add music you have the right to use)
  --ding <path>|none               opening sound (default: role notify from assets/sound-effects/, else sfx/intro-ding.mp3)
  --voice VOICE_ID                 ElevenLabs voice (default: the voice in assets/brand/brand.json, else the built-in one for the language)
  --skip-tts                       reuse src/.work/voice.json, no API call (no credit spent)
  --no-voice                       no ElevenLabs key: silent track, estimated timing, for checking captions/picture only
  --max-sec 59                     length ceiling (warns when exceeded)
Key: ELEVENLABS_API_KEY in the .env file (never printed).
"""
import argparse, base64, datetime, json, os, random, re, shutil, subprocess, sys, urllib.error, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _starter import ROOT, SKILL, FONT, PY, _env, sound  # noqa: E402


HERE = Path(__file__).resolve().parent
MUSIC = SKILL / "music"
MUSIC_DIRS = [ROOT / "assets" / "music", MUSIC]   # the user's own music first, then this skill's folder
CPS_BY_LANG = {"en": 15.0, "th": 14.25}   # speaking rate of v3 in characters per second (Thai measured on real clips; English estimated, about 2.5 words per second)
CPS = CPS_BY_LANG["en"]
BGM_LUFS = -32         # very quiet music
# Public ElevenLabs voice-library voices (add them to My Voices first). Accent follows the voice.
# Built-in voices, used ONLY when the user has no voice of their own (brand.json voices.<lang> / voice; see _system/engine/voice.py).
# Pick with --gender male|female (default male) and, for the Thai male voice, --style narration|social (default narration).
DEFAULT_VOICES = {
    "en": {"male": "jfIS2w2yJi0grJZPyEsk",      # Oliver Silk, British male
           "female": "BcIZ7kngidiArTkRyUAp"},   # Karina, British female
    "th": {"male": {"narration": "xYmBpJREC3WfqEFpbPvO",   # Songkran, Thai male, narration
                    "social": "KhBRXgEQjvfdIyXOu7G3"},     # PapaAndy, Thai male, social media / selling (paid plans only)
           "female": "7XW5JhaNn90bnFojQ5DA"},              # Mali, Thai female
}


def default_voice(lang, gender="male", style="narration"):
    v = DEFAULT_VOICES[lang][gender]
    return v[style] if isinstance(v, dict) else v


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=900, encoding="utf-8", errors="replace", **kw)


def ff(args):
    return sh(["ffmpeg", "-v", "error", *args, "-y"])


def dur(p):
    r = sh(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)])
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


# ---------- 1. TTS ----------
def tts(text, out_json, voice, seed, lang="en"):
    key = _env.key("ELEVENLABS_API_KEY")
    if not key:
        sys.exit("ELEVENLABS_API_KEY not found in the .env file\n"
                 "  Add the key: open .env and add ELEVENLABS_API_KEY=...  (https://elevenlabs.io/app/settings/api-keys)\n"
                 "  or test the captions/picture first with --no-voice (no speech, never post it)")
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps",
        data=json.dumps({
            "text": text, "model_id": "eleven_v3", "language_code": lang,
            "seed": seed,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }).encode(),
        headers={"xi-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            d = json.load(r)
            cost = r.headers.get("character-cost")
    except urllib.error.HTTPError as e:
        why = {401: "wrong key", 402: "out of credit or a paid plan is needed", 429: "too many requests: wait and retry"}.get(e.code, "")
        sys.exit(f"ElevenLabs HTTP {e.code} {why}".strip())
    out_json.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return d, cost


def fake_voice(text, out_json, work, lang="en"):
    """--no-voice: estimated timing from the script (CPS characters per second, a space = a 0.25 s pause) + silent audio. For checking captions/picture only."""
    chars, st, en, t = [], [], [], 0.25
    intag = False
    for ch in text:
        if ch == "[":
            intag = True
        if intag:                      # audio tags are not spoken
            d = 0.0
        elif ch.isspace():
            d = 0.25 if lang == "th" else 1 / CPS     # Thai: a space is a pause; English: spaces are ordinary
        else:
            d = 1 / CPS
        chars.append(ch); st.append(round(t, 3)); t += d; en.append(round(t, 3))
        if ch == "]":
            intag = False
    total = t + 0.3
    silent = work / "silent.mp3"
    ff(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", f"{total:.2f}", "-c:a", "libmp3lame", "-b:a", "64k", str(silent)])
    d = {"audio_base64": base64.b64encode(silent.read_bytes()).decode(),
         "alignment": {"characters": chars, "character_start_times_seconds": st, "character_end_times_seconds": en},
         "fake": True}
    out_json.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return d


def pick_bgm(spec, script_text=""):
    """Pick music: a file name in assets/music/ / a path / auto (guessed from the script) / none"""
    if not spec or spec == "none":
        return None
    p = Path(spec)
    if p.exists():
        return p
    lib = sorted((f for d in MUSIC_DIRS if d.is_dir() for f in d.glob("*") if f.suffix.lower() in (".mp3", ".wav", ".m4a")),
                 key=lambda f: (f.parent != MUSIC_DIRS[0], f.name))
    if not lib:
        print("  no music in assets/music/ -> no music (add music you may use; a calm-/tense-/upbeat- name prefix helps --bgm auto choose)")
        return None
    byname = {f.stem: f for f in lib}
    if spec in byname:
        return byname[spec]
    if spec != "auto":
        print(f"  unknown track '{spec}' -> picking at random")
    t = script_text
    tl = t.lower()
    if any(k in tl for k in ("danger", "deadly", "risk", "warning", "poison", "secret", "mystery",
                              "อันตราย", "ตาย", "เสี่ยง", "ระวัง", "พิษ", "ลับ")):
        mood = "tense"
    elif any(k in tl for k in ("fun", "viral", "best in the world", "ranking", "most ", "top ",
                                "สนุก", "ฮิต", "ไวรัล", "ที่สุดในโลก", "อันดับ")):
        mood = "upbeat"
    else:
        mood = "calm"
    own = [f for f in lib if f.parent == MUSIC_DIRS[0]]
    lib = own or lib   # the user's own music wins over the skill folder
    pool = [f for f in lib if f.stem.startswith(mood)] or lib
    return random.choice(pool)


def build_audio(work, vo_mp3, ding, bgm, total):
    """ding + voice start together at 0 s; the music is normalised to be quiet"""
    voice = work / "voice.m4a"
    if ding and Path(ding).exists():
        ff(["-i", str(ding), "-i", str(vo_mp3), "-filter_complex",
            "[0:a]volume=0.6[d];[1:a]volume=1.0[v];"
            "[d][v]amix=inputs=2:duration=longest:normalize=0[a]",
            "-map", "[a]", "-c:a", "aac", "-b:a", "192k", str(voice)])
    else:
        ff(["-i", str(vo_mp3), "-c:a", "aac", "-b:a", "192k", str(voice)])

    if not (bgm and Path(bgm).exists()):
        return voice

    music = work / "bgm.m4a"
    ff(["-stream_loop", "-1", "-i", str(bgm), "-t", str(total), "-af",
        f"loudnorm=I={BGM_LUFS}:TP=-2:LRA=11,afade=t=in:st=0:d=1,"
        f"afade=t=out:st={max(total - 1.5, 0):.2f}:d=1.5",
        "-c:a", "aac", "-b:a", "128k", str(music)])
    out = work / "final_audio.m4a"
    ff(["-i", str(voice), "-i", str(music), "-filter_complex",
        "[0:a][1:a]amix=inputs=2:duration=first:normalize=0[a]",
        "-map", "[a]", "-c:a", "aac", "-b:a", "192k", str(out)])
    return out


# ---------- 2b. English captions: ASS subtitles burned in by ffmpeg (libass) ----------
def _ass_time(s):
    cs = int(round(s * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


ASS_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Arial,80,&H00FFFFFF,&H00FFFFFF,&H00000000,&H99000000,-1,0,0,0,100,100,0,0,1,7,4,5,70,70,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
# pop-in: fade 60 ms in / 70 ms out, scale from 55%x70% up to 100% in 170 ms, centred at y 1330 (the lower third)
ASS_FX = r"{\pos(540,1330)\fad(60,70)\fscx55\fscy70\t(0,170,\fscx100\fscy100)}"


def write_ass(cues, path):
    """Pop-in captions in the lower third (about y 1180-1480 of 1920): white, bold, black outline, soft shadow."""
    lines = []
    for c in cues:
        txt = c["txt"].replace("{", "(").replace("}", ")").replace("\n", " ")
        lines.append(f"Dialogue: 0,{_ass_time(c['t'])},{_ass_time(c['e'])},Cap,,0,0,0,,{ASS_FX}{txt}")
    Path(path).write_text(ASS_HEAD + "\n".join(lines) + "\n", encoding="utf-8")


def burn_ass_captions(cues, work_dir, broll, audio, out, total):
    """Burn the captions onto the B-roll and mux the audio in one ffmpeg pass. Returns True on success."""
    has = sh(["ffmpeg", "-hide_banner", "-filters"]).stdout
    if " subtitles " not in has:
        print("[!] this ffmpeg has no libass (the 'subtitles' filter). Install a full ffmpeg build "
              "(Windows: winget install Gyan.FFmpeg; macOS: brew install ffmpeg-full) and retry.")
        return False
    write_ass(cues, Path(work_dir) / "captions.ass")
    r = sh(["ffmpeg", "-v", "error", "-y", "-i", str(broll), "-i", str(audio), "-vf", "subtitles=captions.ass",
            "-map", "0:v", "-map", "1:a", "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)], cwd=str(work_dir))
    if r.returncode != 0:
        print(r.stderr[-600:])
    return r.returncode == 0


# ---------- 3. B-roll ----------
MIN_CLIPS = 5          # at least this many different clips per video (hard rule, do not disable)


def build_broll(work, clip_dir, total, min_clips=MIN_CLIPS):
    """Cut the clips to the length of the audio, centre-cropped to 9:16.
    Rules: never the same clip twice in a row, and every clip is used before any repeats."""
    clips = sorted([p for p in Path(clip_dir).iterdir()
                    if p.suffix.lower() in (".mp4", ".webm", ".mov", ".mkv")])
    if not clips:
        sys.exit(f"no clips found in {clip_dir}")
    if len(clips) < min_clips:
        sys.exit(f"only {len(clips)} clips: at least {min_clips} different clips are needed\n"
                 f"   find more: {PY} .claude/skills/indigo11-video/scripts/scan_broll.py --q '<topic>' --out <P>/assets/broll")

    seg_dir = work / "seg"
    seg_dir.mkdir(exist_ok=True)
    for f in seg_dir.glob("*.mp4"):
        f.unlink()

    SEG = 3.6                      # 3.6 s per segment: frequent cuts keep it lively
    n = int(total / SEG) + 1
    durs = {c: dur(c) for c in clips}
    used = {c: 0 for c in clips}

    order, last = [], None
    for _ in range(n):
        pool = [c for c in clips if c != last] or clips
        pick = min(pool, key=lambda c: (used[c], clips.index(c)))
        order.append(pick)
        used[pick] += 1
        last = pick

    made = []
    for i, src in enumerate(order):
        d = durs[src]
        round_i = sum(1 for j in range(i) if order[j] == src)
        ss = min(2 + round_i * (SEG + 4), max(d - SEG - 0.5, 0))
        out = seg_dir / f"s{i:02d}.mp4"
        r = ff(["-ss", f"{ss:.2f}", "-t", f"{SEG}", "-i", str(src), "-vf",
                "scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,setsar=1,fps=30",
                "-an", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "20", "-pix_fmt", "yuv420p", str(out)])
        if r.returncode == 0 and out.exists():
            made.append(out)

    print(f"  {len(set(order))} different clips / {len(made)} segments ({SEG}s each)")
    lst = seg_dir / "list.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in made), encoding="utf-8")
    joined = work / "broll.mp4"
    ff(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(joined)])
    trimmed = work / "broll_t.mp4"
    ff(["-i", str(joined), "-t", f"{total:.2f}", "-c", "copy", str(trimmed)])
    trimmed.replace(joined)
    return joined


def source_flag(clip_dir):
    """Read the _source.json written by scan_broll.py -> (source, publishable)"""
    for d in (Path(clip_dir), Path(clip_dir).parent):
        f = d / "_source.json"
        if f.exists():
            try:
                j = json.loads(f.read_text(encoding="utf-8"))
                return j.get("source", "?"), bool(j.get("publishable"))
            except Exception:
                pass
    return "unknown (clips added by hand)", None


# ---------- check the story structure before spending credit ----------
PIVOT_LO, PIVOT_HI = 25, 75     # measured on 48 real clips: 25-75% covers 47/48
STORY = {
    "en": {
        "pivot": ["but", "until", "actually", "in fact", "the truth is", "turns out", "except", "instead", "yet"],
        "open": "did you know",
        "num": r"(one|two|three|four|five|six|seven|eight|nine|ten|hundred|thousand|million|billion|half|twice|percent)",
    },
    "th": {
        "pivot": ["แต่", "จนกระทั่ง", "ความจริงคือ", "ที่จริง", "กลับกลายเป็น"],
        "open": "รู้ไหมว่า",
        "num": r"(หนึ่ง|สอง|สาม|สี่|ห้า|หก|เจ็ด|แปด|เก้า|สิบ|ร้อย|พัน|หมื่น|แสน|ล้าน|ครึ่ง|เท่า|เปอร์เซ็นต์)",
    },
}


def check_story(plain, lang="en"):
    """Warnings only, never blocks (criteria in references/story-framework.md)."""
    cfg = STORY[lang]
    low = plain.lower()
    warn = []
    if low.lstrip().startswith(cfg["open"]):
        warn.append(f'The script opens with "{cfg["open"]}": the reference channel uses it in only 12% of clips. '
                    "See the template table in SKILL.md and pick an opening that fits the content.")
    pos = []
    for w in cfg["pivot"]:
        pat = r"\b" + re.escape(w) + r"\b" if lang == "en" else re.escape(w)
        pos += [m.start() for m in re.finditer(pat, low) if m.start() > 0]
    if not pos:
        warn.append("No pivot word found (" + "/".join(cfg["pivot"][:3]) + ") - the script may have no PIVOT")
    else:
        pct = min(pos, key=lambda x: abs(x / len(plain) * 100 - 50)) / len(plain) * 100
        if not PIVOT_LO <= pct <= PIVOT_HI:
            warn.append(f"The pivot sits at {pct:.0f}% of the script - it should be at {PIVOT_LO}-{PIVOT_HI}%")
    if not re.search(r"[0-9\u0e50-\u0e59]", plain) and not re.search(cfg["num"], low):
        warn.append("No numbers in the script: unless a mechanism explains the answer, hard evidence needs a number")
    if lang == "th" and re.search(r"[\u0e00-\u0e7f] [A-Za-z]|[A-Za-z] [\u0e00-\u0e7f]", re.sub(r"\[[^\]]+\]", "", plain)):
        warn.append("A space between Thai and English: within one phrase keep them joined (a space = a pause + a caption break)")
    return warn


def project_info(proj):
    pj = proj / "project.json"
    try:
        return json.loads(pj.read_text(encoding="utf-8")) if pj.exists() else {}
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser(description="indigo11-video: script + B-roll -> short video 9:16 (English by default)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--script", help="script file (.txt)")
    g.add_argument("--text", help="inline script text")
    ap.add_argument("--broll", required=True, help="folder of B-roll clips you have looked at (03-selected)")
    ap.add_argument("--out", required=True, help="work folder P (projects/<date>-<slug>)")
    ap.add_argument("--ding", default=str(Path(__file__).resolve().parents[1] / "sfx" / "intro-ding.mp3") if (Path(__file__).resolve().parents[1] / "sfx" / "intro-ding.mp3").exists() else "notify", help="opening sound: a role name (assets/sound-effects/), a path, or none")
    ap.add_argument("--bgm", default="auto", help="music: auto / a file name in assets/music/ / a path / none")
    ap.add_argument("--voice", default=None, help="ElevenLabs voice id (default: the voice in assets/brand/brand.json, else the built-in default). The accent follows the voice, so choose a British voice for British English")
    ap.add_argument("--seed", type=int, default=1614)
    ap.add_argument("--max-sec", type=float, default=59)
    ap.add_argument("--skip-tts", action="store_true")
    ap.add_argument("--no-voice", action="store_true", help="no key: silent audio + estimated timing (check captions/picture only)")
    ap.add_argument("--gender", default=None, choices=["male", "female"], help="built-in voice gender (default: the saved choice, else male). Ignored when the user has their own voice")
    ap.add_argument("--style", default=None, choices=["narration", "social"], help="Thai male built-in voice: narration (default) or social (social media / selling). Ignored when the user has their own voice")
    ap.add_argument("--use-default-voice", action="store_true", help="the user chose the built-in voice for this run (to remember the choice: voice.py default)")
    ap.add_argument("--lang", default=None, choices=["en", "th"], help="language of the script, voice and captions (default: en, or the lang in project.json / brand.json)")
    a = ap.parse_args()

    text = (Path(a.script).read_text(encoding="utf-8") if a.script else a.text).strip()
    plain = re.sub(r"\[[^\]]+\]", "", text)
    proj = Path(a.out).resolve()
    meta = project_info(proj)
    try: brand_lang = json.loads((ROOT / "assets" / "brand" / "brand.json").read_text(encoding="utf-8")).get("lang")
    except Exception: brand_lang = None
    lang = a.lang or meta.get("lang") or brand_lang or "en"
    if lang not in ("en", "th"):
        sys.exit("--lang must be en or th")
    global CPS
    CPS = CPS_BY_LANG[lang]
    if lang == "th":
        _env.needs("pythainlp")
        import thai_captions as tc  # noqa: E402
    else:
        import english_captions as tc  # noqa: E402
    est = len(plain) / CPS
    words = len(plain.split())
    print(f"Language: {'Standard British English' if lang == 'en' else 'Thai'}")
    print(f"Script: {len(plain)} characters" + (f", {words} words" if lang == "en" else "") + f" -> about {est:.1f}s", end="")
    if est > a.max_sec:
        print(f"  [!] over {a.max_sec}s: cut about {int((est - a.max_sec) * CPS)} characters")
    else:
        print("  OK")
    for w in check_story(plain, lang):
        print(f"  [!] {w}")

    if not a.voice:   # order: --voice > the user's own voice for this language > the user's own voice for all languages > built-in
        _b = {}
        try: _b = json.loads((ROOT / "assets" / "brand" / "brand.json").read_text(encoding="utf-8"))
        except Exception: pass
        _own = (_b.get("voices") or {}).get(lang) or _b.get("voice") or ""
        if _own:
            a.voice = _own
            print(f"  [i] voice: your own voice ({(_b.get('voice_names') or {}).get(lang) or _b.get('voice_name') or _own})")
            if a.gender or a.style:
                print("  [i] your own voice is used first: --gender / --style apply only to the built-in voices")
        else:
            _g = a.gender or _b.get("default_gender") or "male"
            _s = a.style or _b.get("default_style") or "narration"
            _chosen = a.use_default_voice or _b.get("voice_mode") == "default" or a.gender or a.style
            if not _chosen and not a.no_voice and not a.skip_tts:
                _names = {"en": "Oliver Silk (male) or Karina (female)", "th": "Songkran (male, narration), PapaAndy (male, social media, paid plan) or Mali (female)"}.get(lang, "the built-in voice")
                sys.exit("STOP: no voice has been chosen for this brand yet, so nothing was spent. Ask the user ONE question (AskUserQuestion), then run this command again:\n"
                         "  1) My own voice, I know the voice id  -> python _system/engine/voice.py use <id> --name \"<name>\" [--lang th]\n"
                         "  2) My own voice, clone it from a recording -> python _system/engine/voice.py clone --sample <file> --name \"<name>\" --i-own-this-voice\n"
                         f"  3) The default voice for {lang}: {_names} -> python _system/engine/voice.py default [--gender female] [--style social]   (remembered for next time)\n"
                         "  4) Another voice from my ElevenLabs account -> python _system/engine/voice.py list, let the user pick, then voice.py use <id>\n"
                         "If the user already told you a voice id or name in this chat, save it with voice.py use NOW instead of asking.")
            a.voice = default_voice(lang, _g, _s)
            print(f"  [i] voice: built-in default ({lang}, {_g}" + (f", {_s}" if lang == "th" and _g == "male" else "") + ")")
    slug = meta.get("slug") or proj.name
    src = proj / "src"            # working files
    assets = src / "assets"       # cues.json + mixed audio + joined video
    work = src / ".work"          # intermediate files, safe to delete
    outd = proj / "out" if (proj / "out").is_dir() and not (proj / "result").is_dir() else proj / "result"   # out/ = older projects
    for d in (assets, work, outd):
        d.mkdir(parents=True, exist_ok=True)

    script_file = a.script
    if not script_file:
        script_file = str(work / "script.txt")
        Path(script_file).write_text(text, encoding="utf-8")

    vj = work / "voice.json"
    if a.no_voice:
        d = fake_voice(text, vj, work, lang)
        print("[!] --no-voice: silent audio + estimated timing (for checking captions/picture only, never post)")
    elif a.skip_tts and vj.exists():
        d = json.loads(vj.read_text(encoding="utf-8"))
        print("reusing the existing voice.json (no API call)")
    else:
        d, cost = tts(text, vj, a.voice, a.seed, lang)
        print(f"TTS done | used {cost} credits")
    (work / "vo.mp3").write_bytes(base64.b64decode(d["audio_base64"]))
    total = round(d["alignment"]["character_end_times_seconds"][-1] + (0.3 if d.get("fake") else 0), 2)
    print(f"voice length {total}s")

    cues = tc.main(str(vj), str(assets / "cues.json"), script_file)
    if cues is None:
        cues = json.loads((assets / "cues.json").read_text(encoding="utf-8"))

    ding = None if a.ding == "none" else (Path(a.ding) if Path(a.ding).exists() else sound(a.ding))
    bgm = pick_bgm(a.bgm, text)
    if bgm:
        print(f"  music: {bgm.name}")
    audio = build_audio(work, work / "vo.mp3", ding, bgm, total)
    shutil.copyfile(audio, assets / "final_audio.m4a")
    broll = build_broll(work, a.broll, total)
    shutil.copyfile(broll, assets / "broll.mp4")

    final = outd / f"{slug}.mp4"
    if lang == "en":
        print("rendering captions (ffmpeg + libass, no Chrome needed)...")
        ok = burn_ass_captions(cues, assets, assets / "broll.mp4", assets / "final_audio.m4a", final, total)
    else:
        print("rendering captions (the pack's Chrome)...")
        node = shutil.which("node") or _env.local().get("node") or "node"
        env = {k: v for k, v in os.environ.items() if k != "NODE_OPTIONS"}
        r = subprocess.run([node, str(HERE / "render_caps.mjs"), f"--cues={assets / 'cues.json'}", f"--broll={assets / 'broll.mp4'}",
                            f"--audio={assets / 'final_audio.m4a'}", f"--out={final}", f"--dur={total}", f"--font={FONT}"],
                           env=env, cwd=str(ROOT))
        ok = r.returncode == 0
    if not ok or not final.exists():
        sys.exit("render failed (see the messages above)")

    source, publishable = source_flag(a.broll)
    note = outd / f"{slug}.NOT-FOR-PUBLISHING.txt"
    if a.no_voice or publishable is False:
        why = []
        if publishable is False:
            why.append(f"B-roll from {source}: other people's copyright, for viewing/prototypes only, never post (for a real post use --source pexels or pixabay)")
        if a.no_voice:
            why.append("no voice-over (--no-voice): for checking captions/picture only")
        note.write_text("DO NOT POST THIS FILE\n\n" + "\n".join("- " + w for w in why) + "\n", encoding="utf-8")
        for w in why:
            print(f"[!] {w}")
    elif note.exists():
        note.unlink()
    if publishable is None:
        print(f"[!] the source of the B-roll in {a.broll} is unknown: record the source and licence of every file in assets/SOURCES.md before posting")

    if meta:
        nf = proj / "NOTES.md"
        row = f"| {datetime.date.today().year + 543}-{datetime.date.today():%m-%d} | indigo11-video render | {outd.name}/{final.name} · {total}s · B-roll: {source} |"
        if nf.exists():
            nf.write_text(nf.read_text(encoding="utf-8").rstrip("\n") + "\n" + row + "\n", encoding="utf-8")
    if meta and meta.get("status") == "done":
        # the project was closed before and this is a RE-RENDER: deliver the new file to projects/results/ at once (otherwise the old
        # video stays in results/ and the new one sits in the project folder)
        try:
            r = subprocess.run([sys.executable, str(HERE / "project.py"), "done", proj.name], capture_output=True, text=True, encoding="utf-8", errors="replace")
            print(r.stdout.strip()[-400:] if r.returncode == 0 else "[!] could not deliver the re-render to results/: run project.py done " + proj.name)
            final = next((Path(p) for p in [proj.parent / "results" / "video-creation" / proj.name / final.name] if Path(p).exists()), final)
        except Exception as e:
            print(f"[!] could not deliver the re-render to results/ ({e}): run project.py done {proj.name}")
    print(f"\nDone -> {final}  ({dur(final):.1f}s)")
    print(f"Check the real file before delivery: {PY} .claude/skills/indigo11-video/scripts/verify.py \"{final}\"  then open the picture")


if __name__ == "__main__":
    main()
