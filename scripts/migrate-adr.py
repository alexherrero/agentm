#!/usr/bin/env python3
"""migrate-adr — the deterministic ADR fold executor (AG Phase 2, design-doc §5/§9.3).

Retires ADR *records* into living designs without losing decision history or
leaving dangling references. Given an explicit **fold-map** (`{ADR stems → a
living design}`), it:

  1. **Rewrites inbound links.** Every wiki markdown link whose href resolves
     (by basename) to a folded ADR is repointed to the target design's stem —
     the bare-basename `(00NN-slug)` links `check-wiki` rule (h) gates **and**
     the un-gated path-form `(decisions/00NN-…)` / `(../decisions/00NN-…)` links
     rule (h) never sees.
  2. **Prunes the index + sidebars.** In the configured index files
     (`Decisions.md` + both `_Sidebar.md`), a list-item line whose link points at
     a folded ADR is removed outright (the record no longer exists), rather than
     href-rewritten — an index must not list a retired record.
  3. **Reports the un-gated prose refs.** Bare `ADR 00NN` / `[ADR 00NN]` prose
     mentions of a folded ADR are **reported, never auto-rewritten** — the
     semantic relabel ("ADR 0018 →the seam design") is a manual, reviewed edit
     (design-doc §7: never a blind find-replace). The end-state must ship zero
     silent dangling refs; this surfaces them for the fold author.
  4. **Retires the records.** Deletes the folded ADR files (move-and-retire — the
     decision *content* lives in the target design's body + amendment log, moved
     there by the fold author **before** this runs; this step only retires the
     husk after its links are repointed).
  5. **Self-asserts.** Runs `scripts/check-all.sh` as its own gate (the repo's
     deterministic-verification-first doctrine) unless `--no-check`.

Safety: **dry-run by default** — reports every planned change and writes/deletes
NOTHING until `--apply`. A record is never deleted before its inbound links are
repointed (both happen in the single `--apply` pass, links first).

Fold-map JSON (per-repo config; the ADR sets differ across repos, the engine does
not — vendor this file byte-identical, point it at a different map):

    {
      "decisions_dir": "wiki/decisions",
      "index_files": ["wiki/decisions/Decisions.md",
                      "wiki/decisions/_Sidebar.md", "wiki/_Sidebar.md"],
      "folds": [
        {"into_stem": "memory-storage-seam",
         "into": "wiki/designs/memory-storage-seam.md",
         "adrs": ["0009-on-host-state-mode-config", "0012-vault-write-protocol"]}
      ]
    }

`decisions_dir` / `index_files` default to the values above when omitted.

CLI:
    migrate-adr.py --fold-map MAP.json [--repo-root DIR] [--apply] [--no-check]

Exit codes:
    0  success (dry-run reported, or --apply applied + check-all green)
    1  error (missing target design / missing ADR / check-all failed /
       dangling refs remain after apply)
    2  usage error

Stdlib-only.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

_DEFAULT_DECISIONS_DIR = "wiki/decisions"
_DEFAULT_INDEX_FILES = [
    "wiki/decisions/Decisions.md",
    "wiki/decisions/_Sidebar.md",
    "wiki/_Sidebar.md",
]

LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
LIST_ITEM_RE = re.compile(r"^\s*[-*]\s")


@dataclass
class Plan:
    """What a (dry-)run would do — the report surface."""
    link_rewrites: list[tuple[str, int, str, str]] = field(default_factory=list)
    index_prunes: list[tuple[str, int, str]] = field(default_factory=list)
    prose_refs: list[tuple[str, int, str]] = field(default_factory=list)
    deletions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _href_basename(href: str) -> str:
    """Reduce a link href to the wiki basename it resolves to (drop dir, .md, #anchor)."""
    page = href.split("#", 1)[0]
    page = page.rsplit("/", 1)[-1]
    if page.endswith(".md"):
        page = page[:-3]
    return page


def _build_map(fold_map: dict, repo: Path, plan: Plan) -> tuple[dict[str, str], dict[str, str]]:
    """Return (stem→target_stem, adr_number→target_stem); record errors in `plan`."""
    decisions_dir = repo / fold_map.get("decisions_dir", _DEFAULT_DECISIONS_DIR)
    stem_to_target: dict[str, str] = {}
    num_to_target: dict[str, str] = {}
    for fold in fold_map.get("folds", []):
        into = repo / fold["into"]
        into_stem = fold["into_stem"]
        if not into.is_file():
            plan.errors.append(f"target design missing: {fold['into']}")
        for adr_stem in fold.get("adrs", []):
            adr_file = decisions_dir / f"{adr_stem}.md"
            if not adr_file.is_file():
                plan.errors.append(f"ADR to fold missing: {adr_file.relative_to(repo)}")
            stem_to_target[adr_stem] = into_stem
            m = re.match(r"(\d{3,4})", adr_stem)
            if m:
                num_to_target[m.group(1)] = into_stem
    return stem_to_target, num_to_target


def plan_fold(fold_map: dict, repo: Path) -> Plan:
    """Compute the full change plan (no writes)."""
    plan = Plan()
    stem_to_target, num_to_target = _build_map(fold_map, repo, plan)
    if not stem_to_target:
        plan.errors.append("fold-map declares no ADRs to fold")
        return plan

    index_files = {
        (repo / p).resolve()
        for p in fold_map.get("index_files", _DEFAULT_INDEX_FILES)
    }
    decisions_dir = repo / fold_map.get("decisions_dir", _DEFAULT_DECISIONS_DIR)
    folded_files = {(decisions_dir / f"{s}.md").resolve() for s in stem_to_target}

    prose_num_re = re.compile(
        r"ADR\s+(" + "|".join(re.escape(n) for n in num_to_target) + r")\b"
    ) if num_to_target else None

    wiki_root = repo / "wiki"
    for md in sorted(wiki_root.rglob("*.md")):
        # Skip the ADR files being deleted themselves (no point rewriting husks).
        if md.resolve() in folded_files:
            continue
        rel = md.relative_to(repo).as_posix()
        is_index = md.resolve() in index_files
        text = md.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            # index/sidebar list item linking to a folded ADR → prune the line
            if is_index and LIST_ITEM_RE.match(line):
                m = LINK_RE.search(line)
                if m and _href_basename(m.group(2)) in stem_to_target:
                    plan.index_prunes.append((rel, i, line.strip()))
                    continue
            # inline link href to a folded ADR → rewrite to target stem
            for m in LINK_RE.finditer(line):
                base = _href_basename(m.group(2))
                if base in stem_to_target:
                    plan.link_rewrites.append(
                        (rel, i, m.group(2), stem_to_target[base])
                    )
            # prose ADR-number mention (not necessarily a link) → report only
            if prose_num_re:
                for pm in prose_num_re.finditer(line):
                    plan.prose_refs.append((rel, i, line.strip()))

    for stem in sorted(stem_to_target):
        f = decisions_dir / f"{stem}.md"
        if f.is_file():
            plan.deletions.append(f.relative_to(repo).as_posix())
    return plan


def apply_fold(fold_map: dict, repo: Path, plan: Plan) -> None:
    """Execute the plan: rewrite links + prune index lines, then delete ADR files."""
    stem_to_target, _ = _build_map(fold_map, repo, Plan())
    index_files = {
        (repo / p).resolve()
        for p in fold_map.get("index_files", _DEFAULT_INDEX_FILES)
    }
    decisions_dir = repo / fold_map.get("decisions_dir", _DEFAULT_DECISIONS_DIR)
    folded_files = {(decisions_dir / f"{s}.md").resolve() for s in stem_to_target}

    wiki_root = repo / "wiki"
    for md in sorted(wiki_root.rglob("*.md")):
        if md.resolve() in folded_files:
            continue
        is_index = md.resolve() in index_files
        text = md.read_text(encoding="utf-8", errors="replace")
        out_lines: list[str] = []
        changed = False
        for line in text.splitlines():
            if is_index and LIST_ITEM_RE.match(line):
                m = LINK_RE.search(line)
                if m and _href_basename(m.group(2)) in stem_to_target:
                    changed = True
                    continue  # prune the line
            def _sub(m: re.Match) -> str:
                base = _href_basename(m.group(2))
                if base in stem_to_target:
                    return f"[{m.group(1)}]({stem_to_target[base]})"
                return m.group(0)
            new_line = LINK_RE.sub(_sub, line)
            if new_line != line:
                changed = True
            out_lines.append(new_line)
        if changed:
            md.write_text("\n".join(out_lines) + ("\n" if text.endswith("\n") else ""),
                          encoding="utf-8")

    # Retire the records (links already repointed above).
    for stem in stem_to_target:
        f = decisions_dir / f"{stem}.md"
        if f.is_file():
            f.unlink()


def _print_plan(plan: Plan, *, applied: bool) -> None:
    tag = "APPLIED" if applied else "DRY-RUN (no changes written)"
    print(f"=== migrate-adr {tag} ===")
    print(f"\nlink rewrites ({len(plan.link_rewrites)}):")
    for rel, ln, old, new in plan.link_rewrites:
        print(f"  {rel}:{ln}  ({old}) -> ({new})")
    print(f"\nindex/sidebar prunes ({len(plan.index_prunes)}):")
    for rel, ln, content in plan.index_prunes:
        print(f"  {rel}:{ln}  - {content}")
    print(f"\nADR files retired ({len(plan.deletions)}):")
    for d in plan.deletions:
        print(f"  {d}")
    print(f"\nUN-GATED prose ADR mentions to reconcile MANUALLY "
          f"({len(plan.prose_refs)}) — not auto-rewritten:")
    for rel, ln, content in plan.prose_refs:
        snippet = content[:100] + ("…" if len(content) > 100 else "")
        print(f"  {rel}:{ln}  {snippet}")
    if plan.errors:
        print(f"\nERRORS ({len(plan.errors)}):")
        for e in plan.errors:
            print(f"  {e}")


def _run_check_all(repo: Path) -> int:
    print("\n=== running scripts/check-all.sh (self-assertion) ===")
    r = subprocess.run(["bash", "scripts/check-all.sh"], cwd=str(repo))
    return r.returncode


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="migrate-adr.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fold-map", required=True, type=Path, help="path to the fold-map JSON")
    ap.add_argument("--repo-root", type=Path, default=None,
                    help="repo root (default: parent of scripts/)")
    ap.add_argument("--apply", action="store_true",
                    help="write the changes (default: dry-run, no writes)")
    ap.add_argument("--no-check", action="store_true",
                    help="skip the check-all.sh self-assertion after --apply")
    try:
        args = ap.parse_args(argv[1:])
    except SystemExit:
        return 2

    repo = (args.repo_root or Path(__file__).resolve().parent.parent).resolve()
    if not args.fold_map.is_file():
        print(f"migrate-adr: fold-map not found: {args.fold_map}", file=sys.stderr)
        return 2
    try:
        fold_map = json.loads(args.fold_map.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        print(f"migrate-adr: bad fold-map JSON: {e}", file=sys.stderr)
        return 2

    plan = plan_fold(fold_map, repo)
    if plan.errors:
        _print_plan(plan, applied=False)
        print("\nmigrate-adr: refusing to proceed — fix the errors above.", file=sys.stderr)
        return 1

    if not args.apply:
        _print_plan(plan, applied=False)
        print("\n(dry-run — re-run with --apply to write. Author the target "
              "design's body + amendment log FIRST.)")
        return 0

    apply_fold(fold_map, repo, plan)
    _print_plan(plan, applied=True)

    if not args.no_check:
        rc = _run_check_all(repo)
        if rc != 0:
            print("\nmigrate-adr: check-all.sh FAILED after apply — review above.",
                  file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
