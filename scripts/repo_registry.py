#!/usr/bin/env python3
"""repo_registry — vault-backed registry of agent-aware repos.

The registry lives at `<vault>/_meta/repos.json` and is a cross-device **index**
of the operator's known agent-aware repos: their slug, root filesystem path, and
wiki path (if applicable). Cross-device naturally — the vault is GDrive-synced, so
all machines that share the vault see the same registry.

It holds **no run configuration** — how the harness runs (vault vs local state
mode) is on-host config in `.agentm-config.json` + the per-repo `.project-mode`
marker (locked DC-8), never in this vault-resident index.

Schema (v1):

    {
      "version": 1,
      "repos": [
        {
          "slug": "agentm",
          "root_path": "/srv/projects/agentm",
          "wiki_path": "/srv/projects/agentm/wiki"            // optional
        },
        ...
      ]
    }

Per-host root paths differ across operator machines (e.g. Unix-style
absolute paths on macOS/Linux vs Windows-style paths on Windows). For v1,
the registry stores the path as-recorded; a later plan (V4 #30 plan 2 or 3)
may introduce per-host overrides if real-use surfaces the need.

Three CLI subcommands:

    list                — emit JSON listing of all registered repos
    register <slug>     — upsert a repo (root + wiki kwargs)
    unregister <slug>   — remove a repo (idempotent)

Graceful-skip:
- If `$MEMORY_VAULT_PATH` env unset AND `vault_path` absent from
  `<install-prefix>/.agentm-config.json` (or any resolved path's directory
  missing) → CLI exits 1 with `{"skipped": true, "reason": "..."}` JSON;
  primitives return None on read, raise on write. Set the path via
  `python3 scripts/agentm_config.py --vault-path <path>` or re-run
  `install.sh --scope user --force-vault-prompt`.

Stdlib-only (ADR 0001). Cross-platform via pathlib.

Per V4 #30 plan #22 task 2.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional

# Allow direct import of harness_memory (same scripts/ dir) for atomic-write
# primitives. Mirrors the pattern harness_memory itself uses for vault_project.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import harness_memory as hm  # noqa: E402


_REGISTRY_REL = "_meta/repos.json"
_SCHEMA_VERSION = 1


# -----------------------------------------------------------------------------
# Path resolution
# -----------------------------------------------------------------------------

def registry_path(vault_path: Path | str) -> Path:
    """Return the registry file path under the given vault.

    Pure path construction; does not check existence.
    """
    return Path(vault_path) / _REGISTRY_REL


def _vault_or_none() -> Optional[Path]:
    """Return MEMORY_VAULT_PATH if accessible, else None.

    Mirrors hm.vault_path() semantics — the directory must exist for the
    path to be returned.
    """
    return hm.vault_path()


# -----------------------------------------------------------------------------
# Read / write primitives
# -----------------------------------------------------------------------------

def read_registry(vault_path: Path | str) -> dict:
    """Read the registry; return `{version, repos: [...]}`.

    First-write semantics: if the registry file doesn't exist, return an
    empty-but-valid registry `{version: 1, repos: []}` (does NOT create the
    file — write_registry is responsible for creation).

    Raises FileNotFoundError if the vault directory itself doesn't exist
    (different failure mode from missing-registry-file).

    Raises json.JSONDecodeError if the file exists but is malformed —
    caller should surface to operator; corruption is not auto-repaired.
    """
    vault = Path(vault_path)
    if not vault.is_dir():
        raise FileNotFoundError(f"vault path does not exist: {vault}")
    path = registry_path(vault)
    if not path.is_file():
        return {"version": _SCHEMA_VERSION, "repos": []}
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    # Schema migration shim: if no "version" field, treat as v1
    data.setdefault("version", _SCHEMA_VERSION)
    data.setdefault("repos", [])
    return data


def write_registry(
    vault_path: Path | str,
    data: dict,
    *,
    expected_hash: Optional[str] = None,
) -> Path:
    """Write the registry atomically.

    Uses `harness_memory.safe_write_replace_style` for atomic temp→fsync→rename
    + an optional content-hash compare-and-swap via `expected_hash` (V5-0; the
    R4 rule-4 currency that replaces the weaker mtime check).

    Pattern for race-protected upsert:

        path = registry_path(vault)
        current_hash = hm.content_hash(path.read_bytes()) if path.exists() else None
        data = read_registry(vault)
        # mutate data ...
        write_registry(vault, data, expected_hash=current_hash)

    Raises ConcurrentModificationError (re-exported from harness_memory) if
    another process wrote between read + write. The read-mutate-CAS callers
    (`register_repo` / `unregister_repo`) drive the retry via `_mutate_registry`
    — they re-read, re-apply, and re-attempt rather than dropping the change.

    Creates parent dir (`<vault>/_meta/`) if absent. Returns the written path.
    """
    vault = Path(vault_path)
    if not vault.is_dir():
        raise FileNotFoundError(f"vault path does not exist: {vault}")
    # Ensure version field — operator should never have to think about it
    data.setdefault("version", _SCHEMA_VERSION)
    data.setdefault("repos", [])
    path = registry_path(vault)
    content = json.dumps(data, indent=2, sort_keys=False) + "\n"
    return hm.safe_write_replace_style(path, content, expected_hash=expected_hash)


# -----------------------------------------------------------------------------
# High-level operations (upsert / list / unregister)
# -----------------------------------------------------------------------------

# A read-mutate-CAS upsert can lose its race: another writer lands between our
# read and our compare-and-swap, so write_registry raises
# ConcurrentModificationError. Five attempts is ample headroom — the vault_mutex
# already serializes same-machine writers, so a CAS miss only happens against a
# *cross-machine* peer on the synced vault (R4 gives no cross-device mutual
# exclusion), which is rare and self-clearing on re-read.
_MAX_REGISTRY_RETRIES = 5

# A mutate-fn returns this sentinel to mean "nothing changed — skip the write."
# Lets an idempotent no-op (unregistering an absent slug) avoid churning the
# registry mtime, which the storage seam reads as its `changed_since` basis.
_SKIP_WRITE: Any = object()


def _mutate_registry(
    vault_path: Path | str,
    mutate: Callable[[dict], Any],
) -> Any:
    """Read-mutate-CAS the registry under the per-vault mutex, retrying on a CAS
    miss so a concurrent writer can't silently drop the change.

    `mutate(data)` edits the freshly-read registry `data` in place and returns
    the caller's result — or the `_SKIP_WRITE` sentinel to skip the write
    entirely. The `vault_mutex` serializes same-machine writers (the vault
    floor's stack); the bounded retry recovers from the one race it can't
    prevent — a cross-machine peer on the synced vault — by re-reading and
    re-applying. After `_MAX_REGISTRY_RETRIES` consecutive collisions it raises
    rather than drop the write, honoring write_registry's retry contract instead
    of leaving it fiction.
    """
    vault = Path(vault_path)
    path = registry_path(vault)
    last_exc: Optional[BaseException] = None
    with hm.vault_mutex(vault):
        for _ in range(_MAX_REGISTRY_RETRIES):
            current_hash = hm.content_hash(path.read_bytes()) if path.exists() else None
            data = read_registry(vault)
            result = mutate(data)
            if result is _SKIP_WRITE:
                return result
            try:
                write_registry(vault, data, expected_hash=current_hash)
                return result
            except hm.ConcurrentModificationError as exc:
                last_exc = exc
    raise hm.ConcurrentModificationError(
        f"registry CAS lost {_MAX_REGISTRY_RETRIES} consecutive times on {path} "
        f"— a cross-machine writer keeps winning the race"
    ) from last_exc


def register_repo(
    vault_path: Path | str,
    slug: str,
    root_path: str | Path,
    *,
    wiki_path: Optional[str | Path] = None,
) -> dict:
    """Upsert a repo entry into the registry.

    If `slug` already exists, the entry is updated in-place (preserving
    other fields not passed here — kwargs-only-update semantics). If the
    slug doesn't exist, a new entry is appended.

    Returns the updated registry dict (after write).

    Concurrency: the read-mutate-CAS runs under `_mutate_registry`, which holds
    the per-vault mutex and retries on a CAS miss — a concurrent registration on
    another machine can no longer silently drop this one.

    Path normalization: `root_path` is stored as a string (str(Path(value)))
    to preserve operator's home-relative paths verbatim. Cross-platform:
    caller decides whether to pass absolute or symlink-resolved paths.
    """
    if not slug:
        raise ValueError("slug must be non-empty")

    # Build the entry — only include fields that have values (avoid writing nulls).
    # Use POSIX-style paths (forward slashes) for vault portability: the registry
    # is GDrive-synced and read by both Mac/Linux + Windows clients; native
    # separators on one would break path semantics on the other.
    new_entry: dict[str, Any] = {"slug": slug, "root_path": Path(root_path).as_posix()}
    if wiki_path is not None:
        new_entry["wiki_path"] = Path(wiki_path).as_posix()

    def _apply(data: dict) -> dict:
        repos = data.get("repos", [])
        # Upsert: replace existing entry by slug, or append new.
        for i, entry in enumerate(repos):
            if entry.get("slug") == slug:
                # Merge: existing fields preserved unless explicitly overwritten.
                merged = dict(entry)
                merged.update(new_entry)
                repos[i] = merged
                break
        else:
            repos.append(new_entry)
        data["repos"] = repos
        return data

    return _mutate_registry(vault_path, _apply)


def unregister_repo(vault_path: Path | str, slug: str) -> bool:
    """Remove a repo entry by slug. Idempotent — returns True if removed,
    False if no matching slug existed.

    Re-reads + writes under `_mutate_registry`: per-vault mutex + content-hash
    CAS + bounded retry, so a concurrent writer can't drop the removal.
    """
    if not slug:
        raise ValueError("slug must be non-empty")

    def _apply(data: dict) -> Any:
        repos = data.get("repos", [])
        new_repos = [r for r in repos if r.get("slug") != slug]
        if len(new_repos) == len(repos):
            # No change — skip write entirely (preserves mtime; no churn).
            return _SKIP_WRITE
        data["repos"] = new_repos
        return True

    return _mutate_registry(vault_path, _apply) is True


def list_repos(vault_path: Path | str) -> list[dict]:
    """Return the list of registered repos.

    Order: insertion order (the order entries were first registered).
    Stable across reads — register_repo's upsert preserves position.
    """
    data = read_registry(vault_path)
    return list(data.get("repos", []))


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _print_skip_and_exit() -> int:
    """Emit the graceful-skip JSON envelope on stdout, exit 1."""
    sys.stdout.write(json.dumps({
        "skipped": True,
        "reason": (
            "MEMORY_VAULT_PATH unset AND no vault_path in .agentm-config.json "
            "(or resolved directory missing). Run "
            "`python3 scripts/agentm_config.py --vault-path <path>` to set, "
            "or re-run `install.sh --scope user --force-vault-prompt`."
        ),
    }) + "\n")
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Vault-backed registry of agent-aware repos.",
    )
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list", help="list registered repos (JSON)")

    p_reg = sub.add_parser("register", help="register or update a repo")
    p_reg.add_argument("slug", help="project slug (e.g. agentm)")
    p_reg.add_argument("--root", required=True, help="root filesystem path")
    p_reg.add_argument("--wiki", default=None, help="wiki path (optional)")

    p_unreg = sub.add_parser("unregister", help="remove a repo by slug")
    p_unreg.add_argument("slug", help="project slug")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    vault = _vault_or_none()
    if vault is None:
        return _print_skip_and_exit()

    if args.cmd is None:
        parser.print_help()
        return 2

    if args.cmd == "list":
        repos = list_repos(vault)
        sys.stdout.write(json.dumps({"repos": repos}, indent=2) + "\n")
        return 0

    if args.cmd == "register":
        try:
            register_repo(
                vault,
                args.slug,
                args.root,
                wiki_path=args.wiki,
            )
        except ValueError as exc:
            print(f"[repo_registry] {exc}", file=sys.stderr)
            return 2
        # Echo the registered slug for chainability.
        sys.stdout.write(args.slug + "\n")
        return 0

    if args.cmd == "unregister":
        try:
            removed = unregister_repo(vault, args.slug)
        except ValueError as exc:
            print(f"[repo_registry] {exc}", file=sys.stderr)
            return 2
        # Idempotent: exit 0 either way; stdout indicates whether action occurred.
        sys.stdout.write(("removed" if removed else "noop") + "\n")
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
