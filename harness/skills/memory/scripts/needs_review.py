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

The dream cycle adds three sections below the notes' own (agentm-vault plan
04): *Possible twins* (bodies at least 0.92 alike), *Shared keys, different
bodies* (contradiction triage), and *Proposed facets* (a diary label recurring
on three or more days). The cycle writes those findings to
`<engine state>/dreaming/review-proposals.json` and this page reads them; it
never merges, supersedes or registers anything. You act on them by hand.

Usage:
    needs_review.py --vault <memory-root> [--write] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path


_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import drive_artifacts  # noqa: E402  (same skill dir)
import engine_state  # noqa: E402  (same skill dir)

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

# Where the dream cycle leaves its twins, shared keys and facets. The name is
# dream.REVIEW_PROPOSALS_NAME; a test holds the two equal.
REVIEW_PROPOSALS_NAME = "review-proposals.json"


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
    judged: str = ""

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
                if self.judged:
                    # The batch read it and scored it under the floor: a
                    # judgment is on record, and the next move is yours.
                    parts.append(f"unfiled{since} — judged below the floor on {self.judged}")
                else:
                    parts.append(f"unfiled{since} — awaiting the batch")
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
            if p.name == "_index.md" or drive_artifacts.is_artifact(p) or p.stem == MOC_SLUG:
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
                source=fm.get("source", ""), judged=(fm.get("enriched_at") or "")[:10],
            ))
    out.sort(key=lambda e: (e.when, e.rel), reverse=True)
    return out


def read_proposals(state_dir: "Path | str | None" = None) -> dict:
    """The dream cycle's last twins, shared keys and facets. Missing or
    unreadable reads as none: the page still renders the notes' own marks."""
    base = Path(state_dir) if state_dir is not None else engine_state.engine_state_dir()
    empty = {"at": None, "twins": [], "same_key": [], "facets": []}
    try:
        data = json.loads((base / "dreaming" / REVIEW_PROPOSALS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    if not isinstance(data, dict):
        return empty
    out = {"at": data.get("at")}
    for key in ("twins", "same_key", "facets"):
        items = data.get(key)
        out[key] = [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
    return out


def summary(vault: "Path | str", *, proposals: "dict | None" = None) -> dict:
    """Counts for the scorecard: one note counts once in `total`, and once
    under each reason it carries in `by_reason`. The dream cycle's findings
    count separately — they are pairs and labels, not notes."""
    entries = collect(vault)
    by_reason = {r: 0 for r in REASON_ORDER}
    for e in entries:
        for r in e.reasons:
            by_reason[r] += 1
    proposals = read_proposals() if proposals is None else proposals
    return {"total": len(entries), "by_reason": by_reason,
            "dreaming": {k: len(proposals.get(k) or []) for k in ("twins", "same_key", "facets")}}


_SECTION_TITLES = {
    "near-duplicate": "Probable duplicates",
    "update-candidate": "Same key, different body",
    "unfiled": "Unfiled captures",
    "low-confidence": "Filed at low confidence",
}


def _link(rel: str) -> str:
    return f"[[{Path(str(rel)).stem}]]"


def _proposal_lines(proposals: dict) -> list:
    """The three dream sections. Each line names what to do, because the page
    is where the decision gets made."""
    lines = []
    twins = proposals.get("twins") or []
    if twins:
        lines += [f"## Possible twins ({len(twins)})", ""]
        for t in twins:
            sim = t.get("similarity")
            alike = f"{sim:.0%} alike" if isinstance(sim, (int, float)) else "alike"
            lines.append(f"- {_link(t.get('a', ''))} and {_link(t.get('b', ''))} — {alike} · "
                         "merge by hand, or write `superseded_by` on the one that should go")
        lines.append("")
    same = proposals.get("same_key") or []
    if same:
        lines += [f"## Shared keys, different bodies ({len(same)})", ""]
        for c in same:
            links = ", ".join(_link(p) for p in c.get("paths") or [])
            lines.append(f"- key `{c.get('slug', '?')}`: {links} — two notes claiming to be one "
                         "memory and saying different things")
        lines.append("")
    facets = proposals.get("facets") or []
    if facets:
        lines += [f"## Proposed facets ({len(facets)})", ""]
        for f in facets:
            lines.append(f"- `{f.get('label', '?')}` on {f.get('days', '?')} days "
                         f"({f.get('first', '?')} … {f.get('last', '?')}) — register it under "
                         "`facets:` in standards/storage-rules.md if it is a facet")
        lines.append("")
    return lines


def render(entries: list, *, created: str, today: str, proposals: "dict | None" = None) -> str:
    proposals = proposals or {}
    lines = [
        "---",
        "title: needs review",
        "kind: moc",
        "status: active",
        f"created: {created}",
        f"updated: {today}",
        "tags: [moc, needs-review]",
        f"slug: {MOC_SLUG}",
        "generated_by: needs_review.py",
        "---",
        "",
        "# Needs review",
        "",
        "[[moc-root]]",
        "",
        f"{len(entries)} note(s) waiting for a judgment. Generated from the notes' own "
        "frontmatter — `filing_confidence`, `status`, `review_flags` — not edited by hand: "
        "re-judge the note (raise its confidence, enrich it, supersede it) and this page "
        "regenerates without it.",
        "",
    ]
    dream_lines = _proposal_lines(proposals)
    for reason in REASON_ORDER:
        group = [e for e in entries if e.primary == reason]
        if not group:
            continue
        lines += [f"## {_SECTION_TITLES[reason]} ({len(group)})", ""]
        for e in group:
            lines.append(f"- [[{e.slug}]] — {e.title} · {e.phrase()}")
        lines.append("")
    if dream_lines:
        at = proposals.get("at")
        when = ""
        if isinstance(at, (int, float)):
            when = f" of {datetime.fromtimestamp(at, tz=timezone.utc).date().isoformat()}"
        lines += [f"The sections below come from the dream cycle{when}. Nothing acts on them "
                  "but you.", ""]
        lines += dream_lines
    return "\n".join(lines).rstrip("\n") + "\n"


def write(vault: "Path | str", *, today: "str | None" = None,
          proposals: "dict | None" = None) -> Path:
    """Regenerate the MOC. `created` survives regeneration (the page is one
    page, not a page a day); `updated` is today. The dream sections come from
    the engine state unless `proposals` is handed in."""
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
    text = render(collect(vault), created=created, today=today,
                  proposals=read_proposals() if proposals is None else proposals)
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
        dream = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in s["dreaming"].items() if v)
        print(f"needs review: {s['total']} — {parts}" + (f"; from dreaming: {dream}" if dream else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
