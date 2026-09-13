#!/usr/bin/env python3
"""Gate: a class directory holds only cards.

The six class directories under `memory/` hold memories (`type:`) and the
records the machinery files beside them (`kind:`), and nothing else
(agentm-vault plan 06). The gate fails on a directory inside a class directory,
a file that is not a note, a note without a frontmatter block, a note carrying
both `type` and `kind` or neither, and a value the filing contract does not
register. Drive's `Icon` files, `.DS_Store` and the card backfill's marker are
ignored.

It reads the live vault, resolved at runtime. Two states, like the other card
gates: until the applied card backfill writes `memory/.card-backfill-complete`
it reports its findings and exits 0 (the purge left six empty opinion-lane
directories inside `crystallized/`, which the backfill removes); once the marker
exists it enforces.

`mocs/` also holds the maps' shape (agentm-vault plan 07): the three named maps
(`moc-root`, `moc-memory`, `needs-review`), the class index, and a page per
memory type with at least `moc_min_members` live notes, never a numbered page.
Those findings have two states of their own: reported until the maps data run
writes `memory/.maps-and-root-notes-complete`, enforced once it has.

Usage:
  python3 scripts/check-class-directories.py                 # the resolved memory root
  python3 scripts/check-class-directories.py --memory-root DIR
Exit: 0 clean (or nothing to check); 1 on a finding.
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
import maps_shape as ms  # noqa: E402

CLASSES = ("semantic", "procedural", "episodic", "entities", "crystallized", "mocs")
IGNORABLE = {".DS_Store", "Icon\r", "Icon", ".gitkeep", cs.MARKER_NAME}


def load_rules():
    """The filing contract, or None when it does not load."""
    try:
        import storage_rules  # noqa: E402
        return storage_rules.load()
    except Exception:
        return None


def load_registers(rules=None):
    """`(memory types, record kinds)` from the filing contract, or None."""
    if rules is None:
        rules = load_rules()
    if rules is None:
        return None
    return set(rules.memory_types()), set(rules.record_kinds())


def findings_for(memory_root: Path, registers=None) -> tuple[list[str], int]:
    out: list[str] = []
    count = 0
    for cls in CLASSES:
        d = memory_root / "memory" / cls
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.name in IGNORABLE:
                continue
            rel = p.relative_to(memory_root).as_posix()
            count += 1
            if p.is_dir():
                out.append(f"{rel}/: a directory inside a class directory")
                continue
            if p.suffix != ".md":
                out.append(f"{rel}: not a note")
                continue
            parsed = cs.split_note(p.read_text(encoding="utf-8", errors="replace"))
            if parsed is None:
                out.append(f"{rel}: no frontmatter block")
                continue
            entries, _rest = parsed
            t = cs.scalar(cs.raw_value(entries, "type"))
            k = cs.scalar(cs.raw_value(entries, "kind"))
            if t and k:
                out.append(f"{rel}: carries both `type` and `kind`")
            elif not t and not k:
                out.append(f"{rel}: carries neither `type` nor `kind`")
            elif registers is not None:
                types, kinds = registers
                if t and t not in types:
                    out.append(f"{rel}: `type: {t}` is not a registered memory type")
                if k and k not in kinds:
                    out.append(f"{rel}: `kind: {k}` is not a registered record kind")
    return out, count


def check(memory_root: Path, registers=None, out=sys.stdout, rules=None) -> int:
    findings, count = findings_for(memory_root, registers)
    if registers is None:
        print("check-class-directories: the filing contract did not load; values are not checked", file=out)
    rc = 0
    if not (memory_root / "memory" / cs.MARKER_NAME).exists():
        print(f"check-class-directories: pre-backfill — {len(findings)} finding(s) over {count} entries, which "
              f"the card backfill clears; enforced once memory/{cs.MARKER_NAME} exists", file=out)
    elif findings:
        print(f"check-class-directories: {len(findings)} finding(s) over {count} entries", file=out)
        for f in findings:
            print(f"  {f}", file=out)
        rc = 1
    else:
        print(f"check-class-directories: clean — {count} entries, every one a card or a record", file=out)
    return max(rc, check_maps(memory_root, rules, out))


def check_maps(memory_root: Path, rules=None, out=sys.stdout) -> int:
    """`mocs/` holds the maps' shape: reported until the maps data run writes its
    marker, enforced once it has."""
    findings = ms.mocs_findings(memory_root, rules)
    if not findings:
        return 0
    if not ms.data_run_done(memory_root):
        print(f"check-class-directories: before the maps data run — {len(findings)} map finding(s) in mocs/, "
              f"which the data run clears; enforced once memory/{ms.MARKER_NAME} exists", file=out)
        for f in findings:
            print(f"  pending: {f}", file=out)
        return 0
    print(f"check-class-directories: {len(findings)} map finding(s) in mocs/", file=out)
    for f in findings:
        print(f"  {f}", file=out)
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--memory-root", default=None,
                    help="memory root to check (default: $MEMORY_ROOT, else the configured one)")
    args = ap.parse_args(argv)
    root = _resolve(args.memory_root)
    if root is None or not (root / "memory").is_dir():
        print("check-class-directories: no memory root resolves; nothing to check")
        return 0
    rules = load_rules()
    return check(root, load_registers(rules), rules=rules)


def _resolve(arg: str | None) -> Path | None:
    if arg:
        return Path(arg)
    import vault_layout  # noqa: E402
    root = vault_layout.env_memory_root()
    if root is None:
        try:
            import harness_memory as hm  # noqa: E402
            root = hm.memory_root()
        except ImportError:
            root = None
    return Path(root) if root else None


if __name__ == "__main__":
    raise SystemExit(main())
