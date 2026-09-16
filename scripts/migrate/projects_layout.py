#!/usr/bin/env python3
"""projects_layout — the projects half of the vault layout (agentm-vault plan 10).

The code half ships first: the resolver reads both layouts, places a new plan in
a numbered task and refuses a bare call on a project that keeps its plans in
tasks. This script moves the data to match — `_harness/` dissolves by a table
rather than a judgment, every plan unit becomes `tasks/NNN-<verb-slug>/` with a
`plan.md`, a `progress.md` and a `tracker.md`, and the rest of each harness
directory lands in the project skeleton the design names.

  dry run   Reads the projects that carry a `_harness/` (never writes): what each
            file maps to, the slug derived for each plan unit, the status each
            spelling maps onto, the trackers to be written and the ones already
            written that move with their plan. Prints a count per table row and
            the manifest — one line per move with its command and its reverse —
            and records the plan under the engine state directory.
  --apply   The recorded plan, refused unless the source listing still matches
            it, the count is confirmed, nothing is staged in the vault's index,
            no writer is live, and every open task's tracker already carries a
            State and a Next. Each file then moves in one `git mv`, so the index
            moves with the tree; trackers are written; every link that named a
            moved file becomes a path link with its words kept.
  --finish  After the deploy and the reindex: every post-condition read from the
            vault — no `_harness/` under any project, every task directory
            numbered and carrying a tracker in one of the five statuses, the
            project skeleton in place, and the journal's counts still true. The
            marker is written only when all of them hold.

`--revert RUN_ID` puts back every move a run made, newest first, refused when
the tree no longer holds what the run left (a destination gone, a source back,
or a rewritten file whose digest no longer matches what the run wrote).

  python3 scripts/migrate/projects_layout.py [--vault VAULT] [--slugs FILE]
  python3 scripts/migrate/projects_layout.py --apply --plan PLAN --confirm-count N
  python3 scripts/migrate/projects_layout.py --finish --plan PLAN
  python3 scripts/migrate/projects_layout.py --revert RUN_ID
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
_SCRIPTS = _REPO / "scripts"
_TOOLKIT = _REPO / "harness" / "skills" / "memory" / "scripts"
for _p in (str(_SCRIPTS), str(_TOOLKIT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tracker as tk  # noqa: E402

STAGE = "projects-layout"
MARKER_REL = "agent/memory/.projects-migration-complete"
# The root-side half of a worktree bind, as `worktree_marker.py` writes it: one
# file per slug in the main clone's `.harness/`, holding the worktree's path.
_POINTER_PREFIX = "worktree-for-"
PROJECTS = "projects"
HARNESS = "_harness"
TASKS = "tasks"
# The finished-work folder at both levels (session 5); there is no `archive/` in
# the new skeleton, so what S4 sent to `archive/` lands here.
COMPLETED = "completed"
DESK = "desk"
ARCHIVED_PROJECTS = "_archive"

# The five statuses a tracker may carry, and what today's nine spellings mean.
# A spelling is read by its first word: the prose after it is a close-out
# narrative, which belongs in the Outcome, not in the status.
STATUS_WORDS = {
    "done": "done", "complete": "done", "completed": "done", "shipped": "done",
    "closed": "done", "superseded": "dropped", "withdrawn": "dropped",
    "dropped": "dropped", "abandoned": "dropped", "retired": "dropped",
    "planning": "queued", "queued": "queued", "staged": "queued", "draft": "queued",
    "none": "queued", "planned": "queued",
    "active": "active", "in-progress": "active", "in": "active",
    "building": "active", "started": "active",
    "parked": "parked", "paused": "parked", "blocked": "parked", "on": "parked",
}
OPEN_STATUSES = ("active", "parked")

_STATUS_LINE = re.compile(
    r"^\s*(?:\*\*Status:?\*\*|Status:)\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_GOAL = re.compile(r"^##+\s*Goal\s*$(.*?)(?=^##+\s|\Z)", re.IGNORECASE | re.MULTILINE | re.DOTALL)
_CREATED = re.compile(r"^\s*(?:\*\*Created:?\*\*|Created:)\s*(\d{4}-\d{2}-\d{2})",
                      re.IGNORECASE | re.MULTILINE)
# The slug is optional: one archived plan from the first month carries its date
# and nothing else, and its name has to come from its title like a singleton's.
_ARCHIVE_NAME = re.compile(r"^PLAN\.archive\.(\d{8})(?:-(.+))?\.md$")
_ARCHIVE_PROGRESS = re.compile(r"^progress\.archive\.(\d{8})(?:-(.+))?\.md$")
_TITLE = re.compile(r"^#\s+(?:Plan:\s*)?(.+?)\s*$", re.MULTILINE)
# A title's own name ends where its subtitle begins.
_TITLE_TAIL = re.compile(r"\s+[—–-]\s+.*$|\s*\(.*$")
_PLAN_NAME = re.compile(r"^PLAN-(.+)\.md$")
_PROGRESS_NAME = re.compile(r"^progress-(.+)\.md$")
_TRACKER_NAME = re.compile(r"^tracker-(.+)\.md$")
_TASK_DIR = re.compile(r"^(\d{3})-(.+)$")

# The machine files a project's vault directory carries. The repo reads its own
# copies from its own `.harness/`, which this migration never walks —
# `_read_project_mode` reads `<project_root>/.harness/.project-mode` and nothing
# reads the vault's twin of it — so these are copies, not state, and they move to
# `desk/` with the rest of what you do not open. `machinery_doctor.py:951` already
# reads `project.json` from both homes for exactly this move.
MACHINE_FILES = ("init.sh", "verify.sh", "verify.ps1", "known-migrations.md",
                 ".project-mode", ".migrated-from-pre-v4.1", "project.json")

# What `desk/` holds: what you do not open.
DESK_FILES = ("board-items.json", "features.json", "corrections.md")
DESK_DIRS = ("labels",)

# Directories the link rewrite never walks.
SKIP_DIRS = {".git", ".obsidian", ".trash", "node_modules", "__pycache__"}

# A verb-first slug starts with one of these. A derived slug that does not is
# listed for the operator's correction rather than guessed at.
VERBS = (
    "add", "audit", "backfill", "build", "carve", "close", "consolidate",
    "convert", "cut", "deprecate", "derive", "design", "dissolve", "document",
    "extend", "fill", "finish", "fix", "fold", "harden", "install", "land",
    "make", "measure", "merge", "migrate", "move", "name", "open", "pair",
    "plan", "probe", "prove", "publish", "purge", "refine", "reindex", "release",
    "rename", "repoint", "research", "retire", "rewrite", "route", "run",
    "scaffold", "seed", "ship", "shrink", "split", "stage", "survey", "sweep",
    "teach", "trim", "tune", "unbundle", "verify", "widen", "wire", "write",
)


class Refused(Exception):
    """The run cannot do what it was asked, and wrote nothing."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load(path: Path) -> str:
    """A note's text, decoded from its bytes rather than read in text mode.

    Every file this run rewrites is journaled by the digest of what it wrote, and
    a digest only names a file if it is taken over the bytes on disk. Text mode
    translates the line endings on Windows in both directions, so a text-mode
    read-transform-write moves the bytes out from under the digest and the revert
    then refuses to restore anything (CI, 2026-09-16). Bytes in, bytes out."""
    return path.read_bytes().decode("utf-8")


