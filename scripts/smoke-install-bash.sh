#!/usr/bin/env bash
# smoke-install-bash.sh — install agentm machine-wide into a scratch prefix and
# assert the expected tree, the installer boundary, idempotence, and that a
# re-run preserves the operator's own settings.json entries.
#
# Used by tests-linux.yml and tests-mac.yml. Invoked from repo root:
#   bash scripts/smoke-install-bash.sh
#
# Exits non-zero on first failed assertion with a diagnostic.
#
# HERMETIC BY CONSTRUCTION — a scratch HOME as well as a scratch prefix.
# $AGENTM_INSTALL_PREFIX redirects only the customizations tree; install.sh
# keys the ~/.local/bin launcher, the launchd daemon plist, and
# ~/.gemini/GEMINI.md off $HOME. Without the fake HOME this smoke test would
# rewrite the developer's real launcher and rebuild their running daemon — which
# is exactly what happened while this suite was being rewritten.
#
# The fake HOME also FORCES RELEASE MODE (no ~/Antigravity/agentm clone at it),
# which is load-bearing rather than incidental: a developer running this locally
# on a machine with a real clone would otherwise silently exercise source mode
# and mask a release-mode-only regression — the exact bug class that once landed
# harness/{agents,skills,hooks} flat under the prefix instead of nested,
# invisible until CI hit the release path with no clone to fall back on.

set -euo pipefail

HARNESS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT

PREFIX="$SCRATCH/prefix"
FAKE_HOME="$SCRATCH/home"
mkdir -p "$PREFIX" "$FAKE_HOME"

run_install() {
  HOME="$FAKE_HOME" CI=true AGENTM_INSTALL_PREFIX="$PREFIX" \
    bash "$HARNESS_ROOT/install.sh" --no-daemon "$@"
}

echo "==> fresh install into $PREFIX"
run_install > "$SCRATCH/install.log"

fail=0

# ── release mode really was forced (keeps the rest of the run honest) ───────
if ! grep -q '"mode": "release"' "$PREFIX/.agentm-config.json"; then
  echo "FAIL: .agentm-config.json mode is not 'release' despite the empty fake HOME —" >&2
  echo "      the trick that keeps this test exercising the release path didn't work" >&2
  fail=1
fi

# ── expected files: the machine-wide tree ───────────────────────────────────
expected=(
  .agentm-config.json
  settings.json
  agents/adapt-evaluator.md
  agents/memory-idea-researcher.md
  skills/doctor/SKILL.md
  skills/memory/SKILL.md
  skills/console/SKILL.md
  skills/design/SKILL.md
  scripts/telemetry.sh
  hooks/harness-context-session-start/harness-context-session-start.sh
  hooks/memory-recall-prompt-submit/memory-recall-prompt-submit.sh
  hooks/memory-recall-session-start/memory-recall-session-start.sh
  hooks/memory-reflect-idle/memory-reflect-idle.sh
  hooks/memory-reflect-stop/memory-reflect-stop.sh
  hooks/verify-dispatch/verify-dispatch.sh
)
for p in "${expected[@]}"; do
  if [[ ! -e "$PREFIX/$p" ]]; then
    echo "MISSING: $p" >&2
    fail=1
  fi
done

# The PATH launcher lands under $HOME, not the prefix.
if [[ ! -x "$FAKE_HOME/.local/bin/agentm-update" ]]; then
  echo "MISSING: \$HOME/.local/bin/agentm-update (the update launcher)" >&2
  fail=1
fi

# ── installer boundary: this repo's own tooling must NOT leak ───────────────
leaks=(
  .github/workflows/tests-linux.yml
  .github/workflows/tests-mac.yml
  .github/workflows/tests-windows.yml
  scripts/smoke-install-bash.sh
  scripts/smoke-install-pwsh.ps1
  scripts/check-parity.sh
  scripts/validate-adapters.py
  scripts/check-references.py
  scripts/check-syntax.sh
  scripts/check-syntax.ps1
  scripts/check-integrity-bash.sh
  scripts/check-integrity-pwsh.ps1
  wiki/Home.md
)
for p in "${leaks[@]}"; do
  if [[ -e "$PREFIX/$p" ]]; then
    echo "LEAK: $p should not be in the install prefix (installer boundary)" >&2
    fail=1
  fi
done

