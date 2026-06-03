#!/usr/bin/env bash
# migrate-harness-to-vault.sh — V4 #26 per-project state migration.
#
# Copies <target>/.harness/<file> → <vault>/projects/<slug>/_harness/<file>
# for the locked file set. Idempotent (safe to re-run) + reversible
# (--rollback writes the repo-local .project-mode=local marker so the dispatcher
# reads from the legacy path again). The mode marker is always repo-local
# (on-host) — config never lives in the vault (DC-8). Leaves legacy files in
# place by default; --cleanup removes them after byte-identical verification.
#
# Usage:
#   bash agentm/scripts/migrate-harness-to-vault.sh [OPTIONS] [TARGET]
#
# Options:
#   --vault-path <path>   Override vault root. Default: $MEMORY_VAULT_PATH env.
#   --preview             Dry-run: print what would change without modifying.
#   --cleanup             After migration, delete <target>/.harness/<file> for
#                         each successfully-migrated file (preserves
#                         .evidence-reads per DC-1 — runtime ephemeral).
#                         Asks for operator confirmation per file unless
#                         --yes is also passed.
#   --rollback            Write the repo-local .project-mode=local marker
#                         (<target>/.harness/.project-mode) so the dispatcher
#                         reads from legacy <target>/.harness/ again. Reversible
#                         escape hatch if vault-mode misbehaves.
#   --yes                 Skip confirmation prompts (use with --cleanup).
#   --help, -h            Print this help and exit.
#
# Positional argument:
#   TARGET                Project path (default: $PWD). Project slug resolved via
#                         scripts/vault_project.py read $TARGET.
#
# Per plan #20 task 6 / plan #18 design `05-state-migration.md` § (b).

set -euo pipefail

# ── argument parsing ──────────────────────────────────────────────────────
VAULT_PATH="${MEMORY_VAULT_PATH:-}"
TARGET=""
PREVIEW=0
CLEANUP=0
ROLLBACK=0
ASSUME_YES=0

print_help() {
    sed -n '/^# migrate-harness-to-vault.sh/,/^[^#]/p' "$0" | sed 's|^# \?||' | sed '$d'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --vault-path)
            VAULT_PATH="${2:-}"
            [[ -z "$VAULT_PATH" ]] && { echo "--vault-path requires a value" >&2; exit 2; }
            shift 2
            ;;
        --preview) PREVIEW=1; shift ;;
        --cleanup) CLEANUP=1; shift ;;
        --rollback) ROLLBACK=1; shift ;;
        --yes|-y) ASSUME_YES=1; shift ;;
        --help|-h) print_help; exit 0 ;;
        --*)
            echo "Unknown option: $1" >&2
            echo "" >&2
            print_help >&2
            exit 2
            ;;
        *)
            if [[ -z "$TARGET" ]]; then
                TARGET="$1"; shift
            else
                echo "Unexpected positional argument: $1 (target already set to: $TARGET)" >&2
                exit 2
            fi
            ;;
    esac
done

# ── resolve target + vault + slug ─────────────────────────────────────────
TARGET="${TARGET:-$PWD}"
if [[ ! -d "$TARGET" ]]; then
    echo "Error: target is not a directory: $TARGET" >&2
    exit 1
fi
TARGET="$(cd "$TARGET" && pwd)"

# v4.5.1: resolution order: --vault-path CLI → $MEMORY_VAULT_PATH env →
# vault_path in .agentm-config.json.
if [[ -z "$VAULT_PATH" ]]; then
    VAULT_PATH="$(python3 "$(dirname "$0")/agentm_config.py" --get vault_path 2>/dev/null || true)"
fi
if [[ -z "$VAULT_PATH" ]]; then
    echo "Error: vault path not configured." >&2
    echo "  Resolution: \$MEMORY_VAULT_PATH env → vault_path in ~/.claude/.agentm-config.json → --vault-path CLI." >&2
    echo "  Set via: python3 \"\$(dirname \"\$0\")/agentm_config.py\" --vault-path <path>" >&2
    echo "       or: bash install.sh --scope user --force-vault-prompt" >&2
    exit 1
fi
if [[ ! -d "$VAULT_PATH" ]]; then
    echo "Error: vault path is not a directory: $VAULT_PATH" >&2
    exit 1
