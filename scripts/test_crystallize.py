#!/usr/bin/env python3
"""Unit tests for `crystallize.py` — phase-close crystallization (AG Wave E
experience plan, task 2).

`crystallize.py` lives in `harness/skills/memory/scripts/` (same cross-dir
import pattern as `test_dream.py` / `test_forward_learning.py`), and
reuses `save.py`'s `save_entry` as its write primitive.

Covers (plan task 2 verification):
  - red-test: crystallizing a fixture exploration produces a digest that
    round-trips through `parse_digest` to the EXACT five-field schema
  - no raw transcript fragment (or any file other than the one crystallized
    entry) is persisted alongside it
  - a malformed/incomplete entry (missing a locked section) raises
    MalformedDigestError from parse_digest — the schema is exact, not
    best-effort
  - the entry lands at the as-built <vault>/<group>/crystallized/<slug>.md
    path (save.py's convention), with kind: crystallized in frontmatter
  - the existing FileExistsError-on-collision contract (never silently
    overwrite) is inherited from save_entry, not reimplemented
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import crystallize as cz  # noqa: E402


_FIXTURE_DIGEST = cz.CrystallizationDigest(
    question="Does the revert-log need per-stage locking or one lock for the whole pass?",
    investigation="Read agentm-runner.md's locked mutex-discipline call and vault_lock.py's existing primitives.",
    findings="Per-stage locking is the locked design call; a whole-pass lock would starve concurrent sessions.",
    lessons="Reuse existing primitives (vault_mutex) rather than inventing new locking for a new module.",
    open_threads="Whether the confirm/expire flow needs its own separate lock to avoid a state.json RMW race.",
)


class _CrystallizeTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir()

    def _snapshot(self) -> set:
        # .as_posix() normalizes to forward slashes -- rglob'd paths carry
        # native separators (backslashes on Windows), and this snapshot's
        # entries get compared against forward-slash-literal expectations.
        return {p.relative_to(self.vault).as_posix() for p in self.vault.rglob("*") if p.is_file()}


class RedTestFixtureExplorationTests(_CrystallizeTestBase):
    def test_digest_round_trips_through_the_exact_five_field_schema(self) -> None:
        path = cz.crystallize_exploration(self.vault, "revert-log-locking-discipline", _FIXTURE_DIGEST)

        parsed = cz.parse_digest(path)
        self.assertEqual(parsed, _FIXTURE_DIGEST)

    def test_no_raw_transcript_fragment_persisted_alongside_it(self) -> None:
        pre = self._snapshot()
        cz.crystallize_exploration(self.vault, "revert-log-locking-discipline", _FIXTURE_DIGEST)
        post = self._snapshot()

        new_files = post - pre
        # Exactly the one crystallized entry (plus, harmlessly, the async
        # embedding-queue sidecar under _meta/ that save_entry always
        # appends to — never a second content file, never a raw transcript).
        content_files = [f for f in new_files if not f.startswith("_meta")]
        self.assertEqual(
            content_files,
            ["personal/crystallized/revert-log-locking-discipline.md"],
        )

    def test_entry_lands_at_the_as_built_group_kind_slug_path(self) -> None:
        path = cz.crystallize_exploration(self.vault, "revert-log-locking-discipline", _FIXTURE_DIGEST)
        self.assertEqual(
            path,
            self.vault / "personal" / "crystallized" / "revert-log-locking-discipline.md",
        )
        self.assertIn("kind: crystallized", path.read_text(encoding="utf-8"))

    def test_body_contains_all_five_locked_section_headers(self) -> None:
        path = cz.crystallize_exploration(self.vault, "revert-log-locking-discipline", _FIXTURE_DIGEST)
        text = path.read_text(encoding="utf-8")
        for title in ("Question", "Investigation", "Findings", "Lessons", "Open threads"):
            self.assertIn(f"## {title}", text)


class MalformedDigestTests(_CrystallizeTestBase):
    def test_missing_section_raises_malformed_digest_error(self) -> None:
        entry = self.vault / "personal" / "crystallized" / "incomplete.md"
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text(
            "---\nkind: crystallized\n---\n"
            "## Question\n\nWhat happened?\n\n"
            "## Investigation\n\nLooked around.\n\n"
            # Findings / Lessons / Open threads deliberately omitted.
            ,
            encoding="utf-8",
        )
        with self.assertRaises(cz.MalformedDigestError):
            cz.parse_digest(entry)


class CollisionContractInheritedFromSaveEntryTests(_CrystallizeTestBase):
    def test_second_crystallization_of_same_slug_raises(self) -> None:
        cz.crystallize_exploration(self.vault, "dup-slug", _FIXTURE_DIGEST)
        with self.assertRaises(FileExistsError):
            cz.crystallize_exploration(self.vault, "dup-slug", _FIXTURE_DIGEST)


if __name__ == "__main__":
    unittest.main()
