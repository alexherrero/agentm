#!/usr/bin/env python3
# arc_registry.py — the arc-as-metadata convention's registry (2026-07-18).
#
# `arc:` is a frontmatter field a permanent decisions/designs entry carries to
# name the temporal wave of work it belongs to (a V5/V6/V7/V8 roadmap wave, the
# architecture-governance track, a lettered AG build wave, consolidation-review,
# …). This module documents the *recognized* set of arc slugs — mirroring
# kind_registry.py's shape exactly (a frozenset + is_kebab/is_known/audit, no
# canonicalization of near-duplicates) — so `vault_lint.py` can reject an
# `arc:` value outside it the same way it already rejects an unrecognized
# `kind:`.
#
# Seeded 2026-07-18 from real vault evidence, three sources:
#   (a) every top-level folder name under `_harness/designs/` and
#       `_harness/archive/designs/` in both agentm's and crickets' vault
#       projects — a folder already has arc identity, by construction;
#   (b) the coarse roadmap-wave labels a version-numbered decisions/designs
#       tag (e.g. `v6-1`, `v6-19`) coarsens to — `v4` through `v8`, matching
#       `ROADMAP-AgentMemoryV{4..8}.md`; `friday` is both a coarse wave label
#       and its own `_harness/designs/friday/` folder;
#   (c) the lettered architecture-governance build waves (`wave-a` … `wave-e`),
#       cross-repo, evidenced by recurring `PLAN.archive.*-wave-<letter>-*`
#       filenames in both repos with no design folder of their own.
#
# This module never mutates a vault note. `audit()` is read-only.

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_KEBAB_SEGMENT = re.compile(r"^[a-z0-9-]+$")

# Recognized arc slugs. See module docstring for provenance; extend this set
# by adding a slug here, not by an entry inventing one no index will collect.
KNOWN_ARCS: frozenset[str] = frozenset({
    # (a) real _harness/designs/ folder names (agentm).
    "architecture-governance", "consolidation-review", "friday",
    "friday-inputs", "post-ag-frontload", "roadmap-finish",
    "roadmap-research-2026-06", "seven-section-convergence",
    "token-efficiency-46", "v5-1-storage-seam", "v5-5-orchestration-split",
    "v5-9-mcp-server", "v5-10-coordinator-team", "v5-11-pm-chief-of-staff",
    "v6-25-external-thinking-audit", "v8-proving", "vault-backing",
    # (a) real _harness/archive/designs/ folder names (crickets — already
    # closed arcs, moved ahead of this convention being named).
    "crickets-v3-native-plugins", "developer-plugin-suite",
    "developer-workflows-autonomy", "efficiency-automation", "wiki-composer",
    "wiki-maintenance", "wiki-maintenance-provisioning",
    "wiki-section-taxonomy", "worktree-pr-loop",
    # (b) coarse roadmap-wave labels (ROADMAP-AgentMemoryV{N}.md).
    "v3", "v4", "v5", "v6", "v7", "v8",
    # (c) lettered AG build waves — cross-repo, no design folder of their own.
    "wave-a", "wave-b", "wave-c", "wave-d", "wave-e",
    # (d) added 2026-07-18 during the UNMATCHED backfill pass — clusters found
    # by date-range + thematic grouping in the real vault, none with their own
    # _harness/designs/ folder (their build history is flat PLAN.archive.*
    # files, not a design folder). `v3` above (agentm's own early wave,
    # predating v4) belongs to this same backfill pass.
    "worktree-native-flow", "observability", "ci-walltime-diet",
    "crickets-v3",
})


def is_kebab(value: str) -> bool:
    """True iff `value` matches save.py's own kebab-case contract."""
    return bool(_KEBAB_SEGMENT.match(value))


def known_arcs() -> frozenset[str]:
    """The recognized arc-slug set. See module docstring for provenance."""
    return KNOWN_ARCS


def is_known(arc: str) -> bool:
    """True iff `arc` is in the recognized set (exact match, case-sensitive —
    the registry does not normalize case; a differently-cased duplicate is a
    distinct, unrecognized value by design)."""
    return arc in KNOWN_ARCS


def _frontmatter_value(content: str, field: str) -> str | None:
    """Extract a raw `<field>:` value from a note's frontmatter, or None if
    absent/malformed-enough that no value can be extracted at all. Returns the
    raw string exactly as written — audit() classifies malformed values, it
    does not repair them."""
    if not content.startswith("---\n"):
        return None
    end = content.find("\n---\n", 4)
    if end == -1:
        return None
    prefix = f"{field}:"
    for line in content[4:end].split("\n"):
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


# Mirrors kind_registry.py's walk roots exactly (agentm-memory-index.md's
# build-from-source path) — `arc:` only ever appears on a permanent
# decisions/designs entry, but the walk itself is the same corpus.
_WALK_SUBDIRS = ("personal", "projects", "_idea-incubator")


def audit(vault_path: Path | str) -> dict:
    """Read-only scan of the vault's `arc:` values. Never writes anything.

    Returns a dict: {"by_arc": {arc: count}, "malformed": [(path, raw_arc)],
    "unrecognized": [(path, raw_arc)], "total_stamped": int}. "malformed" is a
    raw arc value that fails is_kebab(); "unrecognized" is valid kebab-case but
    not in KNOWN_ARCS. A file with no `arc:` field at all is simply not
    counted — most entries carry no arc stamp, and that's expected, not a
    finding.
    """
    vault = Path(vault_path)
    by_arc: dict[str, int] = {}
    malformed: list[tuple[str, str]] = []
    unrecognized: list[tuple[str, str]] = []
    total_stamped = 0

    if not vault.is_dir():
        return {"by_arc": {}, "malformed": [], "unrecognized": [], "total_stamped": 0}

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
            raw_arc = _frontmatter_value(content, "arc")
            if raw_arc is None:
                continue
            total_stamped += 1
            rel = str(md.relative_to(vault)).replace("\\", "/")
            if not is_kebab(raw_arc):
                malformed.append((rel, raw_arc))
                continue
            by_arc[raw_arc] = by_arc.get(raw_arc, 0) + 1
            if not is_known(raw_arc):
                unrecognized.append((rel, raw_arc))

    return {
        "by_arc": by_arc,
        "malformed": malformed,
        "unrecognized": unrecognized,
        "total_stamped": total_stamped,
    }


def _print_report(result: dict) -> None:
    print(f"total entries with an arc: stamp: {result['total_stamped']}")
    print(f"distinct known arcs found: {len(result['by_arc'])}")
    for arc, count in sorted(result["by_arc"].items(), key=lambda kv: -kv[1]):
        print(f"  {count:5d}  {arc}")
    if result["unrecognized"]:
        print(f"\nunrecognized (valid kebab-case, not in KNOWN_ARCS): {len(result['unrecognized'])}")
        for path, arc in result["unrecognized"]:
            print(f"  {path}: arc={arc!r}")
    if result["malformed"]:
        print(f"\nmalformed (not valid kebab-case): {len(result['malformed'])}")
        for path, arc in result["malformed"]:
            print(f"  {path}: arc={arc!r}")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="arc-as-metadata registry")
    sub = parser.add_subparsers(dest="command", required=True)
    audit_p = sub.add_parser("audit", help="read-only scan of a vault's arc: values")
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
