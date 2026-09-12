#!/usr/bin/env python3
"""Tests for scripts/health/session_brief.py — the visible session-start
observability line (`wiki/designs/agentm-autonomy.md` Delivery → Session-start
line; the 2026-07-17 visibility/wiring fix)."""
from __future__ import annotations

import json
import sys
import os
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


def _write_morning(vault: Path, date: str, headline: str, *, mirror: bool = True):
    d = vault / "diagnostics" / "morning"
    d.mkdir(parents=True, exist_ok=True)
    text = ("---\ntitle: Morning\nkind: report\n" f"date: {date}\n"
            f"headline: {json.dumps(headline)}\n---\n\n# Morning — {date}\n")
    (d / f"{date}.md").write_text(text, encoding="utf-8")
    if mirror:
        (d / "latest_morning_note.md").write_text(text, encoding="utf-8")


class LatestDigestTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_none_when_no_briefs_dir(self):
        self.assertIsNone(sb.latest_digest(self.vault))

    def test_none_when_only_park_notes(self):
        briefs = self.vault / "diagnostics/digests"
        briefs.mkdir(parents=True)
        (briefs / "20260717-park-myplan.md").write_text("# Run parked\n", encoding="utf-8")
        self.assertIsNone(sb.latest_digest(self.vault))

    def test_picks_newest_date(self):
        briefs = self.vault / "diagnostics/digests"
        _write_digest(briefs, "20260710", "daily", spend=10.0, events=1)
        _write_digest(briefs, "20260717", "daily", spend=284.8171, events=25)
        d = sb.latest_digest(self.vault)
        self.assertEqual(d["date"].strftime("%Y-%m-%d"), "2026-07-17")
        self.assertAlmostEqual(d["spend"], 284.8171)
        self.assertEqual(d["events"], 25)

    def test_same_date_prefers_daily_over_weekly(self):
        briefs = self.vault / "diagnostics/digests"
        _write_digest(briefs, "20260717", "weekly", spend=999.0, events=99)
        _write_digest(briefs, "20260717", "daily", spend=1.0, events=1)
        d = sb.latest_digest(self.vault)
        self.assertEqual(d["cadence"], "daily")

    def test_headline_formats_spend_and_events(self):
        briefs = self.vault / "diagnostics/digests"
        _write_digest(briefs, "20260717", "daily", spend=2428.18, events=84)
        d = sb.latest_digest(self.vault)
        self.assertIn("$2,428.18", d["headline"])
        self.assertIn("84 events", d["headline"])

    def test_monthly_headline(self):
        briefs = self.vault / "diagnostics/digests"
        _write_digest(briefs, "20260717", "monthly", monthly_total=5000.5)
        d = sb.latest_digest(self.vault)
        self.assertEqual(d["cadence"], "monthly")
        self.assertIn("$5,000.50", d["headline"])
        self.assertIn("30 days", d["headline"])

    def test_headline_falls_back_to_h1_when_body_unparseable(self):
        briefs = self.vault / "diagnostics/digests"
        briefs.mkdir(parents=True)
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
        # Pin the engine state dir to a tmp one so a fixture that writes there
        # (the leftover-staging test below) never touches the operator's real one.
        self._state = os.environ.get("AGENTM_STATE_DIR")
        os.environ["AGENTM_STATE_DIR"] = str(self.tmp / "state")

    def tearDown(self):
        if self._state is None:
            os.environ.pop("AGENTM_STATE_DIR", None)
        else:
            os.environ["AGENTM_STATE_DIR"] = self._state
        self._tmp.cleanup()

    def _brief(self, **kw):
        return sb.build_brief(
            vault=self.vault, now=_NOW, park_dir=self.park, history_path=self.hist, **kw
        )

    def test_quiet_when_ladder_never_ran(self):
        self.assertIsNone(self._brief())

    def test_the_morning_note_headline_is_the_line(self):
        # agentm-vault plan 04: the line reads the morning note's first
        # section, and a digest beside it is the fallback, not the headline.
        _write_digest(self.vault / "diagnostics/digests", "20260717", "daily", spend=284.82, events=25)
        _write_morning(self.vault, "2026-07-17", "enrichment judged 118 (97 active, 2 sank) · 3 list(s) need you")
        b = self._brief()
        self.assertTrue(b["line"].startswith(
            "[agentm] Morning — enrichment judged 118 (97 active, 2 sank) · 3 list(s) need you (written "))
        self.assertNotIn("digest", b["line"])
        self.assertTrue(b["signature"].startswith("morning|2026-07-17|"))

    def test_the_newest_dated_note_serves_when_the_mirror_is_missing(self):
        _write_morning(self.vault, "2026-07-16", "an older night", mirror=False)
        _write_morning(self.vault, "2026-07-17", "last night", mirror=False)
        self.assertIn("Morning — last night", self._brief()["line"])

    def test_a_morning_note_that_stopped_arriving_is_the_deadman(self):
        _write_morning(self.vault, "2026-07-13", "a night four days ago")
        b = self._brief()
        self.assertIn("⚠ Morning note — none in 4 days (last: 2026-07-13)", b["line"])
        self.assertTrue(b["signature"].startswith("morning-deadman|2026-07-13|4|"))

    def test_the_crystallization_count_never_rides_the_morning_line(self):
        _write_morning(self.vault, "2026-07-17", "last night")
        staging = sb._engine_state_dir() / "crystallize-staging"
        staging.mkdir(parents=True)
        (staging / "post-work-a.json").write_text("{}", encoding="utf-8")
        self.assertNotIn("crystalliz", self._brief()["line"])

    def test_fresh_digest_shows_headline_no_warning(self):
        _write_digest(self.vault / "diagnostics/digests", "20260717", "daily", spend=284.82, events=25)
        b = self._brief()
        self.assertNotIn("⚠", b["line"])
        self.assertIn("daily digest: $284.82", b["line"])
        self.assertIn("last cycle", b["line"])
        self.assertTrue(b["signature"].startswith("fresh|"))

    def test_deadman_when_note_is_stale(self):
        # newest note 4 days before _NOW (2026-07-13), default deadman 2 days.
        _write_digest(self.vault / "diagnostics/digests", "20260713", "daily", spend=284.82, events=25)
        b = self._brief()
        self.assertIn("⚠", b["line"])
        self.assertIn("no digest in 4 days", b["line"])
        self.assertIn("last: 2026-07-13", b["line"])
        self.assertTrue(b["signature"].startswith("deadman|"))

    def test_deadman_composes_with_history_when_computed_but_not_delivered(self):
        # The real 2026-07-17 stall shape: note stuck at 07-13, ladder computed
        # through 07-17 in the history ledger but no note reached _briefs/.
        _write_digest(self.vault / "diagnostics/digests", "20260713", "daily", spend=284.82, events=25)
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
        _write_digest(self.vault / "diagnostics/digests", "20260717", "daily", spend=1.0, events=1)
        self.park.mkdir(parents=True)
        (self.park / "myplan-park-state.json").write_text("{}", encoding="utf-8")
        b = self._brief()
        self.assertIn("1 run parked, awaiting resume", b["line"])

    def test_leftover_crystallize_staging_says_nothing(self):
        # Crystallization staging retired in agentm-vault plan 04: nothing
        # writes markers any more, and the ones left on disk are not work
        # awaiting anyone. A leftover directory must not put a count back on
        # the line.
        _write_digest(self.vault / "diagnostics/digests", "20260717", "daily", spend=1.0, events=1)
        staging = sb._engine_state_dir() / "crystallize-staging"
        staging.mkdir(parents=True)
        (staging / "post-work-a.json").write_text("{}", encoding="utf-8")
        b = self._brief()
        self.assertNotIn("crystalliz", b["line"])

    def test_deadman_threshold_is_configurable(self):
        _write_digest(self.vault / "diagnostics/digests", "20260716", "daily", spend=1.0, events=1)  # 1 day old
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
        _write_digest(self.vault / "diagnostics/digests", "20260717", "daily", spend=1.0, events=1)
        first = self._emit(_NOW)
        self.assertTrue(first)
        self.assertTrue(self.state.is_file())
        again = self._emit(datetime(2026, 7, 17, 19, 0, 0, tzinfo=timezone.utc))  # +1h
        self.assertEqual(again, "")

    def test_reshows_after_cooldown(self):
        _write_digest(self.vault / "diagnostics/digests", "20260717", "daily", spend=1.0, events=1)
        self._emit(_NOW)
        again = self._emit(datetime(2026, 7, 18, 0, 0, 0, tzinfo=timezone.utc))  # +6h
        self.assertTrue(again)

    def test_quiet_ladder_emits_nothing(self):
        self.assertEqual(self._emit(_NOW), "")
        self.assertFalse(self.state.is_file())  # no fire recorded


