#!/usr/bin/env python3
"""Tests for scripts/health/session_brief.py — the visible session-start
observability line (`wiki/designs/agentm-autonomy.md` Delivery → Session-start
line; the 2026-07-17 visibility/wiring fix)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import session_brief as sb  # noqa: E402

_NOW = datetime(2026, 7, 17, 18, 0, 0, tzinfo=timezone.utc)


def _write_digest(briefs: Path, date: str, cadence: str, *, spend=None, events=None, monthly_total=None):
    briefs.mkdir(parents=True, exist_ok=True)
    slug = f"{date}-digest-{cadence}"
    lines = [
        "---", "kind: brief", "status: active", f"slug: {slug}",
        f"digest_cadence: {cadence}", "---", "",
        f"# Observability digest — {cadence}", "",
    ]
    if monthly_total is not None:
        lines.append(f"**Total spend, last 30 days: ${monthly_total:.4f}** (7 windows, 40 events)")
    else:
        if spend is not None:
            lines.append(f"- Spend: ${spend:.4f}")
        if events is not None:
            lines.append(f"- Events: {events}")
    p = briefs / f"{slug}.md"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


class LatestDigestTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_none_when_no_briefs_dir(self):
        self.assertIsNone(sb.latest_digest(self.vault))

    def test_none_when_only_park_notes(self):
        briefs = self.vault / "_briefs"
        briefs.mkdir()
        (briefs / "20260717-park-myplan.md").write_text("# Run parked\n", encoding="utf-8")
        self.assertIsNone(sb.latest_digest(self.vault))

    def test_picks_newest_date(self):
        briefs = self.vault / "_briefs"
        _write_digest(briefs, "20260710", "daily", spend=10.0, events=1)
        _write_digest(briefs, "20260717", "daily", spend=284.8171, events=25)
        d = sb.latest_digest(self.vault)
        self.assertEqual(d["date"].strftime("%Y-%m-%d"), "2026-07-17")
        self.assertAlmostEqual(d["spend"], 284.8171)
        self.assertEqual(d["events"], 25)

    def test_same_date_prefers_daily_over_weekly(self):
        briefs = self.vault / "_briefs"
        _write_digest(briefs, "20260717", "weekly", spend=999.0, events=99)
        _write_digest(briefs, "20260717", "daily", spend=1.0, events=1)
        d = sb.latest_digest(self.vault)
        self.assertEqual(d["cadence"], "daily")

    def test_headline_formats_spend_and_events(self):
        briefs = self.vault / "_briefs"
        _write_digest(briefs, "20260717", "daily", spend=2428.18, events=84)
        d = sb.latest_digest(self.vault)
        self.assertIn("$2,428.18", d["headline"])
        self.assertIn("84 events", d["headline"])

    def test_monthly_headline(self):
        briefs = self.vault / "_briefs"
        _write_digest(briefs, "20260717", "monthly", monthly_total=5000.5)
        d = sb.latest_digest(self.vault)
        self.assertEqual(d["cadence"], "monthly")
        self.assertIn("$5,000.50", d["headline"])
        self.assertIn("30 days", d["headline"])

    def test_headline_falls_back_to_h1_when_body_unparseable(self):
        briefs = self.vault / "_briefs"
        briefs.mkdir()
        (briefs / "20260717-digest-daily.md").write_text(
            "---\nkind: brief\n---\n\n# Some other shape\n\nno spend line here\n", encoding="utf-8"
        )
        d = sb.latest_digest(self.vault)
        self.assertEqual(d["headline"], "Some other shape")


class CountParkedTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.park = Path(self._tmp.name) / "park"

    def tearDown(self):
        self._tmp.cleanup()

    def test_zero_when_missing(self):
        self.assertEqual(sb.count_parked(self.park), 0)

    def test_counts_park_state_files(self):
        self.park.mkdir(parents=True)
        (self.park / "a-park-state.json").write_text("{}", encoding="utf-8")
        (self.park / "b-park-state.json").write_text("{}", encoding="utf-8")
        (self.park / "not-a-park.txt").write_text("x", encoding="utf-8")
        self.assertEqual(sb.count_parked(self.park), 2)


class HistoryLatestDateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.hist = Path(self._tmp.name) / "digest-history.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_none_when_missing(self):
        self.assertIsNone(sb.history_latest_date(self.hist))

    def test_returns_max_date_skips_malformed(self):
        self.hist.write_text(
            json.dumps({"cadence": "daily", "date": "2026-07-11"}) + "\n"
            + "not json\n"
            + json.dumps({"cadence": "daily", "date": "2026-07-17"}) + "\n"
            + json.dumps({"cadence": "3day", "date": "2026-07-13"}) + "\n",
            encoding="utf-8",
        )
        d = sb.history_latest_date(self.hist)
        self.assertEqual(d.strftime("%Y-%m-%d"), "2026-07-17")


class BuildBriefTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.vault = self.tmp / "vault"
        self.vault.mkdir()
        self.park = self.tmp / "park"
        self.hist = self.tmp / "digest-history.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def _brief(self, **kw):
        return sb.build_brief(
            vault=self.vault, now=_NOW, park_dir=self.park, history_path=self.hist, **kw
        )

    def test_quiet_when_ladder_never_ran(self):
        self.assertIsNone(self._brief())

    def test_fresh_digest_shows_headline_no_warning(self):
        _write_digest(self.vault / "_briefs", "20260717", "daily", spend=284.82, events=25)
        b = self._brief()
        self.assertNotIn("⚠", b["line"])
        self.assertIn("daily digest: $284.82", b["line"])
        self.assertIn("last cycle", b["line"])
        self.assertTrue(b["signature"].startswith("fresh|"))

    def test_deadman_when_note_is_stale(self):
        # newest note 4 days before _NOW (2026-07-13), default deadman 2 days.
        _write_digest(self.vault / "_briefs", "20260713", "daily", spend=284.82, events=25)
        b = self._brief()
        self.assertIn("⚠", b["line"])
        self.assertIn("no digest in 4 days", b["line"])
        self.assertIn("last: 2026-07-13", b["line"])
        self.assertTrue(b["signature"].startswith("deadman|"))

    def test_deadman_composes_with_history_when_computed_but_not_delivered(self):
        # The real 2026-07-17 stall shape: note stuck at 07-13, ladder computed
        # through 07-17 in the history ledger but no note reached _briefs/.
        _write_digest(self.vault / "_briefs", "20260713", "daily", spend=284.82, events=25)
        self.hist.write_text(json.dumps({"cadence": "daily", "date": "2026-07-17"}) + "\n", encoding="utf-8")
        b = self._brief()
        self.assertIn("no digest in 4 days", b["line"])
        self.assertIn("computed through 2026-07-17 but not delivered", b["line"])

    def test_deadman_no_note_but_history_exists(self):
        self.hist.write_text(json.dumps({"cadence": "daily", "date": "2026-07-17"}) + "\n", encoding="utf-8")
        b = self._brief()
        self.assertIn("⚠", b["line"])
        self.assertIn("no digest note delivered", b["line"])
        self.assertTrue(b["signature"].startswith("deadman-nonote|"))

    def test_parked_clause_appended(self):
        _write_digest(self.vault / "_briefs", "20260717", "daily", spend=1.0, events=1)
        self.park.mkdir(parents=True)
        (self.park / "myplan-park-state.json").write_text("{}", encoding="utf-8")
        b = self._brief()
        self.assertIn("1 run parked, awaiting resume", b["line"])

    def test_deadman_threshold_is_configurable(self):
        _write_digest(self.vault / "_briefs", "20260716", "daily", spend=1.0, events=1)  # 1 day old
        self.assertIsNone(None)  # sanity
        self.assertNotIn("⚠", self._brief()["line"])          # default 2 → fresh
        self.assertIn("⚠", self._brief(deadman_days=1)["line"])  # threshold 1 → deadman


class AntiFatigueTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = Path(self._tmp.name) / "state.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_first_show_when_no_state(self):
        self.assertTrue(sb.should_show({}, "sig-a", _NOW, 4.0))

    def test_same_signature_within_cooldown_suppressed(self):
        state = {"signature": "sig-a", "shown_ts": _NOW.isoformat()}
        later = datetime(2026, 7, 17, 20, 0, 0, tzinfo=timezone.utc)  # +2h < 4h
        self.assertFalse(sb.should_show(state, "sig-a", later, 4.0))

    def test_changed_signature_always_shows(self):
        state = {"signature": "sig-a", "shown_ts": _NOW.isoformat()}
        later = datetime(2026, 7, 17, 18, 30, 0, tzinfo=timezone.utc)
        self.assertTrue(sb.should_show(state, "sig-b", later, 4.0))

    def test_same_signature_after_cooldown_shows(self):
        state = {"signature": "sig-a", "shown_ts": _NOW.isoformat()}
        later = datetime(2026, 7, 17, 23, 0, 0, tzinfo=timezone.utc)  # +5h > 4h
        self.assertTrue(sb.should_show(state, "sig-a", later, 4.0))

    def test_record_and_reload_round_trip(self):
        sb.record_shown(self.state, "sig-x", _NOW)
        st = sb.load_state(self.state)
        self.assertEqual(st["signature"], "sig-x")


class EmitEndToEndTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.vault = self.tmp / "vault"
        self.vault.mkdir()
        self.park = self.tmp / "park"
        self.hist = self.tmp / "digest-history.jsonl"
        self.state = self.tmp / "state.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _emit(self, now, **kw):
        return sb.emit(
            vault=self.vault, now=now, park_dir=self.park, history_path=self.hist,
            state_path=self.state, **kw,
        )

    def test_none_vault_is_empty(self):
        self.assertEqual(sb.emit(vault=None, now=_NOW, state_path=self.state), "")

    def test_emits_then_suppresses_repeat_within_cooldown(self):
        _write_digest(self.vault / "_briefs", "20260717", "daily", spend=1.0, events=1)
        first = self._emit(_NOW)
        self.assertTrue(first)
        self.assertTrue(self.state.is_file())
        again = self._emit(datetime(2026, 7, 17, 19, 0, 0, tzinfo=timezone.utc))  # +1h
        self.assertEqual(again, "")

    def test_reshows_after_cooldown(self):
        _write_digest(self.vault / "_briefs", "20260717", "daily", spend=1.0, events=1)
        self._emit(_NOW)
        again = self._emit(datetime(2026, 7, 18, 0, 0, 0, tzinfo=timezone.utc))  # +6h
        self.assertTrue(again)

    def test_quiet_ladder_emits_nothing(self):
        self.assertEqual(self._emit(_NOW), "")
        self.assertFalse(self.state.is_file())  # no fire recorded


class ResolveVaultTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_arg_path_wins(self):
        v = self.tmp / "vault"
        v.mkdir()
        self.assertEqual(sb.resolve_vault(str(v)), v)

    def test_arg_path_nonexistent_is_none(self):
        self.assertIsNone(sb.resolve_vault(str(self.tmp / "nope")))

    def test_env_var(self):
        import os
        v = self.tmp / "vault"
        v.mkdir()
        old = os.environ.get("MEMORY_VAULT_PATH")
        os.environ["MEMORY_VAULT_PATH"] = str(v)
        try:
            self.assertEqual(sb.resolve_vault(None), v)
        finally:
            if old is None:
                del os.environ["MEMORY_VAULT_PATH"]
            else:
                os.environ["MEMORY_VAULT_PATH"] = old

    def test_config_fallback(self):
        import os
        v = self.tmp / "vault"
        v.mkdir()
        prefix = self.tmp / "prefix"
        prefix.mkdir()
        (prefix / ".agentm-config.json").write_text(
            json.dumps({"plugins.obsidian-vault.vault_path": str(v)}), encoding="utf-8"
        )
        old_env = os.environ.get("MEMORY_VAULT_PATH")
        old_prefix = os.environ.get("AGENTM_INSTALL_PREFIX")
        os.environ.pop("MEMORY_VAULT_PATH", None)
        os.environ["AGENTM_INSTALL_PREFIX"] = str(prefix)
        try:
            self.assertEqual(sb.resolve_vault(None), v)
        finally:
            if old_env is not None:
                os.environ["MEMORY_VAULT_PATH"] = old_env
            if old_prefix is None:
                os.environ.pop("AGENTM_INSTALL_PREFIX", None)
            else:
                os.environ["AGENTM_INSTALL_PREFIX"] = old_prefix


if __name__ == "__main__":
    unittest.main()
