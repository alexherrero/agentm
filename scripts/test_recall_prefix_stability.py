#!/usr/bin/env python3
"""Regression GUARD for recall prefix-stability + floor-dedup (#46 Part A tasks 1-2).

Context — this is a GUARD, not a fail-first bugfix test. The /bugfix Analyze step
(2026-06-13) found the cache-poison signature described in #46 Part A is NOT live:
the recall engine already emits a prefix-stable SessionStart block and already
dedups the always-load floor out of UserPromptSubmit, so tasks 1-2 prescribe no
code change. These tests therefore PASS against current code. Their value is
forward-looking: they fail if a future edit REINTRODUCES the per-turn mutation
that would bust the Anthropic prompt cache — a timestamp/counter in the
SessionStart output, a non-deterministic entry order, or dropping the floor-dedup.

The properties pinned (all proven to be mutation-detectable — see the file's
companion mutation notes in the bugfix PLAN.md):

  Task 1 — prefix-stable SessionStart emission:
    * Two consecutive session_start() runs against the same vault are
      byte-identical (no per-turn-varying token at a fixed early position).
    * Entries are emitted in deterministic sorted() order, not raw glob order
      (stable across process restarts / filesystems, where glob order is not).

  Task 2 — no floor re-emission at UserPromptSubmit:
    * query() excludes always-load paths passed in dedup_paths — proven by the
      paired with/without assertion (the entry IS a grep candidate; dedup is
      what removes it, not a no-match).
    * prompt_submit() end-to-end never re-emits an always-load slug, even when
      the prompt text matches that entry's body.

Pure-Python function tests (no bash subprocess) → cross-platform, no skip.
recall.py is imported directly via sys.path injection of its scripts dir; the
fixture vault has NO vec index, so recall falls back to deterministic grep-only
(mode="stub" keeps the embed step fast + offline). Generous budgets avoid the
embedding/budget flakiness that makes the bash-hook path non-deterministic.

Run: python3 scripts/test_recall_prefix_stability.py
"""
from __future__ import annotations

import io
import re
import sys
import tempfile
import time as _time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_RECALL_SCRIPTS = _REPO / "harness" / "skills" / "memory" / "scripts"
if str(_RECALL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_RECALL_SCRIPTS))

import recall  # noqa: E402 — after sys.path injection


# Distinctive body token (len >= _MIN_TOKEN_LEN, lowercase alnum) so the
# always-load entry is an unambiguous grep match for a query that contains it.
_FLOOR_TOKEN = "zorptackle"

# Real clock fns captured before any patch, so the frozen-clock shim can build
# return values from them without recursing into the patched versions.
_REAL_LOCALTIME = _time.localtime
_REAL_GMTIME = _time.gmtime
_REAL_STRFTIME = _time.strftime


@contextmanager
def _frozen_clock(epoch: float):
    """Freeze the `time` module's WALL-clock readers at `epoch` for recall.

    recall.py does `import time` (module-level) and reads only the `time`
    module for any clock. We patch the wall-clock entrypoints (time/localtime/
    gmtime/strftime/ctime/asctime) so a clock-derived token in recall's output
    is driven by `epoch`. `time.monotonic` is deliberately left REAL — it backs
    the recall time budget, not output, and patching it would break the deadline
    logic. (datetime.now() reads the OS clock directly, not via time.time, so a
    hypothetical datetime-based stamp is out of this shim's reach — that residual
    is covered by the digit/HH:MM heuristic test instead.)
    """
    e = float(epoch)
    with mock.patch.object(_time, "time", lambda: e), \
         mock.patch.object(_time, "localtime",
                           lambda secs=None: _REAL_LOCALTIME(e if secs is None else secs)), \
         mock.patch.object(_time, "gmtime",
                           lambda secs=None: _REAL_GMTIME(e if secs is None else secs)), \
         mock.patch.object(_time, "strftime",
                           lambda fmt, t=None: _REAL_STRFTIME(fmt, _REAL_LOCALTIME(e) if t is None else t)), \
         mock.patch.object(_time, "ctime",
                           lambda secs=None: _REAL_STRFTIME("%a %b %e %H:%M:%S %Y", _REAL_LOCALTIME(e if secs is None else secs))), \
         mock.patch.object(_time, "asctime",
                           lambda t=None: _REAL_STRFTIME("%a %b %e %H:%M:%S %Y", _REAL_LOCALTIME(e) if t is None else t)):
        yield


