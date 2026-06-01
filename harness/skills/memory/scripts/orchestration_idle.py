#!/usr/bin/env python3
"""orchestration_idle.py — the idle-time orchestration chain (V4 #23 task 4).

The agentm `memory-reflect-idle` hook fires this driver to run a small, bounded,
cooldown-gated chain of memory operations during idle time (SessionStart / cron).
It is the "push surface" counterpart to the SessionStart briefing (task 3): the
briefing *surfaces* pending state; this chain *produces* it (reflects unseen
sessions into the inbox, refreshes skill-discovery sources, stages adapt
candidates) so the next briefing has something to show.

Chain steps, in order — each underlying script already self-no-ops when its input
is empty, so the driver just runs them in sequence and records the outcome:

  1. reflect-corpus  : `reflect.py corpus --execute --batch-size 5 --max-batches 1`
                       — proactively mine ≤5 unseen session transcripts per pass
                       (bounded; resumes next pass via reflect's seen-state).
  2. discover-skills : `discover_skills.py --cadence-check`
                       — refresh discovery sources, self-throttled to the cadence.
  3. adapt-pass1     : `adapt_skills.py --limit 3`
                       — Pass-1 enrichment + rubric scoring; stages ≤3 candidate
                       JSONs for the adapt-evaluator (Pass-2, agent-side) to judge.

Pass-2 (the `adapt-evaluator` sub-agent) is intentionally NOT run here: a hook
fires outside the agent loop and cannot dispatch a sub-agent. The chain stages
Pass-1 candidates and surfaces the staged count; the Pass-2 hand-off lands as a
phase-dispatch (task 5) / nudge (task 6) where sub-agent dispatch is legitimate
and operator-gated. (V4 #23 DC-1: hook/file-based; operator call 2026-06-01.)

The whole chain is cooldown-gated by the task-2 state (`idle_chain`, default 24h)
and the `enable_idle_chain` toggle. `--dry-run` returns the resolved step plan
without invoking anything or touching state — the testable seam, alongside the
injectable `runner`. The driver never raises (it runs from a non-blocking hook).
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# sibling import (same scripts dir; Python puts the script dir on sys.path[0],
# and tests insert it explicitly)
import auto_orchestration as ao

_CHAIN = "idle_chain"

# Bounds (the plan's locked per-pass caps; constants for v1 — could become
# config keys if the real-use dogfood shows they need tuning).
_CORPUS_BATCH_SIZE = 5     # ≤5 unseen sessions per pass
_CORPUS_MAX_BATCHES = 1    # one batch per pass
_ADAPT_LIMIT = 3           # ≤3 newly-evaluated adapt candidates per pass

# Per-step wall-clock budgets (seconds). The driver runs detached from the 30s
# SessionStart hook timeout, so these bound a real chain run, not session boot.
_STEP_TIMEOUT_SEC = {
    "reflect-corpus": 120,
    "discover-skills": 60,
    "adapt-pass1": 90,
}


def _scripts_dir() -> Path:
    return Path(__file__).resolve().parent


def _script(name: str) -> str:
    return str(_scripts_dir() / name)


def plan_steps(vault: Path) -> list[tuple[str, list[str]]]:
    """The ordered chain as (step-name, argv) pairs. argv is the script + flags
    (the runner prepends the python executable). Pure — no side effects — so the
    dry-run plan and the executed chain share one definition."""
    v = str(vault)
    return [
        ("reflect-corpus", [
            _script("reflect.py"), "corpus",
            "--vault-path", v,
            "--execute",
            "--batch-size", str(_CORPUS_BATCH_SIZE),
            "--max-batches", str(_CORPUS_MAX_BATCHES),
        ]),
        ("discover-skills", [
            _script("discover_skills.py"),
            "--vault-path", v,
            "--cadence-check",
        ]),
        ("adapt-pass1", [
            _script("adapt_skills.py"),
            "--vault-path", v,
            "--limit", str(_ADAPT_LIMIT),
        ]),
    ]


def _default_runner(name: str, argv: list[str]) -> dict:
    """Run one chain step as a subprocess; return a result dict. Never raises —
    a timeout or spawn failure degrades to a non-zero returncode so the chain
    continues to the next step."""
    try:
        proc = subprocess.run(
            [sys.executable, *argv],
            capture_output=True,
            text=True,
            timeout=_STEP_TIMEOUT_SEC.get(name, 90),
        )
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        return {"returncode": 124, "stdout": "", "stderr": "timeout", "timed_out": True}
    except Exception as e:  # spawn failure, etc. — degrade, don't raise.
        return {"returncode": 1, "stdout": "", "stderr": str(e), "timed_out": False}


def _count_staged(vault: Path) -> int:
    """Count staged Pass-1 candidate JSONs awaiting adapt-evaluation (Pass-2).
    Lives under <vault>/_meta/skill-discovery-cache/adapt-state/<source>/*.json
    (the sibling `evaluated.json` bookkeeping file sits at the root and is a
    file, not a source dir, so it's skipped). Never raises → 0 on any error."""
    root = vault / "_meta" / "skill-discovery-cache" / "adapt-state"
    if not root.is_dir():
        return 0
    n = 0
    try:
        for source_dir in root.iterdir():
            if not source_dir.is_dir():
                continue
            n += sum(1 for p in source_dir.glob("*.json") if p.is_file())
    except OSError:
        return n
    return n


def _summarize(name: str, r: dict) -> str:
    """Best-effort one-word/short outcome for the step (logging only; never
    raises). Distinguishes 'ran' from 'noop' where the underlying script makes
    it cheap to tell, else falls back to the returncode."""
    try:
        if r.get("timed_out"):
            return "timeout"
        rc = r.get("returncode")
        out = (r.get("stdout") or "") + "\n" + (r.get("stderr") or "")
        if name == "adapt-pass1":
            try:
                summary = json.loads(r.get("stdout") or "{}")
                ev = summary.get("evaluated_count", 0)
                return f"evaluated {ev}" if ev else "noop"
            except (ValueError, AttributeError):
                return "ran" if rc == 0 else f"rc={rc}"
        if name == "reflect-corpus":
            return "noop" if "nothing to process" in out else ("ran" if rc == 0 else f"rc={rc}")
        if name == "discover-skills":
            low = out.lower()
            if "throttl" in low or "cadence" in low and "skip" in low:
                return "throttled"
            return "ran" if rc == 0 else f"rc={rc}"
        return "ran" if rc == 0 else f"rc={rc}"
    except Exception:
        return "?"


def run_idle_chain(
    vault: Path,
    config: dict | None = None,
    now: datetime | None = None,
    *,
    dry_run: bool = False,
    runner=None,
) -> dict:
    """Run (or, with dry_run, plan) the idle orchestration chain. Returns a
    result dict; never raises. Status is one of:
      disabled  — enable_idle_chain is false
      dry-run   — plan returned, nothing executed, no state touched
      cooldown  — within the idle_chain cooldown window, nothing executed
      ran       — chain executed; last_fire recorded
      error     — unexpected failure (swallowed → empty-ish result)
    """
    vault = Path(vault)
    if now is None:
        now = datetime.now(timezone.utc)
    if runner is None:
        runner = _default_runner
    result: dict = {"chain": _CHAIN, "status": None, "dry_run": dry_run, "steps": []}
    try:
        if config is None:
            config = ao.load_config(vault)
        if not config.get("enable_idle_chain", True):
            result["status"] = "disabled"
            return result
        state = ao.load_state(vault)
        cooldown = float(config.get("idle_chain_cooldown_hours", 0) or 0)
        cooldown_ok = ao.should_fire(state, _CHAIN, now, cooldown)
        steps = plan_steps(vault)

        if dry_run:
            # Pure planning: no runner, no state write. The testable seam.
            result["status"] = "dry-run"
            result["cooldown_ok"] = cooldown_ok
            result["steps"] = [{"name": n, "argv": argv} for n, argv in steps]
            return result

        if not cooldown_ok:
            result["status"] = "cooldown"
            return result

        # Execute the chain in order. Each underlying script self-no-ops when its
        # input is empty; the driver records each step's outcome and continues
        # regardless (a failed/empty step does not abort the chain).
        for name, argv in steps:
            r = runner(name, argv)
            result["steps"].append({
                "name": name,
                "argv": argv,
                "returncode": r.get("returncode"),
                "timed_out": bool(r.get("timed_out", False)),
                "outcome": _summarize(name, r),
            })

        result["staged_candidates"] = _count_staged(vault)
        ao.record_fire(state, _CHAIN, now)
        ao.save_state(vault, state)
        result["status"] = "ran"
        return result
    except Exception as e:  # never raise out of a hook-invoked driver.
        result["status"] = "error"
        result["error"] = str(e)
        return result


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="orchestration_idle.py")
    parser.add_argument("--vault-path", default=None)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print the resolved step plan without running anything or "
             "touching state (does not consult/record the cooldown).",
    )
    args = parser.parse_args(argv[1:])
    try:
        vault = ao._resolve_vault_path(args.vault_path)
    except ValueError:
        return 0  # no vault → silent, non-blocking
    result = run_idle_chain(vault, dry_run=args.dry_run)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
