#!/usr/bin/env python3
"""dispatch.py — control-plane dispatch wiring through Agent View
(PLAN-autonomy-control-plane task 2). Agent View (`claude --bg` / `claude
agents`) is the substrate task 1 resolved: process-supervised background
sessions that survive machine sleep/restart, unlike Agent Teams' in-process
teammates.

Per-item model/effort resolves via crickets' `classify_work_type()` (the
same three-step resolution every other dispatch point in this harness
uses — persona-declared -> role-name match -> `UNCLASSIFIED-DEFAULT`,
never a silent guess, never `claude-fable-5`), resolved the same
sibling-clone way `session_cost_writer.py` resolves agentm's `save.py`.

Plan/task attribution rides the worktree-local `.harness/active-plan`
marker `event_log.resolve_attribution_tags()` already reads (built by
`PLAN-observability-ledger`) -- this module WRITES that marker into the
dispatch cwd before spawning; it never re-implements the stamping itself.
No host schema carries a plan-ID field (confirmed live, task 1's own
re-verification), so the marker is the only channel.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
_CRICKETS_ROUTING_REL = Path("src") / "tokens" / "scripts"

_classify_module = None
_classify_loaded = False


def _candidate_routing_dirs() -> list[Path]:
    candidates = []
    env_dir = os.environ.get("CRICKETS_SCRIPTS_DIR", "").strip()
    if env_dir:
        candidates.append(Path(os.path.expanduser(env_dir)))
    candidates.append(Path.home() / "Antigravity" / "crickets" / _CRICKETS_ROUTING_REL)
    return candidates


def _find_routing_dir() -> "Path | None":
    for candidate in _candidate_routing_dirs():
        if (candidate / "classify_work_type.py").is_file():
            return candidate
    return None


def load_classify_module():
    """Return crickets' classify_work_type module, loaded once and cached.
    None if crickets is unresolvable (graceful-return; `resolve_dispatch_
    classification()` falls back to the classifier's own fixed default)."""
    global _classify_module, _classify_loaded
    if _classify_loaded:
        return _classify_module
    _classify_loaded = True
    d = _find_routing_dir()
    if d is None:
        _classify_module = None
        return None
    spec = importlib.util.spec_from_file_location("crickets_classify_bridge_dispatch", d / "classify_work_type.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["crickets_classify_bridge_dispatch"] = module
    spec.loader.exec_module(module)
    _classify_module = module
    return module


def _reset_cache_for_tests() -> None:
    global _classify_module, _classify_loaded
    _classify_module = None
    _classify_loaded = False


@dataclass(frozen=True)
class WorkItem:
    plan: str
    task: str
    prompt: str
    role_name: "str | None" = None
    declared: "dict | None" = None
    cwd: "str | None" = None  # defaults to Path.cwd() at dispatch time if None


@dataclass(frozen=True)
class DispatchResult:
    name: str
    plan: str
    task: str
    model_alias: "str | None"
    model_id: str
    effort: str
    tier_source: str
    cwd: str
    returncode: int
    stdout: str
    stderr: str


# The same fixed, hardcoded safe default crickets' classify_work_type.py
# uses when crickets itself IS resolvable but returns UNCLASSIFIED-DEFAULT
# — kept here too as this module's own fallback for the (separate) case
# where crickets is not resolvable at all, so the two failure modes read
# identically to a caller rather than diverging by accident.
_FALLBACK_MODEL_ID = "claude-sonnet-5"
_FALLBACK_EFFORT = "medium"
_FALLBACK_MODEL_ALIAS = "sonnet"
_FALLBACK_TIER_SOURCE = "UNCLASSIFIED-DEFAULT"


def resolve_dispatch_classification(item: WorkItem) -> dict:
    """Resolve `{model_id, effort, tier_source, model_alias}` for a work
    item via crickets' `classify_work_type()`. Never guesses: falls back to
    the classifier's own fixed `UNCLASSIFIED-DEFAULT` shape when crickets
    itself can't be resolved."""
    classify = load_classify_module()
    if classify is None:
        return {
            "model_id": _FALLBACK_MODEL_ID, "effort": _FALLBACK_EFFORT,
            "tier_source": _FALLBACK_TIER_SOURCE, "model_alias": _FALLBACK_MODEL_ALIAS,
        }
    c = classify.classify_work_type(role_name=item.role_name, declared=item.declared)
    alias = classify.agent_tool_alias(c.model_id)
    return {"model_id": c.model_id, "effort": c.effort, "tier_source": c.tier_source, "model_alias": alias}


def dispatch_name(item: WorkItem) -> str:
    return f"{item.plan}-{item.task}"


def _write_active_plan_marker(cwd: Path, plan_slug: str) -> None:
    harness_dir = cwd / ".harness"
    harness_dir.mkdir(parents=True, exist_ok=True)
    (harness_dir / "active-plan").write_text(f"{plan_slug}\n", encoding="utf-8")


def build_dispatch_command(item: WorkItem, classification: dict, *, claude_bin: str = "claude") -> list[str]:
    """The exact `claude --bg ...` argv for this work item. Pure — no I/O,
    easy to assert against in tests without spawning a real process."""
    model_arg = classification["model_alias"] or classification["model_id"]
    return [
        claude_bin, "--bg",
        "--model", model_arg,
        "--effort", classification["effort"],
        "--name", dispatch_name(item),
        item.prompt,
    ]


def dispatch(item: WorkItem, *, claude_bin: str = "claude", runner=subprocess.run) -> DispatchResult:
    """Spawn one work item as a `claude --bg` background session (Agent
    View). Writes the active-plan marker into the dispatch cwd first, so
    the harness's own attribution stamping picks up `plan` automatically
    once the dispatched session's Stop hook fires — this function never
    writes a ledger event itself.
    """
    cwd = Path(item.cwd) if item.cwd is not None else Path.cwd()
    _write_active_plan_marker(cwd, item.plan)
    classification = resolve_dispatch_classification(item)
    cmd = build_dispatch_command(item, classification, claude_bin=claude_bin)
    proc = runner(cmd, cwd=str(cwd), capture_output=True, text=True)
    return DispatchResult(
        name=dispatch_name(item), plan=item.plan, task=item.task,
        model_alias=classification["model_alias"], model_id=classification["model_id"],
        effort=classification["effort"], tier_source=classification["tier_source"],
        cwd=str(cwd), returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr,
    )


def list_agent_view_sessions(*, all_sessions: bool = False, claude_bin: str = "claude", runner=subprocess.run) -> list[dict]:
    """List live (and, with `all_sessions=True`, completed) Agent View
    sessions via `claude agents --json`. Returns `[]` on any exec/parse
    failure — a fleet-status listing must degrade gracefully, never crash a
    caller checking on dispatched work."""
    cmd = [claude_bin, "agents", "--json"]
    if all_sessions:
        cmd.append("--all")
    try:
        proc = runner(cmd, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError):
        return []
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []
