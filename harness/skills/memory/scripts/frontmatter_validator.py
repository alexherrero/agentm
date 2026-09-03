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
import os
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

# Filing-v2 2b: the vault-root `Projects/` generation is a SIBLING of the memory
# root; when the scope names the project space, the root sibling joins the walk
# (union across the merge window). Root-space entries are keyed relative to
# the vault root.
_ROOT_PROJECTS_DIRNAME = "Projects"


def _root_projects_dir(vault):
    """The vault-root `Projects/` space, discovered never conjured (filing-v2
    2b). Flat layout: `<memory-root>/Projects`. Nested layout — the memory
    root sits inside an Obsidian vault, witnessed by `.obsidian/` at the
    parent and none at the memory root itself: the sibling
    `<vault-root>/Projects`. A memory root at the top of its own vault has no
    sibling, whatever directory named `Projects` sits beside it (its parent
    is the operator's home or a sync folder, where one is common and is not
    the vault's). None when no root space exists. Both rungs match the
    directory's exact case."""
    vault = Path(vault)
    flat = vault / "Projects"
    if _is_dir_exact(flat):
        return flat
    parent = vault.parent
    if (parent / ".obsidian").is_dir() and not (vault / ".obsidian").is_dir():
        sibling = parent / "Projects"
        if _is_dir_exact(sibling):
            return sibling
    return None


def _is_dir_exact(path):
    """`path` is a directory whose name matches exactly — on a case-insensitive
    filesystem `Projects/` would otherwise answer for the V4-era `projects/`."""
    try:
        return path.is_dir() and any(p.name == path.name for p in path.parent.iterdir())
    except OSError:
        return False


def _scope_roots(vault: Path, scope_dirs) -> list:
    out = [vault / d for d in scope_dirs]
    if "desk/projects" in scope_dirs:
        root_space = _root_projects_dir(vault)
        if root_space is not None and root_space not in out:
            out.append(root_space)
    return [r for r in out if _is_dir_exact(r)]


def _vault_rel(path: Path, vault: Path) -> str:
    try:
        rel = path.relative_to(vault)
    except ValueError:
        rel = path.relative_to(vault.parent)
    return str(rel).replace("\\", "/")

# The lifecycle, and the ranking axis. Both enum-locked: a status or an altitude
# nothing recognizes is a note no pass can reason about, and a validator that
# waved either through would be leaving the taxonomy's brake off on two more
# fields.
STATUSES = ("unfiled", "active", "superseded", "expired")
ALTITUDES = ("artifact", "canonical")

# The contract every memory carries, from the filing design's own frontmatter
# block. This replaces `kind_registry.REQUIRED_UNIVERSAL_FIELDS`, which describes
# what `save.py` happened to emit rather than what the design requires — and the
# difference is not cosmetic: the daemon's capture path writes `captured` and no
# `group` at all, so the legacy set failed every note the new writer produced.
#
# Dropped from the legacy set, deliberately. `group` was a directory pointer, and
# the layout it pointed into is the thing this rescope replaces — class is a
# directory and everything else is frontmatter, so a group field is a second,
# staler answer to a question the path already answers. `tags` is dropped from
# *required* (it stays validated when present) because an untagged capture is a
# real and ordinary thing, and a validator that refuses one pushes the writer
# into emitting `tags: []` to satisfy a check rather than to say anything.
#
# The vocabulary field is not listed here: it is required conditionally, since an
# `unfiled` note legitimately has neither `type` nor `kind` until filing runs.
REQUIRED_CONTRACT_FIELDS = ("status", "captured", "updated", "slug")

# Legacy spellings still accepted, because the corpus is mid-migration and a
# validator that reports every unmigrated note is a validator nobody reads.
LEGACY_EQUIVALENTS = {"captured": "created"}

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


