# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "pillow"]
# ///
"""Break a video down for frame-by-frame study.

  python3 .claude/skills/indigo11-watch/scripts/breakdown.py videos/ig1_free-apis.mp4 --out breakdown/ig1_free-apis [--fps 6]

Writes:
  grids/g_XXX.jpg     dense contact sheets (every 1/fps s), each cell stamped with time + source frame
  shots.jpg           one thumbnail per detected shot (first frame after each cut) = the storyboard
  signals.png         motion curve (frame diff at full fps) + audio energy, cuts and onsets marked
  signals.json        cuts, per-0.5 s motion, audio onsets, shot lengths
  words.txt           timestamped transcript lines, from --transcript, else <out>/../transcript.json (transcript.py), else
                      (no other source in the starter); a warning is printed when none is found
"""
import _starter  # indigo11 Video: runtime PATH + the Python the installer prepared
_starter.needs("numpy", "PIL")
import argparse, json, subprocess, sys, tempfile, shutil
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def probe(v):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,r_frame_rate:format=duration", "-of", "json", v], capture_output=True, text=True)
    j = json.loads(r.stdout)
    s = j["streams"][0]
    a, b = s["r_frame_rate"].split("/")
    return int(s["width"]), int(s["height"]), float(a) / float(b), float(j["format"]["duration"])


def font(size):
    for p in ["/System/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/SFNSMono.ttf", "/System/Library/Fonts/Helvetica.ttc"]:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default(size=size)


def raw_frames(v, w, h, fps=None, gray=True):
    vf = (f"fps={fps}," if fps else "") + f"scale={w}:{h}" + (",format=gray" if gray else ",format=rgb24")
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", v, "-vf", vf, "-f", "rawvideo", "-"], capture_output=True)
    ch = 1 if gray else 3
    n = len(r.stdout) // (w * h * ch)
    return np.frombuffer(r.stdout[: n * w * h * ch], np.uint8).reshape(n, h, w, ch) if not gray else \
        np.frombuffer(r.stdout[: n * w * h], np.uint8).reshape(n, h, w)


def audio_env(v, sr=16000, hop=0.02):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", v, "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"], capture_output=True)
    x = np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768
    if len(x) == 0:
        return np.zeros(1), hop
    k = int(sr * hop)
    n = len(x) // k
    env = np.sqrt((x[: n * k].reshape(n, k) ** 2).mean(1) + 1e-12)
    return env, hop


