#!/usr/bin/env python3
"""supersession_migrate.py — one shape for the superseded relation.

PLAN-superseded-vocabulary, task 4. The dream layer's stages used to mark a
collapsed copy with `status: superseded` + `supersedes: <winner>` — the
pointer on the loser, running the wrong way. The contract's shape is
`lifecycle: superseded` + `superseded_by: <successor>` on the loser, with
`supersedes:` only ever the successor's back-link. This walks the memory
classes and converts what it finds:

- `status: superseded` and/or a loser-side `supersedes:` on a memory becomes
  `lifecycle: superseded`, `superseded_by: <the note it pointed at>`,
  `lifecycle_since: <today>`, `status: active`, the loser-side `supersedes:`
  removed;
- a note already carrying `superseded_by:` keeps it;
- a note with neither pointer nor lifecycle state is reported, never guessed.

Every conversion is journaled to the lifecycle journal (actor `migration`).
Report-only by default; `--apply` writes. Idempotent: a converted note is
left alone on the next run.

    python3 supersession_migrate.py --vault <memory root> [--apply] [--today YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

MEMORY_DIRS = ("memory",)


def _split(text: str):
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return None, text
    return text[4:end].split("\n"), text[end + 5:]


def _get(lines: list, key: str) -> str:
    for l in lines:
        k, sep, v = l.partition(":")
        if sep and k.strip() == key and l[:1] not in " \t#-":
            return v.strip().strip("'\"")
    return ""


def _set(lines: list, key: str, value: str) -> None:
    for i, l in enumerate(lines):
        k, sep, _ = l.partition(":")
        if sep and k.strip() == key and l[:1] not in " \t#-":
            lines[i] = f"{key}: {value}"
            return
    lines.append(f"{key}: {value}")


def _drop(lines: list, key: str) -> None:
    lines[:] = [l for l in lines if not (l.partition(":")[1] and l.partition(":")[0].strip() == key and l[:1] not in " \t#-")]


def plan(vault: Path) -> list:
    """[(rel, action, successor)] — action is `convert` or `unresolvable`."""
    out = []
    for d in MEMORY_DIRS:
        root = vault / d
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.md")):
            rel = p.relative_to(vault).as_posix()
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            lines, _ = _split(text)
            if lines is None:
                continue
            status = _get(lines, "status").lower()
            lifecycle = _get(lines, "lifecycle").lower()
            supersedes = _get(lines, "supersedes")
            by = _get(lines, "superseded_by")
            old_shape = status == "superseded" or (lifecycle == "superseded" and supersedes and not by)
            if not old_shape:
                continue
            successor = by or supersedes
            if not successor:
                out.append((rel, "unresolvable", ""))
                continue
            out.append((rel, "convert", successor))
    return out


def apply(vault: Path, rows: list, *, today: str, journal=None) -> int:
    import lifecycle_transitions as lt

    n = 0
    for rel, action, successor in rows:
        if action != "convert":
            continue
        p = vault / rel
        text = p.read_text(encoding="utf-8")
        lines, body = _split(text)
        was = _get(lines, "lifecycle").lower() or "active"
        _set(lines, "status", "active")
        _set(lines, "lifecycle", "superseded")
        _set(lines, "superseded_by", successor)
        _set(lines, "lifecycle_since", today)
        # The loser-side pointer is the inverted shape; the successor's own
        # `supersedes:` (if any) is the back-link and lives on the other note.
        _drop(lines, "supersedes")
        tmp = p.with_suffix(".md.tmp")
        tmp.write_text("---\n" + "\n".join(lines) + "\n---\n" + body, encoding="utf-8")
        os.replace(tmp, p)
        lt.journal_append({"ts": today + "T00:00:00+00:00", "rel": rel, "from": was, "to": "superseded", "actor": "migration",
                           "reason": f"one vocabulary for the superseded relation: superseded_by {successor}", "run_id": None},
                          path=journal)
        n += 1
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="convert the superseded relation to the contract's shape")
    ap.add_argument("--vault", required=True, help="the memory root")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--today", default=None)
    a = ap.parse_args(argv)
    vault = Path(a.vault)
    today = a.today or dt.date.today().isoformat()
    rows = plan(vault)
    for rel, action, successor in rows:
        print(f"{action:12s} {rel}" + (f" -> {successor}" if successor else ""))
    convert = [r for r in rows if r[1] == "convert"]
    print(f"{len(convert)} to convert, {len(rows) - len(convert)} unresolvable")
    if a.apply:
        n = apply(vault, rows, today=today)
        print(f"converted {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
