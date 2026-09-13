#!/usr/bin/env python3
"""Gate: every tracker in the projects space has the tracker's one schema.

agentm-vault § Projects and tasks: a task's or a project's living head is one
small file, and `scripts/tracker.py` owns its schema. This gate reads the live
vault, resolved at runtime, and checks every tracker it finds in the places the
design puts one:

  <projects>/<slug>/tracker.md                  the project's own tracker
  <projects>/<slug>/tasks/<task>/tracker.md     a task's tracker
  <projects>/<slug>/_harness/tracker.md         beside the singleton flat pair
  <projects>/<slug>/_harness/tracker-<task>.md  beside a named flat pair

A tracker names the project it sits in, a task's tracker names its task, and a
project's own tracker names none. A Markdown file anywhere else in a project
that declares `kind: tracker` is reported as misplaced.

No tracker exists until the crickets release starts writing them, so today the
gate reads zero files and passes. Every run first proves the checks fire on
fixtures, so a vault-less machine still exercises them.

Usage:
  python3 scripts/check-tracker-schema.py                  # the resolved vault
  python3 scripts/check-tracker-schema.py --projects DIR   # one projects space
  python3 scripts/check-tracker-schema.py --self-test      # the fixtures only
Exit: 0 clean, or nothing to check; 1 on a finding or a failed self-test.
"""
from __future__ import annotations

import argparse
import dataclasses
import sys
import tempfile
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import tracker as tk  # noqa: E402

_HEAD_BYTES = 4096
PLACES = "tracker.md, tasks/<task>/tracker.md, _harness/tracker.md or _harness/tracker-<task>.md"


def placement(parts: tuple) -> Optional[tuple]:
    """Where the design puts a tracker, as `(place, task)`, or None."""
    if parts == ("tracker.md",):
        return ("project", None)
    if len(parts) == 3 and parts[0] == "tasks" and parts[2] == "tracker.md":
        return ("task", parts[1])
    if len(parts) == 2 and parts[0] == "_harness":
        if parts[1] == "tracker.md":
            return ("flat", None)
        if parts[1].startswith("tracker-") and parts[1].endswith(".md"):
            return ("flat", parts[1][len("tracker-"):-len(".md")])
    return None