def _parsed_text(path: Path) -> str:
    """A note's text with its line endings normalised, for parsing.

    Two different jobs were one function and should not have been. Rewriting a
    file wants its bytes back unchanged, so `_load` decodes and does not
    translate. *Parsing* one wants line endings out of the way: `tracker.py`
    refuses a CRLF tracker outright — `no frontmatter block`, because the
    delimiter line does not match before any heading is reached — and the gate
    would report a perfectly good tracker as unreadable. A tracker written on a
    CRLF host is a real tracker; the gate reads it."""
    return _load(path).replace("\r\n", "\n")


def _store(path: Path, text: str) -> bytes:
    """Write `text` as the bytes it is, and answer them, so the caller journals
    the digest of what is now on disk."""
    data = text.encode("utf-8")
    path.write_bytes(data)
    return data


def _git(vault: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True)


def _rel(path: Path, root: Path) -> str:
    """`path` under `root` as one vault-relative string, always forward-slashed.

    Every path in a plan, a manifest, a journal and the table itself is split on
    `/`, and `str(Path.relative_to())` spells a separator the way the host does —
    backslashes on Windows, where the split then finds one field and the table
    reads nothing. The migration runs on one machine, but its tests run on three,
    so the separator is normalised once, here, where a path becomes text."""
    return "/".join(path.relative_to(root).parts)


def _read(path: Path, limit: int = 0) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[:limit] if limit else text


# ── statuses ───────────────────────────────────────────────────────────────

def map_status(raw: Optional[str]) -> str:
    """One of the five statuses, from a Status line in any of today's spellings.

    Read by the first word, lowercased and stripped of the markdown a hand-edit
    left on it. Everything after that word is a close-out narrative — it goes to
    the Outcome, never to the status. An unreadable or absent line reads `done`
    for an archived plan and `queued` otherwise; the caller supplies the default.
    """
    if not raw:
        return ""
    word = re.split(r"[\s,(*.—–-]+", raw.strip().strip("*").lower(), maxsplit=1)[0]
    if word in STATUS_WORDS:
        return STATUS_WORDS[word]
    # "in progress" / "in-progress" and "on hold" arrive as two words.
    two = re.sub(r"[^a-z]+", "-", raw.strip().lower())[:12]
    for key, value in STATUS_WORDS.items():
        if two.startswith(key):
            return value
    return ""


def status_of(path: Path, *, default: str) -> tuple:
    """`(mapped, raw)` for a plan file: what its Status line says and what that
    maps onto. A file with no line maps onto `default`."""
    m = _STATUS_LINE.search(_read(path, 4000))
    raw = m.group(1).strip() if m else ""
    return (map_status(raw) or default, raw)


# ── slugs ──────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", (text or "").lower()).strip("-")


def title_of(path: Path) -> str:
    """A plan's own name, from its first heading with the `Plan:` prefix and any
    subtitle after a dash or a parenthesis dropped."""
    m = _TITLE.search(_read(path, 4000))
    return _TITLE_TAIL.sub("", m.group(1)).strip() if m else ""


def derive_slug(source: str, title: str = "", fallback: str = "") -> tuple:
    """`(slug, needs_review)` for a plan unit.

    The derivation is mechanical and visible: the plan's own slug, with a leading
    date and a `PLAN-` / `PLAN.archive.` prefix removed. A singleton has no slug
    in its name, and neither does the one archived plan that carries a date
    alone, so both take the slug their title gives — the design's own rule for
    the singleton. A title that slugifies to nothing leaves the project's name.

    Whether a slug reads verb-first is a judgment, so the script does not invent
    one — it marks the slug for the operator's correction instead, and `--slugs`
    carries the corrections back in. Guessing a verb would put a wrong name on a
    directory that is then the task's name for good."""
    slug = None
    m = _ARCHIVE_NAME.match(source) or _ARCHIVE_PROGRESS.match(source)
    if m:
        slug = m.group(2)
    else:
        m = _PLAN_NAME.match(source)
        if m:
            slug = m.group(1)
    from_title = slug is None
    slug = _slugify(slug if slug else title) or _slugify(fallback)
    first = slug.split("-", 1)[0]
    return slug, (from_title or first not in VERBS)


def _creation_key(path: Path, *, archived_date: Optional[str]) -> str:
    """When a plan unit was created, for the order the prefixes follow.

    An archived plan's date is the one in its filename; anything else takes its
    `Created:` line, and a unit with neither takes its file mtime. All three read
    as `YYYY-MM-DD`, so one sort orders the whole project."""
    if archived_date:
        return f"{archived_date[:4]}-{archived_date[4:6]}-{archived_date[6:]}"
    m = _CREATED.search(_read(path, 4000))
    if m:
        return m.group(1)
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d")
    except OSError:
        return "9999-12-31"


# ── the table ──────────────────────────────────────────────────────────────

def _row_for(rel: str, name: str) -> str:
    """Which table row a file under `_harness/` belongs to, by its path.

    First match wins and every file matches exactly one row, which is what makes
    "every file moves once, by the table" a property rather than an intention: a
    file that matches no row is listed as unmapped and `--apply` refuses.
    """
    parts = rel.split("/")
    top = parts[0]
    if name in MACHINE_FILES and len(parts) == 1:
        return "machine"
    if top == "queued-plans":
        return "queued-plan" if name.startswith("PLAN") else "queued-other"
    if top == "archive":
        if _ARCHIVE_NAME.match(name):
            return "archived-plan"
        if _ARCHIVE_PROGRESS.match(name) or name.startswith("progress"):
            return "archived-progress"
        return "archived-record"
    if top == "designs":
        return "design"
    if top in ("research", "vault-perfection") or top.startswith("research-"):
        return "research"
    if top in DESK_DIRS:
        return "desk"
    if len(parts) > 1:
        # Any other directory under `_harness/` is a record bundle: it keeps its
        # own directory under `research/`, where a bundle is read.
        return "research"
    if _PLAN_NAME.match(name) or name == "PLAN.md":
        return "plan"
    if _PROGRESS_NAME.match(name) or name == "progress.md":
        return "progress"
    if _TRACKER_NAME.match(name) or name == "tracker.md":
        return "tracker"
    if "handoff" in name.lower():
        return "handoff"
    if name.startswith(("BRIEF-", "PROMPT-", "PROBE-")):
        return "brief"
    if name.startswith(("RESEARCH-", "REFERENCE-")):
        return "research"
    if name.startswith("FOLLOWUP"):
        return "followups"
    if name.startswith("ROADMAP"):
        return "roadmap"
    if name in DESK_FILES or name.startswith("board-items.json."):
        return "desk"
    return "record"


# ── the plan ───────────────────────────────────────────────────────────────

def live_projects(vault: Path) -> list:
    """Every project under `projects/`, in the order the disk lists.

    Wider than `harnessed_projects`: a project with no `_harness/` still has a
    charter, and the skeleton's name for it is `charter.md`."""
    root = vault / PROJECTS
    if not root.is_dir():
        raise Refused(f"{root} is not a directory; is {vault} the vault root?")
    return sorted(p.name for p in root.iterdir()
                  if p.is_dir() and not p.name.startswith("_"))


def harnessed_projects(vault: Path) -> list:
    """Every project that carries a `_harness/`, in the order the disk lists."""
    root = vault / PROJECTS
    if not root.is_dir():
        raise Refused(f"{root} is not a directory; is {vault} the vault root?")
    return sorted(p.name for p in root.iterdir()
                  if p.is_dir() and not p.name.startswith("_") and (p / HARNESS).is_dir())


