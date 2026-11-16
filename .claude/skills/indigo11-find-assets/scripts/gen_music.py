#!/usr/bin/env python3
"""gen_music.py - generate background music from a text prompt, multi-provider.

The paid/gen tail of the music chain: free stock (find_assets.py --type music) first,
generation only when stock has nothing fitting or the user asks. Config-driven like
gen_asset.py: providers.music.chain from ~/.indigo11/config.json, filtered to ones with a
key, else ElevenMusic by default.

Providers (per the live docs):
  mixkit / musicgen : free STOCK or local, handled by find_assets.py (not here)
  elevenmusic : api.elevenlabs.io/v1/music (xi-api-key, prompt + music_length_ms)
  gemini lyria : generativelanguage .../models/lyria-*:generateContent (x-goog-api-key)

Mixkit/MusicGen are not generated here (they're free stock/local via find_assets). This
script is only the API-gen providers. WHAT mood to ask is the caller's prompt.

Usage:
  python3 gen_music.py --prompt "calm lofi for a cooking clip" --duration 20 --out bgm.mp3
  python3 gen_music.py --prompt "..." --estimate-only
"""
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)
import argparse
import base64
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

EL_API = "https://api.elevenlabs.io/v1"
GEMINI_API = "https://generativelanguage.googleapis.com"
THB_PER_USD = 33.0

# config id -> (family, env, model, usd per song estimate | usd_per_min when billed by length)
# music_v2_5 (2026-09): same $0.15/min as music_v1, ~8x faster to generate.
# Lyria prices are per request (ai.google.dev/gemini-api/docs/pricing): clip (always 30s) $0.04,
# full song $0.08. lyria-3-pro-preview is now "legacy" with lyria-3.5 as its documented
# replacement at the same price, so the old gemini_lyria3_pro id points at lyria-3.5 - config
# ids are a contract with buyers' ~/.indigo11/config.json and are never renamed or dropped.
MUSIC_PROVIDERS = {
    "elevenmusic": {"family": "elevenmusic", "env": "ELEVENLABS_API_KEY", "model": "music_v2_5", "usd_per_min": 0.15},
    "gemini_lyria3_clip": {"family": "lyria", "env": "GEMINI_API_KEY", "model": "lyria-3-clip-preview", "usd": 0.04},
    "gemini_lyria3_pro": {"family": "lyria", "env": "GEMINI_API_KEY", "model": "lyria-3.5", "usd": 0.08},
    "gemini_lyria35": {"family": "lyria", "env": "GEMINI_API_KEY", "model": "lyria-3.5", "usd": 0.08},
}
LEGACY_DEFAULT = "elevenmusic"


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


def resolve_provider():
    """First config music.chain entry that is an API-gen provider with its key, else ElevenMusic."""
    for cid in config_chain("music"):
        entry = MUSIC_PROVIDERS.get(cid)
        if entry and os.environ.get(entry["env"], "").strip():
            return entry, cid
    entry = MUSIC_PROVIDERS[LEGACY_DEFAULT]
    if os.environ.get(entry["env"], "").strip():
        return entry, LEGACY_DEFAULT
    needed = sorted({e["env"] for e in MUSIC_PROVIDERS.values()})
    raise RuntimeError("no music-gen provider reachable. set one of: %s (or use free stock: "
                       "find_assets.py --type music)" % ", ".join(needed))


def _post(url, payload, headers, timeout=300, raw_resp=True):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def gen_elevenmusic(prompt, duration_s, out_path, key, entry):
    ms = max(3000, min(600000, int(duration_s * 1000)))
    body = _post(
        f"{EL_API}/music?output_format=mp3_44100_128",
        {"prompt": prompt, "music_length_ms": ms, "model_id": entry["model"]},
        {"xi-api-key": key, "Content-Type": "application/json"},
        timeout=600,
    )
    Path(out_path).write_bytes(body)
    return {"provider": "elevenmusic", "model": entry["model"], "out": out_path,
            "cost_usd": entry["usd_per_min"] * ms / 60000}


