#!/usr/bin/env python3
"""repo_registry — seam-backed registry of agent-aware repos (V5-6).

The registry lives at `_meta/repos.json` in the active storage backend.
On the `obsidian-vault` backend this is `<vault>/_meta/repos.json` —
byte-identical to the pre-V5-6 location; on `device-local` it lives at
`~/.agentm/memory/_meta/repos.json`.

It holds **no run configuration** — how the harness runs (vault vs local state
mode) is on-host config in `.agentm-config.json` + the per-repo `.project-mode`
marker (locked DC-8), never in this backend-resident index.

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
the registry stores the path as-recorded; a later plan may introduce
per-host overrides if real-use surfaces the need.

Three CLI subcommands:

    list                — emit JSON listing of all registered repos
    register <slug>     — upsert a repo (root + wiki kwargs)
    unregister <slug>   — remove a repo (idempotent)

Graceful-skip:
- If the active backend is unavailable (select_backend() raises) → CLI
  exits 1 with `{"skipped": true, "reason": "..."}` JSON. On a fresh
  install with no vault configured, the device-local backend is always
  available (no skip). Set the vault via `agentm_config --vault-path <path>`
  or re-run `install.sh --scope user --force-vault-prompt`.

Stdlib-only (ADR 0001). Cross-platform via pathlib.

V5-6: re-plumbed onto the storage seam (LC-4). Per V4 #30 plan #22 task 2.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from storage_seam import Locator, StorageBackend

# Allow direct import of harness_memory (same scripts/ dir) for CAS utilities.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import harness_memory as hm  # noqa: E402


_REGISTRY_REL = "_meta/repos.json"
_REGISTRY_PARTS = ("_meta", "repos.json")
_SCHEMA_VERSION = 1


# -----------------------------------------------------------------------------
# Locator resolution
# -----------------------------------------------------------------------------

def registry_locator(backend: "StorageBackend") -> "Locator":
    """Return the Locator for the registry file in the given backend.

    Replaces the V5-5 `registry_path(vault_path) -> Path` — on the
    obsidian-vault backend the returned Locator maps to the same
    `<vault>/_meta/repos.json` as before (LC-1 behavior-preserving).
    """
    return backend.resolve(*_REGISTRY_PARTS)


def _backend_or_none() -> Optional["StorageBackend"]:
    """Return the active StorageBackend, or None if unavailable.

    Lazy-imports backend_selection to avoid circular imports. On a fresh
    install with no vault configured, returns the device-local backend
    (never None in normal operation — graceful-skip fires only when
    select_backend() itself raises, e.g., a missing required plugin).
    """
    try:
        import backend_selection as _bs  # noqa: E402
        return _bs.select_backend()
    except Exception:
        return None


# -----------------------------------------------------------------------------
# Read / write primitives
# -----------------------------------------------------------------------------

def read_registry(backend: "StorageBackend") -> dict:
    """Read the registry; return `{version, repos: [...]}`.

    First-write semantics: if the registry file doesn't exist, return an
    empty-but-valid registry `{version: 1, repos: []}` (does NOT create the
    file — write_registry is responsible for creation).

    Raises json.JSONDecodeError if the file exists but is malformed —
    caller should surface to operator; corruption is not auto-repaired.
    """
    loc = registry_locator(backend)
    if not backend.exists(loc):
        return {"version": _SCHEMA_VERSION, "repos": []}
    content = backend.read(loc)
    data = json.loads(content)
    data.setdefault("version", _SCHEMA_VERSION)
    data.setdefault("repos", [])
    return data


def write_registry(
    backend: "StorageBackend",
    data: dict,
    *,
    expected_hash: Optional[str] = None,
) -> "Locator":
    """Write the registry through the storage seam, with an optional CAS guard.

    When `expected_hash` is provided, the registry file's content is re-read
    before writing; if it differs (another process or device wrote it since
    the caller's read), ConcurrentModificationError is raised. The caller
    (`_mutate_registry`) retries after re-reading.

    Pattern for race-protected upsert:

        loc = registry_locator(backend)
        current_hash = (
            hm.content_hash(backend.read(loc).encode("utf-8"))
            if backend.exists(loc) else None
        )
        data = read_registry(backend)
        # mutate data ...
        write_registry(backend, data, expected_hash=current_hash)

    The backend's write verb handles atomic temp→fsync→rename and creates
    parent dirs if absent. For the vault backend, write() additionally holds
    the vault_mutex and does an inner CAS against same-device concurrent
    writers. Returns the written Locator.
    """
    data = dict(data)
    data.setdefault("version", _SCHEMA_VERSION)
    data.setdefault("repos", [])
    loc = registry_locator(backend)
    content = json.dumps(data, indent=2, sort_keys=False) + "\n"

    if expected_hash is not None:
        if backend.exists(loc):
            actual = hm.content_hash(backend.read(loc).encode("utf-8"))
            if actual != expected_hash:
                raise hm.ConcurrentModificationError(
                    f"registry was modified concurrently "
                    f"(expected hash={expected_hash[:12]}…, actual={actual[:12]}…). "
                    f"Re-read and re-apply changes."
                )
        else:
            raise hm.ConcurrentModificationError(
                f"registry was deleted since read "
                f"(expected hash={expected_hash[:12]}…). "
                f"Re-read and re-apply changes."
            )

    return backend.write(loc, content)


# -----------------------------------------------------------------------------
# High-level operations (upsert / list / unregister)
# -----------------------------------------------------------------------------

# A read-mutate-CAS upsert can lose its race: another writer lands between our
# read and our compare-and-swap, so write_registry raises
# ConcurrentModificationError. Five attempts is ample headroom — the vault
# backend's vault_mutex already serializes same-machine writers; a CAS miss
# only happens against a cross-machine peer on the synced vault (R4 gives no
# cross-device mutual exclusion), which is rare and self-clearing on re-read.
_MAX_REGISTRY_RETRIES = 5

# A mutate-fn returns this sentinel to mean "nothing changed — skip the write."
# Lets an idempotent no-op (unregistering an absent slug) avoid churning the
# registry mtime, which the storage seam reads as its `changed_since` basis.
_SKIP_WRITE: Any = object()


def _mutate_registry(
    backend: "StorageBackend",
    mutate: Callable[[dict], Any],
) -> Any:
    """Read-mutate-CAS the registry, retrying on a CAS miss.

    `mutate(data)` edits the freshly-read registry `data` in place and returns
    the caller's result — or the `_SKIP_WRITE` sentinel to skip the write.
    The CAS retry recovers from the one race that can't be prevented locally —
    a cross-machine peer on the synced vault — by re-reading and re-applying.
    After `_MAX_REGISTRY_RETRIES` consecutive collisions it raises rather than
    dropping the write, honoring write_registry's retry contract.

    V5-6: vault_mutex removed from this layer — the vault backend's write()
    verb holds it internally, so serialization is still correct. The outer
    CAS loop (via write_registry's expected_hash) handles cross-device races.
    """
    loc = registry_locator(backend)
    last_exc: Optional[BaseException] = None
    for _ in range(_MAX_REGISTRY_RETRIES):
        current_hash = (
            hm.content_hash(backend.read(loc).encode("utf-8"))
            if backend.exists(loc) else None
        )
        data = read_registry(backend)
        result = mutate(data)
        if result is _SKIP_WRITE:
            return result
        try:
            write_registry(backend, data, expected_hash=current_hash)
            return result
        except hm.ConcurrentModificationError as exc:
            last_exc = exc
    raise hm.ConcurrentModificationError(
        f"registry CAS lost {_MAX_REGISTRY_RETRIES} consecutive times — "
        f"a cross-machine writer keeps winning the race"
    ) from last_exc


def register_repo(
    backend: "StorageBackend",
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

    Concurrency: the read-mutate-CAS runs under `_mutate_registry`, which
    retries on a CAS miss — a concurrent registration on another machine
    can no longer silently drop this one.

    Path normalization: `root_path` is stored as POSIX-style (forward
    slashes) for vault portability (GDrive-synced, read by Mac/Linux +
    Windows clients).
    """
    if not slug:
        raise ValueError("slug must be non-empty")

    new_entry: dict[str, Any] = {"slug": slug, "root_path": Path(root_path).as_posix()}
    if wiki_path is not None:
        new_entry["wiki_path"] = Path(wiki_path).as_posix()

    def _apply(data: dict) -> dict:
        repos = data.get("repos", [])
        for i, entry in enumerate(repos):
            if entry.get("slug") == slug:
                merged = dict(entry)
                merged.update(new_entry)
                repos[i] = merged
                break
        else:
            repos.append(new_entry)
        data["repos"] = repos
        return data

    return _mutate_registry(backend, _apply)


def unregister_repo(backend: "StorageBackend", slug: str) -> bool:
    """Remove a repo entry by slug. Idempotent — returns True if removed,
    False if no matching slug existed.

    Re-reads + writes under `_mutate_registry`: content-hash CAS + bounded
    retry, so a concurrent writer can't drop the removal.
    """
    if not slug:
        raise ValueError("slug must be non-empty")

    def _apply(data: dict) -> Any:
        repos = data.get("repos", [])
        new_repos = [r for r in repos if r.get("slug") != slug]
        if len(new_repos) == len(repos):
            return _SKIP_WRITE
        data["repos"] = new_repos
        return True

    return _mutate_registry(backend, _apply) is True


def list_repos(backend: "StorageBackend") -> list[dict]:
    """Return the list of registered repos.

    Order: insertion order (the order entries were first registered).
    Stable across reads — register_repo's upsert preserves position.
    """
    data = read_registry(backend)
    return list(data.get("repos", []))


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _print_skip_and_exit() -> int:
    """Emit the graceful-skip JSON envelope on stdout, exit 1."""
    sys.stdout.write(json.dumps({
        "skipped": True,
        "reason": (
            "The active storage backend is unavailable (select_backend() failed). "
            "Install the configured backend plugin, or set vault_path via "
            "`python3 scripts/agentm_config.py --vault-path <path>` / "
            "`install.sh --scope user --force-vault-prompt`."
        ),
    }) + "\n")
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Seam-backed registry of agent-aware repos (V5-6).",
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

    backend = _backend_or_none()
    if backend is None:
        return _print_skip_and_exit()

    if args.cmd is None:
        parser.print_help()
        return 2

    if args.cmd == "list":
        repos = list_repos(backend)
        sys.stdout.write(json.dumps({"repos": repos}, indent=2) + "\n")
        return 0

    if args.cmd == "register":
        try:
            register_repo(
                backend,
                args.slug,
                args.root,
                wiki_path=args.wiki,
            )
        except ValueError as exc:
            print(f"[repo_registry] {exc}", file=sys.stderr)
            return 2
        sys.stdout.write(args.slug + "\n")
        return 0

    if args.cmd == "unregister":
        try:
            removed = unregister_repo(backend, args.slug)
        except ValueError as exc:
            print(f"[repo_registry] {exc}", file=sys.stderr)
            return 2
        sys.stdout.write(("removed" if removed else "noop") + "\n")
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
