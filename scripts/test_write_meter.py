#!/usr/bin/env python3
"""The write meter: what the vault wrote in a window, by class, against a budget.

Task 190, step 1. The write-quality audit of 2026-10-07 found that almost no
memory card written on 10-05 and 10-06 was earned, and that maps, trackers,
`needs-review` and entity pages were rewritten with nothing in them changed.
No report could see either. The meter reads the vault's git history, so each
test here builds a scratch vault in the live layout (`agent/memory/...` beside
`projects/...`), commits writes at chosen times, and holds the reading to them:
each count, a date-only rewrite, an order-only rewrite, enrichment in a
project doc, the budget warning, a stalled committer, and the two seams that
print the reading (the scorecard's row and the morning note's line).

Run: python3 scripts/test_write_meter.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_SKILL = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for p in (_SKILL, _HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import corpus_scorecard  # noqa: E402
import morning_note  # noqa: E402
import write_meter as wm  # noqa: E402

# The window every test reads: 2026-10-05 00:00 to 10-06 00:00, local time.
SINCE = datetime(2026, 10, 5, 0, 0).timestamp()
UNTIL = datetime(2026, 10, 6, 0, 0).timestamp()
BEFORE = datetime(2026, 10, 4, 12, 0).timestamp()
INSIDE = datetime(2026, 10, 5, 2, 30).timestamp()
LATER = datetime(2026, 10, 5, 22, 0).timestamp()
AFTER = datetime(2026, 10, 6, 6, 0).timestamp()

PROBE = """---
title: "AgentM self-probe 2026-10-05T07:32:20Z"
type: reference
probe: self-probe
---

Synthetic round-trip probe.
"""

MAP = """---
title: Reference
updated: {updated}
members: 2
---

# Reference

