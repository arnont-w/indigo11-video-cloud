#!/usr/bin/env python3
"""Find and download editing assets - SFX, music, memes/GIFs - from free online sources with fallback chains.

Why fallback chains
-------------------
No source has an SLA. ccMixter went fully down (HTTP 000) during testing while
Mixkit/Incompetech kept working - a single source WILL fail eventually, so every
asset type tries sources in order until one returns a usable file. All download
over plain HTTP with a browser User-Agent (no headless browser).

Sources (no key needed except Klipy, which is optional + user-registered):
  SFX : Mixkit (tag search) -> myinstants (scrape)
  music : Mixkit (tag search) -> Incompetech (direct) -> ccMixter (API)
  meme : Klipy (if KLIPY_API_KEY) -> Tenor -> Giphy -> Imgflip (static template)
  broll : Mixkit stock video (720p) -> Coverr (1080p) -> [paid: gen_asset.py]

License is NOT uniform - "free to download" != "free to reuse". Every result
carries a `license` dict in meta.json; `license_summary.any_restricted` flags
when the edit may need rights cleared:
  Mixkit = commercial OK, NO credit → primary for SFX+music
  Incompetech/ccMixter = CC-BY, commercial OK, credit REQUIRED (saved in credits[])
  myinstants/Klipy/Tenor/Giphy/Imgflip = restrictive ToS → warn before commercial use

Output (kit convention): projects/_work/assets/<query-slug>/<type>/<files> + meta.json

Usage:
  python3 find_assets.py --type sfx --query "swoosh" [--count 3] [--output-dir DIR]
  python3 find_assets.py --type music --query "upbeat corporate"
  python3 find_assets.py --type meme --query "mind blown" [--count 2]

The script is deterministic plumbing (HTTP + file I/O). WHICH asset fits WHICH
beat is an editor decision and stays with the calling agent - this just fetches
candidates and reports paths.
"""
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)
import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse


def _utf8_stdio():
    """Windows consoles default to a legacy code page (cp874/cp1252); printing the
    Thai/non-ASCII JSON these scripts emit (ensure_ascii=False) raises
    UnicodeEncodeError and crashes. Force UTF-8 on stdout/stderr. No-op on macOS/Linux
    (already UTF-8) and harmless if reconfigure isn't available."""
    if os.name != "nt":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def http_get(url, binary=False, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    return data if binary else data.decode("utf-8", "replace")


def download(url, path, timeout=40):
    """Download to path; return bytes written (0 on failure)."""
    try:
        data = http_get(url, binary=True, timeout=timeout)
        if len(data) < 500: # too small = probably an error page
            return 0
        with open(path, "wb") as f:
            f.write(data)
        return len(data)
    except Exception:
        return 0


def slug(s, n=30):
    return re.sub(r"[^a-zA-Z0-9ก-๙ ]", "", s).strip().replace(" ", "-")[:n] or "asset"


def _probe_audio(path):
    """ffprobe/ffmpeg a file for duration + loudness so the caller can pick by
    energy/length (M-SFX4). Returns {duration_s, mean_volume_db, max_volume_db}
    or {error}. Uses volumedetect (present in every ffmpeg) - a coarse energy
    proxy that's enough to compare candidates."""
    if not path or not os.path.exists(path):
        return {"error": "file missing"}
    out = {}
    try:
        import subprocess
        pr = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True)
        if pr.returncode == 0 and pr.stdout.strip():
            out["duration_s"] = round(float(pr.stdout.strip()), 3)
    except Exception:
        pass
    try:
        import subprocess
        vd = subprocess.run(
            ["ffmpeg", "-i", path, "-af", "volumedetect", "-f", "null", os.devnull],
            capture_output=True, text=True)
        for line in (vd.stderr or "").splitlines():
            m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", line)
            if m:
                out["mean_volume_db"] = float(m.group(1))
            m = re.search(r"max_volume:\s*(-?[\d.]+) dB", line)
            if m:
                out["max_volume_db"] = float(m.group(1))
    except Exception:
        pass
    return out or {"error": "probe failed"}


def find_kit_root(start):
    d = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(d, "CLAUDE.md")):
            return d
        p = os.path.dirname(d)
        if p == d:
            return os.path.abspath(start)
        d = p


def load_env():
    """Load KLIPY_API_KEY etc. from nearest .env (don't override real env)."""
    for parent in _parents(os.getcwd()):
        envp = os.path.join(parent, ".env")
        if os.path.exists(envp):
            for line in open(envp, encoding="utf-8", errors="ignore"):
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
            return


