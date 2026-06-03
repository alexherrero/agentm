#!/usr/bin/env python3
"""install_migrate — classify + apply + rollback + cleanup per-project installs.

For V4 #30 plan 3 of 3 (closing). Pairs with install_symlinks (forward
direction) to provide the REVERSE direction — detect what's in a pre-V4.3
per-project install at `<target>/.claude/{skills,hooks,agents,commands}/`
+ migrate it to user scope under `~/.claude/`.

Mirrors V4 #26's `migrate-harness-to-vault.{sh,ps1}` pattern: preview-first;
idempotent; reversible via rollback record; opt-in destructive cleanup after
byte-identical verification.

## Classifications (DC-4)

For each file/dir under `<target>/.claude/`:
  - SAFE_TO_MIGRATE     — byte-identical to source-clone canonical path
                          (SHA256 match). Safe to remove from target;
                          equivalent representation exists in source clone.
  - ALREADY_SYMLINKED   — target IS a symlink (either to source clone in
                          source mode, or to user scope post-prior-migration).
                          No-op; already migrated or never needed migrating.
  - OPERATOR_EDITED     — exists in source-clone mapping but SHA differs.
                          Operator made local edits to a customization.
                          Skip-with-warn by default; --force migrates anyway
                          (with backup so rollback can restore).
  - UNRECOGNIZED        — file has no source-clone mapping entry. Operator's
                          own customization or stale artifact. Leave alone.

## .migrate-record.json schema (v1)

Written to `<target>/.agentm-migrate-record.json` on apply; read on rollback.

```
{
  "version": 1,
  "target_root": "/path/to/project",
  "migrated_at": "2026-05-27T18:00:00Z",
  "source_clones_used": {"agentm": "..."},
  "registered_slug": "myproject",   // null if --no-register or registry skipped
  "actions": [
    {"kind": "safe_to_migrate",
     "rel_path": "agents/foo.md",
     "source_clone": "agentm",
     "source_path": "/.../adapters/claude-code/agents/foo.md",
     "sha256": "..."},
    {"kind": "force_migrated",
     "rel_path": "agents/baz.md",
     "backup_path": ".agentm-migrate-backup/agents/baz.md",
     "target_sha_before": "...",
     "source_sha": "..."},
    {"kind": "operator_edited_skipped",
     "rel_path": "agents/bar.md",
     "target_sha": "...",
     "source_sha": "..."}
  ]
}
```

Stdlib-only (ADR 0001). Per V4 #30 plan 3 task 2.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

# Import the single-source-of-truth mapping from install_symlinks.
# Same lib/install/python/ package; relative import works under both direct
# `python3 install_migrate.py` + `python3 -m install_migrate` invocation.
try:
    from install_symlinks import symlink_targets_for_clone
except ImportError:  # pragma: no cover — only when imported as module
    from .install_symlinks import symlink_targets_for_clone  # type: ignore


# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

# Classifications (DC-4)
SAFE_TO_MIGRATE = "safe_to_migrate"
ALREADY_SYMLINKED = "already_symlinked"
OPERATOR_EDITED = "operator_edited"
UNRECOGNIZED = "unrecognized"

# .migrate-record.json filename — under <target>/
_RECORD_FILENAME = ".agentm-migrate-record.json"
# Backup directory for --force migrations — under <target>/
_BACKUP_DIRNAME = ".agentm-migrate-backup"
# Per-project install subdirs under `<target>/.claude/`
_INSTALL_SUBDIRS = ("skills", "hooks", "agents", "commands")
# Record schema version
_SCHEMA_VERSION = 1


# -----------------------------------------------------------------------------
# SHA256 helpers
# -----------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    """Return hex SHA256 of a file's bytes."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_dir(path: Path) -> str:
    """Return hex SHA256 of a directory tree.

    Hash is computed over a sorted list of `(rel_path, file_sha256)` pairs
    serialized as `<rel>\\t<sha>\\n` lines. Stable across platforms (sorted
    rel paths use forward slashes via `as_posix()`).

    **Dotfile-skip policy:** any path component starting with `.` (e.g.
    `.DS_Store`, `.git/`, `__pycache__/.cache`, editor `.swp` siblings) is
    excluded from the hash. This is mandatory for cross-platform parity —
    macOS Finder sprinkles `.DS_Store` into every visited directory; without
    this skip every dir bundle would misclassify as `OPERATOR_EDITED` on
    macOS even when byte-identical to the source clone. The skip is
    symmetric (applied to both target + source), so two bundles that differ
    only in dotfile noise hash equal.

    Symlinks are followed (per `Path.iterdir` + `is_file` semantics).
    """
    if not path.is_dir():
        raise ValueError(f"not a directory: {path}")
    entries: list[tuple[str, str]] = []
    for child in sorted(path.rglob("*")):
        if not child.is_file():
            continue
        rel = child.relative_to(path).as_posix()
        # Skip any path component that starts with '.' (filesystem noise).
        if any(part.startswith(".") for part in rel.split("/")):
            continue
        entries.append((rel, _sha256_file(child)))
    h = hashlib.sha256()
    for rel, sha in entries:
        h.update(f"{rel}\t{sha}\n".encode("utf-8"))
    return h.hexdigest()


