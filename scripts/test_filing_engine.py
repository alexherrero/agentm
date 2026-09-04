#!/usr/bin/env python3
"""filing_engine.py — the write-time filing decision, on a synthetic corpus.

Every expectation is hand-written from the fixture. Pins: the four operations
(add / update / supersede / noop) and what decides each; the structural key
leads and similarity only flags; the planted contradiction supersedes without
deleting; the planted near-duplicate files flagged, never merged; compatible
facts co-store; a candidate with no type takes the default at low confidence;
a namesake takes `~dup`; and `apply` writes the stamps through the canonical
writer into the class directory.

Needs the daemon binary (the filing contract's only parser): $AGENTMD, or
`agentmd` on PATH, or a Go toolchain — otherwise skipped.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
for p in (_HERE, _REPO / "harness" / "skills" / "memory" / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import storage_rules  # noqa: E402

_BUILD_DIR = None


def setUpModule():
    global _BUILD_DIR
    if os.environ.get("AGENTMD", "").strip():
        storage_rules.DAEMON_BIN = os.environ["AGENTMD"].strip()
        return
    found = shutil.which("agentmd")
    if found:
        os.environ["AGENTMD"] = found
        storage_rules.DAEMON_BIN = found
        return
    if shutil.which("go") is None:
        raise unittest.SkipTest("no agentmd and no go toolchain; set $AGENTMD to a built binary")
    _BUILD_DIR = tempfile.TemporaryDirectory(prefix="agentmd-build-")
    binary = Path(_BUILD_DIR.name) / "agentmd"
    subprocess.run(["go", "build", "-o", str(binary), "./cmd/agentmd"], cwd=str(_REPO / "daemon"), check=True)
    os.environ["AGENTMD"] = str(binary)
    storage_rules.DAEMON_BIN = str(binary)


def tearDownModule():
    if _BUILD_DIR is not None:
        _BUILD_DIR.cleanup()


import filing_engine as fe  # noqa: E402

RULES = _REPO / "daemon" / "internal" / "rules" / "storage-rules.default.md"


def _rules():
    return storage_rules.load_file(RULES)


def _note(path: Path, fm: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{fm}---\n\n{body}\n", encoding="utf-8")


def _vault(td: Path) -> Path:
    root = td / "Vault"
    (root / ".obsidian").mkdir(parents=True)
    v = root / "Agent"
    m = v / "memory"
    for cls in fe.CLASS_DIRS:
        _note(m / cls / "_index.md", f"kind: dir-index\nstatus: active\nslug: {cls}-index\n", f"# {cls}")
    # a filed tool stub, a filed directive, a filed fact, a filed lane supplement
    _note(m / "procedural" / "workflow-bash.md", "type: workflow\nstatus: active\nslug: workflow-bash\nlifecycle: active\n",
          "The `Bash` tool was invoked 12 times during this session. If this represents a repeatable workflow, capture it.")
    _note(m / "semantic" / "never-force-push-main.md", "type: preference\nstatus: active\nslug: never-force-push-main\nlifecycle: active\n",
          "User stated: never force-push to main — rewrite only your own unshared branches.")
    _note(m / "semantic" / "vault-root.md", "type: reference\nstatus: active\nslug: vault-root\nlifecycle: active\ntitle: the vault root is /Users/alex/Vault\n",
          "The vault root is /Users/alex/Vault, a plain folder synced by Drive.")
    _note(m / "semantic" / "plain-fact.md", "type: reference\nstatus: active\nslug: plain-fact\ntitle: the daemon commits markdown on cadence\n",
          "The daemon commits whatever git reports dirty, on a cadence, nothing else.")
    _note(m / "crystallized" / "good" / "lane-entry.md", "kind: opinion-supplement\nstatus: proposed\nslug: lane-entry\n",
          "## good\nA supplement in a lane.")
    return v


class TheKeyExtractor(unittest.TestCase):
    def test_reads_the_three_shapes_and_nothing_else(self):
        self.assertEqual(fe.extract_key("Workflow: Bash used 12x", "The `Bash` tool was invoked 12 times during this session."),
                         ("tool:bash", "invocations", "12"))
        # the title is read first, so the shorter phrasing is the subject
        self.assertEqual(fe.extract_key("never force-push to main", "User stated: never force-push to main — rewrite only your own branches."),
                         ("directive:force-push main", "polarity", "never"))
        # a fact keys only on a value-like object; a prose object is a second fact
        self.assertEqual(fe.extract_key("the vault root is /Users/alex/Vault", ""), ("fact:vault root", "value", "/users/alex/vault"))
        self.assertIsNone(fe.extract_key("the vault root is synced by Drive every minute", ""))
        self.assertEqual(fe.extract_key("always force-push to main", "always force-push to main when the branch is yours")[2], "always")
        self.assertIsNone(fe.extract_key("A thought about nothing in particular", "Some prose that states no directive and no fact shape."))


class TheFourOperations(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.vault = _vault(Path(self.td.name))
        self.rules = _rules()
        self.corpus = fe.CorpusIndex(self.vault)

    def tearDown(self):
        self.td.cleanup()

    def _decide(self, title, body, **kw):
        kw.setdefault("slug", "cand")
        return fe.decide(self.vault, title=title, body=body, rules=self.rules, corpus=self.corpus, **kw)

    def test_the_corpus_index_skips_indexes_and_lanes(self):
        rels = {n.rel for n in self.corpus.notes}
        self.assertNotIn("memory/crystallized/good/lane-entry.md", rels)
        self.assertNotIn("memory/semantic/_index.md", rels)
        self.assertEqual(len(rels), 4)

    def test_a_novel_candidate_adds_to_its_class(self):
        d = self._decide("Prefer rebase over back-merge", "User stated: prefer rebasing a stale branch; merge commits are barred from main.",
                         type_hint="preference", confidence="HIGH")
        self.assertEqual((d.op, d.type, d.class_dir, d.dest_rel, d.filing_confidence),
                         ("add", "preference", "memory/semantic", "memory/semantic/cand.md", "high"))
        self.assertEqual(d.flags, [])

    def test_an_exact_twin_is_a_noop_pointing_at_the_note_already_home(self):
        d = self._decide("Workflow: Bash used 12x",
                         "The `Bash` tool was invoked 12 times during this session. If this represents a repeatable workflow, capture it.",
                         type_hint="workflow")
        self.assertEqual((d.op, d.related, d.dest_rel), ("noop", "memory/procedural/workflow-bash.md", "memory/procedural/workflow-bash.md"))
        self.assertIn("exact-twin", d.flags)

    def test_a_key_match_with_a_different_value_supersedes(self):
        d = self._decide("Workflow: Bash used 40x", "The `Bash` tool was invoked 40 times during this session. Capture it.",
                         type_hint="workflow", slug="workflow-bash-2")
        self.assertEqual((d.op, d.related), ("supersede", "memory/procedural/workflow-bash.md"))
        self.assertIn("contradiction", d.flags)
        self.assertEqual(d.dest_rel, "memory/procedural/workflow-bash-2.md")

    def test_a_planted_contradiction_on_a_directive_supersedes(self):
        d = self._decide("always force-push to main", "User stated: always force-push to main when the branch is yours.",
                         type_hint="preference", slug="always-force-push")
        self.assertEqual((d.op, d.related), ("supersede", "memory/semantic/never-force-push-main.md"))

    def test_a_key_match_with_the_same_value_is_an_update_candidate_filed_beside(self):
        d = self._decide("the vault root is /Users/alex/Vault",
                         "The vault root is /Users/alex/Vault; it also holds the Projects tree and the standards folder.",
                         type_hint="reference", slug="vault-root-again")
        self.assertEqual((d.op, d.related, d.dest_rel), ("update", "memory/semantic/vault-root.md", "memory/semantic/vault-root-again.md"))
        self.assertIn("update-candidate", d.flags)

    def test_compatible_facts_co_store(self):
        # Same subject, a prose object: a second fact about the vault root, not a
        # rival value — the false-contradiction trap. It co-stores.
        d = self._decide("the vault root is synced by Drive every minute",
                         "The vault root is synced by Drive every minute, beside the operator's own notes.",
                         type_hint="reference", slug="vault-root-sync")
        self.assertEqual(d.op, "add")
        self.assertNotIn("contradiction", d.flags)
        self.assertIsNone(d.key)

    def test_similarity_only_flags_and_lowers_confidence(self):
        hits = lambda q: ["memory/semantic/plain-fact.md"]
        d = self._decide("the daemon commits markdown on its cadence", "It commits dirty files on a timer; nothing else moves.",
                         type_hint="reference", confidence="HIGH", search=hits, slug="daemon-commits")
        self.assertEqual((d.op, d.related, d.filing_confidence), ("add", "memory/semantic/plain-fact.md", "low"))
        self.assertIn("near-duplicate", d.flags)

    def test_a_weak_overlap_does_not_flag(self):
        hits = lambda q: ["memory/semantic/plain-fact.md"]
        d = self._decide("Prefer rebase over back-merge", "Rebase a stale branch.", type_hint="preference", search=hits, slug="rebase")
        self.assertEqual((d.op, d.flags), ("add", []))

    def test_no_type_takes_the_default_at_low_confidence(self):
        d = self._decide("A loose thought", "Something the operator said with no shape at all.", confidence="HIGH", slug="loose")
        self.assertEqual((d.type, d.filing_confidence), (self.rules.default_type(), "low"))
        self.assertIn("no-type", d.flags)

    def test_a_retired_value_collapses_and_a_record_kind_is_refused(self):
        d = self._decide("An old preference", "User stated: old preferences shape.", kind_hint="preferences", slug="old")
        self.assertEqual(d.type, "preference")
        with self.assertRaises(ValueError):
            self._decide("A brief", "records are not memories", kind_hint="brief", slug="brief")

    def test_a_namesake_with_a_different_body_takes_a_dup_name(self):
        d = self._decide("Plain fact, restated", "A different body under the same slug.", type_hint="reference", slug="plain-fact")
        self.assertEqual(d.dest_rel, "memory/semantic/plain-fact~dup.md")
        self.assertIn("basename-clash", d.flags)


class Applying(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.vault = _vault(Path(self.td.name))
        self.rules = _rules()
        self.corpus = fe.CorpusIndex(self.vault)

    def tearDown(self):
        self.td.cleanup()

    def test_add_writes_the_stamps_into_the_class_directory(self):
        d = fe.decide(self.vault, title="Prefer rebase", body="User stated: prefer rebasing.", slug="prefer-rebase",
                      type_hint="preference", confidence="MEDIUM", source="conversation", rules=self.rules, corpus=self.corpus)
        written = fe.apply(self.vault, d, body="User stated: prefer rebasing.", tags=["git"])
        self.assertEqual(written, self.vault / "memory" / "semantic" / "prefer-rebase.md")
        text = written.read_text(encoding="utf-8")
        for line in ("type: preference", "lifecycle: active", "source: conversation", "filing_confidence: medium"):
            self.assertIn(line + "\n", text)
        self.assertFalse((self.vault / "memory" / "preference").exists())

    def test_supersede_marks_the_old_note_and_deletes_nothing(self):
        d = fe.decide(self.vault, title="Workflow: Bash used 40x", body="The `Bash` tool was invoked 40 times during this session.",
                      slug="workflow-bash-2", type_hint="workflow", rules=self.rules, corpus=self.corpus)
        written = fe.apply(self.vault, d, body="The `Bash` tool was invoked 40 times during this session.")
        self.assertTrue(written.is_file())
        old = (self.vault / "memory" / "procedural" / "workflow-bash.md").read_text(encoding="utf-8")
        self.assertIn("lifecycle: superseded\n", old)
        self.assertIn("superseded_by: memory/procedural/workflow-bash-2.md\n", old)
        self.assertIn("supersedes: memory/procedural/workflow-bash.md", written.read_text(encoding="utf-8"))

    def test_noop_reinforces_and_writes_nothing(self):
        before = sorted(p.name for p in (self.vault / "memory" / "procedural").glob("*.md"))
        d = fe.decide(self.vault, title="Workflow: Bash used 12x",
                      body="The `Bash` tool was invoked 12 times during this session. If this represents a repeatable workflow, capture it.",
                      slug="workflow-bash-3", type_hint="workflow", rules=self.rules, corpus=self.corpus)
        fe.apply(self.vault, d, body="ignored")
        after = sorted(p.name for p in (self.vault / "memory" / "procedural").glob("*.md"))
        self.assertEqual(before, after)
        self.assertIn("occurrences: 2", (self.vault / "memory" / "procedural" / "workflow-bash.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
