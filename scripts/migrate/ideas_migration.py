#!/usr/bin/env python3
"""ideas_migration — the one-time move of the ideas into `personal/ideas/`
(agentm-vault plan 13).

It reads the operator's corrected mapping — `mapping.md` beside the plan, one
line per card typed `idea` and per entry in `Ideas.md` — checks it against the
vault as it is, and plans every write:

  move       an idea card from `memory/semantic/` to the vault root's
             `personal/ideas/`, as `idea_cards.as_idea_card` shapes it: active,
             at its group, without `lifecycle`. A card that is the same idea
             as an entry takes the entry's first sentence as its summary when it
             has none, and the entry's paragraphs it does not already hold,
             word for word, under `## From Ideas.md (<date>)`.
  supersede  a duplicate, left where it is: `lifecycle: superseded`, the day,
             and `superseded_by` naming the survivor in `personal/ideas/`.
  relabel    a note that holds something real under the wrong label: its
             `type` changed, in place, nothing else.
  create     a card for each entry that had none: its title and date, its
             cluster as `related:`, `dismissed:` where it was struck, and its
             text as the body with each link re-pointed where the vault can say
             where it went.

The deletion list is not this script's to act on. It is written out as
`deletions.txt` for `purge.py select --paths-file`, which deletes only on the
count the operator confirms.

  dry run   (default) Prints every write and the deletion list, and records
            the plan with every note's bytes before and after. Nothing changes.
  --apply   The recorded plan, refused unless the count is confirmed, no writer
            is live (Obsidian quit, the daemon down) and every note still
            hashes as the plan read it. Each write is journaled before it is
            made, and each result is read back.
  --revert  Undoes a run from its journal, newest first, refusing a note that
            no longer holds what the run wrote.

  python3 scripts/migrate/ideas_migration.py --mapping MAPPING [--out DIR]
  python3 scripts/migrate/ideas_migration.py --apply --plan PLAN --confirm-count N
  python3 scripts/migrate/ideas_migration.py --revert JOURNAL
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
for _p in (_REPO / "harness" / "skills" / "memory" / "scripts", _REPO / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import card_shape  # noqa: E402
import idea_cards  # noqa: E402

SEMANTIC = "memory/semantic"
RELABEL_TYPES = ("reference", "convention", "preference", "workflow", "fix")
_ENTRY_HEAD = re.compile(r"^## (~~)?(\d{4}-\d{2}-\d{2}): (.+?)(~~)?(?: — Dismissed (\d{4}-\d{2}-\d{2}))?\s*$")
_CARD_ROW = re.compile(r"^\|\s*`([a-z0-9][a-z0-9~-]*)`\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|")
_ENTRY_ROW = re.compile(r"^\|\s*E(\d{2})\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*`([a-z0-9-]+)`\s*\|")
_LINK = re.compile(r"\[\[([^\]|#]+)(#[^\]|]*)?(\|[^\]]*)?\]\]")
_DISMISSED = re.compile(r"^new, dismissed (\d{4}-\d{2}-\d{2})$")


class Refused(RuntimeError):
    """The mapping, the vault or the operator's count disagree."""


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── reading ───────────────────────────────────────────────────────────────────

def read_entries(ideas_text: str) -> list:
    """The `## YYYY-MM-DD: Title` entries of the hand-kept `Ideas.md`, in order."""
    entries, cur = [], None
    for n, line in enumerate(ideas_text.split("\n"), 1):
        m = _ENTRY_HEAD.match(line)
        if m:
            if cur:
                entries.append(cur)
            cur = {"line": n, "date": m.group(2), "title": m.group(3).strip(),
                   "dismissed": m.group(5), "body": []}
            continue
        if cur is None:
            continue
        if line.strip() == "---":
            entries.append(cur)
            cur = None
        elif not (line.startswith("<!--") and line.rstrip().endswith("-->")):
            cur["body"].append(line)
    if cur:
        entries.append(cur)
    for e in entries:
        e["body"] = "\n".join(e["body"]).strip("\n")
    return entries