# ── the per-project install is really gone ──────────────────────────────────
# Every one of these was produced by the retired --scope project path. Finding
# any of them means a project-scope code path survived the collapse.
retired_project_artifacts=(
  .harness/PLAN.md
  .harness/features.json
  .harness/progress.md
  .harness/init.sh
  .harness/.version
  .harness/verify.sh
  .harness/hooks/precompact.sh
  .harness/hooks/session-start-compact.sh
  .harness/scripts/cross-review.sh
  .claude/settings.json
  .agents/rules/harness.md
  .gemini/settings.json
  AGENTS.md
  CLAUDE.md
  .github/workflows/wiki-sync.yml
)
for p in "${retired_project_artifacts[@]}"; do
  for root in "$PREFIX" "$FAKE_HOME"; do
    if [[ -e "$root/$p" ]]; then
      echo "PROJECT-SCOPE LEAK: $root/$p — the per-project install is retired" >&2
      fail=1
    fi
  done
done

# ── V5 dev-loop slim: phase commands + review sub-agents must NOT install ────
# These moved to the crickets development-lifecycle / code-review plugins.
slimmed=(
  commands/plan.md
  commands/work.md
  commands/review.md
  commands/release.md
  commands/setup.md
  commands/bugfix.md
  agents/explorer.md
  agents/adversarial-reviewer.md
  agents/adversarial-reviewer-cross.md
  skills/explorer/SKILL.md
)
for p in "${slimmed[@]}"; do
  if [[ -e "$PREFIX/$p" ]]; then
    echo "SLIM-LEAK: $p should NOT install after the V5 dev-loop slim" >&2
    fail=1
  fi
done

# ── settings.json: valid JSON, all hook events stored as arrays ─────────────
python3 - "$PREFIX/settings.json" <<'PY' || fail=1
import json, sys
s = json.load(open(sys.argv[1]))
assert 'hooks' in s, 'hooks key missing'
for k, v in s['hooks'].items():
    assert isinstance(v, list), f'{k} is not array (got {type(v).__name__})'
    assert len(v) >= 1, f'{k} is empty'
    assert 'matcher' in v[0], f'{k}[0] missing matcher'
    assert 'hooks' in v[0] and isinstance(v[0]['hooks'], list), f'{k}[0].hooks missing/non-array'
print(f'    settings.json OK ({len(s["hooks"])} events)')
PY

# ── every installed hook's fragment actually merged (the V4 #39 path) ───────
# Installing hook dirs is not enough: each hook's settings-fragment-bash.json
# must be merged into <prefix>/settings.json, absolutized to the installed
# script. Dropping the dirs without merging is the bug class V4 #39 fixed, and
# it is silent — the hooks simply never fire.
user_hooks=(
  harness-context-session-start
  memory-recall-prompt-submit
  memory-recall-session-start
  memory-reflect-idle
  memory-reflect-stop
  verify-dispatch
)
python3 - "$PREFIX/settings.json" "${user_hooks[@]}" <<'PY' || fail=1
import json, sys
path, hook_names = sys.argv[1], sys.argv[2:]
s = json.load(open(path))
commands = [
    h.get("command", "")
    for entries in s.get("hooks", {}).values()
    for entry in entries
    for h in entry.get("hooks", [])
]
missing = [h for h in hook_names if not any(f"{h}.sh" in c for c in commands)]
if missing:
    print(f"FAIL: settings.json has no merged fragment for: {missing}", file=sys.stderr)
    sys.exit(1)
print(f"    settings.json: all {len(hook_names)} hook fragments merged")
PY

if [[ $fail -ne 0 ]]; then
  echo "FAIL: expected-files / boundary / settings.json assertions failed" >&2
  exit 1
fi

# ── post-install integrity ──────────────────────────────────────────────────
echo "==> post-install integrity"
bash "$HARNESS_ROOT/scripts/check-integrity-bash.sh" "$PREFIX"

# ── a re-run is idempotent, and preserves what the operator owns ────────────
# The retired --update flag used to carry this contract for cp_user files. The
# machine-wide equivalent is settings.json: a refresh must merge without
# duplicating its own entries and without dropping entries the operator added
# by hand. Same intent, current contract.
echo "==> re-run is idempotent + preserves operator-authored settings"
python3 - "$PREFIX/settings.json" <<'PY'
import json, sys
path = sys.argv[1]
s = json.load(open(path))
s.setdefault("hooks", {}).setdefault("Stop", []).append({
    "matcher": ".*",
    "hooks": [{"type": "command", "command": "bash /operator/authored/marker.sh"}],
})
json.dump(s, open(path, "w"), indent=2)
PY

