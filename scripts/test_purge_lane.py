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


if __name__ == "__main__":
    unittest.main()
