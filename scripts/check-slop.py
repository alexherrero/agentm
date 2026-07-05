#!/usr/bin/env python3
"""check-slop.py — agentm-side delegator (PLAN-r3-voice-mechanism task 2).

The anti-slop prose gate + its rule pack are mechanism owned by crickets
(voice-cascade-architecture.md: voice is a crickets-side capability) — see
crickets `scripts/check-slop.py` for the real implementation and
`src/wiki-maintenance/skills/diataxis-author/style/voice-rules.json` for the
one shared rule pack. This file locates the crickets sibling checkout and
delegates to it, so agentm's own wiki/ gets the same gate without a second
copy of the rule pack or the scanning logic (mirrors the existing cross-repo
sibling-path pattern in crickets' wiki_watch_config.find_agentm_script, run
in reverse: agentm reaching into crickets instead of crickets into agentm).

Resolution order (first hit wins):
  1. $CRICKETS_REPO_ROOT/scripts/check-slop.py   (explicit operator override)
  2. <this-repo>/../crickets/scripts/check-slop.py (sibling checkout, the
     documented `~/Antigravity/agentm` + `~/Antigravity/crickets` layout)

Graceful-skip (exit 0, dark/skipped jsonl record) when the sibling isn't
checked out — this gate is report-only in both repos' batteries, so a skip
here never blocks agentm's own check-all.sh.

Stdlib-only.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_AGENTM_ROOT = _HERE.parent


def find_crickets_check_slop() -> Path | None:
    env_dir = os.environ.get("CRICKETS_REPO_ROOT", "").strip()
    candidates = []
    if env_dir:
        candidates.append(Path(os.path.expanduser(env_dir)) / "scripts" / "check-slop.py")
    candidates.append(_AGENTM_ROOT.parent / "crickets" / "scripts" / "check-slop.py")
    for c in candidates:
        if c.is_file():
            return c
    return None


def _load_crickets_module(path: Path):
    spec = importlib.util.spec_from_file_location("crickets_check_slop", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["crickets_check_slop"] = mod
    spec.loader.exec_module(mod)
    return mod


def _emit_skip_record(jsonl_out: str | None) -> None:
    if not jsonl_out:
        return
    record = {
        "suite": "check-slop", "axis": "docs+voice health",
        "check": "voice-vocabulary-drift", "pass": None, "weight": 5,
    }
    with open(jsonl_out, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="check-slop", description=__doc__)
    p.add_argument("paths", nargs="*", default=["wiki"])
    p.add_argument("--strict", action="store_true")
    p.add_argument("--report", action="store_true")
    p.add_argument("--jsonl-out", default=None)
    p.add_argument("--vault-path", default=None)
    p.add_argument("--project-slug", default=None)
    p.add_argument("--wiki-root", default=None)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    crickets_script = find_crickets_check_slop()
    if crickets_script is None:
        print("check-slop: crickets sibling checkout not found "
              "($CRICKETS_REPO_ROOT or ../crickets) — skipping (report-only, no block)")
        _emit_skip_record(args.jsonl_out)
        return 0
    mod = _load_crickets_module(crickets_script)
    return mod.main([
        *args.paths,
        *(["--strict"] if args.strict else []),
        *(["--report"] if args.report else []),
        *(["--jsonl-out", args.jsonl_out] if args.jsonl_out else []),
        *(["--vault-path", args.vault_path] if args.vault_path else []),
        *(["--project-slug", args.project_slug] if args.project_slug else []),
        *(["--wiki-root", args.wiki_root] if args.wiki_root else []),
    ])


if __name__ == "__main__":
    raise SystemExit(main())
