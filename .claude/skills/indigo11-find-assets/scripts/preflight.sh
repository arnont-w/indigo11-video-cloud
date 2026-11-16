#!/usr/bin/env bash
# Check what's available for indigo11-find-assets. Outputs JSON.
# All sources are HTTP-only (no binary deps); just needs python3 + network.

check() { command -v "$1" >/dev/null 2>&1 && echo yes || echo no; }

# Python: macOS/Linux ship `python3`; on Windows a real interpreter is usually
# `python` (bare `python3` is the Microsoft Store stub). Accept either so we don't
# false-report "no python" on Windows.
if command -v python3 >/dev/null 2>&1; then
 PYTHON3=yes
elif command -v python >/dev/null 2>&1; then
 PYTHON3=yes
else
 PYTHON3=no
fi

# Optional keys - look in env or nearest .env walking up from cwd.
# Klipy: better memes. OpenRouter: AI image/video generation (paid fallback for broll).
key_status() {
 name="$1"
 if [ -n "$(eval echo "\$$name")" ]; then echo env; return; fi
 d="$PWD"
 while [ "$d" != "/" ]; do
 if [ -f "$d/.env" ] && grep -qE "^[[:space:]]*$name=" "$d/.env"; then echo dotenv; return; fi
 d=$(dirname "$d")
 done
 echo no
}
KLIPY=$(key_status KLIPY_API_KEY)
OPENROUTER=$(key_status OPENROUTER_API_KEY)

# quick network reachability - probe Mixkit (the primary SFX+music source; its
# home returns 200, unlike myinstants which 302-redirects and false-flags 'no').
# -L follows redirects so a 30x still counts as reachable.
NET=no
if command -v curl >/dev/null 2>&1; then
 code=$(curl -sL -o /dev/null -w "%{http_code}" -m 6 -A "Mozilla/5.0" "https://mixkit.co/free-sound-effects/" 2>/dev/null)
 [ "$code" = "200" ] && NET=yes
fi

cat <<JSON
{
 "python3": "$PYTHON3",
 "network": "$NET",
 "klipy_key": "$KLIPY",
 "openrouter_key": "$OPENROUTER",
 "note": "Klipy key is OPTIONAL - without it, memes use Tenor/Giphy/Imgflip (no key). SFX + music + broll never need a key. OpenRouter key is OPTIONAL - enables gen_asset.py (AI image/video, paid, estimate→ask→generate); without it the free broll chain still works.",
 "network_note": "If network=no, do NOT assume offline - a sandbox/hook may block this curl probe while find_assets.py still downloads fine. Just run find_assets.py and check its JSON 'ok' field."
}
JSON
