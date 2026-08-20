#!/usr/bin/env python3
"""The fail-closed halt, proved at the stage that would otherwise file.

`standards/storage-rules.md` decides where a memory goes, and it is read at
runtime. Every source-touching stage in a dreaming pass — a merge, an expiry, a
shelving, a frontmatter repair — decides *where* by those rules. So when the
rules block will not parse, the honest behaviour is to propose nothing rather
than to file under a guess, and that is what these tests pin.

The design's own framing: "A block that fails validation halts enrichment
instead of degrading it: notes wait as `unfiled`, the digest names the parse
failure, and nothing files anywhere until the file parses again."

This file is the *digest* reader of the halt. The other two are
`scripts/check-storage-rules.py` (the gate, which fails CI) and
`machinery_doctor.py`'s `storage-rules` row (the operator surface).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import dream  # noqa: E402
import storage_rules  # noqa: E402


BROKEN_RULES = """\
# Storage rules

```storage-rules
memory_types: [unclosed
```
"""

VALID_RULES = """\
# Storage rules

```storage-rules
classes:
  semantic: Facts and principles.
  procedural: How to do a thing.
  episodic: Session traces.
  entities: One file per referent.
  crystallized: Distilled lessons.
  mocs: Maps of content.
memory_types: [preference, convention, reference, workflow, fix, idea]
default_type: preference
routing:
  preference: memory/semantic
  convention: memory/semantic
  reference: memory/semantic
  workflow: memory/procedural
  fix: memory/procedural
  idea: desk
record_kinds: [brief]
deprecations: {preferences: preference}
warrants: {}
thresholds: {low_confidence: 0.65}
```
"""


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir()

        self._saved = os.environ.get("AGENTM_STORAGE_RULES")
        self.addCleanup(self._restore_env)
        storage_rules._CACHE = None
        self.addCleanup(setattr, storage_rules, "_CACHE", None)

    def _restore_env(self) -> None:
        if self._saved is None:
            os.environ.pop("AGENTM_STORAGE_RULES", None)
        else:
            os.environ["AGENTM_STORAGE_RULES"] = self._saved

    def _rules(self, text: str) -> None:
        path = Path(self._tmp.name) / "storage-rules.md"
        path.write_text(text, encoding="utf-8")
        os.environ["AGENTM_STORAGE_RULES"] = str(path)
        storage_rules._CACHE = None

    def _write(self, name: str, content: str) -> Path:
        path = self.vault / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def _seed_a_mergeable_pair(self) -> list:
        """Two near-identical entries — a run with working rules proposes a
        merge over these, so their survival is what proves the halt held."""
        body = ("The daemon commits whatever git reports dirty, so a vault file "
                "does not need hand-committing before a gate will pass.\n")
        return [
            self._write("dup-a.md", f"---\nkind: workflow\nstatus: active\n---\n{body}"),
            self._write("dup-b.md", f"---\nkind: workflow\nstatus: active\n---\n{body}"),
        ]


class HaltTests(_Base):
    """A block that will not parse stops every stage that would re-file."""

    def test_broken_rules_propose_nothing(self) -> None:
        self._rules(BROKEN_RULES)
        self._seed_a_mergeable_pair()
        digest = dream.run_dream(self.vault, run_id="halted")
        self.assertEqual(digest.proposals, [])
        self.assertEqual(digest.insight_candidates, [])

    def test_the_same_corpus_does_propose_when_the_rules_parse(self) -> None:
        """The control. Without it, "proposed nothing" proves nothing — an empty
        proposal list is also what an inert fixture produces."""
        self._rules(VALID_RULES)
        self._seed_a_mergeable_pair()
        digest = dream.run_dream(self.vault, run_id="healthy")
        self.assertTrue(
            digest.proposals,
            "the fixture must be one a working run acts on, or the halt test is vacuous",
        )

    def test_no_note_is_touched_by_a_halted_run(self) -> None:
        self._rules(BROKEN_RULES)
        paths = self._seed_a_mergeable_pair()
        before = {p: p.read_bytes() for p in paths}
        dream.run_dream(self.vault, run_id="halted")
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content, f"{path.name} was modified")

    def test_the_digest_names_the_parse_failure(self) -> None:
        self._rules(BROKEN_RULES)
        self._write("solo.md", "---\nkind: workflow\n---\nOne note.\n")
        digest = dream.run_dream(self.vault, run_id="halted")
        text = digest.digest_path.read_text(encoding="utf-8")
        self.assertIn("Filing is halted", text)
        self.assertIn("not valid YAML", text)

    def test_the_read_only_meters_still_report(self) -> None:
        """A halt is not a blackout. The corpus is still described; it is just
        not acted on."""
        self._rules(BROKEN_RULES)
        self._write("solo.md", "---\nkind: workflow\n---\nOne note.\n")
        digest = dream.run_dream(self.vault, run_id="halted")
        self.assertEqual(digest.corpus_stats["entry_count"], 1)
        self.assertFalse(digest.corpus_stats["storage_rules_ok"])


class HashWatchTests(_Base):
    """A rules edit is loud by construction."""

    def test_first_run_records_without_claiming_a_change(self) -> None:
        self._rules(VALID_RULES)
        watch = storage_rules.hash_watch(self.vault)
        self.assertTrue(watch["first_run"])
        self.assertFalse(watch["changed"])
        self.assertTrue((self.vault / "_meta" / "storage-rules-state.json").is_file())

    def test_an_unchanged_second_run_reports_no_change(self) -> None:
        self._rules(VALID_RULES)
        storage_rules.hash_watch(self.vault)
        watch = storage_rules.hash_watch(self.vault)
        self.assertFalse(watch["changed"])
        self.assertFalse(watch["first_run"])

    def test_an_edited_block_reports_changed_with_the_old_hash(self) -> None:
        self._rules(VALID_RULES)
        first = storage_rules.hash_watch(self.vault)["current"]
        self._rules(VALID_RULES.replace("low_confidence: 0.65", "low_confidence: 0.8"))
        watch = storage_rules.hash_watch(self.vault)
        self.assertTrue(watch["changed"])
        self.assertEqual(watch["previous"], first)
        self.assertNotEqual(watch["current"], first)

    def test_the_digest_separates_stale_from_never_judged(self) -> None:
        """Two different populations. A stale memory is re-filing work; an
        unjudged one is backlog, and reporting them as one number misstates
        both."""
        self._rules(VALID_RULES)
        current = storage_rules.load().content_hash()
        self._write("judged.md", f"---\nkind: workflow\nrules_hash: {current}\n---\nCurrent.\n")
        self._write("stale.md", "---\nkind: workflow\nrules_hash: 0000000000000000\n---\nOld.\n")
        self._write("never.md", "---\nkind: workflow\n---\nNo stamp at all.\n")
        digest = dream.run_dream(self.vault, run_id="counts")
        self.assertEqual(digest.corpus_stats["storage_rules_stale_count"], 1)
        self.assertEqual(digest.corpus_stats["storage_rules_unjudged_count"], 1)

    def test_the_digest_announces_a_changed_hash(self) -> None:
        self._rules(VALID_RULES)
        storage_rules.hash_watch(self.vault)
        self._rules(VALID_RULES.replace("low_confidence: 0.65", "low_confidence: 0.8"))
        self._write("solo.md", "---\nkind: workflow\n---\nOne note.\n")
        digest = dream.run_dream(self.vault, run_id="changed")
        text = digest.digest_path.read_text(encoding="utf-8")
        self.assertIn("**changed** since the last cycle", text)


if __name__ == "__main__":
    unittest.main()
