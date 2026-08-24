#!/usr/bin/env python3
"""Capture-dedup tests for the reflection routing path.

The inbox and the opinion-supplement lane both wrote through a bare filename
collision handler: if `<slug>.md` existed, the writer appended `-1`, `-2`, `-3`
and kept going, forever. That handler was never a dedup check — nothing
anywhere compared the incoming candidate against what was already on disk — so
re-mining a transcript wrote a byte-identical note again under the next free
number. It is why `always-lies-about-whether-a-branch-12.md` exists: one
operator sentence, thirteen files.

Re-mining is legitimate. A transcript grows, and reflection is invoked again on
the longer file; that has to keep working. What must not happen is the same
candidate landing twice. So the contract these tests pin is a content contract,
not a filename one: the same candidate written twice is one file, a *different*
candidate that happens to slug the same is still two.

Run: python3 scripts/test_reflect_capture_dedup.py
"""
from __future__ import annotations

import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import reflect  # noqa: E402


def _cand(body, *, confidence="LOW", category="preferences", slug="a-slug",
          title="A title", excerpts=None):
    return reflect.Candidate(
        category=category, confidence=confidence, slug=slug,
        title=title, body=body, rationale="test",
        excerpts=list(excerpts or []),
    )


# A standard-shaped rule — the classifier diverts this to the opinion lane.
_STANDARD_BODY = "Never commit without check-all.sh passing green first."
# An ordinary observation — routes to _inbox/ on the normal ladder.
_ORDINARY_BODY = "User stated: the vault root sits outside the repo checkout."


class _Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="reflect-dedup-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "memory").mkdir(parents=True)

    def _route(self, cands, *, session_id=None):
        return reflect.route_candidates(
            cands, [], vault=self.root, mode=reflect.ROUTE_MODE_AUTO,
            session_id=session_id,
            stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO(),
        )

    def _inbox_files(self):
        d = self.root / "memory" / "_inbox"
        return sorted(p.name for p in d.glob("*.md")) if d.is_dir() else []

    def _lane_files(self, opinion="done"):
        d = self.root / "memory" / "_opinions" / opinion
        return sorted(p.name for p in d.glob("*.md")) if d.is_dir() else []


class TestInboxDedup(_Base):
    def test_same_candidate_routed_twice_writes_one_file(self):
        # The 13-copy defect in miniature: one candidate, two routing passes,
        # as happens whenever the same transcript is reflected more than once.
        c = _cand(_ORDINARY_BODY, slug="dupe")
        self._route([c])
        self._route([_cand(_ORDINARY_BODY, slug="dupe")])
        self.assertEqual(self._inbox_files(), ["dupe.md"])

    def test_repeat_is_counted_not_silently_dropped(self):
        # A skipped write must be visible in the stats. Reporting it as an
        # ordinary `inboxed` would overstate what landed; reporting nothing at
        # all would make a re-mine indistinguishable from a no-candidate run.
        self._route([_cand(_ORDINARY_BODY, slug="dupe")])
        stats = self._route([_cand(_ORDINARY_BODY, slug="dupe")])
        self.assertEqual(stats["inboxed"], 0)
        self.assertEqual(stats["deduped"], 1)
        self.assertEqual(stats["errors"], 0)

    def test_thirteen_passes_still_leave_one_file(self):
        # Bounded, not merely reduced: the observed cluster reached -12 because
        # nothing capped the counter, so re-running to that depth is the test.
        for _ in range(13):
            self._route([_cand(_ORDINARY_BODY, slug="dupe")])
        self.assertEqual(self._inbox_files(), ["dupe.md"])

    def test_different_body_same_slug_still_keeps_both(self):
        # The collision handler stays. Two genuinely different captures that
        # slug alike are two notes, and suppressing the second would lose a
        # real capture — a worse failure than the duplication being fixed.
        self._route([_cand("User stated: first thing.", slug="dupe")])
        self._route([_cand("User stated: second, different thing.", slug="dupe")])
        self.assertEqual(self._inbox_files(), ["dupe-1.md", "dupe.md"])

    def test_a_longer_transcript_does_not_write_a_second_copy(self):
        # The shape re-mining actually produces. A transcript grows, the same
        # pattern matches again further down, and the candidate comes back with
        # a higher occurrence count and more excerpts — but the same body, which
        # is cut from the FIRST match and so does not move. On the live vault
        # this is the dominant case: `never-fan-out-parallel-implementers` has
        # 132 files, 5 distinct occurrence counts, and 2 distinct bodies. Keying
        # on anything that grows with the transcript would let all 132 through.
        self._route([_cand(_ORDINARY_BODY, slug="dupe", excerpts=["one"])])
        c = _cand(_ORDINARY_BODY, slug="dupe", excerpts=["one", "two", "three"])
        c.occurrences = 3
        self._route([c])
        self.assertEqual(self._inbox_files(), ["dupe.md"])


class TestOpinionLaneDedup(_Base):
    def test_same_supplement_routed_twice_writes_one_file(self):
        # The lane duplicated harder than the inbox did (86.8% vs 68.5% of
        # files redundant), and it feeds the recurrence gate, so it needs the
        # same guard rather than a weaker one.
        self._route([_cand(_STANDARD_BODY, slug="dupe")], session_id="proj/aaa")
        self._route([_cand(_STANDARD_BODY, slug="dupe")], session_id="proj/aaa")
        self.assertEqual(self._lane_files(), ["dupe.md"])

    def test_repeat_supplement_is_counted_not_silently_dropped(self):
        self._route([_cand(_STANDARD_BODY, slug="dupe")], session_id="proj/aaa")
        stats = self._route([_cand(_STANDARD_BODY, slug="dupe")], session_id="proj/aaa")
        self.assertEqual(stats["opinion_supplements"], 0)
        self.assertEqual(stats["deduped"], 1)
        self.assertEqual(stats["errors"], 0)

    def test_a_second_session_still_writes_its_own_entry(self):
        # The recurrence gate promotes on two DISTINCT session ids. Deduping
        # across sessions would remove the very signal it counts, so the guard
        # has to stop at the session boundary and no further.
        self._route([_cand(_STANDARD_BODY, slug="dupe")], session_id="proj/aaa")
        self._route([_cand(_STANDARD_BODY, slug="dupe")], session_id="proj/bbb")
        self.assertEqual(self._lane_files(), ["dupe-1.md", "dupe.md"])

    def test_different_supplement_same_slug_still_keeps_both(self):
        self._route([_cand("Never push without a green gate.", slug="dupe")],
                    session_id="proj/aaa")
        self._route([_cand("Never merge without a green gate.", slug="dupe")],
                    session_id="proj/aaa")
        self.assertEqual(self._lane_files(), ["dupe-1.md", "dupe.md"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
