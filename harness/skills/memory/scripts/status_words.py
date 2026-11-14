#!/usr/bin/env python3
"""A note's `status`: a short, fixed list of words, and the state it must
follow (task 204).

The vault audit of 2026-11-13 counted more than 60 status words in use, and
found notes whose status said the opposite of their state: 17 archived cards,
26 merged cards and 19 dismissed ideas, all `active`. This module holds:

- the rule a writer applies when it changes a note's state (`sync_status`),
  the Python copy of the daemon's `cardshape.SyncStatus`. Both copies are held
  to one table of cases, `scripts/fixtures/status-sync-cases.json`;
- what the lint and the save-time check say about a note's status
  (`status_findings`): a word that is not on the list is a warning with the
  nearest word suggested, and a status that contradicts the note's own state is
  an error;
- `python3 status_words.py fix [--apply] [--vault PATH]`, which lists the
  rewrite each contradiction calls for, and writes them only with `--apply`.

The list itself is the contract's (`agentmd rules --json` → `statuses`): the
contract's own `status:` list when it names one, else the daemon's built-in
ten. Nothing here keeps a copy of it.
"""
from __future__ import annotations

import argparse
import difflib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

ACTIVE, ARCHIVED, SUPERSEDED, MERGED, DROPPED = "active", "archived", "superseded", "merged", "dropped"
#: The words `sync_status` writes from a note's state.
STATE_STATUSES = (ARCHIVED, SUPERSEDED, MERGED)

_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)[ \t]*:(.*)$")


def _unquote(v: str) -> str:
    return v.strip().strip("\"'").strip()


def _head(text: str) -> "Optional[tuple[int, int]]":
    """(start, end) of the frontmatter's inner text, or None."""
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    return (4, end) if end >= 0 else None


def read_state(text: str) -> dict:
    """The frontmatter keys the rule reads: `status`, `lifecycle`,
    `consolidated_into` (as True/False), `superseded_by` and `dismissed`;
    first value wins. {} for a note with no frontmatter."""
    span = _head(text)
    if span is None:
        return {}
    lines = text[span[0]:span[1]].split("\n")
    out: dict = {}
    for i, line in enumerate(lines):
        if not line or line[0] in " \t#-":
            continue
        m = _KEY.match(line)
        if not m:
            continue
        key, val = m.group(1), _unquote(m.group(2))
        if key in out:
            continue
        if key == "consolidated_into":
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
            out[key] = (val not in ("", "[]")) or nxt.startswith("- ")
        elif key in ("status", "lifecycle", "superseded_by", "dismissed"):
            out[key] = val
    return out


def status_for(lifecycle: str, current: str, consolidated: bool) -> str:
    """The status a note's state calls for, or "" to leave it alone. The
    daemon's `cardshape.StatusFor`, word for word."""
    lc = (lifecycle or "").strip().lower()
    if lc == "archived":
        return ARCHIVED
    if lc == "superseded":
        return SUPERSEDED
    if consolidated:
        return MERGED
    # Only on the lifecycle axis: a note with no `lifecycle` keeps what a person
    # wrote (a project's overtaken plan says `status: superseded` by hand).
    if lc and (current or "").strip().lower() in STATE_STATUSES:
        return ACTIVE
    return ""


def sync_status(text: str) -> str:
    """`text` with its `status` line set to what its state calls for. A note
    with no frontmatter or no `status` line comes back unchanged."""
    span = _head(text)
    if span is None:
        return text
    state = read_state(text)
    if "status" not in state:
        return text
    want = status_for(state.get("lifecycle", ""), state["status"], bool(state.get("consolidated_into")))
    if not want or want == state["status"].lower():
        return text
    lines = text[span[0]:span[1]].split("\n")
    for i, line in enumerate(lines):
        m = _KEY.match(line)
        if m and m.group(1) == "status":
            lines[i] = f"status: {want}"
            break
    return text[:span[0]] + "\n".join(lines) + text[span[1]:]