def _sha256_path(path: Path) -> str:
    """Dispatch SHA256 by file/dir kind. Raises if path is missing."""
    if path.is_dir():
        return _sha256_dir(path)
    return _sha256_file(path)


# -----------------------------------------------------------------------------
# Inverse mapping
# -----------------------------------------------------------------------------

def inverse_mapping_for_clones(
    source_clones: dict[str, str],
) -> dict[str, tuple[str, Path, bool]]:
    """Build the inverse mapping: install-relative path → source-clone info.

    Given the detected source clones (typically from
    `install_state.detect_source_clones()`), invert the forward mapping
    returned by `install_symlinks.symlink_targets_for_clone()` so that for
    any `<install_rel>` (e.g. `agents/foo.md`) we can recover:
      - which source clone provides it (`agentm`)
      - the canonical source path under that clone
      - whether it's a dir bundle (`is_dir=True`) or single file

    If two clones both map the same install_rel (by some accident), the
    FIRST clone iterated wins.
    In practice the symlinkable subset partitions cleanly across clones; the
    overlap path is defensive only.

    Returns a dict keyed by install-relative path string with forward slashes.
    """
    out: dict[str, tuple[str, Path, bool]] = {}
    for slug, clone_root_str in source_clones.items():
        clone_root = Path(clone_root_str)
        if not clone_root.is_dir():
            continue
        for source_path, rel_install, is_dir in symlink_targets_for_clone(slug, clone_root):
            # Normalize rel_install to forward-slash form for cross-platform comparison
            key = rel_install.replace(os.sep, "/")
            if key not in out:
                out[key] = (slug, source_path, is_dir)
    return out


# -----------------------------------------------------------------------------
# Target walking
# -----------------------------------------------------------------------------

def _walk_target(target_root: Path) -> list[tuple[str, Path, bool]]:
    """Walk `<target>/.claude/{skills,hooks,agents,commands}/` and return entries.

    Each entry: `(install_rel_path, abs_path, is_dir)`.

    Walks 1 level deep under each subdir:
      - skills/ + hooks/: emit each immediate child (dirs OR .md files)
      - agents/ + commands/: emit each immediate child .md file

    Returns [] if `<target>/.claude/` is absent. Hidden entries (leading dot)
    are skipped.
    """
    out: list[tuple[str, Path, bool]] = []
    claude_root = target_root / ".claude"
    if not claude_root.is_dir():
        return out
    for subdir in _INSTALL_SUBDIRS:
        sub_path = claude_root / subdir
        if not sub_path.is_dir():
            continue
        for child in sorted(sub_path.iterdir()):
            if child.name.startswith("."):
                continue
            if subdir in ("skills", "hooks"):
                if child.is_dir():
                    out.append((f"{subdir}/{child.name}", child, True))
                elif child.is_file() and child.suffix == ".md":
                    out.append((f"{subdir}/{child.name}", child, False))
            else:  # agents, commands
                if child.is_file() and child.suffix == ".md":
                    out.append((f"{subdir}/{child.name}", child, False))
    return out


# -----------------------------------------------------------------------------
# classify()
# -----------------------------------------------------------------------------

