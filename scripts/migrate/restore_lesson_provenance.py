#!/usr/bin/env python3
"""restore_lesson_provenance — give each lesson back the sources a recheck took
off it, as a manifest the dreaming binary makes through its journal (task 182).

Until task 182, `agentmd crystallize -recheck` released a card the lesson was not
true of in two ways at once: it took the stamp off the card, and it took the
card off the lesson's `consolidated_from` and "What taught it" list. The first
is right — a card the lesson does not subsume should keep its rank. The second
erased provenance: the card still taught the lesson. On 2026-10-01 it erased all
of it from `ci-green-closes-work`, which released every one of its five cards
and was left a lesson with no sources, refused by check-vault-frontmatter.

The operator's ruling of 2026-10-01: a lesson keeps its sources as provenance,
and names the cards it is not true of in a `released:` list. The recheck now
writes exactly that. This pass repairs the lessons the old recheck already
edited: for each lesson, the `consolidated_from` list and "What taught it"
section it was written with — its first version in the vault's git history —
come back, and every entry the recheck had removed is named in `released:`. An
entry is only ever removed by a recheck (crystallize writes the list once, and
enrichment never writes crystallized/), so "listed then, not listed now" is
exactly the set of released cards. Nothing else in the lesson changes.

Nothing here writes a note. Check and make the manifest with
`agentmdream apply -manifest FILE [-apply]`.

    python3 scripts/migrate/restore_lesson_provenance.py [--memory-root DIR] [--out FILE] [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
_SKILL = _REPO / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL) not in sys.path:
    sys.path.insert(0, str(_SKILL))

import card_shape  # noqa: E402

LESSONS = "memory/crystallized"
_LINK = re.compile(r"\[\[([^\]|]+)")
_TAUGHT = "## What taught it"


def _stem(entry: str) -> str:
    m = _LINK.search(entry)
    target = m.group(1).strip() if m else entry.strip().strip('"')
    return target.removesuffix(".md").rsplit("/", 1)[-1]


def _list_block(text: str, key: str) -> "tuple[int, int, list[str]] | None":
    """`(start, end, entries)` of a block list in the frontmatter: the line span
    `key:` and its `  - ` items occupy, and the items as written."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line == "---" and i > 0:
            return None
        if line.startswith(key + ":"):
            j = i + 1
            while j < len(lines) and lines[j].startswith("  - "):
                j += 1
            return i, j, [l[4:] for l in lines[i + 1:j]]
    return None


def _taught_section(text: str) -> "list[str] | None":
    """The "What taught it" list lines, up to the next heading or the end."""
    lines = text.split("\n")
    try:
        i = lines.index(_TAUGHT)
    except ValueError:
        return None
    out = []
    for line in lines[i + 1:]:
        if line.startswith("#"):
            break
        out.append(line)
    return out


def restore(current: str, original: str) -> "tuple[str, list[str]]":
    """The lesson with its original sources back and the removed ones named in
    `released:`, and the released stems. Unchanged when nothing was removed."""
    now = _list_block(current, "consolidated_from")
    then = _list_block(original, "consolidated_from")
    if then is None:
        return current, []
    now_entries = now[2] if now else []
    now_stems = {_stem(e) for e in now_entries}
    removed = [_stem(e) for e in then[2] if _stem(e) not in now_stems]
    if not removed:
        return current, []

    lines = current.split("\n")
    block = ["consolidated_from:"] + ["  - " + e for e in then[2]]
    if now:
        lines[now[0]:now[1]] = block
    else:
        # The key with nothing under it (`consolidated_from:` alone) or gone.
        at = next((i for i, l in enumerate(lines) if l.startswith("consolidated_from")), None)
        if at is None:
            at = next(i for i, l in enumerate(lines) if l == "---" and i > 0)
            lines[at:at] = block
        else:
            lines[at:at + 1] = block
    text = "\n".join(lines)

    taught = _taught_section(original)
    if taught is not None and _TAUGHT in text:
        head, _, tail = text.partition(_TAUGHT + "\n")
        rest = tail.split("\n")
        k = next((i for i, l in enumerate(rest) if l.startswith("#")), len(rest))
        text = head + _TAUGHT + "\n" + "\n".join(taught + rest[k:])

    parsed = card_shape.split_note(text)
    released = list(dict.fromkeys(
        card_shape.flow_list(card_shape.raw_value(parsed[0], "released")) if parsed else []))
    released = [_stem(r) for r in released]
    for s in removed:
        if s not in released:
            released.append(s)
    line = "released: [" + ", ".join(f'"[[{s}]]"' for s in released) + "]"
    entries, rest = parsed
    entries = card_shape.set_value(entries, "released", line.split(": ", 1)[1])
    return card_shape.reorder(card_shape.join_note(entries, rest)), removed


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, check=True).stdout


def plan(root: Path) -> dict:
    acts, lessons = [], []
    for p in sorted((root / LESSONS).glob("*.md")):
        rel = f"{LESSONS}/{p.name}"
        raw = p.read_bytes()
        current = raw.decode("utf-8")
        added = _git(root, "log", "--diff-filter=A", "--format=%H", "--", rel).split()
        if not added:
            continue
        original = _git(root, "show", f"{added[-1]}:./{rel}")
        after, removed = restore(current, original)
        if not removed:
            continue
        lessons.append({"lesson": rel, "released": removed})
        acts.append({"rel": rel, "before_sha256": hashlib.sha256(raw).hexdigest(), "after": after,
                     "summary": f"provenance restored; {len(removed)} source(s) named in released"})
    return {"job": "manifest-restore-lesson-provenance",
            "reason": "a lesson keeps the sources a recheck released as provenance (task 182, "
                      "the operator's ruling of 2026-10-01)",
            "lessons": lessons, "acts": acts}


def _memory_root(arg: "str | None") -> Path:
    if arg:
        return Path(arg)
    if str(_REPO / "scripts") not in sys.path:
        sys.path.insert(0, str(_REPO / "scripts"))
    import harness_memory  # noqa: E402 — resolved at run time, never cached
    root = harness_memory.memory_root()
    if root is None:
        raise SystemExit("restore_lesson_provenance: no memory root resolves; pass --memory-root")
    return Path(root)


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description="plan the restoration of lesson provenance as a manifest")
    ap.add_argument("--memory-root", help="the memory root (default: the configured one)")
    ap.add_argument("--out", help="the manifest file (default: <memory root>/diagnostics/migrations/"
                                  "crystallize-provenance/<today>-restore.json)")
    ap.add_argument("--dry-run", action="store_true", help="print what would change; write nothing")
    a = ap.parse_args(argv)
    root = _memory_root(a.memory_root)
    manifest = plan(root)
    for l in manifest["lessons"]:
        print(f"  {l['lesson']}: {len(l['released'])} released -> {', '.join(l['released'])}")
    print(f"{manifest['job']}: {len(manifest['acts'])} act(s)")
    if a.dry_run or not manifest["acts"]:
        return 0
    out = Path(a.out) if a.out else (root / "diagnostics" / "migrations" / "crystallize-provenance"
                                     / f"{date.today():%Y-%m-%d}-restore.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  manifest {out}\n  check it: agentmdream apply -manifest {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
