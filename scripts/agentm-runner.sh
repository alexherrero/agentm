#!/usr/bin/env bash
# agentm-runner.sh — the runner's host-agnostic entry point (agentm-runner.md).
#
# Every trigger (Claude Desktop / Antigravity Scheduled Tasks, OS cron/launchd,
# or an on-demand call) invokes this same script; only the trigger differs.
# Runs from scripts/ as cwd so `runner.cli`'s sibling-module import of
# `vault_lock` (scripts/vault_lock.py) resolves via sys.path.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
cd "$HERE"
# `runner.cli`'s --jobs-dir/--harness-dir default to CWD-relative paths
# (".harness/jobs", ".harness"), which only resolve correctly from the repo
# root. Since this script must cd into scripts/ for the sibling import above,
# those defaults would silently resolve to scripts/.harness/jobs -- a
# directory that never exists, so `manifest.load_manifests()` returns []
# (its own documented "fresh install, no jobs configured" contract, not an
# error) instead of failing loud. Every launchd-triggered cycle since this
# script was first built (2026-07-05) has run clean with zero jobs discovered
# for exactly this reason -- pass the repo-root-anchored paths explicitly so
# job discovery no longer depends on the cd above.
# A launchd LaunchAgent gets no shell profile and no environment beyond what
# the plist's own EnvironmentVariables block sets (PATH only, on this
# machine) -- MEMORY_VAULT_PATH is never one of them. A job manifest's own
# command (e.g. observability-digest-daily.yaml's `--vault-path
# "$MEMORY_VAULT_PATH"`) then silently expands to an empty string, which
# Path("") resolves to cwd (scripts/, per the cd above) -- inbox_digest.py's
# own is_dir() check passes on that and writes a real note into
# scripts/desk/briefs/ instead of the actual vault, exit 0, no error anywhere.
# Same category of bug as the --jobs-dir fix above (an environment-poor
# launcher's assumptions baked into a script that has to be launcher-
# agnostic) -- resolve it here via the canonical resolver rather than
# depending on the launchd plist to have set it.
#
# The resolver is memory_root(), not vault_path(): $MEMORY_VAULT_PATH names the
# agent's own tree to every consumer that reads it -- recall.py, reflect.py,
# capture.py and inbox_digest.py all join `memory/`, `_meta/` or `desk/briefs/`
# onto it -- so an export is already a memory root, which is exactly what
# memory_root()'s contract says. Exporting vault_path() here put a *vault* root
# in a variable read as a *memory* root, and after the 2026-08-10 git-transport
# cutover made those two different directories it sent every runner-launched job
# one level too high: the 2026-08-10 daily digest landed in `<vault>/desk/briefs/`
# instead of `<vault>/Agent/desk/briefs/`. The reflect/recall hooks were corrected at
# the cutover and already join the configured prefix; the runner was the last
# unpatched export. memory_root() falls back to vault_path() when the config key
# is unset, so an install whose vault root IS its memory root is unchanged.
if [[ -z "${MEMORY_VAULT_PATH:-}" ]]; then
    _resolved_vault="$(python3 -c 'import harness_memory; print(harness_memory.memory_root() or "")' 2>/dev/null || true)"
    if [[ -n "$_resolved_vault" ]]; then
        export MEMORY_VAULT_PATH="$_resolved_vault"
    fi
    unset _resolved_vault
fi

python3 -m runner.cli "$@" --jobs-dir "$REPO_ROOT/.harness/jobs" --harness-dir "$REPO_ROOT/.harness"
_runner_exit=$?

# Commit the engine state repo, if it is one (filing-v2 part 2a). Machine
# state left the vault for $AGENTM_STATE_DIR / ~/.local/state/agentm, and its
# durability property — history — moved with it: the 2a migration initializes
# that directory as a git repository, and this is the cadence that commits it,
# beside the vault commits the daemon already makes per write. Best-effort by
# design: a machine whose state dir was never migrated (no .git) skips
# silently, and a commit failure never fails the runner — the jobs above are
# the runner's contract, this is bookkeeping.
_state_dir="${AGENTM_STATE_DIR:-$HOME/.local/state/agentm}"
if [[ -d "$_state_dir/.git" ]]; then
    if ! git -C "$_state_dir" diff --quiet HEAD 2>/dev/null \
        || [[ -n "$(git -C "$_state_dir" ls-files --others --exclude-standard 2>/dev/null | head -1)" ]]; then
        git -C "$_state_dir" add -A 2>/dev/null \
            && git -C "$_state_dir" commit -q -m "engine state: runner cadence commit" 2>/dev/null \
            || echo "agentm-runner: engine-state commit failed (non-fatal)" >&2
    fi
fi

exit "$_runner_exit"
