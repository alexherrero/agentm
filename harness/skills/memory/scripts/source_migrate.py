#!/usr/bin/env python3
"""source_migrate.py — one meaning for `source:`.

PLAN-source-and-hygiene, task 1. The contract has always said `source:` is
the closed transport vocabulary and read the trust tier from it. The corpus
predates that: alongside the transports it holds bare URLs (a fetched page's
address) and namespaced references (a mined unit's identity), so no gate
could refuse a wrong value and the fetched pages never earned the
`untrusted` tier the contract gives them.

The provenance ruling of 2026-09-06 gives each question its own field:
`source:` is how the memory arrived, `source_url:` is the page it came from,
`source_id:` is the registry identity it was mined from. This pass walks a
vault and moves what it finds:

- a URL in `source:` becomes `source_url: <url>` with `source: external-fetch`
  and the trust tier the contract gives that transport — the transport is
  derived, not guessed: a URL was fetched;
- any other non-vocabulary value becomes `source_id: <value>` verbatim, and
  `source:` is dropped rather than filled in, because nothing on disk says
  how that memory arrived and inventing a transport would put a trust tier
  on a guess;
- a value already in the vocabulary is left alone, as is a note that already
  carries the reference in its own field.

Every write is appended to `<engine state dir>/source-migration.jsonl`, one
object per note. Report-only by default; `--apply` writes.

    python3 source_migrate.py --vault <vault root> [--apply] [--today YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

JOURNAL_NAME = "source-migration.jsonl"

# The reference field a value lands in, by shape.
FETCHED = "fetched"        # a URL — the page's address, transport derivable
REFERENCE = "reference"    # a namespaced or path-shaped identity — transport unknown
SKIP = "skip"              # already the contract's shape


def _split(text: str):
    """(frontmatter lines, body) or (None, text) when there is no block."""
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return None, text
    return text[4:end].split("\n"), text[end + 5:]


def _get(lines: list, key: str) -> str:
    prefix = key + ":"
    for line in lines:
        if line.startswith(prefix):
            return line[len(prefix):].strip().strip("'\"")
    return ""


def _scalar(value: str) -> str:
    """A value YAML will read back as the string it is.

    `_get` strips the quotes off whatever it reads, so a value that arrived
    quoted has to leave quoted or the note stops parsing. The corpus has such
    values: one memory's provenance was `opinion-supplements: good/... (24
    independent minings)`, and writing that bare turns the frontmatter into a
    nested mapping."""
    if value == "":
        return '""'
    needs = (": " in value or value.endswith(":") or "#" in value
             or value != value.strip()
             or value[0] in "-?:,[]{}#&*!|>'\"%@`")
    if not needs:
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _set(lines: list, key: str, value: str) -> None:
    prefix = key + ":"
    rendered = f"{key}: {_scalar(value)}"
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = rendered
            return
    lines.append(rendered)


def _drop(lines: list, key: str) -> None:
    prefix = key + ":"
    lines[:] = [l for l in lines if not l.startswith(prefix)]


def _iter_notes(vault: Path):
    """Every markdown note in the vault, dot-directories pruned — the same
    population `check-vault-frontmatter` scans, so the gate and this pass
    agree on what carries a provenance field."""
    for root, dirs, files in os.walk(vault):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            if name.endswith(".md"):
                yield Path(root) / name


def transports() -> dict:
    """The contract's `sources` map: transport -> tier. Empty when no
    contract answers, which makes every value non-vocabulary and is the
    safe direction — the pass reports rather than writes."""
    try:
        import storage_rules
        return dict(storage_rules.rules().sources() or {})
    except Exception:
        return {}


def is_url(value: str) -> bool:
    return value.lower().startswith(("http://", "https://"))


def plan(vault: Path, *, vocabulary: "dict | None" = None) -> list:
    """[(rel, action, value)] for every note whose `source:` is not the
    contract's transport. `action` is FETCHED or REFERENCE."""
    vocab = transports() if vocabulary is None else vocabulary
    out = []
    for p in _iter_notes(vault):
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines, _ = _split(text)
        if lines is None:
            continue
        value = _get(lines, "source")
        if not value or value in vocab:
            continue
        rel = p.relative_to(vault).as_posix()
        out.append((rel, FETCHED if is_url(value) else REFERENCE, value))
    return out


def apply(vault: Path, rows: list, *, today: str, journal=None,
          vocabulary: "dict | None" = None) -> int:
    """Write the planned moves. Returns how many notes changed."""
    vocab = transports() if vocabulary is None else vocabulary
    written = 0
    for rel, action, value in rows:
        if action not in (FETCHED, REFERENCE):
            continue
        p = vault / rel
        text = p.read_text(encoding="utf-8")
        lines, body = _split(text)
        if lines is None:
            continue
        entry = {"ts": today + "T00:00:00+00:00", "rel": rel, "actor": "migration",
                 "was": value, "action": action}
        if action == FETCHED:
            _set(lines, "source_url", value)
            _set(lines, "source", "external-fetch")
            entry["source"] = "external-fetch"
            entry["field"] = "source_url"
            tier = vocab.get("external-fetch")
            if tier:
                _set(lines, "trust", tier)
                entry["trust"] = tier
        else:
            _set(lines, "source_id", value)
            # Nothing on disk says how this memory arrived. A transport
            # invented here would carry a trust tier nobody measured.
            _drop(lines, "source")
            entry["field"] = "source_id"
            entry["source"] = None
        tmp = p.with_suffix(".md.tmp")
        tmp.write_text("---\n" + "\n".join(lines) + "\n---\n" + body, encoding="utf-8")
        os.replace(tmp, p)
        _journal(entry, journal)
        written += 1
    return written


def _journal(entry: dict, path) -> None:
    if path is None:
        try:
            import engine_state
            path = engine_state.engine_state_dir() / JOURNAL_NAME
        except Exception:
            return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="move the provenance reference out of `source:`")
    ap.add_argument("--vault", required=True, help="the vault root")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--today", default=None)
    a = ap.parse_args(argv)
    vault = Path(a.vault)
    today = a.today or dt.date.today().isoformat()
    vocab = transports()
    if not vocab:
        print("no contract answered: every value would read as non-vocabulary — refusing to write",
              file=sys.stderr)
        return 2
    rows = plan(vault, vocabulary=vocab)
    for rel, action, value in rows:
        print(f"{action:10s} {rel} -> {value[:100]}")
    fetched = [r for r in rows if r[1] == FETCHED]
    reference = [r for r in rows if r[1] == REFERENCE]
    print(f"{len(fetched)} fetched page(s) to source_url:, {len(reference)} reference(s) to source_id:")
    if a.apply:
        n = apply(vault, rows, today=today, vocabulary=vocab)
        print(f"written {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
