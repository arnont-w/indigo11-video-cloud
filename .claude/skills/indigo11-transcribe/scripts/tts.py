#!/usr/bin/env python3
"""tts.py - text to speech, multi-provider, config-driven.

The voice side of the kit (transcribe.py is the ear, this is the mouth). Picks the
provider from ~/.indigo11/config.json (optional, hand-edited: providers.tts.chain) filtered to ones
with an env key, else falls back to ElevenLabs. Writes an audio file (mp3/wav) and
prints a JSON result. WHAT to say is the caller's text; WHERE it goes in the edit is
the agent's job.

Providers (per the live provider docs):
  elevenlabs : api.elevenlabs.io/v1/text-to-speech/{voice_id} (xi-api-key, mp3)
  openai : api.openai.com/v1/audio/speech (Bearer, mp3)
  gemini : generativelanguage .../v1beta/interactions (x-goog-api-key, wav)

Key: whichever the chosen provider needs (ELEVENLABS / OPENAI / GEMINI), from .env or shell.

Usage:
  python3 tts.py --text "สวัสดีครับ" --out voice.mp3 [--provider auto] [--voice <id/name>] [--lang th]
"""
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)
import argparse
import base64
import json
import os
import struct
import sys
import urllib.request
import urllib.error
from pathlib import Path

if os.name == "nt":  # cp874 console crashes printing Thai (rule #32)
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

EL_API = "https://api.elevenlabs.io/v1"
OPENAI_API = "https://api.openai.com/v1"
GEMINI_API = "https://generativelanguage.googleapis.com"

# default voices per provider (overridable with --voice)
# Brian: verified on Thai by round-tripping TTS -> Scribe STT. He is the only
# voice tested that says both "สามศูนย์หนึ่ง" and "โดราเอมอน" correctly; George
# and Chris slip into an English accent on proper nouns, Daniel says "โดเรมอน".
EL_DEFAULT_VOICE = "nPczCjzI2devNBz1zQrb"
OPENAI_DEFAULT_VOICE = "alloy"
GEMINI_DEFAULT_VOICE = "Kore"

