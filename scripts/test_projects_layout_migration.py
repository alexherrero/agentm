#!/usr/bin/env python3
"""Tests for scripts/migrate/projects_layout.py (agentm-vault plan 10).

A scratch vault under git holding two projects in the shapes the table names: a
flat pair with a progress log, a singleton whose name only its title gives, a
queued draft, archived plans with and without a slug, a brief, a design, a
research bundle, the ledgers, a handoff and a charter. The writers-quiet check
and the clock are injected, so no test touches launchd or the wall clock.

What the tests are for: the table is the whole safety argument. Every file moves
once because every file matches exactly one row; nothing is lost because the
manifest records the reverse of each move before any of them runs; and the move
cannot start while an open task's State and Next are still unwritten.

Run directly:

    python3 scripts/test_projects_layout_migration.py
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location(
    "projects_layout", _HERE / "migrate" / "projects_layout.py")
assert _SPEC and _SPEC.loader
mig = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mig)  # type: ignore[union-attr]

TODAY = "2026-09-16"


def _git(vault: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(vault), "-c", "user.name=t",
                        "-c", "user.email=t@example.com", *args],
                       capture_output=True, text=True)
    return r.stdout


def _plan_body(title: str, status: str, created: str = "2026-06-01", goal: str = "The goal.") -> str:
    return (f"# Plan: {title}\n\n**Status:** {status}\n**Created:** {created}\n\n"
            f"## Goal\n\n{goal}\n\n## Tasks\n\n### 1. One\n")


class Fixture(unittest.TestCase):
    """Two projects: `alpha` in the full shape, `beta` with only a singleton."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.vault, self.state = base / "Vault", base / "state"
        self.out = self.state / mig.STAGE
        self.alpha = self.vault / "projects" / "alpha"
        self.beta = self.vault / "projects" / "beta"
        a = self.alpha / "_harness"
        (a / "archive" / "v3").mkdir(parents=True)
        (a / "queued-plans").mkdir()
        (a / "designs" / "a-design").mkdir(parents=True)
        (a / "research-old").mkdir()
        (a / "labels").mkdir()

        self.write(a / "PLAN-build-the-widget.md",
                   _plan_body("Build the widget", "in-progress", "2026-06-10"))
        self.write(a / "progress-build-the-widget.md", "# Progress\n\nA line.\n")
        self.write(a / "PLAN-ship-the-thing.md",
                   _plan_body("Ship the thing", "planning", "2026-06-11"))
        self.write(a / "queued-plans" / "PLAN-trim-the-edges.md",
                   _plan_body("Trim the edges", "planning", "2026-06-12"))
        self.write(a / "archive" / "v3" / "PLAN.archive.20260501-cut-the-rope.md",
                   _plan_body("Cut the rope", "done"))
        self.write(a / "archive" / "v3" / "progress.archive.20260501-cut-the-rope.md",
                   "# Progress\n\nClosed.\n")
        self.write(a / "archive" / "v3" / "PLAN.archive.20260502.md",
                   _plan_body("Name me from my title", "done"))
        self.write(a / "archive" / "v3" / "PLAN.archive.20260503-fold-the-map.md",
                   _plan_body("Fold the map", "SUPERSEDED (2026-07-06) — never built"))
        self.write(a / "archive" / "notes.md", "A finished record.\n")
        self.write(a / "BRIEF-build-the-widget.md", "The brief.\n")
        self.write(a / "PROMPT-build-the-widget.md", "The prompt.\n")
        self.write(a / "BRIEF-unattached.md", "No task yet.\n")
        self.write(a / "designs" / "a-design" / "design-doc.md", "A design.\n")
        self.write(a / "research-old" / "finding.md", "A finding.\n")
        # `research/` is already the destination and names no bundle; a bundle
        # directory names one in its own name. Both are the research row.
        self.write(a / "research" / "deep.md", "A deeper finding.\n")
        self.write(a / "FOLLOWUPS.md", "- one\n")
        self.write(a / "ROADMAP-MASTER.md", "# Roadmap\n")
        self.write(a / "ROADMAP.md", "# The open table\n")
        self.write(a / "board-items.json", '{"items": []}\n')
        self.write(a / "features.json", "[]\n")
        self.write(a / "labels" / "one.md", "A label.\n")
        self.write(a / "week3-handoff.md", "A handoff.\n")
        self.write(a / "init.sh", "#!/bin/sh\n")
        self.write(a / ".project-mode", "local\n")
        self.write(self.alpha / "_index.md", "# Alpha\n")

        b = self.beta / "_harness"
        b.mkdir(parents=True)
        self.write(b / "PLAN.md", _plan_body("Beta — the only plan", "active", "2026-06-05"))
        self.write(b / "progress.md", "# Progress\n\nBeta.\n")
        self.write(self.beta / "_index.md", "# Beta\n")

        # A note elsewhere that links to what moves, and to an ambiguous name.
        self.note = self.vault / "agent" / "memory" / "semantic" / "a-card.md"
        # `progress` names two moved notes once beta's singleton log moves beside
        # a second one, so the rewrite must leave it alone rather than guess.
        self.write(self.alpha / "_harness" / "archive" / "v3" / "progress.md",
                   "# A second progress log\n")
        self.write(self.note,
                   "See [[PLAN-build-the-widget]] and [[PLAN-ship-the-thing|the thing]] "
                   "and [[BRIEF-unattached#Why]] and [[progress]].\n")
        # Five archived projects become `projects/completed/`; a loose file
        # sitting directly in `_archive/` belongs to no project and keeps its
        # own name there.
        self.write(self.vault / "projects" / "_archive" / "old-thing" / "_index.md", "# Old\n")
        self.write(self.vault / "projects" / "_archive" / "a-loose-note.md", "Loose.\n")

        _git(self.vault, "init", "-q")
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-q", "-m", "fixture")

    def write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def plan(self, slugs=None):
        plan = mig.build_plan(self.vault, slugs=slugs or {})
        plan["run_id"] = "run-1"
        plan["slugs_applied"] = slugs or {}
        return plan

    def open_trackers(self) -> None:
        """Write the State and Next an open task's tracker needs, as the operator
        does before the move — beside the flat pair, so the move carries them."""
        import tracker as tk
        for path, project, title in (
            (self.alpha / "_harness" / "tracker-build-the-widget.md", "alpha", "Build the widget"),
            (self.beta / "_harness" / "tracker.md", "beta", "Beta"),
        ):
            self.write(path, tk.render(tk.Tracker(
                title=title, project=project, status="active", opened=TODAY, updated=TODAY,
                objective="The goal.", state="Half of it is built.",
                next_steps="Build the other half.")))

    def apply(self, plan, count=None, writers=lambda: [], repo_roots=None):
        return mig.apply(self.vault, plan, plan["totals"]["moves"] if count is None else count,
                         self.out, writers=writers, today=TODAY,
                         repo_roots=repo_roots or (lambda: []))

    def dest(self, plan, src_suffix: str) -> str:
        hits = [m["dst"] for m in plan["moves"] if m["src"].endswith(src_suffix)]
        self.assertEqual(len(hits), 1, f"{src_suffix}: {hits}")
        return hits[0]


