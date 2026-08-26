#!/usr/bin/env python3
"""Retire-invariant guard for the two duplicate wiki surfaces (2026-08-12).

`harness/skills/wiki-author/` and `adapters/claude-code/commands/recent-wiki-changes.md`
were vendored copies of primitives crickets' `wiki` plugin already owns. Both
installed into `~/.claude/` as *bare-name* standalones —
and a bare-name standalone is never superseded by a plugin, because Claude Code
namespaces plugin primitives unconditionally (`/wiki:recent-wiki-changes`). So
the stale copy kept serving its own aging content forever while the operator
believed the newer plugin had replaced it. Both copies had in fact drifted:

  - `wiki-author`: crickets' copy was at v0.1.1 with the `prose-pass` step and
    the crickets-conventions design links; agentm's was v0.1.0 without them.
  - `recent-wiki-changes`: crickets' copy carries the manifest frontmatter the
    plugin schema requires; agentm's predates it.

The retire follows the established pattern (07f7ddc diataxis-author, 0b831ab
documenter): delete the copy, re-categorize it crickets-shipped in both doctor
surfaces, and let the crickets-absent contract degrade to suggest-then-skip.

These tests pin that invariant so a later change can't silently re-introduce
either copy or a dangling path dependency on one:

  1. Both local paths are gone.
  2. agentm vendors NO claude-code command at all (that adapter dir is the
     surface `recent-wiki-changes` was the last occupant of).
  3. No *live* surface references either deleted PATH. The bare NAMES stay
     legal everywhere — crickets provides both, and doctor names them as
     crickets-provided. Only the vanished local paths are forbidden.
  4. `wiki-author` is categorized crickets-shipped (graceful-skip when crickets
     is unpaired), not harness-required, in both doctor surfaces.

What this retire deliberately does NOT touch: `scripts/recent-wiki-changes.{sh,ps1}`.
crickets' plugin ships its OWN copy of that script (a strict superset, adding a
find_agentm_script resolver so it works from the installed dist location), so
retiring agentm's command orphans nothing — and agentm's scripts remain the
direct-invocation surface for Antigravity + Gemini operators, who have no slash
command. Test 5 pins that they survive.

`CHANGELOG.md` and `wiki/` are excluded from the path scan: they are append-only
historical / design records that legitimately describe the old paths as a record
of what was. Their own gate is `check-wiki.py`, not this guard.

The scan sources candidate files from `git ls-files` rather than a raw filesystem
walk (mirroring test_diataxis_author_retired.py). That excludes any gitignored
path for free -- notably a stale merged-PR worktree checkout left under
`.claude/worktrees/<slug>/` by the worktree-native flow, which a plain walk would
descend into and pick up as a duplicate copy of this very file.
"""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve()

# The deleted local paths. Built by join so this source file does not itself
# contain the literal needles (it would otherwise self-match the scan).
RETIRED_SKILL_PATH = "harness/skills/" + "wiki-author"
RETIRED_COMMAND_PATH = "adapters/claude-code/commands/" + "recent-wiki-changes"
# The installed-side path the retired command used to occupy. A live surface
# still asserting this would re-break the install gates.
RETIRED_INSTALLED_COMMAND = ".claude/commands/" + "recent-wiki-changes"

EXCLUDED_DIRS = {".git", "wiki", "node_modules", "__pycache__"}
EXCLUDED_FILES = {"CHANGELOG.md"}
TEXT_SUFFIXES = {".md", ".py", ".sh", ".ps1", ".json", ".toml", ".yml", ".yaml"}

DOCTOR_SURFACES = (
    "harness/skills/doctor.md",
    "adapters/claude-code/skills/doctor/SKILL.md",
)


def _git_tracked_relpaths(root):
    """Relative paths `git` tracks under `root` (index + working tree)."""
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return [p for p in result.stdout.split("\0") if p]


