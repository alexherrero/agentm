#!/usr/bin/env python3
"""kind_registry.py — the note-vocabulary registry, now read from the rules file.

This module used to *be* the registry: a hardcoded frozenset of every value the
corpus had accumulated, extended by hand each time an audit found a new one. It
reached fifty-odd values that way, because every single addition was individually
defensible and nothing ever asked whether the set still cohered.

The registry now lives in `standards/storage-rules.md`, which the operator owns
and the filing passes read at runtime. This module is the adapter: same public
surface its four callers already use — `is_kebab`, `is_known`, `known_kinds`,
`REQUIRED_UNIVERSAL_FIELDS`, `audit` — resolved against the rules block instead
of against a list in this file. A value added to the rules file is recognized
here on the next call, with no code edit in between; a value removed from it is
recognized nowhere.

Two registers back the vocabulary, and the distinction is the point:

  `memory_types`   the six values a *memory* carries in its `type:` field. These
                   assert something — a preference, a convention, a fact, a
                   recipe, a fix, an idea — and a query can usefully rank by
                   them. Growth is braked: a type is added when a query class
                   needs to rank by it, and not otherwise.

  `record_kinds`   the shapes a *record* carries in its `kind:` field. These
                   record what happened — a nightly brief, a telemetry row, a
                   session trace, an index page. They are not memories, so they
                   carry no `type` at all.

`audit()` reports a third bucket the old version had no name for: **retired**.
A value in the rules file's deprecation map is one the collapse has a
replacement for and has not reached yet. Reporting it as "unrecognized" would
have made a running migration look like a taxonomy failure.

This module never mutates a vault note. `audit()` is read-only.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import storage_rules  # noqa: E402

_KEBAB_SEGMENT = re.compile(r"^[a-z0-9-]+$")

# Universal frontmatter fields save.py requires on every entry, per save.py's own
# REQUIRED_FRONTMATTER_FIELDS (FRONTMATTER_FIELD_ORDER minus the optional set).
# Kept as a tuple, not re-imported, so this module has no import-time dependency
# on save.py (mirrors graph.py's standalone-module convention in this dir).
#
# `kind` names the field as the corpus has always spelled it. A note that has
# been through the collapse carries `type` instead, and `note_kind()` below reads
# either — which is what lets the two field names coexist while the migration
# runs without any caller learning about both.
REQUIRED_UNIVERSAL_FIELDS: tuple = (
    "kind", "status", "created", "updated", "tags", "group", "slug",
)

# The audit walks the corpus: every note under the vault root, which is where
# the daemon's index looks too. It used to walk a hand-kept list — `memory/`,
# `desk/projects/` and `_idea-incubator/` under the memory root, plus a
# `projects/` sibling found through an `.obsidian/` witness — and each move of
# the vault left the list further behind. By 2026-09-20 two of its three names
# existed nowhere, and `standards/`, `calendar/`, `personal/` and the root
# notes sat outside it. A walk that names no directory cannot fall behind one.
# It skips what the index skips, plus the archives this audit never counted:
#
#   dot-directories   `.obsidian/`, `.trash/`, `.git/`: never notes
#   the recall wall   the contract's `recall_exempt_areas`, never entered, so
#                     a file behind it is never opened
#   `_archive/`       at any depth
#   `PLAN.archive.*`  completed plans
#
# `graph_snapshot.py` keeps a hand-listed walk of its own, and deliberately so;
# its `_walk_vault_paths` says why.
_ARCHIVE_DIRNAME = "_archive"
_PLAN_ARCHIVE_PREFIX = "PLAN.archive."

# How a scope count names the notes directly in the corpus root, which belong
# to no directory: the vault's own `index.md` and `Ideas.md`.
TOP_LEVEL = "(top level)"


def corpus_root(root, vault_root=None) -> Path:
    """The directory the audit walks: the vault root, never the memory root.

    `vault_root` is the caller's answer, and the vocabulary gate gives one
    whenever the export or the kernel config resolves it. Without it, the vault
    root is found the way this module used to find the `projects/` sibling:
    the memory root's parent when an Obsidian vault is witnessed there
    (`.obsidian/` at the parent and none at the root), else the root itself,
    a flat vault being both roots at once. A root at the top of its own vault
    has no parent space, whatever sits beside it: its parent is the
    operator's home or a sync folder."""
    if vault_root is not None:
        return Path(vault_root)
    root = Path(root)
    parent = root.parent
    if (parent / ".obsidian").is_dir() and not (root / ".obsidian").is_dir():
        return parent
    return root


