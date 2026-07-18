#!/usr/bin/env python3
"""migrate_arcs — the arc-as-metadata backfill (2026-07-18 convention).

Mirrors the retired `migrate-adr.py`'s shape: dry-run by default, prints a
reviewable mapping table, writes/moves nothing until `--apply`, and any row
without a confident signal is reported UNMATCHED rather than guessed at —
exactly like that script's un-gated prose-ADR-mention bucket.

Three independent passes, invoked separately:

  stamp          infer `arc:` for a project's permanent decisions/designs
                 entries from their existing tags, against
                 arc_registry.KNOWN_ARCS. Never moves a file — only adds a
                 frontmatter field.
  archive-group  propose grouping a project's flat `_harness/archive/
                 PLAN.archive.*.md` files into `archive/<arc>/` subfolders,
                 inferred from the filename slug.
  designs-move   propose moving one named closed arc's design folder from
                 `_harness/designs/<arc>/` to `_harness/archive/designs/<arc>/`,
                 plus a vault-wide sweep for markdown links/wikilinks
                 referencing the old path.

CLI:
    migrate_arcs.py stamp --project agentm [--vault PATH] [--apply]
    migrate_arcs.py archive-group --project agentm [--vault PATH] [--apply]
    migrate_arcs.py designs-move --project crickets --arc consolidation-review \\
        [--vault PATH] [--apply]

Exit codes: 0 success (dry-run reported, or --apply applied); 2 usage error.
Stdlib-only.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import save  # noqa: E402  (FRONTMATTER_FIELD_ORDER — same skill dir)
import arc_registry  # noqa: E402

_DATE_PREFIX_RE = re.compile(r"^\d{8}-?")
# Filename slugs that abbreviate a registered arc rather than spelling it out
# in full — checked before the registry prefix match, longest key first.
_ALIAS_PREFIXES: tuple[tuple[str, str], ...] = (
    ("ag-phase", "architecture-governance"),
)
_VERSION_TAG_RE = re.compile(r"^v(\d+)(-\d+)*$")


# -----------------------------------------------------------------------------
# Minimal frontmatter I/O (standalone-module convention in this scripts/ dir —
# each script keeps its own tiny parser rather than sharing a shared one).
# -----------------------------------------------------------------------------

def _read_frontmatter_lines(text: str) -> tuple[list[str], int, int] | None:
    """Return (raw frontmatter lines, block_start_idx, block_end_idx) over
    `text.splitlines()`, or None if there's no `---` fenced block."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], 0, i
    return None


def _parse_kv(fm_lines: list[str]) -> dict[str, str]:
    fm: dict[str, str] = {}
    for line in fm_lines:
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip()
    return fm


def _parse_tags(raw: str) -> list[str]:
    raw = raw.strip()
    if not raw or raw == "[]":
        return []
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    return [t.strip() for t in raw.split(",") if t.strip()]


def _insert_arc_line(text: str, arc: str) -> str:
    """Return `text` with `arc: <arc>` inserted into its frontmatter at the
    position FRONTMATTER_FIELD_ORDER dictates. Raises ValueError if `text` has
    no frontmatter block or already carries an `arc:` field."""
    lines = text.splitlines()
    block = _read_frontmatter_lines(text)
    if block is None:
        raise ValueError("no frontmatter block")
    fm_lines, _, end = block
    fm = _parse_kv(fm_lines)
    if "arc" in fm:
        raise ValueError("already has an arc: field")
    order = list(save.FRONTMATTER_FIELD_ORDER)
    arc_pos = order.index("arc")
    insert_at = len(fm_lines)  # default: end of frontmatter block
    for i, line in enumerate(fm_lines):
        key = line.partition(":")[0].strip()
        if key in order and order.index(key) > arc_pos:
            insert_at = i
            break
    new_fm_lines = fm_lines[:insert_at] + [f"arc: {arc}"] + fm_lines[insert_at:]
    new_lines = ["---"] + new_fm_lines + ["---"] + lines[end + 1:]
    out = "\n".join(new_lines)
    if text.endswith("\n"):
        out += "\n"
    return out


# -----------------------------------------------------------------------------
# Shared plan/report shape
# -----------------------------------------------------------------------------

@dataclass
class MappingRow:
    path: str
    signal: str          # what was matched on (tag / filename slug / …)
    proposed: str         # proposed arc or destination
    confidence: str       # HIGH | MEDIUM | UNMATCHED


