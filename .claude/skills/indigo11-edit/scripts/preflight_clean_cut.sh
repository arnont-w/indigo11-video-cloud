#!/usr/bin/env bash
# Check what's installed for indigo11-clean-cut. Outputs JSON.
# Used by the skill at trigger time before doing any work.
# Portable across macOS / Linux / Windows (Git Bash, WSL).

check() { command -v "$1" >/dev/null 2>&1 && echo yes || echo no; }

# Platform detection (needed before resolving the python command name)
case "$(uname -s)" in
 Darwin*) PLATFORM="macos" ;;
 Linux*) PLATFORM="linux" ;;
 MINGW*|MSYS*|CYGWIN*) PLATFORM="windows" ;;
 *) PLATFORM="unknown" ;;
esac

# Resolve a usable python command. On Windows, bare `python3` is the Microsoft
# Store stub that errors - prefer `python`, then `py`, then `python3`.
if [ "$PLATFORM" = "windows" ]; then
 for c in python py python3; do command -v "$c" >/dev/null 2>&1 && { PYCMD="$c"; break; }; done
else
 for c in python3 python; do command -v "$c" >/dev/null 2>&1 && { PYCMD="$c"; break; }; done
fi
: "${PYCMD:=python3}"

FFMPEG=$(check ffmpeg)
FFPROBE=$(check ffprobe)
PYTHON3=$(command -v "$PYCMD" >/dev/null 2>&1 && echo yes || echo no)
BREW=$(check brew)

# Python version (e.g. 3.9.6). Empty if python missing.
PYTHON3_VERSION=""
if [ "$PYTHON3" = "yes" ]; then
 PYTHON3_VERSION=$("$PYCMD" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>/dev/null || echo "")
fi

# Silero VAD = venv exists AND imports cleanly. The venv interpreter path is
# per-OS: bin/python3 (POSIX) vs Scripts/python.exe (Windows).
VENV="$HOME/.indigo11/vad-env"
VENV_PY="$VENV/bin/python3"
[ -x "$VENV_PY" ] || VENV_PY="$VENV/Scripts/python.exe"
SILERO=no
if [ -d "$VENV" ] && [ -x "$VENV_PY" ]; then
 if "$VENV_PY" -c "from silero_vad import load_silero_vad" >/dev/null 2>&1; then
 SILERO=yes
 fi
fi

# Saved preference (from save_preference.sh). Falls back to "unset".
# Build the path inside Python (os.path.expanduser): in Git Bash $HOME is an MSYS
# path (/c/Users/...) that native Windows Python can't open. UTF-8 read.
PREF="unset"
PREF_VAL=$("$PYCMD" -c "import json,os; p=os.path.join(os.path.expanduser('~'),'.indigo11','config.json'); print(json.load(open(p,encoding='utf-8')).get('preferred_detector','unset') if os.path.exists(p) else 'unset')" 2>/dev/null || echo "unset")
[ -n "$PREF_VAL" ] && PREF="$PREF_VAL"

cat <<EOF
{
 "ffmpeg": "$FFMPEG",
 "ffprobe": "$FFPROBE",
 "python3": "$PYTHON3",
 "python3_version": "$PYTHON3_VERSION",
 "silero_vad": "$SILERO",
 "brew": "$BREW",
 "platform": "$PLATFORM",
 "vad_env_path": "$VENV",
 "preferred_detector": "$PREF"
}
EOF