# ── what the list says about a note ───────────────────────────────────────────

#: The nearest list word for the words found in the vault on 2026-11-13, where
#: the spelling alone would not find it. A suggestion only: the lint never
#: rewrites a word that is not on the list.
NEAREST = {
    "pending-review": "unfiled", "in-progress": "active", "executing": "active",
    "current": "active", "launched": "active", "final": "active", "ratified": "active",
    "adopted": "active", "accepted": "active", "approved": "active", "filed": "active",
    "chosen": "active", "baseline": "active", "findings": "done", "offer-out": "active",
    "ready": "queued", "spec-ready": "queued", "staged": "queued",
    "proposed": "draft", "seed": "draft", "shaped": "draft", "brief": "draft",
    "scoped": "draft", "research-partial": "draft",
    "deprioritized": "parked", "handoff": "done", "closed": "done", "resolved": "done",
    "built": "done", "landed": "done", "executed": "done", "passed": "dropped",
    "research-complete": "done", "re-audit-complete": "done", "decided-go": "done",
    "played-works": "done", "played-masks-kept": "done",
    "lost": "dropped", "offer-rejected": "dropped", "rejected": "dropped",
    "retired": "archived", "expired": "superseded", "inbox": "unfiled",
}


def nearest(word: str, statuses) -> str:
    w = (word or "").strip().lower()
    if w in NEAREST and NEAREST[w] in statuses:
        return NEAREST[w]
    close = difflib.get_close_matches(w, sorted(statuses), n=1, cutoff=0.0)
    return close[0] if close else ACTIVE


@dataclass
class StatusFinding:
    severity: str      # "error" for a contradiction, "warn" for an off-list word
    message: str
    suggestion: str
    fix: str = ""      # the status a contradiction calls for; "" when none is certain


def in_archive(rel: str) -> bool:
    parts = rel.replace(os.sep, "/").split("/")
    return any(p in ("archive", "_archive", "archived") for p in parts[:-1])


def status_findings(rel: str, text: str, statuses) -> list:
    """What the list and the note's own state say about its `status`. Empty
    for a note with no status. `statuses` is the contract's list; when it is
    empty (no daemon answered) only the contradictions are checked."""
    state = read_state(text)
    status = state.get("status")
    if status is None or status == "":
        return []
    out = []
    word = status.strip()
    if statuses and word not in statuses:
        suggest = nearest(word, statuses)
        out.append(StatusFinding(
            "warn", f"`status: {word}` is not one of the contract's status words",
            f"use `status: {suggest}`; the list is {', '.join(statuses)}"))
    lower = word.lower()
    lc = (state.get("lifecycle") or "").strip().lower()
    want = status_for(lc, lower, bool(state.get("consolidated_into")))
    if want and want != lower:
        why = {ARCHIVED: "its `lifecycle` is archived",
               SUPERSEDED: "its `lifecycle` is superseded",
               MERGED: "its `consolidated_into` names a lesson",
               ACTIVE: f"it carries `status: {lower}` but its state no longer calls for it"}[want]
        out.append(StatusFinding("error", f"`status: {word}`, but {why}",
                                 f"set `status: {want}`", fix=want))
    elif state.get("superseded_by") and lower == ACTIVE and lc != "superseded":
        out.append(StatusFinding("error", "`status: active`, but it names a successor in `superseded_by`",
                                 "set `lifecycle: superseded` and `status: superseded`, or remove "
                                 "`superseded_by` if it is still current"))
    if state.get("dismissed") and lower == ACTIVE:
        out.append(StatusFinding("error", "`status: active`, but the idea was dismissed",
                                 "set `status: dropped`", fix=DROPPED))
    if in_archive(rel) and lower == ACTIVE and not any(f.fix for f in out):
        out.append(StatusFinding("error", "`status: active`, but the note is in an archive folder",
                                 "set `status: archived`, or move it out of the archive", fix=ARCHIVED))
    return out


