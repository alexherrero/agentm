#!/usr/bin/env python3
"""Unit tests for harness/skills/memory/scripts/orchestration_briefing.py (V4 #23 task 3).

Covers the four pending-state signal counters (each defensive), the threshold
+ render logic, and the `emit_briefing` gating (enable toggle + cooldown +
shifted-since-last-shown + never-raises).

Run: python3 scripts/test_orchestration_briefing.py
"""
from __future__ import annotations

import os
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
import orchestration_briefing as ob  # noqa: E402

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _watchlist_entry(vault: Path, source: str, slug: str, classification: str, status: str) -> None:
    d = vault / "personal" / "_skill-watchlist" / source
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.md").write_text(
        f"---\nkind: skill-watchlist\nstatus: {status}\n"
        f"evaluator_classification: {classification}\n---\nbody\n",
        encoding="utf-8",
    )


def _promoted_entry(vault: Path, source: str, slug: str, promoted_at: str) -> None:
    d = vault / "personal" / "_skill-watchlist" / source
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.md").write_text(
        f"---\nkind: skill-watchlist\nstatus: promoted\npromoted_at: {promoted_at}\n---\nbody\n",
        encoding="utf-8",
    )


def _inbox_entry(vault: Path, name: str) -> None:
    d = vault / "personal" / "_inbox"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text("x", encoding="utf-8")


def _incubator(vault: Path, slug: str) -> None:
    (vault / "_idea-incubator" / slug).mkdir(parents=True, exist_ok=True)


