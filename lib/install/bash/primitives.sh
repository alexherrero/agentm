# lib/install/bash/primitives.sh
#
# Shared install plumbing for the agentm / crickets sibling repos.
# This file is BYTE-IDENTICAL across both repos. Modifications go through
# `scripts/sync-lib.sh` to keep checksums aligned. See `CONTRACT.md` for the
# invariants both repos depend on.
#
# Caller contract — these variables must be set in the caller's scope before
# any function here is invoked:
#
#   UPDATE_MODE       integer 0|1. When 1, managed-copy functions overwrite
#                     existing destinations; when 0, skip-if-exists.
#   BOUNDARY_ROOTS    bash array of absolute directory paths. ensure_boundary_src
#                     accepts a source iff it lives under one of these roots.
#
# Functions exposed:
#   ensure_boundary_src <src>
#   cp_user <src> <dst>
#   cp_managed <src> <dst>
#   cp_user_walk <src_root> <dst_root>
#   cp_managed_dir <src> <dst>
#   sync_managed_parents <managed_dirs...> -- <empty_parent_candidates...>

# ── boundary guard ─────────────────────────────────────────────────────────
#
# Asserts that a copy source originates from a known content root in the
# caller repo. Makes out-of-boundary cp regressions fail loudly instead of
# silently propagating wrong content (e.g. the source repo's own wiki/, CI
# workflows, or test infra) into target projects.

ensure_boundary_src() {
    local src="$1"
    local root
    for root in "${BOUNDARY_ROOTS[@]}"; do
        case "$src" in
            "$root"/*) return 0 ;;
        esac
    done
    echo "Error: installer-boundary violation — cp source outside allowed roots:" >&2
    echo "       src: $src" >&2
    echo "       allowed roots:" >&2
    for root in "${BOUNDARY_ROOTS[@]}"; do
        echo "         $root/*" >&2
    done
    exit 1
}

# ── file-level copies ──────────────────────────────────────────────────────

# cp_user: copy only if destination is missing. For files the user owns and edits.
cp_user() {
    local src="$1" dst="$2"
    ensure_boundary_src "$src"
    if [[ ! -e "$dst" ]]; then
        cp "$src" "$dst"
        echo "    created $dst"
    else
        echo "    kept    $dst (exists)"
    fi
}

# cp_managed: in --update mode, always overwrite. Otherwise, skip if exists.
# For repo-authored files the user should not edit.
cp_managed() {
    local src="$1" dst="$2"
    ensure_boundary_src "$src"
    if [[ $UPDATE_MODE -eq 1 && -e "$dst" ]]; then
        if cmp -s "$src" "$dst"; then
            echo "    kept    $dst (up to date)"
        else
            cp "$src" "$dst"
            echo "    updated $dst"
        fi
    elif [[ ! -e "$dst" ]]; then
        cp "$src" "$dst"
        echo "    created $dst"
    else
        echo "    kept    $dst (exists — re-run with --update to refresh)"
    fi
}

# ── directory-level copies ─────────────────────────────────────────────────

# cp_user_walk: walk a source directory recursively and cp_user each file.
# Preserves any files the user has already created in the destination tree;
# fills in missing scaffold files without clobbering. Used for wiki/ where
# scaffold and human-authored pages coexist.
cp_user_walk() {
    local src_root="$1" dst_root="$2"
    [[ -d "$src_root" ]] || return 0
    local src_file rel dst_file
    while IFS= read -r src_file; do
        rel="${src_file#"$src_root"/}"
        dst_file="$dst_root/$rel"
        mkdir -p "$(dirname "$dst_file")"
        cp_user "$src_file" "$dst_file"
    done < <(find "$src_root" -type f)
}

# cp_managed_dir: same managed-update semantics for directory skills.
# Wipe-and-recreate on --update; skip-if-exists otherwise.
cp_managed_dir() {
    local src="$1" dst="$2"
    ensure_boundary_src "$src"
    if [[ $UPDATE_MODE -eq 1 && -e "$dst" ]]; then
        rm -rf "$dst"
        cp -R "$src" "$dst"
        echo "    updated $dst"
    elif [[ ! -e "$dst" ]]; then
        cp -R "$src" "$dst"
        echo "    created $dst"
    else
        echo "    kept    $dst (exists — re-run with --update to refresh)"
    fi
}

# ── --update sync block ────────────────────────────────────────────────────
#
# Wipe fully-managed parent dirs before recreate from source. Called by the
# installer in --update mode. The caller passes its repo-specific list of
# managed parent dirs and empty-parent candidates.
#
# Usage:
#   sync_managed_parents \
#       <managed_dir_1> <managed_dir_2> ... \
#       -- \
#       <empty_parent_candidate_1> <empty_parent_candidate_2> ...
#
# The `--` separates the two lists.
#
# Output (matches the pre-extraction inline behavior verbatim):
#   "    removed <p>/" for each wiped managed dir
#   "    removed empty <p>/" for each removed empty parent
#   "    wiped N managed dir(s); rebuilding from source" summary
#
# Why this exists: cp_managed_dir refreshes content but never removes a dir
# that has been deleted from source. Without this wipe, --update leaves
# orphan files from the previous version's adapter set, and the local tree
# drifts from the GitHub source-of-truth.

sync_managed_parents() {
    local mode="managed"
    local wiped=0
    local p
    for p in "$@"; do
        if [[ "$p" == "--" ]]; then
            mode="empty"
            continue
        fi
        if [[ "$mode" == "managed" ]]; then
            if [[ -d "$p" ]]; then
                rm -rf "$p"
                echo "    removed $p/"
                wiped=$((wiped + 1))
            fi
        else  # mode == "empty"
            if [[ -d "$p" ]] && [[ -z "$(ls -A "$p" 2>/dev/null)" ]]; then
                rmdir "$p"
                echo "    removed empty $p/"
            fi
        fi
    done
    echo "    wiped $wiped managed dir(s); rebuilding from source"
}
