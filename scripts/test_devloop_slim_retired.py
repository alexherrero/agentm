#!/usr/bin/env python3
"""Retire-invariant guard for the V5 dev-loop slim (+ docs-slim residue).

agentm used to vendor the full phase-gated dev loop — the six phase commands
(`setup/plan/work/review/release/bugfix`), the three review sub-agents
(`adversarial-reviewer` / `-cross` / `explorer`), and the `evidence-tracker`
hook — across all three host adapters plus the shared `harness/` source. The V5
"unbundling" (ROADMAP bucket ⑤) removed every one of those copies: they are now
provided solely by the launched + dogfood-proven crickets **developer-workflows**
/ **code-review** plugins. Per DC-2 the slim is a *clean delete*, not a
graceful-skip-with-pointer: agentm ships no invocation surface for the dev loop
and says nothing about it — if crickets is absent, a bare agentm simply has no
`/plan /work /review`. It mirrors the `documenter` / `diataxis-author` retires
(see test_documenter_retired.py).

The same unbundling retired the **docs-slim residue** — agentm's four-mode
`migrate-to-diataxis` skill (`harness/skills/migrate-to-diataxis.md` + its
Claude Code adapter copy). Its one-shot wiki conversion is now provided by
crickets' `wiki-maintenance` / `diataxis-author` (`/diataxis migrate`). Unlike
the dev-loop clean-delete, the *bare name* `migrate-to-diataxis` legitimately
survives in `doctor`'s graceful crickets-provider prose (telling an operator
who had it pre-V5 where the capability went) — only the deleted FILE paths are
pinned absent here (DC-3).

These tests pin that invariant so a later change can't silently re-introduce a
local copy or a dangling path dependency on one:

  1. The deleted dev-loop files are gone.
  2. No *live* surface (code / install scripts / contracts / adapters / skills)
     references a deleted local PATH. Bare command / sub-agent *names* (`/plan`,
     `explorer`, `bugfix`, …) stay legal everywhere — crickets provides them,
     and the doctor skill names them as crickets-provided. Bare *directory*
     mentions (`harness/phases/`, `adapters/antigravity/workflows/`) in
     explanatory comments are also legal — only the exact vanished FILE paths
     are forbidden, which is what a dangling link/dependency would contain.
  3. Both doctor surfaces categorize the six phase commands + three review
     sub-agents as crickets-provided (graceful-skip, never FAIL), with the
     memory-engine pair (`adapt-evaluator`, `memory-idea-researcher`) as the
     harness-required sub-agent set.
  4. KEEP boundary — the memory engine stays in agentm, byte-untouched by the
     slim. The kept sub-agents, the memory hooks, the memory skill, and
     `harness_memory.py` must still exist.

`CHANGELOG.md`, `wiki/`, and `.harness/` are excluded from the path scan: they
are append-only historical / decision / machine-local-planning records that
legitimately describe the old paths as a record of what was (the V5 ADR and the
release note cite the deleted files; the wiki's reference pages link them). Their
own gate is `check-wiki.py`, not this guard.

The scan sources candidate files from `git ls-files` rather than a raw
filesystem walk (mirrors the file-source `check-no-pii.sh` already uses for
its repo-wide scan). That excludes any gitignored path for free -- notably a
stale merged-PR worktree checkout left under `.claude/worktrees/<slug>/` by
the worktree-native flow, which a plain walk would descend into and pick up
as a duplicate copy of one of these guard files (flagged as a known
follow-up in the v5.12.0 CHANGELOG entry; confirmed twice more during the
v5.14.0 release; see test_diataxis_author_retired.py).
"""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve()

# ── deleted dev-loop file paths ──────────────────────────────────────────────
# Built by f-string assembly from components so this source file never contains
# an assembled needle literally (it would otherwise self-match the scan; the
# SELF exclusion below is the belt-and-suspenders second guard).
_PHASE_SPECS = ("01-setup", "02-plan", "03-work", "04-review", "05-release")
_PHASE_CMDS = ("setup", "plan", "work", "review", "release", "bugfix")
_REVIEW_AGENTS = ("adversarial-reviewer", "adversarial-reviewer-cross", "explorer")
_EVIDENCE = ("hook.md", "evidence-tracker.sh", "evidence-tracker.ps1", "evidence_tracker.py")
# Docs-slim residue (DC-3): the four-mode diataxis-migration skill. Assembled
# from parts so the full deleted PATH never appears literally in this file (the
# bare name token is harmless — only the path needles drive the dangling scan).
_MIGRATE = "migrate-" + "to-" + "diataxis"

