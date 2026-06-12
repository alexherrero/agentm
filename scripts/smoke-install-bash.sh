#!/usr/bin/env bash
# smoke-install-bash.sh — install the harness into a scratch dir and assert
# the expected file tree, then re-run for idempotence and --update semantics.
#
# Used by tests-linux.yml and tests-mac.yml. Invoked from repo root:
#   bash scripts/smoke-install-bash.sh
#
# Exits non-zero on first failed assertion with a diagnostic.

set -euo pipefail

HARNESS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT

echo "==> fresh install into $SCRATCH"
bash "$HARNESS_ROOT/install.sh" --hooks "$SCRATCH" > "$SCRATCH/.install.log"

# ── expected files (at least one per adapter + hooks + wiki) ────────────────
expected=(
  .harness/PLAN.md
  .harness/features.json
  .harness/progress.md
  .harness/init.sh
  .harness/known-migrations.md
  .harness/.version
  .harness/scripts/cross-review.sh
  .harness/scripts/cross-review.ps1
  .harness/scripts/telemetry.sh
  .harness/verify.sh
  .harness/verify.ps1
  .harness/hooks/precompact.sh
  .harness/hooks/precompact.ps1
  .harness/hooks/session-start-compact.sh
  .harness/hooks/session-start-compact.ps1
  .claude/commands/plan.md
  .claude/commands/work.md
  .claude/agents/explorer.md
  .claude/skills/doctor/SKILL.md
  .claude/settings.json
  .agents/rules/harness.md
  .agents/workflows/plan.md
  .agents/skills/doctor/SKILL.md
  .gemini/commands/plan.toml
  .gemini/agents/explorer.md
  .gemini/settings.json
  wiki/Home.md
  wiki/README.md
  wiki/_Sidebar.md
  wiki/.diataxis
  wiki/how-to/01-Getting-Started.md
  wiki/how-to/First-How-To.md
  wiki/reference/First-Reference.md
  wiki/explanation/First-Explanation.md
  wiki/decisions/README.md
  wiki/designs/README.md
  AGENTS.md
  CLAUDE.md
  .github/workflows/wiki-sync.yml
)

fail=0
for p in "${expected[@]}"; do
  if [[ ! -e "$SCRATCH/$p" ]]; then
    echo "MISSING: $p" >&2
    fail=1
  fi
done

# ── installer boundary: tests-*.yml and this repo's own wiki/ must NOT leak ─
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
)
for p in "${leaks[@]}"; do
  if [[ -e "$SCRATCH/$p" ]]; then
    echo "LEAK: $p should not be in scratch install (installer boundary)" >&2
    fail=1
  fi
done

# ── settings.json: valid JSON, all hook events stored as arrays ─────────────
python3 - "$SCRATCH/.claude/settings.json" <<'PY' || fail=1
import json, sys
path = sys.argv[1]
s = json.load(open(path))
assert 'hooks' in s, 'hooks key missing'
for k, v in s['hooks'].items():
    assert isinstance(v, list), f'{k} is not array (got {type(v).__name__})'
    assert len(v) >= 1, f'{k} is empty'
    assert 'matcher' in v[0], f'{k}[0] missing matcher'
    assert 'hooks' in v[0] and isinstance(v[0]['hooks'], list), f'{k}[0].hooks missing/non-array'
print(f'    settings.json OK ({len(s["hooks"])} events)')
PY

if [[ $fail -ne 0 ]]; then
  echo "FAIL: expected-files or installer-boundary or settings.json assertions failed" >&2
  exit 1
fi

# ── idempotent re-run: no "created" for previously-created files ────────────
echo "==> idempotent re-run"
bash "$HARNESS_ROOT/install.sh" --hooks "$SCRATCH" > "$SCRATCH/.rerun.log"
if grep -q "created .claude/settings.json with harness hooks" "$SCRATCH/.rerun.log"; then
  echo "FAIL: re-run recreated settings.json (should be idempotent)" >&2
  exit 1
