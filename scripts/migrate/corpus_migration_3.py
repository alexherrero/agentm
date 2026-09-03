#!/usr/bin/env python3
"""corpus_migration_3.py — filing-v2 part 3: the one-time population of the six
classes, dissolving the inbox and every legacy directory.

The dry run IS the default: it walks every population, decides a disposition
per note, and writes a full disposition report — nothing in the corpus moves.
`--apply --phase <route|archive|purge>` performs one phase, under a quiesced
daemon and runner, and the purge additionally demands `--confirm-count N`
equal to the manifest's count: the purge is the arc's only irreversible act
and it never runs past the operator's explicit confirmation of the number.

Populations (memory-root-relative) and what the router does with each:

  memory/_inbox/            live notes route to their class by type; the
                            expired cohort is manifested for the purge
  memory/<legacy type dir>/ `preferences/`, `preference/`, `idea/`, `fix/`,
                            `workflow/`, `workflow-pattern/`, `insight/`, ...
                            — the dir name is the old type; the deprecations
                            map collapses it, then the note routes by type
  memory/2026/              month-bucketed notes route by their own type
  memory/_archive/          into classes carrying `lifecycle: archived`
  memory/_opinions/         `kind: opinion-supplement` records into
                            `crystallized/`, kind kept, the verdict already in
                            `opinion:`; expired supplements stay, archived
  external/                 memories route provenance-tagged
                            (`source: external-fetch`); records are held
  memory/crystallized/<opinion>/  supplements the archive phase already moved
                            home: kept, except an expired one under
                            `--purge-scope all-expired`, which the purge takes
  _vault-archive/, strays   held — listed with a reason, never moved blind

Invariants (design § Migrations): basenames preserved so name-resolved
wikilinks survive; every frontmatter change is line-surgical (one line
rewritten in place, new lines appended before the closing fence — the block is
never parsed and re-serialized); moves are `git mv` in the vault repository;
a second run after an apply is a no-op. Dedup biases conservative: an exact
body-fingerprint twin is filed and marked `lifecycle: superseded` pointing at
its winner, never merged; a basename clash inside one class keeps the basename
on the winner and files the twin under `<stem>~dup.md`, flagged for review.

Usage:
  corpus_migration_3.py                       # dry run, report to the engine state dir
  corpus_migration_3.py --apply --phase route  # inbox live + legacy dirs + dated + external memories
  corpus_migration_3.py --apply --phase archive  # _archive + _opinions
  corpus_migration_3.py --apply --phase purge --confirm-count 2194
  corpus_migration_3.py --purge-scope inbox    # the design's literal cohort, for the comparison count
  corpus_migration_3.py --purge-scope all-expired  # every expired note, the opinion supplements included

Exit: 0 ran (held notes are reported, not failed); 1 with --strict when the
contract cannot place some notes; 2 setup error; the purge refuses (non-zero)
when --confirm-count is missing or wrong.
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
for p in (_REPO / "scripts", _REPO / "harness" / "skills" / "memory" / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import storage_rules  # noqa: E402
from fingerprint import compute_fingerprint  # noqa: E402
from migrate_type_collapse import STATUS_MAP, frontmatter_lines, rewrite_line  # noqa: E402

INBOX = "memory/_inbox"
ARCHIVE = "memory/_archive"
OPINIONS = "memory/_opinions"
DATED = "memory/2026"
EXTERNAL = "external"
VAULT_ARCHIVE = "_vault-archive"
CRYSTALLIZED = "memory/crystallized"
OPINION_KIND = "opinion-supplement"
INDEX_NAME = "_index.md"

PHASES = ("route", "archive", "purge")
PHASE_OF_POPULATION = {"inbox": "route", "legacy": "route", "dated": "route", "external": "route",
                       "archive": "archive", "opinions": "archive"}
# The purge prunes what it empties; a lane the purge empties goes too (reflect
# recreates a lane on the next supplement).
PURGE_PRUNES = ("inbox", "legacy", "archive", "supplements")

CONFIDENCE = {"LOW": "low", "MEDIUM": "medium", "HIGH": "high"}
REPORT_COLUMNS = ("path", "population", "disposition", "dest", "field_before", "value_before",
                  "type_after", "status_before", "status_after", "lifecycle", "source",
                  "filing_confidence", "superseded_by", "flags", "reason")


class Row:
    """One note, one decision."""

    __slots__ = ("path", "rel", "population", "disposition", "dest", "field_before",
                 "value_before", "type_after", "status_before", "status_after", "lifecycle",
                 "source", "filing_confidence", "superseded_by", "flags", "reason",
                 "fingerprint", "sortkey", "kind_after", "lines", "fm_end")

    def __init__(self, path: Path, rel: str, population: str):
        self.path, self.rel, self.population = path, rel, population
        self.disposition = "hold"
        self.dest = ""
        self.field_before = self.value_before = self.type_after = self.kind_after = ""
        self.status_before = self.status_after = ""
        self.lifecycle = self.source = self.filing_confidence = self.superseded_by = ""
        self.flags: list = []
        self.reason = ""
        self.fingerprint = ""
        self.sortkey = ()
        self.lines: list = []
        self.fm_end = -1

    def as_record(self) -> dict:
        return {
            "path": self.rel, "population": self.population, "disposition": self.disposition,
            "dest": self.dest, "field_before": self.field_before, "value_before": self.value_before,
            "type_after": self.type_after, "status_before": self.status_before,
            "status_after": self.status_after, "lifecycle": self.lifecycle, "source": self.source,
            "filing_confidence": self.filing_confidence, "superseded_by": self.superseded_by,
            "flags": " ".join(self.flags), "reason": self.reason,
        }


# ── reading ──────────────────────────────────────────────────────────────────

def read_note(path: Path):
    """(lines, fm_end, fields) — `fields` maps each top-level frontmatter key to
    (line_no, value); fm_end is the closing fence's line index, or -1."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, -1, {}
    lines = text.split("\n")
    if not text.startswith("---\n"):
        return lines, -1, {}
    fields = {}
    fm_end = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            fm_end = i
            break
    for i, key, value in frontmatter_lines(text):
        fields.setdefault(key, (i, value))
    return lines, fm_end, fields


