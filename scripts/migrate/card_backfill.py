#!/usr/bin/env python3
"""card_backfill — every surviving card takes the card's shape (agentm-vault plan 06).

The session-1 card (agentm-vault § The card) was decided after most of the
corpus was written. This migration brings the class directories to it without a
model call:

- deterministic stamps: `trust` from `source`; `created` from the earliest
  evidence (`captured`, the file's first vault commit, `enriched_at`);
  `filing_confidence` from `confidence` against the contract's floor, `medium`
  where nothing was ever scored; `status: unfiled` where a card claims `active`
  at `low`; `lifecycle`, `updated` and `source` where a writer left them off;
- the retired fields dropped, `captured` folded into `created`, empty tag lists
  removed, and a trace's `entities:` renamed `touched:`;
- a title where the body opens with an H1, a summary where the body is one
  sentence, and nothing else — the deep pass writes the rest;
- every note's frontmatter in the card's order;
- counter slugs renamed to their title, or to a name grown from their text, with
  the links to them rewritten in the agent's notes and the calendar.

It never writes `enriched_by` or `enriched_at`. The nightly enrichment decides
what it owes from those two stamps and from a key over the whole file, so this
migration names what it stamped in `backfilled:` instead, and re-records the
standing enrichment refusals of the cards it changed under their new keys.

Dry run by default. The dry run prints a count per stamp and per retirement and
records the plan in the engine state directory. `--apply` recomputes the plan,
refuses unless it matches the recorded one and `--confirm-count`, applies it
through the revert log, and writes a journal whose counts are read back from the
vault. Run the applied pass with the runner and the daemon quiesced, outside the
night's window. `--revert RUN_ID` restores every file the run wrote.

  python3 scripts/migrate/card_backfill.py
  python3 scripts/migrate/card_backfill.py --apply --plan PLAN --confirm-count N
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
_SCRIPTS = _REPO / "scripts"
_TOOLKIT = _REPO / "harness" / "skills" / "memory" / "scripts"
for _p in (str(_SCRIPTS), str(_TOOLKIT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import card_shape as cs  # noqa: E402

_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$")
_IGNORABLE = {".DS_Store", "Icon\r", "Icon", ".gitkeep", cs.MARKER_NAME}
_SKIP_DIRS = {".git", ".obsidian", ".trash"}
# Where a rename may rewrite a link: the agent's half and the shared calendar.
# The project space holds records of past measurements and manifests, whose
# paths say what was measured under the old names; a link there, or anywhere
# else, is listed and not written.
_LINK_SPACES = ("Agent/", "Calendar/")
_LIST_KEYS = ("touched", "related", "derived_from", "supersedes", "superseded_by")
_WS_RUN = re.compile(r"[ \t\f\v]+")


class Refused(Exception):
    """The applied run will not proceed; nothing was written."""


@dataclass
class Contract:
    sources: dict
    floor: float
    default_lifecycle: str


def load_contract(vault: Path) -> Contract:
    import storage_rules  # noqa: E402
    rules = storage_rules.load(vault_path=vault)
    floor = float(rules.thresholds().get("low_confidence", 0.65))
    return Contract(rules.sources(), floor, rules.default_lifecycle() or "active")


class Git:
    """The vault repository's dates for a note, following renames."""

    def __init__(self, vault: Path):
        self.vault = vault
        self.ok = (vault / ".git").exists()

    def _log(self, *args: str) -> list[str]:
        if not self.ok:
            return []
        r = subprocess.run(["git", "-C", str(self.vault), "log", *args],
                           capture_output=True, text=True)
        return [ln for ln in r.stdout.split("\n") if ln.strip()] if r.returncode == 0 else []

    def first_day(self, rel: str) -> str | None:
        out = self._log("--follow", "--diff-filter=A", "--format=%aI", "--", rel)
        return out[-1][:10] if out else None

    def last_day(self, rel: str) -> str | None:
        out = self._log("-1", "--format=%aI", "--", rel)
        return out[0][:10] if out else None


