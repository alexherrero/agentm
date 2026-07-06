#!/usr/bin/env python3
"""Unit tests for check-opinion-honesty.py — the no-orphan-Opinion-reference lint
(agentm-opinion-registry.md Enforcement §3)."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

_SPEC = importlib.util.spec_from_file_location(
    "check_opinion_honesty", _HERE / "check-opinion-honesty.py",
)
assert _SPEC and _SPEC.loader
_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_mod)  # type: ignore[union-attr]


def _write_opinion(root: Path, name: str) -> None:
    d = root / "opinions"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(f"---\nname: {name}\nkind: opinion\n---\nbody", encoding="utf-8")


class TestCheckOpinionHonesty(unittest.TestCase):
    def test_clean_when_nothing_declares_an_opinion(self):
        with TemporaryDirectory() as t:
            root = Path(t)
            _write_opinion(root, "good")
            self.assertEqual(_mod.main(["--root", str(root)]), 0)

    def test_persona_referencing_a_real_opinion_is_clean(self):
        with TemporaryDirectory() as t:
            root = Path(t)
            _write_opinion(root, "good")
            personas = root / "personas"
            personas.mkdir()
            (personas / "reviewer.md").write_text(
                "---\nkind: persona\nname: reviewer\nopinions: [good]\n---\nstance",
                encoding="utf-8",
            )
            self.assertEqual(_mod.main(["--root", str(root)]), 0)

    def test_persona_referencing_an_orphan_opinion_fails(self):
        with TemporaryDirectory() as t:
            root = Path(t)
            _write_opinion(root, "good")
            personas = root / "personas"
            personas.mkdir()
            (personas / "reviewer.md").write_text(
                "---\nkind: persona\nname: reviewer\nopinions: [nonexistent]\n---\nstance",
                encoding="utf-8",
            )
            self.assertEqual(_mod.main(["--root", str(root)]), 1)

    def test_missing_personas_dir_is_not_an_error(self):
        with TemporaryDirectory() as t:
            root = Path(t)
            _write_opinion(root, "good")
            self.assertEqual(_mod.main(["--root", str(root)]), 0)


if __name__ == "__main__":
    unittest.main()