def body_fingerprint(lines: list, fm_end: int) -> str:
    body = "\n".join(lines[fm_end + 1:]) if fm_end >= 0 else "\n".join(lines)
    return compute_fingerprint(body)


def legacy_dirs(vault: Path, rules) -> list:
    """`memory/<name>/` directories whose name is a memory type or a retired
    value — the pre-class layout that filed by type-named folder."""
    names = set(rules.memory_types()) | set(rules.deprecations())
    out = []
    mem = vault / "memory"
    if not mem.is_dir():
        return out
    for d in sorted(mem.iterdir()):
        if d.is_dir() and d.name in names and not d.name.startswith("_"):
            out.append(d)
    return out


def walk_population(vault: Path, rules):
    """Yield (population, path) over every note the migration has an opinion about."""
    def notes(root: Path):
        if not root.is_dir():
            return
        for p in sorted(root.rglob("*.md")):
            if p.name.startswith("Icon"):
                continue
            yield p

    for p in notes(vault / INBOX):
        yield "inbox", p
    for d in legacy_dirs(vault, rules):
        for p in notes(d):
            yield "legacy", p
    for p in notes(vault / DATED):
        yield "dated", p
    for p in notes(vault / ARCHIVE):
        yield "archive", p
    for p in notes(vault / OPINIONS):
        yield "opinions", p
    # Supplements the archive phase already moved home: the lanes are the
    # subdirectories of crystallized/ (a crystallized memory is a flat file).
    # Walked so the purge can reach an expired supplement under `all-expired`
    # after the move; anything else there is kept, untouched.
    crystallized = vault / CRYSTALLIZED
    if crystallized.is_dir():
        for lane in sorted(d for d in crystallized.iterdir() if d.is_dir()):
            for p in notes(lane):
                yield "supplements", p
    for p in notes(vault / EXTERNAL):
        yield "external", p
    for p in notes(vault / VAULT_ARCHIVE):
        yield "vault-archive", p
    mem = vault / "memory"
    if mem.is_dir():
        for p in sorted(mem.glob("*.md")):
            if not p.name.startswith("Icon"):
                yield "stray", p


# ── deciding ─────────────────────────────────────────────────────────────────

def _memory_type(fields: dict, rules) -> tuple:
    """(field, value, resolved) — which vocabulary line the note carries and
    what the contract makes of it: resolved is the current memory type, the
    current record kind, or None when the value is in neither register."""
    for field in ("type", "kind"):
        if field in fields and fields[field][1]:
            value = fields[field][1]
            new = rules.resolve_deprecated(value) or value
            if new in rules.memory_types():
                return field, value, ("type", new)
            if new in rules.record_kinds():
                return field, value, ("kind", new)
            return field, value, None
    return "", "", None


