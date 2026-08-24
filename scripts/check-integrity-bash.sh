#!/usr/bin/env bash
# check-integrity-bash.sh — post-install integrity check on a scratch install prefix.
#
# Called by smoke-install-bash.sh after the bash installer runs into $PREFIX.
# Verifies the installed tree is actually usable on a bash host: every hook
# command points at a file that exists, every installed helper parses cleanly,
# and settings.json uses bash command strings (not pwsh).
#
# Usage: bash scripts/check-integrity-bash.sh <install-prefix>
#
# The hook-path check matters MORE under a machine-wide install than it did
# under the retired per-project one. Project-scope hooks were registered with
# paths relative to the project root, so a wrong path was usually still a path
# into a tree that existed. Machine-wide hooks are registered with absolute
# paths into the install prefix, and a hook whose command points at a file that
# is not there fails silently — the hook simply never fires, with nothing said.

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <install-prefix>" >&2
  exit 2
fi

PREFIX="$1"
fail=0

if [[ ! -d "$PREFIX" ]]; then
  echo "FAIL: install prefix $PREFIX does not exist" >&2
  exit 1
fi

SETTINGS="$PREFIX/settings.json"
if [[ ! -f "$SETTINGS" ]]; then
  echo "FAIL: $SETTINGS missing" >&2
  exit 1
fi

# ── 1. Hook command strings reference files that exist ────────────────────
echo "  [integrity] hook command paths resolve"
python3 - "$SETTINGS" "$PREFIX" <<'PY' || fail=1
import json, os, re, sys
settings_path, prefix = sys.argv[1], sys.argv[2]
s = json.load(open(settings_path))
missing = []
# Machine-wide hooks are absolutized to <prefix>/hooks/<name>/<name>.sh at
# merge time. Pull every .sh/.ps1 path out of each command and require it to
# exist; a dangling one is a hook that will never fire.
path_re = re.compile(r'(/[A-Za-z0-9_./-]+\.(?:sh|ps1))')
for evt, lst in (s.get('hooks') or {}).items():
    for item in lst:
        for h in item.get('hooks', []):
            cmd = h.get('command', '')
            for m in path_re.finditer(cmd):
                p = m.group(1)
                # Only police paths this install owns. An operator's own hook
                # may legitimately point anywhere on their machine.
                if not p.startswith(prefix.rstrip('/') + '/'):
                    continue
                if not os.path.exists(p):
                    missing.append(f'{evt}: {p}')
if missing:
    print('FAIL: hook commands reference missing files:')
    for m in missing:
        print(f'  {m}')
    sys.exit(1)
print('    hook paths OK')
PY

# ── 2. Bash host invariant: no pwsh-prefixed commands in settings.json ────
echo "  [integrity] bash-host shell invariant"
python3 - "$SETTINGS" <<'PY' || fail=1
import json, sys
s = json.load(open(sys.argv[1]))
bad = []
for evt, lst in (s.get('hooks') or {}).items():
    for item in lst:
        for h in item.get('hooks', []):
            cmd = h.get('command', '')
            # On a bash host, hook commands invoke bash (or python3/jq), never
            # "pwsh -". That would mean the wrong fragment was installed.
            if cmd.strip().startswith('pwsh '):
                bad.append(f'{evt}: {cmd[:60]}')
if bad:
    print('FAIL: bash install has pwsh-prefixed hook commands:')
    for b in bad:
        print(f'  {b}')
    sys.exit(1)
print('    bash-host shell OK')
PY

# ── 3. Every installed .sh parses with bash -n (and enough of them exist) ──
echo "  [integrity] .sh syntax"
sh_count=0
while IFS= read -r -d '' f; do
  if ! bash -n "$f" 2>&1; then
    echo "FAIL: bash -n $f" >&2
    fail=1
  fi
  sh_count=$((sh_count + 1))
done < <(find "$PREFIX" -type f -name '*.sh' -print0)
# The five memory/harness hooks each ship a .sh, plus scripts/telemetry.sh.
# A count near zero means the installer silently skipped the bash surface.
if [[ $sh_count -lt 5 ]]; then
  echo "FAIL: only $sh_count .sh files installed — bash helpers missing" >&2
  fail=1
fi
echo "    $sh_count installed .sh files parse"

# ── 4. Required agent / skill files non-empty ────────────────────────────
# The phase-gated dev loop (plan/work/review/release/bugfix) + the review
# sub-agents were slimmed out in the V5 unbundling (now provided by the crickets
# development-lifecycle / code-review plugins), so they no longer install. The
# surviving harness-vendored surface is the memory-engine sub-agents plus the
# shared skills.
required_non_empty=(
  agents/adapt-evaluator.md
  agents/memory-idea-researcher.md
  skills/doctor/SKILL.md
  skills/memory/SKILL.md
)
for p in "${required_non_empty[@]}"; do
  if [[ ! -s "$PREFIX/$p" ]]; then
    echo "FAIL: $p is missing or empty" >&2
    fail=1
  fi
done

# ── 5. settings.json round-trips as valid JSON w/ the expected hook schema ─
echo "  [integrity] settings.json round-trip"
python3 - "$SETTINGS" <<'PY' || fail=1
import json, sys
s = json.load(open(sys.argv[1]))
hooks = s.get('hooks') or {}
if not hooks:
    print('FAIL: settings.json registers no hooks at all')
    sys.exit(1)
# Assert the SHAPE of every registered event rather than a hardcoded event
# list: which events the shipped hooks use is theirs to change, but each entry
# must always be a non-empty array whose first item carries a matcher and at
# least one command, or the host silently ignores it.
for evt, v in hooks.items():
    if not isinstance(v, list) or not v:
        print(f'FAIL: hooks.{evt} is not a non-empty array')
        sys.exit(1)
    if 'matcher' not in v[0] or not v[0].get('hooks'):
        print(f'FAIL: hooks.{evt}[0] missing matcher or hooks')
        sys.exit(1)
    if not v[0]['hooks'][0].get('command'):
        print(f'FAIL: hooks.{evt}[0].hooks[0].command is empty')
        sys.exit(1)
print(f'    settings.json schema OK ({len(hooks)} events)')
PY

# ── 6. install state is present and parseable ─────────────────────────────
echo "  [integrity] install state"
python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
assert d.get('harness_version'), 'harness_version missing'
assert d.get('installer_source'), 'installer_source missing'
" "$PREFIX/.agentm-config.json" \
  || { echo "FAIL: $PREFIX/.agentm-config.json invalid or incomplete" >&2; fail=1; }

if [[ $fail -ne 0 ]]; then
  echo "check-integrity-bash: FAILED" >&2
  exit 1
fi
echo "check-integrity-bash: OK"
