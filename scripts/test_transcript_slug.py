#!/usr/bin/env python3
"""Tests for the transcript-path slug both memory hooks compute.

Why this file exists: the slug formula was wrong in production for 57 days and
CI stayed green the whole time, because `verify-hook-resolution.sh` computed the
expected path with the *same* wrong formula the hooks used. The seeded transcript
landed exactly where the buggy lookup searched, so the gate proved only that the
two sides agreed with each other — never that either agreed with Claude Code.

The bug: an absolute path already begins with '/', so `tr '/' '-'` supplies the
leading '-' on its own. Both hooks prefixed another one, so a cwd of '/a/b' came
out as '--a-b' — a directory Claude Code never creates. Every lookup missed,
`memory-reflect-stop` logged "transcript not found (skipping)" every session,
every marker written by `memory-recall-session-start` was unresolvable, and the
orphan sweeper skipped all of them permanently (200 had accumulated).

So these tests deliberately do NOT re-derive the formula. They pin it against
hand-written literals and assert both hooks agree — the two failure modes a
mirror cannot catch.

Skipped on non-POSIX (the slug expressions are bash, and `tr` is a POSIX tool) —
the same skip the sibling hook suites take. CI runs these on the Linux and macOS
matrices.

Run: python3 scripts/test_transcript_slug.py
"""
from __future__ import annotations

import os
import re
import subprocess
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_HOOKS = _HERE.parent / "harness" / "hooks"

_STOP_HOOK = _HOOKS / "memory-reflect-stop" / "memory-reflect-stop.sh"
_START_HOOK = _HOOKS / "memory-recall-session-start" / "memory-recall-session-start.sh"
_GATE = _HERE / "verify-hook-resolution.sh"

# Known-good pairs: (absolute cwd, the slug Claude Code actually uses).
#
# Written out by hand rather than computed, which is the whole point — a literal
# cannot mirror the formula it is checking. The convention itself was confirmed
# empirically against real directory names under `~/.claude/projects/` (a plain
# repo path, a home directory, and a worktree path); the fixtures below are
# synthetic stand-ins for those shapes, since the PII guardrail rightly keeps
# personal paths out of tracked files.
#
# The third case is the one that matters most: a dot converts too, which is what
# makes `.claude/worktrees/...` sessions resolve at all.
_KNOWN_GOOD = [
    ("/home", "-home"),
    ("/home/u/projects/demo", "-home-u-projects-demo"),
    (
        "/home/u/projects/demo/.claude/worktrees/wt-abc123",
        "-home-u-projects-demo--claude-worktrees-wt-abc123",
    ),
]

_SLUG_LINE = re.compile(r'^\s*CWD_SLUG=(".*")\s*$', re.MULTILINE)


def _extract_slug_expr(script: Path) -> str:
    """Pull the verbatim `CWD_SLUG=...` right-hand side out of a shell script.

    Reading the real line (rather than reimplementing it here) is the point: a
    reimplementation would be one more mirror to drift.
    """
    text = script.read_text(encoding="utf-8")
    matches = _SLUG_LINE.findall(text)
    if len(matches) != 1:
        raise AssertionError(
            f"{script.name}: expected exactly one CWD_SLUG= assignment, found {len(matches)}"
        )
    return matches[0]


def _run_slug(script: Path, cwd_value: str, var: str) -> str:
    """Evaluate that script's own slug expression for `cwd_value`."""
    expr = _extract_slug_expr(script)
    # Bind whichever variable name the script's expression reads, run the
    # assignment verbatim as it appears on disk, then echo the result.
    script_body = f'{var}="{cwd_value}"\nCWD_SLUG={expr}\nprintf "%s" "$CWD_SLUG"\n'
    out = subprocess.run(
        ["bash", "-c", script_body], capture_output=True, text=True, timeout=30
    )
    if out.returncode != 0:
        # Include the exit code: the stderr is often empty (a missing `bash` or
        # `tr` exits non-zero silently), and "failed: " with nothing after it
        # tells the next reader nothing.
        raise AssertionError(
            f"{script.name}: slug expression exited {out.returncode} "
            f"for cwd={cwd_value!r}; stderr={out.stderr.strip()!r}"
        )
    return out.stdout


@unittest.skipIf(os.name == "nt", "bash hook expressions — POSIX only")
class TestTranscriptSlug(unittest.TestCase):
    def test_stop_hook_matches_known_good_slugs(self) -> None:
        """The regression test: the old '-' + tr '/'→'-' turns '/a/b' into '--a-b',
        which fails here rather than silently missing every lookup."""
        for cwd, expected in _KNOWN_GOOD:
            with self.subTest(cwd=cwd):
                self.assertEqual(_run_slug(_STOP_HOOK, cwd, "CWD"), expected)

    def test_session_start_hook_matches_known_good_slugs(self) -> None:
        for cwd, expected in _KNOWN_GOOD:
            with self.subTest(cwd=cwd):
                self.assertEqual(
                    _run_slug(_START_HOOK, cwd, "SESSION_CWD"), expected
                )

    def test_both_hooks_agree(self) -> None:
        """A marker written by one hook must be resolvable by the other — the
        session-start hook records the transcript path the stop hook looks up."""
        for cwd, _ in _KNOWN_GOOD:
            with self.subTest(cwd=cwd):
                self.assertEqual(
                    _run_slug(_STOP_HOOK, cwd, "CWD"),
                    _run_slug(_START_HOOK, cwd, "SESSION_CWD"),
                )

    def test_gate_agrees_with_the_hooks(self) -> None:
        """`verify-hook-resolution.sh` seeds a transcript at the path it expects.
        If it drifts from the hooks again, that gate goes green on broken
        behavior — exactly how this bug survived 57 days."""
        for cwd, expected in _KNOWN_GOOD:
            with self.subTest(cwd=cwd):
                self.assertEqual(_run_slug(_GATE, cwd, "PROJ"), expected)

    def test_no_slug_starts_with_a_double_hyphen(self) -> None:
        """The specific shape of the old bug, named so a regression reads clearly
        rather than as an opaque string mismatch."""
        for cwd, _ in _KNOWN_GOOD:
            with self.subTest(cwd=cwd):
                slug = _run_slug(_STOP_HOOK, cwd, "CWD")
                self.assertFalse(
                    slug.startswith("--"),
                    f"slug for {cwd} starts with '--' ({slug!r}); an absolute path's "
                    "leading '/' already becomes the single leading '-'",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
