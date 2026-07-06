#!/usr/bin/env python3
"""privacy_scrub — the mandatory PII scrub for a `failure-incident` memory
entry (agentm-memory-index.md, AG Wave B leader 3/5).

Failure context (a stack trace, an error log excerpt) is untrusted and
potentially PII-bearing, so a `failure-incident` write redacts before it
ever reaches disk — a persistence-boundary guard the write cannot skip.

**Why this is agentm-native, not a call into crickets' `privacy` capability**
(a deviation from the plan's original framing, noted here rather than
silently reconciled): agentm must never depend on crickets — the one-way
capability bridge runs crickets -> agentm only (`check-process-seam-import-
direction`'s own rule, generalized). Calling out to a crickets-installed
scrubber would invert that. So this module ports the same PATTERN
CATEGORIES `scripts/check-no-pii.sh` already scans for (email, personal
absolute path, API-key shape, US phone number) into a small, self-contained
*redactor* — check-no-pii.sh only detects-and-reports over a git tree; nothing
in the repo redacts an arbitrary in-memory string before a write, which is
what a `failure-incident` entry needs. Deliberately narrower than a full
Semgrep-backed privacy pack (crickets' forward-referenced pack, not built
yet) — this is the persistence-boundary floor, not the whole capability.

Public API:

    scrub_pii(text: str) -> str
        Replace every match with a `[REDACTED-<KIND>]` placeholder. Never
        raises; a regex that fails to compile (it can't, they're static) or
        any unexpected input just passes through unmatched.
"""
from __future__ import annotations

import re

# Each (kind, compiled pattern) — order matters only for readability; matches
# don't overlap across categories in practice.
_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    # Personal absolute paths: /Users/<name>/, /home/<name>/, C:\Users\<name>\
    ("PATH", re.compile(r"(?:/Users/|/home/)[A-Za-z0-9_.-]+(?:/[^\s'\"]*)?")),
    ("PATH", re.compile(r"[A-Za-z]:\\Users\\[A-Za-z0-9_.-]+(?:\\[^\s'\"]*)?")),
    # API-key shapes: OpenAI (sk-...), GitHub PAT (ghp_/gho_/ghs_...), GitLab
    # (glpat-...), AWS access key id (AKIA...).
    ("API-KEY", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("API-KEY", re.compile(r"\bgh[a-z]_[A-Za-z0-9]{20,}\b")),
    ("API-KEY", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("API-KEY", re.compile(r"\bAKIA[A-Z0-9]{16}\b")),
    # US phone numbers: (555) 123-4567 / 555-123-4567 / 555.123.4567. No
    # leading \b before the optional "(" — a `(` is non-word, so `\b` never
    # matches at a whitespace-then-"(" boundary (both sides are \W).
    ("PHONE", re.compile(r"(?:\(\d{3}\)\s?|\b\d{3}[-.])\d{3}[-.]\d{4}\b")),
)


def scrub_pii(text: str) -> str:
    """Redact every PII-shaped match in `text`. Never raises."""
    if not text:
        return text
    scrubbed = text
    for kind, pattern in _PATTERNS:
        scrubbed = pattern.sub(f"[REDACTED-{kind}]", scrubbed)
    return scrubbed