def read_cards(memory_root: Path) -> dict:
    """Every note in `memory/semantic/` typed `idea` (or the legacy `kind`)."""
    out = {}
    folder = memory_root / SEMANTIC
    for p in sorted(folder.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        parsed = card_shape.split_note(text)
        if parsed is None:
            continue
        entries, _ = parsed
        kinds = {card_shape.scalar(card_shape.raw_value(entries, k)) for k in ("type", "kind")}
        if "idea" in kinds:
            out[p.stem] = {"rel": f"{SEMANTIC}/{p.name}", "text": text}
    return out


def parse_mapping(text: str) -> dict:
    """The operator's corrected worksheet, read back: each card's verdict and
    group, each entry's verdict, group and slug."""
    cards, entries = {}, {}
    for line in text.split("\n"):
        me = _ENTRY_ROW.match(line)
        if me:
            n = int(me.group(1))
            if n in entries:
                raise Refused(f"entry E{n:02} appears twice in the mapping")
            entries[n] = (me.group(2).strip(), me.group(3).strip(), me.group(4).strip())
            continue
        mc = _CARD_ROW.match(line)
        if mc and mc.group(2).strip():
            stem, verdict, third = mc.group(1), mc.group(2).strip(), mc.group(3).strip()
            if stem in cards:
                raise Refused(f"{stem} appears twice in the mapping")
            # The deletion table's third column is the title, not a group.
            cards[stem] = (verdict, "" if verdict == "delete" else third)
    return {"cards": cards, "entries": entries}


# ── checking ──────────────────────────────────────────────────────────────────

def check(mapping: dict, cards: dict, entries: list, ideas_dir: Path) -> list:
    """Every reason the mapping cannot be applied to this vault, or []."""
    errs = []
    mc, me = mapping["cards"], mapping["entries"]
    for stem in sorted(set(cards) - set(mc)):
        errs.append(f"{stem} is an idea card in {SEMANTIC}/ with no line in the mapping")
    for stem in sorted(set(mc) - set(cards)):
        errs.append(f"{stem} has a line in the mapping and no idea card in {SEMANTIC}/")
    for n in range(1, len(entries) + 1):
        if n not in me:
            errs.append(f"entry E{n:02} ({entries[n - 1]['title'][:50]}) has no line in the mapping")
    for n in sorted(set(me) - set(range(1, len(entries) + 1))):
        errs.append(f"E{n:02} is in the mapping and not in Ideas.md")
    ideas = {s for s, (v, _g) in mc.items() if v == "idea"}
    landing = {}  # slug -> what lands there
    for stem, (verdict, group) in sorted(mc.items()):
        if verdict == "idea":
            if not idea_cards.valid_area(group):
                errs.append(f"{stem}: the group {group!r} is not one lower-case word")
            landing.setdefault(stem, []).append(stem)
        elif verdict.startswith("dup:"):
            survivor = verdict[4:]
            new_slugs = {slug for (v, _g, slug) in me.values() if v.startswith("new")}
            if survivor not in ideas and survivor not in new_slugs:
                errs.append(f"{stem}: its survivor {survivor} does not land in personal/ideas/")
        elif verdict.startswith("relabel:"):
            if verdict[8:] not in RELABEL_TYPES:
                errs.append(f"{stem}: {verdict[8:]!r} is not one of {', '.join(RELABEL_TYPES)}")
        elif verdict != "delete":
            errs.append(f"{stem}: {verdict!r} is not idea, dup:<survivor>, relabel:<type> or delete")
    for n, (verdict, group, slug) in sorted(me.items()):
        if verdict.startswith("absorbed:"):
            if verdict[9:] not in ideas:
                errs.append(f"E{n:02}: {verdict[9:]} is not a card that stays an idea")
            continue
        if not (verdict == "new" or _DISMISSED.match(verdict)):
            errs.append(f"E{n:02}: {verdict!r} is not new, new, dismissed YYYY-MM-DD, or absorbed:<card>")
            continue
        if not idea_cards.valid_area(group):
            errs.append(f"E{n:02}: the group {group!r} is not one lower-case word")
        if card_shape.cut_slug(slug) != slug or not re.match(r"^[a-z0-9][a-z0-9-]*$", slug):
            errs.append(f"E{n:02}: {slug!r} is not a slug")
        landing.setdefault(slug, []).append(f"E{n:02}")
    for slug, who in sorted(landing.items()):
        if len(who) > 1:
            errs.append(f"personal/ideas/{slug}.md would be written by {', '.join(who)}")
        if (ideas_dir / f"{slug}.md").exists():
            errs.append(f"personal/ideas/{slug}.md already exists and is never overwritten")
    return errs


# ── composing ─────────────────────────────────────────────────────────────────

def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*_`>\[\]]", "", t)).lower()


def lacking_paragraphs(card_text: str, entry: dict, stem: str) -> list:
    """The entry's paragraphs the card does not already hold: fewer than 60% of
    their long words in the card, and not a pointer to the incubator the card
    itself came from."""
    words = set(re.findall(r"[a-z0-9]{6,}", _norm(card_text)))
    out = []
    for para in [p for p in re.split(r"\n\s*\n", entry["body"]) if p.strip()]:
        first = para.strip().split("\n")[0]
        if re.match(r"\*\*(Deep|Cluster) research", first) and "_idea-incubator/" in para:
            continue
        mine = set(re.findall(r"[a-z0-9]{6,}", _norm(para)))
        if mine and len(mine & words) / len(mine) < 0.6:
            out.append(para.strip())
    return out


def summary_of(body: str) -> str:
    """The entry's first sentence of prose, as a card's one-line summary."""
    for para in re.split(r"\n\s*\n", body):
        p = para.strip()
        if not p or p.startswith((">", "<!--", "Source", "**Source", "**Deep", "**Cluster",
                                  "**Update", "**Upgrade", "- ", "~~")):
            continue
        p = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", p)
        p = re.sub(r"\[\[([^\]]+)\]\]", lambda m: m.group(1).rsplit("/", 1)[-1], p)
        p = re.sub(r"\*\*|__|`", "", " ".join(p.split()))
        m = re.match(r"(.+?[.!?])(\s|$)", p)
        s = m.group(1) if m else p
        if len(s) > 220:
            s = s[:220].rsplit(" ", 1)[0] + " …"
        return s
    return ""


def stem_index(vault_root: Path) -> dict:
    """Every note's stem in the vault, lower-cased, to the paths that carry it."""
    out: dict = {}
    for dp, dns, fns in os.walk(vault_root):
        dns[:] = [d for d in dns if d not in {".git", ".obsidian", ".trash"}]
        for fn in fns:
            if fn.endswith(".md"):
                rel = Path(dp, fn).relative_to(vault_root).as_posix()
                out.setdefault(fn[:-3].lower(), []).append(rel)
    return out


def repoint(text: str, vault_root: Path, stems: dict, landed: dict,
            titles: "dict | None" = None) -> "tuple[str, list]":
    """The text with each wikilink that no longer resolves re-pointed where the
    vault can say where it went, and the list of what changed or stayed stale.

    A link that resolves by its path is left as it is. An incubator's summary or
    index becomes the card the incubator became; another entry named by its
    title, that entry's card; a retired `personal-projects/<p>/_index`, that
    project's charter; anything else, its basename when exactly one note has
    it. What none of those can place is kept as written and reported."""
    notes = []
    titles = {k.lower(): v for k, v in (titles or {}).items()}

    def resolves(target: str) -> bool:
        t = target.strip()
        if (vault_root / t).is_file() or (vault_root / f"{t}.md").is_file():
            return True
        # A bare name resolves the way Obsidian resolves it: one note by it.
        return "/" not in t and len(stems.get(t.lower(), [])) == 1

    def one(m: re.Match) -> str:
        target, anchor, label = m.group(1).strip(), m.group(2) or "", m.group(3) or ""
        if resolves(target):
            return m.group(0)
        new = titles.get(target.lower())
        inc = re.match(r"_idea-incubator/([^/]+)/(_summary|_index)$", target)
        if new is not None:
            pass
        elif inc and inc.group(1) in landed:
            new = landed[inc.group(1)]
        elif re.match(r"personal-projects/([^/]+)/_index$", target):
            p = target.split("/")[1]
            for cand in (f"projects/{p}/charter", f"projects/completed/{p}/_index", f"projects/{p}/_index"):
                if resolves(cand):
                    new = cand
                    break
        if new is None:
            base = target.rsplit("/", 1)[-1]
            hits = stems.get(base.lower(), [])
            if len(hits) == 1 and base.lower() not in ("_index", "_summary", "charter"):
                new = base
        if new is None:
            notes.append(f"kept stale: [[{target}]]")
            return m.group(0)
        notes.append(f"[[{target}]] -> [[{new}]]")
        # A heading anchor goes with the move: the heading was the old file's.
        return f"[[{new}{label}]]"

    return _LINK.sub(one, text), notes


def entry_card(entry: dict, *, slug: str, area: str, dismissed: "str | None", related: list,
               today: str, body: str) -> str:
    """A card for an entry that had none, in the card's shape."""
    lines = [f"title: {card_shape.quote(entry['title'])}", "type: idea", f"area: {area}"]
    summary = summary_of(entry["body"])
    if summary:
        lines.append(f"summary: {card_shape.quote(summary)}")
    lines.append("status: active")
    if dismissed:
        lines.append(f"dismissed: {dismissed}")
    lines += ["filing_confidence: high", "source: operator-direct", "trust: trusted",
              f"created: {entry['date']}", f"updated: {today}"]
    if related:
        lines.append("related: [" + ", ".join(card_shape.quote(f"[[{r}]]") for r in related) + "]")
    lines.append(f"slug: {slug}")
    return card_shape.reorder("---\n" + "\n".join(lines) + "\n---\n\n" + body.strip("\n") + "\n")


def with_supersession(text: str, survivor_rel: str, today: str) -> str:
    entries, rest = card_shape.split_note(text)
    entries = card_shape.set_value(entries, "lifecycle", "superseded")
    entries = card_shape.set_value(entries, "lifecycle_since", today)
    entries = card_shape.set_value(entries, "superseded_by", survivor_rel)
    return card_shape.reorder(card_shape.join_note(entries, rest))


def with_type(text: str, new_type: str) -> str:
    entries, rest = card_shape.split_note(text)
    entries = card_shape.set_value(entries, "type", new_type)
    return card_shape.reorder(card_shape.join_note(entries, rest))


# ── planning ──────────────────────────────────────────────────────────────────

def build_plan(vault_root: Path, memory_root: Path, mapping_text: str, *, today: "str | None" = None) -> dict:
    today = today or date.today().isoformat()
    ideas_file = vault_root / "Ideas.md"
    entries = read_entries(ideas_file.read_text(encoding="utf-8")) if ideas_file.is_file() else []
    cards = read_cards(memory_root)
    mapping = parse_mapping(mapping_text)
    ideas_dir = vault_root / "personal" / "ideas"
    errs = check(mapping, cards, entries, ideas_dir)
    if errs:
        raise Refused("the mapping cannot be applied as it stands:\n  " + "\n  ".join(errs))
    mem_rel = memory_root.relative_to(vault_root).as_posix()
    mc, me = mapping["cards"], mapping["entries"]
    stems = stem_index(vault_root)
    # Where each idea lands, by its old name and by the incubator's, so a link
    # to either finds the card.
    landed = {s: s for s, (v, _g) in mc.items() if v == "idea"}
    landed.update({slug: slug for (v, _g, slug) in me.values() if v.startswith("new")})
    landed.setdefault("scheduled-agentic-harness-skills", landed.get("scheduled-agentm-skills", ""))
    landed = {k: v for k, v in landed.items() if v}
    absorbed = {verdict[9:]: entries[n - 1] for n, (verdict, _g, _s) in me.items()
                if verdict.startswith("absorbed:")}
    # An entry named by another entry's text, by its title: the card it became.
    titles = {}
    for n, (verdict, _g, slug) in me.items():
        card = verdict[9:] if verdict.startswith("absorbed:") else slug
        e = entries[n - 1]
        titles[e["title"]] = card
        titles[f"{e['date']}: {e['title']}"] = card
    ops, report, deletions = [], [], []
    for stem, (verdict, group) in sorted(mc.items()):
        src = f"{mem_rel}/{cards[stem]['rel']}"
        before = cards[stem]["text"]
        if verdict == "idea":
            after = idea_cards.as_idea_card(before, area=group, today=today)
            entry = absorbed.get(stem)
            if entry:
                parsed = card_shape.split_note(after)
                ents, rest = parsed
                if not card_shape.scalar(card_shape.raw_value(ents, "summary")):
                    s = summary_of(entry["body"])
                    if s:
                        after = card_shape.reorder(card_shape.join_note(
                            card_shape.set_value(ents, "summary", card_shape.quote(s)), rest))
                lack = lacking_paragraphs(before, entry, stem)
                if lack:
                    text, notes = repoint("\n\n".join(lack), vault_root, stems, landed, titles)
                    after = after.rstrip("\n") + f"\n\n## From Ideas.md ({entry['date']})\n\n" + text + "\n"
                    report += [f"  {stem}: {n}" for n in notes]
            ops.append({"op": "move", "from": src, "to": f"personal/ideas/{stem}.md",
                        "before_sha": sha(before), "before": before, "after": after})
        elif verdict.startswith("dup:"):
            survivor = f"personal/ideas/{verdict[4:]}.md"
            ops.append({"op": "edit", "from": src, "to": src, "before_sha": sha(before), "before": before,
                        "after": with_supersession(before, survivor, today)})
        elif verdict.startswith("relabel:"):
            ops.append({"op": "edit", "from": src, "to": src, "before_sha": sha(before), "before": before,
                        "after": with_type(before, verdict[8:])})
        else:
            deletions.append(cards[stem]["rel"])
    for n, (verdict, group, slug) in sorted(me.items()):
        if verdict.startswith("absorbed:"):
            continue
        entry = entries[n - 1]
        dm = _DISMISSED.match(verdict)
        body, notes = repoint(entry["body"], vault_root, stems, landed, titles)
        report += [f"  E{n:02} {slug}: {x}" for x in notes]
        related = []
        if re.search(r"home-tech-next", entry["body"]) and (vault_root / "projects/home-tech-next/charter.md").is_file():
            related.append("projects/home-tech-next/charter|Home Tech Next")
        if "home-server-cluster" in entry["body"] and "home-server-cluster" in landed:
            related.append("home-server-cluster")
        if "small-coding-projects" in entry["body"] and \
                (vault_root / "projects/completed/small-coding-projects/_index.md").is_file():
            related.append("projects/completed/small-coding-projects/_index|Small Coding Projects")
        after = entry_card(entry, slug=slug, area=group, dismissed=dm.group(1) if dm else None,
                           related=related, today=today, body=body)
        ops.append({"op": "create", "from": None, "to": f"personal/ideas/{slug}.md",
                    "before_sha": None, "before": None, "after": after})
    return {"written": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), "vault": str(vault_root),
            "memory_root": str(memory_root), "today": today, "ops": ops, "deletions": deletions,
            "links": report, "count": len(ops)}


