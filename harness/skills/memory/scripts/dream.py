#!/usr/bin/env python3
"""dream.py — the Python half of the night: what has no Go owner and a reader.

The third step of the night (agentm-vault § Dreaming, plan 04), after the
enrichment batch and the dreaming binary and before the scorecards. It reads,
it reports, and it proposes; it changes no note.

  - the filing contract, read first and fail-closed: a block that will not
    parse halts every proposal below, and the digest says why;
  - the corpus meters (connectivity, browse surface);
  - lint, as a report — its auto-repair lane retired with every other lane
    that applied anything;
  - **possible twins**: dedup at the shipped 0.92 similarity and contradiction
    triage (same key, different body), each a pair with its similarity and
    both titles;
  - **proposed facets**: a diary label recurring on three or more days;
  - the part-5 stages that still have a reader — the enrichment breaker's
    status, the backlink footers, the correction loop;
  - the needs-review map, regenerated with the twins and the facets as
    sections of their own.

The twins and the facets land in the needs-review map, where you act on them
in Obsidian by merging or by writing `superseded_by`. There is no staging
directory and no confirm step: 167 dedup proposals drew 11 confirmations in two
months, and a proposal you act on by hand is the same act with git as the undo.

Retired in plan 04, each with its reason and its last night's count in the
plan's progress: the lifecycle stage and the calendar rollups (the binary owns
both), tidying (it moved your kind-less documents), compression (it proposed
nothing on any run), the opinion supplement (its lanes retired), insight
generation and qualification (no surface read what they wrote), the sampled
audit (it audited a stage that makes no model call), entity rollups, stub
synthesis and the unfiled drain (the class folded; the drain is the batch),
and the confirm-and-revert path.

CLI: `python3 dream.py [--vault-path <memory root>] [--run-id <id>]`.
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Bootstrap this directory before any sibling import: a foreign loader
# (crickets' bridges file-path-load memory-skill modules) has none of it on
# sys.path, and a bare sibling import only ever worked when the hooks ran the
# file as a script. Pinned by scripts/test_skill_modules_file_loadable.py.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import engine_state  # noqa: E402
import storage_rules  # noqa: E402
from vault_lock import atomic_write  # noqa: E402

__all__ = [
    "run_dream",
    "Proposal",
    "DreamDigest",
    "REVIEW_PROPOSALS_NAME",
    "CYCLE_REPORT_NAME",
    "main",
]

# The shipped dedup threshold (agentm-vault § Dreaming keeps it at 0.92).
DEDUP_SIMILARITY_THRESHOLD = 0.92

# Where the cycle leaves what it found, under the engine state dir's
# `dreaming/` — beside the binary's own `last-report.json`. The needs-review
# map reads the first; the morning note reads the second.
REVIEW_PROPOSALS_NAME = "review-proposals.json"
CYCLE_REPORT_NAME = "python-cycle.json"

# Reserved top-level vault dirs a dream pass never reads as source entries —
# mirrors vault_lint.py's _EXCLUDE_DIRS (parity pinned by test_vault_lint.py)
# plus dreaming's own extras: `_dream` (a pass must not dream about its own
# prior output), `.obsidian` (editor config, not notes), and the retired
# opinion lanes and crystallize staging, which no stage may read as corpus.
_EXCLUDE_DIRS = frozenset(
    # Matched per path SEGMENT, so this holds the scratch space's last
    # component ("scratch"), not its "desk/scratch" spelling.
    {"_idea-incubator", "_meta", "_harness", "_inbox", "scratch", "_archive",
     "_dream", ".obsidian", "_opinions", "_crystallize-staging"}
)


# -----------------------------------------------------------------------------
# Result types
# -----------------------------------------------------------------------------

@dataclass
class Proposal:
    """One finding for you to judge. Nothing here is applied: a twin is merged
    by hand, a facet is registered by an edit to the contract.

    `stage` is `dedup`, `contradiction_triage` or `facet_promotion`; `detail`
    carries what the needs-review section renders (the similarity and both
    titles for a twin; the label, its days and a sample for a facet)."""

    stage: str
    kind: str
    paths: list
    summary: str
    detail: dict = field(default_factory=dict)


@dataclass
class DreamDigest:
    run_id: str
    corpus_stats: dict
    proposals: list
    digest_path: Optional[Path] = None
    # The needs-review reading, after the map regenerated with this cycle's
    # twins and facets. None when filing is halted.
    needs_review: Optional[dict] = None


# -----------------------------------------------------------------------------
# Corpus reading
# -----------------------------------------------------------------------------

def _parse_frontmatter(content: str) -> tuple[dict, str]:
    if not content.startswith("---\n"):
        return {}, content
    end = content.find("\n---\n", 4)
    if end == -1:
        return {}, content
    fm_text = content[4:end]
    body = content[end + 5:]
    fm: dict = {}
    for line in fm_text.split("\n"):
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        fm[key] = value
    return fm, body


_CRYSTALLIZED_PARTS = ("memory", "crystallized")
_SUPPLEMENT_KIND_LINE = "kind: opinion-supplement"


def _is_supplement_home(p: Path, rel_parts: tuple) -> bool:
    """A retired opinion lane under `memory/crystallized/`, or a served
    supplement beside the crystallized memories. The lanes retired, and any
    left on disk are nobody's corpus: no stage may call a standard a twin."""
    if tuple(rel_parts[:2]) != _CRYSTALLIZED_PARTS:
        return False
    if len(rel_parts) > 2:
        return True
    try:
        with p.open(encoding="utf-8") as fh:
            head = fh.read(1024)
    except OSError:
        return False
    return _SUPPLEMENT_KIND_LINE in head.split("\n---", 1)[0]