@dataclass
class NoteChange:
    rel: str
    kind: str
    before: str
    after: str = ""
    new_rel: str | None = None
    stamped: dict = field(default_factory=dict)
    corrected: dict = field(default_factory=dict)
    retired: list = field(default_factory=list)
    renamed_keys: dict = field(default_factory=dict)
    empty_tags: bool = False
    reordered: bool = False
    basis: dict = field(default_factory=dict)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _day(raw: str | None) -> str | None:
    v = cs.scalar(raw)
    return v[:10] if v and _DAY.match(v) else None


def _bare(raw: str) -> str:
    """A date or timestamp without its quotes, so it reads as a date."""
    v = cs.scalar(raw)
    return v if v and _ISO.match(v) else raw.strip()


def fingerprint_key(version: str, rules_hash: str, text: str) -> str:
    """The enrichment fingerprint key (`enrich.Fingerprint.Key`), ported."""
    norm = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [c for c in (_WS_RUN.sub(" ", ln.strip()) for ln in norm.split("\n")) if c]
    blob = f"v={version};rules={rules_hash};body={chr(10).join(lines).lower()}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── one note ──────────────────────────────────────────────────────────────────

def plan_note(vault: Path, path: Path, contract: Contract, git: Git):
    """`(change, new_text, facts)`. `change` is None when the note is already in
    shape or is not a note this migration reads."""
    rel = path.relative_to(vault).as_posix()
    text = path.read_text(encoding="utf-8")
    parsed = cs.split_note(text)
    if parsed is None:
        return None, text, {"finding": "no frontmatter block"}
    entries, rest = parsed
    body = rest[4:] if rest.startswith("---\n") else rest
    original = [k for k, _ in entries if k]
    is_card = "type" in original
    if not is_card and "kind" not in original:
        return None, text, {"finding": "neither type nor kind"}
    ch = NoteChange(rel=rel, kind="card" if is_card else "record", before=_sha(text))

    def get(key):
        return cs.raw_value(entries, key)

    for name in cs.RETIRED_FIELDS:
        if name != "captured" and get(name) is not None:
            entries = cs.drop_keys(entries, name)
            ch.retired.append(name)
    if not is_card:
        for old, new in cs.RENAMED_RECORD_FIELDS.items():
            if get(old) is None:
                continue
            if get(new) is None:
                entries = [(new, [g[0].replace(old, new, 1)] + g[1:]) if k == old else (k, g)
                           for k, g in entries]
            else:
                have = cs.flow_list(get(new))
                merged = have + [x for x in cs.flow_list(get(old)) if x not in have]
                entries = cs.set_value(cs.drop_keys(entries, old), new, "[" + ", ".join(merged) + "]")
            ch.renamed_keys[old] = new
        for name in cs.RECORD_NEVER:
            if get(name) is not None:
                entries = cs.drop_keys(entries, name)
                ch.retired.append(name)

    # `created`, with `captured` folded in.
    captured, created = get("captured"), get("created")
    if created is None and is_card:
        evidence = []
        if _day(captured):
            evidence.append((_day(captured), 0, "captured", _bare(captured)))
        first = git.first_day(rel)
        if first:
            evidence.append((first, 1, "first-commit", first))
        if _day(get("enriched_at")):
            evidence.append((_day(get("enriched_at")), 2, "enriched_at", _day(get("enriched_at"))))
        if evidence:
            _, _, basis, value = min(evidence)
            entries = cs.set_value(entries, "created", value)
            ch.stamped["created"], ch.basis["created"] = value, basis
    elif created is not None and _day(captured) and _day(created) and _day(captured) < _day(created):
        value = _bare(captured)
        ch.corrected["created"] = [cs.scalar(created), value]
        ch.basis["created"] = "captured-earlier"
        entries = cs.set_value(entries, "created", value)
    if captured is not None:
        entries = cs.drop_keys(entries, "captured")
        ch.retired.append("captured")

    facts: dict = {"is_card": is_card}
    if is_card:
        probe = get(cs.PROBE_KEY) is not None
        source = cs.scalar(get("source"))
        if source is None and not probe:
            source = "external-fetch" if get("source_url") is not None else "conversation"
            entries = cs.set_value(entries, "source", source)
            ch.stamped["source"] = source
            ch.basis["source"] = "source_url" if source == "external-fetch" else "default"
        if probe:
            facts["probe"] = True
        if source and not probe and source in contract.sources:
            want, have = contract.sources[source], cs.scalar(get("trust"))
            if have is None:
                entries = cs.set_value(entries, "trust", want)
                ch.stamped["trust"] = want
            elif have != want:
                entries = cs.set_value(entries, "trust", want)
                ch.corrected["trust"] = [have, want]
        try:
            confidence = float(cs.scalar(get("confidence"))) if get("confidence") is not None else None
        except ValueError:
            confidence = None
        tier = cs.scalar(get("filing_confidence"))
        if confidence is not None:
            want = "high" if confidence >= contract.floor else "low"
            if tier is None:
                entries = cs.set_value(entries, "filing_confidence", want)
                ch.stamped["filing_confidence"], ch.basis["filing_confidence"] = want, "confidence"
            elif tier != want:
                entries = cs.set_value(entries, "filing_confidence", want)
                ch.corrected["filing_confidence"] = [tier, want]
            tier = want
        elif tier is None:
            entries = cs.set_value(entries, "filing_confidence", "medium")
            ch.stamped["filing_confidence"], ch.basis["filing_confidence"] = "medium", "unscored"
            tier = "medium"
        if cs.scalar(get("status")) == "active" and tier == "low":
            entries = cs.set_value(entries, "status", "unfiled")
            ch.corrected["status"] = ["active", "unfiled"]
        if get("lifecycle") is None:
            entries = cs.set_value(entries, "lifecycle", contract.default_lifecycle)
            ch.stamped["lifecycle"] = contract.default_lifecycle
        if get("updated") is None:
            updated = git.last_day(rel) or _day(get("created"))
            if updated:
                entries = cs.set_value(entries, "updated", updated)
                ch.stamped["updated"] = updated
        if get("title") is None:
            title = cs.title_from_body(body)
            if title:
                entries = cs.set_value(entries, "title", cs.quote(title))
                ch.stamped["title"] = title
        if get("summary") is None:
            summary = cs.summary_from_body(body)
            if summary:
                entries = cs.set_value(entries, "summary", cs.quote(summary))
                ch.stamped["summary"] = summary

    tags = get("tags")
    if tags is not None and not cs.has_continuation(entries, "tags") and not cs.flow_list(tags):
        entries = cs.drop_keys(entries, "tags")
        ch.empty_tags = True

    # The slug is the address and always equals the file name (a machine field,
    # so it is not listed in `backfilled`).
    slug = cs.scalar(get("slug"))
    if slug is not None and slug != path.stem:
        entries = cs.set_value(entries, "slug", path.stem)
        ch.corrected["slug"] = [slug, path.stem]

    if is_card:
        names = [f for f in cs.READ_ORDER if f in ch.stamped or f in ch.corrected]
        if names:
            listed = cs.flow_list(get("backfilled"))
            merged = listed + [n for n in names if n not in listed]
            entries = cs.set_value(entries, "backfilled", "[" + ", ".join(merged) + "]")

    ordered = cs.order_entries(entries)
    final_keys = [k for k, _ in ordered if k]
    kept = [k for k in original if k in final_keys]
    ch.reordered = [k for k in final_keys if k in original] != kept
    new_text = cs.join_note(ordered, rest)
    facts["title"] = cs.scalar(cs.raw_value(ordered, "title"))
    facts["judged"] = cs.raw_value(ordered, "enriched_at") is not None
    facts["body"] = body
    facts["has_slug"] = cs.raw_value(ordered, "slug") is not None
    if new_text == text:
        return None, text, facts
    ch.after = _sha(new_text)
    return ch, new_text, facts


