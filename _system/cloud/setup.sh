#!/usr/bin/env bash
# indigo11 cloud setup: prepares a Claude Code cloud session (Ubuntu Linux) for this pack.
# Run:  bash _system/cloud/setup.sh
# Safe to run again: it skips what is already there. It never prints a key, only "set" or "empty".
# On a PC or Mac this script does nothing: type "install" there instead.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
say() { printf '%s\n' "$*"; }

if [ "$(uname -s)" != "Linux" ]; then
  say "This script is only for the cloud (Linux). On a PC or Mac, type: install"
  exit 0
fi

SUDO=""
if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi

# 1. System tools: ffmpeg (cutting, rendering, captions), tesseract (reading text on screen)
apt_updated=0
apt_get() {
  if [ "$apt_updated" -eq 0 ]; then $SUDO apt-get update -qq >/dev/null 2>&1; apt_updated=1; fi
  $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@" >/dev/null 2>&1
}
if ! command -v ffmpeg >/dev/null 2>&1; then
  say "Installing ffmpeg ..."
  apt_get ffmpeg || say "WARNING: could not install ffmpeg (is the network set to allow package managers?)"
fi
if ! command -v tesseract >/dev/null 2>&1; then
  say "Installing tesseract ..."
  apt_get tesseract-ocr tesseract-ocr-eng tesseract-ocr-tha || say "WARNING: could not install tesseract (only needed to read on-screen text)"
fi

# 2. Python packages
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  say "WARNING: Python not found"
else
  py_has() { "$PY" -c "import $1" >/dev/null 2>&1; }
  pip_install() {
    "$PY" -m pip install -q "$1" >/dev/null 2>&1 || "$PY" -m pip install -q --break-system-packages "$1" >/dev/null 2>&1
  }
  for pair in pythainlp:pythainlp nlpo3:nlpo3 numpy:numpy PIL:Pillow requests:requests certifi:certifi yt_dlp:yt-dlp gdown:gdown; do
    mod="${pair%%:*}"; pkg="${pair##*:}"
    if ! py_has "$mod"; then
      say "Installing Python package $pkg ..."
      pip_install "$pkg" || say "WARNING: could not install $pkg"
    fi
  done
fi

# 3. Node package for Thai captions (render_caps.mjs imports puppeteer-core)
if command -v npm >/dev/null 2>&1 && [ ! -d "$ROOT/node_modules/puppeteer-core" ]; then
  say "Installing puppeteer-core ..."
  (cd "$ROOT" && npm install --no-save --no-package-lock --no-audit --no-fund --silent puppeteer-core >/dev/null 2>&1) \
    || say "WARNING: could not install puppeteer-core (only needed for Thai captions)"
fi

# 4. Chrome for the animation and caption renderers. In a cloud machine Chrome needs --no-sandbox,
#    so the renderers get a small wrapper (they read the path from _system/setup/local.json).
CHROME=""
for c in /usr/bin/google-chrome /usr/bin/google-chrome-stable /usr/bin/chromium /usr/bin/chromium-browser; do
  if [ -x "$c" ]; then CHROME="$c"; break; fi
done
if [ -n "$CHROME" ]; then
  mkdir -p "$HOME/.indigo11" "$ROOT/_system/setup"
  WRAP="$HOME/.indigo11/chrome-cloud.sh"
  printf '#!/usr/bin/env bash\nexec "%s" --no-sandbox --disable-dev-shm-usage "$@"\n' "$CHROME" > "$WRAP"
  chmod +x "$WRAP"
  printf '{"chrome": "%s"}\n' "$WRAP" > "$ROOT/_system/setup/local.json"
fi

# 5. Report (names only, never a key value)
ok() { if "$@" >/dev/null 2>&1; then printf 'ok'; else printf 'MISSING'; fi; }
say ""
say "indigo11 cloud setup report"
say "  ffmpeg:            $(ok command -v ffmpeg)"
say "  ffmpeg captions:   $(ok sh -c 'ffmpeg -hide_banner -filters 2>/dev/null | grep -q " subtitles "')  (libass, for English captions)"
say "  tesseract:         $(ok command -v tesseract)  (optional)"
say "  python:            ${PY:-MISSING}"
if [ -n "$PY" ]; then
  for mod in pythainlp nlpo3 numpy PIL requests certifi yt_dlp gdown; do
    say "    $mod: $(ok "$PY" -c "import $mod")"
  done
fi
say "  node:              $(node --version 2>/dev/null || printf 'MISSING')"
say "  puppeteer-core:    $(ok test -d "$ROOT/node_modules/puppeteer-core")  (Thai captions)"
say "  chrome:            ${CHROME:-MISSING}"
for k in ELEVENLABS_API_KEY PEXELS_API_KEY PIXABAY_API_KEY GEMINI_API_KEY OPENAI_API_KEY XAI_API_KEY; do
  if [ -n "${!k:-}" ]; then v="set"; else v="empty"; fi
  say "  $k: $v"
done
say ""
if command -v ffmpeg >/dev/null 2>&1 && [ -n "$PY" ]; then
  say "Tools: ready"
else
  say "Tools: NOT ready. Fix the MISSING lines above."
fi
if [ -n "${ELEVENLABS_API_KEY:-}" ]; then
  say "Keys: ready"
else
  say "Keys: ELEVENLABS_API_KEY is empty. It goes in the cloud environment settings"
  say "(Environment variables), never in the chat. Also check that this session was"
  say "started in the environment that holds the key."
fi
