#!/usr/bin/env python3
"""note_links.py — read a link-valued frontmatter key in any of its forms.

Task 193, LD-5 (agentm-vault): `supersedes`, `superseded_by`, `related` and
`derived_from` name other notes, and their values come in every shape the
writers have used: a bare memory-root path (`memory/semantic/x.md`), a bare
name (`x`), an absolute path, a quoted wikilink (`"[[x]]"`,
`"[[agent/memory/semantic/x|label]]"`), or a flow or block list of any of
those. The writers are moving to the quoted wikilink, the form Obsidian draws
as a link, and every reader must read both before any writer switches
(*Live renames*: readers first). This is the one parser the readers share.

`links(value)` takes what a frontmatter reader handed back, a string or a list,
and returns the notes it names, in order. A `<source id> at <version>` value
from the sources layer names a source version, not a note, and comes back with
`source=True`. The Go side is `daemon/internal/fmlist/links.go`, held to the
same cases.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_WIKILINK = re.compile(r"\[\[([^\[\]]+?)\]\]")
_SOURCE_VERSION = re.compile(r"^\S+ at \S+$")


@dataclass(frozen=True)
class Link:
    # The path or name as written, without quotes, brackets, label or anchor.
    target: str
    # A `<source id> at <version>` value: a source version, not a note.
    source: bool = False

    @property
    def stem(self) -> str:
        """The file name the link names, without `.md`."""
        name = self.target.rstrip("/").rsplit("/", 1)[-1]
        return name[:-3] if name.endswith(".md") else name

    def memory_rel(self, prefix: str = "") -> str:
        """The target as a memory-root path with `.md`. `prefix` is the memory
        root's own path in the vault (`agent`, or "" for a flat vault): a
        wikilink names a note from the vault root, a bare path from the
        memory root, and this reads both the same way."""
        t = self.target.lstrip("/")
        if prefix and t.startswith(prefix.rstrip("/") + "/"):
            t = t[len(prefix.rstrip("/")) + 1:]
        return t if t.endswith(".md") else t + ".md"


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return s


def _split_flow(inner: str) -> list:
    """A flow list's items: split on commas outside quotes and brackets."""
    out, cur, quote, depth = [], [], "", 0
    for ch in inner:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    out.append("".join(cur))
    return [p for p in (x.strip() for x in out) if p]


def _one(item: str) -> list:
    s = _unquote(item)
    if not s:
        return []
    found = _WIKILINK.findall(s)
    if found:
        out = []
        for inner in found:
            target = re.split(r"[|#]", inner, 1)[0].strip()
            if target:
                out.append(Link(target))
        return out
    s = _unquote(s.strip("[]").strip())
    if not s:
        return []
    if _SOURCE_VERSION.match(s) and "/" not in s:
        return [Link(s, source=True)]
    return [Link(s)]


def links(value) -> list:
    """The notes a link-valued key names, in order: [] for an empty value."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        out = []
        for v in value:
            out += links(v)
        return out
    s = str(value).strip()
    if not s:
        return []
    if s.startswith("[") and s.endswith("]") and not _WIKILINK.fullmatch(s):
        out = []
        for item in _split_flow(s[1:-1]):
            out += _one(item)
        return out
    return _one(s)


def first(value):
    """The first note the value names, skipping a source version; None for none."""
    return next((l for l in links(value) if not l.source), None)
