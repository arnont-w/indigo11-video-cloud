"""Extract every frame (0-based, exact frame numbers) + the audio of a reference video, for frame-level measurement.

    python3 .claude/skills/indigo11-watch/scripts/extract.py <video> <out_dir> [--from F0 --to F1] [--scale 0.5] [--png]

Writes <out_dir>/full/fNNNNN.jpg (or .png), <out_dir>/audio.wav (48 kHz) and <out_dir>/info.json (fps, size, frames, duration).
Frame file N is exactly source frame N (select-by-index, not an fps filter), so numbers match .claude/skills/indigo11-watch/scripts/breakdown.py grids.
Long videos: pass a range; 1 min at 30 fps = 1,800 files.
"""
import _starter  # indigo11 Video: runtime PATH + the Python the installer prepared
import argparse, json, subprocess
from fractions import Fraction
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video"); ap.add_argument("out")
    ap.add_argument("--from", dest="f0", type=int, default=0); ap.add_argument("--to", dest="f1", type=int, default=None)
    ap.add_argument("--scale", type=float, default=1.0); ap.add_argument("--png", action="store_true")
    a = ap.parse_args(); out = Path(a.out); (out / "full").mkdir(parents=True, exist_ok=True)
    pr = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                                    "stream=width,height,r_frame_rate,nb_read_packets:format=duration", "-of", "json", a.video],
                                   capture_output=True, text=True).stdout)
    st = pr["streams"][0]; fps = float(Fraction(st["r_frame_rate"])); n = int(st["nb_read_packets"])
    f1 = min(a.f1 if a.f1 is not None else n - 1, n - 1)
    ext = "png" if a.png else "jpg"
    vf = f"select='between(n\\,{a.f0}\\,{f1})'" + (f",scale=iw*{a.scale}:-2" if a.scale != 1 else "")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", a.video, "-vf", vf, "-fps_mode", "passthrough", "-q:v", "2",
                    "-start_number", str(a.f0), str(out / "full" / f"f%05d.{ext}")], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", a.video, "-vn", "-ac", "2", "-ar", "48000", str(out / "audio.wav")])
    info = {"video": str(Path(a.video).resolve()), "fps": fps, "width": st["width"], "height": st["height"], "frames": n,
            "duration": float(pr["format"]["duration"]), "extracted": [a.f0, f1], "scale": a.scale}
    (out / "info.json").write_text(json.dumps(info, indent=1))
    print(f"{f1 - a.f0 + 1} frames f{a.f0}..f{f1} @ {fps:.3f} fps → {out / 'full'} · audio.wav · info.json")


if __name__ == "__main__":
    main()
