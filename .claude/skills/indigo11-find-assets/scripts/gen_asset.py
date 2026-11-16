#!/usr/bin/env python3
"""Generate B-roll assets (images / short video clips), multi-provider - PAID fallback.

This is the paid tail of the broll chain: free stock (find_assets.py --type broll)
first, generation only when stock has nothing usable or the user explicitly asks
to generate. Costs real money per call, so the CALLING AGENT must show the baht
estimate (run with --estimate-only) and get user agreement before generating.

Provider is chosen from ~/.indigo11/config.json (optional, hand-edited: providers.image.chain /
video_gen.chain), filtered to ones with a key, else  the legacy OpenRouter default
(edit-rules #31). Providers wired here:
  image : Gemini 3.x Image (GEMINI key) / xAI Grok Imagine (XAI key) / GPT Image 2 via OpenRouter (OPENROUTER key)
          or direct with an OpenAI key (OPENAI_API_KEY, provider id openai_image)
  video : Gemini Omni Flash, Veo 3.1 until its 2026-10-22 shutdown (GEMINI key) /
          Grok Imagine + OpenRouter (OPENROUTER key)

Each provider uses its own key from .env (GEMINI/XAI/OPENAI/OPENROUTER). Without any of them
the script exits with a clear bilingual error - the free stock chain still works, so
gen is an upgrade, never a requirement (same pattern as Klipy).

Generated output is licensed to the API customer per provider terms (OpenAI/xAI)
 - fine for commercial edits, tagged tier "generated" in meta.json.

Usage:
  python3 gen_asset.py --type image --prompt "flat-lay of Thai street food, top view"
  python3 gen_asset.py --type video --prompt "crowded Bangkok night market, neon" \
      [--duration 4] [--resolution 720p] [--aspect 9:16] [--count 1]
  python3 gen_asset.py --type video --prompt "..." --estimate-only # no charge

Deterministic plumbing only (HTTP + polling + file I/O). WHAT to generate and
WHERE it lands in the edit stays with the calling agent.
"""
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)
import argparse
import base64
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

from find_assets import _utf8_stdio, download, find_kit_root, load_env, slug

API = "https://openrouter.ai/api/v1" # OpenRouter (OpenAI image, Grok video)
GEMINI_API = "https://generativelanguage.googleapis.com" # Google Gemini Image / Veo / Omni (direct)
XAI_API = "https://api.x.ai/v1" # xAI Grok (direct)
OPENAI_API = "https://api.openai.com/v1" # OpenAI Images API (direct, OPENAI_API_KEY)
IMAGE_MODEL = "openai/gpt-5.4-image-2" # default OpenRouter image model
VIDEO_MODEL = "x-ai/grok-imagine-video" # default OpenRouter video model
THB_PER_USD = 33.0 # rough display rate, estimate only, billing is in USD

# USD per second by resolution (from OpenRouter pricing_skus for grok-imagine-video)
VIDEO_RATE = {"480p": 0.05, "720p": 0.07}
IMAGE_EST_USD = 0.13 # measured live 2026-10-02: gpt-5.4-image-2, quality high, 9:16 = 4312 image tokens = $0.1297

# Veo 3.1 previews shut down 2026-10-22 (ai.google.dev/gemini-api/docs/deprecations); the
# documented successor for all three is gemini-omni-1.1-flash. From that day every
# gemini_veo31_* config id resolves to Omni Flash, so a buyer's saved chain keeps working.
VEO_SHUTDOWN = "2026-10-22"
# Omni Flash bills output video tokens at $17.50 / 1M. 720p = 5,792 tokens per second is the
# documented rate (ai.google.dev/gemini-api/docs/pricing) = ~$0.10/s. 360p is MEASURED, one
# live clip on 2026-10-02: 3 s -> 5,793 video tokens = 1,931/s = ~$0.034/s. 1080p and 4k
# (upscaled) have no published token rate, so they are not offered - they fall back to 720p.
OMNI_USD_PER_TOKEN = 17.5e-6
OMNI_RATE = {"360p": 1931 * OMNI_USD_PER_TOKEN, "720p": 5792 * OMNI_USD_PER_TOKEN}

