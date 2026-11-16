#!/usr/bin/env bash
# Pre-flight for indigo11-capcut. Output JSON on stdout.
# Usage: bash preflight.sh [project-name]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_NAME="${1:-}"
DRAFTS_DIR="${CAPCUT_DRAFTS_DIR:-$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft}"

check() { command -v "$1" >/dev/null 2>&1 && echo yes || echo no; }

FFMPEG=$(check ffmpeg)
FFPROBE=$(check ffprobe)
PYTHON3=$(check python3)
BREW=$(check brew)

case "$(uname -s)" in
    Darwin*) PLATFORM="macos" ;;
    Linux*)  PLATFORM="linux" ;;
    *)       PLATFORM="unknown" ;;
esac

NLPO3="no"
if [ "$PYTHON3" = "yes" ]; then
    python3 -c "import nlpo3, pythainlp" 2>/dev/null && NLPO3="yes"
fi

VAD_ENV="no"
[ -x "$HOME/.indigo11/vad-env/bin/python3" ] && VAD_ENV="yes"

DRAFTS_EXISTS=no
[ -d "$DRAFTS_DIR" ] && DRAFTS_EXISTS=yes

CAPCUT_RUNNING=no
if pgrep -fx CapCut >/dev/null 2>&1; then
    CAPCUT_RUNNING=yes
fi

PROJECT_EXISTS=no
VALID_CLIPS=0
if [ -n "$PROJECT_NAME" ] && [ -d "$DRAFTS_DIR/$PROJECT_NAME" ]; then
    PROJECT_EXISTS=yes
    VALID_CLIPS=$(python3 "$SCRIPT_DIR/read_media.py" --project "$PROJECT_NAME" --count-only 2>/dev/null || echo 0)
fi

cat <<EOF
{
  "ffmpeg": "$FFMPEG",
  "ffprobe": "$FFPROBE",
  "python3": "$PYTHON3",
  "nlpo3_pythainlp": "$NLPO3",
  "vad_env": "$VAD_ENV",
  "platform": "$PLATFORM",
  "brew": "$BREW",
  "drafts_dir": "$DRAFTS_DIR",
  "drafts_dir_exists": "$DRAFTS_EXISTS",
  "capcut_running": "$CAPCUT_RUNNING",
  "project_name": "$PROJECT_NAME",
  "project_exists": "$PROJECT_EXISTS",
  "valid_clips_count": $VALID_CLIPS
}
EOF
