#!/usr/bin/env bash
# check-daemon.sh — build, vet, and test the Go memory daemon.
#
# Skips cleanly (exit 0 with a message) when Go is not installed, so a machine
# without the toolchain still gets a green battery rather than a failure that
# means nothing. CI installs Go, so the gate is real there.
#
# Two flags are load-bearing:
#
#   CGO_ENABLED=0   the daemon ships as a static binary. modernc.org/sqlite is
#                   pure Go with FTS5 built in, so this must hold — a build that
#                   only works with cgo is a different artifact from the one that
#                   cross-compiles to every box on the home network.
#
#   -count=1        the e2e suite drives the daemon as a built binary over HTTP.
#                   Without this flag Go can serve cached e2e results after a
#                   change to internal/, because the test package's compile-time
#                   dependencies do not cover the behaviour it exercises. A suite
#                   that can report PASS without running is the same failure as a
#                   suite that never asked the question.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
DAEMON="$REPO/daemon"

if ! command -v go >/dev/null 2>&1; then
  echo "check-daemon: SKIP — go is not installed on this machine"
  echo "  install with: brew install go   (or https://go.dev/dl/)"
  exit 0
fi

cd "$DAEMON" || { echo "check-daemon: no daemon/ directory" >&2; exit 1; }

echo "check-daemon: go $(go version | awk '{print $3}')"
export CGO_ENABLED=0

fail=0
run() {
  local name="$1"; shift
  printf '  … %s\n' "$name"
  if ! "$@"; then
    echo "  FAIL  $name"
    fail=1
  fi
}

run "gofmt (no unformatted files)" bash -c '
  out=$(gofmt -l .)
  if [ -n "$out" ]; then echo "unformatted:"; echo "$out"; exit 1; fi'
run "go vet" go vet ./...
run "go build (CGO_ENABLED=0)" go build ./...
run "go test -count=1" go test -count=1 ./...

# The daemon must not embed an absolute vault path. The repo-level
# check-no-hardcoded-vault-path gate covers tracked files generally; this is the
# same rule stated where the daemon's own resolver lives, because a cached path
# literal here would be the exact failure the vault-path convention exists to
# prevent.
run "no hardcoded vault literals" bash -c '
  if grep -rn "CloudStorage\|Obsidian/AgentMemory" --include="*.go" . | grep -v "_test.go" | grep -v "^\./internal/config/config.go:.*//"; then
    echo "a vault path literal is embedded in daemon source"; exit 1
  fi'

# `go mod tidy` is a no-op on a tidy module. CI has always checked this; the local
# battery did not, so a dependency added with `go get` and left marked `// indirect`
# passed here and failed there — which is exactly the round trip a local battery
# exists to save. Restores the copy on the way out either way, so a failing run
# never leaves the module edited.
run "dependencies are tidy" bash -c '
  cp go.mod go.mod.orig && cp go.sum go.sum.orig
  go mod tidy >/dev/null 2>&1
  rc=0
  if ! diff -q go.mod go.mod.orig >/dev/null || ! diff -q go.sum go.sum.orig >/dev/null; then
    echo "go mod tidy changed go.mod/go.sum — commit the tidied files:"
    diff -u go.mod.orig go.mod || true
    diff -u go.sum.orig go.sum || true
    rc=1
  fi
  mv go.mod.orig go.mod && mv go.sum.orig go.sum
  exit $rc'

exit "$fail"
