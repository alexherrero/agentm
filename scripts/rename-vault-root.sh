#!/usr/bin/env bash
# rename-vault-root.sh — V5-3 vault-root rename: AgentMemory/ → Agent/
#
# TWO-PHASE SCRIPT — the live folder rename is ALWAYS OPERATOR-PERFORMED.
#
# Phase A (default): backup + print the operator runbook, then EXIT.
#   Run this first. The script creates a full backup and tells you exactly
#   what to do next. It does NOT rename the folder.
#
# Phase B (--apply): apply the Face-① string sweep and update external refs
#   on the already-renamed vault. Run AFTER the operator has:
#     1. Closed Obsidian
#     2. Backed up (done by Phase A)
#     3. mv AgentMemory Agent
#     4. Re-added the vault in Obsidian (or re-pointed the GDrive shortcut)
#     5. Verified the link graph + confirmed Obsidian opens cleanly
#
# Usage:
#   bash agentm/scripts/rename-vault-root.sh [OPTIONS]
#
# Options:
#   --vault-path <path>   Vault root (default: ~/.agentm-config.json vault_path).
#   --new-vault-path <p>  Path to the RENAMED vault (for --apply; default: sibling Agent/).
#   --dry-run             Preview only; no file changes (also the default for Phase A).
#   --apply               Phase B: apply the string sweep + update external refs.
#   --help, -h            Print this help and exit.
#
# V5-3 locked calls encoded here:
#   [LC-4] The vault-root rename gets the strongest reversibility + a hands-on
#           Obsidian/GDrive runbook. The operator does the folder rename.
#   [LC-7] Renames run under the still-present built-in backend (Phase A runs
#           BEFORE the folder move; Phase B runs after, with the built-in intact).

set -euo pipefail

VAULT_PATH="${MEMORY_VAULT_PATH:-}"
NEW_VAULT_PATH=""
DRY_RUN=0
APPLY_MODE=0
_MARKER_FILENAME=".rename-vault-root-complete"

print_help() {
    sed -n '/^# rename-vault-root.sh/,/^[^#]/p' "$0" | sed 's|^# \?||' | sed '$d'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --vault-path)
            VAULT_PATH="${2:-}"; [[ -z "$VAULT_PATH" ]] && { echo "--vault-path requires a value" >&2; exit 2; }
            shift 2 ;;
        --new-vault-path)
            NEW_VAULT_PATH="${2:-}"; [[ -z "$NEW_VAULT_PATH" ]] && { echo "--new-vault-path requires a value" >&2; exit 2; }
            shift 2 ;;
        --dry-run)
            DRY_RUN=1; shift ;;
        --apply)
            APPLY_MODE=1; shift ;;
        --help|-h)
            print_help; exit 0 ;;
        *)
            echo "Unknown option: $1" >&2; print_help >&2; exit 2 ;;
    esac
done

# ── resolve the OLD vault path (AgentMemory/) ─────────────────────────────
_SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "$VAULT_PATH" ]]; then
    VAULT_PATH="$(python3 "$_SCRIPTS_DIR/agentm_config.py" --get vault_path 2>/dev/null || true)"
fi
if [[ -z "$VAULT_PATH" ]]; then
    echo "Error: vault path not configured." >&2
    echo "  Set MEMORY_VAULT_PATH env, or configure vault_path in ~/.claude/.agentm-config.json" >&2
    exit 1
fi
VAULT_PATH="$(python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$VAULT_PATH" 2>/dev/null || echo "$VAULT_PATH")"

# Derive the expected new vault path (sibling Agent/).
VAULT_PARENT="$(dirname "$VAULT_PATH")"
VAULT_NAME="$(basename "$VAULT_PATH")"
if [[ -z "$NEW_VAULT_PATH" ]]; then
    NEW_VAULT_PATH="$VAULT_PARENT/Agent"
fi

# ── portable in-place substitution ───────────────────────────────────────
_inplace_sub() {
    local file="$1" pattern="$2" replacement="$3"
    python3 - "$file" "$pattern" "$replacement" <<'PY'
import sys, pathlib
path, pat, repl = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
text = path.read_text(encoding="utf-8")
new = text.replace(pat, repl)
if new != text:
    path.write_text(new, encoding="utf-8")
    print(f"    rewrote: {path}")
PY
}

# ── sweep targets ─────────────────────────────────────────────────────────
# Face-①: the ~113–144 AgentMemory/ zone/path references in live (non-archive) files.
# Face-②: "Agent M" brand references — NOT swept here (different string, no slash).
# _archive/ and PLAN.archive.*.md are excluded (historical record preserved).
sweep_targets() {
    local vault="$1"
    # Walk all .md files under vault, excluding _archive/ and PLAN archives.
    find "$vault" -type f -name '*.md' \
        -not -path '*/_archive/*' \
        -not -name 'PLAN.archive.*.md' \
        2>/dev/null
}