fi
VAULT_PATH="$(cd "$VAULT_PATH" && pwd)"

# Locate vault_project.py — assumed to live at <agentm>/scripts/.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VP_PY="$HERE/vault_project.py"
if [[ ! -f "$VP_PY" ]]; then
    echo "Error: vault_project.py not found at $VP_PY" >&2
    exit 1
fi

SLUG="$(python3 "$VP_PY" read "$TARGET" 2>/dev/null || true)"
if [[ -z "$SLUG" ]]; then
    echo "Error: could not resolve project slug from $TARGET." >&2
    echo "Tried: .harness/project.json vault_project field; .harness/project.json github.repo; git origin." >&2
    echo "Fix: add { \"vault_project\": \"<slug>\" } to $TARGET/.harness/project.json or set a git origin." >&2
    exit 1
fi

# Prefer post-V4 #26 layout; fall back to legacy.
if [[ -d "$VAULT_PATH/projects" ]]; then
    PROJECT_DIR="$VAULT_PATH/projects/$SLUG"
    PROJECTS_SEGMENT="projects"
elif [[ -d "$VAULT_PATH/personal-projects" ]]; then
    PROJECT_DIR="$VAULT_PATH/personal-projects/$SLUG"
    PROJECTS_SEGMENT="personal-projects"
else
    # Empty vault — assume post-rename layout.
    PROJECT_DIR="$VAULT_PATH/projects/$SLUG"
    PROJECTS_SEGMENT="projects"
fi
HARNESS_DIR="$PROJECT_DIR/_harness"
MARKER="$HARNESS_DIR/.migrated-from-pre-v4.1"
LEGACY_HARNESS="$TARGET/.harness"
# The .project-mode marker is always repo-local (on-host) — config never lives
# in the vault (DC-8). Both rollback (→local) and forward-migrate (→vault) write
# here so the dispatcher's repo-local resolution layer sees it without a vault.
MODE_FILE="$LEGACY_HARNESS/.project-mode"

# ── handle --rollback first (mutually exclusive with migrate) ─────────────
if [[ $ROLLBACK -eq 1 ]]; then
    echo "==> rollback: setting .project-mode=local for project '$SLUG'"
    echo "    project root: $TARGET"
    echo "    vault path:   $PROJECT_DIR"
    if [[ ! -d "$HARNESS_DIR" ]]; then
        echo "  WARN: no vault-side _harness/ exists yet — nothing to roll back." >&2
        echo "  (Legacy <target>/.harness/ remains canonical without action.)" >&2
        exit 0
    fi
    if [[ $PREVIEW -eq 1 ]]; then
        echo "  WOULD: echo 'local' > $MODE_FILE"
        exit 0
    fi
    mkdir -p "$LEGACY_HARNESS"
    echo "local" > "$MODE_FILE"
    echo "  set repo-local .project-mode=local. Dispatcher will now read from $LEGACY_HARNESS/"
    echo "  Re-run without --rollback to flip back to vault mode."
    exit 0
fi

# ── banner ────────────────────────────────────────────────────────────────
[[ $PREVIEW -eq 1 ]] && echo "==> [PREVIEW MODE — no changes will be made]"
echo "==> migrate-harness-to-vault"
echo "    target:       $TARGET"
echo "    vault:        $VAULT_PATH"
echo "    project slug: $SLUG"
echo "    vault dest:   $PROJECT_DIR/_harness/  (using '$PROJECTS_SEGMENT/' segment)"
echo ""

# ── idempotency check ─────────────────────────────────────────────────────
if [[ -f "$MARKER" ]]; then
    MARKER_TS="$(grep -E '^migrated_at:' "$MARKER" 2>/dev/null | head -1 | sed 's|^migrated_at: ||')"
    echo "==> already migrated on ${MARKER_TS:-(unknown date)} — nothing to do."
    if [[ $CLEANUP -eq 1 ]]; then
        echo "    (re-running with --cleanup; will offer to remove legacy paths.)"
    else
        exit 0
    fi
fi

