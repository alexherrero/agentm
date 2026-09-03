#!/usr/bin/env bash
# projects_merge_2b.sh — the vault-side move of filing-v2 part 2b, in one
# scripted, resumable pass: `<memory-root>/desk/projects/*` → `<vault>/Projects/`
# and `<memory-root>/desk/labelling` → `<vault>/Projects/agentm/labelling`.
#
# Run order on the live machine (the live-rename doctrine — seam and tolerant
# readers first, then the data, then drop tolerance): merge the first-half PR,
# advance the clone, rebuild the binary, quiesce the daemon + runner, run with
# --apply, kickstart, then `agentmd embed` — a move re-keys every moved note
# for the vector arm, and the daemon backfills nothing on its own, so the
# retrieval gate reads as a regression until the backfill runs (1,294 notes,
# ~3.5 min on the live merge) — then verify, then the writer-flip commits.
# Until --apply is passed it only prints what it would do — the dry run IS
# the default.
#
# Invariants (design § Migrations): basenames preserved (name-resolved
# wikilinks survive); moves are `git mv` so the vault repository keeps rename
# history; vault-wins on collision — the root `Projects/` shell already exists
# with the operator's own index.md, which stays; a re-run after a partial apply
# completes the remainder and touches nothing done.
set -euo pipefail

VAULT="${MEMORY_VAULT_PATH:-}"          # the MEMORY root (…/Vault/Agent)
if [[ -z "$VAULT" || ! -d "$VAULT" ]]; then
  echo "projects_merge_2b: MEMORY_VAULT_PATH unset or not a directory" >&2
  exit 2
fi
ROOT="$(cd "$VAULT/.." && pwd)"          # the vault root (…/Vault)
DEST="$ROOT/Projects"
SRC="$VAULT/desk/projects"

# The merge targets the vault-root sibling, which exists only in the nested
# layout: the memory root inside an Obsidian vault (`.obsidian/` at the
# parent, none at the memory root). A flat vault keeps `Projects/` inside the
# memory root and has nothing to merge upward — refuse rather than move the
# tree into whatever `Projects/` sits beside the vault.
if [[ -d "$VAULT/.obsidian" || ! -d "$ROOT/.obsidian" ]]; then
  echo "projects_merge_2b: $VAULT is not nested inside an Obsidian vault (no $ROOT/.obsidian, or the memory root is the vault itself) — nothing to merge" >&2
  exit 2
fi

APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1

moved=0; skipped=0
say() { echo "  $*"; }
run() {
  if [[ $APPLY -eq 1 ]]; then "$@"; else say "would: $*"; fi
}

# git-aware move: `git mv` when the source is tracked (rename history), plain
# mv otherwise (Icon files and other untracked artefacts).
gmv() {
  local src="$1" dst="$2"
  if [[ $APPLY -eq 1 ]]; then
    if git -C "$ROOT" ls-files --error-unmatch "$src" >/dev/null 2>&1 \
       || [[ -d "$src" && -n "$(git -C "$ROOT" ls-files "$src" | head -1)" ]]; then
      git -C "$ROOT" mv -k "$src" "$dst" 2>/dev/null || mv "$src" "$dst"
    else
      mv "$src" "$dst"
    fi
  else
    say "would: mv $src -> $dst"
  fi
}

move_entry() { # move_entry <src-entry> <dest-dir>
  local src="$1" dest="$2"
  [[ -e "$src" ]] || { skipped=$((skipped+1)); return 0; }
  local base; base="$(basename "$src")"
  if [[ -e "$dest/$base" ]]; then
    if [[ -d "$src" && -d "$dest/$base" ]]; then
      # Both directories (only the root shell's own files live there today):
      # merge per entry, source wins, then drop the emptied shell.
      say "merge (both directories): $base"
      local e
      for e in "$src"/* "$src"/.[!.]*; do [[ -e "$e" ]] && move_entry "$e" "$dest/$base"; done
      [[ $APPLY -eq 1 ]] && rmdir "$src" 2>/dev/null || true
      return 0
    fi
    if [[ -d "$src" || -d "$dest/$base" ]]; then
      # One side is a directory and the other is not: replacing would delete
      # a whole subtree, or bury a file under a directory. Refuse — a type
      # clash is resolved by hand, with both copies still in place.
      echo "projects_merge_2b: refusing type clash at $dest/$base ($([[ -d "$src" ]] && echo directory || echo file) arriving on a $([[ -d "$dest/$base" ]] && echo directory || echo file)) — resolve by hand and re-run" >&2
      exit 3
    fi
    say "collision: source replaces destination copy: $base"
    run rm -rf "$dest/$base"
  fi
  gmv "$src" "$dest/$base"
  moved=$((moved+1))
}

echo "== projects merge: $SRC -> $DEST =="
run mkdir -p "$DEST"
if [[ -d "$SRC" ]]; then
  for f in "$SRC"/* "$SRC"/.[!.]*; do
    [[ -e "$f" ]] || continue
    base_f="$(basename "$f")"
    [[ "$base_f" == "Icon"* ]] && continue   # Drive artefact, stays for the sync layer
    move_entry "$f" "$DEST"
  done
  [[ $APPLY -eq 1 ]] && { rmdir "$SRC" 2>/dev/null || say "note: $SRC not empty, left in place"; }
fi

echo "== labelling: $VAULT/desk/labelling -> $DEST/agentm/labelling =="
if [[ -d "$VAULT/desk/labelling" ]]; then
  run mkdir -p "$DEST/agentm"
  if [[ -e "$DEST/agentm/labelling" ]]; then
    say "merge (both directories): labelling"
    for f in "$VAULT/desk/labelling"/*; do
      [[ -e "$f" ]] || continue
      [[ "$(basename "$f")" == "Icon"* ]] && continue
      move_entry "$f" "$DEST/agentm/labelling"
    done
    [[ $APPLY -eq 1 ]] && { rmdir "$VAULT/desk/labelling" 2>/dev/null || say "note: desk/labelling not empty, left in place"; }
  else
    gmv "$VAULT/desk/labelling" "$DEST/agentm/labelling"
    moved=$((moved+1))
  fi
fi

echo "== summary: moved=$moved skipped=$skipped apply=$APPLY =="
if [[ $APPLY -eq 1 ]]; then
  git -C "$ROOT" add -A "$DEST" "$VAULT/desk" 2>/dev/null || true
  say "staged in the vault repository — commit with: git -C $ROOT commit -m 'filing v2 2b: projects merge'"
else
  echo "(dry run — re-run with --apply after merging the first-half PR, deploying, and quiescing the daemon and runner)"
fi
exit 0
