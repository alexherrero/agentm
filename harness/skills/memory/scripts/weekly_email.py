#!/usr/bin/env python3
"""weekly_email.py — one weekly summary email, read from the week's records.

The weekly email replaces the stream of separate status emails (the daily
morning email, the weekly field brief, the daemon's non-urgent alerts) with one
message a week: a stack of cards, each with a green / yellow / red pill, at
most three items, and a link to the detail. A card that is green shrinks to a
chip; only cards that are not green are drawn in full.

It reads records, never mail. Every source it summarises already writes one:
the morning notes and scorecards under the memory root, the enrichment and
lesson-compile run logs in the engine state dir, the runner's markers, the
field brief notes, project trackers, and GitHub through `gh`. A project with
tooling of its own can drop a small JSON feed (`projects/<p>/weekly-feed.json`)
that its card shows, so the email needs to know nothing about that project's
internals.

Everything personal is configuration, under `plugins.autonomy.weekly` in
`.agentm-config.json`: the link base (a web view of the vault, since mail
clients strip `obsidian://` links), the GitHub owner, which projects get a card
of their own, and an optional homelab report to quote in one line. Absent the
key, the email still builds, with the agent's own cards and no links.

The same run writes the full report to `diagnostics/weekly/<date>.md` under the
memory root (the chips link to its sections), pushes the vault so those links
resolve, and sends through `session_email`'s mail path with an HTML part.

`--urgent` is the urgent lane's daily check: it emails straight away, once,
when the `claude` login has lapsed or the daemon does not answer, and logs the
send where the weekly email reads it ("Emailed you …"). The daemon logs its
own urgent alerts to the same file.

Never raises out of a collector: a card whose source will not read becomes a
yellow card saying so.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import claude_login  # noqa: E402
import corpus_scorecard  # noqa: E402
import engine_state  # noqa: E402
import morning_note  # noqa: E402
import needs_review  # noqa: E402
import runner_pulse  # noqa: E402
import write_meter  # noqa: E402

WEEKLY_DIR = Path("diagnostics") / "weekly"
URGENT_LOG = "urgent-log.jsonl"
URGENT_STATE = "urgent-sent.json"
CONFIG_KEY = "plugins.autonomy.weekly"

OK, LOOK, ACT = "ok", "look", "act"
_RANK = {OK: 0, LOOK: 1, ACT: 2}
VERDICT = {OK: "All good", LOOK: "Needs a look", ACT: "Needs action"}

# A tracker's Next paragraph that says the operator is the one to move.
_WAITS = re.compile(r"\b(waits? (on|for)|waiting (on|for)|needs) (the operator|you)\b"
                    r"|\b(operator'?s|your) (call|say-so|ruling|decision|answer)\b", re.IGNORECASE)
_BLOCKED = re.compile(r"\bblocked\b", re.IGNORECASE)
# What a private project's card must never carry out of the vault.
_PRIVATE = re.compile(r"[$€£]\s?\d|\b\d{1,3}(,\d{3})+\b|\b\d{4,}\b|\b\d+\s?[kKmM]\b")
STALE_REPO_DAYS = 180
AGING_PR_DAYS = 14


# ── the model ────────────────────────────────────────────────────────────────

@dataclass
class Item:
    text: str
    link: Optional[str] = None
    mark: Optional[str] = None       # ok | look | act, drawn as ✓ ▲ ■
    emailed: Optional[str] = None    # "Emailed you Wed 4 Nov, 02:10"


@dataclass
class Number:
    value: str
    label: str
    link: Optional[str] = None
    status: Optional[str] = None


@dataclass
class Tile:
    name: str
    status: str
    note: str = ""
    emailed: Optional[str] = None
    link: Optional[str] = None


@dataclass
class Meter:
    label: str
    value: float
    total: float
    status: str = OK
    mark: Optional[float] = None     # last week's value, drawn as a tick
    mark_label: str = ""
    link: Optional[str] = None


@dataclass
class Card:
    slug: str
    title: str
    icon: str
    status: str = OK
    chip: str = ""                   # the one or two words a green chip carries
    items: list = field(default_factory=list)
    numbers: list = field(default_factory=list)
    tiles: list = field(default_factory=list)
    strip: Optional[list] = None     # seven night states, Monday first
    meters: list = field(default_factory=list)
    footer: Optional[tuple] = None   # (text, link)
    line: bool = False               # drawn as a single line, never a full card
    tray: bool = False               # the Needs-you action tray
    hidden: bool = False
    detail: list = field(default_factory=list)   # extra Markdown for the report

    def worsen(self, status: str) -> None:
        if _RANK[status] > _RANK[self.status]:
            self.status = status


@dataclass
class Week:
    end: datetime                    # the Sunday the email is for (local)
    start: datetime                  # the Monday before it, 00:00 local
    now: datetime
    cards: list = field(default_factory=list)
    headline: str = ""
    report_link: Optional[str] = None
    report_rel: str = ""

    @property
    def status(self) -> str:
        shown = [c.status for c in self.cards if not c.hidden]
        return max(shown, key=_RANK.get) if shown else OK


# ── configuration and links ──────────────────────────────────────────────────

def _install_prefix() -> Path:
    return Path(os.environ.get("AGENTM_INSTALL_PREFIX") or (Path.home() / ".claude"))


def load_config(install_prefix: Optional[Path] = None) -> dict:
    p = (install_prefix or _install_prefix()) / ".agentm-config.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    cfg = data.get(CONFIG_KEY) if isinstance(data, dict) else None
    return cfg if isinstance(cfg, dict) else {}


class Links:
    """Vault-relative paths to web links. With no base, no links: a link to a
    page that does not exist is worse than none."""

    def __init__(self, base: Optional[str]):
        self.base = (base.rstrip("/") + "/") if base else None

    def __call__(self, rel: Optional[str], anchor: str = "") -> Optional[str]:
        if not self.base or not rel:
            return None
        rel = "/".join(part for part in str(rel).split("/") if part)
        return self.base + rel + (f"#{anchor}" if anchor else "")


def _rel(vault_root: Path, path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(Path(vault_root).resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


# ── the clock ────────────────────────────────────────────────────────────────

def week_of(now: datetime) -> tuple:
    """(start, end): the Monday 00:00 before the latest Sunday, and that Sunday.
    Run on a Sunday, the week is this one; run any other day (a hand run, a
    catch-up), it is the last full week."""
    local = now.astimezone()
    days_back = (local.weekday() + 1) % 7      # Sunday → 0, Monday → 1 …
    end = (local - timedelta(days=days_back)).replace(hour=23, minute=59, second=59, microsecond=0)
    start = (end - timedelta(days=6)).replace(hour=0, minute=0, second=0)
    return start, end


def _day(dt: datetime) -> str:
    return f"{dt:%a} {dt.day} {dt:%b}"


def _stamp(at: float) -> str:
    dt = datetime.fromtimestamp(at).astimezone()
    return f"{dt:%a} {dt.day} {dt:%b}, {dt:%H:%M}"


# ── small readers ────────────────────────────────────────────────────────────

def frontmatter(path: Path) -> dict:
    """The flat `key: value` pairs of a note's frontmatter. Enough for the
    tracker and idea fields read here; nested YAML is left alone."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    out = {}
    for line in text[3:end if end > 0 else 0].splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def section(path: Path, heading: str) -> str:
    """The text under `## <heading>`, up to the next heading."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    m = re.search(rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^#{{1,2}}\s|\Z)", text, re.MULTILINE | re.DOTALL)
    return m.group(1).strip() if m else ""


def _date(value: str) -> Optional[datetime]:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").astimezone()
    except ValueError:
        return None


def _in_week(value, week: Week) -> bool:
    d = _date(value) if not isinstance(value, datetime) else value
    return d is not None and week.start.date() <= d.date() <= week.end.date()


def _short(text: str, words: int = 6) -> str:
    parts = re.sub(r"\s+", " ", text or "").strip().split(" ")
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


def _title(tracker: Path, fm: dict) -> str:
    t = fm.get("title") or tracker.parent.name.split("-", 1)[-1].replace("-", " ")
    return t[:1].upper() + t[1:]


def trackers(vault_root: Path, project: Optional[str] = None) -> list:
    base = Path(vault_root) / "projects"
    glob = f"{project}/tasks/*/tracker.md" if project else "*/tasks/*/tracker.md"
    return sorted(base.glob(glob))


def _first_sentence(text: str) -> str:
    text = re.sub(r"\s+", " ", re.sub(r"\[\[[^|\]]*\|([^\]]*)\]\]|\[\[([^\]]*)\]\]",
                                       lambda m: m.group(1) or m.group(2), text or "")).strip()
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    return (m.group(1) if m else text).strip()


def read_jsonl(path: Path) -> list:
    out = []
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return out


def _epoch(value) -> Optional[float]:
    return morning_note._epoch(value)


# ── the urgent lane's record ─────────────────────────────────────────────────

# What each logged code is about, so the weekly email can put "Emailed you …"
# on the right tile.
URGENT_TILE = {
    "runner-stale": "Runner", "backup-stale": "Backup", "index-stale": "Daemon",
    "commit-stalled": "Daemon", "probe-failed": "Daemon", "daemon-down": "Daemon",
    "claude-login": "Login",
}


def urgent_sends(engine_dir: Path, week: Week) -> dict:
    """{tile name: "Emailed you <when>"} for the week's urgent emails, the
    latest per tile."""
    out = {}
    for rec in read_jsonl(Path(engine_dir) / URGENT_LOG):
        at = _epoch(rec.get("at"))
        if at is None or not (week.start.timestamp() <= at <= week.end.timestamp()):
            continue
        for code in rec.get("codes") or []:
            tile = URGENT_TILE.get(code)
            if tile:
                out[tile] = f"Emailed you {_stamp(at)}"
    return out


def log_urgent(engine_dir: Path, codes: list, *, source: str, at: Optional[float] = None) -> None:
    p = Path(engine_dir) / URGENT_LOG
    p.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(at or time.time(), tz=timezone.utc).isoformat(timespec="seconds")
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": stamp, "codes": list(codes), "source": source}) + "\n")


# ── the cards ────────────────────────────────────────────────────────────────

@dataclass
class Sources:
    vault: Path                      # the memory root
    vault_root: Path
    engine_dir: Path
    runner_dir: Path
    link: Links
    config: dict
    ask: Callable
    gh: Callable                     # (args) -> parsed JSON, or raises


def needs_you(src: Sources, week: Week, health: Optional[Card] = None) -> Card:
    card = Card("needs-you", "Needs you", "✋", tray=True)
    items: list = []
    if health is not None and any(t.name == "Login" and t.status == ACT for t in health.tiles):
        items.append(Item("Sign in: run claude, then /login", src.link("agent/diagnostics/morning"), ACT))
    purge = src.vault / "diagnostics" / "migrations" / "purge"
    for note in sorted(purge.glob("*.md")) if purge.is_dir() else []:
        fm = frontmatter(note)
        if fm.get("approved", "").lower() == "false":
            files = fm.get("files") or fm.get("count") or ""
            label = "Approve retention purge" + (f" · {files} files" if files else "")
            items.append(Item(label, src.link(_rel(src.vault_root, note)), LOOK))
    inbox = src.vault / "inbox"
    waiting = [p for p in inbox.iterdir() if not p.name.startswith(".")] if inbox.is_dir() else []
    if waiting:
        items.append(Item(f"{len(waiting)} notes in the inbox", src.link(_rel(src.vault_root, inbox)), LOOK))
    for tracker in trackers(src.vault_root):
        fm = frontmatter(tracker)
        if fm.get("status") != "active":
            continue
        nxt = section(tracker, "Next")
        if _WAITS.search(nxt):
            items.append(Item(_short(f"{tracker.parent.parent.parent.name}: {_title(tracker, fm)}"),
                              src.link(_rel(src.vault_root, tracker)), LOOK))
    try:
        review = len(needs_review.collect(src.vault))
    except Exception:
        review = 0
    if not items:
        card.hidden = True
        return card
    for it in items:
        card.worsen(it.mark or LOOK)
    card.items = items[:3]
    extra = len(items) - 3
    bits = ([f"{review} notes to review"] if review else []) + ([f"{extra} more waiting"] if extra > 0 else [])
    if bits:
        card.footer = (" · ".join(bits), src.link("agent/memory/mocs/needs-review.md"))
    card.detail = [f"- {it.text}" for it in items]
    return card


def _scorecard_rows(path: Path) -> dict:
    out = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    m = re.search(r"\|\s*gold-set R@5\s*\|\s*[\d.]+\s*\|\s*(\d+) of (\d+)", text)
    if m:
        out["recall"] = (int(m.group(1)), int(m.group(2)))
    m = re.search(r"\|\s*writes, the last 24 hours\s*\|\s*(\d+)\s*\|[^|\n]*?budget (\d+)", text)
    if m:
        out["writes"] = (int(m.group(1)), int(m.group(2)))
    return out


def _scorecards(vault: Path) -> list:
    d = Path(vault) / "diagnostics" / "health"
    return sorted(d.glob("*-health-scorecard.md")) if d.is_dir() else []


def agent_health(src: Sources, week: Week, *, now: float) -> Card:
    card = Card("agent-health", "Agent health", "🧠")
    report = src.link(week.report_rel, "agent-health")
    emailed = urgent_sends(src.engine_dir, week)

    status = None
    try:
        status = (src.ask(["status"]) or {}).get("health") or {}
    except Exception:
        status = None
    codes = {a.get("code") for a in (status or {}).get("alerts") or [] if isinstance(a, dict)}
    if status is None:
        daemon = ACT
    else:
        daemon = ACT if codes & {"index-stale", "probe-failed", "commit-stalled"} else OK
    try:
        pulse = runner_pulse.grade(src.runner_dir, now=now, read_wake=abs(now - time.time()) < 300)
        runner = {"OK": OK, "WARN": LOOK, "FAIL": ACT}.get(pulse.level, LOOK)
    except Exception:
        runner = LOOK
    backup = ACT if "backup-stale" in codes else (LOOK if status is None else OK)

    runs = morning_note.enrich_runs(src.engine_dir)
    week_runs = [r for r in runs if week.start.timestamp() <= r["_at"] <= week.end.timestamp()]
    last = runs[-1] if runs else None
    login = ACT if last and claude_login.lapsed(last.get("errors") or []) else OK

    for name, st in (("Daemon", daemon), ("Runner", runner), ("Backup", backup), ("Login", login)):
        card.tiles.append(Tile(name, st, emailed=emailed.get(name), link=report))
        card.worsen(st)
    if daemon == ACT:
        card.items.append(Item("Daemon not answering" if status is None else "Daemon red: " + ", ".join(sorted(codes)),
                               report, ACT))
    if login == ACT:
        card.items.append(Item("Claude login lapsed", src.link("agent/diagnostics/morning"), ACT))

    # Seven nights, Monday first: ● the batch ran, ■ it did not, ○ not yet.
    ran = {datetime.fromtimestamp(r["_at"]).astimezone().date() for r in week_runs
           if not (r.get("errors") and claude_login.lapsed(r.get("errors")) == len(r.get("errors")))}
    strip = []
    for i in range(7):
        day = (week.start + timedelta(days=i)).date()
        if datetime.fromtimestamp(now).astimezone().date() < day:
            strip.append("future")
        else:
            strip.append(OK if day in ran else ACT)
    card.strip = strip
    missed = strip.count(ACT)
    if missed:
        card.worsen(LOOK if missed == 1 else ACT)

    cards = _scorecards(src.vault)
    this = [p for p in cards if _in_week(p.name[:10], week)]
    if this:
        rows = _scorecard_rows(this[-1])
        before = [p for p in cards if p.name[:10] < this[0].name[:10]]
        prev = _scorecard_rows(before[-1]).get("recall") if before else None
        if "recall" in rows:
            hit, total = rows["recall"]
            st = LOOK if prev and hit < prev[0] else OK
            card.meters.append(Meter(f"Recall {hit}/{total}", hit, total, st,
                                     mark=prev[0] if prev else None,
                                     mark_label=f"last week {prev[0]}" if prev else "",
                                     link=src.link(_rel(src.vault_root, this[-1]))))
            card.worsen(st)
        if "writes" in rows:
            n, budget = rows["writes"]
            st = LOOK if n > budget else OK
            card.meters.append(Meter(f"Writes {n} vs budget {budget}", n, max(n, budget), st, mark=budget,
                                     link=src.link(f"agent/diagnostics/morning/{this[-1].name[:10]}.md")))
            card.worsen(st)
    try:
        offenders = morning_note.vault_offenders(src.vault)
    except Exception:
        offenders = []
    if offenders:
        card.items.append(Item(f"{len(offenders)} note{'s' if len(offenders) > 1 else ''} fail the vault checks",
                               src.link("agent/memory/mocs/needs-review.md"), LOOK))
        card.worsen(LOOK)

    cost = 0.0
    for reader in (morning_note.enrich_runs, morning_note.crystallize_runs, morning_note.restated_runs,
                   morning_note.idea_judge_runs, morning_note.lesson_runs):
        try:
            cost += sum(float(r.get("total_cost_usd") or 0) for r in reader(src.engine_dir)
                        if week.start.timestamp() <= r["_at"] <= week.end.timestamp())
        except Exception:
            continue
    card.footer = (f"${cost:,.0f} this week", report)
    card.chip = "all OK"
    card.items = card.items[:3]
    card.detail = [f"- {t.name}: {VERDICT[t.status] if t.status != OK else 'OK'}"
                   + (f" ({t.emailed})" if t.emailed else "") for t in card.tiles]
    card.detail += [f"- Nights: {' '.join({'ok': '●', 'act': '■', 'future': '○'}[s] for s in strip)} (Mon to Sun)"]
    card.detail += [f"- {m.label}" + (f" ({m.mark_label})" if m.mark_label else "") for m in card.meters]
    card.detail += [f"- Spend this week: ${cost:,.2f}"]
    return card


def _gh_json(gh: Callable, args: list):
    try:
        return gh(args)
    except Exception:
        return None


def github(src: Sources, week: Week, *, now: float) -> Optional[Card]:
    owner = src.config.get("github_owner")
    if not owner:
        return None
    card = Card("github", "GitHub", "🐙")
    repos = _gh_json(src.gh, ["repo", "list", owner, "--limit", "200", "--json",
                              "name,isArchived,pushedAt,defaultBranchRef"]) or []
    repos = [r for r in repos if not r.get("isArchived")]
    if not repos:
        card.status, card.items = LOOK, [Item("GitHub did not answer")]
        return card
    red, alerts_on, high, aging, stale = [], 0, [], [], []
    for r in repos:
        name = r["name"]
        branch = (r.get("defaultBranchRef") or {}).get("name") or "main"
        runs = _gh_json(src.gh, ["run", "list", "-R", f"{owner}/{name}", "--branch", branch, "--limit", "20",
                                 "--json", "workflowName,conclusion,createdAt"]) or []
        latest = {}
        for run in runs:
            latest.setdefault(run.get("workflowName"), run)
        if any(x.get("conclusion") == "failure" and (_epoch(x.get("createdAt")) or 0) >= week.start.timestamp()
               for x in latest.values()):
            red.append(name)
        dep = _gh_json(src.gh, ["api", f"repos/{owner}/{name}/dependabot/alerts?state=open&per_page=100"])
        if isinstance(dep, list):
            alerts_on += 1
            if any(((a.get("security_advisory") or {}).get("severity") in ("high", "critical")) for a in dep):
                high.append(name)
        sec = _gh_json(src.gh, ["api", f"repos/{owner}/{name}/secret-scanning/alerts?state=open"])
        if isinstance(sec, list) and sec:
            high.append(name)
        prs = _gh_json(src.gh, ["pr", "list", "-R", f"{owner}/{name}", "--state", "open", "--json", "number,createdAt"]) or []
        if any(now - (_epoch(p.get("createdAt")) or now) > AGING_PR_DAYS * 86400 for p in prs):
            aging.append(name)
        pushed = _epoch(r.get("pushedAt"))
        if pushed and now - pushed > STALE_REPO_DAYS * 86400:
            stale.append(name)
    total = len(repos)
    base = f"https://github.com/{owner}"
    if high:
        card.numbers.append(Number(str(len(set(high))), "high alert" + ("s" if len(set(high)) > 1 else ""),
                                   f"{base}/{sorted(set(high))[0]}/security", ACT))
        card.worsen(ACT)
    card.numbers.append(Number(str(len(red)), "red on main",
                               f"{base}/{red[0]}/actions" if red else f"{base}?tab=repositories", LOOK if red else None))
    if red:
        card.worsen(LOOK)
    if not high:
        card.numbers.append(Number(f"{alerts_on} of {total}", "alerts on", "https://github.com/settings/security_analysis",
                                   LOOK if alerts_on < total else None))
        if alerts_on < total:
            card.worsen(LOOK)
    card.numbers.append(Number(str(len(aging)), "PRs aging", f"{base}/{aging[0]}/pulls" if aging else None,
                               LOOK if aging else None))
    if aging:
        card.worsen(LOOK)
    card.numbers.append(Number(str(len(stale)), "repos stale", f"{base}?tab=repositories&sort=updated"))
    if stale:
        card.worsen(LOOK)
    card.numbers = card.numbers[:4]
    card.chip = f"{total} green" if not red else f"{total - len(red)} green"
    card.detail = [f"- Red on main: {', '.join(red) or 'none'}",
                   f"- Dependabot alerts on: {alerts_on} of {total}; high or critical: {', '.join(sorted(set(high))) or 'none'}",
                   f"- PRs open over {AGING_PR_DAYS} days: {', '.join(aging) or 'none'}",
                   f"- Quiet over {STALE_REPO_DAYS} days: {', '.join(stale) or 'none'}"]
    return card


def _lesson_cards(vault: Path, week: Week) -> list:
    out = []
    for kind in ("procedural", "semantic"):
        d = Path(vault) / "memory" / kind
        for note in d.glob("*.md") if d.is_dir() else []:
            try:
                head = note.read_text(encoding="utf-8")[:1500]
            except (OSError, UnicodeDecodeError):
                continue
            if "lesson-compile" not in head:
                continue
            fm = frontmatter(note)
            if _in_week(fm.get("created", ""), week):
                out.append((int(fm.get("importance") or 0), fm.get("title") or note.stem, note))
    return sorted(out, key=lambda t: -t[0])


def learned(src: Sources, week: Week) -> Card:
    card = Card("what-the-agent-learned", "What the agent learned", "📚")
    new = aug = 0
    for run in morning_note.lesson_runs(src.engine_dir):
        if not (week.start.timestamp() <= run["_at"] <= week.end.timestamp()):
            continue
        for proj in run.get("projects") or []:
            for a in proj.get("actions") or []:
                new += a.get("action") == "new"
                aug += a.get("action") == "augment"
    card.chip = f"{new} new"
    lessons = _lesson_cards(src.vault, week)
    card.items = [Item(_short(t, 8), src.link(_rel(src.vault_root, p))) for _i, t, p in lessons[:3]]
    card.detail = [f"- {new} new, {aug} updated"] + [f"- [{t}]({src.link(_rel(src.vault_root, p)) or ''})"
                                                     for _i, t, p in lessons[:5]]
    return card


def research(src: Sources, week: Week) -> Card:
    card = Card("agent-and-memory-research", "Agent and memory research", "🔭")
    d = src.vault_root / "resources" / "briefs"
    briefs = [p for p in sorted(d.glob("*-field-brief.md")) if _in_week(p.name[:10], week)] if d.is_dir() else []
    if not briefs:
        card.status = LOOK
        card.items = [Item("No field brief this week", src.link("resources/briefs"))]
        card.chip = "none"
        return card
    note = briefs[-1]
    text = note.read_text(encoding="utf-8")
    stories = re.findall(r"^###\s+\d+\.\s+\[([^\]]+)\]\(([^)]+)\)", text, re.MULTILINE)
    card.chip = f"{len(stories)} stor{'y' if len(stories) == 1 else 'ies'}"
    card.items = [Item(_short(t, 8), u) for t, u in stories[:3]]
    card.footer = ("Read the brief", src.link(_rel(src.vault_root, note)))
    card.detail = [f"- [{t}]({u})" for t, u in stories]
    return card


def _task_states(src: Sources, project: str, week: Week, *, private: bool = False) -> tuple:
    """(items, closed, waiting, blocked) for one project's trackers."""
    items, closed, waiting, blocked = [], 0, 0, 0
    for tracker in trackers(src.vault_root, project):
        fm = frontmatter(tracker)
        link = src.link(_rel(src.vault_root, tracker))
        title = _title(tracker, fm)
        if fm.get("status") in ("done", "closed") and _in_week(fm.get("closed", ""), week):
            closed += 1
            items.append(Item(_short(title), link, OK))
        elif fm.get("status") == "active":
            nxt = section(tracker, "Next")
            if _BLOCKED.search(nxt):
                blocked += 1
                items.append(Item(_short(title), link, ACT))
            elif _WAITS.search(nxt):
                waiting += 1
                items.append(Item(_short(f"{title}: waits on you"), link, LOOK))
            elif _in_week(fm.get("updated", ""), week):
                note = "" if private else _first_sentence(nxt)
                if note and _PRIVATE.search(note):
                    note = ""
                items.append(Item(_short(note or title), link))
    order = {ACT: 0, LOOK: 1, OK: 2, None: 3}
    items.sort(key=lambda it: order.get(it.mark, 3))
    return items, closed, waiting, blocked


