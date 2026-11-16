#!/usr/bin/env python3
"""Transcribe audio/video files via ElevenLabs Scribe, Grok (xAI), Gemini, or OpenAI Whisper.

Outputs raw JSON with normalized shape so the calling agent can post-process.
The calling agent is responsible for spelling fixes, segmentation, and topic
inference - this script only handles the API call and output normalization.

Every provider returns the same keys. `words_raw` is the provider's finest timed
unit and `timing_unit` says what that unit is, because it decides how captions are
built downstream (word_segment.py):

  provider     model                       timing_unit   measured vs Silero VAD (IMG_6894, 14 min Thai)
  elevenlabs   scribe_v2                   char          onset p90 40 ms, every pause visible  <- default
  gemini       gemini-3.5-transcribe       word          onset p90 76 ms (100 ms grid), every pause visible;
                                                         real Thai words, but it REWRITES text
                                                         (ห้าทุ่มครึ่ง -> 23:30น., platform -> แพลตฟอร์ม)
  grok         grok-voice-transcribe-2.0   char          onset p90 ~1 s, 14% of timed speech in silence,
                                                         a tone mark stretched to 6 s
  openai       whisper-1                   token         half the tokens zero-length, 34% in silence,
                                                         15 speech ranges with no text at all

OpenAI's newer gpt-transcribe returns no timestamps of any kind, so it cannot make
captions; whisper-1 stays the OpenAI path until its shutdown (2027-02-26).
"""
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, List, Dict, Union

if os.name == "nt":  # cp874 console crashes printing Thai (rule #32)
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
MAX_OPENAI_SIZE = 25 * 1024 * 1024
MAX_GROK_SIZE = 500 * 1024 * 1024   # docs.x.ai speech-to-text: "Max file size: 500 MB"
MAX_ELEVEN_SIZE = 1024 * 1024 * 1024 # 1GB practical limit
MAX_UPLOAD = {"openai": MAX_OPENAI_SIZE, "grok": MAX_GROK_SIZE}
CHUNK_DURATION = 600


# ---------- .env loader ----------

def load_env_file() -> None:
    """Load .env from the nearest ancestor with a CLAUDE.md (kit root).

    Buyer-friendly: docs say put keys in .env, so this script must honor that.
    Doesn't override real environment vars (existing os.environ wins).
    """
    cwd = Path.cwd().resolve()
    for parent in [cwd] + list(cwd.parents):
        env_path = parent / ".env"
        if env_path.exists():
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = val
            except Exception:
                pass
            return # only the nearest .env


load_env_file()


# ---------- ~/.indigo11/config.json reader (provider chains from ~/.indigo11/config.json) ----------
def load_config() -> dict:
    """Read the per-user config (optional, edited by hand; the starter has no ~/.indigo11/config.json).

    Returns {} on any error (missing file / bad JSON) so the script behaves
    exactly as before when no config exists. CLAUDE.md: per-user preference at
    ~/.indigo11/config.json. Env keys still win over everything (rule #17) - config
    only decides PROVIDER ORDER, never supplies secrets.
    """
    try:
        p = Path.home() / ".indigo11" / "config.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        return {}


def config_chain(task: str) -> List[str]:
    """providers.<task>.chain from config, or [] if absent. Ordered fallback list."""
    try:
        chain = load_config().get("providers", {}).get(task, {}).get("chain", [])
        return [str(c) for c in chain] if isinstance(chain, list) else []
    except Exception:
        return []


# ---------- ffmpeg helpers ----------

