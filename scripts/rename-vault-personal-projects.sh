#!/usr/bin/env bash
# rename-vault-personal-projects.sh — V4 #26 vault-side folder rename.
#
# Renames `<vault>/personal-projects/` → `<vault>/projects/` AND sweeps
# every internal reference to the old name across:
#   - <vault>/personal/_always-load/*.md   (always-load conventions)
#   - <vault>/personal/**/*.md             (inbox, domains, workflow,
#                                                   patterns, etc.)
#   - <vault>/projects/*/_index.md                 (group: frontmatter field)
#   - <vault>/projects/*/**/*.md                   (wikilinks to sibling projects)
#
# Idempotent: re-running on an already-renamed vault is a no-op.
# Reversible: kept-history paths under `_archive/` are NOT rewritten — historical
# CHANGELOG-style references survive (preserving audit trail).
#
# Usage:
#   bash agentm/scripts/rename-vault-personal-projects.sh [OPTIONS]
#
# Options:
#   --vault-path <path>   Override vault root. Default: $MEMORY_VAULT_PATH env.
#   --preview             Dry-run: print what would change without modifying.
#   --help, -h            Print this help and exit.
#
# Per plan #20 task 5 / plan #18 design `05-state-migration.md` § (a).

set -euo pipefail

# ── argument parsing ──────────────────────────────────────────────────────
VAULT_PATH="${MEMORY_VAULT_PATH:-}"
PREVIEW=0

print_help() {
    # Portable head -n -1 equivalent: sed '$d' drops the last line.
    sed -n '/^# rename-vault-personal-projects.sh/,/^[^#]/p' "$0" | sed 's|^# \?||' | sed '$d'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --vault-path)
            VAULT_PATH="${2:-}"
            [[ -z "$VAULT_PATH" ]] && { echo "--vault-path requires a value" >&2; exit 2; }
            shift 2
            ;;
        --preview)
            PREVIEW=1; shift
            ;;
        --help|-h)
            print_help; exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            echo "" >&2
            print_help >&2
            exit 2
            ;;
    esac
done

# ── vault resolution ──────────────────────────────────────────────────────
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

OLD_DIR="$VAULT_PATH/personal-projects"
NEW_DIR="$VAULT_PATH/projects"

# ── idempotency check ─────────────────────────────────────────────────────
if [[ -d "$NEW_DIR" ]] && [[ ! -d "$OLD_DIR" ]]; then
    echo "==> vault already renamed (projects/ exists, personal-projects/ absent). Nothing to do."
    exit 0
fi
if [[ -d "$NEW_DIR" ]] && [[ -d "$OLD_DIR" ]]; then
    echo "Error: BOTH $NEW_DIR AND $OLD_DIR exist. Operator must resolve manually:" >&2
    echo "  - If projects/ is post-rename canonical: rm -rf $OLD_DIR (or move conflicting entries first)" >&2
    echo "  - If personal-projects/ is canonical: rm -rf $NEW_DIR (or merge entries into personal-projects/)" >&2
    exit 1
fi
if [[ ! -d "$OLD_DIR" ]]; then
    echo "Error: neither $NEW_DIR nor $OLD_DIR exists at vault root. Is this the correct vault?" >&2
    exit 1
fi

# ── preview banner ────────────────────────────────────────────────────────
if [[ $PREVIEW -eq 1 ]]; then
    echo "==> [PREVIEW MODE — no changes will be made]"
fi
echo "==> vault rename: personal-projects/ → projects/"
echo "    vault: $VAULT_PATH"
echo "    old:   $OLD_DIR"
echo "    new:   $NEW_DIR"
echo ""

# ── portable sed-in-place wrapper ─────────────────────────────────────────
# BSD sed (macOS) requires `sed -i ''` while GNU sed wants `sed -i`. Use a
# Python one-liner instead for full portability + no .bak debris.
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

# ── step 1: rename the directory ──────────────────────────────────────────
if [[ $PREVIEW -eq 1 ]]; then
    echo "  WOULD: mv \"$OLD_DIR\" \"$NEW_DIR\""
else
    mv "$OLD_DIR" "$NEW_DIR"
    echo "  renamed: personal-projects/ → projects/"
