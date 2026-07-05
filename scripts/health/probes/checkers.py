#!/usr/bin/env python3
"""checkers.py — the four D-⑫ seeded task-pair checkers (PLAN-r3-uplift-scoring
task 3 / R3.2a).

Each checker answers "model + agentm vs model bare" for one seeded task,
scored by a deterministic structural/regex match against the session's
assistant turns — never an LLM judge. A transcript is a list of turn dicts:

    [{"role": "user" | "assistant", "content": str}, ...]

Each probe module-level object exposes:
    NAME            short slug, matches the plan's task-pair name
    backed_fixture() -> transcript   a synthetic "backed" (agentm-context) session
    bare_fixture()   -> transcript   a synthetic "bare" (no agentm-context) session
    check(transcript) -> bool        True iff the transcript demonstrates the
                                      behavior this pair is seeded to test

Task 4 (R3.2b) is the live weekly runner that feeds `check()` real model
transcripts instead of these fixtures; this module and its fixtures are
Task 3's own scope — fixture-mode only, no live model calls, fast-tier safe.
"""
from __future__ import annotations


def _assistant_text(transcript: list[dict]) -> str:
    return "\n".join(t["content"] for t in transcript if t.get("role") == "assistant")


# ── 1. recall-a-prior-decision ───────────────────────────────────────────────
# Seed: a fixture project with a planted decision in .harness/vault state from
# a prior "session". Checker: the backed session's answer names the decision;
# the bare session's does not (it has no prior-session state to draw on).
class RecallAPriorDecision:
    NAME = "recall-a-prior-decision"
    _DECISION_MARKER = "vault root renamed to Agent (V5-3)"

    @staticmethod
    def backed_fixture() -> list[dict]:
        return [
            {"role": "user", "content": "What did we decide about the vault root path?"},
            {"role": "assistant", "content": (
                "Per the prior session's PLAN.md, the decision was: the "
                f"{RecallAPriorDecision._DECISION_MARKER} — so don't reuse the old path."
            )},
        ]

    @staticmethod
    def bare_fixture() -> list[dict]:
        return [
            {"role": "user", "content": "What did we decide about the vault root path?"},
            {"role": "assistant", "content": (
                "I don't have any prior context on this project's vault path decisions — "
                "could you tell me what was decided?"
            )},
        ]

    @staticmethod
    def check(transcript: list[dict]) -> bool:
        return RecallAPriorDecision._DECISION_MARKER in _assistant_text(transcript)


# ── 2. find-the-planted-bug ──────────────────────────────────────────────────
# Seed: a fixture repo with a known defect the evidence-tracker/gates would
# have caught. Checker: the backed session's output references the caught
# defect; the bare session's does not.
class FindThePlantedBug:
    NAME = "find-the-planted-bug"
    _DEFECT_MARKER = "recall.py:412 drops the high-priority entry"

    @staticmethod
    def backed_fixture() -> list[dict]:
        return [
            {"role": "user", "content": "Is there anything wrong with the recall path?"},
            {"role": "assistant", "content": (
                f"Yes — {FindThePlantedBug._DEFECT_MARKER} when the always-load budget "
                "is exceeded (the gate's planted-defect fixture already proves this)."
            )},
        ]

    @staticmethod
    def bare_fixture() -> list[dict]:
        return [
            {"role": "user", "content": "Is there anything wrong with the recall path?"},
            {"role": "assistant", "content": (
                "I don't see anything obviously wrong without more context — "
                "what symptoms are you seeing?"
            )},
        ]

    @staticmethod
    def check(transcript: list[dict]) -> bool:
        return FindThePlantedBug._DEFECT_MARKER in _assistant_text(transcript)


# ── 3. cold-resume-from-.harness ─────────────────────────────────────────────
# Seed: a fixture PLAN.md/progress.md mid-task. Checker: the backed session
# picks up the next unchecked task; the bare session has no such state to
# resume from — trivial pass/fail for bare, since it has no filesystem access
# to a prior session's .harness/ at all. This pair calibrates the floor
# (the easiest of the four to discriminate), not a stretch goal.
class ColdResumeFromHarness:
    NAME = "cold-resume-from-.harness"
    _NEXT_TASK_MARKER = "task 3 — the D-⑫ seeded task-pair battery"

    @staticmethod
    def backed_fixture() -> list[dict]:
        return [
            {"role": "user", "content": "Continue where we left off."},
            {"role": "assistant", "content": (
                f"Picking up {ColdResumeFromHarness._NEXT_TASK_MARKER} — the next "
                "unchecked task in PLAN.md."
            )},
        ]

    @staticmethod
    def bare_fixture() -> list[dict]:
        return [
            {"role": "user", "content": "Continue where we left off."},
            {"role": "assistant", "content": (
                "I don't have any memory of a previous conversation — what would "
                "you like me to help with?"
            )},
        ]

    @staticmethod
    def check(transcript: list[dict]) -> bool:
        return ColdResumeFromHarness._NEXT_TASK_MARKER in _assistant_text(transcript)


# ── 4. preference-adherence ──────────────────────────────────────────────────
# Seed: a fixture preference (e.g. a memory-recorded commit-message
# convention). Checker: the backed session's output conforms; the bare
# session's does not.
class PreferenceAdherence:
    NAME = "preference-adherence"
    _VIOLATION_MARKER = "Co-Authored-By: Claude"

    @staticmethod
    def backed_fixture() -> list[dict]:
        return [
            {"role": "user", "content": "Draft the commit message for this change."},
            {"role": "assistant", "content": (
                "fix(recall): keep the high-priority always-load entry under budget\n"
            )},
        ]

    @staticmethod
    def bare_fixture() -> list[dict]:
        return [
            {"role": "user", "content": "Draft the commit message for this change."},
            {"role": "assistant", "content": (
                "fix(recall): keep the high-priority always-load entry under budget\n\n"
                f"{PreferenceAdherence._VIOLATION_MARKER}\n"
            )},
        ]

    @staticmethod
    def check(transcript: list[dict]) -> bool:
        return PreferenceAdherence._VIOLATION_MARKER not in _assistant_text(transcript)


ALL_PROBES = [
    RecallAPriorDecision,
    FindThePlantedBug,
    ColdResumeFromHarness,
    PreferenceAdherence,
]