def _tracker_of(src: Sources, it: Item) -> Path:
    """The tracker an item links to, back from its link."""
    if it.link and src.link.base and it.link.startswith(src.link.base):
        return src.vault_root / it.link[len(src.link.base):]
    return Path("/nonexistent")


def read_feed(src: Sources, project: str, week: Week) -> Optional[dict]:
    p = src.vault_root / "projects" / project / "weekly-feed.json"
    try:
        feed = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    at = _epoch(feed.get("generated"))
    if not isinstance(feed, dict) or at is None or at < week.start.timestamp() - 86400:
        return None
    return feed


def project_card(src: Sources, week: Week, spec: dict) -> Card:
    project = spec["project"]
    private = bool(spec.get("private"))
    card = Card(re.sub(r"[^a-z0-9]+", "-", spec.get("title", project).lower()).strip("-"),
                spec.get("title", project), spec.get("icon", "📁"))
    items, closed, waiting, blocked = _task_states(src, project, week, private=private)
    own_items = [(it, _date(frontmatter(_tracker_of(src, it)).get("updated", ""))) for it in items]
    for it in items:
        if it.mark in (LOOK, ACT):
            card.worsen(LOOK)
    feed = read_feed(src, project, week)
    if feed:
        for n in feed.get("numbers") or []:
            card.numbers.append(Number(str(n.get("value")), str(n.get("label", "")),
                                       n.get("link") or src.link(n.get("rel")), n.get("status")))
        items = [Item(str(i.get("text", "")), i.get("link") or src.link(i.get("rel")), i.get("mark"))
                 for i in feed.get("items") or []] + items
        if feed.get("status") in _RANK:
            card.worsen(feed["status"])
    if private:
        # Steps and dates only: a private project's own words (a title can name
        # a place, a Next line a figure) never leave the vault.
        words = {OK: "A step closed", LOOK: "A step waits on you", ACT: "A step is blocked"}
        items = [Item(it.text, it.link, it.mark) for it in items[:len(items) - len(own_items)]
                 if not _PRIVATE.search(it.text)] + \
                [Item(words.get(it.mark) or (f"Moved {_day(updated)}" if updated else "In progress"), it.link, it.mark)
                 for it, updated in own_items]
    card.items = items[:3]
    card.numbers = card.numbers[:4]
    card.chip = (feed or {}).get("chip") or (f"{closed} closed" if closed else
                                             "quiet" if not items else f"{len(items)} moving")
    charter = src.vault_root / "projects" / project / "charter.md"
    card.footer = (spec.get("footer") or "Project", src.link(_rel(src.vault_root, charter)) if charter.is_file() else None)
    card.detail = [f"- {'✓ ' if it.mark == OK else '▲ ' if it.mark == LOOK else '■ ' if it.mark == ACT else ''}{it.text}"
                   for it in items]
    return card


