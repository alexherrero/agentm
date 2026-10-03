#!/usr/bin/env python3
"""restore_aliases — give notes back the aliases nightly enrichment dropped, as a
manifest the dreaming binary makes through its journal (task 182 step 5).

Until task 182 a card's re-enrichment replaced its alias list with the pass's
own answer, so the first rewrite of a card lost the aliases written at capture
(the asker's phrasing, which the filing design adopted at 12/12 at rank 1) and
the ones the note itself contains. The operator's ruling of 2026-10-01: a pass
never removes an alias. Enrichment now merges; this pass restores what was
already lost.

What counts as lost: an alias a note carried at the baseline revision (by
default the vault's 2026-09-14 snapshot, `ebce1f0c7`, the last commit before
the batch rewrites began) that the same path no longer carries. Two things are
never restored:

  - an alias the 2026-08-08 cold backfill wrote (its journal names them): the
    measured rule bans a model reading old notes and writing aliases, and the
    rule outranks the ruling's "never remove";
  - the eval canary's: step 7 of task 182 owns that instrument.

Restored aliases are appended after the note's current ones. Nothing here
writes a note; check and make the manifest with
`agentmdream apply -manifest FILE [-apply]`.

    python3 scripts/migrate/restore_aliases.py [--memory-root DIR] [--since REV] [--out FILE] [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
_SKILL = _REPO / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL) not in sys.path:
    sys.path.insert(0, str(_SKILL))

import card_shape  # noqa: E402

BASELINE = "ebce1f0c7"
CANARY = "memory/semantic/eval-canary.md"
BACKFILL_JOURNAL = "alias-backfill-journal-20260808.jsonl"


def note_aliases(text: str) -> list[str]:
    """A note's aliases, flow list or block list, unquoted."""
    parsed = card_shape.split_note(text)
    if parsed is None:
        return []
    for key, group in parsed[0]:
        if key != "aliases":
            continue
        value = group[0].split(":", 1)[1].strip()
        if value:
            return [a for a in card_shape.flow_list(value) if a]
        return [card_shape.scalar(l.strip()[2:]) for l in group[1:] if l.strip().startswith("- ")]
    return []


def with_aliases(text: str, add: list[str]) -> str:
    """The note with `add` appended to its aliases (a flow list either way)."""
    parsed = card_shape.split_note(text)
    if parsed is None:
        # No frontmatter this reader can split (a CRLF note among them): left
        # alone rather than stopping the whole plan (task 182 release review).
        return text
    current = note_aliases(text)
    seen = {a.lower() for a in current}
    merged = current + [a for a in add if a.lower() not in seen]
    entries, rest = parsed
    rendered = "[" + ", ".join(card_shape.quote(a) for a in merged) + "]"
    return card_shape.reorder(card_shape.join_note(card_shape.set_value(entries, "aliases", rendered), rest))


def backfilled(state_dir: "Path | None") -> dict:
    """basename -> the aliases the 2026-08-08 cold backfill wrote, lowercased."""
    out: dict = defaultdict(set)
    if state_dir is None:
        return out
    journal = state_dir / BACKFILL_JOURNAL
    if not journal.is_file():
        return out
    for line in journal.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        stem = os.path.basename(row.get("path") or "").removesuffix(".md")
        for a in row.get("aliases") or []:
            out[stem].add(a.strip().lower())
    return out


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True).stdout


def plan(memory_root: Path, since: str, state_dir: "Path | None") -> dict:
    """Walk the whole vault the memory root sits in."""
    vault = Path(_git(memory_root, "rev-parse", "--show-toplevel").strip() or memory_root)
    prefix = os.path.relpath(memory_root, vault).replace(os.sep, "/")
    banned = backfilled(state_dir)
    acts, notes, skipped = [], [], 0
    for rel in _git(vault, "ls-tree", "-r", "--name-only", since).split("\n"):
        if not rel.endswith(".md"):
            continue
        path = vault / rel
        mem_rel = os.path.relpath(path, memory_root).replace(os.sep, "/")
        if mem_rel == CANARY or not path.is_file():
            continue
        old = note_aliases(_git(vault, "show", f"{since}:{rel}"))
        if not old:
            continue
        raw = path.read_bytes()
        current = raw.decode("utf-8")
        have = {a.lower() for a in note_aliases(current)}
        stem = path.stem
        lost = []
        for a in old:
            if a.lower() in have:
                continue
            if a.lower() in banned.get(stem, set()):
                skipped += 1
                continue
            lost.append(a)
        if not lost:
            continue
        after = with_aliases(current, lost)
        if after == current:
            continue
        notes.append({"note": mem_rel, "restored": lost})
        acts.append({"rel": mem_rel, "before_sha256": hashlib.sha256(raw).hexdigest(), "after": after,
                     "summary": f"{len(lost)} alias(es) a rewrite dropped, restored"})
    return {"job": "manifest-restore-aliases",
            "reason": "a pass never removes an alias (task 182 step 5, the operator's ruling of 2026-10-01)",
            "since": since, "notes": notes, "skipped_backfill": skipped, "acts": acts,
            "prefix": prefix}


def _harness_memory():
    if str(_REPO / "scripts") not in sys.path:
        sys.path.insert(0, str(_REPO / "scripts"))
    import harness_memory  # noqa: E402 — resolved at run time, never cached
    return harness_memory


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description="plan the restoration of dropped aliases as a manifest")
    ap.add_argument("--memory-root", help="the memory root (default: the configured one)")
    ap.add_argument("--state-dir", help="the engine state directory holding the 2026-08-08 backfill journal "
                                        "(default: ~/.local/state/agentm)")
    ap.add_argument("--since", default=BASELINE, help=f"the baseline revision (default {BASELINE})")
    ap.add_argument("--out", help="the manifest file (default: <memory root>/diagnostics/migrations/"
                                  "restore-aliases/<today>.json)")
    ap.add_argument("--dry-run", action="store_true", help="print what would change; write nothing")
    a = ap.parse_args(argv)
    root = Path(a.memory_root) if a.memory_root else _harness_memory().memory_root()
    if root is None:
        raise SystemExit("restore_aliases: no memory root resolves; pass --memory-root")
    state = Path(a.state_dir) if a.state_dir else Path.home() / ".local" / "state" / "agentm"
    manifest = plan(Path(root), a.since, state)
    for n in manifest["notes"]:
        print(f"  {n['note']}: {', '.join(n['restored'])}")
    print(f"{manifest['job']}: {len(manifest['acts'])} act(s); "
          f"{manifest['skipped_backfill']} backfill alias(es) left out")
    if a.dry_run or not manifest["acts"]:
        return 0
    out = Path(a.out) if a.out else (Path(root) / "diagnostics" / "migrations" / "restore-aliases"
                                     / f"{date.today():%Y-%m-%d}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  manifest {out}\n  check it: agentmdream apply -manifest {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