# ── locked file mapping (per design 05-state-migration.md § "Per-file target mapping") ──
# Each entry: "<relative-path-under-.harness>" (file or directory).
# .evidence-reads STAYS per-cwd per DC-1; NOT migrated.
# project.json DEPRECATED post-V4 #26; copied with WARN if present.
declare -a STATE_FILES=(
    "PLAN.md"
    "progress.md"
    "ROADMAP.md"
    "ROADMAP-AgentMemoryV4.md"
    "ROADMAP-AgentMemoryV5.md"
    "ROADMAP-AgentMemoryV6.md"
    "FOLLOWUPS.md"
    "features.json"
    "init.sh"
    "known-migrations.md"
    "verify.sh"
    "verify.ps1"
    ".promoted-progress-cursor"
    "project.json"  # deprecated; copy + warn
)
# Directories migrate as whole subtrees.
declare -a STATE_DIRS=(
    "designs"
    "phases"
)

# ── migrate files ─────────────────────────────────────────────────────────
if [[ ! -d "$LEGACY_HARNESS" ]]; then
    echo "==> no legacy <target>/.harness/ found at $LEGACY_HARNESS"
    echo "    (Nothing to migrate. If this is a fresh project, run /setup first.)"
    exit 0
fi

if [[ $PREVIEW -eq 1 ]]; then
    echo "  WOULD: mkdir -p $HARNESS_DIR"
else
    mkdir -p "$HARNESS_DIR"
fi

migrated_count=0
skipped_count=0
conflict_count=0

migrate_file() {
    local rel="$1"
    local src="$LEGACY_HARNESS/$rel"
    local dst="$HARNESS_DIR/$rel"
    [[ ! -e "$src" ]] && return 0

    if [[ "$rel" == "project.json" ]]; then
        echo "    [DEPRECATED] copying $rel (V4 #26 replaces project.json with vault _index.md frontmatter)" >&2
    fi

    if [[ -e "$dst" ]]; then
        # Conflict — vault already has this file. Compare to decide.
        if cmp -s "$src" "$dst" 2>/dev/null; then
            skipped_count=$((skipped_count + 1))
            return 0
        else
            # Different content. Don't overwrite; warn.
            conflict_count=$((conflict_count + 1))
            echo "  CONFLICT: $rel differs between legacy + vault. NOT overwriting." >&2
            echo "    legacy: $src" >&2
            echo "    vault:  $dst" >&2
            echo "    Resolve manually (diff + merge), then re-run migrate to verify clean." >&2
            return 0
        fi
    fi

    if [[ $PREVIEW -eq 1 ]]; then
        echo "  WOULD: cp $src $dst"
        migrated_count=$((migrated_count + 1))
    else
        mkdir -p "$(dirname "$dst")"
        cp "$src" "$dst"
        echo "    migrated: $rel"
        migrated_count=$((migrated_count + 1))
    fi
}

migrate_dir() {
    local rel="$1"
    local src="$LEGACY_HARNESS/$rel"
    local dst="$HARNESS_DIR/$rel"
    [[ ! -d "$src" ]] && return 0

    if [[ -d "$dst" ]]; then
        # Conflict-free if every file inside also conflict-free.
        # Recurse via find + migrate_file for each.
        while IFS= read -r entry; do
            local relrel="${entry#$src/}"
            migrate_file "$rel/$relrel"
        done < <(find "$src" -type f)
        return 0
    fi

    if [[ $PREVIEW -eq 1 ]]; then
        echo "  WOULD: cp -R $src $dst"
        local n
        n=$(find "$src" -type f | wc -l | tr -d ' ')
        migrated_count=$((migrated_count + n))
    else
        cp -R "$src" "$dst"
        local n
        n=$(find "$src" -type f | wc -l | tr -d ' ')
        echo "    migrated: $rel/ ($n file(s))"
        migrated_count=$((migrated_count + n))
    fi
}

# ── migrate PLAN.archive.*.md (variadic; matches PLAN.archive.YYYYMMDD-<slug>.md) ──
echo "==> copying state files"
for f in "${STATE_FILES[@]}"; do
    migrate_file "$f"
done

# PLAN.archive files (any matching glob)
while IFS= read -r archive_file; do
    [[ -z "$archive_file" ]] && continue
    rel="$(basename "$archive_file")"
    migrate_file "$rel"