class TestCounters(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_inbox_absent_is_zero(self) -> None:
        self.assertEqual(ob.count_inbox(self.vault), 0)

    def test_inbox_counts_md_excludes_index(self) -> None:
        _inbox_entry(self.vault, "a.md")
        _inbox_entry(self.vault, "b.md")
        _inbox_entry(self.vault, "_index.md")  # excluded
        _inbox_entry(self.vault, "notes.txt")  # not .md, excluded by glob
        self.assertEqual(ob.count_inbox(self.vault), 2)

    def test_inbox_ignores_root_level_inbox(self) -> None:
        # Real vault layout is personal/_inbox; confirms this doesn't also
        # (or instead) read a root-level <vault>/_inbox.
        d = self.vault / "_inbox"
        d.mkdir(parents=True, exist_ok=True)
        (d / "a.md").write_text("x", encoding="utf-8")
        self.assertEqual(ob.count_inbox(self.vault), 0)

    def test_watchlist_high_pending_only(self) -> None:
        _watchlist_entry(self.vault, "src", "p1", "HIGH", "pending-review")    # counts
        _watchlist_entry(self.vault, "src", "p2", "HIGH", "promoted")          # not pending
        _watchlist_entry(self.vault, "src", "p3", "MEDIUM", "pending-review")  # not HIGH
        _watchlist_entry(self.vault, "src2", "p4", "high", "pending-review")   # case-insensitive → counts
        self.assertEqual(ob.count_watchlist_high_pending(self.vault), 2)

    def test_watchlist_ignores_archive(self) -> None:
        _watchlist_entry(self.vault, "_archive", "p1", "HIGH", "pending-review")
        self.assertEqual(ob.count_watchlist_high_pending(self.vault), 0)

    def test_incubator_counts_dirs_excludes_underscore(self) -> None:
        _incubator(self.vault, "idea-one")
        _incubator(self.vault, "idea-two")
        _incubator(self.vault, "_archive")  # excluded
        self.assertEqual(ob.count_incubator_pending(self.vault), 2)

    def test_incubator_ignores_personal_nested(self) -> None:
        # Real vault layout is root-level _idea-incubator; confirms this
        # doesn't also (or instead) read <vault>/personal/_idea-incubator.
        (self.vault / "personal" / "_idea-incubator" / "idea-one").mkdir(parents=True)
        self.assertEqual(ob.count_incubator_pending(self.vault), 0)

    def test_idea_ledger_stale(self) -> None:
        ideas = self.vault / "Ideas.md"
        ideas.write_text(
            "# Ideas\n\n"
            "## 2025-01-01: Old idea\nbody\n\n"   # >6mo before _NOW → stale
            "## 2026-05-20: Recent idea\nbody\n"  # recent → not stale
            "## not-a-date: x\n",
            encoding="utf-8",
        )
        os.environ["IDEAS_SURFACE_PATH"] = str(ideas)
        try:
            self.assertEqual(ob.count_idea_ledger_stale(_NOW, 6), 1)
        finally:
            del os.environ["IDEAS_SURFACE_PATH"]

    def test_staged_adapt_counts_only_unevaluated(self) -> None:
        # v4.13.1: staged Pass-1 candidates WITHOUT a watchlist entry = awaiting
        # Pass-2; ones with an entry (Pass-2 done) clear from the count.
        cache = self.vault / "_meta" / "skill-discovery-cache" / "adapt-state"
        (cache / "src").mkdir(parents=True)
        (cache / "src" / "p1.json").write_text("{}", encoding="utf-8")   # no entry → counts
        (cache / "src" / "p2.json").write_text("{}", encoding="utf-8")   # entry exists → not
        (cache / "evaluated.json").write_text("{}", encoding="utf-8")    # root file → skipped
        wl = self.vault / "personal" / "_skill-watchlist" / "src"
        wl.mkdir(parents=True)
        (wl / "p2.md").write_text("---\nstatus: pending-review\n---\nb\n", encoding="utf-8")
        self.assertEqual(ob.count_staged_adapt(self.vault), 1)

    def test_staged_adapt_absent_is_zero(self) -> None:
        self.assertEqual(ob.count_staged_adapt(self.vault), 0)

    def test_idea_ledger_naive_now_does_not_raise(self) -> None:
        # Adversarial #2: a timezone-naive `now` must not raise (counters never
        # raise) — the ledger dates are tz-aware, so subtraction would TypeError.
        ideas = self.vault / "Ideas.md"
        ideas.write_text("## 2025-01-01: Old idea\nbody\n", encoding="utf-8")
        os.environ["IDEAS_SURFACE_PATH"] = str(ideas)
        try:
            naive = datetime(2026, 6, 1, 12, 0, 0)  # no tzinfo
            self.assertEqual(ob.count_idea_ledger_stale(naive, 6), 1)
        finally:
            del os.environ["IDEAS_SURFACE_PATH"]

    def test_promote_suggest_counts_recurring_titles(self) -> None:
        ideas = self.vault / "Ideas.md"
        ideas.write_text(
            "# Ideas\n\n"
            "## 2026-01-01: Build an orchestration mode\nbody\n\n"
            "## 2026-02-01: Build an orchestration mode\nbody\n\n"   # same title x3
            "## 2026-03-01: build an ORCHESTRATION mode\nbody\n\n"   # case-insensitive
            "## 2026-01-05: One-off idea\nbody\n\n"                  # x1
            "## 2026-02-05: Twice idea\nbody\n\n"
            "## 2026-03-05: Twice idea\nbody\n",                     # x2
            encoding="utf-8",
        )
        os.environ["IDEAS_SURFACE_PATH"] = str(ideas)
        try:
            self.assertEqual(ob.count_promote_suggest(3), 1)   # only the x3 title
            self.assertEqual(ob.count_promote_suggest(2), 2)   # x3 + x2
        finally:
            del os.environ["IDEAS_SURFACE_PATH"]

    def test_promote_suggest_absent_surface_is_zero(self) -> None:
        os.environ["IDEAS_SURFACE_PATH"] = str(self.vault / "nope.md")
        try:
            self.assertEqual(ob.count_promote_suggest(3), 0)
        finally:
            del os.environ["IDEAS_SURFACE_PATH"]

    def test_stale_promoted_counts_only_old_promoted(self) -> None:
        _promoted_entry(self.vault, "src", "p1", (_NOW - timedelta(days=45)).isoformat())  # stale → counts
        _promoted_entry(self.vault, "src", "p2", (_NOW - timedelta(days=5)).isoformat())   # recent → not
        _watchlist_entry(self.vault, "src", "p3", "HIGH", "pending-review")                # not promoted
        _promoted_entry(self.vault, "_archive", "p4", (_NOW - timedelta(days=99)).isoformat())  # archived → excluded
        self.assertEqual(ob.count_stale_promoted(self.vault, 30, _NOW), 1)

    def test_stale_promoted_offset_timestamp_as_produced(self) -> None:
        # The exact format watchlist_review._utcnow_iso() writes: +00:00 offset.
        _promoted_entry(self.vault, "src", "p1", "2026-01-01T00:00:00+00:00")  # ~5mo before _NOW
        self.assertEqual(ob.count_stale_promoted(self.vault, 30, _NOW), 1)

    def test_stale_promoted_z_suffix_timestamp(self) -> None:
        # A trailing-Z variant (other tools / hand-edits) must also parse.
        _promoted_entry(self.vault, "src", "p1", "2026-01-01T00:00:00Z")  # ~5mo before _NOW
        self.assertEqual(ob.count_stale_promoted(self.vault, 30, _NOW), 1)

    def test_stale_promoted_future_date_not_stale(self) -> None:
        # A future promoted_at (clock skew) must NOT count as stale (negative age).
        _promoted_entry(self.vault, "src", "p1", (_NOW + timedelta(days=10)).isoformat())
        self.assertEqual(ob.count_stale_promoted(self.vault, 30, _NOW), 0)

    def test_stale_promoted_missing_date_does_not_count(self) -> None:
        d = self.vault / "personal" / "_skill-watchlist" / "src"
        d.mkdir(parents=True, exist_ok=True)
        (d / "p1.md").write_text("---\nstatus: promoted\n---\nbody\n", encoding="utf-8")  # no promoted_at
        self.assertEqual(ob.count_stale_promoted(self.vault, 30, _NOW), 0)

    def test_counters_never_raise_on_garbage(self) -> None:
        # malformed watchlist frontmatter must not raise
        d = self.vault / "personal" / "_skill-watchlist" / "src"
        d.mkdir(parents=True, exist_ok=True)
        (d / "bad.md").write_bytes(b"\xe9\xe9 not utf-8 \xff")
        self.assertEqual(ob.count_watchlist_high_pending(self.vault), 0)


class TestRender(unittest.TestCase):
    def _cfg(self, **over):
        c = dict(ao.DEFAULT_CONFIG)
        c.update(over)
        return c

    def test_nothing_over_threshold_is_empty(self) -> None:
        signals = {"inbox": 0, "watchlist_high": 0, "incubator": 0, "idea_ledger": 0}
        self.assertEqual(ob.build_briefing(signals, self._cfg()), "")

    def test_renders_active_signals(self) -> None:
        signals = {"inbox": 12, "watchlist_high": 3, "incubator": 0, "idea_ledger": 1}
        out = ob.build_briefing(signals, self._cfg())
        self.assertIn("MemoryVault — pending", out)
        self.assertIn("12 inbox entries", out)
        self.assertIn("3 HIGH skill-watchlist patterns", out)
        self.assertIn("/memory watchlist", out)
        self.assertIn("1 idea-ledger entry", out)
        self.assertNotIn("incubator", out)  # below threshold (0)

    def test_renders_nudges(self) -> None:
        signals = {"inbox": 0, "watchlist_high": 0, "incubator": 0, "idea_ledger": 0,
                   "promote_suggest": 2, "stale_promoted": 1}
        out = ob.build_briefing(signals, self._cfg())
        self.assertIn("2 ideas surfaced", out)
        self.assertIn("/memory promote", out)
        self.assertIn("1 skill-watchlist pattern promoted >30d ago", out)
        self.assertIn("author or dismiss", out)

    def test_nudge_singular(self) -> None:
        signals = {"inbox": 0, "watchlist_high": 0, "incubator": 0, "idea_ledger": 0,
                   "promote_suggest": 1, "stale_promoted": 1}
        out = ob.build_briefing(signals, self._cfg())
        self.assertIn("1 idea surfaced", out)
        self.assertIn("1 skill-watchlist pattern promoted", out)

    def test_renders_staged_adapt(self) -> None:
        signals = {"inbox": 0, "watchlist_high": 0, "incubator": 0, "idea_ledger": 0,
                   "staged_adapt": 3}
        out = ob.build_briefing(signals, self._cfg())
        self.assertIn("3 skill candidates staged for adapt-evaluation", out)
        self.assertIn("/memory adapt-skills", out)

    def test_inbox_below_threshold_suppressed(self) -> None:
        signals = {"inbox": 5, "watchlist_high": 0, "incubator": 0, "idea_ledger": 0}
        self.assertEqual(ob.build_briefing(signals, self._cfg(inbox_threshold=10)), "")

    def test_singular_plural(self) -> None:
        signals = {"inbox": 1, "watchlist_high": 1, "incubator": 1, "idea_ledger": 1}
        out = ob.build_briefing(signals, self._cfg(inbox_threshold=1))
        self.assertIn("1 inbox entry to sort", out)
        self.assertIn("1 HIGH skill-watchlist pattern ", out)
        self.assertIn("1 incubator idea ", out)


class TestEmit(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        # one over-threshold signal so a briefing is warranted
        _watchlist_entry(self.vault, "src", "p1", "HIGH", "pending-review")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_emits_when_warranted_and_records_state(self) -> None:
        out = ob.emit_briefing(self.vault, _NOW)
        self.assertIn("HIGH skill-watchlist", out)
        st = ao.load_state(self.vault)
        self.assertIn("briefing", st["last_fire"])
        self.assertEqual(st["last_shown"], {"watchlist_high": 1})

    def test_emit_seeds_operator_config_on_first_use(self) -> None:
        # The operator-tunable config must materialize on first SessionStart so
        # they can find + edit it (the push-surface works on defaults, but the
        # tunability is invisible without the file).
        self.assertFalse(ao.config_path(self.vault).exists())
        ob.emit_briefing(self.vault, _NOW)
        self.assertTrue(ao.config_path(self.vault).exists())

    def test_second_call_unchanged_is_silent(self) -> None:
        first = ob.emit_briefing(self.vault, _NOW)
        self.assertTrue(first)
        # cooldown=8h default; even past cooldown, unchanged state → silent
        later = _NOW + timedelta(hours=24)
        self.assertEqual(ob.emit_briefing(self.vault, later), "")

    def test_resurfaces_when_state_shifts(self) -> None:
        ob.emit_briefing(self.vault, _NOW)
        _watchlist_entry(self.vault, "src", "p2", "HIGH", "pending-review")  # now 2
        later = _NOW + timedelta(hours=24)  # past cooldown
        out = ob.emit_briefing(self.vault, later)
        self.assertIn("2 HIGH skill-watchlist", out)

    def test_clear_then_refill_resurfaces(self) -> None:
        # Adversarial #1 (clear-refill suppression): after a pile fully clears,
        # the cleared snapshot must be recorded so a later *equal-count* pile
        # reads as a genuine shift instead of being suppressed forever.
        first = ob.emit_briefing(self.vault, _NOW)
        self.assertIn("HIGH skill-watchlist", first)
        # operator clears the only pending entry; the clearing session fires even
        # within the cooldown window (recording the clear is cooldown-independent)
        (self.vault / "personal" / "_skill-watchlist" / "src" / "p1.md").unlink()
        cleared = ob.emit_briefing(self.vault, _NOW + timedelta(hours=1))
        self.assertEqual(cleared, "")
        self.assertEqual(ao.load_state(self.vault)["last_shown"], {})  # snapshot reset
        # a fresh, unrelated equal-count pile builds up → must resurface
        _watchlist_entry(self.vault, "src", "p9", "HIGH", "pending-review")  # back to 1
        out = ob.emit_briefing(self.vault, _NOW + timedelta(hours=24))  # past cooldown
        self.assertIn("1 HIGH skill-watchlist", out)

    def test_clear_snapshot_not_rewritten_when_already_empty(self) -> None:
        # The clear-recording must not churn the state file every boot: with an
        # already-empty last_shown and nothing pending, emit writes no state.
        empty = Path(tempfile.mkdtemp())
        try:
            self.assertEqual(ob.emit_briefing(empty, _NOW), "")
            self.assertFalse(ao.state_path(empty).exists())  # no needless write
        finally:
            import shutil
            shutil.rmtree(empty, ignore_errors=True)

    def test_cooldown_blocks_within_window(self) -> None:
        ob.emit_briefing(self.vault, _NOW)
        _watchlist_entry(self.vault, "src", "p2", "HIGH", "pending-review")  # changed
        soon = _NOW + timedelta(hours=2)  # within 8h cooldown
        self.assertEqual(ob.emit_briefing(self.vault, soon), "")

    def test_stale_promoted_nudge_rides_briefing(self) -> None:
        # setUp seeds a HIGH pending entry; add a stale promoted one → both ride
        # the same briefing block + shifted-state guard.
        _promoted_entry(self.vault, "src", "old", (_NOW - timedelta(days=60)).isoformat())
        out = ob.emit_briefing(self.vault, _NOW)
        self.assertIn("promoted >30d ago", out)
        self.assertIn("HIGH skill-watchlist", out)  # consolidated, one block
        st = ao.load_state(self.vault)
        self.assertEqual(st["last_shown"], {"watchlist_high": 1, "stale_promoted": 1})

    def test_staged_adapt_rides_briefing(self) -> None:
        # The idle chain stages a Pass-1 candidate; the briefing must surface it
        # so the operator runs Pass-2 (closes the discover→adapt→evaluate loop).
        cache = self.vault / "_meta" / "skill-discovery-cache" / "adapt-state" / "src"
        cache.mkdir(parents=True)
        (cache / "newpat.json").write_text("{}", encoding="utf-8")
        out = ob.emit_briefing(self.vault, _NOW)
        self.assertIn("staged for adapt-evaluation", out)
        self.assertEqual(ao.load_state(self.vault)["last_shown"],
                         {"watchlist_high": 1, "staged_adapt": 1})

    def test_promote_suggest_toggle_off_suppresses(self) -> None:
        ideas = self.vault / "Ideas.md"
        ideas.write_text(
            "## 2026-01-01: Recurring\nb\n## 2026-02-01: Recurring\nb\n## 2026-03-01: Recurring\nb\n",
            encoding="utf-8",
        )
        os.environ["IDEAS_SURFACE_PATH"] = str(ideas)
        ao.seed_config(self.vault)
        p = ao.config_path(self.vault)
        p.write_text(p.read_text(encoding="utf-8").replace(
            "enable_promote_suggest = true", "enable_promote_suggest = false"), encoding="utf-8")
        try:
            cfg = ao.load_config(self.vault)
            sig = ob.gather_signals(self.vault, cfg, _NOW)
            self.assertEqual(sig["promote_suggest"], 0)  # toggled off → 0, no parse
        finally:
            del os.environ["IDEAS_SURFACE_PATH"]

    def test_disabled_is_silent(self) -> None:
        ao.seed_config(self.vault)
        p = ao.config_path(self.vault)
        p.write_text(p.read_text(encoding="utf-8").replace("enable_briefing = true", "enable_briefing = false"), encoding="utf-8")
        self.assertEqual(ob.emit_briefing(self.vault, _NOW), "")

    def test_nothing_pending_is_silent(self) -> None:
        empty = Path(tempfile.mkdtemp())
        try:
            self.assertEqual(ob.emit_briefing(empty, _NOW), "")
        finally:
            import shutil
            shutil.rmtree(empty, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
