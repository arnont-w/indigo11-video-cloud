#!/usr/bin/env bash
# Check what's installed for indigo11-cutout. Outputs JSON. Used by the skill before doing any work.
# Backend = rembg (u2net) in ~/.indigo11/cutout-env, installed on FIRST USE (~400 MB): cutout.py --install

check() { command -v "$1" >/dev/null 2>&1 && echo yes || echo no; }
FFMPEG=$(check ffmpeg)
FFPROBE=$(check ffprobe)
PYTHON3=$(check python3)
if [ "$PYTHON3" = "no" ] && command -v python >/dev/null 2>&1; then PYTHON3=yes; fi

case "$(uname -s)" in
    Darwin*) PLATFORM="macos" ;;
    Linux*)  PLATFORM="linux" ;;
    MINGW*|MSYS*|CYGWIN*) PLATFORM="windows" ;;
    *)       PLATFORM="unknown" ;;
esac

HOME_DIR="${HOME:-$USERPROFILE}"
ENV="$HOME_DIR/.indigo11/cutout-env"
REMBG=no
if [ -x "$ENV/bin/python3" ] && "$ENV/bin/python3" -c "import rembg, onnxruntime" >/dev/null 2>&1; then REMBG=yes
elif [ -x "$ENV/Scripts/python.exe" ] && "$ENV/Scripts/python.exe" -c "import rembg, onnxruntime" >/dev/null 2>&1; then REMBG=yes
elif command -v rembg >/dev/null 2>&1 && rembg --help >/dev/null 2>&1; then REMBG=cli
fi
# rembg keeps its models in ~/.u2net (U2NET_HOME overrides)
MODEL=no
[ -f "${U2NET_HOME:-$HOME_DIR/.u2net}/u2net_human_seg.onnx" ] && MODEL=yes

cat <<JSON
{
  "platform": "$PLATFORM",
  "ffmpeg": "$FFMPEG",
  "ffprobe": "$FFPROBE",
  "python3": "$PYTHON3",
  "rembg": "$REMBG",
  "model_cached": "$MODEL",
  "install_first_use": "python3 .claude/skills/indigo11-edit/scripts/cutout.py --install  (~400 MB, ask the user first)"
}
JSON
