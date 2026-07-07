#!/usr/bin/env python3
"""Unit tests for `forward_learning.py` — the approved-source forward-
learning pipeline (AG Wave E experience plan, task 1).

`forward_learning.py` lives in `harness/skills/memory/scripts/` (same
cross-dir import pattern as `test_dream.py` / `test_revert_log.py`).

Covers (plan task 1 verification):
  - a dry run against a fixture source set produces watchlist entries
    classified HIGH/MEDIUM/LOW
  - red-test: zero auto-adoption — no file OUTSIDE personal/_watchlist/**
    and _meta/forward-learning-cache/** changes as a result of a scan
  - LOW-scored candidates are dropped, never written to the watchlist
  - no configured sources (opt-in, absent config) -> scan finds nothing,
    writes nothing
  - the watermark (per-source last_scan) advances after a scan
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import forward_learning as fl  # noqa: E402


class _ForwardLearningTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir()

    def _write_sources(self, sources: list) -> None:
        path = self.vault / fl.SOURCES_CONFIG_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"sources": sources}), encoding="utf-8")

    def _snapshot(self) -> dict:
        return {
            str(p.relative_to(self.vault)): p.read_bytes()
            for p in sorted(self.vault.rglob("*"))
            if p.is_file()
        }


def _fixture_fetcher(candidates_by_slug: dict):
    def fetcher(source):
        return candidates_by_slug.get(source.slug, [])
    return fetcher


class DryRunFixtureSourceSetTests(_ForwardLearningTestBase):
    def setUp(self) -> None:
        super().setUp()
        self._write_sources(
            [
                {"slug": "high-src", "kind": "idea", "type": "feed", "url": "https://example.com/high", "trusted": True},
                {"slug": "medium-src", "kind": "pattern", "type": "repo", "url": "https://example.com/medium", "trusted": False},
                {"slug": "low-src", "kind": "reference", "type": "web", "url": "https://example.com/low", "trusted": False},
            ]
        )
        self.fetcher = _fixture_fetcher(
            {
                # trusted (+1) + substantive body >=80 chars (+1) + mentions
                # its own slug (existing tag, +1) = score 3 -> HIGH
                "high-src": [
                    fl.Candidate(
                        slug="high-src",
                        title="A genuinely new technique",
                        body=(
                            "high-src describes a genuinely new technique worth adopting, "
                            "with enough detail here to clear the substantiveness floor."
                        ),
                        url="https://example.com/high/1",
                    )
                ],
                # not trusted, substantive body (+1), mentions its own slug (+1) = score 2 -> MEDIUM
                "medium-src": [
                    fl.Candidate(
                        slug="medium-src",
                        title="An interesting pattern",
                        body=(
                            "medium-src has an interesting pattern that might be worth "
                            "recording for later, though it is not clearly a fit yet."
                        ),
                        url="https://example.com/medium/1",
                    )
                ],
                # not trusted, body too short, no tag match = score 0 -> LOW, dropped
                "low-src": [
                    fl.Candidate(slug="low-src", title="Nothing much", body="tiny", url="https://example.com/low/1")
                ],
            }
        )

    def test_produces_watchlist_entries_classified_high_medium_low(self) -> None:
        pre_snapshot = self._snapshot()
        result = fl.run_forward_learning(self.vault, fetcher=self.fetcher, now=1_700_000_000.0)

        self.assertEqual(result.sources_scanned, 3)
        self.assertEqual(result.candidates_seen, 3)
        self.assertEqual(len(result.written), 2)  # HIGH + MEDIUM only
        self.assertEqual(result.dropped_low, 1)

        tiers = set()
        for path in result.written:
            fm_text = path.read_text(encoding="utf-8")
            self.assertIn("evaluator_classification:", fm_text)
            if "evaluator_classification: HIGH" in fm_text:
                tiers.add("HIGH")
            elif "evaluator_classification: MEDIUM" in fm_text:
                tiers.add("MEDIUM")
        self.assertEqual(tiers, {"HIGH", "MEDIUM"})

        # zero auto-adoption: every changed/new path is under _watchlist/ or
        # the forward-learning cache — nothing else in the vault moved.
        post_snapshot = self._snapshot()
        changed_paths = set(post_snapshot) - set(pre_snapshot)
        changed_paths |= {p for p in pre_snapshot if pre_snapshot.get(p) != post_snapshot.get(p)}
        for rel in changed_paths:
            self.assertTrue(
                rel.startswith(str(fl.WATCHLIST_REL)) or rel.startswith(str(fl.STATE_REL.parent)),
                f"unexpected write outside the watchlist/cache: {rel}",
            )

    def test_low_scored_candidate_is_never_written(self) -> None:
        fl.run_forward_learning(self.vault, fetcher=self.fetcher, now=1_700_000_000.0)
        low_dir = self.vault / fl.WATCHLIST_REL / "low-src"
        self.assertFalse(low_dir.exists())

    def test_watermark_advances_after_scan(self) -> None:
        state_before = fl._load_state(self.vault)
        self.assertEqual(state_before, {})
        fl.run_forward_learning(self.vault, fetcher=self.fetcher, now=1_700_000_000.0)
        state_after = fl._load_state(self.vault)
        for slug in ("high-src", "medium-src", "low-src"):
            self.assertIn("last_scan", state_after[slug])


class NoSourcesConfiguredTests(_ForwardLearningTestBase):
    def test_no_config_finds_nothing_writes_no_watchlist_entries(self) -> None:
        result = fl.run_forward_learning(self.vault, fetcher=_fixture_fetcher({}))
        self.assertEqual(result.sources_scanned, 0)
        self.assertEqual(result.written, [])
        # An (empty) cache state write is expected and allowed; no watchlist
        # dir is ever created when there was nothing to scan.
        self.assertFalse((self.vault / fl.WATCHLIST_REL).exists())


class MalformedSourceEntryTests(_ForwardLearningTestBase):
    def test_malformed_source_entry_is_skipped_not_fatal(self) -> None:
        self._write_sources(
            [
                {"slug": "bad", "kind": "not-a-real-kind", "type": "feed", "url": "https://example.com"},
                {"slug": "good", "kind": "idea", "type": "web", "url": "https://example.com/ok", "trusted": True},
            ]
        )
        sources = fl.load_sources(self.vault)
        self.assertEqual([s.slug for s in sources], ["good"])


class DefaultFetcherGracefulDegradationTests(_ForwardLearningTestBase):
    def test_unreachable_url_returns_empty_not_raises(self) -> None:
        source = fl.Source(slug="x", kind="idea", type="web", url="http://127.0.0.1:1/nope", trusted=False)
        candidates = fl.default_fetcher(source)
        self.assertEqual(candidates, [])


class CliTests(_ForwardLearningTestBase):
    def test_main_no_vault_path_errors(self) -> None:
        import os

        prev = os.environ.pop("MEMORY_VAULT_PATH", None)
        try:
            rc = fl.main([])
        finally:
            if prev is not None:
                os.environ["MEMORY_VAULT_PATH"] = prev
        self.assertEqual(rc, 1)

    def test_main_smoke_run_no_sources(self) -> None:
        rc = fl.main(["--vault-path", str(self.vault)])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
