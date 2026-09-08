#!/usr/bin/env python3
"""A `$var` touching a multibyte character is a build-dependent time bomb.

Bash can read the following character's bytes as identifier characters, so
`"$label…"` names a variable with the ellipsis glued on — unbound, and fatal
under `set -u`. `bash -n` does not catch it: it is not a parse error.

`run-fast-tier.sh` carried one for months, dying on its very first suite and
emitting no check records at all, which is what an eight-failure streak and a
watchdog stop rung look like from outside.

**Which environments trip it is not portable**, and four disagreed:

    a developer Mac    LC_CTYPE=C.UTF-8 fatal, LC_CTYPE=C clean
    the macOS runner   both fatal
    the Linux runner   neither fatal
    the Windows runner a bash on PATH that fails even the braced form

There is therefore no environment you can stand in and trust an unbraced
expansion, and no portable test that says when one will break. Four CI rounds
went into learning that, each with a weaker claim than the last, and the
conclusion is that the measurement is *evidence* — it is why the brace is
there — rather than something to re-derive at test time.

So everything below is a static read of the source: no subprocess, no locale,
no shell. That is also what actually protects the repository, since the next
em-dash somebody puts after a variable will look exactly as correct as that one
did, on whichever machine they happen to be using.

The fix is always a brace: `${label}…`.
"""
from __future__ import annotations

import re
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


class TheScriptThatCarriedIt(unittest.TestCase):
    """The specific file, checked the way everything here is checked: by
    reading it. No subprocess — see the module docstring for why."""

    def test_run_fast_tier_has_no_unbraced_expansion_left(self):
        script = _REPO / "scripts" / "health" / "run-fast-tier.sh"
        self.assertTrue(script.is_file(), f"{script} is missing")
        text = script.read_text(encoding="utf-8")
        m = _UNBRACED_THEN_MULTIBYTE.search(text)
        self.assertIsNone(
            m, f"the expansion is unbraced again: {m.group(0)!r}" if m else "")

    def test_it_still_contains_the_line_this_is_about(self):
        # Guards the check above against quietly passing because somebody
        # deleted the line rather than braced it.
        script = _REPO / "scripts" / "health" / "run-fast-tier.sh"
        text = script.read_text(encoding="utf-8")
        self.assertIn("${label}", text,
                      "the braced expansion is gone — either it regressed to "
                      "an unbraced form this scan should have caught, or the "
                      "line was removed and this test needs retargeting")


if __name__ == "__main__":
    unittest.main()
