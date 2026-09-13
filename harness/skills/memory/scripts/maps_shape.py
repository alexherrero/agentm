#!/usr/bin/env python3
"""maps_shape.py — the maps' shape, as the gates and the maps data run read it
(agentm-vault plan 07).

`memory/mocs/` holds three named maps (`moc-root.md`, `moc-memory.md`,
`needs-review.md`), the class directory's own `_index.md`, and a page per
memory type with at least `moc_min_members` live notes: `<type>.md`,
paginated inside itself, never `<type>-2.md`. The dreaming binary's mocs job
writes the pages; `live_type_counts` counts members the way its `mocMembers`
does, so a gate and the job agree on which types earn a page.

The data run writes `memory/.maps-and-root-notes-complete` once its journal
matches its dry run. A gate that reads the live vault reports its findings
until then and enforces from then on, as the card gates do with the card
backfill's marker.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import card_shape as cs  # noqa: E402
import drive_artifacts  # noqa: E402

MARKER_NAME = ".maps-and-root-notes-complete"
MAP_NAMES = ("moc-root", "moc-memory", "needs-review")
CLASS_INDEX = "_index.md"
MEMBER_CLASSES = ("semantic", "procedural", "episodic", "entities", "crystallized")
DEFAULT_MIN_MEMBERS = 5
_NUMBERED = re.compile(r"^(?P<base>.+)-(?P<n>\d+)$")
_SETTLED = ("superseded", "archived")


def marker_path(memory_root) -> Path:
    return Path(memory_root) / "memory" / MARKER_NAME


def data_run_done(memory_root) -> bool:
    return marker_path(memory_root).exists()


def min_members(rules=None) -> int:
    """The contract's `moc_min_members`, or the job's default without one."""
    raw = (rules.thresholds() if rules is not None else {}).get("moc_min_members", DEFAULT_MIN_MEMBERS)
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        return DEFAULT_MIN_MEMBERS
    return n if n > 0 else DEFAULT_MIN_MEMBERS


def _field(entries, key: str) -> str:
    return (cs.scalar(cs.raw_value(entries, key)) or "").strip()


def live_type_counts(memory_root, rules=None) -> dict:
    """`{type: live notes}` across the classes a map lists, counted as the mocs
    job counts: a record (`kind:`) and the class index are not members, a
    superseded or archived note is not a member, and a retired type counts
    under its replacement."""
    deprecations = rules.deprecations() if rules is not None else {}
    counts: dict = {}
    for cls in MEMBER_CLASSES:
        d = Path(memory_root) / "memory" / cls
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*.md")):
            if p.name == CLASS_INDEX or drive_artifacts.is_artifact(p) or not p.is_file():
                continue
            try:
                parsed = cs.split_note(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if parsed is None:
                continue
            entries, _rest = parsed
            if _field(entries, "kind"):
                continue
            t = _field(entries, "type")
            if not t:
                continue
            t = deprecations.get(t) or t
            if _field(entries, "lifecycle").lower() in _SETTLED or _field(entries, "status").lower() == "superseded":
                continue
            counts[t] = counts.get(t, 0) + 1
    return counts


def mocs_findings(memory_root, rules=None) -> list:
    """What `mocs/` holds that the maps' shape does not allow, one line each."""
    d = Path(memory_root) / "memory" / "mocs"
    if not d.is_dir():
        return []
    counts = live_type_counts(memory_root, rules)
    floor = min_members(rules)
    types = set(rules.memory_types()) if rules is not None else set()
    types |= set(counts)
    out = []
    for p in sorted(d.iterdir()):
        if not p.is_file() or p.suffix != ".md" or p.name == CLASS_INDEX or drive_artifacts.is_artifact(p):
            continue
        stem, rel = p.stem, f"memory/mocs/{p.name}"
        if stem in MAP_NAMES:
            continue
        numbered = _NUMBERED.match(stem)
        if numbered and numbered.group("base") in types:
            out.append(f"{rel}: a numbered map page; a type's page paginates inside itself")
        elif stem in types:
            n = counts.get(stem, 0)
            if n < floor:
                out.append(f"{rel}: `{stem}` has {n} live note(s), under the page threshold of {floor}")
        else:
            out.append(f"{rel}: not a map this directory holds (moc-root, moc-memory, needs-review, or a type's page)")
    return out