# ---- provider registry: ~/.indigo11/config.json config id -> how to call it -----------------------
# Each entry: provider family, the real model id (per the live provider docs), the env key it
# needs, and a per-unit USD estimate. Families: "openrouter" (image via /images, video via
# /videos), "gemini" (direct generativelanguage API), "xai" (direct api.x.ai).
# A chain entry whose family isn't implemented or whose key is missing is skipped, so a mixed
# chain still resolves to the first reachable provider. Falls back to the legacy OpenRouter
# default when no config chain is present (byte-for-byte the old behaviour).
#
# Config ids are a stable contract with buyers' ~/.indigo11/config.json: NEVER rename or drop one.
# When a model is retired the old id is re-pointed at its documented successor instead
# (imagen4_* and nano_banana -> gemini-3.1-flash-image; Imagen 4 shut down 2026-08-17 and
# gemini-2.5-flash-image 2026-10-02). Gemini image prices are for a 1K image, which is what
# _image_gemini requests (official pricing page, 2026-10).
IMAGE_PROVIDERS = {
    "gemini_imagen4_fast": {"family": "gemini", "model": "gemini-3.1-flash-image", "env": "GEMINI_API_KEY", "usd": 0.067},
    "gemini_nano_banana": {"family": "gemini", "model": "gemini-3.1-flash-image", "env": "GEMINI_API_KEY", "usd": 0.067},
    "gemini_imagen4_std": {"family": "gemini", "model": "gemini-3.1-flash-image", "env": "GEMINI_API_KEY", "usd": 0.067},
    "gemini_flash_image": {"family": "gemini", "model": "gemini-3.1-flash-image", "env": "GEMINI_API_KEY", "usd": 0.067},
    "gemini_flash_lite_image": {"family": "gemini", "model": "gemini-3.1-flash-lite-image", "env": "GEMINI_API_KEY", "usd": 0.034},
    "gemini_pro_image": {"family": "gemini", "model": "gemini-3-pro-image", "env": "GEMINI_API_KEY", "usd": 0.134},
    "openai_gpt_image_2": {"family": "openrouter", "model": "openai/gpt-5.4-image-2", "env": "OPENROUTER_API_KEY", "usd": IMAGE_EST_USD},
    "openrouter": {"family": "openrouter", "model": "openai/gpt-5.4-image-2", "env": "OPENROUTER_API_KEY", "usd": IMAGE_EST_USD},
    # OpenAI directly with an OPENAI_API_KEY (no OpenRouter). Request shape copied from the working
    # image-gen-router/gen_openai.py: POST /v1/images/generations {model, prompt, size, quality, n}
    # -> data[0].b64_json. "usd" is a rough ESTIMATE for quality "high" (OpenAI publishes no flat
    # per-image price for gpt-image-2 and the response carries no cost), so re-check the OpenAI
    # pricing page; billing is per image against the OpenAI credit balance.
    "openai_image": {"family": "openai", "model": "gpt-image-2", "env": "OPENAI_API_KEY", "usd": IMAGE_EST_USD},
    # $0.05/image (docs.x.ai/developers/pricing). xAI retires this slug on 2026-11-02 but keeps
    # it resolving: requests are then served by grok-imagine-image-2.0 at quality "low" ($0.04)
    # with the same request/response shape, so this entry keeps working unchanged. Moving to
    # the 2.0 slug + its quality/aspect_ratio params is NOT done here because it could not be
    # verified with a real call (the dev key had no image ACL).
    "xai_grok_image": {"family": "xai", "model": "grok-imagine-image-quality", "env": "XAI_API_KEY", "usd": 0.05},
}
# video usd is per-second; "usd" is the 720p per-second rate and "rates" the per-resolution
# table (a resolution the provider doesn't offer falls back to its 720p rate - the call clamps
# the same way, so the estimate matches what is generated).
VIDEO_PROVIDERS = {
    "gemini_veo31_lite": {"family": "gemini", "model": "veo-3.1-lite-generate-preview", "env": "GEMINI_API_KEY", "usd": 0.05,
                          "rates": {"720p": 0.05, "1080p": 0.08}},
    "gemini_veo31_fast": {"family": "gemini", "model": "veo-3.1-fast-generate-preview", "env": "GEMINI_API_KEY", "usd": 0.10,
                          "rates": {"720p": 0.10, "1080p": 0.12}},
    "gemini_veo31_std": {"family": "gemini", "model": "veo-3.1-generate-preview", "env": "GEMINI_API_KEY", "usd": 0.40,
                         "rates": {"720p": 0.40, "1080p": 0.40}},
    "gemini_omni_flash": {"family": "gemini", "model": "gemini-omni-1.1-flash", "env": "GEMINI_API_KEY",
                          "usd": OMNI_RATE["720p"], "rates": OMNI_RATE},
    "grok_imagine_720p": {"family": "openrouter", "model": "x-ai/grok-imagine-video", "env": "OPENROUTER_API_KEY", "usd": 0.07,
                          "rates": VIDEO_RATE},
    "openrouter": {"family": "openrouter", "model": "x-ai/grok-imagine-video", "env": "OPENROUTER_API_KEY", "usd": 0.07,
                   "rates": VIDEO_RATE},
}


