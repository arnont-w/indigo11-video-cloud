# /// script
# dependencies = ["numpy", "librosa", "soundfile", "scipy"]
# ///
"""Map the sound of any video: tempo + beat grid, strong musical cues, SFX-like hits, silences, loudness arc.

    python3 .claude/skills/indigo11-watch/scripts/audio_cues.py <video_or_audio> <out_dir> [--window 0 30]

Writes <out_dir>/audio_cues.json and audio_cues.md (read the .md). Speech is not transcribed here;
run indigo11-transcribe / video-understand transcript.py for words.

Strong-cue scoring follows brag's analyze_music_cues.py (MIT, latent-spaces/brag):
intensity = 0.45*onset + 0.25*local onset contrast + 0.20*rms + 0.10*bass(30-180 Hz), min gap 0.18 s, keep >= 0.45.
Added here: hits on the percussive layer (HPSS) for SFX, silences, 0.5 s loudness arc.
"""
import _starter  # indigo11 Video: runtime PATH + the Python the installer prepared
_starter.needs("numpy", "librosa", "soundfile", "scipy")
import argparse, json, subprocess, tempfile
from pathlib import Path
import numpy as np, librosa

HOP = 512


def norm(v):
    v = np.clip(np.asarray(v, float), 0, None); p = np.percentile(v, 98) if v.size else 0
    return np.clip(v / p, 0, 1) if p > 0 else np.zeros_like(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("out"); ap.add_argument("--window", nargs=2, type=float, default=None)
    a = ap.parse_args(); out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    wav = Path(tempfile.mkdtemp()) / "a.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", a.src, "-vn", "-ac", "1", "-ar", "44100", str(wav)], check=True)
    y, sr = librosa.load(str(wav), sr=44100, mono=True)
    dur = len(y) / sr
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP); on = norm(onset)
    w = max(1, int(.5 * sr / HOP)); contrast = norm([max(0, on[i] - np.median(on[max(0, i - w): i + w + 1])) for i in range(len(on))])
    rms = norm(librosa.feature.rms(y=y, frame_length=2048, hop_length=HOP)[0])
    S = np.abs(librosa.stft(y, n_fft=4096, hop_length=HOP)); f = librosa.fft_frequencies(sr=sr, n_fft=4096)
    bass = norm(S[(f >= 30) & (f <= 180)].mean(axis=0))
    n = min(len(on), len(contrast), len(rms), len(bass))
    inten = np.clip(.45 * on[:n] + .25 * contrast[:n] + .20 * rms[:n] + .10 * bass[:n], 0, 1)
    tempo, beats = librosa.beat.beat_track(onset_envelope=onset, sr=sr, hop_length=HOP)
    tempo = float(np.atleast_1d(tempo)[0])
    bt = librosa.frames_to_time(beats, sr=sr, hop_length=HOP)
    beat_rows = [{"t": round(float(t), 3), "i": round(float(inten[min(b, n - 1)]), 2)} for t, b in zip(bt, beats)]
    cand = [{"t": r["t"], "i": r["i"], "kind": "beat"} for r in beat_rows]
    for fr in librosa.onset.onset_detect(onset_envelope=onset, sr=sr, hop_length=HOP):
        cand.append({"t": round(float(librosa.frames_to_time(fr, sr=sr, hop_length=HOP)), 3), "i": round(float(inten[min(fr, n - 1)]), 2), "kind": "onset"})
    strong = []
    for c in sorted(cand, key=lambda c: -c["i"]):
        if c["i"] >= .45 and all(abs(c["t"] - s["t"]) >= .18 for s in strong):
            strong.append(c)
    strong = sorted(strong[:64], key=lambda c: c["t"])
    # SFX-like hits: onsets of the percussive layer, ranked by strength
    yp = librosa.effects.hpss(y)[1]
    po = librosa.onset.onset_strength(y=yp, sr=sr, hop_length=HOP); pon = norm(po)
    hits = [{"t": round(float(librosa.frames_to_time(fr, sr=sr, hop_length=HOP)), 3), "s": round(float(pon[fr]), 2)}
            for fr in librosa.onset.onset_detect(onset_envelope=po, sr=sr, hop_length=HOP, backtrack=False) if pon[fr] >= .35]
    # silences (< -45 dBFS for >= 0.25 s) and 0.5 s loudness arc
    fr_rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=HOP)[0]; db = 20 * np.log10(fr_rms + 1e-9)
    tt = librosa.frames_to_time(np.arange(len(db)), sr=sr, hop_length=HOP)
    sil, s0 = [], None
    for t, d in zip(tt, db):
        if d < -45 and s0 is None: s0 = t
        if d >= -45 and s0 is not None:
            if t - s0 >= .25: sil.append([round(float(s0), 2), round(float(t), 2)])
            s0 = None
    if s0 is not None and dur - s0 >= .25: sil.append([round(float(s0), 2), round(dur, 2)])
    arc = [round(float(np.mean(db[(tt >= k) & (tt < k + .5)])), 1) for k in np.arange(0, dur, .5)]
    res = {"duration": round(dur, 3), "tempo": round(tempo, 2), "beat_period": round(60 / tempo, 3) if tempo else None,
           "beats": beat_rows, "strong_cues": strong, "hits": hits, "silences": sil, "loudness_arc_db_per_0.5s": arc,
           "scoring": "0.45*onset + 0.25*local_contrast + 0.20*rms + 0.10*bass (after brag)"}
    (out / "audio_cues.json").write_text(json.dumps(res, indent=1))
    lo, hi = (a.window or (0, dur))
    md = [f"# audio cues: {Path(a.src).name}", f"- duration {dur:.2f}s · tempo {tempo:.1f} BPM (beat {60 / tempo:.3f}s)" if tempo else "- tempo: none",
          f"- beats: {len(beat_rows)} · strong cues: {len(strong)} · percussive hits: {len(hits)} · silences: {len(sil)}",
          "", f"## strong cues {lo:.0f}-{hi:.0f}s (time, intensity, kind)",
          ", ".join(f"{c['t']:.2f}({c['i']:.2f},{c['kind'][0]})" for c in strong if lo <= c["t"] <= hi),
          "", "## top percussive hits (likely SFX / cuts)",
          ", ".join(f"{h['t']:.2f}({h['s']:.2f})" for h in sorted(hits, key=lambda h: -h["s"])[:40]),
          "", "## silences", ", ".join(f"{s[0]}-{s[1]}" for s in sil) or "none",
          "", "## loudness arc (dBFS per 0.5 s)", " ".join(str(v) for v in arc)]
    (out / "audio_cues.md").write_text("\n".join(md) + "\n")
    print("\n".join(md[:3])); print("wrote", out / "audio_cues.md")


if __name__ == "__main__":
    main()