# ── links ─────────────────────────────────────────────────────────────────────

def rewrite_links(text: str, renames: dict, classes: "dict | None" = None) -> tuple[str, int]:
    """`text` with every link to a renamed note pointed at its new name: wikilinks,
    the items of the frontmatter lists that name notes by stem, and paths.

    A path is rewritten only when it names the renamed note itself: under
    `memory/<class>/`, with the note's own class (`classes` maps a stem to it; a
    caller without one accepts any class directory). A path elsewhere that ends
    in the same file name is a different file and keeps its name: a card's
    `derived_from` naming the inbox capture it was mined from, or a class
    directory from before the migration."""
    if not renames:
        return text, 0
    alt = "|".join(re.escape(o) for o in sorted(renames, key=len, reverse=True))
    count = 0

    def wiki(m):
        nonlocal count
        count += 1
        return "[[" + renames[m.group(1)] + (m.group(2) or "") + "]]"

    def path(m):
        nonlocal count
        cls, stem = m.group(1), m.group(2)
        if classes is not None and classes.get(stem) != cls:
            return m.group(0)
        count += 1
        return f"memory/{cls}/{renames[stem]}.md"

    text = re.sub(r"\[\[(" + alt + r")((?:#|\|)[^\]]*)?\]\]", wiki, text)
    text = re.sub(r"memory/([a-z]+)/(" + alt + r")\.md\b", path, text)
    parsed = cs.split_note(text)
    if parsed:
        entries, rest = parsed
        item_re = re.compile(r"([\[,\s\"'])(" + alt + r")(?=[\],\s\"']|$)")

        def item(m):
            nonlocal count
            count += 1
            return m.group(1) + renames[m.group(2)]

        out = []
        for k, group in entries:
            if k in _LIST_KEYS:
                group = [group[0].split(":", 1)[0] + ":" + item_re.sub(item, group[0].split(":", 1)[1])]\
                    + [item_re.sub(item, ln) for ln in group[1:]]
            out.append((k, group))
        text = cs.join_note(out, rest)
    return text, count


