#!/usr/bin/env python3
"""Gate: no hardcoded MemoryVault path literals in non-test repo files.

Fails if any non-test file under the repo root embeds:

  (A) an absolute /Library/CloudStorage/ path — the Mac-specific cloud-drive
      mount that encodes the user's account name and machine-local drive ID.
      Shell tilde-expansions (~/Library/CloudStorage) and variable expansions
      ($HOME/Library/CloudStorage) are not literals and are excluded.

  (B) the retired pre-V5-3 vault root name as a path component:
      /Obsidian/AgentMemory — renamed to /Obsidian/Agent at v5.5.0. Only the
      slash-prefixed form is flagged (prose references like ``Obsidian/AgentMemory``
      in backtick notation without a path prefix are not literals).

Agents must resolve the vault path at runtime (harness_memory.vault_path() or
$MEMORY_VAULT_PATH) — never cache an absolute path as a constant or config value.

Exit:
  0  clean
  1  violations found
  2  setup error (root missing)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


# ── exclusions ────────────────────────────────────────────────────────────────

# The gate script itself contains the literal patterns as string comparisons.
# Skip it to avoid a vacuous self-flag. Test fixtures are excluded by prefix.
_SKIP_NAMES: frozenset[str] = frozenset({Path(__file__).name})
_SKIP_PREFIXES = ("test_",)

# File extensions to scan
_EXTENSIONS = frozenset({
    ".py", ".sh", ".md", ".txt", ".yml", ".yaml", ".json", ".ps1",
})

# Directories to prune from the walk entirely
_SKIP_DIRS = frozenset({".git", "__pycache__", ".harness", "node_modules"})


# ── file discovery ────────────────────────────────────────────────────────────

def _walk(root: Path):
    """Yield scannable files under root, skipping hidden and runtime dirs."""
    for dirpath, dirs, files in os.walk(root):
        dp = Path(dirpath)
        dirs[:] = [
            d for d in dirs
            if d not in _SKIP_DIRS and not d.startswith(".")
        ]
        for name in files:
            fpath = dp / name
            if _should_skip(fpath):
                continue
            yield fpath


def _should_skip(path: Path) -> bool:
    name = path.name
    if name in _SKIP_NAMES:
        return True
    if any(name.startswith(pfx) for pfx in _SKIP_PREFIXES):
        return True
    return path.suffix not in _EXTENSIONS


# ── pattern checks ────────────────────────────────────────────────────────────

_PLACEHOLDER_MARKERS = ("~", "...", "<", "…", "*")  # … = …


def _is_absolute_vault_path(line: str) -> bool:
    """True when line embeds /Library/CloudStorage/ as a literal absolute path."""
    if "/Library/CloudStorage/" not in line:
        return False
    # Shell tilde and variable expansions are runtime resolutions, not literals.
    if "~/Library/CloudStorage" in line:
        return False
    if "$HOME/Library/CloudStorage" in line or "${HOME}/Library/CloudStorage" in line:
        return False
    # Placeholder / example notation is documentation, not a cached literal.
    return not any(m in line for m in _PLACEHOLDER_MARKERS)


def _is_retired_vault_name(line: str) -> bool:
    """True when line uses the retired /Obsidian/AgentMemory path component."""
    if "/Obsidian/AgentMemory" not in line:
        return False
    return not any(m in line for m in _PLACEHOLDER_MARKERS)


# ── main ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument(
        "--root", default=None,
        help="Repo root to scan (default: inferred from script location)",
    )
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else Path(__file__).resolve().parent.parent
    if not root.is_dir():
        print(
            f"check-no-hardcoded-vault-path: not a directory: {root}",
            file=sys.stderr,
        )
        return 2

    violations: list[str] = []

    for fpath in _walk(root):
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError):
            continue

        try:
            rel = fpath.relative_to(root)
        except ValueError:
            rel = fpath

        for lineno, line in enumerate(text.splitlines(), 1):
            if _is_absolute_vault_path(line):
                violations.append(
                    f"  {rel}:{lineno} [absolute-vault-path]  {line.strip()[:120]}"
                )
            elif _is_retired_vault_name(line):
                violations.append(
                    f"  {rel}:{lineno} [retired-vault-name]  {line.strip()[:120]}"
                )

    if not violations:
        return 0

    n = len(violations)
    print(
        f"check-no-hardcoded-vault-path: {n} violation(s) — "
        "vault paths must be resolved at runtime, not cached as literals.",
        file=sys.stderr,
    )
    print(
        "  Resolve via harness_memory.vault_path() or the $MEMORY_VAULT_PATH env var.",
        file=sys.stderr,
    )
    print("  See AGENTS.md § Vault-path convention.", file=sys.stderr)
    for v in violations:
        print(v, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