def _harness_files(vault: Path, project: str) -> list:
    """Every file under a project's `_harness/`, vault-relative, sorted."""
    h = vault / PROJECTS / project / HARNESS
    out = []
    for p in sorted(h.rglob("*")):
        if p.is_file() and not any(part in SKIP_DIRS for part in p.relative_to(h).parts):
            out.append(_rel(p, vault))
    return out


def _units(vault: Path, project: str, files: list, slugs: dict) -> list:
    """The project's plan units: one per plan, with its progress log, its tracker
    when the crickets release already wrote one, and the number its place in
    creation order gives it.

    A unit is named once, here, and everything else that belongs to it — its
    brief, its progress log, the tracker beside it — is addressed through the
    name this gives it. That is what keeps the three files of one task together
    when the table is applied one file at a time."""
    h = f"{PROJECTS}/{project}/{HARNESS}/"
    by_name = {rel[len(h):]: rel for rel in files}
    units = []

    def add(kind, plan_rel, source_key, *, archived_date=None, default_status):
        plan_path = vault / plan_rel
        derived, review = derive_slug(Path(source_key).name, title_of(plan_path),
                                      fallback=project)
        slug = slugs.get(f"{project}/{source_key}") or slugs.get(source_key) or derived
        status, raw = status_of(plan_path, default=default_status)
        if kind == "archived" and status != "dropped":
            # Being in `archive/` is what closes a plan, and one archived plan
            # still carries `in-progress` on a Status line nobody updated at
            # close. The file's place outranks its line; a withdrawal is the one
            # word that still means something, because `dropped` is also final.
            status = "done"
        goal = ""
        m = _GOAL.search(_read(plan_path, 20000))
        if m:
            goal = " ".join(m.group(1).split())[:600]
        units.append({
            "kind": kind, "project": project, "plan": plan_rel, "source": source_key,
            "slug": slug, "derived": derived, "needs_review": review and not slugs.get(source_key),
            "status": status, "status_raw": raw, "goal": goal,
            "created": _creation_key(plan_path, archived_date=archived_date),
            "progress": None, "tracker": None, "brief": None,
        })

    for name, rel in sorted(by_name.items()):
        leaf = Path(name).name
        if name == "PLAN.md":
            add("singleton", rel, name, default_status="active")
        elif _PLAN_NAME.match(name):
            add("named", rel, name, default_status="active")
        elif name.startswith("queued-plans/") and _PLAN_NAME.match(leaf):
            add("queued", rel, name, default_status="queued")
        elif name.startswith("archive/"):
            m = _ARCHIVE_NAME.match(leaf)
            if m:
                add("archived", rel, name, archived_date=m.group(1), default_status="done")

    # Number by creation order; a tie keeps the source order, so two units made
    # the same day never swap places between a dry run and the apply.
    units.sort(key=lambda u: (u["created"], u["source"]))
    for i, u in enumerate(units, start=1):
        u["number"] = f"{i:03d}"
        u["task"] = f"{u['number']}-{u['slug']}"
        u["dir"] = f"{PROJECTS}/{project}/{TASKS}/{u['task']}"

    # Attach each unit's progress log and its already-written tracker.
    index = {u["source"]: u for u in units}
    for name, rel in sorted(by_name.items()):
        leaf = Path(name).name
        owner = None
        if name == "progress.md":
            owner = index.get("PLAN.md")
        elif _PROGRESS_NAME.match(name):
            owner = index.get(f"PLAN-{_PROGRESS_NAME.match(name).group(1)}.md")
        elif name == "tracker.md":
            owner = index.get("PLAN.md")
        elif _TRACKER_NAME.match(name):
            owner = index.get(f"PLAN-{_TRACKER_NAME.match(name).group(1)}.md")
        elif name.startswith("archive/"):
            m = _ARCHIVE_PROGRESS.match(leaf)
            if m:
                twin = ("PLAN.archive." + m.group(1)
                        + (f"-{m.group(2)}" if m.group(2) else "") + ".md")
                # The twin usually sits beside its plan; when it does not, it is
                # still the one archived plan with that file name.
                owner = next((u for u in units if u["kind"] == "archived"
                              and Path(u["source"]).name == twin), None)
        if owner is not None:
            slot = "tracker" if leaf.startswith("tracker") else "progress"
            if owner[slot] is None:
                owner[slot] = rel
    return units


def _brief_owner(units: list, name: str) -> Optional[dict]:
    """The unit a brief belongs to, by the slug in its own name. A brief whose
    slug names no unit has no task to sit in and goes to `desk/briefs/`."""
    stem = re.sub(r"^(BRIEF|PROMPT|PROBE)-", "", Path(name).stem)
    key = re.sub(r"[^a-z0-9-]+", "-", stem.lower()).strip("-")
    for u in units:
        if key and (u["slug"] == key or u["derived"] == key):
            return u
    return None


def _destination(vault: Path, project: str, rel: str, row: str, units: list,
                 by_source: dict) -> Optional[str]:
    """Where one file lands, given its row. None means it stays where it is."""
    h = f"{PROJECTS}/{project}/{HARNESS}/"
    name = rel[len(h):]
    leaf = Path(name).name
    base = f"{PROJECTS}/{project}"
    if row == "machine":
        return f"{base}/{DESK}/{leaf}"
    unit = by_source.get(name)
    if row in ("plan", "queued-plan", "archived-plan") and unit is not None:
        return f"{unit['dir']}/plan.md"
    if row in ("progress", "archived-progress"):
        owner = next((u for u in units if u["progress"] == rel), None)
        if owner is not None:
            return f"{owner['dir']}/progress.md"
        # A progress log with no plan beside it belongs where its plan went; when
        # there is none at all it is a finished record.
        stem = re.sub(r"^progress[-.]", "", Path(name).stem)
        stem = re.sub(r"^archive\.\d{8}-", "", stem)
        owner = next((u for u in units if u["derived"] == stem or u["slug"] == stem), None)
        return f"{owner['dir']}/progress.md" if owner else f"{base}/{COMPLETED}/{leaf}"
    if row == "tracker":
        owner = next((u for u in units if u["tracker"] == rel), None)
        return f"{owner['dir']}/tracker.md" if owner else f"{base}/{DESK}/{leaf}"
    if row == "design":
        return f"{base}/designs/{name.split('/', 1)[1]}"
    if row == "research":
        if name.startswith(("RESEARCH-", "REFERENCE-")):
            return f"{base}/research/{leaf}"
        top, rest = (name.split("/", 1) + [""])[:2]
        # `research-filing-v2/` names its bundle in its own directory name;
        # `research/` is already the destination and names no bundle, so
        # re-prefixing it would give `research/research/`.
        bundle = "" if top == "research" else re.sub(r"^research-", "", top) + "/"
        return f"{base}/research/{bundle}{rest or leaf}"
    if row == "brief":
        owner = _brief_owner(units, leaf)
        if owner is None:
            return f"{base}/{DESK}/briefs/{leaf}"
        # A brief, a prompt and a probe for one task are three different papers,
        # so each keeps its own name inside the task rather than all three
        # claiming `brief.md` and colliding.
        kind = {"BRIEF": "brief", "PROMPT": "prompt", "PROBE": "probe"}[leaf.split("-", 1)[0]]
        return f"{owner['dir']}/{kind}.md"
    if row == "followups":
        return f"{base}/followups.md" if leaf == "FOLLOWUPS.md" else f"{base}/{COMPLETED}/{leaf}"
    if row == "roadmap":
        return f"{base}/roadmap.md" if leaf == "ROADMAP-MASTER.md" else f"{base}/{COMPLETED}/{leaf}"
    if row == "desk":
        return f"{base}/{DESK}/{name}" if "/" in name else f"{base}/{DESK}/{leaf}"
    if row == "queued-other":
        return f"{base}/{DESK}/briefs/{leaf}"
    if row in ("handoff", "record"):
        return f"{base}/{COMPLETED}/{leaf}"
    if row == "archived-record":
        rest = name.split("/", 1)[1]
        if rest.startswith("designs/"):
            return f"{base}/designs/{rest.split('/', 1)[1]}"
        return f"{base}/{COMPLETED}/{rest}"
    return None


