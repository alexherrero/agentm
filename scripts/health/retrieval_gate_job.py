#!/usr/bin/env python3
"""retrieval_gate_job — the nightly runner entry for the regression gate.

The gate itself (`scripts/check-retrieval-regression.sh`) prints a verdict and
exits; this wrapper is what makes that verdict *readable tomorrow*: it runs
the gate, writes `latest_retrieval_gate.json` into the vault's diagnostics
directory beside the scorecards, and the corpus scorecard renders the result
with its age — so a gate that stops running shows up as staleness on the page
somebody reads, rather than as silence.

Never raises past main: a runner job that crashes on a bad environment writes
that fact into the artifact instead, because "the gate could not run" is a
reading, not an absence.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent

sys.path.insert(0, str(_REPO / "harness" / "skills" / "memory" / "scripts"))
import corpus_scorecard as sc  # noqa: E402 — the diagnostics-dir resolver lives there

GATE = _REPO / "scripts" / "check-retrieval-regression.sh"
ARTIFACT_NAME = "latest_retrieval_gate.json"

# The gate's exit codes, translated for the scorecard. 0 covers both PASS and
# SKIP; the verdict line distinguishes them, and both are worth showing.
VERDICTS = {0: "clean-or-skip", 1: "FAIL"}


def run_gate() -> dict:
    try:
        proc = subprocess.run(["bash", str(GATE)], capture_output=True,
                              text=True, timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"exit": None, "verdict": f"gate could not run: {exc}",
                "tail": ""}
    tail = [l for l in (proc.stdout + proc.stderr).strip().splitlines()
            if l.strip()][-3:]
    # The gate itself only ever exits 0 or 1; anything else is the wrapper's
    # environment failing underneath it (127 = command not found), which is
    # a could-not-run reading, not a verdict about the ranker.
    verdict = VERDICTS.get(proc.returncode,
                           f"gate could not run (exit {proc.returncode})")
    if proc.returncode == 0:
        joined = "\n".join(tail)
        verdict = "SKIP" if "SKIP" in joined else "PASS"
    return {"exit": proc.returncode, "verdict": verdict,
            "tail": "\n".join(tail)}


def artifact_path() -> Path:
    """The diagnostics dir the scorecards use, resolved the same way."""
    vault = sc.vault_from_daemon()
    if not vault:
        raise SystemExit("retrieval-gate-job: no vault resolvable — the daemon "
                         "is not answering and nothing says where diagnostics "
                         "live. Not writing an artifact into a guess.")
    out_dir = Path(vault) / sc.diagnostics_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / ARTIFACT_NAME


def main() -> int:
    result = run_gate()
    result["at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = artifact_path()
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    print(f"retrieval-gate-job: {result['verdict']} — artifact at {path}")
    # The job's own exit mirrors the gate's, so the runner's log shows red on
    # a regression without anyone opening the artifact.
    return 1 if result["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