class TheTable(Fixture):
    """Every file matches exactly one row, and each row lands where the design
    says. That is what makes "every file moves once" a property of the run."""

    def test_every_path_in_the_plan_is_forward_slashed(self) -> None:
        # The table, the manifest and the journal all split on `/`, and
        # `str(Path.relative_to())` spells the separator the way the host does.
        # On Windows a backslash made every split find one field, which read as
        # an empty table rather than as a bug (CI, 2026-09-16).
        plan = self.plan()
        for m in plan["moves"]:
            self.assertNotIn("\\", m["src"], m)
            self.assertNotIn("\\", m["dst"], m)
        for t in plan["trackers"]:
            self.assertNotIn("\\", t["path"], t)
        self.assertEqual(mig._rel(self.alpha / "_harness" / "PLAN.md", self.vault),
                         "projects/alpha/_harness/PLAN.md")

    def test_nothing_is_unmapped_and_nothing_collides(self) -> None:
        plan = self.plan()
        self.assertEqual(plan["unmapped"], [])
        self.assertEqual(plan["collisions"], {})

    def test_every_harness_file_is_a_move(self) -> None:
        plan = self.plan()
        moved = {m["src"] for m in plan["moves"]}
        for project in ("alpha", "beta"):
            for rel in mig._harness_files(self.vault, project):
                self.assertIn(rel, moved, f"{rel} matched no row")

    def test_a_plan_and_its_progress_land_in_one_task(self) -> None:
        plan = self.plan()
        p = self.dest(plan, "_harness/PLAN-build-the-widget.md")
        g = self.dest(plan, "_harness/progress-build-the-widget.md")
        self.assertTrue(p.endswith("/plan.md"))
        self.assertEqual(Path(p).parent, Path(g).parent)

    def test_an_archived_plan_becomes_a_numbered_task_not_an_archive(self) -> None:
        # The design supersedes S4 here: a task is never archived.
        plan = self.plan()
        dst = self.dest(plan, "PLAN.archive.20260501-cut-the-rope.md")
        self.assertIn("/tasks/", dst)
        self.assertNotIn("/archive/", dst)
        self.assertTrue(mig._TASK_DIR.match(Path(dst).parent.name))

    def test_an_archived_progress_twin_joins_its_plan(self) -> None:
        plan = self.plan()
        self.assertEqual(
            Path(self.dest(plan, "progress.archive.20260501-cut-the-rope.md")).parent,
            Path(self.dest(plan, "PLAN.archive.20260501-cut-the-rope.md")).parent)

    def test_a_progress_log_with_no_plan_beside_it_goes_where_its_plan_went(self) -> None:
        # crickets carries one of these: a `progress-<slug>.md` left in the
        # harness root after its plan was archived. It belongs to that task.
        self.write(self.alpha / "_harness" / "progress-cut-the-rope.md", "# Orphan\n")
        plan = self.plan()
        self.assertEqual(
            Path(self.dest(plan, "_harness/progress-cut-the-rope.md")).parent,
            Path(self.dest(plan, "PLAN.archive.20260501-cut-the-rope.md")).parent)

    def test_a_queued_draft_becomes_a_task_and_the_directory_dissolves(self) -> None:
        plan = self.plan()
        dst = self.dest(plan, "queued-plans/PLAN-trim-the-edges.md")
        self.assertIn("/tasks/", dst)
        self.assertNotIn("queued-plans", dst)

    def test_the_brief_the_prompt_and_the_probe_keep_their_own_names(self) -> None:
        # Three papers for one task are three files; one `brief.md` would collide.
        plan = self.plan()
        # A destination is a vault-relative, forward-slashed string; `str(Path)`
        # would spell it the host's way and only match on a POSIX runner.
        task = self.dest(plan, "_harness/PLAN-build-the-widget.md").rsplit("/", 1)[0]
        self.assertEqual(self.dest(plan, "BRIEF-build-the-widget.md"), f"{task}/brief.md")
        self.assertEqual(self.dest(plan, "PROMPT-build-the-widget.md"), f"{task}/prompt.md")

    def test_a_brief_with_no_task_goes_to_the_desk(self) -> None:
        self.assertTrue(self.dest(self.plan(), "BRIEF-unattached.md")
                        .endswith("alpha/desk/briefs/BRIEF-unattached.md"))

    def test_the_rest_of_the_table(self) -> None:
        plan = self.plan()
        for suffix, expected in (
            ("designs/a-design/design-doc.md", "alpha/designs/a-design/design-doc.md"),
            ("research-old/finding.md", "alpha/research/old/finding.md"),
            ("_harness/research/deep.md", "alpha/research/deep.md"),
            ("_harness/FOLLOWUPS.md", "alpha/followups.md"),
            ("_harness/ROADMAP-MASTER.md", "alpha/roadmap.md"),
            ("_harness/ROADMAP.md", "alpha/completed/ROADMAP.md"),
            ("_harness/board-items.json", "alpha/desk/board-items.json"),
            ("_harness/features.json", "alpha/desk/features.json"),
            ("_harness/labels/one.md", "alpha/desk/labels/one.md"),
            ("_harness/week3-handoff.md", "alpha/completed/week3-handoff.md"),
            ("_harness/archive/notes.md", "alpha/completed/notes.md"),
            ("alpha/_index.md", "alpha/charter.md"),
            ("_archive/old-thing/_index.md", "projects/completed/old-thing/_index.md"),
            ("_archive/a-loose-note.md", "projects/completed/a-loose-note.md"),
        ):
            with self.subTest(suffix=suffix):
                self.assertTrue(self.dest(plan, suffix).endswith(expected),
                                f"{suffix} -> {self.dest(plan, suffix)}")

    def test_the_vault_copies_of_the_repo_files_go_to_the_desk(self) -> None:
        # The repo reads its own `.harness/`, which this migration never walks —
        # so the vault's twins are copies, not state, and `desk/` is where what
        # you do not open lives. `machinery_doctor.py` already reads
        # `project.json` from both homes for this move.
        plan = self.plan()
        self.assertTrue(self.dest(plan, "_harness/init.sh").endswith("alpha/desk/init.sh"))
        self.assertTrue(self.dest(plan, "_harness/.project-mode")
                        .endswith("alpha/desk/.project-mode"))
        self.assertEqual(plan["counts"]["machine"], 2)

    def test_the_repos_own_harness_is_never_walked(self) -> None:
        # The only tree the migration reads is `<vault>/projects/*/_harness/`.
        for m in self.plan()["moves"]:
            self.assertTrue(m["src"].startswith("projects/"), m["src"])


