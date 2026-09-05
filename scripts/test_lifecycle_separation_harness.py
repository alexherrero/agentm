#!/usr/bin/env python3
"""The lifecycle-separation harness runs against the shipped daemon and reads
what it ranked: every dormant twin below its active twin, every archived twin
hidden until asked for, and the control seeing plain path order. Skipped when
no daemon binary is at hand ($AGENTMD, as the battery exports it)."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE / "health") not in sys.path:
    sys.path.insert(0, str(_HERE / "health"))

import eval_lifecycle_separation as els  # noqa: E402

_BIN = os.environ.get("AGENTMD", "")


class TheSignTest(unittest.TestCase):
    def test_exact_two_sided_binomial(self):
        self.assertAlmostEqual(els.sign_test_two_sided(6, 6), 2 / 64)
        self.assertAlmostEqual(els.sign_test_two_sided(0, 6), 2 / 64)
        self.assertAlmostEqual(els.sign_test_two_sided(3, 6), 1.0)
        self.assertEqual(els.sign_test_two_sided(0, 0), 1.0)


@unittest.skipUnless(_BIN and Path(_BIN).exists(), "needs the daemon binary in $AGENTMD")
class TheMeasurement(unittest.TestCase):
    def test_the_axis_separates_the_twins_and_the_control_does_not(self):
        out = els.measure(_BIN, pairs=6, archived=3)
        m, c = out["measured"], out["control"]
        self.assertEqual((m["dormant_below_active"], m["ties"], m["missing"]), (6, 0, 0), m)
        self.assertLess(m["p_two_sided"], 0.05)
        self.assertEqual(m["archived_hidden_everyday"], 3, m)
        self.assertEqual(m["archived_back_on_explicit"], 3, m)
        self.assertEqual(m["archived_below_active_on_explicit"], 3, m)
        self.assertEqual(c["a_first"], 6, "with the axis removed the a-twin must win the path tiebreak")
        self.assertEqual(c["archived_hidden_everyday"], 0, c)
        self.assertTrue(out["pass"], out["verdict"])
        self.assertIn("verdict", els.render(out))


if __name__ == "__main__":
    unittest.main()
