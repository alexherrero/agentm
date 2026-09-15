#!/usr/bin/env python3
"""A test file's `if __name__ == "__main__":` block is its last statement.

A suite here runs two ways. The battery's runner, `run_unit_suite.py`, imports
each file whole through unittest discovery, so every class in it runs. A hand
run, `python3 scripts/test_x.py`, executes the file from the top and exits
inside the guard, at `unittest.main()`. Anything written below the guard has
not been defined when that run starts. A test class there runs in the battery
and never by hand, and the hand run still reports OK. It is ungoverned as well.
Where a file calls `isolate_module(globals())`, the call sits above the guard
and wraps only the classes defined before it.

On 2026-09-13 six classes sat below a guard, in three files, and a hand run of
each file still said OK. Two of them, in `test_memory_root.py`, subclass
`MemoryRootBase` and name no TestCase on their own line, which a scan for that
word would miss.

So this check does not ask what a statement is. Anything below the guard fails
it: a test class, a helper that a test above calls, or an `isolate_module` call
that a hand run never makes.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent


def main_guard(tree: ast.Module) -> ast.If | None:
    """The first top-level `if __name__ == "__main__":`, written either way round."""
    for node in tree.body:
        test = getattr(node, "test", None)
        if not (isinstance(node, ast.If) and isinstance(test, ast.Compare)
                and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)):
            continue
        sides = [test.left, *test.comparators]
        if (any(isinstance(s, ast.Name) and s.id == "__name__" for s in sides)
                and any(isinstance(s, ast.Constant) and s.value == "__main__" for s in sides)):
            return node
    return None


def below_the_guard(source: str) -> list[tuple[int, str]]:
    """Each top-level statement after the main guard, as (line, that line's text)."""
    tree = ast.parse(source)
    guard = main_guard(tree)
    if guard is None:
        return []
    lines = source.splitlines()
    return [(node.lineno, lines[node.lineno - 1].strip())
            for node in tree.body if node.lineno > guard.lineno]


def _test_files() -> list[Path]:
    return sorted(p for p in _SCRIPTS.rglob("test_*.py") if "__pycache__" not in p.parts)


class EveryTestFileEndsWithItsMainGuard(unittest.TestCase):

    def test_nothing_in_scripts_sits_below_a_main_guard(self):
        found = [f"{path.relative_to(_SCRIPTS.parent).as_posix()}:{line}: {text}"
                 for path in _test_files()
                 for line, text in below_the_guard(path.read_text(encoding="utf-8"))]
        self.assertEqual(
            found, [],
            "these sit below their file's `if __name__ == \"__main__\":` block, so "
            "a hand run exits before it defines them. Move each above the guard, "
            "and keep any `isolate_module(globals())` call below every class.")

    def test_the_scan_reads_this_file_and_finds_its_guard(self):
        """A glob that matched nothing, or a guard the scan could not recognise,
        would leave the check above with nothing to report, and it would pass."""
        me = Path(__file__).resolve()
        self.assertIn(me, _test_files())
        tree = ast.parse(me.read_text(encoding="utf-8"))
        self.assertIs(main_guard(tree), tree.body[-1])


class TheScanReadsPositionNotNames(unittest.TestCase):

    def test_a_class_below_the_guard_is_reported_whatever_it_subclasses(self):
        source = (
            "import unittest\n"
            "\n"
            "class Base(unittest.TestCase):\n"
            "    pass\n"
            "\n"
            "if __name__ == \"__main__\":\n"
            "    unittest.main()\n"
            "\n"
            "class TestSpaces(Base):\n"
            "    def test_defaults(self):\n"
            "        pass\n"
        )
        self.assertEqual(below_the_guard(source), [(9, "class TestSpaces(Base):")])

    def test_a_statement_that_is_not_a_class_is_reported_too(self):
        source = (
            "import unittest\n"
            "from engine_state_isolation import isolate_module\n"
            "\n"
            "if __name__ == \"__main__\":\n"
            "    unittest.main()\n"
            "\n"
            "isolate_module(globals())\n"
        )
        self.assertEqual(below_the_guard(source), [(7, "isolate_module(globals())")])

    def test_a_guard_written_the_other_way_round_is_still_the_guard(self):
        source = (
            "if '__main__' == __name__:\n"
            "    main()\n"
            "\n"
            "def helper():\n"
            "    pass\n"
        )
        self.assertEqual(below_the_guard(source), [(4, "def helper():")])

    def test_a_file_that_ends_with_its_guard_reports_nothing(self):
        source = (
            "import unittest\n"
            "\n"
            "def helper():\n"
            "    return 1\n"
            "\n"
            "class Tests(unittest.TestCase):\n"
            "    def test_helper(self):\n"
            "        self.assertEqual(helper(), 1)\n"
            "\n"
            "if __name__ == \"__main__\":\n"
            "    unittest.main()\n"
        )
        self.assertEqual(below_the_guard(source), [])


if __name__ == "__main__":
    unittest.main()
