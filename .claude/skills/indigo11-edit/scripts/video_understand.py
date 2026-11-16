#!/usr/bin/env python3
"""video_understand.py - let Gemini actually WATCH a video and answer about it.

This is the one thing sparse frames + OCR cannot do (edit-rules #29): a model
that ingests the moving video. Gemini is the only mainstream provider that does it.
Used by indigo11-analyze --understand to enrich context.json with a real video read
(on-screen action, pacing, what's shown when), not just stills.

Deterministic plumbing only: upload via the Files API (the test clips are 50-350MB,
far past the 20MB inline limit), poll until ACTIVE, call generateContent, return the
model's text. WHAT to ask is the caller's prompt; WHAT to do with the answer is the
agent's job.

REST flow (per https://ai.google.dev/gemini-api/docs/video-understanding):
  1. POST /upload/v1beta/files (resumable: start -> get upload url)
  2. PUT/POST bytes (upload, finalize) -> file.uri, file.state
  3. GET /v1beta/files/{id} poll until state == ACTIVE
  4. POST /v1beta/models/<model>:generateContent with file_data {file_uri, mime_type}
  (The page's examples now use the newer /v1beta/interactions endpoint; generateContent
  still serves gemini-3.8-flash - verified live 2026-10-02 - and is what this script
  is tested against.)

Key: GEMINI_API_KEY (.env or shell env). x-goog-api-key auth.

Usage:
  python3 video_understand.py --video clip.mov --prompt "..." [--model gemini-3.8-flash] [--fps 2]
  # prints {"ok": true, "text": "...", "model": "...", "tokens": N}
"""
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)
import argparse
import base64
import json
import mimetypes
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

if os.name == "nt":  # cp874 console crashes printing Thai (rule #32)
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

GEMINI = "https://generativelanguage.googleapis.com"
# gemini-3.8-flash is the docs' default for video and is cheaper than the 3.5-flash this
# used to default to: $0.75 in / $3.75 out per 1M tokens through 2026-12-31, then
# $1.50 / $7.50 (3.5-flash: $1.50 / $9.00). --model gemini-3.5-flash still works.
DEFAULT_MODEL = "gemini-3.8-flash"
INLINE_LIMIT = 14 * 1024 * 1024
RESOLUTION_ENUM = {
    "low": "MEDIA_RESOLUTION_LOW",
    "medium": "MEDIA_RESOLUTION_MEDIUM",
    "high": "MEDIA_RESOLUTION_HIGH",
}


def load_env_file() -> None:
    """Load .env from the nearest ancestor (kit root). Real env wins (rule #17)."""
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
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k and k not in os.environ:
                        os.environ[k] = v
            except Exception:
                pass
            return


def _api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        print(json.dumps({"ok": False, "error": "GEMINI_API_KEY not set (add to .env or shell env)"}))
        sys.exit(1)
    return key


def _req(url, data=None, headers=None, method=None, timeout=120):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.headers, r.read()


def is_http_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def get_duration_s(path: str) -> Optional[float]:
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if res.returncode != 0:
            return None
        return float(res.stdout.strip())
    except Exception:
        return None


CACHE_PATH = Path.home() / ".indigo11" / "gemini-file-cache.json"
CACHE_TTL_S = 40 * 3600  # Files API keeps uploads 48h; leave margin


def _cache_key(path: str) -> str:
    st = os.stat(path)
    return f"{Path(path).resolve()}|{st.st_size}|{int(st.st_mtime)}"


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def cached_file_uri(path: str, key: str) -> Optional[str]:
    """Reuse a previously uploaded file uri if still ACTIVE (saves upload + processing wait)."""
    entry = _load_cache().get(_cache_key(path))
    if not entry or (time.time() - entry.get("t", 0)) > CACHE_TTL_S:
        return None
    uri = entry.get("uri")
    if not uri:
        return None
    try:
        file_id = uri.split("/files/")[-1]
        _, raw = _req(f"{GEMINI}/v1beta/files/{file_id}", headers={"x-goog-api-key": key}, timeout=30)
        if json.loads(raw.decode()).get("state") == "ACTIVE":
            print(f"[video_understand] reusing uploaded file (cache hit): {uri}", file=sys.stderr)
            return uri
    except Exception:
        pass
    return None


