"""Cuts vs speech: speaking rate, shot lengths, and where each cut lands against the nearest word (sentence start, clause or mid-sentence).

    python3 .claude/skills/indigo11-watch/scripts/pacing.py <signals.json> <transcript.json> [<signals.json> <transcript.json> ...]

signals.json comes from breakdown.py. The transcript is transcript.py output (words with t_ms/end_ms) or indigo11-transcribe captions.json (words_raw/words with start/end).
"""
import _starter  # indigo11 Video: runtime PATH + the Python the installer prepared
import json, sys, statistics as st
from pathlib import Path


def load_words(path):
    d = json.loads(Path(path).read_text())
    out = []
    for w in d.get("words_raw") or d.get("words") or []:
        if not w.get("text", "").strip() or w.get("type") in ("spacing", "audio_event"):
            continue
        if "t_ms" in w:
            out.append({"text": w["text"], "start": w["t_ms"] / 1000, "end": w["end_ms"] / 1000})
        else:
            out.append({"text": w["text"], "start": w["start"], "end": w["end"]})
    return out


def report(sig_path, tr_path):
    sig = json.loads(Path(sig_path).read_text())
    words = load_words(tr_path)
    n = sig.get("name") or Path(sig_path).parent.name
    dur = sig["duration"]; lens = [s["len"] for s in sig["shots"]]
    if not words:
        print(f"\n== {n}: {dur:.1f}s, no words in {tr_path}")
    else:
        speech = max(words[-1]["end"] - words[0]["start"], 1e-6)
        print(f"\n== {n}: {dur:.1f}s, {len(words)} words, {len(words)/speech:.2f} w/s speech, first word at {words[0]['start']:.2f}s")
    print(f"   shots {len(lens)} | avg {st.mean(lens):.2f}s | median {st.median(lens):.2f}s | min {min(lens):.2f} | max {max(lens):.2f} | onsets/s {sig.get('audio_onsets_per_s')}")
    if not words:
        return
    for c in [c["t"] for c in sig["cuts"]]:
        i = min(range(len(words)), key=lambda k: abs(words[k]["start"] - c))
        w = words[i]; prev = words[i - 1]["text"] if i else ""
        kind = "SENT" if prev.endswith((".", "?", "!", ":")) else "clause" if prev.endswith((",", ";")) else "mid"
        print(f"   cut {c:6.2f}s  word '{w['text']}' starts {w['start'] - c:+.2f}s from cut  [{kind}]")


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or len(a) % 2:
        sys.exit(__doc__)
    for s, t in zip(a[::2], a[1::2]):
        report(s, t)
