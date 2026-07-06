#!/usr/bin/env python3
"""persona_compile — per-host launch compile (agentm-persona-activation.md
§ "How it launches on each host", AG Wave B leader 4/5).

For a dispatched sub-agent, a persona compiles down to the host's own
agent-definition format at install time, so the host's built-in routing
picks it up (no per-host adapter beyond this one compiler module):

  - **Claude Code**: `name` -> the Task tool's `subagent_type` (the
    filename stem — install.sh already places it at
    `.claude/agents/<name>.md`, matching the existing compound-agent
    dispatch); `triggers:` -> `description` (the host's auto-route field);
    `tier:` -> `model:` frontmatter (via `persona_resolve.resolve_tier`).
  - **Antigravity**: no first-class sub-agent slot, so the persona wraps
    as a `SKILL.md` (the existing sub-agent-as-skill pattern
    `install.sh`'s compound-agent dispatch already uses for legacy agents).

**Scoped gap, not silently glossed over:** the design also calls for
"the `enhances:`-resolved tools -> the `tools:` allowlist." This repo's
`capability_resolver.py` only reports `{available, provider, version,
reason}` per capability — it has no per-capability tool-name manifest to
draw a `tools:` allowlist from. Emitting a fabricated `tools:` list would
be worse than omitting it (a wrong allowlist silently restricts the
persona; omitting it is Claude Code's own documented "no restriction"
default). This compiler omits `tools:` until that substrate exists, rather
than inventing one.

Public API:

    compile_claude_code(name, *, root=None) -> str
        The `.claude/agents/<name>.md` content.

    compile_antigravity(name, *, root=None) -> str
        The `.agents/skills/<name>/SKILL.md` content.

Both call `persona_resolve.adopt(name, "sub-agent", root=root)` and raise
`PersonaCompileError` if the persona doesn't adopt cleanly (gate-failed /
not-found / error) — a compile-time failure, not a silent empty file.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import persona_resolve  # noqa: E402


class PersonaCompileError(ValueError):
    """A persona failed to adopt cleanly at compile time."""


def _adopt_or_raise(name: str, root: Path | None) -> dict:
    result = persona_resolve.adopt(name, "sub-agent", root=root)
    if not result["adopted"]:
        raise PersonaCompileError(
            f"{name}: cannot compile — adopt() returned {result['reason']!r} "
            f"(violations: {result['violations']})"
        )
    return result


def _description_from_triggers(name: str, triggers: list[str] | None) -> str:
    if not triggers:
        return f"The {name} persona."
    return f"Activates on: {', '.join(triggers)}."


def compile_claude_code(name: str, *, root: Path | None = None) -> str:
    """Render the `.claude/agents/<name>.md` content for `name`."""
    result = _adopt_or_raise(name, root)
    repo_root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    manifest_path = repo_root / "personas" / f"{name}.md"
    check_personas = persona_resolve._load_check_personas()
    fm = check_personas._parse_frontmatter(manifest_path) or {}

    lines = ["---", f"name: {name}"]
    lines.append(f"description: {_description_from_triggers(name, fm.get('triggers'))}")
    lines.append("kind: agent")
    if result["tier_binding"] is not None:
        lines.append(f"model: {result['tier_binding']['model']}")
    lines.append("supported_hosts: [claude-code]")
    lines.append("---")
    lines.append("")
    lines.append(result["stance"] or "")
    return "\n".join(lines) + "\n"


def compile_antigravity(name: str, *, root: Path | None = None) -> str:
    """Render the `.agents/skills/<name>/SKILL.md` content for `name`."""
    result = _adopt_or_raise(name, root)
    repo_root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    manifest_path = repo_root / "personas" / f"{name}.md"
    check_personas = persona_resolve._load_check_personas()
    fm = check_personas._parse_frontmatter(manifest_path) or {}

    lines = ["---", f"name: {name}"]
    lines.append(f"description: {_description_from_triggers(name, fm.get('triggers'))}")
    lines.append("kind: skill")
    lines.append("supported_hosts: [antigravity]")
    lines.append("---")
    lines.append("")
    lines.append(result["stance"] or "")
    return "\n".join(lines) + "\n"


def _main(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Compile a persona to a host-native agent-def.")
    p.add_argument("--host", choices=["claude-code", "antigravity"], required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--root", default=None)
    p.add_argument("--out", required=True, help="output file path")
    args = p.parse_args(argv)

    root = Path(args.root) if args.root else None
    try:
        content = (
            compile_claude_code(args.name, root=root)
            if args.host == "claude-code"
            else compile_antigravity(args.name, root=root)
        )
    except PersonaCompileError as e:
        print(f"persona_compile: {e}", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
