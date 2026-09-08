#!/usr/bin/env python3
"""check-payload-parity.py — every copy of the context payload is the template's
derivation, not a hand-kept variant.

`templates/agentmemory-context.md` is the source. Two copies are derived from it:

  - `adapters/antigravity/rules/agentmemory-context.md` — tracked in git, always
    rendered WITHOUT a capture address (the operator's mailbox is not something
    to commit);
  - the `AGENTMEMORY` managed section of `~/.gemini/GEMINI.md` — machine-local,
    rendered WITH the address when one is configured.

Both were hand-kept until 2026-09-07, and both had drifted: the rule and the
template were different documents by then, and Gemini's copy was a July layout.
This gate is what stops that from happening again — a copy edited by hand fails
here, and `/memory payload --write` (equivalently `install.sh --update`)
restores it.

The Gemini half graceful-skips when `~/.gemini/GEMINI.md` is absent, which is
every CI runner and every machine without Antigravity. The Antigravity half
never skips: it is a tracked file, so it is checkable everywhere.

Usage:  python3 scripts/check-payload-parity.py
Exit:   0 iff every present copy equals the render.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import payload_render as pr  # noqa: E402


def check() -> list:
    """Return [(label, status, detail)] — status is OK | FAIL | SKIP."""
    template = pr.read_template()
    neutral = pr.render(template, None)
    address = pr.capture_address()
    addressed = pr.render(template, address) if address else neutral

    results = []

    rule_path = pr.ANTIGRAVITY_RULE_PATH
    expected_rule = pr.antigravity_rule(neutral)
    if not rule_path.is_file():
        results.append(("antigravity rule", "FAIL", f"missing: {rule_path}"))
    else:
        actual = rule_path.read_text(encoding="utf-8")
        if actual == expected_rule:
            results.append(("antigravity rule", "OK", f"sha {pr.short(actual)}"))
        else:
            results.append((
                "antigravity rule", "FAIL",
                f"sha {pr.short(actual)} != template's {pr.short(expected_rule)} ({rule_path})",
            ))

    gemini_path = pr.GEMINI_RULES_PATH
    if not gemini_path.is_file():
        results.append(("gemini managed section", "SKIP", f"no {gemini_path} on this machine"))
    else:
        section = pr.managed_section(gemini_path.read_text(encoding="utf-8"))
        if section is None:
            results.append((
                "gemini managed section", "FAIL",
                f"no {pr.MARKER} managed section in {gemini_path}",
            ))
        elif section == addressed:
            results.append(("gemini managed section", "OK", f"sha {pr.short(section)}"))
        else:
            results.append((
                "gemini managed section", "FAIL",
                f"sha {pr.short(section)} != template's {pr.short(addressed)} ({gemini_path})",
            ))

    return results


def main() -> int:
    results = check()
    failed = [r for r in results if r[1] == "FAIL"]
    for label, status, detail in results:
        print(f"    [{status:<4}] {label} — {detail}")
    sys.stdout.flush()
    if failed:
        print("", file=sys.stderr)
        print("check-payload-parity: FAIL — a payload copy has drifted from the template.", file=sys.stderr)
        print("    Repair: python3 harness/skills/memory/scripts/payload.py --write", file=sys.stderr)
        print("    Never edit a copy by hand — edit templates/agentmemory-context.md.", file=sys.stderr)
        return 1
    print("check-payload-parity: OK — every present copy is the template's derivation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