class TheGoldSet(Fixture):
    """The retrieval gate expects notes the move takes, so the eval gets a remap
    row per expectation and the fixture is never edited. A moved question is
    named with its cause before it is called drift."""

    def _gold(self, *tails: str, raw: "tuple | None" = None) -> Path:
        """A gold set naming each `tails` entry as the fixture spells it: under
        the frozen projects prefix the eval's own 2b remap folds away. That
        prefix comes from the module rather than being typed here, so the tests
        carry no retired Title Case literal of their own; `raw` names a path
        exactly, for an expectation that is not a project note."""
        entries = [mig._GOLD_2B_PREFIX[0] + t for t in tails] + list(raw or ())
        p = self.vault.parent / "gold.json"
        p.write_text(json.dumps({"entries": [
            {"id": "q1", "expected_note_paths": entries}]}), encoding="utf-8")
        return p

    def test_a_row_is_a_full_path_not_a_prefix(self) -> None:
        # A prefix row could rewrite a note it was never measured against.
        pairs, missing = mig.gold_remaps(
            self.plan(), self._gold("alpha/_harness/designs/a-design/design-doc.md"))
        self.assertEqual(missing, [])
        self.assertEqual(pairs, [("projects/alpha/_harness/designs/a-design/design-doc.md",
                                  "projects/alpha/designs/a-design/design-doc.md")])

    def test_a_plan_that_becomes_a_numbered_task_carries_its_number(self) -> None:
        # The number is only knowable from the recorded plan, which is why these
        # are generated rather than written by hand.
        pairs, _missing = mig.gold_remaps(self.plan(), self._gold("beta/_harness/PLAN.md"))
        self.assertEqual(len(pairs), 1)
        old, new = pairs[0]
        self.assertEqual(old, "projects/beta/_harness/PLAN.md")
        self.assertRegex(new, r"^projects/beta/tasks/\d{3}-.+/plan\.md$")

    def test_an_expectation_outside_a_harness_is_not_a_row(self) -> None:
        pairs, missing = mig.gold_remaps(
            self.plan(), self._gold(raw=("agent/memory/semantic/a-card.md",)))
        self.assertEqual((pairs, missing), ([], []))

    def test_an_expectation_the_table_cannot_place_is_reported_not_skipped(self) -> None:
        pairs, missing = mig.gold_remaps(self.plan(), self._gold("alpha/_harness/gone.md"))
        self.assertEqual(pairs, [])
        self.assertEqual(missing, ["projects/alpha/_harness/gone.md"])

    def test_the_rows_are_stable_across_two_runs(self) -> None:
        gold = self._gold("alpha/_harness/designs/a-design/design-doc.md",
                          "beta/_harness/progress.md")
        self.assertEqual(mig.gold_remaps(self.plan(), gold), mig.gold_remaps(self.plan(), gold))

    def test_a_gap_does_not_quietly_shorten_the_table(self) -> None:
        # A gap is the whole point of the mode: it says which expectation the
        # table cannot place, rather than emitting a quietly short list.
        pairs, missing = mig.gold_remaps(
            self.plan(), self._gold("alpha/_harness/gone.md", "alpha/_harness/FOLLOWUPS.md"))
        self.assertEqual(len(pairs), 1)
        self.assertEqual(len(missing), 1)


