#!/usr/bin/env python3
"""Tests for `scripts/vault_note_check.py` (agentm task 195, step 1).

A clean note is silent. Each failure shape the brief found in the vault in the
week to 2026-10-29 is named with its line and its fix: an unquoted colon in
`title:`, a tracker at `status: open`, and the unregistered types `draft`,
`transcript`, `gate-report`, `list` and `check`. A retired type, a note in a
folder the gates skip, a file outside the vault and a file that isn't Markdown
all pass silently. The scope predicates agree with the walks they stand in for.

Run directly:

    python3 scripts/test_vault_note_check.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import vault_note_check as vnc  # noqa: E402

import kind_registry  # noqa: E402  (on sys.path through vault_note_check)
import yaml  # noqa: E402

TRACKER = """---
kind: tracker
title: Stand up the runner
project: home-tech-next
task: 003-stand-up-the-runner
status: {status}
opened: 2026-10-20
updated: 2026-10-21
closed:
---

## Objective

The runner runs.

## State

Not started.

## Next

Start.

## Outcome
"""


class VaultNoteCheck(unittest.TestCase):
    def setUp(self) -> None:
        self.vault = Path(tempfile.mkdtemp(prefix="agentm-note-check-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.vault, ignore_errors=True)

    def _put(self, rel: str, text: str) -> Path:
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def _check(self, rel: str, text: str) -> list:
        return vnc.check_file(self._put(rel, text), self.vault, yaml)

    def _run(self, path: Path) -> tuple:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()) as out:
            code = vnc.main(["--file", str(path), "--vault", str(self.vault)])
        return code, out.getvalue(), err.getvalue()

    def test_a_clean_note_is_silent(self) -> None:
        path = self._put("projects/temple/reports/evening.md",
                         "---\nkind: report\ntitle: \"Evening: the check\"\n---\n\nBody.\n")
        self.assertEqual(self._run(path), (0, "", ""))

    def test_an_unquoted_colon_in_the_title_names_the_line_and_says_quote_it(self) -> None:
        problems = self._check("projects/gbc-doom/design/part-2.md",
                               "---\nkind: design\ntitle: Part 2: the renderer\n---\n")
        self.assertEqual([p.line for p in problems], [3])
        self.assertIn("doesn't parse at `title: Part 2: the renderer`", problems[0].text)
        self.assertIn("mapping values are not allowed here", problems[0].text)
        self.assertIn("must be quoted", problems[0].text)

    def test_a_tracker_at_an_unknown_status_says_fix_the_line_by_hand(self) -> None:
        problems = self._check("projects/home-tech-next/tasks/003-stand-up-the-runner/tracker.md",
                               TRACKER.format(status="open"))
        self.assertEqual([p.line for p in problems], [6])
        self.assertIn("`status: open` is not one of queued, active, parked, done, dropped",
                      problems[0].text)
        self.assertIn("`tracker.py transition` can't move", problems[0].text)
        self.assertIn("by hand", problems[0].text)

    def test_a_good_tracker_is_silent(self) -> None:
        self.assertEqual(self._check(
            "projects/home-tech-next/tasks/003-stand-up-the-runner/tracker.md",
            TRACKER.format(status="queued")), [])

    def test_a_tracker_naming_another_project_is_named(self) -> None:
        problems = self._check("projects/pixelton/tasks/003-stand-up-the-runner/tracker.md",
                               TRACKER.format(status="queued"))
        self.assertEqual(len(problems), 1)
        self.assertIn("is not the project it sits in (`pixelton`)", problems[0].text)

    def test_each_unregistered_type_from_the_brief_is_named_with_registered_neighbours(self) -> None:
        for value in ("draft", "transcript", "gate-report", "list", "check"):
            with self.subTest(value=value):
                problems = self._check(f"projects/pixelton/notes/{value}.md",
                                       f"---\ntitle: A note\nkind: {value}\n---\n")
                self.assertEqual([p.line for p in problems], [3])
                self.assertIn(f"`kind: {value}` isn't a registered type", problems[0].text)
                named = problems[0].text.split("Nearest registered: ")[1].split(".")[0].split(", ")
                self.assertEqual(len(named), 3)
                for kind in named:
                    self.assertTrue(kind_registry.is_known(kind), kind)

    def test_gate_report_offers_report_first(self) -> None:
        problems = self._check("projects/zero-mission-hd/gate.md", "---\nkind: gate-report\n---\n")
        self.assertIn("Nearest registered: report,", problems[0].text)

    def test_the_type_field_is_named_as_written(self) -> None:
        problems = self._check("projects/pixelton/t.md", "---\ntype: transcript\n---\n")
        self.assertIn("`type: transcript`", problems[0].text)

    def test_a_retired_type_awaiting_the_migration_passes(self) -> None:
        self.assertTrue(kind_registry.is_retired("howto"))
        self.assertEqual(self._check("projects/pixelton/how.md", "---\nkind: howto\n---\n"), [])

    def test_a_failing_save_exits_2_with_the_message_on_stderr(self) -> None:
        path = self._put("projects/temple/reports/evening.md", "---\nkind: check\n---\n")
        code, out, err = self._run(path)
        self.assertEqual((code, out), (2, ""))
        self.assertTrue(err.startswith(
            "vault note check: projects/temple/reports/evening.md was saved with 1 problem."))
        self.assertIn("  line 2: `kind: check`", err)

    def test_notes_the_gates_skip_are_skipped(self) -> None:
        bad = "---\nkind: check\ntitle: Part 2: broken\n---\n"
        for rel in (".trash/old.md", ".obsidian/snippet.md", "projects/.hidden/x.md",
                    "standards/templates/a-template.md", "personal/Home/Important Docs/x.md"):
            with self.subTest(rel=rel):
                self.assertEqual(self._run(self._put(rel, bad)), (0, "", ""))

    def test_an_archive_note_skips_the_type_check_but_not_the_header_check(self) -> None:
        # The vocabulary audit never reads `_archive/` or a completed plan; the
        # frontmatter gate reads both. Each check follows its own gate.
        for rel in ("projects/pixelton/_archive/old.md", "projects/pixelton/PLAN.archive.20260101-x.md"):
            with self.subTest(rel=rel):
                self.assertEqual(self._check(rel, "---\nkind: check\n---\n"), [])
                problems = self._check(rel, "---\nkind: check\ntitle: a: b\n---\n")
                self.assertEqual(len(problems), 1)
                self.assertIn("doesn't parse", problems[0].text)

    def test_outside_the_vault_or_not_markdown_is_silent(self) -> None:
        outside = Path(tempfile.mkdtemp(prefix="agentm-note-check-out-"))
        self.addCleanup(shutil.rmtree, outside, True)
        stray = outside / "x.md"
        stray.write_text("---\nkind: check\n---\n", encoding="utf-8")
        self.assertEqual(self._run(stray), (0, "", ""))
        self.assertEqual(self._run(self._put("projects/x/notes.txt", "---\nkind: check\n---\n")),
                         (0, "", ""))

    def test_a_missing_vault_is_silent(self) -> None:
        path = self._put("projects/x/a.md", "---\nkind: check\n---\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = vnc.main(["--file", str(path), "--vault", str(self.vault / "nowhere")])
        self.assertEqual((code, err.getvalue()), (0, ""))

    def test_without_the_contract_the_type_check_steps_aside(self) -> None:
        # No daemon means no registers to check a type against. The header and
        # tracker checks don't need one, and a save must never meet a traceback.
        broken = vnc.storage_rules.StorageRulesError("agentmd is not on PATH")
        with mock.patch.object(vnc.storage_rules, "known_values", side_effect=broken), \
                mock.patch.object(vnc.kind_registry, "in_corpus", side_effect=broken):
            problems = self._check("projects/pixelton/c.md", "---\nkind: check\ntitle: a: b\n---\n")
            self.assertEqual(len(problems), 1)
            self.assertIn("doesn't parse", problems[0].text)
            problems = self._check("projects/home-tech-next/tasks/003-stand-up-the-runner/tracker.md",
                                   TRACKER.format(status="open"))
            self.assertEqual(len(problems), 1)
            self.assertIn("`status: open`", problems[0].text)

    def test_without_pyyaml_the_type_and_tracker_checks_still_run(self) -> None:
        path = self._put("projects/pixelton/c.md", "---\nkind: check\ntitle: a: b\n---\n")
        problems = vnc.check_file(path, self.vault, None)
        self.assertEqual(len(problems), 1)
        self.assertIn("`kind: check`", problems[0].text)


class ScopeAgreesWithTheWalks(unittest.TestCase):
    """The one-note scope predicates against the whole-vault walks they mirror,
    over a tree holding every kind of folder the walks prune."""

    RELS = (
        "index.md", "Ideas.md", ".hidden.md",
        "agent/memory/a.md", "agent/memory/_archive/b.md",
        "projects/p/tracker.md", "projects/p/PLAN.archive.20260101-x.md",
        "projects/p/tasks/001-x/plan.md", "projects/_retired/old.md",
        ".obsidian/x.md", ".trash/y.md", "projects/p/.drafts/z.md",
        "standards/templates/t.md", "standards/s.md",
        "personal/Home/Important Docs/secret.md", "personal/Home/other.md",
        "calendar/2026/10/29.md",
    )

    def setUp(self) -> None:
        self.vault = Path(tempfile.mkdtemp(prefix="agentm-note-scope-"))
        for rel in self.RELS:
            path = self.vault / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("---\nkind: reference\n---\n", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.vault, ignore_errors=True)

    def test_in_scan_matches_the_frontmatter_walk(self) -> None:
        wall = vnc.frontmatter_gate._walled_areas()
        walked = {p.relative_to(self.vault).as_posix()
                  for p in vnc.frontmatter_gate.iter_notes(self.vault, wall)}
        self.assertEqual(walked, {r for r in self.RELS if vnc.frontmatter_gate.in_scan(r, wall)})
        self.assertIn("projects/p/PLAN.archive.20260101-x.md", walked)

    def test_in_corpus_matches_the_vocabulary_walk(self) -> None:
        walked = {rel for _, rel in kind_registry.corpus_notes(self.vault, self.vault)}
        self.assertEqual(walked, {r for r in self.RELS if kind_registry.in_corpus(r)})
        self.assertNotIn("agent/memory/_archive/b.md", walked)


class InstallVerify(unittest.TestCase):
    """`--install` puts the twins in `<vault>/.harness/`, rewrites its own and
    never an operator's."""

    def setUp(self) -> None:
        self.vault = Path(tempfile.mkdtemp(prefix="agentm-note-install-"))
        self.templates = _HERE.parent / "templates"

    def tearDown(self) -> None:
        shutil.rmtree(self.vault, ignore_errors=True)

    def test_installs_both_twins_executable_then_reports_them_current(self) -> None:
        lines = vnc.install_verify(self.vault, self.templates)
        self.assertEqual([line.split(": ")[-1] for line in lines], ["installed", "installed"])
        sh = self.vault / ".harness" / "verify.sh"
        self.assertEqual(sh.read_text(encoding="utf-8"),
                         (self.templates / "vault-verify.sh").read_text(encoding="utf-8"))
        if os.name != "nt":
            self.assertTrue(os.access(sh, os.X_OK))
        self.assertTrue((self.vault / ".harness" / "verify.ps1").is_file())
        lines = vnc.install_verify(self.vault, self.templates)
        self.assertEqual([line.split(": ")[-1] for line in lines], ["current", "current"])

    def test_rewrites_its_own_and_leaves_the_operators(self) -> None:
        harness = self.vault / ".harness"
        harness.mkdir()
        (harness / "verify.sh").write_text("#!/usr/bin/env bash\n# vault-verify (AgentM) old\n",
                                           encoding="utf-8")
        mine = "#!/usr/bin/env bash\necho mine\n"
        (harness / "verify.ps1").write_text(mine, encoding="utf-8")
        lines = vnc.install_verify(self.vault, self.templates)
        self.assertTrue(lines[0].endswith(": installed"))
        self.assertTrue(lines[1].startswith("WARN: "))
        self.assertEqual((harness / "verify.ps1").read_text(encoding="utf-8"), mine)

    def test_no_vault_installs_nothing(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = vnc.main(["--install", "--vault", str(self.vault / "nowhere")])
        self.assertEqual(code, 0)
        self.assertIn("no vault resolves", out.getvalue())


@unittest.skipIf(os.name == "nt" or not shutil.which("bash"), "the bash twin needs bash")
class VerifyScriptEndToEnd(unittest.TestCase):
    """The installed `verify.sh`, run the way verify-dispatch runs it: the clone
    and the vault both come from the install config, nothing from the caller."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="agentm-note-e2e-"))
        self.vault = self.root / "Vault"
        self.prefix = self.root / "prefix"
        self.prefix.mkdir()
        self.vault.mkdir()
        (self.prefix / ".agentm-config.json").write_text(json.dumps({
            "source_clones": {"agentm": str(_HERE.parent)},
            "plugins.obsidian-vault.vault_path": str(self.vault),
        }), encoding="utf-8")
        vnc.install_verify(self.vault, _HERE.parent / "templates")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _save(self, rel: str, text: str) -> subprocess.CompletedProcess:
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        env = {k: v for k, v in os.environ.items()
               if k not in ("MEMORY_ROOT", "MEMORY_VAULT_PATH", "AGENTM_STATE_DIR")}
        env["AGENTM_INSTALL_PREFIX"] = str(self.prefix)
        return subprocess.run(["bash", str(self.vault / ".harness" / "verify.sh"), str(path)],
                              capture_output=True, text=True, env=env, timeout=60)

    def test_a_bad_save_exits_2_with_the_message_and_a_good_one_is_silent(self) -> None:
        bad = self._save("projects/home-tech-next/tasks/003-stand-up-the-runner/tracker.md",
                         TRACKER.format(status="open"))
        self.assertEqual(bad.returncode, 2, bad.stderr)
        self.assertIn("`status: open` is not one of", bad.stderr)
        self.assertEqual(bad.stdout, "")
        good = self._save("projects/home-tech-next/tasks/003-stand-up-the-runner/tracker.md",
                          TRACKER.format(status="active"))
        self.assertEqual((good.returncode, good.stdout, good.stderr), (0, "", ""))

    def test_a_file_that_is_not_markdown_is_silent(self) -> None:
        done = self._save("projects/x/data.json", "{not json")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))


if __name__ == "__main__":
    unittest.main()