def build_plan(vault, slugs: Optional[dict] = None) -> dict:
    """What every file under every `_harness/` becomes. Reads only."""
    vault = Path(vault)
    slugs = slugs or {}
    if not vault.is_dir():
        raise Refused(f"{vault} is not a directory")
    if _git(vault, "rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
        raise Refused(f"{vault} is not a git work tree; the moves must take the index with them")
    projects, moves, trackers, unmapped = [], [], [], []
    counts, statuses = {}, {}
    for project in harnessed_projects(vault):
        files = _harness_files(vault, project)
        units = _units(vault, project, files, slugs)
        by_source = {u["source"]: u for u in units}
        h = f"{PROJECTS}/{project}/{HARNESS}/"
        for rel in files:
            name = rel[len(h):]
            row = _row_for(name, Path(name).name)
            counts[row] = counts.get(row, 0) + 1
            dst = _destination(vault, project, rel, row, units, by_source)
            if dst is None:
                unmapped.append(rel)
                continue
            moves.append({"project": project, "row": row, "src": rel, "dst": dst})
        for u in units:
            statuses.setdefault(u["status_raw"] or "(no line)", u["status"])
            trackers.append({
                "project": project, "task": u["task"], "kind": u["kind"],
                "path": f"{u['dir']}/tracker.md", "status": u["status"],
                "title": u["slug"].replace("-", " "), "objective": u["goal"],
                "closed": u["created"] if u["status"] == "done" else None,
                "source": "moved" if u["tracker"] else "written",
                "from": u["tracker"], "plan": u["plan"],
                "needs_state": u["status"] in OPEN_STATUSES,
            })
        projects.append({
            "project": project, "files": len(files), "units": len(units),
            "slugs": [{"source": u["source"], "derived": u["derived"], "slug": u["slug"],
                       "task": u["task"], "status": u["status"], "raw": u["status_raw"],
                       "needs_review": u["needs_review"]} for u in units],
        })
    # `_index.md` becomes `charter.md` in **every** project, not only the ones
    # that carry a `_harness/`: the charter is the project skeleton's file and
    # three live projects have no harness directory at all. Leaving those on the
    # old name would give the vault two spellings of the same thing.
    for project in live_projects(vault):
        index = f"{PROJECTS}/{project}/_index.md"
        if (vault / index).is_file():
            counts["charter"] = counts.get("charter", 0) + 1
            moves.append({"project": project, "row": "charter", "src": index,
                          "dst": f"{PROJECTS}/{project}/charter.md"})

    # Every archived project moves whole, one move per file, so the manifest
    # names each one and the reverse puts each one back.
    arch = vault / PROJECTS / ARCHIVED_PROJECTS
    if arch.is_dir():
        for p in sorted(arch.rglob("*")):
            if p.is_file():
                rel = _rel(p, vault)
                counts["archived-project"] = counts.get("archived-project", 0) + 1
                # A loose file sitting directly in `_archive/` belongs to no
                # project, so it keeps its own name under `completed/` rather
                # than indexing past the end of its own path.
                tail = _rel(p, arch)
                moves.append({"project": ARCHIVED_PROJECTS, "row": "archived-project", "src": rel,
                              "dst": f"{PROJECTS}/{COMPLETED}/{tail}"})
    destinations = {}
    for m in moves:
        destinations.setdefault(m["dst"], []).append(m["src"])
    collisions = {d: s for d, s in destinations.items() if len(s) > 1}
    return {
        "vault": str(vault), "projects": projects, "moves": moves, "trackers": trackers,
        "unmapped": unmapped, "collisions": collisions, "counts": counts,
        "statuses": statuses,
        "sources_sha": _sha("\n".join(m["src"] for m in moves).encode("utf-8")),
        "open_trackers": [t["path"] for t in trackers if t["needs_state"]],
        "totals": {"moves": len(moves), "trackers_written": sum(1 for t in trackers if t["source"] == "written"),
                   "trackers_moved": sum(1 for t in trackers if t["source"] == "moved"),
                   "projects": len(projects)},
    }


def _signature(plan: dict) -> str:
    return json.dumps({"sources_sha": plan["sources_sha"],
                       "moves": [[m["src"], m["dst"]] for m in plan["moves"]]}, sort_keys=True)


def manifest(plan: dict) -> list:
    """One line per move, with its command and the command that puts it back."""
    vault = Path(plan["vault"])
    v = str(vault)
    out = []
    for m in plan["moves"]:
        out.append(f"- `{m['src']}` becomes `{m['dst']}` ({m['row']}): "
                   f'`git -C "{v}" mv "{m["src"]}" "{m["dst"]}"`. '
                   f'Reverse: `git -C "{v}" mv "{m["dst"]}" "{m["src"]}"`.')
    for t in plan["trackers"]:
        if t["source"] == "written":
            out.append(f"- `{t['path']}` is written at `{t['status']}` for task "
                       f"`{t['task']}`. Reverse: `--revert`, or "
                       f'`git -C "{v}" rm -f "{t["path"]}"`.')
    return out


def print_summary(plan: dict, out=sys.stdout) -> None:
    for p in plan["projects"]:
        print(f"  {p['project']:22s} {p['files']:4d} files, {p['units']:3d} plan units", file=out)
    print("  rows:", file=out)
    for row, n in sorted(plan["counts"].items()):
        print(f"    {row:20s} {n:5d}", file=out)
    t = plan["totals"]
    print(f"  moves: {t['moves']:,}; trackers written {t['trackers_written']}, "
          f"moved {t['trackers_moved']}; projects {t['projects']}", file=out)
    review = [(p["project"], s) for p in plan["projects"] for s in p["slugs"] if s["needs_review"]]
    print(f"  slugs needing a verb-first correction: {len(review)}", file=out)
    for project, s in review[:40]:
        print(f"    {project}/{s['source']} -> {s['task']}", file=out)
    if len(review) > 40:
        print(f"    ... and {len(review) - 40} more (the full list is in the recorded plan)", file=out)
    print(f"  status spellings: {len(plan['statuses'])}", file=out)
    for raw, mapped in sorted(plan["statuses"].items())[:40]:
        print(f"    {mapped:8s} <- {raw[:90]!r}", file=out)
    if plan["open_trackers"]:
        print(f"  open tasks needing a hand-written State and Next: {len(plan['open_trackers'])}", file=out)
        for path in plan["open_trackers"]:
            print(f"    {path}", file=out)
    if plan["unmapped"]:
        print(f"  UNMAPPED ({len(plan['unmapped'])}) — the table names no destination; "
              f"--apply refuses while any remain:", file=out)
        for rel in plan["unmapped"][:40]:
            print(f"    {rel}", file=out)
    if plan["collisions"]:
        print(f"  COLLISIONS ({len(plan['collisions'])}) — two files want one destination; "
              f"--apply refuses while any remain:", file=out)
        for dst, srcs in list(plan["collisions"].items())[:40]:
            print(f"    {dst} <- {', '.join(srcs)}", file=out)
    print("  manifest:", file=out)
    for line in manifest(plan)[:10]:
        print(f"    {line}", file=out)
    print(f"    ... {len(manifest(plan))} manifest lines in all (recorded in the plan file)", file=out)


# ── the writers that must be quiet ─────────────────────────────────────────

def live_writers() -> list:
    """Who could write into a project while its files are in flight."""
    out = []
    uid = os.getuid() if hasattr(os, "getuid") else None
    for job in ("com.agentm.daemon", "com.agentm.runner"):
        if uid is not None and subprocess.run(["launchctl", "print", f"gui/{uid}/{job}"],
                                              capture_output=True).returncode == 0:
            out.append(f"{job} is loaded in launchd; boot it out first")
    if subprocess.run(["pgrep", "-x", "Obsidian"], capture_output=True).returncode == 0:
        out.append("Obsidian is running, and it rewrites links on a rename; quit it first")
    if subprocess.run(["pgrep", "-f", "memory-reflect"], capture_output=True).returncode == 0:
        out.append("a memory-reflect hook is running; wait for it to finish")
    return out


# ── links ──────────────────────────────────────────────────────────────────

def rewrite_links(text: str, moved: dict) -> tuple:
    """`text` with every link that named a moved note pointed at its new path,
    its words kept.

    A basename does not survive this move — three hundred plans all become
    `plan.md` — so a renamed basename is not a link the reader can follow. Each
    one becomes a path link instead, and the words it showed stay the words it
    shows: `[[PLAN-foo]]` becomes `[[projects/p/tasks/042-do-foo/plan|PLAN-foo]]`,
    and an alias that was already there is kept as it was.

    `moved` maps a note's old basename (no extension) to its new vault-relative
    path (no extension). A basename that names two moved notes is ambiguous and
    is left alone — the caller lists those for the operator."""
    if not moved:
        return text, 0
    alt = "|".join(re.escape(k) for k in sorted(moved, key=len, reverse=True))
    pattern = re.compile(r"\[\[(" + alt + r")(#[^\]|]*)?(\|[^\]]*)?\]\]")
    count = 0

    def one(m):
        nonlocal count
        count += 1
        name, anchor, alias = m.group(1), m.group(2) or "", m.group(3)
        return f"[[{moved[name]}{anchor}{alias or '|' + name}]]"

    return pattern.sub(one, text), count


def restore_links(text: str, inverse: dict) -> tuple:
    """The exact mirror of `rewrite_links`: a path link back to the basename it
    was made from.

    The rewrite gave every link an alias, using the old basename when the link
    had none. So an alias equal to the old basename is one the rewrite added and
    comes off again; any other alias was the writer's and stays. Without that
    distinction a revert would leave `[[PLAN-foo|PLAN-foo]]` where `[[PLAN-foo]]`
    stood, and the file would not compare equal to what the run read."""
    if not inverse:
        return text, 0
    alt = "|".join(re.escape(k) for k in sorted(inverse, key=len, reverse=True))
    pattern = re.compile(r"\[\[(" + alt + r")(#[^\]|]*)?\|([^\]]*)\]\]")
    count = 0

    def one(m):
        nonlocal count
        count += 1
        stem, anchor, alias = inverse[m.group(1)], m.group(2) or "", m.group(3)
        return f"[[{stem}{anchor}]]" if alias == stem else f"[[{stem}{anchor}|{alias}]]"

    return pattern.sub(one, text), count


# ── the gold set ───────────────────────────────────────────────────────────

# The eval's own corrections, applied before this one: the gold set is frozen
# evidence and keeps the paths it was labelled with, so its `Agent/desk/projects/`
# prefix and its Title Case root are folded at score time, not edited here.
_GOLD_2B_PREFIX = ("Agent/desk/projects/", "Projects/")  # root-casing: the gold set's frozen spelling on the old side


def _post_2b(path: str) -> str:
    """A gold-set path as the eval's existing remaps leave it: the projects
    merge applied, and the first segment lowercase, which is what the vault
    root lists after the root casing."""
    if path.startswith(_GOLD_2B_PREFIX[0]):
        path = _GOLD_2B_PREFIX[1] + path[len(_GOLD_2B_PREFIX[0]):]
    first, _slash, rest = path.partition("/")
    return f"{first.lower()}/{rest}" if rest else first.lower()


def gold_remaps(plan: dict, gold_path: Path) -> list:
    """The `(old, new)` pairs the retrieval eval needs for this move.

    The gold set expects notes that live under a `_harness/`, and the move takes
    them. A moved question is drift only if nobody named its cause, so the eval
    is given a remap row per expectation and the fixture is never edited. Most
    are a prefix swap; the ones whose plan becomes a numbered task are not, and
    the number is only knowable from the plan the dry run recorded — which is
    why these are generated rather than written by hand.

    Each pair is a full path, not a prefix, so a row can never rewrite a note it
    was not measured against. Pairs are sorted, so two runs over the same plan
    emit the same table."""
    try:
        gold = json.loads(gold_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused(f"cannot read the gold set at {gold_path}: {exc}") from exc
    dest = {m["src"]: m["dst"] for m in plan["moves"]}
    pairs, missing = {}, []
    for entry in gold.get("entries", []):
        for expected in entry.get("expected_note_paths", []):
            if f"/{HARNESS}/" not in expected:
                continue
            now = _post_2b(expected)
            new = dest.get(now)
            if new is None:
                missing.append(now)
                continue
            pairs[now] = new
    return sorted(pairs.items()), sorted(set(missing))


def link_targets(plan: dict) -> tuple:
    """`(moved, ambiguous)` — the basename→new-path map the rewrite uses, and the
    basenames two moved notes share, which it refuses to guess between."""
    seen = {}
    for m in plan["moves"]:
        if not m["src"].endswith(".md"):
            continue
        stem = Path(m["src"]).stem
        seen.setdefault(stem, []).append(m["dst"][:-3] if m["dst"].endswith(".md") else m["dst"])
    moved = {k: v[0] for k, v in seen.items() if len(v) == 1}
    ambiguous = {k: v for k, v in seen.items() if len(v) > 1}
    return moved, ambiguous


# ── apply ──────────────────────────────────────────────────────────────────

def _vault_notes(vault: Path):
    for p in sorted(vault.rglob("*.md")):
        if any(part in SKIP_DIRS for part in p.relative_to(vault).parts):
            continue
        yield p


def open_tracker_gate(vault: Path, plan: dict) -> list:
    """Why the move may not run yet: an open task whose tracker is missing, or
    carries no State or no Next.

    An open task's State and Next are the operator's to write, from the progress
    tail — a generated State on live work is a summary of a summary. They are
    written beside the flat pair, as `/plan` and `/work` write them, and the
    migration moves them like any other file. This gate is what makes "you read
    the four before anything moved" a property of the run."""
    problems = []
    for t in plan["trackers"]:
        if not t["needs_state"]:
            continue
        src = t["from"]
        if src is None:
            stem = Path(t["plan"]).stem
            name = f"tracker-{stem[5:]}.md" if stem.startswith("PLAN-") else "tracker.md"
            problems.append(
                f"{t['project']}/{t['task']} is {t['status']} and has no tracker yet: write "
                f"`{PROJECTS}/{t['project']}/{HARNESS}/{name}` with a State and a Next "
                f"before the move")
            continue
        try:
            parsed = tk.parse(_parsed_text(vault / src))
        except (OSError, tk.TrackerError) as exc:
            problems.append(f"{src} does not read as a tracker: {exc}")
            continue
        if not parsed.state.strip() or parsed.state.strip() == tk.NOT_STARTED:
            problems.append(f"{src} has no State; an open task's State is written by hand")
        if not parsed.next_steps.strip() or parsed.next_steps.strip() == tk.NOT_STARTED:
            problems.append(f"{src} has no Next; an open task's Next is written by hand")
    return problems


def _write_tracker(vault: Path, t: dict, today: str) -> str:
    """The tracker a unit has none of, rendered from what the plan file says.

    A closed task takes only its Objective, `done` and the Outcome its archive
    already records — a generated State on finished work would be a summary of a
    summary, and the design says so."""
    status = t["status"]
    closed = t["closed"] if status in tk.FINAL else None
    outcome = ""
    if status in tk.FINAL:
        outcome = (t["objective"] or "Closed before the migration; the plan and its progress log "
                                    "are in this directory.")
    rendered = tk.render(tk.Tracker(
        title=t["title"] or t["task"], project=t["project"], task=t["task"], status=status,
        opened=t["closed"] or today, updated=today, closed=closed,
        objective=t["objective"] or "Recorded by the migration from the plan's Goal.",
        state="" if status in tk.FINAL else tk.NOT_STARTED,
        next_steps="" if status in tk.FINAL else tk.NOT_STARTED,
        outcome=outcome,
    ))
    path = vault / t["path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    return _sha(_store(path, rendered))


def _prune_empty(vault: Path, plan: dict) -> list:
    """Every directory the moves emptied, deepest first, removed only while it
    holds nothing at all. Returns what was removed, for the record."""
    roots = {vault / PROJECTS / p["project"] / HARNESS for p in plan["projects"]}
    roots.add(vault / PROJECTS / ARCHIVED_PROJECTS)
    out = []
    for root in sorted(roots, key=lambda p: len(p.parts), reverse=True):
        if not root.is_dir():
            continue
        for d in sorted((p for p in root.rglob("*") if p.is_dir()),
                        key=lambda p: len(p.parts), reverse=True):
            if not any(d.iterdir()):
                d.rmdir()
                out.append(_rel(d, vault))
        if not any(root.iterdir()):
            root.rmdir()
            out.append(_rel(root, vault))
    return out


def _stamp_task(vault: Path, t: dict) -> Optional[dict]:
    """A moved tracker's `task:` set to the directory it now sits in.

    The crickets release writes `task:` from the flat slug, or leaves it out
    beside a singleton, because neither knew the number the migration would give
    the task. A task's name is its directory name, so this is what makes the
    moved tracker agree with the gate that checks it. Returns the before and
    after digests, or None when the tracker already named it."""
    path = vault / t["path"]
    try:
        before = _parsed_text(path)
        parsed = tk.parse(before)
    except (OSError, tk.TrackerError):
        return None
    was = parsed.task
    if was == t["task"]:
        return None
    parsed.task = t["task"]
    after = tk.render(parsed)
    return {"path": t["path"], "task": t["task"], "was": was, "before": before,
            "after_sha": _sha(_store(path, after))}


def apply(vault, recorded: dict, confirm_count: int, out_dir, *,
          writers=live_writers, run=None, today: Optional[str] = None,
          repo_roots=None) -> dict:
    vault, out_dir = Path(vault), Path(out_dir)
    plan = build_plan(vault, slugs=recorded.get("slugs_applied") or {})
    if _signature(plan) != _signature(recorded):
        raise Refused("the projects tree is not what the dry run read; run the dry run again. "
                      "Nothing written.")
    if confirm_count != plan["totals"]["moves"]:
        raise Refused(f"the plan moves {plan['totals']['moves']} files; you confirmed "
                      f"{confirm_count}. Nothing written.")
    if plan["unmapped"]:
        raise Refused(f"{len(plan['unmapped'])} file(s) the table names no destination for, "
                      f"starting {plan['unmapped'][0]}. Nothing written.")
    if plan["collisions"]:
        first = next(iter(plan["collisions"]))
        raise Refused(f"{len(plan['collisions'])} destination(s) two files want, starting "
                      f"{first}. Nothing written.")
    staged = _git(vault, "diff", "--cached", "--name-only").stdout.strip()
    if staged:
        raise Refused("the vault's index holds staged changes, and the hand commit after the "
                      f"move must hold the move alone: {staged.splitlines()[0]} … Nothing written.")
    busy = writers()
    if busy:
        raise Refused("a writer is live: " + "; ".join(busy) + ". Nothing written.")
    gate = open_tracker_gate(vault, plan)
    if gate:
        raise Refused("an open task's tracker is not ready:\n  " + "\n  ".join(gate)
                      + "\nNothing written.")

    run = run or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True))
    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    run_id = recorded["run_id"]
    journal = {"run_id": run_id, "applied_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "vault": str(vault), "moves": [], "trackers": [], "stamped": [], "links": [],
               "markers": [], "landed": True}

    # 1. Every file moves once, in one `git mv`, so the index moves with the tree.
    for m in plan["moves"]:
        dst = vault / m["dst"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        result = run(["git", "-C", str(vault), "mv", m["src"], m["dst"]])
        landed = result.returncode == 0 and dst.is_file() and not (vault / m["src"]).exists()
        entry = {**m, "landed": landed}
        if not landed:
            entry["stderr"] = (result.stderr or result.stdout).strip()[:400]
            journal["landed"] = False
        journal["moves"].append(entry)
        if not landed:
            break

    # 2. The trackers no crickets release had written yet, and the `task:` of the
    #    ones it had: a tracker written beside a flat pair names the flat slug,
    #    or nothing at all beside a singleton, and a task's name is its directory
    #    name. Stamping it is the one edit a moved tracker takes.
    if journal["landed"]:
        for t in plan["trackers"]:
            if t["source"] == "written":
                journal["trackers"].append({"path": t["path"], "status": t["status"],
                                            "task": t["task"], "project": t["project"],
                                            "sha": _write_tracker(vault, t, today)})
                continue
            stamped = _stamp_task(vault, t)
            if stamped is not None:
                journal["stamped"].append(stamped)

    # 3. Every link that named a moved note becomes a path link, words kept.
    moved, ambiguous = link_targets(plan)
    journal["ambiguous_basenames"] = ambiguous
    if journal["landed"]:
        for note in _vault_notes(vault):
            try:
                before = _load(note)
            except (OSError, UnicodeDecodeError):
                continue
            after, n = rewrite_links(before, moved)
            if n and after != before:
                journal["links"].append({"rel": _rel(note, vault), "links": n,
                                         "before_sha": _sha(before.encode("utf-8")),
                                         "after_sha": _sha(_store(note, after))})

    # 4. The directories the moves emptied. Git does not track a directory, so
    #    `_harness/` and its subdirectories are still on disk with nothing in
    #    them, and "no `_harness/` under any project" would read false. Removing
    #    an empty directory removes no content; the reverse `git mv` makes each
    #    one again as it puts a file back.
    if journal["landed"]:
        journal["pruned"] = _prune_empty(vault, plan)

    # 5. A session bound to a plan whose slug changed follows it. Without this a
    #    worktree's `.harness/active-plan` names a plan that no longer exists and
    #    the resolver refuses loudly on the next call — correct, but avoidable.
    if journal["landed"]:
        journal["markers"] = _repoint_markers(plan, roots=repo_roots)

    journal["counts"] = {"moved": sum(1 for m in journal["moves"] if m["landed"]),
                         "trackers": len(journal["trackers"]),
                         "notes_relinked": len(journal["links"]),
                         "links_rewritten": sum(e["links"] for e in journal["links"])}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"journal-{run_id}.json").write_text(json.dumps(journal, indent=2) + "\n",
                                                    encoding="utf-8")
    return journal


def _repoint_markers(plan: dict, *, roots=None) -> list:
    """Every worktree marker and root pointer that names a plan whose task now
    carries a different name. Local files, outside the vault; the pre-image is
    journaled so `--revert` puts each back.

    Without this, a session bound to `PLAN-build-the-widget` finds nothing at
    `042-assemble-the-widget` and the resolver refuses loudly on its next call —
    correct, and avoidable, since the migration is the thing that renamed it.

    The two halves of a bind sit in two places: the pointer is
    `<main-root>/.harness/worktree-for-<slug>` and holds the worktree's path,
    and the marker is `<that path>/.harness/active-plan`. The pointer is what
    finds the marker; globbing the main root for `active-plan` finds only a
    direct-mode session's, and renaming the pointer without following it would
    leave the marker naming a plan that no longer exists."""
    out = []
    renamed = {}
    for p in plan["projects"]:
        for s in p["slugs"]:
            old = Path(s["source"]).stem
            old = old[5:] if old.startswith("PLAN-") else old
            if old and old != s["task"]:
                renamed[old] = s["task"]

    def rewrite_marker(marker: Path) -> None:
        try:
            text = _load(marker)
        except OSError:
            return
        old = text.strip()
        if old not in renamed:
            return
        _store(marker, renamed[old] + "\n")
        out.append({"kind": "marker", "path": str(marker), "before": text,
                    "after": renamed[old] + "\n"})

    for repo in (roots() if roots else _repo_roots()):
        harness = repo / ".harness"
        if not harness.is_dir():
            continue
        rewrite_marker(harness / "active-plan")  # a direct-mode session
        for pointer in sorted(harness.glob(f"{_POINTER_PREFIX}*")):
            try:
                worktree = Path(_load(pointer).strip())
            except OSError:
                continue
            rewrite_marker(worktree / ".harness" / "active-plan")
            old = pointer.name[len(_POINTER_PREFIX):]
            if old in renamed:
                new = pointer.with_name(f"{_POINTER_PREFIX}{renamed[old]}")
                pointer.rename(new)
                out.append({"kind": "pointer", "from": str(pointer), "to": str(new)})
    return out


def _repo_roots() -> list:
    """The clones whose `.harness/` can hold a binding. Read from the registry
    when one is reachable; the two siblings otherwise."""
    try:
        import repo_registry  # noqa: E402
        roots = [Path(r["path"]) for r in repo_registry.load().get("repos", [])]
        if roots:
            return [r for r in roots if r.is_dir()]
    except Exception:
        pass
    return [p for p in (Path.home() / "Antigravity" / "agentm",
                        Path.home() / "Antigravity" / "crickets") if p.is_dir()]


# ── finish ─────────────────────────────────────────────────────────────────

def finish(vault, recorded: dict, out_dir) -> list:
    """The post-conditions that still fail; the marker is written when none do."""
    vault, out_dir = Path(vault), Path(out_dir)
    journal_path = out_dir / f"journal-{recorded['run_id']}.json"
    if not journal_path.is_file():
        raise Refused(f"no journal for {recorded['run_id']}; apply the plan first")
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    if not journal.get("landed"):
        raise Refused(f"the applied run did not land as planned; read {journal_path}")
    problems = []
    root = vault / PROJECTS
    for p in sorted(root.iterdir()) if root.is_dir() else []:
        if not p.is_dir() or p.name in (ARCHIVED_PROJECTS, COMPLETED):
            continue
        if (p / HARNESS).is_dir():
            problems.append(f"{p.name} still has a {HARNESS}/ directory")
        if (p / "_index.md").is_file():
            problems.append(f"{p.name} still has _index.md; the charter is charter.md")
        if not (p / "charter.md").is_file():
            problems.append(f"{p.name} has no charter.md")
        tasks = p / TASKS
        if not tasks.is_dir():
            continue
        for t in sorted(tasks.iterdir()):
            if not t.is_dir():
                continue
            if not _TASK_DIR.match(t.name):
                problems.append(f"{p.name}/{TASKS}/{t.name} carries no three-digit prefix")
            if not (t / "plan.md").is_file():
                problems.append(f"{p.name}/{TASKS}/{t.name} has no plan.md")
            tracker = t / "tracker.md"
            if not tracker.is_file():
                problems.append(f"{p.name}/{TASKS}/{t.name} has no tracker.md")
                continue
            try:
                parsed = tk.parse(_parsed_text(tracker))
            except (OSError, tk.TrackerError) as exc:
                problems.append(f"{p.name}/{TASKS}/{t.name}/tracker.md does not read: {exc}")
                continue
            if parsed.status not in tk.STATUSES:
                problems.append(f"{p.name}/{TASKS}/{t.name}/tracker.md carries status "
                                f"{parsed.status!r}, which is not one of {tk.STATUSES}")
            if parsed.task != t.name:
                problems.append(f"{p.name}/{TASKS}/{t.name}/tracker.md names task "
                                f"{parsed.task!r}; a task's name is its directory name")
    if (root / ARCHIVED_PROJECTS).is_dir():
        left = [p for p in (root / ARCHIVED_PROJECTS).rglob("*") if p.is_file()]
        if left:
            problems.append(f"{PROJECTS}/{ARCHIVED_PROJECTS}/ still holds {len(left)} file(s)")
    # Every move the journal claims is where it claims, and nothing is back.
    for m in journal.get("moves", []):
        if not m.get("landed"):
            continue
        if not (vault / m["dst"]).is_file():
            problems.append(f"{m['dst']} is missing; the run says it moved there")
        if (vault / m["src"]).exists():
            problems.append(f"{m['src']} is back; something wrote to the old path")
    for t in journal.get("trackers", []):
        if not (vault / t["path"]).is_file():
            problems.append(f"{t['path']} is missing; the run wrote it")
    if not problems:
        marker = vault / MARKER_REL
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            f"run {recorded['run_id']}\nfinished "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"moves {journal['counts']['moved']}, trackers {journal['counts']['trackers']}, "
            f"links {journal['counts']['links_rewritten']}\n", encoding="utf-8")
    return sorted(set(problems))


