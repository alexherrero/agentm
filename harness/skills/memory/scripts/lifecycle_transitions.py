#!/usr/bin/env python3
"""lifecycle_transitions.py — the governance lanes for the lifecycle axis.

Filing v2 part 6 (task 2). A memory ages on one frontmatter axis,
`lifecycle:`. This module is the one writer of that axis after filing, and
who may move a value is tiered by how hard the move is to undo.

One of the five values also moves the file. Filing v2 said archive never by
moving; session 5 of the vault-perfection series reversed that, and the
reason is the eyeline: Obsidian resolves links by basename, so a moved note
is still found by every link that named it, and what the move buys is what
the frontmatter field never gave — the class folders hold what is alive, and
one folder holds what is not. So `→ archived` moves the note to
`archive/<its class path>` and `archived → active` moves it back, in the same
act that writes the field. Every other transition is a line-surgical edit in
place.

* **active ↔ dormant** runs automatic, by policy (`policy_pass`): a memory
  silent past the contract's `dormant_after_days` sinks to `dormant`; the
  next genuine recall access — the only thing that resets the clock — lifts
  it back to `active` on the next cycle. Capped per cycle and watched by the
  same anomaly breaker the other automatic lanes use.
* **→ archived** happens only through a confirm surface: an explicit
  operator act (`transition(..., actor="operator")`, the CLI's `set`). The
  dreaming binary names the candidates in its report; the dream cycle's
  archive proposal and its confirm step retired in agentm-vault plan 04.
  `transition` refuses the state for any actor outside `CONFIRMED_ACTORS` —
  that refusal is the guarantee the plan asked for by test.
* **purge** is not on this axis at all — see `purge.py`: operator-initiated,
  manifest first, never a policy outcome.

Every transition, whoever made it, is appended to the journal at
`<engine state dir>/lifecycle-journal.jsonl` — one JSON object per line:
`{ts, rel, from, to, actor, reason, run_id}`. The weekly digest's "what
quietly sank" section and the scorecard's lifecycle line read it back.

A transition is a line-surgical edit of `lifecycle:`, plus a
`lifecycle_since:` date beside it, so a note keeps its links and its history;
only the archive lane also moves the file, and it moves it back. `pinned` and
`superseded` are never moved by policy: the first is the operator's word that
the note does not age, the second has a successor that answers instead.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import engine_state  # noqa: E402  (same skill dir)
import drive_artifacts  # noqa: E402  (same skill dir)
from filing_engine import _frontmatter  # noqa: E402

STATES = ("pinned", "active", "dormant", "archived", "superseded")
DEFAULT_STATE = "active"
# The contract's thresholds this module reads, with the packaged defaults
# for a contract that predates them.
DORMANT_AFTER_KEY = "dormant_after_days"
ARCHIVE_AFTER_KEY = "archive_after_days"
DEFAULT_DORMANT_AFTER_DAYS = 365.0
DEFAULT_ARCHIVE_AFTER_DAYS = 1825.0
# One cycle's heads-up before a note crosses into archive-proposal range,
# as a fraction of the archive threshold (the old tidying stage's 4.5y of 5y).
PREVIEW_FRACTION = 0.9
# How many automatic demotions one cycle may make. The live corpus is four
# months old, so the first cycles demote nothing; the cap is for the day an
# anchor goes wrong and everything looks silent at once.
DEMOTION_BATCH_CAP = 200
ACTORS = ("policy", "operator", "dream-confirm")
CONFIRMED_ACTORS = ("operator", "dream-confirm")
JOURNAL_NAME = "lifecycle-journal.jsonl"
CLASS_DIRS = ("semantic", "procedural", "episodic", "entities", "crystallized", "mocs")
# Where an archived note lives, under the memory root: the memory tree
# mirrored one level down (`dreaming.ArchiveDir`). One name in two arms —
# the night moves notes here and this module moves them here and back, and a
# second spelling would be a note the other arm cannot find.
ARCHIVE_DIR = "archive"


class ConfirmationRequired(PermissionError):
    """`archived` was asked for by an actor that is not a confirm surface."""


class NotOnTheAxis(ValueError):
    """A value the contract's `lifecycle:` list does not name."""


