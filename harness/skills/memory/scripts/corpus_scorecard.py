#!/usr/bin/env python3
"""The nightly corpus health scorecard.

What the memory *is*, as opposed to what last night's run *did* — that second
report is the dreaming scorecard and lives beside this one. This is memory
statistics, the diversity meters, the retrieval numbers, component health, and
the memory context graph, written date-marked to `<vault>/desk/diagnostics/`
along with a stable `latest_health_scorecard.md` that a brief can link.

Not `scripts/health/health_score.py`. That name is already taken by the
harness's own capability scorecard — a different subject (are the designed
capabilities built and passing), a different destination
(`~/.cache/agentm/telemetry/`), and its own determinism gate. Two reports, two
names, one of which was here first.

# Nothing is fabricated

The rule this file is built around, and the reason for the `Reading` type below:
every number is either present *with the command that produced it*, or absent
*with the reason*. There is no third state and no default.

A zero standing in for "not measured" is the specific failure that makes a
dashboard worse than nothing. Nobody investigates a green number. A completeness
score of 0.00 on a corpus nobody has graded reads as a catastrophe; a diversity
meter of 0.00 on a corpus with no embedder reads as perfect variety. Both are the
same absence wearing two different disguises, and both would be believed.
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

# Where the two scorecards live, relative to the *memory root* rather than the
# vault root. The design writes them to `Agent/desk/diagnostics/`, and `Agent` is
# the memory root — joining this onto the vault path instead produces a new
# top-level directory beside it, which is what the first run of this file did.
DIAGNOSTICS_DIR = Path("desk") / "diagnostics"
STABLE_NAME = "latest_health_scorecard.md"
COMPLETENESS_RESULT_NAME = "latest_completeness.json"


def _cell(value) -> str:
    """One table cell's text, with pipes escaped.

    A `|` inside a cell ends the cell, so a reason that named a shell pipeline
    silently truncated its own row and left the table a column short. Escaped
    here rather than at each call site, because the next reason to contain one
    will not remember to.
    """
    return str(value).replace("|", r"\|")


@dataclass
class Reading:
    """One number, or one honest absence.

    `value` and `missing` are mutually exclusive by construction — a Reading is
    built through one of the two constructors below, never by hand.
    """

    label: str
    value: Optional[Any] = None
    unit: str = ""
    source: str = ""
    missing: str = ""
    note: str = ""

    @classmethod
    def measured(cls, label, value, *, source, unit="", note=""):
        return cls(label=label, value=value, unit=unit, source=source, note=note)

    @classmethod
    def unavailable(cls, label, why, *, source=""):
        """A number nobody has. `why` is shown verbatim in the report.

        Phrase it as what would make it available, not as an error. "no audit has
        been run" tells the reader what to do; "None" does not.
        """
        return cls(label=label, missing=why, source=source)

    def render(self) -> str:
        if self.missing:
            return (f"| {_cell(self.label)} | — | "
                    f"not measured: {_cell(self.missing)} |")
        shown = self.value
        if isinstance(shown, float):
            shown = f"{shown:.4f}"
        if self.unit:
            shown = f"{shown} {self.unit}"
        detail = self.note or f"`{self.source}`"
        return f"| {_cell(self.label)} | {_cell(shown)} | {_cell(detail)} |"


@dataclass
class Section:
    title: str
    readings: list = field(default_factory=list)
    blurb: str = ""

    def render(self) -> str:
        out = [f"## {self.title}", ""]
        if self.blurb:
            out += [self.blurb, ""]
        if not self.readings:
            out += ["Nothing to report yet.", ""]
            return "\n".join(out)
        out += ["| | | |", "|---|---|---|"]
        out += [r.render() for r in self.readings]
        out.append("")
        return "\n".join(out)


class DaemonUnavailable(RuntimeError):
    """The daemon could not answer.

    Raised rather than defaulted, for the reason in the module docstring: a
    scorecard that quietly reported zeros when the daemon was down would look
    exactly like a scorecard reporting a corpus in trouble.
    """


def _agentmd(args: list) -> Any:
    """Ask the daemon one question and parse its JSON.

    Returns the parsed answer, or raises DaemonUnavailable with what went wrong
    — the caller turns that into a `Reading.unavailable` carrying the reason.
    """
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


# ── the sections ────────────────────────────────────────────────────────────

def section_corpus() -> Section:
    """How much memory there is, and how much of it is waiting."""
    s = Section("The corpus", blurb=(
        "How much there is, and how much of it is still waiting to be filed."))
    try:
        status = _agentmd(["status"])
    except DaemonUnavailable as exc:
        s.readings.append(Reading.unavailable("documents", str(exc)))
        return s

    index = (status or {}).get("index_detail") or {}
    health = (status or {}).get("health") or {}
    queue = health.get("queue") or {}

    if "documents" in index:
        s.readings.append(Reading.measured(
            "documents indexed", index["documents"], source="agentmd status"))
    else:
        s.readings.append(Reading.unavailable(
            "documents indexed", "the index reported no document count"))

    if "unfiled" in queue:
        s.readings.append(Reading.measured(
            "unfiled and waiting", queue["unfiled"], source="agentmd status",
            note="`agentmd status` — filing has not run over these"))
        # The corpus that predates the current capture path is reported apart
        # from what has accumulated since. One is a backlog somebody inherited
        # and one is a queue that is not draining, and a single total hides
        # which of the two a number describes.
        if queue.get("inherited"):
            s.readings.append(Reading.measured(
                "of which inherited", queue["inherited"], source="agentmd status",
                note=f"captured before the baseline; oldest "
                     f"{queue.get('inherited_oldest_age', 'unknown')}"))
        if queue.get("since"):
            s.readings.append(Reading.measured(
                "since the baseline", queue["since"], source="agentmd status",
                note=f"written since {(queue.get('baseline') or '')[:10]}"))
    else:
        s.readings.append(Reading.unavailable(
            "unfiled and waiting", "the queue reported no count"))

    if queue.get("oldest_age"):
        s.readings.append(Reading.measured(
            "oldest unfiled item", queue["oldest_age"], source="agentmd status"))
    return s


def section_completeness(out_dir: Path = None) -> Section:
    """Whether distillation kept what mattered.

    Read from the grading pass's last result rather than graded here. The number
    costs one model call per sampled note, and a scorecard that spends money
    every time somebody renders it is a scorecard nobody renders.
    """
    s = Section("Completeness", blurb=(
        "Whether enrichment kept what the source actually said, graded against "
        "the source claim by claim."))
    path = completeness_result_path(out_dir)
    if path is None or not path.exists():
        s.readings.append(Reading.unavailable(
            "claim-level coverage",
            "no grading run yet — `completeness_grade.py` writes one"))
        return s
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        s.readings.append(Reading.unavailable("claim-level coverage", str(exc)))
        return s

    summary = data.get("summary") or {}
    if summary.get("coverage") is None:
        s.readings.append(Reading.unavailable(
            "claim-level coverage",
            f"the last run graded none of its {summary.get('notes', 0)} note(s)"))
        return s

    scored, notes = summary.get("scored", 0), summary.get("notes", 0)
    s.readings.append(Reading.measured(
        "claim-level coverage", summary["coverage"], source=path.name,
        note=f"{scored} of {notes} sampled note(s), "
             f"{summary.get('replicates', 1)} replicate(s) each"))
    if summary.get("ungraded"):
        # Said out loud rather than folded into the average, because a run the
        # judge could not answer is a different fact from a corpus that lost
        # its content.
        s.readings.append(Reading.measured(
            "notes the judge could not grade", summary["ungraded"],
            source=path.name, note="excluded from the average, not scored zero"))
    s.readings.append(Reading.measured(
        "widest spread across replicates", summary.get("max_spread", 0.0),
        source=path.name,
        note="0 means every replicate of every note agreed"))

    for cls, row in sorted((summary.get("by_class") or {}).items()):
        s.readings.append(Reading.measured(
            f"coverage · {cls}", row["coverage"], source=path.name,
            note=f"n={row['n']}"))
    return s


def completeness_result_path(out_dir: Path = None) -> Path:
    """Where the grading pass leaves its last result.

    Taken from the directory `build` already resolved, not rebuilt from a root.
    The first version of this joined `desk/diagnostics` onto the vault path and
    looked under `/…/Vault/desk/` while the file sat in `/…/Vault/Agent/desk/` —
    the same vault-root-versus-memory-root confusion `diagnostics_dir` was
    written to stop, arrived at by a different door.
    """
    if out_dir is None:
        return None
    return Path(out_dir) / COMPLETENESS_RESULT_NAME


def section_meters() -> Section:
    """Whether the corpus is starting to sound like itself."""
    s = Section("Diversity", blurb=(
        "A corpus written by a model drifts toward itself, and no single note "
        "looks wrong while it happens. These four move when it does."))
    try:
        m = _agentmd(["meters"])
    except DaemonUnavailable as exc:
        for label in ("trigram concentration", "lexical diversity",
                      "pairwise similarity", "nearest-neighbour dispersion"):
            s.readings.append(Reading.unavailable(label, str(exc)))
        return s

    m = m or {}
    window = ""
    if m.get("from"):
        window = f"{m['from'][:10]} to {m['to'][:10]}"
    s.readings.append(Reading.measured(
        "notes measured", m.get("sample", 0), source="agentmd meters",
        note=f"`agentmd meters` — window {window}" if window else "`agentmd meters`"))

    s.readings.append(Reading.measured(
        "trigram concentration", m.get("trigram_concentration"),
        source="agentmd meters", note="rising means house phrasing"))
    s.readings.append(Reading.measured(
        "lexical diversity", m.get("lexical_diversity"),
        source="agentmd meters", note="falling means a narrowing vocabulary"))

    # The dense pair is absent rather than zero when the arm is not there, and
    # the daemon already says why — relayed verbatim rather than re-worded, so
    # the reason a reader sees is the reason the daemon gave.
    unavailable = {u.split(":", 1)[0].strip(): u for u in (m.get("unavailable") or [])}
    if m.get("pairwise_similarity"):
        s.readings.append(Reading.measured(
            "pairwise similarity", m["pairwise_similarity"]["median"],
            source="agentmd meters", note="median; rising means converging"))
    else:
        s.readings.append(Reading.unavailable(
            "pairwise similarity",
            unavailable.get("pairwise similarity", "no vectors to measure")))
    if m.get("dispersion"):
        s.readings.append(Reading.measured(
            "nearest-neighbour dispersion", m["dispersion"]["median"],
            source="agentmd meters", note="median distance; falling means converging"))
    else:
        s.readings.append(Reading.unavailable(
            "nearest-neighbour dispersion",
            unavailable.get("dispersion", "no vectors to measure")))
    return s


def section_retrieval(repo: Path) -> Section:
    """Whether the memory can still be found.

    Read from the pinned baseline that `check-retrieval-regression` already
    guards, rather than re-running the gold set here. One gold-set reader, one
    number; a second would drift from the first and nobody would know which to
    believe.
    """
    s = Section("Retrieval", blurb=(
        "Measured against the frozen gold set. The pinned baseline is what "
        "`check-retrieval-regression` guards on every commit."))
    pinned = repo / "scripts/health/fixtures/week1-gold/shipped-baseline.json"
    if not pinned.exists():
        s.readings.append(Reading.unavailable(
            "gold-set R@5", f"no pinned baseline at {pinned.name}"))
        return s
    try:
        data = json.loads(pinned.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        s.readings.append(Reading.unavailable("gold-set R@5", str(exc)))
        return s

    k = data.get("k", 5)
    s.readings.append(Reading.measured(
        f"gold-set R@{k}", data.get("r_at_k"), source=pinned.name,
        note=f"{data.get('hits')} of {data.get('scored')} scored questions"))
    if data.get("r_at_1") is not None:
        s.readings.append(Reading.measured(
            "gold-set R@1", data.get("r_at_1"), source=pinned.name,
            note=f"{data.get('hits_at_1')} first-slot hits — informational; "
                 f"the ordering headroom lives here"))
    if data.get("avg_rank_to_first_hit") is not None:
        s.readings.append(Reading.measured(
            "average rank of the first hit", data["avg_rank_to_first_hit"],
            source=pinned.name))
    if data.get("false_positives") is not None:
        s.readings.append(Reading.measured(
            "false positives on negatives", data["false_positives"],
            source=pinned.name,
            note=f"of {data.get('negatives')} questions that should return nothing"))
    return s


def section_coverage() -> Section:
    """How much of the corpus each stage has actually processed."""
    s = Section("Coverage", blurb=(
        "What the stages have done, from the coverage ledger."))
    try:
        rep = _agentmd(["ledger", "--pending", "--limit", "0"])
    except DaemonUnavailable as exc:
        s.readings.append(Reading.unavailable("enrichment coverage", str(exc)))
        return s
    rep = rep or {}
    eligible = rep.get("eligible", 0)
    current = rep.get("current", 0)
    if eligible:
        s.readings.append(Reading.measured(
            "enrichment coverage", f"{current} / {eligible}",
            source="agentmd ledger --pending",
            note=f"under contract `{(rep.get('rules_hash') or '')[:12]}`"))
    else:
        s.readings.append(Reading.unavailable(
            "enrichment coverage",
            "no eligible population — nothing is waiting for enrichment"))
    return s


def section_graph(vault: Path, out_dir: Path) -> Section:
    """The shape of what links to what."""
    s = Section("The memory graph", blurb=(
        "Every resolved link in the corpus, laid out by force. Colour is class; "
        "size is how much points at it."))
    svg = out_dir / "memory-graph.svg"
    try:
        rep = _agentmd(["graph", "--render", str(svg)])
    except DaemonUnavailable as exc:
        s.readings.append(Reading.unavailable("linked notes", str(exc)))
        return s
    rep = rep or {}
    s.readings.append(Reading.measured(
        "linked notes", rep.get("nodes", 0), source="agentmd graph --render"))
    s.readings.append(Reading.measured(
        "links between them", rep.get("edges", 0), source="agentmd graph --render"))
    if rep.get("dropped"):
        s.readings.append(Reading.measured(
            "not drawn", rep["dropped"], source="agentmd graph --render",
            note=f"below the {rep.get('cap')}-node cap"))
    return s


# ── the report ──────────────────────────────────────────────────────────────

def render(sections: list, *, now: datetime, vault: Path, tz=None) -> str:
    stamp = now.astimezone(tz).strftime("%Y-%m-%d")
    out = [
        "---",
        "title: Corpus health scorecard",
        "kind: report",
        f"date: {stamp}",
        "---",
        "",
        f"# Corpus health — {stamp}",
        "",
        "What the memory *is*. What last night's run *did* is the dreaming "
        "scorecard, beside this one.",
        "",
        "Every row is either a number with the command that produced it, or a "
        "dash with the reason there is no number. Nothing here is defaulted.",
        "",
    ]
    for s in sections:
        out.append(s.render())
    out += [
        "![memory graph](memory-graph.svg)",
        "",
        "---",
        "",
        f"Written {now.strftime('%Y-%m-%d %H:%M')}Z from `{vault}`.",
        "",
    ]
    return "\n".join(out)


def build(vault: Path, repo: Path, *, now: datetime, rel: Path = None,
          tz=None) -> tuple:
    """Assemble the scorecard and write both copies. Returns (dated, stable).

    `tz` is whose day the filename is named for; None means the machine's own.
    An argument rather than ambient process state, because a test that has to
    mutate the environment to ask this question cannot run on Windows — where
    `time.tzset()` does not exist — and because a decision about whose day it is
    belongs in the signature rather than in the environment.
    """
    out_dir = vault / (rel if rel is not None else DIAGNOSTICS_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    sections = [
        section_corpus(),
        section_completeness(out_dir),
        section_meters(),
        section_retrieval(repo),
        section_coverage(),
        section_graph(vault, out_dir),
    ]
    body = render(sections, now=now, vault=vault, tz=tz)

    # Local date in the name, UTC in the body. A nightly run late in the evening
    # is stamped tomorrow in UTC, and "last night's scorecard" should not be the
    # file dated the day after last night.
    dated = out_dir / f"{now.astimezone(tz).strftime('%Y-%m-%d')}-health-scorecard.md"
    stable = out_dir / STABLE_NAME
    dated.write_text(body, encoding="utf-8")
    # A copy rather than a symlink: the vault syncs across machines and through
    # git, and a symlink is a different thing on the other side of both.
    stable.write_text(body, encoding="utf-8")
    return dated, stable


def diagnostics_dir() -> Path:
    """Where the two scorecards go, vault-relative.

    Derived from the configured `projects` space, whose parent is the desk the
    design puts these beside: `Agent/desk/projects` gives `Agent/desk`, so the
    reports land at `Agent/desk/diagnostics`.

    Asked of the daemon rather than reassembled from a root, and never cached.
    The first version of this joined `desk/diagnostics` straight onto the vault
    path and wrote a brand-new top-level directory beside `Agent/` — the vault
    root and the memory root are different directories, and every vault-relative
    path built from the wrong one lands somewhere plausible that nothing reads.
    """
    try:
        spaces = (_agentmd(["status"]) or {}).get("spaces") or {}
    except DaemonUnavailable:
        spaces = {}
    projects = str(spaces.get("projects") or "").strip("/")
    if projects:
        desk = Path(projects).parent
        if str(desk) not in (".", "/"):
            return desk / "diagnostics"
    # No configured projects space: the flat layout, where desk is top level.
    return DIAGNOSTICS_DIR


def vault_from_daemon() -> str:
    """Where the daemon says the vault is.

    Asked rather than resolved here, for two reasons. The daemon is the component
    that cannot be wrong about which vault it is serving — a second resolver
    would agree with it until somebody edited the config while it was running.
    And a script under `harness/skills/` may not import from `scripts/`, which
    `check-one-way-imports` enforces and which caught the first version of this.
    """
    try:
        return str((_agentmd(["status"]) or {}).get("vault") or "")
    except DaemonUnavailable:
        return ""


def main(argv: list = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    repo = Path(__file__).resolve().parents[4]

    vault = os.environ.get("MEMORY_VAULT_PATH") or vault_from_daemon()
    if not vault:
        print("corpus-scorecard: no vault. Set $MEMORY_VAULT_PATH, or start the "
              "daemon so it can say which vault it is serving.", file=sys.stderr)
        return 2

    dated, stable = build(Path(vault), repo, now=datetime.now(timezone.utc),
                          rel=diagnostics_dir())
    print(f"corpus-scorecard: wrote {dated}")
    print(f"corpus-scorecard: and {stable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
