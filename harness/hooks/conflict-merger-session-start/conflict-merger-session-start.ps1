# conflict-merger-session-start.ps1 — Windows twin of the bash hook.
#
# Detects GDrive conflict files in the MemoryVault on SessionStart.
# Graceful-skip when MEMORY_VAULT_PATH unset or harness_memory.py unavailable.
# Non-blocking; emits findings on stderr; never freezes session boot.

$ErrorActionPreference = 'Continue'  # never block session boot on hook failure

$mode = if ($env:HARNESS_CONFLICT_MERGER_MODE) { $env:HARNESS_CONFLICT_MERGER_MODE } else { 'interactive' }
if ($mode -eq 'off') { exit 0 }

# Resolve the vault path: env -> .agentm-config.json vault_path -> none.
# Claude Code does not inject MEMORY_VAULT_PATH into the hook env on user-scope
# installs, so an env-only check silently skipped on every real session boot.
# Mirrors the bash twin's _resolve_vault_path().
$vaultPath = $env:MEMORY_VAULT_PATH
if (-not $vaultPath) {
    $prefix = if ($env:AGENTM_INSTALL_PREFIX) { $env:AGENTM_INSTALL_PREFIX } else { (Join-Path $HOME '.claude') }
    $cfg = Join-Path $prefix '.agentm-config.json'
    if ((Test-Path -LiteralPath $cfg -PathType Leaf) -and (Get-Command python3 -ErrorAction SilentlyContinue)) {
        $resolveDriver = @"
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
print(d.get('vault_path') or '')
"@
        $v = (& python3 -c $resolveDriver $cfg 2>$null | Out-String).Trim()
        if ($v) { $vaultPath = $v }
    }
}
if (-not $vaultPath) { exit 0 }
if (-not (Test-Path -LiteralPath $vaultPath -PathType Container)) { exit 0 }

# Resolve harness_memory.py path.
$candidates = @(
    (Join-Path $HOME 'Antigravity/agentm/scripts/harness_memory.py'),
    '../agentm/scripts/harness_memory.py',
    '../../agentm/scripts/harness_memory.py'
)
$hmPy = $null
foreach ($c in $candidates) {
    if (Test-Path -LiteralPath $c -PathType Leaf) {
        $hmPy = $c
        break
    }
}
if (-not $hmPy) { exit 0 }

$pythonScript = @"
import importlib.util, sys, os
from pathlib import Path
hm_path, vault_root = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location('harness_memory', hm_path)
hm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hm)
conflicts = hm.detect_conflict_files(Path(vault_root))
if not conflicts:
    sys.exit(0)
mode = os.environ.get('HARNESS_CONFLICT_MERGER_MODE', 'interactive')
sys.stderr.write(f"\n[conflict-merger] {len(conflicts)} GDrive conflict file(s) detected in vault:\n")
for entry in conflicts:
    sys.stderr.write(f"    conflict: {entry['rel']}\n")
    sys.stderr.write(f"    base:     {entry['base'].relative_to(Path(vault_root))}\n")
if mode == 'interactive':
    sys.stderr.write(
        "\n    To merge interactively: review each pair in Obsidian or via\n"
        "    diff base conflict and merge by hand. Run /work from the\n"
        "    affected repo if the conflict is in a vault-backed harness file.\n\n"
        "    To suppress this notice for the current session, set\n"
        "    HARNESS_CONFLICT_MERGER_MODE=silent in the environment.\n\n"
    )
"@

# stderr is intentionally not redirected — the Python writes findings there.
& python3 -c $pythonScript $hmPy $vaultPath

exit 0