# ── the whole plan ────────────────────────────────────────────────────────────

def _vault_notes(vault: Path):
    for p in sorted(vault.rglob("*.md")):
        rel_parts = p.relative_to(vault).parts
        if any(part in _SKIP_DIRS for part in rel_parts):
            continue
        yield p


def _inner_dirs(class_dir: Path) -> tuple[list, list]:
    """The directories inside a class directory, deepest first, as `(empty,
    occupied)`. Empty means nothing in it but sync litter, however deep: it goes,
    because a class directory is flat. An occupied one is a finding for the
    operator and is never removed."""
    empty, occupied = [], []
    dirs = sorted((p for p in class_dir.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True)
    for d in dirs:
        held = [f for f in d.rglob("*") if f.is_file() and f.name not in _IGNORABLE]
        (occupied if held else empty).append(d)
    return empty, occupied


def _standing_refusals(path: Path | None) -> dict:
    rows: dict = {}
    if not path or not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            break
        rel = row.get("rel")
        if not rel:
            continue
        prev = rows.get(rel)
        if prev and prev.get("at", "") > row.get("at", ""):
            continue
        rows[rel] = row
    return rows


def build_plan(vault: Path, memory_root: Path, contract: Contract, git: Git,
               refusals_path: Path | None = None, state_dir: Path | None = None):
    """The plan, and the contents it would write."""
    changes: list[NoteChange] = []
    contents: dict = {}
    facts_by_rel: dict = {}
    findings: list = []
    cards = records = 0
    for cls in cs.SCOPE_CLASSES:
        d = memory_root / "memory" / cls
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.name in _IGNORABLE or p.is_dir() or p.suffix != ".md":
                continue
            ch, new_text, facts = plan_note(vault, p, contract, git)
            rel = p.relative_to(vault).as_posix()
            if "finding" in facts:
                findings.append(f"{rel}: {facts['finding']}")
                continue
            facts_by_rel[rel] = facts
            cards += facts["is_card"]
            records += not facts["is_card"]
            if ch:
                changes.append(ch)
                contents[rel] = new_text

    empty_dirs: list = []
    for cls in cs.SCOPE_CLASSES:
        d = memory_root / "memory" / cls
        if not d.is_dir():
            continue
        empty, occupied = _inner_dirs(d)
        empty_dirs += [p.relative_to(vault).as_posix() for p in empty]
        findings += [f"{p.relative_to(vault).as_posix()}/: a directory holding notes inside a class directory"
                     for p in occupied]

    # Renames: a counter slug takes its title, or a name grown from its text.
    taken = {p.stem.lower() for p in _vault_notes(vault)}
    renames: dict = {}
    classes: dict = {}
    unresolved: list = []
    for rel, facts in sorted(facts_by_rel.items()):
        path = Path(rel)
        if not facts["is_card"]:
            continue
        text = contents.get(rel) or (vault / rel).read_text(encoding="utf-8")
        if not cs.is_counter_slug(path.stem, facts.get("title")):
            continue
        target = cs.rename_target(path.stem, facts.get("title"), facts.get("body", ""), taken)
        if not target:
            unresolved.append(rel)
            continue
        taken.add(target.lower())
        renames[path.stem] = target
        classes[path.stem] = path.parent.name
        new_rel = (path.parent / f"{target}.md").as_posix()
        if facts.get("has_slug"):
            entries, rest = cs.split_note(text)
            text = cs.join_note(cs.set_value(entries, "slug", target), rest)
        ch = next((c for c in changes if c.rel == rel), None)
        if ch is None:
            ch = NoteChange(rel=rel, kind="card", before=_sha((vault / rel).read_text(encoding="utf-8")))
            changes.append(ch)
        ch.new_rel = new_rel
        contents[rel] = text
        ch.after = _sha(text)

    # Links to the renamed notes, everywhere they are.
    link_changes: list = []
    link_contents: dict = {}
    not_written: list = []
    occurrences = 0
    if renames:
        for p in _vault_notes(vault):
            rel = p.relative_to(vault).as_posix()
            current = contents.get(rel)
            text = current if current is not None else p.read_text(encoding="utf-8")
            new_text, n = rewrite_links(text, renames, classes)
            if not n:
                continue
            occurrences += n
            if current is not None:
                contents[rel] = new_text
                ch = next(c for c in changes if c.rel == rel)
                ch.after = _sha(new_text)
            elif rel.startswith(_LINK_SPACES):
                link_changes.append({"rel": rel, "before": _sha(text), "after": _sha(new_text), "occurrences": n})
                link_contents[rel] = new_text
            else:
                not_written.append({"rel": rel, "occurrences": n})

    # Standing refusals of the cards this plan changes, re-keyed.
    refusal_rows = []
    standing = reproduced = 0
    for rel, row in sorted(_standing_refusals(refusals_path).items()):
        p = vault / rel
        if not p.exists():
            continue
        if fingerprint_key(row["version"], row.get("rules_hash", ""), p.read_text(encoding="utf-8")) != row["key"]:
            continue
        standing += 1
        reproduced += 1
        if rel not in contents:
            continue
        ch = next(c for c in changes if c.rel == rel)
        new = dict(row)
        new["rel"] = ch.new_rel or rel
        new["key"] = fingerprint_key(row["version"], row.get("rules_hash", ""), contents[rel])
        refusal_rows.append(new)
    stored = len(_standing_refusals(refusals_path))

    # Sidecar keys are note stems; a renamed note keeps its recall history.
    sidecar_moves = []
    if state_dir and renames:
        for name in (".heat.json", ".lifecycle.json"):
            sp = state_dir / name
            if not sp.exists():
                continue
            entries = json.loads(sp.read_text(encoding="utf-8")).get("entries", {})
            for old, new in sorted(renames.items()):
                if old in entries and new not in entries:
                    sidecar_moves.append({"file": name, "from": old, "to": new})

    counts = _counts(changes, facts_by_rel, contents)
    counts.update({
        "cards": cards, "records": records,
        "files": len(changes) + len(link_changes),
        "renames": len(renames), "rename_unresolved": unresolved,
        "links": {"files": len(link_changes), "occurrences": occurrences, "not_written": not_written},
        "refusals": {"stored": stored, "standing": standing, "reproduced": reproduced,
                     "rerecord": len(refusal_rows)},
        "sidecars": len(sidecar_moves),
        "empty_dirs": len(empty_dirs),
    })
    plan = {
        "vault": str(vault), "memory_root": str(memory_root),
        "notes": [asdict(c) for c in sorted(changes, key=lambda c: c.rel)],
        "links": link_changes, "renames": renames, "refusals": refusal_rows,
        "sidecars": sidecar_moves, "empty_dirs": empty_dirs, "counts": counts, "findings": findings,
    }
    return plan, contents, link_contents


def _counts(changes, facts_by_rel, contents) -> dict:
    stamp, correct, retire, basis = Counter(), Counter(), Counter(), Counter()
    empty = renamed = reordered = 0
    for c in changes:
        stamp.update(c.stamped.keys())
        correct.update(c.corrected.keys())
        retire.update(c.retired)
        basis.update(f"{k}:{v}" for k, v in c.basis.items())
        empty += c.empty_tags
        renamed += len(c.renamed_keys)
        reordered += c.reordered
    judged_titleless, unjudged_titleless, probes = [], 0, []
    for rel, facts in facts_by_rel.items():
        if not facts["is_card"]:
            continue
        if facts.get("probe"):
            probes.append(rel)
        if not facts.get("title"):
            if facts.get("judged"):
                judged_titleless.append(rel)
            else:
                unjudged_titleless += 1
    return {
        "stamp": dict(sorted(stamp.items())), "correct": dict(sorted(correct.items())),
        "retire": dict(sorted(retire.items())), "basis": dict(sorted(basis.items())),
        "empty_tags": empty, "entities_to_touched": renamed, "reordered": reordered,
        "titleless_judged": sorted(judged_titleless), "titleless_unjudged": unjudged_titleless,
        "probes": sorted(probes),
    }


def _signature(plan: dict) -> list:
    return ([(n["rel"], n["new_rel"], n["before"], n["after"]) for n in plan["notes"]]
            + [(l["rel"], l["before"], l["after"]) for l in plan["links"]]
            + [("dir", d) for d in plan.get("empty_dirs", [])])


def print_summary(plan: dict, out=sys.stdout) -> None:
    c = plan["counts"]
    p = lambda *a: print(*a, file=out)  # noqa: E731
    p(f"cards {c['cards']} · records {c['records']} · files to write {c['files']} "
      f"(notes {len(plan['notes'])}, links in {c['links']['files']} other files)")
    p("stamps: " + " · ".join(f"{k} {v}" for k, v in c["stamp"].items()))
    p("  basis: " + " · ".join(f"{k} {v}" for k, v in c["basis"].items()))
    p("corrections: " + (" · ".join(f"{k} {v}" for k, v in c["correct"].items()) or "none"))
    p("retired: " + " · ".join(f"{k} {v}" for k, v in c["retire"].items()))
    p(f"empty tag lists removed {c['empty_tags']} · entities→touched {c['entities_to_touched']} · "
      f"reordered {c['reordered']}")
    p(f"renames {c['renames']} · links rewritten {c['links']['occurrences']} · "
      f"unresolved {len(c['rename_unresolved'])} · links not written {len(c['links']['not_written'])}")
    for old, new in sorted(plan["renames"].items()):
        p(f"  {old} -> {new}")
    for item in c["links"]["not_written"]:
        p(f"  link not written (outside {', '.join(_LINK_SPACES)}): {item['rel']} ×{item['occurrences']}")
    r = c["refusals"]
    p(f"refusals: stored {r['stored']} · standing {r['standing']} · key reproduced {r['reproduced']} · "
      f"re-recorded {r['rerecord']}")
    p(f"sidecar keys moved {c['sidecars']} · empty directories removed {c['empty_dirs']} · "
      f"self-probe cards {len(c['probes'])}")
    for rel in plan.get("empty_dirs", []):
        p(f"  empty directory: {rel}/")
    p(f"title-less: judged {len(c['titleless_judged'])} · awaiting the deep pass {c['titleless_unjudged']}")
    for rel in c["titleless_judged"]:
        p(f"  judged without a title: {rel}")
    for f in plan["findings"]:
        p(f"finding: {f}")


# ── apply ─────────────────────────────────────────────────────────────────────

def apply(vault: Path, memory_root: Path, contract: Contract, git: Git, recorded: dict,
          confirm_count: int, out_dir: Path, run_id: str, refusals_path: Path | None,
          state_dir: Path | None, log_root: Path | None = None) -> dict:
    import revert_log  # noqa: E402
    plan, contents, link_contents = build_plan(vault, memory_root, contract, git, refusals_path, state_dir)
    if _signature(plan) != _signature(recorded):
        raise Refused("the vault is not what the dry run read; run the dry run again. Nothing written.")
    files = plan["counts"]["files"]
    if confirm_count != files:
        raise Refused(f"the plan writes {files} files; you confirmed {confirm_count}. Nothing written.")
    log = revert_log.RevertLog(vault, log_root=log_root)
    entry_ids = {}
    note_muts = []
    for n in plan["notes"]:
        if n["new_rel"]:
            note_muts += [(vault / n["new_rel"], contents[n["rel"]]), (vault / n["rel"], None)]
        else:
            note_muts.append((vault / n["rel"], contents[n["rel"]]))
    if note_muts:
        entry_ids["notes"] = log.record_and_apply(run_id, "card-backfill-notes", note_muts)
    if link_contents:
        entry_ids["links"] = log.record_and_apply(
            run_id, "card-backfill-links", [(vault / rel, t) for rel, t in sorted(link_contents.items())])

    # A class directory is flat: the directories with nothing in them go, sync
    # litter first. They held no note, so a revert does not recreate them.
    removed_dirs = []
    for rel in plan["empty_dirs"]:
        d = vault / rel
        if not d.is_dir():
            continue
        for f in d.rglob("*"):
            if f.is_file() and f.name in _IGNORABLE:
                f.unlink()
        try:
            d.rmdir()
            removed_dirs.append(rel)
        except OSError:
            pass

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if plan["refusals"] and refusals_path:
        with open(refusals_path, "a", encoding="utf-8") as fh:
            for row in plan["refusals"]:
                fh.write(json.dumps(dict(row, at=now)) + "\n")
    sidecar_pre = {}
    for move in plan["sidecars"]:
        sp = state_dir / move["file"]
        data = json.loads(sp.read_text(encoding="utf-8"))
        sidecar_pre.setdefault(move["file"], json.dumps(data))
        entries = data.setdefault("entries", {})
        if move["from"] in entries and move["to"] not in entries:
            entries[move["to"]] = entries.pop(move["from"])
        sp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    # Read the vault back: the journal counts what landed, not what was meant.
    landed = []
    for n in plan["notes"]:
        target = vault / (n["new_rel"] or n["rel"])
        ok = target.exists() and _sha(target.read_text(encoding="utf-8")) == n["after"]
        ok = ok and (not n["new_rel"] or not (vault / n["rel"]).exists())
        landed.append(ok)
    links_ok = all(_sha((vault / l["rel"]).read_text(encoding="utf-8")) == l["after"] for l in plan["links"])
    marker = memory_root / "memory" / cs.MARKER_NAME
    journal = {
        "run_id": run_id, "applied_at": now, "revert_log_entries": entry_ids,
        "notes_landed": sum(landed), "notes_planned": len(plan["notes"]),
        "links_landed": len(plan["links"]) if links_ok else 0, "links_planned": len(plan["links"]),
        "counts": plan["counts"], "renames": plan["renames"],
        "refusals_rerecorded": plan["refusals"], "sidecar_moves": plan["sidecars"],
        "sidecar_pre_images": sidecar_pre, "empty_dirs_removed": removed_dirs,
    }
    journal["matches_dry_run"] = (plan["counts"] == recorded["counts"] and all(landed) and links_ok
                                  and len(removed_dirs) == len(plan["empty_dirs"]))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"journal-{run_id}.json").write_text(json.dumps(journal, indent=2) + "\n", encoding="utf-8")
    if journal["matches_dry_run"]:
        marker.write_text(f"run {run_id}\napplied {now}\nfiles {files}\n", encoding="utf-8")
    return journal


