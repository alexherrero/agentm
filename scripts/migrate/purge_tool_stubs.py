#!/usr/bin/env python3
"""purge_tool_stubs.py — retire the tool-invocation stubs from the corpus.

The retired auto-miner wrote one note per tool per session whose whole body
was a placeholder: "The `Bash` tool was invoked 2592 times during this
session. If this represents a repeatable workflow, capture the sequence +
when to use it." Nothing ever captured the sequence. On 2026-09-04 these
were 824 of the 1,490 flat memories — counts, not procedures — and the
operator ruled them out: the miner stops emitting them (reflect.py) and the
existing ones are purged.

A purge is the operator's act. This script is a dry run by default: it walks
the class directories, lists every stub with its path, title and body hash in
a manifest, reports any inbound wikilink (a stub is a leaf; a link to one is
worth a look before it goes), and writes nothing else. `--apply` removes the
manifested notes only with `--confirm-count N` equal to the manifest — the
count is the ruling, and any other number stops the run. Run it post-merge
under a quiesced daemon, commit the vault before and after, and let the
daemon's reconcile drop the removed files from the index.

Usage:
    purge_tool_stubs.py --vault <memory-root> [--report-dir <dir>]
    purge_tool_stubs.py --vault <memory-root> --apply --confirm-count 824
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

CLASS_DIRS = ("semantic", "procedural", "episodic", "entities", "crystallized", "mocs")
STUB_RE = re.compile(
    r"The `(?P<tool>[^`]+)` tool was invoked (?P<count>\d+) times during this session\.",
)
_LINK_RE = re.compile(r"\[\[([^\]|#]+)")


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


def find_stubs(vault: Path) -> list:
    """Every flat class note whose body is the stub template. A note that
    mentions the phrase inside a longer body is not a stub; the template is
    the whole of what a stub says."""
    out = []
    for cls in CLASS_DIRS:
        d = vault / "memory" / cls
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            if p.name == "_index.md" or p.name.startswith("Icon"):
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            fm, body = _split(text)
            stripped = body.strip()
            m = STUB_RE.match(stripped)
            if not m:
                continue
            rest = stripped[m.end():].strip()
            if rest and not rest.startswith("If this represents a repeatable workflow"):
                continue
            out.append({
                "path": p.relative_to(vault).as_posix(),
                "slug": fm.get("slug") or p.stem,
                "title": fm.get("title") or f"Workflow: {m.group('tool')} used {m.group('count')}x",
                "tool": m.group("tool"), "count": m.group("count"),
                "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            })
    return out


def inbound_links(vault: Path, stems: set) -> list:
    """(linking note, stub stem) for every wikilink into a stub."""
    hits = []
    mem = vault / "memory"
    for p in mem.rglob("*.md"):
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for target in _LINK_RE.findall(text):
            t = target.strip().split("/")[-1]
            if t in stems and p.stem != t:
                hits.append((p.relative_to(vault).as_posix(), t))
    return hits


def write_manifest(rows: list, links: list, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = out_dir / "purge-manifest.csv"
    with manifest.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["path", "slug", "title", "tool", "count", "sha256"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    with (out_dir / "inbound-links.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["linking_note", "stub"])
        w.writerows(links)
    (out_dir / "REPORT.md").write_text(
        f"# Tool-stub purge — {'dry run' if not rows or True else ''}\n\n"
        f"- stubs found: **{len(rows)}**\n- inbound wikilinks into stubs: **{len(links)}**\n"
        f"- manifest: `purge-manifest.csv` (path, slug, title, tool, count, sha256)\n\n"
        "Apply with `--apply --confirm-count N` where N equals the count above; any other number is refused.\n",
        encoding="utf-8")
    return manifest


def apply(vault: Path, rows: list, *, confirm_count) -> int:
    if confirm_count is None or confirm_count != len(rows):
        raise SystemExit(
            f"purge refused: the manifest holds {len(rows)} notes and --confirm-count "
            f"{'is missing' if confirm_count is None else f'says {confirm_count}'}. The count is the ruling.")
    removed = 0
    for r in rows:
        p = vault / r["path"]
        if p.exists():
            p.unlink()
            removed += 1
    return removed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--vault", required=True, help="the memory root (the directory holding memory/)")
    ap.add_argument("--report-dir", help="where the manifest goes (default: <vault>/diagnostics/migrations/tool-stub-purge/<ts>)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--confirm-count", type=int)
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    vault = Path(args.vault)
    if not (vault / "memory").is_dir():
        print(f"error: no memory/ under {vault}", file=sys.stderr)
        return 2
    rows = find_stubs(vault)
    links = inbound_links(vault, {Path(r["path"]).stem for r in rows})
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_dir = Path(args.report_dir) if args.report_dir else vault / "diagnostics" / "migrations" / "tool-stub-purge" / f"{ts}-{'apply' if args.apply else 'dry'}"
    manifest = write_manifest(rows, links, out_dir)
    print(f"stubs: {len(rows)} · inbound links: {len(links)} · manifest {manifest}")
    if not args.apply:
        return 0
    removed = apply(vault, rows, confirm_count=args.confirm_count)
    print(f"applied purge: removed {removed} of {len(rows)} manifested notes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