def decide(row: Row, rules, *, purge_scope: str, targets: set) -> None:
    lines, fm_end, fields = read_note(row.path)
    if lines is None:
        row.disposition, row.reason = "hold", "unreadable"
        return
    row.lines, row.fm_end = lines, fm_end
    if fm_end < 0:
        row.disposition, row.reason = "hold", "no frontmatter"
        return
    row.fingerprint = body_fingerprint(lines, fm_end)
    status = fields.get("status", (None, ""))[1]
    row.status_before = status
    field, value, resolved = _memory_type(fields, rules)
    row.field_before, row.value_before = field, value
    mined = "retired_because" in fields or "mining_confidence" in fields
    created = fields.get("created", (None, ""))[1]
    row.sortkey = (0 if "enriched_at" in fields else 1, 0 if "fingerprint" in fields else 1,
                   created or "9999", row.rel)

    if row.path.name == INDEX_NAME and row.population in ("archive", "external", "legacy", "dated", "inbox"):
        row.disposition, row.reason = "drop-index", "generated index of a dissolving directory"
        return
    if row.population in ("vault-archive", "stray"):
        row.disposition, row.reason = "hold", f"{row.population}: not in the routing map — operator decides"
        return
    if row.population == "supplements":
        if status == "expired" and purge_scope == "all-expired":
            row.disposition, row.reason = "purge", "expired supplement, already in its lane (manifested)"
        else:
            row.disposition, row.reason = "keep", "supplement already home in its lane"
        return

    # The expired cohort. `inbox` is the design's literal cohort; `mined` adds
    # the same auto-miner retirements that sit in the legacy dirs and _archive
    # (never the opinion supplements — dreaming's records); `all-expired` takes
    # every `status: expired` note, supplements included. The operator rules
    # at the purge gate; the report carries every figure.
    if status == "expired":
        if row.population == "inbox" or purge_scope == "all-expired":
            in_scope = True
        elif purge_scope == "mined":
            in_scope = mined and row.population != "opinions"
        else:
            in_scope = False
        if in_scope:
            row.disposition, row.reason = "purge", "expired auto-miner output (manifested)"
            return
        row.flags.append("expired-out-of-scope")

    if resolved is None:
        if not field:
            row.disposition, row.reason = "hold", "no type or kind"
        else:
            row.disposition, row.reason = "hold", f"`{field}: {value}` is in neither register and no deprecation maps it"
        return
    kind_or_type, new_value = resolved

    # `external/` is a project tree: a record there stays a project record even
    # when the deprecations map would collapse its kind into a memory type
    # (`decision` → `convention`) — pulling it into a class would strip it from
    # its project. Only a note already carrying `type:` is a memory to route.
    if row.population == "external" and field == "kind":
        row.disposition, row.reason = "hold", f"external project record (`kind: {value}`) — stays with its project; operator decides"
        return

    if kind_or_type == "kind":
        if new_value == OPINION_KIND:
            # The accumulate loop's lanes keep their shape: `<opinion>/<slug>.md`
            # and the served `<opinion>.md` beside it, now under crystallized/.
            inside = row.rel[len(OPINIONS) + 1:] if row.rel.startswith(OPINIONS + "/") else row.path.name
            row.disposition = "route"
            row.dest = f"{CRYSTALLIZED}/{inside}"
            row.kind_after = new_value
            row.lifecycle = "archived" if (status == "expired" or row.population == "archive") else rules.default_lifecycle()
            row.status_after = status
            row.source = fields.get("source", (None, ""))[1] or "conversation"
            return
        row.disposition, row.reason = "hold", f"record `kind: {new_value}` — records are not memories"
        return

    routing = rules.routing()
    dest_dir = routing.get(new_value)
    if not dest_dir:
        row.disposition, row.reason = "hold", f"no routing for type `{new_value}`"
        return
    row.disposition = "route"
    row.type_after = new_value
    row.dest = f"{dest_dir}/{row.path.name}"
    row.status_after = STATUS_MAP.get(status, status) if status else "active"
    row.lifecycle = "archived" if row.population == "archive" else rules.default_lifecycle()
    if status == "expired":  # out-of-scope expired: retained, marked
        row.lifecycle = "archived"
        row.status_after = status
    row.source = fields.get("source", (None, ""))[1] or ("external-fetch" if row.population == "external" else "conversation")
    row.filing_confidence = fields.get("filing_confidence", (None, ""))[1] or CONFIDENCE.get(
        fields.get("mining_confidence", (None, ""))[1].upper(), "")
    target = fields.get("promoted_to", (None, ""))[1]
    if target:
        norm = _normalize_target(target)
        if norm in targets:
            row.lifecycle = "superseded"
            row.superseded_by = norm
            row.flags.append("promoted-target-exists")
        else:
            row.flags.append("promoted-target-gone")


