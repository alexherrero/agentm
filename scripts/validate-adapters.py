#!/usr/bin/env python3
"""Validate adapter files parse and have required keys.

Checks:
  - TOML (codex/agents/*.toml, gemini/commands/*.toml) parses; has name,
    description; codex agents have sandbox_mode.
  - Markdown YAML frontmatter (claude-code/agents, claude-code/commands,
    antigravity/workflows, antigravity/skills, gemini/agents) parses; has
    name and description.
  - SKILL.md files (claude-code/skills, antigravity/skills, codex/skills)
    have name + description frontmatter.
  - JSON (templates/features.json, adapters/gemini/settings.json,
    templates/hooks/settings-fragment-*.json) parses.
  - Every adapter name has a matching canonical spec under harness/phases/,
    harness/pipelines/, or harness/agents/ (allow-listed exceptions).

Exits non-zero on first failure; prints what and where.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

try:
    import yaml
except ModuleNotFoundError:
    print("FAIL: pyyaml not installed. run: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

ROOT = Path(__file__).resolve().parent.parent
errors: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)
    print(f"FAIL: {msg}", file=sys.stderr)


# ── frontmatter parsing ─────────────────────────────────────────────────────
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def parse_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(text)
    if not m:
        err(f"{path.relative_to(ROOT)}: no YAML frontmatter")
        return {}
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        err(f"{path.relative_to(ROOT)}: invalid YAML frontmatter: {e}")
        return {}
    if not isinstance(fm, dict):
        err(f"{path.relative_to(ROOT)}: frontmatter is not a mapping")
        return {}
    return fm


def require_keys(path: Path, fm: dict, *keys: str) -> None:
    for k in keys:
        v = fm.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            err(f"{path.relative_to(ROOT)}: frontmatter missing '{k}'")


# ── TOML validation ─────────────────────────────────────────────────────────
def check_toml(path: Path, required: list[str]) -> dict:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        err(f"{path.relative_to(ROOT)}: TOML parse error: {e}")
        return {}
    for k in required:
        if k not in data or not data[k]:
            err(f"{path.relative_to(ROOT)}: TOML missing '{k}'")
    return data


def validate_codex_agents() -> None:
    for p in sorted((ROOT / "adapters/codex/agents").glob("*.toml")):
        data = check_toml(p, ["name", "description", "sandbox_mode"])
        if data.get("sandbox_mode") not in (
            None,
            "read-only",
            "workspace-write",
            "danger-full-access",
        ):
            err(f"{p.relative_to(ROOT)}: invalid sandbox_mode: {data['sandbox_mode']}")


def validate_gemini_commands() -> None:
    for p in sorted((ROOT / "adapters/gemini/commands").glob("*.toml")):
        check_toml(p, ["description", "prompt"])


# ── markdown w/ YAML frontmatter validation ─────────────────────────────────
def validate_md_frontmatter(dir_rel: str, *required_keys: str, pattern: str = "*.md") -> None:
    d = ROOT / dir_rel
    if not d.is_dir():
        err(f"{dir_rel}: directory missing")
        return
    for p in sorted(d.glob(pattern)):
        fm = parse_frontmatter(p)
        if fm:
            require_keys(p, fm, *required_keys)


def validate_skill_dirs(dir_rel: str) -> None:
    d = ROOT / dir_rel
    if not d.is_dir():
        return
    for sk in sorted(d.iterdir()):
        if not sk.is_dir():
            continue
        skill_md = sk / "SKILL.md"
        if not skill_md.is_file():
            err(f"{sk.relative_to(ROOT)}: missing SKILL.md")
            continue
        fm = parse_frontmatter(skill_md)
        if fm:
            require_keys(skill_md, fm, "name", "description")


# ── JSON validation ─────────────────────────────────────────────────────────
def check_json(rel: str, required_keys: list[str] | None = None) -> dict:
    p = ROOT / rel
    if not p.is_file():
        err(f"{rel}: file missing")
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        err(f"{rel}: JSON parse error: {e}")
        return {}
    if required_keys:
        for k in required_keys:
            if k not in data:
                err(f"{rel}: missing key '{k}'")
    return data


# ── canonical spec backing check ────────────────────────────────────────────
# Every adapter phase-command must map to a canonical spec at
# harness/phases/NN-<name>.md or harness/pipelines/<name>.md. Sub-agents
# must map to harness/agents/<name>.md. Skills outside dependabot-fixer
# must map to harness/skills/<name>.md.
PHASE_COMMANDS = {"bugfix", "plan", "release", "review", "setup", "work"}
SUBAGENTS = {
    "adversarial-reviewer",
    "adversarial-reviewer-cross",
    "documenter",
    "explorer",
}
SKILLS = {"dependabot-fixer", "ship-release"}


def canonical_phase_exists(name: str) -> bool:
    # setup/plan/work/review/release → harness/phases/NN-<name>.md
    if list((ROOT / "harness/phases").glob(f"*-{name}.md")):
        return True
    # bugfix → harness/pipelines/bugfix.md
    if (ROOT / f"harness/pipelines/{name}.md").is_file():
        return True
    return False


def check_canonical_backing() -> None:
    for name in PHASE_COMMANDS:
        if not canonical_phase_exists(name):
            err(f"phase-command '{name}' has no canonical spec under harness/phases/ or harness/pipelines/")
    for name in SUBAGENTS:
        if not (ROOT / f"harness/agents/{name}.md").is_file():
            err(f"sub-agent '{name}' has no canonical spec at harness/agents/{name}.md")
    for name in SKILLS:
        if not (ROOT / f"harness/skills/{name}.md").is_file():
            err(f"skill '{name}' has no canonical spec at harness/skills/{name}.md")


# ── main ────────────────────────────────────────────────────────────────────
def main() -> int:
    # TOML
    validate_codex_agents()
    validate_gemini_commands()

    # Markdown + frontmatter — required keys differ by surface:
    # - sub-agents (Claude Code, Gemini): name + description
    # - Claude Code slash commands: description (filename IS the name)
    # - Antigravity workflows: description (filename IS the name)
    # - Antigravity rules: trigger (the "always_on" signal)
    validate_md_frontmatter("adapters/claude-code/agents",   "name", "description")
    validate_md_frontmatter("adapters/claude-code/commands", "description")
    validate_md_frontmatter("adapters/gemini/agents",        "name", "description")
    validate_md_frontmatter("adapters/antigravity/workflows", "description")
    validate_md_frontmatter("adapters/antigravity/rules",    "trigger")

    # Skills (SKILL.md)
    validate_skill_dirs("adapters/claude-code/skills")
    validate_skill_dirs("adapters/antigravity/skills")
    validate_skill_dirs("adapters/codex/skills")

    # JSON
    check_json("templates/features.json", required_keys=["features"])
    check_json("adapters/gemini/settings.json")
    check_json("templates/hooks/settings-fragment-bash.json", required_keys=["hooks"])
    check_json("templates/hooks/settings-fragment-pwsh.json", required_keys=["hooks"])

    # Canonical-spec backing
    check_canonical_backing()

    if errors:
        print(f"\nvalidate-adapters: {len(errors)} error(s).")
        return 1
    print("validate-adapters: all adapter files parse and have required keys.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
