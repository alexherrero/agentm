#!/usr/bin/env python3
"""docs_drift_job.py — agentm-side delegator for the Maintainer persona's
docs-drift runner job (PLAN-wave-e-scheduled-surfaces task 2, agentm-runner.md).

Docs-drift detection ("has the code moved away from what the wiki describes")
is mechanism crickets already owns via the wiki-watcher (W1): its deterministic
ENGINE (`wiki_watch_cycle.run_cycle`) polls, detects doc-worthy candidates, and
produces a dispatch PLAN — but never writes anything and never spawns the
`documenter` (that's the wiki-watch SKILL's job, an agent-level step this bare
cron job cannot take). That split is exactly what makes it safe to run
unattended from the agentm runner: this job only ever reports, it never
authors.

This file locates the crickets sibling checkout and delegates to its
`wiki_watch_cycle.py` CLI, so agentm's own repo gets docs-drift detection
without a second copy of the detection logic (mirrors the existing cross-repo
sibling-path pattern in crickets' wiki_watch_config.find_agentm_script, run in
reverse — the same convention scripts/check-slop.py already established:
agentm reaching into crickets instead of crickets into agentm).

Resolution order (first hit wins):
  1. $CRICKETS_REPO_ROOT/src/wiki/scripts/wiki_watch_cycle.py (explicit override)
  2. <this-repo>/../crickets/src/wiki/scripts/wiki_watch_cycle.py (sibling
     checkout, the documented `~/Antigravity/agentm` + `~/Antigravity/crickets`
     layout)

Graceful-skip (exit 0) when the sibling isn't checked out, or when
wiki-watch itself isn't opted into for this repo (device toggle absent, or no
`.harness/wiki-watch.json` marker) — `run_cycle` reports that skip itself; this
delegator never treats "not opted in" as a failure.

Stdlib-only.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_AGENTM_ROOT = _HERE.parent


def find_crickets_wiki_watch_cycle() -> Path | None:
    env_dir = os.environ.get("CRICKETS_REPO_ROOT", "").strip()
    candidates = []
    if env_dir:
        candidates.append(
            Path(os.path.expanduser(env_dir)) / "src" / "wiki" / "scripts" / "wiki_watch_cycle.py"
        )
    candidates.append(
        _AGENTM_ROOT.parent / "crickets" / "src" / "wiki" / "scripts" / "wiki_watch_cycle.py"
    )
    for c in candidates:
        if c.is_file():
            return c
    return None


def _load_crickets_module(path: Path):
    spec = importlib.util.spec_from_file_location("crickets_wiki_watch_cycle", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["crickets_wiki_watch_cycle"] = mod
    spec.loader.exec_module(mod)
    return mod


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="docs-drift", description=__doc__)
    p.add_argument("--repo", default=".", help="repo root to scan for drift (default: cwd)")
    p.add_argument("--slug", default="")
    p.add_argument("--no-cooldown", action="store_true",
                    help="ignore the cooldown gate (manual/forced run)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    crickets_script = find_crickets_wiki_watch_cycle()
    if crickets_script is None:
        print("docs-drift: crickets sibling checkout not found "
              "($CRICKETS_REPO_ROOT or ../crickets) — skipping (report-only, no block)")
        return 0
    mod = _load_crickets_module(crickets_script)
    return mod.main([
        "run",
        "--repo", args.repo,
        *(["--slug", args.slug] if args.slug else []),
        *(["--no-cooldown"] if args.no_cooldown else []),
    ])


if __name__ == "__main__":
    raise SystemExit(main())
