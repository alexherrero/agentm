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


if __name__ == "__main__":
    unittest.main()