def _iter_entries(vault_path: Path) -> list:
    entries = []
    for p in sorted(vault_path.rglob("*.md")):
        rel_parts = p.relative_to(vault_path).parts[:-1]
        if any(part in _EXCLUDE_DIRS for part in rel_parts):
            continue
        if _is_supplement_home(p, rel_parts):
            continue
        entries.append(p)
    return entries


def _load(entries: list) -> dict:
    """path -> (frontmatter dict, body str, raw content str)."""
    loaded = {}
    for p in entries:
        raw = p.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(raw)
        loaded[p] = (fm, body, raw)
    return loaded


def _title(p: Path, fm: dict) -> str:
    return fm.get("title") or p.stem.replace("-", " ")


# -----------------------------------------------------------------------------
# The meters and the contract (deterministic, zero-token)
# -----------------------------------------------------------------------------

def _stage_corpus_stats(entries: list) -> dict:
    return {
        "entry_count": len(entries),
        "total_bytes": sum(p.stat().st_size for p in entries),
    }


def _stage_storage_rules(vault_path: Path, loaded: dict) -> dict:
    """Read the filing contract. Returns stats; never raises.

    `standards/storage-rules.md` is read at runtime rather than compiled in,
    which makes it the one file whose corruption would otherwise be silent: a
    model handed a malformed rule does not stop, it improvises. So the pass
    reads it first and fails closed — on a parse failure the returned dict
    carries `storage_rules_ok: False` and the failure text, and `run_dream`
    proposes nothing for the cycle.
    """
    try:
        rules = storage_rules.load()
    except storage_rules.StorageRulesError as exc:
        return {"storage_rules_ok": False, "storage_rules_error": str(exc)}

    watch = storage_rules.hash_watch(vault_path, current=rules.content_hash())

    # Two different populations, and conflating them would misreport both. A
    # memory whose `rules_hash` differs was judged under rules that have since
    # changed and is re-filing work. A memory with no `rules_hash` at all has
    # never been through a filing judgment — that is backlog, not staleness.
    stale = 0
    unjudged = 0
    for fm, _body, _raw in loaded.values():
        stamped = str(fm.get("rules_hash") or "").strip()
        if not stamped:
            unjudged += 1
        elif stamped != rules.content_hash():
            stale += 1

    return {
        "storage_rules_ok": True,
        "storage_rules_source": str(rules.source),
        "storage_rules_is_default": rules.is_packaged_default,
        "storage_rules_hash": rules.content_hash(),
        "storage_rules_hash_changed": watch["changed"],
        "storage_rules_previous_hash": watch["previous"],
        "storage_rules_stale_count": stale,
        "storage_rules_unjudged_count": unjudged,
    }