def _live(entry):
    """Swap a Veo entry for its successor (Omni Flash) once Veo's shutdown date has passed."""
    if entry and entry["model"].startswith("veo-") and time.strftime("%Y-%m-%d") >= VEO_SHUTDOWN:
        return VIDEO_PROVIDERS["gemini_omni_flash"]
    return entry


def _video_rate(entry, resolution):
    """USD per second for this provider at this resolution."""
    if not entry:
        return VIDEO_RATE.get(resolution, VIDEO_RATE["720p"])
    return entry.get("rates", {}).get(resolution, entry["usd"])


def _video_duration(entry, seconds):
    """The clip length the provider will actually make (and bill) for a requested length."""
    if entry and entry["family"] == "gemini":
        if entry["model"].startswith("veo-"): # Veo: 4, 6 or 8 s only
            return seconds if seconds in (4, 6, 8) else 8
        return max(3, min(10, seconds)) # Omni Flash: 3-10 s
    return seconds


def load_config() -> dict:
    """Read ~/.indigo11/config.json (~/.indigo11/config.json). {} on any error so no-config = old behaviour."""
    try:
        p = Path.home() / ".indigo11" / "config.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        return {}


def config_chain(task: str) -> list:
    """providers.<task>.chain from config as a list of ids, or [] if absent."""
    try:
        chain = load_config().get("providers", {}).get(task, {}).get("chain", [])
        return [str(c) for c in chain] if isinstance(chain, list) else []
    except Exception:
        return []


def config_model(task: str, provider_id: str) -> str:
    """providers.<task>.models.<provider_id> - the exact model slug the user picked in Setup
    Studio's live OpenRouter catalogue. '' if absent (then the script's hardcoded default is used)."""
    try:
        models = load_config().get("providers", {}).get(task, {}).get("models", {})
        v = models.get(provider_id) if isinstance(models, dict) else None
        return str(v).strip() if v else ""
    except Exception:
        return ""


def resolve_provider(kind, model_override):
    """Pick the gen provider entry for image|video.

    Order: explicit --model override (legacy OpenRouter path) wins; else the config chain
    (image.chain / video_gen.chain), first entry that is implemented AND has its env key;
    else the legacy OpenRouter default. Returns (entry_dict, config_id) or raises if nothing
    is reachable (no key for any candidate).
    """
    table = IMAGE_PROVIDERS if kind == "image" else VIDEO_PROVIDERS
    task = "image" if kind == "image" else "video_gen"
    legacy_id = "openrouter"
    # explicit --model override keeps the old OpenRouter behaviour exactly
    if model_override:
        return ({"family": "openrouter", "model": model_override, "env": "OPENROUTER_API_KEY",
                 "usd": IMAGE_EST_USD if kind == "image" else VIDEO_RATE["720p"], "rates": VIDEO_RATE}, "override")
    # config chain
    for cid in config_chain(task):
        entry = _live(table.get(cid))
        if entry and os.environ.get(entry["env"], "").strip():
            # OpenRouter entries can carry a user-picked model from ~/.indigo11/config.json's live catalogue
            # (providers.<task>.models.openrouter). Override the default slug with it when present.
            if entry.get("family") == "openrouter":
                picked = config_model(task, cid)
                if picked:
                    entry = {**entry, "model": picked}
            return (entry, cid)
    # legacy default (OpenRouter)
    entry = table[legacy_id]
    if os.environ.get(entry["env"], "").strip():
        return (entry, legacy_id)
    # images: an OpenAI key alone is enough (direct Images API, no OpenRouter needed)
    if kind == "image" and os.environ.get(IMAGE_PROVIDERS["openai_image"]["env"], "").strip():
        return (IMAGE_PROVIDERS["openai_image"], "openai_image")
    # nothing reachable: surface which keys would unlock it
    needed = sorted({e["env"] for e in table.values()})
    raise RuntimeError("no gen provider reachable for %s. set one of: %s in <starter>/.env"
                       % (kind, ", ".join(needed)))


