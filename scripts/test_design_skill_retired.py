"""Retire-invariant: agentm no longer ships its own `design` skill.

crickets' `design` plugin owns `/design`. agentm's copy at
`harness/skills/design/` was installed as a bare `~/.claude/skills/design`
standalone, and a bare standalone always beats a plugin of the same name,
so it silently served a stale design pipeline (no prose pass) in place of
the plugin. Retired 2026-11-21. These tests keep it from coming back.
"""

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

LIVE_SURFACES = (
    "scripts/check-multi-plan-naming.sh",
    "scripts/test_check_multi_plan_naming.py",
    "scripts/smoke-install-bash.sh",
    "scripts/smoke-install-pwsh.ps1",
    "adapters/antigravity/rules/harness.md",
    "harness/skills/doctor.md",
    "adapters/claude-code/skills/doctor/SKILL.md",
    "harness/skills/memory/SKILL.md",
)


class DesignSkillRetired(unittest.TestCase):
    def test_source_directory_is_gone(self):
        self.assertFalse(
            (ROOT / "harness/skills/design").exists(),
            "harness/skills/design must stay retired; crickets' design "
            "plugin owns /design, and a bare standalone would shadow it.",
        )

    def test_no_live_surface_installs_or_lists_it(self):
        for rel in LIVE_SURFACES:
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("skills/design/", text, f"{rel} still points at the retired skill")

    def test_doctor_lists_design_as_crickets_shipped(self):
        for rel in ("harness/skills/doctor.md", "adapters/claude-code/skills/doctor/SKILL.md"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("plus the `/design` command", text, f"{rel} should name /design as crickets-shipped")
            self.assertNotIn("`design, memory`", text, f"{rel} still lists design as harness-shipped")


if __name__ == "__main__":
    unittest.main()