def _connectivity_meter(loaded: dict) -> dict:
    """The connectivity meter (auto-org part 2 task 7). Two numbers, counted
    independently so the linker cannot inflate its own success number:
    `organic_connectivity` (the share of notes with at least one real,
    non-generated link — detected by `graph.extract_edges` on the content with
    every `**Related:**` line stripped, so fenced and inline code never count)
    and `generated_link_count` (the wikilinks on those Related lines)."""
    import graph  # noqa: E402  (lazy)
    import markdown_spans  # noqa: E402

    total = 0
    organic = 0
    generated_links = 0
    for path, (_fm, _body, raw) in loaded.items():
        total += 1
        fenced = markdown_spans.fenced_ranges(raw)
        for m in markdown_spans.RELATED_LINE_RE.finditer(raw):
            if markdown_spans.in_any_range(m.start(), fenced):
                continue
            generated_links += len(markdown_spans.RELATED_WIKILINK_RE.findall(m.group(1)))
        stripped = markdown_spans.RELATED_LINE_RE.sub("", raw)
        if graph.extract_edges(str(path), stripped):
            organic += 1
    return {
        "organically_linked_count": organic,
        "organic_connectivity": (organic / total) if total else 0.0,
        "generated_link_count": generated_links,
    }


def _browse_surface_counts(vault_path: Path, entries: list) -> dict:
    """The three-state browse-surface meter (task 8): live / shelved /
    archived. Nothing shelves any more — tidying retired — but a `_shelf/`
    left on disk is still counted rather than read as live."""
    live = sum(1 for p in entries if "_shelf" not in p.parts)
    shelved = sum(1 for p in entries if "_shelf" in p.parts)
    archived = sum(
        1 for p in vault_path.rglob("*.md")
        if "_archive" in p.relative_to(vault_path).parts
    )
    return {
        "browse_live_count": live,
        "browse_shelved_count": shelved,
        "browse_archived_count": archived,
    }


# Contradiction check_ids (vault_lint.py, task 7) that count toward the lint
# report's own "contradiction" summary — distinct from ordinary schema and
# wikilink findings.
_LINT_CONTRADICTION_CHECK_IDS = frozenset(
    {"supersede-cycle", "supersede-fork", "dangling-supersession"}
)


def _stage_lint(vault_path: Path) -> dict:
    """The lint report. `lint.run_lint(vault_path)` is the identical engine
    `/memory lint` calls on demand; its findings are counted for the morning
    note and nothing is repaired — the mis-cased-wikilink repair was the lint
    engine's one auto-applied lane, and it retired with the rest. What it
    would repair is counted too, so the report still says so."""
    import lint as lint_module  # noqa: E402  (lazy)

    report = lint_module.run_lint(vault_path)
    return {
        "lint_orphan_count": len(report.orphans),
        "lint_contradiction_count": sum(
            1 for f in report.findings if f.check_id in _LINT_CONTRADICTION_CHECK_IDS
        ),
        "lint_mean_quality_score": report.mean_quality_score,
        "lint_graph_snapshot_mismatch_count": report.graph_snapshot_mismatch_count,
        "lint_repairable_count": len(report.repairs),
    }


# -----------------------------------------------------------------------------
# Possible twins and proposed facets
# -----------------------------------------------------------------------------

def _is_live(fm: dict) -> bool:
    """A note still in play: not a tombstone by `status`, not settled by the
    lifecycle axis (`superseded`, `archived`)."""
    status = str(fm.get("status") or "").strip().strip("'\"").lower()
    lifecycle = str(fm.get("lifecycle") or "").strip().strip("'\"").lower()
    return status not in ("superseded", "expired", "deleted") and lifecycle not in ("superseded", "archived")


def _stage_dedup(entries: list, loaded: dict, vault_path: Path) -> list:
    """Pairs whose bodies are at least 0.92 alike. Each note joins at most one
    pair, so a family of copies reads as a chain of pairs rather than every
    combination of them."""
    proposals = []
    matched = set()
    for i, a in enumerate(entries):
        if a in matched or not _is_live(loaded[a][0]):
            continue
        fm_a, body_a, _ = loaded[a]
        for b in entries[i + 1:]:
            if b in matched or not _is_live(loaded[b][0]):
                continue
            fm_b, body_b, _ = loaded[b]
            ratio = difflib.SequenceMatcher(None, body_a, body_b).ratio()
            if ratio < DEDUP_SIMILARITY_THRESHOLD:
                continue
            ra, rb = _rel(a, vault_path), _rel(b, vault_path)
            proposals.append(Proposal(
                stage="dedup", kind="possible-twin", paths=[ra, rb],
                summary=f"{a.name} and {b.name} are {ratio:.0%} alike — merge by hand, or "
                        "write `superseded_by` on the one that should go",
                detail={"similarity": round(ratio, 3), "a": ra, "b": rb,
                        "a_title": _title(a, fm_a), "b_title": _title(b, fm_b)},
            ))
            matched.add(b)
    return proposals


