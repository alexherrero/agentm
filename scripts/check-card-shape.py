#!/usr/bin/env python3
"""Gate: every note in the class directories has the card's shape, and every
idea card in `personal/ideas/` has the idea card's.

agentm-vault § The card fixes what a memory holds and the order Obsidian's
properties panel shows it in; the card backfill (agentm-vault plan 06) brought
the corpus to it, and every writer of a class card now emits it. This gate reads
the live vault, resolved at runtime, over the class directories other than
`mocs/` (generated pages; agentm-vault plan 07 owns their shape).

  a card (`type:`)    carries `title`, `status`, `lifecycle`,
                      `filing_confidence`, `source`, `trust`, `created` and
                      `updated`. A card no pass has judged yet (no
                      `enriched_at`) may wait for the deep pass to write its
                      title, and the daemon's self-probe names no transport.
                      Its fields are in the card's order; it carries no retired
                      field; `trust` is the contract's tier for its `source`;
                      `filing_confidence` is `high` exactly where `confidence`
                      is at or above the contract's floor; it is not `active` at
                      `low`; `lifecycle` is on the contract's axis (`pinned`
                      included); `slug` equals the file name; and the file name
                      is a name, not a counter (the naming rule).
  a record (`kind:`)  keeps the card's order for the fields it shares, carries
                      no retired field, never carries `importance`, `why`,
                      `filing_confidence` or `trust`, and names what a trace
                      touched as `touched:`.

The idea cards are a second walk with a shape of their own (agentm-vault plan
13, the operator's rulings of 2026-09-20). Every markdown file directly in the
vault root's `personal/ideas/`, no dotfile and nothing in a subfolder, is one:

  an idea card        is `type: idea` and carries an `area:`, the operator's
                      group name (an open set: any value is theirs). A
                      `status`, when it carries one, is `active`; a card with
                      none is read as active and the night writes it. A
                      `dismissed:`, when it carries one, is a YYYY-MM-DD day.
                      It never carries `lifecycle` or `lifecycle_since`. Its
                      fields are in the card's order, and it carries no
                      retired field.

The class card's rules stop there on purpose. Its required set names
`lifecycle`, which an idea card never carries, and it holds `filing_confidence`
to the contract's floor, while the night keeps an idea's filing whatever the
score (ruling 6). A card the operator makes by hand needs only `type: idea` and
an `area:`, so nothing else is required of one.

Two states for each walk. Until the applied card backfill writes
`memory/.card-backfill-complete` the class walk reports how many findings the
backfill will clear and exits 0: a gate that failed every build until the
operator's deploy ran would gate nothing. Once the marker exists, every finding
fails. The idea walk has its own marker, `memory/.ideas-surface-complete`, which
the ideas move writes from `scripts/migrate/ideas_migration.py --finish` once
every card reads clean. Until it exists the walk lists each finding and exits 0;
once it exists, every finding fails.

Usage:
  python3 scripts/check-card-shape.py                 # the resolved memory root
  python3 scripts/check-card-shape.py --memory-root DIR
Exit: 0 clean, before a walk's marker exists, or nothing to check; 1 on a finding.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TOOLKIT = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for _p in (str(_HERE), str(_TOOLKIT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import card_shape as cs  # noqa: E402
import idea_cards as ic  # noqa: E402

IGNORABLE = {".DS_Store", "Icon\r", "Icon", ".gitkeep", cs.MARKER_NAME}
IDEAS_REL = "/".join(ic.IDEAS_DIR_REL)


class Contract:
    """What the gate reads from the filing contract."""

    def __init__(self, sources: dict, floor: float, lifecycles):
        self.sources = dict(sources)
        self.floor = float(floor)
        self.lifecycles = set(lifecycles)


def load_contract() -> Contract | None:
    try:
        import storage_rules  # noqa: E402
        rules = storage_rules.load()
    except Exception:  # the daemon did not answer; the contract checks are skipped, loudly
        return None
    return Contract(rules.sources(), rules.thresholds().get("low_confidence", 0.65), rules.lifecycles())


def note_findings(rel: str, text: str, contract: Contract | None) -> list[str]:
    parsed = cs.split_note(text)
    if parsed is None:
        return [f"{rel}: no frontmatter block"]
    entries, _rest = parsed
    keys = [k for k, _ in entries if k]

    def get(key):
        value = cs.scalar(cs.raw_value(entries, key))
        return value if value not in ("",) else None

    out = [f"{rel}: {f}" for f in cs.order_findings(keys)]
    retired = [k for k in keys if k in cs.RETIRED_FIELDS]
    if retired:
        out.append(f"{rel}: retired field(s) {retired}")
    stem = Path(rel).stem
    if get("slug") is not None and get("slug") != stem:
        out.append(f"{rel}: `slug: {get('slug')}` is not the file name")

    if "type" in keys:
        if "kind" in keys:
            out.append(f"{rel}: carries both `type` and `kind`")
        judged = get("enriched_at") is not None
        probe = cs.raw_value(entries, cs.PROBE_KEY) is not None
        for name in cs.REQUIRED_CARD_FIELDS:
            if get(name) is not None:
                continue
            if name == "title" and not judged:
                continue  # waits for the deep pass, which writes it
            if name in ("source", "trust") and probe:
                continue  # synthetic; names no transport
            out.append(f"{rel}: missing `{name}`")
        if cs.is_counter_slug(stem, get("title")):
            out.append(f"{rel}: the name `{stem}` ends in a counter (the naming rule)")
        tier = get("filing_confidence")
        if get("status") == "active" and tier == "low":
            out.append(f"{rel}: `status: active` at `filing_confidence: low`")
        if contract is not None:
            source, trust = get("source"), get("trust")
            if source in contract.sources and trust is not None and trust != contract.sources[source]:
                out.append(f"{rel}: `trust: {trust}` but the contract gives `{source}` "
                           f"`{contract.sources[source]}`")
            try:
                confidence = float(get("confidence")) if get("confidence") is not None else None
            except ValueError:
                confidence = None
            if confidence is not None and tier is not None:
                want = "high" if confidence >= contract.floor else "low"
                if tier != want:
                    out.append(f"{rel}: `filing_confidence: {tier}` at `confidence: {confidence}` "
                               f"(the floor is {contract.floor})")
            lifecycle = get("lifecycle")
            if lifecycle is not None and contract.lifecycles and lifecycle not in contract.lifecycles:
                out.append(f"{rel}: `lifecycle: {lifecycle}` is not on the contract's axis")
    elif "kind" in keys:
        never = [k for k in keys if k in cs.RECORD_NEVER]
        if never:
            out.append(f"{rel}: a record carries {never}")
        renamed = [k for k in keys if k in cs.RENAMED_RECORD_FIELDS]
        if renamed:
            out.append(f"{rel}: {renamed} is named {[cs.RENAMED_RECORD_FIELDS[k] for k in renamed]} now")
    else:
        out.append(f"{rel}: carries neither `type` nor `kind`")
    return out


def notes(memory_root: Path):
    for cls in cs.SCOPE_CLASSES:
        d = memory_root / "memory" / cls
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.name in IGNORABLE or p.is_dir() or p.suffix != ".md":
                continue
            yield p


def idea_findings(rel: str, text: str) -> list[str]:
    """What keeps one note in `personal/ideas/` from being an idea card, per
    field; empty when it is one."""
    parsed = cs.split_note(text)
    if parsed is None:
        return [f"{rel}: no frontmatter block"]
    entries, _rest = parsed
    keys = [k for k, _ in entries if k]

    def get(key):
        value = cs.scalar(cs.raw_value(entries, key))
        return value if value not in ("",) else None

    out = [f"{rel}: {f}" for f in cs.order_findings(keys)]
    retired = [k for k in keys if k in cs.RETIRED_FIELDS]
    if retired:
        out.append(f"{rel}: retired field(s) {retired}")
    card_type = get("type")
    if card_type != ic.IDEA_TYPE:
        if card_type is not None:
            what = f"`type: {card_type}`"
        elif get("kind") is not None:
            what = f"a record (`kind: {get('kind')}`)"
        else:
            what = "no `type`"
        out.append(f"{rel}: {what}; {IDEAS_REL}/ holds only `type: {ic.IDEA_TYPE}`")
        return out
    out += [f"{rel}: missing `{name}`" for name in ic.REQUIRED_FIELDS if get(name) is None]
    status = get("status")
    if status is not None and status != ic.IDEA_STATUS:
        out.append(f"{rel}: `status: {status}`; an idea card is `{ic.IDEA_STATUS}` whatever the score")
    out += [f"{rel}: carries `{name}`; an idea card has no aging axis"
            for name in ic.DROPPED_KEYS if name in keys]
    dismissed = get("dismissed")
    if dismissed is not None and not ic.DAY_RE.match(dismissed):
        out.append(f"{rel}: `dismissed: {dismissed}` is not a YYYY-MM-DD day")
    return out


def ideas_walk(memory_root: Path) -> tuple[list[str], int]:
    """Every finding over the idea cards, named from the vault root, and how many
    cards were read. `scripts/migrate/ideas_migration.py --finish` reads this
    too, so the marker and the gate agree about what "in shape" means."""
    vault = ic.vault_root(memory_root)
    cards = ic.card_paths(memory_root)
    findings: list[str] = []
    for p in cards:
        findings += idea_findings(p.relative_to(vault).as_posix(),
                                  p.read_text(encoding="utf-8", errors="replace"))
    return findings, len(cards)


def check_ideas(memory_root: Path, out=sys.stdout) -> int:
    """The idea walk: listed until the ideas move's `--finish` writes its
    marker, enforced once it has."""
    if not ic.ideas_dir(memory_root).is_dir():
        print(f"check-card-shape: no {IDEAS_REL}/ at the vault root; no idea card to check", file=out)
        return 0
    findings, count = ideas_walk(memory_root)
    if not ic.marker_path(memory_root).exists():
        print(f"check-card-shape: idea cards report only — {len(findings)} finding(s) over {count} card(s) "
              f"in {IDEAS_REL}/; enforced once memory/{ic.MARKER_NAME} exists, which "
              "scripts/migrate/ideas_migration.py --finish writes once every card reads clean", file=out)
        for f in findings:
            print(f"  pending: {f}", file=out)
        return 0
    if findings:
        print(f"check-card-shape: {len(findings)} finding(s) over {count} idea card(s) in {IDEAS_REL}/", file=out)
        for f in findings:
            print(f"  {f}", file=out)
        return 1
    print(f"check-card-shape: clean — {count} idea card(s) in {IDEAS_REL}/ have the idea card's shape", file=out)
    return 0


def check(memory_root: Path, contract: Contract | None, out=sys.stdout) -> int:
    findings: list[str] = []
    count = 0
    for p in notes(memory_root):
        count += 1
        findings += note_findings(p.relative_to(memory_root).as_posix(),
                                  p.read_text(encoding="utf-8", errors="replace"), contract)
    if contract is None:
        print("check-card-shape: the filing contract did not load; the trust, floor and lifecycle "
              "checks are skipped", file=out)
    rc = 0
    if not (memory_root / "memory" / cs.MARKER_NAME).exists():
        print(f"check-card-shape: pre-backfill — {len(findings)} finding(s) over {count} notes, which "
              "scripts/migrate/card_backfill.py brings to shape (dry run first, --apply under quiesce); "
              f"enforced once memory/{cs.MARKER_NAME} exists", file=out)
    elif findings:
        print(f"check-card-shape: {len(findings)} finding(s) over {count} notes under {memory_root}", file=out)
        for f in findings:
            print(f"  {f}", file=out)
        rc = 1
    else:
        print(f"check-card-shape: clean — {count} notes have the card's shape", file=out)
    return max(rc, check_ideas(memory_root, out))


def resolve_memory_root(arg: str | None) -> Path | None:
    if arg:
        return Path(arg)
    import vault_layout  # noqa: E402
    root = vault_layout.env_memory_root()
    if root is None:
        try:
            import harness_memory as hm  # noqa: E402
            root = hm.memory_root()
        except ImportError:
            root = None
    return Path(root) if root else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--memory-root", default=None,
                    help="memory root to check (default: $MEMORY_ROOT, else the configured one)")
    args = ap.parse_args(argv)
    root = resolve_memory_root(args.memory_root)
    if root is None or not (root / "memory").is_dir():
        print("check-card-shape: no memory root resolves; nothing to check")
        return 0
    return check(root, load_contract())


if __name__ == "__main__":
    raise SystemExit(main())
