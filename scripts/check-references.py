#!/usr/bin/env python3
"""Cross-reference integrity checks for the harness source tree.

Run against HARNESS_ROOT. Verifies that adapter files and phase specs
don't drift from the canonical spec layout under `harness/`.

Checks:
  1. Every `harness/<phases|agents|skills|pipelines>/<name>.md` or
     `harness/documentation.md` mentioned in any adapter file actually
     exists at that path.
  2. Every phase spec under `harness/phases/` references existing
     canonical agents/skills when it tells the phase to dispatch one.
  3. `templates/hooks/settings-fragment-bash.json` and
     `settings-fragment-pwsh.json` have matching top-level structure:
     same hook event names, same matcher patterns. Only `command`
     strings are allowed to differ.
  4. Each adapter SKILL.md, sub-agent file, and phase-command file
     references the canonical spec for *its own* name (catches
     copy-paste drift like a new skill forgetting to update the link).

Run:
  python3 scripts/check-references.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
errors: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)
    print(f"FAIL: {msg}", file=sys.stderr)


# ── check 1: referenced harness/*.md paths exist ────────────────────────────
HARNESS_PATH_RE = re.compile(
    r"\bharness/(phases|agents|skills|pipelines|documentation)(?:/([A-Za-z0-9_-]+))?\.md\b"
)
# Matches `harness/foo/bar.md` OR `harness/documentation.md`.

ADAPTER_GLOBS = [
    "adapters/**/*.md",
    "adapters/**/*.toml",
]


def iter_adapter_files() -> list[Path]:
    files: list[Path] = []
    for g in ADAPTER_GLOBS:
        files.extend(sorted(ROOT.glob(g)))
    return files


def check_referenced_paths_exist() -> None:
    for f in iter_adapter_files():
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in HARNESS_PATH_RE.finditer(text):
            full = m.group(0)
            # normalize: 'harness/documentation.md' has no subdir
            target = ROOT / full
            if not target.is_file():
                err(
                    f"{f.relative_to(ROOT)}: references '{full}' which does not exist"
                )


# ── check 2: each adapter file references its own canonical spec ────────────
# Drift catcher: if adapters/.../skills/foo/SKILL.md links to harness/skills/bar.md
# (copy-paste), we catch it.
NAME_FROM_SKILL_DIR_RE = re.compile(r"adapters/[^/]+/skills/([A-Za-z0-9_-]+)/SKILL\.md$")
NAME_FROM_AGENT_FILE_RE = re.compile(
    r"adapters/[^/]+/agents/([A-Za-z0-9_-]+)\.(md|toml)$"
)
NAME_FROM_COMMAND_FILE_RE = re.compile(
    r"adapters/[^/]+/(commands|workflows)/([A-Za-z0-9_-]+)\.(md|toml)$"
)

# Codex skills are prefixed 'harness-<phase>' — strip the prefix before matching.
CODEX_PHASE_PREFIX = "harness-"

# Shared skills that live under skills/ but map to harness/skills/<name>.md
SHARED_SKILLS = {"dependabot-fixer", "ship-release"}

# Antigravity puts sub-agents under skills/ (no separate sub-agent primitive).
# These skill names map to harness/agents/<name>.md, not harness/skills/<name>.md.
ANTIGRAVITY_AGENT_LIKE_SKILLS = {
    "adversarial-reviewer",
    "adversarial-reviewer-cross",
    "documenter",
    "explorer",
}


def expected_canonical_for(adapter_file: Path) -> str | None:
    """Return the canonical path each adapter file should link to, or None.

    Uses POSIX-style paths so the same regexes work on Windows (where
    Path.relative_to yields backslash separators natively).
    """
    rel = adapter_file.relative_to(ROOT).as_posix()

    m = NAME_FROM_SKILL_DIR_RE.search(rel)
    if m:
        skill_name = m.group(1)
        # Codex: 'harness-plan' → phase 'plan'
        if skill_name.startswith(CODEX_PHASE_PREFIX) and "codex" in rel:
            phase = skill_name[len(CODEX_PHASE_PREFIX):]
            if phase == "bugfix":
                return f"harness/pipelines/{phase}.md"
            # match phases/NN-<phase>.md
            for p in (ROOT / "harness/phases").glob(f"*-{phase}.md"):
                return p.relative_to(ROOT).as_posix()
            return None
        # Antigravity sub-agents-as-skills
        if "antigravity" in rel and skill_name in ANTIGRAVITY_AGENT_LIKE_SKILLS:
            return f"harness/agents/{skill_name}.md"
        # Shared skills (dependabot-fixer, ship-release)
        if skill_name in SHARED_SKILLS:
            return f"harness/skills/{skill_name}.md"
        return None

    m = NAME_FROM_AGENT_FILE_RE.search(rel)
    if m:
        name = m.group(1)
        return f"harness/agents/{name}.md"

    m = NAME_FROM_COMMAND_FILE_RE.search(rel)
    if m:
        name = m.group(2)
        if name == "bugfix":
            return "harness/pipelines/bugfix.md"
        for p in (ROOT / "harness/phases").glob(f"*-{name}.md"):
            return p.relative_to(ROOT).as_posix()
        return None

    return None


def check_self_reference() -> None:
    for f in iter_adapter_files():
        expected = expected_canonical_for(f)
        if expected is None:
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        # Accept either bare path or link form.
        if expected not in text:
            err(
                f"{f.relative_to(ROOT)}: expected to reference canonical spec "
                f"'{expected}' but it's not mentioned"
            )


# ── check 3: hook fragment schema parity ────────────────────────────────────
def check_fragment_parity() -> None:
    bash_path = ROOT / "templates/hooks/settings-fragment-bash.json"
    pwsh_path = ROOT / "templates/hooks/settings-fragment-pwsh.json"
    if not (bash_path.is_file() and pwsh_path.is_file()):
        err("settings-fragment-{bash,pwsh}.json missing")
        return
    try:
        bash = json.loads(bash_path.read_text(encoding="utf-8"))
        pwsh = json.loads(pwsh_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        err(f"settings-fragment JSON parse error: {e}")
        return

    bash_events = sorted((bash.get("hooks") or {}).keys())
    pwsh_events = sorted((pwsh.get("hooks") or {}).keys())
    if bash_events != pwsh_events:
        err(
            f"fragment event-name mismatch: bash={bash_events} vs pwsh={pwsh_events}"
        )
        return

    for evt in bash_events:
        bash_matchers = sorted(h.get("matcher", "") for h in bash["hooks"][evt])
        pwsh_matchers = sorted(h.get("matcher", "") for h in pwsh["hooks"][evt])
        if bash_matchers != pwsh_matchers:
            err(
                f"fragment matcher mismatch for '{evt}': "
                f"bash={bash_matchers} vs pwsh={pwsh_matchers}"
            )

    # Commands MUST differ (shell is different); if any command string is
    # identical it's a sign one side wasn't ported.
    for evt in bash_events:
        for bh, ph in zip(bash["hooks"][evt], pwsh["hooks"][evt]):
            bc = (bh.get("hooks") or [{}])[0].get("command", "")
            pc = (ph.get("hooks") or [{}])[0].get("command", "")
            if bc and pc and bc == pc:
                err(
                    f"fragment commands for '{evt}' are identical across shells "
                    f"(one side likely wasn't ported): {bc[:80]!r}"
                )


# ── check 4: phase specs reference existing canonical agents/skills ────────
# When a phase spec says "dispatch the `<name>` sub-agent" or "invoke the
# `<name>` skill", that canonical spec file must exist.
DISPATCH_AGENT_RE = re.compile(
    r"`([A-Za-z0-9_-]+)`\s+sub-agent", re.IGNORECASE
)
INVOKE_SKILL_RE = re.compile(
    r"`([A-Za-z0-9_-]+)`\s+skill", re.IGNORECASE
)


def check_phase_spec_dispatches() -> None:
    for spec in sorted((ROOT / "harness/phases").glob("*.md")):
        text = spec.read_text(encoding="utf-8")
        for m in DISPATCH_AGENT_RE.finditer(text):
            name = m.group(1)
            agent_spec = ROOT / f"harness/agents/{name}.md"
            if not agent_spec.is_file():
                err(
                    f"{spec.relative_to(ROOT)}: references "
                    f"`{name}` sub-agent but harness/agents/{name}.md is missing"
                )
        for m in INVOKE_SKILL_RE.finditer(text):
            name = m.group(1)
            skill_spec = ROOT / f"harness/skills/{name}.md"
            if not skill_spec.is_file():
                err(
                    f"{spec.relative_to(ROOT)}: references "
                    f"`{name}` skill but harness/skills/{name}.md is missing"
                )


def main() -> int:
    check_referenced_paths_exist()
    check_self_reference()
    check_fragment_parity()
    check_phase_spec_dispatches()

    if errors:
        print(f"\ncheck-references: {len(errors)} error(s).")
        return 1
    print("check-references: all cross-references resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