# ── revert ─────────────────────────────────────────────────────────────────

def revert(vault, run_id: str, out_dir, *, run=None) -> None:
    vault, out_dir = Path(vault), Path(out_dir)
    journal_path = out_dir / f"journal-{run_id}.json"
    if not journal_path.is_file():
        raise Refused(f"no journal for {run_id}")
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    done = [m for m in journal.get("moves", []) if m.get("landed")]
    problems = []
    for m in done:
        if not (vault / m["dst"]).is_file():
            problems.append(f"{m['dst']} is gone; nothing to move back")
        if (vault / m["src"]).exists():
            problems.append(f"{m['src']} exists again; the run's state is not what is on disk")
    for t in journal.get("trackers", []):
        p = vault / t["path"]
        if p.is_file() and _sha(p.read_bytes()) != t["sha"]:
            problems.append(f"{t['path']} no longer holds what the run wrote; removing it "
                            f"would lose a later write")
    for e in journal.get("links", []):
        p = vault / e["rel"]
        if p.is_file() and _sha(p.read_bytes()) != e["after_sha"]:
            problems.append(f"{e['rel']} no longer holds what the run wrote; rewriting it "
                            f"would lose a later write")
    for s in journal.get("stamped", []):
        p = vault / s["path"]
        if p.is_file() and _sha(p.read_bytes()) != s["after_sha"]:
            problems.append(f"{s['path']} no longer holds what the run stamped; restoring it "
                            f"would lose a later write")
    if problems:
        raise Refused("; ".join(problems[:6]) + f" ({len(problems)} in all). Nothing restored.")
    run = run or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True))
    # The links first: their map is keyed on the destinations, which still exist.
    inverse = {}
    for m in done:
        if m["src"].endswith(".md"):
            inverse[m["dst"][:-3] if m["dst"].endswith(".md") else m["dst"]] = Path(m["src"]).stem
    for e in journal.get("links", []):
        p = vault / e["rel"]
        if not p.is_file():
            continue
        restored, _n = restore_links(_load(p), inverse)
        _store(p, restored)
    for s in journal.get("stamped", []):
        p = vault / s["path"]
        if p.is_file():
            _store(p, s["before"])
    for t in journal.get("trackers", []):
        p = vault / t["path"]
        if p.is_file():
            p.unlink()
    for m in reversed(done):
        (vault / m["src"]).parent.mkdir(parents=True, exist_ok=True)
        result = run(["git", "-C", str(vault), "mv", m["dst"], m["src"]])
        if result.returncode != 0 or not (vault / m["src"]).is_file():
            raise Refused(f"moving {m['dst']} back to {m['src']} failed: "
                          f"{(result.stderr or result.stdout).strip()[:300]}")
    for entry in reversed(journal.get("markers", [])):
        if entry["kind"] == "marker":
            _store(Path(entry["path"]), entry["before"])
        else:
            Path(entry["to"]).rename(entry["from"])
    marker = vault / MARKER_REL
    if marker.exists():
        marker.unlink()


