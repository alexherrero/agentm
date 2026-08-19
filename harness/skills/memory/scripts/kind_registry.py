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

# Vault walk roots. Shared with `graph_snapshot.py`, which is the walk to keep
# this in step with.
_WALK_SUBDIRS = ("memory", "desk/projects", "_idea-incubator")


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


def audit(vault_path: Path | str) -> dict:
    """Read-only scan of the corpus's vocabulary. Never writes anything.

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

    walk_roots = [vault / d for d in _WALK_SUBDIRS if (vault / d).is_dir()]
    for root in walk_roots:
        for md in sorted(root.rglob("*.md")):
            if any(p == "_archive" for p in md.parts):
                continue
            if md.name.startswith("PLAN.archive."):
                continue
            try:
                content = md.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            total_files += 1
            raw = note_kind(content)
            if raw is None:
                continue
            rel = str(md.relative_to(vault)).replace("\\", "/")
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


def _print_report(result: dict) -> None:
    print(f"total files scanned: {result['total_files']}")
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
    audit_p.add_argument("vault", help="path to the vault root")
    return parser.parse_args(argv)


def main(argv: list) -> int:
    args = _parse_args(argv)
    if args.command == "audit":
        _print_report(audit(args.vault))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
