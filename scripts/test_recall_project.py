#!/usr/bin/env python3
"""The session's project in recall (agentm-vault plan 09, task 3).

A card whose `project:` matches the session's binding ranks above an equal card
that does not, in the in-process arm and through the daemon; a session with no
binding ranks exactly as before. The ranker never multiplies above 1.0, so the
lift is a mild dampening of every card the session's project does not match. The
prompt hook reads the session's directory from the payload's `cwd`, and an
installed binary that predates the flag is asked again without it.
"""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import recall  # noqa: E402

BODY = "The release gate waits for the checks to finish before the tag is pushed.\n"
QUERY = "release gate checks tag"


def _note(vault: Path, rel: str, project: str | None = None) -> None:
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    fm = "---\ntitle: Gate\nkind: reference\nstatus: active\n"
    if project:
        fm += f"project: {project}\n"
    p.write_text(fm + "---\n\n" + BODY, encoding="utf-8")


class _Vault(unittest.TestCase):
    def setUp(self):
        self.vault = Path(tempfile.mkdtemp(prefix="recall-project-"))
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)
        (self.vault / "memory" / "semantic").mkdir(parents=True)

    def _rows(self, **kw):
        return recall.query(vault=self.vault, query_text=QUERY, k=5, **kw)

    def _paths(self, **kw):
        return [r["path"] for r in self._rows(**kw)]


class TheInProcessArm(_Vault):
    def setUp(self):
        super().setUp()
        # The unmatched twins sort first by path, so a tie would put them on top:
        # the order below is the project, not the tiebreak.
        _note(self.vault, "memory/semantic/a-crickets.md", "crickets")
        _note(self.vault, "memory/semantic/b-unstamped.md")
        _note(self.vault, "memory/semantic/c-agentm.md", "agentm")

    def test_a_matching_card_ranks_above_its_equal_twins(self):
        self.assertEqual(self._paths(project="agentm")[0], "memory/semantic/c-agentm.md")

    def test_no_project_ranks_as_before(self):
        self.assertEqual(self._paths(), self._paths(project=None))
        self.assertEqual(self._paths()[0], "memory/semantic/a-crickets.md")

    def test_the_lift_is_the_mild_dampening_of_the_rest(self):
        # The fused base is by rank, so each card is compared with itself: the
        # same query without a project leaves the matching card as it was and
        # multiplies every other card by the mismatch weight.
        with_project = {r["path"]: r["combined"] for r in self._rows(project="agentm")}
        without = {r["path"]: r["combined"] for r in self._rows()}
        self.assertAlmostEqual(with_project["memory/semantic/c-agentm.md"],
                               without["memory/semantic/c-agentm.md"])
        for path in ("memory/semantic/a-crickets.md", "memory/semantic/b-unstamped.md"):
            self.assertAlmostEqual(with_project[path], without[path] * recall._PROJECT_MISMATCH)

    def test_a_project_matches_without_regard_to_case_or_quotes(self):
        _note(self.vault, "memory/semantic/d-quoted.md", '"AgentM"')
        top = self._paths(project="agentm")[:2]
        self.assertEqual(sorted(top), ["memory/semantic/c-agentm.md", "memory/semantic/d-quoted.md"])

    def test_the_mismatch_weight_mirrors_the_daemons(self):
        daemon = Path(__file__).resolve().parent.parent / "daemon" / "internal" / "note" / "classify.go"
        self.assertIn(f"ProjectMismatch = {recall._PROJECT_MISMATCH:.2f}", daemon.read_text(encoding="utf-8"))


class TheDaemonPath(_Vault):
    def _argv_seen(self, returns=None, **kw):
        seen = []
        returns = list(returns or [])

        def fake_run(argv, **_kw):
            seen.append(list(argv))
            if returns:
                return returns.pop(0)
            # `always_load_hidden` rides on every current daemon response, zero or
            # not: its presence is how the hook knows the always-load drop has
            # already happened, so a fake without it spends a second subprocess
            # reading the contract and the argv count stops meaning what this
            # test reads it to mean.
            return subprocess.CompletedProcess(
                argv, 0,
                stdout=json.dumps({"results": [], "always_load_hidden": 0}), stderr="")

        with mock.patch.object(recall.subprocess, "run", side_effect=fake_run):
            out = recall._daemon_search(vault=self.vault, query_text="release gate checks", k=5, drops={}, **kw)
        return seen, out

    def test_the_project_reaches_the_daemon(self):
        seen, _ = self._argv_seen(project="agentm")
        argv = seen[0]
        self.assertIn("-project", argv)
        self.assertEqual(argv[argv.index("-project") + 1], "agentm")
        self.assertLess(argv.index("-project"), len(argv) - 1, "the flag goes before the search terms")

    def test_no_project_sends_no_flag(self):
        seen, _ = self._argv_seen()
        self.assertNotIn("-project", seen[0])

    def test_a_binary_that_predates_the_flag_is_asked_again_without_it(self):
        refused = subprocess.CompletedProcess(
            [], 2, stdout="", stderr="flag provided but not defined: -project\nUsage of search:")
        seen, out = self._argv_seen(returns=[refused], project="agentm")
        self.assertEqual(len(seen), 2)
        self.assertIn("-project", seen[0])
        self.assertNotIn("-project", seen[1])
        self.assertEqual(out, [])

    def test_any_other_failure_is_not_retried(self):
        broken = subprocess.CompletedProcess([], 1, stdout="", stderr="index is locked")
        seen, out = self._argv_seen(returns=[broken], project="agentm")
        self.assertEqual(len(seen), 1)
        self.assertIsNone(out)


class ThePromptHookReadsTheSessionDirectory(unittest.TestCase):
    def test_the_payload_carries_the_prompt_and_the_directory(self):
        payload = io.StringIO(json.dumps({"prompt": "what is next", "cwd": "/repo"}))
        self.assertEqual(recall._read_prompt_payload(payload), ("what is next", "/repo"))

    def test_a_payload_without_a_directory_binds_nothing(self):
        self.assertEqual(recall._read_prompt_payload(io.StringIO(json.dumps({"prompt": "x"}))), ("x", None))
        self.assertEqual(recall._read_prompt_payload(io.StringIO("not json")), (None, None))

    def test_the_old_reader_still_returns_the_prompt_alone(self):
        self.assertEqual(recall._read_prompt_from_stdin(io.StringIO(json.dumps({"prompt": "x", "cwd": "/r"}))), "x")


if __name__ == "__main__":
    unittest.main()
