# recent-wiki-changes.ps1 — cross-repo "show me all my recent wiki changes" surface.
#
# Windows twin of recent-wiki-changes.sh. Walks repo_registry.list_repos()
# from V4 #30 plan 1; for each registered repo's root_path, walks wiki/
# subtree for files modified within the last N days; emits sorted table.
#
# Usage:
#   pwsh -File scripts\recent-wiki-changes.ps1 [-Repo <slug>] [-Days <N>] [-Limit <N>] [-VaultPath <path>]
#
# Env: MEMORY_ROOT (the memory root; MEMORY_VAULT_PATH is its deprecated alias), AGENTM_WIKI_RECENT_DAYS
#
# Built as part of V4 #30 plan 2 task 6.

#Requires -Version 7.0
[CmdletBinding()]
param(
    [string]$Repo = '',
    [int]$Days = 0,
    [int]$Limit = 50,
    [string]$VaultPath = ''
)

$ErrorActionPreference = 'Stop'

if (-not $VaultPath) { $VaultPath = if ($env:MEMORY_ROOT) { $env:MEMORY_ROOT } else { $env:MEMORY_VAULT_PATH } }
# Fall back to the memory root the kernel config resolves to when env+CLI are empty
# (the registry is <memory-root>/_meta/repos.json, so the bare vault_path is the wrong root on the split layout).
if (-not $VaultPath) {
    try {
        $VaultPath = (& python3 '-c' 'import sys; sys.path.insert(0, sys.argv[1]); import harness_memory; print(harness_memory.memory_root() or "")' $PSScriptRoot 2>$null | Out-String).Trim()
    } catch { $VaultPath = '' }
}
if (-not $VaultPath -or -not (Test-Path -LiteralPath $VaultPath -PathType Container)) {
    Write-Output '{"skipped": true, "reason": "MEMORY_ROOT unset AND no vault_path in .agentm-config.json (or resolved directory missing). Run agentm_config.py --vault-path <path> to set."}'
    exit 1
}

if ($Days -le 0) {
    if ($env:AGENTM_WIKI_RECENT_DAYS) {
        $Days = [int]$env:AGENTM_WIKI_RECENT_DAYS
    } else {
        $Days = 7
    }
}

$scriptDir = Split-Path -Parent $PSCommandPath
$registryPy = Join-Path $scriptDir 'repo_registry.py'
if (-not (Test-Path $registryPy)) {
    $registryPy = Join-Path (Join-Path $scriptDir '..') 'lib/install/python/repo_registry.py'
}
if (-not (Test-Path $registryPy)) {
    Write-Error "repo_registry.py not found relative to $scriptDir"
    exit 1
}

$pythonCmd = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $pythonCmd) { $pythonCmd = Get-Command python -ErrorAction SilentlyContinue }
if (-not $pythonCmd) {
    Write-Error 'python3 required on PATH'
    exit 1
}

# Set env for the Python child
$env:MEMORY_ROOT = $VaultPath
$env:MEMORY_VAULT_PATH = $VaultPath   # deprecated alias, same value
$env:AGENTM_WIKI_RECENT_DAYS = $Days
$env:_RWC_REPO_FILTER = $Repo
$env:_RWC_LIMIT = $Limit
$env:_RWC_REGISTRY_PY = $registryPy

# Embedded Python script — delegates the walk + table format.
$pythonScript = @'
import json
import os
import subprocess
import sys
import time
from pathlib import Path

vault = os.environ["MEMORY_ROOT"]
days = int(os.environ.get("AGENTM_WIKI_RECENT_DAYS", "7"))
filter_slug = os.environ.get("_RWC_REPO_FILTER", "")
limit = int(os.environ.get("_RWC_LIMIT", "50"))
registry_py = os.environ["_RWC_REGISTRY_PY"]

try:
    res = subprocess.run(
        [sys.executable, registry_py, "list"],
        capture_output=True, text=True, env={**os.environ, "MEMORY_ROOT": vault, "MEMORY_VAULT_PATH": vault},
    )
    data = json.loads(res.stdout or '{"repos": []}')
except Exception:
    data = {"repos": []}

repos = data.get("repos", [])
if filter_slug:
    repos = [r for r in repos if r.get("slug") == filter_slug]

if not repos:
    if filter_slug:
        print(f"No repo registered with slug: {filter_slug}", file=sys.stderr)
    else:
        print("No repos registered in <vault>/_meta/repos.json.", file=sys.stderr)
    sys.exit(0)

cutoff = time.time() - (days * 86400)
rows = []
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
        mode = parts[0] if parts and parts[0] in VALID_MODES else "-"
        page = str(rel)
        rows.append((mtime, slug, mode, page))

rows.sort(key=lambda r: r[0], reverse=True)
rows = rows[:limit]

if not rows:
    print(f"No wiki changes in the last {days} day(s) across registered repos.", file=sys.stderr)
    sys.exit(0)

headers = ["SLUG", "MODE", "PAGE", "MODIFIED"]
formatted = [headers]
for mtime, slug, mode, page in rows:
    iso = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
    formatted.append([slug, mode, page, iso])
widths = [max(len(r[i]) for r in formatted) for i in range(4)]
header = formatted[0]
print(f"{header[0]:<{widths[0]}}  {header[1]:<{widths[1]}}  {header[2]:<{widths[2]}}  {header[3]:<{widths[3]}}")
print(f"{'-'*widths[0]}  {'-'*widths[1]}  {'-'*widths[2]}  {'-'*widths[3]}")
for r in formatted[1:]:
    print(f"{r[0]:<{widths[0]}}  {r[1]:<{widths[1]}}  {r[2]:<{widths[2]}}  {r[3]:<{widths[3]}}")
print(f"\n({len(rows)} row(s); last {days} day(s); --limit {limit})")
'@

# Pipe the script to python via stdin
$pythonScript | & $pythonCmd.Source -
exit $LASTEXITCODE