def classify(
    target_root: Path | str,
    source_clones: dict[str, str],
) -> list[dict]:
    """Classify every entry under `<target>/.claude/{...}/`.

    Returns a list of dicts, one per entry, each with keys:
      - rel_path:         "agents/foo.md", "skills/pii-scrubber", etc.
      - classification:   one of SAFE_TO_MIGRATE / ALREADY_SYMLINKED /
                          OPERATOR_EDITED / UNRECOGNIZED
      - target_path:      absolute path under target_root
      - source_clone:     slug from source_clones (None if UNRECOGNIZED)
      - source_path:      absolute path under source clone (None if UNRECOGNIZED)
      - is_dir:           True for dir bundles
      - target_sha:       SHA256 of target (None if symlink)
      - source_sha:       SHA256 of source path (None if no mapping entry)

    Pure read; no writes.
    """
    target = Path(target_root)
    inverse = inverse_mapping_for_clones(source_clones)
    out: list[dict] = []
    for rel_path, target_path, is_dir in _walk_target(target):
        entry: dict = {
            "rel_path": rel_path,
            "target_path": str(target_path),
            "is_dir": is_dir,
            "source_clone": None,
            "source_path": None,
            "target_sha": None,
            "source_sha": None,
        }
        # Lookup mapping
        mapping = inverse.get(rel_path)
        # Detect symlink first (cheap; handles both already-migrated cases)
        if target_path.is_symlink():
            entry["classification"] = ALREADY_SYMLINKED
            if mapping is not None:
                entry["source_clone"] = mapping[0]
                entry["source_path"] = str(mapping[1])
            out.append(entry)
            continue
        # Not a symlink — check mapping
        if mapping is None:
            entry["classification"] = UNRECOGNIZED
            out.append(entry)
            continue
        clone_slug, source_path, _src_is_dir = mapping
        entry["source_clone"] = clone_slug
        entry["source_path"] = str(source_path)
        # Compare SHAs
        if not source_path.exists():
            # Source clone has been pruned since the per-project install
            entry["classification"] = UNRECOGNIZED
            out.append(entry)
            continue
        try:
            target_sha = _sha256_path(target_path)
            source_sha = _sha256_path(source_path)
        except (OSError, ValueError) as exc:
            # Defensive: treat unreadable as unrecognized
            entry["classification"] = UNRECOGNIZED
            entry["_error"] = str(exc)
            out.append(entry)
            continue
        entry["target_sha"] = target_sha
        entry["source_sha"] = source_sha
        if target_sha == source_sha:
            entry["classification"] = SAFE_TO_MIGRATE
        else:
            entry["classification"] = OPERATOR_EDITED
        out.append(entry)
    return out


# -----------------------------------------------------------------------------
# apply()
# -----------------------------------------------------------------------------

def _record_path(target_root: Path) -> Path:
    return target_root / _RECORD_FILENAME


def _backup_root(target_root: Path) -> Path:
    return target_root / _BACKUP_DIRNAME


def _remove_path(p: Path) -> None:
    """Remove a file or directory tree. No-op if absent."""
    if p.is_symlink() or p.is_file():
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    elif p.is_dir():
        shutil.rmtree(p)