def projects_in_motion(src: Sources, week: Week, *, merged: Optional[dict] = None) -> Card:
    card = Card("projects-in-motion", "Projects in motion", "🚀")
    own = {c.get("project") for c in src.config.get("project_cards") or []} | set(src.config.get("exclude_projects") or [])
    rows, quiet = [], []
    base = src.vault_root / "projects"
    for proj in sorted(p.name for p in base.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))) if base.is_dir() else []:
        if proj in own:
            continue
        items, closed, _waiting, blocked = _task_states(src, proj, week)
        prs = (merged or {}).get(proj, 0)
        if not (closed or prs or any(it.mark is None or it.mark == LOOK for it in items) or blocked):
            if (base / proj / "tasks").is_dir():
                quiet.append(proj)
            continue
        active = next((it for it in items if it.mark in (ACT, LOOK, None)), None)
        if blocked:
            row = Item(f"{proj} ■ {active.text}" if active else f"{proj} ■ blocked", active.link if active else None, ACT)
        else:
            done = f" ✓ {prs} PRs merged" if prs else (f" ✓ {closed} done" if closed else "")
            nxt = f" → {active.text}" if active else ""
            row = Item(_short(f"{proj}{done}{nxt}", 8), active.link if active else None, OK if done else None)
        rows.append(row)
        if blocked:
            card.worsen(LOOK)
    rows.sort(key=lambda it: 0 if it.mark == ACT else 1)
    card.items = rows[:5]
    card.chip = f"{len(rows)} moved"
    if quiet:
        card.footer = (f"{len(quiet)} quiet", src.link("projects/moc-projects.md"))
    card.detail = [f"- {it.text}" for it in rows] + ([f"- Quiet: {', '.join(quiet)}"] if quiet else [])
    return card


