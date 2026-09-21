#!/usr/bin/env python3
"""The vocabulary-membership gate's ratchet semantics, pinned.

The census finding this gate answers: the XOR rule held while enum membership
was never checked. The pinned behaviors: a baseline records legacy drift once;
any offender not in it fails immediately (a *set* comparison, so a swap cannot
hide inside a stable count); the baseline only ever shrinks; retired values are
migration-pending, never violations; `--strict` ignores the baseline entirely.

No daemon binary for the ratchet: the contract is injected through
`storage_rules`' module cache, which is the same seam the runtime uses — these
tests exercise the real audit walker and the real gate logic over scratch
vaults. `CorpusResolution` drives the real script the way check-all does, so it
asks a built daemon, pointed at the packaged contract.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for p in (str(_HERE), str(_SKILL_SCRIPTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

import storage_rules  # noqa: E402
from storage_rules import StorageRules  # noqa: E402

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "check_vocabulary_membership", _HERE / "check-vocabulary-membership.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


CONTRACT = {
    "memory_types": ["preference", "convention", "reference", "workflow", "fix", "idea"],
    "record_kinds": ["brief", "telemetry"],
    "deprecations": {"insight": "idea"},
}


def _note(kind_line: str) -> str:
    return f"---\n{kind_line}\n---\n\nbody\n"


class RatchetSemantics(unittest.TestCase):
    def setUp(self):
        self._saved_cache = storage_rules._CACHE
        storage_rules._CACHE = StorageRules(CONTRACT)
        self._td = tempfile.TemporaryDirectory()
        self.vault = Path(self._td.name)
        self.notes = self.vault / "memory" / "semantic"
        self.notes.mkdir(parents=True)
        self.baseline = self.vault / "baseline.json"

    def tearDown(self):
        storage_rules._CACHE = self._saved_cache
        self._td.cleanup()

    def _run(self, *, strict: bool = False) -> tuple:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = gate.run_corpus_check(self.vault, self.baseline, strict=strict)
        return code, out.getvalue()

    def test_first_run_records_the_baseline_and_passes(self):
        (self.notes / "legacy.md").write_text(_note("kind: analysis"), encoding="utf-8")
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("baseline recorded", out)
        code, _ = self._run()
        self.assertEqual(code, 0, "the recorded offender must be tolerated on the next run")

    def test_a_new_offender_fails_and_is_named(self):
        (self.notes / "legacy.md").write_text(_note("kind: analysis"), encoding="utf-8")
        self._run()  # record baseline
        (self.notes / "fresh.md").write_text(_note("kind: report"), encoding="utf-8")
        code, out = self._run()
        self.assertEqual(code, 1)
        self.assertIn("fresh.md", out)
        self.assertIn("report", out)
        self.assertNotIn("legacy.md: ", out.split("FAIL")[1],
                         "the tolerated legacy offender must not be re-reported as new")

    def test_a_swap_cannot_hide_inside_a_stable_count(self):
        (self.notes / "legacy.md").write_text(_note("kind: analysis"), encoding="utf-8")
        self._run()
        (self.notes / "legacy.md").write_text(_note("kind: brief"), encoding="utf-8")  # fixed
        (self.notes / "swap.md").write_text(_note("kind: seed-meta"), encoding="utf-8")  # new
        code, out = self._run()
        self.assertEqual(code, 1, "count stayed at one, but the offender is new — sets, not counts")
        self.assertIn("swap.md", out)

    def test_draining_ratchets_the_baseline_down(self):
        (self.notes / "legacy.md").write_text(_note("kind: analysis"), encoding="utf-8")
        self._run()
        (self.notes / "legacy.md").write_text(_note("kind: brief"), encoding="utf-8")
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("ratcheted down", out)
        # The drained offender must not be tolerated if it reappears.
        (self.notes / "legacy.md").write_text(_note("kind: analysis"), encoding="utf-8")
        code, _ = self._run()
        self.assertEqual(code, 1, "a drained offender that returns is new again")

    def test_retired_values_are_migration_pending_not_violations(self):
        (self.notes / "old.md").write_text(_note("kind: insight"), encoding="utf-8")
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("1 retired-value note", out)
        self.assertIn("0 unregistered-value offender", out)

    def test_malformed_values_are_violations(self):
        (self.notes / "bad.md").write_text(_note("kind: Not Kebab"), encoding="utf-8")
        self._run()  # baseline tolerates it
        (self.notes / "bad2.md").write_text(_note("kind: Also Bad"), encoding="utf-8")
        code, out = self._run()
        self.assertEqual(code, 1)
        self.assertIn("bad2.md", out)

    def test_a_corrupt_baseline_halts_instead_of_rebaselining(self):
        (self.notes / "legacy.md").write_text(_note("kind: analysis"), encoding="utf-8")
        self._run()  # record a real baseline
        self.baseline.write_text("{not valid json", encoding="utf-8")
        (self.notes / "flood.md").write_text(_note("kind: report"), encoding="utf-8")
        with self.assertRaises(gate.BaselineCorrupt) as ctx:
            gate.run_corpus_check(self.vault, self.baseline, strict=False)
        self.assertIn(str(self.baseline), str(ctx.exception))
        # The corrupt file must not have been overwritten by a fresh baseline —
        # silently swallowing the flood is the exact inversion of the ratchet.
        self.assertEqual(self.baseline.read_text(encoding="utf-8"), "{not valid json")

    def test_strict_ignores_the_baseline(self):
        (self.notes / "legacy.md").write_text(_note("kind: analysis"), encoding="utf-8")
        self._run()  # tolerated by ratchet
        code, out = self._run(strict=True)
        self.assertEqual(code, 1, "--strict is the post-migration mode: any violation fails")
        self.assertIn("legacy.md", out)
        (self.notes / "legacy.md").write_text(_note("kind: brief"), encoding="utf-8")
        code, _ = self._run(strict=True)
        self.assertEqual(code, 0)


class SelfTestMembershipHalf(unittest.TestCase):
    """The audit half of --self-test, runnable without a daemon binary."""

    def test_unregistered_value_in_scratch_vault_is_caught(self):
        saved = storage_rules._CACHE
        storage_rules._CACHE = StorageRules(CONTRACT)
        try:
            with tempfile.TemporaryDirectory() as td:
                vault = Path(td)
                notes = vault / "memory" / "semantic"
                notes.mkdir(parents=True)
                (notes / "stray.md").write_text(_note("kind: definitely-not-registered"),
                                                encoding="utf-8")
                import kind_registry
                offenders = gate._offenders(kind_registry.audit(vault))
                self.assertIn(("memory/semantic/stray.md", "definitely-not-registered"),
                              offenders)
        finally:
            storage_rules._CACHE = saved


_GATE = _HERE / "check-vocabulary-membership.py"
_PACKAGED_CONTRACT = _HERE.parent / "daemon" / "internal" / "rules" / "storage-rules.default.md"

# The shipped layout: the memory root is <vault>/agent, and the operator's
# spaces sit beside it. Each value is the note's vocabulary line, or None for a
# note that carries none.
_NESTED_VAULT = {
    "agent/memory/semantic/fine.md": "type: reference",
    "agent/diagnostics/2026-09-20-brief.md": "kind: brief",
    "projects/p/tasks/001-a/plan.md": "kind: unregistered-in-projects",
    "standards/rule.md": "kind: unregistered-in-standards",
    "personal/Home/recipe.md": None,
    # The packaged contract's recall_exempt_areas: never indexed, never served,
    # and never read by this gate either.
    "personal/Home/Important Docs/codes.md": "kind: behind-the-wall",
}


class CorpusResolution(unittest.TestCase):
    """Where the gate looks when check-all runs it, and what it reads there.

    check-all.sh exports no `$MEMORY_ROOT`, and until 2026-09-20 the gate read
    nothing else: it printed a skip line, exited 0, and the battery showed PASS
    over zero notes. With the export set, it walked the memory root's
    `memory/` and a `projects/` sibling it could only find through an
    `.obsidian/` witness, and never opened `standards/`.
    """

    @classmethod
    def setUpClass(cls):
        cls._build = None
        binary = os.environ.get("AGENTMD", "").strip()
        if not binary:
            if shutil.which("go") is None:
                raise unittest.SkipTest("go is not on this machine; set $AGENTMD to a built binary")
            cls._build = tempfile.TemporaryDirectory(prefix="agentmd-build-")
            binary = str(Path(cls._build.name) / "agentmd")
            subprocess.run(["go", "build", "-o", binary, "./cmd/agentmd"],
                           cwd=_HERE.parent / "daemon", check=True, capture_output=True)
        cls._agentmd = binary

    @classmethod
    def tearDownClass(cls):
        if cls._build is not None:
            cls._build.cleanup()

    def _vault(self, tmp: Path, *, configured: bool) -> tuple:
        """The nested vault, and an install prefix whose config names its
        memory root — and, when `configured`, the vault itself."""
        vault = tmp / "vault"
        for rel, line in _NESTED_VAULT.items():
            path = vault / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_note(line) if line else "# no frontmatter\n", encoding="utf-8")
        prefix = tmp / "prefix"
        prefix.mkdir()
        config = {"plugins.obsidian-vault.memory_root": "agent"}
        if configured:
            config["plugins.obsidian-vault.vault_path"] = str(vault)
        (prefix / ".agentm-config.json").write_text(json.dumps(config), encoding="utf-8")
        return vault, prefix

    def _run(self, tmp: Path, prefix: Path, export) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items()
               if k not in ("MEMORY_ROOT", "MEMORY_VAULT_PATH")}
        env.update({
            "AGENTMD": self._agentmd,
            "AGENTM_INSTALL_PREFIX": str(prefix),
            "AGENTM_STORAGE_RULES": str(_PACKAGED_CONTRACT),
            "AGENTM_STATE_DIR": str(tmp / "state"),
            "AGENTM_VOCAB_BASELINE": str(tmp / "baseline.json"),
        })
        if export is not None:
            # Both names: `$MEMORY_ROOT` wins over its deprecated alias, so an
            # alias left alone would put a live export in charge.
            env["MEMORY_ROOT"] = str(export)
            env["MEMORY_VAULT_PATH"] = str(export)
        return subprocess.run([sys.executable, str(_GATE), "--strict"],
                              capture_output=True, text=True, env=env)

    def _assert_both_spaces_caught(self, result: subprocess.CompletedProcess) -> None:
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("2 unregistered-value offender(s)", result.stdout)
        self.assertIn("projects/p/tasks/001-a/plan.md: 'unregistered-in-projects'", result.stdout)
        self.assertIn("standards/rule.md: 'unregistered-in-standards'", result.stdout)

    def test_a_nested_export_reaches_projects_and_standards(self):
        # The export names the memory root, as the hooks set it. The vault
        # root comes off the configured prefix, with no `.obsidian/` to witness
        # it, so only the resolver can find the spaces beside `agent/`.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            vault, prefix = self._vault(tmp, configured=False)
            result = self._run(tmp, prefix, vault / "agent")
        self._assert_both_spaces_caught(result)

    def test_with_nothing_exported_the_configured_vault_is_read(self):
        # What check-all.sh does: it exports neither name. The gate used to
        # skip here and pass the battery over zero notes.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _vault, prefix = self._vault(tmp, configured=True)
            result = self._run(tmp, prefix, None)
        self._assert_both_spaces_caught(result)
        self.assertNotIn("skipping", result.stdout)

    def test_the_report_names_the_spaces_it_walked(self):
        # A walk that narrows again shows in the output: the scope line names
        # each top-level directory under the vault root and the notes read in
        # it, counted in the walk itself.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            vault, prefix = self._vault(tmp, configured=False)
            result = self._run(tmp, prefix, vault / "agent")
        self.assertIn("5 notes scanned", result.stdout)
        self.assertIn("scope: agent 2, personal 1, projects 1, standards 1", result.stdout)

    def test_the_recall_wall_is_never_read(self):
        # The note behind the wall carries an unregistered value. Reported, it
        # would prove the file was opened; counted, it would too. It is
        # neither: the walk never enters the area.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _vault, prefix = self._vault(tmp, configured=True)
            result = self._run(tmp, prefix, None)
        self.assertNotIn("behind-the-wall", result.stdout + result.stderr)
        self.assertNotIn("Important Docs", result.stdout + result.stderr)
        self.assertIn("5 notes scanned", result.stdout)

    def test_a_broken_export_skips_rather_than_reading_the_configured_vault(self):
        # The export is the override, and an override is hermetic: one that
        # names nothing must not fall through to the configured vault, which on
        # this machine is the operator's own.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _vault, prefix = self._vault(tmp, configured=True)
            result = self._run(tmp, prefix, tmp / "no-such-memory-root")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("skipping", result.stdout)
        self.assertNotIn("notes scanned", result.stdout)

    def test_nothing_resolving_skips(self):
        # A CI runner: no export, no config. The self-test half still runs; the
        # corpus half names why it did not.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            result = self._run(tmp, tmp / "no-such-prefix", None)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("self-test OK", result.stdout)
        self.assertIn("skipping", result.stdout)
        self.assertNotIn("notes scanned", result.stdout)


if __name__ == "__main__":
    unittest.main()
