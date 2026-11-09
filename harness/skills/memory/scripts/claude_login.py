#!/usr/bin/env python3
"""Has the `claude` CLI's login lapsed? (task 198)

The nightly enrichment, the weekly field brief and the other paid jobs shell
out to `claude -p`. When its OAuth session expires, every call fails the same
way, at no cost, and each job used to report only that it "failed": the
morning brief of 2026-11-01 read "7 failed", and the field brief of 10-31
exited 3. The fix is the operator's, in a terminal; no job re-authenticates.
This module recognises the failure and says what to do about it.

Read-only.
"""
from __future__ import annotations

from typing import Iterable

ADVICE = "the Claude login has lapsed — run `claude` then `/login` in a terminal"

# What `claude -p` says when its session is gone. "Failed to authenticate:
# OAuth session expired and could not be refreshed" on 2026-09-14, 10-03 and
# 11-01; "Not logged in" or "Invalid API key" are the other shapes the CLI
# prints for the same state.
_SIGNS = ("failed to authenticate", "oauth", "not logged in", "invalid api key", "/login")


def is_lapse(text: str) -> bool:
    """True when an error message says the CLI could not authenticate."""
    t = (text or "").lower()
    return any(s in t for s in _SIGNS)


def lapsed(errors: Iterable[str]) -> int:
    """How many of a run's errors are a lapsed login."""
    return sum(1 for e in errors or () if isinstance(e, str) and is_lapse(e))