RETIRED_PATHS = tuple(
    [f"harness/phases/{p}.md" for p in _PHASE_SPECS]
    + [f"harness/pipelines/{'bugfix'}.md"]
    + [f"harness/agents/{a}.md" for a in _REVIEW_AGENTS]
    + [f"harness/hooks/evidence-tracker/{f}" for f in _EVIDENCE]
    + [f"adapters/claude-code/commands/{c}.md" for c in _PHASE_CMDS]
    + [f"adapters/claude-code/agents/{a}.md" for a in _REVIEW_AGENTS]
    + [f"adapters/antigravity/workflows/{c}.md" for c in _PHASE_CMDS]
    + [f"adapters/antigravity/skills/{a}/SKILL.md" for a in _REVIEW_AGENTS]
    + [f"adapters/gemini/commands/{c}.toml" for c in _PHASE_CMDS]
    + [f"adapters/gemini/agents/{a}.md" for a in _REVIEW_AGENTS]
    # docs-slim residue — the retired migration skill's two vendored copies
    + [f"harness/skills/{_MIGRATE}.md"]
    + [f"adapters/claude-code/skills/{_MIGRATE}/SKILL.md"]
)

# ── memory-engine KEEP boundary (must survive the slim, byte-untouched) ──────
KEPT_PATHS = (
    "harness/agents/adapt-evaluator.md",
    "harness/agents/memory-idea-researcher.md",
    "harness/skills/memory",
    "harness/skills/doctor.md",
    # NB: `harness/hooks/conflict-merger-session-start` was a KEEP path until
    # V5-2 task 2 re-homed the vault conflict sweep out of this storage-agnostic
    # kernel into the crickets obsidian-vault plugin. It is no longer kept here —
    # the plugin owns the hook now. (Not added to RETIRED_PATHS: that set is the
    # V5 dev-loop slim, a different axis from the V5-2 vault re-home.)
    "harness/hooks/harness-context-session-start",
    "harness/hooks/memory-recall-prompt-submit",
    "harness/hooks/memory-recall-session-start",
    "harness/hooks/memory-reflect-idle",
    "harness/hooks/memory-reflect-stop",
    "scripts/harness_memory.py",
)

EXCLUDED_DIRS = {".git", ".harness", "wiki", "node_modules", "__pycache__"}
EXCLUDED_FILES = {"CHANGELOG.md"}
TEXT_SUFFIXES = {".md", ".py", ".sh", ".ps1", ".json", ".toml", ".yml", ".yaml"}


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