def _parents(p):
    p = os.path.abspath(p)
    while True:
        yield p
        np = os.path.dirname(p)
        if np == p:
            return
        p = np


# ---------------- SFX: Mixkit -> myinstants ----------------
def _mixkit_sfx(query, count, out_dir):
    out = []
    q = urllib.parse.quote(query.strip().replace(" ", "-"))
    html = http_get(f"https://mixkit.co/free-sound-effects/{q}/")
    urls = []
    for u in re.findall(r"https://assets\.mixkit\.co/active_storage/sfx/[0-9]+/[0-9]+-preview\.(?:mp3|wav)", html):
        if u not in urls:
            urls.append(u)
    for u in urls[:count]:
        fn = os.path.join(out_dir, f"{len(out)+1}-mixkit-{os.path.basename(u)}")
        if download(u, fn):
            out.append({"file": fn, "source": "mixkit", "url": u, "credit": None})
    return out


def _myinstants_sfx(query, count, out_dir):
    out = []
    html = http_get(f"https://www.myinstants.com/en/search/?name={urllib.parse.quote(query)}")
    seen = []
    for p in re.findall(r"/media/sounds/[^'\"]+\.mp3", html):
        if p in seen:
            continue
        seen.append(p)
        url = "https://www.myinstants.com" + p
        name = os.path.splitext(os.path.basename(p))[0]
        fn = os.path.join(out_dir, f"{len(out)+1}-{slug(name)}.mp3")
        if download(url, fn):
            out.append({"file": fn, "source": "myinstants", "url": url, "credit": None})
        if len(out) >= count:
            break
    return out


def fetch_sfx(query, count, out_dir):
    # Mixkit first (royalty-free, no credit), then myinstants (meme-sound stingers)
    notes = []
    for name, fn in [("mixkit", _mixkit_sfx), ("myinstants", _myinstants_sfx)]:
        try:
            r = fn(query, count, out_dir)
            if r:
                return r, ("; ".join(notes) or None)
            notes.append(f"{name} returned 0")
        except Exception as e:
            notes.append(f"{name} failed: {e}")
    return [], "; ".join(notes) or "all SFX sources failed"


# ---------------- music: Mixkit -> Incompetech -> ccMixter ----------------
# Mixkit is a real open-ended search (by mood tag) AND royalty-free with NO credit
# required, so it's the primary. Incompetech (CC-BY, credit) is the no-search
# fallback; ccMixter (CC-BY) is the last resort (it has gone fully down before).

# Incompetech has no search API - it serves /mp3-royaltyfree/<Title>.mp3 by exact
# name. A small curated pool keyed by mood is only used when Mixkit is unreachable.
INCOMPETECH = {
    "upbeat": ["Carefree", "Inspired"],
    "corporate": ["Inspired", "Carefree"],
    "calm": ["Reverie", "Inspired"],
    "default": ["Carefree", "Inspired"],
}


# A few mood synonyms → Mixkit tags, so "dark suspense" finds the mystery/thriller
# tag even though Mixkit has no "suspense" tag. Anything not mapped is matched
# directly against the live tag list (substring) before falling back.
MIXKIT_MOOD = {
    "suspense": "thriller", "tense": "thriller", "tension": "thriller",
    "dark": "mystery", "scary": "horror", "creepy": "horror",
    "happy": "comedy", "fun": "comedy", "energetic": "action", "energy": "action",
    "calm": "background", "chill": "cocktail-lounge", "sad": "drama",
    "epic": "epic-drama", "intense": "action", "upbeat": "corporate",
}


def _mixkit_tags():
    """Live list of valid Mixkit music tag slugs (so we never guess a 301 slug)."""
    html = http_get("https://mixkit.co/free-stock-music/")
    return sorted(set(re.findall(r"/free-stock-music/tag/([a-z0-9-]+)/", html)))


