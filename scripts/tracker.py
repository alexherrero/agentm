#!/usr/bin/env python3
"""The tracker: a task's or a project's living head, in one schema.

agentm-vault § Projects and tasks. A tracker is its own small file beside the
plan and the progress log: `tasks/<slug>/tracker.md` for a task (or
`tracker-<slug>.md` beside a flat pair until the migration moves it), and
`<project>/tracker.md` for the project itself. The session that changes a
tracker rewrites it in place. Its body has four sections:

  Objective  what done looks like, copied from the plan's Goal at open
  State      what is true right now, rewritten rather than appended
  Next       the immediate steps in order; the first line is the next session's
  Outcome    written once, at close

A step rewrites State and Next without moving the status: `transition` to a
tracker's own status, when that status is not final, is that rewrite. It names
State, Next or both and stamps `updated`; it never writes the Outcome or
`closed`, and `done` and `dropped` are never rewritten.

This module owns the schema so both plugins write it the same way. agentm
imports it; crickets shells to its command line rather than re-deriving it.
Standard library only.

Usage:
  python3 scripts/tracker.py new --title T --project P [--task S] --objective TEXT
                                 --next TEXT [--importance N] [--issue N]
                                 [--design PATH] [--today YYYY-MM-DD] [--out PATH]
  python3 scripts/tracker.py show PATH
  python3 scripts/tracker.py transition PATH --to STATUS [--state TEXT] [--next TEXT]
                                 [--outcome TEXT] [--today YYYY-MM-DD]
  python3 scripts/tracker.py check PATH [PATH ...]
Exit: 0 ok · 1 a finding, a refused transition or rewrite, or a file that changed
under the write · 2 a usage or I/O error
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

KIND = "tracker"
STATUSES = ("queued", "active", "parked", "done", "dropped")
FINAL = frozenset({"done", "dropped"})
# What each status may become. Open writes `queued`; the first step, `active`;
# park and resume move between `active` and `parked`; close writes `done`, and a
# withdrawal `dropped`. Both of those are final. No status lists itself: a
# `transition` to a non-final tracker's own status is not a move but a rewrite
# of State and Next in place, which each later step writes under `active`.
TRANSITIONS = {
    "queued": ("active", "dropped"),
    "active": ("parked", "done", "dropped"),
    "parked": ("active", "dropped"),
    "done": (),
    "dropped": (),
}
# The frontmatter, in the order it is written. `task` is absent on a project's
# own tracker; `importance`, `issue`, `design` and `sensitivity` are written
# when they exist; `closed` is always written and stays empty until the tracker
# is final. `sensitivity` is the operator's marking (`personal-financial` on
# the home project's notes), carried through every rewrite and never set here.
FIELDS = ("kind", "title", "project", "task", "status", "importance",
          "opened", "updated", "closed", "issue", "design", "sensitivity")
REQUIRED = ("kind", "title", "project", "status", "opened", "updated", "closed")
SECTIONS = ("Objective", "State", "Next", "Outcome")
NOT_STARTED = "Not started."

_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):(.*)$")
_HEADING = re.compile(r"^## (.*?)[ \t]*$", re.M)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PLAIN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_ ./()'-]*$")
_NUMBERISH = re.compile(r"^[-+]?[0-9][0-9_.eE+-]*$")
_YAML_WORDS = frozenset({"true", "false", "yes", "no", "on", "off", "null", "y", "n", "~"})


class TrackerError(ValueError):
    """A tracker that does not parse, or a change the schema refuses."""


class ChangedError(TrackerError):
    """The file changed between the read and the write."""


def _clean(text: Optional[str]) -> str:
    """A section's text without surrounding blank lines or trailing spaces."""
    lines = (text or "").strip("\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip("\n")


@dataclass
class Tracker:
    title: str
    project: str
    status: str = "queued"
    task: Optional[str] = None
    importance: Optional[int] = None
    opened: str = ""
    updated: str = ""
    closed: Optional[str] = None
    issue: Optional[int] = None
    design: Optional[str] = None
    sensitivity: Optional[str] = None
    objective: str = ""
    state: str = ""
    next_steps: str = ""
    outcome: str = ""

    def __post_init__(self) -> None:
        for name in ("objective", "state", "next_steps", "outcome"):
            setattr(self, name, _clean(getattr(self, name)))
        for name in ("task", "closed", "design", "sensitivity"):
            if getattr(self, name) == "":
                setattr(self, name, None)

    def sections(self) -> dict:
        return dict(zip(SECTIONS, (self.objective, self.state, self.next_steps, self.outcome)))

    def to_dict(self) -> dict:
        fields = {"kind": KIND}
        for name in FIELDS[1:]:
            fields[name] = getattr(self, name)
        fields["sections"] = self.sections()
        return fields


# --- text -------------------------------------------------------------------

def _scalar_out(value: str) -> str:
    """A string as YAML: plain when that is unambiguous, else double-quoted."""
    v = str(value)
    if (_PLAIN.match(v) and not v.endswith(" ") and v.lower() not in _YAML_WORDS
            and not _NUMBERISH.match(v)):
        return v
    return json.dumps(v, ensure_ascii=False)


def _scalar_in(raw: str, key: str) -> str:
    v = raw.strip()
    if len(v) >= 2 and v[0] == v[-1] == '"':
        try:
            return json.loads(v)
        except ValueError as exc:
            raise TrackerError(f"`{key}` is not a readable quoted string: {exc}") from exc
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1].replace("''", "'")
    return v