def revert(vault: Path, memory_root: Path, run_id: str, log_root: Path | None = None) -> None:
    import revert_log  # noqa: E402
    revert_log.RevertLog(vault, log_root=log_root).revert(run_id)
    marker = memory_root / "memory" / cs.MARKER_NAME
    if marker.exists():
        marker.unlink()


# ── command line ──────────────────────────────────────────────────────────────

def _defaults():
    import harness_memory as hm  # noqa: E402
    import engine_state  # noqa: E402
    return Path(hm.vault_path()), Path(hm.memory_root()), Path(engine_state.engine_state_dir())


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", help="vault root (default: the configured one)")
    ap.add_argument("--memory-root", help="memory root (default: the configured one)")
    ap.add_argument("--state-dir", help="engine state directory (default: the configured one)")
    ap.add_argument("--apply", action="store_true", help="apply a recorded plan")
    ap.add_argument("--plan", help="the plan file the dry run recorded")
    ap.add_argument("--confirm-count", type=int, help="the plan's file count, typed by the operator")
    ap.add_argument("--revert", metavar="RUN_ID", help="restore every file a run wrote")
    args = ap.parse_args(argv)

    vault = memory_root = state_dir = None
    if not (args.vault and args.memory_root and args.state_dir):
        vault, memory_root, state_dir = _defaults()
    vault = Path(args.vault) if args.vault else vault
    memory_root = Path(args.memory_root) if args.memory_root else memory_root
    state_dir = Path(args.state_dir) if args.state_dir else state_dir
    out_dir = state_dir / "card-backfill"
    refusals = state_dir / "enrich-refusals.jsonl"

    if args.revert:
        revert(vault, memory_root, args.revert)
        print(f"card backfill: reverted {args.revert}")
        return 0

    contract = load_contract(vault)
    git = Git(vault)
    if args.apply:
        if not args.plan or args.confirm_count is None:
            print("card backfill: --apply needs --plan and --confirm-count", file=sys.stderr)
            return 2
        recorded = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        try:
            journal = apply(vault, memory_root, contract, git, recorded, args.confirm_count,
                            out_dir, recorded["run_id"], refusals, state_dir)
        except Refused as exc:
            print(f"card backfill: refused — {exc}", file=sys.stderr)
            return 1
        print(f"card backfill: applied {recorded['run_id']} — notes {journal['notes_landed']}/"
              f"{journal['notes_planned']}, links {journal['links_landed']}/{journal['links_planned']}, "
              f"refusals re-recorded {len(journal['refusals_rerecorded'])}, sidecar keys moved "
              f"{len(journal['sidecar_moves'])}; journal matches the dry run: "
              f"{'yes' if journal['matches_dry_run'] else 'NO'}")
        print(f"journal: {out_dir / ('journal-' + recorded['run_id'] + '.json')}")
        return 0 if journal["matches_dry_run"] else 1

    run_id = "card-backfill-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    plan, _contents, _links = build_plan(vault, memory_root, contract, git, refusals, state_dir)
    plan["run_id"] = run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    plan_path = out_dir / f"plan-{run_id}.json"
    plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    print(f"card backfill — dry run {run_id}")
    print_summary(plan)
    print(f"plan: {plan_path}")
    print(f"apply: python3 scripts/migrate/card_backfill.py --apply --plan {plan_path} "
          f"--confirm-count {plan['counts']['files']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
