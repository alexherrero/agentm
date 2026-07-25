#!/usr/bin/env python3
"""Standard-shaped routing — Stage 1 of the accumulate loop.

The contract (`wiki/designs/agentm-experience-and-dreaming.md`, the
accumulate-loop section): when a capture path produces a candidate that is
*standard-shaped* — "a rule about how work should be judged or done, not a
fact or preference" — it targets an opinion supplement instead of general
memory. Classification is deterministic-first.

This module ships the deterministic half only. Two deliberate limits, both
recorded as locked design calls in `PLAN-loose-ends-accumulate-loop.md`:

- **Text-shape, not source-attribution.** The spec's signal → opinion table
  routes by *where a lesson came from* (a `/review` correction, a token-audit
  finding, a PII incident). Those sources emit nothing machine-readable
  today, so that half cannot be built as specified. This classifies from the
  candidate's own text instead.
- **No LLM assist.** The spec allows one at MEDIUM confidence; its dispatch
  is undesigned, so a candidate this module cannot classify deterministically
  simply routes normally.

Being wrong in the "not standard-shaped" direction is cheap — the candidate
takes its usual path. Being wrong the other way puts noise in front of the
standards the agent works to, so every rule below is deliberately narrow:
a candidate must look normative *and* be about doing work.
"""
from __future__ import annotations

import re
from typing import Optional

# The opinions the spec's own signal map names as routing targets. The other
# coded bases (simple, ready, worth-knowing) are real opinions but are not
# targets in that table, so nothing here routes to them.
ROUTABLE_OPINIONS = ("good", "done", "efficient", "how-we-engineer",
                     "recoverable", "private")

# A candidate has to sound like a rule before anything else is considered.
# Normative modals and prohibitions — the grammar of a standard.
_RULE_SHAPED = re.compile(
    r"\b(always|never|must|should|shouldn't|don't|do not|"
    r"required?|require[sd]?|forbid(?:den|s)?|"
    r"before you |prefer\w* .{0,40}\bover\b)\b",
    re.IGNORECASE,
)

# ...and be about doing work, which is what separates a standard from a
# personal preference expressed in the same normative grammar ("always use
# dark mode" is a preference; "always run the gates before committing" is a
# standard).
_WORK_DOMAIN = re.compile(
    r"\b(test|tests|testing|commit|commits|gate|gates|review|reviews|"
    r"ship|shipping|release|releases|plan|plans|deploy|deploys|"
    r"pr|prs|merge|merges|verif\w+|lint|build|builds|ci|"
    r"refactor\w*|debug\w*|document\w*|docs|design|designs|"
    r"branch|branches|rollback|revert|migration|migrations)\b",
    re.IGNORECASE,
)

# Voice and prose lessons are explicitly NOT opinions — the spec calls
# double-capture forbidden ("one lesson, one home"); they belong to the
# style overlay. Checked before anything else so a normative sentence about
# wording can never be pulled into an opinion supplement.
_VOICE_LESSON = re.compile(
    r"\b(voice|tone|prose|wording|phrasing|register|"
    r"style guide|styleguide|word choice|copy|readab\w+)\b",
    re.IGNORECASE,
)

# Text-shape signals per opinion. Ordered most- to least-specific: the first
# family that matches wins, so a narrowly-scoped opinion (private, recoverable)
# is not swallowed by a broad one (good).
_OPINION_SIGNALS: tuple[tuple[str, re.Pattern], ...] = (
    # NOTE: "token" is deliberately scoped to credential contexts here. Bare
    # `token\w*` also matches "token cost" / "token budget", which belong to
    # `efficient` — and since private is evaluated first, the unscoped version
    # silently stole every cost lesson. Caught by a test, not by review.
    ("private", re.compile(
        r"\b(pii|secret|secrets|credential\w*|password\w*|api key|"
        r"(?:api|auth|access|bearer)[ -]token\w*|"
        r"privacy|private data|redact\w*|scrub\w*|leak\w*)\b", re.IGNORECASE)),
    ("recoverable", re.compile(
        r"\b(recoverab\w+|unrecoverab\w+|revert\w*|rollback|roll back|undo|"
        r"force-push|force push|destructive|irreversib\w+|data loss|"
        r"safety gate|blast radius)\b", re.IGNORECASE)),
    ("efficient", re.compile(
        r"\b(token\w*|cost\w*|budget\w*|cheap\w*|expensive|"
        r"model tier|tier|routing|opus|sonnet|haiku|context window)\b",
        re.IGNORECASE)),
    ("done", re.compile(
        r"\b(done|definition of done|done-criteria|acceptance|complete\w*|"
        r"ship-ready|green|passing|wake-on-ci|check-all)\b", re.IGNORECASE)),
    ("good", re.compile(
        r"\b(quality|correct\w*|bug\w*|defect\w*|adversarial|regression\w*|"
        r"edge case\w*|flaky|review\w*)\b", re.IGNORECASE)),
    ("how-we-engineer", re.compile(
        r"\b(process|architect\w+|convention\w*|retro\w*|incident\w*|"
        r"workflow|phase|handoff|design review)\b", re.IGNORECASE)),
)


def _text_of(candidate) -> str:
    """The candidate's own prose — title and body only.

    Duck-typed on purpose: this takes reflect's `Candidate`, but the spec
    routes edit-driven capture and the watchlist through the same rule, and
    those carry different shapes. Anything with title/body works.

    `category` is deliberately NOT folded in, even though the spec names
    `kind` as a deterministic input. reflect's category vocabulary
    ("workflow", "preferences", "fix", "idea") overlaps the opinion signal
    vocabulary — "workflow" is also a how-we-engineer signal — so blending
    them made every default-category candidate match how-we-engineer
    regardless of its text. A test caught it. Using `kind` as a real signal
    needs a mapping that doesn't collide with the anchor vocabulary, which
    is design work this stage deliberately isn't doing.
    """
    parts = [
        getattr(candidate, "title", "") or "",
        getattr(candidate, "body", "") or "",
    ]
    return "\n".join(parts)


def classify_standard_shaped(candidate) -> Optional[str]:
    """Return the opinion a standard-shaped candidate belongs to, or None.

    None means "not standard-shaped" — route it the normal way. That is the
    safe default and the common case; this returns a target only when the
    candidate reads as a rule about how work is done or judged.
    """
    text = _text_of(candidate)
    if not text.strip():
        return None

    # Voice lessons have their own home. Checked first so no later rule can
    # claim one.
    if _VOICE_LESSON.search(text):
        return None

    # Must read as a rule, and must be about work. Both, not either: the pair
    # is what separates a standard from a preference wearing the same grammar.
    if not _RULE_SHAPED.search(text):
        return None
    if not _WORK_DOMAIN.search(text):
        return None

    for name, pattern in _OPINION_SIGNALS:
        if pattern.search(text):
            return name
    # Rule-shaped and work-related, but matching no opinion's vocabulary.
    # Deliberately not defaulted into how-we-engineer: an unclassifiable
    # standard is exactly the MEDIUM-confidence case the spec hands to an
    # LLM assist, and that assist is undesigned. Route it normally.
    return None
