#!/usr/bin/env bash
# recent-wiki-changes.sh — cross-repo "show me all my recent wiki changes" surface.
#
# Walks `repo_registry.list_repos()` (the registry lives in engine state, at
# `~/.local/state/agentm/repos.json` unless `$AGENTM_STATE_DIR` says otherwise);
# for each registered repo's `root_path`, walks the `wiki/` subtree for files
# modified within the last N days; emits a one-row-per-modified-page table
# sorted by mtime descending.
#
# Built as part of V4 #30 plan 2 task 6 (Wiki I/O codification +
# cross-repo views). Companion to the new `/recent-wiki-changes` slash
# command (task 7).
#
# Usage:
#   bash agentm/scripts/recent-wiki-changes.sh [OPTIONS]
#
# Options:
#   --repo <slug>       Filter to one repo only (default: all registered)
#   --days N            Override AGENTM_WIKI_RECENT_DAYS env (default: 7)
#   --limit N           Cap rows shown (default: 50)
#   --vault-path <path> Override $MEMORY_ROOT env (the memory root)
#   --help, -h          Print this help and exit
#
# Env:
#   MEMORY_ROOT               the memory root (optional; a vault's legacy registry
#                             copy sits at `<memory-root>/_meta/repos.json`);
#                             MEMORY_VAULT_PATH is its deprecated alias
#   AGENTM_WIKI_RECENT_DAYS   default recent-window in days (default: 7)
#
# Exit:
#   0  success (may emit 0 rows if no recent changes or no registered repos)
#   1  graceful skip with a JSON skip marker: the memory root is not a directory,
#      or repo_registry.py cannot select a storage backend
#   2  argument error

set -euo pipefail

VAULT_PATH="${MEMORY_ROOT:-${MEMORY_VAULT_PATH:-}}"
REPO_FILTER=""
DAYS="${AGENTM_WIKI_RECENT_DAYS:-7}"
LIMIT=50

print_help() {
    sed -n '/^# recent-wiki-changes.sh/,/^[^#]/p' "$0" | sed 's|^# \?||' | sed '$d'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo)
            REPO_FILTER="${2:-}"
            [[ -z "$REPO_FILTER" ]] && { echo "--repo requires a value" >&2; exit 2; }
            shift 2
            ;;
        --days)
            DAYS="${2:-}"
            [[ -z "$DAYS" ]] && { echo "--days requires a value" >&2; exit 2; }
            if ! [[ "$DAYS" =~ ^[0-9]+$ ]]; then
                echo "--days must be a positive integer, got: $DAYS" >&2; exit 2
            fi
            shift 2
            ;;
        --limit)
            LIMIT="${2:-}"
            [[ -z "$LIMIT" ]] && { echo "--limit requires a value" >&2; exit 2; }
            if ! [[ "$LIMIT" =~ ^[0-9]+$ ]]; then
                echo "--limit must be a positive integer, got: $LIMIT" >&2; exit 2
            fi
            shift 2
            ;;
        --vault-path)
            VAULT_PATH="${2:-}"
            [[ -z "$VAULT_PATH" ]] && { echo "--vault-path requires a value" >&2; exit 2; }
            shift 2
            ;;
        --help|-h) print_help; exit 0 ;;
        *) echo "Unknown option: $1" >&2; echo "" >&2; print_help >&2; exit 2 ;;
    esac
done

# Resolution order: --vault-path CLI → $MEMORY_ROOT env (or its deprecated alias
# $MEMORY_VAULT_PATH; set as $VAULT_PATH default above) → the memory root the
# kernel config resolves to. None of them is required: the registry lives in the
# engine state directory (`harness_memory.engine_state_dir() / "repos.json"`),
# which needs no vault. A memory root still matters while a vault holds the only
# copy of the registry, because repo_registry.py then reads that legacy copy at
# `<memory-root>/_meta/repos.json` through the vault backend, and an exported
# $MEMORY_ROOT roots that backend there (and selects it when the config names no
# backend). So the value exported is the memory root: the config's bare
# vault_path (exported here until 2026-09-11) would point that read at
# `<vault>/_meta/` on the split layout.
if [[ -z "$VAULT_PATH" ]]; then
    VAULT_PATH="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import harness_memory; print(harness_memory.memory_root() or "")' "$(dirname "$0")" 2>/dev/null || true)"
fi
if [[ -n "$VAULT_PATH" && ! -d "$VAULT_PATH" ]]; then
    echo '{"skipped": true, "reason": "The memory root (--vault-path, $MEMORY_ROOT or the configured vault_path) is not a directory."}'
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY_PY="$SCRIPT_DIR/repo_registry.py"
if [[ ! -f "$REGISTRY_PY" ]]; then
    REGISTRY_PY="$SCRIPT_DIR/../lib/install/python/repo_registry.py"
