#!/usr/bin/env python3
"""The correction loop — what it does, and more importantly what it refuses to.

Two of these are the plan's own verification criteria, verbatim: a collapsed
cluster is re-enriched from source on a scratch vault and the run reverts through
`revert_log` to byte-identical originals; a persistent trend produces a proposal
and never an auto-applied change.

The rest exist because this is the only part of the arc that rewrites memories,
and the failure that matters is not "it corrected the wrong thing" — it is "it
corrected something nobody could classify".
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "harness/skills/memory/scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import correction  # noqa: E402
import dream_confirm  # noqa: E402
from revert_log import RevertLog  # noqa: E402


def cluster(kind, members, *, provenance=None, max_sim=0.97, why="because"):
    return {
        "kind": kind,
        "members": list(members),
        "max_sim": max_sim,
        "min_sim": max_sim,
        "chained": False,
        "provenance": provenance or {m: [f"src-of-{m}"] for m in members},
        "why": why,
    }


class Vault:
    """A scratch vault, with a revert log that never touches the real cache."""

    def __init__(self, stack):
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        logs = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        locks = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.log = RevertLog(self.root, log_root=logs, lock_root=locks)

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def read(self, rel):
        return (self.root / rel).read_text(encoding="utf-8")

    def bytes_of(self, rel):
        return (self.root / rel).read_bytes()


class VaultCase(unittest.TestCase):
    def setUp(self):
        import contextlib
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.v = Vault(self.stack)


# ── the seam ───────────────────────────────────────────────────────────────

class SeamTests(unittest.TestCase):
    """Every other test here builds its clusters by hand.

    That is fine for the decisions, and useless for the contract: rename a Go
    struct tag and all fifty of them stay green while the shipped path reads
    `None` for the field that decides whether a cluster gets rewritten. So the
    field names are pinned against the Go source that emits them.
    """

    GO = (Path(__file__).resolve().parent.parent
          / "daemon/internal/meters/clusters.go")
    CMD = (Path(__file__).resolve().parent.parent
           / "daemon/cmd/agentmd/clusters.go")

    def tags(self, path):
        import re
        return set(re.findall(r'json:"([a-z_]+)', path.read_text(encoding="utf-8")))

    def test_every_cluster_field_python_reads_is_one_go_emits(self):
        emitted = self.tags(self.GO)
        # Read in `plan_action`, `_reason_for`, `build_merge_proposal`,
        # `redistill` and `digest_line`. Each one decides something.
        for field in ("kind", "members", "max_sim", "provenance", "why"):
            self.assertIn(field, emitted,
                          f"correction.py reads cluster[{field!r}] and no Go "
                          f"struct tag emits it; emitted = {sorted(emitted)}")

    def test_every_report_field_python_reads_is_one_go_emits(self):
        emitted = self.tags(self.CMD)
        for field in ("clusters", "unavailable", "scope", "sample",
                      "threshold", "from", "to"):
            self.assertIn(field, emitted,
                          f"correction.py reads report[{field!r}] and no Go "
                          f"struct tag emits it; emitted = {sorted(emitted)}")

    def test_the_kinds_python_branches_on_are_the_kinds_go_writes(self):
        # `plan_action` sends `duplicate` to the merge arm and `collapsed` to the
        # rewriting arm. A kind renamed on the Go side without this would fall
        # through to `review_only` — silently correct-looking, and the corpus
        # would simply stop being corrected.
        go = self.GO.read_text(encoding="utf-8")
        for kind in ("duplicate", "collapsed", "mixed", "unknown"):
            self.assertIn(f'ClusterKind = "{kind}"', go,
                          f"correction.py branches on {kind!r} and Go no longer "
                          f"emits it")

    def test_python_asks_the_daemon_for_the_threshold_rather_than_keeping_one(self):
        # One number, one place. A default repeated on this side is a second
        # place to change it and a silent disagreement the first time only one of
        # them moves.
        src = (Path(__file__).resolve().parent.parent
               / "harness/skills/memory/scripts/correction.py"
               ).read_text(encoding="utf-8")
        self.assertNotIn("0.95", src,
                         "correction.py has a copy of the cluster threshold; it "
                         "belongs to the daemon, which measured it")


# ── which arm a cluster goes to ────────────────────────────────────────────

class PlanningTests(unittest.TestCase):
    def test_a_duplicate_stages_a_merge(self):
        self.assertEqual(
            correction.plan_action(cluster("duplicate", ["a.md", "b.md"]),
                                   enrich_enabled=True),
            "merge_proposed")

    def test_a_collapsed_cluster_redistills(self):
        self.assertEqual(
            correction.plan_action(cluster("collapsed", ["a.md", "b.md"]),
                                   enrich_enabled=True),
            "redistilled")

    def test_a_collapsed_cluster_defers_when_enrichment_is_off(self):
        # The shipped configuration. It has to be a different answer from
        # "nothing to do", because re-distilling is exactly what is owed and
        # exactly what cannot be paid for.
        self.assertEqual(
            correction.plan_action(cluster("collapsed", ["a.md", "b.md"]),
                                   enrich_enabled=False),
            "deferred")

    def test_a_mixed_cluster_is_never_acted_on(self):
        for enabled in (True, False):
            self.assertEqual(
                correction.plan_action(cluster("mixed", ["a.md", "b.md"]),
                                       enrich_enabled=enabled),
                "review_only",
                f"enrich_enabled={enabled}")

    def test_an_unknown_cluster_is_never_acted_on(self):
        for enabled in (True, False):
            self.assertEqual(
                correction.plan_action(cluster("unknown", ["a.md", "b.md"]),
                                       enrich_enabled=enabled),
                "review_only",
                f"enrich_enabled={enabled}")

    def test_a_kind_nobody_wrote_a_rule_for_is_review_only(self):
        # The default direction. A fifth kind added to the daemon and forgotten
        # here must not fall through into an arm that writes.
        self.assertEqual(
            correction.plan_action(cluster("something-new", ["a.md", "b.md"]),
                                   enrich_enabled=True),
            "review_only")
        self.assertEqual(
            correction.plan_action({}, enrich_enabled=True), "review_only")


# ── arm one: a merge is described, never applied ───────────────────────────

class MergeProposalTests(VaultCase):
    def test_a_merge_proposal_changes_nothing_on_disk(self):
        before = {}
        for rel in ("m/a.md", "m/b.md"):
            self.v.write(rel, f"---\nstatus: active\n---\n\nbody of {rel}\n")
            before[rel] = self.v.bytes_of(rel)

        correction.build_merge_proposal(self.v.root,
                                        cluster("duplicate", ["m/a.md", "m/b.md"]))

        for rel, was in before.items():
            self.assertEqual(self.v.bytes_of(rel), was,
                             f"{rel} changed; a proposal describes a mutation "
                             f"and must not perform it")

    def test_the_merge_stage_is_not_auto_appliable(self):
        # `dream_confirm.AUTO_APPLY_STAGES`'s own docstring says dedup/promote
        # must never be added "without a fresh, separate operator ruling". This
        # module is not that ruling, so its proposals must carry a stage name
        # that is not in the set.
        self.assertNotIn(correction.MERGE_STAGE, dream_confirm.AUTO_APPLY_STAGES)

    def test_no_correction_stage_name_is_auto_appliable(self):
        # Stated over every stage name this module writes, not just the merge
        # one, so a later arm cannot become auto-applied by being named after a
        # stage that already is.
        for name in (correction.MERGE_STAGE, "correction_redistill", "correction"):
            self.assertNotIn(name, dream_confirm.AUTO_APPLY_STAGES, name)

    def test_the_proposal_keeps_one_note_and_supersedes_the_rest(self):
        for rel in ("m/a.md", "m/b.md", "m/c.md"):
            self.v.write(rel, f"---\nstatus: active\ntitle: {rel}\n---\n\nbody\n")
        p = correction.build_merge_proposal(
            self.v.root, cluster("duplicate", ["m/c.md", "m/a.md", "m/b.md"]))

        self.assertEqual(len(p["mutations"]), 2,
                         "three notes merge into one survivor and two superseded")
        for path, content in p["mutations"]:
            self.assertIn("status: superseded", content)
            self.assertIn("supersedes: m/a.md", content,
                          "the survivor is the first by path order")
            self.assertNotIn("m/a.md", Path(path).name)

    def test_superseding_preserves_the_body(self):
        # The design is explicit that a superseded memory is rank-penalized
        # rather than removed, and that its text stays in git. A merge that
        # rewrote the body would make that promise false.
        body = "the whole point of this note, which must survive\n"
        self.v.write("m/a.md", "---\nstatus: active\n---\n\nkeeper\n")
        self.v.write("m/b.md", f"---\nstatus: active\ntitle: b\n---\n\n{body}")
        p = correction.build_merge_proposal(self.v.root,
                                            cluster("duplicate", ["m/a.md", "m/b.md"]))
        _, content = p["mutations"][0]
        self.assertIn(body, content)
        self.assertIn("title: b", content, "unrelated frontmatter is kept")

    def test_an_existing_status_is_replaced_rather_than_duplicated(self):
        self.v.write("m/a.md", "---\nstatus: active\n---\n\nkeeper\n")
        self.v.write("m/b.md", "---\nstatus: active\n---\n\nbody\n")
        p = correction.build_merge_proposal(self.v.root,
                                            cluster("duplicate", ["m/a.md", "m/b.md"]))
        _, content = p["mutations"][0]
        head = content.split("---")[1]
        self.assertEqual(head.count("status:"), 1,
                         f"two status keys in one frontmatter block:\n{head}")
        self.assertNotIn("status: active", head)

    def test_a_merge_of_one_note_is_refused(self):
        self.v.write("m/a.md", "---\nstatus: active\n---\n\nbody\n")
        with self.assertRaises(correction.CorrectionError):
            correction.build_merge_proposal(self.v.root,
                                            cluster("duplicate", ["m/a.md"]))

    def test_an_unreadable_member_refuses_rather_than_describing_half_a_merge(self):
        self.v.write("m/a.md", "---\nstatus: active\n---\n\nbody\n")
        with self.assertRaises(correction.SourceUnavailable):
            correction.build_merge_proposal(
                self.v.root, cluster("duplicate", ["m/a.md", "m/gone.md"]))


# ── arm two: re-distilling, and the revert path ────────────────────────────

def loud_distiller(rel, raw, units):
    """A deterministic stand-in for the enrichment pass.

    Deterministic on purpose: a test that called a model would measure the model.
    What is being verified here is the write path and the undo path, and both are
    independent of what the distiller says.
    """
    return f"---\nstatus: active\n---\n\nre-distilled {rel} from {units[0]}\n"


class RedistillTests(VaultCase):
    def setUp(self):
        super().setUp()
        self.originals = {
            "m/one.md": "---\nstatus: active\ntitle: one\n---\n\nfirst body\n",
            # Deliberately awkward: CRLF, a trailing space, no final newline, and
            # a non-ASCII character. `record_and_apply` captures pre-images as
            # raw bytes for exactly this reason, and "byte-identical" is only a
            # real claim if the fixture can tell the difference.
            "m/two.md": "---\r\nstatus: active\r\n---\r\n\r\nsecond bódy \r\n"
                        "no trailing newline",
        }
        for rel, text in self.originals.items():
            (self.v.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.v.root / rel).write_bytes(text.encode("utf-8"))
        self.before = {rel: self.v.bytes_of(rel) for rel in self.originals}
        self.cluster = cluster("collapsed", list(self.originals),
                               provenance={"m/one.md": ["https://x.test/one"],
                                           "m/two.md": ["https://x.test/two"]})

    def test_a_collapsed_cluster_is_redistilled_and_reverts_byte_identically(self):
        # The plan's verification criterion, verbatim.
        entry = correction.redistill(self.v.root, self.v.log, "run-1",
                                     self.cluster, loud_distiller, version="v2")
        for rel in self.originals:
            self.assertIn("re-distilled", self.v.read(rel))
            self.assertNotEqual(self.v.bytes_of(rel), self.before[rel])

        self.v.log.revert("run-1", entry)

        for rel, was in self.before.items():
            self.assertEqual(self.v.bytes_of(rel), was,
                             f"{rel} did not come back byte for byte")

    def test_reverting_the_whole_run_also_restores_everything(self):
        # The entry-less form, which is what a person reaching for the undo after
        # a bad night actually types.
        correction.redistill(self.v.root, self.v.log, "run-2", self.cluster,
                             loud_distiller, version="v2")
        self.v.log.revert("run-2")
        for rel, was in self.before.items():
            self.assertEqual(self.v.bytes_of(rel), was, rel)

    def test_the_new_body_carries_the_new_pass_version(self):
        # Without the stamp the ledger still reads the note at the old version,
        # so the next cycle finds it stale and re-distills it again — a model
        # call per note per night, forever.
        correction.redistill(self.v.root, self.v.log, "run-3", self.cluster,
                             loud_distiller, version="pass-v7")
        for rel in self.originals:
            self.assertIn("enriched_by: pass-v7", self.v.read(rel), rel)

    def test_an_old_version_stamp_is_replaced_rather_than_accumulated(self):
        def stamped(rel, raw, units):
            return "---\nstatus: active\nenriched_by: pass-v1\n---\n\nnew\n"

        correction.redistill(self.v.root, self.v.log, "run-4", self.cluster,
                             stamped, version="pass-v2")
        head = self.v.read("m/one.md").split("---")[1]
        self.assertEqual(head.count("enriched_by:"), 1, head)
        self.assertIn("enriched_by: pass-v2", head)
        self.assertNotIn("pass-v1", head)

    def test_a_member_with_no_provenance_refuses_and_writes_nothing(self):
        bad = cluster("collapsed", list(self.originals),
                      provenance={"m/one.md": ["https://x.test/one"]})
        with self.assertRaises(correction.SourceUnavailable):
            correction.redistill(self.v.root, self.v.log, "run-5", bad,
                                 loud_distiller, version="v2")
        for rel, was in self.before.items():
            self.assertEqual(self.v.bytes_of(rel), was,
                             f"{rel} was rewritten before the refusal — a "
                             f"cluster is corrected whole or not at all")

    def test_an_empty_distillation_refuses_and_writes_nothing(self):
        # An empty result is not a shorter memory, it is a lost one. And the
        # refusal has to come before any write, or the first note in the cluster
        # is already gone.
        for empty in (lambda r, w, u: "", lambda r, w, u: "   \n\n"):
            with self.subTest(empty=repr(empty(None, None, None))):
                with self.assertRaises(correction.SourceUnavailable):
                    correction.redistill(self.v.root, self.v.log, "run-6",
                                         self.cluster, empty, version="v2")
                for rel, was in self.before.items():
                    self.assertEqual(self.v.bytes_of(rel), was, rel)

    def test_a_failure_on_the_second_note_leaves_the_first_untouched(self):
        # One `record_and_apply` for the whole cluster, not one per note. A
        # half-corrected cluster is worse than an uncorrected one: the notes that
        # moved now sit at a different pass version than the notes that did not,
        # and the ledger reads that as work finished.
        def fails_on_two(rel, raw, units):
            if rel == "m/two.md":
                raise correction.SourceUnavailable("upstream is gone")
            return loud_distiller(rel, raw, units)

        with self.assertRaises(correction.SourceUnavailable):
            correction.redistill(self.v.root, self.v.log, "run-7", self.cluster,
                                 fails_on_two, version="v2")
        self.assertEqual(self.v.bytes_of("m/one.md"), self.before["m/one.md"])

    def test_an_empty_cluster_is_refused(self):
        with self.assertRaises(correction.CorrectionError):
            correction.redistill(self.v.root, self.v.log, "run-8",
                                 cluster("collapsed", []), loud_distiller,
                                 version="v2")

    def test_the_distiller_is_given_the_notes_provenance(self):
        # It re-distills *from source*. A distiller handed only the current body
        # would be rewriting the drifted note, which is the thing being corrected.
        seen = {}

        def recording(rel, raw, units):
            seen[rel] = (raw, list(units))
            return loud_distiller(rel, raw, units)

        correction.redistill(self.v.root, self.v.log, "run-9", self.cluster,
                             recording, version="v2")
        self.assertEqual(seen["m/one.md"][1], ["https://x.test/one"])
        self.assertEqual(seen["m/two.md"][1], ["https://x.test/two"])
        self.assertIn("first body", seen["m/one.md"][0])


# ── arm three: a trend asks for a person ───────────────────────────────────

class TrendTests(VaultCase):
    def test_three_consecutive_bad_movements_are_persistent(self):
        self.assertTrue(correction.is_persistent([0.1, 0.2, 0.3, 0.4],
                                                 up_is_bad=True))

    def test_a_meter_falling_when_falling_is_bad_is_persistent(self):
        self.assertTrue(correction.is_persistent([0.9, 0.8, 0.7, 0.6],
                                                 up_is_bad=False))

    def test_direction_is_per_meter(self):
        # A growing queue and growing coverage are both "up" and only one is bad.
        rising = [0.1, 0.2, 0.3, 0.4]
        self.assertTrue(correction.is_persistent(rising, up_is_bad=True))
        self.assertFalse(correction.is_persistent(rising, up_is_bad=False))

    def test_two_movements_are_not_a_trend(self):
        self.assertFalse(correction.is_persistent([0.1, 0.2, 0.3], up_is_bad=True))

    def test_a_wobble_is_not_a_trend(self):
        self.assertFalse(correction.is_persistent([0.1, 0.2, 0.15, 0.4],
                                                  up_is_bad=True))

    def test_a_flat_reading_breaks_the_run(self):
        # Equal is not movement. A meter that has not moved is not evidence about
        # the direction it last moved in.
        self.assertFalse(correction.is_persistent([0.1, 0.2, 0.2, 0.3],
                                                  up_is_bad=True))

    def test_a_high_but_steady_meter_is_not_a_trend(self):
        # Level is a corpus that was always like that; movement is a corpus
        # becoming like that, and only the second says anything about the pass.
        self.assertFalse(correction.is_persistent([0.97, 0.97, 0.97, 0.97],
                                                  up_is_bad=True))

    def test_a_cold_history_says_no_rather_than_guessing(self):
        for readings in ([], [0.5], [0.4, 0.5], [0.3, 0.4, 0.5]):
            self.assertFalse(correction.is_persistent(readings, up_is_bad=True),
                             f"{readings} is not enough history for "
                             f"{correction.TREND_CYCLES} movements")

    def test_only_the_most_recent_cycles_count(self):
        # A long history with an old bad run and a calm present is calm.
        self.assertFalse(
            correction.is_persistent([0.1, 0.2, 0.3, 0.4, 0.2, 0.2, 0.2],
                                     up_is_bad=True))

    def test_a_calm_start_does_not_excuse_a_bad_finish(self):
        # The complement of the test above, and the one that actually separates
        # "the last three movements" from "every movement on record". A long
        # history that settled and then started climbing is climbing now, and
        # reading the whole history would let one old good night vouch for it
        # indefinitely.
        self.assertTrue(
            correction.is_persistent([0.9, 0.05, 0.1, 0.2, 0.3, 0.4],
                                     up_is_bad=True))

    def test_a_proposal_is_written_and_no_memory_is_touched(self):
        # The plan's second verification criterion.
        self.v.write("m/a.md", "---\nstatus: active\n---\n\nuntouched\n")
        before = self.v.bytes_of("m/a.md")

        out = correction.propose_prompt_change(
            self.v.root, "run-1",
            [{"meter": "pairwise_similarity", "readings": [0.4, 0.5, 0.6, 0.7],
              "up_is_bad": True}],
            now=1_756_000_000.0)

        self.assertTrue(out.exists())
        self.assertEqual(self.v.bytes_of("m/a.md"), before,
                         "the trend arm wrote to a memory")

    def test_the_proposal_names_the_meter_and_its_readings(self):
        out = correction.propose_prompt_change(
            self.v.root, "run-2",
            [{"meter": "dispersion", "readings": [0.30, 0.22, 0.15, 0.09],
              "up_is_bad": False}],
            now=1_756_000_000.0)
        text = out.read_text(encoding="utf-8")
        self.assertIn("dispersion", text)
        self.assertIn("0.0900", text, "the actual readings, not a summary")
        self.assertIn("falling", text, "which direction is the bad one")

    def test_the_proposal_says_nothing_was_changed(self):
        out = correction.propose_prompt_change(
            self.v.root, "run-3",
            [{"meter": "x", "readings": [1, 2, 3, 4], "up_is_bad": True}],
            now=1_756_000_000.0)
        self.assertIn("Nothing has been changed",
                      out.read_text(encoding="utf-8"))

    def test_the_proposal_is_marked_proposed_rather_than_active(self):
        out = correction.propose_prompt_change(
            self.v.root, "run-4",
            [{"meter": "x", "readings": [1, 2, 3, 4], "up_is_bad": True}],
            now=1_756_000_000.0)
        self.assertIn("status: proposed", out.read_text(encoding="utf-8"))


# ── the stage ──────────────────────────────────────────────────────────────

class StageTests(VaultCase):
    def test_every_note_is_a_digest_line(self):
        # Every other stage in `dream_stages` appends a readable string. A stage
        # whose notes are a different type is a trap set for whoever writes the
        # renderer that finally reads them.
        self.v.write("m/a.md", "---\nstatus: active\n---\n\nbody\n")
        self.v.write("m/b.md", "---\nstatus: active\n---\n\nbody\n")
        self._with_clusters({"clusters": [
            cluster("duplicate", ["m/a.md", "m/b.md"],
                    provenance={"m/a.md": ["s"], "m/b.md": ["s"]}),
            cluster("mixed", ["m/a.md", "m/b.md"]),
        ], "unavailable": ["something could not be measured"]})

        res = correction.stage_correction(self.v.root, enrich_enabled=True,
                                          trends=[{"meter": "m",
                                                   "readings": [1, 2, 3, 4],
                                                   "up_is_bad": True}])
        self.assertTrue(res.notes)
        for n in res.notes:
            self.assertIsInstance(n, str, f"{n!r} is not a digest line")

    def test_a_redistill_puts_its_revert_handle_in_the_digest(self):
        # Without it, "two notes were re-distilled" is a sentence a person cannot
        # act on — the undo needs the entry id.
        for rel in ("m/a.md", "m/b.md"):
            self.v.write(rel, f"---\nstatus: active\n---\n\n{rel}\n")
        self._with_clusters({"clusters": [cluster("collapsed", ["m/a.md", "m/b.md"])]})
        res = correction.stage_correction(
            self.v.root, revert_log=self.v.log, run_id="r",
            distiller=loud_distiller, version="v2", enrich_enabled=True)
        line = next(n for n in res.notes if n.startswith("redistilled:"))
        self.assertIn("revert with entry", line)

    def test_a_long_cluster_does_not_print_every_member(self):
        # A digest line naming forty notes is a digest nobody reads.
        members = [f"m/n{i}.md" for i in range(8)]
        for rel in members:
            self.v.write(rel, f"---\nstatus: active\n---\n\n{rel}\n")
        self._with_clusters({"clusters": [
            cluster("duplicate", members,
                    provenance={m: ["one-source"] for m in members})]})
        res = correction.stage_correction(self.v.root, enrich_enabled=True)
        line = next(n for n in res.notes if n.startswith("merge_proposed:"))
        self.assertIn("and 5 more", line)
        self.assertNotIn("m/n7.md", line)


    def _with_clusters(self, report):
        original = correction.work_ledger.clusters
        correction.work_ledger.clusters = lambda *a, **k: report
        self.addCleanup(lambda: setattr(correction.work_ledger, "clusters", original))

    def test_an_unreachable_daemon_is_reported_rather_than_read_as_clean(self):
        def raises(*a, **k):
            raise correction.work_ledger.LedgerUnavailable("no daemon")

        original = correction.work_ledger.clusters
        correction.work_ledger.clusters = raises
        self.addCleanup(lambda: setattr(correction.work_ledger, "clusters", original))

        res = correction.stage_correction(self.v.root)
        self.assertTrue(res.unavailable)
        self.assertEqual(res.considered, 0)

    def test_a_clean_corpus_reports_a_cycle_that_found_nothing(self):
        self._with_clusters({"clusters": [], "counts": {}})
        res = correction.stage_correction(self.v.root)
        self.assertFalse(res.unavailable, "a clean corpus is not an outage")
        self.assertEqual(res.considered, 0)

    def test_the_daemons_own_unavailability_is_carried_into_the_digest(self):
        # `agentmd clusters` reports "no vectors" as unavailability rather than
        # as zero clusters. Dropping that on the floor here would turn a broken
        # dense arm back into "the corpus is clean".
        self._with_clusters({"clusters": [],
                             "unavailable": ["no vectors to measure"]})
        res = correction.stage_correction(self.v.root)
        self.assertIn("no vectors to measure", res.notes)

    def test_a_duplicate_cluster_stages_and_changes_nothing(self):
        self.v.write("m/a.md", "---\nstatus: active\n---\n\nbody\n")
        self.v.write("m/b.md", "---\nstatus: active\n---\n\nbody\n")
        before = {r: self.v.bytes_of(r) for r in ("m/a.md", "m/b.md")}
        self._with_clusters({"clusters": [
            cluster("duplicate", ["m/a.md", "m/b.md"],
                    provenance={"m/a.md": ["s"], "m/b.md": ["s"]})]})

        res = correction.stage_correction(self.v.root, enrich_enabled=True)

        self.assertEqual(res.enqueued, 1)
        self.assertEqual(res.written, 0)
        for rel, was in before.items():
            self.assertEqual(self.v.bytes_of(rel), was, rel)

    def test_a_collapsed_cluster_defers_when_enrichment_is_off(self):
        self.v.write("m/a.md", "---\nstatus: active\n---\n\nbody\n")
        self.v.write("m/b.md", "---\nstatus: active\n---\n\nbody\n")
        before = self.v.bytes_of("m/a.md")
        self._with_clusters({"clusters": [cluster("collapsed", ["m/a.md", "m/b.md"])]})

        res = correction.stage_correction(self.v.root, enrich_enabled=False)

        self.assertEqual(res.written, 0)
        self.assertEqual(res.skipped, 1)
        self.assertEqual(self.v.bytes_of("m/a.md"), before)
        self.assertTrue(any(n.startswith("deferred:") for n in res.notes),
                        f"notes = {res.notes}")

    def test_a_collapsed_cluster_redistills_when_everything_is_supplied(self):
        for rel in ("m/a.md", "m/b.md"):
            self.v.write(rel, f"---\nstatus: active\n---\n\n{rel}\n")
        self._with_clusters({"clusters": [cluster("collapsed", ["m/a.md", "m/b.md"])]})

        res = correction.stage_correction(
            self.v.root, revert_log=self.v.log, run_id="r", distiller=loud_distiller,
            version="v2", enrich_enabled=True)

        self.assertEqual(res.written, 2)
        self.assertIn("re-distilled", self.v.read("m/a.md"))

    def test_enrichment_on_without_a_revert_log_defers_rather_than_writing(self):
        # The dangerous half-configuration: the pass is on, so the arm is
        # licensed, but there is nothing to undo it with. Writing here would be
        # the one mutation in this module with no way back.
        for rel in ("m/a.md", "m/b.md"):
            self.v.write(rel, f"---\nstatus: active\n---\n\n{rel}\n")
        before = self.v.bytes_of("m/a.md")
        self._with_clusters({"clusters": [cluster("collapsed", ["m/a.md", "m/b.md"])]})

        res = correction.stage_correction(self.v.root, enrich_enabled=True,
                                          distiller=loud_distiller, version="v2")

        self.assertEqual(res.written, 0)
        self.assertEqual(self.v.bytes_of("m/a.md"), before)

    def test_a_mixed_cluster_is_counted_and_left_alone(self):
        self.v.write("m/a.md", "---\nstatus: active\n---\n\nbody\n")
        self.v.write("m/b.md", "---\nstatus: active\n---\n\nbody\n")
        before = self.v.bytes_of("m/a.md")
        self._with_clusters({"clusters": [cluster("mixed", ["m/a.md", "m/b.md"])]})

        res = correction.stage_correction(
            self.v.root, revert_log=self.v.log, run_id="r",
            distiller=loud_distiller, version="v2", enrich_enabled=True)

        self.assertEqual(res.considered, 1)
        self.assertEqual(res.written, 0)
        self.assertEqual(res.enqueued, 0)
        self.assertEqual(self.v.bytes_of("m/a.md"), before)

    def test_one_failing_cluster_does_not_stop_the_others(self):
        self.v.write("m/a.md", "---\nstatus: active\n---\n\nbody\n")
        self.v.write("m/b.md", "---\nstatus: active\n---\n\nbody\n")
        self._with_clusters({"clusters": [
            cluster("duplicate", ["m/gone-1.md", "m/gone-2.md"]),
            cluster("duplicate", ["m/a.md", "m/b.md"],
                    provenance={"m/a.md": ["s"], "m/b.md": ["s"]}),
        ]})
        res = correction.stage_correction(self.v.root, enrich_enabled=True)
        self.assertEqual(res.considered, 2)
        self.assertEqual(res.enqueued, 1, "the readable cluster still staged")
        self.assertEqual(res.skipped, 1)

    def test_a_persistent_trend_is_reported_and_a_wobble_is_not(self):
        self._with_clusters({"clusters": []})
        # The meters are named for nothing in particular on purpose. An earlier
        # version called one of them `rising`, which is also the word for the
        # direction — so the direction assertion passed on the meter's name and
        # would have passed with the direction dropped altogether.
        res = correction.stage_correction(self.v.root, trends=[
            {"meter": "pairwise_similarity", "readings": [0.1, 0.2, 0.3, 0.4],
             "up_is_bad": True},
            {"meter": "dispersion", "readings": [0.9, 0.8, 0.7, 0.6],
             "up_is_bad": False},
            {"meter": "trigram_concentration", "readings": [0.1, 0.2, 0.15, 0.4],
             "up_is_bad": True},
        ])
        flagged = [n for n in res.notes if n.startswith("trend:")]
        self.assertEqual(len(flagged), 2, f"notes = {res.notes}")

        climbing = next(n for n in flagged if "pairwise_similarity" in n)
        self.assertIn("rising", climbing)
        falling = next(n for n in flagged if "dispersion" in n)
        self.assertIn("falling", falling,
                      "direction is per meter, and this one is bad falling")
        self.assertFalse(any("trigram_concentration" in n for n in flagged),
                         "a wobble is not a trend")


if __name__ == "__main__":
    unittest.main()
