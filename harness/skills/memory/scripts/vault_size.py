#!/usr/bin/env python3
"""vault_size.py — how many notes the vault holds, by area, and how that moved.

Task 199. The write meter counts what the vault wrote in a day; this counts
what it holds, so a vault that grows faster than anyone meant shows the
morning it starts rather than at the next audit. It reads the vault's git
history, like the write meter: the notes in the last commit before the
moment, the last commit a day before it, and the last commit a week before
it. Only Markdown files count, and git never lists its own `.git`.

The areas are the memory root's `memory/` and `diagnostics/`, and the vault
root's `projects/`, `personal/`, `resources/`, `systems/`, `calendar/` and
`standards/`. The total is every note in the vault, in those areas or not.

Three readings are warnings: memory growing more than 15 notes a day over
seven days, the whole vault growing more than 60 net in a day, and any one
area growing more than 100 in a day.

It is read-only: it runs `git rev-list` and `git ls-tree` and writes nothing.

Run: python3 vault_size.py [--vault-path <memory root>] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import write_meter  # noqa: E402  (same skill dir)

DAY = 86400
WEEK = 7

# (name, folder, under the memory root rather than the vault root)
AREAS = (
    ("memory", "memory/", True),
    ("diagnostics", "diagnostics/", True),
    ("projects", "projects/", False),
    ("personal", "personal/", False),
    ("resources", "resources/", False),
    ("systems", "systems/", False),
    ("calendar", "calendar/", False),
    ("standards", "standards/", False),
)

MEMORY_DAILY_RATE = 15   # memory notes a day, averaged over the week
VAULT_DAILY_NET = 60     # notes a day, net, across the whole vault
AREA_DAILY_JUMP = 100    # notes a day in any one area


def _commit_before(root: Path, at: float) -> "str | None":
    rc, out = write_meter._git(root, "rev-list", "-1", f"--before=@{int(at)}", "HEAD")
    if rc != 0:
        return None
    return out.strip() or None


def counts(root: Path, sha: str, mem: str) -> dict:
    """{"total": n, <area>: n, …} for the notes in one commit."""
    rc, out = write_meter._git(root, "ls-tree", "-r", "-z", "--name-only", sha)
    if rc != 0:
        raise RuntimeError(f"git ls-tree failed: {out.strip()[:200]}")
    prefixes = [(name, (mem if under_mem else "") + folder) for name, folder, under_mem in AREAS]
    out_counts = {"total": 0, **{name: 0 for name, _f, _m in AREAS}}
    for path in out.split("\0"):
        if not path.endswith(".md"):
            continue
        out_counts["total"] += 1
        for name, prefix in prefixes:
            if path.startswith(prefix):
                out_counts[name] += 1
                break
    return out_counts


def measure(memory_root, *, now: "float | None" = None) -> dict:
    """The counts now, a day ago and a week ago, and any warnings.

    Raises RuntimeError when the vault is not a git repository or holds no
    commit yet: an absence the caller reports as one, never a quiet zero.
    """
    memory_root = Path(memory_root)
    root = write_meter.vault_root_of(memory_root)
    rc, out = write_meter._git(root, "rev-parse", "--is-inside-work-tree")
    if rc != 0 or out.strip() != "true":
        raise RuntimeError(f"{root} is not a git working tree")
    now = now if now is not None else time.time()
    try:
        rel = os.path.relpath(os.path.realpath(memory_root), root).replace(os.sep, "/")
    except ValueError:
        rel = "."
    mem = "" if rel in (".", "") else rel.rstrip("/") + "/"

    head = _commit_before(root, now)
    if head is None:
        raise RuntimeError("the vault has no commit yet")
    day_sha = _commit_before(root, now - DAY)
    week_sha = _commit_before(root, now - WEEK * DAY)
    m = {
        "now": counts(root, head, mem),
        "day": counts(root, day_sha, mem) if day_sha else None,
        "week": counts(root, week_sha, mem) if week_sha else None,
    }
    m["warnings"] = warnings(m)
    return m


def warnings(m: dict) -> list:
    out = []
    now, day, week = m["now"], m["day"], m["week"]
    if week is not None:
        rate = (now["memory"] - week["memory"]) / WEEK
        if rate > MEMORY_DAILY_RATE:
            out.append(f"memory grew {rate:.1f} a day over {WEEK} days, over {MEMORY_DAILY_RATE}")
    if day is not None:
        net = now["total"] - day["total"]
        if net > VAULT_DAILY_NET:
            out.append(f"the vault grew {net} notes in a day, over {VAULT_DAILY_NET}")
        for name, _f, _m in AREAS:
            jump = now[name] - day[name]
            if jump > AREA_DAILY_JUMP:
                out.append(f"{name} grew {jump} in a day, over {AREA_DAILY_JUMP}")
    return out


def describe(m: dict) -> str:
    """The reading in one line, for the morning note."""
    now, day, week = m["now"], m["day"], m["week"]

    def count(key: str, noun: str = "") -> str:
        text = f"{now[key]:,}{noun}"
        if day is not None and now[key] != day[key]:
            text += f" ({now[key] - day[key]:+,})"
        return text

    parts = [count("total", " notes"), " · ".join(f"{name} {count(name)}" for name, _f, _m in AREAS)]
    if week is not None:
        parts.append(f"memory {(now['memory'] - week['memory']) / WEEK:+.1f} a day over {WEEK} days")
    line = " — ".join(parts)
    if day is None:
        line += " — no commit a day old to compare against"
    if m["warnings"]:
        line += " — ⚠ " + "; ".join(m["warnings"])
    return line


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description="Count the vault's notes by area.")
    ap.add_argument("--vault-path", default=None, help="the memory root (overrides MEMORY_ROOT)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    vault = args.vault_path or os.environ.get("MEMORY_ROOT") or os.environ.get("MEMORY_VAULT_PATH")
    if not vault:
        import corpus_scorecard  # same skill dir
        vault = corpus_scorecard.memory_root_from_daemon()
    if not vault:
        print("vault-size: no memory root. Pass --vault-path or set $MEMORY_ROOT.", file=sys.stderr)
        return 2
    try:
        m = measure(Path(vault))
    except RuntimeError as exc:
        print(f"vault-size: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(m, indent=2) if args.json else f"vault-size: {describe(m)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
