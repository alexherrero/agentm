#!/usr/bin/env python3
"""Gate: every `governs:`/`area:` stamp in wiki/designs/ resolves cleanly (R0.10).

Two checks, both driven by `governs_resolver.build_index()` (the same index
`governs_resolver.resolve_governing_design()` uses at runtime):

  1. **Overlap / multi-owner** — the same exact `governs:` pattern string
     stamped by two or more distinct designs. `governs_resolver` already
     fail-loud-refuses to guess in this case (`{"governed": false, "reason":
     "overlap"}`) — this gate catches the authoring mistake at commit time
     instead of waiting for a grounding hook to silently treat the file as
     greenfield (agTrack#0's exact failure mode).

  2. **Unknown area** — a design's `area:` value isn't in the canonical AG
     taxonomy (`_harness/designs/architecture-governance/area-taxonomy.md`
     in the vault; mirrored here since gates must run without vault access).

Exit:
  0  every governs: stamp resolves cleanly; every area: is known
  1  overlap and/or unknown-area violations found (listed on stderr)
  2  setup error (wiki/designs/ missing)

Usage: python3 scripts/check-governs-index.py [--root DIR]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import governs_resolver  # noqa: E402

# The canonical AG area vocabulary (area-taxonomy.md in the vault — mirrored
# here so this gate runs without vault access; see governs_resolver.py's
# module docstring / wiki/reference/Design-Governance.md for the same list).
_KNOWN_AREAS = frozenset({
    "shared/foundations",
    "agentm/architecture", "agentm/memory", "agentm/memory-index",
    "agentm/storage", "agentm/experience", "agentm/opinions",
    "agentm/opinion-registry", "agentm/personas", "agentm/model-effort-routing",
    "agentm/runner",
    "agentm/capability-resolution", "agentm/phase-contract", "agentm/mcp",
    "agentm/vault-taxonomy",
    "crickets/architecture", "crickets/build-system", "crickets/composition",
    "crickets/development-lifecycle", "crickets/code-review", "crickets/design",
    "crickets/developer-safety", "crickets/wiki", "crickets/github-projects",
    "crickets/maintenance", "crickets/conventions", "crickets/obsidian-vault",
    "crickets/token-audit", "crickets/privacy", "crickets/research",
    "crickets/diagnostics", "crickets/reporting",
    "governance",
})


def find_overlaps(entries: list) -> dict[str, set[str]]:
    """Map pattern -> the set of designs that stamped it, for patterns
    stamped by more than one design (non-empty patterns only — an empty
    pattern marks an area-only design and never collides)."""
    by_pattern: dict[str, set[str]] = {}
    for e in entries:
        if not e.pattern:
            continue
        by_pattern.setdefault(e.pattern, set()).add(e.design)
    return {p: designs for p, designs in by_pattern.items() if len(designs) > 1}


def find_unknown_areas(entries: list) -> dict[str, str]:
    """Map design -> its unrecognized area:, for designs whose area: isn't
    in the canonical vocabulary (one entry per design, deduplicated)."""
    unknown: dict[str, str] = {}
    for e in entries:
        if e.area and e.area not in _KNOWN_AREAS:
            unknown[e.design] = e.area
    return unknown


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="check-governs-index.py")
    ap.add_argument("--root", default=None, help="repo root (default: this script's repo)")
    ns = ap.parse_args(argv)

    root = Path(ns.root) if ns.root else None
    designs_dir = governs_resolver._repo_root(root) / "wiki" / "designs"
    if not designs_dir.is_dir():
        print(f"check-governs-index: missing {designs_dir}", file=sys.stderr)
        return 2

    entries = governs_resolver.build_index(root)
    overlaps = find_overlaps(entries)
    unknown_areas = find_unknown_areas(entries)

    if not overlaps and not unknown_areas:
        print(f"check-governs-index: clean ({len(entries)} governs:/area: entries checked)")
        return 0

    if overlaps:
        print("check-governs-index: OVERLAP — the same governs: pattern is stamped by multiple designs", file=sys.stderr)
        for pattern, designs in sorted(overlaps.items()):
            print(f"  {pattern!r}: {', '.join(sorted(designs))}", file=sys.stderr)
    if unknown_areas:
        print("check-governs-index: UNKNOWN AREA — area: not in the canonical taxonomy", file=sys.stderr)
        for design, area in sorted(unknown_areas.items()):
            print(f"  {design}: area: {area!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
