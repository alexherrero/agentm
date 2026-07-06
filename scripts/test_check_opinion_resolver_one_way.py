#!/usr/bin/env python3
"""Unit tests for check-opinion-resolver-one-way.py.

Mirrors test_check_capability_resolver_one_way.py:
  - The real opinion_resolver.py passes (self-check against the shipped file).
  - A deliberate violation (a non-stdlib top-level import) fails.
  - A missing module is skipped gracefully.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

_SPEC = importlib.util.spec_from_file_location(
    "check_opinion_resolver_one_way", _HERE / "check-opinion-resolver-one-way.py",
)
assert _SPEC and _SPEC.loader
_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_mod)  # type: ignore[union-attr]
_main = _mod._main


class TestCheckOpinionResolverOneWay(unittest.TestCase):
    def test_the_real_module_is_clean(self):
        rc = _main(["prog"])  # default root: the real repo
        self.assertEqual(rc, 0)

    def test_a_non_stdlib_import_fails(self):
        with TemporaryDirectory() as t:
            scripts = Path(t) / "scripts"
            scripts.mkdir()
            (scripts / "opinion_resolver.py").write_text(
                "import some_plugin_module\n", encoding="utf-8",
            )
            rc = _main(["prog", "--root", t])
            self.assertEqual(rc, 1)

    def test_a_dynamic_import_call_fails(self):
        with TemporaryDirectory() as t:
            scripts = Path(t) / "scripts"
            scripts.mkdir()
            (scripts / "opinion_resolver.py").write_text(
                "import importlib\nimportlib.import_module('x')\n", encoding="utf-8",
            )
            rc = _main(["prog", "--root", t])
            self.assertEqual(rc, 1)

    def test_missing_module_is_skipped_not_failed(self):
        with TemporaryDirectory() as t:
            (Path(t) / "scripts").mkdir()
            rc = _main(["prog", "--root", t])
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
