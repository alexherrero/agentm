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
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        # Both export names: `$MEMORY_ROOT` wins over its deprecated alias, so
        # setting the alias alone leaves a live export in charge and the gate
        # scans the real vault.
        env = {
            **os.environ,
            "MEMORY_ROOT": str(_HERE / "no-such-vault-dir"),
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


@unittest.skipIf(yaml is None, "PyYAML not installed")
class TestScanRoot(unittest.TestCase):
    """Which directory the gate scans when nobody names one."""

    def _run_resolved(self, tmp: str, notes: dict[str, str]) -> subprocess.CompletedProcess:
        # The shipped layout: the memory root is <vault>/agent, and the
        # operator's spaces sit beside it. The export names the memory root,
        # as the hooks and the runner set it.
        vault = _vault(str(Path(tmp) / "vault"), notes)
        prefix = Path(tmp) / "prefix"
        prefix.mkdir()
        (prefix / ".agentm-config.json").write_text(
            json.dumps({"plugins.obsidian-vault.memory_root": "agent"}), encoding="utf-8",
        )
        env = {
            **os.environ,
            "MEMORY_ROOT": str(vault / "agent"),
            "MEMORY_VAULT_PATH": str(vault / "agent"),
            "AGENTM_INSTALL_PREFIX": str(prefix),
        }
        return subprocess.run(
            [sys.executable, str(_GATE)], capture_output=True, text=True, env=env,
        )

    def test_every_space_beside_the_memory_root_is_scanned(self) -> None:
        # The regression: rooted at memory_root(), the gate read agent/ alone,
        # reported it clean, and never opened a tracker, a plan, a standard
        # or the operator's own notes.
        broken = "---\nkind: note\nstatus: a: b\n---\n"
        beside = [
            "projects/agentm/tasks/001-a/tracker.md",
            "personal/Home/note.md",
            "standards/storage-rules.md",
            "calendar/2026/09/2026-09-20-diary.md",
            "index.md",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_resolved(tmp, {
                "agent/memory/semantic/fine.md": "---\nkind: reference\n---\n",
                **{rel: broken for rel in beside},
            })
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("5 violation(s) across 6 notes", result.stderr)
        for rel in beside:
            self.assertIn(f"{rel} [parse-error]", result.stderr)

    def test_the_report_names_the_spaces_it_scanned(self) -> None:
        # The scope line is what makes a narrowed root visible where the gate
        # runs: a scan of the memory root would name memory/ and inbox/, not
        # agent/ and projects/.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_resolved(tmp, {
                "agent/memory/semantic/a.md": "---\nkind: reference\n---\n",
                "agent/inbox/b.md": "---\nkind: reference\n---\n",
                "projects/agentm/tasks/001-a/tracker.md": "---\nkind: tracker\n---\n",
                "Ideas.md": "# No frontmatter\n",
            })
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("4 notes clean", result.stdout)
        self.assertIn("scope: agent 2, projects 1, (top level) 1", result.stdout)


@unittest.skipIf(yaml is None, "PyYAML not installed")
class TestSupersededShape(unittest.TestCase):
    """One shape for the superseded relation (PLAN-superseded-vocabulary): the
    superseded memory names its successor; `supersedes:` is the successor's."""

    def test_the_contract_shape_is_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            findings = _scan(tmp, {
                "memory/semantic/old.md": "---\nkind: reference\nstatus: active\nlifecycle: superseded\n"
                                          "superseded_by: memory/semantic/new.md\n---\n\nOld.\n",
                "memory/semantic/new.md": "---\nkind: reference\nstatus: active\nsupersedes: memory/semantic/old.md\n---\n\nNew.\n",
            })
        self.assertEqual(_codes(findings), [])

    def test_a_superseded_note_without_a_successor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            findings = _scan(tmp, {
                "memory/semantic/orphan.md": "---\nkind: reference\nstatus: active\nlifecycle: superseded\n---\n\nBy what?\n",
            })
        self.assertEqual(_codes(findings), ["missing-successor"])
        self.assertEqual(findings[0].line, 4)  # the `lifecycle:` line

    def test_the_inverted_shape_on_either_axis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            findings = _scan(tmp, {
                "memory/semantic/old-status.md": "---\nkind: reference\nstatus: superseded\n"
                                                 "supersedes: memory/semantic/new.md\n---\n\nInverted, old axis.\n",
                "memory/semantic/old-lifecycle.md": "---\nkind: reference\nlifecycle: superseded\n"
                                                    "superseded_by: memory/semantic/new.md\nsupersedes: memory/semantic/new.md\n---\n\nInverted, new axis.\n",
            })
        self.assertEqual(_codes(findings), ["inverted-supersession", "inverted-supersession"])
        by_file = {f.rel: f.line for f in findings}
        self.assertEqual(by_file["memory/semantic/old-status.md"], 4)
        self.assertEqual(by_file["memory/semantic/old-lifecycle.md"], 5)

    def test_a_successor_back_link_alone_is_not_a_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            findings = _scan(tmp, {
                "memory/semantic/new.md": "---\nkind: reference\nstatus: active\nsupersedes: memory/semantic/old.md\n---\n\nNew.\n",
            })
        self.assertEqual(_codes(findings), [])