def _normalize_target(target: str) -> str:
    """A `promoted_to:` path as written by an older layout, spelled on the
    current memory root (`personal/` was renamed `memory/`)."""
    t = target.strip().strip('"').strip("'")
    if t.startswith("personal/"):
        t = "memory/" + t[len("personal/"):]
    return t


def dedupe(rows: list) -> None:
    """Exact-twin marking and basename-clash resolution over the routed rows."""
    routed = [r for r in rows if r.disposition == "route"]
    by_fp = defaultdict(list)
    for r in routed:
        by_fp[r.fingerprint].append(r)
    for group in by_fp.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda r: r.sortkey)
        winner = group[0]
        for loser in group[1:]:
            loser.lifecycle = "superseded"
            loser.superseded_by = winner.dest
            loser.flags.append("exact-twin")
    by_dest = defaultdict(list)
    for r in routed:
        by_dest[r.dest].append(r)
    for dest, group in by_dest.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda r: r.sortkey)
        stem, ext = os.path.splitext(dest)
        for n, loser in enumerate(group[1:], start=1):
            loser.dest = f"{stem}~dup{'' if n == 1 else n}{ext}"
            loser.flags.append("basename-clash")


def settle_against_existing(rows: list, vault: Path) -> None:
    """A routed note whose destination already holds a file — a re-capture of
    a memory an earlier pass filed, or a genuine namesake — is never an
    overwrite. Identical body: the arriving note is the twin, filed superseded
    by the note already home; different body: a basename clash, filed as
    `<stem>~dup.md`. Either way it is flagged for review, and a later pass
    over a corpus the daemon kept writing into stays resumable."""
    for r in rows:
        if r.disposition != "route":
            continue
        dest = vault / r.dest
        if not dest.exists() or dest.resolve() == r.path.resolve():
            continue
        try:
            existing = body_fingerprint(*read_note(dest)[:2])
        except Exception:
            existing = ""
        if existing and existing == r.fingerprint:
            r.lifecycle = "superseded"
            r.superseded_by = r.dest
            r.flags.append("exact-twin")
        else:
            r.flags.append("basename-clash")
        stem, ext = os.path.splitext(r.dest)
        n = 1
        while (vault / f"{stem}~dup{'' if n == 1 else n}{ext}").exists():
            n += 1
        r.dest = f"{stem}~dup{'' if n == 1 else n}{ext}"


def existing_targets(vault: Path) -> set:
    """Every current memory-root-relative note path, for `promoted_to` checks."""
    out = set()
    mem = vault / "memory"
    if mem.is_dir():
        for p in mem.rglob("*.md"):
            out.add(p.relative_to(vault).as_posix())
    return out


def plan(vault: Path, rules, *, purge_scope: str) -> list:
    targets = existing_targets(vault)
    rows = []
    for population, path in walk_population(vault, rules):
        row = Row(path, path.relative_to(vault).as_posix(), population)
        decide(row, rules, purge_scope=purge_scope, targets=targets)
        rows.append(row)
    dedupe(rows)
    settle_against_existing(rows, vault)
    # A promotion target that is itself routed moves too: point at where it lands.
    lands = {r.rel: r.dest for r in rows if r.disposition == "route"}
    for r in rows:
        if r.superseded_by in lands:
            r.superseded_by = lands[r.superseded_by]
    return rows


# ── reporting ────────────────────────────────────────────────────────────────

