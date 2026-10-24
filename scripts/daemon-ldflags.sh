#!/usr/bin/env bash
# daemon-ldflags.sh — print the -ldflags that stamp a daemon build with the
# checkout it was built from (#819).
#
# Usage: scripts/daemon-ldflags.sh <checkout>
#
# Go stamps vcs.revision on its own, but it looks for a `.git` directory, so a
# build from a git worktree walks past the worktree's `.git` file to the clone
# that encloses it and stamps that clone's HEAD. The build scripts know which
# checkout they are building, so they say it here: the full commit, whether
# daemon/ (the only tree compiled in) had uncommitted changes, and
# `git describe --tags` as the version. `agentmd status` and `version` print it.
#
# Outside a git checkout it prints nothing, and the build keeps Go's own stamp
# or none.
set -euo pipefail

checkout="${1:?usage: daemon-ldflags.sh <checkout>}"
pkg="github.com/alexherrero/agentm/daemon/internal/buildinfo"

commit="$(git -C "$checkout" rev-parse HEAD 2>/dev/null)" || exit 0
modified=false
if [[ -n "$(git -C "$checkout" status --porcelain -- daemon 2>/dev/null)" ]]; then
  modified=true
fi
flags="-X $pkg.commit=$commit -X $pkg.modified=$modified"
version="$(git -C "$checkout" describe --tags --always 2>/dev/null || true)"
if [[ -n "$version" ]]; then
  flags+=" -X main.version=$version"
fi
printf '%s\n' "$flags"