class ResolveMemoryRootTests(unittest.TestCase):
    """The hook passes no path, so the config decides. The config names the
    vault root; what the brief reads lives under the memory root."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.prefix = self.tmp / "prefix"
        self.prefix.mkdir()
        self.vault = self.tmp / "Vault"
        (self.vault / "Agent").mkdir(parents=True)
        self._env = {k: os.environ.get(k) for k in ("MEMORY_VAULT_PATH", "AGENTM_INSTALL_PREFIX")}
        os.environ.pop("MEMORY_ROOT", None)
        os.environ.pop("MEMORY_VAULT_PATH", None)
        os.environ["AGENTM_INSTALL_PREFIX"] = str(self.prefix)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def _config(self, **fields):
        (self.prefix / ".agentm-config.json").write_text(json.dumps(fields), encoding="utf-8")

    def test_the_configured_memory_root_is_joined(self):
        self._config(**{"plugins.obsidian-vault.vault_path": str(self.vault),
                        "plugins.obsidian-vault.memory_root": "Agent"})
        self.assertEqual(sb.resolve_vault(None), self.vault / "Agent")

    def test_no_memory_root_keeps_the_vault(self):
        self._config(**{"plugins.obsidian-vault.vault_path": str(self.vault)})
        self.assertEqual(sb.resolve_vault(None), self.vault)

    def test_a_memory_root_that_is_not_there_keeps_the_vault(self):
        self._config(**{"plugins.obsidian-vault.vault_path": str(self.vault),
                        "plugins.obsidian-vault.memory_root": "Gone"})
        self.assertEqual(sb.resolve_vault(None), self.vault)

    def test_the_hook_finds_a_morning_note_under_the_memory_root(self):
        self._config(**{"plugins.obsidian-vault.vault_path": str(self.vault),
                        "plugins.obsidian-vault.memory_root": "Agent"})
        _write_morning(self.vault / "Agent", "2026-07-17", "last night")
        vault = sb.resolve_vault(None)
        self.assertIsNotNone(sb.latest_morning_note(vault))


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
        os.environ.pop("MEMORY_ROOT", None)
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


# Staging lives in the engine state dir, not the vault (filing-v2 part 2a), so
# these read the operator's real state directory and go red the moment a daemon
# has staged anything. This suite sits one directory below the helper, so
# `scripts/` joins the path here rather than only the test's own directory.
import os.path as _osp  # noqa: E402
import sys as _sys  # noqa: E402

for _p in (_osp.dirname(_osp.abspath(__file__)),
           _osp.dirname(_osp.dirname(_osp.abspath(__file__)))):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
from engine_state_isolation import isolate_module  # noqa: E402

isolate_module(globals())


if __name__ == "__main__":
    unittest.main()
