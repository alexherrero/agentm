#!/usr/bin/env python3
"""supersession_migrate.py — one shape for the superseded relation.

PLAN-superseded-vocabulary, task 4. The dream layer's stages used to mark a
collapsed copy with `status: superseded` + `supersedes: <winner>` — the
pointer on the loser, running the wrong way. The contract's shape is
`lifecycle: superseded` + `superseded_by: <successor>` on the loser, with
`supersedes:` only ever the successor's back-link. This walks every note in
the vault (dot-directories pruned — the population the frontmatter gate
scans) and converts what it finds:

- `status: superseded` and/or a loser-side `supersedes:` on a memory becomes
  `lifecycle: superseded`, `superseded_by: <the note it pointed at>`,
  `lifecycle_since: <today>`, `status: active`, the loser-side `supersedes:`
  removed;
- a note already carrying `superseded_by:` keeps it;
- the successor is resolved inside the vault — a vault-relative path, an
  absolute path under the vault root (rewritten relative), or a note found by
  file stem or `slug:` — so the migration never writes a pointer the vault
  cannot answer to; a source version (`<id> at <version>`) is kept verbatim;
- a note whose successor is not in the vault is revived rather than walled:
  `status: active`, the dead pointer dropped, the lifecycle left as it was,
  the old pointer kept in the journal line;
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

def _iter_notes(vault: Path):
    """Every markdown note in the vault, dot-directories pruned — the same
    population `check-vault-frontmatter` scans, so the gate and the
    migration agree on what carries the relation."""
    for root, dirs, files in os.walk(vault):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            if name.endswith(".md"):
                yield Path(root) / name


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


def _resolve_successor(vault: Path, value: str):
    """The successor as a vault-relative path, the value itself for a source
    version (`<id> at <version>`), or None when nothing in the vault answers
    to it."""
    v = value.strip().strip("'\"")
    if not v:
        return None
    if " at " in v:
        return v
    cand = Path(v)
    if cand.is_absolute():
        try:
            rel = cand.resolve().relative_to(vault.resolve())
        except ValueError:
            rel = None
        if rel is not None and (vault / rel).is_file():
            return rel.as_posix()
    elif (vault / v).is_file():
        return Path(v).as_posix()
    stem = cand.stem if ("/" in v or v.endswith(".md")) else v
    by_slug = None
    for p in _iter_notes(vault):
        if p.stem == stem:
            return p.relative_to(vault).as_posix()
        if by_slug is None:
            try:
                lines, _ = _split(p.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
            if lines is not None and _get(lines, "slug").strip().strip("'\"") == stem:
                by_slug = p.relative_to(vault).as_posix()
    return by_slug


def plan(vault: Path) -> list:
    """[(rel, action, successor)] — action is `convert` (successor resolved
    inside the vault), `revive` (successor not in the vault; the value is
    the dead pointer) or `unresolvable` (no successor named at all)."""
    out = []
    for p in _iter_notes(vault):
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
        resolved = _resolve_successor(vault, successor)
        if resolved is None:
            out.append((rel, "revive", successor))
            continue
        out.append((rel, "convert", resolved))
    return out


def apply(vault: Path, rows: list, *, today: str, journal=None) -> int:
    import lifecycle_transitions as lt

    n = 0
    for rel, action, successor in rows:
        if action not in ("convert", "revive"):
            continue
        p = vault / rel
        text = p.read_text(encoding="utf-8")
        lines, body = _split(text)
        was = _get(lines, "lifecycle").lower() or "active"
        if action == "revive":
            # Nothing in the vault answers to the successor: walling the note
            # would hide the only copy left. It returns to active with the
            # lifecycle it had; the dead pointer survives in the journal line.
            _set(lines, "status", "active")
            _drop(lines, "supersedes")
            _drop(lines, "superseded_by")
            tmp = p.with_suffix(".md.tmp")
            tmp.write_text("---\n" + "\n".join(lines) + "\n---\n" + body, encoding="utf-8")
            os.replace(tmp, p)
            lt.journal_append({"ts": today + "T00:00:00+00:00", "rel": rel, "from": was, "to": was, "actor": "migration",
                               "reason": f"one vocabulary for the superseded relation: successor not in the vault, note returned to active (was superseded by {successor})",
                               "run_id": None}, path=journal)
            n += 1
            continue
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
    revive = [r for r in rows if r[1] == "revive"]
    print(f"{len(convert)} to convert, {len(revive)} to revive (successor not in the vault), "
          f"{len(rows) - len(convert) - len(revive)} unresolvable")
    if a.apply:
        n = apply(vault, rows, today=today)
        print(f"written {n}: converted {len(convert)}, revived {len(revive)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