# ── command line ───────────────────────────────────────────────────────────

def _defaults():
    import engine_state  # noqa: E402
    import harness_memory as hm  # noqa: E402
    return Path(hm.vault_path()), Path(engine_state.engine_state_dir())


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", help="vault root (default: the configured one)")
    ap.add_argument("--state-dir", help="engine state directory (default: the configured one)")
    ap.add_argument("--slugs", help="JSON map of {<source>: <verb-slug>} — the operator's "
                                    "corrections to the derived task slugs")
    ap.add_argument("--apply", action="store_true", help="apply a recorded plan")
    ap.add_argument("--finish", action="store_true", help="check the post-conditions and write the marker")
    ap.add_argument("--plan", help="the plan file the dry run recorded")
    ap.add_argument("--confirm-count", type=int, help="the plan's move count, typed by the operator")
    ap.add_argument("--revert", metavar="RUN_ID", help="put back every move a run made")
    ap.add_argument("--gold-remap", metavar="PLAN", nargs="?", const="",
                    help="print the retrieval eval's remap rows for a recorded plan "
                         "(default: a fresh dry run), and exit")
    args = ap.parse_args(argv)

    vault = state_dir = None
    if not (args.vault and args.state_dir):
        vault, state_dir = _defaults()
    vault = Path(args.vault) if args.vault else vault
    state_dir = Path(args.state_dir) if args.state_dir else state_dir
    out_dir = state_dir / STAGE

    try:
        if args.gold_remap is not None:
            recorded = (json.loads(Path(args.gold_remap).read_text(encoding="utf-8"))
                        if args.gold_remap else build_plan(vault))
            gold = _REPO / "scripts" / "health" / "fixtures" / "week1-gold" / "gold-set-v3.json"
            pairs, missing = gold_remaps(recorded, gold)
            print("# agentm-vault plan 10: the gold set's `_harness/` expectations, each a full")
            print("# path, generated from the recorded plan. Paste into eval_retrieval_shipped.py.")
            print("_PROJECTS_REMAPS = (")
            for old, new in pairs:
                print(f'    ("{old}", "{new}"),')
            print(")")
            if missing:
                print(f"\n# {len(missing)} expectation(s) the table names no destination for:",
                      file=sys.stderr)
                for m in missing:
                    print(f"#   {m}", file=sys.stderr)
            print(f"\n# {len(pairs)} row(s).")
            return 1 if missing else 0
        if args.revert:
            revert(vault, args.revert, out_dir)
            print(f"projects layout: reverted {args.revert}")
            return 0
        if args.apply or args.finish:
            if not args.plan:
                print("projects layout: --apply and --finish need --plan", file=sys.stderr)
                return 2
            recorded = json.loads(Path(args.plan).read_text(encoding="utf-8"))
            if args.apply:
                if args.confirm_count is None:
                    print("projects layout: --apply needs --confirm-count", file=sys.stderr)
                    return 2
                journal = apply(vault, recorded, args.confirm_count, out_dir)
                c = journal["counts"]
                print(f"  moved {c['moved']} of {len(journal['moves'])} file(s); wrote "
                      f"{c['trackers']} tracker(s); rewrote {c['links_rewritten']} link(s) in "
                      f"{c['notes_relinked']} note(s); repointed {len(journal['markers'])} marker(s)")
                for m in journal["moves"]:
                    if not m.get("landed"):
                        print(f"  DID NOT LAND: {m['src']} -> {m['dst']}: {m.get('stderr')}")
                if journal.get("ambiguous_basenames"):
                    print(f"  {len(journal['ambiguous_basenames'])} basename(s) two moved notes "
                          f"share; their links were left alone for you to read:")
                    for k, v in list(journal["ambiguous_basenames"].items())[:20]:
                        print(f"    {k}: {', '.join(v)}")
                print(f"projects layout: applied {recorded['run_id']}; journal matches the plan: "
                      f"{'yes' if journal['landed'] else 'NO'}")
                print(f'next: git -C "{vault}" commit -m "the projects migration: _harness/ '
                      f'dissolves" (the moves are staged; the trackers and the link rewrites are '
                      f"not), then the deploy, the reindex and --finish --plan {args.plan}")
                return 0 if journal["landed"] else 1
            problems = finish(vault, recorded, out_dir)
            if problems:
                print(f"projects layout: {len(problems)} post-condition(s) fail; no marker written",
                      file=sys.stderr)
                for p in problems[:60]:
                    print(f"  {p}", file=sys.stderr)
                if len(problems) > 60:
                    print(f"  ... and {len(problems) - 60} more", file=sys.stderr)
                return 1
            marker = vault / MARKER_REL
            print(f"projects layout: every post-condition holds; wrote {marker}")
            print(f'it is a new dot-named file, which the daemon does not commit: git -C "{vault}" '
                  f'add "{MARKER_REL}" && git -C "{vault}" commit -m "the projects migration: '
                  f'the data run\'s marker"')
            return 0
        slugs = json.loads(Path(args.slugs).read_text(encoding="utf-8")) if args.slugs else {}
        run_id = "projects-layout-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        plan = build_plan(vault, slugs=slugs)
    except Refused as exc:
        print(f"projects layout: refused — {exc}", file=sys.stderr)
        return 1
    plan["run_id"] = run_id
    plan["slugs_applied"] = slugs
    out_dir.mkdir(parents=True, exist_ok=True)
    plan_path = out_dir / f"plan-{run_id}.json"
    plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    print(f"projects layout — dry run {run_id} over {vault}")
    print_summary(plan)
    moved, ambiguous = link_targets(plan)
    print(f"  links: {len(moved)} moved note basenames become path links; "
          f"{len(ambiguous)} basename(s) two moved notes share are left alone")
    print(f"plan: {plan_path}")
    print(f'apply: python3 scripts/migrate/projects_layout.py --vault "{vault}" --apply '
          f"--plan {plan_path} --confirm-count {plan['totals']['moves']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