def _stage_contradiction_triage(entries: list, loaded: dict, vault_path: Path) -> list:
    """Notes that share a key and differ in body: two notes claiming to be the
    same memory and saying different things."""
    by_slug: dict = {}
    for p in entries:
        fm, _, _ = loaded[p]
        slug = fm.get("slug")
        if not slug or not _is_live(fm):
            continue
        by_slug.setdefault(slug, []).append(p)

    proposals = []
    for slug, paths in sorted(by_slug.items()):
        if len(paths) < 2:
            continue
        bodies = {p: loaded[p][1] for p in paths}
        if len(set(bodies.values())) < 2:
            continue  # identical bodies — dedup's job, not a contradiction
        rels = [_rel(p, vault_path) for p in paths]
        proposals.append(Proposal(
            stage="contradiction_triage", kind="same-key", paths=rels,
            summary=f"{len(paths)} notes share the key {slug!r} with different bodies",
            detail={"slug": slug, "titles": [_title(p, loaded[p][0]) for p in paths]},
        ))
    return proposals


def _stage_facet_promotion(vault_path, *, today=None, rules=None) -> list:
    """A diary label recurring on three or more distinct days is a standing
    facet the registry is missing. Proposed, never registered: the agent does
    not widen its own contract. Best-effort: a vault without a register
    proposes nothing."""
    try:
        import calendar_promotion  # function-local: keeps dream's import graph flat
        found = calendar_promotion.proposals(vault_path, today=today, rules=rules)
    except Exception as e:  # pragma: no cover
        print(f"warning: facet-promotion detector failed: {e}", file=sys.stderr)
        return []
    out = []
    for label, s, path, _new_text in found:
        out.append(Proposal(
            stage="facet_promotion", kind="proposed-facet", paths=[str(path)],
            summary=(f"the diary carries `{label}` on {s.days} days ({s.first} … {s.last}, "
                     f"{s.entries} entries; e.g. {s.sample!r}) — register it under `facets:` "
                     "in standards/storage-rules.md if it is a facet"),
            detail={"label": label, "days": s.days, "first": s.first, "last": s.last,
                    "entries": s.entries, "sample": s.sample},
        ))
    return out


def _rel(p: Path, vault_path: Path) -> str:
    try:
        return p.relative_to(vault_path).as_posix()
    except ValueError:
        return str(p)


# -----------------------------------------------------------------------------
# The part-5 stages that still have a reader
# -----------------------------------------------------------------------------

def _stage_part5_jobs(vault_path: Path) -> list:
    """The enrichment breaker's status, the backlink footers and the
    correction loop, through `dream_stages`. Wrapped so a daemon that cannot
    be reached costs this pass nothing."""
    try:
        import dream_stages
    except ImportError:  # pragma: no cover - install-shape dependent
        return []
    try:
        return dream_stages.run_new_stages(vault_path)
    except Exception as exc:  # pragma: no cover - defensive
        return [dream_stages.StageResult(
            stage="part5_jobs", unavailable=f"{type(exc).__name__}: {exc}")]


# -----------------------------------------------------------------------------
# What the cycle leaves behind
# -----------------------------------------------------------------------------

def _dreaming_dir() -> Path:
    return engine_state.engine_state_dir() / "dreaming"


def _write_review_proposals(run_id: str, proposals: list, now: float) -> Path:
    """The twins and the facets, for the needs-review map. The map regenerates
    from whatever wrote it last, so the findings live here rather than in the
    map: any caller that regenerates it reads the same night's pairs."""
    path = _dreaming_dir() / REVIEW_PROPOSALS_NAME
    by_kind = {"possible-twin": [], "same-key": [], "proposed-facet": []}
    for p in proposals:
        if p.kind in by_kind:
            by_kind[p.kind].append({"paths": p.paths, "summary": p.summary, **p.detail})
    atomic_write(path, json.dumps({
        "run_id": run_id, "at": now,
        "twins": by_kind["possible-twin"], "same_key": by_kind["same-key"],
        "facets": by_kind["proposed-facet"],
    }, indent=2) + "\n")
    return path