TTS_ENV = {"elevenlabs": "ELEVENLABS_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}
# Only the v3/v4 families speak Thai (/v1/models reports th:true for eleven_v3 and
# eleven_v4, th:false for every v2 family member). Sending Thai to multilingual_v2
# does not error - it reads the Thai letters as English gibberish
# ("A kens chunihui ne pkinikdiba..."), which sounds like a broken voice rather
# than a wrong model. Do not "optimize" this back to v2/flash/turbo for Thai.
# eleven_v4 (2026-09): list price = v3 ($0.08/1K chars; $0.022 with the 72% launch
# discount to 2026-10-12 - elevenlabs.io/pricing/api), ~1.7x faster, 10k-char limit
# (v3: 5k); Scribe round-trip on Thai matched v3 word for word. The models page only
# says v4 is "available via the Text to Dialogue API", but /v1/text-to-speech/{voice}
# accepts it (re-verified live 2026-10-02).
#
# openai: gpt-4o-mini-tts ($0.60/1M text in, $12/1M audio out) is still the only
# model family /v1/audio/speech takes, but BOTH its snapshots (-2025-03-20,
# -2025-12-15, the alias target) plus tts-1/tts-1-hd SHUT DOWN 2027-01-06
# (deprecations page, 2026-10-01). The named replacement gpt-realtime-2.1-mini is
# Realtime-only (WebRTC/WebSocket/SIP; /v1/audio/speech answers 404 for it), so
# there is no plain-HTTPS successor to move to yet. Re-check before that date;
# until then auto fail-over to elevenlabs/gemini covers an OpenAI outage.
#
# gemini: gemini-3.8-flash-tts is the docs' named replacement for
# gemini-2.5-flash-preview-tts (no shutdown date yet, but Google is restricting the
# 2.5 family to keys that already used it). Thai is supported on Flash but NOT on
# gemini-3.8-flash-lite-tts - do not "save money" by switching to Lite.
# $0.50/1M text in, $9/1M audio out (~$0.00225 per 10s) through 2026-12-31, then
# $1.00 / $18.
TTS_MODELS = {"elevenlabs": "eleven_v4", "openai": "gpt-4o-mini-tts", "gemini": "gemini-3.8-flash-tts"}


def load_env_file() -> None:
    cwd = Path.cwd().resolve()
    for parent in [cwd] + list(cwd.parents):
        env_path = parent / ".env"
        if env_path.exists():
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k and k not in os.environ:
                        os.environ[k] = v
            except Exception:
                pass
            return


def load_config() -> dict:
    try:
        p = Path.home() / ".indigo11" / "config.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        return {}


def config_chain(task: str) -> list:
    try:
        chain = load_config().get("providers", {}).get(task, {}).get("chain", [])
        return [str(c) for c in chain] if isinstance(chain, list) else []
    except Exception:
        return []


def provider_candidates(explicit: str) -> list:
    """Ordered keyed TTS providers for runtime fail-over.

    explicit --provider = exactly that one (fail-fast). auto = config chain then
    elevenlabs>openai>gemini, keyed + deduped.
    """
    if explicit and explicit != "auto":
        return [explicit]
    ordered = []
    for cid in config_chain("tts"):
        if cid in TTS_ENV and os.environ.get(TTS_ENV[cid], "").strip() and cid not in ordered:
            ordered.append(cid)
    for p in ("elevenlabs", "openai", "gemini"):
        if os.environ.get(TTS_ENV[p], "").strip() and p not in ordered:
            ordered.append(p)
    return ordered


def classify_status(code: int) -> dict:
    """HTTP code -> {kind, failover}. Shared with transcribe's classifier semantics."""
    if code == 401:
        return {"kind": "invalid", "failover": True}
    if code == 402:
        return {"kind": "billing", "failover": True}
    if code == 403:
        return {"kind": "scope", "failover": True}
    if code == 429:
        return {"kind": "rate_limit", "failover": True}
    if code >= 500:
        return {"kind": "provider_down", "failover": True}
    if code == 400:
        return {"kind": "bad_request", "failover": False}
    return {"kind": "other", "failover": False}


def _post(url, payload, headers, timeout=120, raw=False):
    data = payload if raw else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


_LANG = "th"


def own_voice():
    """The user's own ElevenLabs voice from assets/brand/brand.json: voices.<lang>, else voice. '' if none."""
    try:
        b = json.loads((Path(__file__).resolve().parents[4] / "assets" / "brand" / "brand.json").read_text(encoding="utf-8"))
        return (b.get("voices") or {}).get(_LANG) or b.get("voice") or ""
    except Exception:
        return ""


def tts_elevenlabs(text, out_path, key, voice):
    voice = voice or own_voice() or EL_DEFAULT_VOICE   # --voice > own voice (per language, then general) > built-in
    body = _post(
        f"{EL_API}/text-to-speech/{voice}?output_format=mp3_44100_128",
        {"text": text, "model_id": TTS_MODELS["elevenlabs"]},
        {"xi-api-key": key, "Content-Type": "application/json"},
        timeout=180,
    )
    Path(out_path).write_bytes(body)
    return {"provider": "elevenlabs", "model": TTS_MODELS["elevenlabs"], "voice": voice}


def tts_openai(text, out_path, key, voice):
    voice = voice or OPENAI_DEFAULT_VOICE
    body = _post(
        f"{OPENAI_API}/audio/speech",
        {"model": TTS_MODELS["openai"], "input": text, "voice": voice, "response_format": "mp3"},
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=180,
    )
    Path(out_path).write_bytes(body)
    return {"provider": "openai", "model": TTS_MODELS["openai"], "voice": voice}


def _pcm_to_wav(pcm: bytes, rate=24000, ch=1, bits=16) -> bytes:
    # Headerless little-endian PCM (audio/l16) -> playable WAV. Only for PCM: Gemini 3.8
    # already answers with a complete WAV, see the RIFF check in tts_gemini.
    byte_rate = rate * ch * bits // 8
    block_align = ch * bits // 8
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE" + b"fmt " + struct.pack(
        "<IHHIIHH", 16, 1, ch, rate, byte_rate, block_align, bits) + b"data" + struct.pack("<I", len(pcm))
    return header + pcm


