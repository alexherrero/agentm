#!/usr/bin/env python3
"""check-opinion-honesty.py — no orphan Opinion references (agentm-opinion-registry.md
Enforcement §3).

Every consumer-declared Opinion name must resolve to a real `opinions/<name>.md`
entry — today that means a persona's `opinions:` manifest field, an
unambiguous, dedicated field (agentm-side; the persona-tier manifest axis —
itself `[PENDING-IMPL]` in agentm-persona-activation.md, so today this is
almost always zero personas, which is correct, not a bug — the honesty lint
stays quiet until there's something to say).

**Deliberately out of scope: capability/skill `requires:`/`enhances:` edges.**
The parent design describes a capability or skill binding to an Opinion
"through the composition `requires:`/`enhances:` edges" — the *same* fields
a plain capability dependency uses. An earlier version of this lint tried
to disambiguate an Opinion reference from a capability reference by value
(treat a `requires:`/`enhances:` entry as an Opinion reference iff it
matches a live catalog name) — that's structurally unable to ever catch an
*orphan* Opinion reference: an orphan is, by definition, not in the
catalog, so a catalog-membership filter applied at collection time would
never even flag it as an Opinion reference to check. Real disambiguation
needs either a dedicated field (e.g. `opinions:` on a skill/agent, not
`requires:`/`enhances:`) or a cross-check against the capability resolver
too (a value that resolves to neither a capability nor an opinion is a
*generic* broken reference, not specifically an orphan-opinion one) —
exactly the "consumer syntax co-evolves" risk the parent design already
names. Deferred until that grammar is locked, rather than ship a check that
cannot structurally do what its name claims.

Usage:
    python3 scripts/check-opinion-honesty.py [--root DIR]

Exit codes:
    0  clean — every declared Opinion reference resolves
    1  an orphan reference found
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import opinion_resolver as orz  # noqa: E402

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def _persona_opinion_refs(root: Path) -> list[tuple[str, str]]:
    """[(opinion_name, "personas/<file>")] for every persona's `opinions:` entry."""
    refs: list[tuple[str, str]] = []
    personas_dir = root / "personas"
    if not personas_dir.is_dir() or yaml is None:
        return refs
    for p in sorted(personas_dir.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, _ = orz._parse_frontmatter(text)
        for name in orz._as_list(fm.get("opinions")):
            refs.append((name, f"personas/{p.name}"))
    return refs


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    args = list(argv or sys.argv[1:])
    while args:
        if args[0] == "--root" and len(args) >= 2:
            root = Path(args[1]); args = args[2:]
        else:
            print(f"check-opinion-honesty: unknown arg: {args[0]}", file=sys.stderr)
            return 2

    index = orz.build_index(root)
    catalog = set(index)

    orphans: list[tuple[str, str]] = []
    for name, source in _persona_opinion_refs(root):
        if name not in catalog:
            orphans.append((name, source))

    if orphans:
        print("check-opinion-honesty: FAIL — orphan Opinion reference(s):", file=sys.stderr)
        for name, source in orphans:
            print(f"  - {source} declares opinion {name!r} — no opinions/{name}.md entry exists", file=sys.stderr)
        return 1

    print("check-opinion-honesty: clean — every declared Opinion reference resolves.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