def store_file_uri(path: str, uri: str) -> None:
    cache = _load_cache()
    cache[_cache_key(path)] = {"uri": uri, "t": time.time()}
    # prune expired entries so the file never grows unbounded
    cache = {k: v for k, v in cache.items() if (time.time() - v.get("t", 0)) <= CACHE_TTL_S}
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache), encoding="utf-8")
    except Exception:
        pass


def upload_file(path: str, key: str, mime: str) -> str:
    """Resumable upload, returns the file uri (files/...)."""
    size = os.path.getsize(path)
    name = Path(path).name
    # 1) start a resumable session
    start_headers, _ = _req(
        f"{GEMINI}/upload/v1beta/files",
        data=json.dumps({"file": {"display_name": name}}).encode(),
        headers={
            "x-goog-api-key": key,
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size),
            "X-Goog-Upload-Header-Content-Type": mime,
            "Content-Type": "application/json",
        },
        method="POST",
        timeout=60,
    )
    upload_url = start_headers.get("x-goog-upload-url") or start_headers.get("X-Goog-Upload-URL")
    if not upload_url:
        raise RuntimeError("no resumable upload url returned by Files API")
    # 2) upload + finalize in one shot (clips fit in memory fine; streaming would need chunking)
    print(f"[video_understand] uploading {name} ({size/1e6:.1f}MB)...", file=sys.stderr)
    with open(path, "rb") as f:
        body = f.read()
    _, raw = _req(
        upload_url,
        data=body,
        headers={
            "x-goog-api-key": key,
            "Content-Length": str(size),
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize",
        },
        method="POST",
        timeout=600,
    )
    info = json.loads(raw.decode())
    uri = (info.get("file") or {}).get("uri")
    if not uri:
        raise RuntimeError(f"upload finalize gave no file uri: {json.dumps(info)[:300]}")
    return uri


def wait_active(file_uri: str, key: str, timeout_s: int = 300) -> None:
    """Poll the file until state == ACTIVE (video needs processing before use)."""
    file_id = file_uri.split("/files/")[-1] if "/files/" in file_uri else file_uri.split("/")[-1]
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        _, raw = _req(f"{GEMINI}/v1beta/files/{file_id}", headers={"x-goog-api-key": key}, timeout=60)
        state = json.loads(raw.decode()).get("state")
        if state == "ACTIVE":
            return
        if state == "FAILED":
            raise RuntimeError("Gemini file processing FAILED")
        print(f"[video_understand] file state: {state}", file=sys.stderr)
        time.sleep(5)
    raise RuntimeError("file did not become ACTIVE in time")