def summarize(rows: list, *, purge_scope: str) -> dict:
    by_pop = Counter(r.population for r in rows)
    by_disp = Counter(r.disposition for r in rows)
    by_pop_disp = Counter((r.population, r.disposition) for r in rows)
    by_dest_dir = Counter(os.path.dirname(r.dest) for r in rows if r.disposition == "route")
    flags = Counter(f for r in rows for f in r.flags)
    inbox_expired = sum(1 for r in rows if r.population == "inbox" and r.status_before == "expired")
    all_expired = sum(1 for r in rows if r.status_before == "expired")
    return {
        "notes": len(rows),
        "purge_scope": purge_scope,
        "by_population": dict(sorted(by_pop.items())),
        "by_disposition": dict(sorted(by_disp.items())),
        "by_population_disposition": {f"{p}/{d}": n for (p, d), n in sorted(by_pop_disp.items())},
        "routed_by_class": dict(sorted(by_dest_dir.items())),
        "flags": dict(sorted(flags.items())),
        "purge_count": by_disp.get("purge", 0),
        "purge_count_inbox_only": inbox_expired,
        "purge_count_all_expired": all_expired,
        "holds": [(r.rel, r.reason) for r in rows if r.disposition == "hold"],
    }


def render_summary(s: dict, *, run_id: str, vault: Path, applied: str = "") -> str:
    out = [f"# Corpus migration — part 3 · {run_id}", "",
           f"Vault (memory root): `{vault}`  ·  purge scope: `{s['purge_scope']}`"
           + (f"  ·  applied phase: `{applied}`" if applied else "  ·  dry run"), "",
           f"Notes considered: **{s['notes']}**", "", "## By population", "",
           "| population | notes |", "|---|---|"]
    out += [f"| {p} | {n} |" for p, n in s["by_population"].items()]
    out += ["", "## By disposition", "", "| disposition | notes |", "|---|---|"]
    out += [f"| {d} | {n} |" for d, n in s["by_disposition"].items()]
    out += ["", "## Population × disposition", "", "| population/disposition | notes |", "|---|---|"]
    out += [f"| {k} | {n} |" for k, n in s["by_population_disposition"].items()]
    out += ["", "## Routed, by class", "", "| class dir | notes |", "|---|---|"]
    out += [f"| {d} | {n} |" for d, n in s["routed_by_class"].items()]
    out += ["", "## Flags", "", "| flag | notes |", "|---|---|"]
    out += [f"| {f} | {n} |" for f, n in s["flags"].items()]
    out += ["", f"## Purge cohort: **{s['purge_count']}** notes (scope `{s['purge_scope']}`; "
                f"the design's inbox-only figure would be {s['purge_count_inbox_only']}; "
                f"every `status: expired` note, supplements included, would be {s['purge_count_all_expired']})", ""]
    if s["holds"]:
        out += ["## Held (never moved blind)", ""]
        reasons = Counter(reason for _, reason in s["holds"])
        out += [f"- {n} × {reason}" for reason, n in reasons.most_common()]
        out.append("")
    return "\n".join(out) + "\n"


