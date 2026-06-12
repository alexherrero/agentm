#!/usr/bin/env bash
# test_migrate_fixture.sh — V4 #30 plan 3 task 6 mid-build smoke.
#
# Builds a deterministic /tmp/fake-home fixture simulating a pre-V4.3
# per-project install (.claude/{skills,agents,commands,hooks}/ populated
# from byte-identical copies of agentm + crickets source clones, plus
# one deliberately operator-edited file), then exercises the full
# migrate-to-user-scope.sh cycle:
#
#   1. preview (default)        — verify classification table
#   2. --apply                  — verify safe migrated; operator-edited skipped
#   3. --apply (idempotent)     — verify second run is no-op
#   4. --rollback               — verify safe restored; record gone
#   5. --apply --force          — verify operator-edited migrated with backup
#   6. --rollback               — verify everything restored
#   7. --apply --yes            — fresh migrate for cleanup test
#   8. --cleanup                — verify install subdirs removed
#
# Exit 0 on full pass; non-zero on any failure (with actionable message).
#
# Per V4 #30 plan 3 of 3 task 6. Mid-build dogfood is fixture-only per
# DC-8 (operator's 3 repos already migrated in plan 1 task 11).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

# Sandbox fixture under a temp dir we can blow away.
FIXTURE_ROOT="$(mktemp -d -t agentm-migrate-fixture-XXXXXX)"
trap 'rm -rf "$FIXTURE_ROOT"' EXIT

FAKE_TARGET="$FIXTURE_ROOT/fake-home/project"
mkdir -p "$FAKE_TARGET/.claude/skills"
mkdir -p "$FAKE_TARGET/.claude/hooks"
mkdir -p "$FAKE_TARGET/.claude/agents"
mkdir -p "$FAKE_TARGET/.claude/commands"

# ── locate source-clone fixtures ──────────────────────────────────────────
AGENTM="$REPO"
CRICKETS="$REPO/../crickets"
if [[ ! -d "$CRICKETS" ]]; then
    echo "FAIL: crickets sibling not found at $CRICKETS" >&2
    exit 1
fi
CRICKETS="$(cd "$CRICKETS" && pwd)"

# ── populate fixture from source clones (byte-identical copies) ───────────
echo "==> building fixture under $FAKE_TARGET"

# Pick 2 agentm agents + 1 command + 1 skill bundle. The agents are the
# surviving memory-engine agents (adapt-evaluator / memory-idea-researcher) —
# the V5 dev-loop slim removed the review agents, so harness/agents/ now holds
# only the memory-engine pair, which is the stable fixture source here.
# IMPORTANT: agents are sourced from harness/agents/ (the FIRST inverse-mapping
# match in install_symlinks.symlink_targets_for_clone — see DC-4 tie-break
# "first clone iterated wins").
AGENT_FILES=(
    "$AGENTM/harness/agents/memory-idea-researcher.md"
    "$AGENTM/harness/agents/adapt-evaluator.md"
)
COMMAND_FILES=(
    "$AGENTM/adapters/claude-code/commands/recent-wiki-changes.md"
)
SKILL_DIRS=(
    # Pick one whose source is in agentm; one from crickets.
)
# Find one agentm skill dir + one crickets skill dir for the fixture.
agentm_skill="$(find "$AGENTM/harness/skills" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | head -1)"
crickets_skill="$(find "$CRICKETS/skills" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | head -1)"
if [[ -z "$agentm_skill" || -z "$crickets_skill" ]]; then
    echo "FAIL: could not locate skill dirs in source clones" >&2
    exit 1
fi
SKILL_DIRS=("$agentm_skill" "$crickets_skill")
# Find one crickets hook dir.
crickets_hook="$(find "$CRICKETS/hooks" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | head -1)"

for f in "${AGENT_FILES[@]}"; do
    [[ -f "$f" ]] && cp "$f" "$FAKE_TARGET/.claude/agents/$(basename "$f")"
done
for f in "${COMMAND_FILES[@]}"; do
    [[ -f "$f" ]] && cp "$f" "$FAKE_TARGET/.claude/commands/$(basename "$f")"