def merged_prs(src: Sources, week: Week) -> dict:
    owner = src.config.get("github_owner")
    if not owner:
        return {}
    found = _gh_json(src.gh, ["search", "prs", "--owner", owner, "--merged",
                              "--merged-at", f">={week.start:%Y-%m-%d}", "--limit", "300",
                              "--json", "repository"]) or []
    out: dict = {}
    for pr in found:
        name = (pr.get("repository") or {}).get("name")
        if name:
            out[name] = out.get(name, 0) + 1
    return out


def homelab(src: Sources, week: Week, *, fetch: Callable) -> Optional[Card]:
    spec = src.config.get("homelab")
    if not isinstance(spec, dict):
        return None
    card = Card("homelab", spec.get("title", "Homelab"), spec.get("icon", "🖥"), line=True)
    fill = {"date": f"{week.end:%Y-%m-%d}", "year": f"{week.end:%Y}"}
    link = (spec.get("link") or "").format(**fill) or None
    text = None
    try:
        text = fetch((spec.get("report_url") or "").format(**fill), spec.get("token_command"))
    except Exception:
        text = None
    card.footer = (None, link)
    if not text:
        card.status, card.chip = LOOK, "no report"
        card.items = [Item(spec.get("name", "Report") + " · not in yet", link)]
        return card
    m = re.search(r"^\*\*(All good|Needs a look|Needs action)\.\*\*", text, re.MULTILINE)
    verdict = m.group(1) if m else "No verdict"
    card.status = {"All good": OK, "Needs a look": LOOK, "Needs action": ACT}.get(verdict, LOOK)
    areas = re.findall(r"^\|\s*(?:LOOK|ACT)\s*\|\s*\[([^\]]+)\]", text, re.MULTILINE)
    words = verdict + (" · " + ", ".join(a.lower() for a in areas[:2]) if areas else "")
    card.items = [Item(f"{spec.get('name', 'NAS')} · {words}", link)]
    card.chip = "all good" if card.status == OK else verdict.lower()
    card.detail = [f"- {spec.get('name', 'NAS')}: {words}"]
    return card


