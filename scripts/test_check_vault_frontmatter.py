#!/usr/bin/env python3
"""Tests for scripts/check-vault-frontmatter.py.

The gate is the only strict YAML parse over vault frontmatter. Both existing
linters split lines instead of parsing, and both exclude `_harness/`, so a
syntax error there survived for two months under green CI.

CI runners have no vault, so the gate's scan mode always skips there. These
tests are what actually exercises the scanner in CI: they drive it against
scratch vaults on every OS. Cases are hand-written — each asserts a literal
expected outcome, never one recomputed with the scanner's own logic.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_GATE = _HERE / "check-vault-frontmatter.py"

_SPEC = importlib.util.spec_from_file_location("check_vault_frontmatter", _GATE)
assert _SPEC and _SPEC.loader
_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_mod)  # type: ignore[union-attr]

try:
    import yaml
except ImportError:  # pragma: no cover - pyyaml is a declared dependency
    yaml = None


def _vault(tmp: str, notes: dict[str, str]) -> Path:
    """Build a scratch vault of notes, keyed by vault-relative path."""
    root = Path(tmp)
    for rel, body in notes.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


def _scan(tmp: str, notes: dict[str, str]) -> list:
    return _mod.scan_vault(_vault(tmp, notes), yaml)[0]


def _codes(findings) -> list[str]:
    return sorted(f.code for f in findings)


@unittest.skipIf(yaml is None, "PyYAML not installed")
class TestDefectClasses(unittest.TestCase):
    """The three classes the gate exists to catch."""

    def test_colon_space_in_unquoted_scalar_is_a_parse_error(self) -> None:
        # The class that hid nine notes: YAML reads the second colon as a
        # nested mapping and raises "mapping values are not allowed here".
        with tempfile.TemporaryDirectory() as tmp:
            findings = _scan(tmp, {"n.md": "---\nstatus: done: and more\n---\n"})
        self.assertEqual(_codes(findings), ["parse-error"])
        self.assertIn("mapping values", findings[0].detail)

    def test_hash_in_unquoted_scalar_truncates_silently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            findings = _scan(tmp, {
                "n.md": "---\nprd: codified from item #13 plus the rest\n---\n",
            })
        self.assertEqual(_codes(findings), ["truncated-value"])
        # "codified from item #13 plus the rest" is 36 characters; YAML keeps
        # "codified from item", which is 18. Both counted off the fixture.
        self.assertIn("loses 18 of 36 characters", findings[0].detail)
        self.assertEqual(findings[0].line, 2)

    def test_block_that_is_not_a_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            findings = _scan(tmp, {
                "list.md": "---\n- just\n- a list\n---\n",
                "scalar.md": "---\nbare string\n---\n",
                "empty.md": "---\n\n---\n",
            })
        self.assertEqual(_codes(findings), ["not-a-mapping"] * 3)
        self.assertEqual(
            sorted(f.detail for f in findings),
            [
                "frontmatter parses to NoneType, not a mapping",
                "frontmatter parses to list, not a mapping",
                "frontmatter parses to str, not a mapping",
            ],
        )

    def test_truncation_in_a_block_list_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            findings = _scan(tmp, {
                "n.md": "---\naliases:\n  - clean one\n  - bad #7 one\n---\n",
            })
        self.assertEqual(_codes(findings), ["truncated-value"])
        self.assertEqual(findings[0].line, 4)

    def test_value_wholly_eaten_by_a_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            findings = _scan(tmp, {"n.md": "---\ntitle: #13\nkind: note\n---\n"})
        self.assertEqual(_codes(findings), ["truncated-value"])
        self.assertIn("loses 3 of 3 characters", findings[0].detail)


@unittest.skipIf(yaml is None, "PyYAML not installed")
class TestNoFalsePositives(unittest.TestCase):
    """Legal frontmatter the gate must stay quiet about."""

    def test_deliberate_comment_after_a_scalar(self) -> None:
        # `# ` opens a real comment. The vault has several, written on purpose.
        with tempfile.TemporaryDirectory() as tmp:
            findings = _scan(tmp, {
                "n.md": "---\narea: agentm/storage   # the seam owns this\n---\n",
            })
        self.assertEqual(findings, [])

    def test_quoted_values_holding_a_hash_or_a_colon(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            findings = _scan(tmp, {
                "double.md": '---\ntitle: "Writer #2: source resolution"\n---\n',
                "single.md": "---\ninputs: 'ROADMAP #13: the re-audit'\n---\n",
            })
        self.assertEqual(findings, [])

    def test_comment_on_a_flow_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            findings = _scan(tmp, {
                "n.md": "---\ngoverns: []  # stamped at lift\ntags: [a, b]\n---\n",
            })
        self.assertEqual(findings, [])

    def test_explicit_null_is_not_an_eaten_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            findings = _scan(tmp, {
                "null.md": "---\ntitle: null\n---\n",
                "tilde.md": "---\ntitle: ~\n---\n",
                "empty.md": "---\ntitle:\nkind: note\n---\n",
            })
        self.assertEqual(findings, [])

    def test_a_note_without_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            findings = _scan(tmp, {"n.md": "# Heading\n\nBody with a # in it.\n"})
        self.assertEqual(findings, [])

    def test_hash_inside_a_multi_line_block_scalar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            findings = _scan(tmp, {
                "n.md": "---\nnotes: |\n  ROADMAP item #13 stays whole here.\n---\n",
            })
        self.assertEqual(findings, [])


@unittest.skipIf(yaml is None, "PyYAML not installed")
class TestWalkScope(unittest.TestCase):
    """The scope decision the gate turns on."""

    def test_underscore_directories_are_scanned(self) -> None:
        # The reason this gate exists: both existing linters carry an
        # `_EXCLUDE_DIRS` frozenset holding `_harness`, and that is exactly
        # where the broken notes were found. Inheriting it would be the bug.
        excluded_by_the_other_linters = [
            "_harness", "_meta", "_inbox", "_archive", "desk/scratch",
            "_opinions", "_idea-incubator", "_crystallize-staging",
        ]
        notes = {
            f"desk/projects/agentm/{d}/broken.md": "---\nstatus: a: b\n---\n"
            for d in excluded_by_the_other_linters
        }
        with tempfile.TemporaryDirectory() as tmp:
            findings = _scan(tmp, notes)
        self.assertEqual(
            len(findings), len(excluded_by_the_other_linters),
            "a directory both vault linters exclude went unscanned",
        )

    def test_dot_directories_are_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _vault(tmp, {".obsidian/plugin.md": "---\nbad: a: b\n---\n"})
            findings, scanned = _mod.scan_vault(root, yaml)
        self.assertEqual(findings, [])
        self.assertEqual(scanned, 0)

    def test_only_markdown_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _vault(tmp, {
                "note.md": "---\nkind: note\n---\n",
                "data.json": "---\nbad: a: b\n---\n",
                "notes.txt": "---\nbad: a: b\n---\n",
            })
            findings, scanned = _mod.scan_vault(root, yaml)
        self.assertEqual(findings, [])
        self.assertEqual(scanned, 1)


class TestCli(unittest.TestCase):
    """The gate as check-all.sh and CI invoke it."""

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(_GATE), *args],
            capture_output=True, text=True,
        )

    def test_self_test_mode_passes(self) -> None:
        # The mode CI runs, since a runner has no vault to scan.
        result = self._run("--self-test")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_clean_vault_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _vault(tmp, {"n.md": "---\nkind: note\ntitle: fine\n---\n"})
            result = self._run("--vault", tmp)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("1 notes clean", result.stdout)

    def test_dirty_vault_exits_one_and_names_the_file_and_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _vault(tmp, {"deep/n.md": "---\nkind: note\nstatus: a: b\n---\n"})
            result = self._run("--vault", tmp)
        self.assertEqual(result.returncode, 1)
        self.assertIn("deep/n.md", result.stderr)
        self.assertIn("parse-error", result.stderr)

    def test_missing_vault_is_a_setup_error_when_named(self) -> None:
        # An explicit --vault that does not exist is a broken invocation, not a
        # reason to report success.
        result = self._run("--vault", str(_HERE / "no-such-vault-dir"))
        self.assertEqual(result.returncode, 2)

    def test_unresolvable_vault_skips_rather_than_failing(self) -> None:
        # What a CI runner hits. A skip keeps the gate off the critical path of
        # machines that have no vault; it must never read as a pass over notes
        # that were not looked at.
        env = {
            **{k: v for k, v in __import__("os").environ.items()},
            "MEMORY_VAULT_PATH": str(_HERE / "no-such-vault-dir"),
            "AGENTM_INSTALL_PREFIX": str(_HERE / "no-such-prefix"),
        }
        result = subprocess.run(
            [sys.executable, str(_GATE)],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("skipping", result.stdout + result.stderr)
        self.assertNotIn("clean", result.stdout)


if __name__ == "__main__":
    unittest.main()