done < <(find "$LEGACY_HARNESS" -maxdepth 1 -name 'PLAN.archive.*.md' 2>/dev/null)

# ── migrate directories ───────────────────────────────────────────────────
for d in "${STATE_DIRS[@]}"; do
    migrate_dir "$d"
done

# ── write marker + project-mode flag ──────────────────────────────────────
if [[ $PREVIEW -eq 0 ]]; then
    {
        echo "# Migration marker — written by migrate-harness-to-vault.sh"
        echo "migrated_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "source: $LEGACY_HARNESS"
        echo "target: $HARNESS_DIR"
        echo "slug: $SLUG"
        echo "files_migrated: $migrated_count"
        echo "files_skipped_identical: $skipped_count"
        echo "files_in_conflict: $conflict_count"
        echo "v4_26_plan: agentm v4.1.0"
    } > "$MARKER"
    # Write the repo-local .project-mode=vault marker (DC-8) — an explicit
    # per-repo override pinning this migrated repo to vault canonical reads,
    # even on a machine whose device default is local.
    echo "vault" > "$MODE_FILE"
fi

# ── summary ───────────────────────────────────────────────────────────────
echo ""
echo "==> migration summary"
echo "    migrated:                 $migrated_count file(s)"
echo "    skipped (already vault):  $skipped_count file(s)"
echo "    conflicts:                $conflict_count file(s)"
if [[ $PREVIEW -eq 0 ]]; then
    echo "    marker:                   $MARKER"
    echo "    project mode:             $MODE_FILE → vault"
fi

# ── cleanup mode ──────────────────────────────────────────────────────────
if [[ $CLEANUP -eq 1 && $PREVIEW -eq 0 ]]; then
    echo ""
    echo "==> cleanup: removing legacy paths after byte-identical verification"
    if [[ $conflict_count -gt 0 ]]; then
        echo "  ABORT: $conflict_count conflict(s) detected. Resolve before cleanup." >&2
        exit 1
    fi
    if [[ $ASSUME_YES -eq 0 ]]; then
        printf "  proceed with cleanup? [y/N] "
        read -r response
        if [[ "$response" != "y" && "$response" != "Y" ]]; then
            echo "  cleanup skipped (operator declined)."
            exit 0
        fi
    fi
    cleaned=0
    for f in "${STATE_FILES[@]}"; do
        src="$LEGACY_HARNESS/$f"
        dst="$HARNESS_DIR/$f"
        [[ ! -e "$src" ]] && continue
        if cmp -s "$src" "$dst"; then
            rm "$src"
            cleaned=$((cleaned + 1))
        fi
    done
    # PLAN.archive cleanup
    while IFS= read -r archive_file; do
        [[ -z "$archive_file" ]] && continue
        rel="$(basename "$archive_file")"
        src="$LEGACY_HARNESS/$rel"
        dst="$HARNESS_DIR/$rel"
        if [[ -e "$src" ]] && cmp -s "$src" "$dst"; then
            rm "$src"
            cleaned=$((cleaned + 1))
        fi
    done < <(find "$LEGACY_HARNESS" -maxdepth 1 -name 'PLAN.archive.*.md' 2>/dev/null)
    # Directories
    for d in "${STATE_DIRS[@]}"; do
        src="$LEGACY_HARNESS/$d"
        [[ ! -d "$src" ]] && continue
        # Whole-subtree compare is fragile in pure bash; safer to leave dir
        # cleanup to the operator after a visual diff. Just announce.
        echo "  (not removing $d/ — operator should verify + rm manually)"
    done
    echo "  cleaned $cleaned legacy file(s) (.evidence-reads + dirs preserved per DC-1)."
fi

echo ""
echo "==> done."
echo ""
echo "Next steps:"
echo "  - Inspect $HARNESS_DIR/ in Obsidian. The harness will now read state from there."
echo "  - Run /work or /release in $TARGET to confirm the dispatcher uses the vault path."
echo "  - To roll back: bash $0 --rollback $TARGET (sets .project-mode=local)."
if [[ $CLEANUP -eq 0 ]]; then
    echo "  - To remove legacy <target>/.harness/<file>: re-run with --cleanup after verifying."
fi
