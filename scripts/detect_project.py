#!/usr/bin/env python3
"""detect_project — auto-detect what an unconfigured repo is + propose a config.

V4 #32 (auto-detect + auto-configure on first session). The first conversation
in a repo the harness hasn't seen runs this engine: it scans the cwd against a
set of deterministic, side-effect-free rules and emits a **default-all-enabled**
proposed config. Rules do NOT gate which skills/hooks are present — every
enableable skill/hook starts enabled; a matched rule attaches the *rationale*
for why that skill/hook is relevant to THIS repo (so the operator can make an
informed opt-out decision at approval time).

The actual scan -> propose -> approve -> write flow is agent-driven via `/setup`
(hooks are non-interactive). This module is the deterministic engine underneath:
it computes the proposal; it never writes anything.

Locked design calls (V4 #32 plan):
- DC-6: `type` defaults to "coding"; the type taxonomy (build/vacation/research)
  is V5, so `R-non-coding` ships as a stub that never matches.
- DC-7: default-all-enabled. Detection surfaces rationale; it never gates.

Stdlib-only. Cross-platform via pathlib. No third-party deps.

Usage from another module:

    from detect_project import detect, ProposedConfig
    proposal = detect(Path("/path/to/repo"))
    if proposal.verdict == "bypass":
        ...  # this IS a harness repo — skip detection
    else:
        ...  # proposal.skills / proposal.hooks carry per-target rationale

Usage from CLI:

    python3 scripts/detect_project.py <cwd> --format json   # structured proposal
    python3 scripts/detect_project.py <cwd> --format text    # propose-config block

CLI exits 0 on a computed proposal (propose OR bypass verdict).
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

# -----------------------------------------------------------------------------
# Canonical enableable sets (default-all-enabled baseline per DC-7)
# -----------------------------------------------------------------------------

# Skills subject to per-project enablement. Harness-utility skills (doctor,
# migrate-to-diataxis, wiki-author) are always-on tooling and intentionally not
# listed — they aren't gated per project.
ENABLEABLE_SKILLS: tuple[str, ...] = (
    "memory",
    "design",
    "diataxis-author",
    "pii-scrubber",
    "ship-release",
    "dependabot-fixer",
)

# Hooks subject to per-project enablement.
#
# evidence-tracker was removed in the V5 dev-loop slim — it backed the /work
# task-closeout gate, which now lives in the crickets developer-safety /
# code-review plugins, not in agentm. The rule that justified it (R-tests) is
# gone with it.
ENABLEABLE_HOOKS: tuple[str, ...] = (
    "kill-switch",
    "steer",
    "commit-on-stop",
    "memory-recall-session-start",
    "memory-recall-prompt-submit",
    "memory-reflect-idle",
    "memory-reflect-stop",
)

# Default rationale per target when no rule matched it (DC-7: still enabled).
_DEFAULT_RATIONALE: dict[str, str] = {
    "memory": "operator-personal projects all get memory (default)",
    "kill-switch": "operator-control default",
    "steer": "operator-control default",
    "commit-on-stop": "operator-control default",
}
_GENERIC_DEFAULT = "enabled by default"

# Memory hooks justified together by R-vault-content.
_MEMORY_HOOKS: tuple[str, ...] = (
    "memory-recall-session-start",
    "memory-recall-prompt-submit",
    "memory-reflect-idle",
    "memory-reflect-stop",
)


# -----------------------------------------------------------------------------
# Data shapes
# -----------------------------------------------------------------------------

@dataclass
class RuleMatch:
    """A rule fired against the cwd. `skills`/`hooks` name the targets the rule
    justifies; `rationale` is the human-readable reason. `bypass` short-circuits
    the whole proposal (R-harness)."""

    rule_id: str
    rationale: str
    skills: tuple[str, ...] = ()
    hooks: tuple[str, ...] = ()
    bypass: bool = False
    # R-harness only: whether a legacy <repo>/.harness/ state dir is present.
    legacy_harness_present: bool = False


@dataclass
class TargetState:
    enabled: bool
    auto_detected: bool
    rationale: str
    rule_id: Optional[str] = None


@dataclass
class ProposedConfig:
    verdict: str  # "propose" | "bypass"
    type: str = "coding"
    skills: dict[str, TargetState] = field(default_factory=dict)
    hooks: dict[str, TargetState] = field(default_factory=dict)
    matched_rules: tuple[str, ...] = ()
    # bypass-only context
    bypass_reason: Optional[str] = None
    legacy_harness_present: bool = False

    def to_dict(self) -> dict:
        if self.verdict == "bypass":
            return {
                "verdict": "bypass",
                "reason": self.bypass_reason,
                "legacy_harness_present": self.legacy_harness_present,
            }
        return {
            "verdict": "propose",
            "type": self.type,
            "skills": {
                name: {
                    "enabled": st.enabled,
                    "auto_detected": st.auto_detected,
                    "rationale": st.rationale,
                    "rule_id": st.rule_id,
                }
                for name, st in self.skills.items()
            },
            "hooks": {
                name: {
                    "enabled": st.enabled,
                    "auto_detected": st.auto_detected,
                    "rationale": st.rationale,
                    "rule_id": st.rule_id,
                }
                for name, st in self.hooks.items()
            },
            "matched_rules": list(self.matched_rules),
        }


# -----------------------------------------------------------------------------
# Detection rules — each is (cwd: Path) -> Optional[RuleMatch], side-effect-free
# -----------------------------------------------------------------------------

def _has_any_glob(cwd: Path, patterns: tuple[str, ...]) -> bool:
    for pat in patterns:
        for _ in cwd.glob(pat):
            return True
    return False


def rule_wiki(cwd: Path) -> Optional[RuleMatch]:
    if (cwd / "wiki").is_dir():
        return RuleMatch(
            rule_id="R-wiki",
            rationale="Found wiki/ dir -> diataxis-author manages Diataxis-shaped documentation.",
            skills=("diataxis-author",),
        )
    return None


_LANG_MANIFESTS: tuple[str, ...] = ("package.json", "pyproject.toml", "Cargo.toml", "go.mod")


def rule_changelog(cwd: Path) -> Optional[RuleMatch]:
    if (cwd / "CHANGELOG.md").is_file() and any((cwd / m).is_file() for m in _LANG_MANIFESTS):
        return RuleMatch(
            rule_id="R-changelog",
            rationale="Found CHANGELOG + language manifest -> ship-release manages the release workflow.",
            skills=("ship-release",),
        )
    return None


def rule_dependabot(cwd: Path) -> Optional[RuleMatch]:
    gh = cwd / ".github"
    if (gh / "dependabot.yml").is_file() or (gh / "dependabot.yaml").is_file():
        return RuleMatch(
            rule_id="R-dependabot",
            rationale="Found dependabot config -> dependabot-fixer helps fix breakage on update PRs.",
            skills=("dependabot-fixer",),
        )
    return None


def rule_pii(cwd: Path) -> Optional[RuleMatch]:
    # Deterministic signal: presence of .env* files. Content secret-pattern
    # scanning is intentionally NOT done here (noisy + slow); .envrc (direnv) is
    # a known false positive the operator declines at approval (recorded in
    # operator_overrides so a future --redetect won't re-suggest).
    if _has_any_glob(cwd, (".env", ".env.*", ".env*")):
        return RuleMatch(
            rule_id="R-pii",
            rationale="Found .env* files -> pii-scrubber as a pre-commit/pre-push guardrail.",
            skills=("pii-scrubber",),
        )
    return None


def rule_harness(cwd: Path) -> Optional[RuleMatch]:
    # The harness SOURCE repo (agentm itself) carries the harness/ spec tree +
    # scripts/harness_memory.py. Detection is meaningless there — bypass. A
    # project that merely USES the harness has a .harness/ STATE dir but no
    # harness/ SOURCE tree; that case is handled upstream by is_registered()
    # gating the nudge. If /setup --detect is run in the source repo explicitly,
    # the legacy .harness/ dir (if any) is surfaced for migration.
    #
    # Pre-V5 this keyed on harness/phases/ — that dir was removed in the dev-loop
    # slim, so the marker moved to the durable memory-engine pair: the harness/
    # spec tree + the scripts/harness_memory.py state resolver, neither of which
    # a harness-using project vendors.
    if (cwd / "harness").is_dir() and (cwd / "scripts" / "harness_memory.py").is_file():
        return RuleMatch(
            rule_id="R-harness",
            rationale="This IS the harness source repo (harness/ + scripts/harness_memory.py present) -> skipping detection.",
            bypass=True,
            legacy_harness_present=(cwd / ".harness").is_dir(),
        )
    return None


def _package_json_has_scripts(cwd: Path) -> bool:
    pkg = cwd / "package.json"
    if not pkg.is_file():
        return False
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and bool(data.get("scripts"))


def rule_pkg_scripts(cwd: Path) -> Optional[RuleMatch]:
    if _package_json_has_scripts(cwd) or (cwd / "Makefile").is_file() or (cwd / "justfile").is_file():
        return RuleMatch(
            rule_id="R-pkg-scripts",
            rationale="Found scripts/tasks -> kill-switch ready to interrupt + steer ready to redirect long-running ones.",
            hooks=("kill-switch", "steer"),
        )
    return None


def rule_vault_content(cwd: Path) -> Optional[RuleMatch]:
    has_index = (cwd / "_index.md").is_file()
    has_decisions = (cwd / "decisions").is_dir() and (cwd / "conventions.md").is_file()
    if has_index or has_decisions:
        return RuleMatch(
            rule_id="R-vault-content",
            rationale="Found vault-content shape -> this looks operator-personal; memory hooks will capture context.",
            skills=("memory",),
            hooks=_MEMORY_HOOKS,
        )
    return None


def rule_design(cwd: Path) -> Optional[RuleMatch]:
    if (cwd / "wiki" / "explanation" / "designs").is_dir() or (cwd / "docs" / "design").is_dir():
        return RuleMatch(
            rule_id="R-design",
            rationale="Found a design dir -> design skill manages the design-then-implement pipeline.",
            skills=("design",),
        )
    return None


def rule_non_coding(cwd: Path) -> Optional[RuleMatch]:
    # V5 — type-aware enablement (build/vacation/research from _index.md
    # frontmatter `type:`). Stub returns None in v1 per DC-6.
    return None


# Ordered registry. R-harness first so a bypass short-circuits cleanly.
RULES: tuple[Callable[[Path], Optional[RuleMatch]], ...] = (
    rule_harness,
    rule_wiki,
    rule_changelog,
    rule_dependabot,
    rule_pii,
    rule_pkg_scripts,
    rule_vault_content,
    rule_design,
    rule_non_coding,
)


# -----------------------------------------------------------------------------
# Engine
# -----------------------------------------------------------------------------

def detect(cwd: Path | str) -> ProposedConfig:
    """Scan `cwd` against all rules; compose a default-all-enabled proposal.

    If any rule sets `bypass` (R-harness), return a bypass verdict immediately.
    Otherwise build the proposal: every enableable skill/hook starts enabled
    with its default rationale, then matched rules overlay the detected
    rationale + rule_id onto their named targets.
    """
    cwd = Path(cwd)

    matches: list[RuleMatch] = []
    for rule in RULES:
        m = rule(cwd)
        if m is None:
            continue
        if m.bypass:
            return ProposedConfig(
                verdict="bypass",
                bypass_reason=m.rationale,
                legacy_harness_present=m.legacy_harness_present,
            )
        matches.append(m)

    skills = {
        name: TargetState(
            enabled=True,
            auto_detected=False,
            rationale=_DEFAULT_RATIONALE.get(name, _GENERIC_DEFAULT),
        )
        for name in ENABLEABLE_SKILLS
    }
    hooks = {
        name: TargetState(
            enabled=True,
            auto_detected=False,
            rationale=_DEFAULT_RATIONALE.get(name, _GENERIC_DEFAULT),
        )
        for name in ENABLEABLE_HOOKS
    }

    for m in matches:
        for s in m.skills:
            if s in skills:
                skills[s] = TargetState(True, True, m.rationale, m.rule_id)
        for h in m.hooks:
            if h in hooks:
                hooks[h] = TargetState(True, True, m.rationale, m.rule_id)

    return ProposedConfig(
        verdict="propose",
        type="coding",
        skills=skills,
        hooks=hooks,
        matched_rules=tuple(m.rule_id for m in matches),
    )


# -----------------------------------------------------------------------------
# Rendering
# -----------------------------------------------------------------------------

def render_text(proposal: ProposedConfig, repo_name: str, slug: str) -> str:
    """Render the operator-facing propose-config block (the a/b/c UX)."""
    if proposal.verdict == "bypass":
        lines = [
            f"This looks like a harness project ({repo_name}).",
            "",
            proposal.bypass_reason or "Skipping detection.",
        ]
        if proposal.legacy_harness_present:
            lines.append("A legacy .harness/ state dir is present — consider migrating it to the vault.")
        return "\n".join(lines) + "\n"

    out: list[str] = []
    out.append("Hey, this looks like a new project I haven't seen before.")
    out.append("")
    out.append(f"Repo: {repo_name}")
    out.append(f"Suggested vault slug: {slug}")
    out.append(f"Suggested type: {proposal.type} (default)")
    out.append("")
    out.append("Skills enabled (with rationale):")
    for name, st in proposal.skills.items():
        mark = "✓" if st.enabled else "✗"
        out.append(f"  {mark} {name:<16} — {st.rationale}")
    out.append("")
    out.append("Hooks enabled (with rationale):")
    for name, st in proposal.hooks.items():
        mark = "✓" if st.enabled else "✗"
        out.append(f"  {mark} {name:<28} — {st.rationale}")
    out.append("")
    out.append("Default: all enabled. Want to:")
    out.append("  (a) Register with all-enabled")
    out.append("  (b) Register with custom selection (will prompt per-skill)")
    out.append("  (c) Skip registration (one-time scratch session)")
    return "\n".join(out) + "\n"


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="detect_project",
        description="Auto-detect what a repo is + propose a default-all-enabled config (V4 #32).",
    )
    p.add_argument("cwd", nargs="?", default=".", help="repo root to scan (default: cwd)")
    p.add_argument("--format", choices=("json", "text"), default="json", help="output format")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    cwd = Path(args.cwd).resolve()
    proposal = detect(cwd)
    if args.format == "json":
        print(json.dumps(proposal.to_dict(), indent=2, ensure_ascii=False))
    else:
        repo_name = cwd.name
        # Slug suggestion mirrors the repo dir name; /setup may refine it.
        print(render_text(proposal, repo_name=repo_name, slug=repo_name), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
