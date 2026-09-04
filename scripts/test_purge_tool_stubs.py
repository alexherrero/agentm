#!/usr/bin/env python3
"""The tool-stub purge (miner-provenance, task 2): a dry run manifests exactly
the stubs and touches nothing; the apply refuses without the ruling's count
and removes exactly the manifest with it; a note that merely mentions a tool
count inside a longer body is not a stub."""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_MIGRATE = _HERE.parent / "scripts" / "migrate"
if str(_MIGRATE) not in sys.path:
    sys.path.insert(0, str(_MIGRATE))

import purge_tool_stubs as pts  # noqa: E402

STUB = ("---\ntype: workflow\nstatus: active\nslug: {slug}\ntitle: Workflow: {tool} used {n}x\n---\n\n"
        "The `{tool}` tool was invoked {n} times during this session. If this represents a repeatable "
        "workflow, capture the sequence + when to use it.\n")


class TheStubPurge(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="stub-purge-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.proc = self.root / "memory" / "procedural"
        self.sem = self.root / "memory" / "semantic"
        self.proc.mkdir(parents=True); self.sem.mkdir(parents=True)
        for tool, n in (("Bash", 2592), ("Edit", 60), ("Agent", 8)):
            (self.proc / f"workflow-{tool.lower()}.md").write_text(
                STUB.format(slug=f"workflow-{tool.lower()}", tool=tool, n=n), encoding="utf-8")
        # Two real notes: a procedure, and a note that mentions a tool count in passing.
        (self.proc / "battery-first.md").write_text(
            "---\ntype: workflow\nstatus: active\nslug: battery-first\n---\n\nRun the battery before every commit.\n",
            encoding="utf-8")
        (self.sem / "about-counts.md").write_text(
            "---\ntype: reference\nstatus: active\nslug: about-counts\n---\n\n"
            "The `Bash` tool was invoked 12 times during this session. That number is why the volume gate exists: "
            "a session that shells out a thousand times is a session to look at.\n", encoding="utf-8")
        (self.sem / "links-to-a-stub.md").write_text(
            "---\ntype: reference\nstatus: active\nslug: links-to-a-stub\n---\n\nSee [[workflow-bash]] for the count.\n",
            encoding="utf-8")

    def _files(self):
        return sorted(p.name for p in (self.root / "memory").rglob("*.md"))

    def test_a_dry_run_manifests_exactly_the_stubs_and_touches_nothing(self):
        out = self.root / "report"
        rc = pts.main(["--vault", str(self.root), "--report-dir", str(out)])
        self.assertEqual(rc, 0)
        manifest = (out / "purge-manifest.csv").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(manifest), 1 + 3)
        self.assertTrue(all("memory/procedural/workflow-" in line for line in manifest[1:]))
        self.assertNotIn("about-counts", "".join(manifest))
        links = (out / "inbound-links.csv").read_text(encoding="utf-8").splitlines()
        self.assertEqual(links[1:], ["memory/semantic/links-to-a-stub.md,workflow-bash"])
        self.assertEqual(len(self._files()), 6)

    def test_the_apply_refuses_without_the_ruling_and_with_the_wrong_count(self):
        out = self.root / "report"
        with self.assertRaises(SystemExit) as cm:
            pts.main(["--vault", str(self.root), "--report-dir", str(out), "--apply"])
        self.assertIn("refused", str(cm.exception))
        with self.assertRaises(SystemExit):
            pts.main(["--vault", str(self.root), "--report-dir", str(out), "--apply", "--confirm-count", "824"])
        self.assertEqual(len(self._files()), 6)

    def test_the_apply_removes_exactly_the_manifest_with_the_right_count(self):
        out = self.root / "report"
        rc = pts.main(["--vault", str(self.root), "--report-dir", str(out), "--apply", "--confirm-count", "3"])
        self.assertEqual(rc, 0)
        self.assertEqual(self._files(), ["about-counts.md", "battery-first.md", "links-to-a-stub.md"])

    def test_a_manifest_row_carries_the_hash_of_what_went(self):
        rows = pts.find_stubs(self.root)
        self.assertEqual({r["tool"] for r in rows}, {"Bash", "Edit", "Agent"})
        self.assertTrue(all(len(r["sha256"]) == 64 for r in rows))


if __name__ == "__main__":
    unittest.main()
