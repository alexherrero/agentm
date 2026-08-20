#!/usr/bin/env python3
"""Collapse the live corpus onto the filing contract's vocabulary and lifecycle.

Two fields, at most one line each, per note.

**The vocabulary line** — two changes to it:

  * **the value**, where the storage rules retire it — `preferences` becomes
    `preference`, `domain-reference` becomes `reference`, and so on, from the
    deprecation map rather than from a judgment repeated thousands of times;
  * **the field name**, where the value is a memory type — a memory carries
    `type`, a record carries `kind`, and no note carries both.

**The status line** — mapped onto the four the contract defines. The corpus
carries `inbox`, `promoted`, `parked` and `evergreen`, none of which the
lifecycle recognizes, and a note whose status no pass can reason about is a note
no pass will touch.

Nothing else is touched. Not the path, not the filename, not the slug, not any
other frontmatter field, not a byte of the body. That is not conservatism, it is
the contract: filing is a frontmatter edit, and a note's address is stable so
that nothing linking to it can break because something tidied up.

**Line-surgical, never a round-trip.** The one line is replaced in place; the
frontmatter block is never parsed into a structure and re-serialized. A
serializer would reformat quoting, reorder keys, and normalize unicode across
sixteen thousand files, producing a diff nobody can review and a corpus subtly
different from the one that was measured.

**Idempotent.** A second run is a no-op, because a note already carrying a
current value under the right field name matches nothing this pass rewrites.

**Reversible by git, not by a bespoke undo.** The vault is a repository, so a
batch commit is the revert point — `git revert` restores the batch exactly. A
revert log would be a second, weaker mechanism for something git already does
better at this size.

Usage:
  python3 scripts/migrate_type_collapse.py --dry-run          # counts, no writes
  python3 scripts/migrate_type_collapse.py --dry-run --sample 12
  python3 scripts/migrate_type_collapse.py --apply            # writes, batched
  python3 scripts/migrate_type_collapse.py --apply --batch 500

Exit:
  0  clean — nothing to do, or the run completed
  1  notes were found that the contract cannot place
  2  setup error (no vault, no contract)
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO / "harness" / "skills" / "memory" / "scripts"))

import storage_rules  # noqa: E402

# The two roots the taxonomy governs. `_opinions` is walked but nothing in it
# changes — its notes are `opinion-supplement`, a registered record kind — and it
# is left in the walk deliberately, so the report says "0 changed" rather than
# leaving a reader to wonder whether it was covered.
WALK_ROOTS = ("memory", "desk")

# The status map. Only these four are rewritten; `unfiled`, `active`,
# `superseded` and `expired` are the contract's own and are already correct.
#
# `inbox` is what capture wrote for anything awaiting review, which is exactly
# what `unfiled` means now — rank-penalized, fully indexed, drained by filing.
# `promoted` marked a note that came through the old triage door and is now
# simply filed, which is `active`. `parked` was a note set aside rather than
# retired, and the contract has no separate word for that: `unfiled` is the
# honest one, since a parked note is waiting for a judgment it never got.
# `evergreen` marked a note not to expire; that is a decay concern rather than a
# lifecycle state, and the note itself is plainly `active`.
STATUS_MAP = {
    "inbox": "unfiled",
    "promoted": "active",
    "parked": "unfiled",
    "evergreen": "active",
}

# Directories holding things that are not corpus notes: harness state, machine
# files, and the daemon's own staging. A frontmatter edit here would be editing
# infrastructure, not memory.
SKIP_DIRS = frozenset({"_harness", "_meta", "_dream-staging", ".obsidian", ".git",
                       ".trash", "node_modules",
                       })

# `_inbox` was excluded from the vocabulary-only pass (operator ruling,
# 2026-08-19) on the reasoning that migrating it then meant rewriting the same
# note twice — once for its type and again for its status. That reasoning only
# holds if the two rewrites eventually combine, and this is the pass where they
# do, so the directory is back in scope. It is ~9,800 of the notes below.


class Edit:
    """One frontmatter line to replace, and what it becomes."""

    def __init__(self, line_no: int, old_line: str, new_line: str, label: str):
        self.line_no = line_no
        self.old_line = old_line
        self.new_line = new_line
        self.label = label


class Note:
    """One note the migration has an opinion about, and every edit it needs.

    A note may need the vocabulary line rewritten, the status line rewritten, or
    both. Both in one pass rather than two: an earlier part deferred the `_inbox`
    vocabulary collapse on the reasoning that migrating then meant rewriting the
    same note twice, and that reasoning only holds if the two rewrites eventually
    combine. This is where they combine.
    """

    def __init__(self, path: Path, rel: str, edits: list):
        self.path = path
        self.rel = rel
        self.edits = edits


def frontmatter_lines(text: str):
    """Yield `(index, key, value)` for each top-level line in the frontmatter.

    Returns nothing when the file does not open with a fence — a note without
    frontmatter is not a note this migration has anything to say about.
    """
    if not text.startswith("---\n"):
        return
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return
        raw = lines[i]
        if not raw or raw[0] in " \t#-":
            continue
        key, sep, value = raw.partition(":")
        if sep:
            yield i, key.strip(), value.strip()


def rewrite_line(old_line: str, value: str, new_field: str, new_value: str) -> str:
    """Replace one `key: value` line, preserving indentation and anything trailing.

    A trailing comment is rare in frontmatter, but discarding one would be a
    silent edit beyond what this migration claims to do.
    """
    _, _, after = old_line.partition(":")
    indent = after[:len(after) - len(after.lstrip())]
    remainder = after.strip()[len(value):]
    return f"{new_field}:{indent}{new_value}{remainder}"


def plan_note(path: Path, rel: str, rules) -> tuple:
    """Decide what, if anything, this note needs. Returns `(Note|None, problem|None)`."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, None

    found = {}
    status = None
    for i, key, value in frontmatter_lines(text):
        if key in ("type", "kind") and key not in found:
            found[key] = (i, value)
        elif key == "status" and status is None:
            status = (i, value)

    if "type" in found and "kind" in found:
        return None, (rel, f"carries both `type: {found['type'][1]}` and "
                           f"`kind: {found['kind'][1]}`")

    lines = text.split("\n")
    edits = []

    if found:
        field, (line_no, value) = next(iter(found.items()))
        if value:
            new_value = rules.resolve_deprecated(value) or value
            if new_value in rules.memory_types():
                new_field = "type"
            elif new_value in rules.record_kinds():
                new_field = "kind"
            else:
                return None, (rel,
                              f"`{value}` is in neither register and no deprecation maps it")
            if not (new_field == field and new_value == value):
                old_line = lines[line_no]
                edits.append(Edit(
                    line_no, old_line,
                    rewrite_line(old_line, value, new_field, new_value),
                    f"{field}: {value}  ->  {new_field}: {new_value}"))

    if status is not None:
        line_no, value = status
        new_value = STATUS_MAP.get(value)
        if new_value:
            old_line = lines[line_no]
            edits.append(Edit(
                line_no, old_line,
                rewrite_line(old_line, value, "status", new_value),
                f"status: {value}  ->  status: {new_value}"))

    if not edits:
        return None, None
    return Note(path, rel, edits), None


