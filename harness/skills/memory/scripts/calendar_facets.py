#!/usr/bin/env python3
"""calendar_facets.py — the daily register's facet writers.

Filing v2 part 5, the calendar (design §DD5). `Calendar/YYYY/` holds one
note per day per facet — `YYYY-MM-DD-<facet>.md` for the facets the contract
registers (meetings, correspondence, docs, diary) — and a facet note exists
only on a day that had content for it. Facet membership is the selection
rule: a meeting happened, correspondence produced a reply or a deadline,
substantive work landed in an artifact, or something was worth a line in the
diary, the zero-bar catch-all where the operator's quick capture lands. No
importance score anywhere.

A facet note is append-only while its day is open: new content arrives as a
new paragraph at the end, and nothing already written is edited — that is
what keeps the chunker's earlier chunks stable and the register a record of
trajectory rather than a document that was tidied later. The frontmatter is
written once, at creation, and never touched again by an append.

The register is a vault-root space, a sibling of the memory root like
`Projects/`: discovered, never conjured. Flat layout — `<memory-root>/Calendar`;
nested layout — the memory root sits inside an Obsidian vault, witnessed by
`.obsidian/` at the parent and none at the memory root itself, so the
register is `<vault-root>/Calendar`. The year directory and the day's facet
file are created lazily; the space itself is not.

Usage:
    calendar_facets.py --vault <memory-root> append --facet meetings --text "…" [--day YYYY-MM-DD]
    calendar_facets.py --vault <memory-root> quick --text "…"
    calendar_facets.py --vault <memory-root> correct --facet meetings --day YYYY-MM-DD --text "…"
    calendar_facets.py --vault <memory-root> facets

The day index (`calendar_index.py`) is regenerated after every append.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from vault_lock import atomic_write, vault_mutex  # noqa: E402

SPACE = "Calendar"
FACET_KIND = "calendar-facet"
DIARY = "diary"
# The registry when no contract answers: the four facets the operator ruled
# on 2026-09-01. The contract is the source; this is the fallback that keeps a
# checkout with no vault attached able to write a diary line in a test.
DEFAULT_FACETS = ("meetings", "correspondence", "docs", DIARY)


class ClosedDay(ValueError):
    """A day before today is closed: its facet notes are a record, and a
    record is corrected by a new dated entry that supersedes it, never by an
    edit into the past. `correct()` is that entry."""


class UnknownFacet(ValueError):
    """A facet the registry does not carry. Adding one is an edit to
    `standards/storage-rules.md`, gated by the recurrence rule — never a
    call-site improvisation."""


@dataclass
class Appended:
    path: Path
    rel: str          # vault-root-relative, e.g. Calendar/2026/2026-09-04-meetings.md
    day: date
    facet: str
    created: bool     # True when this append brought the note into being


# ── the space ─────────────────────────────────────────────────────────────────

def _is_dir_exact(path: Path) -> bool:
    try:
        return path.is_dir() and path.name in {p.name for p in path.parent.iterdir()}
    except OSError:
        return False


def calendar_root(vault: "Path | str") -> "Path | None":
    """The vault-root `Calendar/` space, discovered never conjured — the same
    two rungs the projects space uses. None when no register exists yet."""
    vault = Path(vault)
    flat = vault / SPACE
    if _is_dir_exact(flat):
        return flat
    parent = vault.parent
    if (parent / ".obsidian").is_dir() and not (vault / ".obsidian").is_dir():
        sibling = parent / SPACE
        if _is_dir_exact(sibling):
            return sibling
    return None


def vault_root_of(vault: "Path | str") -> Path:
    """The directory `Calendar/` sits under — the vault root in the nested
    layout, the memory root in the flat one."""
    root = calendar_root(vault)
    return root.parent if root is not None else Path(vault)


# ── the registry ──────────────────────────────────────────────────────────────

def facets(rules=None) -> tuple:
    """The contract's facet registry, in registry order."""
    try:
        import storage_rules  # same skill dir
        registered = tuple((rules or storage_rules.rules()).facets())
        if registered:
            return registered
    except Exception:
        pass
    return DEFAULT_FACETS


# ── the notes ─────────────────────────────────────────────────────────────────

def facet_rel(day: date, facet: str) -> str:
    return f"{SPACE}/{day.year:04d}/{day.isoformat()}-{facet}.md"


