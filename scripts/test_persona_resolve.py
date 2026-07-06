#!/usr/bin/env python3
"""Unit tests for persona_resolve.py's adopt() pipeline (agentm-persona-
activation.md, AG Wave B leader 4/5).

Mirrors test_opinion_resolver.py's never-raise shape for each of the three
resolver calls adopt() makes (tier, opinions, capabilities).
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import persona_resolve as pr


def _write_persona(root: Path, name: str, frontmatter_extra: str = "",
                    body: str = "The stance.") -> Path:
    d = root / "personas"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.md"
    p.write_text(
        f"---\nkind: persona\nname: {name}\nrequires: []\nenhances: []\n"
        + frontmatter_extra + f"\n---\n{body}\n",
        encoding="utf-8",
    )
    return p


def _write_opinion(root: Path, name: str, body: str = "the standard") -> None:
    d = root / "opinions"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(f"---\nname: {name}\nkind: opinion\n---\n{body}", encoding="utf-8")


def _make_root(tmp: str) -> Path:
    root = Path(tmp)
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    return root


class TestResolveTier(unittest.TestCase):
    def test_known_tiers_resolve(self):
        for tier in ("T0", "T1", "T2", "T3", "T4"):
            result = pr.resolve_tier(tier)
            self.assertIsNotNone(result)
            self.assertIn("model", result)
            self.assertIn("effort", result)

    def test_unknown_tier_returns_none(self):
        self.assertIsNone(pr.resolve_tier("T9"))


class TestAdopt(unittest.TestCase):
    def test_not_found_never_raises(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t)
            result = pr.adopt("nonexistent-persona", "sub-agent", root=root)
            self.assertFalse(result["adopted"])
            self.assertEqual(result["reason"], "not-found")

    def test_gate_failing_manifest_is_never_adopted(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t)
            _write_persona(root, "bad", frontmatter_extra="always_load: true")
            result = pr.adopt("bad", "sub-agent", root=root)
            self.assertFalse(result["adopted"])
            self.assertEqual(result["reason"], "gate-failed")
            self.assertTrue(result["violations"])
            # No bindings resolved for a gate-failing manifest.
            self.assertEqual(result["opinion_bindings"], {})
            self.assertEqual(result["capability_bindings"], {})

    def test_valid_persona_adopts_with_stance(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t)
            _write_persona(root, "reviewer", body="Adversarial critic stance.")
            result = pr.adopt("reviewer", "sub-agent", root=root)
            self.assertTrue(result["adopted"])
            self.assertEqual(result["reason"], "adopted")
            self.assertEqual(result["stance"], "Adversarial critic stance.")
            self.assertEqual(result["mode"], "sub-agent")

    def test_tier_binding_resolves(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t)
            _write_persona(root, "p", frontmatter_extra="tier: T2")
            result = pr.adopt("p", "interactive", root=root)
            self.assertTrue(result["adopted"])
            self.assertEqual(result["tier_binding"], {"model": "strongest", "effort": "high"})

    def test_no_tier_declared_binding_is_none(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t)
            _write_persona(root, "p")
            result = pr.adopt("p", "interactive", root=root)
            self.assertIsNone(result["tier_binding"])

    def test_opinions_binding_resolves_served_or_base_only(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t)
            _write_opinion(root, "good")
            _write_persona(root, "reviewer", frontmatter_extra="opinions: [good]")
            result = pr.adopt("reviewer", "sub-agent", root=root)
            self.assertTrue(result["adopted"])
            self.assertIn("good", result["opinion_bindings"])
            self.assertIn(result["opinion_bindings"]["good"]["reason"], ("served", "base-only"))

    def test_opinions_binding_degrades_gracefully_for_unknown_name(self):
        """A persona declaring an opinion with no real opinions/*.md entry
        still adopts — the binding degrades to no-opinion, it never blocks
        adoption (the resolver never raises, and shape-only gate validation
        already let this through)."""
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t)
            _write_persona(root, "p", frontmatter_extra="opinions: [nonexistent]")
            result = pr.adopt("p", "sub-agent", root=root)
            self.assertTrue(result["adopted"])
            self.assertEqual(result["opinion_bindings"]["nonexistent"]["reason"], "no-opinion")

    def test_enhances_binding_resolves_through_capability_resolver(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t)
            _write_persona(root, "p")
            # Overwrite with a real enhances: entry (no fixture capability
            # registry exists, so this exercises the graceful-degrade path —
            # exactly the "absent -> quiet absence" behavior the design calls for).
            (root / "personas" / "p.md").write_text(
                "---\nkind: persona\nname: p\nrequires: []\n"
                "enhances: [some-capability]\n---\nstance\n",
                encoding="utf-8",
            )
            result = pr.adopt("p", "sub-agent", root=root)
            self.assertTrue(result["adopted"])
            self.assertIn("some-capability", result["capability_bindings"])
            self.assertFalse(result["capability_bindings"]["some-capability"]["available"])

    def test_requires_is_not_double_resolved_as_a_capability(self):
        """requires: is validated by the gate (substrate-script existence);
        it is deliberately NOT re-resolved through capability_resolver —
        that would look up an agentm script stem in the crickets-plugin
        capability registry, which is a category error."""
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t)
            (root / "scripts" / "harness_memory.py").write_text("# stub\n", encoding="utf-8")
            (root / "personas" / "p.md").parent.mkdir(parents=True, exist_ok=True)
            (root / "personas" / "p.md").write_text(
                "---\nkind: persona\nname: p\nrequires: [harness_memory]\n"
                "enhances: []\n---\nstance\n",
                encoding="utf-8",
            )
            result = pr.adopt("p", "sub-agent", root=root)
            self.assertTrue(result["adopted"])
            self.assertEqual(result["capability_bindings"], {})

    def test_real_reviewer_persona_would_adopt_if_it_existed(self):
        """Sanity check against the real repo tree — a manifest with no
        activation axes declared still adopts cleanly (backward compat)."""
        repo_root = Path(__file__).resolve().parent.parent
        # rememberer.md is real and always present per test_check_personas.py.
        result = pr.adopt("rememberer", "interactive", root=repo_root)
        self.assertTrue(result["adopted"], result["violations"])


if __name__ == "__main__":
    unittest.main()