@dataclass
class Plan:
    rows: list[MappingRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _print_plan(kind: str, plan: Plan, *, applied: bool) -> None:
    tag = "APPLIED" if applied else "DRY-RUN (nothing written/moved)"
    print(f"=== migrate_arcs {kind} — {tag} ===\n")
    high = [r for r in plan.rows if r.confidence == "HIGH"]
    med = [r for r in plan.rows if r.confidence == "MEDIUM"]
    unmatched = [r for r in plan.rows if r.confidence == "UNMATCHED"]
    already = [r for r in plan.rows if r.confidence == "SKIP"]
    for label, rows in (("HIGH confidence", high), ("MEDIUM confidence", med)):
        print(f"{label} ({len(rows)}):")
        for r in rows:
            print(f"  {r.path}\n    signal: {r.signal}  ->  {r.proposed}")
        print()
    print(f"UNMATCHED — needs operator decision, never auto-applied ({len(unmatched)}):")
    for r in unmatched:
        print(f"  {r.path}\n    signal: {r.signal}")
    print()
    if already:
        print(f"already stamped/moved, skipped ({len(already)}):")
        for r in already:
            print(f"  {r.path}")
        print()
    if plan.errors:
        print(f"ERRORS ({len(plan.errors)}):")
        for e in plan.errors:
            print(f"  {e}")


# -----------------------------------------------------------------------------
# Pass 1 — stamp (permanent decisions/designs entries)
# -----------------------------------------------------------------------------

def _infer_arc_from_tags(tags: list[str]) -> tuple[str, str, str] | None:
    """Return (arc, signal, confidence) or None. Checked in order:
    (1) a tag that IS a registered arc slug verbatim — HIGH;
    (2) a version-shaped tag (v6-1, v6-19, …) coarsened to its major v{N},
        when that coarse label is itself registered — MEDIUM."""
    for t in tags:
        if arc_registry.is_known(t):
            return t, f"tag `{t}` is a registered arc slug", "HIGH"
    for t in tags:
        m = _VERSION_TAG_RE.match(t)
        if m:
            coarse = f"v{m.group(1)}"
            if arc_registry.is_known(coarse):
                return coarse, f"tag `{t}` coarsened to `{coarse}`", "MEDIUM"
    return None


def plan_stamp(vault: Path, project: str) -> Plan:
    plan = Plan()
    proj_root = vault / "projects" / project
    for sub in ("decisions", "designs"):
        root = proj_root / sub
        if not root.is_dir():
            continue
        for md in sorted(root.rglob("*.md")):
            rel = md.relative_to(vault).as_posix()
            try:
                text = md.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                plan.errors.append(f"{rel}: unreadable ({e})")
                continue
            block = _read_frontmatter_lines(text)
            if block is None:
                continue
            fm = _parse_kv(block[0])
            if "arc" in fm:
                plan.rows.append(MappingRow(rel, "", fm["arc"], "SKIP"))
                continue
            tags = _parse_tags(fm.get("tags", ""))
            inferred = _infer_arc_from_tags(tags)
            if inferred is None:
                plan.rows.append(MappingRow(rel, f"tags={tags}", "", "UNMATCHED"))
                continue
            arc, signal, confidence = inferred
            plan.rows.append(MappingRow(rel, signal, arc, confidence))
    return plan


def apply_stamp(vault: Path, plan: Plan) -> None:
    for row in plan.rows:
        if row.confidence not in ("HIGH", "MEDIUM"):
            continue
        p = vault / row.path
        text = p.read_text(encoding="utf-8")
        p.write_text(_insert_arc_line(text, row.proposed), encoding="utf-8")


# -----------------------------------------------------------------------------
# Pass 2 — archive-group (flat _harness/archive/PLAN.archive.*.md files)
# -----------------------------------------------------------------------------

def _match_registry_prefix(slug: str) -> str | None:
    """Longest-matching registered arc slug that `slug` starts with (as a
    whole segment: equal, or followed by `-`), or an alias-prefix hit, or
    None."""
    candidates = sorted(arc_registry.known_arcs(), key=len, reverse=True)
    for arc in candidates:
        if slug == arc or slug.startswith(arc + "-"):
            return arc
    for prefix, arc in _ALIAS_PREFIXES:
        if slug.startswith(prefix):
            return arc
    return None


def plan_archive_group(vault: Path, project: str) -> Plan:
    plan = Plan()
    archive_root = vault / "projects" / project / "_harness" / "archive"
    if not archive_root.is_dir():
        plan.errors.append(f"no archive dir: {archive_root}")
        return plan
    for f in sorted(archive_root.iterdir()):
        if not f.is_file() or not f.name.startswith("PLAN.archive."):
            continue
        rel = f.relative_to(vault).as_posix()
        stem = f.name[len("PLAN.archive."):-len(".md")] if f.name.endswith(".md") else f.name[len("PLAN.archive."):]
        slug = _DATE_PREFIX_RE.sub("", stem).strip("-")
        if not slug:
            plan.rows.append(MappingRow(rel, f"stem={stem!r} (no slug after date)", "", "UNMATCHED"))
            continue
        arc = _match_registry_prefix(slug)
        if arc is None:
            plan.rows.append(MappingRow(rel, f"slug={slug!r}", "", "UNMATCHED"))
            continue
        dest = (archive_root / arc / f.name).relative_to(vault).as_posix()
        plan.rows.append(MappingRow(rel, f"slug={slug!r} -> arc `{arc}`", dest, "HIGH"))
    return plan


def apply_archive_group(vault: Path, plan: Plan) -> None:
    for row in plan.rows:
        if row.confidence not in ("HIGH", "MEDIUM"):
            continue
        src = vault / row.path
        dest = vault / row.proposed
        dest.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dest)


