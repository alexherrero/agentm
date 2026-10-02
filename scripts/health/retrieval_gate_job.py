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
import os
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

# The gate's exit codes, translated for the scorecard. One code per verdict:
# until 2026-09-18 a skip and a pass both exited 0 and this dict said
# `clean-or-skip`, so the artifact's verdict was decided by grepping the gate's
# last three lines for the string "SKIP". A measurement that never ran was one
# reworded log line away from being recorded as PASS — which is how a decay
# flip nearly went in behind a gate that had printed `no reachable vault`.
VERDICTS = {0: "PASS", 1: "FAIL", 2: "SKIP"}


def embed_first() -> dict:
    """Bring the vector arm current before the gate grades it (task 182 step 7).

    Nothing scheduled `agentmd embed`, so a day of moves and rewrites left the
    corpus with stale vectors and the gate skipped: four of thirteen nights from
    2026-09-19 to 2026-10-01 measured nothing. The embed is recorded beside the
    verdict and never decides it — a failed embed still lets the gate run, and
    the gate says SKIP on its own if the corpus is still stale.
    """
    binary = os.environ.get("AGENTMD", "").strip() or "agentmd"
    try:
        proc = subprocess.run([binary, "embed"], capture_output=True, text=True, timeout=1200)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"exit": None, "tail": f"embed could not run: {exc}"}
    tail = [l for l in (proc.stdout + proc.stderr).strip().splitlines() if l.strip()][-2:]
    return {"exit": proc.returncode, "tail": "\n".join(tail)}


def run_gate() -> dict:
    try:
        proc = subprocess.run(["bash", str(GATE)], capture_output=True,
                              text=True, timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"exit": None, "verdict": f"gate could not run: {exc}",
                "tail": ""}
    tail = [l for l in (proc.stdout + proc.stderr).strip().splitlines()
            if l.strip()][-3:]
    # The gate exits 0, 1 or 2 and nothing else; anything else is the wrapper's
    # environment failing underneath it (127 = command not found), which is
    # a could-not-run reading, not a verdict about the ranker. Read from the
    # exit code alone — the tail is evidence for a human, never the verdict.
    verdict = VERDICTS.get(proc.returncode,
                           f"gate could not run (exit {proc.returncode})")
    return {"exit": proc.returncode, "verdict": verdict,
            "measured": verdict in ("PASS", "FAIL"),
            "tail": "\n".join(tail)}


def artifact_path() -> Path:
    """The diagnostics dir the scorecards use, resolved the same way.

    Rooted at the *memory* root, which is what `diagnostics_dir()` is relative
    to. This asked for the vault root instead and wrote a second `diagnostics/`
    beside the memory space — the same wrong-root mistake the corpus scorecard
    made on 2026-09-04, in the caller its fix note said deserved the same look.
    Both roots exist and both joins produce a plausible path; only one of them
    is read by anything.
    """
    root = sc.memory_root_from_daemon()
    if not root:
        raise SystemExit("retrieval-gate-job: no memory root resolvable — the "
                         "daemon is not answering and nothing says where "
                         "diagnostics live. Not writing an artifact into a guess.")
    out_dir = Path(root) / sc.diagnostics_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / ARTIFACT_NAME


def main() -> int:
    embed = embed_first()
    result = run_gate()
    result["embed"] = embed
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
