"""test_ci_consistency.py — the local battery and CI must agree on "green".

The both-places rule (testInfra#3): every gate `scripts/check-all.sh` runs also
runs in CI — either as a direct workflow step, or via a documented unit-test
wrapper that subprocess-invokes the gate script from inside the auto-discovered
`scripts/test_*.py` suite. Nothing enforced this until now, which is how two
gates (`check-vault-lock-parity.sh` — testInfra#0, `verify-orchestration-briefing.sh`
— testInfra#1) went invisible to CI while the local battery stayed green.

This test parses the real files (text-level, on purpose — it asserts what the
files say, not what a YAML loader normalizes them into):

  1. Every `gate "<name>" <command...>` line in check-all.sh names a script that
     either appears as a step in tests-linux.yml, or is in `UNIT_WRAPPED` below
     with a wrapper file that actually references the gate by name (so the
     allowlist can't silently rot into "gate coverage none of us checked").
  2. The known gate flags ride along (--strict on check-wiki, --all on
     check-no-pii) in both check-all.sh and tests-linux.yml.
  3. ci-all.yml's WORKFLOWS list points at workflow FILES that exist — the
     aggregate waits by filename, so a rename silently breaks it.

Runs as part of the battery itself (unittest discovery over scripts/).
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHECK_ALL = REPO / "scripts" / "check-all.sh"
WORKFLOWS_DIR = REPO / ".github" / "workflows"
LINUX = WORKFLOWS_DIR / "tests-linux.yml"
CI_ALL = WORKFLOWS_DIR / "ci-all.yml"

GATE_FILE_RE = re.compile(r"[\w./*-]+\.(?:py|sh|ps1)\b")

# Gates that do not appear as a direct tests-linux.yml step but are exercised via
# a `scripts/test_*.py` file that subprocess-invokes (or, for check-worktree-slug
# and check-no-auto-worktree, both subprocess-invokes AND unit-tests the Python
# helper the gate delegates to) the gate script by name. Each entry is verified
# below to actually reference the gate — an allowlist entry whose wrapper no
# longer mentions the gate is exactly the silent-rot this test exists to prevent.
UNIT_WRAPPED = {
    "check-capability-resolver-one-way.py": "test_check_capability_resolver_one_way.py",
    "check-multi-plan-naming.sh": "test_check_multi_plan_naming.py",
    "check-no-auto-worktree.sh": "test_worktree_slug_probe.py",
    "check-no-hardcoded-vault-path.py": "test_check_no_hardcoded_vault_path.py",
    "check-process-seam-import-direction.sh": "test_process_seam.py",
    "check-storage-seam-no-path-leak.py": "test_storage_seam.py",
    "check-workflow-parity.sh": "test_check_workflow_parity.py",
    "check-worktree-slug.sh": "test_worktree_slug_probe.py",
}


def battery_gate_lines():
    """The `gate "<name>" <command...>` lines from check-all.sh."""
    lines = []
    for line in CHECK_ALL.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith('gate "'):
            lines.append(stripped)
    return lines


class TestBatteryMatchesLinuxWorkflow(unittest.TestCase):
    """Rule 1: every battery gate is either a direct CI step or unit-wrapped."""

    def setUp(self):
        self.linux = LINUX.read_text(encoding="utf-8")
        self.gates = battery_gate_lines()
        self.assertGreaterEqual(
            len(self.gates), 6, "check-all.sh should declare at least 6 gates"
        )

    def test_every_battery_gate_visible_or_unit_wrapped(self):
        for gate in self.gates:
            tokens = GATE_FILE_RE.findall(gate)
            self.assertTrue(tokens, f"no script token found in gate line: {gate}")
            for token in tokens:
                # check-all runs scripts via their path; the workflow may too —
                # match on the basename so `scripts/x.py` == `x.py`.
                basename = token.rsplit("/", 1)[-1]
                if basename in self.linux:
                    continue
                self.assertIn(
                    basename,
                    UNIT_WRAPPED,
                    f"battery gate references {basename!r} but tests-linux.yml "
                    f"never runs it and it is not in UNIT_WRAPPED — the both-places "
                    f"rule is broken (gate line: {gate})",
                )

    def test_unit_wrapped_allowlist_entries_still_reference_their_gate(self):
        for gate_basename, wrapper_name in UNIT_WRAPPED.items():
            wrapper = REPO / "scripts" / wrapper_name
            self.assertTrue(
                wrapper.is_file(),
                f"UNIT_WRAPPED[{gate_basename!r}] names {wrapper_name!r}, which "
                f"does not exist — the allowlist entry is stale",
            )
            text = wrapper.read_text(encoding="utf-8")
            gate_slug = gate_basename.rsplit(".", 1)[0]
            self.assertIn(
                gate_slug,
                text.replace("_", "-"),
                f"{wrapper_name} no longer mentions {gate_basename!r} — the "
                f"UNIT_WRAPPED allowlist entry has rotted",
            )

    def test_gate_flags_match(self):
        battery = CHECK_ALL.read_text(encoding="utf-8")
        for script, flag in (("check-wiki.py", "--strict"), ("check-no-pii.sh", "--all")):
            for name, text in (("check-all.sh", battery), ("tests-linux.yml", self.linux)):
                pattern = re.compile(re.escape(script) + r"[^\n]*" + re.escape(flag))
                self.assertRegex(
                    text,
                    pattern,
                    f"{name} must run {script} with {flag}",
                )


class TestAggregateFilenameCoupling(unittest.TestCase):
    """Rule 3: ci-all.yml's WORKFLOWS list matches real workflow files."""

    def test_workflows_list_files_exist(self):
        text = CI_ALL.read_text(encoding="utf-8")
        m = re.search(r'WORKFLOWS="([^"]+)"', text)
        self.assertIsNotNone(m, 'ci-all.yml must declare WORKFLOWS="..."')
        names = m.group(1).split()
        self.assertEqual(
            sorted(names),
            ["tests-linux", "tests-mac", "tests-windows"],
            "ci-all.yml's WORKFLOWS list changed — update this test + the CI design",
        )
        for name in names:
            self.assertTrue(
                (WORKFLOWS_DIR / f"{name}.yml").is_file(),
                f"ci-all.yml waits on {name}.yml, which does not exist — "
                f"the aggregate (and the badge) would hang/fail",
            )


if __name__ == "__main__":
    unittest.main()
