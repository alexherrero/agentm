#!/usr/bin/env python3
"""morning_note.py — one note a morning (agentm-vault plan 04, task 6).

The last step of the night. It reads what the night's other steps left behind
and writes one page: `<memory root>/diagnostics/morning/YYYY-MM-DD.md`, with a
`latest_morning_note.md` mirror beside it. The same page is the daily email's
body, and its first section is the line the session brief shows. It replaces
the dreaming scorecard and the daily observability digest.

Four sections, each left out when it has nothing to say:

  1. **What ran.** Enrichment as judged · filed active · below the floor ·
     sank · calls · tokens · model; the dreaming binary's seven jobs; the
     Python cycle's findings; and which nightly steps did not run, and why.
  2. **What needs you.** Unfiled notes the batch judged below the floor,
     possible twins, shared keys, proposed facets, the binary's archive
     candidates, and what sank this week — counts and the first five of each,
     with the needs-review map holding the full lists.
  3. **The corpus**, in one line: class populations, the queue and its oldest
     item, enrichment coverage, and a link to the day's corpus scorecard.
  4. **Spend.** Last night's tokens against the tier's line, the seven-day
     total, and session spend when there was any.

Everything here is read from files the night's steps already write — the
enrichment run record, the binary's last report, the Python cycle's report and
findings, the lifecycle journal, the runner's markers — plus two questions to
the daemon for the corpus line. A step that did not run is a sentence saying so
and why, never a row of zeros.

CLI: `python3 morning_note.py [--vault-path <memory root>]`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

# Bootstrap this directory before any sibling import: a foreign loader
# (crickets' bridges file-path-load memory-skill modules) has none of it on
# sys.path. Pinned by scripts/test_skill_modules_file_loadable.py.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import corpus_scorecard  # noqa: E402  (same skill dir)
import engine_state  # noqa: E402
import lifecycle_transitions  # noqa: E402
import needs_review  # noqa: E402

DIAGNOSTICS_DIR = Path("diagnostics") / "morning"
STABLE_NAME = "latest_morning_note.md"

# The night window every nightly step carries (templates/jobs/*.yaml), as
# local minutes. The note's "last night" is everything since its last opening.
NIGHT_WINDOW = (2 * 60, 6 * 60)

# The nightly steps, by runner job name, in the night's order.
NIGHT_JOBS = (
    ("enrich-nightly", "enrichment"),
    ("dreaming", "the dreaming binary"),
    ("dream", "the Python cycle"),
    ("corpus-scorecard", "the corpus scorecard"),
)

# How many items each "what needs you" list shows before "and N more".
FIRST = 5

# How long to wait before reading coverage a second time, when the first answer
# says nothing is eligible over a corpus that has cards. The note runs right
# after the batch rewrites notes, and one read can land while the daemon is
# still reconciling them.
COVERAGE_REREAD_PAUSE = 2.0

# The operator's budget (agentm-vault § Dreaming, Q2): a token line per night
# per tier, and a call guard. The Go constants StrongTokenLine, CheapTokenLine
# and CallGuard are the enforcing copy, and a test holds these equal. A run
# record carries the line it ran under, which a hand run may have lowered with
# a flag, so the note compares the night against these rather than the last
# run's flags.
OPERATOR_LINES = {"strong": 2_000_000, "cheap": 2_000_000}
CALL_GUARD = 250

ENRICH_RUNS = "enrich-runs.jsonl"
# The weekly crystallize phase keeps its own record beside enrichment's, because
# it is a different job on a different cadence and folding it into the
# enrichment row would make one night's batch look like it judged notes it never
# saw. The two are read together only in the spend section, where the design
# asks for a line per job (agentm-vault, "loose ends from session 4").
CRYSTALLIZE_RUNS = "crystallize-runs.jsonl"

# The tier table's job names, in the order the spend section lists them, with
# the words a reader wants rather than the table's slugs.
JOB_NAMES = {
    "classify-unfiled": "enrichment deep",
    "summarize": "enrichment light",
    "crystallize": "crystallize",
}
LAST_REPORT = Path("dreaming") / "last-report.json"
# `dream.CYCLE_REPORT_NAME`; a test holds the two equal.
CYCLE_REPORT = Path("dreaming") / "python-cycle.json"

DEFAULT_ROLLUP = Path.home() / ".cache" / "agentm" / "telemetry" / "rollup.db"


def default_runner_state() -> Path:
    """`~/.cache/agentm/runner`, honouring `XDG_CACHE_HOME`, read on every
    call: the rule the runner's own `state.default_state_root` follows, kept
    as a copy because the skill's scripts stand alone once installed. A test's
    moved cache root moves the note's reading of the night with the runner's
    markers; a machine with no such variable reads where it always has."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "agentm" / "runner"


