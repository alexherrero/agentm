#!/usr/bin/env python3
# kind_registry.py — V6-15 typed-object schema-registry.
#
# The vault's `kind:` frontmatter field has always been free-form kebab-case
# (save.py's _validate_kebab enforces the *shape*, never a fixed set). This
# module documents the *recognized* set — every kind value shipped code
# actually references, plus every distinct value seeded from a real-vault
# frequency audit at authoring time (PLAN-v6-15-v6-18-typed-object-moc, 2026-07-10)
# — without collapsing near-duplicates or fixing malformed entries. That
# canonicalization is an explicit operator judgment call, parked as its own
# backlog item (agentm #273), not decided here.
#
# This module never mutates a vault note. `audit()` is read-only.

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_KEBAB_SEGMENT = re.compile(r"^[a-z0-9-]+$")

# Recognized kind values, seeded 2026-07-10 from:
#   (a) values shipped code actually writes or reads (failure-incident in
#       save.py, crystallized in crystallize.py/consolidate.py, session-cost
#       as a vestigial reserved value per agentm-memory-index.md);
#   (b) every distinct, validly-kebab-case value found in a frequency audit
#       of the real vault's personal/ + projects/ trees at that date.
# Near-duplicates (e.g. "convention" vs "conventions") are kept as SEPARATE
# entries deliberately — this registry documents what's recognized, it does
# not decide which spelling is canonical. See b-kind-taxonomy-canonicalization.
KNOWN_KINDS: frozenset[str] = frozenset({
    # Reserved values (agentm-memory-index.md, shipped code).
    "failure-incident", "session-cost", "crystallized",
    # Real-vault frequency audit (2026-07-10), validly-kebab entries only.
    "preferences", "preference", "workflow", "workflow-pattern", "idea",
    "fix", "research", "research-synthesis", "research-index",
    "convention", "conventions", "skill-watchlist", "skill-watchlist-entry",
    "skill", "domain-reference", "design", "design-call", "project-index",
    "project", "non-negotiable", "reference", "pattern", "handoff-artifact",
    "handoff-index", "session-handoff", "session-brief", "session-findings",
    "decision", "decision-summary", "telemetry", "snippet", "runbook",
    "roadmap-integration", "persona", "moc", "insight", "gap", "feedback",
    "evidence", "conversation", "archive",
    # _idea-incubator/ tree kinds — missed in the first personal/+projects/
    # seed grep, caught by this module's own first real-vault audit() run
    # (the walk correctly covers _idea-incubator/ per vec_index.py's
    # full_sync convention; the seed grep that authored this set did not).
    "idea-incubator", "idea-incubator-summary", "idea-incubator-research",
    "idea-incubator-runbook",
})

# Universal frontmatter fields save.py requires on every entry, per
# save.py's own REQUIRED_FRONTMATTER_FIELDS (FRONTMATTER_FIELD_ORDER minus
# the optional set). Kept as a tuple, not re-imported, so this module has no
# import-time dependency on save.py (mirrors graph.py's standalone-module
# convention in this scripts/ dir).
REQUIRED_UNIVERSAL_FIELDS: tuple[str, ...] = (
    "kind", "status", "created", "updated", "tags", "group", "slug",
)

# Vault walk roots + excludes, mirroring vec_index.py's full_sync walk
# exactly (agentm-memory-index.md's build-from-source path).
_WALK_SUBDIRS = ("personal", "projects", "_idea-incubator")


def is_kebab(value: str) -> bool:
    """True iff `value` matches save.py's own kebab-case contract."""
    return bool(_KEBAB_SEGMENT.match(value))


def known_kinds() -> frozenset[str]:
    """The recognized kind set. See module docstring for provenance."""
    return KNOWN_KINDS


def is_known(kind: str) -> bool:
    """True iff `kind` is in the recognized set (exact match, case-sensitive —
    the registry does not normalize case; a differently-cased duplicate is a
    distinct, unrecognized value by design)."""
    return kind in KNOWN_KINDS


def _frontmatter_kind(content: str) -> str | None:
    """Extract the raw `kind:` value from a note's frontmatter, or None if
    absent/malformed-enough that no value can be extracted at all. Returns
    the raw string exactly as written (not stripped of malformed suffixes) —
    audit() classifies malformed values, it does not repair them."""
    if not content.startswith("---\n"):
        return None
    end = content.find("\n---\n", 4)
    if end == -1:
        return None
    for line in content[4:end].split("\n"):
        if line.startswith("kind:"):
            return line[len("kind:"):].strip()
    return None


def audit(vault_path: Path | str) -> dict:
    """Read-only scan of the vault's kind: values. Never writes anything.

    Returns a dict: {"by_kind": {kind: count}, "malformed": [(path, raw_kind)],
    "unrecognized": [(path, raw_kind)], "total_files": int}. "malformed" is a
    raw kind value that fails is_kebab(); "unrecognized" is valid kebab-case
    but not in KNOWN_KINDS. A file with no extractable kind at all is counted
    in total_files but omitted from every other bucket (not this module's
    job to flag missing-kind — that's frontmatter_validator.py, task 2).
    """
    vault = Path(vault_path)
    by_kind: dict[str, int] = {}
    malformed: list[tuple[str, str]] = []
    unrecognized: list[tuple[str, str]] = []
    total_files = 0

    if not vault.is_dir():
        return {"by_kind": {}, "malformed": [], "unrecognized": [], "total_files": 0}

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
            raw_kind = _frontmatter_kind(content)
            if raw_kind is None:
                continue
            rel = str(md.relative_to(vault)).replace("\\", "/")
            if not is_kebab(raw_kind):
                malformed.append((rel, raw_kind))
                continue
            by_kind[raw_kind] = by_kind.get(raw_kind, 0) + 1
            if not is_known(raw_kind):
                unrecognized.append((rel, raw_kind))

    return {
        "by_kind": by_kind,
        "malformed": malformed,
        "unrecognized": unrecognized,
        "total_files": total_files,
    }


def _print_report(result: dict) -> None:
    print(f"total files scanned: {result['total_files']}")
    print(f"distinct known kinds found: {len(result['by_kind'])}")
    for kind, count in sorted(result["by_kind"].items(), key=lambda kv: -kv[1]):
        print(f"  {count:5d}  {kind}")
    if result["unrecognized"]:
        print(f"\nunrecognized (valid kebab-case, not in KNOWN_KINDS): {len(result['unrecognized'])}")
        for path, kind in result["unrecognized"]:
            print(f"  {path}: kind={kind!r}")
    if result["malformed"]:
        print(f"\nmalformed (not valid kebab-case): {len(result['malformed'])}")
        for path, kind in result["malformed"]:
            print(f"  {path}: kind={kind!r}")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="V6-15 kind-taxonomy registry")
    sub = parser.add_subparsers(dest="command", required=True)
    audit_p = sub.add_parser("audit", help="read-only scan of a vault's kind: values")
    audit_p.add_argument("vault", help="path to the vault root")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    if args.command == "audit":
        _print_report(audit(args.vault))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