def _fetch(url: str, token_command: Optional[list]) -> Optional[str]:
    """GET a report over HTTPS with a token read from a command (a keychain
    lookup), passed straight to the request and never kept."""
    if not url:
        return None
    import urllib.request
    headers = {}
    if token_command:
        tok = subprocess.run(token_command, capture_output=True, text=True, timeout=10).stdout.strip()
        if tok:
            headers["Authorization"] = f"token {tok}"
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler())
    with opener.open(urllib.request.Request(url, headers=headers), timeout=20) as resp:
        return resp.read().decode("utf-8", "replace")


def ideas(src: Sources, week: Week) -> Card:
    card = Card("ideas", "Ideas", "💡")
    new, added, best = [], 0, None
    d = src.vault_root / "personal" / "ideas"
    for note in sorted(d.glob("*.md")) if d.is_dir() else []:
        fm = frontmatter(note)
        if _in_week(fm.get("created", ""), week):
            new.append(note)
        try:
            text = note.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        dreamed = section(note, "Added by dreaming")
        if dreamed and any(_in_week(x, week) for x in re.findall(r"\d{4}-\d{2}-\d{2}", dreamed)):
            added += 1
        if not fm.get("dismissed"):
            score = int(re.sub(r"\D", "", fm.get("importance", "") or "0") or 0)
            if best is None or score > best[0]:
                best = (score, fm.get("title") or note.stem, note)
    card.chip = f"{len(new)} new" if new else "quiet"
    if not new and not added:
        card.hidden = True
    card.items = [Item(_short(frontmatter(n).get("title") or n.stem), src.link(_rel(src.vault_root, n))) for n in new[:2]]
    if best:
        card.items.append(Item("Worth a look: " + _short(best[1], 6), src.link(_rel(src.vault_root, best[2]))))
    card.detail = [f"- New: {len(new)} · dreaming added to {added}"] + \
                  ([f"- Worth a look: {best[1]}"] if best else [])
    return card


