#!/usr/bin/env python3
"""Gate: the root notes the maps retired stay retired (agentm-vault plan 07).

`moc-root.md` is the agent's entry point, generated with the other maps, and
the vault root's `index.md` is the one root map, yours. So once the maps data
run has gone:

  - `Home.md` is gone from the memory root and `Filing.md` from the vault root;
  - no note links to either;
  - `index.md` carries the write-authority table once.

A link is matched by its text, not by where it resolves. With the map gone, a
bare `[[Home]]` quietly opens `Personal/Home/…/Home.md`, and a dangling-link
check reads that as green. A record under a `_harness/` directory keeps the
names it was written with, `Personal/` is yours, and a link shown inside code
is not a link, so none of those is a finding.

It reads the live vault, resolved at runtime. Until the maps data run writes
`memory/.maps-and-root-notes-complete` it reports what it finds and exits 0;
once the marker exists it enforces.

Usage:
  python3 scripts/check-root-notes.py                 # the resolved memory root
  python3 scripts/check-root-notes.py --memory-root DIR
Exit: 0 clean (or nothing to check, or before the data run); 1 on a finding.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TOOLKIT = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for _p in (str(_HERE), str(_TOOLKIT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import maps_shape as ms  # noqa: E402
import markdown_spans  # noqa: E402
import vault_layout  # noqa: E402

SKIP_DIRS = {".git", ".obsidian", ".trash", "_harness"}
OWNER_SPACES = {"Personal"}
RETIRED_NAMES = {"home", "filing"}
_WIKILINK = re.compile(r"\[\[([^\]\n]+?)\]\]")
_MDLINK = re.compile(r"\]\(([^)\s]+?\.md)(?:#[^)]*)?\)", re.IGNORECASE)
_INLINE_CODE = re.compile(r"(`+)[^\n]*?\1")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}")


def vault_root(memory_root: Path) -> Path:
    return vault_layout.vault_root_candidates(memory_root)[0]


def visible(text: str) -> str:
    """The text with code blanked out and its newlines kept, so a line number
    still points at the line a link sits on."""
    chars = list(text)
    for start, end in markdown_spans.fenced_ranges(text):
        for i in range(start, end):
            if chars[i] != "\n":
                chars[i] = " "
    return _INLINE_CODE.sub(lambda m: " " * len(m.group(0)), "".join(chars))


def authority_tables(text: str) -> int:
    """How many markdown tables carry an `authority` column."""
    lines = visible(text).splitlines()
    count = 0
    for head, rule in zip(lines, lines[1:]):
        if head.lstrip().startswith("|") and _TABLE_RULE.match(rule):
            if "authority" in (cell.strip().lower() for cell in head.strip().strip("|").split("|")):
                count += 1
    return count


def _notes(vault: Path):
    for dirpath, dirnames, filenames in os.walk(vault):
        top = Path(dirpath) == vault
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not (top and d in OWNER_SPACES))
        for name in sorted(filenames):
            if name.endswith(".md"):
                yield Path(dirpath) / name


def _key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path)).lower()


def retired_links(vault: Path, memory_root: Path):
    """`(note, line, link)` for every link naming `Home.md` or `Filing.md`."""
    retired = {_key(memory_root / "Home.md"), _key(vault / "Filing.md")}

    def names_retired(note: Path, target: str) -> bool:
        target = target.strip()
        if "/" not in target:
            stem = target[:-3] if target.lower().endswith(".md") else target
            return stem.lower() in RETIRED_NAMES
        path = target.lstrip("/")
        if not path.lower().endswith(".md"):
            path += ".md"
        return any(_key(base / path) in retired for base in (vault, note.parent))

    for note in _notes(vault):
        if _key(note) in retired:
            continue
        try:
            text = visible(note.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        for m in _WIKILINK.finditer(text):
            target = re.split(r"\\?\||#", m.group(1), maxsplit=1)[0]
            if target.strip() and names_retired(note, target):
                yield note, text.count("\n", 0, m.start()) + 1, m.group(0)
        for m in _MDLINK.finditer(text):
            target = m.group(1).replace("%20", " ")
            if "://" not in target and names_retired(note, target):
                yield note, text.count("\n", 0, m.start()) + 1, m.group(0)


def _rel(path: Path, vault: Path) -> str:
    try:
        return path.relative_to(vault).as_posix()
    except ValueError:
        return str(path)


def findings(memory_root: Path) -> list:
    memory_root = Path(memory_root)
    vault = vault_root(memory_root)
    out = []
    home, filing, index = memory_root / "Home.md", vault / "Filing.md", vault / "index.md"
    if home.exists():
        out.append(f"{_rel(home, vault)}: still exists; moc-root.md is the agent's entry point")
    if filing.exists():
        out.append(f"{_rel(filing, vault)}: still exists; index.md carries the write-authority table")
    if index.is_file():
        n = authority_tables(index.read_text(encoding="utf-8", errors="replace"))
        if n != 1:
            out.append(f"index.md: carries the write-authority table {n} time(s), not once")
    else:
        out.append("index.md: missing; it is the vault's one root map")
    for note, line, link in retired_links(vault, memory_root):
        out.append(f"{_rel(note, vault)}:{line}: {link} names a retired note")
    return out


def check(memory_root: Path, out=sys.stdout) -> int:
    found = findings(memory_root)
    if not found:
        print("check-root-notes: clean — Home.md and Filing.md are retired, nothing links to them, "
              "and index.md carries the write-authority table once", file=out)
        return 0
    if not ms.data_run_done(memory_root):
        print(f"check-root-notes: before the maps data run — {len(found)} finding(s), which the data run "
              f"clears; enforced once memory/{ms.MARKER_NAME} exists", file=out)
        for f in found:
            print(f"  pending: {f}", file=out)
        return 0
    print(f"check-root-notes: {len(found)} finding(s)", file=out)
    for f in found:
        print(f"  {f}", file=out)
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--memory-root", default=None,
                    help="memory root to check (default: $MEMORY_ROOT, else the configured one)")
    args = ap.parse_args(argv)
    root = _resolve(args.memory_root)
    if root is None or not (root / "memory").is_dir():
        print("check-root-notes: no memory root resolves; nothing to check")
        return 0
    return check(root)


def _resolve(arg: str | None) -> Path | None:
    if arg:
        return Path(arg)
    root = vault_layout.env_memory_root()
    if root is None:
        try:
            import harness_memory as hm  # noqa: E402
            root = hm.memory_root()
        except ImportError:
            root = None
    return Path(root) if root else None


if __name__ == "__main__":
    raise SystemExit(main())
