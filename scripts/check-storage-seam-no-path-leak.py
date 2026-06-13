#!/usr/bin/env python3
"""check-storage-seam-no-path-leak.py — no pathlib.Path crosses the storage seam (V5-1).

The storage seam exists so the memory engine learns no filesystem assumption: the
verbs return the seam's own ``Locator``/``Info`` types, never a ``pathlib.Path``.
If a verb returned a ``Path``, the engine would hold a filesystem handle and the
seam's whole point — "swap the backend, the engine doesn't notice" — would leak
away. This gate is the executable enforcement of that contract.

It is *static* (AST, not import): it parses each storage-seam source file and
flags any seam **verb** whose *return annotation* references a path type
(``Path``, ``PurePath``, ``PosixPath``, ``WindowsPath``, ``os.PathLike``, or a
``pathlib.``-qualified form), anywhere in the annotation — bare, or nested like
``list[Path]`` / ``Path | None`` / ``Optional[Path]``. Targeting the *return
annotation* specifically is the point a line-grep can't reach: a filesystem
backend (parts 2 / 4) legitimately uses ``Path`` internally (``root / key``);
only *handing one back across the seam* is the violation.

Scanned: ``<root>/scripts/storage_*.py`` — the seam contract module today, and the
concrete backend modules that adopt the convention as they land. ``test_*.py`` is
never matched by that glob, so the conformance fixtures are out of scope. The
verb set is the seven seam verbs; a same-named helper elsewhere is not a concern
because the glob already narrows scanning to the seam surface.

Usage:  python3 scripts/check-storage-seam-no-path-leak.py [--root DIR]
  --root DIR   scan DIR instead of the repo root — the negative test points the
               gate at a fixture tree carrying a deliberate Path-returning verb.
Exit:   0  no verb returns a path type (the seam is clean)
        1  a verb returns a path type (a forbidden leak)
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


def _scan_source(text: str, label: str) -> list[str]:
    """Return one message per verb in ``text`` whose return annotation leaks a path."""
    tree = ast.parse(text, filename=label)
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in VERBS:
            continue
        if node.returns is not None and _annotation_mentions_path(node.returns):
            ann = ast.unparse(node.returns)
            hits.append(f"{label}:{node.lineno}: verb {node.name!r} returns {ann!r}")
    return hits


def _storage_files(root: Path) -> list[Path]:
    scripts = root / "scripts"
    if not scripts.is_dir():
        return []
    return sorted(scripts.glob("storage_*.py"))


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

    if hits:
        print(
            "check-storage-seam-no-path-leak: a storage verb returns a path type —",
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