# ── the frontmatter edit ───────────────────────────────────────────────────────

def lifecycle_of(text: str) -> str:
    """The note's state, `active` when it carries none."""
    fm, _ = _frontmatter(text)
    v = str(fm.get("lifecycle") or "").strip().strip("'\"").lower()
    return v or DEFAULT_STATE


def set_lifecycle_text(text: str, to: str, *, since: str) -> str:
    """The note with `lifecycle: <to>` and `lifecycle_since: <since>` —
    line-surgical, nothing else touched, the body byte-identical. A note
    without frontmatter is returned unchanged (nothing to edit)."""
    if not text.startswith("---\n"):
        return text
    lines = text.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return text
    seen = {}
    for i in range(1, end):
        k, sep, _ = lines[i].partition(":")
        if sep and lines[i][:1] not in " \t#-":
            seen.setdefault(k.strip(), i)
    if "lifecycle" in seen:
        lines[seen["lifecycle"]] = f"lifecycle: {to}"
        at = seen["lifecycle"] + 1
    else:
        lines.insert(end, f"lifecycle: {to}")
        at = end + 1
    if "lifecycle_since" in seen:
        lines[seen["lifecycle_since"]] = f"lifecycle_since: {since}"
    else:
        lines.insert(at, f"lifecycle_since: {since}")
    return "\n".join(lines)


# ── the journal ───────────────────────────────────────────────────────────────

def in_archive(rel: str) -> bool:
    """Whether a memory-root-relative path sits in the memory archive."""
    return str(rel).replace("\\", "/").startswith(ARCHIVE_DIR + "/")


def archive_destination(rel: str) -> str:
    """Where `rel` goes when it is archived: the same path, one level down."""
    return ARCHIVE_DIR + "/" + str(rel).replace("\\", "/")


def class_destination(rel: str) -> str:
    """Where an archived note goes when it comes back: its own class path."""
    rel = str(rel).replace("\\", "/")
    return rel[len(ARCHIVE_DIR) + 1:] if in_archive(rel) else rel


def destination_for(rel: str, to: str) -> str:
    """The path a note should sit at once it is in state `to`.

    Only the archive lane moves anything. A note already where it belongs is
    returned unchanged, so a caller can compare and skip the move rather than
    rename a file onto itself.
    """
    if to == "archived":
        return rel if in_archive(rel) else archive_destination(rel)
    if in_archive(rel) and to in ("active", "pinned"):
        return class_destination(rel)
    return rel


def journal_path(path: "Path | str | None" = None) -> Path:
    return Path(path) if path else engine_state.engine_state_dir() / JOURNAL_NAME


def journal_append(entry: dict, *, path: "Path | str | None" = None) -> Path:
    p = journal_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return p


def journal_entries(*, since: "str | None" = None, path: "Path | str | None" = None) -> list:
    """Every entry, oldest first; `since` (ISO date or datetime) bounds by ts."""
    p = journal_path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since and str(e.get("ts", ""))[:len(since)] < since:
            continue
        out.append(e)
    return out


# ── one transition ────────────────────────────────────────────────────────────

def _states(rules=None) -> tuple:
    try:
        import storage_rules  # same skill dir
        r = rules or storage_rules.rules()
        got = tuple(r.lifecycles())
        return got or STATES
    except Exception:
        return STATES


def _now_iso(now: "_dt.datetime | str | None") -> str:
    if now is None:
        return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()
    if isinstance(now, str):
        return now
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)
    return now.replace(microsecond=0).isoformat()


