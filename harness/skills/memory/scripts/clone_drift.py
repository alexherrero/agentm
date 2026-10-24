#!/usr/bin/env python3
"""How far the primary clone is behind `origin/main` (#815).

The launchd jobs run from the primary clone, so whatever it has checked out is
the live code. A clone missing merged commits runs the bugs they fixed and
reports healthy. The operator's ruling of 2026-10-23: a job that would start
from such a clone runs anyway, and the gap is reported loudly — in the runner's
cycle record, in `agentmd status`, in the doctor's `install-head` row and in the
morning note — as the count of commits behind and the newest merged commit it
lacks.

Local only. The comparison is against the `origin/main` ref the last fetch
left, never a fetch of its own: a fetch per cycle costs the network every few
minutes, and a session working mid-branch must never be blocked or moved. A
clone that is ahead of `origin/main`, or on a branch, is said to be so and is
not an error.

Read-only. It moves no ref and checks nothing out.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

REMOTE_MAIN = "refs/remotes/origin/main"


@dataclass
class Drift:
    """One reading. `behind` and `ahead` are None when they could not be read;
    `error` then says why."""
    head: Optional[str] = None
    branch: Optional[str] = None
    behind: Optional[int] = None
    ahead: Optional[int] = None
    # The newest commit on origin/main the clone lacks, when behind.
    newest_missing: Optional[str] = None
    newest_missing_subject: Optional[str] = None
    error: Optional[str] = None

    def as_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


def _git(repo: Path, *args: str) -> Optional[str]:
    try:
        proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                              text=True, timeout=10, env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def read(repo: Path) -> Drift:
    """The clone at `repo` against its local `origin/main`."""
    head = _git(repo, "rev-parse", "HEAD")
    if head is None:
        return Drift(error=f"not a readable git clone at {repo}")
    out = Drift(head=head)
    branch = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    out.branch = branch or None
    if _git(repo, "rev-parse", "--verify", "--quiet", REMOTE_MAIN) is None:
        out.error = "no origin/main ref to compare with"
        return out
    counts = _git(repo, "rev-list", "--left-right", "--count", f"{REMOTE_MAIN}...HEAD")
    parts = (counts or "").split()
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        out.error = "git could not count the commits between HEAD and origin/main"
        return out
    out.behind, out.ahead = int(parts[0]), int(parts[1])
    if out.behind:
        line = _git(repo, "log", "-1", "--format=%H%x09%s", f"HEAD..{REMOTE_MAIN}")
        if line and "\t" in line:
            out.newest_missing, out.newest_missing_subject = line.split("\t", 1)
    return out


def sentence(d: Drift) -> Optional[str]:
    """The loud line for a clone behind main, or None when it is not behind."""
    if not d.behind:
        return None
    s = f"the primary clone is {d.behind} commit{'s' if d.behind != 1 else ''} behind main"
    if d.newest_missing:
        s += f"; the newest it lacks is {d.newest_missing[:8]}"
        if d.newest_missing_subject:
            s += f' "{d.newest_missing_subject}"'
    return s


def note(d: Drift) -> Optional[str]:
    """The quiet line for a clone ahead or on a branch: reported, not an error."""
    bits = []
    if d.branch and d.branch != "main":
        bits.append(f"on branch `{d.branch}`")
    if d.ahead:
        bits.append(f"{d.ahead} commit{'s' if d.ahead != 1 else ''} ahead of main")
    return "the primary clone is " + " and ".join(bits) if bits else None