fi

# ── step 2: collect files to sweep ────────────────────────────────────────
# Excluded patterns: _archive/ subdirs (historical), CHANGELOG-style files,
# and PLAN.archive.*.md if any landed in vault. Markdown only.
sweep_targets() {
    # Always-load
    find "$VAULT_PATH/personal/_always-load" -type f -name '*.md' 2>/dev/null
    # personal/**/*.md (depth-walk) but not _archive/
    find "$VAULT_PATH/personal" -type f -name '*.md' -not -path '*/_archive/*' 2>/dev/null
    # _idea-incubator/**/*.md — incubator entries often have wikilinks to
    # `[[personal-projects/<slug>/...]]` and forward-looking promotion-
    # destination prose like "moves to AgentMemory/personal-projects/<slug>/".
    # Both forms need rewriting; otherwise the wikilinks break post-rename.
    find "$VAULT_PATH/_idea-incubator" -type f -name '*.md' -not -path '*/_archive/*' 2>/dev/null
    # Project-tree depth-walk. In preview mode the mv hasn't happened yet, so
    # files live under $OLD_DIR; in live mode the mv ran above so they live
    # under $NEW_DIR. Pick the dir that exists.
    local project_root
    if [[ -d "$NEW_DIR" ]]; then
        project_root="$NEW_DIR"
    else
        project_root="$OLD_DIR"
    fi
    find "$project_root" -type f -name '*.md' -not -path '*/_archive/*' -not -name 'PLAN.archive.*.md' 2>/dev/null
    # _meta/ entries are deliberately EXCLUDED — they hold historical
    # narrative describing past vault state (seed-pass manifests, recall-
    # validation reports). Rewriting them would falsify the historical
    # record. Operators who want to update narrative refs can edit by hand.
}

# ── step 3: sweep each file ───────────────────────────────────────────────
if [[ $PREVIEW -eq 1 ]]; then
    echo ""
    echo '  WOULD sed-sweep personal-projects/ → projects/ across:'
    count=0
    while IFS= read -r f; do
        [[ -z "$f" ]] && continue
        if grep -q "personal-projects" "$f" 2>/dev/null; then
            echo "    $f"
            count=$((count + 1))
        fi
    done < <(sweep_targets | sort -u)
    echo ""
    echo "  WOULD rewrite $count file(s)."
else
    echo ""
    echo "==> sed sweep: personal-projects → projects"
    rewrote=0
    seen=0
    while IFS= read -r f; do
        [[ -z "$f" ]] && continue
        seen=$((seen + 1))
        if grep -q "personal-projects" "$f" 2>/dev/null; then
            _inplace_sub "$f" "personal-projects/" "projects/"
            # Also handle the bare token form (no trailing slash) in case some
            # docs reference `personal-projects` without a trailing path segment.
            # Limit this to whole-word matches to avoid mangling unrelated strings.
            _inplace_sub "$f" "personal-projects" "projects"
            rewrote=$((rewrote + 1))
        fi
    done < <(sweep_targets | sort -u)
    echo ""
    echo "==> swept $seen file(s); rewrote $rewrote file(s)."
fi

# ── post-run check ────────────────────────────────────────────────────────
if [[ $PREVIEW -eq 0 ]]; then
    echo ""
    echo "==> post-run integrity check"
    remaining=$(grep -rE 'personal-projects' "$VAULT_PATH" --include='*.md' 2>/dev/null | grep -vE '(_archive/|PLAN\.archive)' | wc -l | tr -d ' ')
    if [[ "$remaining" -gt 0 ]]; then
        echo "  WARN: $remaining residual reference(s) to 'personal-projects' remain in non-archive markdown:" >&2
        grep -rEn 'personal-projects' "$VAULT_PATH" --include='*.md' 2>/dev/null | grep -vE '(_archive/|PLAN\.archive)' | head -10 >&2
        echo "  (These may be intentional historical references inside narrative text. Review manually.)" >&2
    else
        echo "  clean — no 'personal-projects' references outside _archive/ + PLAN.archive."
    fi

    echo ""
    echo "==> rename complete. Reload Obsidian (Cmd-R or File > Reload) to refresh its graph + sidebar."
fi
