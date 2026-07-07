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
        # brain.md is real and always present per test_check_personas.py.
        result = pr.adopt("brain", "interactive", root=repo_root)
        self.assertTrue(result["adopted"], result["violations"])


class TestRealRosterAdoptsInEachDeclaredMode(unittest.TestCase):
    """Wave D persona-roster manifests (PLAN-wave-d-personas task 2) — each
    of the 9 new manifests adopts cleanly through at least one of its own
    declared `modes:` entries, gates on tier, loads, resolves bindings, and
    composes without error. Exercises the real repo tree, not a fixture —
    a regression here means a shipped manifest stopped adopting."""

    _ROSTER = (
        "architect", "designer", "tech-lead", "engineer", "reviewer",
        "operator", "troubleshooter", "researcher", "maintainer",
    )

    def test_each_new_persona_adopts_via_its_own_first_declared_mode(self):
        repo_root = Path(__file__).resolve().parent.parent
        check_personas = pr._load_check_personas()
        for name in self._ROSTER:
            with self.subTest(persona=name):
                manifest_path = repo_root / "personas" / f"{name}.md"
                fm = check_personas._parse_frontmatter(manifest_path)
                self.assertIsNotNone(fm, f"{name}.md: unparseable frontmatter")
                modes = fm.get("modes") or []
                self.assertTrue(modes, f"{name}.md: modes: must be non-empty")
                mode = modes[0]

                result = pr.adopt(name, mode, root=repo_root)

                self.assertTrue(result["adopted"], (name, result["violations"]))
                self.assertEqual(result["reason"], "adopted")
                self.assertEqual(result["mode"], mode)
                self.assertTrue(result["stance"], f"{name}.md: empty stance body")
                # tier: is declared on every new-roster manifest -> the tier
                # binding must resolve (never None for a valid T0-T4 value).
                self.assertIsNotNone(
                    result["tier_binding"],
                    f"{name}.md: tier: declared but resolve_tier() returned None",
                )
                self.assertIn("model", result["tier_binding"])
                self.assertIn("effort", result["tier_binding"])
                # opinions: — every declared opinion gets a binding entry
                # (degrade-gracefully is fine; absence of the key is not).
                for opinion in (fm.get("opinions") or []):
                    self.assertIn(opinion, result["opinion_bindings"])
                # enhances: — every declared capability gets a binding entry.
                for cap in (fm.get("enhances") or []):
                    self.assertIn(cap, result["capability_bindings"])

    def test_reviewer_is_sub_agent_only_and_adopts_cold(self):
        """The reviewer's one locked mode (agentm-persona-activation.md:
        'The Reviewer runs cold on the sub-agent path, for adversarial
        independence') adopts cleanly and declares no other mode."""
        repo_root = Path(__file__).resolve().parent.parent
        check_personas = pr._load_check_personas()
        fm = check_personas._parse_frontmatter(repo_root / "personas" / "reviewer.md")
        self.assertEqual(fm.get("modes"), ["sub-agent"])
        result = pr.adopt("reviewer", "sub-agent", root=repo_root)
        self.assertTrue(result["adopted"])


if __name__ == "__main__":
    unittest.main()
