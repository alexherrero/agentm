#!/usr/bin/env python3
"""check-agent-tools — every sub-agent agentm ships states its tool allowlist.

Claude Code grants an agent with no `tools:` line every tool, Edit and Write
included. So a missing line is never a neutral default, and a read-only
contract held only in prose is not enforced: the host enforces the allowlist,
not the body. Two rules, over every agent file agentm ships
(`harness/agents/*.md` and `adapters/*/agents/*.md`):

  1. Every agent carries a `tools:` line, unless its `description` says
     "Full tool access" — a missing line has to be a stated choice.
  2. An agent that says it is read-only — in its `description`, or in its
     body as "read-only by contract" / "a read-only <agent|sub-agent|grader|…>"
     — carries a `tools:` line naming none of Edit, Write, NotebookEdit,
     MultiEdit, and not `*`.

The body match is deliberately narrow: an agent that writes may still call one
of its tools read-only ("Bash (read-only: `git diff`)"), and that is not a
claim about the agent. Modelled on `lint_agent_tools` in crickets
`scripts/lint_src.py` (crickets #285); keep the two rules the same.

Run: `python3 scripts/check-agent-tools.py [--root DIR]`

Exit codes:
    0  every agent passes
    1  one or more violations found
    2  setup error (PyYAML missing, or an agent's frontmatter does not parse)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:
    print("check-agent-tools: pyyaml not installed — run: pip install pyyaml",
          file=sys.stderr)
    raise SystemExit(2)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Where agentm keeps the agents it ships, relative to the repo root.
AGENT_GLOBS = ("harness/agents/*.md", "adapters/*/agents/*.md")

# Claude Code tools that change files. A read-only agent's allowlist names
# none of them (`*` grants all of them).
WRITE_TOOLS = frozenset({"Edit", "Write", "NotebookEdit", "MultiEdit"})

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL)
_FULL_ACCESS_RE = re.compile(r"\bfull tool access\b", re.I)
_READ_ONLY_RE = re.compile(r"\bread-only\b", re.I)
# A body's self-declaration of read-only status — see the module docstring.
_READ_ONLY_BODY_RE = re.compile(
    r"read-only by contract"
    r"|\b(?:a|an|is|are)\s+\**read-only\**\s+(?:[\w-]+\s+)?"
    r"(?:sub-agent|subagent|agent|grader|explorer|coordinator|role|reviewer|evaluator)\b",
    re.I,
)


class FrontmatterError(Exception):
    pass


def split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter mapping, body). Raises FrontmatterError when the
    file has no frontmatter block or it is not a YAML mapping."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise FrontmatterError("no YAML frontmatter (--- ... ---)")
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        raise FrontmatterError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(fm, dict):
        raise FrontmatterError("frontmatter is not a mapping")
    return fm, text[m.end():]


def parse_tools(value) -> list[str] | None:
    """`tools:` as Claude Code reads it — a comma string or a YAML list.
    None when the value has neither shape or names nothing."""
    if isinstance(value, str):
        items = [t.strip() for t in value.split(",")]
    elif isinstance(value, list) and all(isinstance(t, str) for t in value):
        items = [t.strip() for t in value]
    else:
        return None
    items = [t for t in items if t]
    return items or None


def claims_read_only(fm: dict, body: str) -> bool:
    """True when the agent says it is read-only — in its description, or in
    its body as a self-declaration (see `_READ_ONLY_BODY_RE`)."""
    return bool(_READ_ONLY_RE.search(str(fm.get("description") or ""))
                or _READ_ONLY_BODY_RE.search(body))


def lint_agent_tools(fm: dict, body: str) -> list[str]:
    """The two allowlist rules for one agent. Returns the violations."""
    out: list[str] = []
    desc = str(fm.get("description") or "")
    read_only = claims_read_only(fm, body)
    if "tools" not in fm:
        if read_only:
            out.append("says it is read-only but has no 'tools:' line — Claude Code "
                       "grants a tools-less agent every tool, Edit and Write included; "
                       "add an allowlist without " + "/".join(sorted(WRITE_TOOLS)))
        elif not _FULL_ACCESS_RE.search(desc):
            out.append("has no 'tools:' line, so Claude Code grants it every tool — "
                       "add an allowlist, or say 'Full tool access' in its description "
                       "if that is the intent")
        return out
    tools = parse_tools(fm["tools"])
    if tools is None:
        out.append(f"'tools' must be a comma-separated string or a list of tool "
                   f"names (got {fm['tools']!r})")
        return out
    if read_only:
        wide = sorted(t for t in tools if t in WRITE_TOOLS or t == "*")
        if wide:
            out.append(f"says it is read-only but its 'tools:' line grants "
                       f"{', '.join(wide)}")
    return out


def agent_files(root: Path) -> list[Path]:
    found: set[Path] = set()
    for pattern in AGENT_GLOBS:
        found.update(p for p in root.glob(pattern) if p.is_file())
    return sorted(found)


def check(root: Path) -> tuple[list[str], list[str]]:
    """Return (violations, setup_errors) for every shipped agent under root."""
    violations: list[str] = []
    setup: list[str] = []
    for path in agent_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            fm, body = split_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, FrontmatterError) as exc:
            setup.append(f"{rel}: {exc}")
            continue
        for reason in lint_agent_tools(fm, body):
            violations.append(f"{rel}: agent {reason}")
    return violations, setup


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=REPO_ROOT,
                    help="repo root to scan (default: this checkout)")
    args = ap.parse_args(argv)
    root = args.root.resolve()
    violations, setup = check(root)
    for line in setup:
        print(f"check-agent-tools: {line}", file=sys.stderr)
    for line in violations:
        print(f"check-agent-tools: {line}", file=sys.stderr)
    if setup:
        return 2
    if violations:
        print(f"check-agent-tools: FAIL — {len(violations)} violation(s)", file=sys.stderr)
        return 1
    n = len(agent_files(root))
    print(f"check-agent-tools: OK — {n} agent(s) state their tool allowlist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