count_face1_hits() {
    local vault="$1"
    local count=0
    while IFS= read -r f; do
        [[ -z "$f" ]] && continue
        if grep -q "AgentMemory/" "$f" 2>/dev/null; then
            count=$((count + 1))
        fi
    done < <(sweep_targets "$vault" | sort -u)
    echo "$count"
}

# ══════════════════════════════════════════════════════════════════════════
# PHASE A — backup + runbook
# ══════════════════════════════════════════════════════════════════════════
if [[ $APPLY_MODE -eq 0 ]]; then

    # If the old vault is already gone and the new one exists, there's nothing
    # to do in Phase A (operator already renamed).
    if [[ ! -d "$VAULT_PATH" ]] && [[ -d "$NEW_VAULT_PATH" ]]; then
        echo "==> Vault already renamed (Agent/ exists, AgentMemory/ absent)."
        echo "    If you haven't run Phase B yet, run:"
        echo "      bash scripts/rename-vault-root.sh --apply --new-vault-path \"$NEW_VAULT_PATH\""
        exit 0
    fi

    if [[ ! -d "$VAULT_PATH" ]]; then
        echo "Error: vault not found at $VAULT_PATH" >&2
        exit 1
    fi

    echo "==> rename-vault-root.sh PHASE A — backup + runbook"
    echo "    vault (current): $VAULT_PATH"
    echo "    vault (after rename): $NEW_VAULT_PATH"
    echo ""

    # Dry-run scan: show Face-① scope.
    echo "--- Face-① scan (AgentMemory/ zone refs in live non-archive .md files) ---"
    face1_count=0
    while IFS= read -r f; do
        [[ -z "$f" ]] && continue
        if grep -q "AgentMemory/" "$f" 2>/dev/null; then
            echo "  $f"
            face1_count=$((face1_count + 1))
        fi
    done < <(sweep_targets "$VAULT_PATH" | sort -u)
    echo "  → $face1_count file(s) contain 'AgentMemory/' and will be swept in Phase B."
    echo ""

    # Show external refs that need updating.
    echo "--- External refs to update (Phase B will apply these) ---"
    AGENTM_CONFIG="$HOME/.claude/.agentm-config.json"
    if [[ -f "$AGENTM_CONFIG" ]]; then
        echo "  ~/.claude/.agentm-config.json  (vault_path: AgentMemory → Agent)"
    fi
    echo ""

    if [[ $DRY_RUN -eq 1 ]]; then
        echo "==> [DRY-RUN — no changes made]"
        exit 0
    fi

    # Create full backup.
    BACKUP_PATH="$VAULT_PARENT/${VAULT_NAME}.pre-rename-backup"
    if [[ -d "$BACKUP_PATH" ]]; then
        echo "==> Backup already exists at $BACKUP_PATH — skipping backup step."
        echo "    (Delete the backup if you want to re-run Phase A with a fresh backup.)"
    else
        echo "==> Creating full backup at $BACKUP_PATH"
        cp -r "$VAULT_PATH" "$BACKUP_PATH"
        echo "    Backup complete: $BACKUP_PATH"
    fi
    echo ""

    # Print the operator runbook and exit.
    cat <<RUNBOOK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HARD STOP — OPERATOR PERFORMS THE FOLDER RENAME
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Backup created at:
  $BACKUP_PATH

Now you must perform the live vault rename manually:

1. CLOSE OBSIDIAN completely (not just hide — quit the app).

2. Rename the folder:
   cd "$VAULT_PARENT"
   mv AgentMemory Agent

3. Re-add the vault in Obsidian:
   Option A (preferred): Open Obsidian → vault switcher → "Open folder as vault" → select Agent/
   Option B: Re-point the Google Drive shortcut in .shortcut-targets-by-id/ to Agent/
             (then reopen Obsidian — it should find the vault at the new path)

4. Let Obsidian re-open and verify:
   - The vault opens cleanly (no "vault not found" errors)
   - Spot-check a few wikilinks — they should resolve (Obsidian's link graph
     is basename-relative and rides along free)
   - The graph view shows your usual note structure

5. Verify the external refs:
   - The agentmemory MCP server: it points to the Obsidian/ parent dir, not
     AgentMemory/ directly, so it should resolve automatically.
   - The session-start hook reads vault_path from ~/.claude/.agentm-config.json
     — Phase B updates this.

6. Then run Phase B to apply the Face-① string sweep + update configs:
   bash scripts/rename-vault-root.sh --apply --new-vault-path "$NEW_VAULT_PATH"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Rollback (if anything goes wrong before Phase B):
  rm -rf "$NEW_VAULT_PATH"
  cp -r "$BACKUP_PATH" "$VAULT_PATH"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RUNBOOK
    exit 0
fi

# ══════════════════════════════════════════════════════════════════════════
# PHASE B — apply string sweep + update external refs
# ══════════════════════════════════════════════════════════════════════════
echo "==> rename-vault-root.sh PHASE B — string sweep + external refs"
echo "    renamed vault: $NEW_VAULT_PATH"
echo ""