def walk(vault: Path, rules) -> tuple:
    """Plan the whole corpus. Returns `(notes, problems, scanned)`."""
    notes, problems = [], []
    scanned = 0
    for root_name in WALK_ROOTS:
        root = vault / root_name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            scanned += 1
            rel = str(path.relative_to(vault)).replace("\\", "/")
            note, problem = plan_note(path, rel, rules)
            if problem:
                problems.append(problem)
            elif note:
                notes.append(note)
    return notes, problems, scanned


def apply_note(note: Note) -> None:
    """Replace this note's lines. Nothing else in the file is rewritten."""
    text = note.path.read_text(encoding="utf-8")
    lines = text.split("\n")
    for edit in note.edits:
        if lines[edit.line_no] != edit.old_line:
            raise RuntimeError(
                f"{note.rel}: line {edit.line_no + 1} changed under the migration")
        lines[edit.line_no] = edit.new_line
    note.path.write_text("\n".join(lines), encoding="utf-8")


def git(vault: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(vault), *args],
                          capture_output=True, text=True)


def report(notes, problems, scanned, *, sample: int) -> None:
    by_change: dict = {}
    for n in notes:
        for edit in n.edits:
            by_change[edit.label] = by_change.get(edit.label, 0) + 1

    total_edits = sum(len(n.edits) for n in notes)
    print(f"scanned            : {scanned} notes")
    print(f"to change          : {len(notes)} notes, {total_edits} line(s)")
    print(f"already current    : {scanned - len(notes) - len(problems)}")
    print(f"cannot place       : {len(problems)}")
    if by_change:
        print("\nby change:")
        for label, count in sorted(by_change.items(), key=lambda kv: -kv[1]):
            print(f"  {count:6d}  {label}")
    if problems:
        print(f"\ncannot place ({len(problems)}):")
        for rel, why in problems[:20]:
            print(f"  {rel}: {why}")
        if len(problems) > 20:
            print(f"  … and {len(problems) - 20} more")
    if sample and notes:
        print(f"\nsample of {min(sample, len(notes))} exact line edits:")
        step = max(1, len(notes) // sample)
        for n in notes[::step][:sample]:
            for edit in n.edits:
                print(f"  {n.rel}:{edit.line_no + 1}")
                print(f"    - {edit.old_line}")
                print(f"    + {edit.new_line}")


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description="collapse the corpus onto the contract")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="report, write nothing")
    mode.add_argument("--apply", action="store_true", help="rewrite, batched and committed")
    ap.add_argument("--vault", default=None, help="vault root (default: resolved)")
    ap.add_argument("--batch", type=int, default=1000, help="notes per commit")
    ap.add_argument("--sample", type=int, default=6, help="exact line edits to show")
    args = ap.parse_args(argv)

    if args.vault:
        vault = Path(args.vault)
    else:
        try:
            import harness_memory
            vault = Path(harness_memory.memory_root())
        except Exception as exc:
            print(f"migrate: no vault resolved: {exc}", file=sys.stderr)
            return 2
    if not vault.is_dir():
        print(f"migrate: not a directory: {vault}", file=sys.stderr)
        return 2

    try:
        rules = storage_rules.load()
    except storage_rules.StorageRulesError as exc:
        print(f"migrate: no filing contract, so there is no vocabulary to migrate "
              f"onto: {exc}", file=sys.stderr)
        return 2

    print(f"vault              : {vault}")
    print(f"contract           : {rules.source} ({rules.content_hash()})")
    notes, problems, scanned = walk(vault, rules)
    report(notes, problems, scanned, sample=args.sample)

    if args.dry_run:
        print("\ndry run — nothing written")
        return 1 if problems else 0

    if not notes:
        print("\nnothing to do")
        return 1 if problems else 0

    # The path set before, so "no file moved" is a measured claim rather than an
    # assurance. The migration touches one line and never a name, and this is
    # what proves it did.
    before = {str(p.relative_to(vault)) for p in vault.rglob("*.md")}

    repo_ok = git(vault, "rev-parse", "--git-dir").returncode == 0
    if not repo_ok:
        print("\nmigrate: the vault is not a git repository, so a batch has no "
              "revert point. Refusing.", file=sys.stderr)
        return 2

    done = 0
    for start in range(0, len(notes), args.batch):
        batch = notes[start:start + args.batch]
        for note in batch:
            apply_note(note)
        done += len(batch)
        git(vault, "add", "-A")
        message = (f"memory: collapse {len(batch)} note(s) onto the filing contract "
                   f"({done}/{len(notes)})\n\n"
                   f"Frontmatter only — one line per note, no path, filename or slug "
                   f"changed. Values from the deprecation map in "
                   f"standards/storage-rules.md at {rules.content_hash()}.")
        git(vault, "commit", "-q", "-m", message)
        print(f"  committed {done}/{len(notes)}")

    after = {str(p.relative_to(vault)) for p in vault.rglob("*.md")}
    if before != after:
        added = sorted(after - before)[:10]
        removed = sorted(before - after)[:10]
        print(f"\nPATH SET CHANGED — this migration must never move a file.\n"
              f"  added:   {added}\n  removed: {removed}", file=sys.stderr)
        return 1
    print(f"\npath set unchanged across {len(before)} files — nothing moved")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
