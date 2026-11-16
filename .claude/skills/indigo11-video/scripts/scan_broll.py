#!/usr/bin/env python3
"""indigo11-video: search, download and build ONE contact sheet of B-roll clips so a person can pick by eye (run from the project folder).

  # 1. search + download + sheet in one command (the topic is the keyword)   P = projects/<date>-<slug>
  python3 .claude/skills/indigo11-video/scripts/scan_broll.py --q "beer" --out P/assets/broll

  # 2. open P/assets/broll/02-frames/_sheets/all.jpg: ONE sheet is all you need.
  #    Red frame = caption zone (a dark shade covers it at render); text inside the frame passes.
  #    Do not run verify.py on the raw clips again: the sheet is already made.

  # 3. cut only the ranges you picked
  python3 .claude/skills/indigo11-video/scripts/scan_broll.py --cut P/assets/broll --pick "c0:5-12,c2:3-9"

Sources (--source, default auto):
  auto     = pexels if PEXELS_API_KEY is in .env, else pixabay if PIXABAY_API_KEY is, else youtube (warning: never post)
  pexels / pixabay = stock libraries, free for commercial use, may be posted (free key, no card)
  youtube  = real footage/brands/events, but other people's copyright: research and prototypes only, **never post**
Every run writes _source.json (source + whether it may be posted) so make_short.py can warn, and adds source rows to SOURCES.md.
It stays fast with 2 parallel lanes (more triggers rate limits) and one sheet.
"""
import argparse, concurrent.futures as cf, datetime, importlib.util, json, os, pathlib, random, re, shutil, subprocess, sys, threading, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _starter import ROOT, PY, _env  # noqa: E402

_env.needs("PIL")   # verify.py (contact sheet labels) runs with this same Python


def _yt_cmd():
    """Newest yt-dlp: the project's own Python, then ~/.ytdlp-venv, then a system yt-dlp."""
    if importlib.util.find_spec("yt_dlp"):
        return [sys.executable, "-m", "yt_dlp"]
    for c in (pathlib.Path.home() / ".ytdlp-venv" / ("Scripts/yt-dlp.exe" if _env.WIN else "bin/yt-dlp"),):
        if c.exists():
            return [str(c)]
    w = shutil.which("yt-dlp")
    return [w] if w else None


YT = _yt_cmd()
_BROWSER = os.environ.get("YTDLP_BROWSER", "").strip()
COOKIES = ["--cookies-from-browser", _BROWSER] if _BROWSER else []

NET = int(os.environ.get("YTDLP_JOBS", "2"))
GAP = float(os.environ.get("YTDLP_GAP", "1.5"))
RETRY = 3

_net_sem = threading.Semaphore(NET)
_gate = threading.Lock()
_last = [0.0]

THROTTLE = ["--sleep-requests", "1", "--min-sleep-interval", "1",
            "--max-sleep-interval", "3", "--retries", "3",
            "--retry-sleep", "linear=1::5"]

_BLOCK = ("not a bot", "429", "too many requests", "rate", "forbidden")


def _pace():
    """Force every request to be at least GAP seconds apart (counted across all threads)."""
    with _gate:
        wait = GAP - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()


def sh_yt(args, t=900):
    """Call yt-dlp with pacing and back-off when blocked. Every YouTube request must go through this function; never call sh([YT,...]) directly."""
    for attempt in range(RETRY):
        with _net_sem:
            _pace()
            r = sh([*YT, *COOKIES, *THROTTLE, *args], t=t)
        out = (r.stdout + r.stderr).lower()
        if r.returncode == 0 or not any(b in out for b in _BLOCK):
            return r
        if attempt < RETRY - 1:
            back = (2 ** attempt) * 5 + random.uniform(0, 3)
            print(f"  [!] rate limited: waiting {back:.0f}s, then retrying "
                  f"({attempt + 2}/{RETRY})")
            time.sleep(back)
    if "not a bot" in out and not COOKIES:
        print("  [!] YouTube asks to confirm you are not a bot: set YTDLP_BROWSER=chrome and retry")
    return r
STEP = 3.0
THUMB = 200