def _is_contract_exempt(path: Path, vault: Path | None = None) -> bool:
    """Whether this note lives in a space the contract does not govern.

    A space is a *top-level* directory, so the check needs the path as the vault
    sees it. Without a vault to relativize against there is no top level, and the
    first version of this got that wrong in a way a test caught: it tested every
    path segment, which made `Agent/desk/projects/x/personal/notes.md` exempt
    because one of its directories happened to be called `personal`. A space is a
    space, not a word.

    Falls back to `$MEMORY_VAULT_PATH` and its parent — the split layout keeps
    memory under `<vault>/Agent/` and the operator's spaces beside it, so the
    exempt space is a sibling of the memory root rather than inside it.

    Returns False when no vault can be determined. That direction is deliberate:
    an unrecognized path gets validated, and a false finding is cheaper than a
    file that silently stops being checked.
    """
    try:
        exempt = storage_rules.rules().contract_exempt_spaces()
    except storage_rules.StorageRulesError:
        return False
    if not exempt:
        return False

    candidates = []
    if vault is not None:
        candidates.append(Path(vault))
    env = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if env:
        candidates.append(Path(env))
        candidates.append(Path(env).parent)

    resolved = Path(path).resolve()
    for root in candidates:
        try:
            rel = resolved.relative_to(Path(root).resolve())
        except (ValueError, OSError):
            continue
        if storage_rules.in_space(rel.as_posix(), exempt):
            return True
    return False


def validate(note_path: Path | str, *, vault: Path | str | None = None) -> list[str]:
    """Check one note's frontmatter. Returns a list of violation strings
    (empty = clean). Never writes to `note_path`."""
    path = Path(note_path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"unreadable: {exc}"]

    # Contract exemption, checked before anything else and stated rather than
    # inherited from a scope list.
    #
    # `vault` is passed by validate_vault, which knows the root. A caller that
    # does not know it falls back to $MEMORY_VAULT_PATH; a path under no known
    # vault is validated rather than skipped.
    #
    # This was already true by accident: the scope dirs sit under the memory root
    # and `Personal/` sits outside it, so nothing walked it. That is a fragile
    # kind of correct — widen a scope and 385 documents become findings with no
    # rule saying they should not. The rule now exists.
    #
    # Note the design's own wording is wrong here in a way worth recording: it
    # says these files "carry no frontmatter". Every one of them has frontmatter,
    # just of its own shape — `title`, `created`, `updated`, and nothing the
    # memory contract asks for. A rule written against "no frontmatter" would
    # have matched none of them.
    if _is_contract_exempt(path, vault):
        return []

    fm = _parse_frontmatter(text)
    if fm is None:
        return ["no frontmatter block found"]

    # Filing-v2 part 3: a supplement of the accumulate loop keeps its bespoke
    # shape wherever it lives — the lanes sit under `memory/crystallized/`
    # now — so the exemption follows the kind, not the directory.
    if str(fm.get("kind") or "").strip() == "opinion-supplement":
        return []

    violations: list[str] = []
    has_type = bool(str(fm.get("type") or "").strip())
    status = str(fm.get("status") or "").strip()

    for field_name in REQUIRED_CONTRACT_FIELDS:
        if field_name in fm:
            continue
        # Two fields have a legacy spelling that is still all over the corpus.
        # Accepting either is what lets the validator run over a half-migrated
        # vault without reporting every unmigrated note as broken.
        if field_name in LEGACY_EQUIVALENTS and LEGACY_EQUIVALENTS[field_name] in fm:
            continue
        violations.append(f"missing required field `{field_name}`")

    # The vocabulary is required — except while the note is `unfiled`, which is
    # exactly the state a capture lands in before anything has judged it. Filing
    # assigns the type, and the capture transaction never waits on a model, so a
    # freshly captured note legitimately has neither field yet.
    if not has_type and "kind" not in fm and status != "unfiled":
        violations.append(
            "no `type` or `kind` — a memory carries a `type` and a record carries "
            "a `kind`; only an `unfiled` note, which nothing has judged yet, may "
            "carry neither"
        )

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

    # The lifecycle is enum-locked for the same reason the taxonomy is: a status
    # nothing recognizes is a note no pass can reason about.
    if status and status not in STATUSES:
        violations.append(
            f"status {status!r} is not one of: {', '.join(STATUSES)}"
        )

    # Altitude is the axis ranking dampens on, and it is enum-locked rather than
    # free text for the same reason `type` is: a value nothing recognizes ranks
    # as nothing. Absent is legal — the default applies — but present-and-wrong
    # is not.
    altitude = str(fm.get("altitude") or "").strip()
    if altitude and altitude not in ALTITUDES:
        violations.append(
            f"altitude {altitude!r} is not one of: {', '.join(ALTITUDES)}"
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
    for root in _scope_roots(vault, scope_dirs):
        for md in sorted(root.rglob("*.md")):
            if any(p in _EXCLUDE_DIRS for p in md.parts):
                continue
            if md.name.startswith("PLAN.archive."):
                continue
            violations = validate(md, vault=vault)
            if violations:
                rel = _vault_rel(md, vault)
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