fi
if [[ ! -f "$REGISTRY_PY" ]]; then
    echo "Error: repo_registry.py not found relative to $SCRIPT_DIR" >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 required on PATH" >&2
    exit 1
fi

# Delegate the heavy lifting to a Python script via stdin to avoid bash heredoc
# quote-nesting hell. Exports env so the registry CLI the child runs sees the
# same memory root.
if [[ -n "$VAULT_PATH" ]]; then
    export MEMORY_ROOT="$VAULT_PATH"
    export MEMORY_VAULT_PATH="$VAULT_PATH"   # deprecated alias, same value
fi
export AGENTM_WIKI_RECENT_DAYS="$DAYS"
export _RWC_REPO_FILTER="$REPO_FILTER"
export _RWC_LIMIT="$LIMIT"
export _RWC_REGISTRY_PY="$REGISTRY_PY"

python3 - <<'PYEOF'
import json
import os
import subprocess
import sys
import time
from pathlib import Path

days = int(os.environ.get("AGENTM_WIKI_RECENT_DAYS", "7"))
filter_slug = os.environ.get("_RWC_REPO_FILTER", "")
limit = int(os.environ.get("_RWC_LIMIT", "50"))
registry_py = os.environ["_RWC_REGISTRY_PY"]

# Fetch registry via the existing CLI
try:
    res = subprocess.run(
        [sys.executable, registry_py, "list"],
        capture_output=True, text=True,
    )
    data = json.loads(res.stdout or '{"repos": []}')
except (subprocess.CalledProcessError, json.JSONDecodeError):
    data = {"repos": []}

# The registry CLI prints a skip marker when it cannot select a storage backend;
# relay it, so a misconfigured backend never reads as an empty registry.
if data.get("skipped"):
    print(json.dumps(data))
    sys.exit(1)

repos = data.get("repos", [])
if filter_slug:
    repos = [r for r in repos if r.get("slug") == filter_slug]

if not repos:
    if filter_slug:
        print(f"No repo registered with slug: {filter_slug}", file=sys.stderr)
        print(f"Available: python3 {registry_py} list", file=sys.stderr)
    else:
        print("No repos registered in ~/.local/state/agentm/repos.json ($AGENTM_STATE_DIR/repos.json when set).", file=sys.stderr)
        print(f"Register one: python3 {registry_py} register <slug> --root <path>", file=sys.stderr)
    sys.exit(0)

cutoff = time.time() - (days * 86400)
rows = []  # (mtime, slug, mode, page)

VALID_MODES = {"tutorials", "how-to", "reference", "explanation"}

for repo in repos:
    slug = repo.get("slug", "?")
    root = repo.get("root_path", "")
    if not root:
        continue
    wiki_dir = Path(root) / "wiki"
    if not wiki_dir.is_dir():
        continue
    for md_path in wiki_dir.rglob("*.md"):
        if not md_path.is_file():
            continue
        try:
            mtime = md_path.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            continue
        rel = md_path.relative_to(wiki_dir)
        parts = rel.parts
        mode = parts[0] if parts and parts[0] in VALID_MODES else "—"
        page = str(rel)
        rows.append((mtime, slug, mode, page))

rows.sort(key=lambda r: r[0], reverse=True)
rows = rows[:limit]

if not rows:
    print(f"No wiki changes in the last {days} day(s) across registered repos.", file=sys.stderr)
    sys.exit(0)

# Format aligned table
def col_widths():
    headers = ["SLUG", "MODE", "PAGE", "MODIFIED"]
    formatted = [headers]
    for mtime, slug, mode, page in rows:
        iso = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
        formatted.append([slug, mode, page, iso])
    return [max(len(r[i]) for r in formatted) for i in range(4)], formatted

widths, formatted = col_widths()
header = formatted[0]
print(f"{header[0]:<{widths[0]}}  {header[1]:<{widths[1]}}  {header[2]:<{widths[2]}}  {header[3]:<{widths[3]}}")
print(f"{'-'*widths[0]}  {'-'*widths[1]}  {'-'*widths[2]}  {'-'*widths[3]}")
for r in formatted[1:]:
    print(f"{r[0]:<{widths[0]}}  {r[1]:<{widths[1]}}  {r[2]:<{widths[2]}}  {r[3]:<{widths[3]}}")

print(f"\n({len(rows)} row(s); last {days} day(s); --limit {limit})")
PYEOF
