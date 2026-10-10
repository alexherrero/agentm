#!/usr/bin/env python3
"""write_meter.py — what the vault wrote in a window, by class, against a budget.

Task 190, step 1. The write-quality audit of 2026-10-07 judged every file the
vault wrote on 10-05 and 10-06 and found that almost none of the new memory
cards were earned, and that derived pages were rewritten with nothing in them
changed. Nothing reported either. "Writes per day" (`volume_gate`) counts the
corpus by its `captured` date, so it cannot see a card that was written and
deleted the same day, a map rewritten with only a date moved, or enrichment
appending prose to a design. This reads the vault's git history instead, so
it sees every write the committer recorded:

- **New memory cards, by class.** A file added under `memory/<class>/` in
  the window, including one deleted again inside it. The daemon's self-probe
  (`probe: self-probe` in the added file) is counted apart: it is written and
  retired daily by design, and it is not a card anyone earned.
- **The vault's net notes.** Markdown files added less those deleted,
  across the whole vault. A move counts as neither.
- **Derived-page rewrites.** Each commit that modified a map, a project
  tracker, `needs-review` or an entity page, and how many of those changed
  only dates (`2026-10-06`, `02:26`, `12 days`) or only the order of lines.
  Either way the page says what it said before.
- **Enrichment in project docs.** Each commit that restamped `enriched_at`
  in a file under `projects/`, and how many `## Added by dreaming` sections
  it wrote there.

The budget is the contract's `thresholds.daily_card_budget` (default 30 new
cards a day, self-probes aside), scaled to the window's length. A window over
budget is a warning, and so is any rewrite that changed only dates or order.

Git is the instrument, and git can stall. When the window reaches the present
and the working tree holds changes newer than the last commit by more than
an hour, the reading says the committer is behind, rather than reporting a
quiet day. It is read-only: it runs `git log` and `git status` and writes
nothing.

Run: python3 write_meter.py [--vault-path <memory root>]
         [--since "2026-10-05 00:00"] [--until "2026-10-06 21:13"] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

DEFAULT_BUDGET = 30
BUDGET_KEY = "daily_card_budget"
DAY = 86400
# How far the newest uncommitted change may run ahead of the last commit before
# the reading calls the committer behind. The daemon commits on its reconcile
# tick, minutes apart; an hour is well past any healthy gap.
STALL_AFTER = 3600

CLASS_DIRS = ("semantic", "procedural", "episodic", "entities", "crystallized", "mocs")
DERIVED = ("maps", "trackers", "needs-review", "entities")
PROBE_LINE = re.compile(r"^probe:\s*['\"]?self-probe['\"]?\s*$", re.M)
DREAMING_HEADING = "## Added by dreaming"

# What masks a line down to what it says. An ISO date with an optional time, a
# bare clock time, and a count of days ("12 days silent", "1,204 days"): the
# three ways a page restates the calendar without saying anything new.
_DATE = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?")
_CLOCK = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?Z?\b")
_DAYS = re.compile(r"\b\d[\d,]*\s+days?\b")


def budget(rules=None) -> "int | None":
    """New cards a day the vault may gain: the contract's threshold, else the
    default. None when the contract sets it to 0, which turns the warning off."""
    try:
        import storage_rules  # same skill dir
        rules = rules or storage_rules.rules()
        value = rules.thresholds().get(BUDGET_KEY)
    except Exception:
        value = None
    if value is None:
        return DEFAULT_BUDGET
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return DEFAULT_BUDGET
    return n if n > 0 else None


def mask(line: str) -> str:
    line = _DATE.sub("<date>", line)
    line = _CLOCK.sub("<time>", line)
    return _DAYS.sub("<n> days", line)


def classify_change(removed: list, added: list) -> str:
    """`order-only`, `date-only` or `content`, for one file's diff in one commit.

    Order-only: the same lines, rearranged. Date-only: the lines differ, but
    not once dates, clock times and day counts are masked out. A mix of the two
    (a date moved and lines reordered) reads as date-only: neither is a change
    in what the page says.
    """
    if Counter(removed) == Counter(added):
        return "order-only"
    if Counter(mask(l) for l in removed) == Counter(mask(l) for l in added):
        return "date-only"
    return "content"


def _git(root: Path, *args: str) -> "tuple[int, str]":
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotePath=false", "-C", str(root), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return proc.returncode, proc.stdout if proc.returncode == 0 else (proc.stderr or proc.stdout)


def _unquote(path: str) -> str:
    """A path as git prints it with quotePath off: raw UTF-8, except that a
    name holding a quote, a backslash or a control character is C-quoted."""
    if not (len(path) >= 2 and path[0] == '"' and path[-1] == '"'):
        return path
    body, out, i = path[1:-1], bytearray(), 0
    simple = {"n": b"\n", "t": b"\t", '"': b'"', "\\": b"\\", "a": b"\a",
              "b": b"\b", "f": b"\f", "r": b"\r", "v": b"\v"}
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in simple:
                out += simple[nxt]
                i += 2
                continue
            if body[i + 1:i + 4].isdigit():
                out.append(int(body[i + 1:i + 4], 8) & 0xFF)
                i += 4
                continue
        out += ch.encode("utf-8")
        i += 1
    return out.decode("utf-8", "replace")


def vault_root_of(memory_root: Path) -> Path:
    rc, out = _git(Path(memory_root), "rev-parse", "--show-toplevel")
    if rc == 0 and out.strip():
        return Path(os.path.realpath(out.strip()))
    return Path(os.path.realpath(memory_root))


class Layout:
    """Which class a vault-relative path belongs to. `mem` is the memory root's
    path inside the vault, with a trailing slash (`agent/`), or empty when the
    memory root is the vault root."""

    def __init__(self, mem: str):
        m = re.escape(mem)
        self.card = re.compile(rf"^{m}memory/({'|'.join(CLASS_DIRS)})/(.+\.md)$")
        self.needs_review = re.compile(rf"^{m}memory/mocs/needs-review\.md$")
        self.map = re.compile(rf"^(?:{m}memory/mocs/[^/]+|projects/[^/]+/moc-[^/]+)\.md$")
        self.tracker = re.compile(r"^projects/[^/]+/tracker\.md$")
        self.entity = re.compile(rf"^{m}memory/entities/.+\.md$")
        self.project_doc = re.compile(r"^projects/.+\.md$")

    def card_class(self, rel: str) -> "str | None":
        m = self.card.match(rel)
        if not m or m.group(2).rsplit("/", 1)[-1] == "_index.md":
            return None
        return m.group(1)

    def derived(self, rel: str) -> "str | None":
        if self.needs_review.match(rel):
            return "needs-review"
        if self.entity.match(rel):
            return "entities"
        if self.tracker.match(rel):
            return "trackers"
        if self.map.match(rel):
            return "maps"
        return None


def _log_name_status(root: Path, since: int, until: int) -> list:
    """[(sha, status, path, source)] for every file every commit in the window
    touched. A move is one `R` row whose `source` is where it came from, so a
    card refiled from one class to another is not read as a new one."""
    rc, out = _git(root, "log", f"--since=@{since}", f"--until=@{until}", "-M",
                   "--name-status", "--format=%x01%H")
    if rc != 0:
        raise RuntimeError(f"git log failed: {out.strip()[:200]}")
    rows, sha = [], ""
    for line in out.splitlines():
        if line.startswith("\x01"):
            sha = line[1:].strip()
        elif "\t" in line:
            fields = line.split("\t")
            status = fields[0][:1]
            if status in ("R", "C") and len(fields) >= 3:
                rows.append((sha, status, _unquote(fields[2]), _unquote(fields[1])))
            else:
                rows.append((sha, status, _unquote(fields[1]), ""))
    return rows


def _log_diffs(root: Path, since: int, until: int, pathspecs: list) -> list:
    """[(sha, path, removed lines, added lines)] for each file modified (not
    added or deleted) by each commit in the window, with no context lines."""
    rc, out = _git(root, "log", f"--since=@{since}", f"--until=@{until}", "--no-renames",
                   "-p", "-U0", "--no-color", "--no-ext-diff", "--format=%x01%H",
                   "--", *pathspecs)
    if rc != 0:
        raise RuntimeError(f"git log -p failed: {out.strip()[:200]}")
    diffs, sha = [], ""
    cur = None  # [path, removed, added, is_modify]
    in_header = False
    old = new = None

    def flush():
        if cur is not None and cur[3]:
            diffs.append((sha, cur[0], cur[1], cur[2]))

    for line in out.splitlines():
        if line.startswith("\x01"):
            flush()
            cur, sha = None, line[1:].strip()
            continue
        if line.startswith("diff --git "):
            flush()
            cur, in_header, old, new = None, True, None, None
            continue
        if in_header:
            if line.startswith("--- "):
                old = line[4:]
            elif line.startswith("+++ "):
                new = line[4:]
            elif line.startswith("@@"):
                in_header = False
                is_modify = old not in (None, "/dev/null") and new not in (None, "/dev/null")
                path = _unquote(new)[2:] if new and new != "/dev/null" else ""
                cur = [path, [], [], is_modify]
            continue
        if cur is None:
            continue
        if line.startswith("@@"):
            continue
        if line.startswith("-"):
            cur[1].append(line[1:])
        elif line.startswith("+"):
            cur[2].append(line[1:])
    flush()
    return diffs


def _is_probe(root: Path, sha: str, rel: str) -> bool:
    rc, out = _git(root, "show", f"{sha}:{rel}")
    if rc != 0:
        return False
    head = out.split("\n---", 1)[0] if out.startswith("---") else ""
    return bool(PROBE_LINE.search(head))


def _behind(root: Path, last_commit: "int | None") -> "int | None":
    """Seconds the newest uncommitted Markdown change runs ahead of the last
    commit, when it is more than STALL_AFTER; else None."""
    rc, out = _git(root, "status", "--porcelain", "--untracked-files=all", "--", "*.md")
    if rc != 0 or not out.strip():
        return None
    newest = 0.0
    for line in out.splitlines():
        rel = _unquote(line[3:].split(" -> ")[-1])
        try:
            newest = max(newest, (root / rel).stat().st_mtime)
        except OSError:
            continue
    if not newest or last_commit is None:
        return None
    gap = int(newest - last_commit)
    return gap if gap > STALL_AFTER else None


def measure(memory_root, *, since: "float | None" = None, until: "float | None" = None,
            rules=None, card_budget: "int | None" = -1) -> dict:
    """The meter's reading for one window (the last 24 hours by default).

    Raises RuntimeError when the vault is not a git repository or git fails:
    an absence the caller reports as one, never a quiet zero.
    """
    memory_root = Path(memory_root)
    root = vault_root_of(memory_root)
    rc, out = _git(root, "rev-parse", "--is-inside-work-tree")
    if rc != 0 or out.strip() != "true":
        raise RuntimeError(f"{root} is not a git working tree")
    now = time.time()
    until = int(until if until is not None else now)
    since = int(since if since is not None else until - DAY)
    try:
        rel = os.path.relpath(os.path.realpath(memory_root), root).replace(os.sep, "/")
    except ValueError:
        rel = "."
    mem = "" if rel in (".", "") else rel.rstrip("/") + "/"
    layout = Layout(mem)

    rows = _log_name_status(root, since, until)
    commits = {sha for sha, _st, _p, _src in rows}
    cards: Counter = Counter()
    probes = 0
    net = 0
    for sha, status, path, source in rows:
        if status == "D":
            net -= path.endswith(".md")
            continue
        if not path.endswith(".md"):
            continue
        if status in ("A", "C"):
            net += 1
        elif status != "R" or layout.card_class(source):
            continue  # a modification, or a move between memory classes
        cls = layout.card_class(path)
        if cls:
            if cls == "semantic" and _is_probe(root, sha, path):
                probes += 1
            else:
                cards[cls] += 1

    pathspecs = [f"{mem}memory/mocs", f"{mem}memory/entities", "projects"]
    derived = {cls: {"rewrites": 0, "date_only": 0, "order_only": 0} for cls in DERIVED}
    date_only_paths: list = []
    enrich_docs = 0
    enrich_sections = 0
    for sha, path, removed, added in _log_diffs(root, since, until, pathspecs):
        cls = layout.derived(path)
        if cls:
            kind = classify_change(removed, added)
            d = derived[cls]
            d["rewrites"] += 1
            if kind == "date-only":
                d["date_only"] += 1
                date_only_paths.append(path)
            elif kind == "order-only":
                d["order_only"] += 1
                date_only_paths.append(path)
            continue
        if layout.project_doc.match(path) and any(l.startswith("enriched_at:") for l in added):
            enrich_docs += 1
            enrich_sections += sum(1 for l in added if l.startswith(DREAMING_HEADING))

    rc, out = _git(root, "log", "-1", "--format=%ct")
    last_commit = int(out.strip()) if rc == 0 and out.strip().isdigit() else None
    behind = _behind(root, last_commit) if until >= now - STALL_AFTER else None

    if card_budget == -1:
        card_budget = budget(rules)
    new_cards = sum(cards.values())
    days = max(1.0, (until - since) / DAY)
    allowed = None if card_budget is None else int(round(card_budget * days))
    no_change = sum(d["date_only"] + d["order_only"] for d in derived.values())
    warnings = []
    if allowed is not None and new_cards > allowed:
        warnings.append(f"{new_cards} new cards, over the budget of {allowed}")
    if no_change:
        warnings.append(f"{no_change} derived-page rewrite(s) changed only dates or order")
    if behind:
        warnings.append(f"the committer is {behind // 60} minutes behind the newest change; "
                        "these counts are short")
    return {
        "since": since, "until": until, "vault_root": str(root), "commits": len(commits),
        "new_cards": new_cards, "by_class": {c: cards[c] for c in CLASS_DIRS if cards[c]},
        "probes": probes, "net_notes": net, "derived": derived,
        "no_change_rewrites": sorted(set(date_only_paths)),
        "enrichment": {"project_docs": enrich_docs, "sections": enrich_sections},
        "budget": card_budget, "allowed": allowed, "last_commit": last_commit,
        "behind_seconds": behind, "warnings": warnings,
    }


def describe(m: dict) -> str:
    """The reading in one line, for the scorecard's note and the morning note."""
    classes = " · ".join(f"{c} {n}" for c, n in m["by_class"].items()) or "none"
    parts = [f"{m['new_cards']} new card(s) ({classes})"
             + (f" + {m['probes']} self-probe(s)" if m["probes"] else "")]
    if m["allowed"] is not None:
        parts.append(f"budget {m['allowed']}")
    parts.append(f"vault {m['net_notes']:+d} notes net")
    rewrites = []
    for cls in DERIVED:
        d = m["derived"][cls]
        if d["rewrites"]:
            idle = d["date_only"] + d["order_only"]
            rewrites.append(f"{cls} {d['rewrites']}" + (f" ({idle} dates/order only)" if idle else ""))
    parts.append("derived rewrites " + (", ".join(rewrites) if rewrites else "none"))
    e = m["enrichment"]
    if e["project_docs"]:
        parts.append(f"enrichment in {e['project_docs']} project doc(s)"
                     + (f", {e['sections']} section(s) appended" if e["sections"] else ""))
    line = " · ".join(parts)
    if m["warnings"]:
        line += " — ⚠ " + "; ".join(m["warnings"])
    return line