# ── the week ─────────────────────────────────────────────────────────────────

def _guard(name: str, fn: Callable, *a, **kw) -> Optional[Card]:
    try:
        return fn(*a, **kw)
    except Exception as exc:  # one source that will not read costs one card
        return Card(re.sub(r"[^a-z0-9]+", "-", name.lower()), name, "⚠️", LOOK, chip="unread",
                    items=[Item(_short(f"could not read: {type(exc).__name__}", 8))])


def collect(src: Sources, *, now: Optional[float] = None, fetch: Callable = _fetch) -> Week:
    now = now if now is not None else time.time()
    start, end = week_of(datetime.fromtimestamp(now, tz=timezone.utc))
    week = Week(end=end, start=start, now=datetime.fromtimestamp(now).astimezone())
    week.report_rel = _rel(src.vault_root, src.vault / WEEKLY_DIR / f"{end:%Y-%m-%d}.md")
    week.report_link = src.link(week.report_rel)

    health = _guard("Agent health", agent_health, src, week, now=now)
    merged = _gh_json(lambda a: merged_prs(src, week), []) or {}
    cards = [
        _guard("Needs you", needs_you, src, week, health),
        health,
        _guard("GitHub", github, src, week, now=now),
        _guard("What the agent learned", learned, src, week),
        _guard("Agent and memory research", research, src, week),
        _guard("Projects in motion", projects_in_motion, src, week, merged=merged),
    ]
    for spec in src.config.get("project_cards") or []:
        if isinstance(spec, dict) and spec.get("project"):
            cards.append(_guard(spec.get("title", spec["project"]), project_card, src, week, spec))
    cards.append(_guard("Homelab", homelab, src, week, fetch=fetch))
    cards.append(_guard("Ideas", ideas, src, week))
    week.cards = [c for c in cards if c is not None]
    week.headline = headline(week)
    return week