def generate(part: dict, prompt: str, model: str, key: str, fps=None, resolution: str = "low",
             response_schema: Optional[dict] = None) -> dict:
    if fps:
        part["video_metadata"] = {"fps": fps}
    payload = {"contents": [{"parts": [part, {"text": prompt}]}]}
    gen_cfg = {}
    if resolution != "default":
        gen_cfg["mediaResolution"] = RESOLUTION_ENUM[resolution]
    # Structured output: when a JSON schema is supplied, Gemini is forced to return
    # JSON matching it instead of prose. This is what makes the answer machine-usable
    # (an edit template a generator reads directly) rather than a paragraph a human/agent
    # has to re-interpret - the interpretation step is exactly where drift creeps in.
    if response_schema is not None:
        gen_cfg["responseMimeType"] = "application/json"
        gen_cfg["responseSchema"] = response_schema
    if gen_cfg:
        payload["generationConfig"] = gen_cfg
    _, raw = _req(
        f"{GEMINI}/v1beta/models/{model}:generateContent",
        data=json.dumps(payload).encode(),
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        method="POST",
        timeout=300,
    )
    d = json.loads(raw.decode())
    parts = (((d.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise RuntimeError(f"empty model response: {json.dumps(d)[:400]}")
    tokens = (d.get("usageMetadata") or {}).get("totalTokenCount")
    out = {"text": text, "tokens": tokens}
    if response_schema is not None:
        try:
            out["data"] = json.loads(text)
        except json.JSONDecodeError:
            out["data"] = None  # caller falls back to text
    return out


def auto_fps(duration_s: Optional[float], fps):
    if fps is not None:
        return fps
    if duration_s and duration_s > 2700:
        return 0.25
    if duration_s and duration_s > 900:
        return 0.5
    return None


def understand(video_path: str, prompt: str, model: str = DEFAULT_MODEL, fps=None, resolution: str = "low",
               response_schema: Optional[dict] = None) -> dict:
    key = _api_key()
    if is_http_url(video_path):
        part = {"file_data": {"file_uri": video_path}}
        res = generate(part, prompt, model, key, fps=fps, resolution=resolution, response_schema=response_schema)
        res["model"] = model
        res["source"] = video_path
        return res

    mime = mimetypes.guess_type(video_path)[0] or "video/mp4"
    if not mime.startswith("video/"):
        mime = "video/mp4"
    duration_s = get_duration_s(video_path)
    fps = auto_fps(duration_s, fps)
    if os.path.getsize(video_path) <= INLINE_LIMIT and duration_s is not None and duration_s <= 60:
        b64 = base64.b64encode(Path(video_path).read_bytes()).decode()
        part = {"inline_data": {"mime_type": mime, "data": b64}}
        res = generate(part, prompt, model, key, fps=fps, resolution=resolution, response_schema=response_schema)
        res["model"] = model
        res["inline"] = True
        return res

    uri = cached_file_uri(video_path, key)
    if uri is None:
        uri = upload_file(video_path, key, mime)
        wait_active(uri, key)
        store_file_uri(video_path, uri)
    res = generate({"file_data": {"mime_type": mime, "file_uri": uri}}, prompt, model, key,
                   fps=fps, resolution=resolution, response_schema=response_schema)
    res["model"] = model
    res["file_uri"] = uri
    return res


def main():
    ap = argparse.ArgumentParser(description="Gemini video understanding (watches the actual video).")
    ap.add_argument("--video", required=True, help="video file path")
    ap.add_argument("--prompt", required=True, help="what to ask about the video")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--fps", type=float, default=None, help="frame sampling rate (lower = fewer tokens)")
    ap.add_argument("--resolution", choices=["low", "default", "medium", "high"], default="low",
                    help="Gemini media resolution. low is the default for speed/cost.")
    ap.add_argument("--schema-file", default=None,
                    help="Path to a JSON file holding a Gemini responseSchema. When given, Gemini "
                         "returns JSON matching it (structured) instead of prose; the parsed object "
                         "is in result['data'].")
    args = ap.parse_args()

    load_env_file()
    path = os.path.expanduser(args.video)
    if not is_http_url(path) and not os.path.exists(path):
        print(json.dumps({"ok": False, "error": f"video not found: {path}"}))
        sys.exit(1)

    schema = None
    if args.schema_file:
        schema = json.loads(Path(os.path.expanduser(args.schema_file)).read_text(encoding="utf-8"))

    started = time.time()
    try:
        res = understand(path, args.prompt, model=args.model, fps=args.fps,
                         resolution=args.resolution, response_schema=schema)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")[:500]
        print(json.dumps({"ok": False, "error": f"HTTP {e.code}: {body}"}, ensure_ascii=False))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)
    res["ok"] = True
    res["seconds"] = round(time.time() - started, 1)
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