def summary(plan: dict) -> str:
    kinds = {}
    for op in plan["ops"]:
        kinds[op["op"]] = kinds.get(op["op"], 0) + 1
    moves = [o for o in plan["ops"] if o["op"] == "move"]
    lines = [f"idea cards: {plan['count']} write(s) — " + ", ".join(f"{v} {k}" for k, v in sorted(kinds.items())),
             f"  personal/ideas/ would hold {len(moves) + kinds.get('create', 0)} card(s)",
             f"  deletion list: {len(plan['deletions'])} (purge.py select --paths-file, on your confirmed count)"]
    for op in plan["ops"]:
        lines.append(f"  {op['op']:9} {op['from'] or '(new)'} -> {op['to']}")
    if plan["links"]:
        lines.append("  links:")
        lines += [f"  {x}" for x in plan["links"]]
    return "\n".join(lines) + "\n"


# ── applying ──────────────────────────────────────────────────────────────────

def live_writers() -> list:
    """What would write the vault under the move: Obsidian, the daemon."""
    found = []
    for args, name in ((["pgrep", "-x", "Obsidian"], "Obsidian"),
                       (["pgrep", "-f", r"agentmd serve"], "the agentm daemon")):
        try:
            if subprocess.run(args, capture_output=True).returncode == 0:
                found.append(name)
        except FileNotFoundError:
            found.append(f"{name} (pgrep unavailable, so it cannot be ruled out)")
    return found