def transition(vault: "Path | str", rel: str, to: str, *, actor: str, reason: str = "",
               now=None, run_id: "str | None" = None, rules=None,
               journal: "Path | str | None" = None) -> dict:
    """Move one note to `to`, in place, and journal it. Refuses a state the
    contract does not name, an actor this module does not know, and —
    the tier that matters — `archived` from any actor but a confirm
    surface. A note already in `to` is a no-op that journals nothing."""
    vault = Path(vault)
    to = str(to).strip().lower()
    if to not in _states(rules):
        raise NotOnTheAxis(f"{to!r} is not a lifecycle state the contract names ({', '.join(_states(rules))})")
    if actor not in ACTORS:
        raise ValueError(f"unknown actor {actor!r} (one of {', '.join(ACTORS)})")
    if to == "archived" and actor not in CONFIRMED_ACTORS:
        raise ConfirmationRequired(
            f"{rel}: entering `archived` needs a confirm surface — the operator's own `set` — "
            f"not {actor!r}. Policy may sink a memory to `dormant`; it never archives one.")
    p = vault / rel
    text = p.read_text(encoding="utf-8")
    frm = lifecycle_of(text)
    ts = _now_iso(now)
    if frm == to:
        return {"rel": rel, "from": frm, "to": to, "changed": False}
    new = set_lifecycle_text(text, to, since=ts[:10])
    if new == text:
        raise ValueError(f"{rel}: no frontmatter to edit")
    dest = destination_for(rel, to)
    try:
        from vault_lock import vault_mutex  # type: ignore
        ctx = vault_mutex(vault)
    except ImportError:  # pragma: no cover - install-shape dependent
        from contextlib import nullcontext
        ctx = nullcontext()
    with ctx:
        if dest == rel:
            p.write_text(new, encoding="utf-8")
        else:
            # Write the new content at the destination, then drop the old
            # path. In that order: a crash between the two leaves the note in
            # both places, which a reader can sort out, where the other order
            # leaves it in neither.
            target = vault / dest
            if target.exists():
                raise FileExistsError(
                    f"{dest}: something is already there, so {rel} was not moved")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(new, encoding="utf-8")
            p.unlink()
    entry = {"ts": ts, "rel": rel, "from": frm, "to": to, "actor": actor, "reason": reason, "run_id": run_id}
    if dest != rel:
        entry["moved_to"] = dest
    journal_append(entry, path=journal)
    return dict(entry, changed=True)


def find_archived(vault: "Path | str", slug: str) -> "str | None":
    """The archived note this slug names, as a memory-root-relative path.

    A slug rather than a path, because the slug is what the operator has: it
    is what the morning note printed, what the lesson's `consolidated_from`
    links, and what Obsidian shows. A path they would have to reconstruct from
    a class they may not remember is a worse question to ask.
    """
    vault = Path(vault)
    base = vault / ARCHIVE_DIR
    if not base.is_dir():
        return None
    slug = str(slug).strip()
    if slug.endswith(".md"):
        slug = slug[:-3]
    for p in sorted(base.rglob("*.md")):
        if p.stem == slug:
            return p.relative_to(vault).as_posix()
    return None


def revive(vault: "Path | str", slug: str, *, reason: str = "operator revived it",
           now=None, journal: "Path | str | None" = None) -> dict:
    """Bring an archived note back: to its class folder, `active`, clock reset.

    The operator's own act, so it goes in as `operator` — the same actor that
    was allowed to archive it. Reviving is the cheaper direction of the pair
    and it would be strange to gate it harder than the move it undoes.
    """
    rel = find_archived(vault, slug)
    if rel is None:
        raise FileNotFoundError(
            f"{slug}: nothing by that name in {ARCHIVE_DIR}/. `search --deep` "
            f"looks in the deletion manifests and the vault's git history too.")
    return transition(vault, rel, "active", actor="operator", reason=reason,
                      now=now, journal=journal)


# ── the policy: active ↔ dormant, automatic ───────────────────────────────────