def _frontmatter(day: date, facet: str, now: datetime) -> str:
    stamp = now.date().isoformat()
    return "\n".join([
        "---",
        f"kind: {FACET_KIND}",
        "status: active",
        "altitude: artifact",
        f"created: {stamp}",
        f"updated: {stamp}",
        f"tags: [calendar, {facet}]",
        "group: calendar",
        f"slug: {day.isoformat()}-{facet}",
        f"day: {day.isoformat()}",
        f"facet: {facet}",
        "---",
        "",
        f"# {day.isoformat()} — {facet}",
        "",
    ]) + "\n"


def _paragraph(text: str, now: datetime) -> str:
    body = " ".join(text.strip().split("\n")).strip()
    return f"\n{now.strftime('%H:%M')} — {body}\n"


def append(vault: "Path | str", facet: str, text: str, *, day: "date | None" = None,
           now: "datetime | None" = None, rules=None, lock_timeout: float = 10.0,
           index: bool = True) -> Appended:
    """Add one paragraph to the day's facet note, creating the note (and its
    year directory) on first use. Append-only: the frontmatter and every
    earlier paragraph are left byte for byte as they were. A day before
    today is closed and refuses the append — see `correct()`."""
    if not text or not text.strip():
        raise ValueError("nothing to record: the text is empty")
    now = now or datetime.now(timezone.utc).astimezone()
    if day is not None and day < now.date():
        raise ClosedDay(
            f"{day.isoformat()} is closed; the register is corrected by a new dated entry, never by an edit "
            f"into the past: calendar_facets.py --vault <memory-root> correct --facet {facet} --day "
            f"{day.isoformat()} --text \"…\"")
    registry = facets(rules)
    if facet not in registry:
        raise UnknownFacet(
            f"facet {facet!r} is not registered; the register carries {', '.join(registry)}. "
            "Adding a facet is an edit to standards/storage-rules.md, proposed when a pattern "
            "recurs three times in the diary — never a call-site improvisation.")
    vault = Path(vault)
    root = calendar_root(vault)
    if root is None:
        raise FileNotFoundError(
            f"no Calendar/ space beside {vault}: the register is discovered, never conjured — "
            "create the directory once at the vault root.")
    day = day or now.date()
    rel = facet_rel(day, facet)
    target = root / f"{day.year:04d}" / f"{day.isoformat()}-{facet}.md"
    _write_entry(vault, target, _frontmatter(day, facet, now), text, now, lock_timeout)
    created = getattr(_write_entry, "created", False)
    if index:
        # The day index follows every append (task 2): regenerated from what
        # exists, byte-stable when nothing changed. Outside the mutex, since
        # the generator takes it itself.
        import calendar_index  # same skill dir; function-local to keep the import graph flat
        calendar_index.regenerate(vault, day, lock_timeout=lock_timeout)
    return Appended(path=target, rel=rel, day=day, facet=facet, created=created)


def _write_entry(vault: Path, target: Path, head: str, text: str, now: datetime, lock_timeout: float) -> None:
    """Create the note with `head` on first use, else append one paragraph.
    The append-only discipline lives here, for facet notes and corrections
    alike. Leaves `created` on the function for the caller (one process, one
    call at a time under the mutex)."""
    with vault_mutex(vault, timeout=lock_timeout):
        created = not target.exists()
        if created:
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(target, head + _paragraph(text, now).lstrip("\n"))
        else:
            with target.open("a", encoding="utf-8") as fh:
                fh.write(_paragraph(text, now))
    _write_entry.created = created


def correction_rel(today: date, facet: str, corrected: date) -> str:
    return f"{SPACE}/{today.year:04d}/{today.isoformat()}-{facet}-corrects-{corrected.isoformat()}.md"


def _correction_frontmatter(today: date, facet: str, corrected: date, now: datetime) -> str:
    stamp = now.date().isoformat()
    return "\n".join([
        "---",
        f"kind: {FACET_KIND}",
        "status: active",
        "altitude: artifact",
        f"created: {stamp}",
        f"updated: {stamp}",
        f"tags: [calendar, {facet}, correction]",
        "group: calendar",
        f"slug: {today.isoformat()}-{facet}-corrects-{corrected.isoformat()}",
        f"day: {today.isoformat()}",
        f"facet: {facet}",
        f"corrects: {corrected.isoformat()}",
        f"supersedes: {facet_rel(corrected, facet)}",
        "---",
        "",
        f"# {today.isoformat()} — {facet} — corrects {corrected.isoformat()}",
        "",
        f"A correction to [[{corrected.isoformat()}-{facet}]]. The original stays as it was; this entry supersedes it.",
        "",
    ]) + "\n"


