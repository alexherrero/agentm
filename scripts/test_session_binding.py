#!/usr/bin/env python3
"""Tests for `session_binding` (agentm-vault plan 09, task 3).

A directory's `.harness/project.json` names the vault project and its
`.harness/active-plan` names the task; a directory with no `project.json` is
unbound, and so is one whose values are not single path components. A
transcript's own `cwd` decides the binding before any fallback. The marker's
reading agrees with `harness_memory`'s, row for row.

Run directly:

    python3 scripts/test_session_binding.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for _p in (str(_SKILL), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import harness_memory as hm  # noqa: E402
import session_binding as sb  # noqa: E402


class _Dir(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="agentm-binding-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.repo = self.root / "repo"
        (self.repo / ".harness").mkdir(parents=True)

    def _project_json(self, data) -> None:
        text = data if isinstance(data, str) else json.dumps(data)
        (self.repo / ".harness" / "project.json").write_text(text, encoding="utf-8")

    def _marker(self, text: str) -> None:
        (self.repo / ".harness" / "active-plan").write_text(text, encoding="utf-8")


class ReadBinding(_Dir):
    def test_no_project_json_is_unbound(self) -> None:
        self._marker("foo\n")
        self.assertEqual(sb.read_binding(self.repo), sb.UNBOUND)
        self.assertEqual(sb.read_binding(None), sb.UNBOUND)
        self.assertEqual(sb.read_binding(self.root / "absent"), sb.UNBOUND)

    def test_the_vault_project_and_the_task(self) -> None:
        self._project_json({"vault_project": "agentm", "github": {"repo": "alexherrero/other"}})
        self._marker("agentm-vault-09-projects-writers\n")
        self.assertEqual(sb.read_binding(self.repo), sb.Binding("agentm", "agentm-vault-09-projects-writers"))

    def test_the_repo_name_when_no_vault_project_is_named(self) -> None:
        self._project_json({"github": {"repo": "alexherrero/sherwood.git"}})
        self.assertEqual(sb.read_binding(self.repo), sb.Binding("sherwood", None))

    def test_a_marker_in_the_plan_file_form_names_its_slug(self) -> None:
        self._project_json({"vault_project": "agentm"})
        for text in ("PLAN-foo.md\n", "PLAN-foo\n", "  foo  \n"):
            with self.subTest(text=text):
                self._marker(text)
                self.assertEqual(sb.read_binding(self.repo).task, "foo")

    def test_a_blank_or_unsafe_marker_binds_no_task(self) -> None:
        self._project_json({"vault_project": "agentm"})
        for text in ("", "   \n", "../escape\n", "a/b\n", "PLAN.md\n"):
            with self.subTest(text=text):
                self._marker(text)
                self.assertEqual(sb.read_binding(self.repo), sb.Binding("agentm", None))

    def test_an_unreadable_or_unsafe_project_is_unbound(self) -> None:
        self._marker("foo\n")
        for data in ("{not json", "[]", {"vault_project": "../up"}, {"vault_project": ""},
                     {"github": {"repo": ""}}):
            with self.subTest(data=data):
                self._project_json(data)
                self.assertEqual(sb.read_binding(self.repo), sb.UNBOUND)

    def test_stamps_carry_only_what_is_bound(self) -> None:
        self.assertEqual(sb.stamps(sb.Binding("agentm", "foo")), {"project": "agentm", "task": "foo"})
        self.assertEqual(sb.stamps(sb.Binding("agentm", None)), {"project": "agentm"})
        self.assertEqual(sb.stamps(sb.UNBOUND), {})


class Transcripts(_Dir):
    def _transcript(self, *cwds) -> Path:
        path = self.root / "session.jsonl"
        rows = [json.dumps({"type": "summary"})]
        rows += [json.dumps({"type": "user", "cwd": c}) for c in cwds]
        path.write_text("\n".join(rows) + "\nnot json\n", encoding="utf-8")
        return path

    def test_the_transcript_records_the_session_directory(self) -> None:
        self.assertEqual(sb.transcript_cwd(self._transcript(str(self.repo), "/elsewhere")), str(self.repo))
        self.assertIsNone(sb.transcript_cwd(self._transcript()))
        self.assertIsNone(sb.transcript_cwd(self.root / "absent.jsonl"))

    def test_the_transcript_decides_before_the_fallback(self) -> None:
        self._project_json({"vault_project": "agentm"})
        other = self.root / "other"
        (other / ".harness").mkdir(parents=True)
        (other / ".harness" / "project.json").write_text('{"vault_project": "crickets"}', encoding="utf-8")
        self.assertEqual(sb.for_transcript(self._transcript(str(self.repo)), fallback=other).project, "agentm")
        self.assertEqual(sb.for_transcript(self._transcript(), fallback=other).project, "crickets")
        self.assertEqual(sb.for_transcript(self._transcript()), sb.UNBOUND)


class AgreesWithTheResolver(unittest.TestCase):
    def test_the_marker_reading_matches_harness_memory(self) -> None:
        rows = ["foo", "PLAN-foo.md", "PLAN-foo", " foo ", "PLAN", "PLAN.md", "", "../etc",
                "a/b", "x\\y", "..", "PLAN-PLAN.md", "foo\x00"]
        for raw in rows:
            with self.subTest(raw=raw):
                resolver = hm._normalize_plan_name(raw)
                if resolver is not None and not hm._is_safe_plan_slug(resolver):
                    resolver = None
                self.assertEqual(sb.task_slug(raw), resolver)


if __name__ == "__main__":
    unittest.main()