@unittest.skipIf(yaml is None, "PyYAML not installed")
class TestSourceIsATransport(unittest.TestCase):
    """One meaning for `source:` (PLAN-source-and-hygiene): the contract's
    closed transport vocabulary. The unit a memory came from lives in
    `source_url:` or `source_id:`."""

    def test_every_transport_the_contract_names_is_clean(self) -> None:
        notes = {
            f"memory/semantic/{t}.md": f"---\nkind: reference\nstatus: active\nsource: {t}\n---\n\nx.\n"
            for t in ("operator-direct", "conversation", "external-fetch", "inbox")
        }
        with tempfile.TemporaryDirectory() as tmp:
            findings = _scan(tmp, notes)
        self.assertEqual(_codes(findings), [])

    def test_the_gate_names_exactly_the_packaged_contracts_transports(self) -> None:
        # The gate keeps its own copy of the vocabulary, since a CI runner has
        # no contract to resolve, and a copy drifts: agentm-vault plan 16 traded
        # `email` for `inbox` in the contract alone, which left the gate set to
        # refuse every card the inbox review files. The packaged contract is in
        # every checkout, so the copy is held to it here, in both directions.
        shipped = _HERE.parent / "daemon" / "internal" / "rules" / "storage-rules.default.md"
        text = shipped.read_text(encoding="utf-8")
        block = text.split("```storage-rules\n", 1)[1].split("\n```", 1)[0]
        self.assertEqual(sorted(yaml.safe_load(block)["sources"]), sorted(_mod._TRANSPORTS))

    def test_a_url_in_the_transport_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            findings = _scan(tmp, {
                "memory/semantic/a.md": "---\nkind: reference\nsource: https://example.com/x\n---\n\nx.\n",
            })
        self.assertEqual(_codes(findings), ["source-not-a-transport"])
        self.assertIn("source_url", findings[0].detail)
        self.assertEqual(findings[0].line, 3)

    def test_a_reference_in_the_transport_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            findings = _scan(tmp, {
                "memory/semantic/a.md": "---\nkind: reference\nsource: email:<abc@example.com>\n---\n\nx.\n",
            })
        self.assertEqual(_codes(findings), ["source-not-a-transport"])
        self.assertIn("source_id", findings[0].detail)

    def test_the_reference_fields_are_not_checked_against_the_vocabulary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            findings = _scan(tmp, {
                "memory/semantic/a.md": "---\nkind: reference\nsource: external-fetch\n"
                                        "source_url: https://example.com/x\nsource_id: url:https://example.com/x\n"
                                        "---\n\nx.\n",
            })
        self.assertEqual(_codes(findings), [])

    def test_a_note_with_no_source_is_not_a_finding(self) -> None:
        # Most of the corpus predates the field. Absence is not a wrong value.
        with tempfile.TemporaryDirectory() as tmp:
            findings = _scan(tmp, {"memory/semantic/a.md": "---\nkind: reference\n---\n\nx.\n"})
        self.assertEqual(_codes(findings), [])


_PACKAGED_CONTRACT = _HERE.parent / "daemon" / "internal" / "rules" / "storage-rules.default.md"
# The packaged contract's one walled area.
_WALLED = "personal/Home/Important Docs"


