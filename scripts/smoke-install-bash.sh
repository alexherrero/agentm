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
  .claude/agents/documenter.md
  .claude/skills/dependabot-fixer/SKILL.md
  .claude/settings.json
  .agent/rules/harness.md
  .agent/workflows/plan.md
  .agent/skills/dependabot-fixer/SKILL.md
  .agents/skills/harness-plan/SKILL.md
  .agents/skills/dependabot-fixer/SKILL.md
  .codex/agents/explorer.toml
  .codex/agents/documenter.toml
  .gemini/commands/plan.toml
  .gemini/agents/explorer.md
  .gemini/settings.json
  wiki/Home.md
  wiki/README.md
  wiki/_Sidebar.md
  wiki/.diataxis
  wiki/tutorials/01-Getting-Started.md
  wiki/how-to/First-How-To.md
  wiki/reference/First-Reference.md
  wiki/explanation/First-Explanation.md
  wiki/explanation/decisions/README.md
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

echo "==> smoke-install-bash: OK"
