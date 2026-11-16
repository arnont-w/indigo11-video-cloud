"""Compact YouTube auto-caption VTTs: strip inline timing tags, drop rolling-caption duplicates, emit "[mm:ss] text" chunks.

  python3 .claude/skills/indigo11-watch/scripts/vtt_compact.py <vtt_dir> <out_dir> [--chunk 12]
"""
import _starter  # indigo11 Video: runtime PATH + the Python the installer prepared
import re, sys, json
from pathlib import Path

TS = re.compile(r"(\d+):(\d\d):(\d\d)\.(\d+)\s+-->\s+(\d+):(\d\d):(\d\d)\.(\d+)")
TAG = re.compile(r"<[^>]+>")


def parse(path):
    cues, cur_t, buf = [], None, []
    for line in Path(path).read_text(errors="ignore").splitlines():
        m = TS.search(line)
        if m:
            if cur_t is not None and buf:
                cues.append((cur_t, " ".join(buf)))
            h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
            cur_t, buf = h * 3600 + mi * 60 + s, []
        elif line.strip() and not line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")) and cur_t is not None:
            txt = TAG.sub("", line).strip()
            if txt:
                buf.append(txt)
    if cur_t is not None and buf:
        cues.append((cur_t, " ".join(buf)))
    # rolling captions repeat the previous line; keep only the new words
    out, prev = [], ""
    for t, txt in cues:
        if txt == prev or txt in prev:
            continue
        new = txt[len(prev):].strip() if txt.startswith(prev) else txt
        for line in txt.split(" ") and [txt]:
            pass
        if prev and txt.startswith(prev.split(" ")[-1]) is False and prev in txt:
            new = txt.split(prev, 1)[1].strip()
        out.append((t, new))
        prev = txt
    return out


def dedup_words(items):
    # second pass: remove leading overlap between consecutive fragments (common in auto captions)
    res, tail = [], []
    for t, txt in items:
        w = txt.split()
        k = 0
        for n in range(min(len(tail), len(w)), 0, -1):
            if tail[-n:] == w[:n]:
                k = n; break
        w = w[k:]
        if w:
            res.append((t, " ".join(w)))
            tail = (tail + w)[-30:]
    return res


def main():
    src, dst = Path(sys.argv[1]), Path(sys.argv[2]); dst.mkdir(parents=True, exist_ok=True)
    chunk = float(sys.argv[sys.argv.index("--chunk") + 1]) if "--chunk" in sys.argv else 12
    stats = {}
    for vtt in sorted(src.glob("*.vtt")):
        vid = vtt.name.split(".")[0]
        items = dedup_words(parse(vtt))
        lines, start, words = [], None, []
        for t, txt in items:
            if start is None:
                start = t
            words.append(txt)
            if t - start >= chunk:
                lines.append(f"[{int(start // 60):02d}:{int(start % 60):02d}] " + " ".join(words)); start, words = None, []
        if words:
            lines.append(f"[{int(start // 60):02d}:{int(start % 60):02d}] " + " ".join(words))
        text = "\n".join(lines) + "\n"
        (dst / f"{vid}.txt").write_text(text)
        stats[vid] = {"vtt_bytes": vtt.stat().st_size, "compact_bytes": len(text.encode()), "words": len(text.split())}
    (dst / "_stats.json").write_text(json.dumps(stats, indent=1))
    tv = sum(s["vtt_bytes"] for s in stats.values()); tc = sum(s["compact_bytes"] for s in stats.values())
    print(f"{len(stats)} files, vtt {tv/1e6:.2f} MB -> compact {tc/1e6:.2f} MB ({tc/tv*100:.0f}%)")


if __name__ == "__main__":
    main()