def headline(week: Week) -> str:
    """Twelve words or fewer, from the cards."""
    needs = next((c for c in week.cards if c.slug == "needs-you" and not c.hidden), None)
    red = [c.title for c in week.cards if c.status == ACT and not c.hidden and c.slug != "needs-you"]
    if red:
        return _short(f"{' and '.join(red[:2])} need action.", 12)
    if needs:
        n = len(needs.items)
        return f"A healthy week. {n} decision{'s' if n != 1 else ''} wait on you."
    if week.status == OK:
        return "Everything checked out."
    look = [c.title for c in week.cards if c.status == LOOK and not c.hidden]
    return _short(f"A few things to look at: {', '.join(look[:2]).lower()}.", 12)


def subject(week: Week) -> str:
    names = [c.title for c in week.cards if c.status != OK and not c.hidden]
    tail = (" · " + ", ".join(names[:5]) + (", …" if len(names) > 5 else "")) if names else ""
    return f"AgentM weekly · {VERDICT[week.status]}{tail}"


# ── delivery ─────────────────────────────────────────────────────────────────

def _gh(args: list):
    out = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:200])
    return json.loads(out.stdout or "null")


def wait_for_commit(vault_root: Path, rel: str, *, timeout: float = 300, step: float = 10,
                    clock: Callable = time.monotonic, sleep: Callable = time.sleep) -> bool:
    """Wait until the vault's own committer has committed the report. The
    daemon commits on its own cycle, and a push that runs first leaves the
    email's links on a 404 until the next push: the first live run on
    2026-11-08 pushed six seconds before the report was committed."""
    deadline = clock() + timeout
    while True:
        try:
            out = subprocess.run(["git", "-C", str(vault_root), "status", "--porcelain", "--", rel],
                                 capture_output=True, text=True, timeout=30)
            if out.returncode == 0 and not out.stdout.strip():
                return True
        except (OSError, subprocess.SubprocessError):
            return False
        if clock() >= deadline:
            return False
        sleep(step)