def gen_lyria(prompt, duration_s, out_path, key, entry):
    raw = _post(
        f"{GEMINI_API}/v1beta/models/{entry['model']}:generateContent",
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["AUDIO"]},
        },
        {"x-goog-api-key": key, "Content-Type": "application/json"},
        timeout=600,
    )
    d = json.loads(raw.decode())
    parts = (((d.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
    b64 = None
    for p in parts:
        inline = p.get("inlineData") or p.get("inline_data")
        if inline and inline.get("data"):
            b64 = inline["data"]
            break
    if not b64:
        raise RuntimeError(f"no audio in Lyria response: {json.dumps(d)[:300]}")
    Path(out_path).write_bytes(base64.b64decode(b64))
    return {"provider": "lyria", "model": entry["model"], "out": out_path, "cost_usd": entry["usd"]}


def main():
    ap = argparse.ArgumentParser(description="Generate background music (multi-provider, config-driven).")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--duration", type=int, default=20, help="seconds (3-600)")
    ap.add_argument("--out", required=True, help="output audio path (.mp3)")
    ap.add_argument("--estimate-only", action="store_true")
    ap.add_argument("--confirm-cost", action="store_true",
                    help="REQUIRED to actually generate (bills real money). Without it, prints "
                         "the estimate and exits non-zero (estimate->ask->generate).")
    args = ap.parse_args()

    # env loader + UTF-8 stdio guard from find_assets (shared)
    from find_assets import load_env, _utf8_stdio
    _utf8_stdio()
    load_env()

    try:
        entry, prov_id = resolve_provider()
    except Exception as e:
        if args.estimate_only:
            print(json.dumps({"ok": True, "estimate": {"usd": 0.0, "thb_approx": 0.0}, "provider": None, "charged": False}))
            return
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)

    usd = entry["usd_per_min"] * max(3, min(600, args.duration)) / 60 if "usd_per_min" in entry else entry["usd"]
    est = {"usd": round(usd, 3), "thb_approx": round(usd * THB_PER_USD, 1)}
    if args.estimate_only:
        print(json.dumps({"ok": True, "estimate": est, "provider": prov_id, "charged": False}))
        return

    # Hard cost gate (M-FA1): music generation bills real money on EVERY provider
    # incl. ElevenMusic (it is NOT free-and-commercial-safe - P1-05). No silent charge.
    if not args.confirm_cost:
        print(json.dumps({"ok": False, "needs_confirmation": True, "estimate": est,
                          "provider": prov_id, "charged": False,
                          "message_en": "Music generation bills real money. Re-run with --confirm-cost."},
                         ensure_ascii=False))
        sys.exit(2)

    key = os.environ.get(entry["env"], "").strip()
    out_path = os.path.expanduser(args.out)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    try:
        if entry["family"] == "elevenmusic":
            info = gen_elevenmusic(args.prompt, args.duration, out_path, key, entry)
        else:
            info = gen_lyria(args.prompt, args.duration, out_path, key, entry)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")[:400]
        kind = ({401: "invalid", 402: "billing", 403: "scope",
                 429: "rate_limit"}.get(e.code, "provider_down" if e.code >= 500 else "other"))
        msg_en = {"invalid": "invalid or expired key", "billing": "quota or credit used up - top up your account",
                  "scope": "the key is not enabled for music", "rate_limit": "rate limited - try again",
                  "provider_down": "the provider is down"}.get(kind, "the API call failed")
        print(json.dumps({"ok": False, "provider": prov_id, "http": e.code, "kind": kind,
                          "error": f"HTTP {e.code} ({kind}): {body}", "message_en": f"{prov_id}: {msg_en}",
                          "charged": False}, ensure_ascii=False))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"ok": False, "provider": prov_id, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)
    final = info.get("out", out_path)
    print(json.dumps({"ok": True, "file": final, "bytes": os.path.getsize(final),
                      "provider": prov_id, "model": info["model"],
                      "cost": {"usd": round(info["cost_usd"], 4), "thb_approx": round(info["cost_usd"] * THB_PER_USD, 1)},
                      # commercial rights depend on the user's plan + provider terms - don't assert.
                      "license": {"tier": "generated", "provider": prov_id, "commercial_ok": None,
                                  "credit_required": None,
                                  "note": "AI-generated; verify commercial rights for your plan in the "
                                          "provider's terms before a paid/brand deliverable."}},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