class _VaultFixture(unittest.TestCase):
    """Builds a tempfile vault with always-load entries under
    personal/_always-load/. No vec index → recall is grep-only +
    deterministic."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        self.always_load = self.vault / "personal" / "_always-load"
        self.always_load.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_entry(self, slug: str, body: str, *, kind: str = "convention",
                     tags: str = "[test]") -> None:
        (self.always_load / f"{slug}.md").write_text(
            f"---\nname: {slug}\ndescription: guard fixture\nkind: {kind}\n"
            f"tags: {tags}\nmetadata:\n  type: reference\n---\n\n{body}\n",
            encoding="utf-8",
        )

    def _session_start(self, *, budget_ms: int = 10_000, at_epoch: float | None = None) -> str:
        out, err = io.StringIO(), io.StringIO()
        if at_epoch is None:
            rc = recall.session_start(
                vault=self.vault, budget_ms=budget_ms, stdout=out, stderr=err,
            )
        else:
            with _frozen_clock(at_epoch):
                rc = recall.session_start(
                    vault=self.vault, budget_ms=budget_ms, stdout=out, stderr=err,
                )
        self.assertEqual(rc, 0, err.getvalue())
        return out.getvalue()


class TestSessionStartPrefixStable(_VaultFixture):

    def test_session_start_emits_byte_identical_prefix_across_runs(self) -> None:
        # Task 1 — catches per-CALL variance: a counter, a PID, or random()
        # injected at this fixed early position would bust the KV-cache; this
        # fails the instant two back-to-back runs diverge by a single byte.
        # (NOTE: two in-process calls share the wall clock to the millisecond,
        # so this does NOT catch a clock-derived token — see the clock-skew test
        # below, which is the real cross-session guard.)
        self._write_entry("alpha-pref", f"first body with {_FLOOR_TOKEN}")
        self._write_entry("zeta-pref", "second body")
        first = self._session_start()
        second = self._session_start()
        self.assertTrue(first.strip(), "session_start emitted nothing — fixture broken")
        self.assertEqual(first, second, "SessionStart output is not byte-identical "
                         "across runs — a per-turn-varying token would poison the cache")

    def test_session_start_byte_identical_under_clock_skew(self) -> None:
        # Task 1 — THE cross-session guard (closes the in-process blind spot the
        # back-to-back test misses). The cache lives across real sessions —
        # separate processes, separate days — so a clock-derived "freshness"
        # token (e.g. a `loaded <time.strftime(...)>` stamp in the header) is
        # constant within one process but varies session-to-session, busting the
        # cache every session. We render SessionStart at two wall-clock instants
        # > a day apart (differing in day/hour/minute/second/weekday) and require
        # byte-identity: any `time`-module-derived token diverges and fails here.
        self._write_entry("alpha-pref", f"first body with {_FLOOR_TOKEN}")
        self._write_entry("zeta-pref", "second body")
        epoch_a = 1_700_000_000          # 2023-11-14 ~22:13 UTC
        epoch_b = epoch_a + 93_784       # +1d 2h 3m 4s → every field differs
        at_a = self._session_start(at_epoch=epoch_a)
        at_b = self._session_start(at_epoch=epoch_b)
        self.assertTrue(at_a.strip(), "session_start emitted nothing — fixture broken")
        self.assertEqual(at_a, at_b, "SessionStart prefix varies with the wall clock — "
                         "a clock-derived token (freshness stamp, etc.) poisons the "
                         "KV-cache across sessions")

    def test_session_start_emits_entries_in_deterministic_sorted_order(self) -> None:
        # Task 1 — order must be sorted(), not raw glob order, so the prefix is
        # stable across process restarts / filesystems where glob order can vary.
        # zeta written first; sorted() must still place alpha before zeta.
        self._write_entry("zeta-pref", "zeta body")
        self._write_entry("alpha-pref", "alpha body")
        out = self._session_start()
        i_alpha, i_zeta = out.index("alpha-pref"), out.index("zeta-pref")
        self.assertLess(i_alpha, i_zeta, "entries not in deterministic sorted order "
                        "(creation order leaked into the emitted prefix)")

    def test_session_start_output_has_no_timestamp_like_tokens(self) -> None:
        # Task 1 — belt-and-suspenders for the residual the clock-skew test can't
        # reach: a `datetime.now()`-based stamp reads the OS clock directly (not
        # the `time` module the skew shim patches), so it would slip past that
        # test. The fixture bodies/framing contain NO digits and NO `HH:MM`, so
        # any 4+ digit run (ISO date, year, epoch) or clock-time pattern in the
        # output is a smell — almost certainly an injected timestamp.
        self._write_entry("alpha-pref", f"body {_FLOOR_TOKEN}")
        out = self._session_start()
        self.assertIsNone(re.search(r"\d{4,}", out),
                          "SessionStart output contains a 4+ digit run — possible "
                          f"timestamp/date token (cache-poison): {out!r}")
        self.assertIsNone(re.search(r"\b\d{1,2}:\d{2}\b", out),
                          "SessionStart output contains an HH:MM clock pattern — "
                          f"possible timestamp token (cache-poison): {out!r}")


class TestPromptSubmitNoFloorReemission(_VaultFixture):

    def _always_load_rel(self, slug: str) -> str:
        return (self.always_load / f"{slug}.md").relative_to(self.vault).as_posix()

    def test_query_dedups_always_load_paths(self) -> None:
        # Task 2 — the rigorous core. The always-load entry IS a grep candidate
        # (its body contains the query token, and _always-load/ is in the grep
        # walk). Proven by the paired assertion: present without dedup, absent
        # with it. This rules out "absent for the wrong reason (no match at all)".
        self._write_entry("floor-entry", f"durable note about {_FLOOR_TOKEN}")
        rel = self._always_load_rel("floor-entry")

        without = recall.query(vault=self.vault, query_text=_FLOOR_TOKEN,
                               dedup_paths=set(), mode="stub")
        slugs_without = {r["slug"] for r in without}
        self.assertIn("floor-entry", slugs_without,
                      "fixture broken: always-load entry is not even a grep match")

        with_dedup = recall.query(vault=self.vault, query_text=_FLOOR_TOKEN,
                                  dedup_paths={rel}, mode="stub")
        slugs_with = {r["slug"] for r in with_dedup}
        self.assertNotIn("floor-entry", slugs_with,
                         "query() re-surfaced an always-load path despite dedup — "
                         "the floor would be re-emitted per turn (cache-poison)")

    def test_prompt_submit_does_not_reemit_always_load_floor(self) -> None:
        # Task 2 — end-to-end. Even when the prompt matches the floor entry's
        # body, prompt_submit() must not emit the floor slug (it was already
        # emitted once at SessionStart). Generous budget + stub embed keep the
        # grep path deterministic.
        self._write_entry("floor-entry", f"durable note about {_FLOOR_TOKEN}")
        out, err = io.StringIO(), io.StringIO()
        rc = recall.prompt_submit(
            vault=self.vault, prompt=f"tell me about {_FLOOR_TOKEN}",
            budget_ms=10_000, mode="stub", stdout=out, stderr=err,
        )
        self.assertEqual(rc, 0, err.getvalue())
        self.assertNotIn("floor-entry", out.getvalue(),
                         "UserPromptSubmit re-emitted an always-load entry — "
                         "the floor is being re-injected per turn (cache-poison)")


if __name__ == "__main__":
    unittest.main()