@dataclass
class PolicyResult:
    demoted: list = field(default_factory=list)      # [(rel, days_silent)]
    revived: list = field(default_factory=list)      # [(rel, days_silent)]
    archive_candidates: list = field(default_factory=list)  # [(rel, days_silent)] — dormant past archive_after
    previews: list = field(default_factory=list)     # [(rel, days_silent)] — dormant, nearing archive_after
    skipped_by_cap: int = 0
    considered: int = 0

    def as_dict(self) -> dict:
        return {"demoted": [list(x) for x in self.demoted], "revived": [list(x) for x in self.revived],
                "archive_candidates": [list(x) for x in self.archive_candidates],
                "previews": [list(x) for x in self.previews],
                "skipped_by_cap": self.skipped_by_cap, "considered": self.considered}


def thresholds(rules=None) -> tuple:
    """(dormant_after_days, archive_after_days) from the contract, defaults
    for a contract that predates the keys."""
    try:
        import storage_rules  # same skill dir
        t = (rules or storage_rules.rules()).thresholds()
    except Exception:
        t = {}

    def _f(key, default):
        try:
            v = float(t.get(key, default))
        except (TypeError, ValueError):
            return default
        return v if v > 0 else default
    return _f(DORMANT_AFTER_KEY, DEFAULT_DORMANT_AFTER_DAYS), _f(ARCHIVE_AFTER_KEY, DEFAULT_ARCHIVE_AFTER_DAYS)


def memory_notes(vault: "Path | str"):
    """Every memory note, the archived ones included.

    Both trees, because archiving moves the file now: a summary that walked
    only `memory/` would report the archive emptying out as notes left it, and
    the population line would say the corpus was shrinking when it was only
    being filed. The Go arm walks the same pair (`dreaming.MemoryNotes`).
    """
    for base in (Path(vault) / "memory", Path(vault) / ARCHIVE_DIR / "memory"):
        for cls in CLASS_DIRS:
            d = base / cls
            if not d.is_dir():
                continue
            for p in sorted(d.rglob("*.md")):
                if p.name == "_index.md" or drive_artifacts.is_artifact(p):
                    continue
                yield p


def survey(vault: "Path | str", *, now: "str | None" = None, rules=None) -> list:
    """Every memory with its state and its silence: [(rel, state, days_silent
    or None)]. Read-only; the policy and the previews both read this."""
    import lifecycle  # same skill dir
    vault = Path(vault)
    today = now or _dt.date.today().isoformat()
    out = []
    for p in memory_notes(vault):
        rel = p.relative_to(vault).as_posix()
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm, _ = _frontmatter(text)
        state = lifecycle_of(text)
        slug = str(fm.get("slug") or p.stem)
        try:
            days = lifecycle.days_since_last_genuine_access(vault, slug, fm, rel, now=today[:10])
        except Exception:
            days = None
        out.append((rel, state, days))
    return out


def policy_pass(vault: "Path | str", *, now=None, rules=None, cap: int = DEMOTION_BATCH_CAP) -> PolicyResult:
    """The automatic lane, read-only: the active memories silent past
    `dormant_after_days` (what the binary sinks to `dormant`), the dormant ones
    a genuine recall touched (what it lifts back), and the dormant ones past
    `archive_after_days` — the archive candidates the confirm surface
    proposes, never moved by policy. The sinking and the lifting themselves
    are the dreaming binary's (`agentmdream`, filing v2 part 6), journaled to
    the same lifecycle journal; nothing here writes."""
    vault = Path(vault)
    today = _now_iso(now)[:10]
    dormant_after, archive_after = thresholds(rules)
    res = PolicyResult()
    for rel, state, days in survey(vault, now=today, rules=rules):
        res.considered += 1
        if days is None or state in ("pinned", "superseded", "archived"):
            continue
        if state == DEFAULT_STATE and days > dormant_after:
            if len(res.demoted) >= cap:
                res.skipped_by_cap += 1
                continue
            res.demoted.append((rel, days))
        elif state == "dormant":
            if days <= dormant_after:
                res.revived.append((rel, days))
            elif days > archive_after:
                res.archive_candidates.append((rel, days))
            elif days > archive_after * PREVIEW_FRACTION:
                res.previews.append((rel, days))
    return res


