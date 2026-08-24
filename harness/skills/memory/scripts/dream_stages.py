"""The stages part 5 adds to the nightly pass.

`dream.py` already runs twelve. These are the four the filing contract's job
list names and the corpus does not have: entity rollups, backlink footers, stub
synthesis, and draining the unfiled queue. They live here rather than in
`dream.py` because that file is 1,635 lines of working stages and a fifth of it
again would make the run harder to read, not the pass more capable.

Every one of them keeps this module's existing contract: they PROPOSE. The one
thing they write for real is a footer, and a footer is written below a fenced
marker precisely so it can be rewritten or removed without touching a word
anybody typed.

# Discovery is decoupled from repair

Three of the four find gaps that another stage owns. A mentioned entity with no
file, a wikilink pointing at nothing, a memory whose contract hash went stale —
each is enqueued naming an owner and a reason, and the stage moves on. Owners
drain their own queues under their own caps, which is what stops one cycle
trying to do everything and what makes "how far behind are we" a number rather
than a feeling.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

try:  # pragma: no cover - import shape mirrors dream.py's own
    import enrichment_breaker
    import work_ledger
except ImportError:  # pragma: no cover
    from . import enrichment_breaker  # type: ignore
    from . import work_ledger  # type: ignore


# ── the footer, and the marker that makes it safe to rewrite ───────────────

FOOTER_BEGIN = "<!-- agentm:backlinks -->"
FOOTER_END = "<!-- /agentm:backlinks -->"

# Everything between the markers is machine-owned and rewritten whole on every
# pass. Everything outside them is the operator's, and this module never touches
# it. That boundary is the entire safety story for the one stage here that
# writes: without a marker, "update the footer" means diffing prose against
# prose and hoping.
_FOOTER_BLOCK = re.compile(
    re.escape(FOOTER_BEGIN) + r".*?" + re.escape(FOOTER_END),
    re.DOTALL,
)

# The rollup threshold. An entity mentioned in this many notes has enough said
# about it to be worth a file; below it, a rollup would mostly restate its own
# title. The design's worked example is an entity mentioned in forty notes with
# no entity file, which is well clear of this — the number is a floor, not a
# target.
ROLLUP_MIN_MENTIONS = 5

# A stub is worth synthesizing when more than one note expects the same missing
# target. One note linking to something that does not exist is as likely to be a
# typo as a gap, and a stub for a typo is a note nobody wanted that now resolves
# the link and hides the mistake.
STUB_MIN_SOURCES = 2

# Owners, matching the queue's own vocabulary.
OWNER_ROLLUP = "entity-rollup"
OWNER_STUB = "stub-synthesis"
OWNER_ENRICH = "enrich"


@dataclass
class StageResult:
    """What one stage did, in the numbers the digest reports."""

    stage: str
    considered: int = 0
    enqueued: int = 0
    written: int = 0
    skipped: int = 0
    # `unavailable` is not a failure. The daemon owns the ledger and the queues,
    # and a cycle that ran without it did not do less work badly — it did not do
    # the work, and the digest should say which.
    unavailable: str = ""
    notes: list = field(default_factory=list)

    def as_dict(self) -> dict:
        out = {
            "stage": self.stage,
            "considered": self.considered,
            "enqueued": self.enqueued,
            "written": self.written,
            "skipped": self.skipped,
        }
        if self.unavailable:
            out["unavailable"] = self.unavailable
        if self.notes:
            out["notes"] = self.notes
        return out


# ── entity rollups ─────────────────────────────────────────────────────────

def stage_entity_rollups(*, min_mentions: int = ROLLUP_MIN_MENTIONS) -> StageResult:
    """Enqueue a rollup for every entity the corpus talks about and has no file
    for.

    Discovery only. Building the file is a token-bearing job with its own tier
    and its own budget, and doing it here would mean the reconcile scan decided
    how much a cycle spends — which is exactly the coupling the queues exist to
    break.
    """
    res = StageResult(stage="entity_rollups")
    try:
        mentions = work_ledger.entity_mentions(min_mentions=min_mentions)
    except work_ledger.LedgerUnavailable as exc:
        res.unavailable = str(exc)
        return res

    for entity in mentions:
        res.considered += 1
        if entity.get("file"):
            res.skipped += 1
            continue
        uri = entity.get("uri", "")
        if not uri:
            res.skipped += 1
            continue
        try:
            work_ledger.enqueue(
                OWNER_ROLLUP, uri,
                f"mentioned in {entity.get('mentions', 0)} notes with no entity file",
            )
        except work_ledger.LedgerUnavailable as exc:
            res.unavailable = str(exc)
            return res
        res.enqueued += 1
    return res


# ── stub synthesis ─────────────────────────────────────────────────────────

def stage_stub_synthesis(*, min_sources: int = STUB_MIN_SOURCES) -> StageResult:
    """Enqueue a stub for every target the corpus expects and does not have."""
    res = StageResult(stage="stub_synthesis")
    try:
        targets = work_ledger.dangling_targets(min_sources=min_sources)
    except work_ledger.LedgerUnavailable as exc:
        res.unavailable = str(exc)
        return res

    for target in targets:
        res.considered += 1
        name = target.get("target", "")
        sources = target.get("sources") or []
        if not name:
            res.skipped += 1
            continue
        try:
            work_ledger.enqueue(
                OWNER_STUB, name,
                f"{len(sources)} notes link to it and nothing answers",
            )
        except work_ledger.LedgerUnavailable as exc:
            res.unavailable = str(exc)
            return res
        res.enqueued += 1
    return res


# ── backlink footers ───────────────────────────────────────────────────────

def render_footer(sources: list) -> str:
    """The footer block for a note with these inbound links.

    Sorted and deduplicated, so a pass over an unchanged corpus rewrites nothing
    — this is the one stage here that writes, and every write it makes lands in
    the vault's git history.
    """
    unique = sorted({s for s in sources if s})
    lines = [FOOTER_BEGIN, "", "**Referenced by:**", ""]
    lines += [f"- [[{s}]]" for s in unique]
    lines += ["", FOOTER_END]
    return "\n".join(lines)


def apply_footer(body: str, footer: str) -> str:
    """Put `footer` at the end of `body`, replacing any earlier one.

    Replaced rather than appended, or a note linked to for a year would carry a
    year of footers. The markers are what make the replacement a substitution
    rather than a guess: everything between them is machine-owned, and the pass
    never reads or edits a byte outside them.
    """
    existing = _FOOTER_BLOCK.search(body)
    if existing:
        return body[:existing.start()] + footer + body[existing.end():]
    return body.rstrip("\n") + "\n\n" + footer + "\n"


def strip_footer(body: str) -> str:
    """`body` without its machine-owned footer.

    The revert, and the thing that makes the footer stage safe to run at all:
    what it wrote can be removed exactly, leaving what the operator wrote
    byte-identical.
    """
    stripped = _FOOTER_BLOCK.sub("", body)
    return stripped.rstrip("\n") + "\n" if stripped.strip() else stripped


def stage_backlink_footers(vault_path, targets: list, *, write=None) -> StageResult:
    """Refresh the backlink footer on each of `targets`.

    `write` is injected so a caller can preview rather than write, and so a test
    can check what would land without a vault. Production passes nothing and the
    file is written.
    """
    res = StageResult(stage="backlink_footers")
    vault_path = Path(vault_path)
    writer = write or (lambda path, text: Path(path).write_text(text, encoding="utf-8"))

    for rel in targets:
        res.considered += 1
        try:
            links = work_ledger.backlinks(rel)
        except work_ledger.LedgerUnavailable as exc:
            res.unavailable = str(exc)
            return res

        # `resolved` carries the source path on a backlink query — see
        # `work_ledger.backlinks` for why the field means the opposite of what
        # it says here.
        sources = [l.get("Resolved") or l.get("resolved") or "" for l in links]
        sources = [s for s in sources if s and s != rel]

        abs_path = vault_path / rel
        try:
            body = abs_path.read_text(encoding="utf-8")
        except OSError:
            # In the index and not on disk is a drifted index, which the
            # reconcile pass fixes. It is not this stage's job to repair, and
            # failing the whole run over one missing file would make a drifted
            # index look like a broken pass.
            res.skipped += 1
            continue

        if not sources:
            # Nothing points here any more. An empty footer is worse than none:
            # it claims the question was asked and answered nothing.
            updated = strip_footer(body)
        else:
            updated = apply_footer(body, render_footer(sources))

        if updated == body:
            res.skipped += 1
            continue
        writer(abs_path, updated)
        res.written += 1
    return res


# ── draining the unfiled queue ─────────────────────────────────────────────

def stage_breaker_status(vault_path) -> StageResult:
    """Report the breaker every cycle, open or closed.

    Every cycle rather than only when it is open. A line that appeared solely on
    the bad nights would leave the reader unable to tell "auto-apply is running"
    from "nobody checked", which is the same absence-versus-zero confusion the
    scorecards are built to avoid.
    """
    res = StageResult(stage="breaker")
    st = enrichment_breaker.state(vault_path, OWNER_ENRICH)
    res.notes.append(enrichment_breaker.digest_line(st))
    if st.open:
        res.skipped = 1
    return res


def stage_unfiled_drain(*, enabled: bool = False, budget: int = 0,
                        vault_path=None) -> StageResult:
    """Enqueue re-enrichment for what the coverage ledger says is pending.

    Discovery only, and deliberately so. Part 4 built the drain itself — the
    pass, its eleven gates and its budget all live in `agentmd enrich` — and
    deferred running it over the standing queue, which at last count was 8,765
    notes. Nothing here changes that: this stage asks the ledger what is
    pending, enqueues it, and lets the owner drain under its own cap.

    `enabled` is off, matching `daemon.enrich_enabled`. A stage that started
    spending because a binary was updated is the thing that flag exists to
    prevent, and the queue depth it would fill is the number part 6's meters
    read before anybody decides to turn it on.
    """
    res = StageResult(stage="unfiled_drain")

    # The breaker first, because a paused pass should not spend a ledger query
    # working out how much it is not allowed to do.
    if vault_path is not None:
        st = enrichment_breaker.state(vault_path, OWNER_ENRICH)
        if not st.may_auto_apply():
            res.unavailable = ""
            res.notes.append(
                f"paused: {st.reason}. Nothing is enqueued until somebody clears "
                f"the breaker — it is a decision, not a timeout.")
            return res

    if not enabled:
        res.notes.append(
            "off: enrichment spends per note and the standing queue is the "
            "corpus. The ledger's pending count is reported without acting on it."
        )

    try:
        report = work_ledger.pending("enrich")
    except work_ledger.LedgerUnavailable as exc:
        res.unavailable = str(exc)
        return res

    items = report.get("pending") or []
    res.considered = len(items)
    res.notes.append(
        f"coverage {report.get('current', 0)}/{report.get('eligible', 0)}"
    )

    if not enabled:
        res.skipped = len(items)
        return res

    for item in items[: budget or len(items)]:
        target = item.get("target", "")
        if not target:
            res.skipped += 1
            continue
        try:
            work_ledger.enqueue(
                OWNER_ENRICH, target, item.get("reason", "pending"),
            )
        except work_ledger.LedgerUnavailable as exc:
            res.unavailable = str(exc)
            return res
        res.enqueued += 1
    return res


def stage_correction(vault_path, *, revert_log=None, run_id: str = "",
                     distiller=None, version: str = "",
                     enrich_enabled: bool = False, trends=None) -> StageResult:
    """The correction loop, forwarded.

    Lives in `correction.py` — it is a few hundred lines with three arms and its
    own refusals, and folding that into this module would make a file about
    enqueueing work into a file about rewriting memories.

    Forwarded rather than imported at module scope so a `dream_stages` import
    still works on an install without it, which is the same shape every other
    daemon-dependent stage here already has.
    """
    try:
        import correction
    except ImportError as exc:
        return StageResult(stage="correction", unavailable=str(exc))
    return correction.stage_correction(
        vault_path, revert_log=revert_log, run_id=run_id, distiller=distiller,
        version=version, enrich_enabled=enrich_enabled, trends=trends)


def run_new_stages(vault_path, *, footer_targets=None, enrich_enabled=False,
                   revert_log=None, run_id: str = "", distiller=None,
                   version: str = "", trends=None) -> list:
    """Every stage this module adds, in the order the job list names them.

    Returned rather than printed, so `dream.py` folds them into the one digest
    it already writes instead of this module growing a second reporting surface.
    """
    results = [
        stage_breaker_status(vault_path),
        stage_entity_rollups(),
        stage_stub_synthesis(),
    ]
    if footer_targets:
        results.append(stage_backlink_footers(vault_path, footer_targets))
    results.append(stage_unfiled_drain(enabled=enrich_enabled,
                                       vault_path=vault_path))
    # Last, and after the drain. Correction reads what the corpus currently
    # looks like, so it should run over the state this cycle leaves behind
    # rather than the state it started from.
    results.append(stage_correction(
        vault_path, revert_log=revert_log, run_id=run_id, distiller=distiller,
        version=version, enrich_enabled=enrich_enabled, trends=trends))
    return results
