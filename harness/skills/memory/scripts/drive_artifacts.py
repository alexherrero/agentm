#!/usr/bin/env python3
"""Sync-layer files that are not content.

Google Drive writes a file named ``Icon`` followed by a carriage return into
every folder it mirrors, and Finder leaves ``.DS_Store`` beside it. Neither is a
memory, neither was written by anything in this system, and neither should be
visible to anything that reads the vault — but both are files, so a directory
holding nothing else is not empty to ``iterdir()``, and the empty-directory
cleanup walks away from it.

That is not hypothetical. The 2026-09-06 layout survey found 310 of them, one
per folder, and the corpus migration's cleanup had left ``desk/{briefs,
diagnostics,projects,scratch,tasks}`` standing because each still "held" its
icon file. The count is zero today — the vault's Drive mirror was rebuilt on a
different account on 2026-09-07 — which makes this a latent hole rather than a
live one. The mirror is live again, so they come back.

The rule lived as ``p.name.startswith("Icon")`` copied into ten call sites, one
at a time, each added by whoever next tripped over it. A walker written next
week does not inherit a copied idiom, which is the actual defect this module
fixes: one predicate, imported, and a gate that fails on a fresh copy of the
literal.

``startswith`` was also wrong in a small way. It skips ``Iconography.md`` — a
note somebody could legitimately write — because it happens to share a prefix
with a sync artifact. The predicate here matches the artifacts and nothing else.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Iterator

# The names the sync layers write, exactly.
#
# The carriage return is spelled as an escape rather than typed, deliberately:
# editors, heredocs and clipboards all quietly normalize a real CR, and a
# literal that arrives here as "Icon\n" or "Icon" would match nothing while
# looking correct.
ICON_NAME = "Icon\r"

#: Every name treated as absent. ``Icon`` without its carriage return is
#: included because some tools create and copy it that way, and a bare ``Icon``
#: file is a sync artifact under either spelling.
ARTIFACT_NAMES = frozenset({ICON_NAME, "Icon", ".DS_Store"})


def is_artifact(name: "str | Path") -> bool:
    """Whether ``name`` is a sync-layer artifact rather than content.

    Accepts a bare name or a path; only the final component is examined.
    """
    return (name.name if isinstance(name, Path) else str(name)) in ARTIFACT_NAMES


def visible(paths: Iterable[Path]) -> Iterator[Path]:
    """``paths`` with the sync artifacts dropped.

    The shape most call sites want: wrap an ``iterdir()`` and the artifacts
    stop being entries.
    """
    return (p for p in paths if not is_artifact(p))


def is_empty(directory: Path) -> bool:
    """Whether ``directory`` holds nothing but sync artifacts.

    A missing directory is empty — the question every caller is really asking
    is "is there anything here to keep", and "no, it isn't even there" is a
    yes. A path that is not a directory is not empty: it is a file, and
    deleting the thing you were only measuring would be the worst possible
    reading of this function.
    """
    if not directory.exists():
        return True
    if not directory.is_dir():
        return False
    return next(visible(directory.iterdir()), None) is None


def remove_artifacts(directory: Path) -> int:
    """Delete the sync artifacts directly inside ``directory``; return how many.

    Only for a caller that has already decided the directory itself is going.
    Removing an artifact from a directory that stays is pointless — the sync
    layer writes it back — and would churn the mirror for nothing.
    """
    removed = 0
    if not directory.is_dir():
        return 0
    for child in directory.iterdir():
        if child.is_file() and is_artifact(child):
            try:
                child.unlink()
            except OSError:
                continue
            removed += 1
    return removed
