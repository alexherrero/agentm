#!/usr/bin/env python3
"""calendar_promotion.py — the facet-promotion trigger.

Filing v2 part 5, task 5. The diary is the zero-bar catch-all, and a pattern
that keeps recurring there is the signal that a standing facet is missing —
the same recurrence gate consolidation uses. A diary entry that opens with
a label ("gym: 40 minutes", "reading — chapter three") names its pattern;
when one label recurs on three or more distinct days inside the window,
this module emits a suggestion.

A suggestion is a proposal, never an applied edit. The facet registry lives
in `standards/storage-rules.md`, an operator-owned file, and adding a facet
is an edit to it: the dreaming cycle stages the proposal — the contract file
with one line added under `facets:` — for the operator to confirm through
the same confirm flow every other proposal uses. The agent never widens its
own registry.

Usage:
    calendar_promotion.py --vault <memory-root> [--today YYYY-MM-DD] [--window 30]
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import calendar_facets as cf  # noqa: E402

THRESHOLD = 3
WINDOW_DAYS = 30
# `HH:MM — <label>: rest` or `HH:MM — <label> — rest`; a label is one to three
# words of letters, digits or hyphens, so a sentence never reads as a label.
_ENTRY_RE = re.compile(r"(?m)^\d{2}:\d{2} — ([A-Za-z][\w-]{0,29}(?: [A-Za-z][\w-]{0,29}){0,2})\s*(?::|—|-)\s+\S")
_FACETS_BLOCK = re.compile(r"(?ms)^facets:\n((?:[ \t]+-[ \t]+[^\n]*\n)+)")


@dataclass
class Suggestion:
    label: str
    entries: int
    days: int
    sample: str
    first: str
    last: str


def _label_of(text: str) -> "str | None":
    m = _ENTRY_RE.match(text)
    if not m:
        return None
    return re.sub(r"\s+", "-", m.group(1).strip().lower())


def detect(vault: "Path | str", *, today: "date | None" = None, window_days: int = WINDOW_DAYS,
           threshold: int = THRESHOLD, rules=None) -> list:
    """Labels recurring on `threshold` or more distinct days of the window,
    excluding the facets the registry already carries. Deterministic, order
    by days then entries then label."""
    vault = Path(vault)
    root = cf.calendar_root(vault)
    if root is None:
        return []
    today = today or date.today()
    registry = set(cf.facets(rules))
    seen: dict = {}
    for i in range(window_days):
        d = today - timedelta(days=i)
        p = root / f"{d.year:04d}" / f"{d.isoformat()}-{cf.DIARY}.md"
        if not p.is_file():
            continue
        try:
            body = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line in body.splitlines():
            label = _label_of(line)
            if not label or label in registry:
                continue
            rec = seen.setdefault(label, {"entries": 0, "days": set(), "sample": line.split(" — ", 1)[-1]})
            rec["entries"] += 1
            rec["days"].add(d)
    out = []
    for label, rec in seen.items():
        if len(rec["days"]) >= threshold:
            days = sorted(rec["days"])
            out.append(Suggestion(label=label, entries=rec["entries"], days=len(days), sample=rec["sample"],
                                  first=days[0].isoformat(), last=days[-1].isoformat()))
    out.sort(key=lambda s: (-s.days, -s.entries, s.label))
    return out


def contract_path(rules=None) -> "Path | None":
    try:
        import storage_rules  # same skill dir
        src = (rules or storage_rules.rules()).source()
        return Path(src) if src else None
    except Exception:
        return None


def proposal_text(contract_text: str, label: str) -> "str | None":
    """The contract with `  - <label>` added at the end of its `facets:`
    block, and nothing else changed; None when the block is missing or the
    label is already there."""
    m = _FACETS_BLOCK.search(contract_text)
    if not m:
        return None
    block = m.group(1)
    if re.search(rf"(?m)^[ \t]+-[ \t]+{re.escape(label)}[ \t]*$", block):
        return None
    indent = re.match(r"[ \t]+", block).group(0)
    new_block = block + f"{indent}- {label}\n"
    return contract_text[:m.start(1)] + new_block + contract_text[m.end(1):]


def proposals(vault: "Path | str", *, today: "date | None" = None, rules=None) -> list:
    """(label, suggestion, contract_path, new_text) for every suggestion the
    contract can take — what the dreaming cycle stages for the operator."""
    out = []
    path = contract_path(rules)
    if path is None or not path.is_file():
        return out
    text = path.read_text(encoding="utf-8")
    for s in detect(vault, today=today, rules=rules):
        new = proposal_text(text, s.label)
        if new is not None:
            out.append((s.label, s, path, new))
    return out


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description="the facet-promotion trigger over the diary")
    ap.add_argument("--vault", required=True, help="the memory root (the directory holding memory/)")
    ap.add_argument("--today", help="YYYY-MM-DD")
    ap.add_argument("--window", type=int, default=WINDOW_DAYS)
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    today = date.fromisoformat(args.today) if args.today else None
    found = detect(args.vault, today=today, window_days=args.window)
    if not found:
        print("no recurring diary pattern past the threshold; nothing to propose")
        return 0
    for s in found:
        print(f"{s.label}: {s.entries} entries on {s.days} days ({s.first} … {s.last}) — e.g. {s.sample!r}")
    print("A suggestion is a proposal: the dreaming cycle stages the rules edit for your confirmation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
