#!/usr/bin/env python3
"""vo_assemble.py - voice-over-driven mode (Flow A-VO): the voice-over is the clock.

The buyer has a NEW CapCut project with lots of footage AND a separate voice-over in
the Media panel (nothing on the timeline). This script turns that into a finished
edit in three deterministic steps. Every editorial choice (which shot under which
line, B-roll, effects, SFX, caption style) lives in PLAN.md and is made by the agent
and approved by the buyer - the script only measures, validates and writes.

  prep   read-only on CapCut. Finds the voice-over in the Media panel, transcribes it
         (word timing, indigo11-transcribe, cached), watches every footage clip with
         indigo11-watch level 1 (shot index + OCR + timestamped frame grids), and writes
         projects/<date>-<name>/PLAN.md: one row per voice-over phrase, footage column
         empty for the agent to fill. Nothing is written to CapCut.
  build  read-only on CapCut. Parses + validates PLAN.md against the Media panel and
         writes the EDL (VO = master clock) and every timing file the existing scripts
         take. Reports EVERY problem at once; writes nothing when any exists.
  apply  the only step that writes CapCut, and only when PLAN.md says `approved: yes`.
         Safety copy of the whole project, then in order: import_media (B-roll + SFX)
         -> inject_edl (footage cut to the VO, muted) -> add_sfx --track auto (the VO,
         full length) -> add_overlay (B-roll) -> capcut_apply_resource (transitions,
         effects, filters) -> add_sfx --track auto (SFX) -> add_captions (Thai, VO
         transcript) -> qc_edit. Each write is its own reversible op (rule #42); the
         FIRST op's restore undoes the whole chain.

PLAN.md contract (the table under "## ช็อต"; column order is free, headers are matched
by name):
  เวลา VO     0.00-2.40          voice-over range in seconds (= timeline time)
  คำพูด       text               information only
  ฟุตเทจ      clip.mp4           a Media-panel clip, or a file in <P>/assets (imported)
  เริ่มที่      1.50               in-point inside that clip (seconds); out = in + row length
  B-roll      - | file[@in]      a video/photo on the overlay track for the whole row;
                                 "ต้องหา: <query>" = still missing -> build refuses
  เอฟเฟกต์     - | zoom 1.15 | transition <name> | effect <name> | filter <name>
                                 (comma-separated; transition = INTO this row)
  SFX         - | role[@cut|@<word>|@<sec>]  roles from _system/engine/sfx_roles.py
  ซับ          ✓ | -              "-" hides the captions over this row

Settings (bullet list under "## ตั้งค่า"): vo, captions (plain N | karaoke N |
accumulate | none), caption_flags, footage_audio (mute|keep), fx_source, sfx_volume,
approved (no|yes).

Usage:
  python3 vo_assemble.py prep  --project 1004 [--work projects/2026-10-04-1004] [--vo vo.wav]
  python3 vo_assemble.py build --project 1004 --plan projects/2026-10-04-1004/PLAN.md
  python3 vo_assemble.py apply --project 1004 --plan projects/2026-10-04-1004/PLAN.md
"""
from __future__ import annotations
import _starter  # noqa: F401,E402  (starter: runtime PATH + prepared Python)

import argparse
import array
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _capcut_lib import (  # noqa: E402
    CAPCUT_DRAFTS_DIR, KIT_ROOT, SKILL_DIR, MS, S, draft_state_path, emit_err,
    emit_ok, find_timeline_uuid, is_capcut_main_running, read_media_panel,
    resolve_project,
)

SCRIPTS = Path(__file__).resolve().parent
SKILLS = SKILL_DIR.parent
TRANSCRIBE = SKILLS / "indigo11-transcribe" / "scripts"
WATCH = SKILLS / "indigo11-watch" / "scripts"
ENGINE = KIT_ROOT / "_system" / "engine"

SNAP_MS = 80            # a seam closer than this to the previous row's end is snapped
TAIL_EXTEND_MS = 300    # last row may stop this short of the VO end; it is extended
FRAME_MS = 40
WHOOSH_ROLES = ("whoosh", "swish", "swipe", "riser")
DEFAULT_SFX_VOLUME = 0.5
YES = {"yes", "y", "ok", "true", "ใช่", "อนุมัติ", "อนุมัติแล้ว", "approved"}


# ----------------------------------------------------------------- small helpers

def ffprobe_ms(path) -> int:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(path)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        return int(round(float(r.stdout.strip()) * 1000))
    except Exception:
        return 0


_PEAK_CACHE: dict = {}


