#!/usr/bin/env bash
# regenerate-banner.sh — re-render the Agent M brand banner PNGs from assets/banner.html
#
# Run as part of release prep:
#   bash scripts/regenerate-banner.sh                 # auto-detect version from CHANGELOG.md
#   bash scripts/regenerate-banner.sh v3.0.2          # explicit version
#
# The script updates the Version line in assets/banner.html in place, then
# renders 2 PNGs via headless Chrome:
#   assets/agent-m/banner-1600.png (1600×640, README hero size)
#   assets/agent-m/banner-3200.png (3200×1280, retina/2x)
#
# Reqs: a Chrome install (macOS, Linux apt, or Windows in Git Bash / MSYS).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BANNER_SRC="$REPO_ROOT/assets/banner.html"
CHANGELOG="$REPO_ROOT/CHANGELOG.md"
OUT_DIR="$REPO_ROOT/assets/agent-m"

# ---------- detect headless Chrome (cross-platform) ----------
detect_chrome() {
  case "$(uname -s)" in
    Darwin)
      [ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ] && \
        { echo "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"; return 0; }
      ;;
    Linux)
      for c in google-chrome google-chrome-stable chromium chromium-browser; do
        command -v "$c" >/dev/null 2>&1 && { command -v "$c"; return 0; }
      done
      ;;
    MINGW*|CYGWIN*|MSYS*)
      for c in "/c/Program Files/Google/Chrome/Application/chrome.exe" \
               "/c/Program Files (x86)/Google/Chrome/Application/chrome.exe"; do
        [ -x "$c" ] && { echo "$c"; return 0; }
      done
      ;;
  esac
  return 1
}

CHROME="$(detect_chrome)" || {
  echo "ERROR: headless Chrome not found." >&2
  echo "  macOS:   install Google Chrome from https://chrome.google.com/" >&2
  echo "  Linux:   apt install google-chrome-stable  OR  apt install chromium-browser" >&2
  echo "  Windows: install Chrome to default Program Files path" >&2
  exit 1
}

[ -f "$BANNER_SRC" ] || { echo "ERROR: banner source missing at $BANNER_SRC" >&2; exit 1; }

# ---------- determine target version ----------
NEW_VERSION="${1:-}"
if [ -z "$NEW_VERSION" ]; then
  # Auto-detect from CHANGELOG.md first "## [vX.Y.Z] — ..." header
  [ -f "$CHANGELOG" ] || { echo "ERROR: no CHANGELOG at $CHANGELOG and no version arg given" >&2; exit 1; }
  NEW_VERSION="$(grep -E '^## \[v?[0-9]+\.[0-9]+\.[0-9]+\]' "$CHANGELOG" | head -1 | grep -oE 'v?[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
  [ -n "$NEW_VERSION" ] || { echo "ERROR: could not auto-detect version from $CHANGELOG" >&2; exit 1; }
  [[ "$NEW_VERSION" == v* ]] || NEW_VERSION="v$NEW_VERSION"
  echo "Auto-detected version from CHANGELOG: $NEW_VERSION"
fi

# ---------- substitute version into banner.html ----------
# Match: [ Version &nbsp;<span class="sep">|</span> vX.Y.Z ]
# BSD/GNU sed compatibility via -i.bak then remove backup
sed -i.bak -E "s|(Version &nbsp;<span class=\"sep\">\|</span> )v[0-9]+\.[0-9]+\.[0-9]+|\1${NEW_VERSION}|" "$BANNER_SRC"
rm -f "$BANNER_SRC.bak"
echo "Updated $BANNER_SRC version line to $NEW_VERSION"

# ---------- render ----------
mkdir -p "$OUT_DIR"

render() {
  local w="$1" h="$2" out="$3"
  echo "Rendering ${w}×${h} → $out ..."
  "$CHROME" \
    --headless --disable-gpu --hide-scrollbars \
    --window-size="$w,$h" \
    --default-background-color=00000000 \
    --screenshot="$out" \
    "file://$BANNER_SRC" 2>/dev/null
}

render 1600 640  "$OUT_DIR/banner-1600.png"
render 3200 1280 "$OUT_DIR/banner-3200.png"

# Portable file-size readout
fsize() {
  if stat -f %z "$1" >/dev/null 2>&1; then stat -f %z "$1"
  else stat -c %s "$1"
  fi
}

echo ""
echo "Done."
echo "  $OUT_DIR/banner-1600.png ($(fsize "$OUT_DIR/banner-1600.png") bytes)"
echo "  $OUT_DIR/banner-3200.png ($(fsize "$OUT_DIR/banner-3200.png") bytes)"
echo ""
echo "Commit the regenerated banners + banner.html change alongside the release."
