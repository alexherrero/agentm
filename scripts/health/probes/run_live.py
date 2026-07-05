#!/usr/bin/env python3
"""run_live.py — the live weekly runner for the D-⑫ seeded task-pair battery
(PLAN-r3-uplift-scoring task 4 / R3.2b).

For each of Task 3's four seeded task pairs (checkers.py), seeds a bare
fixture directory (no agentm context) and a backed fixture directory
(a fixture .harness/vault state matching the pair's scenario), invokes the
real `claude` CLI headlessly (--print --output-format json) against each
with the pair's seed prompt, extracts the assistant's response as the
transcript, and scores it with checkers.py's deterministic check() — the
same checker Task 3 already proved discriminates against hand-built fixtures.

Appends one trend-line row to scripts/health/probe-history.jsonl, keyed
(agentm_sha, fixture_pack_version, rule_pack_version) — the same versioning
discipline scripts/health/health_score.py's own history.jsonl uses
(PLAN-r1-dashboard.md's Locked design calls; this plan's own Locked design
calls: "History versioning inherits the R1.8 contract unchanged").

Advisory only: this script's exit code reflects whether the RUN completed
(setup succeeded, the claude CLI was reachable), never the probes' pass/fail
— pass/fail is trend-line data for the scorecard, never a gate. The caller
(.github/workflows/probe-weekly.yml) never treats a probe failure as a CI
failure, matching health-nightly.yml's own advisory-only convention.

Requires the `claude` CLI on PATH with API credentials configured. Not
exercised by scripts/check-all.sh or any fast-tier CI job — this is Task 4's
own scheduled-workflow scope (weekly, advisory, real model calls), mirroring
how run-heavy-tier.sh is excluded from the fast battery.

Usage:
    python3 scripts/health/probes/run_live.py [--model <model>]
Exit:
    0  the run completed (probe pass/fail is data, not this script's outcome)
    2  setup error (claude CLI not found)
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HEALTH_DIR = HERE.parent
REPO = HEALTH_DIR.parent.parent
PROBE_HISTORY_PATH = HEALTH_DIR / "probe-history.jsonl"

sys.path.insert(0, str(HERE))
import checkers  # noqa: E402

FIXTURE_PACK_VERSION = "v1"
RULE_PACK_VERSION = "v1"


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def _invoke_claude(prompt: str, cwd: Path, *, model: str | None = None) -> str:
    """Run `claude --print` headlessly in `cwd`, return the assistant's final
    text response. Raises RuntimeError on a non-zero exit or unparseable
    output — the caller records that as a per-probe error, not a crash."""
    cmd = ["claude", "--print", "--output-format", "json", prompt]
    if model:
        cmd += ["--model", model]
    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI exited {result.returncode}: {result.stderr[-500:]}")
    payload = json.loads(result.stdout)
    return payload.get("result", "")


def _seed_bare(tmp: Path) -> Path:
    """A bare fixture: an empty scratch directory — no agentm state, no
    .harness/, nothing but the prompt itself."""
    bare = tmp / "bare"
    bare.mkdir(parents=True, exist_ok=True)
    return bare


def _seed_backed_recall_a_prior_decision(tmp: Path) -> Path:
    backed = tmp / "backed"
    (backed / ".harness").mkdir(parents=True, exist_ok=True)
    (backed / ".harness" / "PLAN.md").write_text(
        "# Plan\n\n## Locked design calls\n\n"
        "- vault root renamed to Agent (V5-3) — do not reuse the old path.\n",
        encoding="utf-8",
    )
    return backed


def _seed_backed_find_the_planted_bug(tmp: Path) -> Path:
    backed = tmp / "backed"
    scripts_dir = backed / "harness" / "skills" / "memory" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "recall.py").write_text(
        "# recall.py:412 drops the high-priority entry when the always-load "
        "budget is exceeded (planted defect fixture — R1.8 voice#0).\n",
        encoding="utf-8",
    )
    return backed


def _seed_backed_cold_resume_from_harness(tmp: Path) -> Path:
    backed = tmp / "backed"
    (backed / ".harness").mkdir(parents=True, exist_ok=True)
    (backed / ".harness" / "PLAN.md").write_text(
        "# Plan\n\n## Tasks\n\n"
        "### 1. Earlier task\n- **Status:** [x]\n\n"
        "### 2. task 3 — the D-⑫ seeded task-pair battery\n- **Status:** [ ]\n",
        encoding="utf-8",
    )
    (backed / ".harness" / "progress.md").write_text(
        "2026-07-05 completed task 1\n", encoding="utf-8",
    )
    return backed


def _seed_backed_preference_adherence(tmp: Path) -> Path:
    backed = tmp / "backed"
    always_load = backed / "_vault" / "_always-load"
    always_load.mkdir(parents=True, exist_ok=True)
    (always_load / "commit-message-convention.md").write_text(
        "---\npriority: high\n---\n"
        "Do not append a Co-Authored-By trailer naming the agent to git commit messages.\n",
        encoding="utf-8",
    )
    return backed


_SEED_BACKED = {
    "recall-a-prior-decision": _seed_backed_recall_a_prior_decision,
    "find-the-planted-bug": _seed_backed_find_the_planted_bug,
    "cold-resume-from-.harness": _seed_backed_cold_resume_from_harness,
    "preference-adherence": _seed_backed_preference_adherence,
}

_SEED_PROMPT = {
    "recall-a-prior-decision": "What did we decide about the vault root path?",
    "find-the-planted-bug": "Is there anything wrong with the recall path?",
    "cold-resume-from-.harness": "Continue where we left off.",
    "preference-adherence": "Draft the commit message for this change.",
}


def run_probe(probe, *, model: str | None) -> dict:
    """Run one probe's bare + backed live sessions, score both with the
    probe's own checker. On any invocation failure, records the error and
    leaves both pass fields None — a probe error is data, not a crash."""
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        bare_dir = _seed_bare(tmp)
        backed_dir = _SEED_BACKED[probe.NAME](tmp)
        prompt = _SEED_PROMPT[probe.NAME]

        try:
            bare_text = _invoke_claude(prompt, bare_dir, model=model)
            backed_text = _invoke_claude(prompt, backed_dir, model=model)
        except Exception as e:
            return {"subsystem": probe.NAME, "backed_pass": None, "bare_pass": None, "error": str(e)}

        bare_transcript = [{"role": "user", "content": prompt}, {"role": "assistant", "content": bare_text}]
        backed_transcript = [{"role": "user", "content": prompt}, {"role": "assistant", "content": backed_text}]

        return {
            "subsystem": probe.NAME,
            "backed_pass": bool(probe.check(backed_transcript)),
            "bare_pass": bool(probe.check(bare_transcript)),
        }


def append_history_row(results: list[dict], *, model: str | None, trigger: str = "scheduled") -> dict:
    row = {
        "agentm_sha": _git_sha(),
        "fixture_pack_version": FIXTURE_PACK_VERSION,
        "rule_pack_version": RULE_PACK_VERSION,
        "ts": int(time.time()),
        "model": model or "default",
        "trigger": trigger,
        "results": results,
    }
    PROBE_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PROBE_HISTORY_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", default=None, help="model alias/name to pass to the claude CLI")
    p.add_argument("--trigger", default="scheduled", choices=("scheduled", "manual", "model-upgrade"),
                   help="why this run fired — recorded in the history row, never affects scoring")
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    if shutil.which("claude") is None:
        print("run_live: the `claude` CLI is not on PATH — cannot run live probes", file=sys.stderr)
        return 2

    results = [run_probe(probe, model=args.model) for probe in checkers.ALL_PROBES]
    row = append_history_row(results, model=args.model, trigger=args.trigger)
    print(json.dumps(row, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
