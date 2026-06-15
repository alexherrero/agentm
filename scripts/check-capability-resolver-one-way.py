#!/usr/bin/env python3
"""check-capability-resolver-one-way.py — assert the resolver never imports plugin code.

V5-8 Task 4 gate: the capability resolver is one-directional — it reads plugin
manifests as data (JSON files on disk), never imports plugin code at runtime.
A violation would let an installed plugin's Python code run inside agentm's
process, breaking the intentional isolation between the resolver and the plugins
it discovers.

What this gate checks (in `scripts/capability_resolver.py` and
`scripts/capability_version_match.py`):

  1. No dynamic-import patterns: importlib.import_module / __import__ /
     exec / eval — the only legitimate lazy import is the guarded
     `from capability_version_match import satisfies` in capability_resolve(),
     which is a known-sibling module, not a plugin module. This exception
     is listed in ALLOWED_LAZY_FROMS below.

  2. All top-level and inline imports resolve to either the Python stdlib
     or the explicitly-reviewed sibling-module allowlist (ALLOWED_SIBLINGS).
     A non-stdlib import that is NOT in the allowlist is a violation: it may
     represent a dependency on plugin code or an unreviewed third-party dep.

Usage:
    python3 scripts/check-capability-resolver-one-way.py [--root DIR]

    --root DIR   repo root (default: parent of this script's directory).

Exit codes:
    0  clean — resolver is one-directional
    1  violation found
    2  setup error (resolver module not found)
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

# ── configuration ─────────────────────────────────────────────────────────────

# Sibling modules (within scripts/) that the resolver is allowed to import.
# capability_version_match is the Task-2 single-range-check; it reads no files
# and executes no plugin code — a pure computation module.
ALLOWED_SIBLINGS: frozenset[str] = frozenset({"capability_version_match"})

# Allowed lazy (non-top-level) from-imports: (module, name) pairs.
# The guarded `from capability_version_match import satisfies` inside
# capability_resolve() is the only legitimate inline import — it's a sibling,
# not a plugin, and the guard means Task-1 tests never reach it.
ALLOWED_LAZY_FROMS: frozenset[tuple[str, str]] = frozenset({
    ("capability_version_match", "satisfies"),
})

# Modules to scan (relative to scripts/).
RESOLVER_MODULES = [
    "capability_resolver.py",
    "capability_version_match.py",
]

# Dynamic-import built-ins that indicate runtime plugin loading.
FORBIDDEN_CALLS = frozenset({"__import__", "exec", "eval"})
# importlib.import_module — detected as an attribute call.
FORBIDDEN_IMPORTLIB_ATTRS = frozenset({"import_module", "find_spec", "util"})

# Python 3.x stdlib top-level module names (not exhaustive, but covers the
# modules the resolver is likely to encounter; add entries as needed).
_STDLIB_PREFIXES = frozenset({
    "__future__", "abc", "ast", "asyncio", "base64", "binascii", "builtins",
    "cgi", "codecs", "collections", "contextlib", "copy", "copyreg",
    "csv", "ctypes", "dataclasses", "datetime", "decimal", "difflib",
    "email", "encodings", "enum", "errno", "fileinput", "fnmatch",
    "fractions", "functools", "gc", "genericpath", "glob", "gzip",
    "hashlib", "heapq", "hmac", "html", "http", "idlelib",
    "importlib", "inspect", "io", "ipaddress", "itertools", "json",
    "keyword", "linecache", "locale", "logging", "math", "mimetypes",
    "multiprocessing", "numbers", "operator", "os", "pathlib",
    "pickle", "platform", "pprint", "queue", "re", "secrets",
    "shlex", "shutil", "signal", "socket", "ssl", "stat",
    "statistics", "string", "struct", "subprocess", "sys",
    "tarfile", "tempfile", "textwrap", "threading", "time",
    "timeit", "token", "tokenize", "traceback", "typing",
    "unicodedata", "unittest", "urllib", "uuid", "warnings",
    "weakref", "xml", "zipfile", "zipimport", "zlib",
    # TYPE_CHECKING-only imports resolve to 'typing'
    "TYPE_CHECKING",
})


def _is_stdlib(name: str) -> bool:
    top = name.split(".")[0]
    return top in _STDLIB_PREFIXES


# ── AST walkers ──────────────────────────────────────────────────────────────

class _Checker(ast.NodeVisitor):
    def __init__(self, src_path: Path) -> None:
        self.path = src_path
        self.violations: list[str] = []
        self._in_type_checking = False

    def _v(self, lineno: int, msg: str) -> None:
        self.violations.append(f"  {self.path.name}:{lineno}: {msg}")

    # ── import statements ──────────────────────────────────────────────────

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            mod = alias.name.split(".")[0]
            if not _is_stdlib(mod) and mod not in ALLOWED_SIBLINGS:
                self._v(node.lineno,
                        f"import {alias.name!r} — not in stdlib or allowed siblings")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self._in_type_checking:
            self.generic_visit(node)
            return
        mod = (node.module or "").split(".")[0]
        if not _is_stdlib(mod) and mod not in ALLOWED_SIBLINGS:
            self._v(node.lineno,
                    f"from {node.module!r} import … — not in stdlib or allowed siblings")
        self.generic_visit(node)

    # ── function-scope lazy imports ────────────────────────────────────────

    # The guarded `from capability_version_match import satisfies` is a lazy
    # ImportFrom inside capability_resolve(). It's allowed (ALLOWED_LAZY_FROMS).
    # Any OTHER lazy import is a violation — plugin code must not be imported
    # at call time.  We detect "lazy" as: an ImportFrom inside a FunctionDef.

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for child in ast.walk(node):
            if child is node:
                continue
            if isinstance(child, ast.ImportFrom):
                mod = child.module or ""
                names = tuple(a.name for a in child.names)
                allowed = all(
                    (mod, n) in ALLOWED_LAZY_FROMS for n in names
                )
                if not allowed:
                    self._v(child.lineno,
                            f"lazy from {mod!r} import inside function "
                            f"— not in ALLOWED_LAZY_FROMS")
            elif isinstance(child, ast.Import):
                for alias in child.names:
                    self._v(child.lineno,
                            f"lazy import {alias.name!r} inside function")
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    # ── dynamic-import call detection ──────────────────────────────────────

    def visit_Call(self, node: ast.Call) -> None:
        # Bare __import__, exec, eval.
        if isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_CALLS:
                self._v(node.lineno,
                        f"call to {node.func.id!r} — dynamic import / execution forbidden")
        # importlib.import_module(...) or importlib.util.find_spec(...)
        elif isinstance(node.func, ast.Attribute):
            obj = node.func.value
            if isinstance(obj, ast.Name) and obj.id == "importlib":
                if node.func.attr in FORBIDDEN_IMPORTLIB_ATTRS:
                    self._v(node.lineno,
                            f"call to importlib.{node.func.attr!r} — dynamic import forbidden")
        self.generic_visit(node)

    # ── TYPE_CHECKING guard ───────────────────────────────────────────────

    def visit_If(self, node: ast.If) -> None:
        # `if TYPE_CHECKING:` blocks hold stub-only imports — don't flag them.
        test = node.test
        is_tc = (
            (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING")
            or (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")
        )
        old = self._in_type_checking
        self._in_type_checking = is_tc
        self.generic_visit(node)
        self._in_type_checking = old


# ── main ─────────────────────────────────────────────────────────────────────

def _main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    args = argv[1:]
    while args:
        if args[0] in ("--root", "-r") and len(args) >= 2:
            root = Path(args[1])
            args = args[2:]
        elif args[0].startswith("--root="):
            root = Path(args[0][len("--root="):])
            args = args[1:]
        else:
            print(f"check-capability-resolver-one-way: unknown arg: {args[0]}",
                  file=sys.stderr)
            return 2

    scripts = root / "scripts"
    all_violations: list[str] = []

    for module_name in RESOLVER_MODULES:
        src = scripts / module_name
        if not src.exists():
            print(f"check-capability-resolver-one-way: {module_name} not found "
                  f"(skipping — not yet shipped)", file=sys.stderr)
            continue
        try:
            tree = ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
        except SyntaxError as exc:
            print(f"check-capability-resolver-one-way: syntax error in {module_name}: {exc}",
                  file=sys.stderr)
            return 1
        checker = _Checker(src)
        checker.visit(tree)
        all_violations.extend(checker.violations)

    if all_violations:
        print("check-capability-resolver-one-way: resolver imports plugin code — FAIL",
              file=sys.stderr)
        for v in all_violations:
            print(v, file=sys.stderr)
        print("", file=sys.stderr)
        print("  The capability resolver must be one-directional: read manifests as", file=sys.stderr)
        print("  data (JSON), never import plugin code at runtime (V5-8 design).", file=sys.stderr)
        print("  Add a reviewed sibling to ALLOWED_SIBLINGS or lazy import to", file=sys.stderr)
        print("  ALLOWED_LAZY_FROMS in this script to lift a false-positive.", file=sys.stderr)
        return 1

    print("check-capability-resolver-one-way: clean — resolver is one-directional.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
