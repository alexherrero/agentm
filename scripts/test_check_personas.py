#!/usr/bin/env python3
"""Unit tests for check-personas.py.

Covers Task 2 verification:
  - Valid persona (brain-like, empty requires) passes.
  - Persona with a non-substrate requires: entry is REJECTED.
  - Persona with always_load: true is REJECTED.
  - Real tree (the actual personas/ directory) passes.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# Load the gate module (hyphen in filename → importlib).
_SPEC = importlib.util.spec_from_file_location(
    "check_personas",
    _HERE / "check-personas.py",
)
assert _SPEC and _SPEC.loader
_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_mod)  # type: ignore[union-attr]
_main = _mod._main


def _make_root(tmp: str, personas: dict[str, str],
               scripts: list[str] | None = None) -> str:
    """Write a fixture repo root under tmp.

    personas: {filename: content} written under <root>/personas/
    scripts:  list of stem names; each gets a stub .py under <root>/scripts/
              (defaults to ["harness_memory", "queue_status_lite"] if None)
    """
    root = Path(tmp)
    personas_dir = root / "personas"
    personas_dir.mkdir(parents=True)
    for name, content in personas.items():
        (personas_dir / name).write_text(content, encoding="utf-8")

    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    stubs = scripts if scripts is not None else ["harness_memory", "queue_status_lite"]
    for stem in stubs:
        (scripts_dir / f"{stem}.py").write_text("# stub\n", encoding="utf-8")

    return tmp


_BRAIN_LIKE = """\
---
kind: persona
name: brain
requires: []
enhances: []
---

Degenerate persona — no hard deps, no composed capabilities.
"""

_NON_SUBSTRATE_REQUIRE = """\
---
kind: persona
name: bad-persona
requires:
  - developer-workflows
enhances: []
---

This persona illegally requires a crickets capability.
"""

_ALWAYS_LOAD_PERSONA = """\
---
kind: persona
name: bad-always-load
requires: []
enhances: []
always_load: true
---

This persona illegally declares always_load.
"""

_ALWAYS_LOAD_HYPHEN = """\
---
kind: persona
name: bad-always-load-hyphen
requires: []
enhances: []
always-load: true
---

Hyphenated form of always-load — also rejected.
"""

_VALID_WITH_REQUIRES = """\
---
kind: persona
name: valid-with-requires
requires:
  - harness_memory
  - queue_status_lite
enhances: []
---

Valid: requires entries exist in scripts/ as .py files.
"""


class TestPass(unittest.TestCase):

    def test_brain_like_passes(self):
        """A persona with empty requires and no always-load passes."""
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t, {"brain.md": _BRAIN_LIKE})
            rc = _main(["check-personas.py", "--root", root])
        self.assertEqual(rc, 0)

    def test_valid_substrate_requires_passes(self):
        """A persona whose requires: entries exist in scripts/ passes."""
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t, {"valid.md": _VALID_WITH_REQUIRES})
            rc = _main(["check-personas.py", "--root", root])
        self.assertEqual(rc, 0)

    def test_no_personas_dir_passes(self):
        """A root without a personas/ directory passes (nothing to check)."""
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "scripts").mkdir()
            rc = _main(["check-personas.py", "--root", t])
        self.assertEqual(rc, 0)

    def test_empty_personas_dir_passes(self):
        """An empty personas/ directory passes."""
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t, {})
            rc = _main(["check-personas.py", "--root", root])
        self.assertEqual(rc, 0)

    def test_real_tree_passes(self):
        """The actual personas/ directory in the repo must pass."""
        repo_root = str(_HERE.parent)
        rc = _main(["check-personas.py", "--root", repo_root])
        self.assertEqual(rc, 0,
                         "Real personas/ directory failed check-personas gate — "
                         "check personas/*.md for invalid requires: or always_load.")

    def test_real_manifests_carry_all_four_activation_axes(self):
        """agentm-persona-activation.md:135 named this 'still open': neither
        brain.md nor team-coordinator.md carried tier:/opinions:/modes:/
        triggers: before the Wave D retrofit. Assert the axes are present
        AND non-empty on both real manifests (a gate-vacuous pass — e.g. an
        absent field, which the shape-only gate accepts — would not catch
        the regression this test guards against)."""
        repo_root = _HERE.parent
        for name in ("brain", "team-coordinator"):
            path = repo_root / "personas" / f"{name}.md"
            fm = _mod._parse_frontmatter(path)
            self.assertIsNotNone(fm, f"{name}.md: unparseable frontmatter")
            self.assertIn("tier", fm, f"{name}.md: missing tier:")
            self.assertTrue(fm["tier"], f"{name}.md: tier: is empty")
            self.assertIn("modes", fm, f"{name}.md: missing modes:")
            self.assertTrue(fm["modes"], f"{name}.md: modes: is empty")
            # opinions: and triggers: may legitimately be an empty list
            # (brain leans on no Opinion and answers no trigger — it composes
            # automatically beneath every adoption rather than being adopted
            # itself) — assert *presence* (the field is declared), not
            # non-emptiness, for these two.
            self.assertIn("opinions", fm, f"{name}.md: missing opinions:")
            self.assertIn("triggers", fm, f"{name}.md: missing triggers:")


class TestRejectNonSubstrateRequires(unittest.TestCase):
    """Acceptance criterion (a): reject a persona whose requires names a non-substrate capability."""

    def test_crickets_capability_in_requires_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t, {"bad.md": _NON_SUBSTRATE_REQUIRE})
            rc = _main(["check-personas.py", "--root", root])
        self.assertEqual(rc, 1,
                         "Persona with non-substrate requires: should be rejected (exit 1)")

    def test_unknown_name_not_in_scripts_rejected(self):
        """A requires: entry with no matching scripts/<stem>.py or .sh is rejected."""
        persona = """\