def apply(
    target_root: Path | str,
    *,
    source_clones: dict[str, str],
    dry_run: bool = True,
    force: bool = False,
    registry_slug: Optional[str] = None,
) -> dict:
    """Apply the migration: remove SAFE_TO_MIGRATE entries from target.

    Returns a summary dict:
      {
        "classified":   [list of classify() entries],
        "actions":      [list of action records written to .migrate-record.json],
        "record_path":  str path to written record (or None if dry_run),
        "dry_run":      bool,
        "skipped_force_needed": int,  # count of OPERATOR_EDITED that would need --force
      }

    Per-classification behavior:
      - SAFE_TO_MIGRATE:    remove from target; record action.
      - ALREADY_SYMLINKED:  no-op (already migrated).
      - OPERATOR_EDITED:    skip-with-warn by default; with `force=True`:
                            back up to <target>/.agentm-migrate-backup/<rel>;
                            remove from target; record `force_migrated` action.
                            ALWAYS records `operator_edited_skipped` if not forcing.
      - UNRECOGNIZED:       no-op (operator's own content).

    Idempotent: re-running after a partial migration only acts on entries
    that still need acting.

    If `dry_run=True` (default), no filesystem mutation occurs and no record
    is written.

    `registry_slug` is recorded in the JSON record for downstream registry
    integration (executed by the CLI script, not this primitive). Pass None
    to leave it null.
    """
    target = Path(target_root)
    classified = classify(target, source_clones)
    actions: list[dict] = []
    skipped_force_needed = 0
    for entry in classified:
        cls = entry["classification"]
        rel = entry["rel_path"]
        target_path = Path(entry["target_path"])
        if cls == SAFE_TO_MIGRATE:
            action = {
                "kind": "safe_to_migrate",
                "rel_path": rel,
                "source_clone": entry["source_clone"],
                "source_path": entry["source_path"],
                "sha256": entry["target_sha"],
            }
            if not dry_run:
                _remove_path(target_path)
            actions.append(action)
        elif cls == OPERATOR_EDITED:
            if force:
                backup_path = _backup_root(target) / rel
                # Backup-collision policy: if a backup already exists at this
                # rel_path (from a prior force-apply run), DO NOT overwrite —
                # that would silently destroy the original-preserved content
                # and leave the record's target_sha_before stale + lying.
                # Refuse this entry by recording an `operator_edited_skipped`
                # action with a `backup_collision: true` flag; operator must
                # manually rollback the prior migration before retrying.
                if not dry_run and backup_path.exists():
                    actions.append({
                        "kind": "operator_edited_skipped",
                        "rel_path": rel,
                        "target_sha": entry["target_sha"],
                        "source_sha": entry["source_sha"],
                        "backup_collision": True,
                    })
                    skipped_force_needed += 1
                    continue
                action = {
                    "kind": "force_migrated",
                    "rel_path": rel,
                    "backup_path": str(backup_path.relative_to(target)),
                    "target_sha_before": entry["target_sha"],
                    "source_sha": entry["source_sha"],
                }
                if not dry_run:
                    backup_path.parent.mkdir(parents=True, exist_ok=True)
                    if target_path.is_dir():
                        shutil.copytree(target_path, backup_path)
                        shutil.rmtree(target_path)
                    else:
                        shutil.copy2(target_path, backup_path)
                        target_path.unlink()
                actions.append(action)
            else:
                skipped_force_needed += 1
                actions.append({
                    "kind": "operator_edited_skipped",
                    "rel_path": rel,
                    "target_sha": entry["target_sha"],
                    "source_sha": entry["source_sha"],
                })
        # ALREADY_SYMLINKED + UNRECOGNIZED: no action recorded

    record_path = None
    if not dry_run and actions:
        record = {
            "version": _SCHEMA_VERSION,
            "target_root": str(target.resolve()),
            "migrated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source_clones_used": source_clones,
            "registered_slug": registry_slug,
            "actions": actions,
        }
        rp = _record_path(target)
        # If record already exists (partial-migration re-run), MERGE actions
        # by appending new actions to existing list. Preserve original
        # migrated_at as the first-migration timestamp.
        if rp.exists():
            try:
                with rp.open() as f:
                    prev = json.load(f)
                # Dedup key: (rel_path, kind) — same rel_path may legitimately
                # appear in multiple kinds across re-runs (e.g. force_migrated
                # on first apply, then operator_edited_skipped with
                # backup_collision on second apply when the operator restored
                # the file post-migration; both records carry distinct
                # information). De-dup only when BOTH rel_path AND kind match.
                existing_keys = {
                    (a.get("rel_path"), a.get("kind"))
                    for a in prev.get("actions", [])
                }
                merged_actions = list(prev.get("actions", []))
                for new_a in actions:
                    key = (new_a.get("rel_path"), new_a.get("kind"))
                    if key not in existing_keys:
                        merged_actions.append(new_a)
                record["actions"] = merged_actions
                record["migrated_at"] = prev.get("migrated_at", record["migrated_at"])
            except (json.JSONDecodeError, OSError):
                # Corrupted record — overwrite (operator should rollback if needed)
                pass
        tmp = rp.with_suffix(rp.suffix + ".tmp")
        with tmp.open("w") as f:
            json.dump(record, f, indent=2)
            f.write("\n")
        tmp.replace(rp)
        record_path = str(rp)

    return {
        "classified": classified,
        "actions": actions,
        "record_path": record_path,
        "dry_run": dry_run,
        "skipped_force_needed": skipped_force_needed,
    }