def _write_atomic(path: Path, text: str, *, exclusive: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.idea-cards.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    try:
        if exclusive:
            os.link(tmp, path)  # refuses an existing name rather than replacing it
        else:
            os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def apply(plan: dict, confirm_count: int, journal: Path, *, writers=live_writers) -> int:
    vault_root = Path(plan["vault"])
    if confirm_count != plan["count"]:
        raise Refused(f"the plan holds {plan['count']} writes; you confirmed {confirm_count}. Nothing written.")
    live = writers()
    if live:
        raise Refused(f"still running: {', '.join(live)}. Quit Obsidian and boot the daemon out first. "
                      f"Nothing written.")
    for op in plan["ops"]:
        dest = vault_root / op["to"]
        if op["op"] in ("move", "create") and (dest.exists() or dest.is_symlink()):
            raise Refused(f"{op['to']} already exists. Nothing written.")
        if op["from"]:
            src = vault_root / op["from"]
            if not src.is_file() or sha(src.read_text(encoding="utf-8")) != op["before_sha"]:
                raise Refused(f"{op['from']} changed since the plan was written. Nothing written.")
    journal.parent.mkdir(parents=True, exist_ok=True)
    with open(journal, "a", encoding="utf-8") as j:
        for i, op in enumerate(plan["ops"]):
            line = {"i": i, "op": op["op"], "from": op["from"], "to": op["to"],
                    "before_sha": op["before_sha"], "after_sha": sha(op["after"]),
                    "before": op["before"], "at": datetime.now(timezone.utc).isoformat()}
            j.write(json.dumps(line, ensure_ascii=False) + "\n")
            j.flush()
            os.fsync(j.fileno())
            dest = vault_root / op["to"]
            if op["op"] == "edit":
                _write_atomic(dest, op["after"], exclusive=False)
            else:
                _write_atomic(dest, op["after"], exclusive=True)
                if op["op"] == "move":
                    (vault_root / op["from"]).unlink()
            if sha(dest.read_text(encoding="utf-8")) != sha(op["after"]):
                raise Refused(f"{op['to']} does not read back as written; stopping here. The journal "
                              f"{journal} holds what landed, for --revert.")
            j.write(json.dumps({"i": i, "done": True}) + "\n")
            j.flush()
    return plan["count"]


def revert(journal: Path, vault_root: Path) -> int:
    """Undo a run, newest first. A note that no longer holds what the run wrote
    is left alone and named; the rest go back."""
    lines = [json.loads(l) for l in journal.read_text(encoding="utf-8").splitlines() if l.strip()]
    ops = {l["i"]: l for l in lines if "op" in l}
    done = {l["i"] for l in lines if l.get("done")}
    undone, left = 0, []
    for i in sorted(ops, reverse=True):
        op = ops[i]
        dest = vault_root / op["to"]
        if i not in done and not dest.exists():
            continue
        if not dest.is_file() or sha(dest.read_text(encoding="utf-8")) != op["after_sha"]:
            left.append(op["to"])
            continue
        if op["op"] == "create":
            dest.unlink()
        elif op["op"] == "edit":
            _write_atomic(dest, op["before"], exclusive=False)
        else:
            src = vault_root / op["from"]
            if src.exists():
                left.append(op["from"])
                continue
            _write_atomic(src, op["before"], exclusive=True)
            dest.unlink()
        undone += 1
    if left:
        raise Refused(f"reverted {undone}; left alone because they changed since the run: {', '.join(left)}")
    return undone


# ── the command ───────────────────────────────────────────────────────────────

def _roots(vault_arg: "str | None", memory_arg: "str | None") -> "tuple[Path, Path]":
    if vault_arg and memory_arg:
        return Path(vault_arg), Path(memory_arg)
    import harness_memory  # noqa: E402 — resolved at run time, never cached
    vault = Path(vault_arg) if vault_arg else harness_memory.vault_path()
    mem = Path(memory_arg) if memory_arg else harness_memory.memory_root()
    if vault is None or mem is None:
        raise Refused("no vault resolves; pass --vault and --memory-root")
    return vault, mem


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description="the one-time move of the ideas into personal/ideas/")
    ap.add_argument("--vault", help="vault root (default: the configured one)")
    ap.add_argument("--memory-root", help="memory root (default: the configured one)")
    ap.add_argument("--mapping", help="the operator's corrected mapping.md")
    ap.add_argument("--out", help="where the plan, the deletion list and the journal go")
    ap.add_argument("--apply", action="store_true", help="apply a recorded plan")
    ap.add_argument("--plan", help="the plan file the dry run recorded")
    ap.add_argument("--confirm-count", type=int, help="the plan's write count, typed by the operator")
    ap.add_argument("--revert", metavar="JOURNAL", help="undo a run from its journal")
    a = ap.parse_args(argv)
    try:
        if a.revert:
            journal = Path(a.revert)
            plan = json.loads((journal.parent / "plan.json").read_text(encoding="utf-8"))
            n = revert(journal, Path(plan["vault"]))
            print(f"reverted {n} write(s) from {journal}")
            return 0
        if a.apply:
            if not a.plan or a.confirm_count is None:
                raise Refused("--apply needs --plan and --confirm-count")
            plan_path = Path(a.plan)
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            n = apply(plan, a.confirm_count, plan_path.parent / "journal.jsonl")
            print(f"applied {n} write(s); journal at {plan_path.parent / 'journal.jsonl'}")
            return 0
        if not a.mapping:
            raise Refused("pass --mapping, the operator's corrected mapping.md")
        vault, mem = _roots(a.vault, a.memory_root)
        plan = build_plan(vault, mem, Path(a.mapping).read_text(encoding="utf-8"))
        sys.stdout.write(summary(plan))
        if a.out:
            out = Path(a.out)
            out.mkdir(parents=True, exist_ok=True)
            (out / "plan.json").write_text(json.dumps(plan, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
            (out / "deletions.txt").write_text(
                "# the deletion list, memory-root relative — for purge.py select --paths-file\n"
                + "".join(f"{r}\n" for r in plan["deletions"]), encoding="utf-8")
            print(f"plan at {out / 'plan.json'}; to apply: --apply --plan {out / 'plan.json'} "
                  f"--confirm-count {plan['count']}")
        return 0
    except Refused as e:
        print(f"idea cards: refused — {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
