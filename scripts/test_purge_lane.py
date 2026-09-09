#!/usr/bin/env python3
"""The operator-only purge lane (filing v2 part 6, task 2).

A purge starts with a manifest and deletes exactly what the manifest lists,
on a count the operator typed; it refuses a wrong count, a changed file and
a stale manifest; every deletion is journaled as the operator's; and nothing
under the dreaming layer or the runner can reach it.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import lifecycle_transitions as lt  # noqa: E402
import purge  # noqa: E402

TODAY = "2026-09-05"


class _Vault(unittest.TestCase):
    def setUp(self):
        self.top = Path(tempfile.mkdtemp(prefix="purge-lane-"))
        self.addCleanup(shutil.rmtree, self.top, ignore_errors=True)
        self.vault = self.top / "vault"
        (self.vault / "memory" / "semantic").mkdir(parents=True)
        self._env = os.environ.get("AGENTM_STATE_DIR")
        os.environ["AGENTM_STATE_DIR"] = str(self.top / "state")
        self.addCleanup(self._restore)

    def _restore(self):
        if self._env is None:
            os.environ.pop("AGENTM_STATE_DIR", None)
        else:
            os.environ["AGENTM_STATE_DIR"] = self._env

    def _note(self, name, *, lifecycle="archived", since=None, body="A body.\n"):
        rel = f"memory/semantic/{name}.md"
        p = self.vault / rel
        fm = f"---\ntitle: {name}\nkind: reference\nstatus: active\nlifecycle: {lifecycle}\n"
        if since:
            fm += f"lifecycle_since: {since}\n"
        p.write_text(fm + "---\n\n" + body, encoding="utf-8")
        return rel


class TheManifest(_Vault):
    def test_select_lists_only_the_state_asked_for_and_deletes_nothing(self):
        a = self._note("a", lifecycle="archived", since="2026-01-01")
        self._note("b", lifecycle="dormant")
        self._note("c", lifecycle="active")
        rows = purge.select(self.vault, lifecycle="archived", now=TODAY)
        self.assertEqual([r["rel"] for r in rows], [a])
        self.assertEqual(rows[0]["since"], "2026-01-01")
        self.assertEqual(len(rows[0]["sha256"]), 64)
        self.assertTrue((self.vault / a).exists())

    def test_older_than_reads_the_dated_entry_into_the_state(self):
        old = self._note("old", since="2025-01-01")
        self._note("new", since="2026-09-01")
        self._note("undated")
        rows = purge.select(self.vault, older_than_days=180, now=TODAY)
        self.assertEqual([r["rel"] for r in rows], [old])

    def test_the_manifest_names_the_links_a_purge_would_break(self):
        a = self._note("a")
        (self.vault / "memory/semantic/linker.md").write_text(
            "---\ntitle: L\nlifecycle: active\n---\nSee [[a]] and [[a|alias]] and [[elsewhere]].\n", encoding="utf-8")
        rows = purge.select(self.vault, now=TODAY)
        path = purge.write_manifest(self.vault, rows, criteria={"lifecycle": "archived"}, out_dir=self.top / "m", now="20260905T090000")
        m = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(m["count"], 1)
        self.assertEqual([r["rel"] for r in m["rows"]], [a])
        self.assertEqual(len(m["inbound_links"]), 2)
        self.assertTrue(all(l["from"] == "memory/semantic/linker.md" for l in m["inbound_links"]))


class TheApply(_Vault):
    def _manifest(self, **kw):
        rows = purge.select(self.vault, now=TODAY, **kw)
        return purge.write_manifest(self.vault, rows, criteria=kw, out_dir=self.top / "m", now="20260905T090000"), rows

    def test_deletes_exactly_the_manifest_and_journals_each_as_the_operators(self):
        a = self._note("a"); b = self._note("b")
        keep = self._note("keep", lifecycle="dormant")
        path, rows = self._manifest()
        n = purge.apply(self.vault, path, confirm_count=2, now=TODAY + "T10:00:00+00:00")
        self.assertEqual(n, 2)
        self.assertFalse((self.vault / a).exists())
        self.assertFalse((self.vault / b).exists())
        self.assertTrue((self.vault / keep).exists())
        j = lt.journal_entries()
        self.assertEqual({e["rel"] for e in j}, {a, b})
        self.assertTrue(all(e["to"] == "purged" and e["actor"] == "operator" and str(path) in e["reason"] for e in j))

    def test_a_wrong_count_refuses_and_deletes_nothing(self):
        a = self._note("a"); self._note("b")
        path, _ = self._manifest()
        with self.assertRaises(purge.RefusedPurge):
            purge.apply(self.vault, path, confirm_count=1)
        self.assertTrue((self.vault / a).exists())
        self.assertEqual(lt.journal_entries(), [])

    def test_a_file_that_changed_since_the_manifest_refuses_the_whole_purge(self):
        a = self._note("a"); b = self._note("b")
        path, _ = self._manifest()
        (self.vault / b).write_text((self.vault / b).read_text(encoding="utf-8") + "\nedited since\n", encoding="utf-8")
        with self.assertRaises(purge.RefusedPurge):
            purge.apply(self.vault, path, confirm_count=2)
        self.assertTrue((self.vault / a).exists(), "nothing deleted when one row is stale")

    def test_a_file_already_gone_refuses(self):
        a = self._note("a")
        path, _ = self._manifest()
        (self.vault / a).unlink()
        with self.assertRaises(purge.RefusedPurge):
            purge.apply(self.vault, path, confirm_count=1)

    def test_the_cli_refuses_without_a_count_and_exits_three_on_a_mismatch(self):
        self._note("a")
        path, _ = self._manifest()
        import subprocess
        r = subprocess.run([sys.executable, str(_SCRIPTS / "purge.py"), "--vault", str(self.vault), "apply",
                            "--manifest", str(path), "--confirm-count", "5"],
                           capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("refused", r.stderr)
        r2 = subprocess.run([sys.executable, str(_SCRIPTS / "purge.py"), "--vault", str(self.vault), "apply",
                             "--manifest", str(path)], capture_output=True, text=True)
        self.assertNotEqual(r2.returncode, 0, "the count is required")


class NobodyElseHoldsIt(unittest.TestCase):
    """Purge is operator-initiated only. No module of the memory skill, the
    dreaming layer, the runner or the hooks may import it or call its apply."""

    def test_no_automated_caller(self):
        roots = [_SCRIPTS, _HERE.parent / "harness" / "hooks", _HERE / "runner"]
        offenders = []
        for root in roots:
            if not root.exists():
                continue
            for p in root.rglob("*.py"):
                if p.name == "purge.py" or p.name.startswith("test_"):
                    continue
                text = p.read_text(encoding="utf-8", errors="replace")
                if re.search(r"^\s*(import purge\b|from purge\b)", text, re.M) or "purge.apply(" in text:
                    offenders.append(str(p.relative_to(_HERE.parent)))
        self.assertEqual(offenders, [])


class TheRuledPopulations(_Vault):
    """The six residue populations ruled by count on 2026-09-06 (agentm-vault 02).

    A population is re-selected on the day it runs, and its fresh count is the
    gate: equal to the ruling it may run, different and the run stops.
    """

    def _note(self, cls, name, body, **fm):
        d = self.vault / "memory" / cls
        d.mkdir(parents=True, exist_ok=True)
        head = "".join(f"{k}: {v}\n" for k, v in fm.items())
        (d / name).write_text(f"---\n{head}---\n\n{body}\n", encoding="utf-8")
        return f"memory/{cls}/{name}"

    def test_each_population_claims_its_own_shape(self):
        a = self._note("procedural", "tally.md", "The `Bash` tool was invoked 92 times during this session.",
                       title="Bash 92")
        # a blurb carries the ingest's rubric stamp; that is what tells it from
        # the research corpus, which arrives through the same fetch path
        d = self._note("semantic", "blurb.md", "A distributed file system.", title="3FS",
                       tags="[skill-discovery]", rubric_score="2", evaluator_classification="MEDIUM")
        c = self._note("procedural", "fix.md", "Fix observed: the cache was stale.", title="stale cache")
        e = self._note("crystallized", "supp.md", "User stated: never freeform.", kind="opinion-supplement")
        b = self._note("semantic", "frag.md", "User stated: always squash merges.", title="squash")
        f = self._note("semantic", "twin~dup.md", "a copy", title="twin")
        got = purge.classify_populations(self.vault)
        self.assertEqual(got, {a: "A", d: "D", c: "C", e: "E", b: "B", f: "F"})

    def test_a_path_belongs_to_exactly_one_population(self):
        # a crystallized supplement whose body also carries the `User stated:` prefix
        # is E, not B: the claim order decides, so the counts add and nothing is ruled twice.
        rel = self._note("crystallized", "both.md", "User stated: never freeform.", kind="opinion-supplement")
        claimed = purge.classify_populations(self.vault)
        self.assertEqual(claimed[rel], "E")
        self.assertEqual(len(purge.select_population(self.vault, "B", claimed=claimed)), 0)

    def test_a_population_only_claims_from_its_own_class_dirs(self):
        # the same `User stated:` fragment under procedural/ is nobody's: B is semantic-only
        rel = self._note("procedural", "frag.md", "User stated: always squash merges.", title="squash")
        self.assertNotIn(rel, purge.classify_populations(self.vault))

    def test_the_rows_carry_the_hash_apply_verifies(self):
        rel = self._note("semantic", "twin~dup.md", "a copy", title="twin")
        rows = purge.select_population(self.vault, "F")
        self.assertEqual([r["rel"] for r in rows], [rel])
        self.assertEqual(rows[0]["sha256"], purge._hash((self.vault / rel).read_text(encoding="utf-8")))

    def test_an_unknown_letter_is_refused(self):
        with self.assertRaises(purge.RefusedPurge):
            purge.select_population(self.vault, "Z")

    def test_the_cli_exits_four_when_the_count_moved_and_deletes_nothing(self):
        self._note("semantic", "twin~dup.md", "a copy", title="twin")
        out = self.top / "report"
        code = purge.main(["--vault", str(self.vault), "select", "--population", "F",
                           "--expect-count", "4", "--report-dir", str(out)])
        self.assertEqual(code, 4)
        manifest = json.loads((out / purge.MANIFEST_NAME).read_text(encoding="utf-8"))
        self.assertFalse(manifest["criteria"]["count_matches"])
        self.assertEqual(manifest["criteria"]["fresh_count"], 1)
        self.assertTrue((self.vault / "memory" / "semantic" / "twin~dup.md").exists())

    def test_the_cli_exits_zero_when_the_fresh_count_matches_the_ruling(self):
        self._note("semantic", "twin~dup.md", "a copy", title="twin")
        out = self.top / "report-ok"
        code = purge.main(["--vault", str(self.vault), "select", "--population", "F",
                           "--expect-count", "1", "--report-dir", str(out)])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads((out / purge.MANIFEST_NAME).read_text(encoding="utf-8"))["criteria"]["count_matches"])

    def test_a_selected_population_manifest_is_what_apply_consumes(self):
        rel = self._note("semantic", "twin~dup.md", "a copy", title="twin")
        rows = purge.select_population(self.vault, "F")
        m = purge.write_manifest(self.vault, rows, criteria={"population": "F"}, out_dir=self.top / "m")
        self.assertEqual(purge.apply(self.vault, m, confirm_count=1), 1)
        self.assertFalse((self.vault / rel).exists())

    def test_the_research_corpus_is_not_a_skill_discovery_blurb(self):
        """A regression, paid for on 2026-09-09.

        The ingest's blurbs and the authored research corpus both arrive through
        the fetch path and both carry `source: external-fetch`. Selecting D on
        the source swept nine research notes — the frozen gold set's whole
        `research-corpus` stratum — into the purge; they were restored from the
        baseline commit. Only the ingest writes a rubric score, so that is what
        D selects on now.
        """
        blurb = self._note("semantic", "3fs.md", "A distributed file system.\n\nSource: https://x",
                           title="3FS", tags="[skill-discovery, web]", source="external-fetch",
                           evaluator_classification="MEDIUM", rubric_score="2")
        research = self._note(
            "semantic", "read-all-versus-search.md",
            "Inference from comparing the Always-On agent to agentm. Handing an LLM every "
            "memory beats any retrieval step when the corpus fits the context window.",
            title="Read-all versus search is a corpus-size decision, not a quality one",
            tags="[design-judgment, memory-architecture, retrieval, scaling]",
            aliases='["is rag necessary at small scale"]', source="external-fetch")
        claimed = purge.classify_populations(self.vault)
        self.assertEqual(claimed.get(blurb), "D")
        self.assertNotIn(research, claimed,
                         "an authored research note was claimed for purge; only the ingest's "
                         "rubric-scored blurbs belong to D")

    def test_a_blurb_needs_the_ingest_stamp_and_its_tag(self):
        # the stamp alone is not enough: something else scoring a note does not
        # make it the ingest's.
        scored_elsewhere = self._note("semantic", "other.md", "a note", title="o",
                                      rubric_score="2", source="conversation")
        self.assertNotIn(scored_elsewhere, purge.classify_populations(self.vault))

    def test_the_ruled_counts_are_recorded_beside_each_population(self):
        self.assertEqual({k: v[1] for k, v in purge.POPULATIONS.items()},
                         {"A": 292, "B": 107, "C": 21, "D": 116, "E": 32, "F": 4})
        self.assertEqual(sorted(purge.CLAIM_ORDER), sorted(purge.POPULATIONS))


if __name__ == "__main__":
    unittest.main()