def contract_statuses() -> tuple:
    """The contract's list, asked of the daemon; () when nothing answers."""
    try:
        import storage_rules
        return tuple(storage_rules.rules().statuses())
    except Exception:
        return ()


# ── the walk and the fixer ────────────────────────────────────────────────────

#: Never walked: machine folders, and the walled areas whose notes are not
#: the vault's (templates are status placeholders by design).
_SKIP_DIRS = {".git", ".obsidian", ".trash", ".harness", "node_modules"}
_SKIP_PREFIXES = ("standards/templates/",)


def walk(vault_root: Path):
    """(rel, path) for every note under the vault root, in path order."""
    vault_root = Path(vault_root)
    for root, dirs, files in os.walk(vault_root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            if not name.endswith(".md"):
                continue
            path = Path(root) / name
            rel = path.relative_to(vault_root).as_posix()
            if rel.startswith(_SKIP_PREFIXES):
                continue
            yield rel, path


def vault_findings(vault_root: Path, statuses=None) -> list:
    """(rel, StatusFinding) for every note under the vault root."""
    statuses = tuple(statuses) if statuses is not None else contract_statuses()
    out = []
    for rel, path in walk(vault_root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        out += [(rel, f) for f in status_findings(rel, text, statuses)]
    return out


def fixed_text(text: str, want: str) -> str:
    """`text` with its status line set to `want`, nothing else touched."""
    span = _head(text)
    if span is None:
        return text
    lines = text[span[0]:span[1]].split("\n")
    for i, line in enumerate(lines):
        m = _KEY.match(line)
        if m and m.group(1) == "status":
            lines[i] = f"status: {want}"
            return text[:span[0]] + "\n".join(lines) + text[span[1]:]
    return text


def plan_fixes(vault_root: Path) -> list:
    """(rel, from, to) for every contradiction with one certain fix. An
    off-list word is never on this list: choosing its word is the operator's."""
    out = []
    for rel, path in walk(vault_root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for f in status_findings(rel, text, ()):
            if f.fix:
                out.append((rel, read_state(text).get("status", ""), f.fix))
                break
    return out


def apply_fixes(vault_root: Path, fixes: list) -> int:
    n = 0
    for rel, _old, new in fixes:
        path = Path(vault_root) / rel
        text = path.read_text(encoding="utf-8")
        after = fixed_text(text, new)
        if after != text:
            path.write_text(after, encoding="utf-8")
            n += 1
    return n


def _vault_root(arg: Optional[str]) -> Optional[Path]:
    if arg:
        return Path(arg).expanduser()
    root = os.environ.get("MEMORY_ROOT") or os.environ.get("MEMORY_VAULT_PATH")
    if not root:
        return None
    root = Path(root)
    return root.parent if (root.parent / ".obsidian").is_dir() and not (root / ".obsidian").is_dir() else root


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Status words: list contradictions and their fixes.")
    ap.add_argument("command", choices=("fix", "report"))
    ap.add_argument("--vault", default=None, help="the vault root (else derived from MEMORY_ROOT)")
    ap.add_argument("--apply", action="store_true", help="write the fixes (fix only); without it nothing is written")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    vault = _vault_root(args.vault)
    if vault is None or not vault.is_dir():
        print("status-words: no vault. Pass --vault or set $MEMORY_ROOT.", file=sys.stderr)
        return 2
    if args.command == "report":
        found = vault_findings(vault)
        for rel, f in found:
            print(f"[{f.severity}] {rel}: {f.message} -> {f.suggestion}")
        errs = sum(1 for _r, f in found if f.severity == "error")
        print(f"status-words: {errs} contradiction(s), {len(found) - errs} off-list word(s)")
        return 0
    fixes = plan_fixes(vault)
    for rel, old, new in fixes:
        print(f"{rel}: status {old} -> {new}")
    if args.apply:
        print(f"status-words: rewrote {apply_fixes(vault, fixes)} note(s)")
    else:
        print(f"status-words: {len(fixes)} note(s) would change; nothing written (pass --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
