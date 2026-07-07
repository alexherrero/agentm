#!/usr/bin/env python3
"""sibling_repo_root.py -- worktree-aware sibling-checkout root resolution.

Shared by check-slop.py and model_effort_routing_refresh.py (and, once its
own worktree merges, docs_drift_job.py), each of which locates a file
inside the `crickets` sibling checkout via
`<sibling-layout-root>/crickets/...` -- the documented
`~/Antigravity/agentm` + `~/Antigravity/crickets` layout.

The sibling-layout root is the parent of the MAIN agentm checkout, not the
parent of whatever directory happens to hold the running script. A `/work`
session running from a worktree
(`~/Antigravity/agentm/.claude/worktrees/<slug>/scripts/...`) would resolve
a plain `Path(__file__).parent.parent.parent` to `.claude/worktrees/`
instead, so the sibling-checkout candidate silently never matches and the
delegator falls back to graceful-skip even though the sibling repo is
present. `git rev-parse --git-common-dir` always resolves to the MAIN
checkout's `.git` directory regardless of which worktree invoked it, so
resolving through it is agnostic to worktree count and naming convention
(unlike matching on the literal `.claude/worktrees` path segment).

Stdlib-only.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def sibling_layout_root(start: Path | None = None) -> Path | None:
    """Return the parent of the MAIN checkout (e.g. `~/Antigravity` in the
    documented sibling layout) -- the root a sibling repo like `crickets`
    sits under as `<root>/crickets`.

    Resolves via `git rev-parse --git-common-dir`, which points at the main
    repo's `.git` directory whether invoked from the main checkout or any
    `git worktree` of it. Returns None on any resolution failure (not a git
    repo, git unavailable, etc.) -- callers should treat that the same as
    "sibling not found," not raise.
    """
    cwd = start or Path(__file__).resolve().parent
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    raw = proc.stdout.strip()
    if not raw:
        return None
    common_dir = Path(raw)
    if not common_dir.is_absolute():
        common_dir = cwd / common_dir
    common_dir = common_dir.resolve()
    # common_dir is the main checkout's `.git` directory; its parent is the
    # main checkout root, and that root's parent is the sibling-layout root.
    return common_dir.parent.parent