def _mixkit_music(query, count, out_dir):
    out = []
    words = re.findall(r"[a-z]+", query.lower())
    tags = _mixkit_tags()
    # pick a tag: mood synonym first, then any query word that IS a real tag
    chosen = None
    for w in words:
        if w in MIXKIT_MOOD and MIXKIT_MOOD[w] in tags:
            chosen = MIXKIT_MOOD[w]
            break
    if not chosen:
        for w in words:
            hit = [t for t in tags if w == t or w in t]
            if hit:
                chosen = hit[0]
                break
    candidates = []
    if chosen:
        candidates.append(f"https://mixkit.co/free-stock-music/tag/{chosen}/")
    candidates.append("https://mixkit.co/free-stock-music/") # last resort
    for url in candidates:
        try:
            html = http_get(url)
        except Exception:
            continue
        urls = []
        for u in re.findall(r"https://assets\.mixkit\.co/music/[0-9]+/[0-9]+\.mp3", html):
            if u not in urls:
                urls.append(u)
        for u in urls[:count]:
            fn = os.path.join(out_dir, f"{len(out)+1}-mixkit-{os.path.basename(u)}")
            if download(u, fn, timeout=60):
                out.append({"file": fn, "source": f"mixkit:{chosen or 'all'}",
                            "url": u, "credit": None})
        if out:
            return out
    return out


def _incompetech_music(query, count, out_dir):
    out = []
    q = query.lower()
    mood = next((k for k in INCOMPETECH if k != "default" and k in q), "default")
    for title in INCOMPETECH[mood]:
        if len(out) >= count:
            break
        url = f"https://incompetech.com/music/royalty-free/mp3-royaltyfree/{title}.mp3"
        fn = os.path.join(out_dir, f"{len(out)+1}-{title}.mp3")
        if download(url, fn, timeout=60):
            out.append({"file": fn, "source": "incompetech", "url": url,
                        "credit": f"Music: {title} by Kevin MacLeod (incompetech.com), CC-BY"})
    return out


def _ccmixter_music(query, count, out_dir):
    out = []
    api = (f"http://ccmixter.org/api/query?f=json&limit={count*2}"
           f"&tags=instrumental&sort=rank")
    data = json.loads(http_get(api))
    for t in data:
        if len(out) >= count:
            break
        files = t.get("files", [])
        url = files[0].get("download_url", "") if files else ""
        if not url:
            continue
        fn = os.path.join(out_dir, f"{len(out)+1}-{slug(t.get('upload_name','track'))}.mp3")
        if download(url, fn, timeout=60):
            out.append({"file": fn, "source": "ccmixter", "url": url,
                        "credit": f"Music: {t.get('upload_name')} by {t.get('user_name')} (ccMixter), CC-BY"})
    return out


def fetch_music(query, count, out_dir):
    notes = []
    for name, fn in [("mixkit", _mixkit_music),
                     ("incompetech", _incompetech_music),
                     ("ccmixter", _ccmixter_music)]:
        try:
            r = fn(query, count, out_dir)
            if r:
                return r, ("; ".join(notes) or None)
            notes.append(f"{name} returned 0")
        except Exception as e:
            notes.append(f"{name} failed: {e}")
    return [], "; ".join(notes) or "all music sources failed"


# ---------------- broll: Mixkit stock video -> Coverr ----------------
# Both keyless. Mixkit serves 720p previews (1080p/4k are behind login → 403);
# Coverr's search page embeds 1080p CDN URLs directly. 720p is plenty for a
# short-form insert that gets cropped/overlaid anyway. If neither has the shot,
# the PAID fallback is gen_asset.py (OpenRouter) - agent asks the user first.
def _mixkit_broll(query, count, out_dir):
    out = []
    q = urllib.parse.quote(query.strip().replace(" ", "-"))
    html = http_get(f"https://mixkit.co/free-stock-video/{q}/")
    ids = []
    for vid in re.findall(r"https://assets\.mixkit\.co/videos/([0-9]+)/[0-9]+-360\.mp4", html):
        if vid not in ids:
            ids.append(vid)
    for vid in ids[:count]:
        fn = os.path.join(out_dir, f"{len(out)+1}-mixkit-{vid}.mp4")
        # 720p first, 360p preview as fallback
        for res in ("720", "360"):
            if download(f"https://assets.mixkit.co/videos/{vid}/{vid}-{res}.mp4", fn, timeout=90):
                out.append({"file": fn, "source": "mixkit",
                            "url": f"https://assets.mixkit.co/videos/{vid}/{vid}-{res}.mp4",
                            "credit": None})
                break
    return out