def tts_gemini(text, out_path, key, voice):
    voice = voice or GEMINI_DEFAULT_VOICE
    # Gemini 3.8 TTS reads `input` as a VERBATIM transcript. The 2.5 preview needed a
    # "read this aloud" prefix or it answered "Model tried to generate text"; 3.8 SPEAKS
    # that prefix (measured: 9.0s instead of 6.0s, Scribe heard
    # "อ่านออกเสียงข้อความต่อไปนี้ ..." in front of the sentence). Send the text bare.
    d = json.loads(_post(
        f"{GEMINI_API}/v1beta/interactions",
        {
            "model": TTS_MODELS["gemini"],
            "input": [{"type": "user_input", "content": [{"type": "text", "text": text}]}],
            "response_format": {"type": "audio"},
            "generation_config": {"speech_config": [{"voice": voice}]},
        },
        {"x-goog-api-key": key, "Content-Type": "application/json"},
        timeout=180,
    ).decode())
    b64 = None
    for step in d.get("steps") or []:
        if step.get("type") != "model_output":
            continue
        for c in step.get("content") or []:
            if c.get("type") == "audio" and c.get("data"):
                b64 = c["data"]  # last audio block wins, as in the docs' example
    if not b64:
        raise RuntimeError(f"no audio in Gemini TTS response: {json.dumps(d)[:300]}")
    audio = base64.b64decode(b64)
    # Unary 3.8 returns a finished WAV (RIFF header, 24kHz mono 16-bit). Wrapping that
    # again gives a file with two headers; only headerless PCM gets one added.
    if audio[:4] != b"RIFF":
        audio = _pcm_to_wav(audio)
    # ensure .wav since Gemini gives WAV
    if not str(out_path).lower().endswith(".wav"):
        out_path = str(Path(out_path).with_suffix(".wav"))
    Path(out_path).write_bytes(audio)
    return {"provider": "gemini", "model": TTS_MODELS["gemini"], "voice": voice, "out": out_path}


PROVIDERS = {"elevenlabs": tts_elevenlabs, "openai": tts_openai, "gemini": tts_gemini}


def main():
    ap = argparse.ArgumentParser(description="Text to speech (multi-provider, config-driven).")
    ap.add_argument("--text", required=True)
    ap.add_argument("--out", required=True, help="output audio path (.mp3, or .wav for gemini)")
    ap.add_argument("--provider", default="auto", choices=["auto", "elevenlabs", "openai", "gemini"])
    ap.add_argument("--voice", default=None, help="voice id (elevenlabs) or name (openai/gemini)")
    ap.add_argument("--lang", default="th", help="language hint (informational)")
    args = ap.parse_args()
    global _LANG
    _LANG = args.lang

    load_env_file()
    candidates = provider_candidates(args.provider)
    if not candidates:
        print(json.dumps({"ok": False, "error": "no_provider_key",
                          "message_en": "No TTS provider key set (ELEVENLABS/OPENAI/GEMINI)."}, ensure_ascii=False))
        sys.exit(1)

    out_path = os.path.expanduser(args.out)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    info = None
    provider = None
    attempts = []
    explicit = args.provider != "auto"
    for cand in candidates:
        key = os.environ.get(TTS_ENV[cand], "").strip()
        if not key:
            attempts.append({"provider": cand, "skipped": "no_key"})
            continue
        try:
            info = PROVIDERS[cand](args.text, out_path, key, args.voice)
            provider = cand
            break
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="ignore")[:400]
            v = classify_status(e.code)
            attempts.append({"provider": cand, "code": e.code, "kind": v["kind"]})
            if explicit or not v["failover"] or cand == candidates[-1]:
                print(json.dumps({"ok": False, "provider": cand, "http": e.code, "kind": v["kind"],
                                  "attempts": attempts, "error": f"HTTP {e.code}: {body}"}, ensure_ascii=False))
                sys.exit(1)
            print(f"[tts] {cand} failed ({v['kind']} HTTP {e.code}) - trying next", file=sys.stderr)
        except Exception as e:
            attempts.append({"provider": cand, "kind": "error"})
            if explicit or cand == candidates[-1]:
                print(json.dumps({"ok": False, "provider": cand, "attempts": attempts,
                                  "error": str(e)}, ensure_ascii=False))
                sys.exit(1)
            print(f"[tts] {cand} error: {str(e)[:120]} - trying next", file=sys.stderr)
    final = info.get("out", out_path)
    print(json.dumps({"ok": True, "file": final, "bytes": os.path.getsize(final), **info}, ensure_ascii=False))


if __name__ == "__main__":
    main()
