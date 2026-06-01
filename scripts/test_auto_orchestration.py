#!/usr/bin/env python3
"""Unit tests for harness/skills/memory/scripts/auto_orchestration.py (V4 #23 task 2).

Covers the state + config primitives that the push-surface (briefing, idle-chain,
nudges) builds on: state round-trip + corruption tolerance, cooldown logic,
the "only when state shifted" guard, and the operator-editable config
(seed-is-idempotent / never-clobbers / parse + type-coercion).

Run: python3 scripts/test_auto_orchestration.py
Discovered by CI via `(cd scripts && python3 -m unittest discover -p 'test_*.py')`.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import auto_orchestration as ao  # noqa: E402

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


class TestState(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_load_absent_returns_empty_shape(self) -> None:
        st = ao.load_state(self.vault)
        self.assertEqual(st, {"last_fire": {}, "last_shown": {}})

    def test_save_load_round_trip(self) -> None:
        st = ao.load_state(self.vault)
        ao.record_fire(st, "idle_chain", _NOW)
        ao.record_shown(st, {"inbox": 12})
        ao.save_state(self.vault, st)
        again = ao.load_state(self.vault)
        self.assertEqual(again["last_shown"], {"inbox": 12})
        self.assertIn("idle_chain", again["last_fire"])

    def test_corrupt_file_returns_empty_shape(self) -> None:
        ao.state_path(self.vault).parent.mkdir(parents=True, exist_ok=True)
        ao.state_path(self.vault).write_text("{ not json", encoding="utf-8")
        self.assertEqual(ao.load_state(self.vault), {"last_fire": {}, "last_shown": {}})

    def test_non_dict_subfields_normalized(self) -> None:
        ao.state_path(self.vault).parent.mkdir(parents=True, exist_ok=True)
        ao.state_path(self.vault).write_text('{"last_fire": [], "last_shown": 5}', encoding="utf-8")
        st = ao.load_state(self.vault)
        self.assertEqual(st["last_fire"], {})
        self.assertEqual(st["last_shown"], {})

    def test_non_utf8_state_degrades_to_empty(self) -> None:
        # a non-UTF-8 state file must degrade, not crash the hook (adversarial #1)
        p = ao.state_path(self.vault)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\xe9\xe9 not utf-8")
        self.assertEqual(ao.load_state(self.vault), {"last_fire": {}, "last_shown": {}})


class TestCooldown(unittest.TestCase):
    def test_never_fired_is_eligible(self) -> None:
        self.assertTrue(ao.should_fire({}, "idle_chain", _NOW, 24))

    def test_within_cooldown_blocks(self) -> None:
        st = ao.record_fire({}, "idle_chain", _NOW)
        soon = _NOW + timedelta(hours=10)
        self.assertFalse(ao.should_fire(st, "idle_chain", soon, 24))

    def test_past_cooldown_fires(self) -> None:
        st = ao.record_fire({}, "idle_chain", _NOW)
        later = _NOW + timedelta(hours=25)
        self.assertTrue(ao.should_fire(st, "idle_chain", later, 24))

    def test_zero_cooldown_always_fires(self) -> None:
        st = ao.record_fire({}, "briefing", _NOW)
        self.assertTrue(ao.should_fire(st, "briefing", _NOW, 0))

    def test_unparseable_timestamp_is_eligible(self) -> None:
        st = {"last_fire": {"idle_chain": "not-a-date"}}
        self.assertTrue(ao.should_fire(st, "idle_chain", _NOW, 24))

    def test_per_chain_isolation(self) -> None:
        st = ao.record_fire({}, "idle_chain", _NOW)
        # a different chain is unaffected
        self.assertTrue(ao.should_fire(st, "briefing", _NOW, 24))


class TestShiftedGuard(unittest.TestCase):
    def test_identical_signals_not_shifted(self) -> None:
        st = ao.record_shown({}, {"inbox": 12, "watchlist_high": 3})
        self.assertFalse(ao.state_shifted_since_last_shown(st, {"inbox": 12, "watchlist_high": 3}))

    def test_changed_count_is_shifted(self) -> None:
        st = ao.record_shown({}, {"inbox": 12})
        self.assertTrue(ao.state_shifted_since_last_shown(st, {"inbox": 13}))

    def test_new_signal_is_shifted(self) -> None:
        st = ao.record_shown({}, {"inbox": 12})
        self.assertTrue(ao.state_shifted_since_last_shown(st, {"inbox": 12, "watchlist_high": 1}))

    def test_cleared_signal_is_shifted(self) -> None:
        st = ao.record_shown({}, {"inbox": 12, "watchlist_high": 3})
        self.assertTrue(ao.state_shifted_since_last_shown(st, {"inbox": 12}))  # watchlist cleared

    def test_first_time_is_shifted(self) -> None:
        self.assertTrue(ao.state_shifted_since_last_shown({}, {"inbox": 1}))


class TestConfig(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_absent_config_is_all_defaults(self) -> None:
        self.assertEqual(ao.load_config(self.vault), dict(ao.DEFAULT_CONFIG))

    def test_seed_writes_then_is_idempotent(self) -> None:
        self.assertTrue(ao.seed_config(self.vault))          # created
        self.assertTrue(ao.config_path(self.vault).exists())
        self.assertFalse(ao.seed_config(self.vault))         # already present → no-op

    def test_seed_never_clobbers_operator_edits(self) -> None:
        ao.seed_config(self.vault)
        p = ao.config_path(self.vault)
        edited = p.read_text(encoding="utf-8").replace("inbox_threshold = 10", "inbox_threshold = 99")
        p.write_text(edited, encoding="utf-8")
        ao.seed_config(self.vault)  # re-seed
        self.assertEqual(ao.load_config(self.vault)["inbox_threshold"], 99)  # edit survived

    def test_operator_override_merges_over_defaults(self) -> None:
        ao.seed_config(self.vault)
        p = ao.config_path(self.vault)
        p.write_text(
            p.read_text(encoding="utf-8")
            .replace("inbox_threshold = 10", "inbox_threshold = 25")
            .replace("enable_idle_chain = true", "enable_idle_chain = false"),
            encoding="utf-8",
        )
        cfg = ao.load_config(self.vault)
        self.assertEqual(cfg["inbox_threshold"], 25)        # overridden int
        self.assertFalse(cfg["enable_idle_chain"])           # overridden bool
        self.assertEqual(cfg["stale_promotion_days"], 30)    # untouched default

    def test_type_coercion_and_inline_comment(self) -> None:
        cfg = ao._parse_config_md(
            "```settings\n"
            "inbox_threshold = 7   # operator note\n"
            "enable_briefing = false\n"
            "bogus_key = 1\n"        # unknown → ignored
            "not a kv line\n"
            "# a comment\n"
            "```\n"
        )
        self.assertEqual(cfg["inbox_threshold"], 7)
        self.assertIsInstance(cfg["inbox_threshold"], int)
        self.assertFalse(cfg["enable_briefing"])
        self.assertNotIn("bogus_key", cfg)

    def test_colon_separator_also_parsed(self) -> None:
        cfg = ao._parse_config_md("inbox_threshold: 42\n")
        self.assertEqual(cfg["inbox_threshold"], 42)

    def test_bad_int_falls_back_to_default(self) -> None:
        cfg = ao._parse_config_md("```settings\ninbox_threshold = not-a-number\n```")
        self.assertEqual(cfg["inbox_threshold"], ao.DEFAULT_CONFIG["inbox_threshold"])

    def test_non_utf8_config_degrades_to_defaults(self) -> None:
        # a non-UTF-8 config file must degrade to defaults, not crash (adversarial #1)
        p = ao.config_path(self.vault)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"inbox_threshold = 10\n\xe9\xe9 garbage\n")
        self.assertEqual(ao.load_config(self.vault), dict(ao.DEFAULT_CONFIG))

    def test_settings_fence_preferred_over_illustrative_fence(self) -> None:
        # an illustrative fence above the settings fence must not be parsed (adversarial nit)
        cfg = ao._parse_config_md(
            "```text\ninbox_threshold = 999\n```\n\n"
            "```settings\ninbox_threshold = 7\n```\n"
        )
        self.assertEqual(cfg["inbox_threshold"], 7)


if __name__ == "__main__":
    unittest.main()