def _coverr_broll(query, count, out_dir):
    out = []
    html = http_get(f"https://coverr.co/s?q={urllib.parse.quote(query)}")
    seen = []
    for u in re.findall(r"https://cdn\.coverr\.co/videos/([^\"/]+)/(?:1080p|720p|360p)\.mp4", html):
        # skip Coverr's watermarked temp uploads - they render with a logo overlay
        if u in seen or "watermark" in u.lower():
            continue
        seen.append(u)
    for slug_ in seen[:count]:
        fn = os.path.join(out_dir, f"{len(out)+1}-coverr-{slug(slug_)}.mp4")
        for res in ("1080p", "720p", "360p"):
            if download(f"https://cdn.coverr.co/videos/{slug_}/{res}.mp4", fn, timeout=90):
                # Coverr's current free license REQUIRES attribution (P1-46), and
                # does NOT clear every depicted trademark/property right. Surface
                # both so the caller doesn't treat it as no-credit like Mixkit.
                out.append({"file": fn, "source": "coverr",
                            "url": f"https://cdn.coverr.co/videos/{slug_}/{res}.mp4",
                            "landing_url": f"https://coverr.co/videos/{slug_}",
                            "credit": f"Video from Coverr (coverr.co/videos/{slug_})",
                            "license_note": ("Coverr free license: credit required; does not clear "
                                             "depicted logos/people/property - verify for brand use")})
                break
    return out


def _stock_key(name):
    return os.environ.get(name, "").strip()


def _api_get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _pexels_image(query, count, out_dir):
    key = _stock_key("PEXELS_API_KEY")
    if not key:
        raise RuntimeError("no PEXELS_API_KEY in .env (free, no card: pexels.com/api)")
    d = _api_get(f"https://api.pexels.com/v1/search?query={urllib.parse.quote(query)}&per_page={count}", {"Authorization": key})
    out = []
    for ph in d.get("photos", [])[:count]:
        u = ph["src"].get("large2x") or ph["src"].get("original")
        fn = os.path.join(out_dir, f"{len(out)+1}-pexels-{ph['id']}.jpg")
        if download(u, fn):
            out.append({"file": fn, "source": "pexels", "url": u, "landing_url": ph.get("url"),
                        "credit": None})
    return out


def _pixabay_image(query, count, out_dir):
    key = _stock_key("PIXABAY_API_KEY")
    if not key:
        raise RuntimeError("no PIXABAY_API_KEY in .env (free: pixabay.com/api/docs)")
    d = _api_get(f"https://pixabay.com/api/?key={key}&q={urllib.parse.quote(query)}&image_type=photo&per_page={max(3, count)}")
    out = []
    for h in d.get("hits", [])[:count]:
        u = h.get("largeImageURL") or h.get("webformatURL")
        fn = os.path.join(out_dir, f"{len(out)+1}-pixabay-{h['id']}.jpg")
        if download(u, fn):
            out.append({"file": fn, "source": "pixabay", "url": u, "landing_url": h.get("pageURL"), "credit": None})
    return out


def fetch_image(query, count, out_dir):
    """Step 2 for pictures: Pexels, then Pixabay (both free for commercial use, free key in .env)."""
    notes = []
    for name, fn in [("pexels", _pexels_image), ("pixabay", _pixabay_image)]:
        try:
            r = fn(query, count, out_dir)
            if r:
                return r, ("; ".join(notes) or None)
            notes.append(f"{name} returned 0")
        except Exception as e:
            notes.append(f"{name} failed: {e}")
    notes.append("free stock exhausted: step 3 = draw it yourself (not a real scene) or gen_asset.py (real photo needed; ask the user first, show the price)")
    return [], "; ".join(notes)


ICON_SETS = "lucide,tabler,ph,mdi,material-symbols"     # open-source sets: ISC / MIT / Apache-2.0, commercial OK, no credit needed in a video


def fetch_icon(query, count, out_dir, color="#FFFFFF", height=512):
    """Step 2 for icons: the Iconify open-source libraries (keyless). Saves SVG files (colour baked in, so they render the same everywhere)."""
    notes = []
    try:
        d = _api_get(f"https://api.iconify.design/search?query={urllib.parse.quote(query)}&limit={count * 2}&prefixes={ICON_SETS}")
        out = []
        for name in d.get("icons", []):
            pre, nm = name.split(":", 1)
            u = f"https://api.iconify.design/{pre}/{nm}.svg?height={height}&color={urllib.parse.quote(color)}"
            fn = os.path.join(out_dir, f"{len(out)+1}-{pre}-{nm}.svg")
            if download(u, fn):
                out.append({"file": fn, "source": f"iconify:{pre}", "url": u, "credit": None})
            if len(out) >= count:
                break
        if out:
            return out, None
        notes.append("iconify returned 0")
    except Exception as e:
        notes.append(f"iconify failed: {e}")
    notes.append("free icons exhausted: step 3 = DRAW it yourself as an SVG (simple shapes, free), or generate an image only if a real picture is needed (ask the user first, show the price)")
    return [], "; ".join(notes)


