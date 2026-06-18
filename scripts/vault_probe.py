#!/usr/bin/env python3
"""vault_probe — rank + refine candidate MemoryVault roots for the installer.

`install.sh`'s first-run vault detection (`_agentm_vault_first_run_prompt`,
macOS-only) runs a bounded `find` under `~/Library/CloudStorage` for two marker
kinds and must turn the hits into a sane, ranked list of candidate vault roots.
This module owns that path/filesystem logic so the bash stays thin and the
ranking is unit-testable on every OS.

Two marker kinds:

  ``*/_meta/repos.json``  — AUTHORITATIVE: "this IS the agent MemoryVault root".
                            Vault root = parent of ``_meta`` (dirname twice).
  ``*/.obsidian``         — WEAK: "this is an Obsidian app-vault" — which may
                            *wrap* the MemoryVault in a subfolder (e.g.
                            ``.../Obsidian/AgentMemory``). Vault root = parent
                            of ``.obsidian`` (dirname once).

v4.5.2 bug this fixes
---------------------
The probe used a flat ``-maxdepth 5`` find and treated both markers equally.
On a Google-Drive-shortcut-linked vault the real marker
``.../.shortcut-targets-by-id/<id>/Obsidian/AgentMemory/_meta/repos.json`` sits
at depth 7 — beyond the find's reach — while ``.../Obsidian/.obsidian`` (depth 5)
matched. So the parent Obsidian app-vault (`.../Obsidian`) was surfaced as the
only candidate and selected, splitting the operator's harness state across two
roots (recall went blind; state forked). See agentm FOLLOWUPS "Installer
vault-detection probe picks the wrong root".

Two-part fix, both here (bash keeps the shallow find — no deeper `-L` traversal,
which would risk symlink-loop hangs when no `timeout` binary is present):

  1. ``rank_candidates`` — repos.json roots rank FIRST; any ``.obsidian`` root
     that is an *ancestor of* (or equal to) a repos root is SUPPRESSED (it's the
     wrapping Obsidian vault; the real vault is the nested repos one); dedup;
     stable order.
  2. ``find_nested_vault`` — given a candidate root, descend ONE level into it:
     if the root itself lacks the vault shape but exactly one immediate child
     has it (``_meta/repos.json`` or a ``personal/`` dir), return that
     child. This recovers the deep-nested AgentMemory subfolder via its parent's
     shallow ``.obsidian`` hit, without deepening the find.

Stdlib-only. ``rank_candidates`` is pure path logic (no filesystem touch);
``find_nested_vault`` touches the filesystem (tmpdir-tested).
"""
from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath
from typing import Optional


# -----------------------------------------------------------------------------
# Pure path ranking (no filesystem access — deterministic, cross-platform tests)
# -----------------------------------------------------------------------------

def _vault_root_for_marker(marker: str) -> Optional[tuple[str, str]]:
    """Return ``(kind, vault_root)`` for a marker path, or None if unrecognized.

    ``kind`` is ``"repos"`` or ``"obsidian"``. Marker paths come from macOS
    ``find`` output, i.e. POSIX-style — parsed with ``PurePosixPath`` so the
    logic is identical on Windows CI runners.
    """
    m = marker.strip()
    if not m:
        return None
    p = PurePosixPath(m)
    if p.name == "repos.json" and p.parent.name == "_meta":
        # .../<vault>/_meta/repos.json → vault root is the parent of _meta.
        return ("repos", str(p.parent.parent))
    if p.name == ".obsidian":
        # .../<vault>/.obsidian → vault root is the parent.
        return ("obsidian", str(p.parent))
    return None


def _is_ancestor(ancestor: str, descendant: str) -> bool:
    """True iff ``ancestor`` is a strict parent directory of ``descendant``."""
    a = PurePosixPath(ancestor)
    d = PurePosixPath(descendant)
    if a == d:
        return False
    try:
        d.relative_to(a)
        return True
    except ValueError:
        return False


def rank_candidates(marker_paths: list[str]) -> list[dict]:
    """Turn marker paths into a ranked, deduped list of ``{"root", "kind"}``.

    Ranking: repos.json roots first (authoritative); ``.obsidian`` roots that
    wrap (are ancestors of, or equal to) any repos root are dropped; remaining
    ``.obsidian`` roots follow. Stable within each group by first-seen order.
    """
    repos_roots: list[str] = []
    obsidian_roots: list[str] = []
    for marker in marker_paths:
        res = _vault_root_for_marker(marker)
        if res is None:
            continue
        kind, root = res
        bucket = repos_roots if kind == "repos" else obsidian_roots
        if root not in bucket:
            bucket.append(root)

    # Suppress .obsidian roots that wrap (== or ancestor of) any repos root.
    kept_obsidian = [
        o for o in obsidian_roots
        if not any(o == r or _is_ancestor(o, r) for r in repos_roots)
    ]

    out: list[dict] = []
    for root in repos_roots:
        out.append({"root": root, "kind": "repos"})
    for root in kept_obsidian:
        out.append({"root": root, "kind": "obsidian"})
    return out


# -----------------------------------------------------------------------------
# Filesystem refinement (descend an Obsidian wrapper into its nested vault)
# -----------------------------------------------------------------------------

def _has_vault_shape(p: Path) -> bool:
    """True iff ``p`` looks like a MemoryVault root.

    Strongest signal: ``_meta/repos.json``. Fallback shape: a
    ``personal/`` dir (the always-load conventions live there).
    """
    try:
        if (p / "_meta" / "repos.json").is_file():
            return True
        if (p / "personal").is_dir():
            return True
    except OSError:
        return False
    return False


def find_nested_vault(root: str) -> str:
    """Return the real MemoryVault dir for a candidate ``root``.

    - If ``root`` itself has the vault shape → return ``root``.
    - Else if exactly ONE immediate child has the vault shape → return that
      child (recovers a nested ``Obsidian/AgentMemory`` from its parent).
    - Else (no child, or ambiguous >1) → return ``root`` unchanged.

    Never raises — returns ``root`` on any I/O error.
    """
    base = Path(root)
    try:
        if not base.is_dir():
            return root
        if _has_vault_shape(base):
            return str(base)
        nested = [c for c in sorted(base.iterdir()) if c.is_dir() and _has_vault_shape(c)]
    except OSError:
        return root
    if len(nested) == 1:
        return str(nested[0])
    return root


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="vault_probe",
        description="Rank + refine candidate MemoryVault roots from find(1) markers.",
    )
    parser.add_argument(
        "--rank", action="store_true",
        help="read marker paths from stdin (one per line); emit ranked vault "
             "roots (one per line) to stdout",
    )
    parser.add_argument(
        "--show-kind", action="store_true",
        help="with --rank, prefix each root with '<kind>\\t'",
    )
    parser.add_argument(
        "--refine", metavar="PATH", default=None,
        help="descend a candidate root one level into a nested MemoryVault if "
             "the root wraps one; print the refined path",
    )
    args = parser.parse_args(argv)

    if args.refine is not None:
        sys.stdout.write(find_nested_vault(args.refine) + "\n")
        return 0

    if args.rank:
        markers = sys.stdin.read().splitlines()
        for cand in rank_candidates(markers):
            if args.show_kind:
                sys.stdout.write(f"{cand['kind']}\t{cand['root']}\n")
            else:
                sys.stdout.write(cand["root"] + "\n")
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
