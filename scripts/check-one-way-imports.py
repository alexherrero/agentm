#!/usr/bin/env python3
"""check-one-way-imports.py — one config-driven checker over agentm's
one-way import-direction invariants (CONS-1 merge of what were three
separate scripts: check-opinion-resolver-one-way.py,
check-capability-resolver-one-way.py, check-process-seam-import-direction.sh).

The shell-based process-seam check converges on Python here (2 of the 3
originals already were) — its three back-edge scans are re-expressed as AST
module-import scans instead of line regexes, which also removes the need for
the old regex's dedicated `process_seam_helper` word-boundary special case:
an AST import node's module name is compared for exact equality, so a
similarly-named module can never be mistaken for the real one.

Two rule *kinds*, each a plain data list — the "config list" this gate is
driven by:

  RESOLVER_RULES — "does this resolver module import only stdlib + an
      explicitly reviewed sibling, with no dynamic import machinery?" Same
      `_Checker` AST visitor the two retired scripts each carried a copy of.
      - opinion-resolver: opinion_resolver.py never imports plugin code.
      - capability-resolver: capability_resolver.py + capability_version_match.py
        never import plugin code (capability_version_match is an allowed
        sibling; its lazy from-import inside capability_resolve() is the one
        allowed lazy import).

  BACK_EDGE_RULES — "does anything outside an allowlisted exception import
      this specific forbidden module?" Ports check-process-seam-import-direction.sh's
      three scans:
      - process-seam: the memory engine must never import process_seam
        (LC-4) — scanned across scripts/harness/lib/templates/.github.
      - lc8-storage-vault: the de-vaulted routing files (harness_memory.py,
        repo_registry.py) must never import the storage_vault capability
        plugin directly (LC-8) — the seam's abstract types are the contract.
      - lc8-bridge: kernel toolkit scripts under harness/skills/memory/scripts/
        must never import harness_memory back (V5-5 LC-8 bridge extension) —
        the bridge calls them as subprocesses, never the reverse.

Usage:
    python3 scripts/check-one-way-imports.py [--root DIR] [--rule NAME]

    --root DIR   repo root (default: parent of this script's directory).
    --rule NAME  run just one named rule (opinion-resolver, capability-resolver,
                 process-seam, lc8-storage-vault, lc8-bridge) instead of all 5.

Exit codes:
    0  clean — every requested rule is one-directional
    1  a violation was found in at least one requested rule
    2  setup error (bad --rule name, missing root)
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

# ── shared stdlib allowlist (used by RESOLVER_RULES) ────────────────────────

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

_FORBIDDEN_CALLS = frozenset({"__import__", "exec", "eval"})
_FORBIDDEN_IMPORTLIB_ATTRS = frozenset({"import_module", "find_spec", "util"})


def _is_stdlib(name: str) -> bool:
    return name.split(".")[0] in _STDLIB_PREFIXES


class _NoPluginImportChecker(ast.NodeVisitor):
    """AST visitor: flags any non-stdlib, non-allowed-sibling import (top-level
    or lazy), plus any dynamic-import call (__import__/exec/eval/importlib.*).
    Shared by every RESOLVER_RULES entry — the two retired scripts carried
    byte-identical copies of this class, parametrized only by the allowlists.
    """

    def __init__(self, src_path: Path, allowed_siblings: frozenset,
                 allowed_lazy_froms: frozenset) -> None:
        self.path = src_path
        self.allowed_siblings = allowed_siblings
        self.allowed_lazy_froms = allowed_lazy_froms
        self.violations: list[str] = []
        self._in_type_checking = False

    def _v(self, lineno: int, msg: str) -> None:
        self.violations.append(f"  {self.path.name}:{lineno}: {msg}")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            mod = alias.name.split(".")[0]
            if not _is_stdlib(mod) and mod not in self.allowed_siblings:
                self._v(node.lineno, f"import {alias.name!r} — not in stdlib or allowed siblings")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self._in_type_checking:
            self.generic_visit(node)
            return
        mod = (node.module or "").split(".")[0]
        if not _is_stdlib(mod) and mod not in self.allowed_siblings:
            self._v(node.lineno, f"from {node.module!r} import … — not in stdlib or allowed siblings")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for child in ast.walk(node):
            if child is node:
                continue
            if isinstance(child, ast.ImportFrom):
                mod = child.module or ""
                names = tuple(a.name for a in child.names)
                if not all((mod, n) in self.allowed_lazy_froms for n in names):
                    self._v(child.lineno, f"lazy from {mod!r} import inside function — not in allowed_lazy_froms")
            elif isinstance(child, ast.Import):
                for alias in child.names:
                    self._v(child.lineno, f"lazy import {alias.name!r} inside function")
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            if node.func.id in _FORBIDDEN_CALLS:
                self._v(node.lineno, f"call to {node.func.id!r} — dynamic import / execution forbidden")
        elif isinstance(node.func, ast.Attribute):
            obj = node.func.value
            if isinstance(obj, ast.Name) and obj.id == "importlib":
                if node.func.attr in _FORBIDDEN_IMPORTLIB_ATTRS:
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


def _run_resolver_rule(root: Path, rule: dict) -> list[str]:
    scripts = root / "scripts"
    violations: list[str] = []
    for module_name in rule["modules"]:
        src = scripts / module_name
        if not src.exists():
            continue  # not yet shipped — graceful skip, matches the retired scripts
        try:
            tree = ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
        except SyntaxError as exc:
            violations.append(f"  syntax error in {module_name}: {exc}")
            continue
        checker = _NoPluginImportChecker(src, rule["allowed_siblings"], rule["allowed_lazy_froms"])
        checker.visit(tree)
        violations.extend(checker.violations)
    return violations


RESOLVER_RULES = [
    {
        "name": "opinion-resolver",
        "modules": ["opinion_resolver.py"],
        "allowed_siblings": frozenset(),
        "allowed_lazy_froms": frozenset(),
        "doc": "opinion_resolver.py never imports plugin code (Enforcement §1)",
    },
    {
        "name": "capability-resolver",
        "modules": ["capability_resolver.py", "capability_version_match.py"],
        "allowed_siblings": frozenset({"capability_version_match"}),
        "allowed_lazy_froms": frozenset({("capability_version_match", "satisfies")}),
        "doc": "capability_resolver.py + capability_version_match.py never import plugin code (V5-8)",
    },
]


# ── back-edge scans (process-seam family) ───────────────────────────────────

def _iter_py_files(target_dir: Path):
    if not target_dir.is_dir():
        return
    yield from sorted(target_dir.rglob("*.py"))


def _imports_forbidden_module(tree: ast.AST, forbidden: str) -> list[int]:
    """Line numbers where `tree` imports exactly `forbidden` (or a dotted
    submodule of it) — never a same-prefix sibling like `<forbidden>_helper`,
    since AST compares the parsed module name, not a substring/regex."""
    hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top == forbidden:
                    hits.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            top = mod.split(".")[0]
            if top == forbidden:
                hits.append(node.lineno)
    return hits


def _scan_dirs_for_back_edge(root: Path, forbidden: str, scan_dirs: list[str],
                              exclude_basenames: frozenset[str],
                              allowed_consumers: frozenset[str]) -> list[str]:
    violations: list[str] = []
    for d in scan_dirs:
        for f in _iter_py_files(root / d):
            base = f.name
            if base.startswith("test_"):
                continue  # tests import the target by design
            if base in exclude_basenames or base in allowed_consumers:
                continue
            try:
                tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for lineno in _imports_forbidden_module(tree, forbidden):
                violations.append(f"  {f.relative_to(root)}:{lineno}: imports {forbidden!r} (forbidden back-edge)")
    return violations


def _scan_files_for_back_edge(root: Path, forbidden: str, rel_files: list[str]) -> list[str]:
    violations: list[str] = []
    for rel in rel_files:
        f = root / rel
        if not f.is_file():
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for lineno in _imports_forbidden_module(tree, forbidden):
            violations.append(f"  {rel}:{lineno}: imports {forbidden!r} (forbidden routing→plugin dependency, LC-8)")
    return violations


def _run_process_seam(root: Path) -> list[str]:
    # LC-4: the memory engine must never import the process-seam client back.
    # Reviewed in-repo process-side consumers (empty today — LC-5: the real
    # consumers live crickets-side).
    return _scan_dirs_for_back_edge(
        root, "process_seam",
        scan_dirs=["scripts", "harness", "lib", "templates", ".github"],
        exclude_basenames=frozenset({"process_seam.py"}),
        allowed_consumers=frozenset(),
    )


def _run_lc8_storage_vault(root: Path) -> list[str]:
    # LC-8: de-vaulted routing mechanisms may import the storage seam's
    # abstract types, never a concrete capability plugin (storage_vault)
    # directly.
    return _scan_files_for_back_edge(
        root, "storage_vault",
        rel_files=["scripts/harness_memory.py", "scripts/repo_registry.py"],
    )


def _run_lc8_bridge(root: Path) -> list[str]:
    # V5-5 LC-8 bridge extension: kernel toolkit scripts are called by
    # harness_memory.py as subprocesses; a toolkit script importing back
    # creates a cycle.
    return _scan_dirs_for_back_edge(
        root, "harness_memory",
        scan_dirs=["harness/skills/memory/scripts"],
        exclude_basenames=frozenset(),
        allowed_consumers=frozenset(),
    )


BACK_EDGE_RULES = [
    {"name": "process-seam", "fn": _run_process_seam,
     "doc": "the memory engine never imports process_seam back (LC-4)"},
    {"name": "lc8-storage-vault", "fn": _run_lc8_storage_vault,
     "doc": "routing files never import storage_vault directly (LC-8)"},
    {"name": "lc8-bridge", "fn": _run_lc8_bridge,
     "doc": "kernel toolkit scripts never import harness_memory back (V5-5 LC-8 bridge)"},
]

ALL_RULE_NAMES = [r["name"] for r in RESOLVER_RULES] + [r["name"] for r in BACK_EDGE_RULES]


def _run_rule(root: Path, name: str) -> list[str]:
    for rule in RESOLVER_RULES:
        if rule["name"] == name:
            return _run_resolver_rule(root, rule)
    for rule in BACK_EDGE_RULES:
        if rule["name"] == name:
            return rule["fn"](root)
    raise KeyError(name)


def _main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=None, help="repo root (default: parent of this script's dir)")
    p.add_argument("--rule", default=None, choices=ALL_RULE_NAMES,
                   help="run just one named rule instead of all of them")
    # argv[0] is always a placeholder program name (real sys.argv[0] at the
    # __main__ call site, a throwaway string in tests) — never a real arg.
    args = p.parse_args(argv[1:])

    root = Path(args.root) if args.root else Path(__file__).resolve().parent.parent
    if not root.is_dir():
        print(f"check-one-way-imports: not a directory: {root}", file=sys.stderr)
        return 2

    names = [args.rule] if args.rule else ALL_RULE_NAMES
    all_violations: list[str] = []
    for name in names:
        all_violations.extend(_run_rule(root, name))

    if all_violations:
        print("check-one-way-imports: one-way import invariant violated —", file=sys.stderr)
        for v in all_violations:
            print(v, file=sys.stderr)
        return 1

    print(f"check-one-way-imports: clean — {len(names)} rule(s) checked, all one-directional.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