def onsets(env, hop):
    db = 20 * np.log10(env + 1e-6)
    flux = np.maximum(0, np.diff(db, prepend=db[0]))
    win = int(0.5 / hop)
    out = []
    for i in range(1, len(flux) - 1):
        lo, hi = max(0, i - win), min(len(flux), i + win)
        thr = flux[lo:hi].mean() + 2.2 * flux[lo:hi].std()
        if flux[i] > thr and flux[i] >= flux[i - 1] and flux[i] >= flux[i + 1] and db[i] > -45:
            if not out or (i * hop - out[-1][0]) > 0.09:
                out.append((round(i * hop, 2), round(float(flux[i]), 1)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--out", required=True)
    ap.add_argument("--fps", type=float, default=6)
    ap.add_argument("--cut", type=float, default=0, help="cut threshold on normalized diff (0 = auto)")
    ap.add_argument("--transcript", default=None, help="transcript.py JSON or indigo11-transcribe captions.json")
    a = ap.parse_args()
    v = str(Path(a.video).resolve())
    out = Path(a.out); (out / "grids").mkdir(parents=True, exist_ok=True)
    W, H, FPS, DUR = probe(v)
    name = Path(v).stem

    # ---- motion + cuts at full frame rate on tiny gray thumbs
    tw = 96; th = int(round(tw * H / W / 2)) * 2
    g = raw_frames(v, tw, th).astype(np.float32)
    d = np.abs(np.diff(g, axis=0)).mean(axis=(1, 2))           # mean abs diff between consecutive frames (0-255)
    diff = np.concatenate([[0], d])
    # a cut is a spike far above its neighbourhood
    cuts = []
    for i in range(1, len(diff)):
        lo, hi = max(1, i - 6), min(len(diff), i + 7)
        nb = np.concatenate([diff[lo:i], diff[i + 1:hi]])
        base = np.median(nb) if len(nb) else 0
        thr = a.cut or max(18.0, base * 3.5 + 6)
        if diff[i] > thr and diff[i] == diff[lo:hi].max():
            cuts.append(i)
    times = np.arange(len(diff)) / FPS
    bins = []
    for b0 in np.arange(0, DUR, 0.5):
        m = (times >= b0) & (times < b0 + .5)
        if m.any():
            bins.append({"t": round(float(b0), 1), "motion": round(float(diff[m].mean()), 2), "peak": round(float(diff[m].max()), 1)})
    shot_starts = [0] + cuts
    shots = [{"start": round(s / FPS, 3), "frame": int(s),
              "len": round(((shot_starts[k + 1] if k + 1 < len(shot_starts) else len(diff)) - s) / FPS, 3)}
             for k, s in enumerate(shot_starts)]

    env, hop = audio_env(v)
    ons = onsets(env, hop)
    still = sum(1 for b in bins if b["motion"] < 0.4)

    sig = {"name": name, "width": W, "height": H, "fps": FPS, "duration": round(DUR, 3),
           "cuts": [{"t": round(c / FPS, 3), "frame": int(c), "score": round(float(diff[c]), 1)} for c in cuts],
           "shots": shots, "avg_shot_len": round(DUR / max(1, len(shots)), 2),
           "near_still_halfsec_bins": still, "motion_bins": bins,
           "audio_onsets": ons, "audio_onsets_per_s": round(len(ons) / DUR, 2)}
    (out / "signals.json").write_text(json.dumps(sig, indent=1))

    # ---- signals chart
    CW, CH = 1800, 420
    im = Image.new("RGB", (CW, CH), "white"); dr = ImageDraw.Draw(im); f = font(14)
    X = lambda t: 40 + (CW - 60) * t / DUR
    mv = diff / max(1, diff.max())
    pts = [(X(i / FPS), 190 - 170 * mv[i]) for i in range(len(mv))]
    dr.line(pts, fill=(40, 90, 200), width=1)
    ae = env / max(1e-6, env.max())
    pts = [(X(i * hop), 400 - 170 * ae[i]) for i in range(len(ae))]
    dr.line(pts, fill=(60, 60, 60), width=1)
    for c in cuts:
        dr.line([(X(c / FPS), 10), (X(c / FPS), 200)], fill=(220, 40, 40), width=2)
    for t, _ in ons:
        dr.line([(X(t), 222), (X(t), 236)], fill=(240, 140, 0), width=2)
    for s in range(int(DUR) + 1):
        dr.line([(X(s), 200), (X(s), 208)], fill="black"); dr.text((X(s) - 6, 206), str(s), fill="black", font=f)
    dr.text((44, 12), f"{name}  motion (blue) · cuts (red, {len(cuts)})", fill="black", font=f)
    dr.text((44, 240), f"audio energy (grey) · onsets (orange, {len(ons)})", fill="black", font=f)
    im.save(out / "signals.png")

    # ---- dense contact sheets
    if H > W:   cols, rows, cw = 8, 2, 236
    elif H == W: cols, rows, cw = 5, 3, 300
    else:       cols, rows, cw = 4, 4, 420
    chh = int(round(cw * H / W / 2)) * 2
    tmp = Path(tempfile.mkdtemp())
    step = max(1, round(FPS / a.fps))                          # exact source frames (every step-th), so labels never drift
    subprocess.run(["ffmpeg", "-v", "error", "-i", v, "-vf", f"select='not(mod(n\\,{step}))',scale={cw}:{chh}",
                    "-fps_mode", "vfr", "-q:v", "3", str(tmp / "f_%05d.jpg")])
    files = sorted(tmp.glob("f_*.jpg"))
    per = cols * rows; lab = 22; f2 = font(15)
    for gi in range(0, len(files), per):
        sheet = Image.new("RGB", (cols * (cw + 4) + 4, rows * (chh + lab + 4) + 4), (245, 245, 245))
        sd = ImageDraw.Draw(sheet)
        for k, fp in enumerate(files[gi: gi + per]):
            fr = (gi + k) * step; t = fr / FPS
            x = 4 + (k % cols) * (cw + 4); y = 4 + (k // cols) * (chh + lab + 4)
            newshot = any(fr - step < c <= fr for c in cuts)
            sd.rectangle([x, y, x + cw, y + lab], fill=(200, 40, 40) if newshot else (30, 30, 30))
            sd.text((x + 4, y + 3), f"{t:6.2f}s  f{fr}" + ("  CUT" if newshot else ""), fill="white", font=f2)
            sheet.paste(Image.open(fp), (x, y + lab))
        sheet.save(out / "grids" / f"g_{gi // per:03d}_{gi * step / FPS:05.2f}s.jpg", quality=88)

    # ---- storyboard: first frame of every shot (slightly after the cut)
    sw = 200 if H > W else 320; shh = int(round(sw * H / W / 2)) * 2
    scols = 10 if H > W else 6
    n = len(shots); srows = (n + scols - 1) // scols
    sb = Image.new("RGB", (scols * (sw + 4) + 4, srows * (shh + lab + 4) + 4), (245, 245, 245)); sdd = ImageDraw.Draw(sb)
    for k, s in enumerate(shots):
        t = min(DUR - .05, s["start"] + min(.15, s["len"] / 2))
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", v, "-frames:v", "1", "-vf", f"scale={sw}:{shh}", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True)
        x = 4 + (k % scols) * (sw + 4); y = 4 + (k // scols) * (shh + lab + 4)
        sdd.rectangle([x, y, x + sw, y + lab], fill=(30, 30, 30))
        sdd.text((x + 3, y + 3), f"#{k + 1} {s['start']:.2f}s ({s['len']:.2f}s)", fill="white", font=f2)
        if r.stdout:
            from io import BytesIO
            sb.paste(Image.open(BytesIO(r.stdout)), (x, y + lab))
    sb.save(out / "shots.jpg", quality=88)
    shutil.rmtree(tmp, ignore_errors=True)

    # ---- transcript lines
    os_root = Path(__file__).resolve().parents[3]
    cands = ([Path(a.transcript)] if a.transcript else []) + [out.parent / "transcript.json"] + \
            sorted((os_root / "research/transcripts").glob(f"**/{name}/captions.json"))
    cap = next((c for c in cands if c.exists()), None)
    if cap is None:
        print(f"warning: no transcript found for {name} (pass --transcript PATH); words.txt not written")
    else:
        j = json.loads(cap.read_text())
        words = [dict(w, start=w["t_ms"] / 1000, end=w["end_ms"] / 1000) if "t_ms" in w else w for w in (j.get("words_raw") or j.get("words") or [])]
        words = [w for w in words if w.get("type") not in ("spacing", "audio_event")]
        lines, cur, st = [], [], None
        for w in words:
            txt = w.get("text", "").strip()
            if not txt:
                continue
            if st is None:
                st = w["start"]
            cur.append(txt)
            if txt.endswith((".", "?", "!", ",")) and len(cur) >= 3 or len(cur) >= 9:
                lines.append(f"[{st:6.2f}-{w['end']:6.2f}] {' '.join(cur)}"); cur, st = [], None
        if cur:
            lines.append(f"[{st:6.2f}-{words[-1]['end']:6.2f}] {' '.join(cur)}")
        (out / "words.txt").write_text("\n".join(lines) + "\n")

    print(f"{name}: {W}x{H} {FPS:g}fps {DUR:.2f}s | shots {len(shots)} avg {sig['avg_shot_len']}s | onsets {len(ons)} | grids {(len(files) + per - 1) // per}")


if __name__ == "__main__":
    main()
