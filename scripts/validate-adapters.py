#!/usr/bin/env python3
"""Validate adapter files parse and have required keys.

Checks:
  - Markdown YAML frontmatter (claude-code/commands, antigravity/rules)
    parses; has the required keys.
  - SKILL.md files (claude-code/skills) have name + description frontmatter.
  - JSON (templates/features.json, adapters/gemini/settings.json,
    templates/hooks/settings-fragment-*.json) parses.
  - Every remaining skill maps to a canonical spec under harness/skills/.

The phase-gated dev loop + the review sub-agents were slimmed out of agentm in
the V5 unbundling (moved to the crickets developer-workflows / code-review
plugins). Their adapter surfaces (gemini/commands, claude-code/agents,
gemini/agents, antigravity/workflows, antigravity/skills) no longer exist, so
they are not validated here; their absence is pinned by
scripts/test_devloop_slim_retired.py.

Exits non-zero on first failure; prints what and where.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

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
# Each remaining single-file skill (doctor) must map to a canonical spec at
# harness/skills/<name>.md. The phase commands + review sub-agents were slimmed
# out in the V5 unbundling (moved to crickets), so there is no longer a
# canonical harness/phases/ or harness/agents/ backing to check for them; the
# four-mode diataxis-migration skill likewise retired to crickets' wiki-
# maintenance in the V5 docs slim.
SKILLS = {"doctor"}


def check_canonical_backing() -> None:
    for name in SKILLS:
        if not (ROOT / f"harness/skills/{name}.md").is_file():
            err(f"skill '{name}' has no canonical spec at harness/skills/{name}.md")


# ── main ────────────────────────────────────────────────────────────────────
def main() -> int:
    # Markdown + frontmatter — required keys differ by surface:
    # - Claude Code slash commands: description (filename IS the name)
    # - Antigravity rules: trigger (the "always_on" signal)
    validate_md_frontmatter("adapters/claude-code/commands", "description")
    validate_md_frontmatter("adapters/antigravity/rules",    "trigger")

    # Skills (SKILL.md)
    validate_skill_dirs("adapters/claude-code/skills")

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