BEFORE_COMMANDS="$(python3 -c "
import json,sys
s=json.load(open('$PREFIX/settings.json'))
print(sum(len(e.get('hooks',[])) for v in s.get('hooks',{}).values() for e in v))
")"

run_install > "$SCRATCH/rerun.log"

python3 - "$PREFIX/settings.json" "$BEFORE_COMMANDS" <<'PY' || exit 1
import json, sys
s = json.load(open(sys.argv[1]))
before = int(sys.argv[2])
commands = [
    h.get("command", "")
    for entries in s.get("hooks", {}).values()
    for entry in entries
    for h in entry.get("hooks", [])
]
if not any("/operator/authored/marker.sh" in c for c in commands):
    print("FAIL: re-run dropped an operator-authored settings.json hook", file=sys.stderr)
    sys.exit(1)
if len(commands) != before:
    print(
        f"FAIL: re-run changed the hook count {before} -> {len(commands)} "
        "(a refresh must not duplicate its own entries)",
        file=sys.stderr,
    )
    sys.exit(1)
print(f"    re-run idempotent ({len(commands)} hook commands, operator entry intact)")
PY

# ── --local-state: first-class repo-local (vault-less) mode ─────────────────
# Hardening I #44 task 4. Proves the entry point end to end: the flag writes
# state_mode:local to .agentm-config.json (the on-host config; DC-8), and a
# subsequent state write lands repo-local with NO vault configured. This is the
# state-mode axis, which is orthogonal to install scope and survives it intact.
echo "==> --local-state writes state_mode:local + state lands repo-local"
LOCAL_PREFIX="$SCRATCH/local-prefix"
LOCAL_PROJECT="$SCRATCH/local-project"
mkdir -p "$LOCAL_PREFIX" "$LOCAL_PROJECT/.harness"

HOME="$FAKE_HOME" CI=true AGENTM_INSTALL_PREFIX="$LOCAL_PREFIX" \
  bash "$HARNESS_ROOT/install.sh" --no-daemon --local-state > "$SCRATCH/local.log"

python3 - "$LOCAL_PREFIX/.agentm-config.json" <<'PY' || exit 1
import json, sys
c = json.load(open(sys.argv[1]))
assert c.get("state_mode") == "local", f"state_mode not 'local': {c.get('state_mode')!r}"
print("    state_mode:local OK")
PY

printf '{"vault_project": "smokedemo"}\n' > "$LOCAL_PROJECT/.harness/project.json"
echo "# smoke PLAN" | env -u MEMORY_VAULT_PATH AGENTM_INSTALL_PREFIX="$LOCAL_PREFIX" \
  python3 "$HARNESS_ROOT/scripts/harness_memory.py" write-state \
  --project-root "$LOCAL_PROJECT" PLAN.md > /dev/null
if [[ ! -f "$LOCAL_PROJECT/.harness/PLAN.md" ]]; then
  echo "FAIL: --local-state write-state did not land repo-local at .harness/PLAN.md" >&2
  exit 1
fi
GOT="$(env -u MEMORY_VAULT_PATH AGENTM_INSTALL_PREFIX="$LOCAL_PREFIX" \
  python3 "$HARNESS_ROOT/scripts/harness_memory.py" read-state \
  --project-root "$LOCAL_PROJECT" PLAN.md)"
if [[ "$GOT" != "# smoke PLAN" ]]; then
  echo "FAIL: --local-state read-state round-trip mismatch: got '$GOT'" >&2
  exit 1
fi
echo "    repo-local write/read round-trip OK"

# ── retired flags fail loudly rather than being ignored ─────────────────────
echo "==> retired flags are rejected"
for flag in --scope --update --hooks; do
  if HOME="$FAKE_HOME" CI=true AGENTM_INSTALL_PREFIX="$PREFIX" \
       bash "$HARNESS_ROOT/install.sh" "$flag" >/dev/null 2>&1; then
    echo "FAIL: $flag was accepted — retired flags must fail, not no-op" >&2
    exit 1
  fi
done
if HOME="$FAKE_HOME" CI=true AGENTM_INSTALL_PREFIX="$PREFIX" \
     bash "$HARNESS_ROOT/install.sh" "$SCRATCH/some-project" >/dev/null 2>&1; then
  echo "FAIL: a positional target was accepted — there is no target any more" >&2
  exit 1
fi
echo "    --scope / --update / --hooks / positional target all rejected"

echo "==> smoke-install-bash: OK"