def _iter_live_text_files(root=ROOT):
    for rel in _git_tracked_relpaths(root):
        path = root / rel
        if not path.is_file():
            continue
        if path.resolve() == SELF:
            continue  # don't self-match on the needles in this file
        rel_parts = Path(rel).parts
        if any(part in EXCLUDED_DIRS for part in rel_parts):
            continue
        if path.name in EXCLUDED_FILES:
            continue
        if path.suffix not in TEXT_SUFFIXES:
            continue
        yield path


class TestWikiDupesRetired(unittest.TestCase):
    def test_local_copies_removed(self):
        """Neither vendored copy exists in the harness tree."""
        self.assertFalse(
            (ROOT / "harness" / "skills" / "wiki-author").exists(),
            "agentm's wiki-author copy must be retired; crickets' `wiki` "
            "plugin is the single source.",
        )
        self.assertFalse(
            (ROOT / "adapters" / "claude-code" / "commands"
             / "recent-wiki-changes.md").exists(),
            "agentm's recent-wiki-changes command must be retired; crickets' "
            "`wiki` plugin is the single source.",
        )

    def test_no_claude_code_commands_vendored(self):
        """agentm vendors no claude-code command at all.

        recent-wiki-changes was the last one. The adapter dir may be absent
        entirely or present-but-empty; what must not happen is a *.md
        reappearing in it, which would re-create the bare-name shadowing this
        retire removed.
        """
        cmd_dir = ROOT / "adapters" / "claude-code" / "commands"
        found = sorted(p.name for p in cmd_dir.glob("*.md")) if cmd_dir.is_dir() else []
        self.assertEqual(
            found,
            [],
            "agentm ships no claude-code commands (the phase commands went to "
            "crickets' development-lifecycle in the V5 slim; recent-wiki-changes "
            f"went to crickets' wiki plugin in 2026-08-12's retire). Found: {found}",
        )

    def test_no_dangling_paths_in_live_surfaces(self):
        """No live surface references either deleted path.

        The bare names `wiki-author` / `recent-wiki-changes` stay legal
        (crickets provides both, and doctor must name them as crickets-provided
        so the graceful-skip contract is documented). Only the deleted PATHS —
        source-side and installed-side — are forbidden.
        """
        needles = (
            RETIRED_SKILL_PATH,
            RETIRED_COMMAND_PATH,
            RETIRED_INSTALLED_COMMAND,
        )
        offenders = []
        for path in _iter_live_text_files():
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                for needle in needles:
                    if needle in line:
                        rel = path.relative_to(ROOT)
                        offenders.append(f"{rel}:{lineno}: {line.strip()}")
                        break
        self.assertEqual(
            offenders,
            [],
            "live surfaces still reference a retired wiki-dupe path:\n  "
            + "\n  ".join(offenders),
        )

    def test_wiki_author_categorized_crickets_shipped(self):
        """Both doctor surfaces list wiki-author as crickets-provided.

        This is the crickets-absent contract: after the retire the harness
        assumes no local copy and degrades to suggest-then-skip rather than
        hard-failing on a missing required skill. Pinned by asserting the name
        is present (doctor must still say where the capability went) while the
        harness-required framing no longer claims it.
        """
        for rel in DOCTOR_SURFACES:
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn(
                "wiki-author",
                text,
                f"{rel}: doctor must still name wiki-author, as a "
                "crickets-provided skill (where the capability went).",
            )
            self.assertNotIn(
                "doctor, wiki-author",
                text,
                f"{rel}: wiki-author must no longer appear in the "
                "harness-required skills set — it is crickets-shipped now.",
            )

    def test_underlying_scripts_not_orphaned(self):
        """agentm's recent-wiki-changes.{sh,ps1} survive the command retire.

        Only the claude-code slash command was a duplicate. crickets ships its
        own copy of these scripts, so nothing crickets-side depends on agentm's
        — but Antigravity + Gemini operators invoke agentm's directly (they have
        no slash command), so removing them would be a real capability loss.
        """
        for name in ("recent-wiki-changes.sh", "recent-wiki-changes.ps1"):
            self.assertTrue(
                (ROOT / "scripts" / name).is_file(),
                f"scripts/{name} must survive the command retire — it is the "
                "direct-invocation surface for hosts without slash commands.",
            )


if __name__ == "__main__":
    unittest.main()