def correct(vault: "Path | str", facet: str, corrected: date, text: str, *, now: "datetime | None" = None,
            rules=None, lock_timeout: float = 10.0, index: bool = True) -> Appended:
    """Correct a closed day: a new entry dated today, in its own note that
    carries `supersedes:` back to the original facet note — the graph reads
    that edge — and `corrects:` naming the day. The original is never
    touched. A second correction of the same day and facet, the same day,
    appends to the same correction note."""
    if not text or not text.strip():
        raise ValueError("nothing to record: the text is empty")
    registry = facets(rules)
    if facet not in registry:
        raise UnknownFacet(f"facet {facet!r} is not registered; the register carries {', '.join(registry)}.")
    vault = Path(vault)
    root = calendar_root(vault)
    if root is None:
        raise FileNotFoundError(f"no Calendar/ space beside {vault}: the register is discovered, never conjured.")
    now = now or datetime.now(timezone.utc).astimezone()
    today = now.date()
    if corrected >= today:
        raise ValueError(f"{corrected.isoformat()} is still open; append to it instead of correcting it")
    original = root / f"{corrected.year:04d}" / f"{corrected.isoformat()}-{facet}.md"
    if not original.is_file():
        raise FileNotFoundError(f"nothing to correct: {facet_rel(corrected, facet)} does not exist")
    rel = correction_rel(today, facet, corrected)
    target = root / f"{today.year:04d}" / Path(rel).name
    _write_entry(vault, target, _correction_frontmatter(today, facet, corrected, now), text, now, lock_timeout)
    created = getattr(_write_entry, "created", False)
    if index:
        import calendar_index  # same skill dir
        calendar_index.regenerate(vault, today, lock_timeout=lock_timeout)
        calendar_index.regenerate(vault, corrected, lock_timeout=lock_timeout)
    return Appended(path=target, rel=rel, day=today, facet=facet, created=created)


def corrections_of(vault: "Path | str", day: date) -> list:
    """(facet, path) for every correction note that supersedes one of the
    day's facet notes, whatever day it was written."""
    root = calendar_root(vault)
    if root is None:
        return []
    out = []
    suffix = f"-corrects-{day.isoformat()}.md"
    for p in sorted(root.glob(f"*/*{suffix}")):
        name = p.name[: -len(suffix)]           # YYYY-MM-DD-<facet>
        out.append((name[11:], p))
    return out


def quick(vault: "Path | str", text: str, *, now: "datetime | None" = None, **kw) -> Appended:
    """The operator's quick capture: today's diary, the zero-bar catch-all."""
    return append(vault, DIARY, text, now=now, **kw)


def notes_for_day(vault: "Path | str", day: date) -> list:
    """The facet notes that exist for a day, in registry order — exactly the
    files, never a facet that has no note."""
    root = calendar_root(vault)
    if root is None:
        return []
    out = []
    for facet in facets():
        p = root / f"{day.year:04d}" / f"{day.isoformat()}-{facet}.md"
        if p.is_file():
            out.append((facet, p))
    return out


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description="the daily register's facet writers")
    ap.add_argument("--vault", required=True, help="the memory root (the directory holding memory/)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("append", help="add a paragraph to a facet note")
    a.add_argument("--facet", required=True)
    a.add_argument("--text", required=True)
    a.add_argument("--day", help="YYYY-MM-DD (default: today)")
    q = sub.add_parser("quick", help="a diary line for today")
    q.add_argument("--text", required=True)
    c = sub.add_parser("correct", help="correct a closed day with a new dated entry that supersedes it")
    c.add_argument("--facet", required=True)
    c.add_argument("--day", required=True, help="the closed day being corrected, YYYY-MM-DD")
    c.add_argument("--text", required=True)
    sub.add_parser("facets", help="print the registry")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.cmd == "facets":
            print("\n".join(facets()))
            return 0
        if args.cmd == "quick":
            r = quick(args.vault, args.text)
        elif args.cmd == "correct":
            r = correct(args.vault, args.facet, date.fromisoformat(args.day), args.text)
        else:
            day = date.fromisoformat(args.day) if args.day else None
            r = append(args.vault, args.facet, args.text, day=day)
        print(f"{'created' if r.created else 'appended'} {r.rel}")
        return 0
    except (ClosedDay, UnknownFacet, ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
