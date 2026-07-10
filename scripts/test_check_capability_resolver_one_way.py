#!/usr/bin/env python3
"""Unit tests for check-one-way-imports.py's `capability-resolver` rule.

Was check-capability-resolver-one-way.py (a standalone script) — CONS-1 merged
it into one of check-one-way-imports.py's rules. Same AST checker, same
allowlists, just invoked with `--rule capability-resolver`.

Covers Task 4 verification:
  - Clean resolver modules pass (the real files in scripts/).
  - Deliberate violations fail the gate (negative-path tests).
  - Missing module is skipped gracefully (gate doesn't fail on not-yet-shipped).
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import importlib
import importlib.util

# Load the check module (has a hyphen in filename, use importlib).
_SPEC = importlib.util.spec_from_file_location(
    "check_one_way_imports",
    _HERE / "check-one-way-imports.py",
)
assert _SPEC and _SPEC.loader
_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_mod)  # type: ignore[union-attr]
_main = _mod._main
_RULE = ["--rule", "capability-resolver"]


def _fixture_root(tmp: str, capability_resolver_src: str,
                  version_match_src: str | None = None) -> str:
    """Write fixture scripts/ under tmp and return the root path."""
    scripts = Path(tmp) / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "capability_resolver.py").write_text(
        capability_resolver_src, encoding="utf-8"
    )
    if version_match_src is not None:
        (scripts / "capability_version_match.py").write_text(
            version_match_src, encoding="utf-8"
        )
    return tmp


# ── passing cases ─────────────────────────────────────────────────────────────

class TestClean(unittest.TestCase):

    def test_real_resolver_modules_pass(self):
        """The actual shipped resolver + version_match modules must be clean."""
        rc = _main(["check-one-way-imports.py",
                    "--root", str(_HERE.parent), *_RULE])
        self.assertEqual(rc, 0,
                         "Real resolver modules failed the one-way gate — "
                         "check scripts/ for forbidden imports.")

    def test_stdlib_imports_allowed(self):
        src = "import json\nimport sys\nfrom pathlib import Path\n"
        with tempfile.TemporaryDirectory() as t:
            root = _fixture_root(t, src)
            rc = _main(["check-one-way-imports.py", "--root", root, *_RULE])
        self.assertEqual(rc, 0)

    def test_allowed_sibling_import_allowed(self):
        src = (
            "from capability_version_match import satisfies\n"
            "import json\n"
        )
        with tempfile.TemporaryDirectory() as t:
            root = _fixture_root(t, src)
            rc = _main(["check-one-way-imports.py", "--root", root, *_RULE])
        self.assertEqual(rc, 0)

    def test_allowed_lazy_from_inside_function_allowed(self):
        src = (
            "def capability_resolve(name):\n"
            "    from capability_version_match import satisfies\n"
            "    return satisfies('1.0', '>= 1.0')\n"
        )
        with tempfile.TemporaryDirectory() as t:
            root = _fixture_root(t, src)
            rc = _main(["check-one-way-imports.py", "--root", root, *_RULE])
        self.assertEqual(rc, 0)

    def test_missing_module_skipped_not_fail(self):
        """A module not yet on disk is skipped (graceful — not yet shipped)."""
        src = "import json\n"
        with tempfile.TemporaryDirectory() as t:
            scripts = Path(t) / "scripts"
            scripts.mkdir()
            (scripts / "capability_resolver.py").write_text(src, encoding="utf-8")
            # capability_version_match.py deliberately absent.
            rc = _main(["check-one-way-imports.py", "--root", t, *_RULE])
        self.assertEqual(rc, 0)


# ── failing cases (negative path) ────────────────────────────────────────────

class TestViolations(unittest.TestCase):

    def _expect_fail(self, src: str, match_src: str | None = None) -> None:
        with tempfile.TemporaryDirectory() as t:
            root = _fixture_root(t, src, match_src)
            rc = _main(["check-one-way-imports.py", "--root", root, *_RULE])
        self.assertEqual(rc, 1, f"Expected violation but gate passed for:\n{src}")

    def test_third_party_import_fails(self):
        self._expect_fail("import requests\n")

    def test_plugin_module_import_fails(self):
        self._expect_fail("import git_review_plugin\n")

    def test_exec_call_fails(self):
        self._expect_fail("exec(open('plugin.py').read())\n")

    def test_eval_call_fails(self):
        self._expect_fail("result = eval(code)\n")

    def test_dunder_import_fails(self):
        self._expect_fail("mod = __import__(plugin_name)\n")

    def test_importlib_import_module_fails(self):
        self._expect_fail("import importlib\nimportlib.import_module(slug)\n")

    def test_unapproved_lazy_from_in_function_fails(self):
        src = (
            "def load_plugin(name):\n"
            "    from plugin_package import api\n"
            "    return api\n"
        )
        self._expect_fail(src)

    def test_unapproved_lazy_import_in_function_fails(self):
        src = (
            "def run():\n"
            "    import plugin_pkg\n"
            "    return plugin_pkg.go()\n"
        )
        self._expect_fail(src)


if __name__ == "__main__":
    unittest.main()
