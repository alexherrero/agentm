#!/usr/bin/env python3
"""Every memory-skill module that imports a sibling must bootstrap its own
directory onto sys.path before it does.

Two foreign loaders file-path-load these modules from another process with
a pristine sys.path — crickets' research bridge (forward_learning.py,
adapt_skills.py) and its resolve_project (recall.py, harness_memory.py) —
and inside agentm the hooks run them as scripts. A bare ``import
engine_state`` (filing-v2 2a's vendored resolver) works in the second case
by accident of the script's own directory being sys.path[0] and fails in
the first, which is exactly how crickets' learn-forward test broke after
2a shipped. This pins the contract for the whole directory: load each
module the way a foreign bridge does and require it to import.

Run: python3 scripts/test_skill_modules_file_loadable.py
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

_SKILL = Path(__file__).resolve().parent.parent / "harness" / "skills" / "memory" / "scripts"

_LOADER = """
import importlib.util, sys
sys.path[:] = [p for p in sys.path if p not in ("", ".", {skill!r})]
spec = importlib.util.spec_from_file_location("foreign_load_probe", {path!r})
m = importlib.util.module_from_spec(spec)
sys.modules["foreign_load_probe"] = m
spec.loader.exec_module(m)
"""


def _sibling_importers() -> list[Path]:
    out = []
    for p in sorted(_SKILL.glob("*.py")):
        if p.name.startswith("test_"):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if "import engine_state" in text:
            out.append(p)
    return out


class ForeignFileLoad(unittest.TestCase):
    def test_every_engine_state_importer_loads_with_a_pristine_sys_path(self) -> None:
        importers = _sibling_importers()
        self.assertTrue(importers, "no engine_state importers found — the glob is wrong")
        failures = []
        for p in importers:
            code = _LOADER.format(skill=str(_SKILL), path=str(p))
            proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                  cwd=str(Path(__file__).resolve().parent.parent), timeout=60)
            if proc.returncode != 0:
                tail = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "(no stderr)"
                failures.append(f"{p.name}: {tail}")
        self.assertEqual(failures, [], "a foreign loader cannot import these modules:\n  " + "\n  ".join(failures))


# Every test in this module gets its own engine state dir. Without it a hand
# run shares one directory across the file and reads what the last test left
# (PLAN-source-and-hygiene, task 3); the battery's runner hid that from CI.
# The path insert is here rather than assumed: a hand run reaches this module
# as `scripts.<name>`, which puts the repo root on the path and not `scripts/`.
import os.path as _osp  # noqa: E402
import sys as _sys  # noqa: E402

if _osp.dirname(_osp.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _osp.dirname(_osp.abspath(__file__)))
from engine_state_isolation import isolate_module  # noqa: E402

isolate_module(globals())


if __name__ == "__main__":
    unittest.main()
