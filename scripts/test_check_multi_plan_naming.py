#!/usr/bin/env python3
"""Positive + negative tests for scripts/check-multi-plan-naming.sh (V5-10 part 1, tasks 4-5).

The gate locks three halves of the named-plan naming contract:

  1. scripts/harness_memory.py exposes the resolver surface (`resolve_active_plan`,
     `harness_state_dir`);
  2. no curated `harness/*.md` doc hard-asserts a singleton plan ("the `PLAN.md`" /
     "`PLAN.md`'s"), while still PERMITTING legitimate `PLAN-<name>.md` / `PLAN*.md`
     / CLI-example mentions;
  3. both session-start hook twins (`harness-context-session-start.{sh,ps1}`) keep a
     `PLAN-*.md` glob so named-plan discovery at session boot can't silently regress
     (added task 5).

These tests drive the real gate via subprocess: it passes against the live repo,
**fails** on a re-introduced singleton assertion (the mandatory negative test),
fails when the resolver surface goes missing, fails when a hook loses its glob, and
still passes when a curated doc mentions a named plan. The `--root` flag points the
gate at a throwaway fixture tree so the negative cases never touch the repo.

Skipped on non-POSIX (the gate is a bash script; Windows has no POSIX bash),
matching the other bash-driving test suites in this directory.

Run directly:

    python3 scripts/test_check_multi_plan_naming.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_GATE = _HERE / "check-multi-plan-naming.sh"

# Must match the CURATED list inside the gate exactly — a missing curated file is
# a setup error (exit 2), so a fixture root has to carry all of them.
_CURATED = (
    "harness/principles.md",
    "harness/hooks.md",
    "harness/verification.md",
    "harness/documentation.md",
    "harness/skills/doctor.md",
    "harness/skills/memory/SKILL.md",
    "harness/skills/design/SKILL.md",
)

# A harness_memory.py that satisfies assertion 1 (both defs at column 0).
_HM_WITH_SURFACE = (
    "def resolve_active_plan(resolution, *, plan_arg=None):\n"
    "    return ('PLAN.md', 'progress.md')\n\n\n"
    "def harness_state_dir(resolution):\n"
    "    return None\n"
)
# ...and one that has lost it (assertion 1 must fail).
_HM_WITHOUT_SURFACE = "def something_else():\n    return None\n"

_CLEAN_DOC = "# placeholder\n\nNo singleton assertion here.\n"

# Session-start hook dir + stubs for assertion 3. The gate only greps for the
# glob token, so the stub bodies are minimal — one carries `PLAN-*.md`, one omits it.
_HOOK_DIR = "harness/hooks/harness-context-session-start"
_HOOK_WITH_GLOB = '#!/usr/bin/env bash\n# stub\nfor f in "$D"/PLAN-*.md; do :; done\n'
_HOOK_WITHOUT_GLOB = "#!/usr/bin/env bash\n# stub — singleton-only, no named-plan glob\necho hi\n"


def _run_gate(root: Path | None = None) -> subprocess.CompletedProcess:
    cmd = ["bash", str(_GATE)]
    if root is not None:
        cmd += ["--root", str(root)]
    return subprocess.run(cmd, capture_output=True, text=True)


class _FixtureRoot:
    """A throwaway ROOT carrying a passing harness_memory.py + clean curated docs.
    Tests mutate individual files to exercise each failure branch."""

    def __init__(self, base: Path) -> None:
        self.root = base
        (self.root / "scripts").mkdir(parents=True)
        (self.root / "scripts" / "harness_memory.py").write_text(
            _HM_WITH_SURFACE, encoding="utf-8"
        )
        for rel in _CURATED:
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(_CLEAN_DOC, encoding="utf-8")
        # Both session-start hook twins, each carrying the PLAN-*.md glob so
        # assertion 3 passes for the clean fixture (and only the mutated branch fails).
        hook_dir = self.root / _HOOK_DIR
        hook_dir.mkdir(parents=True, exist_ok=True)
        for fn in ("harness-context-session-start.sh", "harness-context-session-start.ps1"):
            (hook_dir / fn).write_text(_HOOK_WITH_GLOB, encoding="utf-8")

    def write(self, rel: str, body: str) -> None:
        (self.root / rel).write_text(body, encoding="utf-8")


@unittest.skipIf(os.name == "nt", "bash gate — POSIX only")
class CheckMultiPlanNaming(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="agentm-multi-plan-gate-")
        self.fx = _FixtureRoot(Path(self._tmp) / "root")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    # --- positive: the real repo satisfies the contract ---

    def test_gate_passes_on_repo(self) -> None:
        proc = _run_gate()  # no --root → the live repo
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_clean_fixture_passes(self) -> None:
        proc = _run_gate(self.fx.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    # --- negative: a re-introduced singleton assertion (the mandatory one) ---

    def test_reintroduced_definite_article_fails(self) -> None:
        self.fx.write(
            "harness/principles.md",
            "# principles\n\nAlways read the `PLAN.md` before doing anything.\n",
        )
        proc = _run_gate(self.fx.root)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("principles.md", proc.stderr)

    def test_reintroduced_possessive_fails(self) -> None:
        self.fx.write(
            "harness/verification.md",
            "# verification\n\nDoes it satisfy `PLAN.md`'s task criteria?\n",
        )
        proc = _run_gate(self.fx.root)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("verification.md", proc.stderr)

    # --- negative: the resolver surface went missing ---

    def test_missing_resolver_surface_fails(self) -> None:
        (self.fx.root / "scripts" / "harness_memory.py").write_text(
            _HM_WITHOUT_SURFACE, encoding="utf-8"
        )
        proc = _run_gate(self.fx.root)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("resolve_active_plan", proc.stderr)

    # --- permit-filter: legitimate named mentions never trip the gate ---

    def test_named_plan_mention_is_permitted(self) -> None:
        # A line that names PLAN-<name>.md (or globs PLAN*.md) is named-plan-aware
        # by construction — it must pass even though it contains "PLAN.md".
        self.fx.write(
            "harness/documentation.md",
            "# docs\n\nEnumerate the `PLAN-foo.md` and every `PLAN*.md` in `_harness/`.\n",
        )
        proc = _run_gate(self.fx.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_cli_example_is_permitted(self) -> None:
        # The doctor's `vault-state-path PLAN.md` CLI example is not a singleton
        # assertion — it is a literal command arg.
        self.fx.write(
            "harness/skills/doctor.md",
            "# doctor\n\nRun `harness_memory.py vault-state-path PLAN.md` to resolve.\n",
        )
        proc = _run_gate(self.fx.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    # --- negative: a session-start hook lost its PLAN-*.md glob (assertion 3) ---

    def test_bash_hook_without_glob_fails(self) -> None:
        self.fx.write(
            f"{_HOOK_DIR}/harness-context-session-start.sh", _HOOK_WITHOUT_GLOB
        )
        proc = _run_gate(self.fx.root)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("harness-context-session-start.sh", proc.stderr)
        self.assertIn("named-plan discovery lost", proc.stderr)

    def test_ps1_hook_without_glob_fails(self) -> None:
        # The pwsh twin is held to the same contract — a drift between the twins
        # (bash globs, pwsh doesn't) must fail just as loudly.
        self.fx.write(
            f"{_HOOK_DIR}/harness-context-session-start.ps1", _HOOK_WITHOUT_GLOB
        )
        proc = _run_gate(self.fx.root)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("harness-context-session-start.ps1", proc.stderr)

    # --- setup error: a curated file is missing → exit 2 (distinct from 1) ---

    def test_missing_curated_file_is_setup_error(self) -> None:
        (self.fx.root / "harness" / "principles.md").unlink()
        proc = _run_gate(self.fx.root)
        self.assertEqual(proc.returncode, 2, proc.stdout)

    def test_missing_hook_is_setup_error(self) -> None:
        # A vanished session-start hook is a setup error (exit 2), distinct from a
        # present-but-glob-less hook (exit 1).
        (self.fx.root / _HOOK_DIR / "harness-context-session-start.ps1").unlink()
        proc = _run_gate(self.fx.root)
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("missing session-start hook", proc.stderr)


if __name__ == "__main__":
    unittest.main()
