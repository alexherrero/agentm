#!/usr/bin/env python3
"""Tests for scripts/check-governs-index.py (R0.10 / agTrack#0, agentmDesigns#1).

Before the fix landed, `governs_resolver.py --json scripts/harness_memory.py`
and `--json scripts/check-personas.py` both returned
`{"governed": false, "reason": "overlap"}` — two design docs each stamped the
same exact `governs:` pattern. Confirms both now resolve cleanly on the real
repo, that `check-governs-index.py` exits 0 there, and that it correctly
fails on a synthetic overlap / unknown-area fixture.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import governs_resolver as gr  # noqa: E402

# Load the gate module via importlib (hyphen in filename prevents direct import).
_SPEC = importlib.util.spec_from_file_location(
    "check_governs_index", _HERE / "check-governs-index.py",
)
assert _SPEC and _SPEC.loader
_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_mod)  # type: ignore[union-attr]
_main = _mod.main


def _write_design(root: Path, name: str, *, status: str = "launched",
                   area: str | None = None, governs: list[str] | None = None) -> Path:
    designs = root / "wiki" / "designs"
    designs.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"title: {name}", f"status: {status}", "kind: design", "scope: arc"]
    if area is not None:
        lines.append(f"area: {area}")
    if governs is not None:
        lines.append("governs:")
        for g in governs:
            lines.append(f"  - {g}")
    lines += ["---", "", f"# {name}", "body"]
    p = designs / name
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


class TestRealRepoGovernsOverlapFixed(unittest.TestCase):
    """R0.10's actual fix: the two real overlaps now resolve cleanly."""

    def test_harness_memory_py_is_governed(self) -> None:
        result = gr.resolve_governing_design("scripts/harness_memory.py", root=_REPO)
        self.assertEqual(result["reason"], "governed")
        self.assertTrue(result["governed"])

    def test_check_personas_py_is_governed(self) -> None:
        result = gr.resolve_governing_design("scripts/check-personas.py", root=_REPO)
        self.assertEqual(result["reason"], "governed")
        self.assertTrue(result["governed"])

    def test_check_governs_index_exits_zero_on_real_repo(self) -> None:
        self.assertEqual(_main(["--root", str(_REPO)]), 0)


class TestCheckGovernsIndexFixture(unittest.TestCase):
    """Synthetic fixtures proving the gate actually detects violations."""

    def test_exits_zero_on_clean_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_design(root, "a.md", area="agentm/memory", governs=["scripts/a.py"])
            _write_design(root, "b.md", area="agentm/storage", governs=["scripts/b.py"])
            self.assertEqual(_main(["--root", str(root)]), 0)

    def test_exits_nonzero_on_synthetic_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_design(root, "a.md", area="agentm/memory", governs=["scripts/shared.py"])
            _write_design(root, "b.md", area="agentm/storage", governs=["scripts/shared.py"])
            self.assertEqual(_main(["--root", str(root)]), 1)

    def test_exits_nonzero_on_unknown_area(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_design(root, "a.md", area="totally/made-up-area", governs=["scripts/a.py"])
            self.assertEqual(_main(["--root", str(root)]), 1)


if __name__ == "__main__":
    unittest.main()