class SlugsAndNumbers(Fixture):
    def test_a_slug_comes_from_the_plan_name(self) -> None:
        self.assertEqual(mig.derive_slug("PLAN-build-the-widget.md")[0], "build-the-widget")

    def test_a_singleton_takes_the_slug_its_title_gives(self) -> None:
        plan = self.plan()
        dst = self.dest(plan, "beta/_harness/PLAN.md")
        self.assertTrue(Path(dst).parent.name.endswith("-beta"), dst)

    def test_an_archived_plan_with_no_slug_takes_its_title(self) -> None:
        dst = self.dest(self.plan(), "PLAN.archive.20260502.md")
        self.assertTrue(Path(dst).parent.name.endswith("-name-me-from-my-title"), dst)

    def test_a_slug_that_is_not_verb_first_is_marked_for_correction(self) -> None:
        plan = self.plan()
        alpha = next(p for p in plan["projects"] if p["project"] == "alpha")
        by_source = {s["source"]: s for s in alpha["slugs"]}
        self.assertFalse(by_source["PLAN-build-the-widget.md"]["needs_review"])
        self.assertTrue(by_source["archive/v3/PLAN.archive.20260502.md"]["needs_review"])

    def test_a_correction_replaces_the_derived_slug(self) -> None:
        plan = self.plan(slugs={"PLAN-build-the-widget.md": "assemble-the-widget"})
        self.assertIn("-assemble-the-widget/",
                      self.dest(plan, "_harness/PLAN-build-the-widget.md"))

    def test_numbers_are_three_digits_in_creation_order(self) -> None:
        plan = self.plan()
        alpha = next(p for p in plan["projects"] if p["project"] == "alpha")
        tasks = [s["task"] for s in alpha["slugs"]]
        self.assertEqual(sorted(tasks), tasks)
        for t in tasks:
            self.assertRegex(t, r"^\d{3}-")
        # The archived plans were made first, so they take the first numbers.
        self.assertTrue(tasks[0].endswith("-cut-the-rope"), tasks)

    def test_every_number_is_unique_within_a_project(self) -> None:
        for p in self.plan()["projects"]:
            numbers = [s["task"].split("-", 1)[0] for s in p["slugs"]]
            self.assertEqual(len(numbers), len(set(numbers)))