done
for d in "${SKILL_DIRS[@]}"; do
    [[ -d "$d" ]] && cp -R "$d" "$FAKE_TARGET/.claude/skills/$(basename "$d")"
done
if [[ -n "$crickets_hook" && -d "$crickets_hook" ]]; then
    cp -R "$crickets_hook" "$FAKE_TARGET/.claude/hooks/$(basename "$crickets_hook")"
fi

# Add one deliberately-edited file (NEW; SHA differs from source).
edited_target="$FAKE_TARGET/.claude/agents/memory-idea-researcher.md"
if [[ -f "$edited_target" ]]; then
    echo "" >> "$edited_target"
    echo "<!-- operator-edited line for fixture-test -->" >> "$edited_target"
fi

# Add one operator-only file (no source mapping) → UNRECOGNIZED expected.
cat > "$FAKE_TARGET/.claude/agents/my-custom-agent.md" <<'EOF'
# My custom agent
operator's own thing; no source-clone mapping.
EOF

echo "==> fixture populated:"
find "$FAKE_TARGET/.claude" -mindepth 1 -maxdepth 2 | sort

# ── helper: run migrate-to-user-scope.sh against the fixture ──────────────
SCRIPT="$REPO/scripts/migrate-to-user-scope.sh"
run_script() {
    # Disable register (no vault for fixture) + always pass --yes.
    bash "$SCRIPT" --no-register --yes --ci-override "$@" "$FAKE_TARGET"
}

# ── helper: assert a file/dir exists / doesn't exist ──────────────────────
assert_exists() {
    local what="$1" path="$2"
    if [[ -e "$path" || -L "$path" ]]; then
        echo "  OK: $what exists: $path"
    else
        echo "  FAIL: $what missing: $path" >&2
        exit 2
    fi
}
assert_absent() {
    local what="$1" path="$2"
    if [[ -e "$path" || -L "$path" ]]; then
        echo "  FAIL: $what still present: $path" >&2
        exit 2
    fi
    echo "  OK: $what absent: $path"
}

# ── step 1: preview ───────────────────────────────────────────────────────
echo ""
echo "==> step 1: preview (default)"
preview_out=$(run_script 2>&1)
echo "$preview_out" | head -20
echo "  ..."
echo "$preview_out" | grep -E 'safe_to_migrate|operator_edited|unrecognized|already_symlinked' | wc -l | xargs -I{} echo "  classification lines: {}"
if ! echo "$preview_out" | grep -q "safe_to_migrate"; then
    echo "  FAIL: preview should report safe_to_migrate entries" >&2
    exit 2
fi
if ! echo "$preview_out" | grep -q "operator_edited"; then
    echo "  FAIL: preview should report operator_edited entries" >&2
    exit 2
fi
if ! echo "$preview_out" | grep -q "unrecognized"; then
    echo "  FAIL: preview should report unrecognized entries" >&2
    exit 2
fi
assert_exists "memory-idea-researcher.md (still present in preview)" "$FAKE_TARGET/.claude/agents/memory-idea-researcher.md"
assert_absent "migrate-record (preview shouldn't write it)" "$FAKE_TARGET/.agentm-migrate-record.json"
echo "  PASS: step 1"

# ── step 2: apply ─────────────────────────────────────────────────────────
echo ""
echo "==> step 2: apply"
apply_out=$(run_script --apply 2>&1)
echo "$apply_out" | tail -8
# Safe-to-migrate files should be gone
assert_absent "adapt-evaluator.md (was safe_to_migrate)" "$FAKE_TARGET/.claude/agents/adapt-evaluator.md"
# Operator-edited should still be there (skipped without --force)
assert_exists "memory-idea-researcher.md (operator-edited; skipped)" "$FAKE_TARGET/.claude/agents/memory-idea-researcher.md"
# Unrecognized should still be there
assert_exists "my-custom-agent.md (unrecognized; left alone)" "$FAKE_TARGET/.claude/agents/my-custom-agent.md"
# Record file written
assert_exists "migrate record" "$FAKE_TARGET/.agentm-migrate-record.json"
echo "  PASS: step 2"

