#!/usr/bin/env python3
"""payload.py — `/memory payload`: print the context payload, or rewrite the
copies derived from it.

The payload is the text pasted into claude.ai and the Gem. It has one source,
`templates/agentmemory-context.md`, and two derived copies — the Antigravity
rule tracked in the repo and the managed section of `~/.gemini/GEMINI.md`.
This command is the operator's end of that: it prints exactly what to paste,
and under `--write` it regenerates both copies so nothing has to be kept by
hand.

Usage:
    python3 payload.py                 # print the body, then the surface list
    python3 payload.py --body-only     # just the body, for piping to a clipboard
    python3 payload.py --write         # regenerate both derived copies
    python3 payload.py --check         # report drift without writing anything

Exit:
    0  printed, or written
    1  --check found a copy that has drifted
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _agentm_scripts() -> Path:
    """The agentm repo's scripts/ directory, from this file's own location.

    harness/skills/memory/scripts/payload.py -> <repo>/scripts
    """
    return Path(__file__).resolve().parents[4] / "scripts"


sys.path.insert(0, str(_agentm_scripts()))

import payload_render as pr  # noqa: E402

SURFACES = [
    ("claude.ai", "Settings -> Custom instructions, or a Project's instructions", "paste"),
    ("Gemini (the Gem)", "the Gem's Instructions", "paste"),
    ("Antigravity", "adapters/antigravity/rules/agentmemory-context.md + ~/.gemini/GEMINI.md", "derived"),
    ("Claude Code", "SessionStart / UserPromptSubmit hooks", "automatic"),
]


def print_body(body_only: bool = False) -> int:
    body = pr.render(pr.read_template(), pr.capture_address())
    print(body, end="")
    if body_only:
        return 0
    print()
    print("--- paste the body above into: ---")
    width = max(len(name) for name, _, _ in SURFACES)
    for name, where, how in SURFACES:
        marker = {"paste": "paste", "derived": "derived", "automatic": "no paste"}[how]
        print(f"  {name:<{width}}  [{marker}]  {where}")
    print()
    print("A re-paste is owed only when the posture, the card guide or the surface")
    print("list changes. Run --write to regenerate the derived copies.")
    return 0


def write_copies() -> int:
    print("regenerating the copies derived from templates/agentmemory-context.md:")
    print(f"    antigravity rule       {pr.write_antigravity_rule()}  ({pr.ANTIGRAVITY_RULE_PATH})")
    action = pr.write_gemini_section()
    where = "no ~/.gemini on this machine" if action == "absent" else str(pr.GEMINI_RULES_PATH)
    print(f"    gemini managed section {action}  ({where})")
    return 0


def check_copies() -> int:
    sys.path.insert(0, str(_agentm_scripts()))
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "check_payload_parity", _agentm_scripts() / "check-payload-parity.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod.main()


def main(argv: list | None = None) -> int:
    p = argparse.ArgumentParser(prog="/memory payload", description=__doc__.splitlines()[0])
    p.add_argument("--write", action="store_true", help="regenerate both derived copies")
    p.add_argument("--check", action="store_true", help="report drift without writing")
    p.add_argument("--body-only", action="store_true", help="print the body with no surface list")
    args = p.parse_args(argv if argv is not None else sys.argv[1:])
    if args.write and args.check:
        p.error("--write and --check are alternatives")
    if args.write:
        return write_copies()
    if args.check:
        return check_copies()
    return print_body(args.body_only)


if __name__ == "__main__":
    raise SystemExit(main())