class TestDevloopSlimRetired(unittest.TestCase):
    def test_local_files_removed(self):
        """The deleted dev-loop primitives no longer exist in the tree."""
        for rel in RETIRED_PATHS:
            self.assertFalse(
                (ROOT / rel).exists(),
                f"agentm's vendored dev-loop primitive `{rel}` must be retired "
                "(V5 dev-loop slim); the crickets developer-workflows / "
                "code-review plugins are the sole providers.",
            )

    def test_no_dangling_path_in_live_surfaces(self):
        """No live surface references a deleted local dev-loop path.

        Bare command / sub-agent names and bare directory mentions are allowed;
        only the exact vanished FILE paths are forbidden (a dangling link or a
        broken install/test dependency is what would contain one).
        """
        offenders = []
        for path in _iter_live_text_files():
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                for needle in RETIRED_PATHS:
                    if needle in line:
                        rel = path.relative_to(ROOT)
                        offenders.append(f"{rel}:{lineno}: {line.strip()}")
                        break
        self.assertEqual(
            offenders,
            [],
            "Dangling reference to a retired dev-loop path in live surface(s) — "
            "the dev loop is crickets-provided now; drop the path or repoint it "
            "to a surviving file:\n" + "\n".join(offenders),
        )

    def test_stale_worktree_checkout_excluded_from_scan(self):
        """A stale merged-PR worktree under `.claude/worktrees/<slug>/` must
        not surface a duplicate copy of this file to the scanner.

        Regression for the `check-all.sh` false-positive class flagged in the
        v5.12.0 CHANGELOG entry (see test_diataxis_author_retired.py's sibling
        test for the full write-up).
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            fake_root = Path(tmp)
            subprocess.run(
                ["git", "init", "-q"], cwd=fake_root, check=True, timeout=10
            )
            (fake_root / ".gitignore").write_text(
                ".claude/worktrees/\n", encoding="utf-8"
            )
            live = fake_root / "scripts" / "some_live_module.py"
            live.parent.mkdir(parents=True)
            live.write_text("# a real, tracked file\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "-A"], cwd=fake_root, check=True, timeout=10
            )

            stale = (
                fake_root
                / ".claude"
                / "worktrees"
                / "fake-slug"
                / "scripts"
                / "test_devloop_slim_retired.py"
            )
            stale.parent.mkdir(parents=True)
            stale.write_text(
                "# duplicate copy left by a stale merged-PR worktree\n"
                f"# forbidden needle: {RETIRED_PATHS[0]}\n",
                encoding="utf-8",
            )

            scanned = {
                path.relative_to(fake_root)
                for path in _iter_live_text_files(root=fake_root)
            }
            self.assertIn(
                Path("scripts/some_live_module.py"),
                scanned,
                "a genuinely tracked live file must still be scanned",
            )
            self.assertNotIn(
                Path(
                    ".claude/worktrees/fake-slug/scripts/"
                    "test_devloop_slim_retired.py"
                ),
                scanned,
                "the scan must not descend into a gitignored "
                "`.claude/worktrees/` checkout",
            )

    def test_doctor_categorizes_devloop_as_crickets(self):
        """Both doctor surfaces categorize the dev loop as crickets-provided.

        The six phase commands + the three review sub-agents are crickets-
        provided (graceful-skip, never FAIL); the memory-engine pair is the
        harness-required sub-agent set.
        """
        required_subagents = "adapt-evaluator, memory-idea-researcher"
        phase_commands = "bugfix, plan, release, review, setup, work"
        review_agents = "adversarial-reviewer, adversarial-reviewer-cross, explorer"
        for rel in (
            "harness/skills/doctor.md",
            "adapters/claude-code/skills/doctor/SKILL.md",
        ):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn(
                required_subagents,
                text,
                f"{rel}: the memory-engine pair `{required_subagents}` must be "
                "the harness-required sub-agent set after the slim.",
            )
            self.assertIn(
                phase_commands,
                text,
                f"{rel}: the six phase commands `{phase_commands}` must be named "
                "as crickets-provided (developer-workflows).",
            )
            self.assertIn(
                review_agents,
                text,
                f"{rel}: the review agents `{review_agents}` must be named as "
                "crickets-provided (code-review / developer-workflows).",
            )
            self.assertIn(
                "developer-workflows",
                text,
                f"{rel}: the doctor must name the crickets developer-workflows "
                "plugin as the dev-loop provider.",
            )
            # The phase commands must NOT be re-listed as harness-required.
            self.assertNotIn(
                "**Phase commands** (required)",
                text,
                f"{rel}: phase commands must not be framed as harness-required "
                "after the V5 dev-loop slim.",
            )

    def test_migrate_to_diataxis_doctor_reframe(self):
        """migrate-to-diataxis left the required-skills set (docs slim, DC-3).

        The skill is deleted; doctor's harness-required skills are now `doctor,
        wiki-author`. The bare name may survive only as crickets-provider
        graceful prose (where the capability went) — both surfaces phrase that
        via crickets' `diataxis-author`, which absorbed the migration.
        """
        required_skills = "doctor, wiki-author"
        for rel in (
            "harness/skills/doctor.md",
            "adapters/claude-code/skills/doctor/SKILL.md",
        ):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn(
                required_skills,
                text,
                f"{rel}: after the docs slim the harness-required skills must be "
                f"`{required_skills}` (the four-mode migration skill retired to "
                "crickets).",
            )
            self.assertIn(
                "diataxis-author",
                text,
                f"{rel}: doctor must name crickets' diataxis-author as the "
                "provider that absorbed the retired migration skill.",
            )

    def test_keep_memory_engine(self):
        """The memory engine stays in agentm — the slim must not remove it."""
        for rel in KEPT_PATHS:
            self.assertTrue(
                (ROOT / rel).exists(),
                f"memory-engine path `{rel}` must survive the V5 dev-loop slim "
                "(byte-untouched KEEP boundary).",
            )


if __name__ == "__main__":
    unittest.main()