def render(t: Tracker) -> str:
    lines = ["---", f"kind: {KIND}", f"title: {_scalar_out(t.title)}",
             f"project: {_scalar_out(t.project)}"]
    if t.task:
        lines.append(f"task: {_scalar_out(t.task)}")
    lines.append(f"status: {t.status}")
    if t.importance is not None:
        lines.append(f"importance: {t.importance}")
    lines.append(f"opened: {t.opened}")
    lines.append(f"updated: {t.updated}")
    lines.append(f"closed: {t.closed}" if t.closed else "closed:")
    if t.issue is not None:
        lines.append(f"issue: {t.issue}")
    if t.design:
        lines.append(f"design: {_scalar_out(t.design)}")
    if t.sensitivity:
        lines.append(f"sensitivity: {_scalar_out(t.sensitivity)}")
    lines.append("---")
    body = []
    for name, text in t.sections().items():
        body.append(f"## {name}")
        if text:
            body += ["", text]
        body.append("")
    return "\n".join(lines) + "\n\n" + "\n".join(body).rstrip("\n") + "\n"


def _split(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        raise TrackerError("no frontmatter block")
    end = text.find("\n---\n", 3)
    if end == -1:
        raise TrackerError("the frontmatter block is not closed")
    return text[4:end], text[end + 5:]


def _sections(body: str) -> dict:
    found = [(m.group(1), m.start(), m.end()) for m in _HEADING.finditer(body)]
    names = [name for name, _, _ in found]
    if names != list(SECTIONS):
        raise TrackerError(f"the body's sections are {names}, not {list(SECTIONS)} in that order")
    if body[:found[0][1]].strip():
        raise TrackerError("the body has text before `## Objective`")
    out = {}
    for i, (name, _start, end) in enumerate(found):
        stop = found[i + 1][1] if i + 1 < len(found) else len(body)
        out[name] = _clean(body[end:stop])
    return out


def _int(value: str, key: str) -> Optional[int]:
    if value == "":
        return None
    if not re.fullmatch(r"-?\d+", value):
        raise TrackerError(f"`{key}: {value}` is not a whole number")
    return int(value)


def parse(text: str) -> Tracker:
    head, body = _split(text)
    fm: dict = {}
    for n, line in enumerate(head.split("\n"), 1):
        if not line.strip():
            continue
        m = _KEY.match(line)
        if not m or line[:1] in (" ", "\t", "-", "#"):
            raise TrackerError(f"frontmatter line {n} is not `key: value`: {line!r}")
        key = m.group(1)
        if key not in FIELDS:
            raise TrackerError(f"`{key}` is not a tracker field")
        if key in fm:
            raise TrackerError(f"`{key}` appears twice")
        fm[key] = _scalar_in(m.group(2), key)
    missing = [k for k in REQUIRED if k not in fm]
    if missing:
        raise TrackerError(f"missing {', '.join(f'`{k}`' for k in missing)}")
    if fm["kind"] != KIND:
        raise TrackerError(f"`kind: {fm['kind']}` is not `{KIND}`")
    sections = _sections(body)
    return Tracker(
        title=fm["title"], project=fm["project"], status=fm["status"],
        task=fm.get("task") or None, importance=_int(fm.get("importance", ""), "importance"),
        opened=fm["opened"], updated=fm["updated"], closed=fm.get("closed") or None,
        issue=_int(fm.get("issue", ""), "issue"), design=fm.get("design") or None,
        sensitivity=fm.get("sensitivity") or None,
        objective=sections["Objective"], state=sections["State"],
        next_steps=sections["Next"], outcome=sections["Outcome"],
    )


# --- the schema ---------------------------------------------------------------

def _is_date(value: Optional[str]) -> bool:
    if not value or not _DATE.match(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_component(value: str) -> bool:
    return (value not in (".", "..") and "/" not in value and "\\" not in value
            and "\x00" not in value and value == value.strip())


def findings(t: Tracker) -> list[str]:
    """What the schema refuses in a parsed tracker; empty when it holds."""
    out = []
    if not t.title.strip():
        out.append("`title` is empty")
    if not t.project:
        out.append("`project` is empty")
    for key in ("project", "task"):
        value = getattr(t, key)
        if value and not _is_component(value):
            out.append(f"`{key}: {value}` is not a single path component")
    if t.status not in STATUSES:
        out.append(f"`status: {t.status}` is not one of {', '.join(STATUSES)}")
    dates_ok = True
    for key in ("opened", "updated"):
        if not _is_date(getattr(t, key)):
            out.append(f"`{key}` is not a date (YYYY-MM-DD)")
            dates_ok = False
    if t.closed is not None and not _is_date(t.closed):
        out.append("`closed` is not a date (YYYY-MM-DD)")
        dates_ok = False
    if t.status in FINAL and t.closed is None:
        out.append(f"a `{t.status}` tracker has no `closed` date")
    if t.status in STATUSES and t.status not in FINAL and t.closed is not None:
        out.append(f"`closed` is set on a `{t.status}` tracker")
    if dates_ok:
        if t.updated < t.opened:
            out.append("`updated` is before `opened`")
        if t.closed is not None and t.closed < t.opened:
            out.append("`closed` is before `opened`")
    if t.importance is not None and not 1 <= t.importance <= 10:
        out.append(f"`importance: {t.importance}` is outside 1-10")
    if t.issue is not None and t.issue < 1:
        out.append(f"`issue: {t.issue}` is not an issue number")
    if not t.objective:
        out.append("the Objective is empty")
    if t.status in FINAL and not t.outcome:
        out.append(f"a `{t.status}` tracker has no Outcome")
    return out


def check_text(text: str) -> list[str]:
    try:
        return findings(parse(text))
    except TrackerError as exc:
        return [str(exc)]


def new(*, title: str, project: str, objective: str, next_step: str, today: str,
        task: Optional[str] = None, importance: Optional[int] = None,
        issue: Optional[int] = None, design: Optional[str] = None) -> Tracker:
    """A tracker at open: `queued`, State not started, Next the first step."""
    t = Tracker(title=title, project=project, task=task, status="queued",
                importance=importance, opened=today, updated=today, issue=issue,
                design=design, objective=objective, state=NOT_STARTED,
                next_steps=next_step)
    problems = findings(t)
    if problems:
        raise TrackerError("; ".join(problems))
    return t


def transition(t: Tracker, to: str, *, today: str, state: Optional[str] = None,
               next_steps: Optional[str] = None, outcome: Optional[str] = None) -> Tracker:
    """The tracker after `t.status` becomes `to`, or TrackerError when the table
    does not allow it. `updated` becomes today; a final status stamps `closed`
    and needs an Outcome.

    `to` equal to a non-final `t.status` is a rewrite in place: the State and
    Next given replace the ones on file, `updated` becomes today, and nothing
    else changes. A rewrite names `state`, `next_steps` or both, so a bare call
    cannot stamp `updated` and read as progress, and it never names an Outcome,
    which is written once, at close. A final tracker is not rewritten."""
    if to not in STATUSES:
        raise TrackerError(f"`{to}` is not a status")
    if not _is_date(today):
        raise TrackerError(f"`{today}` is not a date (YYYY-MM-DD)")
    allowed = TRANSITIONS.get(t.status, ())
    if to == t.status:
        if to in FINAL:
            raise TrackerError(f"a `{to}` tracker is final and is not rewritten")
        if outcome is not None:
            raise TrackerError(f"a rewrite under `{to}` does not write the Outcome, "
                               "which is written once, at close")
        if state is None and next_steps is None:
            raise TrackerError(f"a rewrite under `{to}` names State, Next or both")
    elif to not in allowed:
        may = ", ".join(f"`{s}`" for s in allowed) or "nothing, because it is final"
        raise TrackerError(f"a `{t.status}` tracker cannot become `{to}`; it may become {may}")
    changes: dict = {"status": to, "updated": today}
    if state is not None:
        changes["state"] = state
    if next_steps is not None:
        changes["next_steps"] = next_steps
    if outcome is not None:
        changes["outcome"] = outcome
    if to in FINAL:
        changes["closed"] = today
        if not _clean(outcome if outcome is not None else t.outcome):
            raise TrackerError(f"closing a tracker as `{to}` writes its Outcome")
    return dataclasses.replace(t, **changes)


# --- files --------------------------------------------------------------------

def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read(path: Path) -> tuple[Tracker, str]:
    """The tracker at `path` and the hash of the text it was read from."""
    text = Path(path).read_text(encoding="utf-8")
    return parse(text), content_hash(text)


def write(path: Path, t: Tracker, *, expected_hash: Optional[str] = None) -> Path:
    """Replace `path` with the rendered tracker, atomically.

    With `expected_hash`, refuses when the file no longer holds the text that
    hash was taken from: a session rewriting a tracker another writer changed
    would erase that change."""
    path = Path(path)
    problems = findings(t)
    if problems:
        raise TrackerError("; ".join(problems))
    if expected_hash is not None:
        current = content_hash(path.read_text(encoding="utf-8")) if path.exists() else None
        if current != expected_hash:
            raise ChangedError(f"{path} changed since it was read; read it again")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(render(t))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


# --- command line -------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tracker.py", description="The tracker schema: new, show, transition, check.")
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", help="render a tracker at `queued`")
    n.add_argument("--title", required=True)
    n.add_argument("--project", required=True)
    n.add_argument("--task", default=None, help="omit for a project's own tracker")
    n.add_argument("--objective", required=True, help="the plan's Goal")
    n.add_argument("--next", dest="next_step", required=True, help="the first step")
    n.add_argument("--importance", type=int, default=None)
    n.add_argument("--issue", type=int, default=None)
    n.add_argument("--design", default=None)
    n.add_argument("--today", default=None, help="YYYY-MM-DD (default: today)")
    n.add_argument("--out", default=None, help="write here instead of stdout; refuses an existing file")

    s = sub.add_parser("show", help="print a tracker's fields and sections as JSON")
    s.add_argument("path")

    t = sub.add_parser("transition", help="change a tracker's status in place, or rewrite "
                                          "State and Next under its own status")
    t.add_argument("path")
    t.add_argument("--to", required=True, choices=STATUSES,
                   help="the new status, or the tracker's own to rewrite State and Next")
    t.add_argument("--state", default=None)
    t.add_argument("--next", dest="next_steps", default=None)
    t.add_argument("--outcome", default=None)
    t.add_argument("--today", default=None, help="YYYY-MM-DD (default: today)")

    c = sub.add_parser("check", help="report schema findings")
    c.add_argument("paths", nargs="+")
    return p


def main(argv: Optional[list] = None) -> int:
    args = _build_parser().parse_args(argv)
    today = getattr(args, "today", None) or date.today().isoformat()
    try:
        if args.cmd == "new":
            t = new(title=args.title, project=args.project, task=args.task,
                    objective=args.objective, next_step=args.next_step, today=today,
                    importance=args.importance, issue=args.issue, design=args.design)
            if args.out is None:
                sys.stdout.write(render(t))
                return 0
            out = Path(args.out)
            if out.exists():
                print(f"tracker: {out} exists; a tracker is opened once", file=sys.stderr)
                return 1
            print(write(out, t))
            return 0
        if args.cmd == "show":
            tracker, _hash = read(Path(args.path))
            print(json.dumps(tracker.to_dict(), indent=2, ensure_ascii=False))
            return 0
        if args.cmd == "transition":
            path = Path(args.path)
            before, digest = read(path)
            after = transition(before, args.to, today=today, state=args.state,
                               next_steps=args.next_steps, outcome=args.outcome)
            write(path, after, expected_hash=digest)
            print(f"{path}: {before.status} -> {after.status}")
            return 0
        bad = 0
        for raw in args.paths:
            try:
                text = Path(raw).read_text(encoding="utf-8")
            except OSError as exc:
                print(f"{raw}: {exc}")
                bad += 1
                continue
            for finding in check_text(text):
                print(f"{raw}: {finding}")
                bad += 1
        return 1 if bad else 0
    except TrackerError as exc:
        print(f"tracker: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"tracker: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
