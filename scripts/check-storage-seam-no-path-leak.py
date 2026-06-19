#!/usr/bin/env python3
"""check-storage-seam-no-path-leak.py — no pathlib.Path crosses the storage seam (V5-1/V5-6).

The storage seam exists so the memory engine learns no filesystem assumption: the
verbs return the seam's own ``Locator``/``Info`` types, never a ``pathlib.Path``.
If a verb returned a ``Path``, the engine would hold a filesystem handle and the
seam's whole point — "swap the backend, the engine doesn't notice" — would leak
away. This gate is the executable enforcement of that contract, in two passes:

**Pass 1 — storage-seam verbs** (V5-1): parses each ``storage_*.py`` file and
flags any seam **verb** whose *return annotation* references a path type.

**Pass 2 — routing mechanism functions** (V5-6): parses the routing layer files
(``harness_memory.py``, ``repo_registry.py``) and flags any **routing function**
whose *return annotation* references a path type. Tasks 1–3 of V5-6 re-pointed
these functions to return ``Locator``s / seam-typed results — this pass confirms
they never regress to returning a ``Path``.

Both passes target the *return annotation* specifically — the point a line-grep
can't reach: a routing function legitimately uses ``Path`` internally; only
*handing one back from the function* is the violation.

Scanned:
  Pass 1: ``<root>/scripts/storage_*.py`` (seam + backend modules)
  Pass 2: ``<root>/scripts/harness_memory.py``, ``<root>/scripts/repo_registry.py``

Usage:  python3 scripts/check-storage-seam-no-path-leak.py [--root DIR]
  --root DIR   scan DIR instead of the repo root — the negative test points the
               gate at a fixture tree carrying a deliberate Path-returning function.
Exit:   0  no function returns a path type (both surfaces are clean)
        1  a function returns a path type (a forbidden leak)
        2  setup error (root missing, or a source file won't parse)
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

# The seven seam verbs. A `def` with one of these names inside a `storage_*.py`
# file is, by construction of the seam surface, a storage verb.
VERBS = frozenset({"resolve", "read", "write", "list", "exists", "info", "mkdir"})

# The routing mechanism functions that must not return pathlib.Path (V5-6 tasks 1–3).
# These were re-pointed to return Locators / seam-typed results (dicts, lists, bool);
# naming them explicitly scopes the routing pass to what matters.
ROUTING_FUNCTIONS = frozenset({
    "resolve_project",
    "_vault_projects_dir",
    "registry_locator",
    "read_registry",
    "write_registry",
    "register_repo",
    "unregister_repo",
    "list_repos",
})

# Routing mechanism files that host the re-pointed functions (V5-6 tasks 1–3).
# Explicit list (not a glob) so the gate is narrowed to the known routing surface.
_ROUTING_FILENAMES = ("harness_memory.py", "repo_registry.py")

# Path-type names that must never appear in a verb's return annotation. Matched on
# the *attribute*/identifier name, so both bare `Path` and qualified `pathlib.Path`
# / `os.PathLike` are caught.
PATH_NAMES = frozenset(
    {"Path", "PurePath", "PosixPath", "WindowsPath", "PurePosixPath", "PureWindowsPath", "PathLike"}
)


def _annotation_mentions_path(node: ast.AST) -> bool:
    """True if the annotation subtree references any path type, however nested."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in PATH_NAMES:
            return True
        if isinstance(sub, ast.Attribute) and sub.attr in PATH_NAMES:
            return True
    return False


def _scan_source(
    text: str, label: str, *, names: frozenset[str] = VERBS, tag: str = "verb"
) -> list[str]:
    """Return one message per named function in ``text`` whose return annotation leaks a path."""
    tree = ast.parse(text, filename=label)
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in names:
            continue
        if node.returns is not None and _annotation_mentions_path(node.returns):
            ann = ast.unparse(node.returns)
            hits.append(f"{label}:{node.lineno}: {tag} {node.name!r} returns {ann!r}")
    return hits


def _storage_files(root: Path) -> list[Path]:
    scripts = root / "scripts"
    if not scripts.is_dir():
        return []
    return sorted(scripts.glob("storage_*.py"))


def _routing_files(root: Path) -> list[Path]:
    scripts = root / "scripts"
    if not scripts.is_dir():
        return []
    return [scripts / name for name in _ROUTING_FILENAMES if (scripts / name).is_file()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--root", default=None)
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else Path(__file__).resolve().parent.parent
    if not root.is_dir():
        print(f"check-storage-seam-no-path-leak: not a directory: {root}", file=sys.stderr)
        return 2

    hits: list[str] = []
    for f in _storage_files(root):
        try:
            hits.extend(_scan_source(f.read_text(encoding="utf-8"), str(f)))
        except SyntaxError as exc:  # a source file that won't parse is a setup error
            print(f"check-storage-seam-no-path-leak: cannot parse {f}: {exc}", file=sys.stderr)
            return 2

    for f in _routing_files(root):
        try:
            hits.extend(
                _scan_source(
                    f.read_text(encoding="utf-8"), str(f),
                    names=ROUTING_FUNCTIONS, tag="routing fn",
                )
            )
        except SyntaxError as exc:
            print(f"check-storage-seam-no-path-leak: cannot parse {f}: {exc}", file=sys.stderr)
            return 2

    if hits:
        print(
            "check-storage-seam-no-path-leak: a storage verb or routing function returns a path type —",
            file=sys.stderr,
        )
        for h in hits:
            print(f"    {h}", file=sys.stderr)
        print("", file=sys.stderr)
        print(
            "  Seam verbs must return the seam's own Locator/Info types, never a\n"
            "  pathlib.Path: that is what keeps a filesystem assumption from crossing\n"
            "  the seam to the engine. Internal Path use is fine — change the *return*\n"
            "  to a Locator (or Info), not the implementation.",
            file=sys.stderr,
        )
        return 1

    print("check-storage-seam-no-path-leak: clean — no Path crosses the seam.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