def count_audio_tracks(file_path: str) -> int:
    result = subprocess.run(
        ["ffprobe", "-i", file_path, "-show_streams", "-select_streams", "a"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return result.stdout.count("codec_type=audio")


def extract_audio(video_path: str, output_path: str) -> None:
    """Extract a 16k mono wav suitable for any STT provider.

    Multi-track files get an amix of all tracks - but iPhone 16+ records a SECOND
    spatial-audio stream (4ch, codec ffmpeg cannot decode), which makes amix exit 234.
    So: try the mix, and on ANY failure fall back to the first audio stream alone.
    """
    tracks = count_audio_tracks(video_path)
    out_ext = Path(output_path).suffix.lower()
    audio_args = ["-ac", "1", "-ar", "16000"]
    if out_ext == ".m4a":
        audio_args += ["-c:a", "aac", "-b:a", "48k"]
    else:
        audio_args += ["-c:a", "pcm_s16le"]
    if tracks > 1:
        filter_str = "".join(f"[0:a:{i}]" for i in range(tracks))
        filter_str += f"amix=inputs={tracks}:duration=longest"
        mix = subprocess.run(
            ["ffmpeg", "-i", video_path, "-filter_complex", filter_str,
             "-vn", *audio_args, "-y", output_path],
            capture_output=True,
        )
        if mix.returncode == 0:
            return
    subprocess.run(
        ["ffmpeg", "-i", video_path, "-map", "0:a:0", "-vn", *audio_args, "-y", output_path],
        capture_output=True, check=True,
    )


def transcode_audio_for_upload(input_path: str, output_path: str) -> None:
    subprocess.run(
        ["ffmpeg", "-i", input_path, "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "aac", "-b:a", "48k", "-y", output_path],
        capture_output=True, check=True,
    )


def extract_multichannel_audio(input_path: str, output_path: str) -> None:
    subprocess.run(
        ["ffmpeg", "-i", input_path, "-map", "0:a:0", "-vn", "-ar", "16000",
         "-c:a", "pcm_s16le", "-y", output_path],
        capture_output=True, check=True,
    )


def get_duration(file_path: str) -> float:
    result = subprocess.run(
        ["ffmpeg", "-i", file_path, "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    for line in result.stderr.split("\n"):
        if "Duration:" in line:
            time_str = line.split("Duration:")[1].split(",")[0].strip()
            parts = time_str.split(":")
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    return 0.0


# ---------- multipart helper (no external deps) ----------

MultipartValue = Union[str, int, float, List[str]]


def multipart_post(url: str, headers: Dict[str, str], fields: Dict[str, MultipartValue], file_path: str, file_field: str = "file") -> bytes:
    """POST multipart/form-data without requests/form-data. Stdlib only."""
    boundary = "----HFBoundary" + os.urandom(8).hex()
    body = bytearray()
    for k, value in fields.items():
        values = value if isinstance(value, list) else [value]
        for v in values:
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
            body.extend(f"{v}\r\n".encode())
    fname = os.path.basename(file_path)
    ext = os.path.splitext(fname)[1].lower()
    mime = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
            ".webm": "audio/webm", ".flac": "audio/flac", ".ogg": "audio/ogg"}.get(ext, "application/octet-stream")
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(f'Content-Disposition: form-data; name="{file_field}"; filename="{fname}"\r\n'.encode())
    body.extend(f"Content-Type: {mime}\r\n\r\n".encode())
    with open(file_path, "rb") as f:
        body.extend(f.read())
    body.extend(f"\r\n--{boundary}--\r\n".encode())

    req_headers = dict(headers)
    req_headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    req = urllib.request.Request(url, data=bytes(body), headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {msg}") from e


# ---------- providers ----------

def with_spacing(units: List[Dict], text: str) -> List[Dict]:
    """Rebuild the spaces a provider wrote in `text` but left out of its timed units.

    A space is where the provider heard a clause end, and word_segment.py cuts
    phrases there. Only valid when the units concatenate to the text exactly;
    otherwise the units are returned untouched."""
    if "".join(u["text"] for u in units) != re.sub(r"\s+", "", text):
        return [dict(u, type="word") for u in units]
    out: List[Dict] = []
    pos = 0
    for u in units:
        while pos < len(text) and text[pos].isspace():
            if out and out[-1]["type"] != "spacing":
                out.append({"text": " ", "start": out[-1]["end"], "end": u["start"], "type": "spacing"})
            pos += 1
        out.append(dict(u, type="word"))
        pos += len(u["text"])
    return out


def phrases_from_units(units: List[Dict]) -> List[Dict]:
    """Merge timed units into phrases at spacing / audio-event / speaker boundaries."""
    merged: List[Dict] = []
    cur: Optional[Dict] = None
    for w in units:
        text = w.get("text", "")
        speaker, channel = w.get("speaker_id"), w.get("channel_index")
        if cur and (speaker != cur.get("speaker_id") or channel != cur.get("channel_index")):
            merged.append(cur)
            cur = None
        if w.get("type") in ("spacing", "audio_event") or text.isspace():
            if cur:
                merged.append(cur)
            cur = None
            continue
        if cur is None:
            cur = {"text": "", "start": float(w.get("start", 0)), "end": 0.0}
            if speaker is not None:
                cur["speaker_id"] = speaker
            if channel is not None:
                cur["channel_index"] = channel
        cur["text"] += text
        cur["end"] = float(w.get("end", cur["start"]))
    if cur:
        merged.append(cur)
    return merged


def transcribe_openai(audio_path: str, lang: Optional[str], prompt: Optional[str]) -> Dict:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    # whisper-1 is the ONLY OpenAI model that returns timestamps ("timestamp_granularities[]
    # is only supported for whisper-1"); gpt-transcribe / gpt-4o-transcribe return text only.
    # Deprecated, shutdown 2027-02-26 - there is no timestamped successor to move to yet.
    fields = {"model": "whisper-1", "response_format": "verbose_json",
              "timestamp_granularities[]": "word"}
    if lang:
        fields["language"] = lang
    if prompt:
        fields["prompt"] = prompt
    raw = multipart_post(
        "https://api.openai.com/v1/audio/transcriptions",
        {"Authorization": f"Bearer {api_key}"},
        fields, audio_path,
    )
    data = json.loads(raw)
    words = []
    for w in data.get("words", []):
        words.append({"text": w.get("word", ""),
                      "start": float(w.get("start", 0)),
                      "end": float(w.get("end", 0))})
    return {
        "provider": "openai",
        "language": data.get("language"),
        "duration": data.get("duration"),
        "text": data.get("text", ""),
        "words": words,
        "timing_unit": "token",   # Thai comes back as sub-word pieces, not words
    }


def transcribe_grok(audio_path: str, lang: Optional[str]) -> Dict:
    api_key = os.environ.get("XAI_API_KEY")
    if not api_key:
        raise RuntimeError("XAI_API_KEY not set")
    # Pin the model: the docs and the release notes disagree on which one is the default.
    fields = {"model": os.environ.get("GROK_STT_MODEL", "grok-voice-transcribe-2.0")}
    if lang:
        fields["language"] = lang
        fields["format"] = "true"    # formatting needs a language; without one the API rejects it
    raw = multipart_post(
        "https://api.x.ai/v1/stt",
        {"Authorization": f"Bearer {api_key}"},
        fields, audio_path,
    )
    data = json.loads(raw)
    # Thai comes back one CODE POINT per entry (a tone mark is its own entry), with no
    # entry for spaces - those exist only in `text`, so put them back for phrase cuts.
    units = [{"text": w.get("text", ""), "start": float(w.get("start", 0)), "end": float(w.get("end", 0))}
             for w in data.get("words", []) or [] if w.get("text", "").strip()]
    raw_words = with_spacing(units, data.get("text", ""))
    return {
        "provider": "grok",
        "language": data.get("language"),
        "duration": data.get("duration"),
        "text": data.get("text", ""),
        "words": phrases_from_units(raw_words),
        "words_raw": raw_words,
        "timing_unit": "char",
    }


def validate_elevenlabs_options(options: Optional[Dict] = None) -> Dict:
    normalized = dict(options or {})
    num_speakers = normalized.get("num_speakers")
    threshold = normalized.get("diarization_threshold")
    multichannel = bool(normalized.get("use_multi_channel"))
    diarize = bool(normalized.get("diarize"))
    keyterms = list(normalized.get("keyterms") or [])
    if num_speakers is not None and not 1 <= int(num_speakers) <= 32:
        raise ValueError("num_speakers must be between 1 and 32")
    if threshold is not None and not 0.1 <= float(threshold) <= 0.4:
        raise ValueError("diarization_threshold must be between 0.1 and 0.4")
    if threshold is not None and num_speakers is not None:
        raise ValueError("diarization_threshold cannot be combined with num_speakers")
    if threshold is not None and not diarize:
        raise ValueError("diarization_threshold requires diarize=true")
    if multichannel and (diarize or num_speakers is not None or threshold is not None):
        raise ValueError("use_multi_channel cannot be combined with diarization options")
    if len(keyterms) > 1000:
        raise ValueError("keyterms cannot exceed 1000")
    for term in keyterms:
        if (not isinstance(term, str) or not term or len(term) >= 50 or
                any(c in term for c in "<>{}[]\\") or len(term.split()) > 5):
            raise ValueError(f"invalid keyterm: {term!r}")
    normalized["keyterms"] = keyterms
    return normalized


def transcribe_elevenlabs(audio_path: str, lang: Optional[str], options: Optional[Dict] = None) -> Dict:
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY not set")
    # ElevenLabs uses ISO 639-3 codes (tha, eng, jpn). Map common 2-letter inputs.
    iso3 = {"th": "tha", "en": "eng", "ja": "jpn", "zh": "zho", "ko": "kor",
            "es": "spa", "fr": "fra", "de": "deu", "id": "ind", "vi": "vie"}.get(lang, lang)
    options = validate_elevenlabs_options(options)
    fields: Dict[str, MultipartValue] = {"model_id": "scribe_v2", "timestamps_granularity": "word"}
    if iso3:
        fields["language_code"] = iso3
    if options.get("diarize"):
        fields["diarize"] = "true"
    if options.get("num_speakers") is not None:
        fields["num_speakers"] = int(options["num_speakers"])
    if options.get("diarization_threshold") is not None:
        fields["diarization_threshold"] = options["diarization_threshold"]
    if options.get("use_multi_channel"):
        fields["use_multi_channel"] = "true"
        fields["diarize"] = "false"
        fields["multichannel_output_style"] = "combined"
    if options.get("no_verbatim"):
        fields["no_verbatim"] = "true"
    if options.get("tag_audio_events") is False:
        fields["tag_audio_events"] = "false"
    if options.get("keyterms"):
        fields["keyterms"] = list(options["keyterms"])
    raw = multipart_post(
        "https://api.elevenlabs.io/v1/speech-to-text",
        {"xi-api-key": api_key},
        fields, audio_path,
    )
    data = json.loads(raw)
    # ElevenLabs returns per-character "words" entries. Build phrase-level for the
    # main `words` field, but ALSO preserve the original character-level array
    # under `words_raw` so post-process pass 2 (re-segmentation, word_segment.py)
    # can work from the un-merged source. Without this, `raw-elevenlabs.json` is
    # not actually raw and downstream re-granulation is impossible.
    raw_words = data.get("words", []) or []
    merged = phrases_from_units(raw_words)
    speaker_ids = list(dict.fromkeys(
        str(w.get("speaker_id")) for w in raw_words if w.get("speaker_id") is not None
    ))
    if not speaker_ids and options.get("use_multi_channel"):
        speaker_ids = [f"channel_{i}" for i in dict.fromkeys(
            int(w.get("channel_index")) for w in raw_words if w.get("channel_index") is not None
        )]
    return {
        "provider": "elevenlabs",
        "language": data.get("language_code"),
        "duration": max((float(w.get("end", 0)) for w in raw_words), default=0.0),
        "text": data.get("text", ""),
        "words": merged,
        "words_raw": raw_words, # char-level original from ElevenLabs (for re-segmentation)
        "timing_unit": "char",
        "speaker_ids": speaker_ids,
        "diarized": bool(options.get("diarize")),
        "multi_channel": bool(options.get("use_multi_channel")),
    }


def _loads_lenient(s: str) -> dict:
    """Parse model JSON that may have a markdown fence, trailing commas, or a
    truncated `words` array. Best-effort: recover as many complete word objects
    as possible so a long clip still yields usable timing instead of crashing."""
    import re
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s).rstrip("`").rstrip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # strip trailing commas then retry
    try:
        return json.loads(re.sub(r",(\s*[}\]])", r"\1", s))
    except json.JSONDecodeError:
        pass
    # salvage complete {"text":..,"start":..,"end":..} objects from a cut-off array;
    # full-text field is unrecoverable here - caller rebuilds it by joining the words
    words = []
    for m in re.finditer(r"\{[^{}]*?\"text\"\s*:\s*\"(?:[^\"\\]|\\.)*\"[^{}]*?\}", s):
        try:
            words.append(json.loads(m.group(0)))
        except json.JSONDecodeError:
            continue
    return {"words": words, "text": ""}


GEMINI_API = "https://generativelanguage.googleapis.com"
GEMINI_AUDIO_MIME = {"mp3": "audio/mp3", "wav": "audio/wav", "m4a": "audio/mp4",
                     "aac": "audio/aac", "flac": "audio/flac", "ogg": "audio/ogg"}


def _gemini_req(url: str, key: str, data: Optional[bytes] = None, headers: Optional[Dict] = None,
                timeout: int = 300):
    req = urllib.request.Request(url, data=data, headers={"x-goog-api-key": key, **(headers or {})},
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.headers, r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}") from e


def _gemini_upload(path: str, key: str, mime: str) -> str:
    """Files API resumable upload -> file uri. gemini-3.5-transcribe takes fileData only."""
    import time
    size = os.path.getsize(path)
    head, _ = _gemini_req(
        f"{GEMINI_API}/upload/v1beta/files", key,
        data=json.dumps({"file": {"display_name": Path(path).name}}).encode(),
        headers={"X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
                 "X-Goog-Upload-Header-Content-Length": str(size),
                 "X-Goog-Upload-Header-Content-Type": mime, "Content-Type": "application/json"},
        timeout=60)
    upload_url = head.get("x-goog-upload-url") or head.get("X-Goog-Upload-URL")
    if not upload_url:
        raise RuntimeError("Gemini STT: Files API returned no upload url")
    _, raw = _gemini_req(upload_url, key, data=Path(path).read_bytes(),
                         headers={"Content-Length": str(size), "X-Goog-Upload-Offset": "0",
                                  "X-Goog-Upload-Command": "upload, finalize"}, timeout=600)
    info = json.loads(raw.decode()).get("file") or {}
    uri = info.get("uri")
    if not uri:
        raise RuntimeError("Gemini STT: upload gave no file uri")
    file_id = uri.split("/files/")[-1]
    deadline = time.time() + 300
    while info.get("state") not in (None, "ACTIVE"):
        if info.get("state") == "FAILED" or time.time() > deadline:
            raise RuntimeError(f"Gemini STT: uploaded file never became ACTIVE ({info.get('state')})")
        time.sleep(2)
        info = json.loads(_gemini_req(f"{GEMINI_API}/v1beta/files/{file_id}", key, timeout=60)[1].decode())
    return uri


def transcribe_gemini_native(audio_path: str, lang: Optional[str], model: str, api_key: str) -> Dict:
    """Gemini's transcription model: real word timestamps from audioTranscriptionConfig.

    Three things to know before choosing it over ElevenLabs for captions:
      - units are WORDS, and for Thai they are real words (อะไร, นะครับ stay whole) -
        word_segment.py uses them as-is instead of running a dictionary tokenizer;
      - offsets sit on a 100 ms grid, so karaoke is coarser than char-level providers;
      - VERBATIM mode still normalizes: ห้าทุ่มครึ่ง -> 23:30น., platform -> แพลตฟอร์ม,
        หนึ่งร้อยเปอร์เซนต์ -> 100%. The caption then shows what was not said.
    Limit: 30 minutes of audio when word timestamps are on.
    """
    ext = Path(audio_path).suffix.lower().lstrip(".")
    mime = GEMINI_AUDIO_MIME.get(ext, "audio/mp3")
    uri = _gemini_upload(audio_path, api_key, mime)
    cfg: Dict = {"wordTimestamp": True}
    if lang:
        cfg["languageCodes"] = [{"th": "th-TH", "en": "en-US", "ja": "ja-JP", "ko": "ko-KR",
                                 "zh": "cmn-Hans-CN", "vi": "vi-VN", "id": "id-ID"}.get(lang, lang)]
    body = json.dumps({
        "contents": [{"parts": [{"fileData": {"fileUri": uri, "mimeType": mime}}]}],
        "generationConfig": {"audioTranscriptionConfig": cfg},
    }).encode()
    _, raw = _gemini_req(f"{GEMINI_API}/v1beta/models/{model}:generateContent", api_key,
                         data=body, headers={"Content-Type": "application/json"}, timeout=600)
    resp = json.loads(raw.decode())
    try:
        parts = resp["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Gemini STT: unexpected response shape: {e}; resp={str(resp)[:400]}")
    units, text = [], ""
    for part in parts:
        text += part.get("text", "")
        for w in (part.get("audioTranscription") or {}).get("words", []) or []:
            if (w.get("word") or "").strip():
                units.append({"text": w["word"],
                              "start": float(str(w.get("startOffset", "0s")).rstrip("s") or 0),
                              "end": float(str(w.get("endOffset", "0s")).rstrip("s") or 0)})
    if not units:
        raise RuntimeError(f"Gemini STT: no word timestamps in response; resp={str(resp)[:400]}")
    raw_words = with_spacing(units, text)
    return {
        "provider": "gemini",
        "language": lang,
        "duration": max(u["end"] for u in units),
        "text": text or "".join(u["text"] for u in units),
        "words": phrases_from_units(raw_words),
        "words_raw": raw_words,
        "timing_unit": "word",
        "timing_resolution_ms": 100,
    }


def transcribe_gemini(audio_path: str, lang: Optional[str]) -> Dict:
    """Transcribe via Gemini.

    Default model gemini-3.5-transcribe has a native transcription path with real word
    timestamps (transcribe_gemini_native). Setting GEMINI_STT_MODEL to a general model
    (e.g. gemini-3.8-flash) uses the older prompted path below, whose word times are
    the model's ESTIMATE, not an alignment - usable for a rough caption, never for
    tight karaoke (see edit-rules #16).
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")
    model = os.environ.get("GEMINI_STT_MODEL", "gemini-3.5-transcribe")
    if "transcribe" in model:
        return transcribe_gemini_native(audio_path, lang, model, api_key)
    import base64
    b64 = base64.b64encode(Path(audio_path).read_bytes()).decode()
    lang_hint = f" The spoken language is '{lang}'." if lang else ""
    prompt = (
        "Transcribe this audio verbatim." + lang_hint +
        " Keep foreign brand/English words as-is (do not translate)."
        " Return WORD-LEVEL timing for every spoken word."
        " Output STRICT JSON only (no markdown fence), shape:"
        ' {"text":"<full transcript>","language":"<iso code>",'
        '"words":[{"text":"<one word>","start":<seconds float>,"end":<seconds float>}]}'
    )
    ext = Path(audio_path).suffix.lower().lstrip(".")
    mime = GEMINI_AUDIO_MIME.get(ext, "audio/mp3")
    body = json.dumps({
        "contents": [{"parts": [
            {"text": prompt},
            {"inline_data": {"mime_type": mime, "data": b64}},
        ]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }).encode()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        resp = json.loads(r.read().decode())
    try:
        cand = resp["candidates"][0]
        text_out = cand["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Gemini STT: unexpected response shape: {e}; resp={str(resp)[:400]}")
    if cand.get("finishReason") == "MAX_TOKENS":
        # word-by-word JSON blew the output cap and got cut mid-array. A salvaged
        # partial would silently DROP the clip's tail - fail loudly instead so the
        # caller picks a bigger-cap model or an alignment provider.
        raise RuntimeError(
            "Gemini STT: output hit MAX_TOKENS (word-level JSON is very long). "
            "Retry with GEMINI_STT_MODEL set to a higher-cap model, or use a "
            "provider with native alignment (elevenlabs) for long clips.")
    parsed = _loads_lenient(text_out)
    words = []
    for w in parsed.get("words", []) or []:
        words.append({"text": w.get("text", ""),
                      "start": float(w.get("start", 0)),
                      "end": float(w.get("end", 0))})
    return {
        "provider": "gemini",
        "language": parsed.get("language") or lang,
        "duration": None,
        "text": parsed.get("text", "") or " ".join(w["text"] for w in words),
        "words": words,
        "timing_unit": "word",
        "timing_estimated": True, # model-estimated, not forced-alignment (see docstring)
    }


PROVIDERS = {
    "openai": transcribe_openai,
    "grok": transcribe_grok,
    "elevenlabs": transcribe_elevenlabs,
    "gemini": transcribe_gemini,
}


STT_ENV = {"grok": "XAI_API_KEY", "elevenlabs": "ELEVENLABS_API_KEY", "openai": "OPENAI_API_KEY",
           "gemini": "GEMINI_API_KEY"}

# ~/.indigo11/config.json writes config provider ids that don't all map 1:1 to what transcribe.py
# implements. Translate config id -> implemented provider; ids with no implementation are
# silently skipped (so a chain like ["gemini","elevenlabs"] still works via elevenlabs).
STT_CONFIG_ALIAS = {
    "elevenlabs": "elevenlabs",
    "openai": "openai",
    "openai_gpt4o": "openai", # same Whisper-class endpoint here
    "grok": "grok",
    "gemini": "gemini", # gemini-3.5-transcribe: real word timestamps, but normalizes text (see docstring)
}


def provider_candidates(lang: Optional[str] = None) -> list:
    """Ordered list of STT providers that have an env key, for runtime fail-over.

    Same precedence as before (config chain, then language-aware default) but
    returns the FULL keyed list rather than just the first entry, so main() can
    fail over to the next provider when a call 401/402/429/5xx's (P0-04/H1).
    De-duplicated, order preserved.
    """
    ordered = []
    for cid in config_chain("stt"):
        prov = STT_CONFIG_ALIAS.get(cid)
        if prov and os.environ.get(STT_ENV[prov]) and prov not in ordered:
            ordered.append(prov)
    default = ("elevenlabs", "grok", "openai") if (lang and lang.lower() in ("th", "tha", "thai")) \
        else ("grok", "elevenlabs", "openai")
    for p in default:
        if os.environ.get(STT_ENV[p]) and p not in ordered:
            ordered.append(p)
    return ordered


def auto_provider(lang: Optional[str] = None) -> str:
    """First keyed STT provider (kept for callers that want a single choice)."""
    cands = provider_candidates(lang)
    if not cands:
        raise RuntimeError("No STT provider env keys set "
                           "(ELEVENLABS_API_KEY / XAI_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY)")
    return cands[0]


def classify_http_error(err: Exception) -> dict:
    """Turn a provider RuntimeError('HTTP <code>: ...') into a typed verdict.

    Returns {code, kind, retryable, failover}. kind in
    invalid|billing|scope|rate_limit|provider_down|other. `failover` = should we
    try the NEXT keyed provider (bad key / quota / outage / throttle), vs a
    problem re-trying elsewhere won't fix (e.g. a malformed-input 400).
    """
    msg = str(err)
    m = re.search(r"HTTP (\d{3})", msg)
    code = int(m.group(1)) if m else 0
    if code == 401:
        return {"code": code, "kind": "invalid", "retryable": False, "failover": True}
    if code == 402:
        return {"code": code, "kind": "billing", "retryable": False, "failover": True}
    if code == 403:
        return {"code": code, "kind": "scope", "retryable": False, "failover": True}
    if code == 429:
        return {"code": code, "kind": "rate_limit", "retryable": True, "failover": True}
    if code >= 500:
        return {"code": code, "kind": "provider_down", "retryable": True, "failover": True}
    if code == 400:
        return {"code": code, "kind": "bad_request", "retryable": False, "failover": False}
    return {"code": code, "kind": "other", "retryable": False, "failover": False}


# ---------- main ----------

def main():
    parser = argparse.ArgumentParser(description="Transcribe audio/video to JSON.")
    parser.add_argument("file", help="Audio or video file path")
    parser.add_argument("--provider", choices=["openai", "grok", "elevenlabs", "gemini", "auto"],
                        default="auto",
                        help="STT provider. auto = the config chain, then elevenlabs > grok > openai for "
                             "Thai (grok > elevenlabs > openai otherwise), by env keys. gemini is opt-in "
                             "only: word-level timing on a 100 ms grid, and it rewrites numbers/loanwords")
    parser.add_argument("--lang", help="Language hint (e.g. th, en). Provider-specific code mapping handled internally.")
    parser.add_argument("--prompt", help="Optional vocabulary/context prompt (OpenAI only)")
    parser.add_argument("--diarize", action="store_true",
                        help="ElevenLabs only: annotate each word with speaker_id")
    parser.add_argument("--num-speakers", type=int,
                        help="ElevenLabs diarization hint (1-32)")
    parser.add_argument("--diarization-threshold", type=float,
                        help="ElevenLabs diarization threshold (0.1-0.4; cannot combine with --num-speakers)")
    parser.add_argument("--use-multi-channel", action="store_true",
                        help="ElevenLabs only: one isolated speaker per audio channel, maximum 5")
    parser.add_argument("--keyterm", action="append", default=[],
                        help="ElevenLabs keyterm hint; repeat for multiple names/brands")
    parser.add_argument("--no-verbatim", action="store_true",
                        help="ElevenLabs only: remove fillers/false starts; never use for edit source truth")
    parser.add_argument("--no-audio-events", action="store_true",
                        help="ElevenLabs only: disable laughter/applause event tags")
    parser.add_argument("--output", help="Output JSON path. Default: <input>.transcript.json next to input")
    parser.add_argument("--save-all", metavar="DIR",
                        help="Save raw + captions + words.json to a directory (3-output mode for CapCut workflow). "
                             "raw-elevenlabs.json kept for re-segmentation. captions.json is phrase-level. "
                             "words.json is nlpo3-segmented (needs --words-per-group N).")
    parser.add_argument("--words-per-group", type=int, default=2,
                        help="With --save-all: words per caption group for words.json (default 2)")
    parser.add_argument("--no-gap-close", action="store_true",
                        help="Don't extend each phrase/group's end to the next one's start. "
                             "Default OFF (captions ARE gap-closed) so caption renderers never "
                             "blink to black between phrases - the current line holds across "
                             "short silences until the next caption begins. Applies to both "
                             "captions.json and words.json. Raw response (raw-elevenlabs.json) "
                             "is always kept un-closed for re-segmentation.")
    args = parser.parse_args()

    try:
        validate_elevenlabs_options({
            "diarize": args.diarize or args.num_speakers is not None or args.diarization_threshold is not None,
            "num_speakers": args.num_speakers,
            "diarization_threshold": args.diarization_threshold,
            "use_multi_channel": args.use_multi_channel,
            "keyterms": args.keyterm,
        })
    except ValueError as exc:
        parser.error(str(exc))

    eleven_only = bool(args.diarize or args.num_speakers is not None or
                       args.diarization_threshold is not None or args.use_multi_channel or
                       args.keyterm or args.no_verbatim or args.no_audio_events)
    if eleven_only and args.provider not in ("auto", "elevenlabs"):
        parser.error("speaker/keyterm options require --provider elevenlabs")

    file_path = os.path.expanduser(args.file)
    if not os.path.exists(file_path):
        print(f"Error: file not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    # Build the ordered candidate list. Explicit --provider = exactly one
    # (fail-fast, no surprise fallback / cost change). auto = full keyed chain
    # so a 401/quota/outage on the first provider fails over to the next.
    if args.provider != "auto":
        candidates = [args.provider]
    elif eleven_only:
        candidates = ["elevenlabs"] if os.environ.get("ELEVENLABS_API_KEY") else []
    else:
        candidates = provider_candidates(args.lang)
        if not candidates:
            print(json.dumps({"ok": False, "error": "no_provider_key",
                              "message_en": "No STT provider key set. Add a key to <starter>/.env "
                                            "(ELEVENLABS/OPENAI/XAI/GEMINI)."}, ensure_ascii=False),
                  file=sys.stderr)
            sys.exit(1)
    if not candidates:
        print(json.dumps({"ok": False, "error": "no_elevenlabs_key",
                          "message_en": "ElevenLabs options were requested but ELEVENLABS_API_KEY is not set."},
                         ensure_ascii=False), file=sys.stderr)
        sys.exit(1)

    with tempfile.TemporaryDirectory() as tmp:
        ext = Path(file_path).suffix.lower()
        if ext in VIDEO_EXTENSIONS:
            audio_path = os.path.join(tmp, "audio.wav" if args.use_multi_channel else "audio.m4a")
            if args.use_multi_channel:
                extract_multichannel_audio(file_path, audio_path)
            else:
                extract_audio(file_path, audio_path)
        else:
            audio_path = file_path

        size = os.path.getsize(audio_path)
        if ext not in VIDEO_EXTENSIONS and size > 20 * 1024 * 1024:
            compressed = os.path.join(tmp, "audio.wav" if args.use_multi_channel else "audio.m4a")
            if args.use_multi_channel:
                extract_multichannel_audio(audio_path, compressed)
            else:
                transcode_audio_for_upload(audio_path, compressed)
            audio_path = compressed
            size = os.path.getsize(audio_path)
        # Most sources we hit are <25MB after wav-16k. If bigger, agent should
        # split first - keep this script simple.
        if size > MAX_ELEVEN_SIZE:
            print(f"Error: audio is {size/1024/1024:.0f}MB; split with ffmpeg first", file=sys.stderr)
            sys.exit(1)

        result = None
        provider = None
        attempts = []
        for cand in candidates:
            if size > MAX_UPLOAD.get(cand, MAX_ELEVEN_SIZE):
                attempts.append({"provider": cand, "skipped": "over_size_limit"})
                continue
            if args.prompt and cand != "openai":
                print(f"Note: --prompt is ignored for provider={cand} (OpenAI-only).", file=sys.stderr)
            fn = PROVIDERS[cand]
            try:
                if cand == "openai":
                    result = fn(audio_path, args.lang, args.prompt)
                elif cand == "elevenlabs":
                    result = fn(audio_path, args.lang, {
                        "diarize": args.diarize or args.num_speakers is not None or args.diarization_threshold is not None,
                        "num_speakers": args.num_speakers,
                        "diarization_threshold": args.diarization_threshold,
                        "use_multi_channel": args.use_multi_channel,
                        "keyterms": args.keyterm,
                        "no_verbatim": args.no_verbatim,
                        "tag_audio_events": not args.no_audio_events,
                    })
                else:
                    result = fn(audio_path, args.lang)
                provider = cand
                break
            except RuntimeError as e:
                verdict = classify_http_error(e)
                attempts.append({"provider": cand, "code": verdict["code"], "kind": verdict["kind"]})
                # Explicit --provider or a non-failover error (bad input): stop now.
                if args.provider != "auto" or not verdict["failover"] or cand == candidates[-1]:
                    print(json.dumps({"ok": False, "provider": cand, "http": verdict["code"],
                                      "kind": verdict["kind"], "attempts": attempts,
                                      "message_en": f"{cand} STT failed ({verdict['kind']}, HTTP {verdict['code']}). {str(e)[:200]}"}, ensure_ascii=False),
                          file=sys.stderr)
                    sys.exit(1)
                # else: fall over to the next keyed provider
                print(f"[transcribe] {cand} failed ({verdict['kind']} HTTP {verdict['code']}) - "
                      f"trying next provider", file=sys.stderr)

    # Pin language from --lang if provided. Otherwise providers may auto-detect
    # per-segment and the last segment's detection wins, which is misleading.
    if args.lang:
        result["language"] = args.lang

    if args.save_all:
        # 3-output mode: raw + captions + words.json
        save_dir = Path(os.path.expanduser(args.save_all))
        save_dir.mkdir(parents=True, exist_ok=True)
        raw_path = save_dir / "raw-elevenlabs.json"
        captions_path = save_dir / "captions.json"
        words_path = save_dir / "words.json"
        meta_path = save_dir / "meta.json"

        # raw - kept untouched (char-level original from ElevenLabs, plus the merged
        # phrase-level we built). This is the source of truth for re-segmentation;
        # gap-close is NOT applied here on purpose so anyone re-segmenting from raw
        # sees actual word boundaries.
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        # captions - phrase-level, GAP-CLOSED by default. Each phrase's `end` is
        # extended to the next phrase's `start` so caption renderers (CapCut /
        # HyperFrames / others) never show a black blink between phrases. ElevenLabs
        # leaves 20–500ms silences between phrases for breath/sentence breaks;
        # holding the previous line across that silence reads as natural pacing,
        # not as a dropped frame. The author can hand-trim a tail back in their NLE
        # to restore a hard cut. Pass --no-gap-close to disable. The `text` field
        # and `words_raw` (char-level) are unchanged. See edit-rules #11/#16.
        captions_result = dict(result)
        if not args.no_gap_close:
            phrases = [dict(p) for p in result.get("words", [])]
            for i in range(len(phrases) - 1):
                gap = phrases[i + 1]["start"] - phrases[i]["end"]
                if 0 < gap <= 1.2:      # a breath: bridge it
                    phrases[i]["end"] = phrases[i + 1]["start"]
                elif gap > 1.2:         # a real pause: hold briefly, then clear (rule #11b)
                    phrases[i]["end"] += max(0.0, min(1.2, gap - 1.25))
            captions_result["words"] = phrases
        captions_result["gap_closed"] = not args.no_gap_close
        with open(captions_path, "w", encoding="utf-8") as f:
            json.dump(captions_result, f, ensure_ascii=False, indent=2)
        # words.json: word_segment.py reads the provider's finest timed units (`words_raw`,
        # else `words`) and `timing_unit`, so every provider that returned timing gets one.
        words_data = None
        if result.get("words_raw") or result.get("words"):
            seg_script = Path(__file__).parent / "word_segment.py"
            tmp_words = save_dir / f"_seg_tmp.json"
            seg_cmd = [
                sys.executable, str(seg_script), str(raw_path),
                "-o", str(words_path),
                "--engine", "nlpo3",
                "--words-per-group", str(args.words_per_group),
            ]
            if args.no_gap_close:
                seg_cmd.append("--no-gap-close")
            r = subprocess.run(seg_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode == 0:
                words_data = {"output": str(words_path), "words_per_group": args.words_per_group}
            else:
                words_data = {"error": "word_segment failed", "stderr": r.stderr[:200]}

        meta = {
            "skill": "indigo11-transcribe", "version": "v1",
            "source_file": file_path, "source_size": (os.path.getsize(file_path) if os.path.exists(file_path) else None), "provider": result["provider"],
            "language": result.get("language"),
            "words_per_group": args.words_per_group,
            "diarized": bool(result.get("diarized")),
            "multi_channel": bool(result.get("multi_channel")),
            "speaker_ids": result.get("speaker_ids", []),
            "ran_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        summary = {
            "ok": True,
            "provider": result["provider"],
            "language": result.get("language"),
            "wordCount": len(result.get("words", [])),
            "outputs": {
                "raw": str(raw_path),
                "captions": str(captions_path),
                "words": str(words_path) if words_data and "output" in words_data else None,
                "meta": str(meta_path),
            },
            "words_per_group": args.words_per_group,
            "words_status": words_data,
            "note": "captions.json needs post-process #1 (text fix). words.json needs post-process #2 (boundary fix). See indigo11-transcribe/references/post-process-protocol.md and indigo11-edit/references/post-process-protocol-v2.md.",
        }
        print(json.dumps(summary, ensure_ascii=False))
        return

    out_path = args.output or str(Path(file_path).with_suffix("")) + ".transcript.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    summary = {
        "ok": True,
        "provider": result["provider"],
        "language": result.get("language"),
        "wordCount": len(result.get("words", [])),
        "transcriptPath": out_path,
    }
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
