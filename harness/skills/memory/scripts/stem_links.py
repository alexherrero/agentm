#!/usr/bin/env python3
"""stem_links.py — the unique-stem rule for the links a writer generates.

Task 193, LD-4 (agentm-vault): a generated link names a note by its bare file
name only when no other note in the vault has that name. Obsidian resolves a
bare `[[name]]` against every note in the vault, and when several share the
name it lands on whichever one it prefers, so a shared name is linked by the
note's vault-relative path with the name as its label:
`[[agent/memory/semantic/_index|_index]]`. A unique name keeps its bare link,
so a page whose names are all unique renders exactly as it did before.

The names counted are Obsidian's: every `.md` file outside the dot-folders,
matched case-insensitively in NFC. The walk lists names and opens no note.
The Go side is `daemon/internal/dreaming/stemlinks.go`, held to the same cases.
"""
from __future__ import annotations

import os
import unicodedata
from collections import Counter
from pathlib import Path

# A label may not close the link or start an alias inside it.
_LABEL_TABLE = str.maketrans({"|": "-", "[": "(", "]": ")", "\n": " "})


def stem_key(stem: str) -> str:
    """A name as Obsidian compares it: NFC, case folded."""
    return unicodedata.normalize("NFC", stem).lower()


class StemIndex:
    """How many notes in the vault carry each file name."""

    def __init__(self, vault_root, memory_root=None):
        self.vault_root = Path(vault_root)
        self.memory_root = Path(memory_root) if memory_root else self.vault_root
        self._counts: Counter = Counter()
        for dirpath, dirnames, filenames in os.walk(self.vault_root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if name.endswith(".md") and not name.startswith("."):
                    self._counts[stem_key(name[:-3])] += 1

    @classmethod
    def for_memory_root(cls, memory_root) -> "StemIndex":
        import idea_cards  # function-local: keeps this module's import graph flat
        return cls(idea_cards.vault_root(memory_root), memory_root)

    def unique(self, stem: str) -> bool:
        """No other note shares the name. A note not on disk yet counts as
        unique while nothing else carries its name."""
        return self._counts[stem_key(stem)] <= 1

    def vault_rel(self, rel) -> str:
        """`rel` as a vault-relative path; see the module's `vault_rel`."""
        return _vault_rel(rel, self.memory_root, self.vault_root)

    def link(self, rel, label: "str | None" = None) -> str:
        """The wikilink to the note at `rel`: `[[stem]]` when its name is the
        vault's only one and the label is the name, `[[stem|label]]` when
        unique under another label, else `[[path|label]]`."""
        vrel = self.vault_rel(rel)
        target = vrel[:-3] if vrel.endswith(".md") else vrel
        stem = target.rsplit("/", 1)[-1]
        label = (label or stem).translate(_LABEL_TABLE).strip()
        if self.unique(stem):
            return f"[[{stem}]]" if label == stem else f"[[{stem}|{label}]]"
        return f"[[{target}|{label}]]"


def _vault_rel(rel, memory_root: Path, vault_root: Path) -> str:
    """`rel` read from the memory root, else from the vault root (an idea
    card's path is the vault root's), as a vault-relative path. A path on
    neither is read from the memory root."""
    rel = str(rel).replace("\\", "/")
    for base in (memory_root, vault_root):
        p = base / rel
        if p.is_file():
            try:
                return p.relative_to(vault_root).as_posix()
            except ValueError:
                continue
    try:
        return (memory_root / rel).relative_to(vault_root).as_posix()
    except ValueError:
        return rel


def vault_rel(rel, memory_root) -> str:
    """`rel`, a memory-root or vault-root path, as a vault-relative path."""
    import idea_cards  # function-local: keeps this module's import graph flat
    memory_root = Path(memory_root)
    return _vault_rel(rel, memory_root, Path(idea_cards.vault_root(memory_root)))


def link_value_for(values, memory_root) -> str:
    """A link-valued key's value naming `values` (task 193 LD-5): each a
    memory-root or vault-root path, written as a quoted wikilink by its vault
    path; a value already a wikilink is kept as it is. "" for none."""
    import note_links  # function-local: keeps this module's import graph flat
    if isinstance(values, str):
        values = [values]
    links = []
    for v in values or []:
        v = str(v).strip().strip("\"'")
        if not v:
            continue
        links.append(v if "[[" in v else note_links.path_link(vault_rel(v, memory_root)))
    return note_links.link_value(*links)