def _write_cycle_report(digest: DreamDigest, part5: list, now: float) -> Path:
    """The cycle in numbers, for the morning note's "What ran"."""
    counts = {}
    for p in digest.proposals:
        counts[p.kind] = counts.get(p.kind, 0) + 1
    stats = digest.corpus_stats
    report = {
        "run_id": digest.run_id,
        "at": now,
        "storage_rules_ok": stats.get("storage_rules_ok", True),
        "storage_rules_error": stats.get("storage_rules_error"),
        "entries": stats.get("entry_count", 0),
        "lint": {k[len("lint_"):]: v for k, v in stats.items() if k.startswith("lint_")},
        "possible_twins": counts.get("possible-twin", 0),
        "same_key": counts.get("same-key", 0),
        "proposed_facets": counts.get("proposed-facet", 0),
        "needs_review": digest.needs_review,
        "part5": part5,
    }
    path = _dreaming_dir() / CYCLE_REPORT_NAME
    atomic_write(path, json.dumps(report, indent=2, default=str) + "\n")
    return path


def _render_digest(digest: DreamDigest) -> str:
    stats = digest.corpus_stats
    lines = [
        f"# Dream digest — run {digest.run_id}",
        "",
        f"Corpus: {stats['entry_count']} entries, {stats['total_bytes']} bytes.",
    ]
    # The filing contract, before every other line: when it fails, nothing
    # else in this digest describes work that happened.
    if stats.get("storage_rules_ok") is False:
        lines += [
            "",
            "**Filing is halted.** The storage-rules block does not parse, so nothing "
            "filed this cycle and every note stays where it was:",
            "",
            f"> {stats.get('storage_rules_error', 'unknown parse failure')}",
            "",
            "Fix the block and the next cycle picks up where this one stopped. Notes "
            "wait as `unfiled`; none of them was filed under a guess.",
        ]
    elif "storage_rules_hash" in stats:
        source = ("the packaged default" if stats.get("storage_rules_is_default")
                  else stats.get("storage_rules_source", "the vault"))
        line = f"Filing rules: hash `{stats['storage_rules_hash']}` from {source}"
        if stats.get("storage_rules_hash_changed"):
            line += (f" — **changed** since the last cycle (was "
                     f"`{stats.get('storage_rules_previous_hash')}`); "
                     f"{stats.get('storage_rules_stale_count', 0)} memory(ies) "
                     f"now carry a stale `rules_hash`")
        else:
            line += f" · {stats.get('storage_rules_stale_count', 0)} stale"
        line += f", {stats.get('storage_rules_unjudged_count', 0)} never judged."
        lines.append(line)
    if "organic_connectivity" in stats:
        lines.append(
            f"Connectivity: {stats['organic_connectivity']:.1%} organic "
            f"({stats['organically_linked_count']} of {stats['entry_count']} notes with ≥1 "
            f"real, non-generated link) · {stats['generated_link_count']} generated link(s) "
            "(counted separately)."
        )
    if "browse_live_count" in stats:
        lines.append(
            f"Browse surface: {stats['browse_live_count']} live, "
            f"{stats['browse_shelved_count']} shelved, {stats['browse_archived_count']} archived."
        )
    if "lint_orphan_count" in stats:
        lines.append(
            f"Lint: {stats['lint_orphan_count']} orphan(s), "
            f"{stats['lint_contradiction_count']} contradiction(s), "
            f"{stats.get('lint_graph_snapshot_mismatch_count', 0)} graph-snapshot mismatch(es), "
            f"{stats.get('lint_repairable_count', 0)} mis-cased link(s) it would repair, "
            f"mean quality score {stats['lint_mean_quality_score']:.2f} · a report, nothing "
            "applied · full report via `/memory lint`."
        )
    if digest.needs_review is not None:
        n = digest.needs_review
        reasons = ", ".join(f"{k} {v}" for k, v in (n.get("by_reason") or {}).items() if v) \
            or "nothing waiting"
        lines.append(f"Needs review: {n['total']} note(s) — {reasons} · MOC {n.get('moc', '')}")
    lines += ["", "## For you to judge", ""]
    if not digest.proposals:
        lines.append("Nothing this run.")
    for p in digest.proposals:
        lines.append(f"- {p.stage} · {p.kind}: {p.summary} ({', '.join(p.paths)})")
    return "\n".join(lines) + "\n"


