#!/usr/bin/env python3
"""project_config — read/write the V4 #32 enablement block on project.json.

The enablement block records which skills/hooks are enabled for a project + the
detection rationale for each. It is an ADDITIVE block on the existing
`project.json` (which already carries `vault_project`, `github`, `env`) — NOT a
new file, and explicitly NOT `features.json` (that stays the governed
verification ledger). Per V4 #32 locked DC-1.

`project.json` is vault-resident post-V4-#26: it resolves to
`<vault>/projects/<slug>/_harness/project.json` via the harness_memory
dispatcher. The merge-writer reads through that resolution and writes back via
`safe_write_replace_style` (preserving `vault_project`/`github`/`env`).

Pure functions (no I/O): `build_enablement_block`, `merge_enablement`,
`apply_override`, `is_registered`, `diff_detection`, `apply_redetect`. I/O
wrappers: `load_project_json`, `write_config`, `register`, `redetect`. CLI:
`is-registered`, `should-nudge`, `register`, `redetect`.

Locked design calls (the `/setup --redetect` follow-up to V4 #32 DC-4):
- RD-1: re-detect SURFACES, it does not apply. The default run renders the diff
  and writes nothing but the `last_redetect_at` stamp; `--apply` is an explicit
  second invocation that re-scans from scratch (no stale diff is ever applied).
- RD-2: apply refreshes DETECTION METADATA only (`auto_detected` / `rule_id` /
  `rationale`); it never flips `enabled`. Detection surfaces rationale and never
  gates (V4 #32 DC-7), so a lapsed rule returns a target to its default
  rationale rather than disabling it. Disabling stays operator-driven through
  `apply_override`.
- RD-3: `operator_overrides` suppress enablement SUGGESTIONS for their target,
  never target-inventory facts. A `retired-target` (the harness dropped the
  skill/hook from the enableable set) is reported even when overridden, because
  a dead config entry is housekeeping, not a re-suggestion of a declined skill.

Stdlib-only. Cross-platform.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import detect_project as dp  # noqa: E402
import harness_memory as hm  # noqa: E402
import repo_registry  # noqa: E402

# Keys the enablement block owns. Everything else on project.json
# (vault_project, github, env, ...) is preserved untouched by the merge.
_ENABLEMENT_KEYS = (
    "type",
    "skills",
    "hooks",
    "registered_at",
    "registered_via",
    "operator_overrides",
    "last_redetect_at",
)

_NO_REGISTER_MARKER = ".agentm-no-register"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -----------------------------------------------------------------------------
# Pure functions
# -----------------------------------------------------------------------------

def _target_dict(states: dict) -> dict:
    return {
        name: {
            "enabled": st.enabled,
            "auto_detected": st.auto_detected,
            "rationale": st.rationale,
            "rule_id": st.rule_id,
            "operator_action": None,
        }
        for name, st in states.items()
    }


def build_enablement_block(
    proposal: dp.ProposedConfig,
    *,
    registered_via: str = "auto-detect",
    now: Optional[str] = None,
) -> dict:
    """Convert a (propose-verdict) ProposedConfig into the enablement block dict.

    Raises ValueError on a bypass proposal — you don't write config for a
    harness repo.
    """
    if proposal.verdict != "propose":
        raise ValueError(f"cannot build enablement block from verdict={proposal.verdict!r}")
    return {
        "type": proposal.type,
        "skills": _target_dict(proposal.skills),
        "hooks": _target_dict(proposal.hooks),
        "registered_at": now or _utcnow_iso(),
        "registered_via": registered_via,
        "operator_overrides": [],
        "last_redetect_at": None,
    }


def merge_enablement(project_json: dict, enablement: dict) -> dict:
    """Merge the enablement block into project.json, preserving every other key.

    `vault_project`, `github`, `env` (and anything else) survive verbatim —
    only the enablement keys are overwritten.
    """
    out = dict(project_json)
    for k in _ENABLEMENT_KEYS:
        if k in enablement:
            out[k] = enablement[k]
    return out


def apply_override(
    config: dict,
    *,
    kind: str,
    target: str,
    action: str = "disabled-at-registration",
    reason: Optional[str] = None,
    now: Optional[str] = None,
) -> dict:
    """Record an operator opt-out: flip the target's enabled→False, set its
    operator_action, and append an entry to operator_overrides.

    `kind` is "skill" or "hook". Returns a new config dict (does not mutate the
    input). Raises KeyError if the target isn't present in the named section.
    """
    if kind not in ("skill", "hook"):
        raise ValueError(f"kind must be 'skill' or 'hook', got {kind!r}")
    section_key = "skills" if kind == "skill" else "hooks"
    out = dict(config)
    section = dict(out.get(section_key, {}))
    if target not in section:
        raise KeyError(f"{target!r} not in {section_key}")
    entry = dict(section[target])
    entry["enabled"] = False
    entry["operator_action"] = action
    section[target] = entry
    out[section_key] = section
    overrides = list(out.get("operator_overrides", []))
    overrides.append(
        {
            "at": now or _utcnow_iso(),
            "skill_or_hook": target,
            "action": action,
            "reason": reason,
        }
    )
    out["operator_overrides"] = overrides
    return out


def is_registered(
    project_json: Optional[dict],
    *,
    backend=None,
    slug: Optional[str] = None,
) -> bool:
    """A project is registered if its project.json carries a non-empty `skills`
    enablement block OR it has an entry in the backend repo_registry."""
    if isinstance(project_json, dict):
        skills = project_json.get("skills")
        if isinstance(skills, dict) and skills:
            return True
    if backend is not None and slug:
        try:
            for r in repo_registry.list_repos(backend):
                if r.get("slug") == slug:
                    return True
        except Exception:
            return False
    return False


# -----------------------------------------------------------------------------
# Re-detection diff (the /setup --redetect follow-up to DC-4)
# -----------------------------------------------------------------------------

# Diff categories, in report order. The first three are the three the V4
# design's "Re-detection flow" names; the last two cover the harness itself
# growing or retiring an enableable target between runs.
CHANGE_NEWLY_DETECTED = "newly-detected"
CHANGE_NO_LONGER_DETECTED = "no-longer-detected"
CHANGE_RULE_CHANGED = "rule-changed"
CHANGE_NEW_TARGET = "new-target"
CHANGE_RETIRED_TARGET = "retired-target"

_CHANGE_ORDER = (
    CHANGE_NEWLY_DETECTED,
    CHANGE_NO_LONGER_DETECTED,
    CHANGE_RULE_CHANGED,
    CHANGE_NEW_TARGET,
    CHANGE_RETIRED_TARGET,
)

# What each category proposes, in the operator's words.
_CHANGE_PROPOSAL: dict[str, str] = {
    CHANGE_NEWLY_DETECTED: "a rule now justifies this — refresh its rationale",
    CHANGE_NO_LONGER_DETECTED: "the rule that justified this no longer matches — consider disabling it",
    CHANGE_RULE_CHANGED: "a different rule justifies this now — refresh its rationale",
    CHANGE_NEW_TARGET: "the harness added this enableable — add it to the config",
    CHANGE_RETIRED_TARGET: "the harness retired this enableable — drop the stale entry",
}

# Categories an operator_override suppresses. A retired target is deliberately
# absent per RD-3: it is inventory housekeeping, not a re-suggestion.
_SUPPRESSIBLE = frozenset(_CHANGE_ORDER) - {CHANGE_RETIRED_TARGET}


@dataclass
class TargetChange:
    """One target whose detection result moved since the config was written."""

    kind: str  # "skill" | "hook"
    name: str
    change: str  # one of the CHANGE_* categories
    was: Optional[dict] = None  # stored TargetState dict, if any
    now: Optional[dict] = None  # freshly-detected TargetState dict, if any
    # Set only on a suppressed change.
    suppressed_by: Optional[dict] = None

    @property
    def proposal(self) -> str:
        return _CHANGE_PROPOSAL[self.change]

    def to_dict(self) -> dict:
        out = {
            "kind": self.kind,
            "name": self.name,
            "change": self.change,
            "proposal": self.proposal,
            "was": self.was,
            "now": self.now,
        }
        if self.suppressed_by is not None:
            out["suppressed_by"] = self.suppressed_by
        return out


@dataclass
class RedetectDiff:
    """The result of diffing a fresh scan against the stored enablement block.

    `changes` are the diffs awaiting operator judgment. `suppressed` are the
    diffs an `operator_overrides` entry already answered — reported so the
    suppression is visible, never folded into `changes` (RD-3).
    """

    changes: list[TargetChange] = field(default_factory=list)
    suppressed: list[TargetChange] = field(default_factory=list)
    type_was: Optional[str] = None
    type_now: Optional[str] = None
    matched_rules: tuple[str, ...] = ()

    @property
    def type_changed(self) -> bool:
        return self.type_was != self.type_now

    def has_changes(self) -> bool:
        return bool(self.changes) or self.type_changed

    def to_dict(self) -> dict:
        return {
            "changes": [c.to_dict() for c in self.changes],
            "suppressed": [c.to_dict() for c in self.suppressed],
            "type": {"was": self.type_was, "now": self.type_now, "changed": self.type_changed},
            "matched_rules": list(self.matched_rules),
            "has_changes": self.has_changes(),
        }


def _override_index(config: dict) -> dict[str, dict]:
    """Map target name -> the most recent operator_overrides entry for it.

    A target also counts as overridden when its stored entry carries a non-null
    `operator_action` — the two are written together by `apply_override`, but a
    hand-edited config may carry only one, and honoring either is the safe read.
    """
    index: dict[str, dict] = {}
    for ov in config.get("operator_overrides", []) or []:
        if isinstance(ov, dict) and ov.get("skill_or_hook"):
            index[str(ov["skill_or_hook"])] = ov
    for section in ("skills", "hooks"):
        for name, entry in (config.get(section) or {}).items():
            if isinstance(entry, dict) and entry.get("operator_action") and name not in index:
                index[name] = {
                    "at": None,
                    "skill_or_hook": name,
                    "action": entry["operator_action"],
                    "reason": None,
                }
    return index


def _classify(stored: Optional[dict], fresh: Optional[dict]) -> Optional[str]:
    """Which CHANGE_* category (if any) describes stored -> fresh for one target."""
    if stored is None and fresh is None:
        return None
    if stored is None:
        return CHANGE_NEW_TARGET
    if fresh is None:
        return CHANGE_RETIRED_TARGET
    was_detected = bool(stored.get("auto_detected"))
    now_detected = bool(fresh.get("auto_detected"))
    if not was_detected and now_detected:
        return CHANGE_NEWLY_DETECTED
    if was_detected and not now_detected:
        return CHANGE_NO_LONGER_DETECTED
    if was_detected and now_detected and stored.get("rule_id") != fresh.get("rule_id"):
        return CHANGE_RULE_CHANGED
    return None


def diff_detection(config: dict, proposal: dp.ProposedConfig) -> RedetectDiff:
    """Diff a fresh detection `proposal` against the stored enablement `config`.

    Pure: reads both, writes neither. Raises ValueError on a bypass proposal —
    there is nothing to diff against a repo detection declines to scan.
    """
    if proposal.verdict != "propose":
        raise ValueError(f"cannot diff a proposal with verdict={proposal.verdict!r}")

    fresh = proposal.to_dict()
    overrides = _override_index(config)
    diff = RedetectDiff(
        type_was=config.get("type"),
        type_now=proposal.type,
        matched_rules=proposal.matched_rules,
    )

    by_category: dict[str, list[TargetChange]] = {c: [] for c in _CHANGE_ORDER}
    suppressed_by_category: dict[str, list[TargetChange]] = {c: [] for c in _CHANGE_ORDER}

    for kind, section in (("skill", "skills"), ("hook", "hooks")):
        stored_section = config.get(section) or {}
        fresh_section = fresh.get(section) or {}
        for name in sorted(set(stored_section) | set(fresh_section)):
            stored = stored_section.get(name)
            new = fresh_section.get(name)
            category = _classify(stored, new)
            if category is None:
                continue
            change = TargetChange(kind=kind, name=name, change=category, was=stored, now=new)
            override = overrides.get(name)
            if override is not None and category in _SUPPRESSIBLE:
                change.suppressed_by = override
                suppressed_by_category[category].append(change)
            else:
                by_category[category].append(change)

    for category in _CHANGE_ORDER:
        diff.changes.extend(by_category[category])
        diff.suppressed.extend(suppressed_by_category[category])
    return diff


def apply_redetect(config: dict, diff: RedetectDiff, *, now: Optional[str] = None) -> dict:
    """Apply a `diff`'s un-suppressed changes to `config`; return a new dict.

    Per RD-2 this refreshes detection metadata only — `enabled` and
    `operator_action` are carried over from the stored entry untouched, so an
    apply can never turn a skill off behind the operator's back. Suppressed
    changes are skipped entirely (RD-3). Also stamps `last_redetect_at`.
    """
    out = dict(config)
    sections = {
        "skills": dict(out.get("skills") or {}),
        "hooks": dict(out.get("hooks") or {}),
    }

    for change in diff.changes:
        section_key = "skills" if change.kind == "skill" else "hooks"
        section = sections[section_key]
        if change.change == CHANGE_RETIRED_TARGET:
            section.pop(change.name, None)
            continue
        fresh = dict(change.now or {})
        stored = change.was or {}
        # Detection owns rationale; the operator owns enablement.
        fresh["enabled"] = stored.get("enabled", fresh.get("enabled", True))
        fresh["operator_action"] = stored.get("operator_action")
        section[change.name] = fresh

    out["skills"] = sections["skills"]
    out["hooks"] = sections["hooks"]
    if diff.type_changed and diff.type_now is not None:
        out["type"] = diff.type_now
    out["last_redetect_at"] = now or _utcnow_iso()
    return out


def render_redetect_text(diff: RedetectDiff, *, repo_name: str, applied: bool = False) -> str:
    """Render the operator-facing re-detect diff."""
    out: list[str] = [f"Re-detected {repo_name}."]
    rules = ", ".join(diff.matched_rules) if diff.matched_rules else "none"
    out.append(f"Rules matching now: {rules}")
    out.append("")

    if not diff.has_changes():
        out.append("No changes — the stored config still matches what this repo looks like.")
    else:
        if diff.type_changed:
            out.append(f"Project type: {diff.type_was!r} -> {diff.type_now!r}")
            out.append("")
        if diff.changes:
            out.append("Proposed changes:" if not applied else "Applied changes:")
            for c in diff.changes:
                out.append(f"  [{c.change}] {c.kind} {c.name}")
                out.append(f"      {c.proposal}")
                if c.change in (CHANGE_NEWLY_DETECTED, CHANGE_RULE_CHANGED) and c.now:
                    out.append(f"      now: {c.now.get('rule_id')} — {c.now.get('rationale')}")
                elif c.change == CHANGE_NO_LONGER_DETECTED and c.was:
                    out.append(f"      was: {c.was.get('rule_id')} — {c.was.get('rationale')}")
            out.append("")

    if diff.suppressed:
        out.append("Suppressed — you already decided these, so re-detect will not re-suggest them:")
        for c in diff.suppressed:
            ov = c.suppressed_by or {}
            reason = ov.get("reason") or ov.get("action") or "operator override"
            at = ov.get("at")
            when = f" on {at}" if at else ""
            out.append(f"  [{c.change}] {c.kind} {c.name} — declined{when} ({reason})")
        out.append("")

    if applied:
        out.append("Applied. Enablement was not touched — `enabled` flags are yours to set.")
        out.append("To turn something off, record it as an override instead of a detection result.")
    elif diff.has_changes():
        out.append("Nothing was changed. Re-run with --apply to refresh the detection rationale.")
        out.append("Applying never flips `enabled` — it only updates why each target is justified.")
    return "\n".join(out) + "\n"


# -----------------------------------------------------------------------------
# I/O wrappers
# -----------------------------------------------------------------------------

def load_project_json(resolution: dict) -> dict:
    """Load project.json via the dispatcher resolution. Returns {} if absent."""
    raw = hm.read_state_file(resolution, "project.json")
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def write_config(resolution: dict, config: dict) -> Path:
    """Atomically write `config` back to project.json.

    Routes through the dispatcher's `write_state_file`, which is backend-aware
    (ADR 0020): it writes to `<vault>/projects/<slug>/_harness/` when a live
    synced backend is active, else to device-local `<repo>/.harness/` (vault
    absent, or a `.project-mode=local` opt-out). This MUST match where
    `load_project_json` read from — both traverse the same seam — otherwise a
    project could read one location and write the other, dropping keys that live
    only in the read location (e.g. `github`/`env`). Returns the path written.
    """
    payload = json.dumps(config, indent=2, ensure_ascii=False) + "\n"
    return hm.write_state_file(resolution, "project.json", payload)


def register(
    cwd: Path,
    *,
    registered_via: str = "auto-detect",
    disable: Optional[list[str]] = None,
) -> dict:
    """Run detection on `cwd`, build + write the enablement block to project.json,
    and register the repo in the vault repo_registry.

    `disable` is a list of skill/hook names to opt out at registration. Returns
    the written config dict. Raises on a bypass verdict.
    """
    cwd = Path(cwd)
    proposal = dp.detect(cwd)
    if proposal.verdict != "propose":
        raise ValueError("detection returned a bypass verdict — not a configurable project")

    resolution = hm.resolve_project({"cwd": cwd})
    project_json = load_project_json(resolution)
    enablement = build_enablement_block(proposal, registered_via=registered_via)
    config = merge_enablement(project_json, enablement)

    for name in disable or []:
        kind = "skill" if name in config.get("skills", {}) else "hook"
        config = apply_override(config, kind=kind, target=name)

    write_config(resolution, config)

    # Register in the backend repo_registry. Best-effort: the config write above
    # already succeeded and registration self-heals on the next run, so a
    # failure is non-fatal — but it is *logged*, never swallowed silently. A
    # failure here is a real signal (a CAS that lost every retry, a lock
    # timeout, an I/O error), not noise.
    backend = resolution.get("backend")
    slug = resolution.get("slug")
    if backend is not None and slug:
        try:
            repo_registry.register_repo(backend, slug, cwd)
        except Exception as e:
            print(
                f"warning: repo registration for {slug!r} failed "
                f"({type(e).__name__}: {e}); will retry on next run",
                file=sys.stderr,
            )
    return config


class NotRegisteredError(RuntimeError):
    """Raised when re-detect runs against a repo that was never registered."""


def redetect(
    cwd: Path,
    *,
    apply: bool = False,
    dry_run: bool = False,
    now: Optional[str] = None,
) -> tuple[RedetectDiff, dict]:
    """Re-scan `cwd`, diff the result against the stored enablement block, and
    return `(diff, config)`.

    Writes nothing to the enablement keys unless `apply=True` (RD-1). The
    default run still advances `last_redetect_at`, because that field records
    when the scan last ran — pass `dry_run=True` for a read-only preview that
    touches the file not at all.

    Raises `NotRegisteredError` when the repo has no enablement block to diff
    against, and `ValueError` on a bypass verdict.
    """
    cwd = Path(cwd)
    resolution = hm.resolve_project({"cwd": cwd})
    config = load_project_json(resolution)
    if not is_registered(config):
        raise NotRegisteredError(
            "no enablement block in project.json — run the detect flow first "
            "(python3 scripts/project_config.py register .)"
        )

    proposal = dp.detect(cwd)
    if proposal.verdict != "propose":
        raise ValueError(
            "detection returned a bypass verdict — this repo is not one detection scans"
        )

    diff = diff_detection(config, proposal)
    if dry_run:
        return diff, config

    if apply:
        config = apply_redetect(config, diff, now=now)
    else:
        config = dict(config)
        config["last_redetect_at"] = now or _utcnow_iso()
    write_config(resolution, config)
    return diff, config


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _cmd_is_registered(cwd: Path) -> int:
    resolution = hm.resolve_project({"cwd": cwd})
    pj = load_project_json(resolution)
    reg = is_registered(pj, backend=resolution.get("backend"), slug=resolution.get("slug"))
    print("registered" if reg else "unconfigured")
    return 0 if reg else 1


def _cmd_should_nudge(cwd: Path) -> int:
    """Exit 0 (+ 'nudge') if this cwd should get the configure nudge; else exit 1."""
    cwd = Path(cwd)
    # `.git` is a dir in a normal clone but a FILE in a git worktree/submodule
    # (`gitdir: …`). Accept either so worktrees still get the nudge.
    if not (cwd / ".git").exists():
        print("silent: not a git repo")
        return 1
    if (cwd / _NO_REGISTER_MARKER).exists():
        print("silent: .agentm-no-register marker present")
        return 1
    if dp.detect(cwd).verdict == "bypass":
        print("silent: harness source repo")
        return 1
    resolution = hm.resolve_project({"cwd": cwd})
    pj = load_project_json(resolution)
    if is_registered(pj, backend=resolution.get("backend"), slug=resolution.get("slug")):
        print("silent: already registered")
        return 1
    print("nudge")
    return 0


def _cmd_register(cwd: Path, registered_via: str, disable: list[str]) -> int:
    try:
        config = register(cwd, registered_via=registered_via, disable=disable)
    except ValueError as e:
        print(f"register: {e}", file=sys.stderr)
        return 2
    print(json.dumps({"slug": config.get("vault_project"), "type": config.get("type"),
                      "registered_via": config.get("registered_via")}, ensure_ascii=False))
    return 0


def _cmd_redetect(cwd: Path, fmt: str, apply: bool, dry_run: bool) -> int:
    """Exit 0 when the config still matches the repo, 1 when changes are
    surfaced (or applied), 2 when re-detect could not run at all."""
    try:
        diff, _ = redetect(cwd, apply=apply, dry_run=dry_run)
    except NotRegisteredError as e:
        print(f"redetect: {e}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"redetect: {e}", file=sys.stderr)
        return 2

    if fmt == "json":
        payload = diff.to_dict()
        payload["applied"] = apply and not dry_run
        payload["dry_run"] = dry_run
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(
            render_redetect_text(
                diff, repo_name=Path(cwd).name, applied=apply and not dry_run
            ),
            end="",
        )
    return 1 if diff.has_changes() else 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="project_config", description="V4 #32 project.json enablement block.")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("is-registered", help="exit 0 if the repo is configured, 1 if not")
    pr.add_argument("cwd", nargs="?", default=".")

    pn = sub.add_parser("should-nudge", help="exit 0 if this cwd should get the configure nudge")
    pn.add_argument("cwd", nargs="?", default=".")

    pg = sub.add_parser("register", help="detect + write the enablement block + register the repo")
    pg.add_argument("cwd", nargs="?", default=".")
    pg.add_argument("--registered-via", default="auto-detect")
    pg.add_argument("--disable", action="append", default=[], help="skill/hook name to opt out (repeatable)")

    prd = sub.add_parser(
        "redetect",
        help="re-scan + diff against the stored enablement block (surfaces; does not apply)",
    )
    prd.add_argument("cwd", nargs="?", default=".")
    prd.add_argument("--format", choices=("text", "json"), default="text", help="output format")
    prd.add_argument(
        "--apply",
        action="store_true",
        help="apply the diff's detection metadata (never flips `enabled`)",
    )
    prd.add_argument(
        "--dry-run",
        action="store_true",
        help="read-only preview — does not even stamp last_redetect_at",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    cwd = Path(args.cwd).resolve()
    if args.cmd == "is-registered":
        return _cmd_is_registered(cwd)
    if args.cmd == "should-nudge":
        return _cmd_should_nudge(cwd)
    if args.cmd == "register":
        return _cmd_register(cwd, args.registered_via, args.disable)
    if args.cmd == "redetect":
        return _cmd_redetect(cwd, args.format, args.apply, args.dry_run)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
