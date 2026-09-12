#!/usr/bin/env python3
"""Gate: no note in the class directories carries an empty tag list.

agentm-vault § The card: `tags` is never an empty list — a card with no tags
omits the field. The card backfill (agentm-vault plan 06) removed the empty
lists the corpus held, and every writer of a class card omits one. The gate
reads the live vault, resolved at runtime, over all six class directories and
fails on `tags: []`, `tags: ""` or a bare `tags:` with nothing under it.

Two states, like the card-shape gate: until the applied backfill writes
`memory/.card-backfill-complete` the gate reports the count and exits 0; once
the marker exists it enforces.

Usage:
  python3 scripts/check-no-empty-tags.py                 # the resolved memory root
  python3 scripts/check-no-empty-tags.py --memory-root DIR
Exit: 0 clean, pre-backfill, or nothing to check; 1 on a finding.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TOOLKIT = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for _p in (str(_HERE), str(_TOOLKIT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import card_shape as cs  # noqa: E402

CLASSES = ("semantic", "procedural", "episodic", "entities", "crystallized", "mocs")
IGNORABLE = {".DS_Store", "Icon\r", "Icon", ".gitkeep", cs.MARKER_NAME}


def empty_tags(text: str) -> bool:
    parsed = cs.split_note(text)
    if parsed is None:
        return False
    entries, _rest = parsed
    raw = cs.raw_value(entries, "tags")
    return raw is not None and not cs.has_continuation(entries, "tags") and not cs.flow_list(raw)


def check(memory_root: Path, out=sys.stdout) -> int:
    hits = []
    for cls in CLASSES:
        d = memory_root / "memory" / cls
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.name in IGNORABLE or p.is_dir() or p.suffix != ".md":
                continue
            if empty_tags(p.read_text(encoding="utf-8", errors="replace")):
                hits.append(p.relative_to(memory_root).as_posix())
    if not (memory_root / "memory" / cs.MARKER_NAME).exists():
        print(f"check-no-empty-tags: pre-backfill — {len(hits)} note(s) carry an empty tag list, which "
              f"the card backfill removes; enforced once memory/{cs.MARKER_NAME} exists", file=out)
        return 0
    if hits:
        print(f"check-no-empty-tags: {len(hits)} note(s) carry an empty tag list", file=out)
        for rel in hits:
            print(f"  {rel}", file=out)
        return 1
    print("check-no-empty-tags: clean — no empty tag list in the class directories", file=out)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--memory-root", default=None,
                    help="memory root to check (default: $MEMORY_ROOT, else the configured one)")
    args = ap.parse_args(argv)
    root = Path(args.memory_root) if args.memory_root else None
    if root is None:
        import vault_layout  # noqa: E402
        root = vault_layout.env_memory_root()
        if root is None:
            try:
                import harness_memory as hm  # noqa: E402
                root = hm.memory_root()
            except ImportError:
                root = None
    if root is None or not (Path(root) / "memory").is_dir():
        print("check-no-empty-tags: no memory root resolves; nothing to check")
        return 0
    return check(Path(root))


if __name__ == "__main__":
    raise SystemExit(main())
