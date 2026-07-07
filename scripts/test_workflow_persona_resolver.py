#!/usr/bin/env python3
"""Tests for workflow_persona_resolver.py (PLAN-wave-d-personas task 3).

One subTest per wired phase command (plan/work/review/bugfix) asserting the
correct persona resolves via workflow-step adoption when no explicit
invocation is given, plus explicit-invocation-wins coverage and the CLI's
exit-code contract.
"""
from __future__ import annotations

import contextlib
import io
import unittest

import workflow_persona_resolver as wpr


class TestResolveWorkflowPersona(unittest.TestCase):
    _WIRED = (
        ("plan-phase", "tech-lead"),
        ("work-phase", "engineer"),
        ("review-phase", "reviewer"),
        ("bugfix-phase", "troubleshooter"),
    )

    def test_each_wired_phase_command_resolves_its_persona(self):
        for step, expected in self._WIRED:
            with self.subTest(step=step):
                self.assertEqual(wpr.resolve_workflow_persona(step), expected)

    def test_unknown_step_with_no_explicit_returns_none(self):
        self.assertIsNone(wpr.resolve_workflow_persona("no-such-phase"))

    def test_explicit_invocation_overrides_the_workflow_step_default(self):
        for step, default in self._WIRED:
            with self.subTest(step=step):
                self.assertEqual(
                    wpr.resolve_workflow_persona(step, explicit="architect"),
                    "architect",
                )
                self.assertNotEqual(
                    wpr.resolve_workflow_persona(step, explicit="architect"),
                    default,
                )

    def test_explicit_invocation_wins_even_for_an_unknown_step(self):
        self.assertEqual(
            wpr.resolve_workflow_persona("no-such-phase", explicit="researcher"),
            "researcher",
        )


class TestMainCLI(unittest.TestCase):
    def _run(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = wpr.main(["workflow_persona_resolver.py"] + argv)
        return out.getvalue().strip(), rc

    def test_wired_step_prints_persona_exit_zero(self):
        out, rc = self._run(["plan-phase"])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "tech-lead")

    def test_unknown_step_no_output_exit_one(self):
        out, rc = self._run(["no-such-phase"])
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")

    def test_explicit_flag_overrides_and_exits_zero(self):
        out, rc = self._run(["work-phase", "--explicit", "architect"])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "architect")

    def test_no_step_no_explicit_exits_two(self):
        out, rc = self._run([])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