# Validate the renamed vault exists.
if [[ ! -d "$NEW_VAULT_PATH" ]]; then
    echo "Error: renamed vault not found at $NEW_VAULT_PATH" >&2
    echo "  Have you run Phase A and performed the folder rename?" >&2
    exit 1
fi

# Idempotency: if the marker exists, we already ran Phase B.
MARKER="$NEW_VAULT_PATH/$_MARKER_FILENAME"
if [[ -f "$MARKER" ]]; then
    echo "==> Phase B already complete (marker $MARKER_FILENAME found). Nothing to do."
    exit 0
fi

if [[ $DRY_RUN -eq 1 ]]; then
    echo "==> [DRY-RUN — showing what Phase B would do]"
    echo ""
    echo "--- Face-① sweep (AgentMemory/ → Agent/ in non-archive .md files) ---"
    face1_count=0
    while IFS= read -r f; do
        [[ -z "$f" ]] && continue
        if grep -q "AgentMemory/" "$f" 2>/dev/null; then
            echo "  WOULD sweep: $f"
            face1_count=$((face1_count + 1))
        fi
    done < <(sweep_targets "$NEW_VAULT_PATH" | sort -u)
    echo "  → $face1_count file(s) would be swept."
    echo ""
    echo "--- External refs ---"
    echo "  WOULD update: ~/.claude/.agentm-config.json (vault_path: AgentMemory → Agent)"
    exit 0
fi

# ── step 1: Face-① string sweep (AgentMemory/ → Agent/) ──────────────────
echo "==> Step 1: Face-① string sweep (AgentMemory/ → Agent/)"
swept=0
checked=0
while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    checked=$((checked + 1))
    if grep -q "AgentMemory/" "$f" 2>/dev/null; then
        _inplace_sub "$f" "AgentMemory/" "Agent/"
        swept=$((swept + 1))
    fi
done < <(sweep_targets "$NEW_VAULT_PATH" | sort -u)
echo "    swept $swept / $checked file(s)."
echo ""

# ── step 2: update ~/.claude/.agentm-config.json vault_path ──────────────
echo "==> Step 2: update ~/.claude/.agentm-config.json"
AGENTM_CONFIG="$HOME/.claude/.agentm-config.json"
if [[ -f "$AGENTM_CONFIG" ]]; then
    python3 - "$AGENTM_CONFIG" "$VAULT_PATH" "$NEW_VAULT_PATH" <<'PY'
import sys, json, pathlib
config_path = pathlib.Path(sys.argv[1])
old_vault = sys.argv[2]
new_vault = sys.argv[3]
d = json.loads(config_path.read_text())
if d.get("vault_path") == old_vault:
    d["vault_path"] = new_vault
    config_path.write_text(json.dumps(d, indent=2) + "\n")
    print(f"    updated vault_path: {old_vault} → {new_vault}")
elif d.get("vault_path") == new_vault:
    print(f"    vault_path already points to renamed vault: {new_vault}")
else:
    print(f"    WARN: vault_path in config ({d.get('vault_path')}) does not match expected old path ({old_vault})")
    print(f"    Manual update may be needed.")
PY
else
    echo "    ~/.claude/.agentm-config.json not found — skipping."
fi
echo ""

# ── step 3: post-run residual check ──────────────────────────────────────
echo "==> Step 3: residual AgentMemory/ check (non-archive .md files)"
residual=$(grep -rn "AgentMemory/" "$NEW_VAULT_PATH" --include='*.md' 2>/dev/null \
    | grep -vE '(_archive/|PLAN\.archive|pre-rename-backup)' | wc -l | tr -d ' ')
if [[ "$residual" -gt 0 ]]; then
    echo "  WARN: $residual residual 'AgentMemory/' reference(s) remain (review manually):" >&2
    grep -rn "AgentMemory/" "$NEW_VAULT_PATH" --include='*.md' 2>/dev/null \
        | grep -vE '(_archive/|PLAN\.archive|pre-rename-backup)' | head -10 >&2
    echo "  (These may be intentional prose or unconventional path forms — inspect them.)" >&2
else
    echo "    clean — no 'AgentMemory/' references outside _archive/."
fi
echo ""

# ── step 4: write idempotency marker ─────────────────────────────────────
date -u +"%Y-%m-%dT%H:%M:%SZ" > "$MARKER"
echo "==> Phase B complete. Marker written at $MARKER"
echo ""
echo "Next steps:"
echo "  1. Verify Obsidian opens the renamed vault cleanly and the link graph is intact."
echo "  2. Re-run the parallel-run conformance suite (V5-2) against the renamed vault:"
echo "     python3 scripts/storage_conformance_run.py --vault-path \"$NEW_VAULT_PATH\""
echo "  3. If everything is green, proceed to V5-3 Part 2 (the built-in backend delete)."
echo "  4. Keep the backup ($VAULT_PARENT/${VAULT_NAME}.pre-rename-backup) until V5-3 Part 2"
echo "     is fully verified and the post-soak cleanup is done."