def sh(cmd, t=900):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=t, encoding="utf-8", errors="replace")


def dur(p):
    r = sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "csv=p=0", str(p)])
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0




BAD_TITLE = ("facts", "explained", "reaction", "reacts", "storytime",
             "story time", "top 10", "top10", "did you know", "tutorial",
             "vs ", "review", "asmr eating", "podcast", "interview",
             "ranking", "compilation", "moments", "best of", "funniest",
             "top 5", "top5", "countdown", "tier list", "tierlist")


def title_ok(t):
    """False = the title hints at full-screen captions, so skip it without downloading."""
    return not any(b in (t or "").lower() for b in BAD_TITLE)


#
#
INNERTUBE = "https://www.youtube.com/youtubei/v1/search?prettyPrint=false"
SP_SHORT = "EgIYAQ%3D%3D"          # the web page's "short duration" filter


def innertube_shorts(q, n=30):
    """Search Shorts the way a person does on YouTube: gives videoId and the real title."""
    import urllib.request
    body = json.dumps({
        "context": {"client": {"clientName": "WEB",
                               "clientVersion": "2.20240101.00.00"}},
        "query": q, "params": SP_SHORT}).encode()
    req = urllib.request.Request(INNERTUBE, data=body, headers={
        "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
    except Exception as e:
        print(f"  [!] InnerTube search failed ({type(e).__name__}); falling back to ytsearch")
        return []

    out = []

    def walk(o):
        if isinstance(o, dict):
            if "shortsLockupViewModel" in o:
                v = o["shortsLockupViewModel"]
                vid = (v.get("onTap", {}).get("innertubeCommand", {})
                        .get("reelWatchEndpoint", {}).get("videoId"))
                t = v.get("overlayMetadata", {}).get(
                    "primaryText", {}).get("content") or ""
                if vid:
                    out.append((vid, t))
            if "reelItemRenderer" in o:
                v = o["reelItemRenderer"]
                if v.get("videoId"):
                    out.append((v["videoId"],
                                (v.get("headline") or {}).get("simpleText") or ""))
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(d)
    seen, uniq = set(), []
    for vid, t in out:
        if vid not in seen:
            seen.add(vid)
            uniq.append({"id": vid, "vertical": True, "views": 0,
                         "dur": 0, "t": t[:60]})
    return uniq[:n]


def search_meta(q, n=24, allow_landscape=False, landscape_only=False):
    """Search and get metadata in one call (about 30 times faster than one clip at a time).

    `ytsearch:` is the main route, same as typing a search on YouTube. The hashtag route used to be main
    (to get portrait clips), but it returns duration=None and title=None often, so clips cannot be filtered
    before download, and tags attract unrelated clips. `ytsearch` gives full duration and title and reaches
    BBC Earth / Nat Geo. Hashtag pages are now only a top-up when the search finds too few. Also returns view_count."""
    if not landscape_only:
        sh = innertube_shorts(q, n)
        if sh:
            print(f"  Shorts from web-style search '{q}': {len(sh)} clips")
            return sh

    urls = [f"ytsearch{n}:{q}"]

    out = []
    for u in urls:
        r = sh_yt([u, "--flat-playlist", "--playlist-end", str(n),
                   "-J", "--no-warnings"], t=300)
        try:
            d = json.loads(r.stdout)
        except Exception:
            continue
        for e in d.get("entries") or []:
            vid, url = e.get("id"), e.get("url") or ""
            if not vid:
                continue
            out.append({"id": vid,
                        "vertical": "/shorts/" in url,
                        "views": e.get("view_count") or 0,
                        "dur": e.get("duration") or 0,
                        "t": (e.get("title") or "")[:60]})
    seen, uniq = set(), []
    for m in out:
        if m["id"] not in seen:
            seen.add(m["id"])
            uniq.append(m)
    return uniq


#
#
PEXELS_KEY = _env.key("PEXELS_API_KEY")
PEXELS_API = "https://api.pexels.com/v1/videos/search"
UA = "indigo11-video/1.0"


def pexels_search(q, n=30, max_dur=30, vertical=False):
    """Search Pexels; returns metadata in the same shape as search_meta() so the same pipeline works.

    Needs PEXELS_API_KEY (free, no card: pexels.com/api). Limit 25,000 requests a month.
    vertical=False by default because the render crops the centre to 9:16; forcing portrait throws good clips away."""
    import urllib.request, urllib.parse, urllib.error
    p = {"query": q, "per_page": min(n, 80)}
    if vertical:
        p["orientation"] = "portrait"
    req = urllib.request.Request(f"{PEXELS_API}?{urllib.parse.urlencode(p)}",
                                 headers={"Authorization": PEXELS_KEY,
                                          "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            left = r.headers.get("x-ratelimit-remaining")
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        msg = {401: "PEXELS_API_KEY is wrong or expired",
               429: "Pexels rate limit exceeded: wait and retry"}.get(
                   e.code, f"Pexels error {e.code}")
        print(f"  [!] {msg}")
        return []
    except Exception as e:
        print(f"  [!] cannot reach Pexels: {e}")
        return []

    out = []
    for v in d.get("videos", []):
        dur_s = v.get("duration") or 0
        if max_dur and dur_s > max_dur:
            continue
        best = None
        for f in v.get("video_files", []):
            fw, fh = f.get("width") or 0, f.get("height") or 0
            if not fw or not fh:
                continue
            if vertical and fh <= fw:
                continue
            s = max(1080 / fw, 1920 / fh)
            if s > 1.34:
                continue
            px = fw * fh
            if best is None or px < best[3]:
                best = (f.get("link"), fh, fw, px)
        if not best:
            files = [f for f in v.get("video_files", []) if f.get("link")]
            if not files:
                continue
            f = max(files, key=lambda x: (x.get("width") or 0) * (x.get("height") or 0))
            best = (f["link"], f.get("height") or 0, f.get("width") or 0, 0)
        out.append({"id": f"px{v['id']}", "url": best[0],
                    "vertical": best[1] > best[2], "views": 0,
                    "dur": dur_s, "t": (v.get("alt") or q)[:60],
                    "page": v.get("url") or "",
                    "by": (v.get("user") or {}).get("name", "")})
    if out:
        print(f"  Pexels '{q}': {len(out)} clips (quota left {left})")
    return out


PIXABAY_KEY = _env.key("PIXABAY_API_KEY")
PIXABAY_API = "https://pixabay.com/api/videos/"


def pixabay_search(q, n=30, max_dur=30, vertical=False):
    """Search Pixabay; returns metadata in the same shape as pexels_search().

    Pixabay's video API has no orientation parameter; only about 6% of results are portrait, which is fine
    because the render crops the centre. Limit 100 requests per 60 seconds."""
    import urllib.request, urllib.parse, urllib.error
    p = {"key": PIXABAY_KEY, "q": q, "per_page": max(3, min(n, 200))}
    req = urllib.request.Request(f"{PIXABAY_API}?{urllib.parse.urlencode(p)}",
                                 headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            left = r.headers.get("x-ratelimit-remaining")
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        msg = {400: "Pixabay key is wrong or a parameter is invalid",
               429: "Pixabay rate limit exceeded (100/minute): wait and retry"}.get(
                   e.code, f"Pixabay error {e.code}")
        print(f"  [!] {msg}")
        return []
    except Exception as e:
        print(f"  [!] cannot reach Pixabay: {e}")
        return []

    out = []
    for v in d.get("hits", []):
        dur_s = v.get("duration") or 0
        if max_dur and dur_s > max_dur:
            continue
        best = None
        for k, f in (v.get("videos") or {}).items():
            url = f.get("url") or ""
            fw, fh = f.get("width") or 0, f.get("height") or 0
            if not url or not fw or not fh:
                continue
            if vertical and fh <= fw:
                continue
            s = max(1080 / fw, 1920 / fh)
            if s > 1.34:
                continue
            px = fw * fh
            if best is None or px < best[3]:
                best = (url, fh, fw, px)
        if not best:
            continue
        out.append({"id": f"pb{v['id']}", "url": best[0],
                    "vertical": best[1] > best[2], "views": v.get("views") or 0,
                    "dur": dur_s, "t": (v.get("tags") or q)[:60],
                    "page": v.get("pageURL") or "",
                    "by": v.get("user", "")})
    out.sort(key=lambda m: -m["views"])
    if out:
        print(f"  Pixabay '{q}': {len(out)} clips (quota left {left})")
    return out


def stock_download(m, outdir):
    """Download a clip from a stock library: no pacing needed and no YouTube-style rate limit."""
    import urllib.request, shutil as _sh
    dest = outdir / f"{m['id']}.mp4"
    try:
        req = urllib.request.Request(m["url"], headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as r, \
                open(dest, "wb") as f:
            _sh.copyfileobj(r, f)
        return dest if dest.exists() and dest.stat().st_size > 0 else None
    except Exception as e:
        print(f"  [!] could not download {m['id']}: {e}")
        return None


def download(vid, outdir, dur_s=0, want=30.0):
    """Download a clip. For long clips only a middle section is fetched (documentaries run 40 minutes but we use 3-6 s),
    via `--download-sections`, starting at about 20% because openings are often intros or logos."""
    args = [f"https://youtu.be/{vid}", "-f", "bv*[height<=1080]/b",
            "--no-warnings", "--no-playlist"]
    if dur_s and dur_s > want * 1.5:
        ss = min(max(dur_s * 0.2, 5), dur_s - want - 1)
        args += ["--download-sections", f"*{ss:.0f}-{ss + want:.0f}",
                 "--force-keyframes-at-cuts"]
    args += ["-o", str(outdir / f"{vid}.%(ext)s")]
    r = sh_yt(args)
    return vid if r.returncode == 0 else None


def frames_of(clip, scan_dir, ci):
    """Extract the frames of a whole clip in a single ffmpeg pass."""
    d = dur(clip)
    if d < 4:
        return []
    pat = str(scan_dir / f"c{ci}_%03d.jpg")
    sh(["ffmpeg", "-v", "error", "-i", str(clip),
        "-vf", f"fps=1/{STEP},scale={THUMB}:-1", "-vsync", "0", pat, "-y"], t=600)
    out = []
    for i, f in enumerate(sorted(scan_dir.glob(f"c{ci}_*.jpg"))):
        out.append({"t": round(2.0 + i * STEP, 1), "img": str(f)})
    return out


def subzone_ink(clip):
    """Measure line density in the caption zone (y 60-78%) to guess whether text is present.
    Use it only to ORDER the clips for viewing, never to reject them automatically. About 0.04 s per clip.
    The fps=1/3 filter must come BEFORE the crop so samples spread across the whole clip."""
    r = sh(["ffmpeg", "-v", "error", "-i", str(clip),
            "-vf", "fps=1/3,crop=iw:ih*0.18:0:ih*0.60,edgedetect=low=0.1:high=0.3,"
                   "signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-",
            "-frames:v", "6", "-f", "null", "-"], t=120)
    vals = [float(x) for x in re.findall(r"YAVG=([\d.]+)", r.stdout + r.stderr)]
    return round(sum(vals) / len(vals), 1) if vals else 0.0


def one_sheet(clip_dir, dest):
    """One contact sheet for all clips, made with verify.py (14 columns, seconds on every frame).
    The frame rate adapts to the total length so the result stays one sheet (verify.py starts a new sheet every 140 frames)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    for old in dest.parent.glob(f"{dest.stem}*.jpg"):
        old.unlink()

    clips = [p for p in pathlib.Path(clip_dir).iterdir()
             if p.suffix.lower() in (".mp4", ".webm", ".mkv")]
    total = sum(dur(c) for c in clips) or 1
    fps = min(1.0, 130 / total)
    fps = max(fps, 0.15)

    r = sh([sys.executable, str(pathlib.Path(__file__).parent / "verify.py"),
            str(clip_dir), "--fps", f"{fps:.3f}", "-o", str(dest), "--zone"], t=900)
    print(r.stdout.strip()[-400:] if r.stdout else r.stderr[-300:])
    return sorted(dest.parent.glob(f"{dest.stem}*.jpg"))


LICENCE = {
    "pexels": "Pexels License: free, commercial use OK, credit not required (https://www.pexels.com/license/)",
    "pixabay": "Pixabay Content License: free, commercial use OK (https://pixabay.com/service/license-summary/)",
    "youtube": "YouTube: the uploader's copyright. Research/reference only, NOT publishable",
}


def record_sources(root, source, metas, files):
    """Write _source.json (read by make_short.py), SOURCES.md in the B-roll folder, and rows in the Files table of <P>/assets/SOURCES.md."""
    publishable = source in ("pexels", "pixabay")
    (root / "_source.json").write_text(json.dumps(
        {"source": source, "publishable": publishable, "date": f"{datetime.date.today().year + 543}-{datetime.date.today():%m-%d}",
         "note": "" if publishable else "YouTube = research and prototypes only, never post"}, ensure_ascii=False, indent=1), encoding="utf-8")
    byid = {m["id"]: m for m in metas}
    rows = []
    for f in files:
        m = byid.get(f.stem, {})
        url = m.get("page") or (f"https://youtu.be/{f.stem}" if source == "youtube" else m.get("url", ""))
        what = ("B-roll: " + (m.get("t") or "")).replace("|", "/").strip()
        rows.append(f"| {f.name} | {what} | {url} | {LICENCE[source]} | {(m.get('by') or '-').replace('|', '/')} |")
    head = "# B-roll sources\n\n| file | what | source URL | licence | on-screen credit |\n|---|---|---|---|---|\n"
    (root / "SOURCES.md").write_text(head + "\n".join(rows) + "\n", encoding="utf-8")
    proj_src = root.parent / "SOURCES.md"      # <P>/assets/SOURCES.md when --out is <P>/assets/broll
    if proj_src.exists() and rows:
        txt = proj_src.read_text(encoding="utf-8")
        new = [r for r, f in zip(rows, files) if f.name not in txt]
        if new:
            proj_src.write_text(txt.rstrip("\n") + "\n" + "\n".join(new) + "\n", encoding="utf-8")
            print(f"  added {len(new)} rows to {proj_src}")
    if not publishable:
        print("\n[!] YouTube clips belong to other people: research and prototypes only, never post them."
              "\n    For a video you will post: add PEXELS_API_KEY or PIXABAY_API_KEY (both free) to .env and rerun with --source pexels")


def cmd_scan(a):
    root = pathlib.Path(a.out)
    clips  = root / "01-raw-clips"
    scan   = root / "02-frames"
    sheets = root / "02-frames" / "_sheets"
    for d in (clips, scan, sheets):
        d.mkdir(parents=True, exist_ok=True)

    def gather(landscape_only=False):
        with cf.ThreadPoolExecutor(NET) as ex:
            lists = list(ex.map(
                lambda q: search_meta(q, a.per_query,
                                      landscape_only=landscape_only), a.q))
        seen, out = set(), []
        for L in lists:
            for m in L:
                if m["id"] not in seen:
                    seen.add(m["id"])
                    out.append(m)
        return out

    def usable(metas):
        """Filter before download: free, because metadata comes with the search results."""
        out, drop_t, drop_d = [], 0, 0
        for m in metas:
            if not title_ok(m["t"]):
                drop_t += 1
                continue
            out.append(m)
        if drop_t or drop_d:
            print(f"  filtered before download: titles hinting at full-screen captions {drop_t} / too long "
                  f"{a.max_dur:.0f}s {drop_d}")
        return out

    if a.source in ("pexels", "pixabay"):
        if a.source == "pexels":
            if not PEXELS_KEY:
                sys.exit("PEXELS_API_KEY is required first (free, no card: https://www.pexels.com/api/)\n"
                         "  Open the .env file in the project folder and add PEXELS_API_KEY=...")
            finder, label, site = pexels_search, "Pexels", "https://www.pexels.com"
        else:
            if not PIXABAY_KEY:
                sys.exit("PIXABAY_API_KEY is required first (free: https://pixabay.com/api/docs/)\n"
                         "  Open the .env file in the project folder and add PIXABAY_API_KEY=...")
            finder, label, site = pixabay_search, "Pixabay", "https://pixabay.com"
        metas, seen = [], set()
        for q in a.q:
            for m in finder(q, a.per_query, a.max_dur):
                if m["id"] not in seen:
                    seen.add(m["id"])
                    metas.append(m)
        if not metas:
            sys.exit(f"{label} returned no usable clips: try a broader keyword")
        good = metas[:a.n]
        print(f"{label}: {len(good)}/{len(metas)} usable clips, downloading")
        with cf.ThreadPoolExecutor(4) as ex:
            oks = [p for p in ex.map(lambda m: stock_download(m, clips), good) if p]
        print(f"downloaded {len(oks)} clips")
        cred = [f"{m['id']}: {m['by']}" for m in good if m.get("by")]
        if cred:
            (root / "CREDITS.txt").write_text(
                f"B-roll from {label} (free for commercial use)\n"
                f"{site}\n\n" + "\n".join(cred) + "\n",
                encoding="utf-8")
            print(f"  credits written to {root}/CREDITS.txt")
        files = sorted(clips.glob("*.mp4"))
        record_sources(root, a.source, good, files)
        index = {}
        with cf.ThreadPoolExecutor(6) as ex:
            futs = {ex.submit(frames_of, f, scan, ci): (ci, f)
                    for ci, f in enumerate(files)}
            for fut in cf.as_completed(futs):
                ci, f = futs[fut]
                index[f"c{ci}"] = {"file": str(f), "frames": fut.result()}
        for key, v in index.items():
            v["ink"] = subzone_ink(v["file"])
        for key, v in sorted(index.items(), key=lambda kv: kv[1]["ink"]):
            print(f"  {key} = {pathlib.Path(v['file']).name[:28]:30s} "
                  f"{len(v['frames']):2d} frames  caption-zone ink={v['ink']:5.1f}")
        (root / "index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
        made = one_sheet(clips, sheets / "all.jpg")
        print("\nOpen the single contact sheet:")
        for m in made:
            print(f"  {m}")
        print(f"When chosen, run: {PY} .claude/skills/indigo11-video/scripts/scan_broll.py --cut {root} --pick \"c0:5-60,...\"")
        return

    metas = gather()
    good = sorted(usable(metas), key=lambda m: (not m["vertical"], -m["views"]))
    nv = sum(1 for m in good if m["vertical"])
    print(f"found {len(good)}/{len(metas)} clips ({nv} portrait first, "
          f"the rest are cropped)")

    if len(good) < a.n and not a.shorts_only:
        print(f"  fewer than {a.n}: adding clips from hashtag pages")
        seen = {m["id"] for m in good}
        good += [m for m in sorted(usable(gather(landscape_only=True)),
                                   key=lambda m: -m["views"])
                 if m["id"] not in seen]

    good = good[:int(a.n * 1.4) if a.max_dur else a.n]
    print(f"{len(good)} clips in total, downloading")

    with cf.ThreadPoolExecutor(NET) as ex:
        oks = [v for v in ex.map(
            lambda m: download(m["id"], clips, m.get("dur") or 0,
                               a.max_dur or 30), good) if v]
    files = sorted([p for p in clips.iterdir()
                    if p.suffix.lower() in (".mp4", ".webm", ".mkv")])

    #
    if a.max_dur:
        limit = a.max_dur * 1.5
        keep, cut = [], 0
        for p in files:
            if dur(p) > limit:
                p.unlink()
                cut += 1
            else:
                keep.append(p)
        if cut:
            print(f"  dropped {cut} clips longer than {limit:.0f}s")
        files = keep
    print(f"downloaded {len(files)} clips, extracting frames")
    record_sources(root, "youtube", good, files)

    index = {}
    with cf.ThreadPoolExecutor(6) as ex:
        futs = {ex.submit(frames_of, f, scan, ci): (ci, f)
                for ci, f in enumerate(files)}
        for fut in cf.as_completed(futs):
            ci, f = futs[fut]
            index[f"c{ci}"] = {"file": str(f), "frames": fut.result()}

    for key, v in index.items():
        v["ink"] = subzone_ink(v["file"])
    for key, v in sorted(index.items(), key=lambda kv: kv[1]["ink"]):
        flag = "text?" if v["ink"] > 12 else "clean"
        print(f"  {key} = {pathlib.Path(v['file']).name[:28]:30s} "
              f"{len(v['frames']):2d} frames  caption-zone ink={v['ink']:5.1f} {flag}")

    (root / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")

    made = one_sheet(clips, sheets / "all.jpg")
    print(f"\nOpen the single contact sheet:")
    for m in made:
        print(f"  {m}")
    print("\nReject only two things: a watermark or big text mid-screen | footage too dark or blurred to read")
    print("Inside the red frame is the caption zone: a dark shade is added at render, so do not reject for that")
    print(f"When chosen, run: {PY} .claude/skills/indigo11-video/scripts/scan_broll.py --cut {root} --pick \"c0:5-60,...\"")


def cmd_cut(a):
    """--pick 'c0:48-106,c2:10-205' -> cut only the ranges that passed the eye test"""
    root = pathlib.Path(a.cut)
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    ok_dir = root / "03-selected"
    ok_dir.mkdir(exist_ok=True)
    for f in ok_dir.glob("*"):
        f.unlink()
    if (root / "_source.json").exists():
        shutil.copyfile(root / "_source.json", ok_dir / "_source.json")

    for i, part in enumerate(a.pick.split(",")):
        key, rng = part.split(":")
        s, e = [float(x) for x in rng.split("-")]
        src = index[key.strip()]["file"]
        out = ok_dir / f"{i:02d}_{key.strip()}.mp4"
        sh(["ffmpeg", "-v", "error", "-ss", f"{s}", "-to", f"{e}", "-i", src,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-an", str(out), "-y"])
        print(f"  {key}: {s}-{e}s -> {out.name}")
    print(f"\nready: {ok_dir}")
    print(f"frames can be deleted now: {root}/02-frames")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", nargs="+", help="search keywords (several allowed)")
    ap.add_argument("--out", help="output folder")
    ap.add_argument("-n", type=int, default=12, help="how many clips to download")
    ap.add_argument("--per-query", type=int, default=30,
                    help="how many to inspect per keyword (more finds more portrait clips)")
    ap.add_argument("--max-dur", type=float, default=30,
                    help="drop clips longer than this (seconds); the hashtag route gives no "
                         "duration before download, so it is cut after download, before frames")
    ap.add_argument("--shorts-only", action="store_true",
                    help="never use landscape clips, even if too few are found")
    ap.add_argument("--source", choices=("auto", "youtube", "pexels", "pixabay"), default="auto",
                    help="auto = pexels/pixabay if a key is in .env (may be posted), else youtube | "
                         "youtube = real brands/events but never post (research/prototype) | "
                         "pexels / pixabay = free for commercial use, no watermark")
    ap.add_argument("--cut", help="a folder already scanned")
    ap.add_argument("--pick", help="chosen ranges, e.g. c0:48-106,c2:10-205")
    a = ap.parse_args()

    if a.cut:
        if not a.pick:
            sys.exit("--cut needs --pick")
        cmd_cut(a)
    elif a.q and a.out:
        # STEP 1 of the search order (local files first, then free stock, then draw/generate, buying last):
        # list the user's own matching videos before downloading anything. Not blocking: the scan below still runs.
        try:
            sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "indigo11-find-assets" / "scripts"))
            from find_local import find_local
            for q in a.q:
                loc = find_local("video", q, 3)
                if loc:
                    print(f"LOCAL FILES FIRST - your own videos for '{q}' (use these before the downloads below):")
                    for h in loc:
                        print("   ", h["file"])
        except Exception as e:      # never block the scan
            print(f"(local search skipped: {e})")
        if a.source == "auto":
            a.source = "pexels" if PEXELS_KEY else "pixabay" if PIXABAY_KEY else "youtube"
            print(f"clip source: {a.source}" + ("" if a.source != "youtube" else
                  " (no PEXELS_API_KEY / PIXABAY_API_KEY in .env: clips are for viewing/prototypes only, never post)"))
        if a.source == "youtube" and not YT:
            sys.exit(f"yt-dlp is missing. Install it with: {PY} _system/setup/common/skilldeps.py  (or use --source pexels)")
        cmd_scan(a)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