def _write_digest(digest: DreamDigest) -> Path:
    # The run's digest, a record in the engine state dir — one file, no
    # staged proposals beside it.
    path = engine_state.engine_state_dir() / "dream-runs" / digest.run_id / "digest.md"
    atomic_write(path, _render_digest(digest))
    return path


# -----------------------------------------------------------------------------
# The pass
# -----------------------------------------------------------------------------

def run_dream(vault_path: Path, *, run_id: str | None = None) -> DreamDigest:
    """Run the cycle once against `vault_path` (the memory root). Changes no
    note; writes the needs-review map, the review proposals, the cycle report
    and the run's digest."""
    vault_path = Path(vault_path)
    run_id = run_id or f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    now = time.time()

    entries = _iter_entries(vault_path)
    loaded = _load(entries)

    corpus_stats = _stage_corpus_stats(entries)
    corpus_stats.update(_connectivity_meter(loaded))
    corpus_stats.update(_browse_surface_counts(vault_path, entries))
    corpus_stats.update(_stage_storage_rules(vault_path, loaded))

    # Fail closed. Every finding below is judged by the rules that just failed
    # to parse. The read-only meters above already ran, so the digest still
    # reports the corpus; it simply proposes nothing about it.
    if not corpus_stats.get("storage_rules_ok", True):
        digest = DreamDigest(run_id=run_id, corpus_stats=corpus_stats, proposals=[])
        digest.digest_path = _write_digest(digest)
        _write_cycle_report(digest, [], now)
        return digest

    # Lint first, before anything touches the graph snapshot: its own
    # snapshot cross-check must see what persisted from before this cycle.
    corpus_stats.update(_stage_lint(vault_path))
    proposals = []
    proposals.extend(_stage_dedup(entries, loaded, vault_path))
    proposals.extend(_stage_contradiction_triage(entries, loaded, vault_path))
    proposals.extend(_stage_facet_promotion(vault_path))
    part5 = [r.as_dict() for r in _stage_part5_jobs(vault_path)]
    corpus_stats["part5_stages"] = part5

    digest = DreamDigest(run_id=run_id, corpus_stats=corpus_stats, proposals=proposals)
    _write_review_proposals(run_id, proposals, now)

    # The needs-review map regenerates here, with this cycle's twins and
    # facets now on disk for it to read. Best-effort.
    try:
        import needs_review  # function-local: keeps dream's import graph flat
        moc_path = needs_review.write(vault_path)
        digest.needs_review = dict(needs_review.summary(vault_path), moc=str(moc_path))
    except Exception as e:  # pragma: no cover
        print(f"warning: needs-review MOC regeneration failed: {e}", file=sys.stderr)

    digest.digest_path = _write_digest(digest)
    _write_cycle_report(digest, part5, now)
    return digest


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _resolve_vault_path(arg_vault_path: str | None) -> Path | None:
    import os

    if arg_vault_path:
        return Path(arg_vault_path).expanduser()
    env_path = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    return Path(env_path).expanduser() if env_path else None


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Python half of the night once.")
    parser.add_argument("--vault-path", help="the memory root (overrides MEMORY_VAULT_PATH)")
    parser.add_argument("--run-id", help="override the generated run id")
    # Accepted and ignored, so a manifest written before plan 04 still runs:
    # the cycle applies nothing, so there is nothing to cap or to skip.
    parser.add_argument("--batch-cap", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--no-auto-apply", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    vault = _resolve_vault_path(args.vault_path)
    if vault is None or not vault.exists():
        print("ERROR: no vault path resolved (set --vault-path or MEMORY_VAULT_PATH)", file=sys.stderr)
        return 1

    digest = run_dream(vault, run_id=args.run_id)
    kinds = {}
    for p in digest.proposals:
        kinds[p.kind] = kinds.get(p.kind, 0) + 1
    found = ", ".join(f"{v} {k}" for k, v in sorted(kinds.items())) or "nothing to judge"
    print(f"dream run {digest.run_id}: {found} — digest at {digest.digest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
