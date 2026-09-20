#!/usr/bin/env python3
"""test_check_memory_root_shape.py — the shape gate's three states (agentm-vault
plan 05): pre-migration is reported and passes, the trimmed shape passes,
and residue or a mixed state fails with the item named."""
from __future__ import annotations

import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TOOLKIT = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
_MIGRATE = _HERE / "migrate"
for _p in (str(_HERE), str(_TOOLKIT), str(_MIGRATE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine_state_isolation as esi  # noqa: E402
import memory_root_trims as mrt  # noqa: E402
from test_memory_root_trims_migration import build_pre_trims_vault  # noqa: E402

_spec = importlib.util.spec_from_file_location("check_memory_root_shape", _HERE / "check-memory-root-shape.py")
shape = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shape)


class ShapeGateTests(unittest.TestCase):
    def setUp(self):
        cm = esi.isolated_engine_state()
        self.engine = Path(cm.__enter__())
        self.addCleanup(cm.__exit__, None, None, None)
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = build_pre_trims_vault(Path(self._td.name))
        self.vault = self.root.parent

    def _check(self) -> tuple[int, str]:
        out = io.StringIO()
        rc = shape.check(self.root, out=out)
        return rc, out.getvalue()

    def test_pre_migration_is_reported_and_passes(self):
        rc, text = self._check()
        self.assertEqual(rc, 0)
        self.assertIn("pre-migration", text)
        self.assertIn("pending: memory/_always-load", text)
        self.assertIn("pending: _meta", text)

    def test_the_trimmed_shape_passes(self):
        mrt.Trims(self.root, self.engine, apply=True, out=io.StringIO()).run()
        rc, text = self._check()
        self.assertEqual(rc, 0, text)
        self.assertIn("clean", text)

    def test_a_recreated_retired_path_fails_by_name(self):
        mrt.Trims(self.root, self.engine, apply=True, out=io.StringIO()).run()
        (self.root / "memory" / "_watchlist").mkdir()
        rc, text = self._check()
        self.assertEqual(rc, 1)
        self.assertIn("memory/_watchlist", text)

    def test_a_mixed_state_fails(self):
        (self.vault / "standards" / "user-preferences.md").write_text("mine\n", encoding="utf-8")
        rc, text = self._check()
        self.assertEqual(rc, 1)
        self.assertIn("retired location still present", text)

    def test_loose_files_and_engine_json_are_named(self):
        mrt.Trims(self.root, self.engine, apply=True, out=io.StringIO()).run()
        (self.root / "memory" / "stray.md").write_text("x\n", encoding="utf-8")
        (self.root / "notes.json").write_text("{}", encoding="utf-8")
        (self.root / "extra").mkdir()
        rc, text = self._check()
        self.assertEqual(rc, 1)
        self.assertIn("memory/ holds a loose file: stray.md", text)
        self.assertIn("engine JSON under agent/: notes.json", text)
        self.assertIn("extra directory: extra/", text)

    def test_home_md_and_drive_residue_are_tolerated(self):
        mrt.Trims(self.root, self.engine, apply=True, out=io.StringIO()).run()
        (self.root / ".DS_Store").write_bytes(b"\x00")
        (self.root / ".rename-vault-root-complete").write_text("done\n", encoding="utf-8")
        # Drive's folder-icon file is `Icon\r`; Windows cannot create that name,
        # so the runner writes the plain spelling the gate also ignores.
        import os
        (self.root / "memory" / ("Icon" if os.name == "nt" else "Icon\r")).write_bytes(b"")
        rc, _text = self._check()
        self.assertEqual(rc, 0)

    def test_home_md_is_named_once_the_maps_data_run_is_done(self):
        # The retired map is tolerated until the maps data run (agentm-vault
        # plan 07) writes its marker; after that it cannot come back.
        mrt.Trims(self.root, self.engine, apply=True, out=io.StringIO()).run()
        (self.root / "Home.md").write_text("# Home\n", encoding="utf-8")
        (self.root / "memory" / ".maps-and-root-notes-complete").write_text("run m\n", encoding="utf-8")
        rc, text = self._check()
        self.assertEqual(rc, 1, text)
        self.assertIn("agent/ holds a loose file: Home.md", text)
        self.assertNotIn(".maps-and-root-notes-complete", text, "the data run's own marker was named")
        (self.root / "Home.md").unlink()
        rc, text = self._check()
        self.assertEqual(rc, 0, text)

    def test_the_root_casings_marker_is_tolerated_in_memory(self):
        # The root casing's data run (agentm-vault plan 08) writes
        # `memory/.root-casing-complete` beside the other data runs' markers;
        # the first battery after the run named it as a loose file.
        mrt.Trims(self.root, self.engine, apply=True, out=io.StringIO()).run()
        (self.root / "memory" / ".root-casing-complete").write_text("run r\nfinished t\n", encoding="utf-8")
        rc, text = self._check()
        self.assertEqual(rc, 0, text)
        self.assertNotIn(".root-casing-complete", text, "the data run's own marker was named")

    def test_the_inbox_is_a_standard_child_and_a_fifth_one_is_not(self):
        # agentm-vault plan 16: `agent/` holds exactly archive/, diagnostics/,
        # inbox/ and memory/. The gate has to accept the fourth and still fail
        # on a fifth, or "nothing loose" has quietly become "anything at all".
        mrt.Trims(self.root, self.engine, apply=True, out=io.StringIO()).run()
        (self.root / "inbox").mkdir(exist_ok=True)
        (self.root / "archive").mkdir(exist_ok=True)
        rc, text = self._check()
        self.assertEqual(rc, 0, text)
        self.assertNotIn("inbox", text, "the fourth standard child was named as a finding")
        self.assertEqual(
            sorted(p.name for p in self.root.iterdir() if p.is_dir()),
            ["archive", "diagnostics", "inbox", "memory"])
        (self.root / "outbox").mkdir()
        rc, text = self._check()
        self.assertEqual(rc, 1, text)
        self.assertIn("extra directory: outbox/", text)

    def test_a_card_in_the_inbox_is_not_a_loose_file(self):
        # The inbox's whole content is cards. The gate walks `agent/` one level
        # for loose files; a card one level deeper must not read as one.
        mrt.Trims(self.root, self.engine, apply=True, out=io.StringIO()).run()
        (self.root / "inbox").mkdir(exist_ok=True)
        (self.root / "inbox" / "a-thought.md").write_text(
            "---\ntitle: a thought\n---\n\nbody\n", encoding="utf-8")
        rc, text = self._check()
        self.assertEqual(rc, 0, text)
        self.assertNotIn("a-thought.md", text)

    def test_the_retired_staging_inbox_is_named_rather_than_swept(self):
        # The hourly sweep no longer walks `memory/_inbox/` (plan 16). A vault
        # that still holds one holds cards nothing will ever drain, so the gate
        # says so by name — the alternative is a directory that looks live and
        # is not.
        mrt.Trims(self.root, self.engine, apply=True, out=io.StringIO()).run()
        (self.root / "memory" / "_inbox").mkdir()
        (self.root / "memory" / "_inbox" / "stranded.md").write_text(
            "---\nstatus: inbox\n---\n", encoding="utf-8")
        rc, text = self._check()
        self.assertEqual(rc, 1, text)
        self.assertIn("memory/_inbox/", text)
        self.assertIn("retired staging directory", text)
        self.assertIn("agent/inbox/", text)

    def test_a_missing_standards_file_is_named(self):
        mrt.Trims(self.root, self.engine, apply=True, out=io.StringIO()).run()
        (self.vault / "standards" / "moc-standards.md").unlink()
        rc, text = self._check()
        self.assertEqual(rc, 1)
        self.assertIn("standards/ is missing moc-standards.md", text)

    def test_no_memory_root_is_nothing_to_check(self):
        import subprocess
        env = {"PATH": "/usr/bin:/bin", "MEMORY_ROOT": str(Path(self._td.name) / "nowhere"),
               "AGENTM_STATE_DIR": str(self.engine), "HOME": self._td.name}
        r = subprocess.run([sys.executable, str(_HERE / "check-memory-root-shape.py")],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nothing to check", r.stdout)


if __name__ == "__main__":
    unittest.main()
