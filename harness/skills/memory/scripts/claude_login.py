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

import re
from typing import Iterable

ADVICE = "the Claude login has lapsed — run `claude` then `/login` in a terminal"

# What `claude -p` says when its session is gone. "Failed to authenticate:
# OAuth session expired and could not be refreshed" on 2026-09-14, 10-03 and
# 11-01; "Not logged in · Please run /login" or "Invalid API key" are the other
# shapes the CLI prints for the same state.
#
# Whole phrases only. A bare "oauth" or "/login" anywhere in an error matched
# the words of the note being worked on: "c-oauth-or-guard.sh" on 2026-11-22
# and a note named "google-oauth-sign-in…" on 11-16 each sent a false
# "urgent: claude-login" email while every call reached the model.
_SIGNS = re.compile(
    r"\bfailed to authenticate\b"
    r"|\boauth (?:session|token)\b[^.\n]*?\b(?:expired|revoked|invalid)\b"
    r"|\bnot logged in\b"
    r"|\binvalid api key\b"
    r"|\bplease run /login\b",
    re.IGNORECASE)

# An error a gate raised over the model's reply. The model answered, so the
# login worked, whatever the rejected text quotes.
_ANSWERED = re.compile(r":\s*post-gate\b", re.IGNORECASE)


def is_lapse(text: str) -> bool:
    """True when an error message says the CLI could not authenticate."""
    t = text or ""
    return not _ANSWERED.search(t) and bool(_SIGNS.search(t))


def lapsed(errors: Iterable[str]) -> int:
    """How many of a run's errors are a lapsed login."""
    return sum(1 for e in errors or () if isinstance(e, str) and is_lapse(e))