def declares_tracker(path: Path) -> bool:
    """Whether a note's frontmatter says `kind: tracker`, reading only its head."""
    try:
        with path.open("rb") as fh:
            head = fh.read(_HEAD_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return False
    head = head.replace("\r\n", "\n")  # a note saved on Windows ends its lines in CRLF
    if not head.startswith("---\n"):
        return False
    end = head.find("\n---", 3)
    block = head[4:end] if end != -1 else head[4:]
    return any(line.strip() in ("kind: tracker", 'kind: "tracker"', "kind: 'tracker'")
               for line in block.split("\n"))


def placement_findings(t: tk.Tracker, slug: str, place: str, task: Optional[str]) -> list:
    out = []
    if t.project != slug:
        out.append(f"`project: {t.project}` is not the project it sits in (`{slug}`)")
    if place == "project" and t.task:
        out.append(f"a project's own tracker names no task, and this one names `{t.task}`")
    if task is not None and t.task != task:
        out.append(f"`task: {t.task}` is not the task it sits beside (`{task}`)")
    return out


def projects_findings(projects: Path) -> tuple:
    """`(trackers read, findings)` over one projects space."""
    count, findings = 0, []
    for project in sorted(p for p in projects.iterdir()
                          if p.is_dir() and not p.name.startswith((".", "_"))):
        for path in sorted(project.rglob("*.md")):
            parts = path.relative_to(project).parts
            if any(part.startswith(".") for part in parts):
                continue
            rel = "/".join((project.name,) + parts)
            placed = placement(parts)
            if placed is None:
                if declares_tracker(path):
                    count += 1
                    findings.append(f"{rel}: declares `kind: tracker` outside a tracker's place ({PLACES})")
                continue
            count += 1
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                findings.append(f"{rel}: {exc}")
                continue
            problems = tk.check_text(text)
            if not problems:
                problems = placement_findings(tk.parse(text), project.name, *placed)
            findings += [f"{rel}: {p}" for p in problems]
    return count, findings


def resolve_projects_spaces() -> list:
    """The projects spaces this machine's vault holds: the root `Projects/`, and
    the memory-root layouts older vaults used."""
    try:
        import harness_memory as hm  # noqa: E402
    except ImportError:
        return []
    candidates = []
    try:
        vault = hm.vault_path()
    except Exception:
        vault = None
    if vault:
        candidates.append(Path(vault) / getattr(hm, "_VAULT_ROOT_PROJECTS_REL", "Projects"))
    try:
        memory = hm.memory_root()
    except Exception:
        memory = None
    if memory:
        for rel in (getattr(hm, "_VAULT_PROJECTS_REL_NEW", "desk/projects"),
                    getattr(hm, "_VAULT_PROJECTS_REL_LEGACY", "personal-projects")):
            candidates.append(Path(memory).joinpath(*rel.split("/")))
    spaces, seen = [], set()
    for c in candidates:
        if c.is_dir() and c.resolve() not in seen:
            seen.add(c.resolve())
            spaces.append(c)
    return spaces


def _put(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def self_test(out=None) -> int:
    """The checks fire on fixtures: a good tracker in each place passes, and a
    malformed, a mismatched and a misplaced one are each named."""
    # Resolved per call rather than when the module loads, so a caller that
    # redirects stdout reads the failure.
    out = sys.stdout if out is None else out
    good = tk.new(title="Fixture task", project="fixture", task="build-it",
                  objective="The fixture is built.", next_step="Start.", today="2026-09-12")
    with tempfile.TemporaryDirectory(prefix="check-tracker-schema-") as tmp:
        projects = Path(tmp)
        project = projects / "fixture"
        _put(project / "tracker.md", tk.render(dataclasses.replace(good, task=None)))
        _put(project / "tasks" / "build-it" / "tracker.md", tk.render(good))
        _put(project / "_harness" / "tracker-build-it.md", tk.render(good))
        _put(project / "tasks" / "broken" / "tracker.md",
             tk.render(dataclasses.replace(good, task="broken")).replace("status: queued", "status: complete"))
        _put(project / "_harness" / "tracker-other.md", tk.render(good))
        _put(project / "notes" / "stray.md", tk.render(good))
        _put(projects / "_archive" / "old" / "tracker.md", "not a tracker\n")
        count, findings = projects_findings(projects)
    flagged = sorted({f.split(": ", 1)[0] for f in findings})
    expected = ["fixture/_harness/tracker-other.md", "fixture/notes/stray.md",
                "fixture/tasks/broken/tracker.md"]
    ok = count == 6 and flagged == expected
    if not ok:
        print(f"check-tracker-schema: self-test FAILED — read {count} (expected 6), "
              f"flagged {flagged} (expected {expected})", file=out)
        return 1
    return 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--projects", default=None,
                    help="a projects space to check (default: the resolved vault's)")
    ap.add_argument("--self-test", action="store_true", help="check the fixtures only")
    args = ap.parse_args(argv)
    if self_test() != 0:
        return 1
    if args.self_test:
        print("check-tracker-schema: self-test OK")
        return 0
    spaces = [Path(args.projects)] if args.projects else resolve_projects_spaces()
    spaces = [s for s in spaces if s.is_dir()]
    if not spaces:
        print("check-tracker-schema: self-test OK; no projects space resolves, nothing else to check")
        return 0
    total, findings = 0, []
    for space in spaces:
        count, found = projects_findings(space)
        total += count
        findings += found
    if findings:
        print(f"check-tracker-schema: {len(findings)} finding(s) over {total} tracker(s)")
        for finding in findings:
            print(f"  {finding}")
        return 1
    print(f"check-tracker-schema: self-test OK; clean — {total} tracker(s) in "
          f"{len(spaces)} projects space(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