def peak_offset_ms(path, limit_ms=600) -> int:
    """Where the loudest sample of a short sound sits (ms from its start).

    A whoosh is placed so its PEAK lands on the cut, not its first sample - the
    owner's rule "whoosh starts slightly before the motion". One ffmpeg decode to
    8 kHz mono s16, stdlib only.
    """
    key = str(path)
    if key in _PEAK_CACHE:
        return _PEAK_CACHE[key]
    off = 0
    try:
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-t", "2", "-ac", "1",
                            "-ar", "8000", "-f", "s16le", "-"], capture_output=True, timeout=60)
        a = array.array("h")
        a.frombytes(r.stdout[: len(r.stdout) // 2 * 2])
        if len(a):
            i = max(range(len(a)), key=lambda k: abs(a[k]))
            off = min(limit_ms, int(i / 8))
    except Exception:
        off = 0
    _PEAK_CACHE[key] = off
    return off


def parse_time_s(txt: str):
    """'1.5' / '01:02.5' / '0:03' -> seconds (float) or None."""
    t = (txt or "").strip()
    if not t:
        return None
    try:
        parts = [float(p) for p in t.split(":")]
    except ValueError:
        return None
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def parse_range_ms(txt: str):
    m = re.match(r"^\s*([0-9:.]+)\s*[-–—]\s*([0-9:.]+)\s*$", txt or "")
    if not m:
        return None
    a, b = parse_time_s(m.group(1)), parse_time_s(m.group(2))
    if a is None or b is None:
        return None
    return int(round(a * 1000)), int(round(b * 1000))


def is_empty(cell: str) -> bool:
    return (cell or "").strip() in ("", "-", "–", "—", "ไม่มี", "none", "n/a")


def slugify(s: str) -> str:
    s = re.sub(r"[^\w\-]+", "-", s.strip(), flags=re.UNICODE).strip("-").lower()
    return s or "vo"


def run_json(cmd: list) -> tuple:
    """Run a kit script; (returncode, last-line JSON or {}, stderr tail)."""
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    last = {}
    for line in reversed((r.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                last = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    return r.returncode, last, (r.stderr or "")[-600:]


def media_entries(project_dir: Path) -> list:
    """Every live Media-panel entry (video / photo / music), ghosts filtered (rule #13)."""
    meta_p = project_dir / "draft_meta_info.json"
    if not meta_p.exists():
        return []
    meta = json.load(open(meta_p, encoding="utf-8"))
    out = []
    for g in meta.get("draft_materials", []):
        if g.get("type") != 0:
            continue
        for v in g.get("value", []):
            p = v.get("file_Path") or v.get("file_path") or ""
            if not p or not os.path.exists(p):
                continue
            mt = str(v.get("metetype", "")).lower() or "video"
            dur_us = int(v.get("duration", 0) or 0)
            if mt != "photo" and dur_us < 100_000:
                continue
            out.append({"name": v.get("extra_info") or os.path.basename(p), "path": p,
                        "kind": mt, "duration_ms": dur_us // MS,
                        "width": v.get("width", 0), "height": v.get("height", 0)})
    return out


def main_video_segments(project_dir: Path) -> int:
    try:
        d = json.load(open(draft_state_path(project_dir), encoding="utf-8"))
    except Exception:
        return 0
    for t in d.get("tracks") or []:
        if t.get("type") == "video":
            return len(t.get("segments") or [])
    return 0


def transcript_dir_for(vo_path: str) -> Path:
    return KIT_ROOT / "projects" / "_work" / "transcribe" / Path(vo_path).stem


# ----------------------------------------------------------------- PLAN.md parsing

COLS = [  # (key, header keywords) - checked in this order, first hit wins
    ("idx", ("#",)),
    ("sfx", ("sfx", "เสียงประกอบ", "เสียงเอฟเฟกต์")),
    ("broll", ("b-roll", "broll", "บีโรล")),
    ("fx", ("เอฟเฟกต์", "effect", "fx", "transition")),
    ("clip", ("ฟุตเทจ", "footage", "clip", "คลิป")),
    ("in", ("เริ่มที่", "in-point", "in point", "in")),
    ("said", ("คำพูด", "said", "line", "text", "บท")),
    ("cap", ("ซับ", "caption")),
    ("vo", ("เวลา", "vo", "time")),
]


def _col_key(header: str):
    h = header.strip().lower()
    for key, words in COLS:
        for w in words:
            if key == "idx" and h == "#":
                return key
            if key == "in" and w == "in" and h != "in":
                continue
            if key != "idx" and w in h:
                return key
    return None


def _cells(line: str) -> list:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def parse_plan(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    settings, rows, errors = {}, [], []
    section = ""
    for ln in lines:
        if ln.startswith("## "):
            section = ln[3:].strip().lower()
            continue
        if ("ตั้งค่า" in section or "setting" in section) and re.match(r"^\s*[-*]\s+\w", ln):
            m = re.match(r"^\s*[-*]\s+([A-Za-z_]+)\s*:\s*(.*)$", ln)
            if m:
                val = re.split(r"\s{2,}#|\s+#\s", m.group(2), maxsplit=1)[0].strip()
                settings[m.group(1).lower()] = val.strip("`")
    # the first table that has both a VO-time column and a footage column
    i = 0
    header = None
    while i < len(lines):
        if lines[i].strip().startswith("|") and i + 1 < len(lines) and \
                re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            keys = [_col_key(c) for c in _cells(lines[i])]
            if "vo" in keys and "clip" in keys:
                header = keys
                i += 2
                break
        i += 1
    if header is None:
        errors.append("PLAN.md ไม่มีตารางช็อต (needs a table with 'เวลา VO' and 'ฟุตเทจ' columns)")
        return {"settings": settings, "rows": rows, "errors": errors}
    while i < len(lines) and lines[i].strip().startswith("|"):
        cells = _cells(lines[i])
        row = {"line": i + 1}
        for k, c in zip(header, cells):
            if k:
                row[k] = c
        rows.append(row)
        i += 1
    return {"settings": settings, "rows": rows, "errors": errors}


# ----------------------------------------------------------------- transcript

def load_transcript(tdir: Path) -> dict:
    out = {"raw": None, "phrases": [], "chars": []}
    raw_p = tdir / "raw-elevenlabs.json"
    if raw_p.exists():
        raw = json.load(open(raw_p, encoding="utf-8"))
        out["raw"] = raw_p
        for c in raw.get("words_raw") or raw.get("words") or []:
            t = c.get("text") or ""
            if c.get("start") is None or not t.strip() or t.strip().startswith("[") \
                    or c.get("type") == "audio_event":
                continue
            out["chars"].append((t, float(c["start"]), float(c.get("end", c["start"]))))
        out["phrases"] = [p for p in (raw.get("words") or []) if p.get("text")]
    cap_p = tdir / "captions.json"
    if cap_p.exists() and not out["phrases"]:
        out["phrases"] = json.load(open(cap_p, encoding="utf-8")).get("words") or []
    return out


def find_word_ms(chars: list, word: str, a_ms: int, b_ms: int):
    """Start time (ms) of `word` spoken inside [a_ms, b_ms), from char-level timing."""
    inside = [(t, s) for t, s, _e in chars if a_ms - 20 <= s * 1000 < b_ms]
    if not inside:
        return None
    joined, starts = "", []
    for t, s in inside:
        for ch in t:
            joined += ch
            starts.append(s)
    w = word.replace(" ", "")
    flat = joined.replace(" ", "")
    flat_starts = [st for ch, st in zip(joined, starts) if ch != " "]
    k = flat.find(w)
    if k < 0:
        k = flat.lower().find(w.lower())
    if k < 0:
        return None
    return int(round(flat_starts[k] * 1000))


def words_for_n(tdir: Path, work: Path, n: int) -> Path:
    """words.json cut for N words/caption: the transcript's own (maybe pass-2) file
    when it matches N, else re-cut from raw by word_segment.py (no STT call)."""
    own = tdir / "words.json"
    if own.exists():
        try:
            if int(json.load(open(own, encoding="utf-8")).get("words_per_group") or 0) == n:
                return own
        except Exception:
            pass
    out = work / f"words-n{n}.json"
    if out.exists():
        return out
    raw = tdir / "raw-elevenlabs.json"
    r = subprocess.run([sys.executable, str(TRANSCRIBE / "word_segment.py"), str(raw),
                        "-o", str(out), "--words-per-group", str(n)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"word_segment failed: {(r.stderr or r.stdout)[-300:]}")
    return out


# ----------------------------------------------------------------- prep

PLAN_HEAD = """# PLAN · จัดคลิปตามเสียงพากย์ · {project}

เสียงพากย์คือนาฬิกาหลัก: 1 แถว = 1 ช่วงเสียงพากย์ ภาพในแถวนั้นยาวเท่าช่วงเสียงพอดี
AI เติมตาราง → ผู้ใช้อ่าน/แก้ → เปลี่ยน `approved: no` เป็น `approved: yes` → AI ค่อยเขียนลง CapCut
(ยังไม่มีอะไรถูกเขียนลง CapCut จนกว่าจะอนุมัติ)

## ตั้งค่า
- project: {project}
- vo: {vo}
- captions: karaoke 2          # plain N | karaoke N | accumulate | none
- caption_flags: --vertical-pct 78
- footage_audio: mute          # mute = ปิดเสียงฟุตเทจ ให้เสียงพากย์ชัด | keep
- fx_source: {fx_source}       # โปรเจกต์ CapCut ที่เก็บ transition/effect/filter ไว้ (ว่าง = ไม่ใช้)
- sfx_volume: 0.5
- approved: no

## ช็อต
| # | เวลา VO | คำพูด | ฟุตเทจ | เริ่มที่ | B-roll | เอฟเฟกต์ | SFX | ซับ |
|---|---|---|---|---|---|---|---|---|
"""

PLAN_TAIL = """
## ฟุตเทจที่มี (ดูแล้วด้วย indigo11-watch ระดับ 1)
{inventory}

## B-roll ที่ต้องหา
- (ถ้าแถวไหนไม่มีฟุตเทจที่ตรงคำพูด เขียน `ต้องหา: <คำค้นภาษาอังกฤษ>` ในช่อง B-roll แล้วจดไว้ตรงนี้)

## วิธีอ่านตาราง
- **เวลา VO** วินาทีในเสียงพากย์ (= เวลาบน timeline) แถวต้องต่อกันพอดี ไม่มีรู
- **ฟุตเทจ** ชื่อไฟล์ใน Media panel · **เริ่มที่** วินาทีในคลิปนั้น (ใช้ยาวเท่าแถว)
- **B-roll** ภาพซ้อนบนฟุตเทจทั้งแถว `file.mp4` หรือ `file.mp4@2.5` (เริ่มที่วินาที 2.5 ของไฟล์)
- **เอฟเฟกต์** `zoom 1.15` (ซูมเข้า) · `transition <ชื่อ>` (เปลี่ยนฉากเข้าแถวนี้) · `effect <ชื่อ>` · `filter <ชื่อ>`
  ชื่อ transition/effect/filter ต้องมีในโปรเจกต์ `fx_source` (เช่น fxlib)
- **SFX** role จากชุดเสียงของ starter: pop click tick swish whoosh whoosh_small impact riser sparkle success notify coin
  `whoosh@cut` ตรงจุดตัด · `pop@ราคา` ตรงคำว่า "ราคา" · `impact@3.2` ที่วินาที 3.2
- **ซับ** ✓ = มีซับ · - = ไม่ขึ้นซับช่วงนี้
"""


def cmd_prep(args) -> int:
    project_dir = resolve_project(args.project, args.drafts_dir)
    if not project_dir.exists():
        emit_err(f"ไม่พบ project {args.project} (project not found)", project_dir=str(project_dir))
    media = media_entries(project_dir)
    videos = [m for m in media if m["kind"] in ("video", "movie")]
    audios = [m for m in media if m["kind"] == "music"]
    if not videos:
        emit_err("ไม่มีฟุตเทจใน Media panel (no footage in the Media panel)",
                 fix="เปิด CapCut ลากฟุตเทจทั้งหมด + ไฟล์เสียงพากย์ลง Media panel (ไม่ใช่ timeline) แล้ว Cmd+S → Cmd+Q")
    if not audios:
        emit_err("ไม่มีไฟล์เสียงพากย์ใน Media panel (no voice-over audio in the Media panel)",
                 fix="ลากไฟล์เสียงพากย์ลง Media panel แล้ว save + quit")
    notes = []
    if args.vo:
        hit = [a for a in audios if args.vo.lower() in a["name"].lower()]
        if not hit:
            emit_err(f"ไม่เจอเสียงพากย์ '{args.vo}' ใน Media panel",
                     audio_in_panel=[a["name"] for a in audios])
        vo = hit[0]
    else:
        vo = max(audios, key=lambda a: a["duration_ms"])
        if len(audios) > 1:
            notes.append(f"มีไฟล์เสียง {len(audios)} ไฟล์ - เลือก {vo['name']} (ยาวสุด) เป็นเสียงพากย์; "
                         f"ไม่ใช่ให้ส่ง --vo <ชื่อ>")
    vo_ms = ffprobe_ms(vo["path"]) or vo["duration_ms"]

    if main_video_segments(project_dir):
        notes.append("timeline มีคลิปอยู่แล้ว - โหมดนี้สร้าง timeline ใหม่ จะ apply ไม่ได้ "
                     "(the timeline is not blank; apply will refuse - make a NEW project)")

    date = f"{int(time.strftime('%Y')) + 543}-{time.strftime('%m-%d')}"   # Buddhist Era year: 2026 -> 2569
    work = Path(args.work) if args.work else KIT_ROOT / "projects" / f"{date}-{slugify(project_dir.name)}"
    if not work.is_absolute():
        work = KIT_ROOT / work
    (work / "assets").mkdir(parents=True, exist_ok=True)
    (work / "vo").mkdir(exist_ok=True)

    # transcript (cached; indigo11-transcribe)
    tdir = Path(args.transcript) if args.transcript else transcript_dir_for(vo["path"])
    if not (tdir / "raw-elevenlabs.json").exists():
        tdir.mkdir(parents=True, exist_ok=True)
        rc, out, err = run_json([sys.executable, str(TRANSCRIBE / "transcribe.py"), vo["path"],
                                 "--provider", args.provider, "--lang", args.lang,
                                 "--save-all", str(tdir), "--words-per-group", str(args.words_per_group)])
        if not (tdir / "raw-elevenlabs.json").exists():
            emit_err("ถอดเสียงพากย์ไม่สำเร็จ (voice-over transcription failed)", detail=err[-300:],
                     fix="ใส่ ELEVENLABS_API_KEY ใน .env แล้วรันใหม่ หรือส่ง --transcript <โฟลเดอร์ที่มี "
                         "raw-elevenlabs.json> (put the key in .env, or pass an existing transcript)")
    tr = load_transcript(tdir)
    if not tr["phrases"]:
        emit_err(f"transcript ว่าง: {tdir}", fix="ตรวจไฟล์เสียงพากย์ แล้วถอดใหม่")

    # watch every footage clip (indigo11-watch level 1)
    watched, watch_errors = [], []
    for v in videos:
        wdir = work / "watch" / slugify(Path(v["name"]).stem)
        entry = {"clip": v["name"], "duration_s": round(v["duration_ms"] / 1000, 2),
                 "size": f'{v["width"]}x{v["height"]}', "dir": str(wdir)}
        if not args.no_watch:
            wdir.mkdir(parents=True, exist_ok=True)
            if not (wdir / "manifest.json").exists():
                cmd = [sys.executable, str(SKILL_DIR / "scripts" / "frame_grid.py"), v["path"], "--out-dir", str(wdir),
                       "--cells", str(args.cells)]
                r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
                if r.returncode != 0:
                    watch_errors.append({"clip": v["name"], "step": "frames", "error": (r.stderr or r.stdout)[-300:]})
            entry["index_md"] = str(wdir / "index.md") if (wdir / "index.md").exists() else None
            entry["grids"] = sorted(str(p) for p in wdir.glob("grid_*.*")
                                    if p.suffix.lower() in (".png", ".jpg"))[:40]
        watched.append(entry)

    # rows: one per VO phrase, short phrases merged, contiguous from 0 to the VO end
    rows, cur = [], None
    for p in tr["phrases"]:
        s, e = int(float(p["start"]) * 1000), int(float(p["end"]) * 1000)
        if cur and (e - cur["start"] < args.min_row_ms or cur["end"] - cur["start"] < args.min_row_ms):
            cur["end"] = e
            cur["text"] += p["text"]
        else:
            if cur:
                rows.append(cur)
            cur = {"start": s, "end": e, "text": p["text"]}
    if cur:
        rows.append(cur)
    rows[0]["start"] = 0
    for a, b in zip(rows, rows[1:]):
        a["end"] = b["start"]
    rows[-1]["end"] = max(rows[-1]["end"], vo_ms)

    fx_default = "fxlib" if (resolve_project("fxlib", args.drafts_dir)).exists() else ""
    body = PLAN_HEAD.format(project=project_dir.name, vo=vo["name"], fx_source=fx_default)
    for k, r in enumerate(rows, 1):
        said = r["text"].replace("|", "/").strip()
        body += f"| {k} | {r['start']/1000:.2f}-{r['end']/1000:.2f} | {said} | ? | 0 | - | - | - | ✓ |\n"
    inv = "\n".join(
        f"- `{w['clip']}` {w['duration_s']}s {w['size']}"
        + (f" · index: `{os.path.relpath(w['index_md'], work)}`" if w.get("index_md") else "")
        for w in watched)
    body += PLAN_TAIL.format(inventory=inv or "-")

    plan = work / "PLAN.md"
    wrote = plan
    if plan.exists():
        if args.overwrite_plan:
            shutil.copy2(plan, work / f"PLAN.md.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        else:
            wrote = work / "PLAN.new.md"
            notes.append(f"มี PLAN.md อยู่แล้ว - ไม่เขียนทับ เขียนร่างใหม่ที่ {wrote.name}")
    wrote.write_text(body, encoding="utf-8")

    src_md = work / "assets" / "SOURCES.md"
    if not src_md.exists():
        src_md.write_text("# Sources\n\n## Media (every file in assets/ and every SFX)\n"
                          "| file | what | source URL | licence | on-screen credit |\n"
                          "|---|---|---|---|---|\n", encoding="utf-8")
    pj = work / "project.json"
    if not pj.exists():
        pj.write_text(json.dumps({"date": date, "style": "capcut-vo", "slug": work.name,
                                  "title": f"CapCut {project_dir.name} (เสียงพากย์)",
                                  "status": "draft", "capcut_project": project_dir.name},
                                 ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    json.dump({"project": project_dir.name, "vo": vo, "vo_ms": vo_ms, "transcript_dir": str(tdir),
               "footage": watched, "audio_in_panel": [a["name"] for a in audios]},
              open(work / "vo" / "media.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    emit_ok(op="vo-prep", project=project_dir.name, work=str(work), plan=str(wrote),
            vo=vo["name"], vo_s=round(vo_ms / 1000, 2), rows=len(rows), footage=len(videos),
            transcript_dir=str(tdir), watch=watched, watch_errors=watch_errors, notes=notes,
            capcut_written=False,
            next_step="อ่าน watch/*/index.md + grid ทุกคลิป แล้วเติมคอลัมน์ ฟุตเทจ/เริ่มที่/B-roll/เอฟเฟกต์/SFX "
                      "ใน PLAN.md → vo_assemble.py build → ให้ผู้ใช้อนุมัติ (approved: yes)")
    return 0


# ----------------------------------------------------------------- build

def _resolve_file(name: str, media: list, work: Path, kinds: tuple):
    """A Media-panel entry by name, else a file in <work>/assets / <work> / absolute."""
    n = name.strip().strip("`")
    low = n.lower()
    for m in media:
        if m["kind"] in kinds and m["name"].lower() == low:
            return dict(m, in_panel=True)
    for m in media:
        if m["kind"] in kinds and Path(m["name"]).stem.lower() == Path(low).stem:
            return dict(m, in_panel=True)
    for cand in (Path(n).expanduser(), work / "assets" / n, work / n):
        if cand.is_file():
            p = cand.resolve()
            for m in media:  # imported earlier under its absolute path
                if os.path.realpath(m["path"]) == str(p):
                    return dict(m, in_panel=True)
            ext = p.suffix.lower()
            kind = "photo" if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic") else "video"
            return {"name": p.name, "path": str(p), "kind": kind,
                    "duration_ms": ffprobe_ms(p) if kind == "video" else 0, "in_panel": False}
    return None


def _fx_catalog(fx_dir: Path) -> dict:
    cat = {"transition": [], "effect": [], "filter": []}
    try:
        d = json.load(open(draft_state_path(fx_dir), encoding="utf-8"))
    except Exception:
        return cat
    m = d.get("materials") or {}
    for c, kind in (("transitions", "transition"), ("video_effects", "effect"),
                    ("effects", "effect"), ("filters", "filter")):
        for it in m.get(c) or []:
            if isinstance(it, dict) and it.get("name"):
                cat[kind].append(it["name"])
    return cat


def _sfx_resolver():
    sys.path.insert(0, str(ENGINE))
    try:
        import sfx_roles  # noqa: E402
        return sfx_roles.resolve
    except Exception:
        return None


def build_plan(args, plan_path: Path, write=True) -> dict:
    """Validate PLAN.md and (when clean) write every timing file. Never writes CapCut."""
    plan = parse_plan(plan_path)
    st, errors, warnings = plan["settings"], list(plan["errors"]), []
    work = plan_path.resolve().parent
    out = Path(args.out) if getattr(args, "out", None) else work / "vo" / "build"
    project = args.project or st.get("project")
    if not project:
        emit_err("ไม่รู้ชื่อ project (give --project or 'project:' in PLAN.md)")
    project_dir = resolve_project(project, args.drafts_dir)
    if not project_dir.exists():
        emit_err(f"ไม่พบ project {project} (project not found)", project_dir=str(project_dir))
    media = media_entries(project_dir)
    panel_videos = read_media_panel(project_dir)
    idx_by_path = {os.path.realpath(c["path"]): c["index"] for c in panel_videos}

    # voice-over
    vo_name = st.get("vo", "")
    vo = next((m for m in media if m["kind"] == "music" and m["name"].lower() == vo_name.lower()), None) or \
        next((m for m in media if m["kind"] == "music" and vo_name and vo_name.lower() in m["name"].lower()), None)
    if vo is None:
        emit_err(f"ไม่เจอเสียงพากย์ '{vo_name}' ใน Media panel (voice-over not in the Media panel)",
                 audio_in_panel=[m["name"] for m in media if m["kind"] == "music"],
                 fix="ลากไฟล์เสียงพากย์ลง Media panel หรือแก้ 'vo:' ใน PLAN.md")
    vo_ms = ffprobe_ms(vo["path"]) or vo["duration_ms"]
    tdir = None
    if st.get("transcript"):
        tdir = Path(st["transcript"])
    else:
        try:  # prep records where it found/made the transcript
            tdir = Path(json.load(open(work / "vo" / "media.json", encoding="utf-8"))["transcript_dir"])
        except Exception:
            tdir = None
    if tdir is None or not (tdir / "raw-elevenlabs.json").exists():
        tdir = transcript_dir_for(vo["path"])
    tr = load_transcript(tdir)
    if not tr["raw"]:
        errors.append(f"ไม่มี transcript ของเสียงพากย์ที่ {tdir} - รัน vo_assemble.py prep ก่อน")

    # captions setting
    cap_raw = (st.get("captions") or "karaoke 2").strip().lower()
    cap_mode, cap_n = "none", 2
    m = re.match(r"^(plain|karaoke|accumulate|none|ไม่มี)\s*(\d+)?", cap_raw)
    if not m:
        errors.append(f"captions: '{cap_raw}' ไม่รู้จัก (plain N | karaoke N | accumulate | none)")
    else:
        cap_mode = "none" if m.group(1) in ("none", "ไม่มี") else m.group(1)
        cap_n = int(m.group(2) or 2)
    footage_mute = (st.get("footage_audio") or "mute").strip().lower() != "keep"
    try:
        sfx_base = float(st.get("sfx_volume") or DEFAULT_SFX_VOLUME)
    except ValueError:
        sfx_base = DEFAULT_SFX_VOLUME
    fx_source = (st.get("fx_source") or "").strip()
    fx_dir = resolve_project(fx_source, args.drafts_dir) if fx_source else None
    fx_cat = _fx_catalog(fx_dir) if fx_dir and fx_dir.exists() else None
    resolve_role = _sfx_resolver()

    rows = []
    for k, r in enumerate(plan["rows"]):
        ln = r["line"]
        rng = parse_range_ms(r.get("vo", ""))
        if not rng:
            errors.append(f"บรรทัด {ln}: เวลา VO '{r.get('vo','')}' อ่านไม่ได้ (use 1.20-3.45)")
            continue
        row = {"n": r.get("idx") or str(k + 1), "line": ln, "start_ms": rng[0], "end_ms": rng[1],
               "said": r.get("said", ""), "show_caption": not is_empty(r.get("cap", "✓")),
               "fx": [], "sfx": [], "broll": None}
        if row["end_ms"] <= row["start_ms"]:
            errors.append(f"บรรทัด {ln}: เวลาจบต้องมากกว่าเวลาเริ่ม")
        clip_cell = (r.get("clip") or "").strip()
        if is_empty(clip_cell) or clip_cell == "?":
            errors.append(f"แถว {row['n']} (บรรทัด {ln}): ยังไม่ได้เลือกฟุตเทจ")
        else:
            c = _resolve_file(clip_cell, media, work, ("video", "movie"))
            if c is None:
                errors.append(f"แถว {row['n']}: ไม่เจอฟุตเทจ '{clip_cell}' ใน Media panel หรือ {work.name}/assets")
            elif c["kind"] == "photo":
                errors.append(f"แถว {row['n']}: '{clip_cell}' เป็นรูปนิ่ง - ช่องฟุตเทจต้องเป็นวิดีโอ "
                              f"(ใส่รูปในช่อง B-roll แทน)")
                c = None
            row["clip"] = c
        in_s = parse_time_s((r.get("in") or "0").split("-")[0]) if not is_empty(r.get("in", "")) else 0.0
        if in_s is None:
            errors.append(f"แถว {row['n']}: เริ่มที่ '{r.get('in')}' อ่านไม่ได้")
            in_s = 0.0
        row["in_ms"] = int(round(in_s * 1000))
        b = (r.get("broll") or "").strip()
        if not is_empty(b):
            if re.match(r"^(ต้องหา|need|todo|find)\s*[:：]", b, re.I):
                errors.append(f"แถว {row['n']}: B-roll ยังไม่ได้หา ('{b}') - หาด้วย indigo11-find-assets "
                              f"ลง {work.name}/assets แล้วใส่ชื่อไฟล์")
            else:
                name, _, at = b.partition("@")
                bf = _resolve_file(name, media, work, ("video", "movie", "photo"))
                if bf is None:
                    errors.append(f"แถว {row['n']}: ไม่เจอไฟล์ B-roll '{name.strip()}'")
                else:
                    bin_s = parse_time_s(at) if at else 0.0
                    row["broll"] = dict(bf, in_ms=int(round((bin_s or 0) * 1000)))
        for item in [x.strip() for x in re.split(r"[,+]", r.get("fx") or "") if x.strip()]:
            if is_empty(item):
                continue
            mm = re.match(r"^(zoom|ซูม|transition|effect|filter)\s+(.+)$", item, re.I)
            if not mm:
                errors.append(f"แถว {row['n']}: เอฟเฟกต์ '{item}' ไม่รู้จัก (zoom 1.2 | transition X | effect X | filter X)")
                continue
            kind, val = mm.group(1).lower(), mm.group(2).strip()
            if kind in ("zoom", "ซูม"):
                try:
                    z = float(val)
                    assert 1.0 <= z <= 3.0
                    row["zoom"] = z
                except Exception:
                    errors.append(f"แถว {row['n']}: zoom ต้องเป็นตัวเลข 1.0-3.0")
                continue
            if fx_cat is None:
                errors.append(f"แถว {row['n']}: '{item}' ต้องมี fx_source (โปรเจกต์ CapCut ที่ใส่ {kind} นี้ไว้) "
                              f"- ตอนนี้ fx_source='{fx_source}' ไม่มี/ไม่เจอ")
                continue
            if not any(val.lower() in nm.lower() for nm in fx_cat[kind]):
                errors.append(f"แถว {row['n']}: ไม่เจอ {kind} '{val}' ใน {fx_source} "
                              f"(มี: {', '.join(sorted(set(fx_cat[kind]))[:12]) or '-'})")
                continue
            if kind == "transition" and k == 0:
                errors.append(f"แถว {row['n']}: แถวแรกใส่ transition ไม่ได้ (ไม่มีช็อตก่อนหน้า)")
                continue
            row["fx"].append({"kind": kind, "name": val})
        for item in [x.strip() for x in (r.get("sfx") or "").split(",") if x.strip()]:
            if is_empty(item):
                continue
            role, _, where = item.partition("@")
            row["sfx"].append({"role": role.strip(), "where": (where or "cut").strip()})
        rows.append(row)

    if not rows and not errors:
        errors.append("ตารางช็อตว่าง")
    rows.sort(key=lambda x: x["start_ms"])

    # coverage: the VO is the clock - rows must tile [0, VO end] with no hole / overlap
    if rows:
        if rows[0]["start_ms"] > SNAP_MS:
            errors.append(f"แถวแรกเริ่ม {rows[0]['start_ms']/1000:.2f}s - ต้องเริ่มที่ 0 (ภาพดำตอนต้น)")
        rows[0]["start_ms"] = 0
        for a, b in zip(rows, rows[1:]):
            gap = b["start_ms"] - a["end_ms"]
            if abs(gap) <= SNAP_MS:
                a["end_ms"] = b["start_ms"]
            elif gap > 0:
                errors.append(f"รูที่ {a['end_ms']/1000:.2f}-{b['start_ms']/1000:.2f}s ระหว่างแถว {a['n']} กับ {b['n']} "
                              f"(ยืดแถว {a['n']} ให้ถึง {b['start_ms']/1000:.2f})")
            else:
                errors.append(f"แถว {a['n']} กับ {b['n']} ทับกัน {(-gap)/1000:.2f}s")
        short = vo_ms - rows[-1]["end_ms"]
        if 0 < short <= TAIL_EXTEND_MS:
            rows[-1]["end_ms"] = vo_ms
        elif short > TAIL_EXTEND_MS:
            errors.append(f"ภาพจบที่ {rows[-1]['end_ms']/1000:.2f}s แต่เสียงพากย์ยาว {vo_ms/1000:.2f}s "
                          f"(ภาพหมดก่อนเสียง - ยืดแถวสุดท้าย)")
        elif short < -SNAP_MS:
            warnings.append(f"ภาพยาวเกินเสียงพากย์ {(-short)/1000:.2f}s (ท้ายคลิปไม่มีเสียงพูด)")

    # per-row footage room, SFX placement
    sfx_list, overlays, fx_ops, imports = [], [], [], {}
    for k, row in enumerate(rows):
        dur = row["end_ms"] - row["start_ms"]
        c = row.get("clip")
        if c:
            if c["kind"] != "photo":
                room = c["duration_ms"] - row["in_ms"]
                if room + FRAME_MS < dur:
                    errors.append(f"แถว {row['n']}: {c['name']} เริ่มที่ {row['in_ms']/1000:.2f}s เหลือแค่ "
                                  f"{max(0, room)/1000:.2f}s แต่แถวยาว {dur/1000:.2f}s "
                                  f"(เลื่อนจุดเริ่ม หรือแบ่งแถวเป็น 2 ช็อต)")
            if not c["in_panel"]:
                imports[c["path"]] = c["name"]
        bro = row.get("broll")
        if bro:
            if not bro["in_panel"]:
                imports[bro["path"]] = bro["name"]
            overlays.append({"file": bro["path"], "at_ms": row["start_ms"], "duration_ms": dur,
                             "source_in_ms": bro["in_ms"], "volume": 0.0, "x": 0.0, "y": 0.0,
                             "scale": 1.0, "row": row["n"]})
        for fx in row["fx"]:
            if fx["kind"] == "transition":
                fx_ops.append({"kind": "transition", "name": fx["name"], "start_ms": row["start_ms"], "row": row["n"]})
            else:
                fx_ops.append({"kind": fx["kind"], "name": fx["name"], "start_ms": row["start_ms"],
                               "dur_ms": dur, "row": row["n"]})
        for j, sx in enumerate(row["sfx"]):
            hits = resolve_role(sx["role"]) if resolve_role else []
            if not hits:
                errors.append(f"แถว {row['n']}: SFX role '{sx['role']}' ไม่มีไฟล์ในเครื่อง "
                              f"(ดู: python3 _system/engine/sfx_roles.py)")
                continue
            f, opts, src = hits[(k + j) % len(hits)]
            w = sx["where"]
            if w in ("cut", "ตัด", "start", ""):
                at = row["start_ms"]
                if any(x in sx["role"] for x in WHOOSH_ROLES) or opts.get("align") == "peak":
                    at -= peak_offset_ms(f)
            elif parse_time_s(w) is not None and re.match(r"^[0-9:.]+$", w):
                at = int(round(parse_time_s(w) * 1000))
            else:
                at = find_word_ms(tr["chars"], w, row["start_ms"], row["end_ms"])
                if at is None:
                    errors.append(f"แถว {row['n']}: ไม่เจอคำ '{w}' ในเสียงพากย์ช่วง "
                                  f"{row['start_ms']/1000:.2f}-{row['end_ms']/1000:.2f}s")
                    continue
                if opts.get("align") == "peak":
                    at -= peak_offset_ms(f)
            at = max(0, at)
            gain = opts.get("gain_db")
            vol = sfx_base * (10 ** ((float(gain) + 10) / 20) if gain is not None else 1.0)
            ent = {"file": str(f), "at_ms": int(at), "volume": round(min(1.0, max(0.05, vol)), 3),
                   "role": sx["role"], "source": src, "row": row["n"]}
            if opts.get("max_len"):
                ent["max_ms"] = int(float(opts["max_len"]) * 1000)
            sfx_list.append(ent)
            imports[str(f)] = Path(f).name

    result = {"project": project_dir.name, "project_dir": str(project_dir), "plan": str(plan_path),
              "work": str(work), "out": str(out), "settings": st, "errors": errors,
              "warnings": warnings, "rows": len(rows), "vo": vo["name"], "vo_ms": vo_ms,
              "imports": sorted(imports), "overlays": len(overlays), "fx": len(fx_ops),
              "sfx": len(sfx_list), "captions": cap_mode if cap_mode == "none" else f"{cap_mode} {cap_n}",
              "approved": (st.get("approved") or "").strip().lower() in YES}
    if errors or not write:
        return result

    out.mkdir(parents=True, exist_ok=True)
    segs = []
    for row in rows:
        c = row["clip"]
        dur = row["end_ms"] - row["start_ms"]
        seg = {"clip_name": c["name"], "clip_path": c["path"],
               "clip_index": idx_by_path.get(os.path.realpath(c["path"])),
               "source_start_ms": row["in_ms"], "source_end_ms": row["in_ms"] + dur,
               "timeline_start_ms": row["start_ms"], "timeline_end_ms": row["end_ms"],
               "row": row["n"]}
        if footage_mute:
            seg["volume"] = 0.0
        if row.get("zoom"):
            seg["scale"] = row["zoom"]
        segs.append(seg)
    edl = {"master_clock": "vo", "mode": "vo-assemble", "project": project_dir.name,
           "vo": {"name": vo["name"], "path": vo["path"], "duration_ms": vo_ms},
           "footage_audio": "mute" if footage_mute else "keep",
           "segments": segs, "total_duration_ms": rows[-1]["end_ms"],
           "rows": [{"n": r["n"], "start_ms": r["start_ms"], "end_ms": r["end_ms"], "said": r["said"],
                     "clip": r["clip"]["name"], "in_ms": r["in_ms"],
                     "broll": r["broll"]["name"] if r.get("broll") else None,
                     "fx": r["fx"] + ([{"kind": "zoom", "value": r["zoom"]}] if r.get("zoom") else []),
                     "sfx": [s for s in sfx_list if s["row"] == r["n"]],
                     "caption": r["show_caption"]} for r in rows],
           "warnings": [], "notes": warnings}
    json.dump(edl, open(out / "edl-vo.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump({"sfx": [{"file": vo["path"], "at_ms": 0, "volume": 1.0}]},
              open(out / "vo-timing.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump({"sfx": sfx_list}, open(out / "sfx-timing.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    json.dump({"overlays": overlays}, open(out / "overlay-timing.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    json.dump({"fx_source": fx_source, "ops": fx_ops}, open(out / "fx.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    json.dump({"clips": panel_videos, "per_clip": [], "master_clock": "vo"},
              open(out / "stage-vo.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    cap_file, cap_flags = None, []
    if cap_mode != "none" and tr["raw"]:
        wf = words_for_n(tdir, out, cap_n)
        words = json.load(open(wf, encoding="utf-8"))
        hidden = [(r["start_ms"] / 1000, r["end_ms"] / 1000) for r in rows if not r["show_caption"]]

        def visible(g):
            mid = (float(g["start"]) + float(g["end"])) / 2
            return not any(a <= mid < b for a, b in hidden)
        words["groups"] = [g for g in words.get("groups", []) if visible(g)]
        words["source"] = str(wf)
        cap_file = out / "captions-words.json"
        json.dump(words, open(cap_file, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        if cap_mode == "karaoke":
            cap_flags.append("--karaoke")
        elif cap_mode == "accumulate":
            cap_flags.append("--accumulate")
        if st.get("caption_flags"):
            cap_flags += re.findall(r'"[^"]*"|\S+', st["caption_flags"])
            cap_flags = [f.strip('"') for f in cap_flags]
    json.dump({"words": str(cap_file) if cap_file else None, "flags": cap_flags},
              open(out / "captions.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    result.update(edl=str(out / "edl-vo.json"), caption_words=str(cap_file) if cap_file else None,
                  caption_flags=cap_flags)
    return result


def cmd_build(args) -> int:
    plan_path = Path(args.plan)
    if not plan_path.exists():
        emit_err(f"ไม่พบ PLAN: {plan_path}")
    res = build_plan(args, plan_path)
    if res["errors"]:
        emit_err(f"PLAN.md มีปัญหา {len(res['errors'])} จุด - ยังไม่เขียนอะไร (nothing written)",
                 op="vo-build", errors=res["errors"], warnings=res["warnings"])
    emit_ok(op="vo-build", capcut_written=False,
            next_step=("ผู้ใช้อนุมัติแล้ว → vo_assemble.py apply" if res["approved"] else
                       "ให้ผู้ใช้อ่าน PLAN.md แล้วอนุมัติ (approved: yes) ก่อน apply"), **res)
    return 0


# ----------------------------------------------------------------- apply

def cmd_apply(args) -> int:
    plan_path = Path(args.plan)
    if not plan_path.exists():
        emit_err(f"ไม่พบ PLAN: {plan_path}")
    res = build_plan(args, plan_path, write=False)
    if res["errors"]:
        emit_err(f"PLAN.md มีปัญหา {len(res['errors'])} จุด - ยังไม่เขียนอะไรลง CapCut",
                 op="vo-apply", errors=res["errors"])
    if not res["approved"]:
        emit_err("PLAN.md ยังไม่ได้อนุมัติ - ไม่เขียนลง CapCut (PLAN not approved; set 'approved: yes' "
                 "only after the buyer says OK)", op="vo-apply", plan=str(plan_path))
    project_dir = Path(res["project_dir"])
    n_main = main_video_segments(project_dir)
    if n_main:
        emit_err(f"{project_dir.name} มีคลิปบน timeline แล้ว {n_main} ท่อน - โหมดเสียงพากย์สร้าง timeline ใหม่ "
                 f"จึงไม่เขียน (the timeline already has {n_main} segment(s); make a NEW project, "
                 f"or ask the buyer before anything destructive)", op="vo-apply")
    if is_capcut_main_running() and not args.force_kill:
        emit_err("CapCut เปิดอยู่ - กด Cmd+S แล้ว Cmd+Q ก่อน (CapCut is open - save + quit first). "
                 "ถ้าผู้ใช้ยืนยันว่าไม่มีงานค้าง ใช้ --force-kill", op="vo-apply")

    drafts = str(project_dir.parent)
    name = project_dir.name
    common = ["--drafts-dir", drafts] + (["--force-kill"] if args.force_kill else [])
    py = sys.executable

    # safety copy of the WHOLE project before the first byte (never deleted)
    snap = KIT_ROOT / "projects" / "_work" / "capcut" / name / "backups" / \
        f"pre-vo-assemble-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copytree(project_dir, snap / "project", ignore=shutil.ignore_patterns(".indigo11-write.lock"))

    steps = []

    def step(label, cmd, required=True):
        rc, out, err = run_json(cmd)
        rec = {"step": label, "ok": bool(out.get("ok")) and rc == 0,
               "op_id": out.get("op_id"), "backup_dir": out.get("backup_dir")}
        if not rec["ok"]:
            rec["error"] = out.get("error") or err[-300:]
            rec["detail"] = {k: out[k] for k in ("errors", "fix", "detail") if k in out}
        steps.append(rec)
        if not rec["ok"] and required:
            first = next((s for s in steps if s.get("op_id")), None)
            emit_err(f"หยุดที่ขั้น {label}: {rec['error']}", op="vo-apply", steps=steps,
                     safety_copy=str(snap),
                     undo=(f"python3 {SCRIPTS / 'restore_project.py'} --project {name} "
                           f"--drafts-dir \"{drafts}\" --op {first['op_id']}") if first else None)
        return out

    imports = res["imports"]
    visual_imports = [f for f in imports
                      if Path(f).suffix.lower() not in (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".aif", ".aiff")]
    if imports:
        step("import-media", [py, str(SCRIPTS / "import_media.py"), "--project", name,
                              "--files", *imports] + common)
    res = build_plan(args, plan_path, write=True)       # indices now include the imports
    if res["errors"]:
        emit_err("PLAN ใช้ไม่ได้หลัง import", op="vo-apply", errors=res["errors"], steps=steps)
    out = Path(res["out"])
    step("inject-edl", [py, str(SCRIPTS / "inject_edl.py"), "--project", name,
                        "--edl", str(out / "edl-vo.json")] + common)
    step("voice-over", [py, str(SCRIPTS / "add_sfx.py"), "--project", name, "--track", "auto",
                        "--sfx-timing", str(out / "vo-timing.json")] + common)
    if res["overlays"]:
        step("b-roll", [py, str(SCRIPTS / "add_overlay.py"), "--project", name,
                        "--overlay-timing", str(out / "overlay-timing.json")] + common)
    fx = json.load(open(out / "fx.json", encoding="utf-8"))
    for op in fx["ops"]:
        cmd = [py, str(SCRIPTS / "capcut_apply_resource.py"), "--target", name,
               "--source", fx["fx_source"], "--name", op["name"], "--start-ms", str(op["start_ms"])]
        cmd += ["--transition"] if op["kind"] == "transition" else ["--dur-ms", str(op["dur_ms"])]
        step(f"{op['kind']}:{op['name']}@row{op['row']}", cmd + common)
    if res["sfx"]:
        step("sfx", [py, str(SCRIPTS / "add_sfx.py"), "--project", name, "--track", "auto",
                     "--sfx-timing", str(out / "sfx-timing.json")] + common)
    capj = json.load(open(out / "captions.json", encoding="utf-8"))
    if capj.get("words"):
        step("captions", [py, str(SCRIPTS / "add_captions.py"), "--project", name,
                          "--words", capj["words"], *capj["flags"]] + common)

    # SOURCES.md: one row per SFX file actually used (append only)
    src_md = Path(res["work"]) / "assets" / "SOURCES.md"
    try:
        sfx_used = json.load(open(out / "sfx-timing.json", encoding="utf-8"))["sfx"]
        txt = src_md.read_text(encoding="utf-8") if src_md.exists() else \
            "# Sources\n\n| file | what | source URL | licence | on-screen credit |\n|---|---|---|---|---|\n"
        add = []
        for s in sfx_used:
            fn = Path(s["file"]).name
            if fn not in txt and fn not in "".join(add):
                lic = "CC0 (starter)" if s["source"] == "starter-cc0" else \
                    ("ไฟล์ของผู้ใช้" if s["source"] == "own" else "ชุดเสียงที่ผู้ใช้โหลด (ห้ามแจกต่อ)")
                add.append(f"| {fn} | SFX role {s['role']} | assets/sound-effects ({s['source']}) | {lic} | - |\n")
        if add:
            src_md.write_text(txt.rstrip("\n") + "\n" + "".join(add), encoding="utf-8")
        src_txt = src_md.read_text(encoding="utf-8")
        missing_src = [Path(f).name for f in visual_imports if Path(f).name not in src_txt]
    except Exception:
        missing_src = [Path(f).name for f in visual_imports]

    rc, qc, _err = run_json([py, str(SCRIPTS / "qc_edit.py"), "--project", str(project_dir),
                             "--edl", str(out / "edl-vo.json"), "--stage", str(out / "stage-vo.json")]
                            + (["--words", capj["words"]] if capj.get("words") else []) + ["--json"])
    first = next((s for s in steps if s.get("op_id")), None)
    payload = dict(op="vo-apply", project=name, steps=steps, safety_copy=str(snap),
                   qc={k: qc.get(k) for k in ("ok", "fail", "warn", "issues", "duration_s", "segments")},
                   sources_missing=missing_src,
                   undo_all=(f"python3 {SCRIPTS / 'restore_project.py'} --project {name} "
                             f"--drafts-dir \"{drafts}\" --op {first['op_id']}") if first else None,
                   next_step=f"เปิด CapCut → {name} ดู timeline")
    if not qc.get("ok"):
        print(json.dumps({"ok": False, "error": "QC FAIL - แก้ก่อนส่ง", **payload}, ensure_ascii=False))
        return 1
    emit_ok(**payload)
    return 0


# ----------------------------------------------------------------- cli

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prep", help="transcribe VO + watch footage + write PLAN.md (no CapCut writes)")
    p.add_argument("--project", required=True)
    p.add_argument("--work", help="projects/<date>-<name> (default: today + project name)")
    p.add_argument("--vo", help="voice-over name in the Media panel (default: the longest audio)")
    p.add_argument("--transcript", help="existing transcript dir (raw-elevenlabs.json + captions.json)")
    p.add_argument("--provider", default="elevenlabs")
    p.add_argument("--lang", default="th")
    p.add_argument("--words-per-group", type=int, default=2)
    p.add_argument("--min-row-ms", type=int, default=1200, help="merge VO phrases shorter than this")
    p.add_argument("--no-watch", action="store_true")
    p.add_argument("--no-ocr", action="store_true")
    p.add_argument("--ocr-lang", default="th,en")
    p.add_argument("--cells", type=int, default=36)
    p.add_argument("--overwrite-plan", action="store_true", help="back up PLAN.md and write a new one")
    p.add_argument("--drafts-dir", default=str(CAPCUT_DRAFTS_DIR))

    for name in ("build", "apply"):
        p = sub.add_parser(name)
        p.add_argument("--plan", required=True)
        p.add_argument("--project", help="default: 'project:' in PLAN.md")
        p.add_argument("--out", help="where timing files go (default <work>/vo/build)")
        p.add_argument("--drafts-dir", default=str(CAPCUT_DRAFTS_DIR))
        if name == "apply":
            p.add_argument("--force-kill", action="store_true",
                           help="Kill a running CapCut instead of refusing. Only after the buyer confirms.")
    args = ap.parse_args()
    return {"prep": cmd_prep, "build": cmd_build, "apply": cmd_apply}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
