#!/usr/bin/env python3
"""workflow_persona_resolver — resolves a crickets workflow step to the
persona it wears (agentm-persona-activation.md's workflow-step selection
path, PLAN-wave-d-personas task 3).

The phase spec is the source of truth for a workflow-step adoption (the
design's locked selection policy): a crickets phase command names the
persona it wears for its step. This module holds that mapping so it is
one testable place instead of copy-pasted prose; it is NOT derived from
any manifest's `triggers:` field at runtime — `triggers:` feeds only the
sub-agent/description routing path and never becomes a second, competing
selector for workflow steps (agentm-persona-activation.md:47).

CLI / exit-code contract (the crickets `resolve_workflow_persona.py`
bridge targets this):

    workflow_persona_resolver.py <step> [--explicit NAME]

    A persona resolves:      prints its name to stdout, exit 0.
    Nothing resolves:        prints nothing, exit 1 (graceful-skip — the
                              phase proceeds with no persona adopted).
    Usage error:              exit 2.

Public API:

    resolve_workflow_persona(step, explicit=None) -> str | None
        Precedence: an explicit invocation always wins over the
        workflow-step default (agentm-persona-activation.md's selection
        policy — "an explicit choice wins"). Returns None when neither an
        explicit persona nor a known step mapping applies. Never raises.
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional

# One entry per wired phase command (PLAN-wave-d-personas task 3). Mirrors
# what each new-roster manifest's own `triggers:` field documents today
# (personas/tech-lead.md: plan-phase, personas/engineer.md: work-phase,
# personas/reviewer.md: review-phase, personas/troubleshooter.md:
# bugfix-phase) — kept as a separate, explicit table per the design's
# locked call that `triggers:` never doubles as this lookup.
WORKFLOW_PERSONAS: dict[str, str] = {
    "plan-phase": "tech-lead",
    "work-phase": "engineer",
    "review-phase": "reviewer",
    "bugfix-phase": "troubleshooter",
}


def resolve_workflow_persona(step: str, explicit: Optional[str] = None) -> Optional[str]:
    """Resolve the persona a workflow step wears. Explicit invocation wins
    over the step's default; an unknown step with no explicit override
    resolves to None. Never raises."""
    if explicit:
        return explicit
    return WORKFLOW_PERSONAS.get(step)


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(
        prog="workflow_persona_resolver.py",
        description="Resolve the persona a crickets workflow step wears "
                    "(explicit invocation takes precedence).",
        add_help=True,
    )
    ap.add_argument("step", nargs="?", help="workflow-step name, e.g. plan-phase")
    ap.add_argument("--explicit", default=None,
                    help="an already-adopted persona name this session wears; "
                         "wins over the step's default when present")
    try:
        args = ap.parse_args(argv[1:])
    except SystemExit:
        return 2
    if not args.step and not args.explicit:
        print("usage: workflow_persona_resolver.py <step> [--explicit NAME]",
              file=sys.stderr)
        return 2

    persona = resolve_workflow_persona(args.step or "", explicit=args.explicit)
    if persona:
        print(persona)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
