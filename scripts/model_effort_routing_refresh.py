#!/usr/bin/env python3
"""model_effort_routing_refresh.py -- content-refresh's re-pin entrypoint
for agentm-model-effort-routing.md's tier-scale model-id strings
(PLAN-wave-d-personas task 5).

`content-refresh` (crickets `src/maintenance/scripts/content_refresh.py`)
already shipped in Wave C maintenance -- confirmed at this task's start,
not assumed. It is a generic, consumer-agnostic bounded re-pin engine:
`refresh(target_path, item, vault)` classifies a `{old_ref, new_ref,
context}` checklist item as `mechanical` (an existing `old_ref` found in
the target file -- auto-applied) or `judgment-bound` (no existing
reference to rename from -- surfaced to the watchlist, never auto-edited).
It needs no per-consumer wiring on its own side.

This file is the agentm-side half of that contract: the named checklist
of this chart's OWN pinned model-id strings (the ones a model release
could rename), plus a thin entrypoint that calls the crickets engine
directly -- bypassing the not-yet-built weekly model-drift-detector
scheduler, per the plan's explicit constraint ("this task does NOT build
content-refresh... only the minimal re-pin consumer contract"). Nothing
here builds a scheduler, a cron job, or a second re-pin engine -- it is
purely the checklist + a one-call bridge into the real engine, mirroring
the existing check-slop.py sibling-checkout delegator pattern.

Resolution order for the crickets sibling checkout (first hit wins):
  1. $CRICKETS_REPO_ROOT/src/maintenance/scripts/content_refresh.py
  2. <this-repo>/../crickets/src/maintenance/scripts/content_refresh.py

Public API:

    CHECKLIST -- the list of {old_ref, new_ref, context} dicts naming
        this chart's current pinned model ids. `new_ref` intentionally
        equals `old_ref` at rest (nothing has been renamed yet); a real
        re-pin run is invoked with an updated `new_ref` once a model
        release actually renames one of these ids (the model-drift
        detector's future job, or a manual invocation today).

    find_content_refresh() -> Path | None
        Locates the crickets sibling checkout's content_refresh.py.

    refresh_chart(target_path, item, vault, *, root=None) -> dict
        Calls the crickets engine's refresh() against `target_path` with
        `item`. Raises ContentRefreshUnavailable if the sibling checkout
        isn't found -- a clear, named failure, not a silent no-op, since
        this is a direct CLI invocation (not check-slop's graceful-skip
        report-only gate; a caller here is asking for a real re-pin).

CLI:
    python3 scripts/model_effort_routing_refresh.py --old-ref ID --new-ref ID
        [--target PATH] [--vault-path PATH] [--context TEXT]

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

# The chart's own pinned model-id strings (agentm-model-effort-routing.md
# line 66), named as content-refresh checklist items. `new_ref == old_ref`
# at rest -- this is the standing list of "things a model release could
# rename," not a pending rename. A real re-pin invocation overrides
# `new_ref` via the CLI's --new-ref flag against the specific id changing.
CHECKLIST: list[dict[str, str]] = [
    {
        "old_ref": "claude-opus-4-8",
        "new_ref": "claude-opus-4-8",
        "context": "T3-T4 tier rows (Architect, Researcher, Reviewer-audit) -- current-generation Opus.",
    },
    {
        "old_ref": "claude-sonnet-5",
        "new_ref": "claude-sonnet-5",
        "context": "T1-T2 tier rows (Engineer, Tech-Lead/Designer authoring shape) -- current generation, introductory pricing through 2026-08-31.",
    },
    {
        "old_ref": "claude-sonnet-4-6",
        "new_ref": "claude-sonnet-4-6",
        "context": "prior generation, still a named row in the five-id set the chart draws on.",
    },
    {
        "old_ref": "claude-haiku-4-5",
        "new_ref": "claude-haiku-4-5",
        "context": "T0 tier row (Operator, Maintainer, Memory) -- cheapest mechanical floor.",
    },
    {
        "old_ref": "claude-fable-5",
        "new_ref": "claude-fable-5",
        "context": "named, pinned, deliberately unrouted (no work-type resolves to it) -- the Mythos-incident cautionary id.",
    },
]


class ContentRefreshUnavailable(RuntimeError):
    """The crickets sibling checkout's content_refresh.py wasn't found."""


def find_content_refresh() -> Path | None:
    env_dir = os.environ.get("CRICKETS_REPO_ROOT", "").strip()
    candidates = []
    if env_dir:
        candidates.append(
            Path(os.path.expanduser(env_dir)) / "src" / "maintenance" / "scripts" / "content_refresh.py"
        )
    candidates.append(_AGENTM_ROOT.parent / "crickets" / "src" / "maintenance" / "scripts" / "content_refresh.py")
    for c in candidates:
        if c.is_file():
            return c
    return None


def _load_content_refresh(path: Path):
    spec = importlib.util.spec_from_file_location("crickets_content_refresh", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["crickets_content_refresh"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def refresh_chart(target_path: Path, item: dict, vault: Path, *, root: Path | None = None) -> dict:
    """Call the crickets content-refresh engine's refresh() against
    `target_path` with `item`. Raises ContentRefreshUnavailable if the
    sibling checkout can't be found -- a direct CLI ask for a real re-pin
    should fail loudly, not silently no-op."""
    script = find_content_refresh()
    if script is None:
        raise ContentRefreshUnavailable(
            "crickets sibling checkout not found ($CRICKETS_REPO_ROOT or ../crickets) "
            "-- content-refresh's engine lives in crickets/src/maintenance/scripts/content_refresh.py"
        )
    mod = _load_content_refresh(script)
    return mod.refresh(target_path, item, vault)


def _main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--old-ref", required=True, help="the currently-pinned model id being replaced")
    p.add_argument("--new-ref", required=True, help="the model id to re-pin to")
    p.add_argument("--context", default=None, help="override the checklist's context note")
    p.add_argument(
        "--target",
        default=str(_AGENTM_ROOT / "wiki" / "designs" / "agentm-model-effort-routing.md"),
        help="the file to re-pin (default: the tier-scale chart itself)",
    )
    p.add_argument("--vault-path", required=True, help="vault root for a judgment-bound watchlist entry")
    args = p.parse_args(argv)

    item = next((dict(i) for i in CHECKLIST if i["old_ref"] == args.old_ref), None)
    if item is None:
        print(
            f"model_effort_routing_refresh: {args.old_ref!r} is not a checklist entry "
            f"(known: {[i['old_ref'] for i in CHECKLIST]})",
            file=sys.stderr,
        )
        return 2
    item["new_ref"] = args.new_ref
    if args.context:
        item["context"] = args.context

    try:
        result = refresh_chart(Path(args.target), item, Path(args.vault_path))
    except ContentRefreshUnavailable as exc:
        print(f"model_effort_routing_refresh: {exc}", file=sys.stderr)
        return 1

    result = dict(result)
    result["watchlist_path"] = str(result["watchlist_path"]) if result.get("watchlist_path") else None
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