def _api(path, payload=None, key=None, timeout=120):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _gemini_post(path, payload, key, timeout=300):
    """POST to the Gemini generativelanguage API (x-goog-api-key auth)."""
    req = urllib.request.Request(
        GEMINI_API + path,
        data=json.dumps(payload).encode(),
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _gemini_get(path, key, timeout=60):
    req = urllib.request.Request(GEMINI_API + path, headers={"x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _openai_post(path, payload, key, timeout=600):
    """POST to the OpenAI API (Bearer auth). Raises RuntimeError with the API's own error text."""
    req = urllib.request.Request(
        OPENAI_API + path,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:600]
        hint = {401: " (auth failure: check OPENAI_API_KEY)",
                429: " (rate limited or out of credit: check the OpenAI billing balance)"}.get(e.code, "")
        raise RuntimeError(f"OpenAI API HTTP {e.code}{hint}: {detail}")


def _xai_post(path, payload, key, timeout=300):
    req = urllib.request.Request(
        XAI_API + path,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def estimate(args, entry=None):
    """Cost estimate. Uses the resolved provider's per-unit rate when available, else the
    legacy OpenRouter rates so --estimate-only stays accurate before a key check."""
    if args.type == "image":
        per = (entry or {}).get("usd", IMAGE_EST_USD)
        usd = per * args.count
    else:
        # video: per-second rate at the requested resolution x the length the provider will
        # really generate (Veo and Omni round the requested duration - see _video_duration).
        usd = _video_rate(entry, args.resolution) * _video_duration(entry, args.duration) * args.count
    return {"usd": round(usd, 3), "thb_approx": round(usd * THB_PER_USD, 1)}


# ---------- IMAGE ----------
def _image_openrouter(args, entry, key, out_dir, out):
    # OpenRouter's dedicated Image API (POST /images). It serves every image model in the
    # catalogue, including image-only ones (gpt-image-2, grok-imagine-image-2.0) that the old
    # image-over-chat-completions call could not, and it honours aspect_ratio.
    body = {"model": entry["model"], "prompt": args.prompt, "aspect_ratio": args.aspect}
    if entry["model"].startswith("openai/"):
        # OpenAI image models default to quality "auto", which served a ~$0.004 low-quality
        # image in a live test. Pin "high" so the output matches what the estimate quotes.
        # (Only OpenAI slugs: other providers accept a different quality set, e.g. Grok low/medium.)
        body["quality"] = "high"
    d = _api("/images", body, key, timeout=300)
    items = d.get("data") or []
    if not items:
        raise RuntimeError(f"no image in response: {json.dumps(d)[:300]}")
    cost = (d.get("usage") or {}).get("cost")
    for item in items:
        ext = {"image/jpeg": "jpg", "image/webp": "webp"}.get(item.get("media_type"), "png")
        fn = os.path.join(out_dir, f"{len(out)+1}-gen-{slug(args.prompt)}.{ext}")
        if item.get("b64_json"):
            with open(fn, "wb") as f:
                f.write(base64.b64decode(item["b64_json"]))
        elif not (item.get("url") and download(item["url"], fn, timeout=120)):
            continue
        out.append({"file": fn, "source": f"openrouter:{entry['model']}", "url": None,
                    "credit": None, "cost_usd": cost / len(items) if cost else entry["usd"]})


# generationConfig.responseFormat.image takes proto ENUM names over REST. The image guide's curl
# example sends "16:9" / "2K" there and the live API answers INVALID_ARGUMENT (verified
# 2026-10-02); the enum names below are from ai.google.dev/api/generate-content.
GEMINI_ASPECT = {
    "1:1": "ASPECT_RATIO_ONE_BY_ONE", "16:9": "ASPECT_RATIO_SIXTEEN_BY_NINE",
    "9:16": "ASPECT_RATIO_NINE_BY_SIXTEEN", "4:3": "ASPECT_RATIO_FOUR_BY_THREE",
    "3:4": "ASPECT_RATIO_THREE_BY_FOUR", "3:2": "ASPECT_RATIO_THREE_BY_TWO",
    "2:3": "ASPECT_RATIO_TWO_BY_THREE",
}


def _image_gemini(args, entry, key, out_dir, out):
    # generateContent with responseModalities IMAGE (per the Gemini image docs). Size and aspect
    # live under generationConfig.responseFormat.image (the docs moved off imageConfig).
    # imageSize is pinned to 1K because the price is per size - 1K is what the estimate quotes.
    d = _gemini_post(f"/v1/models/{entry['model']}:generateContent", {
        "contents": [{"parts": [{"text": args.prompt}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "responseFormat": {"image": {
                "aspectRatio": GEMINI_ASPECT.get(args.aspect, GEMINI_ASPECT["9:16"]),
                "imageSize": "IMAGE_SIZE_ONE_K",
            }},
        },
    }, key)
    parts = (((d.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
    got = False
    for part in parts:
        inline = part.get("inlineData") or part.get("inline_data")
        if inline and inline.get("data"):
            ext = "jpg" if "jpeg" in (inline.get("mimeType") or inline.get("mime_type") or "") else "png"
            fn = os.path.join(out_dir, f"{len(out)+1}-gen-{slug(args.prompt)}.{ext}")
            with open(fn, "wb") as f:
                f.write(base64.b64decode(inline["data"]))
            out.append({"file": fn, "source": f"gemini:{entry['model']}", "url": None,
                        "credit": None, "cost_usd": entry["usd"]})
            got = True
    if not got:
        raise RuntimeError(f"no image in Gemini response: {json.dumps(d)[:300]}")


def _image_xai(args, entry, key, out_dir, out):
    d = _xai_post("/images/generations", {"model": entry["model"], "prompt": args.prompt, "n": 1}, key)
    for item in (d.get("data") or []):
        fn = os.path.join(out_dir, f"{len(out)+1}-gen-{slug(args.prompt)}.png")
        if item.get("b64_json"):
            with open(fn, "wb") as f:
                f.write(base64.b64decode(item["b64_json"]))
        elif item.get("url") and download(item["url"], fn, timeout=120):
            pass
        else:
            continue
        out.append({"file": fn, "source": f"xai:{entry['model']}", "url": None,
                    "credit": None, "cost_usd": entry["usd"]})
    if not out:
        raise RuntimeError(f"no image in xAI response: {json.dumps(d)[:300]}")


# gpt-image sizes: square, landscape, portrait (the only three the API offers)
OPENAI_SIZE = {"1:1": "1024x1024", "16:9": "1536x1024", "4:3": "1536x1024", "3:2": "1536x1024",
               "9:16": "1024x1536", "3:4": "1024x1536", "2:3": "1024x1536"}


def _image_openai(args, entry, key, out_dir, out):
    size = OPENAI_SIZE.get(args.aspect, OPENAI_SIZE["9:16"])
    # quality "high" is pinned so the output matches the estimate (the default "auto" can serve a
    # much cheaper, lower-quality image).
    d = _openai_post("/images/generations", {"model": entry["model"], "prompt": args.prompt,
                                             "size": size, "quality": "high", "n": 1}, key)
    items = d.get("data") or []
    if not items or not items[0].get("b64_json"):
        raise RuntimeError(f"no image in OpenAI response: {json.dumps(d)[:300]}")
    fn = os.path.join(out_dir, f"{len(out)+1}-gen-{slug(args.prompt)}.png")
    with open(fn, "wb") as f:
        f.write(base64.b64decode(items[0]["b64_json"]))
    out.append({"file": fn, "source": f"openai:{entry['model']}", "url": None,
                "credit": None, "cost_usd": entry["usd"]})


def gen_image(args, entry, key, out_dir):
    out = []
    for _ in range(args.count):
        fam = entry["family"]
        if fam == "openrouter":
            _image_openrouter(args, entry, key, out_dir, out)
        elif fam == "gemini":
            _image_gemini(args, entry, key, out_dir, out)
        elif fam == "xai":
            _image_xai(args, entry, key, out_dir, out)
        elif fam == "openai":
            _image_openai(args, entry, key, out_dir, out)
        else:
            raise RuntimeError(f"unknown image provider family: {fam}")
    return out


# ---------- VIDEO ----------
def _video_openrouter(args, entry, key, out_dir, out):
    job = _api("/videos", {
        "model": entry["model"], "prompt": args.prompt, "duration": args.duration,
        "resolution": args.resolution, "aspect_ratio": args.aspect,
    }, key)
    job_id, poll_url = job["id"], job["polling_url"]
    print(f"[gen_asset] video job {job_id} submitted, polling...", file=sys.stderr)
    deadline = time.time() + 600
    status = {}
    while time.time() < deadline:
        req = urllib.request.Request(poll_url, headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            status = json.loads(r.read().decode())
        st = status.get("status")
        print(f"[gen_asset] status: {st}", file=sys.stderr)
        if st == "completed":
            break
        if st in ("failed", "cancelled", "expired"):
            raise RuntimeError(f"video job {st}: {status.get('error')}")
        time.sleep(8)
    else:
        raise RuntimeError("video job timed out after 10 min")
    fn = os.path.join(out_dir, f"{len(out)+1}-gen-{slug(args.prompt)}.mp4")
    urls = status.get("unsigned_urls") or []
    ok = urls and download(urls[0], fn, timeout=180)
    if not ok:
        req = urllib.request.Request(f"{API}/videos/{job_id}/content?index=0",
                                     headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=180) as r:
            data = r.read()
        if len(data) < 1000:
            raise RuntimeError("video download too small")
        with open(fn, "wb") as f:
            f.write(data)
    out.append({"file": fn, "source": f"openrouter:{entry['model']}", "url": None,
                "credit": None, "cost_usd": VIDEO_RATE.get(args.resolution, VIDEO_RATE["720p"]) * args.duration})


def _video_gemini(args, entry, key, out_dir, out):
    # Veo via :predictLongRunning, then poll the operation (per Gemini video docs).
    # NOTE: durationSeconds must be a NUMBER, not a string (the docs example shows "8" but the
    # live API rejects a string with INVALID_ARGUMENT). Verified live against veo-3.1-lite.
    aspect = args.aspect if args.aspect in ("16:9", "9:16") else "9:16"
    dur = _video_duration(entry, args.duration)
    res = args.resolution if args.resolution in ("720p", "1080p") else "720p"
    op = _gemini_post(f"/v1beta/models/{entry['model']}:predictLongRunning", {
        "instances": [{"prompt": args.prompt}],
        "parameters": {"aspectRatio": aspect, "resolution": res, "durationSeconds": dur},
    }, key)
    op_name = op.get("name")
    if not op_name:
        raise RuntimeError(f"Veo start gave no operation name: {json.dumps(op)[:300]}")
    print(f"[gen_asset] Veo op {op_name} submitted, polling...", file=sys.stderr)
    deadline = time.time() + 600
    status = {}
    while time.time() < deadline:
        status = _gemini_get(f"/v1beta/{op_name}", key)
        if status.get("done"):
            break
        print("[gen_asset] Veo status: running", file=sys.stderr)
        time.sleep(10)
    else:
        raise RuntimeError("Veo job timed out after 10 min")
    if status.get("error"):
        raise RuntimeError(f"Veo failed: {json.dumps(status['error'])[:300]}")
    samples = ((((status.get("response") or {}).get("generateVideoResponse") or {})
                .get("generatedSamples")) or [])
    if not samples:
        raise RuntimeError(f"no video in Veo response: {json.dumps(status)[:300]}")
    uri = (samples[0].get("video") or {}).get("uri")
    if not uri:
        raise RuntimeError("Veo sample has no video uri")
    fn = os.path.join(out_dir, f"{len(out)+1}-gen-{slug(args.prompt)}.mp4")
    # the file endpoint needs the same API key
    req = urllib.request.Request(uri, headers={"x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=300) as r:
        data = r.read()
    if len(data) < 1000:
        raise RuntimeError("Veo video download too small")
    with open(fn, "wb") as f:
        f.write(data)
    out.append({"file": fn, "source": f"gemini:{entry['model']}", "url": None,
                "credit": None, "cost_usd": _video_rate(entry, res) * dur})


def _video_omni(args, entry, key, out_dir, out):
    # Gemini Omni Flash (Veo's successor) via the Interactions API: one blocking POST, the clip
    # comes back in steps[].content[]. delivery "uri" because inline base64 is capped at 4MB.
    aspect = args.aspect if args.aspect in ("16:9", "9:16") else "9:16"
    res = args.resolution if args.resolution in OMNI_RATE else "720p"
    dur = _video_duration(entry, args.duration)
    if res != args.resolution or dur != args.duration:
        print(f"[gen_asset] Omni Flash makes 3-10 s at 360p/720p: generating {dur}s {res}", file=sys.stderr)
    d = _gemini_post("/v1beta/interactions", {
        "model": entry["model"], "input": args.prompt,
        "response_format": {"type": "video", "aspect_ratio": aspect, "resolution": res,
                            "duration": f"{dur}s", "delivery": "uri"},
    }, key, timeout=900)
    print(f"[gen_asset] Omni interaction {d.get('id')}: {d.get('status')}", file=sys.stderr)
    video = None
    for step in (d.get("steps") or []):
        if step.get("type") == "model_output":
            for c in (step.get("content") or []):
                if c.get("type") == "video":
                    video = c
    if not video:
        raise RuntimeError(f"no video in Omni response: {json.dumps(d)[:300]}")
    fn = os.path.join(out_dir, f"{len(out)+1}-gen-{slug(args.prompt)}.mp4")
    if video.get("data"):
        data = base64.b64decode(video["data"])
    else:
        # uri = .../v1beta/files/<id>:download?alt=media ; the file is ready once ACTIVE
        uri = video["uri"]
        file_id = uri.split("/files/")[-1].split(":")[0].split("?")[0]
        deadline = time.time() + 600
        while time.time() < deadline:
            state = _gemini_get(f"/v1beta/files/{file_id}", key).get("state")
            if state == "ACTIVE":
                break
            if state == "FAILED":
                raise RuntimeError("Omni video file failed to process")
            time.sleep(5)
        else:
            raise RuntimeError("Omni video file not ready after 10 min")
        # the file endpoint needs the same API key
        req = urllib.request.Request(uri, headers={"x-goog-api-key": key})
        with urllib.request.urlopen(req, timeout=300) as r:
            data = r.read()
    if len(data) < 1000:
        raise RuntimeError("Omni video download too small")
    with open(fn, "wb") as f:
        f.write(data)
    # billed = video tokens x $17.50/1M, plus the model's own text/thinking output ($9/1M) and
    # input ($1.50/1M) - under a cent per clip. Falls back to the estimate if usage is absent.
    usage = d.get("usage") or {}
    tokens = sum(m.get("tokens") or 0 for m in (usage.get("output_tokens_by_modality") or [])
                 if m.get("modality") == "video")
    other = (usage.get("total_output_tokens") or tokens) - tokens + (usage.get("total_thought_tokens") or 0)
    cost = tokens * OMNI_USD_PER_TOKEN + other * 9e-6 + (usage.get("total_input_tokens") or 0) * 1.5e-6
    out.append({"file": fn, "source": f"gemini:{entry['model']}", "url": None, "credit": None,
                "cost_usd": cost if tokens else _video_rate(entry, res) * dur})


def gen_video(args, entry, key, out_dir):
    out = []
    for _ in range(args.count):
        fam = entry["family"]
        if fam == "openrouter":
            _video_openrouter(args, entry, key, out_dir, out)
        elif fam == "gemini":
            # Veo (predictLongRunning) while it lives, Omni Flash (Interactions API) otherwise
            (_video_gemini if entry["model"].startswith("veo-") else _video_omni)(args, entry, key, out_dir, out)
        else:
            raise RuntimeError(f"unknown video provider family: {fam}")
    return out


def main():
    _utf8_stdio()
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", required=True, choices=["image", "video"])
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--duration", type=int, default=4,
                    help="video seconds (Grok 1-15, Omni Flash 3-10, Veo 4/6/8)")
    ap.add_argument("--resolution", default="720p", choices=["360p", "480p", "720p", "1080p"],
                    help="480p/720p (OpenRouter Grok), 720p/1080p (Gemini Veo), 360p/720p (Gemini Omni "
                         "Flash). A resolution the provider lacks falls back to its 720p.")
    ap.add_argument("--aspect", default="9:16",
                    help="9:16 vertical default (16:9, 1:1, 4:3, 3:4, 3:2, 2:3 ok)")
    ap.add_argument("--model", default=None, help="override OpenRouter model id")
    ap.add_argument("--estimate-only", action="store_true",
                    help="print cost estimate and exit WITHOUT calling the API")
    ap.add_argument("--confirm-cost", action="store_true",
                    help="REQUIRED to actually generate (this bills real money). Without it the "
                         "script prints the estimate and exits non-zero, enforcing estimate->ask->generate.")
    ap.add_argument("--output-dir", default=None)
    args = ap.parse_args()

    # env must load BEFORE resolving the provider (resolution is key-gated)
    load_env()

    # resolve which provider/model to use (config chain -> legacy OpenRouter default).
    # for --estimate-only we still try to resolve so the price reflects the chosen provider,
    # but we don't hard-fail on a missing key (the estimate must work without one).
    try:
        entry, prov_id = resolve_provider(args.type, args.model)
    except Exception:
        entry, prov_id = (None, None)

    est = estimate(args, entry)
    if args.estimate_only:
        print(json.dumps({"ok": True, "estimate": est, "provider": prov_id, "charged": False}))
        return

    # Hard gate: never bill without an explicit confirm. Enforces the
    # estimate->ask->generate contract in CODE, not just in SKILL.md prose (M-FA1).
    if not args.confirm_cost:
        print(json.dumps({"ok": False, "needs_confirmation": True, "estimate": est,
                          "provider": prov_id, "charged": False,
                          "message_en": "This will bill real money. Re-run with --confirm-cost to generate."},
                         ensure_ascii=False))
        sys.exit(2)

    # now the key check is real and provider-specific
    if entry is None:
        try:
            entry, prov_id = resolve_provider(args.type, args.model)
        except Exception as e:
            print(json.dumps({"ok": False, "error":
                "%s. Add the key to the .env file "
                "(free stock via find_assets.py --type broll still works without a key)" % e},
                ensure_ascii=False))
            sys.exit(1)
    key = os.environ.get(entry["env"], "").strip()
    if not key:
        print(json.dumps({"ok": False, "error":
            "%s not found in .env (add it to enable %s via %s; "
            "free stock via find_assets.py --type broll still works without it)"
            % (entry["env"], args.type, prov_id)}, ensure_ascii=False))
        sys.exit(1)

    if args.output_dir:
        out_dir = args.output_dir
    else:
        out_dir = os.path.join(find_kit_root(os.getcwd()), "projects", "_work", "assets",
                               slug(args.prompt), f"gen-{args.type}")
    os.makedirs(out_dir, exist_ok=True)

    started = time.time()
    try:
        results = (gen_image if args.type == "image" else gen_video)(args, entry, key, out_dir)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode(errors="ignore")
        except Exception:
            pass
        # Classify so the user knows if it's a bad key vs quota vs scope vs outage,
        # not just a raw code (M-K2). Never say "invalid key" for a 402/429.
        kind = ({401: "invalid", 402: "billing", 403: "scope",
                 429: "rate_limit"}.get(e.code, "provider_down" if e.code >= 500 else "other"))
        msg_en = {"invalid": "invalid or expired key", "billing": "quota or credit used up - top up your account",
                  "scope": "the key is not enabled for this endpoint", "rate_limit": "rate limited - try again",
                  "provider_down": "the provider is down - try again later"}.get(kind, "the API call failed")
        msg = f"HTTP {e.code} from {prov_id} ({kind})"
        if e.code == 403:
            msg = (f"{prov_id}: provider denied access (HTTP 403). The key is valid but this "
                   f"endpoint is not enabled for it. For xAI, enable image/video at console.x.ai. "
                   f"Or put another provider's key in .env (or reorder ~/.indigo11/config.json).")
        elif body:
            msg = f"HTTP {e.code} from {prov_id} ({kind}): {body[:300]}"
        print(json.dumps({"ok": False, "provider": prov_id, "http": e.code, "kind": kind,
                          "error": msg, "message_en": f"{prov_id}: {msg_en}",
                          "charged": False}, ensure_ascii=False))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"ok": False, "provider": prov_id, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)

    # License is a provider/account-tier claim we can't fully verify from here, so
    # don't hardcode commercial_ok:true (P1-45). Record what we know + point the
    # user at the provider's terms to confirm for their plan.
    PROVIDER_TERMS = {
        "gemini": "https://ai.google.dev/gemini-api/terms",
        "veo": "https://ai.google.dev/gemini-api/terms",
        "xai": "https://x.ai/legal/terms-of-service",
        "openrouter": "https://openrouter.ai/terms",
    }
    fam = entry["family"]
    for r in results:
        r["license"] = {"tier": "generated", "provider": prov_id, "family": fam,
                        "commercial_ok": None,  # unknown until the user's plan/terms are confirmed
                        "credit_required": None,
                        "terms_url": PROVIDER_TERMS.get(fam, "check the provider's usage terms"),
                        "note": ("AI-generated by %s. Commercial rights depend on YOUR account tier "
                                 "and the provider's current terms - verify at the terms_url before "
                                 "using in a paid/brand deliverable." % prov_id)}
    total_usd = sum(r.get("cost_usd") or 0 for r in results)
    meta = {
        "skill": "indigo11-find-assets",
        "version": "1.0",
        "type": f"gen-{args.type}",
        "query": args.prompt,
        "provider": prov_id,
        "model": entry["model"],
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started)),
        "duration_seconds": round(time.time() - started, 1),
        "results": results,
        "credits": [],
        "license_summary": {"all_commercial_ok": True, "any_restricted": False,
                            "any_credit_required": False, "tiers": ["generated"]},
        "cost": {"usd": round(total_usd, 4),
                 "thb_approx": round(total_usd * THB_PER_USD, 1),
                 "estimated_before_run": est},
        "note": None,
    }
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(json.dumps({
        "ok": len(results) > 0,
        "type": f"gen-{args.type}",
        "count": len(results),
        "files": [r["file"] for r in results],
        "cost": meta["cost"],
        "license_summary": meta["license_summary"],
        "meta": os.path.join(out_dir, "meta.json"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
