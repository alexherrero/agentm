"""The part-5 stages the nightly pass still runs.

Three are left: the enrichment breaker's status line, the backlink footers, and
the correction loop. Entity rollups, stub synthesis and the unfiled drain
retired in agentm-vault plan 04 — the entity class folded, the stubs they
queued were residue (`-f .harness/STOP`, `../`), nothing ever drained either
queue, and the unfiled queue is the enrichment batch's own work now.

The one thing here that writes is a footer, and a footer is written below a
fenced marker precisely so it can be rewritten or removed without touching a
word anybody typed.
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

# The enrichment owner, matching the queue's own vocabulary — the breaker is
# keyed on it.
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


# ── the breaker ──────────────────────────────────────────────────────────

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
    """Every stage this module still runs, in order.

    Returned rather than printed, so `dream.py` folds them into the one digest
    it already writes instead of this module growing a second reporting surface.
    """
    results = [stage_breaker_status(vault_path)]
    if footer_targets:
        results.append(stage_backlink_footers(vault_path, footer_targets))
    # Last. Correction reads what the corpus currently looks like, so it should
    # run over the state this cycle leaves behind rather than the one it found.
    results.append(stage_correction(
        vault_path, revert_log=revert_log, run_id=run_id, distiller=distiller,
        version=version, enrich_enabled=enrich_enabled, trends=trends))
    return results
