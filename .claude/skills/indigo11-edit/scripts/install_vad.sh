#!/usr/bin/env bash
# Install Silero VAD into ~/.indigo11/vad-env (Python venv). FIRST USE only (the starter installer never runs this):
# ask the user first - it downloads ~450 MB (PyTorch). Uses the starter's Python (~/.indigo11/venv) as the base when present.
# ~437MB total (torch is the bulk). Idempotent - safe to re-run.

set -e

VENV="$HOME/.indigo11/vad-env"

# Resolve the venv interpreter/pip per-OS: POSIX puts them in bin/, Windows
# (Git Bash) in Scripts/python.exe + Scripts/pip.exe. Set after venv creation
# too; these defaults let the "already installed?" check work on both OSes.
venv_py() { [ -x "$VENV/bin/python3" ] && echo "$VENV/bin/python3" || echo "$VENV/Scripts/python.exe"; }
venv_pip() { [ -x "$VENV/bin/pip" ] && echo "$VENV/bin/pip" || echo "$VENV/Scripts/pip.exe"; }

# Resolve a usable host python. On Windows, bare `python3` is the Microsoft
# Store stub that errors - prefer `python`/`py`, then `python3` on POSIX.
case "$(uname -s)" in
 MINGW*|MSYS*|CYGWIN*) PY_ORDER="$HOME/.indigo11/venv/Scripts/python.exe python py python3" ;;
 *) PY_ORDER="$HOME/.indigo11/venv/bin/python3 python3 python" ;;
esac
PYCMD=""
for c in $PY_ORDER; do command -v "$c" >/dev/null 2>&1 && { PYCMD="$c"; break; }; done

# Already installed? Done.
if [ -d "$VENV" ] && "$(venv_py)" -c "from silero_vad import load_silero_vad" 2>/dev/null; then
 echo "✓ Silero VAD already installed at $VENV"
 exit 0
fi

# Python 3.9+ required
if [ -z "$PYCMD" ]; then
 echo "✗ python not found. Install Python 3.9+ first:" >&2
 echo " macOS: brew install python (or download from python.org)" >&2
 echo " Linux: apt-get install python3 python3-venv" >&2
 echo " Windows: download from python.org (or use install.ps1)" >&2
 exit 1
fi

PY_VER=$("$PYCMD" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "")
PY_OK=$("$PYCMD" -c "import sys; print('yes' if sys.version_info >= (3, 9) else 'no')" 2>/dev/null || echo "no")
if [ "$PY_OK" != "yes" ]; then
 echo "✗ Need Python 3.9+, found $PY_VER. Upgrade Python before retrying." >&2
 exit 1
fi

# venv module available?
if ! "$PYCMD" -c "import venv" 2>/dev/null; then
 echo "✗ Python venv module missing. On Debian/Ubuntu run:" >&2
 echo " sudo apt-get install python3-venv" >&2
 exit 1
fi

# Free disk: need ~600MB headroom in $HOME (~437MB install + safety margin)
if command -v df >/dev/null 2>&1; then
 AVAIL_KB=$(df -k "$HOME" | tail -1 | awk '{print $4}')
 AVAIL_MB=$((AVAIL_KB / 1024))
 if [ "$AVAIL_MB" -lt 600 ]; then
 echo "⚠ Only ${AVAIL_MB}MB free in \$HOME - install needs ~600MB. Free up space and retry." >&2
 exit 1
 fi
fi

echo "→ Creating venv at $VENV (~437MB, ~3-5 min depending on network)"
mkdir -p "$HOME/.indigo11"
"$PYCMD" -m venv "$VENV" || {
 echo "✗ venv creation failed. Common causes:" >&2
 echo " - python3-venv missing (Linux): apt-get install python3-venv" >&2
 echo " - corporate proxy blocking download" >&2
 echo " - disk full or permissions issue" >&2
 exit 1
}

# venv interpreter/pip resolved per-OS now that the venv exists.
VENV_PY=$(venv_py)
VENV_PIP=$(venv_pip)

echo "→ Upgrading pip..."
"$VENV_PIP" install --quiet --upgrade pip 2>&1 | tail -3 || {
 echo "✗ pip upgrade failed. Network issue? Re-run when connected." >&2
 exit 1
}

echo "→ Installing silero-vad + soundfile + numpy (this is the slow step)..."
"$VENV_PIP" install --quiet silero-vad soundfile numpy 2>&1 | tail -3 || {
 echo "✗ pip install failed. Common causes:" >&2
 echo " - network interrupted partway through 400MB download" >&2
 echo " - pip cache corrupted: rm -rf ~/.cache/pip and retry" >&2
 echo " - older Python (< 3.9): check 'python --version'" >&2
 exit 1
}

# Verify the whole chain imports
if "$VENV_PY" -c "from silero_vad import load_silero_vad, read_audio, get_speech_timestamps" 2>/dev/null; then
 echo "✓ Silero VAD installed at $VENV"
 echo " Use: $VENV_PY <script>"
else
 echo "✗ Install verification failed - silero_vad imports but missing helpers." >&2
 echo " Try: rm -rf $VENV and re-run this script." >&2
 exit 1
fi
