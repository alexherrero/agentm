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
`apply_override`, `is_registered`. I/O wrappers: `load_project_json`,
`write_config`, `register`. CLI: `is-registered`, `should-nudge`, `register`.

Stdlib-only. Cross-platform.
"""
from __future__ import annotations

import argparse
import json
import sys
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
    vault_path: Optional[Path] = None,
    slug: Optional[str] = None,
) -> bool:
    """A project is registered if its project.json carries a non-empty `skills`
    enablement block OR it has an entry in the vault repo_registry."""
    if isinstance(project_json, dict):
        skills = project_json.get("skills")
        if isinstance(skills, dict) and skills:
            return True
    if vault_path is not None and slug:
        try:
            for r in repo_registry.list_repos(vault_path):
                if r.get("slug") == slug:
                    return True
        except Exception:
            return False
    return False


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

    Routes through the dispatcher's `write_state_file`, which is
    `.project-mode`-aware (writes to legacy `<repo>/.harness/` when the project
    opted out of vault-mode). This MUST match where `load_project_json` read
    from — otherwise a local-mode project would read the legacy file but write
    the vault file, dropping the vault's `github`/`env` keys. Raises ValueError
    if the resolution lacks a vault_path.
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

    # Register in the vault repo_registry (best-effort; skip silently if no vault).
    v = hm.vault_path()
    slug = resolution.get("slug")
    if v is not None and slug:
        try:
            repo_registry.register_repo(v, slug, cwd)
        except Exception:
            pass
    return config


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _cmd_is_registered(cwd: Path) -> int:
    resolution = hm.resolve_project({"cwd": cwd})
    pj = load_project_json(resolution)
    reg = is_registered(pj, vault_path=hm.vault_path(), slug=resolution.get("slug"))
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
    if is_registered(pj, vault_path=hm.vault_path(), slug=resolution.get("slug")):
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
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