- [[alpha]] — Alpha · {alpha}
- [[beta]] — Beta
"""


class _Rules:
    def __init__(self, **thresholds):
        self._t = thresholds

    def thresholds(self):
        return dict(self._t)


class _Vault(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="write-meter-")))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        empty = self.tmp / "gitconfig"
        empty.write_text("", encoding="utf-8")
        env = {"GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
            os.environ.pop(key, None)
        self.vault = self.tmp / "vault"
        self.mem = self.vault / "agent"
        (self.mem / "memory" / "semantic").mkdir(parents=True)
        self.git("init", "-q", str(self.vault), cwd=self.tmp)
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.write("agent/memory/semantic/seed.md", "# seed\n")
        self.commit(BEFORE)

    def git(self, *args, cwd=None):
        env = dict(os.environ)
        env.update(getattr(self, "_dates", {}))
        subprocess.run(["git", *args], cwd=cwd or self.vault, env=env, check=True,
                       capture_output=True, text=True)

    def write(self, rel: str, text: str) -> Path:
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def remove(self, rel: str):
        (self.vault / rel).unlink()

    def commit(self, at: float, msg: str = "vault: local edits"):
        stamp = f"@{int(at)} +0000"
        self._dates = {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", msg)
        self._dates = {}

    def measure(self, **kw):
        kw.setdefault("since", SINCE)
        kw.setdefault("until", UNTIL)
        kw.setdefault("rules", _Rules())
        return wm.measure(self.mem, **kw)


class CardTests(_Vault):

    def test_new_cards_are_counted_by_class_and_the_probe_apart(self):
        self.write("agent/memory/episodic/2026-10-05-session-a.md", "# a\n")
        self.write("agent/memory/episodic/2026-10-05-session-b.md", "# b\n")
        self.write("agent/memory/crystallized/lesson.md", "# lesson\n")
        self.write("agent/memory/entities/issues/o-r-870.md", "# o/r#870\n")
        self.write("agent/memory/semantic/agentm-self-probe-2026-10-05t07-32-20z.md", PROBE)
        self.commit(INSIDE)
        m = self.measure()
        self.assertEqual(m["new_cards"], 4)
        self.assertEqual(m["by_class"], {"episodic": 2, "entities": 1, "crystallized": 1})
        self.assertEqual(m["probes"], 1)

    def test_a_card_written_and_deleted_inside_the_window_still_counts(self):
        # The audit's two mined preference candidates: written at 02:35 and
        # purged the same evening. A census of the corpus never sees them.
        self.write("agent/memory/semantic/candidate-x.md", "# x\n")
        self.commit(INSIDE)
        self.remove("agent/memory/semantic/candidate-x.md")
        self.commit(LATER)
        m = self.measure()
        self.assertEqual(m["new_cards"], 1)
        self.assertEqual(m["net_notes"], 0)

    def test_writes_outside_the_window_are_not_counted(self):
        self.write("agent/memory/episodic/early.md", "# early\n")
        self.commit(BEFORE + 60)
        self.write("agent/memory/episodic/late.md", "# late\n")
        self.commit(AFTER)
        self.assertEqual(self.measure()["new_cards"], 0)

    def test_a_card_moved_between_classes_is_not_new(self):
        self.write("agent/memory/semantic/rule.md", "# a rule\n\n" + "the same body\n" * 20)
        self.commit(BEFORE + 60)
        (self.vault / "agent/memory/procedural").mkdir(parents=True)
        os.rename(self.vault / "agent/memory/semantic/rule.md",
                  self.vault / "agent/memory/procedural/rule.md")
        self.commit(INSIDE)
        m = self.measure()
        self.assertEqual(m["new_cards"], 0)
        self.assertEqual(m["net_notes"], 0)

    def test_a_note_filed_into_a_class_from_outside_memory_is_new(self):
        self.write("agent/inbox/capture.md", "# capture\n\n" + "what was said\n" * 20)
        self.commit(BEFORE + 60)
        os.rename(self.vault / "agent/inbox/capture.md",
                  self.vault / "agent/memory/semantic/capture.md")
        self.commit(INSIDE)
        self.assertEqual(self.measure()["by_class"], {"semantic": 1})

    def test_an_index_page_is_not_a_card(self):
        self.write("agent/memory/episodic/_index.md", "# index\n")
        self.commit(INSIDE)
        self.assertEqual(self.measure()["new_cards"], 0)

    def test_the_vaults_net_notes_count_adds_less_deletes(self):
        self.write("projects/p/research/one.md", "# one\n")
        self.write("projects/p/research/two.md", "# two\n")
        self.write("projects/p/data.json", "{}\n")
        self.commit(INSIDE)
        self.remove("agent/memory/semantic/seed.md")
        self.commit(LATER)
        m = self.measure()
        self.assertEqual(m["net_notes"], 1)
        self.assertEqual(m["commits"], 2)

    @unittest.skipIf(os.name == "nt", "Windows allows no name git would quote: no quote, backslash or control character")
    def test_a_name_git_quotes_is_still_read(self):
        self.write('agent/memory/episodic/a "quoted" name.md', "# q\n")
        self.commit(INSIDE)
        self.assertEqual(self.measure()["by_class"], {"episodic": 1})

    def test_a_flat_layout_reads_the_same(self):
        # A memory root that is the vault root, with no `agent/` above it.
        (self.vault / "memory" / "episodic").mkdir(parents=True)
        self.write("memory/episodic/flat.md", "# flat\n")
        self.commit(INSIDE)
        m = wm.measure(self.vault, since=SINCE, until=UNTIL, rules=_Rules())
        self.assertEqual(m["by_class"], {"episodic": 1})


class RewriteTests(_Vault):

    def seed_map(self):
        self.write("agent/memory/mocs/reference.md", MAP.format(updated="2026-10-04", alpha="2026-10-01"))
        self.commit(BEFORE + 60)

    def test_a_map_whose_only_change_is_its_date_is_a_date_only_rewrite(self):
        self.seed_map()
        self.write("agent/memory/mocs/reference.md", MAP.format(updated="2026-10-05", alpha="2026-10-05"))
        self.commit(INSIDE)
        m = self.measure()
        self.assertEqual(m["derived"]["maps"], {"rewrites": 1, "date_only": 1, "order_only": 0})
        self.assertEqual(m["no_change_rewrites"], ["agent/memory/mocs/reference.md"])
        self.assertIn("1 derived-page rewrite(s) changed only dates or order", m["warnings"])

    def test_a_map_whose_lines_only_moved_is_an_order_only_rewrite(self):
        self.seed_map()
        text = MAP.format(updated="2026-10-04", alpha="2026-10-01")
        a, b = "- [[alpha]] — Alpha · 2026-10-01\n", "- [[beta]] — Beta\n"
        self.write("agent/memory/mocs/reference.md", text.replace(a + b, b + a))
        self.commit(INSIDE)
        self.assertEqual(self.measure()["derived"]["maps"],
                         {"rewrites": 1, "date_only": 0, "order_only": 1})

    def test_a_map_that_gained_a_member_changed(self):
        self.seed_map()
        text = MAP.format(updated="2026-10-05", alpha="2026-10-01") + "- [[gamma]] — Gamma\n"
        self.write("agent/memory/mocs/reference.md", text)
        self.commit(INSIDE)
        m = self.measure()
        self.assertEqual(m["derived"]["maps"], {"rewrites": 1, "date_only": 0, "order_only": 0})
        self.assertEqual(m["warnings"], [])

    def test_needs_review_moving_only_with_the_calendar_is_date_only(self):
        # The audit's shape: today's date stamped and "N days silent" ticking.
        page = ("---\nupdated: {d}\n---\n\nThe sections below come from the dream cycle of {d}.\n\n"
                "- [[old-note]] ({n} days silent)\n")
        self.write("agent/memory/mocs/needs-review.md", page.format(d="2026-10-04", n="1,203"))
        self.commit(BEFORE + 60)
        self.write("agent/memory/mocs/needs-review.md", page.format(d="2026-10-05", n="1,204"))
        self.commit(INSIDE)
        m = self.measure()
        self.assertEqual(m["derived"]["needs-review"], {"rewrites": 1, "date_only": 1, "order_only": 0})
        self.assertEqual(m["derived"]["maps"]["rewrites"], 0)

    def test_each_commit_that_rewrites_a_tracker_is_a_rewrite(self):
        # The audit's double write: rendered, then line-edited the same night.
        tracker = "---\nkind: tracker\nupdated: 2026-10-04\nactivity: {a}\n---\n\n## State\n\n{s}\n"
        self.write("projects/p/tracker.md", tracker.format(a="quiet", s="one task"))
        self.commit(BEFORE + 60)
        self.write("projects/p/tracker.md", tracker.format(a="quiet", s="two tasks"))
        self.commit(INSIDE)
        self.write("projects/p/tracker.md", tracker.format(a="active", s="two tasks"))
        self.commit(INSIDE + 60)
        self.assertEqual(self.measure()["derived"]["trackers"]["rewrites"], 2)

    def test_a_task_tracker_is_project_work_not_a_derived_page(self):
        self.write("projects/p/tasks/001-x/tracker.md", "---\nupdated: 2026-10-04\n---\n")
        self.commit(BEFORE + 60)
        self.write("projects/p/tasks/001-x/tracker.md", "---\nupdated: 2026-10-05\n---\n")
        self.commit(INSIDE)
        self.assertEqual(self.measure()["derived"]["trackers"]["rewrites"], 0)

    def test_entity_pages_and_project_maps_are_derived(self):
        self.write("agent/memory/entities/issues/o-r-1.md", "Mentioned in 2 notes.\n")
        self.write("projects/p/moc-p.md", "- [[a]]\n")
        self.commit(BEFORE + 60)
        self.write("agent/memory/entities/issues/o-r-1.md", "Mentioned in 3 notes.\n")
        self.write("projects/p/moc-p.md", "- [[a]]\n- [[b]]\n")
        self.commit(INSIDE)
        m = self.measure()
        self.assertEqual(m["derived"]["entities"]["rewrites"], 1)
        self.assertEqual(m["derived"]["maps"]["rewrites"], 1)

    def test_a_page_written_new_or_deleted_is_not_a_rewrite(self):
        self.write("agent/memory/mocs/workflow.md", "# workflow\n")
        self.commit(INSIDE)
        self.remove("agent/memory/mocs/workflow.md")
        self.commit(LATER)
        self.assertEqual(self.measure()["derived"]["maps"]["rewrites"], 0)


class EnrichmentTests(_Vault):

    DESIGN = "---\ntitle: A design\nupdated: 2026-10-01\n---\n\n# A design\n\nThe operator's words.\n"

    def test_enrichment_in_a_project_doc_is_counted_with_its_section(self):
        self.write("projects/p/designs/d.md", self.DESIGN)
        self.commit(BEFORE + 60)
        enriched = self.DESIGN.replace(
            "updated: 2026-10-01\n",
            "updated: 2026-10-05\nenriched_by: enrich/2\nenriched_at: 2026-10-05T09:30:00Z\n")
        enriched += "\n## Added by dreaming (2026-10-05)\n\nA restatement of the links.\n"
        self.write("projects/p/designs/d.md", enriched)
        self.commit(INSIDE)
        self.assertEqual(self.measure()["enrichment"], {"project_docs": 1, "sections": 1})

    def test_an_operator_edit_to_a_project_doc_is_not_enrichment(self):
        self.write("projects/p/designs/d.md", self.DESIGN)
        self.commit(BEFORE + 60)
        self.write("projects/p/designs/d.md", self.DESIGN + "\nA new paragraph.\n")
        self.commit(INSIDE)
        self.assertEqual(self.measure()["enrichment"], {"project_docs": 0, "sections": 0})


class BudgetTests(_Vault):

    def cards(self, n):
        for i in range(n):
            self.write(f"agent/memory/episodic/s-{i}.md", f"# {i}\n")
        self.commit(INSIDE)

    def test_a_day_over_budget_is_a_warning(self):
        self.cards(3)
        m = self.measure(rules=_Rules(daily_card_budget=2))
        self.assertEqual(m["allowed"], 2)
        self.assertIn("3 new cards, over the budget of 2", m["warnings"])

    def test_a_day_at_budget_is_not(self):
        self.cards(2)
        self.assertEqual(self.measure(rules=_Rules(daily_card_budget=2))["warnings"], [])

    def test_self_probes_do_not_count_against_the_budget(self):
        self.write("agent/memory/semantic/agentm-self-probe-x.md", PROBE)
        self.commit(INSIDE)
        self.assertEqual(self.measure(rules=_Rules(daily_card_budget=1))["warnings"], [])
        self.assertEqual(self.measure(rules=_Rules(daily_card_budget=1))["probes"], 1)

    def test_the_default_budget_is_thirty_a_day(self):
        self.assertEqual(wm.budget(_Rules()), 30)
        self.assertEqual(self.measure()["allowed"], 30)

    def test_a_budget_of_zero_turns_the_warning_off(self):
        self.cards(3)
        m = self.measure(rules=_Rules(daily_card_budget=0))
        self.assertIsNone(m["allowed"])
        self.assertEqual(m["warnings"], [])

    def test_a_longer_window_scales_the_budget(self):
        m = self.measure(since=SINCE, until=SINCE + 2 * wm.DAY, rules=_Rules(daily_card_budget=30))
        self.assertEqual(m["allowed"], 60)

    def test_the_shipped_contract_names_the_budget(self):
        shipped = (_HERE.parent / "daemon/internal/rules/storage-rules.default.md").read_text(encoding="utf-8")
        self.assertRegex(shipped, r"(?m)^  daily_card_budget: 30$")


class StallTests(_Vault):

    def test_changes_far_newer_than_the_last_commit_say_the_committer_is_behind(self):
        now = time.time()
        self.commit(now - 3 * 3600)
        p = self.write("agent/memory/semantic/uncommitted.md", "# not yet\n")
        os.utime(p, (now - 60, now - 60))
        m = wm.measure(self.mem, rules=_Rules())
        self.assertIsNotNone(m["behind_seconds"])
        self.assertTrue(any("the committer is" in w and "behind" in w for w in m["warnings"]), m["warnings"])

    def test_a_clean_tree_is_not_behind(self):
        self.commit(time.time() - 3 * 3600)
        self.assertIsNone(wm.measure(self.mem, rules=_Rules())["behind_seconds"])

    def test_a_past_window_does_not_ask(self):
        self.write("agent/memory/semantic/uncommitted.md", "# not yet\n")
        self.assertIsNone(self.measure()["behind_seconds"])


class AbsenceTests(unittest.TestCase):

    def test_a_vault_outside_git_raises_rather_than_reading_zero(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "memory").mkdir()
            with self.assertRaisesRegex(RuntimeError, "not a git working tree"):
                wm.measure(Path(d), rules=_Rules())


class ClassifyTests(unittest.TestCase):

    def test_masking_covers_dates_clock_times_and_day_counts(self):
        self.assertEqual(wm.mask("updated: 2026-10-05"), "updated: <date>")
        self.assertEqual(wm.mask("at 2026-10-05T07:32:20Z"), "at <date>")
        self.assertEqual(wm.mask("Written 2026-10-06 02:26Z"), "Written <date>")
        self.assertEqual(wm.mask("ran at 02:26"), "ran at <time>")
        self.assertEqual(wm.mask("(1,204 days silent)"), "(<n> days silent)")

    def test_a_count_that_moved_is_content(self):
        self.assertEqual(wm.classify_change(["Mentioned in 6 notes."], ["Mentioned in 7 notes."]), "content")

    def test_a_link_to_a_new_note_is_content_even_when_it_carries_a_date(self):
        old = ["- [[agentm-self-probe-2026-10-05t07-32-20z]]"]
        new = ["- [[agentm-self-probe-2026-10-06t07-35-48z]]"]
        self.assertEqual(wm.classify_change(old, new), "content")


class SeamTests(_Vault):

    def test_the_scorecard_carries_a_writes_row(self):
        self.write("agent/memory/episodic/s.md", "# s\n")
        self.commit(time.time() - 600)
        row = corpus_scorecard._write_meter_reading(self.mem).render()
        self.assertTrue(row.startswith("| writes, the last 24 hours | 1 |"), row)
        self.assertIn("episodic 1", row)

    def test_the_scorecard_row_is_an_absence_outside_git(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "memory").mkdir()
            row = corpus_scorecard._write_meter_reading(Path(d)).render()
        self.assertIn("| — | not measured:", row)
        self.assertIn("not a git working tree", row)

    def test_the_morning_note_carries_one_line(self):
        self.write("agent/memory/episodic/s.md", "# s\n")
        self.commit(time.time() - 600)
        night = morning_note.Night(now=time.time(), start=time.time() - 3600)
        morning_note.read_writes(night, self.mem)
        lines = morning_note.writes_line(night)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("- **Writes, the last 24 hours:** 1 new card(s) (episodic 1)"), lines[0])

    def test_the_morning_note_says_when_writes_were_not_measured(self):
        night = morning_note.Night(now=time.time(), start=time.time() - 3600)
        with tempfile.TemporaryDirectory() as d:
            morning_note.read_writes(night, Path(d))
        self.assertIn("not measured", morning_note.writes_line(night)[0])


if __name__ == "__main__":
    unittest.main()
