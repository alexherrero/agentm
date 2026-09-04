#!/usr/bin/env python3
"""needs_review.py — the needs-review reading and its generated MOC.

Filing v2, the write path (task 3). There is no staging directory any more:
a candidate the writer was unsure about is filed at its real destination and
marked in its own frontmatter — `filing_confidence: low`, `status: unfiled`,
or `review_flags` naming a probable duplicate. This module is the reading over
those marks: it walks the class directories, collects every note that is
waiting for a judgment, and renders one Map of Content at
`memory/mocs/needs-review.md` with a context phrase per link saying why the
note is there.

The page is generated, never authored. An entry clears when the note is
re-judged — the enrichment pass raises it to `active` at high confidence, the
operator edits the stamp, a later note supersedes it — and the next
regeneration simply does not list it. Nothing here writes to any note.

Usage:
    needs_review.py --vault <memory-root> [--write] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from filing_engine import _frontmatter  # noqa: E402  (same skill dir)

# The six classes, as the scorecard counts them. Defined here rather than
# imported so the scorecard can import this module for its reading; a test
# holds the two lists equal.
CLASS_DIRS = ("semantic", "procedural", "episodic", "entities", "crystallized", "mocs")
MOC_REL = "memory/mocs/needs-review.md"
MOC_SLUG = "needs-review"

# The marks the reading selects on, in the order a reviewer should meet them:
# a duplicate concern first (two notes may be one), then a capture nobody has
# typed, then a filing the writer itself doubted.
REASON_ORDER = ("near-duplicate", "update-candidate", "unfiled", "low-confidence")
_REVIEW_FLAGS = ("near-duplicate", "update-candidate")
_SETTLED_LIFECYCLES = ("superseded", "archived")


@dataclass
class Entry:
    rel: str
    slug: str
    title: str
    type: str
    reasons: list = field(default_factory=list)
    related: str = ""
    when: str = ""
    source: str = ""

    @property
    def primary(self) -> str:
        return next(r for r in REASON_ORDER if r in self.reasons)

    def phrase(self) -> str:
        """Why the note is here, in words a reviewer can act on."""
        parts = []
        twin = f"[[{Path(self.related).stem}]]" if self.related else "another note"
        for reason in REASON_ORDER:
            if reason not in self.reasons:
                continue
            if reason == "near-duplicate":
                parts.append(f"probable duplicate of {twin} — filed beside it, never merged")
            elif reason == "update-candidate":
                parts.append(f"same key as {twin}, different body — filed beside it")
            elif reason == "unfiled":
                since = f" since {self.when}" if self.when else ""
                parts.append(f"unfiled{since} — awaiting enrichment")
            elif reason == "low-confidence":
                via = f" via {self.source}" if self.source else ""
                parts.append(f"filed as {self.type or 'an untyped note'} at low confidence{via}")
        return "; ".join(parts)


def _list_value(raw: str) -> list:
    raw = (raw or "").strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    return [t.strip().strip("'\"") for t in raw.split(",") if t.strip()]


def _reasons(fm: dict) -> list:
    reasons = [f for f in _list_value(fm.get("review_flags", "")) if f in _REVIEW_FLAGS]
    if fm.get("status") == "unfiled":
        reasons.append("unfiled")
    if fm.get("filing_confidence") == "low":
        reasons.append("low-confidence")
    return reasons


def collect(vault: "Path | str") -> list:
    """Every note waiting for a judgment, newest first. Flat notes one level
    under each class only — a lane, an index, or the MOC itself never counts —
    and a note that is already settled (superseded, archived) is not waiting."""
    vault = Path(vault)
    out = []
    for cls in CLASS_DIRS:
        d = vault / "memory" / cls
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            if p.name == "_index.md" or p.name.startswith("Icon") or p.stem == MOC_SLUG:
                continue
            try:
                fm, _body = _frontmatter(p.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
            if fm.get("lifecycle") in _SETTLED_LIFECYCLES:
                continue
            reasons = _reasons(fm)
            if not reasons:
                continue
            when = (fm.get("captured") or fm.get("created") or "")[:10]
            out.append(Entry(
                rel=p.relative_to(vault).as_posix(), slug=fm.get("slug") or p.stem,
                title=fm.get("title") or p.stem.replace("-", " "), type=fm.get("type", ""),
                reasons=reasons, related=fm.get("related", ""), when=when,
                source=fm.get("source", ""),
            ))
    out.sort(key=lambda e: (e.when, e.rel), reverse=True)
    return out


def summary(vault: "Path | str") -> dict:
    """Counts for the scorecard: one note counts once in `total`, and once
    under each reason it carries in `by_reason`."""
    entries = collect(vault)
    by_reason = {r: 0 for r in REASON_ORDER}
    for e in entries:
        for r in e.reasons:
            by_reason[r] += 1
    return {"total": len(entries), "by_reason": by_reason}


_SECTION_TITLES = {
    "near-duplicate": "Probable duplicates",
    "update-candidate": "Same key, different body",
    "unfiled": "Unfiled captures",
    "low-confidence": "Filed at low confidence",
}


def render(entries: list, *, created: str, today: str) -> str:
    lines = [
        "---",
        "title: needs review",
        "kind: moc",
        "status: active",
        f"created: {created}",
        f"updated: {today}",
        "tags: [moc, needs-review]",
        "group: memory",
        f"slug: {MOC_SLUG}",
        "generated_by: needs_review.py",
        "---",
        "",
        "# Needs review",
        "",
        "[[Home]]",
        "",
        f"{len(entries)} note(s) waiting for a judgment. Generated from the notes' own "
        "frontmatter — `filing_confidence`, `status`, `review_flags` — not edited by hand: "
        "re-judge the note (raise its confidence, enrich it, supersede it) and this page "
        "regenerates without it.",
        "",
    ]
    for reason in REASON_ORDER:
        group = [e for e in entries if e.primary == reason]
        if not group:
            continue
        lines += [f"## {_SECTION_TITLES[reason]} ({len(group)})", ""]
        for e in group:
            lines.append(f"- [[{e.slug}]] — {e.title} · {e.phrase()}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def write(vault: "Path | str", *, today: "str | None" = None) -> Path:
    """Regenerate the MOC. `created` survives regeneration (the page is one
    page, not a page a day); `updated` is today."""
    vault = Path(vault)
    today = today or date.today().isoformat()
    target = vault / MOC_REL
    created = today
    if target.exists():
        try:
            created = _frontmatter(target.read_text(encoding="utf-8"))[0].get("created") or today
        except (OSError, UnicodeDecodeError):
            pass
    target.parent.mkdir(parents=True, exist_ok=True)
    text = render(collect(vault), created=created, today=today)
    if not target.exists() or target.read_text(encoding="utf-8") != text:
        target.write_text(text, encoding="utf-8")
    return target


def _parse_args(argv: list) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="the needs-review reading and its generated MOC")
    p.add_argument("--vault", required=True, help="the memory root (the directory holding memory/)")
    p.add_argument("--write", action="store_true", help="regenerate memory/mocs/needs-review.md")
    p.add_argument("--json", action="store_true", help="print the summary as JSON")
    return p.parse_args(argv)


def main(argv: "list | None" = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    vault = Path(args.vault)
    if not (vault / "memory").is_dir():
        print(f"error: no memory/ under {vault}", file=sys.stderr)
        return 2
    if args.write:
        print(write(vault))
    s = summary(vault)
    if args.json:
        print(json.dumps(s, indent=2))
    else:
        parts = ", ".join(f"{k} {v}" for k, v in s["by_reason"].items() if v) or "nothing waiting"
        print(f"needs review: {s['total']} — {parts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
