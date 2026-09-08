#!/usr/bin/env python3
"""A `$var` touching a multibyte character is a locale-dependent time bomb.

`bash -n` does not catch it and neither does a C-locale run. Under a UTF-8
`LC_CTYPE`, bash reads the following character's bytes as identifier
characters, so `"$label…"` names a variable `label…` — unbound, and fatal
under `set -u`.

`run-fast-tier.sh` carried one for months. It ran clean from a C-locale shell
and died on its very first suite from a UTF-8 one, emitting no check records at
all, which is what an eight-failure streak and a watchdog stop rung look like
from the outside.

The fix is always a brace: `${label}…`. This pins it for every shell script in
the repository, because the next em-dash or ellipsis somebody puts after a
variable will look exactly as correct as that one did.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent

# An unbraced expansion whose next character is non-ASCII. `${var}` is fine —
# the brace ends the name — and so is any ASCII follower.
_UNBRACED_THEN_MULTIBYTE = re.compile(r"\$[A-Za-z_][A-Za-z_0-9]*[^\x00-\x7f]")

_SKIPPED_DIRS = {".git", ".claude", "node_modules", "__pycache__", ".venv"}


def _shell_sources() -> list:
    out = []
    for p in sorted(_REPO.rglob("*.sh")):
        # Relative parts, not absolute. A checkout living inside a worktree
        # directory has `.claude` in every absolute path, so filtering the
        # absolute parts skipped the entire repository and left the scan
        # silently green — an empty population is not a clean one.
        if any(part in _SKIPPED_DIRS for part in p.relative_to(_REPO).parts):
            continue
        out.append(p)
    return out


class NoUnbracedExpansionBeforeAMultibyteCharacter(unittest.TestCase):
    def test_every_shell_script(self):
        findings = []
        for p in _shell_sources():
            text = p.read_text(encoding="utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), start=1):
                m = _UNBRACED_THEN_MULTIBYTE.search(line)
                if m:
                    rel = p.relative_to(_REPO).as_posix()
                    findings.append(f"{rel}:{lineno}: {m.group(0)!r} in {line.strip()!r}")
        self.assertEqual(
            findings, [],
            "unbraced expansion followed by a multibyte character — under a "
            "UTF-8 LC_CTYPE bash reads those bytes as part of the variable "
            "name, and `set -u` makes it fatal. Brace it: ${name}\n  "
            + "\n  ".join(findings))

    def test_the_pattern_finds_the_shape_it_is_looking_for(self):
        # A matcher that matches nothing is indistinguishable from a clean
        # repository, and this one is checking an absence.
        self.assertIsNotNone(_UNBRACED_THEN_MULTIBYTE.search('echo "running $label…"'))
        self.assertIsNone(_UNBRACED_THEN_MULTIBYTE.search('echo "running ${label}…"'))
        self.assertIsNone(_UNBRACED_THEN_MULTIBYTE.search('echo "running $label..."'))

    def test_it_scans_a_real_population(self):
        self.assertGreater(len(_shell_sources()), 10,
                           "the glob found almost no shell scripts; it is wrong")


class TheBashBehaviourThisIsAbout(unittest.TestCase):
    """The mechanism itself, so the rule above is a fact rather than folklore."""

    def _run(self, snippet: str, ctype: str):
        env = dict(os.environ)
        env["LC_CTYPE"] = ctype
        return subprocess.run(["bash", "-c", snippet], env=env, capture_output=True)

    UNBRACED = 'set -u; label=hello; echo "running $label…"'
    BRACED = 'set -u; label=hello; echo "running ${label}…"'

    def test_unbraced_dies_under_a_utf8_ctype(self):
        r = self._run(self.UNBRACED, "C.UTF-8")
        if r.returncode == 0:
            self.skipTest("this bash does not absorb the ellipsis under C.UTF-8")
        self.assertIn(b"unbound variable", r.stderr)

    def test_braced_survives_the_same_environment(self):
        r = self._run(self.BRACED, "C.UTF-8")
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))

    def test_unbraced_looks_fine_in_the_c_locale(self):
        # Which is why it survived: the failure is in the caller's environment,
        # not in the file, so a local run and CI can both be green.
        r = self._run(self.UNBRACED, "C")
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))


class TheScriptThatCarriedIt(unittest.TestCase):
    def test_run_fast_tier_starts_its_suites_under_a_utf8_ctype(self):
        script = _REPO / "scripts" / "health" / "run-fast-tier.sh"
        self.assertTrue(script.is_file())
        env = dict(os.environ)
        env["LC_CTYPE"] = "C.UTF-8"
        # `-n` is a parse, not a run: the whole point is that this bug is not a
        # parse error, so the check here is that the braced form is what is in
        # the file, verified by the pattern rather than by a 20-minute run.
        text = script.read_text(encoding="utf-8")
        self.assertIsNone(_UNBRACED_THEN_MULTIBYTE.search(text))
        r = subprocess.run(["bash", "-n", str(script)], env=env, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main()
