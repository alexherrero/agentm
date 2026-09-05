#!/usr/bin/env python3
"""calendar_index.py — the generated day index of the daily register.

Filing v2 part 5, task 2. The bare-date file `Calendar/YYYY/YYYY-MM-DD.md`
is generated, never authored: it lists exactly the facet notes that exist for
the day, each with a context phrase (the first entry's words and how many
entries the note holds), links to the day's episodic session traces when any
exist, and the system digest when one was written. Nothing else appears, and
a day with nothing on it has no index at all.

Regeneration is idempotent: the frontmatter carries the day, not the moment
of regeneration, so an unchanged day regenerates to the same bytes and the
file is left untouched. The facet writer regenerates after every append;
the dreaming cadence regenerates the days it touches.

Usage:
    calendar_index.py --vault <memory-root> --day YYYY-MM-DD [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import calendar_facets as cf  # noqa: E402
from vault_lock import atomic_write, vault_mutex  # noqa: E402

INDEX_KIND = "day-index"
DIGEST_DIR = ("diagnostics", "digests")
_ENTRY = re.compile(r"(?m)^\d{2}:\d{2} — (.*)$")
_PHRASE_CHARS = 120


def index_rel(day: date) -> str:
    return f"{cf.SPACE}/{day.year:04d}/{day.isoformat()}.md"


def _split(text: str) -> "tuple[dict, str]":
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    fm = {}
    for line in text[4:end].splitlines():
        k, sep, v = line.partition(":")
        if sep:
            fm.setdefault(k.strip(), v.strip().strip("'\""))
    return fm, text[end + 5:]


def _phrase(path: Path) -> "tuple[str, int]":
    """The first entry's words, cut on a word boundary, and the entry count."""
    _fm, body = _split(path.read_text(encoding="utf-8"))
    entries = _ENTRY.findall(body)
    if not entries:
        return "", 0
    first = entries[0].strip()
    if len(first) > _PHRASE_CHARS:
        first = first[:_PHRASE_CHARS].rsplit(" ", 1)[0] + " …"
    return first, len(entries)


def _corrections_written_on(vault: "Path | str", day: date) -> list:
    """(facet, corrected-day, path) for the correction notes dated `day`."""
    root = cf.calendar_root(vault)
    if root is None:
        return []
    out = []
    for p in sorted((root / f"{day.year:04d}").glob(f"{day.isoformat()}-*-corrects-*.md")):
        stem = p.stem                              # YYYY-MM-DD-<facet>-corrects-YYYY-MM-DD
        facet, corrected = stem[11:].rsplit("-corrects-", 1)
        out.append((facet, corrected, p))
    return out


def episodic_traces(vault: "Path | str", day: date) -> list:
    """(slug, title) for every flat episodic note that belongs to the day —
    by its `day:` field, else by `created:`. None exist before the
    lifecycle-dreaming part writes them; the index simply has no section then."""
    d = Path(vault) / "memory" / "episodic"
    if not d.is_dir():
        return []
    out = []
    key = day.isoformat()
    for p in sorted(d.glob("*.md")):
        if p.name == "_index.md":
            continue
        try:
            fm, _ = _split(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if (fm.get("day") or fm.get("created", ""))[:10] == key:
            out.append((fm.get("slug") or p.stem, fm.get("title") or p.stem.replace("-", " ")))
    return out


def digest_embed(vault: "Path | str", day: date) -> "str | None":
    """The Obsidian embed for the day's digest, when the digest exists —
    written vault-root-relative, so the nested layout's `Agent/` prefix
    comes from the layout rather than a remembered path."""
    vault = Path(vault)
    name = f"{day.strftime('%Y%m%d')}-digest-daily"
    p = vault.joinpath(*DIGEST_DIR) / f"{name}.md"
    if not p.is_file():
        return None
    vroot = cf.vault_root_of(vault)
    try:
        rel = p.with_suffix("").relative_to(vroot).as_posix()
    except ValueError:
        rel = p.with_suffix("").as_posix()
    return f"![[{rel}]]"


def render(vault: "Path | str", day: date) -> "str | None":
    """The index text, or None when the day has nothing — no facet note, no
    trace, no digest — so that nothing is written for an empty day."""
    facets = cf.notes_for_day(vault, day)
    traces = episodic_traces(vault, day)
    digest = digest_embed(vault, day)
    corrections_out = _corrections_written_on(vault, day)
    corrections_in = cf.corrections_of(vault, day)
    if not facets and not traces and not digest and not corrections_out:
        return None
    key = day.isoformat()
    lines = [
        "---",
        f"kind: {INDEX_KIND}",
        "status: active",
        "altitude: artifact",
        f"created: {key}",
        f"updated: {key}",
        "tags: [calendar, day-index]",
        "group: calendar",
        f"slug: {key}",
        f"day: {key}",
        "generated_by: calendar_index.py",
        "---",
        "",
        f"# {key}",
        "",
        "Generated from the day's facet notes — the notes are the source; this page is regenerated, never edited.",
        "",
    ]
    for facet, path in facets:
        phrase, n = _phrase(path)
        count = f"{n} entr{'y' if n == 1 else 'ies'}"
        lines += [f"## {facet}", "", f"- [[{path.stem}]] — {phrase} ({count})" if phrase else f"- [[{path.stem}]] ({count})", ""]
    if corrections_out:
        lines += ["## Corrections made today", ""]
        for facet, corrected, path in corrections_out:
            phrase, _n = _phrase(path)
            lines.append(f"- [[{path.stem}]] — corrects {corrected} ({facet}): {phrase}" if phrase else f"- [[{path.stem}]] — corrects {corrected} ({facet})")
        lines.append("")
    if corrections_in:
        lines += ["## Corrected later", ""] + [f"- [[{p.stem}]] ({facet})" for facet, p in corrections_in] + [""]
    if traces:
        lines += ["## Session traces", ""] + [f"- [[{slug}]] — {title}" for slug, title in traces] + [""]
    if digest:
        lines += ["## Digest", "", digest, ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def regenerate(vault: "Path | str", day: date, *, lock_timeout: float = 10.0) -> "Path | None":
    """Write the day's index when its content changed; leave it byte for byte
    when it did not; remove nothing (an index for a day that later lost all
    its notes stays, listing nothing — a purge is an operator's act). Returns
    the index path, or None when the day has nothing to index."""
    vault = Path(vault)
    root = cf.calendar_root(vault)
    text = render(vault, day)
    if root is None or text is None:
        return None
    target = root / f"{day.year:04d}" / f"{day.isoformat()}.md"
    with vault_mutex(vault, timeout=lock_timeout):
        try:
            current = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            current = None
        if current != text:
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(target, text)
    return target


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description="the daily register's generated day index")
    ap.add_argument("--vault", required=True, help="the memory root (the directory holding memory/)")
    ap.add_argument("--day", required=True, help="YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true", help="print the index instead of writing it")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    day = date.fromisoformat(args.day)
    if args.dry_run:
        text = render(args.vault, day)
        print(text if text is not None else f"(nothing on {day.isoformat()})")
        return 0
    out = regenerate(args.vault, day)
    print(out if out is not None else f"(nothing on {day.isoformat()}; no index written)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