# ── the reading: what quietly sank ────────────────────────────────────────────

def summarize(vault: "Path | str", *, now: "str | None" = None, window_days: int = 7, rules=None,
              journal: "Path | str | None" = None) -> dict:
    """Populations per state, and the journal's last `window_days`: sank,
    revived, archived, purged — the digest's section and the scorecard's line."""
    vault = Path(vault)
    if isinstance(now, _dt.datetime):
        today = now.date()
    elif isinstance(now, _dt.date):
        today = now
    else:
        today = _dt.date.fromisoformat((now or _dt.date.today().isoformat())[:10])
    pops = {s: 0 for s in STATES}
    for _rel, state, _days in survey(vault, now=today.isoformat(), rules=rules):
        pops[state] = pops.get(state, 0) + 1
    since = (today - _dt.timedelta(days=window_days)).isoformat()
    recent = journal_entries(since=since, path=journal)
    moves = {"sank": [], "revived": [], "archived": [], "purged": [], "other": []}
    for e in recent:
        to, frm = e.get("to"), e.get("from")
        if to == "dormant":
            moves["sank"].append(e)
        elif to == "active" and frm == "dormant":
            moves["revived"].append(e)
        elif to == "archived":
            moves["archived"].append(e)
        elif to == "purged":
            moves["purged"].append(e)
        else:
            moves["other"].append(e)
    return {"populations": pops, "window_days": window_days, "since": since,
            "moves": {k: len(v) for k, v in moves.items()}, "entries": moves}


def describe(summary: dict) -> str:
    p, m = summary["populations"], summary["moves"]
    return (f"pinned {p.get('pinned', 0)} · active {p.get('active', 0)} · dormant {p.get('dormant', 0)} · "
            f"archived {p.get('archived', 0)} · superseded {p.get('superseded', 0)} · last {summary['window_days']}d: "
            f"sank {m['sank']}, revived {m['revived']}, archived {m['archived']}, purged {m['purged']}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    import argparse
    # No `policy` subcommand. It passed `apply=` to a `policy_pass` that has no
    # such parameter, so every invocation raised TypeError — which nobody saw,
    # because the automatic lane moved to the `agentmdream` binary in filing v2
    # part 6 and `policy_pass` has been read-only since. The function stays: it
    # is what the binary's parity recording was taken against, and the summary
    # surfaces still read it. Only the door that could not open is gone.
    ap = argparse.ArgumentParser(description="the lifecycle axis: move one memory (operator), read the journal, summarize")
    ap.add_argument("--vault", required=True, help="the memory root (the directory holding memory/)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set", help="the operator moves one memory — the one lane that may enter `archived` by hand")
    s.add_argument("rel"); s.add_argument("state", choices=STATES); s.add_argument("--reason", default="operator")
    rv = sub.add_parser("revive", help="bring an archived note back to its class folder, active")
    rv.add_argument("slug", help="the note's slug, as the morning note or a link prints it")
    rv.add_argument("--reason", default="operator revived it")
    j = sub.add_parser("journal", help="what moved, and who moved it")
    j.add_argument("--since", help="ISO date lower bound")
    sm = sub.add_parser("summary", help="populations per state and the last week's moves")
    sm.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    vault = Path(a.vault)
    if a.cmd == "set":
        e = transition(vault, a.rel, a.state, actor="operator", reason=a.reason)
        print(json.dumps(e, ensure_ascii=False))
    elif a.cmd == "revive":
        try:
            e = revive(vault, a.slug, reason=a.reason)
        except FileNotFoundError as err:
            print(str(err), file=sys.stderr)
            return 1
        print(json.dumps(e, ensure_ascii=False))
    elif a.cmd == "journal":
        for e in journal_entries(since=a.since):
            print(json.dumps(e, ensure_ascii=False))
    elif a.cmd == "summary":
        summ = summarize(vault)
        print(json.dumps(summ, indent=2) if a.json else describe(summ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