def _walked(name: str, rel: str) -> bool:
    """Whether the walk enters a directory, by its name and its path from the
    corpus root."""
    if name.startswith(".") or name == _ARCHIVE_DIRNAME:
        return False
    return not storage_rules.is_recall_exempt(rel)


def _is_note(name: str, rel: str) -> bool:
    return (name.endswith(".md") and not name.startswith((".", _PLAN_ARCHIVE_PREFIX))
            and not storage_rules.is_recall_exempt(rel))


def _walk_roots(vault, vault_root=None) -> list:
    """The top-level directories of the corpus the audit walks, in order."""
    corpus = corpus_root(vault, vault_root)
    try:
        children = sorted(corpus.iterdir())
    except OSError:
        return []
    return [c for c in children if c.is_dir() and _walked(c.name, c.name)]


def corpus_notes(vault, vault_root=None) -> list:
    """Every note the audit reads, as `(path, key)` pairs sorted by key.

    The key is the note's path from the corpus root, in POSIX form: the
    spelling the daemon keys its rows on and the contract names its areas in.
    That is `agent/memory/…` on the shipped layout and `memory/…` on a flat
    one."""
    corpus = corpus_root(vault, vault_root)
    try:
        notes = [(p, p.name) for p in corpus.iterdir()
                 if p.is_file() and _is_note(p.name, p.name)]
    except OSError:
        return []
    for top in _walk_roots(vault, vault_root):
        for dirpath, dirnames, filenames in os.walk(top):
            here = Path(dirpath)
            rel_dir = here.relative_to(corpus).as_posix()
            dirnames[:] = [d for d in dirnames if _walked(d, f"{rel_dir}/{d}")]
            notes.extend((here / name, f"{rel_dir}/{name}") for name in filenames
                         if _is_note(name, f"{rel_dir}/{name}"))
    notes.sort(key=lambda pair: pair[1])
    return notes


def scope_line(spaces: dict) -> str:
    """The top-level directories a walk read, with the notes in each."""
    named = sorted(s for s in spaces if s != TOP_LEVEL)
    parts = [f"{s} {spaces[s]}" for s in named]
    if TOP_LEVEL in spaces:
        parts.append(f"{TOP_LEVEL} {spaces[TOP_LEVEL]}")
    return "scope: " + (", ".join(parts) or "no notes")


def __getattr__(name: str):
    """`KNOWN_KINDS` resolves lazily, against the rules file.

    Lazy rather than computed at import, so a broken rules file surfaces as the
    parse error it is — at the call that needed the vocabulary — rather than as
    an ImportError chain three modules deep.
    """
    if name == "KNOWN_KINDS":
        return storage_rules.known_values()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def is_kebab(value: str) -> bool:
    """True iff `value` matches save.py's own kebab-case contract."""
    return bool(_KEBAB_SEGMENT.match(value))


def known_kinds() -> frozenset:
    """Every value either register recognizes."""
    return storage_rules.known_values()


def is_known(kind: str) -> bool:
    """True iff `kind` is currently registered — a memory type or a record kind.

    Exact match, case-sensitive: the registry does not normalize case, so a
    differently-cased duplicate is a distinct, unrecognized value by design.
    """
    return kind in storage_rules.known_values()


def is_retired(kind: str) -> bool:
    """True iff `kind` is a value the collapse has a replacement for."""
    return kind in storage_rules.rules().deprecations()


def replacement_for(kind: str):
    """The value that replaces a retired one, or None if it is not retired."""
    return storage_rules.rules().resolve_deprecated(kind)


def _frontmatter(content: str) -> dict:
    """The note's frontmatter as raw `key: value` strings, or `{}`.

    Deliberately minimal — this module classifies one field and has no business
    parsing nested YAML. Mirrors `frontmatter_validator._parse_frontmatter`.
    """
    if not content.startswith("---\n"):
        return {}
    end = content.find("\n---", 4)
    if end == -1:
        return {}
    fields = {}
    for line in content[4:end].split("\n"):
        key, sep, value = line.partition(":")
        if sep and key and not key.startswith((" ", "\t", "#")):
            fields[key.strip()] = value.strip()
    return fields


