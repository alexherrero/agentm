#!/usr/bin/env python3
"""Tests for model_effort_routing_refresh.py (PLAN-wave-d-personas task 5).

Calls the re-pin entrypoint directly, bypassing the not-yet-built
model-drift-detector scheduler, against a fixture with a stale model-id
string -- asserting the mechanical rename path re-pins it, and that a
genuinely new model (no existing reference to rename from) surfaces as a
judgment-bound watchlist entry rather than being auto-guessed into a tier
placement. Skips gracefully if the crickets sibling checkout (or its own
agentm bridge) is unavailable, mirroring content_refresh.py's own test's
real-bridge skip precedent.

stdlib only -- no pytest.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import model_effort_routing_refresh as merr


class TestChecklist(unittest.TestCase):
    def test_checklist_names_the_five_pinned_ids(self):
        old_refs = {item["old_ref"] for item in merr.CHECKLIST}
        self.assertEqual(
            old_refs,
            {
                "claude-opus-4-8", "claude-sonnet-5", "claude-sonnet-4-6",
                "claude-haiku-4-5", "claude-fable-5",
            },
        )

    def test_checklist_entries_are_at_rest_new_ref_equals_old_ref(self):
        for item in merr.CHECKLIST:
            self.assertEqual(item["old_ref"], item["new_ref"])


class TestFindContentRefresh(unittest.TestCase):
    def test_finds_real_sibling_checkout_or_returns_none_gracefully(self):
        # Never raises regardless of whether crickets is checked out.
        result = merr.find_content_refresh()
        self.assertTrue(result is None or isinstance(result, Path))


@unittest.skipIf(
    merr.find_content_refresh() is None,
    "crickets sibling checkout unavailable -- content-refresh engine not found",
)
class TestRefreshChart(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_mechanical_rename_re_pins_the_stale_id(self):
        """A fixture chart carrying the OLD id, re-pinned to a NEW one --
        the mechanical path (an existing old_ref found in the target)."""
        chart_path = Path(self._tmp.name) / "chart.md"
        original = (
            "| Tier | Model |\n|---|---|\n"
            "| T1 | claude-sonnet-4-6 |\n"
        )
        chart_path.write_text(original, encoding="utf-8")
        item = {
            "old_ref": "claude-sonnet-4-6",
            "new_ref": "claude-sonnet-4-7",
            "context": "test: a model release renamed the prior-generation id",
        }

        result = merr.refresh_chart(chart_path, item, self.vault)

        self.assertEqual(result["classification"], "mechanical")
        self.assertTrue(result["applied"])
        self.assertEqual(
            chart_path.read_text(encoding="utf-8"),
            original.replace("claude-sonnet-4-6", "claude-sonnet-4-7"),
        )

    def test_genuinely_new_model_is_judgment_bound_not_auto_guessed(self):
        """A model with no existing pin to rename FROM -- surfaced to the
        watchlist, the chart file stays byte-identical (never auto-edited
        with a guessed tier placement)."""
        chart_path = Path(self._tmp.name) / "chart.md"
        original = "| Tier | Model |\n|---|---|\n| T3 | claude-opus-4-8 |\n"
        chart_path.write_text(original, encoding="utf-8")
        item = {
            "old_ref": None,
            "new_ref": "claude-opus-5",
            "context": "test: a genuinely new model, needs a tier placement -- not a drop-in rename",
        }

        result = merr.refresh_chart(chart_path, item, self.vault)

        self.assertEqual(result["classification"], "judgment-bound")
        self.assertFalse(result["applied"])
        self.assertEqual(chart_path.read_text(encoding="utf-8"), original)
        self.assertIsNotNone(result["watchlist_path"])

    def test_real_chart_file_carries_every_checklist_id_today(self):
        """Sanity check against the real repo tree: every CHECKLIST
        old_ref actually appears in the real chart file right now (a
        stale checklist entry naming an id the chart no longer carries
        would silently never re-pin anything)."""
        repo_root = Path(__file__).resolve().parent.parent
        chart_path = repo_root / "wiki" / "designs" / "agentm-model-effort-routing.md"
        content = chart_path.read_text(encoding="utf-8")
        for item in merr.CHECKLIST:
            self.assertIn(
                item["old_ref"], content,
                f"{item['old_ref']!r} not found in the real chart -- stale checklist entry",
            )


class TestUnavailableRaisesNamedError(unittest.TestCase):
    def test_missing_sibling_checkout_raises_not_silently_no_ops(self):
        """A direct CLI ask for a real re-pin fails loudly when the engine
        isn't found -- distinct from check-slop.py's graceful-skip
        report-only gate, since this is an actual re-pin request."""
        import model_effort_routing_refresh as _merr_mod

        original_finder = _merr_mod.find_content_refresh
        _merr_mod.find_content_refresh = lambda: None
        try:
            with tempfile.TemporaryDirectory() as t:
                vault = Path(t) / "vault"
                vault.mkdir()
                target = Path(t) / "chart.md"
                target.write_text("stub", encoding="utf-8")
                with self.assertRaises(_merr_mod.ContentRefreshUnavailable):
                    _merr_mod.refresh_chart(target, {"old_ref": "a", "new_ref": "b"}, vault)
        finally:
            _merr_mod.find_content_refresh = original_finder


if __name__ == "__main__":
    unittest.main()
