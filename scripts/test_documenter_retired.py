#!/usr/bin/env python3
"""Retire-invariant guard for the duplicate `documenter` sub-agent copy.

agentm used to vendor its own copy of the `documenter` sub-agent — one harness
spec plus three adapter copies — of a sub-agent crickets now owns canonically
at `crickets/src/wiki-maintenance/agents/documenter.md`. The seven-section
convergence follow-on (disposition (b), the ADR 0006 single-source move)
removed agentm's four copies and rewired every live dispatch site to defer to
crickets' `wiki-maintenance:documenter` with the graceful-skip contract
(suggest-then-skip when crickets is absent — never hard-fail). It mirrors the
`diataxis-author` retire (see test_diataxis_author_retired.py).

These tests pin that invariant so a later change can't silently re-introduce a
local copy or a dangling path dependency on one:

  1. The four vendored files are gone.
  2. No *live* surface (code / install scripts / contracts / phase specs /
     adapters) references a deleted local PATH. The bare sub-agent *name*
     `documenter` stays legal everywhere — crickets provides it, the phase
     specs dispatch it by name, and `harness_memory.py` keys its recall
     pseudo-phase on it. Only the four vanished local file paths are forbidden.

     NOTE on the needle choice: crickets' canonical path
     `src/wiki-maintenance/agents/documenter.md` *contains* the suffix
     `agents/documenter.md`, so a bare-suffix scan would false-positive on
     every legitimate crickets redirect URL. The guard therefore anchors on
     the four exact deleted-local prefixes, which the crickets path never
     matches.
  3. The sub-agent is categorized as crickets-shipped (graceful-skip if
     crickets is not paired), not as a harness-required sub-agent, in both
     doctor surfaces.
  4. KEEP boundary — the doc-write-time recall ENGINE stays in agentm. It is
     an integration point crickets' documenter calls into via preflight
     (`python3 scripts/harness_memory.py documenter-context --slug ...`), not a
     duplicate of the sub-agent. So `harness_memory.py` must still define the
     `documenter-context` subcommand + the `"documenter"` recall pseudo-phase,
     and `scripts/test_harness_memory_documenter.py` must still exist.

`CHANGELOG.md` and `wiki/` are excluded from the path scan: they are
append-only historical / decision records that legitimately describe the old
paths as a record of what was (ADRs 0002/0004 cite the old sub-agent file, the
release note shipped the copies). Their own gate is `check-wiki.py`, not this
guard.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve()

# The four deleted local paths. Built by join so this source file does not
# itself contain the literal needles (it would otherwise self-match the scan).
RETIRED_PATHS = (
    "harness/agents/" + "documenter.md",
    "adapters/claude-code/agents/" + "documenter.md",
    "adapters/gemini/agents/" + "documenter.md",
    "adapters/antigravity/skills/documenter/" + "SKILL.md",
)

# Append-only historical / decision records that legitimately retain the old
# paths as a record of what was. Excluded from the live-surface scan.
# `.harness` is gitignored, untracked machine-local planning/design state (the
# V4 design docs cite the old sub-agent path as a historical assumption) — not
# a shipped harness surface, so it gets the same exclusion as wiki/CHANGELOG.
EXCLUDED_DIRS = {".git", ".harness", "wiki", "node_modules", "__pycache__"}
EXCLUDED_FILES = {"CHANGELOG.md"}
TEXT_SUFFIXES = {".md", ".py", ".sh", ".ps1", ".json", ".toml", ".yml", ".yaml"}


def _iter_live_text_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.resolve() == SELF:
            continue  # don't self-match on the needles in this file
        rel_parts = path.relative_to(ROOT).parts
        if any(part in EXCLUDED_DIRS for part in rel_parts):
            continue
        if path.name in EXCLUDED_FILES:
            continue
        if path.suffix not in TEXT_SUFFIXES:
            continue
        yield path


class TestDocumenterRetired(unittest.TestCase):
    def test_local_files_removed(self):
        """The four vendored copies no longer exist in the tree."""
        for rel in RETIRED_PATHS:
            self.assertFalse(
                (ROOT / rel).exists(),
                f"agentm's vendored documenter copy `{rel}` must be retired "
                "(disposition (b)); crickets' wiki-maintenance plugin is the "
                "single source.",
            )

    def test_no_dangling_path_in_live_surfaces(self):
        """No live surface references a deleted local path.

        The bare sub-agent name `documenter` is allowed (crickets provides it,
        the phase specs dispatch it by name); crickets' canonical
        `src/wiki-maintenance/agents/documenter.md` is allowed (it's the
        redirect target). Only the four deleted local paths are forbidden.
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
            "Dangling reference to a retired local documenter path in live "
            "surface(s) — rewire to crickets' wiki-maintenance:documenter with "
            "graceful-skip:\n" + "\n".join(offenders),
        )

    def test_doctor_categorizes_documenter_as_crickets(self):
        """Both doctor surfaces list documenter as crickets-shipped.

        Pins the crickets-absent graceful-skip contract: documenter is grouped
        with the other crickets-shipped sub-agents (graceful-skip if crickets
        is not paired), not injected into the harness-required sub-agent list.
        """
        name = "documenter"
        crickets_group = "diataxis-evaluator, documenter, evaluator"
        # Post-V5 dev-loop slim, the harness-required sub-agent set is the
        # memory-engine pair — the three review agents (adversarial-reviewer /
        # -cross / explorer) moved to crickets, so they are no longer the
        # required list. documenter must still sit in the crickets group, never
        # injected into this required list.
        required_list = "adapt-evaluator, memory-idea-researcher"
        for rel in (
            "harness/skills/doctor.md",
            "adapters/claude-code/skills/doctor/SKILL.md",
        ):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn(name, text, f"{rel} should still mention {name}")
            self.assertIn(
                crickets_group,
                text,
                f"{rel}: documenter should sit in the crickets-shipped "
                f"sub-agent group `{crickets_group}`.",
            )
            self.assertIn(
                required_list,
                text,
                f"{rel}: the harness-required sub-agent list `{required_list}` "
                "must stay intact (documenter not injected into it).",
            )
            self.assertNotIn(
                "explorer, documenter",
                text,
                f"{rel}: documenter must not be appended to the "
                "harness-required sub-agent list.",
            )

    def test_keep_documenter_context_engine(self):
        """The doc-write-time recall ENGINE stays in agentm.

        It's the integration point crickets' documenter calls into via
        preflight — NOT a duplicate of the sub-agent. So `harness_memory.py`
        must still define the `documenter-context` subcommand + the
        `"documenter"` recall pseudo-phase, and its test must still exist.
        """
        mem = (ROOT / "scripts" / "harness_memory.py").read_text(encoding="utf-8")
        self.assertIn(
            "documenter-context",
            mem,
            "harness_memory.py must keep the `documenter-context` subcommand — "
            "it is crickets' documenter preflight recall engine, not a "
            "duplicate of the sub-agent.",
        )
        # The PHASES tuple must still carry the `documenter` recall pseudo-phase.
        self.assertIn(
            '"bugfix", "documenter"',
            mem,
            "harness_memory.py must keep `documenter` in its recall-phase "
            "tuple — the context-load surface the documenter sub-agent reads.",
        )
        self.assertTrue(
            (ROOT / "scripts" / "test_harness_memory_documenter.py").exists(),
            "scripts/test_harness_memory_documenter.py must still exist — it "
            "guards the recall engine that stays in agentm.",
        )


if __name__ == "__main__":
    unittest.main()
