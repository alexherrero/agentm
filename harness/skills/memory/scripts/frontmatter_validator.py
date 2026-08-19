#!/usr/bin/env python3
# frontmatter_validator.py — V6-15 check-only frontmatter validator.
#
# Checks a note's frontmatter against the universal contract kind_registry.py
# already stages: kind is known/kebab-case, and the required universal fields
# (kind, status, created, updated, tags, group, slug) are present. This is a
# narrow slice of vault_lint.py's nine-check sweep (kind + the required-field
# trio only) — never a replacement for it. Read-only: never writes to any
# file it checks.

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import storage_rules  # noqa: E402
from kind_registry import is_kebab, is_known, REQUIRED_UNIVERSAL_FIELDS  # noqa: E402

# Same default scan scope as vault_lint.py's "all" _SCOPE_DIRS — deliberately
# excludes _idea-incubator, which carries a documented bespoke frontmatter
# shape (DC-4 exemption) this validator's universal-field check would
# otherwise false-positive against.
_DEFAULT_SCOPE_DIRS = ("memory", "desk/projects")

# Mirrors vault_lint.py's _EXCLUDE_DIRS exactly (DC-4): these subdirectories
# carry non-memory-entry content (harness state, dev-loop infra, staging
# areas, retired entries, opinion-supplement lanes) that was never meant to
# satisfy the universal frontmatter contract. Without this, e.g.
# projects/<repo>/_harness/PLAN.md (plain harness state, no frontmatter at
# all) or a personal/_opinions/ lane entry (bespoke shape — no
# `updated`/`tags`/`group`) floods every check-vault run with false
# violations. A deliberate standalone copy, not an import (same-dir
# convention); test_vault_lint.py's parity test pins it to vault_lint.py's.
_EXCLUDE_DIRS = frozenset(
    # NOTE: matched per path SEGMENT, so this holds the last component of the
    # scratch space ("scratch"), not its "desk/scratch" spelling — a
    # two-segment entry here silently matches nothing.
    {"_idea-incubator", "_meta", "_harness", "_inbox", "scratch", "_archive",
     "_opinions", "_crystallize-staging"}
)


def _parse_frontmatter(text: str) -> dict | None:
    """Minimal frontmatter extraction — key: raw-value pairs only, no nested
    structures. Returns None when no frontmatter block is present. Mirrors
    vault_lint.py's parse_frontmatter contract (stdlib-only, no PyYAML)."""
    if not text.startswith("---"):
        return None
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None
    fm: dict = {}
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        key = key.strip()
        if key:
            fm[key] = value.strip()
    return fm


def validate(note_path: Path | str) -> list[str]:
    """Check one note's frontmatter. Returns a list of violation strings
    (empty = clean). Never writes to `note_path`."""
    path = Path(note_path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"unreadable: {exc}"]

    fm = _parse_frontmatter(text)
    if fm is None:
        return ["no frontmatter block found"]

    violations: list[str] = []
    has_type = bool(str(fm.get("type") or "").strip())
    for field_name in REQUIRED_UNIVERSAL_FIELDS:
        # `type` and `kind` are the same slot under two names while the collapse
        # runs. A note that carries `type` has been through it; requiring `kind`
        # as well would fail exactly the notes the migration has fixed.
        if field_name == "kind" and has_type:
            continue
        if field_name not in fm:
            violations.append(f"missing required field `{field_name}`")

    # A note carries `type` or `kind`, never both: two fields that can disagree
    # about what a note is will eventually disagree.
    try:
        value = storage_rules.note_type(fm)
    except storage_rules.ContractViolation as exc:
        violations.append(str(exc))
        return violations

    if value is not None:
        if not is_kebab(value):
            violations.append(f"{'type' if has_type else 'kind'} {value!r} is not valid kebab-case")
        elif has_type and value not in storage_rules.memory_types():
            violations.append(
                f"type {value!r} is not one of the six memory types "
                f"({', '.join(sorted(storage_rules.memory_types()))})"
            )
        elif not has_type and not is_known(value):
            violations.append(f"kind {value!r} is not a recognized kind (unrecognized, not rejected)")

    # `type` names a memory, and a memory that has settled to `active` has been
    # through a filing judgment. Before that it may legitimately carry none:
    # enrichment assigns the type, and the capture transaction never waits on a
    # model to do it.
    status = str(fm.get("status") or "").strip()
    if status == "active" and value is None:
        violations.append(
            "an `active` memory carries a `type` — a memory reaches `active` by "
            "being filed, and filing is what assigns it"
        )

    return violations


def validate_vault(vault_path: Path | str, *, scope_dirs=_DEFAULT_SCOPE_DIRS) -> dict[str, list[str]]:
    """Check every note under `vault_path`'s scope dirs. Returns
    {rel_path: [violations]} for notes that have at least one violation —
    clean notes are omitted. Never writes anything."""
    vault = Path(vault_path)
    if not vault.is_dir():
        return {}

    results: dict[str, list[str]] = {}
    for scope_dir in scope_dirs:
        root = vault / scope_dir
        if not root.is_dir():
            continue
        for md in sorted(root.rglob("*.md")):
            if any(p in _EXCLUDE_DIRS for p in md.parts):
                continue
            if md.name.startswith("PLAN.archive."):
                continue
            violations = validate(md)
            if violations:
                rel = str(md.relative_to(vault)).replace("\\", "/")
                results[rel] = violations
    return results


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="V6-15 frontmatter validator (check-only)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", metavar="PATH", help="check a single note")
    group.add_argument("--check-vault", metavar="VAULT_PATH", help="check every note under the vault's default scope")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    if args.check:
        violations = validate(args.check)
        if not violations:
            print(f"{args.check}: clean")
            return 0
        print(f"{args.check}:")
        for v in violations:
            print(f"  - {v}")
        return 1
    else:
        results = validate_vault(args.check_vault)
        if not results:
            print("clean: no violations found")
            return 0
        for rel, violations in results.items():
            print(f"{rel}:")
            for v in violations:
                print(f"  - {v}")
        print(f"\n{len(results)} note(s) with violations")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