def push_vault(command: Optional[list]) -> bool:
    """Run the vault's backup push so the report's links resolve. A failure
    is logged and the email goes anyway: the links catch up on the next push."""
    if not command:
        return False
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=300).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _session_email():
    import importlib.util
    path = _HERE.parents[3] / "scripts" / "health" / "session_email.py"
    spec = importlib.util.spec_from_file_location("weekly_session_email", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(mod)
    return mod


def build_sources(vault: Path, config: dict) -> Sources:
    vault = Path(vault)
    return Sources(vault=vault, vault_root=write_meter.vault_root_of(vault),
                   engine_dir=engine_state.engine_state_dir(), runner_dir=morning_note.default_runner_state(),
                   link=Links(config.get("link_base")), config=config, ask=corpus_scorecard._agentmd, gh=_gh)


def run_weekly(vault: Path, *, mail: bool = True, push: bool = True, now: Optional[float] = None,
               install_prefix: Optional[Path] = None) -> int:
    import weekly_render
    config = load_config(install_prefix)
    src = build_sources(vault, config)
    week = collect(src, now=now)
    out = src.vault / WEEKLY_DIR / f"{week.end:%Y-%m-%d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(weekly_render.markdown(week), encoding="utf-8")
    print(f"weekly-email: wrote {out} — {VERDICT[week.status]}")
    if push:
        if config.get("push_command") and not wait_for_commit(src.vault_root, _rel(src.vault_root, out)):
            print("weekly-email: the report is not committed yet; pushing anyway")
        pushed = push_vault(config.get("push_command"))
        print(f"weekly-email: vault push {'done' if pushed else 'skipped or failed'}")
    if not mail:
        return 0
    se = _session_email()
    if se is None:
        print("weekly-email: no mail path in this checkout", file=sys.stderr)
        return 1
    res = se.send(subject(week), weekly_render.text(week), html=weekly_render.html(week),
                  install_prefix=install_prefix, sender_name="AgentM weekly")
    print(f"weekly-email: {'sent' if res.get('sent') else 'not sent: ' + str(res.get('skipped'))}")
    return 0 if res.get("sent") or not res.get("configured") else 1


def run_urgent(vault: Path, *, now: Optional[float] = None, install_prefix: Optional[Path] = None,
               ask: Optional[Callable] = None, send: Optional[Callable] = None) -> int:
    """The urgent lane's daily check, for what the daemon cannot see or say:
    a lapsed `claude` login, and the daemon itself not answering. Once per
    condition until it clears."""
    now = now if now is not None else time.time()
    engine_dir = engine_state.engine_state_dir()
    state_path = Path(engine_dir) / URGENT_STATE
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    runs = morning_note.enrich_runs(engine_dir)
    codes, lines = [], []
    if runs and claude_login.lapsed(runs[-1].get("errors") or []):
        codes.append("claude-login")
        lines.append("The claude login has lapsed, so the nightly work cannot reach the model. "
                     "Run `claude`, then `/login`, in a terminal.")
    try:
        (ask or corpus_scorecard._agentmd)(["status"])
    except Exception:
        codes.append("daemon-down")
        lines.append("The memory daemon did not answer `agentmd status`.")
    fresh = [c for c in codes if c not in state]
    state = {c: state.get(c, now) for c in codes}       # a cleared condition may page again
    emailed = []
    if fresh:
        send = send or (lambda s, b: (_session_email() or object()).send(s, b, install_prefix=install_prefix))
        res = send(f"[agentm] urgent: {', '.join(fresh)}", "\n\n".join(lines) + "\n")
        if isinstance(res, dict) and res.get("sent"):
            log_urgent(engine_dir, fresh, source="urgent-lane", at=now)
            emailed = fresh
        else:
            state = {c: v for c, v in state.items() if c not in fresh}
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    print(f"urgent-lane: {', '.join(codes) or 'nothing urgent'}; emailed {', '.join(emailed) or 'nothing'}")
    return 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="The weekly summary email, or the urgent lane's daily check.")
    ap.add_argument("--vault-path", default=None, help="the memory root (overrides MEMORY_ROOT)")
    ap.add_argument("--no-mail", action="store_true", help="write the report, send nothing")
    ap.add_argument("--no-push", action="store_true", help="do not push the vault before sending")
    ap.add_argument("--urgent", action="store_true", help="run the urgent lane's daily check instead")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    vault = args.vault_path or os.environ.get("MEMORY_ROOT") or corpus_scorecard.memory_root_from_daemon()
    if not vault:
        print("weekly-email: no memory root. Set $MEMORY_ROOT, or start the daemon.", file=sys.stderr)
        return 2
    if args.urgent:
        return run_urgent(Path(vault))
    return run_weekly(Path(vault), mail=not args.no_mail, push=not args.no_push)


if __name__ == "__main__":
    raise SystemExit(main())