@unittest.skipIf(yaml is None, "PyYAML not installed")
class TestRecallWall(unittest.TestCase):
    """The contract's one wall. The folder that holds certificates and
    recovery codes is never entered, so nothing in it is parsed, counted or
    quoted. A finding quotes the block it came from, and until this rule the
    gate opened every file there."""

    # Broken, so a parse of it would quote the sentinel in its finding.
    _BROKEN = "---\nstatus: sentinel-behind-the-wall: a colon-space\n---\n"
    _CLEAN = "---\nkind: note\n---\n"
    _built = None

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._built is not None:
            cls._built.cleanup()

    def _agentmd(self) -> str:
        binary = os.environ.get("AGENTMD", "").strip()
        if binary:
            return binary
        if shutil.which("go") is None:
            self.skipTest("go is not on this machine; set $AGENTMD to a built binary")
        if TestRecallWall._built is None:
            TestRecallWall._built = tempfile.TemporaryDirectory(prefix="agentmd-build-")
            subprocess.run(
                ["go", "build", "-o", str(Path(TestRecallWall._built.name) / "agentmd"), "./cmd/agentmd"],
                cwd=_HERE.parent / "daemon", check=True, capture_output=True,
            )
        return str(Path(TestRecallWall._built.name) / "agentmd")

    def test_a_walled_directory_is_never_entered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _vault(tmp, {
                f"{_WALLED}/codes.md": self._BROKEN,
                f"{_WALLED}/deeper/more.md": self._BROKEN,
                "personal/Home/recipe.md": self._CLEAN,
            })
            findings, scanned = _mod.scan_vault(root, yaml, wall=[_WALLED])
        self.assertEqual((findings, scanned), ([], 1))

    def test_a_walled_file_is_never_opened(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _vault(tmp, {
                "standards/secret.md": self._BROKEN,
                "standards/open.md": self._CLEAN,
            })
            findings, scanned = _mod.scan_vault(root, yaml, wall=["standards/secret.md"])
        self.assertEqual((findings, scanned), ([], 1))

    def test_a_root_below_the_vault_root_is_placed_under_it(self) -> None:
        # Areas are named from the vault root. Scanned from `personal/`, the
        # folder reads `Home/Important Docs`, which matches no area unless the
        # root's own place comes first.
        with tempfile.TemporaryDirectory() as tmp:
            root = _vault(tmp, {f"{_WALLED}/codes.md": self._BROKEN})
            findings, scanned = _mod.scan_vault(
                root / "personal", yaml, wall=[_WALLED], root_rel="personal/",
            )
        self.assertEqual((findings, scanned), ([], 0))

    def test_without_a_contract_the_wall_falls_back_closed(self) -> None:
        import storage_rules
        failing = mock.patch.object(
            storage_rules, "rules", side_effect=storage_rules.StorageRulesError("no daemon"),
        )
        with failing:
            walled = _mod._walled_areas()
        # Closed means the shipped contract's own wall, mirrored — never an
        # empty list. The list grew `standards/templates` on 2026-09-24; the
        # private area is what "closed" must still cover.
        self.assertEqual(walled, list(storage_rules._FALLBACK_RECALL_EXEMPT_AREAS))
        self.assertIn(_WALLED, walled)

    def _layout(self, tmp: Path) -> tuple:
        """A configured vault with a broken note behind each of two walls, and
        a contract that walls one more area than the shipped one — so the
        contract being honoured cannot be mistaken for the fallback."""
        vault = _vault(str(tmp / "vault"), {
            "agent/memory/semantic/fine.md": "---\nkind: reference\n---\n",
            "personal/Home/recipe.md": self._CLEAN,
            f"{_WALLED}/codes.md": self._BROKEN,
            "projects/walled/plan.md": self._BROKEN,
        })
        prefix = tmp / "prefix"
        prefix.mkdir()
        (prefix / ".agentm-config.json").write_text(json.dumps({
            "plugins.obsidian-vault.vault_path": str(vault),
            "plugins.obsidian-vault.memory_root": "agent",
        }), encoding="utf-8")
        text = _PACKAGED_CONTRACT.read_text(encoding="utf-8")
        marker = f"recall_exempt_areas:\n  - {_WALLED}\n"
        self.assertIn(marker, text, "the packaged contract's wall moved; update this fixture")
        contract = tmp / "storage-rules.md"
        contract.write_text(text.replace(marker, marker + "  - projects/walled\n"), encoding="utf-8")
        return vault, prefix, contract

    def _run(self, prefix: Path, contract: Path, agentmd: str, *args: str) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k not in ("MEMORY_ROOT", "MEMORY_VAULT_PATH")}
        env.update({
            "AGENTMD": agentmd,
            "AGENTM_INSTALL_PREFIX": str(prefix),
            "AGENTM_STORAGE_RULES": str(contract),
        })
        return subprocess.run(
            [sys.executable, str(_GATE), *args], capture_output=True, text=True, env=env,
        )

    def test_the_contracts_wall_holds_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _vault_root, prefix, contract = self._layout(tmp)
            result = self._run(prefix, contract, self._agentmd())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("2 notes clean", result.stdout)
        self.assertIn("scope: agent 1, personal 1", result.stdout)
        self.assertNotIn("sentinel-behind-the-wall", result.stdout + result.stderr)

    def test_without_a_daemon_the_shipped_wall_still_holds(self) -> None:
        # The contract cannot be read, so its extra area is not walled, and
        # the note there is parsed and reported. The shipped area is.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _vault_root, prefix, contract = self._layout(tmp)
            result = self._run(prefix, contract, str(tmp / "no-such-agentmd"))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("1 violation(s) across 3 notes", result.stderr)
        self.assertIn("projects/walled/plan.md [parse-error]", result.stderr)
        self.assertNotIn("Important Docs", result.stdout + result.stderr)

    def test_a_named_root_below_the_vault_root_still_honours_the_wall(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            vault, prefix, contract = self._layout(tmp)
            result = self._run(prefix, contract, str(tmp / "no-such-agentmd"),
                               "--vault", str(vault / "personal"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("1 notes clean", result.stdout)
        self.assertNotIn("sentinel-behind-the-wall", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