fi
if grep -q "created .claude/commands/plan.md" "$SCRATCH/.rerun.log"; then
  echo "FAIL: re-run recreated managed file (should be kept)" >&2
  exit 1
fi

# ── --update preserves user edits to user-owned files ─────────────────────
echo "==> --update preserves user edits (cp_user semantics)"
# wiki/Home.md is cp_user — an edit must survive --update.
USER_MARK="# USER-EDIT-MARKER-$(date +%s%N)"
echo "$USER_MARK" >> "$SCRATCH/wiki/Home.md"
# AGENTS.md is cp_user — same deal.
USER_MARK2="# USER-AGENTS-MARKER-$(date +%s%N)"
echo "$USER_MARK2" >> "$SCRATCH/AGENTS.md"

bash "$HARNESS_ROOT/install.sh" --update --hooks "$SCRATCH" > "$SCRATCH/.update.log"

if ! grep -qF "$USER_MARK" "$SCRATCH/wiki/Home.md"; then
  echo "FAIL: --update clobbered user edit in wiki/Home.md" >&2
  exit 1
fi
if ! grep -qF "$USER_MARK2" "$SCRATCH/AGENTS.md"; then
  echo "FAIL: --update clobbered user edit in AGENTS.md" >&2
  exit 1
fi

# ── --update: managed files refresh semantics ───────────────────────────────
# after --update, managed files should show "up to date" or "updated"
if ! grep -qE "(up to date|updated)" "$SCRATCH/.update.log"; then
  echo "FAIL: --update produced no 'up to date' / 'updated' markers" >&2
  exit 1
fi

# ── Post-install integrity check ────────────────────────────────────────────
echo "==> post-install integrity"
bash "$HARNESS_ROOT/scripts/check-integrity-bash.sh" "$SCRATCH"

# ── --local-state: first-class repo-local (vault-less) mode (Hardening I #44 task 4) ──
# Proves the entry point end-to-end: the flag writes state_mode:local to
# .agentm-config.json (the on-host config; DC-8), and a subsequent state write
# lands repo-local with NO vault configured.
echo "==> --local-state writes state_mode:local + state lands repo-local"
LOCAL_SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH" "$LOCAL_SCRATCH"' EXIT   # extend cleanup to the new scratch
bash "$HARNESS_ROOT/install.sh" --local-state "$LOCAL_SCRATCH" > "$LOCAL_SCRATCH/.install.log"

# 1. .agentm-config.json carries state_mode:local
python3 - "$LOCAL_SCRATCH/.claude/.agentm-config.json" <<'PY' || exit 1
import json, sys
c = json.load(open(sys.argv[1]))
assert c.get("state_mode") == "local", f"state_mode not 'local': {c.get('state_mode')!r}"
print("    state_mode:local OK")
PY

# 2. a subsequent write-state lands repo-local (no MEMORY_VAULT_PATH), reads back
printf '{"vault_project": "smokedemo"}\n' > "$LOCAL_SCRATCH/.harness/project.json"
echo "# smoke PLAN" | env -u MEMORY_VAULT_PATH AGENTM_INSTALL_PREFIX="$LOCAL_SCRATCH/.claude" \
  python3 "$HARNESS_ROOT/scripts/harness_memory.py" write-state \
  --project-root "$LOCAL_SCRATCH" PLAN.md > /dev/null
if [[ ! -f "$LOCAL_SCRATCH/.harness/PLAN.md" ]]; then
  echo "FAIL: --local-state write-state did not land repo-local at .harness/PLAN.md" >&2
  exit 1
fi
GOT="$(env -u MEMORY_VAULT_PATH AGENTM_INSTALL_PREFIX="$LOCAL_SCRATCH/.claude" \
  python3 "$HARNESS_ROOT/scripts/harness_memory.py" read-state \
  --project-root "$LOCAL_SCRATCH" PLAN.md)"
if [[ "$GOT" != "# smoke PLAN" ]]; then
  echo "FAIL: --local-state read-state round-trip mismatch: got '$GOT'" >&2
  exit 1
fi
echo "    repo-local write/read round-trip OK"

echo "==> smoke-install-bash: OK"
