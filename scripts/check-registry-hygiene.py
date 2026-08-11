#!/usr/bin/env python3
"""check-registry-hygiene — no throwaway path may be registered as a real repo.

`repo_registry.register_repo` writes into the live vault at
`<memory_root>/_meta/repos.json`, and a test that builds a fixture repo in a
temp directory and registers it leaves an entry whose `root_path` is dead the
moment the test exits. Three such entries accumulated by 2026-08-10 —
`novault-marker`, `redetect-cli`, `redetect-demo` — because the tests that made
them popped `$MEMORY_VAULT_PATH` to simulate "no vault" without redirecting
`$AGENTM_INSTALL_PREFIX`, so `vault_path()` fell through to the operator's real
config and resolved the real vault.

The cost is not the junk entries themselves. It is that they re-appear on every
test run, so `Agent/_meta/repos.json` is permanently modified-but-uncommitted —
and because the daemon commits markdown only, nothing ever clears it and
`agentmd gate corpus-write` stays shut. A gate held closed by unrelated churn
gets worked around, and then it protects nothing.

This makes the leak loud at the moment it happens instead of the next time
someone wonders why the gate refuses. Tests should use the
`no_vault_configured()` helper in `scripts/test_project_config.py`, which
redirects both variables.

Usage:
    python3 scripts/check-registry-hygiene.py [--registry PATH]

Exit 0 when clean or when there is nothing to check, 1 when a throwaway path is
registered, 2 on a malformed registry.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def _temp_roots() -> list[Path]:
    """Directories a throwaway fixture is built under, both spellings.

    macOS resolves `/var` to `/private/var`, and `tempfile.gettempdir()` returns
    whichever form `$TMPDIR` carries, so a path is compared against both the
    literal and the fully-resolved root.
    """
    roots: list[Path] = []
    for raw in (tempfile.gettempdir(), "/tmp", "/var/folders", "/private/var/folders"):
        p = Path(raw)
        for candidate in (p, Path(os.path.realpath(p))):
            if candidate not in roots:
                roots.append(candidate)
    return roots


def _is_throwaway(root_path: str, temp_roots: list[Path]) -> bool:
    if not root_path:
        return False
    try:
        resolved = Path(os.path.realpath(os.path.expanduser(root_path)))
    except (OSError, ValueError):
        return False
    candidates = {Path(os.path.expanduser(root_path)), resolved}
    for cand in candidates:
        for root in temp_roots:
            try:
                cand.relative_to(root)
                return True
            except ValueError:
                continue
    return False


def _default_registry() -> Path | None:
    """The live registry, resolved — never a cached literal (AGENTS.md)."""
    try:
        import harness_memory as hm  # noqa: WPS433
    except ImportError:
        return None
    root = hm.memory_root()
    return None if root is None else root / "_meta" / "repos.json"


def check(registry: Path | None) -> int:
    if registry is None:
        print("check-registry-hygiene: no vault resolves; nothing to check")
        return 0
    if not registry.is_file():
        print(f"check-registry-hygiene: no registry at {registry}; nothing to check")
        return 0
    try:
        data = json.loads(registry.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        print(f"check-registry-hygiene: cannot read {registry}: {exc}", file=sys.stderr)
        return 2

    if isinstance(data, dict):
        repos = data.get("repos")
    elif isinstance(data, list):
        repos = data
    else:
        repos = None
    if repos is None:
        print(f"check-registry-hygiene: {registry} has no `repos` list", file=sys.stderr)
        return 2
    if not isinstance(repos, list):
        print(f"check-registry-hygiene: `repos` in {registry} is not a list", file=sys.stderr)
        return 2

    temp_roots = _temp_roots()
    leaked = [
        (r.get("slug") or "(no slug)", r.get("root_path") or "")
        for r in repos
        if isinstance(r, dict) and _is_throwaway(r.get("root_path") or "", temp_roots)
    ]

    if not leaked:
        print(f"check-registry-hygiene: clean ({len(repos)} repo(s), none throwaway)")
        return 0

    print("check-registry-hygiene: a test registered a throwaway path as a real repo",
          file=sys.stderr)
    for slug, path in leaked:
        print(f"  {slug} -> {path}", file=sys.stderr)
    print(
        "\nThese paths stopped existing when the test that made them exited. They "
        "re-appear on every run, so the registry is permanently dirty — and since "
        "the daemon commits markdown only, nothing clears it and `agentmd gate "
        "corpus-write` stays shut.\n"
        "Fix the test to resolve against its own install prefix (see "
        "`no_vault_configured()` in scripts/test_project_config.py — popping "
        "$MEMORY_VAULT_PATH alone is not enough, $AGENTM_INSTALL_PREFIX has to "
        "move too), then remove the entries above from the registry.",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--registry", metavar="PATH", default=None,
                    help="registry to check (default: <memory_root>/_meta/repos.json)")
    args = ap.parse_args(argv)
    registry = Path(args.registry) if args.registry else _default_registry()
    return check(registry)


if __name__ == "__main__":
    sys.exit(main())
