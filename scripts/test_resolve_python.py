#!/usr/bin/env python3
"""Unit tests for harness/hooks/lib/resolve-python.sh — the canonical
interpreter resolver the memory hooks and machinery_doctor both go through.

Hermetic. Every test builds a scratch directory of *fake* interpreters and puts
it on PATH, so the assertions are about the resolver's selection logic and
never about which Pythons happen to be installed on the box running the suite.
A fake interpreter is a one-line shell script whose exit code answers the only
question the resolver asks it ("does your sqlite3 have enable_load_extension?"):
exit 0 for capable, exit 1 for not. That is the real contract — the resolver
probes by exit status, not by parsing output — so the fakes exercise the
production code path rather than a stand-in for it.

The case worth naming: `test_prefers_versioned_when_bare_python3_is_incapable`
is this whole area's actual bug. The resolver this one replaced listed only
bare-name paths, Homebrew ships only a versioned `python3.13`, and macOS's bare
`python3` is Apple's incapable build — so every candidate missed and it fell
through to the floor while looking like it had tried.

Run: `cd scripts && python3 -m unittest test_resolve_python -v`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_RESOLVER = _REPO / "harness" / "hooks" / "lib" / "resolve-python.sh"
# Resolved before any PATH override, since the tests deliberately hand the
# resolver a PATH containing nothing but their own fakes.
_BASH = shutil.which("bash") or "/bin/bash"


@unittest.skipIf(os.name == "nt", "bash resolver is the POSIX half; the pwsh twin is AST-checked by check-syntax.ps1")
class ResolvePythonTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.bin = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _fake(self, name: str, *, capable: bool) -> Path:
        """A fake interpreter that answers the capability probe by exit code."""
        p = self.bin / name
        p.write_text("#!/bin/sh\nexit {}\n".format(0 if capable else 1), encoding="utf-8")
        p.chmod(0o755)
        return p

    def _run(self, **env_overrides) -> str:
        env = dict(os.environ)
        # An isolated PATH so a real interpreter on the developer's box can
        # never satisfy a case that is meant to find nothing. /usr/bin stays
        # off it deliberately.
        env["PATH"] = str(self.bin)
        env.pop("AGENTM_PYTHON", None)
        env.pop("AGENT_TOOLKIT_PYTHON", None)
        env.update({k: v for k, v in env_overrides.items() if v is not None})
        proc = subprocess.run(
            [_BASH, str(_RESOLVER)], capture_output=True, text=True, env=env, timeout=60,
        )
        self.assertEqual(proc.returncode, 0, f"resolver must always exit 0; stderr={proc.stderr!r}")
        self.assertEqual(
            len(proc.stdout.strip().splitlines()), 1,
            f"resolver must print exactly one line, got {proc.stdout!r}",
        )
        return proc.stdout.strip()

    def test_picks_capable_bare_python3_when_it_works(self):
        """The already-healthy box: nothing changes, one probe, bare name kept."""
        self._fake("python3", capable=True)
        self.assertEqual(self._run(), "python3")

    def test_prefers_versioned_when_bare_python3_is_incapable(self):
        """The bug this resolver exists for: macOS's bare `python3` is Apple's
        incapable build and Homebrew installs only a versioned binary."""
        self._fake("python3", capable=False)
        self._fake("python3.13", capable=True)
        self.assertEqual(self._run(), "python3.13")

    def test_skips_multiple_incapable_candidates(self):
        self._fake("python3", capable=False)
        self._fake("python3.11", capable=False)
        self._fake("python3.12", capable=True)
        self.assertEqual(self._run(), "python3.12")

    def _assert_capable_or_floor(self, resolved: str) -> None:
        """The invariant when PATH offers nothing capable.

        Asserted this way rather than as `== "python3"` on purpose. The
        resolver's absolute-prefix backstop (/opt/homebrew/bin, pyenv shims, …)
        is reachable regardless of PATH, so a developer box with a Homebrew
        Python installed legitimately resolves to that instead of the floor —
        pinning the literal would make this test pass or fail on who ran it.
        What must hold everywhere is the real contract: the resolver returns a
        capable interpreter, or the documented bare-`python3` floor, and never
        an incapable choice it picked over a capable one.
        """
        if resolved == "python3":
            return
        probe = subprocess.run(
            [resolved, "-c", "import sqlite3, sys; sys.exit(0 if hasattr(sqlite3.Connection, 'enable_load_extension') else 1)"],
            capture_output=True,
        )
        self.assertEqual(
            probe.returncode, 0,
            f"resolver returned {resolved!r}, which is neither the floor nor extension-capable",
        )

    def test_floor_when_nothing_on_path_is_capable(self):
        """Never worse than the pre-resolver behavior: vec_index.py's own
        graceful skip still applies downstream."""
        self._fake("python3", capable=False)
        self._assert_capable_or_floor(self._run())

    def test_floor_when_no_interpreter_exists_on_path_at_all(self):
        self._assert_capable_or_floor(self._run())

    def test_explicit_override_wins(self):
        capable = self._fake("python3", capable=True)
        override = self._fake("my-python", capable=True)
        self.assertEqual(self._run(AGENTM_PYTHON=str(override)), str(override))
        self.assertNotEqual(self._run(AGENTM_PYTHON=str(override)), str(capable))

    def test_explicit_override_is_honored_even_when_incapable(self):
        """Deliberate: an operator who names an interpreter gets that one, and
        the doctor row names the override as the cause. Silently substituting a
        'better' interpreter would make that diagnosis impossible."""
        self._fake("python3", capable=True)
        override = self._fake("my-python", capable=False)
        self.assertEqual(self._run(AGENTM_PYTHON=str(override)), str(override))

    def test_back_compat_alias_still_honored(self):
        override = self._fake("legacy-python", capable=True)
        self.assertEqual(self._run(AGENT_TOOLKIT_PYTHON=str(override)), str(override))

    def test_agentm_python_takes_precedence_over_the_alias(self):
        primary = self._fake("primary-python", capable=True)
        legacy = self._fake("legacy-python", capable=True)
        self.assertEqual(
            self._run(AGENTM_PYTHON=str(primary), AGENT_TOOLKIT_PYTHON=str(legacy)),
            str(primary),
        )

    def test_unrunnable_override_falls_through_to_the_probe(self):
        """A stale override must not wedge the hooks on a path that no longer
        exists — it degrades to normal resolution."""
        self._fake("python3", capable=False)
        self._fake("python3.13", capable=True)
        self.assertEqual(self._run(AGENTM_PYTHON="/nonexistent/python3"), "python3.13")


@unittest.skipIf(os.name == "nt", "POSIX half")
class ResolverIsWiredIntoHooksTests(unittest.TestCase):
    """The resolver being correct is worthless if a hook doesn't call it. These
    assert the wiring against the hook sources — the gap that made the original
    bug invisible was precisely that the resolver existed (in one hook) and the
    other three still ran a bare `python3`."""

    HOOKS = [
        "memory-recall-session-start",
        "memory-recall-prompt-submit",
        "memory-reflect-stop",
        "memory-reflect-idle",
    ]

    def _source(self, name: str, ext: str) -> str:
        return (_REPO / "harness" / "hooks" / name / f"{name}.{ext}").read_text(encoding="utf-8")

    def test_every_memory_hook_bootstraps_the_resolver(self):
        for name in self.HOOKS:
            with self.subTest(hook=name):
                self.assertIn("_resolve_agentm_python", self._source(name, "sh"))
                self.assertIn('AGENTM_PY="$(_resolve_agentm_python)"', self._source(name, "sh"))

    def test_every_memory_hook_ps1_twin_bootstraps_the_resolver(self):
        for name in self.HOOKS:
            with self.subTest(hook=name):
                src = self._source(name, "ps1")
                self.assertIn("Resolve-AgentmPython", src)
                self.assertIn("$Py = Resolve-AgentmPython", src)

    def test_no_memory_hook_runs_a_memory_script_under_bare_python3(self):
        """The regression guard with teeth. A memory script invoked as
        `python3 "$SOME_PY"` is the exact shape of the original bug; the cheap
        inline `python3 -c` JSON reads are fine and deliberately excluded, since
        parsing a small config file needs no extension support."""
        import re
        # `python3 "$VAR"` — running a resolved script path, not an inline -c.
        offender = re.compile(r'(?<!")\bpython3 "\$[A-Z_]+"')
        for name in self.HOOKS:
            with self.subTest(hook=name):
                hits = [
                    line.strip()
                    for line in self._source(name, "sh").splitlines()
                    if offender.search(line) and not line.strip().startswith("#")
                ]
                self.assertEqual(
                    hits, [],
                    f"{name}.sh runs a memory script under a bare `python3`: {hits}",
                )

    def test_the_resolver_itself_ships_both_halves(self):
        self.assertTrue(_RESOLVER.is_file(), f"{_RESOLVER} missing")
        self.assertTrue(_RESOLVER.with_suffix(".ps1").is_file(), "pwsh twin missing")


if __name__ == "__main__":
    unittest.main()