def fetch_font(query, count, out_dir, subset="latin"):
    """Step 2 for fonts: Fontsource (the Google Fonts and other open-source families, mostly SIL OFL: commercial OK). Saves .ttf files (400 and 700)."""
    notes = []
    try:
        fams = _api_get("https://api.fontsource.org/v1/fonts?family=" + urllib.parse.quote(query.strip()))
        if not fams:                      # try the id form: "Noto Sans Thai" -> noto-sans-thai
            try:
                fams = [_api_get("https://api.fontsource.org/v1/fonts/" + re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-"))]
            except Exception:
                fams = []
        out = []
        for fam in fams[:max(1, count)]:
            info = _api_get("https://api.fontsource.org/v1/fonts/" + fam["id"])
            sub = subset if subset in info.get("subsets", []) else info.get("defSubset", "latin")
            for w in (400, 700):
                v = (((info.get("variants") or {}).get(str(w)) or {}).get("normal") or {}).get(sub)
                if v and v["url"].get("ttf"):
                    fn = os.path.join(out_dir, f"{fam['id']}-{sub}-{w}.ttf")
                    if download(v["url"]["ttf"], fn):
                        out.append({"file": fn, "source": "fontsource", "url": v["url"]["ttf"], "credit": None,
                                    "license_note": f"{info.get('family')}: {info.get('license')}; subset {sub}"})
        if out:
            return out, "to keep a font: copy it into assets/fonts/<Family>/ and write its name in the Fonts section of assets/brand/BRAND.md"
        notes.append("fontsource: no family with that exact name (use the family name, for example 'Noto Sans Thai')")
    except Exception as e:
        notes.append(f"fontsource failed: {e}")
    notes.append("no free font found: step 3 = use the closest free font you already have (assets/fonts) and say so; step 4 = buying a paid font, only with the user's yes")
    return [], "; ".join(notes)


def fetch_broll(query, count, out_dir):
    notes = []
    for name, fn in [("mixkit", _mixkit_broll), ("coverr", _coverr_broll)]:
        try:
            r = fn(query, count, out_dir)
            if r:
                return r, ("; ".join(notes) or None)
            notes.append(f"{name} returned 0")
        except Exception as e:
            notes.append(f"{name} failed: {e}")
    notes.append("free stock exhausted - paid fallback: gen_asset.py (ask user first)")
    return [], "; ".join(notes)


# ---------------- meme: Klipy -> Tenor scrape -> Giphy scrape -> Imgflip ----------------
def _klipy(query, count, out_dir, key):
    out = []
    cid = "indigo11"
    url = (f"https://api.klipy.com/api/v1/{key}/gifs/search?"
           f"q={urllib.parse.quote(query)}&customer_id={cid}&per_page={count}")
    d = json.loads(http_get(url))
    for it in d["data"]["data"][:count]:
        g = it["file"]["hd"]["gif"]["url"]
        fn = os.path.join(out_dir, f"{len(out)+1}-{slug(it.get('title','meme'))}.gif")
        if download(g, fn):
            out.append({"file": fn, "source": "klipy", "url": g, "credit": None})
    return out


def _tenor_scrape(query, count, out_dir):
    out = []
    q = urllib.parse.quote(query.replace(" ", "-"))
    html = http_get(f"https://tenor.com/search/{q}-gifs")
    urls = []
    for u in re.findall(r"https://media[0-9]?\.tenor\.com/[^\"']+\.gif", html):
        if u not in urls:
            urls.append(u)
    for u in urls[:count]:
        fn = os.path.join(out_dir, f"{len(out)+1}-tenor.gif")
        if download(u, fn):
            out.append({"file": fn, "source": "tenor", "url": u, "credit": None})
    return out


def _giphy_scrape(query, count, out_dir):
    out = []
    html = http_get(f"https://giphy.com/search/{urllib.parse.quote(query.replace(' ', '-'))}")
    urls = []
    for u in re.findall(r"https://media[0-9]\.giphy\.com/media/[^\"']+/giphy\.gif", html):
        if u not in urls:
            urls.append(u)
    for u in urls[:count]:
        fn = os.path.join(out_dir, f"{len(out)+1}-giphy.gif")
        if download(u, fn):
            out.append({"file": fn, "source": "giphy", "url": u, "credit": None})
    return out


def _imgflip(query, count, out_dir):
    out = []
    d = json.loads(http_get("https://api.imgflip.com/get_memes"))
    memes = d["data"]["memes"]
    ql = query.lower()
    ranked = sorted(memes, key=lambda m: ql not in m["name"].lower())
    for m in ranked[:count]:
        ext = ".gif" if m["url"].endswith(".gif") else ".jpg"
        fn = os.path.join(out_dir, f"{len(out)+1}-{slug(m['name'])}{ext}")
        if download(m["url"], fn):
            # Imgflip returns STATIC meme TEMPLATES (blank jpg/png), not animated
            # reaction GIFs - last-resort fallback only; the caller should treat these
            # as a still image, not a looping GIF.
            out.append({"file": fn, "source": "imgflip-static-template",
                        "url": m["url"], "credit": None})
    return out


def fetch_meme(query, count, out_dir):
    key = os.environ.get("KLIPY_API_KEY", "").strip()
    chain = []
    if key:
        chain.append(("klipy", lambda: _klipy(query, count, out_dir, key)))
    chain += [
        ("tenor", lambda: _tenor_scrape(query, count, out_dir)),
        ("giphy", lambda: _giphy_scrape(query, count, out_dir)),
        ("imgflip", lambda: _imgflip(query, count, out_dir)),
    ]
    notes = []
    for name, fn in chain:
        try:
            r = fn()
            if r:
                if notes:
                    r[0].setdefault("_chain_note", "; ".join(notes))
                return r, ("; ".join(notes) or None)
            notes.append(f"{name} returned 0")
        except Exception as e:
            notes.append(f"{name} failed: {e}")
    return [], "; ".join(notes) or "all meme sources failed"


# Per-source license so downstream sees more than just `credit` - it can decide
# whether an asset is safe for a commercial/brand edit without re-reading the docs.
# commercial_ok=True, credit_required=False → drop in anywhere
# commercial_ok=True, credit_required=True → must credit (CC-BY)
# commercial_ok=False → restrictive ToS; warn before commercial use
LICENSE = {
    "local": ("own", True, False, "the user's own file - confirm they hold the rights if it is not their own footage"),
    "pexels": ("clean", True, False, "Pexels License - commercial OK, credit not required"),
    "pixabay": ("clean", True, False, "Pixabay Content License - commercial OK"),
    "fontsource": ("clean", True, False, "open-source font (Google Fonts / Fontsource, mostly SIL OFL 1.1) - commercial OK in videos, no credit; do not sell the font file itself"),
    "iconify": ("clean", True, False, "Iconify open-source sets (Lucide ISC, Tabler MIT, Phosphor MIT, Material Design Icons / Symbols Apache-2.0) - commercial OK, no credit needed in a video"),
    "mixkit": ("clean", True, False, "Mixkit License - commercial OK, no credit"),
    "coverr": ("clean", True, False, "Coverr License - commercial OK, no credit"),
    "incompetech": ("cc-by", True, True, "CC-BY - commercial OK, credit REQUIRED"),
    "ccmixter": ("cc-by", True, True, "CC-BY - commercial OK, credit REQUIRED"),
    "myinstants": ("restricted", False, False, "MyInstants ToS - reuse/commercial restricted; verify rights"),
    "klipy": ("restricted", False, False, "Klipy ToS - meme content; REQUIRES a visible 'Powered by Klipy' attribution when displaying results, and a test key is capped at 100 calls/hr (production key = unlimited). Verify commercial rights."),
    "tenor": ("restricted", False, False, "Tenor ToS - meme content; verify commercial rights"),
    "giphy": ("restricted", False, False, "GIPHY ToS - meme content; verify commercial rights"),
    "imgflip-static-template": ("restricted", False, False, "Imgflip - static meme template; verify rights"),
}


def license_of(source):
    base = source.split(":", 1)[0] # "mixkit:corporate" -> "mixkit"
    tier, comm, cred, note = LICENSE.get(base, ("unknown", False, False, "unknown source - verify rights"))
    return {"tier": tier, "commercial_ok": comm, "credit_required": cred, "note": note}


def main():
    _utf8_stdio()
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", required=True, choices=["sfx", "music", "meme", "broll", "image", "icon", "font"])
    ap.add_argument("--subset", default="latin", help="font subset (type font only): latin (default) or thai")
    ap.add_argument("--color", default="#FFFFFF", help="icon colour (type icon only), default white")
    ap.add_argument("--skip-local", action="store_true", help="do not look in the user's own folders first")
    ap.add_argument("--query", required=True)
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--inspect", action="store_true",
                    help="ffprobe each result for duration + loudness (LUFS-ish mean/peak) so "
                         "the agent can pick the SFX/track that fits the beat by ENERGY and "
                         "DURATION, not just tag order. Adds a `probe` field to each result.")
    ap.add_argument("--output-dir", default=None,
                    help="Exact directory to save into. If omitted, defaults to "
                         "<starter>/projects/_work/assets/<query-slug>/<type>/.")
    args = ap.parse_args()

    load_env()
    # Warn when the query is non-Latin: Mixkit/myinstants/Incompetech index in
    # English, so a Thai query almost always misses (M-SFX2). The agent should
    # translate the beat to an English noun+mood before searching. We don't
    # auto-translate (that needs an LLM), but we surface the miss loudly.
    non_latin_warning = None
    if re.search(r"[฀-๿]", args.query):  # Thai block
        non_latin_warning = ("query contains Thai characters - stock sources index in ENGLISH, "
                             "so this will likely miss. Translate the beat to an English keyword "
                             "(e.g. 'ระเบิด' -> 'explosion', 'เสียงกดปุ่ม' -> 'ui click') and retry.")
        print(f"[find-assets] WARNING: {non_latin_warning}", file=sys.stderr)

    # Only the DEFAULT path appends /<type>; an explicit --output-dir is used verbatim
    # (so 'save to /path/x' lands in /path/x, not /path/x/<type>).
    if args.output_dir:
        out_dir = args.output_dir
    else:
        out_dir = os.path.join(find_kit_root(os.getcwd()), "projects", "_work", "assets",
                               slug(args.query), args.type)
    os.makedirs(out_dir, exist_ok=True)

    started = time.time()
    fn = {"sfx": fetch_sfx, "music": fetch_music, "meme": fetch_meme,
          "broll": fetch_broll, "image": fetch_image,
          "icon": lambda q, c, o: fetch_icon(q, c, o, args.color),
          "font": lambda q, c, o: fetch_font(q, c, o, args.subset)}[args.type]
    results, note = [], None
    if args.type in ("broll", "image", "icon", "music", "sfx", "font") and not args.skip_local:
        # STEP 1 of the search order: the user's own files, before any website
        from find_local import find_local
        loc = find_local({"broll": "video"}.get(args.type, args.type), args.query, args.count)
        if loc:
            results = [{"file": h["file"], "source": "local", "url": None, "credit": None} for h in loc]
            note = "found in the user's own files (step 1); nothing downloaded"
    if not results:
        results, note = fn(args.query, args.count, out_dir)

    # attach per-result license so downstream/meta carries it
    for r in results:
        r["license"] = license_of(r["source"])

    # Optional audition: probe each file so the agent can select by energy/duration.
    if args.inspect:
        for r in results:
            r["probe"] = _probe_audio(r.get("file"))
    any_restricted = any(not r["license"]["commercial_ok"] for r in results)
    any_credit_required = any(r["license"]["credit_required"] for r in results)

    meta = {
        "skill": "indigo11-find-assets",
        "version": "1.0",
        "type": args.type,
        "query": args.query,
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started)),
        "duration_seconds": round(time.time() - started, 1),
        "results": results,
        "credits": [r["credit"] for r in results if r.get("credit")],
        "license_summary": {
            "all_commercial_ok": not any_restricted,
            "any_restricted": any_restricted,
            "any_credit_required": any_credit_required,
            "tiers": sorted({r["license"]["tier"] for r in results}),
        },
        "note": note,
        "query_warning": non_latin_warning,
    }
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "ok": len(results) > 0,
        "type": args.type,
        "count": len(results),
        "query_warning": non_latin_warning,
        "probes": [r.get("probe") for r in results] if args.inspect else None,
        "files": [r["file"] for r in results],
        "sources": list({r["source"] for r in results}),
        "credits": meta["credits"],
        "license_summary": meta["license_summary"],
        "note": note,
        "meta": os.path.join(out_dir, "meta.json"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
