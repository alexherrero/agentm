#!/usr/bin/env python3
"""check-opinion-resolver-one-way.py — assert the resolver never imports plugin code.

agentm-opinion-registry.md Enforcement §1: `opinion_resolver.py` discovers
Opinions by reading `opinions/*.md` frontmatter as data — it never imports
design/plugin code at runtime. Mirrors check-capability-resolver-one-way.py
and check-governs-resolver-one-way.py exactly: an AST scan forbidding
`import_module` / `__import__` / `exec` / `eval`, any lazy (function-scope)
import, and any top-level import outside the stdlib or a reviewed sibling
allowlist (currently empty — the resolver has no sibling dependencies).

Usage:
    python3 scripts/check-opinion-resolver-one-way.py [--root DIR]

Exit codes:
    0  clean — resolver is one-directional
    1  violation found
    2  setup error (resolver module not found)
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

# No reviewed siblings today — opinion_resolver.py is stdlib-only.
ALLOWED_SIBLINGS: frozenset[str] = frozenset()
ALLOWED_LAZY_FROMS: frozenset[tuple[str, str]] = frozenset()

RESOLVER_MODULES = ["opinion_resolver.py"]

FORBIDDEN_CALLS = frozenset({"__import__", "exec", "eval"})
FORBIDDEN_IMPORTLIB_ATTRS = frozenset({"import_module", "find_spec", "util"})

_STDLIB_PREFIXES = frozenset({
    "__future__", "abc", "argparse", "ast", "asyncio", "base64", "binascii", "builtins",
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
    "TYPE_CHECKING",
})


def _is_stdlib(name: str) -> bool:
    return name.split(".")[0] in _STDLIB_PREFIXES


class _Checker(ast.NodeVisitor):
    def __init__(self, src_path: Path) -> None:
        self.path = src_path
        self.violations: list[str] = []
        self._in_type_checking = False

    def _v(self, lineno: int, msg: str) -> None:
        self.violations.append(f"  {self.path.name}:{lineno}: {msg}")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            mod = alias.name.split(".")[0]
            if not _is_stdlib(mod) and mod not in ALLOWED_SIBLINGS:
                self._v(node.lineno, f"import {alias.name!r} — not in stdlib or allowed siblings")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self._in_type_checking:
            self.generic_visit(node)
            return
        mod = (node.module or "").split(".")[0]
        if not _is_stdlib(mod) and mod not in ALLOWED_SIBLINGS:
            self._v(node.lineno, f"from {node.module!r} import … — not in stdlib or allowed siblings")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for child in ast.walk(node):
            if child is node:
                continue
            if isinstance(child, ast.ImportFrom):
                mod = child.module or ""
                names = tuple(a.name for a in child.names)
                if not all((mod, n) in ALLOWED_LAZY_FROMS for n in names):
                    self._v(child.lineno, f"lazy from {mod!r} import inside function — not in ALLOWED_LAZY_FROMS")
            elif isinstance(child, ast.Import):
                for alias in child.names:
                    self._v(child.lineno, f"lazy import {alias.name!r} inside function")
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_CALLS:
                self._v(node.lineno, f"call to {node.func.id!r} — dynamic import / execution forbidden")
        elif isinstance(node.func, ast.Attribute):
            obj = node.func.value
            if isinstance(obj, ast.Name) and obj.id == "importlib":
                if node.func.attr in FORBIDDEN_IMPORTLIB_ATTRS:
                    self._v(node.lineno, f"call to importlib.{node.func.attr!r} — dynamic import forbidden")
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        test = node.test
        is_tc = (
            (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING")
            or (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")
        )
        old = self._in_type_checking
        self._in_type_checking = is_tc
        self.generic_visit(node)
        self._in_type_checking = old


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
            print(f"check-opinion-resolver-one-way: unknown arg: {args[0]}", file=sys.stderr)
            return 2

    scripts = root / "scripts"
    all_violations: list[str] = []
    for module_name in RESOLVER_MODULES:
        src = scripts / module_name
        if not src.exists():
            print(f"check-opinion-resolver-one-way: {module_name} not found (skipping — not yet shipped)",
                  file=sys.stderr)
            continue
        try:
            tree = ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
        except SyntaxError as exc:
            print(f"check-opinion-resolver-one-way: syntax error in {module_name}: {exc}", file=sys.stderr)
            return 1
        checker = _Checker(src)
        checker.visit(tree)
        all_violations.extend(checker.violations)

    if all_violations:
        print("check-opinion-resolver-one-way: resolver imports plugin code — FAIL", file=sys.stderr)
        for v in all_violations:
            print(v, file=sys.stderr)
        return 1

    print("check-opinion-resolver-one-way: clean — resolver is one-directional.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
