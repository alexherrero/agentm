#!/usr/bin/env bash
# deploy-daemon.sh — rebuild the daemon binaries from this checkout, swap them
# in over the live ones, and restart the resident daemon.
#
# Usage: scripts/deploy-daemon.sh [--no-restart] [agentmd] [agentmdream]
#        (no names = both)
#
# This is the redeploy half of install.sh's refresh mode, without the rest of
# the install. Each binary is built beside the live one as <name>.new and only
# renamed over it once the build succeeds, so a failed build leaves the running
# daemon untouched. The rename is atomic and the running process keeps its own
# inode until the restart.
#
# Use it instead of a hand-typed `go build -o …new && mv -f …`: `mv` is on the
# global ask list, so every by-hand redeploy stalled on a permission prompt.
#
# The restart is `launchctl kickstart -k`, followed by a /health probe. It is
# skipped off macOS, when agentmd was not rebuilt, and under --no-restart.
# `agentmd status` right after a swap without a restart still reports the old
# process.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="$HOME/.local/bin"
LABEL="com.agentm.daemon"
HEALTH_TIMEOUT="${AGENTM_DAEMON_HEALTH_TIMEOUT:-30}"

restart=1
names=()
for arg in "$@"; do
  case "$arg" in
    --no-restart) restart=0 ;;
    agentmd|agentmdream) names+=("$arg") ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "deploy-daemon: unknown argument: $arg" >&2; exit 2 ;;
  esac
done
[[ ${#names[@]} -gt 0 ]] || names=(agentmd agentmdream)

command -v go >/dev/null 2>&1 || { echo "deploy-daemon: Go is not installed (brew install go)" >&2; exit 1; }

rev="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "==> Deploying from $REPO @ $rev"
if [[ -n "$(git -C "$REPO" status --porcelain -- daemon 2>/dev/null)" ]]; then
  echo "    note: daemon/ has uncommitted changes; they are in this build" >&2
fi

mkdir -p "$BIN_DIR"
swapped_daemon=0
for name in "${names[@]}"; do
  live="$BIN_DIR/$name"
  if ( cd "$REPO/daemon" && CGO_ENABLED=0 go build -o "$live.new" "./cmd/$name" ); then
    mv -f "$live.new" "$live"
    echo "    swapped $live"
    [[ "$name" == agentmd ]] && swapped_daemon=1
  else
    rm -f "$live.new"
    echo "deploy-daemon: $name build failed; the live binary was left in place" >&2
    exit 1
  fi
done

if [[ $restart -eq 0 || $swapped_daemon -eq 0 || "$(uname -s)" != Darwin ]]; then
  echo "==> Done (no restart)"
  exit 0
fi

port="$(python3 "$REPO/scripts/agentm_config.py" --get daemon.port 2>/dev/null || true)"
[[ "$port" =~ ^[0-9]+$ ]] || port=7821

echo "==> Restarting $LABEL"
launchctl kickstart -k "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || {
  echo "deploy-daemon: kickstart failed — is the launchd job installed? (install.sh --daemon)" >&2
  exit 1
}
for _ in $(seq 1 "$HEALTH_TIMEOUT"); do
  if curl -fsS -m 2 "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
    echo "    /health answered on :$port"
    exit 0
  fi
  sleep 1
done
echo "deploy-daemon: the daemon did not answer /health on :$port within ${HEALTH_TIMEOUT}s (see agentmd status)" >&2
exit 1