# -----------------------------------------------------------------------------
# Pass 3 — designs-move (one named closed arc's design folder)
# -----------------------------------------------------------------------------

def plan_designs_move(vault: Path, project: str, arc: str) -> Plan:
    plan = Plan()
    src = vault / "projects" / project / "_harness" / "designs" / arc
    dest = vault / "projects" / project / "_harness" / "archive" / "designs" / arc
    if not src.is_dir():
        plan.errors.append(f"no such design folder: {src.relative_to(vault)}")
        return plan
    if dest.exists():
        plan.errors.append(f"destination already exists: {dest.relative_to(vault)}")
        return plan
    plan.rows.append(MappingRow(
        src.relative_to(vault).as_posix(), "operator-named closed arc",
        dest.relative_to(vault).as_posix(), "HIGH",
    ))
    # Vault-wide link sweep: any markdown/wikilink reference to the old path.
    old_needle = f"_harness/designs/{arc}/"
    new_needle = f"_harness/archive/designs/{arc}/"
    for md in sorted(vault.rglob("*.md")):
        if any(p == "_archive" for p in md.parts):
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        count = text.count(old_needle)
        if count:
            rel = md.relative_to(vault).as_posix()
            plan.rows.append(MappingRow(
                rel, f"{count} reference(s) to `{old_needle}`", new_needle, "HIGH",
            ))
    return plan


def apply_designs_move(vault: Path, project: str, arc: str, plan: Plan) -> None:
    src = vault / "projects" / project / "_harness" / "designs" / arc
    dest = vault / "projects" / project / "_harness" / "archive" / "designs" / arc
    dest.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dest)
    old_needle = f"_harness/designs/{arc}/"
    new_needle = f"_harness/archive/designs/{arc}/"
    for row in plan.rows:
        if row.path == src.relative_to(vault).as_posix():
            continue  # the folder move itself, handled above
        md = vault / row.path
        if not md.is_file():
            continue
        text = md.read_text(encoding="utf-8")
        new_text = text.replace(old_needle, new_needle)
        if new_text != text:
            md.write_text(new_text, encoding="utf-8")


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _resolve_vault(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser()
    env = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if env:
        return Path(env).expanduser()
    raise FileNotFoundError("no vault path — set --vault or MEMORY_VAULT_PATH")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="migrate_arcs.py", description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    for name in ("stamp", "archive-group"):
        p = sub.add_parser(name)
        p.add_argument("--project", required=True)
        p.add_argument("--vault", default=None)
        p.add_argument("--apply", action="store_true")

    p = sub.add_parser("designs-move")
    p.add_argument("--project", required=True)
    p.add_argument("--arc", required=True)
    p.add_argument("--vault", default=None)
    p.add_argument("--apply", action="store_true")

    args = ap.parse_args(argv)
    try:
        vault = _resolve_vault(args.vault)
    except FileNotFoundError as e:
        print(f"migrate_arcs: {e}", file=sys.stderr)
        return 2
    if not vault.is_dir():
        print(f"migrate_arcs: vault not found: {vault}", file=sys.stderr)
        return 2

    if args.command == "stamp":
        plan = plan_stamp(vault, args.project)
        if args.apply:
            apply_stamp(vault, plan)
        _print_plan("stamp", plan, applied=args.apply)
    elif args.command == "archive-group":
        plan = plan_archive_group(vault, args.project)
        if args.apply:
            apply_archive_group(vault, plan)
        _print_plan("archive-group", plan, applied=args.apply)
    elif args.command == "designs-move":
        plan = plan_designs_move(vault, args.project, args.arc)
        if plan.errors:
            _print_plan("designs-move", plan, applied=False)
            return 1
        if args.apply:
            apply_designs_move(vault, args.project, args.arc, plan)
        _print_plan("designs-move", plan, applied=args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