def note_kind(content: str):
    """The raw vocabulary value from a note's frontmatter, or None.

    Returns the value exactly as written — `audit()` classifies malformed values,
    it does not repair them. Reads `type` in preference to `kind`, so a collapsed
    note and an uncollapsed one both answer.
    """
    try:
        return storage_rules.note_type(_frontmatter(content))
    except storage_rules.ContractViolation:
        # A note carrying both fields is a contract violation, and `audit()`
        # surfaces it as malformed rather than silently picking a side.
        return "<both type and kind>"


def audit(vault_path: Path | str, *, vault_root=None, spaces: dict | None = None) -> dict:
    """Read-only scan of the corpus's vocabulary. Never writes anything.

    `vault_path` is the memory root, as every caller has always passed it. The
    walk covers the vault root it sits in (`corpus_root`), which `vault_root`
    names outright when the caller has resolved it. `spaces`, when given, is
    filled with the notes read per top-level directory, counted in the same
    walk, so the scope a caller prints is the scope that was read.

    Returns `{"by_kind", "malformed", "unrecognized", "retired", "total_files"}`.
    `malformed` fails kebab-case; `retired` is a value the deprecation map has a
    replacement for; `unrecognized` is valid kebab-case, not registered, and not
    retired — the genuine "nobody knows what this is" bucket. A file with no
    extractable value at all is counted in `total_files` and omitted from every
    other bucket: missing-kind is `frontmatter_validator.py`'s question.
    """
    vault = Path(vault_path)
    by_kind: dict = {}
    malformed: list = []
    unrecognized: list = []
    retired: list = []
    total_files = 0

    if not vault.is_dir():
        return {"by_kind": {}, "malformed": [], "unrecognized": [], "retired": [],
                "total_files": 0}

    known = storage_rules.known_values()
    deprecations = storage_rules.rules().deprecations()

    for md, rel in corpus_notes(vault, vault_root):
        try:
            content = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        total_files += 1
        if spaces is not None:
            space = rel.split("/", 1)[0] if "/" in rel else TOP_LEVEL
            spaces[space] = spaces.get(space, 0) + 1
        raw = note_kind(content)
        if raw is None:
            continue
        if not is_kebab(raw):
            malformed.append((rel, raw))
            continue
        by_kind[raw] = by_kind.get(raw, 0) + 1
        if raw in deprecations:
            retired.append((rel, raw))
        elif raw not in known:
            unrecognized.append((rel, raw))

    return {
        "by_kind": by_kind,
        "malformed": malformed,
        "unrecognized": unrecognized,
        "retired": retired,
        "total_files": total_files,
    }


def print_report(result: dict, spaces: dict | None = None) -> None:
    print(f"total files scanned: {result['total_files']}")
    if spaces is not None:
        print(scope_line(spaces))
    print(f"distinct values found: {len(result['by_kind'])}")
    for kind, count in sorted(result["by_kind"].items(), key=lambda kv: -kv[1]):
        print(f"  {count:5d}  {kind}")
    if result["retired"]:
        counts: dict = {}
        for _path, kind in result["retired"]:
            counts[kind] = counts.get(kind, 0) + 1
        print(f"\nretired — the collapse has a replacement and has not reached these: "
              f"{len(result['retired'])} note(s)")
        for kind, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {count:5d}  {kind} → {replacement_for(kind)}")
    if result["unrecognized"]:
        print(f"\nunrecognized (valid kebab-case, registered nowhere, not retired): "
              f"{len(result['unrecognized'])}")
        for path, kind in result["unrecognized"]:
            print(f"  {path}: {kind!r}")
    if result["malformed"]:
        print(f"\nmalformed (not valid kebab-case): {len(result['malformed'])}")
        for path, kind in result["malformed"]:
            print(f"  {path}: {kind!r}")


def _parse_args(argv: list) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="the note-vocabulary registry")
    sub = parser.add_subparsers(dest="command", required=True)
    audit_p = sub.add_parser("audit", help="read-only scan of a vault's vocabulary")
    audit_p.add_argument("vault", help="the memory root; the walk covers the vault root it sits in")
    return parser.parse_args(argv)


def main(argv: list) -> int:
    args = _parse_args(argv)
    if args.command == "audit":
        spaces: dict = {}
        print_report(audit(args.vault, spaces=spaces), spaces)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