# -----------------------------------------------------------------------------
# rollback()
# -----------------------------------------------------------------------------

def rollback(target_root: Path | str) -> dict:
    """Reverse a prior apply() by reading `.agentm-migrate-record.json`.

    Returns a summary dict:
      {
        "restored":    [list of rel_paths restored],
        "skipped":     [list of (rel_path, reason) tuples],
        "record_path": str,
      }

    Reverses each action in INVERSE order:
      - safe_to_migrate:           copy back from source_clone[source_path]
                                    to <target>/.claude/<rel_path>.
      - force_migrated:            move backup_path back to <target>/.claude/<rel_path>.
      - operator_edited_skipped:   no-op (file was never moved).

    On success: removes the record file + the backup directory.
    On partial failure: leaves record + backup in place; operator can rerun.

    Raises FileNotFoundError if no record exists.
    """
    target = Path(target_root)
    rp = _record_path(target)
    if not rp.exists():
        raise FileNotFoundError(
            f"no migration record at {rp}; nothing to rollback"
        )
    with rp.open() as f:
        record = json.load(f)
    restored: list[str] = []
    skipped: list[tuple[str, str]] = []
    # Reverse-order: undo last action first
    for action in reversed(record.get("actions", [])):
        kind = action.get("kind")
        rel = action.get("rel_path")
        if rel is None:
            continue
        dest = target / ".claude" / rel
        if kind == "safe_to_migrate":
            src = Path(action.get("source_path", ""))
            if not src.exists():
                skipped.append((rel, f"source path gone: {src}"))
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                # Refuse overwrite IRRESPECTIVE of file-or-dir — operator
                # may have re-staged something at the destination after
                # apply(). Symmetric across both branches so single-file
                # restore can never silently clobber operator content.
                if dest.exists() or dest.is_symlink():
                    skipped.append((rel, "target dest exists; not overwriting"))
                    continue
                if src.is_dir():
                    shutil.copytree(src, dest)
                else:
                    shutil.copy2(src, dest)
                restored.append(rel)
            except OSError as exc:
                skipped.append((rel, f"copy failed: {exc}"))
        elif kind == "force_migrated":
            backup_rel = action.get("backup_path")
            if not backup_rel:
                skipped.append((rel, "no backup_path in action record"))
                continue
            backup_path = target / backup_rel
            if not backup_path.exists():
                skipped.append((rel, f"backup gone: {backup_path}"))
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                # Symmetric refusal — backup-collision scenario (fix #3)
                # makes "dest already exists at rollback time" a reachable
                # production case. Single-file branch MUST guard too.
                if dest.exists() or dest.is_symlink():
                    skipped.append((rel, "target dest exists; not overwriting"))
                    continue
                if backup_path.is_dir():
                    shutil.copytree(backup_path, dest)
                    shutil.rmtree(backup_path)
                else:
                    shutil.copy2(backup_path, dest)
                    backup_path.unlink()
                restored.append(rel)
            except OSError as exc:
                skipped.append((rel, f"restore failed: {exc}"))
        # operator_edited_skipped + other kinds: no-op

    # If nothing skipped, remove the record + backup dir
    if not skipped:
        try:
            rp.unlink()
        except FileNotFoundError:
            pass
        bd = _backup_root(target)
        if bd.is_dir():
            try:
                shutil.rmtree(bd)
            except OSError:
                pass

    return {
        "restored": restored,
        "skipped": skipped,
        "record_path": str(rp),
    }


# -----------------------------------------------------------------------------
# cleanup()
# -----------------------------------------------------------------------------

