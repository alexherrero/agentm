#!/usr/bin/env python3
"""The corpus scorecard, and the one rule it exists to keep.

Every test here is ultimately about the same property: a number that nobody
measured must never appear as a number. That is the difference between a
dashboard and a dashboard nobody should trust, and it cannot be checked by
looking at a healthy run — only by running it against a system that has nothing
to say and reading what it says anyway.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "harness/skills/memory/scripts"))

import corpus_scorecard as sc  # noqa: E402


AT = datetime(2026, 8, 22, 9, 30, tzinfo=timezone.utc)


def answers(**by_command):
    """A fake daemon that answers the commands named and refuses the rest.

    Refuses rather than returns empty, because "the daemon said nothing" and
    "the daemon is not there" are different states and the scorecard reports them
    differently.
    """
    def fake(args):
        cmd = args[0]
        if cmd not in by_command:
            raise sc.DaemonUnavailable(f"no fake answer for {cmd}")
        value = by_command[cmd]
        if isinstance(value, Exception):
            raise value
        return value
    return fake


HEALTHY = {
    "status": {
        "spaces": {"memory": "Agent/memory", "projects": "Agent/desk/projects"},
        "index_detail": {"documents": 1200},
        "health": {"queue": {"unfiled": 40, "oldest_age": "2d1h"}},
    },
    "meters": {
        "sample": 100, "embedded": 100, "from": "2026-08-01T00:00:00Z",
        "to": "2026-08-20T00:00:00Z",
        "trigram_concentration": 0.25, "lexical_diversity": 0.8,
        "pairwise_similarity": {"median": 0.5, "n": 4950},
        "dispersion": {"median": 0.03, "n": 100},
    },
    "ledger": {"eligible": 100, "current": 60, "rules_hash": "abc123def456ghi"},
    "graph": {"nodes": 30, "edges": 44},
}


class ScorecardTests(unittest.TestCase):
    def build(self, fake, tmp: Path):
        with mock.patch.object(sc, "_agentmd", side_effect=fake):
            return sc.build(tmp, REPO, now=AT, rel=Path("desk/diagnostics"))

    def read(self, tmp: Path) -> str:
        return (tmp / "desk/diagnostics" / sc.STABLE_NAME).read_text(
            encoding="utf-8")

    # ── the rule ────────────────────────────────────────────────────────────

    def test_a_silent_daemon_produces_dashes_not_zeroes(self):
        """The bar. Nothing answers, and the report says so everywhere.

        A zero standing in for "not measured" is the failure that makes a
        dashboard worse than nothing: a diversity meter of 0.00 reads as a
        perfectly varied corpus and a coverage of 0 reads as a crisis, and both
        are the same absence.
        """
        with self.subTest("renders at all"):
            import tempfile

            with tempfile.TemporaryDirectory() as d:
                tmp = Path(d)
                self.build(answers(), tmp)
                body = self.read(tmp)

        self.assertIn("not measured:", body)

        # Every row the daemon would have supplied is a dash. Scoped to those
        # sections rather than banning zeroes outright: the retrieval rows come
        # from a file on disk and stay measured when the daemon is silent, and
        # one of them is a legitimate zero — no false positives is a result, not
        # an absence. Banning the character would have called that a bug.
        daemon_sections = ("The corpus", "Diversity", "Coverage", "The memory graph")
        section = ""
        for line in body.splitlines():
            if line.startswith("## "):
                section = line[3:].strip()
            if not line.startswith("| ") or line.startswith("|---") or line == "| | | |":
                continue
            if section not in daemon_sections:
                continue
            self.assertIn("| — |", line,
                          f"a silent daemon produced a number in {section!r}, "
                          f"which reads as a measurement: {line}")

    def test_every_absence_carries_a_reason(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self.build(answers(), tmp)
            body = self.read(tmp)

        for line in body.splitlines():
            # Absence is the value cell being a bare dash — `| — |` — not any
            # em-dash anywhere in the row; the notes column writes prose, and
            # prose in this repo uses em-dashes freely.
            if line.startswith("|") and "| — |" in line:
                self.assertIn("not measured:", line,
                              f"a dash with no reason: {line}")
                reason = line.split("not measured:", 1)[1].strip(" |")
                self.assertTrue(len(reason) > 8,
                                f"the reason is too short to act on: {line}")

    def test_a_measured_number_names_what_produced_it(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self.build(answers(**HEALTHY), tmp)
            body = self.read(tmp)

        # A row whose detail column comes from `source` alone, rather than one
        # whose note happens to mention the same command — the earlier version
        # of this test passed on the note and never checked the source field.
        row = next((l for l in body.splitlines()
                    if l.startswith("| documents indexed |")), "")
        self.assertIn("1200", row)
        self.assertIn("`agentmd status`", row,
                      f"the row does not name what produced it: {row}")
        self.assertIn("agentmd meters", body)

    def test_the_queue_row_reports_the_number_the_daemon_gives(self):
        """The key the daemon actually uses, not the one that reads well.

        The first version of this file looked for `count` and the daemon reports
        `unfiled`, so the row silently became a dash on a corpus with 9,447 notes
        waiting — an absence that looked exactly like an honest one. Nothing
        tested it, so nothing caught it until it ran.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self.build(answers(**HEALTHY), tmp)
            body = self.read(tmp)

        row = next((l for l in body.splitlines()
                    if l.startswith("| unfiled and waiting |")), "")
        self.assertTrue(row, "there is no unfiled row at all")
        self.assertIn("| 40 |", row,
                      f"the queue row did not carry the daemon's number: {row}")

    def test_the_completeness_row_is_named_even_with_no_run(self):
        """The headline number, when nobody has graded anything yet.

        Named rather than omitted: a scorecard silently missing its headline
        number reads as a scorecard whose headline number is fine. This used to
        assert the reason was "not built yet"; the pass is built now, so the
        reason changed and the rule it is here to protect did not.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self.build(answers(**HEALTHY), tmp)
            body = self.read(tmp)

        self.assertIn("claim-level coverage", body)
        self.assertIn("not measured:", body)
        self.assertNotIn("| claim-level coverage | 0", body,
                         "an ungraded corpus reported as zero coverage")

    def test_a_graded_run_is_reported_by_class(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            out = tmp / "desk/diagnostics"
            out.mkdir(parents=True)
            (out / sc.COMPLETENESS_RESULT_NAME).write_text(json.dumps({
                "summary": {"notes": 6, "scored": 5, "ungraded": 1,
                            "coverage": 0.9333, "max_spread": 0.3333,
                            "replicates": 3,
                            "by_class": {"workflow": {"n": 3, "coverage": 1.0},
                                         "fact": {"n": 2, "coverage": 0.8333}}},
            }), encoding="utf-8")
            self.build(answers(**HEALTHY), tmp)
            body = self.read(tmp)

        self.assertIn("| claim-level coverage | 0.9333 |", body)
        self.assertIn("coverage · workflow", body)
        self.assertIn("coverage · fact", body)
        self.assertIn("widest spread across replicates", body)
        self.assertIn("notes the judge could not grade", body,
                      "a note nobody could grade vanished from the report")

    def test_a_run_that_graded_nothing_is_not_zero_coverage(self):
        # The whole point of the file this lives in: an outage and a corpus that
        # lost its content must not render the same.
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            out = tmp / "desk/diagnostics"
            out.mkdir(parents=True)
            (out / sc.COMPLETENESS_RESULT_NAME).write_text(json.dumps({
                "summary": {"notes": 4, "scored": 0, "ungraded": 4,
                            "coverage": None, "by_class": {}},
            }), encoding="utf-8")
            self.build(answers(**HEALTHY), tmp)
            body = self.read(tmp)

        self.assertIn("not measured:", body)
        self.assertNotIn("| claim-level coverage | 0.0000 |", body)

    def test_a_pipe_in_a_reason_does_not_break_the_table(self):
        # A reason naming a shell pipeline ended its own cell and left the row a
        # column short, which reads as a rendering glitch rather than a lost
        # number.
        r = sc.Reading.unavailable("x", "run `a --json | b --json` first")
        row = r.render()
        # Count the delimiters, not the escapes: `\|` is a literal pipe inside a
        # cell and does not end it, so counting every `|` character would fail on
        # correct output.
        delimiters = len(re.findall(r"(?<!\\)\|", row))
        self.assertEqual(delimiters, 4,
                         f"the cell was split by its own text: {row}")
        self.assertIn(r"\|", row, "the pipe was dropped instead of escaped")

    def test_a_partial_daemon_reports_what_it_has(self):
        """One command down does not take the rest of the report with it."""
        import tempfile

        partial = dict(HEALTHY)
        partial["meters"] = sc.DaemonUnavailable("the embedder is not running")

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self.build(answers(**partial), tmp)
            body = self.read(tmp)

        self.assertIn("| documents indexed | 1200 |", body)
        self.assertIn("the embedder is not running", body)

    def test_the_dense_meters_relay_the_daemons_own_reason(self):
        """When the daemon explains an absence, the scorecard does not re-word it.

        Both rows are checked. Asserting the phrase appears *somewhere* passed
        when only one of the two had been broken, because the other still
        supplied it.
        """
        import tempfile

        blind = dict(HEALTHY)
        blind["meters"] = dict(HEALTHY["meters"])
        del blind["meters"]["pairwise_similarity"]
        del blind["meters"]["dispersion"]
        blind["meters"]["unavailable"] = [
            "pairwise similarity: no vectors to measure",
            "dispersion: no vectors to measure",
        ]

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self.build(answers(**blind), tmp)
            body = self.read(tmp)

        for label in ("pairwise similarity", "nearest-neighbour dispersion"):
            row = next((l for l in body.splitlines()
                        if l.startswith(f"| {label} |")), "")
            self.assertIn("no vectors to measure", row,
                          f"{label} did not relay the daemon's own reason: {row}")
        self.assertNotIn("| pairwise similarity | 0", body)

    # ── the files ───────────────────────────────────────────────────────────

    def test_it_writes_a_dated_file_and_a_stable_one(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            dated, stable = self.build(answers(**HEALTHY), tmp)

            self.assertTrue(dated.exists(), "no dated scorecard")
            self.assertTrue(stable.exists(), "no stable copy for a brief to link")
            self.assertEqual(dated.read_text(encoding="utf-8"),
                             stable.read_text(encoding="utf-8"))
            self.assertIn("2026-08-22", dated.name)
            self.assertEqual(stable.name, sc.STABLE_NAME)

    def test_the_stable_copy_is_a_file_rather_than_a_link(self):
        """The vault crosses machines and git; a symlink is a different thing on
        the other side of both."""
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _, stable = self.build(answers(**HEALTHY), tmp)
            self.assertFalse(stable.is_symlink(), "the stable copy is a symlink")

    def test_it_creates_the_directory_it_writes_into(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self.assertFalse((tmp / "desk").exists())
            self.build(answers(**HEALTHY), tmp)
            self.assertTrue((tmp / "desk/diagnostics").is_dir())

    def test_a_rerun_replaces_rather_than_appends(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self.build(answers(**HEALTHY), tmp)
            first = self.read(tmp)
            self.build(answers(**HEALTHY), tmp)
            self.assertEqual(first, self.read(tmp),
                             "a second run in the same day changed the report")

    # ── where it writes ─────────────────────────────────────────────────────

    def test_the_directory_follows_the_configured_desk(self):
        """The vault root and the memory root are different directories.

        Building the path from the wrong one wrote a whole new top-level
        directory beside `Agent/` on the first live run, which is the usual shape
        of this mistake: a plausible path nothing reads.
        """
        with mock.patch.object(sc, "_agentmd", side_effect=answers(**HEALTHY)):
            self.assertEqual(sc.diagnostics_dir(),
                             Path("Agent/desk/diagnostics"))

    def test_a_flat_layout_keeps_desk_at_the_top(self):
        flat = dict(HEALTHY)
        flat["status"] = dict(HEALTHY["status"])
        flat["status"]["spaces"] = {"memory": "memory", "projects": "desk/projects"}
        with mock.patch.object(sc, "_agentmd", side_effect=answers(**flat)):
            self.assertEqual(sc.diagnostics_dir(), Path("desk/diagnostics"))

    def test_an_unreachable_daemon_falls_back_rather_than_failing(self):
        with mock.patch.object(sc, "_agentmd", side_effect=answers()):
            self.assertEqual(sc.diagnostics_dir(), sc.DIAGNOSTICS_DIR)

    # ── the retrieval row ───────────────────────────────────────────────────

    def test_retrieval_reads_the_pinned_baseline(self):
        """One gold-set reader, not two. A second would drift from the first and
        nobody would know which to believe."""
        pinned = REPO / "scripts/health/fixtures/week1-gold/shipped-baseline.json"
        if not pinned.exists():
            self.skipTest("no pinned baseline in this checkout")
        data = json.loads(pinned.read_text(encoding="utf-8"))

        import tempfile

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self.build(answers(**HEALTHY), tmp)
            body = self.read(tmp)

        self.assertIn(f"gold-set R@{data.get('k', 5)}", body)
        self.assertIn(f"{data['r_at_k']:.4f}", body)


if __name__ == "__main__":
    unittest.main()


class DaemonSeamTests(unittest.TestCase):
    """`_agentmd` itself, which every other test here patches out.

    Its whole job is turning a failure into a sentence somebody can act on, and
    a battery run found that job had no test: the mutation that stopped it
    reading exit codes changed nothing, because nothing ever ran it.
    """

    def test_a_missing_binary_says_how_to_supply_one(self):
        with mock.patch.object(sc, "DAEMON_BIN", "a-binary-that-is-not-installed"):
            with self.assertRaises(sc.DaemonUnavailable) as caught:
                sc._agentmd(["status"])
        self.assertIn("not on PATH", str(caught.exception))
        self.assertIn("AGENTMD", str(caught.exception))

    def test_a_non_zero_exit_carries_the_reason(self):
        proc = mock.Mock(returncode=3, stdout="", stderr="the vault is not a directory")
        with mock.patch.object(sc.subprocess, "run", return_value=proc):
            with self.assertRaises(sc.DaemonUnavailable) as caught:
                sc._agentmd(["status"])
        said = str(caught.exception)
        self.assertIn("exited 3", said)
        self.assertIn("the vault is not a directory", said,
                      "the daemon's own words were dropped")

    def test_a_non_zero_exit_with_no_output_still_explains_itself(self):
        proc = mock.Mock(returncode=1, stdout="", stderr="")
        with mock.patch.object(sc.subprocess, "run", return_value=proc):
            with self.assertRaises(sc.DaemonUnavailable) as caught:
                sc._agentmd(["status"])
        self.assertIn("no reason given", str(caught.exception))

    def test_output_that_is_not_json_is_a_refusal(self):
        proc = mock.Mock(returncode=0, stdout="not json at all", stderr="")
        with mock.patch.object(sc.subprocess, "run", return_value=proc):
            with self.assertRaises(sc.DaemonUnavailable) as caught:
                sc._agentmd(["status"])
        self.assertIn("not JSON", str(caught.exception))

    def test_empty_output_is_not_an_error(self):
        """A command with nothing to say said nothing, which is an answer."""
        proc = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(sc.subprocess, "run", return_value=proc):
            self.assertIsNone(sc._agentmd(["status"]))


class DateTests(unittest.TestCase):
    """The filename carries the reader's date, not UTC's.

    The zone is passed in rather than forced onto the process. `time.tzset()`
    does not exist on Windows, so the first version of this test could not run
    on a third of the CI matrix — and a test that silently does not run is the
    same as no test.
    """

    def test_the_name_uses_the_readers_date(self):
        import tempfile
        from datetime import timedelta

        # UTC+14. 2026-08-22T20:00Z is already the 23rd there.
        far_east = timezone(timedelta(hours=14))
        evening = datetime(2026, 8, 22, 20, 0, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            with mock.patch.object(sc, "_agentmd", side_effect=answers(**HEALTHY)):
                dated, _ = sc.build(tmp, REPO, now=evening,
                                    rel=Path("desk/diagnostics"), tz=far_east)
        self.assertIn("2026-08-23", dated.name,
                      "the report is named for UTC's date rather than the "
                      "reader's; a run late in the evening would be filed under "
                      "the wrong day")

    def test_a_reader_behind_utc_gets_their_own_day_too(self):
        """The other direction, so the test cannot pass by always adding a day."""
        import tempfile
        from datetime import timedelta

        far_west = timezone(timedelta(hours=-11))
        early = datetime(2026, 8, 22, 3, 0, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            with mock.patch.object(sc, "_agentmd", side_effect=answers(**HEALTHY)):
                dated, _ = sc.build(tmp, REPO, now=early,
                                    rel=Path("desk/diagnostics"), tz=far_west)
        self.assertIn("2026-08-21", dated.name)


class RetrievalTests(unittest.TestCase):
    """The retrieval row when there is no pinned baseline to read.

    This branch never ran, because the checkout has one — so the code that says
    "no baseline" could have been anything at all.
    """

    def test_a_missing_baseline_is_a_dash_rather_than_a_zero(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            empty_repo = Path(d) / "repo"
            (empty_repo / "scripts/health/fixtures/week1-gold").mkdir(parents=True)
            section = sc.section_retrieval(empty_repo)

        rendered = section.render()
        self.assertIn("not measured:", rendered)
        self.assertIn("no pinned baseline", rendered)
        for forbidden in ("| 0 |", "| 0.0000 |"):
            self.assertNotIn(forbidden, rendered,
                             "a missing baseline produced a number")

    def test_r_at_1_renders_from_the_pinned_baseline(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "repo"
            gold = repo / "scripts/health/fixtures/week1-gold"
            gold.mkdir(parents=True)
            (gold / "shipped-baseline.json").write_text(json.dumps({
                "k": 5, "r_at_k": 0.7344, "hits": 47, "scored": 64,
                "r_at_1": 0.375, "hits_at_1": 24,
            }), encoding="utf-8")
            rendered = sc.section_retrieval(repo).render()

        self.assertIn("gold-set R@1", rendered)
        self.assertIn("0.3750", rendered)
        self.assertIn("24 first-slot hits", rendered)

    def test_a_baseline_without_r_at_1_omits_the_row_not_the_section(self):
        # An older pin predates the field; the row must vanish rather than
        # render a fabricated zero — the file's one rule.
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "repo"
            gold = repo / "scripts/health/fixtures/week1-gold"
            gold.mkdir(parents=True)
            (gold / "shipped-baseline.json").write_text(json.dumps({
                "k": 5, "r_at_k": 0.7344, "hits": 47, "scored": 64,
            }), encoding="utf-8")
            rendered = sc.section_retrieval(repo).render()

        self.assertIn("gold-set R@5", rendered)
        self.assertNotIn("gold-set R@1", rendered)

    def test_an_unreadable_baseline_is_a_dash_too(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "repo"
            gold = repo / "scripts/health/fixtures/week1-gold"
            gold.mkdir(parents=True)
            (gold / "shipped-baseline.json").write_text(
                "{ not json", encoding="utf-8")
            rendered = sc.section_retrieval(repo).render()

        self.assertIn("not measured:", rendered)