class Statuses(Fixture):
    def test_the_spellings_map_onto_the_five(self) -> None:
        for raw, expected in (
            ("done", "done"), ("DONE (2026-06-02) — all 3 tasks complete", "done"),
            ("done  *(Part 2 complete)", "done"), ("planning", "queued"),
            ("in-progress", "active"), ("active *(started 2026-08-21)", "active"),
            ("none — no plan currently queued.", "queued"),
            ("SUPERSEDED (2026-07-06) — never built", "dropped"),
            ("withdrawn", "dropped"), ("parked", "parked"), ("", ""),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(mig.map_status(raw), expected)

    def test_an_archived_plan_is_done_even_with_a_stale_status_line(self) -> None:
        self.write(self.alpha / "_harness" / "archive" / "v3" / "PLAN.archive.20260504-stale.md",
                   _plan_body("Stale", "in-progress"))
        trackers = {t["task"]: t for t in self.plan()["trackers"]}
        self.assertEqual(next(t["status"] for k, t in trackers.items() if k.endswith("-stale")),
                         "done")

    def test_a_withdrawn_archived_plan_stays_dropped(self) -> None:
        trackers = {t["task"]: t for t in self.plan()["trackers"]}
        self.assertEqual(next(t["status"] for k, t in trackers.items() if k.endswith("-fold-the-map")),
                         "dropped")

    def test_every_unit_gets_exactly_one_tracker(self) -> None:
        plan = self.plan()
        units = sum(p["units"] for p in plan["projects"])
        self.assertEqual(len(plan["trackers"]), units)
        self.assertEqual(len({t["path"] for t in plan["trackers"]}), units)


class TheOpenTrackerGate(Fixture):
    def test_the_gate_refuses_while_an_open_task_has_no_tracker(self) -> None:
        problems = mig.open_tracker_gate(self.vault, self.plan())
        self.assertTrue(any("build-the-widget" in p for p in problems))
        self.assertTrue(any("tracker.md" in p and "beta" in p for p in problems))

    def test_the_gate_names_the_file_to_write(self) -> None:
        problems = mig.open_tracker_gate(self.vault, self.plan())
        self.assertTrue(any(p.endswith("before the move")
                            and "_harness/tracker-build-the-widget.md" in p for p in problems))
        self.assertTrue(any("beta/_harness/tracker.md" in p for p in problems))

    def test_the_gate_refuses_an_empty_state(self) -> None:
        import tracker as tk
        self.open_trackers()
        path = self.alpha / "_harness" / "tracker-build-the-widget.md"
        t = tk.parse(path.read_text(encoding="utf-8"))
        t.state = ""
        path.write_text(tk.render(t), encoding="utf-8")
        self.assertTrue(any("has no State" in p
                            for p in mig.open_tracker_gate(self.vault, self.plan())))

    def test_the_gate_passes_once_both_are_written(self) -> None:
        self.open_trackers()
        self.assertEqual(mig.open_tracker_gate(self.vault, self.plan()), [])

    def test_a_closed_task_needs_no_hand_written_state(self) -> None:
        self.open_trackers()
        closed = [t for t in self.plan()["trackers"] if t["status"] == "done"]
        self.assertTrue(closed)
        self.assertFalse(any(t["needs_state"] for t in closed))

    def test_apply_refuses_while_the_gate_has_a_finding(self) -> None:
        with self.assertRaises(mig.Refused) as caught:
            self.apply(self.plan())
        self.assertIn("Nothing written", str(caught.exception))
        self.assertTrue((self.alpha / "_harness" / "PLAN-build-the-widget.md").is_file())


class Refusals(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.open_trackers()
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-q", "-m", "trackers")

    def test_a_tree_that_moved_since_the_dry_run_is_refused(self) -> None:
        plan = self.plan()
        self.write(self.alpha / "_harness" / "PLAN-a-latecomer.md", _plan_body("Latecomer", "planning"))
        with self.assertRaises(mig.Refused) as caught:
            self.apply(plan)
        self.assertIn("not what the dry run read", str(caught.exception))

    def test_a_wrong_count_is_refused(self) -> None:
        plan = self.plan()
        with self.assertRaises(mig.Refused) as caught:
            self.apply(plan, count=plan["totals"]["moves"] - 1)
        self.assertIn("you confirmed", str(caught.exception))

    def test_a_staged_index_is_refused(self) -> None:
        plan = self.plan()
        self.write(self.vault / "loose.md", "x\n")
        _git(self.vault, "add", "loose.md")
        with self.assertRaises(mig.Refused) as caught:
            self.apply(plan)
        self.assertIn("staged changes", str(caught.exception))

    def test_a_live_writer_is_refused(self) -> None:
        with self.assertRaises(mig.Refused) as caught:
            self.apply(self.plan(), writers=lambda: ["Obsidian is running"])
        self.assertIn("a writer is live", str(caught.exception))

    def test_a_refusal_writes_nothing(self) -> None:
        before = _git(self.vault, "status", "--porcelain")
        with self.assertRaises(mig.Refused):
            self.apply(self.plan(), writers=lambda: ["Obsidian is running"])
        self.assertEqual(_git(self.vault, "status", "--porcelain"), before)


class Applying(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.open_trackers()
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-q", "-m", "trackers")
        self.recorded = self.plan()
        self.journal = self.apply(self.recorded)

    def test_the_journal_matches_the_dry_run(self) -> None:
        self.assertTrue(self.journal["landed"])
        self.assertEqual(self.journal["counts"]["moved"], self.recorded["totals"]["moves"])

    def test_no_harness_directory_is_left(self) -> None:
        for project in ("alpha", "beta"):
            self.assertFalse((self.vault / "projects" / project / "_harness").exists())
        self.assertFalse((self.vault / "projects" / "_archive").exists())

    def test_every_task_has_a_plan_and_a_tracker(self) -> None:
        import tracker as tk
        for t in self.recorded["trackers"]:
            task = self.vault / Path(t["path"]).parent
            self.assertTrue((task / "plan.md").is_file(), t["path"])
            self.assertTrue((task / "tracker.md").is_file(), t["path"])
            parsed = tk.parse((task / "tracker.md").read_text(encoding="utf-8"))
            self.assertIn(parsed.status, tk.STATUSES)
            self.assertEqual(parsed.task, task.name)

    def test_a_tracker_written_before_the_move_is_moved_not_rewritten(self) -> None:
        # 7(c): the crickets release opens a tracker for any plan `/work` touches.
        # The migration carries it; it never writes a second one over it.
        import tracker as tk
        moved = [t for t in self.recorded["trackers"] if t["source"] == "moved"]
        self.assertEqual(len(moved), 2)
        for t in moved:
            parsed = tk.parse((self.vault / t["path"]).read_text(encoding="utf-8"))
            self.assertEqual(parsed.state, "Half of it is built.")
            self.assertNotIn(t["path"], [w["path"] for w in self.journal["trackers"]])

    def test_a_closed_tracker_carries_the_outcome_and_no_state(self) -> None:
        import tracker as tk
        done = next(t for t in self.recorded["trackers"] if t["status"] == "done")
        parsed = tk.parse((self.vault / done["path"]).read_text(encoding="utf-8"))
        self.assertEqual(parsed.status, "done")
        self.assertTrue(parsed.closed)
        self.assertEqual(parsed.state, "")
        self.assertTrue(parsed.outcome)

    def test_the_index_moved_with_the_tree(self) -> None:
        tracked = _git(self.vault, "ls-files").splitlines()
        self.assertFalse([p for p in tracked
                          if "_harness/PLAN" in p or "_harness/progress" in p])
        self.assertTrue([p for p in tracked if "/tasks/" in p and p.endswith("/plan.md")])

    def test_nothing_was_deleted(self) -> None:
        # Every source is accounted for: it is either at its destination or it is
        # one of the files that stay.
        for m in self.recorded["moves"]:
            self.assertTrue((self.vault / m["dst"]).is_file(), m["dst"])
            self.assertFalse((self.vault / m["src"]).exists(), m["src"])

    def test_a_link_to_a_moved_note_becomes_a_path_link_with_its_words_kept(self) -> None:
        text = self.note.read_text(encoding="utf-8")
        self.assertIn("|PLAN-build-the-widget]]", text)
        self.assertIn("/tasks/", text.split("|PLAN-build-the-widget]]")[0])
        self.assertIn("|the thing]]", text)          # an alias that was there is kept
        self.assertIn("#Why|BRIEF-unattached]]", text)  # an anchor survives
        self.assertNotIn("[[PLAN-build-the-widget]]", text)

    def test_every_journaled_digest_names_the_bytes_on_disk(self) -> None:
        # The invariant `--revert` rests on. Text mode translates line endings on
        # Windows, so a text-mode read-transform-write moved the bytes out from
        # under the digest and the revert refused to restore anything (CI,
        # 2026-09-16). Stated here directly, so it is checked on every runner.
        import hashlib
        for t in self.journal["trackers"]:
            data = (self.vault / t["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), t["sha"], t["path"])
        for e in self.journal["links"]:
            data = (self.vault / e["rel"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), e["after_sha"], e["rel"])
        for s in self.journal["stamped"]:
            data = (self.vault / s["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), s["after_sha"], s["path"])
        self.assertTrue(self.journal["trackers"] and self.journal["links"]
                        and self.journal["stamped"])

    def test_an_ambiguous_basename_is_left_alone_and_listed(self) -> None:
        self.assertIn("[[progress]]", self.note.read_text(encoding="utf-8"))
        self.assertIn("progress", self.journal["ambiguous_basenames"])


class BoundSessions(Fixture):
    """A session bound to a plan whose slug the migration changes follows it.

    The layout is the one `worktree_marker.py write` actually makes, not a
    convenient one: the pointer is `<main-root>/.harness/worktree-for-<slug>`
    and holds the worktree's path, and the marker is
    `<that path>/.harness/active-plan` — outside the main root, reachable only
    by following the pointer.

    Hermetic: the repo roots are injected, so the test never reads the real
    clones' `.harness/` — a marker rewrite against the live machine would be a
    leak, not a test."""

    def setUp(self) -> None:
        super().setUp()
        self.open_trackers()
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-q", "-m", "trackers")
        self.repo = self.vault.parent / "repo"
        (self.repo / ".harness").mkdir(parents=True)
        self.worktree = self.repo / ".claude" / "worktrees" / "build-the-widget"
        (self.worktree / ".harness").mkdir(parents=True)
        self.marker = self.worktree / ".harness" / "active-plan"
        self.marker.write_text("build-the-widget\n", encoding="utf-8")
        self.pointer = self.repo / ".harness" / "worktree-for-build-the-widget"
        self.pointer.write_text(str(self.worktree) + "\n", encoding="utf-8")

    def _apply(self, slugs=None):
        self.recorded = self.plan(slugs=slugs)
        return self.apply(self.recorded, repo_roots=lambda: [self.repo])

    def test_a_marker_follows_its_plan_into_the_numbered_task(self) -> None:
        journal = self._apply(slugs={"PLAN-build-the-widget.md": "assemble-the-widget"})
        self.assertEqual(self.marker.read_text(encoding="utf-8").strip(),
                         "004-assemble-the-widget")
        self.assertTrue(any(e["kind"] == "marker" for e in journal["markers"]))

    def test_the_root_pointer_is_renamed_with_it(self) -> None:
        self._apply(slugs={"PLAN-build-the-widget.md": "assemble-the-widget"})
        self.assertFalse(self.pointer.exists())
        self.assertTrue((self.repo / ".harness"
                         / "worktree-for-004-assemble-the-widget").is_file())

    def test_revert_puts_the_binding_back(self) -> None:
        self._apply(slugs={"PLAN-build-the-widget.md": "assemble-the-widget"})
        mig.revert(self.vault, "run-1", self.out)
        self.assertEqual(self.marker.read_text(encoding="utf-8").strip(), "build-the-widget")
        self.assertTrue(self.pointer.is_file())

    def test_a_marker_naming_a_plan_that_did_not_change_is_left_alone(self) -> None:
        # The number is part of the name, so every plan's name changes; a marker
        # for a plan this project does not have must still be untouched.
        self.marker.write_text("some-other-plan\n", encoding="utf-8")
        self._apply()
        self.assertEqual(self.marker.read_text(encoding="utf-8").strip(), "some-other-plan")

    def test_the_marker_is_found_through_the_pointer_not_by_globbing(self) -> None:
        # The marker sits outside the main root, so a run that only globbed
        # `<repo>/.harness/` would rename the pointer and leave the marker
        # naming a plan that no longer exists — worse than doing neither.
        self.assertFalse(self.marker.is_relative_to(self.repo / ".harness"))
        journal = self._apply(slugs={"PLAN-build-the-widget.md": "assemble-the-widget"})
        kinds = sorted(e["kind"] for e in journal["markers"])
        self.assertEqual(kinds, ["marker", "pointer"])

    def test_a_direct_mode_marker_in_the_main_root_is_rewritten_too(self) -> None:
        direct = self.repo / ".harness" / "active-plan"
        direct.write_text("build-the-widget\n", encoding="utf-8")
        self._apply(slugs={"PLAN-build-the-widget.md": "assemble-the-widget"})
        self.assertEqual(direct.read_text(encoding="utf-8").strip(), "004-assemble-the-widget")

    def test_a_pointer_whose_worktree_is_gone_does_not_stop_the_run(self) -> None:
        self.pointer.write_text("/no/such/worktree\n", encoding="utf-8")
        journal = self._apply(slugs={"PLAN-build-the-widget.md": "assemble-the-widget"})
        self.assertTrue(journal["landed"])
        self.assertEqual([e["kind"] for e in journal["markers"]], ["pointer"])


class Finishing(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.open_trackers()
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-q", "-m", "trackers")
        self.recorded = self.plan()
        self.apply(self.recorded)

    def test_every_post_condition_holds_and_the_marker_is_written(self) -> None:
        self.assertEqual(mig.finish(self.vault, self.recorded, self.out), [])
        self.assertTrue((self.vault / mig.MARKER_REL).is_file())

    def test_a_returned_harness_directory_fails_and_writes_no_marker(self) -> None:
        self.write(self.alpha / "_harness" / "PLAN-came-back.md", "x\n")
        problems = mig.finish(self.vault, self.recorded, self.out)
        self.assertTrue(any("_harness/ directory" in p for p in problems))
        self.assertFalse((self.vault / mig.MARKER_REL).is_file())

    def test_a_charter_that_did_not_land_fails(self) -> None:
        (self.alpha / "charter.md").unlink()
        self.assertTrue(any("charter.md" in p
                            for p in mig.finish(self.vault, self.recorded, self.out)))

    def test_a_task_with_no_prefix_fails(self) -> None:
        tasks = self.alpha / "tasks"
        first = sorted(p for p in tasks.iterdir() if p.is_dir())[0]
        first.rename(tasks / "unnumbered")
        self.assertTrue(any("three-digit prefix" in p
                            for p in mig.finish(self.vault, self.recorded, self.out)))

    def test_finish_refuses_without_a_journal(self) -> None:
        with self.assertRaises(mig.Refused):
            mig.finish(self.vault, {"run_id": "no-such-run"}, self.out)


class Reverting(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.open_trackers()
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-q", "-m", "trackers")
        self.before = {p: p.read_bytes() for p in self.vault.rglob("*")
                       if p.is_file() and ".git/" not in str(p)}
        self.recorded = self.plan()
        self.apply(self.recorded)

    def test_revert_puts_every_file_back(self) -> None:
        mig.revert(self.vault, "run-1", self.out)
        after = {p: p.read_bytes() for p in self.vault.rglob("*")
                 if p.is_file() and ".git/" not in str(p)}
        self.assertEqual(sorted(str(p) for p in after), sorted(str(p) for p in self.before))

    def test_revert_puts_the_links_back(self) -> None:
        mig.revert(self.vault, "run-1", self.out)
        self.assertEqual(self.note.read_bytes(), self.before[self.note])

    def test_revert_removes_the_marker(self) -> None:
        mig.finish(self.vault, self.recorded, self.out)
        self.assertTrue((self.vault / mig.MARKER_REL).is_file())
        mig.revert(self.vault, "run-1", self.out)
        self.assertFalse((self.vault / mig.MARKER_REL).is_file())

    def test_revert_refuses_when_a_moved_file_changed_since(self) -> None:
        # A written tracker that someone has since edited: putting it back would
        # lose that edit, so the run refuses and restores nothing.
        written = self.journal_trackers()[0]
        (self.vault / written["path"]).write_text("edited since\n", encoding="utf-8")
        with self.assertRaises(mig.Refused) as caught:
            mig.revert(self.vault, "run-1", self.out)
        self.assertIn("Nothing restored", str(caught.exception))

    def test_revert_refuses_when_a_destination_is_gone(self) -> None:
        dst = next(m["dst"] for m in self.recorded["moves"] if m["dst"].endswith("/plan.md"))
        (self.vault / dst).unlink()
        with self.assertRaises(mig.Refused):
            mig.revert(self.vault, "run-1", self.out)

    def journal_trackers(self) -> list:
        journal = json.loads((self.out / "journal-run-1.json").read_text(encoding="utf-8"))
        return journal["trackers"]


class TheCommandLine(Fixture):
    def _run(self, *args: str) -> int:
        return mig.main(["--vault", str(self.vault), "--state-dir", str(self.state), *args])

    def test_a_dry_run_records_a_plan_and_writes_nothing_else(self) -> None:
        before = _git(self.vault, "status", "--porcelain")
        self.assertEqual(self._run(), 0)
        self.assertEqual(_git(self.vault, "status", "--porcelain"), before)
        self.assertTrue(list(self.out.glob("plan-*.json")))

    def test_apply_and_finish_need_a_plan(self) -> None:
        self.assertEqual(self._run("--apply"), 2)
        self.assertEqual(self._run("--finish"), 2)

    def test_the_manifest_records_a_reverse_for_every_move(self) -> None:
        plan = self.plan()
        lines = mig.manifest(plan)
        self.assertEqual(sum(1 for line in lines if "Reverse:" in line), len(lines))
        self.assertGreaterEqual(len(lines), plan["totals"]["moves"])


if __name__ == "__main__":
    unittest.main()
