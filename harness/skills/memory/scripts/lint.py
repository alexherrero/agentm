#!/usr/bin/env python3
"""lint.py — the weekly + on-demand lint engine (auto-organization part 3,
task 7).

Composes three existing signals into one report and one auto-repair pass:

  - `vault_lint.py`'s structural checks (frontmatter, wikilinks, supersede
    integrity, including the new supersede-cycle / supersede-fork /
    dangling-supersession contradiction checks this task adds there) —
    the largest, longest-standing check suite in this skill.
  - `graph_snapshot.orphans()` (part 2) — notes with zero links in either
    direction. Already feeds `dream.py`'s own link-improvement backfill
    pool directly; this module just reports the same signal for
    visibility, it doesn't create a second feed.
  - A new per-note quality score (frontmatter completeness + link
    presence + `lifecycle.compute_decay_score`'s staleness axis),
    weighted-averaged and rolled up vault-wide.

The one new BEHAVIOR this module adds on top of those existing signals: a
mis-cased wikilink (`[[Wrong-Case]]` where `[[Correct-Case]]` is the only
vault-wide case-insensitive match, no alias, no anchor — the plan's own
"anything unsafe is surfaced instead" keeps the auto-repair scope to the
simple, unambiguous case) gets AUTO-CORRECTED, never just reported. A
genuinely broken link (no case-insensitive match, or an ambiguous one) is
left exactly as `vault_lint.check_wikilinks` already reports it — an
error finding, never auto-resolved.

Read-only by itself: `run_lint()` never writes anything. Repairs come
back as `(entry, old_raw, new_raw)` triples — proposed content, not yet
applied — so both callers (this module's own CLI `main()` and `dream.py`'s
`_stage_lint()`) can route them through the identical revert-logged
apply path every other auto-apply stage uses, rather than this module
writing directly.

Public surface:

    run_lint(vault_path, *, now=None) -> LintReport
        The full scan: findings (vault_lint's checks, mis-cased wikilink
        errors replaced with an info-level "auto-corrected" note),
        repairs (proposed mutations), orphans, and per-note quality
        scores + the vault-wide mean.

CLI: `python3 lint.py [--vault-path PATH] [--apply]`. `--apply` writes the
repairs directly (CLI-only convenience, NOT how the weekly cycle applies
them — see `dream.py::_stage_lint`, which routes through
`revert_log.record_and_apply` like every other auto-apply stage).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import vault_lint  # noqa: E402  (same skill dir — the structural check suite)

__all__ = ["LintReport", "run_lint", "main"]

# Contradiction check_ids (vault_lint.py) rolled into this module's
# "contradiction count" summary — the signal task 7's digest/meter
# reporting cares about, distinct from ordinary schema findings.
_CONTRADICTION_CHECK_IDS = frozenset(
    {"supersede-cycle", "supersede-fork", "dangling-supersession"}
)

# completeness: frontmatter_validator.validate() violation count, penalized
# per violation. linked: 0 if the note is an orphan, else 1 — a binary
# signal, not a graded one (a note either has >=1 real link or it doesn't).
# freshness: lifecycle.compute_decay_score's own (0.0, 1.0] staleness axis.
# Weights sum to 1.0; no single axis dominates the composite.
_QUALITY_WEIGHTS = {"completeness": 0.4, "linked": 0.3, "freshness": 0.3}
_COMPLETENESS_PENALTY_PER_VIOLATION = 0.25


@dataclass
class LintReport:
    model: object              # vault_lint.VaultModel
    findings: list             # vault_lint.Finding — mis-cased wikilinks replaced with info notes
    repairs: list              # [(vault_lint.Entry, old_raw, new_raw), ...] — proposed, not applied
    orphans: list              # vault-relative path strings (graph_snapshot.orphans())
    quality_scores: dict = field(default_factory=dict)  # rel -> float in [0.0, 1.0]
    mean_quality_score: float = 1.0

    @property
    def contradiction_count(self) -> int:
        return sum(1 for f in self.findings if f.check_id in _CONTRADICTION_CHECK_IDS)


def _case_insensitive_match(t: str, model) -> "str | None":
    """The unique case-insensitive match for `t` among vault_lint's
    link_stems/link_paths, or None if zero or more than one candidate —
    an ambiguous case match is never auto-repaired, it stays a genuinely
    broken link (the plan's own "unsafe" bucket)."""
    lowered = t.lower()
    pool = model.link_paths if "/" in t else model.link_stems
    candidates = {c for c in pool if c.lower() == lowered}
    return next(iter(candidates)) if len(candidates) == 1 else None


def _find_miscased_wikilinks(model) -> list:
    """[(entry, wrong_target, correct_target, start, end), ...] — one
    entry per REPAIRABLE occurrence, with the exact raw-file character
    span of the bracket pair so `_build_repairs` can replace that span
    precisely — never a blind whole-file string `.replace()`, which
    could also silently rewrite an unrelated identical-looking
    occurrence elsewhere in the same file (e.g. the same wrong-cased
    target shown as a documentation example).

    Scans the RAW file content (frontmatter + body), not just
    `entry.body` — a wikilink never legitimately appears in frontmatter,
    so this produces identical matches for real links while giving the
    replacement step file-accurate offsets directly, with no body-to-raw
    offset translation needed.

    Skips a wikilink carrying an alias (`|`) or an anchor (`#`/`^`):
    rewriting just the file-target portion while preserving the rest
    byte-for-byte adds risk for little payoff, so those stay in
    `vault_lint.check_wikilinks`'s ordinary unresolved-link bucket
    instead of being auto-repaired. Also skips any match inside a
    fenced code block (reusing `write_time_linker._fenced_ranges` /
    `_in_any_range` — the identical fence-exclusion this skill's
    link-improvement sweep already relies on, adversarial review found
    missing here): a wikilink shown as a worked example in a fence is
    documentation, not a real link, and must never be auto-rewritten."""
    import write_time_linker  # noqa: E402  (lazy — same skill dir)

    found = []
    for entry in model.entries:
        try:
            raw = entry.path.read_text(encoding="utf-8")
        except OSError:
            continue
        fenced = write_time_linker._fenced_ranges(raw)
        for m in vault_lint._WIKILINK_RE.finditer(raw):
            if write_time_linker._in_any_range(m.start(), fenced):
                continue
            full = m.group(1)
            if "|" in full or "#" in full or "^" in full:
                continue
            target = full.strip()
            if not target or vault_lint._wikilink_resolves(target, model):
                continue
            t = target[:-3] if target.endswith(".md") else target
            correct = _case_insensitive_match(t, model)
            if correct is None or correct == t:
                continue
            found.append((entry, target, correct, m.start(), m.end()))
    return found


def _build_repairs(miscased: list) -> list:
    """Groups `_find_miscased_wikilinks`' flat list by entry and returns
    `[(entry, old_raw, new_raw), ...]` — the actual mutation payload.
    Still just PROPOSED here; nothing is written by this function.

    Applies each occurrence's exact span (never a whole-file string
    replace), processing spans in descending start-offset order per
    entry so earlier offsets stay valid as later (rightward) edits are
    spliced in first."""
    by_rel: dict = {}
    for entry, _wrong, correct, start, end in miscased:
        spans = by_rel.setdefault(entry.rel, (entry, []))[1]
        spans.append((start, end, f"[[{correct}]]"))

    repairs = []
    for entry, spans in by_rel.values():
        try:
            raw = entry.path.read_text(encoding="utf-8")
        except OSError:
            continue
        new_raw = raw
        for start, end, replacement in sorted(spans, key=lambda s: s[0], reverse=True):
            new_raw = new_raw[:start] + replacement + new_raw[end:]
        if new_raw != raw:
            repairs.append((entry, raw, new_raw))
    return repairs


def _quality_score(vault_path: Path, entry, orphan_rels: set, *, now=None) -> float:
    import frontmatter_validator  # noqa: E402  (lazy — same skill dir)
    import lifecycle  # noqa: E402

    violations = frontmatter_validator.validate(entry.path)
    completeness = max(0.0, 1.0 - _COMPLETENESS_PENALTY_PER_VIOLATION * len(violations))

    linked = 0.0 if entry.rel in orphan_rels else 1.0

    try:
        freshness = lifecycle.compute_decay_score(
            vault_path, entry.frontmatter.get("slug") or entry.path.stem,
            entry.frontmatter, entry.rel, now=now,
        )
    except Exception:
        freshness = 1.0  # best-effort — a scoring failure never blocks the report

    return (
        _QUALITY_WEIGHTS["completeness"] * completeness
        + _QUALITY_WEIGHTS["linked"] * linked
        + _QUALITY_WEIGHTS["freshness"] * freshness
    )


def run_lint(vault_path: "Path | str", *, now: "str | None" = None) -> LintReport:
    """The full read-only scan. Never writes — `repairs` are proposed
    mutations a caller applies (or doesn't)."""
    vault_path = Path(vault_path)
    import graph_snapshot  # noqa: E402  (lazy — same skill dir)

    graph_snapshot.rebuild(vault_path)
    model = vault_lint.build_model(vault_path)

    miscased = _find_miscased_wikilinks(model)

    # Count-based suppression, not a boolean "any match" check (adversarial
    # review): `vault_lint.check_wikilinks` renders one Finding PER
    # occurrence, and two occurrences of the SAME target (e.g. a bare
    # `[[Foo]]` that got repaired plus an aliased `[[Foo|other]]` that
    # deliberately never does — see `_find_miscased_wikilinks`'s own
    # alias-skip) produce byte-identical messages, since the alias is
    # stripped before rendering. Dropping "any" matching finding would
    # silently discard the aliased one too, even though it's genuinely
    # still broken on disk. Only drop as many findings per (entry, wrong
    # target) as there are ACTUAL repaired occurrences.
    repaired_counts: dict = {}
    for entry, wrong, _correct, _start, _end in miscased:
        key = (entry.rel, wrong)
        repaired_counts[key] = repaired_counts.get(key, 0) + 1

    raw_findings = vault_lint.lint_model(model)
    findings = []
    for f in raw_findings:
        if f.check_id == "wikilink-resolution":
            matched_key = next(
                (key for key in repaired_counts
                 if key[0] == f.entry_path and f"[[{key[1]}]]" in f.message
                 and repaired_counts[key] > 0),
                None,
            )
            if matched_key is not None:
                repaired_counts[matched_key] -= 1
                continue  # superseded by the auto-corrected info note below
        findings.append(f)

    seen_info: set = set()
    for entry, wrong, correct, _start, _end in miscased:
        info_key = (entry.rel, wrong, correct)
        if info_key in seen_info:
            continue  # one info note per (entry, wrong, correct), not per occurrence
        seen_info.add(info_key)
        findings.append(vault_lint.Finding(
            "wikilink-resolution", "info", entry.rel,
            f"wikilink `[[{wrong}]]` was mis-cased — auto-corrected to `[[{correct}]]`",
            "no action needed — already repaired and revert-logged",
        ))

    repairs = _build_repairs(miscased)
    orphans = graph_snapshot.orphans(vault_path)
    orphan_set = set(orphans)
    quality_scores = {
        entry.rel: _quality_score(vault_path, entry, orphan_set, now=now)
        for entry in model.entries
    }
    mean_score = (sum(quality_scores.values()) / len(quality_scores)) if quality_scores else 1.0

    return LintReport(
        model=model, findings=findings, repairs=repairs,
        orphans=orphans, quality_scores=quality_scores, mean_quality_score=mean_score,
    )


def _render_report(report: LintReport) -> str:
    errs = sum(1 for f in report.findings if f.severity == "error")
    warns = sum(1 for f in report.findings if f.severity == "warn")
    infos = sum(1 for f in report.findings if f.severity == "info")
    lines = [
        f"lint: {errs} error · {warns} warn · {infos} info across "
        f"{len(report.model.entries)} entries",
        f"orphans: {len(report.orphans)}",
        f"contradictions: {report.contradiction_count}",
        f"auto-repairs: {len(report.repairs)} note(s) with a mis-cased wikilink corrected",
        f"mean quality score: {report.mean_quality_score:.2f}",
    ]
    for f in report.findings:
        lines.append(f"  [{f.severity}] {f.check_id} {f.entry_path}: {f.message}")
    return "\n".join(lines) + "\n"


def main(argv: "list | None" = None) -> int:
    parser = argparse.ArgumentParser(description="Run the lint pass (structural checks + orphans + quality score).")
    parser.add_argument("--vault-path", help="MemoryVault root (overrides MEMORY_VAULT_PATH env var)")
    parser.add_argument(
        "--apply", action="store_true",
        help="write auto-repairable mis-cased-wikilink fixes directly (CLI-only convenience; "
             "the weekly cycle applies through the revert log instead — see dream.py::_stage_lint)",
    )
    args = parser.parse_args(argv)

    try:
        vault = vault_lint._resolve_vault(args.vault_path)
    except FileNotFoundError as e:
        print(f"[lint] {e}", file=sys.stderr)
        return 2

    report = run_lint(vault)
    print(_render_report(report), end="")

    if args.apply:
        for entry, _old_raw, new_raw in report.repairs:
            entry.path.write_text(new_raw, encoding="utf-8")
        if report.repairs:
            print(f"applied {len(report.repairs)} repair(s) directly (--apply, no revert log)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