def write_report(rows: list, summary: dict, out_dir: Path, *, run_id: str, vault: Path, applied: str = "") -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "dispositions.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REPORT_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r.as_record())
    with (out_dir / "purge-manifest.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(("path", "population", "title_or_slug", "sha256"))
        for r in rows:
            if r.disposition != "purge":
                continue
            w.writerow((r.rel, r.population, _title(r), _sha256(r.path)))
    (out_dir / "summary.md").write_text(render_summary(summary, run_id=run_id, vault=vault, applied=applied),
                                        encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    review = [r for r in rows if r.flags or r.disposition == "hold"]
    with (out_dir / "needs-review.md").open("w", encoding="utf-8") as f:
        f.write(f"# Needs review · {run_id}\n\n")
        for r in review:
            f.write(f"- `{r.rel}` — {r.disposition}" + (f" → `{r.dest}`" if r.dest else "")
                    + (f" · {' '.join(r.flags)}" if r.flags else "") + (f" · {r.reason}" if r.reason else "") + "\n")


def _title(row: Row) -> str:
    for i, key, value in frontmatter_lines("\n".join(row.lines)):
        if key in ("title", "slug") and value:
            return value.strip('"').strip("'")
    return row.path.stem


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


# ── applying ─────────────────────────────────────────────────────────────────

def edited_text(row: Row) -> str:
    """The note's text with its frontmatter lines rewritten and stamped."""
    lines = list(row.lines)
    fields = {}
    text = "\n".join(lines)
    for i, key, value in frontmatter_lines(text):
        fields.setdefault(key, (i, value))
    if row.field_before and (row.type_after or row.kind_after):
        i, value = fields[row.field_before]
        new_field = "type" if row.type_after else "kind"
        new_value = row.type_after or row.kind_after
        if not (new_field == row.field_before and new_value == value):
            lines[i] = rewrite_line(lines[i], value, new_field, new_value)
    if "status" in fields and row.status_after and row.status_after != fields["status"][1]:
        i, value = fields["status"]
        lines[i] = rewrite_line(lines[i], value, "status", row.status_after)
    additions = []
    for key, val in (("lifecycle", row.lifecycle), ("source", row.source),
                     ("filing_confidence", row.filing_confidence), ("superseded_by", row.superseded_by)):
        if not val:
            continue
        if key in fields:
            i, value = fields[key]
            if value != val:
                lines[i] = rewrite_line(lines[i], value, key, val)
        else:
            additions.append(f"{key}: {val}")
    if additions:
        lines[row.fm_end:row.fm_end] = additions
    return "\n".join(lines)


def _git(vault_root: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(vault_root), *args], capture_output=True, text=True)


def _tracked(vault_root: Path, path: Path) -> bool:
    if not (vault_root / ".git").exists():
        return False
    return _git(vault_root, "ls-files", "--error-unmatch", str(path)).returncode == 0


def move(vault_root: Path, src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if _tracked(vault_root, src):
        r = _git(vault_root, "mv", "-k", str(src), str(dest))
        if r.returncode == 0 and dest.exists():
            return
    src.rename(dest)


def apply_phase(rows: list, vault: Path, *, phase: str, confirm_count) -> tuple:
    """Perform one phase. Returns (moved, purged, dropped, edited)."""
    vault_root = vault.parent if (vault.parent / ".git").exists() else vault
    moved = purged = dropped = edited = 0
    if phase == "purge":
        cohort = [r for r in rows if r.disposition == "purge"]
        if confirm_count is None or confirm_count != len(cohort):
            raise SystemExit(f"purge refused: the manifest holds {len(cohort)} notes and --confirm-count "
                             f"{'was not given' if confirm_count is None else f'said {confirm_count}'} "
                             f"— the operator confirms the exact number, every time")
        for r in cohort:
            if _tracked(vault_root, r.path):
                _git(vault_root, "rm", "-q", str(r.path))
            if r.path.exists():
                r.path.unlink()
            purged += 1
        dropped = _drop_emptied(rows, vault, vault_root, populations=PURGE_PRUNES)
        return moved, purged, dropped, edited
    pops = tuple(p for p, ph in PHASE_OF_POPULATION.items() if ph == phase)
    for r in rows:
        if r.population not in pops or r.disposition != "route":
            continue
        dest = vault / r.dest
        if dest.exists() and dest.resolve() != r.path.resolve():
            raise SystemExit(f"refusing to overwrite {r.dest} (from {r.rel}) — resolve by hand")
        r.path.write_text(edited_text(r), encoding="utf-8")
        edited += 1
        move(vault_root, r.path, dest)
        moved += 1
    dropped = _drop_emptied(rows, vault, vault_root, populations=pops)
    return moved, purged, dropped, edited


def _population_bases(rows: list, vault: Path, populations) -> list:
    """The directories a phase dissolves: each population's own root."""
    fixed = {"inbox": INBOX, "dated": DATED, "archive": ARCHIVE, "opinions": OPINIONS, "external": EXTERNAL}
    bases = []
    for pop in populations:
        if pop in fixed:
            bases.append(vault / fixed[pop])
        elif pop == "supplements":
            # Each lane on its own — never the class directory, whose index
            # and flat memories are not a dissolving population.
            for r in rows:
                if r.population == "supplements" and r.path.parent not in bases:
                    bases.append(r.path.parent)
        elif pop == "legacy":
            for r in rows:
                if r.population == "legacy":
                    base = vault.joinpath(*Path(r.rel).parts[:2])
                    if base not in bases:
                        bases.append(base)
    return bases


def _prune(dir_path: Path, vault_root: Path) -> int:
    """Bottom-up over a dissolving directory: Drive `Icon` artefacts go, a lone
    generated `_index.md` goes with its directory, and a directory with nothing
    left in it is removed. Anything else — a held note, a purge-cohort note
    awaiting its confirmation — keeps the directory. Returns indexes dropped."""
    dropped = 0
    if not dir_path.is_dir():
        return 0
    for child in sorted(dir_path.iterdir()):
        if child.is_dir():
            dropped += _prune(child, vault_root)
    entries = [p for p in dir_path.iterdir() if not p.name.startswith("Icon")]
    if len(entries) == 1 and entries[0].is_file() and entries[0].name == INDEX_NAME:
        if _tracked(vault_root, entries[0]):
            _git(vault_root, "rm", "-q", str(entries[0]))
        if entries[0].exists():
            entries[0].unlink()
        dropped += 1
        entries = []
    if not entries:
        for icon in dir_path.glob("Icon*"):
            icon.unlink()
        try:
            dir_path.rmdir()
        except OSError:
            pass
    return dropped


def _drop_emptied(rows: list, vault: Path, vault_root: Path, *, populations) -> int:
    dropped = 0
    for base in _population_bases(rows, vault, populations):
        dropped += _prune(base, vault_root)
    return dropped


# ── main ─────────────────────────────────────────────────────────────────────

def resolve_vault(arg) -> Path:
    if arg:
        return Path(arg).expanduser()
    env = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if env:
        return Path(env).expanduser()
    raise SystemExit("no vault — pass --vault or set MEMORY_VAULT_PATH (the memory root)")


def default_report_dir(vault: Path, *, applied: bool) -> Path:
    if applied:
        return vault / "diagnostics" / "migrations" / "corpus-migration-3"
    state = os.environ.get("AGENTM_STATE_DIR", "").strip()
    base = Path(state).expanduser() if state else Path.home() / ".local" / "state" / "agentm"
    return base / "migrations" / "corpus-migration-3"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="corpus_migration_3.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vault", help="the memory root (default: $MEMORY_VAULT_PATH)")
    ap.add_argument("--rules", help="a rules file to load instead of the vault's (tests)")
    ap.add_argument("--apply", action="store_true", help="perform --phase; without it, dry run")
    ap.add_argument("--phase", choices=PHASES, help="which phase to apply")
    ap.add_argument("--confirm-count", type=int, default=None, help="the purge cohort size the operator confirmed")
    ap.add_argument("--purge-scope", choices=("inbox", "mined", "all-expired"), default="mined",
                    help="`inbox`: the design's literal cohort; `mined`: every auto-miner-retired expired note "
                         "outside the opinion lanes (default); `all-expired`: every expired note, supplements included")
    ap.add_argument("--report-dir", help="where the report lands (default: engine state; vault diagnostics on apply)")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 when a note carries a value the contract cannot place (the close-out battery's setting)")
    args = ap.parse_args(argv)

    vault = resolve_vault(args.vault)
    if not vault.is_dir():
        print(f"corpus_migration_3: {vault} is not a directory", file=sys.stderr)
        return 2
    if args.apply and not args.phase:
        print("corpus_migration_3: --apply needs --phase", file=sys.stderr)
        return 2
    try:
        rules = storage_rules.load_file(args.rules) if args.rules else storage_rules.load(vault_path=vault.parent)
    except storage_rules.StorageRulesError as exc:
        print(f"corpus_migration_3: {exc}", file=sys.stderr)
        return 2

    rows = plan(vault, rules, purge_scope=args.purge_scope)
    summary = summarize(rows, purge_scope=args.purge_scope)
    run_id = _dt.datetime.now().strftime("%Y%m%dT%H%M%S") + ("-" + args.phase if args.apply else "-dry")
    out_dir = Path(args.report_dir).expanduser() if args.report_dir else default_report_dir(vault, applied=args.apply)
    out_dir = out_dir / run_id

    if args.apply:
        moved, purged, dropped, edited = apply_phase(rows, vault, phase=args.phase, confirm_count=args.confirm_count)
        summary["applied"] = {"phase": args.phase, "moved": moved, "purged": purged,
                              "dropped_indexes": dropped, "edited": edited}
        write_report(rows, summary, out_dir, run_id=run_id, vault=vault, applied=args.phase)
        print(f"applied {args.phase}: moved={moved} edited={edited} purged={purged} dropped_indexes={dropped} · report {out_dir}")
    else:
        write_report(rows, summary, out_dir, run_id=run_id, vault=vault)
        print(render_summary(summary, run_id=run_id, vault=vault), end="")
        print(f"report: {out_dir}")

    unplaceable = [r for r in rows if r.disposition == "hold" and "neither register" in r.reason]
    if unplaceable:
        print(f"note: {len(unplaceable)} held note(s) carry a value the contract cannot place "
              f"(see needs-review.md){' — strict: failing' if args.strict else ''}", file=sys.stderr)
    return 1 if (unplaceable and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
