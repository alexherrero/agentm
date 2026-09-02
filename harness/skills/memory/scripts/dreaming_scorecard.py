#!/usr/bin/env python3
"""The nightly dreaming scorecard.

What last night's run *did*, as opposed to what the memory *is* — that second
report is the corpus scorecard beside this one. Stages and what each one
touched, how the queues and coverage moved, which tier each job ran on, and the
run's own health.

# Movement needs a yesterday

"The queue is 9,447" is a number. "The queue is 9,447, up 312 since last night"
is a finding, and only the second one tells a reader whether to do anything. So
each edition carries its own raw numbers in frontmatter, and the next edition
reads them back to compute the deltas.

The report is its own state file. A separate one would be a second thing to keep
in agreement with the report it describes, and the first time they disagreed
nobody would know which to believe.

# Absence is not zero

Inherited from `dream_stages.StageResult`, which already draws the distinction
this report has to preserve: *"a cycle that ran without [the daemon] did not do
less work badly — it did not do the work, and the digest should say which."* A
stage that could not run reports why. It never reports 0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DAEMON_BIN = os.environ.get("AGENTMD", "agentmd")
_TIMEOUT_SECONDS = 300

STABLE_NAME = "latest_dreaming_scorecard.md"
DIAGNOSTICS_DIR = Path("diagnostics") / "dreaming"
# Where `dream.py` stages each run. One directory per run id.
STAGING_DIR = Path("desk") / "scratch"


class DaemonUnavailable(RuntimeError):
    """The daemon could not answer. Raised rather than defaulted."""


def _agentmd(args: list) -> Any:
    argv = [DAEMON_BIN, args[0], "--json"] + args[1:]
    try:
        # `encoding` named explicitly: `text=True` alone decodes the child's
        # output with the *locale* encoding, which is cp1252 on Windows. The
        # daemon writes UTF-8 and its own messages carry em-dashes — the meters'
        # "no vectors to measure —" among them — so the default would mojibake
        # the reason a report is about to print.
        proc = subprocess.run(argv, capture_output=True, text=True,
                              encoding="utf-8", timeout=_TIMEOUT_SECONDS)
    except FileNotFoundError as exc:
        raise DaemonUnavailable(
            f"{DAEMON_BIN} is not on PATH; set $AGENTMD to a built binary") from exc
    except subprocess.TimeoutExpired as exc:
        raise DaemonUnavailable(
            f"{DAEMON_BIN} {args[0]} did not answer within "
            f"{_TIMEOUT_SECONDS}s") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise DaemonUnavailable(
            f"{DAEMON_BIN} {args[0]} exited {proc.returncode}: "
            + (detail[-1][:200] if detail else "no reason given"))
    try:
        return json.loads(proc.stdout) if proc.stdout.strip() else None
    except json.JSONDecodeError as exc:
        raise DaemonUnavailable(
            f"{DAEMON_BIN} {args[0]} returned something that is not JSON") from exc


# ── movement ────────────────────────────────────────────────────────────────

@dataclass
class Movement:
    """One number and how far it has moved since the last report."""

    label: str
    now: Optional[int] = None
    before: Optional[int] = None
    missing: str = ""
    note: str = ""
    # Whether going up is the bad direction. Written down per number rather than
    # inferred, because it differs: a queue growing is bad and coverage growing
    # is good, and a reader should not have to work that out per row.
    up_is_bad: bool = True

    def delta(self) -> Optional[int]:
        if self.now is None or self.before is None:
            return None
        return self.now - self.before

    def render(self) -> str:
        if self.missing:
            return f"| {self.label} | — | not measured: {self.missing} |"
        d = self.delta()
        if d is None:
            # First edition, or the previous one did not carry this number.
            # Stated rather than shown as "+0", which would claim a night of no
            # change that nobody observed.
            moved = "no previous reading to compare against"
        elif d == 0:
            moved = "unchanged"
        else:
            direction = "up" if d > 0 else "down"
            worrying = (d > 0) == self.up_is_bad
            moved = f"{direction} {abs(d)}" + ("" if not worrying else " ⚠")
        detail = f"{moved}" + (f" · {self.note}" if self.note else "")
        return f"| {self.label} | {self.now} | {detail} |"


@dataclass
class StageRow:
    stage: str
    considered: int = 0
    enqueued: int = 0
    written: int = 0
    skipped: int = 0
    unavailable: str = ""

    def render(self) -> str:
        if self.unavailable:
            return f"| {self.stage} | — | did not run: {self.unavailable} |"
        did = []
        for label, n in (("considered", self.considered), ("enqueued", self.enqueued),
                         ("written", self.written), ("skipped", self.skipped)):
            if n:
                did.append(f"{n} {label}")
        return f"| {self.stage} | {self.considered} | {', '.join(did) or 'nothing to do'} |"


# ── reading the run ─────────────────────────────────────────────────────────

def latest_run(vault: Path, staging: Path = None) -> Optional[dict]:
    """The most recent staged run, or None if nothing has ever run.

    Chosen by the staged-at timestamp inside each manifest rather than by
    directory mtime: a vault that syncs across machines rewrites mtimes, and a
    scorecard that reported the wrong night's run would be worse than one that
    reported none.
    """
    root = vault / (staging if staging is not None else STAGING_DIR)
    if not root.is_dir():
        return None
    best = None
    for manifest in root.glob("*/proposals.json"):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or "run_id" not in data:
            continue
        data["_dir"] = str(manifest.parent)
        if best is None or data.get("staged_at", 0) > best.get("staged_at", 0):
            best = data
    return best


def previous_numbers(out_dir: Path) -> dict:
    """The raw numbers the last edition recorded, or {} if there is no last one.

    Read from the stable copy's frontmatter. Failing to parse is the same as
    having none — the deltas go missing and say so, which is better than a report
    that will not render because yesterday's file was edited by hand.
    """
    stable = out_dir / STABLE_NAME
    if not stable.exists():
        return {}
    try:
        text = stable.read_text(encoding="utf-8")
    except OSError:
        return {}
    marker = "readings: "
    for line in text.splitlines():
        if line.startswith(marker):
            try:
                return json.loads(line[len(marker):])
            except json.JSONDecodeError:
                return {}
    return {}


def gather(vault: Path, out_dir: Path, *, staging: Path = None) -> tuple:
    """Everything the report needs: stage rows, movements, tiers, and notes."""
    before = previous_numbers(out_dir)
    now_numbers: dict = {}
    notes: list = []

    run = latest_run(vault, staging)
    stages: list = []
    if run is None:
        notes.append("No staged run was found. Either dreaming has not run yet, "
                     "or its staging directory has been cleared.")
    else:
        by_stage: dict = {}
        for p in run.get("proposals") or []:
            row = by_stage.setdefault(p.get("stage", "unknown"), StageRow(p.get("stage", "unknown")))
            row.considered += 1
            if p.get("mutations"):
                row.written += 1
        stages = [by_stage[k] for k in sorted(by_stage)]
        # A run's own stage results, when the pass recorded them.
        for s in run.get("stages") or []:
            stages.append(StageRow(
                stage=s.get("stage", "unknown"),
                considered=s.get("considered", 0), enqueued=s.get("enqueued", 0),
                written=s.get("written", 0), skipped=s.get("skipped", 0),
                unavailable=s.get("unavailable", "")))

    movements: list = []

    try:
        status = _agentmd(["status"]) or {}
        queue = (status.get("health") or {}).get("queue") or {}
        unfiled = queue.get("unfiled")
        now_numbers["unfiled"] = unfiled
        movements.append(Movement(
            "unfiled queue", now=unfiled, before=before.get("unfiled"),
            note="growing means capture is outrunning filing"))
    except DaemonUnavailable as exc:
        movements.append(Movement("unfiled queue", missing=str(exc)))

    try:
        led = _agentmd(["ledger", "--pending", "--limit", "0"]) or {}
        current, eligible = led.get("current"), led.get("eligible")
        now_numbers["coverage_current"] = current
        now_numbers["coverage_eligible"] = eligible
        movements.append(Movement(
            "enrichment coverage", now=current, before=before.get("coverage_current"),
            up_is_bad=False, note=f"of {eligible} eligible"))
    except DaemonUnavailable as exc:
        movements.append(Movement("enrichment coverage", missing=str(exc)))

    for owner in ("enrich", "entity-rollup"):
        try:
            rows = _agentmd(["queue", "--owner", owner]) or []
            depth = rows[0].get("depth", 0) if rows else 0
            parked = len(rows[0].get("parked") or []) if rows else 0
            now_numbers[f"queue_{owner}"] = depth
            movements.append(Movement(
                f"{owner} queue", now=depth, before=before.get(f"queue_{owner}"),
                note=f"{parked} parked" if parked else ""))
        except DaemonUnavailable as exc:
            movements.append(Movement(f"{owner} queue", missing=str(exc)))

    tiers: list = []
    try:
        tiers = _agentmd(["tiers"]) or []
    except DaemonUnavailable as exc:
        notes.append(f"Tier routing could not be read: {exc}")

    return stages, movements, tiers, now_numbers, notes, run


# ── the report ──────────────────────────────────────────────────────────────

def render(stages, movements, tiers, numbers, notes, run, *, now: datetime,
           vault: Path, tz=None) -> str:
    stamp = now.astimezone(tz).strftime("%Y-%m-%d")
    out = [
        "---",
        "title: Dreaming scorecard",
        "kind: report",
        f"date: {stamp}",
        # The machine-readable tail. The next edition reads this back to work out
        # what moved, which is why the report is its own state file.
        "readings: " + json.dumps(numbers, sort_keys=True),
        "---",
        "",
        f"# Dreaming — {stamp}",
        "",
        "What last night's run did. What the memory *is* is the corpus "
        "scorecard, beside this one.",
        "",
    ]
    if run:
        out += [f"Run `{run.get('run_id')}`, staged from `{run.get('_dir', '')}`.", ""]

    out += ["## Stages", ""]
    if stages:
        out += ["| stage | considered | |", "|---|---|---|"]
        out += [s.render() for s in stages]
    else:
        out += ["No stage reported anything. A run that did nothing and a run "
                "that never happened are different, and the note below says "
                "which this was."]
    out.append("")

    out += ["## Movement", "",
            "Each number against the last edition of this report.", "",
            "| | now | since last night |", "|---|---|---|"]
    out += [m.render() for m in movements]
    out.append("")

    out += ["## Tiers", "",
            "Which model tier each token-bearing job is routed to, and why.", ""]
    if tiers:
        out += ["| job | tier | why |", "|---|---|---|"]
        for t in tiers:
            out.append(f"| {t.get('job')} | {t.get('tier')} | {t.get('why')} |")
    else:
        out.append("No tier routing to report.")
    out.append("")

    if notes:
        out += ["## Notes", ""]
        out += [f"- {n}" for n in notes]
        out.append("")

    out += ["---", "", f"Written {now.strftime('%Y-%m-%d %H:%M')}Z from `{vault}`.", ""]
    return "\n".join(out)


def build(vault: Path, *, now: datetime, rel: Path = None,
          staging: Path = None, tz=None) -> tuple:
    out_dir = vault / (rel if rel is not None else DIAGNOSTICS_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    stages, movements, tiers, numbers, notes, run = gather(
        vault, out_dir, staging=staging)
    body = render(stages, movements, tiers, numbers, notes, run, now=now,
                  vault=vault, tz=tz)

    dated = out_dir / f"{now.astimezone(tz).strftime('%Y-%m-%d')}-dreaming-scorecard.md"
    stable = out_dir / STABLE_NAME
    dated.write_text(body, encoding="utf-8")
    stable.write_text(body, encoding="utf-8")
    return dated, stable


def diagnostics_dir() -> Path:
    """Where the scorecards go, vault-relative. See corpus_scorecard for why
    this is derived from the configured desk rather than assembled from a root."""
    try:
        spaces = (_agentmd(["status"]) or {}).get("spaces") or {}
    except DaemonUnavailable:
        spaces = {}
    configured = str(spaces.get("diagnostics") or "").strip("/")
    if configured:
        return Path(configured) / "dreaming"
    return DIAGNOSTICS_DIR


def staging_dir() -> Path:
    """Where `dream.py` stages runs, vault-relative — the sibling `scratch`."""
    try:
        spaces = (_agentmd(["status"]) or {}).get("spaces") or {}
    except DaemonUnavailable:
        spaces = {}
    projects = str(spaces.get("projects") or "").strip("/")
    if projects:
        desk = Path(projects).parent
        if str(desk) not in (".", "/"):
            return desk / "scratch"
    return STAGING_DIR


def vault_from_daemon() -> str:
    try:
        return str((_agentmd(["status"]) or {}).get("vault") or "")
    except DaemonUnavailable:
        return ""


def main(argv: list = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    vault = os.environ.get("MEMORY_VAULT_PATH") or vault_from_daemon()
    if not vault:
        print("dreaming-scorecard: no vault. Set $MEMORY_VAULT_PATH, or start "
              "the daemon so it can say which vault it is serving.",
              file=sys.stderr)
        return 2

    dated, stable = build(Path(vault), now=datetime.now(timezone.utc),
                          rel=diagnostics_dir(), staging=staging_dir())
    print(f"dreaming-scorecard: wrote {dated}")
    print(f"dreaming-scorecard: and {stable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
