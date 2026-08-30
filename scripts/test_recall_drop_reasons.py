#!/usr/bin/env python3
"""Why an empty recall was empty — the drop counters.

`hit_count: 0` with `hits: []` cannot separate a retrieval miss from
over-filtering, and those have opposite fixes. These tests pin the counts that
tell them apart, and — just as load-bearing — pin what is *not* recorded: no
terms, no prompt text, nothing prompt-derived.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

_REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "harness" / "skills" / "memory" / "scripts"))

import recall  # noqa: E402
import recall_counter  # noqa: E402


def daemon_returning(paths: list):
    """A daemon answering with these paths, as the hook would see them."""
    payload = {"results": [{"path": p, "score": 1.0} for p in paths]}
    done = __import__("subprocess").CompletedProcess(
        [], 0, stdout=json.dumps(payload), stderr="")
    return mock.patch.object(recall.subprocess, "run", return_value=done)


class TheDropCounters(unittest.TestCase):
    def _search(self, paths: list, **kw):
        drops: dict = {}
        with tempfile.TemporaryDirectory() as d:
            vault = pathlib.Path(d)
            (vault / "Agent" / "memory").mkdir(parents=True)
            with daemon_returning(paths):
                got = recall._daemon_search(
                    vault=vault, query_text="a real question here",
                    k=5, drops=drops, **kw)
        return got, drops

    def test_an_empty_daemon_response_records_zero_returned(self):
        # The retrieval-miss case: nothing came back, so nothing was filtered.
        got, drops = self._search([])
        self.assertEqual(drops["returned"], 0)
        self.assertEqual(sum(v for k, v in drops.items() if k != "returned"), 0)
        self.assertFalse(got)

    def test_all_inadmissible_records_five_returned_and_five_dropped(self):
        # The over-filtering case, and the whole reason this task exists: five
        # results found, none survived — indistinguishable from the case above
        # in the ledger as it stood.
        got, drops = self._search([
            f"Agent/memory/_inbox/n{i}.md" for i in range(5)])
        self.assertEqual(drops["returned"], 5)
        self.assertEqual(drops["inadmissible"], 5)
        self.assertFalse(got)

    def test_a_malformed_row_is_counted_as_malformed(self):
        drops: dict = {}
        # Two distinct branches reach this counter: a row that is not a dict
        # at all, and a dict whose path is missing or empty. The first
        # version of this test exercised only the second, so deleting the
        # first counter's call changed nothing and the mutation survived.
        payload = {"results": ["not-a-dict", {"no_path": True}, {"path": ""}]}
        done = __import__("subprocess").CompletedProcess(
            [], 0, stdout=json.dumps(payload), stderr="")
        with tempfile.TemporaryDirectory() as d:
            vault = pathlib.Path(d)
            (vault / "Agent" / "memory").mkdir(parents=True)
            with mock.patch.object(recall.subprocess, "run", return_value=done):
                recall._daemon_search(
                    vault=vault, query_text="a real question here",
                    k=5, drops=drops)
        self.assertEqual(drops["malformed"], 3)

    def test_a_deduped_row_is_counted_as_deduped(self):
        # Always-load notes are injected separately and deduped here. An
        # always-load set that swallowed the whole slate would look identical
        # to a retrieval miss without this counter.
        drops: dict = {}
        with tempfile.TemporaryDirectory() as d:
            vault = pathlib.Path(d)
            (vault / "Agent" / "memory").mkdir(parents=True)
            (vault / "Agent" / "memory" / "a.md").write_text("x", encoding="utf-8")
            with daemon_returning(["Agent/memory/a.md"]):
                recall._daemon_search(
                    vault=vault, query_text="a real question here", k=5,
                    dedup_paths={"Agent/memory/a.md"}, drops=drops)
        self.assertEqual(drops["returned"], 1)
        self.assertEqual(drops["deduped"], 1)

    def test_the_sink_is_optional_and_absent_by_default(self):
        # Every existing caller passes nothing; the counters must not become a
        # required argument or a crash when unwired.
        with tempfile.TemporaryDirectory() as d:
            vault = pathlib.Path(d)
            (vault / "Agent" / "memory").mkdir(parents=True)
            with daemon_returning(["Agent/memory/_inbox/x.md"]):
                recall._daemon_search(
                    vault=vault, query_text="a real question here", k=5)


    def test_a_prompt_with_no_content_words_never_reaches_the_daemon(self):
        """A fourth category, found by a test that failed for the right reason.

        The extractor drops stopwords, so a prompt like "ok do it now" yields
        no search terms and `_daemon_search` returns before any query. The sink
        stays empty, the ledger writes no `drops` key, and that absence is
        itself the diagnosis: not a retrieval miss, not over-filtering — the
        query never happened. Some share of the 36% is likely this.
        """
        drops: dict = {}
        with tempfile.TemporaryDirectory() as d:
            vault = pathlib.Path(d)
            (vault / "Agent" / "memory").mkdir(parents=True)
            with daemon_returning(["Agent/memory/a.md"]) as ran:
                got = recall._daemon_search(vault=vault, query_text="ok do it",
                                            k=5, drops=drops)
                self.assertFalse(ran.called, "the daemon was queried anyway")
        self.assertIsNone(got)
        self.assertEqual(drops, {}, "an unqueried recall recorded drop counts")


class TheLedgerRow(unittest.TestCase):
    def _row(self, **kw) -> dict:
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "h.jsonl"
            recall_counter.record_recall("some prompt", [], history_path=path, **kw)
            return json.loads(path.read_text(encoding="utf-8").strip())

    def test_the_counts_are_persisted(self):
        row = self._row(drops={"returned": 5, "inadmissible": 5, "deduped": 0})
        self.assertEqual(row["drops"]["returned"], 5)
        self.assertEqual(row["drops"]["inadmissible"], 5)

    def test_no_drops_writes_no_key(self):
        # Back-compat: 9,500 existing rows have no `drops`, and a reader must
        # not have to distinguish "absent" from "all zero".
        self.assertNotIn("drops", self._row())
        self.assertNotIn("drops", self._row(drops={}))

    def test_the_row_still_carries_only_a_hashed_query(self):
        # The privacy contract, pinned. The extracted terms were the obvious
        # thing to add here and are deliberately absent.
        row = self._row(drops={"returned": 1})
        self.assertIn("query_hash", row)
        self.assertNotIn("terms", row)
        self.assertNotIn("query", row)
        self.assertNotIn("prompt", row)
        self.assertNotIn("some prompt", json.dumps(row))

    def test_the_counts_are_integers_not_whatever_arrived(self):
        # `assertIsInstance(True, int)` passes — bool subclasses int — so the
        # first version of this test could not see an uncoerced value at all.
        # The type must be int exactly, and True must have become 1.
        row = self._row(drops={"returned": True, "deduped": 2})
        self.assertIs(type(row["drops"]["returned"]), int)
        self.assertEqual(row["drops"]["returned"], 1)
        self.assertEqual(row["drops"]["deduped"], 2)


if __name__ == "__main__":
    unittest.main()