def cleanup(target_root: Path | str) -> dict:
    """Opt-in destructive post-verification cleanup.

    Verifies the migration outcome + removes the now-empty per-project
    install dirs (`.claude/{skills,hooks,agents,commands}/`) if no
    operator-edited files remain.

    **Verification gate (strict)**: cleanup walks `<target>/.claude/{...}/`
    DIRECTLY (not via `_walk_target` — that walker filters by known shapes
    and would miss operator-dropped `.py` / `.txt` / no-extension files,
    silently destroying operator content. Cleanup's verification must be
    SHAPE-AGNOSTIC: refuse if ANY non-symlink content remains, regardless
    of file extension or kind). Symlinks are exempt (they're either
    pointing at source-clone canonical paths post-migration, or at
    user-scope post-prior-migration — both expected).

    Returns:
      {
        "removed":  [list of rel paths removed],
        "kept":     [list of rel paths kept (operator content)],
        "refused":  bool — True if verification failed; nothing was removed.
      }

    The cleanup keeps the `.agentm-migrate-record.json` + backup directory
    (operator may still want to rollback). Only the install subdirs are
    affected.
    """
    target = Path(target_root)
    claude_root = target / ".claude"
    if not claude_root.is_dir():
        return {"removed": [], "kept": [], "refused": False}

    # Shape-agnostic walk: enumerate EVERY entry under each install subdir
    # (excluding dotfile noise + symlinks). Refuse cleanup if anything
    # remains — operator's non-symlink content is sacred.
    kept_rels: list[str] = []
    for subdir in _INSTALL_SUBDIRS:
        sub_path = claude_root / subdir
        if not sub_path.is_dir():
            continue
        for child in sorted(sub_path.iterdir()):
            if child.name.startswith("."):
                continue
            # Symlinks are post-migration-expected; exempt.
            if child.is_symlink():
                continue
            # Real file OR real dir bundle present → operator content. Refuse.
            kept_rels.append(f"{subdir}/{child.name}")

    if kept_rels:
        return {"removed": [], "kept": kept_rels, "refused": True}

    # No leftover operator content — safe to remove the empty install subdirs.
    removed_rels: list[str] = []
    for subdir in _INSTALL_SUBDIRS:
        sub_path = claude_root / subdir
        if sub_path.is_dir():
            # Track which symlink entries existed (for reporting)
            for child in sorted(sub_path.iterdir()):
                if not child.name.startswith("."):
                    removed_rels.append(f"{subdir}/{child.name}")
            shutil.rmtree(sub_path)

    return {"removed": removed_rels, "kept": [], "refused": False}


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Classify / apply / rollback / cleanup per-project install migration.",
    )
    parser.add_argument("target_root", help="path to the project root to migrate")
    parser.add_argument(
        "--mode", choices=("classify", "apply", "rollback", "cleanup"),
        default="classify",
        help="operation to perform (default: classify = preview only)",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="shorthand for --mode=apply (with mutation)",
    )
    parser.add_argument(
        "--rollback", action="store_true",
        help="shorthand for --mode=rollback",
    )
    parser.add_argument(
        "--cleanup", action="store_true",
        help="shorthand for --mode=cleanup (destructive post-verification)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="migrate operator-edited files anyway (with backup for rollback)",
    )
    parser.add_argument(
        "--registry-slug", default=None,
        help="slug to record for downstream registry integration",
    )
    parser.add_argument(
        "--agentm", default=None,
        help="path to agentm source clone (default: ~/Antigravity/agentm)",
    )
    return parser


def _resolve_source_clones(args) -> dict[str, str]:
    """Resolve the --agentm arg via install_state.detect_source_clones."""
    try:
        from install_state import detect_source_clones
    except ImportError:  # pragma: no cover
        from .install_state import detect_source_clones  # type: ignore
    return detect_source_clones(args.agentm)


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Flag-shorthand → mode override
    mode = args.mode
    if args.apply:
        mode = "apply"
    if args.rollback:
        mode = "rollback"
    if args.cleanup:
        mode = "cleanup"

    target_root = Path(os.path.expanduser(args.target_root))
    if not target_root.is_dir():
        print(f"[install_migrate] target not a directory: {target_root}", file=sys.stderr)
        return 2

    if mode == "rollback":
        try:
            result = rollback(target_root)
        except FileNotFoundError as exc:
            print(f"[install_migrate] {exc}", file=sys.stderr)
            return 3
    elif mode == "cleanup":
        result = cleanup(target_root)
    else:
        source_clones = _resolve_source_clones(args)
        if not source_clones:
            print(
                "[install_migrate] no source clones detected; cannot classify",
                file=sys.stderr,
            )
            return 2
        dry_run = (mode == "classify")
        result = apply(
            target_root,
            source_clones=source_clones,
            dry_run=dry_run,
            force=args.force,
            registry_slug=args.registry_slug,
        )

    sys.stdout.write(json.dumps(result, indent=2, default=str) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
