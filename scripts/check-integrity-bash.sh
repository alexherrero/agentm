#!/usr/bin/env bash
# check-integrity-bash.sh — post-install integrity check on a scratch dir.
#
# Called by smoke-install-bash.sh after the bash installer runs into
# $SCRATCH. Verifies that the installed tree is actually usable on a
# bash host: every hook command points at a file that exists, every
# installed helper parses cleanly, and the settings.json uses the
# bash-shell command strings (not pwsh).
#
# Usage: bash scripts/check-integrity-bash.sh <SCRATCH_DIR>

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <scratch-dir>" >&2
  exit 2
fi

SCRATCH="$1"
fail=0

if [[ ! -d "$SCRATCH" ]]; then
  echo "FAIL: scratch dir $SCRATCH does not exist" >&2
  exit 1
fi

# ── 1. Hook command strings reference files that exist ────────────────────
echo "  [integrity] hook command paths resolve"
python3 - "$SCRATCH/.claude/settings.json" "$SCRATCH" <<'PY' || fail=1
import json, os, re, sys
settings_path, scratch = sys.argv[1], sys.argv[2]
s = json.load(open(settings_path))
missing = []
# Scan every hook command for relative paths to .harness/... and verify existence.
path_re = re.compile(r'(\.harness/[A-Za-z0-9_./-]+\.(?:sh|ps1))')
for evt, lst in (s.get('hooks') or {}).items():
    for item in lst:
        for h in item.get('hooks', []):
            cmd = h.get('command', '')
            for m in path_re.finditer(cmd):
                rel = m.group(1)
                full = os.path.join(scratch, rel)
                if not os.path.exists(full):
                    missing.append(f'{evt}: {rel}')
if missing:
    print('FAIL: hook commands reference missing files:')
    for m in missing:
        print(f'  {m}')
    sys.exit(1)
print('    hook paths OK')
PY

# ── 2. Bash host invariant: no pwsh-prefixed commands in settings.json ────
echo "  [integrity] bash-host shell invariant"
python3 - "$SCRATCH/.claude/settings.json" <<'PY' || fail=1
import json, sys
s = json.load(open(sys.argv[1]))
bad = []
for evt, lst in (s.get('hooks') or {}).items():
    for item in lst:
        for h in item.get('hooks', []):
            cmd = h.get('command', '')
            # On a bash host, hook commands should invoke bash (or jq),
            # never "pwsh -". That would indicate the wrong fragment was
            # installed.
            if cmd.strip().startswith('pwsh '):
                bad.append(f'{evt}: {cmd[:60]}')
if bad:
    print('FAIL: bash install has pwsh-prefixed hook commands:')
    for b in bad:
        print(f'  {b}')
    sys.exit(1)
print('    bash-host shell OK')
PY

# ── 3. Every installed .sh parses with bash -n (and at least one exists) ──
echo "  [integrity] .sh syntax"
sh_count=0
while IFS= read -r -d '' f; do
  if ! bash -n "$f" 2>&1; then
    echo "FAIL: bash -n $f" >&2
    fail=1
  fi
  sh_count=$((sh_count + 1))
done < <(find "$SCRATCH" -type f -name '*.sh' -print0)
# A bash install must ship verify.sh + hook .sh helpers. 0 would mean the
# installer silently skipped the bash block.
if [[ $sh_count -lt 5 ]]; then
  echo "FAIL: only $sh_count .sh files installed — bash helpers missing" >&2
  fail=1
fi
echo "    $sh_count installed .sh files parse"

# ── 4. Required utility-command / agent / skill files non-empty ──────────
# The phase-gated dev loop (plan/work/review/release/bugfix) + the review
# sub-agents were slimmed out in the V5 unbundling (now provided by the crickets
# developer-workflows / code-review plugins), so they no longer install. The
# surviving harness-vendored surface is: the recent-wiki-changes utility
# command, the memory-engine sub-agents, the shared skills (doctor,
# migrate-to-diataxis), and the Antigravity rules + Gemini settings.json.
required_non_empty=(
  .claude/commands/recent-wiki-changes.md
  .claude/agents/adapt-evaluator.md
  .claude/skills/doctor/SKILL.md
  .claude/skills/migrate-to-diataxis/SKILL.md
  .agents/rules/harness.md
  .agents/skills/doctor/SKILL.md
  .agents/skills/migrate-to-diataxis/SKILL.md
  .gemini/settings.json
)
for p in "${required_non_empty[@]}"; do
  full="$SCRATCH/$p"
  if [[ ! -s "$full" ]]; then
    echo "FAIL: $p is missing or empty" >&2
    fail=1
  fi
done

# ── 5. settings.json round-trips as valid JSON w/ expected hook schema ────
echo "  [integrity] settings.json round-trip"
python3 - "$SCRATCH/.claude/settings.json" <<'PY' || fail=1
import json, sys
s = json.load(open(sys.argv[1]))
expected_events = {'PostToolUse', 'PreCompact', 'SessionStart'}
got = set((s.get('hooks') or {}).keys())
if not expected_events.issubset(got):
    print(f'FAIL: settings.json hooks missing events: {expected_events - got}')
    sys.exit(1)
# Each event must be a list with at least one entry that has matcher + hooks[0].command.
for evt in expected_events:
    v = s['hooks'][evt]
    if not isinstance(v, list) or not v:
        print(f'FAIL: hooks.{evt} is not a non-empty array')
        sys.exit(1)
    if 'matcher' not in v[0] or not v[0].get('hooks'):
        print(f'FAIL: hooks.{evt}[0] missing matcher or hooks')
        sys.exit(1)
    if not v[0]['hooks'][0].get('command'):
        print(f'FAIL: hooks.{evt}[0].hooks[0].command is empty')
        sys.exit(1)
print('    settings.json schema OK')
PY

# ── 6. .gemini/settings.json valid JSON ────────────────────────────────────
echo "  [integrity] .gemini/settings.json"
python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$SCRATCH/.gemini/settings.json" \
  || { echo "FAIL: .gemini/settings.json is not valid JSON" >&2; fail=1; }

# ── 7. .harness/features.json and .harness/PLAN.md present + parseable ────
echo "  [integrity] .harness state files"
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); assert 'features' in d" \
  "$SCRATCH/.harness/features.json" \
  || { echo "FAIL: .harness/features.json invalid" >&2; fail=1; }

if [[ ! -s "$SCRATCH/.harness/PLAN.md" ]]; then
  echo "FAIL: .harness/PLAN.md empty or missing" >&2
  fail=1
fi

if [[ $fail -ne 0 ]]; then
  echo "check-integrity-bash: FAILED" >&2
  exit 1
fi
echo "check-integrity-bash: OK"