# ── step 3: re-apply (idempotent) ─────────────────────────────────────────
echo ""
echo "==> step 3: re-apply (idempotent)"
record_before=$(cat "$FAKE_TARGET/.agentm-migrate-record.json")
run_script --apply > /dev/null 2>&1 || true
record_after=$(cat "$FAKE_TARGET/.agentm-migrate-record.json")
# Action counts should not double — actions list should be the same (merge-by-key dedup)
n_before=$(python3 -c "import json; print(len(json.loads(\"\"\"$record_before\"\"\").get('actions', [])))" 2>/dev/null || echo 0)
n_after=$(python3 -c "import json; print(len(json.loads(\"\"\"$record_after\"\"\").get('actions', [])))" 2>/dev/null || echo 0)
if [[ "$n_before" != "$n_after" ]]; then
    echo "  FAIL: idempotency broken — actions went from $n_before to $n_after" >&2
    exit 2
fi
echo "  OK: idempotent re-apply (actions stable at $n_after)"
echo "  PASS: step 3"

# ── step 4: rollback ──────────────────────────────────────────────────────
echo ""
echo "==> step 4: rollback"
run_script --rollback > /dev/null 2>&1
# Safe-to-migrate files restored
assert_exists "adapt-evaluator.md (restored)" "$FAKE_TARGET/.claude/agents/adapt-evaluator.md"
# Record gone
assert_absent "migrate record (gone post-rollback)" "$FAKE_TARGET/.agentm-migrate-record.json"
# Operator content still there (was never moved)
assert_exists "memory-idea-researcher.md (still operator-edited)" "$FAKE_TARGET/.claude/agents/memory-idea-researcher.md"
assert_exists "my-custom-agent.md (still operator's own)" "$FAKE_TARGET/.claude/agents/my-custom-agent.md"
echo "  PASS: step 4"

# ── step 5: apply --force ─────────────────────────────────────────────────
echo ""
echo "==> step 5: apply --force (migrates operator-edited too)"
force_out=$(run_script --apply --force 2>&1)
echo "$force_out" | tail -6
# Operator-edited file should now be gone (migrated with backup)
assert_absent "memory-idea-researcher.md (force-migrated)" "$FAKE_TARGET/.claude/agents/memory-idea-researcher.md"
# Backup exists
assert_exists "backup of memory-idea-researcher.md" "$FAKE_TARGET/.agentm-migrate-backup/agents/memory-idea-researcher.md"
echo "  PASS: step 5"

# ── step 6: rollback (restores from backup) ──────────────────────────────
echo ""
echo "==> step 6: rollback (restores from backup)"
run_script --rollback > /dev/null 2>&1
assert_exists "memory-idea-researcher.md (restored from backup with operator edits)" "$FAKE_TARGET/.claude/agents/memory-idea-researcher.md"
# Verify content has the operator-edited line
if ! grep -q "operator-edited line for fixture-test" "$FAKE_TARGET/.claude/agents/memory-idea-researcher.md"; then
    echo "  FAIL: restored memory-idea-researcher.md missing the operator-edit marker" >&2
    exit 2
fi
assert_absent "backup dir (gone after full rollback)" "$FAKE_TARGET/.agentm-migrate-backup"
echo "  PASS: step 6"

# ── step 7: fresh apply (without --force) for cleanup test ────────────────
echo ""
echo "==> step 7: fresh apply (without --force) for cleanup test"
# Remove operator-edited + unrecognized files so cleanup verification passes.
rm "$FAKE_TARGET/.claude/agents/memory-idea-researcher.md"
rm "$FAKE_TARGET/.claude/agents/my-custom-agent.md"
run_script --apply > /dev/null 2>&1
assert_exists "migrate record" "$FAKE_TARGET/.agentm-migrate-record.json"
echo "  PASS: step 7"

# ── step 8: cleanup ───────────────────────────────────────────────────────
echo ""
echo "==> step 8: cleanup"
cleanup_out=$(run_script --cleanup 2>&1)
echo "$cleanup_out" | tail -5
# All install subdirs should be gone
for sub in skills hooks agents commands; do
    assert_absent ".claude/$sub" "$FAKE_TARGET/.claude/$sub"
done
echo "  PASS: step 8"

echo ""
echo "============================================================"
echo "ALL 8 FIXTURE STEPS PASSED — migrate-to-user-scope.sh works"
echo "============================================================"
exit 0