# ── the clock ────────────────────────────────────────────────────────────────

def night_start(now: float) -> float:
    """The most recent opening of the night window at or before `now`.

    Calendar arithmetic on local wall-clock time, the way the runner computes
    the same opening, so a daylight-saving night lands on 02:00 rather than an
    hour off it."""
    t = datetime.fromtimestamp(now)
    opening = t.replace(hour=NIGHT_WINDOW[0] // 60, minute=NIGHT_WINDOW[0] % 60,
                        second=0, microsecond=0)
    if opening > t:
        opening -= timedelta(days=1)
    return opening.timestamp()


def _age(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    return f"{int(seconds // 86400)} d ago"


# agentmd writes a run's `at` as Go's RFC3339Nano, which trims a fraction's
# trailing zeros, so the fraction runs from one digit to nine
# (`2026-09-14T01:26:17.80599Z`). fromisoformat takes only three or six before
# Python 3.11, and macOS's python3 is 3.9, so on 2026-09-13 a run whose `at` did
# not parse fell out of the note: it read 61 judged over two runs and missed a
# third, with its $3.16. The fraction is cut or padded to six digits first.
_FRACTION = re.compile(r"(\d{2}:\d{2}:\d{2})\.(\d+)")


def _epoch(value) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        text = _FRACTION.sub(lambda m: f"{m.group(1)}.{m.group(2)[:6].ljust(6, '0')}",
                             value.replace("Z", "+00:00"), count=1)
        try:
            return datetime.fromisoformat(text).timestamp()
        except ValueError:
            return None
    return None


# ── what the night left behind ───────────────────────────────────────────────

def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _runs_file(path: Path) -> list:
    """One JSONL record of runs, oldest first, each line stamped with `_at`."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            run = json.loads(line)
        except ValueError:
            continue
        at = _epoch(run.get("at")) if isinstance(run, dict) else None
        if at is not None:
            run["_at"] = at
            out.append(run)
    return sorted(out, key=lambda r: r["_at"])


def enrich_runs(engine_dir: Path) -> list:
    """Every run the enrichment record holds, oldest first, each with `_at`."""
    return _runs_file(Path(engine_dir) / ENRICH_RUNS)


def crystallize_runs(engine_dir: Path) -> list:
    """Every run the weekly phase recorded, oldest first, each with `_at`."""
    return _runs_file(Path(engine_dir) / CRYSTALLIZE_RUNS)


def binary_report(engine_dir: Path) -> Optional[dict]:
    """The dreaming binary's last completed pass, with the file's `_mtime`."""
    p = Path(engine_dir) / LAST_REPORT
    data = _read_json(p)
    if not isinstance(data, dict) or not data.get("run_id"):
        return None
    try:
        data["_mtime"] = p.stat().st_mtime
    except OSError:
        return None
    return data


def python_cycle(engine_dir: Path) -> Optional[dict]:
    data = _read_json(Path(engine_dir) / CYCLE_REPORT)
    if not isinstance(data, dict) or _epoch(data.get("at")) is None:
        return None
    data["_at"] = _epoch(data.get("at"))
    return data


def runner_state(state_dir: Path) -> tuple:
    """(markers by job, the last cycle's outcomes by job, when that cycle started)."""
    markers = {}
    for job, _label in NIGHT_JOBS:
        m = _read_json(Path(state_dir) / f"{job}.json")
        if isinstance(m, dict):
            markers[job] = m
    outcomes = {}
    cycle = _read_json(Path(state_dir) / "last-cycle.json")
    if not isinstance(cycle, dict):
        cycle = {}
    rows = cycle.get("outcomes")
    for o in rows if isinstance(rows, list) else []:
        if isinstance(o, dict) and o.get("job"):
            outcomes[o["job"]] = o
    return markers, outcomes, _epoch(cycle.get("at"))


def session_spend(rollup: Path, now: float) -> Optional[tuple]:
    """(cost, events) the observability rollup recorded in the last day, or
    None when there is no rollup to read. Read as data: the ladder's own code
    lives outside this skill and is not imported."""
    p = Path(rollup)
    if not p.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            rows = conn.execute("SELECT window_start, cost_usd, event_count FROM by_window").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    cost, events = 0.0, 0
    for start, c, n in rows:
        at = _epoch(start)
        if at is None or at < now - 86400:
            continue
        cost += float(c or 0)
        events += int(n or 0)
    return cost, events


# ── the night, gathered ──────────────────────────────────────────────────────

Ask = Callable[[list], object]


@dataclass
class Night:
    now: float
    start: float
    tonight_runs: list = field(default_factory=list)
    week_runs: list = field(default_factory=list)
    crystallize_tonight: list = field(default_factory=list)
    crystallize_week: list = field(default_factory=list)
    binary: Optional[dict] = None
    binary_tonight: bool = False
    python: Optional[dict] = None
    ran: dict = field(default_factory=dict)       # job -> ran since the opening
    reasons: dict = field(default_factory=dict)   # job -> why it did not
    below_floor: list = field(default_factory=list)
    proposals: dict = field(default_factory=dict)
    sank: list = field(default_factory=list)
    populations: Optional[dict] = None
    queue: Optional[dict] = None
    queue_missing: str = ""
    coverage: Optional[dict] = None
    coverage_missing: str = ""
    scorecard: str = ""
    sessions: Optional[tuple] = None


# The reasons a cycle gives whatever the hour, because it reads the switch and
# the watchdog before it looks at the window. Every other reason is that
# cycle's reading of its own moment: outside the window, not due, over the
# ceiling.
STANDING_REASONS = ("disabled", "watchdog-stop")


def _skip_reason(job: str, outcomes: dict, registered: bool, *, cycle_tonight: bool) -> str:
    o = outcomes.get(job)
    if o is None:
        return "not registered" if not registered else "no cycle has reported it"
    if not cycle_tonight and o.get("skipped_reason") not in STANDING_REASONS:
        return "no reason on record for the night"
    if o.get("dry_run"):
        return "dry run"
    if o.get("skipped_reason"):
        return str(o["skipped_reason"])
    if o.get("ran") and o.get("exit_code") not in (0, None):
        return f"exited {o.get('exit_code')}"
    return "not due"


def _cards(populations: Optional[dict]) -> int:
    return sum(flat for flat, _lanes in (populations or {}).values())


def _coverage(ask: Ask, populations: Optional[dict], pause: float) -> tuple:
    """The ledger's coverage, read twice when the first answer cannot be right.

    Zero eligible over a corpus that holds cards is not a measurement: on
    2026-09-11 the note read "0 of 0" straight after a batch while the ledger
    held 18 of 183, and the same call a minute later answered correctly. So an
    inconsistent answer is read once more after a pause, and one that stays
    inconsistent is reported as not measured rather than printed as a zero."""
    def read():
        try:
            return ask(["ledger", "--pending", "--limit", "0"]) or {}, ""
        except corpus_scorecard.DaemonUnavailable as exc:
            return None, str(exc)

    cov, missing = read()
    if cov is None:
        return None, missing
    if not cov.get("eligible") and _cards(populations):
        time.sleep(pause)
        cov, missing = read()
        if cov is None:
            return None, missing
        if not cov.get("eligible"):
            return None, (f"the ledger answered 0 eligible twice over a corpus of "
                          f"{_cards(populations)} cards — read it again")
    return cov, ""


def gather(vault: Path, *, now: float, engine_dir: Path, runner_dir: Path,
           rollup: Path, out_dir: Path, ask: Ask, pause: float = COVERAGE_REREAD_PAUSE) -> Night:
    start = night_start(now)
    night = Night(now=now, start=start)

    runs = enrich_runs(engine_dir)
    night.tonight_runs = [r for r in runs if r["_at"] >= start]
    night.week_runs = [r for r in runs if r["_at"] >= now - 7 * 86400]
    cryst = crystallize_runs(engine_dir)
    night.crystallize_tonight = [r for r in cryst if r["_at"] >= start]
    # Seven days rather than tonight for the lesson list as well: the phase runs
    # weekly, and a note that only listed what last night wrote would name the
    # week's lessons on one morning in seven and say nothing on the other six.
    night.crystallize_week = [r for r in cryst if r["_at"] >= now - 7 * 86400]
    night.binary = binary_report(engine_dir)
    night.binary_tonight = bool(night.binary and night.binary["_mtime"] >= start)
    cycle = python_cycle(engine_dir)
    night.python = cycle if cycle and cycle["_at"] >= start else None

    markers, outcomes, cycle_at = runner_state(runner_dir)
    # A cycle writes its account only when it ends, and the note runs inside
    # one, so the account on disk is an earlier cycle's. Only a cycle that
    # started inside the window saw the night. On 2026-09-13 the note read the
    # 01:52 cycle, where every night step was outside the window, and gave that
    # as the reason enrichment did not run; the 02:22 cycle it ran in had held
    # the batch at the budget ceiling, and the 02:56 cycle ran it.
    closes = datetime.fromtimestamp(start).replace(
        hour=NIGHT_WINDOW[1] // 60, minute=NIGHT_WINDOW[1] % 60).timestamp()
    cycle_tonight = cycle_at is not None and start <= cycle_at < closes
    today = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    scorecard = out_dir.parent / "health" / f"{today}-health-scorecard.md"
    for job, _label in NIGHT_JOBS:
        m = markers.get(job) or {}
        last = m.get("last_real_run", m.get("last_run")) if m.get("status") == "done" else None
        ran = last is not None and float(last) >= start
        # What a step left behind counts as well as the runner's word: a batch
        # run by hand inside the window ran, whatever its manifest says.
        if job == "enrich-nightly":
            ran = ran or bool(night.tonight_runs)
        elif job == "dreaming":
            ran = ran or night.binary_tonight
        elif job == "dream":
            ran = ran or night.python is not None
        elif job == "corpus-scorecard":
            ran = ran or (scorecard.is_file() and scorecard.stat().st_mtime >= start)
        night.ran[job] = ran
        if not ran:
            night.reasons[job] = _skip_reason(job, outcomes, registered=bool(m),
                                              cycle_tonight=cycle_tonight)
    if scorecard.is_file():
        night.scorecard = scorecard.stem

    night.below_floor = [e for e in needs_review.collect(vault)
                         if "unfiled" in e.reasons and e.judged]
    night.proposals = needs_review.read_proposals(engine_dir)
    try:
        summary = lifecycle_transitions.summarize(
            vault, now=today, journal=Path(engine_dir) / lifecycle_transitions.JOURNAL_NAME)
        night.sank = [e.get("rel", "") for e in summary["entries"]["sank"] if e.get("rel")]
    except Exception:  # a journal that will not read costs the note one list
        night.sank = []

    night.populations = corpus_scorecard.class_populations(vault)
    try:
        status = ask(["status"]) or {}
        night.queue = (status.get("health") or {}).get("queue") or {}
    except corpus_scorecard.DaemonUnavailable as exc:
        night.queue_missing = str(exc)
    night.coverage, night.coverage_missing = _coverage(ask, night.populations, pause)
    night.sessions = session_spend(rollup, now)
    return night


# ── the sections ─────────────────────────────────────────────────────────────

def _link(rel: str) -> str:
    return f"[[{Path(str(rel)).stem}]]"


def _first(items: list, fmt) -> str:
    shown = [fmt(i) for i in items[:FIRST]]
    more = len(items) - FIRST
    return ", ".join(shown) + (f", and {more} more" if more > 0 else "")


def _sum(runs: list, key: str) -> int:
    return sum(int(r.get(key) or 0) for r in runs)


def _verdicts(runs: list, key: str) -> int:
    return sum(int((r.get("verdicts") or {}).get(key) or 0) for r in runs)


def _enrichment_line(runs: list) -> str:
    models = sorted({r.get("model") for r in runs if r.get("model")})
    # Project records are merged into rather than filed (agentm-vault plan 09), so
    # they sit beside the filing verdicts instead of inside them. A night that
    # merged none reads as it always did.
    records = _verdicts(runs, "records")
    merged = f"{records} project records · " if records else ""
    line = (f"**Enrichment** — {_sum(runs, 'notes_sent')} judged · "
            f"{_verdicts(runs, 'filed_active')} filed active · "
            f"{_verdicts(runs, 'below_floor')} below the floor · "
            f"{_verdicts(runs, 'sank')} sank · {merged}{_sum(runs, 'model_calls')} calls · "
            f"{sum(_tier_added(runs).values()):,} tokens against the line · "
            f"{', '.join(models) or 'no model recorded'}")
    failed = _sum(runs, "failed")
    if failed:
        line += f" · {failed} failed"
    stopped = [r["stopped_by"] for r in runs if r.get("stopped_by")]
    if stopped:
        line += f". Stopped by {stopped[-1]}"
    return line + "." + _refusal_sentence(runs)


def _refusal_sentence(runs: list) -> str:
    """What the post-gates refused, and what not asking again saved.

    A refused card leaves no stamp, so it comes back around every night unless
    something remembers. `refusals_open` is what the last run saw standing and
    `refused` is how many of them it declined for free instead of paying twice
    to be told no again. Silence here means nothing is standing, which is the
    state worth being able to tell apart from nobody counting."""
    if not runs:
        return ""
    standing = int(runs[-1].get("refusals_open") or 0)
    if not standing:
        return ""
    skipped = _sum(runs, "refused")
    out = f" {standing} card(s) stand refused at this pass"
    if skipped:
        out += f", {skipped} of them skipped free rather than judged again"
    return out + "."


def _binary_rows(rep: dict) -> list:
    """The binary's seven jobs, one row each."""
    n = lambda key, d: len(d.get(key) or [])
    plan = rep.get("plan") or {}
    copies = rep.get("copies") or {}
    refile = rep.get("refile") or {}
    promote = rep.get("promote") or {}
    cal = rep.get("calendar") or {}
    mocs = rep.get("mocs") or {}
    dates = rep.get("dates") or {}
    changed = sum(1 for p in (mocs.get("pages") or []) if isinstance(p, dict) and p.get("changed"))
    calendar = cal.get("skipped") or f"{n('written', cal)} written of {cal.get('refreshed', 0)} checked"
    return [
        "| job | what the pass did |", "|---|---|",
        f"| lifecycle | sank {n('demoted', plan)}, revived {n('revived', plan)}, archive candidates "
        f"{n('archive_candidates', plan)}, held by cap {plan.get('skipped_by_cap', 0)} |",
        f"| copies | {n('families', copies)} families collapsed, {copies.get('deferred', 0)} deferred |",
        f"| refile | {n('moves', refile)} moves, {n('unflags', refile)} unflags, {n('blocked', refile)} blocked |",
        f"| promote | {n('promotions', promote)} new candidates, {n('existing', promote)} already carded |",
        f"| calendar | {calendar} |",
        f"| mocs | {changed} regenerated of {n('pages', mocs)} pages |",
        f"| dates | {n('glossed', dates)} glosses across {dates.get('aging', 0)} aging notes |",
    ]


def _python_line(cycle: dict) -> str:
    if cycle.get("storage_rules_ok") is False:
        return (f"**The Python cycle** — filing is halted: the contract did not parse "
                f"({cycle.get('storage_rules_error') or 'no reason recorded'}).")
    lint = cycle.get("lint") or {}
    return (f"**The Python cycle** — {cycle.get('possible_twins', 0)} possible twin(s) · "
            f"{cycle.get('same_key', 0)} shared key(s) · {cycle.get('proposed_facets', 0)} "
            f"proposed facet(s) · lint {lint.get('orphan_count', 0)} orphan(s), "
            f"{lint.get('contradiction_count', 0)} contradiction(s), "
            f"{lint.get('repairable_count', 0)} mis-cased link(s) it would repair.")


def what_ran(night: Night) -> list:
    lines = []
    if night.tonight_runs:
        lines.append(f"- {_enrichment_line(night.tonight_runs)}")
    if night.binary_tonight:
        rep = night.binary
        decision = rep.get("decision") or {}
        lines.append(f"- **The dreaming binary** — {rep.get('mode', '?')} pass, outcome "
                     f"{rep.get('outcome', '?')}; gate: {decision.get('reason', '?')}.")
        lines += [""] + _binary_rows(rep) + [""]
    elif night.ran.get("dreaming") and night.binary:
        lines.append(f"- **The dreaming binary** — ran, and its gate held; the last pass was "
                     f"{_age(night.now - night.binary['_mtime'])} ({night.binary.get('outcome', '?')}).")
    if night.python is not None:
        lines.append(f"- {_python_line(night.python)}")
    skipped = [f"{label} ({night.reasons[job]})" for job, label in NIGHT_JOBS
               if not night.ran.get(job)]
    if skipped:
        lines.append(f"- Did not run last night: {' · '.join(skipped)}.")
    return lines


def needs_you(night: Night) -> list:
    lines = []
    if night.below_floor:
        lines.append(f"- **Unfiled below the floor** ({len(night.below_floor)}): "
                     + _first(night.below_floor, lambda e: f"[[{e.slug}]]"))
    twins = night.proposals.get("twins") or []
    if twins:
        def twin(t):
            sim = t.get("similarity")
            pct = f" ({sim:.0%})" if isinstance(sim, (int, float)) else ""
            return f"{_link(t.get('a', ''))} and {_link(t.get('b', ''))}{pct}"
        lines.append(f"- **Possible twins** ({len(twins)}): " + _first(twins, twin))
    same = night.proposals.get("same_key") or []
    if same:
        lines.append(f"- **Shared keys** ({len(same)}): " + _first(
            same, lambda c: f"`{c.get('slug', '?')}` in " + " and ".join(_link(p) for p in c.get("paths") or [])))
    facets = night.proposals.get("facets") or []
    if facets:
        lines.append(f"- **Proposed facets** ({len(facets)}): " + _first(
            facets, lambda f: f"`{f.get('label', '?')}` on {f.get('days', '?')} days"))
    if night.sank:
        lines.append(f"- **Sank this week** ({len(night.sank)}): " + _first(night.sank, _link))

    # The two forward lists, each omitted when empty. What the axis is about to
    # do, before it does it: the whole of "you see a demotion before it happens"
    # is this line and the facet below it.
    #
    # `archive_candidates` used to be listed here as what the operator had to
    # confirm. The night moves them itself now, so what is worth their eye is
    # not the backlog but what is coming — a threshold can be argued with while
    # it is still thirty days off.
    plan = (night.binary or {}).get("plan") or {}
    forward = (
        ("sinking", plan.get("sinking_within_30_days") or []),
        ("archiving", plan.get("archiving_within_30_days") or []),
    )
    for what, items in forward:
        if not items:
            continue
        lines.append(f"- **{what.capitalize()} within 30 days** ({len(items)}): " + _first(
            items, lambda c: f"{_link(c.get('rel', ''))} ({int(c.get('days') or 0):,} days silent)"))

    # Every lesson the weekly phase wrote, named rather than counted.
    #
    # The re-audit trigger the design sets for this phase is "any crystallized
    # note you would not keep", and that cannot be read from a count. A lesson
    # never decays and nothing ages it out, so the morning it is written is the
    # cheapest morning to disagree with it.
    lessons = [l for r in night.crystallize_week for l in (r.get("lessons") or [])]
    if lessons:
        def lesson(l):
            stem = str(l.get("rel", "")).rsplit("/", 1)[-1][:-3]
            return f"[[{stem}]] (from {len(l.get('consolidated_from') or [])})"
        lines.append(f"- **Lessons this week** ({len(lessons)}): "
                     + _first(lessons, lesson)
                     + " — permanent learning; nothing ages one out but you.")

    facet = (night.binary or {}).get("facet") or {}
    if facet.get("rel"):
        lines.append(f"- **What the night did** ({facet.get('acts', 0)} act(s)): "
                     f"{_link(facet['rel'])} — every move, with the manifest behind any deletion.")
    if lines:
        lines.append(f"- The full lists: [[{needs_review.MOC_SLUG}]].")
    return lines


def corpus_line(night: Night) -> list:
    parts = []
    if night.populations:
        parts.append(" · ".join(f"{cls} {flat}" for cls, (flat, _lanes) in night.populations.items()))
    if night.queue is not None:
        q = night.queue
        oldest = f", the oldest {q['oldest_age']}" if q.get("oldest_age") else ""
        parts.append(f"{q.get('unfiled', 0)} awaiting a judgment{oldest}")
    elif night.queue_missing:
        parts.append(f"the queue not measured ({night.queue_missing})")
    if night.coverage:
        parts.append(f"coverage {night.coverage.get('current', 0)} of "
                     f"{night.coverage.get('eligible', 0)} stamped at this pass")
    elif night.coverage_missing:
        parts.append(f"coverage not measured ({night.coverage_missing})")
    if night.scorecard:
        parts.append(f"[[{night.scorecard}]]")
    return [" — ".join(parts)] if parts else []


def _tier_added(runs: list) -> dict:
    """What the line counts, per tier: the tokens a call added — input, cache
    writes, output — and not the cached prefix it re-read (the operator's
    ruling, 2026-09-11). Every call re-reads the same ~37,000 tokens of Claude
    Code's own baseline; counted, that constant was most of every card."""
    out: dict = {}
    for r in runs:
        for tier, u in (r.get("usage") or {}).items():
            if not isinstance(u, dict):
                continue
            n = sum(int(u.get(k) or 0) for k in (
                "input_tokens", "cache_creation_input_tokens", "output_tokens"))
            out[tier] = out.get(tier, 0) + n
    return out


def _by_job(runs: list) -> dict:
    """What each tier-table job spent across these runs.

    Read from each run's own `by_job` block, which the meter fills from the
    route the tier table chose. A run written before that block existed
    contributes nothing here and still counts in the totals — the per-job lines
    are a finer reading of the same spend, not a second accounting of it.
    """
    out: dict = {}
    for r in runs:
        for job, u in (r.get("by_job") or {}).items():
            if not isinstance(u, dict):
                continue
            added = sum(int(u.get(k) or 0) for k in (
                "input_tokens", "cache_creation_input_tokens", "output_tokens"))
            cur = out.setdefault(job, {"tokens": 0, "calls": 0, "cost": 0.0})
            cur["tokens"] += added
            cur["calls"] += int(u.get("calls") or 0)
            cur["cost"] += float(u.get("cost_usd") or 0)
    return out


def per_job(night: Night) -> list:
    """A line for each job that spent last night, in the table's own order.

    The design's ask (session 4's second loose end): "per-job lines for
    enrichment deep, enrichment light, and crystallize, alongside nightly and
    seven-day totals". The cadence re-audit for the weekly phase reads its line
    after three runs, and a single total cannot answer what that phase costs
    while the batch is spending on the same nights.
    """
    by = _by_job(night.tonight_runs)
    for job, u in _by_job(night.crystallize_tonight).items():
        cur = by.setdefault(job, {"tokens": 0, "calls": 0, "cost": 0.0})
        cur["tokens"] += u["tokens"]
        cur["calls"] += u["calls"]
        cur["cost"] += u["cost"]
    if not by:
        return []
    order = list(JOB_NAMES) + sorted(k for k in by if k not in JOB_NAMES)
    lines = []
    for job in order:
        u = by.get(job)
        if not u:
            continue
        lines.append(f"  - {JOB_NAMES.get(job, job)}: {u['tokens']:,} tokens · "
                     f"{u['calls']} call(s) · ${u['cost']:,.2f}")
    return lines


def spend(night: Night) -> list:
    lines = []
    runs = night.tonight_runs
    if runs:
        added = _tier_added(runs)
        against = " · ".join(
            f"{tier} {n:,} of {OPERATOR_LINES[tier]:,}" if tier in OPERATOR_LINES else f"{tier} {n:,}"
            for tier, n in sorted(added.items()))
        calls = _sum(runs, "model_calls")
        cost = sum(float(r.get("total_cost_usd") or 0) for r in runs)
        lines.append(f"- Last night: {sum(added.values()):,} tokens against the line"
                     + (f" ({against})" if against else "")
                     + f" · {_sum(runs, 'tokens'):,} processed"
                     + f" · {calls} calls of the {CALL_GUARD}-call guard · ${cost:,.2f}")
    lines += per_job(night)
    if night.crystallize_tonight:
        cryst = night.crystallize_tonight
        wrote = sum(len(r.get("lessons") or []) for r in cryst)
        cost = sum(float(r.get("total_cost_usd") or 0) for r in cryst)
        lines.append(f"- The weekly phase: {wrote} lesson(s) from "
                     f"{_sum(cryst, 'clusters')} recurrence(s) over the bar · "
                     f"{_sum(cryst, 'model_calls')} call(s) · ${cost:,.2f}")
    week = night.week_runs + night.crystallize_week
    if week:
        cost = sum(float(r.get("total_cost_usd") or 0) for r in week)
        lines.append(f"- Seven days: {sum(_tier_added(week).values()):,} tokens against "
                     f"the line · {_sum(night.week_runs, 'tokens'):,} processed across "
                     f"{len(week)} run(s) · ${cost:,.2f}")
    if night.sessions and (night.sessions[0] > 0 or night.sessions[1] > 0):
        lines.append(f"- Sessions, the last day: ${night.sessions[0]:,.2f} across "
                     f"{night.sessions[1]} event(s)")
    return lines


def headline(night: Night, needs: list) -> str:
    """The note's first section in one line — what the session brief shows."""
    parts = []
    if night.tonight_runs:
        runs = night.tonight_runs
        judged = (f"enrichment judged {_sum(runs, 'notes_sent')} "
                  f"({_verdicts(runs, 'filed_active')} active, "
                  f"{_verdicts(runs, 'sank')} sank)")
        # The standing refusals ride on the headline rather than only in the
        # body, because a set that grows quietly is the failure this number was
        # added to prevent, and the headline is the line that gets read.
        standing = int(runs[-1].get("refusals_open") or 0)
        if standing:
            judged += f", {standing} refused"
        parts.append(judged)
    elif "enrich-nightly" in night.reasons:
        parts.append(f"enrichment did not run ({night.reasons['enrich-nightly']})")
    if night.binary_tonight:
        parts.append(f"the binary {night.binary.get('outcome', 'ran')}")
    if night.python is not None:
        if night.python.get("storage_rules_ok") is False:
            parts.append("filing is halted")
        else:
            parts.append(f"{night.python.get('possible_twins', 0)} possible twin(s)")
    n = sum(1 for line in needs if not line.startswith("- The full lists"))
    parts.append(f"{n} list(s) need you" if n else "nothing needs you")
    return " · ".join(parts)


def render(night: Night, *, vault: Path) -> tuple:
    """(the note's text, its headline)."""
    stamp = datetime.fromtimestamp(night.now).strftime("%Y-%m-%d")
    ran, needs, corpus, money = what_ran(night), needs_you(night), corpus_line(night), spend(night)
    head = headline(night, needs)
    out = [
        "---",
        f"title: Morning {stamp}",
        "kind: report",
        f"date: {stamp}",
        # JSON-quoted, which is a valid YAML double-quoted scalar whatever the
        # line holds.
        f"headline: {json.dumps(head, ensure_ascii=False)}",
        "generated_by: morning_note.py",
        "---",
        "",
        f"# Morning — {stamp}",
        "",
    ]
    for title, body in (("What ran", ran), ("What needs you", needs),
                        ("The corpus", corpus), ("Spend", money)):
        if body:
            out += [f"## {title}", ""] + body + [""]
    if not (ran or needs or money):
        out += ["Nothing ran last night and nothing needs you.", ""]
    out += ["---", "",
            f"Written {datetime.fromtimestamp(night.now, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}Z "
            f"from `{vault}`.", ""]
    return "\n".join(out), head


def build(vault: Path, *, now: Optional[float] = None, rel: Path = None,
          engine_dir: Path = None, runner_dir: Path = None, rollup: Path = None,
          ask: Ask = None) -> tuple:
    """Gather, render and write the dated note and its mirror. Returns
    (dated, stable, headline)."""
    vault = Path(vault)
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    out_dir = vault / (rel if rel is not None else DIAGNOSTICS_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    night = gather(
        vault, now=now,
        engine_dir=Path(engine_dir) if engine_dir is not None else engine_state.engine_state_dir(),
        runner_dir=Path(runner_dir) if runner_dir is not None else default_runner_state(),
        rollup=Path(rollup) if rollup is not None else DEFAULT_ROLLUP,
        out_dir=out_dir, ask=ask or corpus_scorecard._agentmd)
    text, head = render(night, vault=vault)
    dated = out_dir / f"{datetime.fromtimestamp(now).strftime('%Y-%m-%d')}.md"
    stable = out_dir / STABLE_NAME
    dated.write_text(text, encoding="utf-8")
    # A copy rather than a symlink: the vault syncs across machines and through
    # git, and a symlink is a different thing on the other side of both.
    stable.write_text(text, encoding="utf-8")
    return dated, stable, head


def main(argv: list = None) -> int:
    ap = argparse.ArgumentParser(description="Write the morning note.")
    ap.add_argument("--vault-path", default=None, help="the memory root (overrides MEMORY_ROOT)")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    vault = args.vault_path or (os.environ.get("MEMORY_ROOT") or os.environ.get("MEMORY_VAULT_PATH")) or corpus_scorecard.memory_root_from_daemon()
    if not vault:
        print("morning-note: no memory root. Set $MEMORY_ROOT, or start the daemon "
              "so it can say which vault it is serving.", file=sys.stderr)
        return 2
    # Always the memory root's diagnostics/morning: the session brief and the
    # email read that path without asking the daemon, so the note is written
    # where they look rather than wherever a configured space would put it.
    dated, stable, head = build(Path(vault))
    print(f"morning-note: wrote {dated} and {stable.name} — {head}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