---
kind: persona
name: test-persona
requires:
  - some-unknown-plugin
enhances: []
---
"""
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t, {"p.md": persona})
            rc = _main(["check-personas.py", "--root", root])
        self.assertEqual(rc, 1)


class TestRejectAlwaysLoad(unittest.TestCase):
    """Acceptance criterion (b): reject a persona declaring always_load."""

    def test_always_load_underscore_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t, {"bad.md": _ALWAYS_LOAD_PERSONA})
            rc = _main(["check-personas.py", "--root", root])
        self.assertEqual(rc, 1,
                         "Persona with always_load: true should be rejected (exit 1)")

    def test_always_load_hyphen_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t, {"bad.md": _ALWAYS_LOAD_HYPHEN})
            rc = _main(["check-personas.py", "--root", root])
        self.assertEqual(rc, 1,
                         "Persona with always-load: true should be rejected (exit 1)")


class TestMiscValidation(unittest.TestCase):

    def test_wrong_kind_rejected(self):
        persona = """\
---
kind: skill
name: wrong-kind
requires: []
enhances: []
---
"""
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t, {"p.md": persona})
            rc = _main(["check-personas.py", "--root", root])
        self.assertEqual(rc, 1)

    def test_requires_sh_stem_accepted(self):
        """A requires: entry matching a .sh file (not just .py) is valid."""
        persona = """\
---
kind: persona
name: uses-sh
requires:
  - my-shell-script
enhances: []
---
"""
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "personas").mkdir()
            (root / "personas" / "p.md").write_text(persona, encoding="utf-8")
            scripts = root / "scripts"
            scripts.mkdir()
            (scripts / "my-shell-script.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            rc = _main(["check-personas.py", "--root", str(root)])
        self.assertEqual(rc, 0)


class TestActivationAxes(unittest.TestCase):
    """agentm-persona-activation.md's four new manifest axes — shape only."""

    def _persona(self, extra_frontmatter: str) -> str:
        return (
            "---\nkind: persona\nname: p\nrequires: []\nenhances: []\n"
            + extra_frontmatter + "\n---\nstance\n"
        )

    def test_valid_tier_passes(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t, {"p.md": self._persona("tier: T2")})
            self.assertEqual(_main(["check-personas.py", "--root", root]), 0)

    def test_invalid_tier_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t, {"p.md": self._persona("tier: T9")})
            self.assertEqual(_main(["check-personas.py", "--root", root]), 1)

    def test_valid_modes_passes(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t, {"p.md": self._persona("modes: [sub-agent, interactive]")})
            self.assertEqual(_main(["check-personas.py", "--root", root]), 0)

    def test_invalid_mode_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t, {"p.md": self._persona("modes: [not-a-real-mode]")})
            self.assertEqual(_main(["check-personas.py", "--root", root]), 1)

    def test_empty_modes_list_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t, {"p.md": self._persona("modes: []")})
            self.assertEqual(_main(["check-personas.py", "--root", root]), 1)

    def test_valid_triggers_passes(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t, {"p.md": self._persona("triggers: [review-phase]")})
            self.assertEqual(_main(["check-personas.py", "--root", root]), 0)

    def test_empty_string_trigger_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t, {"p.md": self._persona('triggers: [""]')})
            self.assertEqual(_main(["check-personas.py", "--root", root]), 1)

    def test_valid_opinions_passes(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t, {"p.md": self._persona("opinions: [good, done]")})
            self.assertEqual(_main(["check-personas.py", "--root", root]), 0)

    def test_opinions_not_a_list_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t, {"p.md": self._persona("opinions: good")})
            self.assertEqual(_main(["check-personas.py", "--root", root]), 1)

    def test_opinions_shape_only_unresolvable_name_still_passes(self):
        """An opinions: entry that names no real opinions/*.md entry is NOT
        this gate's concern (resolution stays a runtime, graceful check) —
        only shape (list of non-empty strings) is enforced here."""
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t, {"p.md": self._persona("opinions: [nonexistent-opinion-name]")})
            self.assertEqual(_main(["check-personas.py", "--root", root]), 0)

    def test_all_four_axes_together_passes(self):
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t, {"p.md": self._persona(
                "tier: T1\nmodes: [sub-agent, loop]\n"
                "triggers: [review-phase]\nopinions: [good]"
            )})
            self.assertEqual(_main(["check-personas.py", "--root", root]), 0)

    def test_axes_absent_still_passes(self):
        """None of the four axes is required — a persona with only the
        three built fields still passes (backward compatible)."""
        with tempfile.TemporaryDirectory() as t:
            root = _make_root(t, {"p.md": self._persona("")})
            self.assertEqual(_main(["check-personas.py", "--root", root]), 0)


if __name__ == "__main__":
    unittest.main()
