#!/usr/bin/env python3
"""Unit tests for scripts/capability_version_match.py — stdlib unittest.

Covers Task 2 verification (version-range ⊨ version truth-table):
  - in-range, out-of-range, no-range/None, malformed inputs
  - all supported operators: >=, >, <=, <, ==, !=, ~=
  - version tuple padding (1.2 vs 1.2.0)
  - no transitive / solver behavior (single range only)
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from capability_version_match import satisfies  # noqa: E402


class TestSatisfiesGE(unittest.TestCase):
    """Operator >= (most common in enhances:)."""

    def test_equal_satisfies(self):
        self.assertTrue(satisfies("1.2.0", ">= 1.2"))

    def test_greater_satisfies(self):
        self.assertTrue(satisfies("1.5.0", ">= 1.2"))

    def test_greater_major_satisfies(self):
        self.assertTrue(satisfies("2.0.0", ">= 1.2"))

    def test_lesser_fails(self):
        self.assertFalse(satisfies("1.1.0", ">= 1.2"))

    def test_lesser_patch_fails(self):
        self.assertFalse(satisfies("1.2.0", ">= 1.2.1"))

    def test_patch_padding(self):
        self.assertTrue(satisfies("1.2", ">= 1.2.0"))


class TestSatisfiesGT(unittest.TestCase):
    def test_strictly_greater_satisfies(self):
        self.assertTrue(satisfies("1.3.0", "> 1.2"))

    def test_equal_fails(self):
        self.assertFalse(satisfies("1.2.0", "> 1.2"))

    def test_lesser_fails(self):
        self.assertFalse(satisfies("1.1.0", "> 1.2"))


class TestSatisfiesLE(unittest.TestCase):
    def test_equal_satisfies(self):
        self.assertTrue(satisfies("1.2.0", "<= 1.2"))

    def test_lesser_satisfies(self):
        self.assertTrue(satisfies("1.1.0", "<= 1.2"))

    def test_greater_fails(self):
        self.assertFalse(satisfies("1.3.0", "<= 1.2"))


class TestSatisfiesLT(unittest.TestCase):
    def test_strictly_lesser_satisfies(self):
        self.assertTrue(satisfies("1.1.0", "< 1.2"))

    def test_equal_fails(self):
        self.assertFalse(satisfies("1.2.0", "< 1.2"))

    def test_greater_fails(self):
        self.assertFalse(satisfies("1.3.0", "< 1.2"))


class TestSatisfiesEQ(unittest.TestCase):
    def test_exact_match_satisfies(self):
        self.assertTrue(satisfies("1.2.3", "== 1.2.3"))

    def test_minor_differs_fails(self):
        self.assertFalse(satisfies("1.2.4", "== 1.2.3"))

    def test_major_differs_fails(self):
        self.assertFalse(satisfies("2.0.0", "== 1.2.3"))

    def test_padded_patch_matches(self):
        self.assertTrue(satisfies("1.2.0", "== 1.2"))


class TestSatisfiesNE(unittest.TestCase):
    def test_different_satisfies(self):
        self.assertTrue(satisfies("1.3.0", "!= 1.2"))

    def test_equal_fails(self):
        self.assertFalse(satisfies("1.2.0", "!= 1.2"))


class TestSatisfiesCompatible(unittest.TestCase):
    """Operator ~= (compatible release)."""

    def test_exact_boundary_satisfies(self):
        # ~= 1.2 means >= 1.2 AND < 2
        self.assertTrue(satisfies("1.2.0", "~= 1.2"))

    def test_within_range_satisfies(self):
        self.assertTrue(satisfies("1.9.9", "~= 1.2"))

    def test_above_upper_fails(self):
        self.assertFalse(satisfies("2.0.0", "~= 1.2"))

    def test_below_lower_fails(self):
        self.assertFalse(satisfies("1.1.0", "~= 1.2"))

    def test_three_component_range(self):
        # ~= 1.2.3 means >= 1.2.3 AND < 1.3
        self.assertTrue(satisfies("1.2.5", "~= 1.2.3"))
        self.assertFalse(satisfies("1.3.0", "~= 1.2.3"))
        self.assertFalse(satisfies("1.2.2", "~= 1.2.3"))


class TestGracefulDegrade(unittest.TestCase):
    """Malformed / absent inputs → False, never raise (LC-4)."""

    def test_none_installed_version(self):
        self.assertFalse(satisfies(None, ">= 1.0"))

    def test_empty_installed_version(self):
        self.assertFalse(satisfies("", ">= 1.0"))

    def test_empty_range_str(self):
        self.assertFalse(satisfies("1.0.0", ""))

    def test_non_numeric_installed_version(self):
        self.assertFalse(satisfies("not-a-version", ">= 1.0"))

    def test_malformed_range_no_operator(self):
        self.assertFalse(satisfies("1.0.0", "1.2.3"))

    def test_malformed_range_bad_version(self):
        self.assertFalse(satisfies("1.0.0", ">= abc"))

    def test_malformed_range_garbage(self):
        self.assertFalse(satisfies("1.0.0", "completely broken range"))

    def test_none_installed_never_raises(self):
        try:
            result = satisfies(None, ">= 1.0")
        except Exception as exc:  # noqa: BLE001
            self.fail(f"satisfies raised with None installed: {exc}")
        self.assertFalse(result)

    def test_tilde_single_component_is_invalid(self):
        # ~= 1 (single component) is invalid per PEP 440
        self.assertFalse(satisfies("1.0.0", "~= 1"))


class TestNoSolverBehavior(unittest.TestCase):
    """Verify single-range check — no transitive / compound solver semantics."""

    def test_single_range_only_not_compound(self):
        # ">= 1.0, < 2.0" is NOT a supported format — only one operator per call.
        # The function should return False (parse failure) rather than trying to
        # interpret compound specifiers.
        result = satisfies("1.5.0", ">= 1.0, < 2.0")
        self.assertFalse(result)

    def test_version_padding_consistency(self):
        # 1.2 and 1.2.0 and 1.2.0.0 should all compare as equal.
        self.assertTrue(satisfies("1.2", "== 1.2.0"))
        self.assertTrue(satisfies("1.2.0", "== 1.2"))


class TestIntegrationWithResolver(unittest.TestCase):
    """Verify that capability_resolver correctly routes to satisfies (Task 2)."""

    def setUp(self):
        # Put the scripts dir on sys.path so resolver can import version_match.
        if str(_HERE) not in sys.path:
            sys.path.insert(0, str(_HERE))

    def test_version_mismatch_reason(self):
        import capability_resolver as cr
        reg = {"cap": cr.ProviderEntry("provider", "1.0.0", True)}
        result = cr.capability_resolve("cap", version=">= 2.0", registry=reg)
        self.assertEqual(result["reason"], "version-mismatch")
        self.assertFalse(result["available"])

    def test_version_satisfied_available(self):
        import capability_resolver as cr
        reg = {"cap": cr.ProviderEntry("provider", "1.5.0", True)}
        result = cr.capability_resolve("cap", version=">= 1.2", registry=reg)
        self.assertEqual(result["reason"], "available")
        self.assertTrue(result["available"])

    def test_none_version_skips_check(self):
        import capability_resolver as cr
        reg = {"cap": cr.ProviderEntry("provider", "1.0.0", True)}
        result = cr.capability_resolve("cap", version=None, registry=reg)
        self.assertEqual(result["reason"], "available")
        self.assertTrue(result["available"])


if __name__ == "__main__":
    import unittest
    unittest.main()
