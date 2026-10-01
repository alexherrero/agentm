#!/usr/bin/env python3
"""test_plan_c_manifests.py — the one-time cleanups of Plan C (task 178),
planned as manifests the dreaming binary makes through its journal."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for d in (_HERE / "migrate", _HERE.parent / "harness" / "skills" / "memory" / "scripts"):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

import episodic_trace as et  # noqa: E402
import plan_c_manifests as pcm  # noqa: E402


def _apply(root: Path, manifest: dict) -> None:
    """What `agentmdream apply -apply` does, for a test: every act's note
    still hashes as planned, then its bytes are written."""
    for a in manifest["acts"]:
        p = root / a["rel"]
        assert hashlib.sha256(p.read_bytes()).hexdigest() == a["before_sha256"], a["rel"]
    for a in manifest["acts"]:
        (root / a["rel"]).write_text(a["after"], encoding="utf-8")


class TraceFoldTests(unittest.TestCase):
    SID = "9b9d740e-0d29-4ae9-a7a9-23793b27de80"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "agent"
        self.folder = self.root / "memory" / "episodic"
        self.folder.mkdir(parents=True)

    def _write(self, name, title, sid=None, recalled=(), outcome=""):
        t = et.Trace(when=date(2026, 9, 13), session_id=sid or self.SID, title=title, asked=title,
                     touched=list(recalled), recalled=list(recalled), outcome=outcome)
        text = t.render().replace(f"slug: {t.slug}\n", f"slug: {Path(name).stem}\n")
        (self.folder / name).write_text(text, encoding="utf-8")

    def test_a_session_with_two_traces_keeps_the_titled_one(self):
        self._write("2026-09-13-let-s-do-follow-up-b-9b9d740e.md", "let's do follow-up b",
                    recalled=["memory/semantic/a.md"], outcome="Started.")
        self._write("2026-09-13-session-9b9d740e-9b9d740e.md", et.fallback_title(self.SID),
                    recalled=["memory/semantic/b.md"], outcome="Finished.")
        self._write("2026-09-13-another-ffff1111.md", "another", sid="ffff1111-2222",
                    recalled=["memory/semantic/c.md"])
        m = pcm.plan_traces(self.root)
        self.assertEqual(m["job"], "manifest-plan-c-traces")
        self.assertEqual(m["sessions"], [{"session": self.SID,
                                          "survivor": "memory/episodic/2026-09-13-let-s-do-follow-up-b-9b9d740e.md",
                                          "folded": ["memory/episodic/2026-09-13-session-9b9d740e-9b9d740e.md"]}])
        self.assertEqual(len(m["acts"]), 2)
        loser = m["acts"][1]
        self.assertEqual((loser["from"], loser["to"]), ("active", "superseded"))
        self.assertNotIn("from", m["acts"][0], "the survivor's rewrite is no lifecycle move")
        _apply(self.root, m)
        survivor = (self.folder / "2026-09-13-let-s-do-follow-up-b-9b9d740e.md").read_text(encoding="utf-8")
        self.assertIn("[[memory/semantic/a]]", survivor)
        self.assertIn("[[memory/semantic/b]]", survivor)
        folded = (self.folder / "2026-09-13-session-9b9d740e-9b9d740e.md").read_text(encoding="utf-8")
        self.assertIn("lifecycle: superseded\n", folded)
        self.assertIn("superseded_by: memory/episodic/2026-09-13-let-s-do-follow-up-b-9b9d740e.md\n", folded)
        self.assertIn("status: active\n", folded, "the relation's shape leaves status alone")
        # Once folded, the session has one trace, and a re-plan finds nothing.
        self.assertEqual(et.find_session_trace(self.root, self.SID).name,
                         "2026-09-13-let-s-do-follow-up-b-9b9d740e.md")
        self.assertEqual(pcm.plan_traces(self.root)["acts"], [])

    def test_a_trace_saved_with_crlf_line_endings_is_folded_too(self):
        # A Windows editor saves CRLF; the manifest hashes the bytes as they are.
        self._write("2026-09-13-a-9b9d740e.md", "a", recalled=["memory/semantic/a.md"])
        self._write("2026-09-13-session-9b9d740e.md", et.fallback_title(self.SID), recalled=["memory/semantic/b.md"])
        for p in self.folder.glob("*.md"):
            p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        m = pcm.plan_traces(self.root)
        self.assertEqual(len(m["acts"]), 2)
        self.assertEqual(m["sessions"][0]["survivor"], "memory/episodic/2026-09-13-a-9b9d740e.md")
        raw = (self.folder / "2026-09-13-a-9b9d740e.md").read_bytes()
        self.assertEqual(m["acts"][0]["before_sha256"], hashlib.sha256(raw).hexdigest())

    def test_a_manifest_is_written_where_the_operator_reads_it(self):
        self._write("2026-09-13-a-9b9d740e.md", "a", recalled=["memory/semantic/a.md"])
        self._write("2026-09-13-b-9b9d740e.md", "b", recalled=["memory/semantic/b.md"])
        out = Path(self._tmp.name) / "m.json"
        self.assertEqual(pcm.main(["traces", "--memory-root", str(self.root), "--out", str(out)]), 0)
        self.assertEqual(len(json.loads(out.read_text(encoding="utf-8"))["acts"]), 2)



class ChunkFoldTests(unittest.TestCase):
    """One article is one note (task 178 step 5): an article's chunk notes and an
    earlier full-length ingest of the same page fold into its document; the
    facts drawn from it stay."""

    URL = "https://example.com/always-on-agent"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "agent"
        self.sem = self.root / "memory" / "semantic"
        self.sem.mkdir(parents=True)

    def _note(self, name, *, created, words, url=None, lifecycle="active"):
        fm = [f"type: reference", "status: active", f"lifecycle: {lifecycle}", f"created: {created}"]
        if url:
            fm.append(f"source_url: {url}")
        (self.sem / name).write_text("---\n" + "\n".join(fm) + "\n---\n\n" + " ".join(["word"] * words) + "\n",
                                     encoding="utf-8")

    def test_the_chunks_and_an_earlier_ingest_fold_into_the_document(self):
        self._note("article.md", created="2026-09-11", words=1100, url=self.URL)
        for i in (0, 1, 2, 10):
            self._note(f"article-chunk-{i}.md", created="2026-09-11", words=80, url=self.URL)
        self._note("article-stack.md", created="2026-08-11", words=4600, url=self.URL)
        self._note("a-fact-from-it.md", created="2026-08-11", words=120, url=self.URL)
        self._note("other-chunk-0.md", created="2026-09-11", words=80)  # no document beside it
        self._note("article-chunk-3.md", created="2026-09-11", words=80, lifecycle="superseded")
        m = pcm.plan_chunks(self.root)
        self.assertEqual(m["families"], [{"document": "memory/semantic/article.md", "folded": [
            "memory/semantic/article-chunk-0.md", "memory/semantic/article-chunk-1.md",
            "memory/semantic/article-chunk-2.md", "memory/semantic/article-chunk-10.md",
            "memory/semantic/article-stack.md"]}])
        _apply(self.root, m)
        for name in ("article-chunk-0.md", "article-stack.md"):
            text = (self.sem / name).read_text(encoding="utf-8")
            self.assertIn("lifecycle: superseded\n", text)
            self.assertIn("superseded_by: memory/semantic/article.md\n", text)
        self.assertIn("lifecycle: active", (self.sem / "a-fact-from-it.md").read_text(encoding="utf-8"))
        self.assertEqual(pcm.plan_chunks(self.root)["acts"], [], "a re-plan after the fold finds nothing")

    def test_a_chunk_whose_document_is_superseded_stays(self):
        self._note("article.md", created="2026-09-11", words=1100, url=self.URL, lifecycle="superseded")
        self._note("article-chunk-0.md", created="2026-09-11", words=80, url=self.URL)
        self.assertEqual(pcm.plan_chunks(self.root)["acts"], [])



class IdeaFoldTests(unittest.TestCase):
    """An idea adds to its card (task 178 step 6): a semantic copy of a card is
    superseded by it, and words only the copy held reach the card first."""

    CARD = ("---\ntitle: \"Streaming, but not 4K Blu-ray yet\"\ntype: idea\narea: home-tech\nstatus: active\n"
            "---\n\nStream the everyday catalogue, keep buying 4K discs for the films worth the bitrate.\n")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name) / "vault"
        self.root = self.vault / "agent"
        (self.vault / ".obsidian").mkdir(parents=True)
        self.sem = self.root / "memory" / "semantic"
        self.sem.mkdir(parents=True)
        (self.vault / "personal" / "ideas").mkdir(parents=True)
        self.card = self.vault / "personal" / "ideas" / "streaming-but-not-4k-bluray-yet.md"
        self.card.write_text(self.CARD, encoding="utf-8")

    def _copy(self, name, body):
        (self.sem / name).write_text("---\ntype: idea\nstatus: unfiled\nlifecycle: active\n---\n\n" + body + "\n",
                                     encoding="utf-8")

    def _apply_rel(self, manifest):
        import os
        for a in manifest["acts"]:
            path = Path(os.path.normpath(self.root / a["rel"]))
            assert hashlib.sha256(path.read_bytes()).hexdigest() == a["before_sha256"], a["rel"]
            path.write_text(a["after"], encoding="utf-8")

    def test_a_word_for_word_copy_is_superseded_by_its_card(self):
        self._copy("streaming-but-not-4k-bluray-yet.md",
                   "Stream the everyday catalogue, keep buying 4K discs for the films worth the bitrate.")
        self._copy("drip-irrigation.md", "Drip lines on a timer for the tomato beds this spring.")
        m = pcm.plan_ideas(self.root, today="2026-09-29")
        self.assertEqual(m["folded"], [{"copy": "memory/semantic/streaming-but-not-4k-bluray-yet.md",
                                        "card": "personal/ideas/streaming-but-not-4k-bluray-yet.md",
                                        "added_to_card": False}])
        self._apply_rel(m)
        copy = (self.sem / "streaming-but-not-4k-bluray-yet.md").read_text(encoding="utf-8")
        self.assertIn("lifecycle: superseded\n", copy)
        self.assertIn("lifecycle_since: 2026-09-29\n", copy)
        self.assertIn("superseded_by: personal/ideas/streaming-but-not-4k-bluray-yet.md\n", copy)
        self.assertEqual(self.card.read_text(encoding="utf-8"), self.CARD)
        self.assertIn("lifecycle: active", (self.sem / "drip-irrigation.md").read_text(encoding="utf-8"))

    def test_words_only_the_copy_held_reach_the_card(self):
        self._copy("streaming-but-not-4k-bluray-yet.md", "Also: the Apple TV handles Dolby Vision now.")
        m = pcm.plan_ideas(self.root, today="2026-09-29")
        self.assertEqual(m["acts"][0]["rel"], "../personal/ideas/streaming-but-not-4k-bluray-yet.md")
        self._apply_rel(m)
        card = self.card.read_text(encoding="utf-8")
        self.assertIn("## Added by capture", card)
        self.assertIn("folded from memory/semantic/streaming-but-not-4k-bluray-yet.md", card)
        self.assertIn("Dolby Vision", card)



class LabelBackfillTests(unittest.TestCase):
    """Label existing memories where the source makes it clear (task 178 step
    10): a trace through its session's transcript, a card through links that
    reach one project; conventions and preferences stay global."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.vault = base / "vault"
        self.root = self.vault / "agent"
        (self.vault / ".obsidian").mkdir(parents=True)
        self.code = base / "code"
        for slug in ("agentm", "crickets"):
            (self.code / slug).mkdir(parents=True)
            (self.vault / "projects" / slug / "research").mkdir(parents=True)
            (self.vault / "projects" / slug / "project.yaml").write_text(
                f"slug: {slug}\ncode_paths:\n  - {self.code / slug}\n", encoding="utf-8")
        (self.vault / "projects" / "agentm" / "research" / "retrieval-notes.md").write_text("# notes\n",
                                                                                           encoding="utf-8")
        self.claude = base / "claude-projects"
        self.ep = self.root / "memory" / "episodic"
        self.ep.mkdir(parents=True)
        self.sem = self.root / "memory" / "semantic"
        self.sem.mkdir(parents=True)

    def _session(self, sid, cwd):
        d = self.claude / "-encoded-cwd"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{sid}.jsonl").write_text(json.dumps({"type": "user", "cwd": str(cwd)}) + "\n", encoding="utf-8")

    def _trace(self, name, sid, project=None):
        t = et.Trace(when=date(2026, 9, 20), session_id=sid, title=name, asked=name,
                     recalled=["memory/semantic/x.md"], touched=["memory/semantic/x.md"], project=project or "")
        (self.ep / f"{name}.md").write_text(t.render().replace(f"slug: {t.slug}", f"slug: {name}"), encoding="utf-8")

    def _card(self, name, type_, **fields):
        extra = "".join(f"{k}: {v}\n" for k, v in fields.items())
        (self.sem / f"{name}.md").write_text(f"---\ntype: {type_}\nstatus: active\nlifecycle: active\n{extra}---\n\n"
                                             f"{name}\n", encoding="utf-8")

    def test_traces_take_their_session_s_project_and_cards_their_links_project(self):
        self._session("s-agentm", self.code / "agentm" / ".claude" / "worktrees" / "slot")
        self._session("s-crickets", self.code / "crickets")
        self._session("s-stray", self.root.parent.parent / "downloads")
        self._trace("t-agentm", "s-agentm")
        self._trace("t-crickets", "s-crickets")
        self._trace("t-stray", "s-stray")
        self._trace("t-gone", "s-no-transcript")
        self._trace("t-labelled", "s-agentm", project="agentm")
        self._card("a-fix", "fix", related='["[[t-agentm]]"]')
        self._card("a-reference", "reference", related='["[[projects/agentm/research/retrieval-notes]]"]')
        self._card("a-bare-name", "reference", related='["[[retrieval-notes]]"]')
        self._card("split", "reference", related='["[[t-agentm]]", "[[t-crickets]]"]')
        self._card("a-convention", "convention", related='["[[t-agentm]]"]')
        self._card("unlinked", "reference")
        m = pcm.plan_labels(self.root, claude_projects=self.claude)
        got = {(l["note"].rsplit("/", 1)[-1], l["project"], l["by"]) for l in m["labels"]}
        self.assertEqual(got, {("t-agentm.md", "agentm", "transcript"), ("t-crickets.md", "crickets", "transcript"),
                               ("a-fix.md", "agentm", "links"), ("a-reference.md", "agentm", "links"),
                               ("a-bare-name.md", "agentm", "links")})
        self.assertEqual(sorted(u["note"].rsplit("/", 1)[-1] for u in m["unresolved"]), ["t-gone.md", "t-stray.md"])
        _apply(self.root, m)
        self.assertIn("\nproject: agentm\n", (self.ep / "t-agentm.md").read_text(encoding="utf-8"))
        text = (self.sem / "a-fix.md").read_text(encoding="utf-8")
        self.assertIn("\nproject: agentm\n", text)
        import card_shape
        self.assertEqual(card_shape.reorder(text), text)
        self.assertNotIn("project:", (self.sem / "a-convention.md").read_text(encoding="utf-8"))
        self.assertEqual(pcm.plan_labels(self.root, claude_projects=self.claude)["acts"], [],
                         "a re-plan after the backfill labels nothing twice")

    def test_a_record_name_two_projects_share_labels_nothing(self):
        """`[[tracker]]` sits in many projects; the first one read used to win
        (stack-sherwood and stack-dev-setup were labelled `home`)."""
        for slug in ("agentm", "crickets"):
            (self.vault / "projects" / slug / "tracker.md").write_text("# tracker\n", encoding="utf-8")
        self._session("s-agentm", self.code / "agentm")
        self._trace("t-agentm", "s-agentm")
        self._card("stack-crickets", "reference", related='["[[tracker]]"]')
        self._card("mixed", "reference", related='["[[tracker]]", "[[t-agentm]]"]')
        m = pcm.plan_labels(self.root, claude_projects=self.claude)
        self.assertEqual({l["note"].rsplit("/", 1)[-1] for l in m["labels"]}, {"t-agentm.md"})

    def test_a_trace_whose_session_moved_into_its_repo_is_labelled(self):
        """The head records only a scratch workspace; the host filed the
        transcript under the repo's folder."""
        import session_binding
        d = self.claude / session_binding.host_folder_name(str(self.code / "crickets"))
        d.mkdir(parents=True)
        (d / "s-moved.jsonl").write_text(json.dumps({"type": "user", "cwd": "/tmp/scratch-workspace"}) + "\n",
                                         encoding="utf-8")
        self._trace("t-moved", "s-moved")
        m = pcm.plan_labels(self.root, claude_projects=self.claude)
        self.assertEqual([(l["note"].rsplit("/", 1)[-1], l["project"]) for l in m["labels"]],
                         [("t-moved.md", "crickets")])


if __name__ == "__main__":
    unittest.main()