def _when(text: str) -> float:
    return datetime.fromisoformat(text).timestamp()


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description="Count what the vault wrote in a window.")
    ap.add_argument("--vault-path", default=None, help="the memory root (overrides MEMORY_ROOT)")
    ap.add_argument("--since", default=None, help="window start, local time (default: 24 hours before --until)")
    ap.add_argument("--until", default=None, help="window end, local time (default: now)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    vault = args.vault_path or os.environ.get("MEMORY_ROOT") or os.environ.get("MEMORY_VAULT_PATH")
    if not vault:
        import corpus_scorecard  # same skill dir
        vault = corpus_scorecard.memory_root_from_daemon()
    if not vault:
        print("write-meter: no memory root. Pass --vault-path or set $MEMORY_ROOT.", file=sys.stderr)
        return 2
    until = _when(args.until) if args.until else None
    since = _when(args.since) if args.since else None
    try:
        m = measure(Path(vault), since=since, until=until)
    except RuntimeError as exc:
        print(f"write-meter: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(m, indent=2, ensure_ascii=False))
    else:
        span = (f"{datetime.fromtimestamp(m['since']):%Y-%m-%d %H:%M} to "
                f"{datetime.fromtimestamp(m['until']):%Y-%m-%d %H:%M}")
        print(f"write-meter: {span}, {m['commits']} commit(s): {describe(m)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
