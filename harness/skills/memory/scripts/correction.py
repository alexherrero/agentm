#!/usr/bin/env python3
"""What dreaming does when the meters find a cluster.

The meters say the corpus is converging; `agentmd clusters` says where and what
kind. This is the acting half, and it follows the design's own sentence:

    Genuine duplicates merge through the existing supersede machinery.
    Pattern-collapsed notes — distinct sources flattened into near-identical
    prose — are re-enriched from their originals, which is always possible
    because the source survives in git and through `source`; the re-run lands
    under a new pass version and the revert log covers it. And a persistent
    trend is a prompt problem, which becomes a self-improvement proposal in the
    brief: dreaming proposes the voice or prompt change, and a supervised
    session lands it.

Three severities, three different amounts of autonomy, and the ordering is the
point — the loudest action is the one that needs a person.

## What each arm may do

**Duplicates stage a proposal and change nothing.** `dream_confirm` deliberately
keeps `dedup`/`promote` out of `AUTO_APPLY_STAGES`, and its docstring says the
set must never grow to include them "without a fresh, separate operator ruling".
This module is not that ruling. A duplicate cluster produces a proposal in the
same shape `dream.py`'s own dedup stage produces, staged for a human to confirm,
and the mutation it describes stays undone until somebody confirms it.

**Collapsed clusters re-distill, behind the revert log.** This is the only arm
that writes, and every write goes through `RevertLog.record_and_apply` so the
whole run reverts to byte-identical originals. It needs the enrichment pass to
produce the new body, so it does nothing when enrichment is off — which is the
shipped configuration, and it says so rather than reporting zero work.

**A persistent trend writes a proposal for a person.** Never a change. The
failure it describes is in the prompt, and a pass rewriting its own prompt on the
strength of its own output is the loop this design does not build.

## Nothing here decides what a cluster is

The kind comes from the daemon, computed from provenance, deterministically. Two
of the four kinds are `mixed` and `unknown`, and both mean "no automatic action":
a cluster where some members share a source and some do not has both problems and
one fix for neither, and a cluster where provenance was never recorded is a
finding about metadata rather than about notes.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import work_ledger  # noqa: E402
from vault_lock import atomic_write  # noqa: E402

# ── the numbers, written down before the run ───────────────────────────────

# How many consecutive cycles a meter has to move the wrong way before the trend
# is called persistent.
#
# Three, and the reasoning is the same one `check_stage_anomaly` uses for its own
# cold-start guard: two points are a line through any two numbers, and a meter
# that alarms on two consecutive readings will alarm on ordinary noise. Three
# consecutive movements in one direction is p = 0.25 under a null of independent
# coin flips, which is weak on its own — so this arm writes a proposal for a
# person to read rather than changing anything, and the bar is set for "worth a
# look" rather than for "act".
#
# It is deliberately not tuned against a baseline, because there is no baseline:
# the meters' scope was corrected on 2026-08-23 and every reading before that
# describes a different population. *Re-audit trigger: the first month of
# readings taken under the corrected scope.*
TREND_CYCLES = 3

# The stage name correction's merge proposals carry.
#
# Not `dedup`, so that a rule about one cannot silently become a rule about the
# other, and specifically not any name in `AUTO_APPLY_STAGES` — a merge here is
# staged for a person exactly like `dream.py`'s own.
MERGE_STAGE = "correction_merge"

# Where a self-improvement proposal lands: alongside the digest a person already
# reads, rather than in a surface nobody opens.
PROPOSAL_DIRNAME = "corrections"


class CorrectionError(RuntimeError):
    """The correction loop could not do what it was asked."""


class SourceUnavailable(CorrectionError):
    """A note's source could not be read, so it cannot be re-distilled.

    Raised rather than defaulted, because the two defaults are both wrong: an
    empty source re-distills the note into nothing, and skipping silently leaves
    a converged cluster reported as corrected.
    """


@dataclass
class Action:
    """What the loop did, or declined to do, about one cluster."""

    kind: str  # "merge_proposed" | "redistilled" | "review_only" | "deferred"
    cluster_kind: str
    members: list
    reason: str
    # entry_id is the revert-log handle for an arm that wrote. Empty for every
    # arm that did not, which is most of them.
    entry_id: str = ""
    # proposal is the staged mutation set for a merge — described, not applied.
    proposal: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        out = {
            "kind": self.kind,
            "cluster_kind": self.cluster_kind,
            "members": list(self.members),
            "reason": self.reason,
        }
        if self.entry_id:
            out["entry_id"] = self.entry_id
        if self.proposal:
            out["proposal"] = self.proposal
        return out

    def digest_line(self) -> str:
        """One line for the digest, in the shape every other stage reports.

        The revert entry id is in the line rather than only in the structured
        form, because a person reading "two notes were re-distilled" and wanting
        them back needs the handle in front of them.
        """
        where = ", ".join(self.members[:3])
        if len(self.members) > 3:
            where += f" and {len(self.members) - 3} more"
        line = f"{self.kind}: {where} — {self.reason}"
        if self.entry_id:
            line += f" (revert with entry {self.entry_id})"
        return line


# ── deciding, which is separate from doing ─────────────────────────────────

def plan_action(cluster: dict, *, enrich_enabled: bool) -> str:
    """Which arm a cluster belongs to. Pure, and the only place the mapping
    lives.

    Separate from the arms themselves so the decision can be tested without a
    vault, a revert log or a model — and so that "a mixed cluster is never acted
    on" is one readable line rather than a property of three call sites.
    """
    kind = cluster.get("kind", "")
    if kind == "duplicate":
        return "merge_proposed"
    if kind == "collapsed":
        return "redistilled" if enrich_enabled else "deferred"
    return "review_only"


def _reason_for(cluster: dict, arm: str) -> str:
    members = len(cluster.get("members", []))
    kind = cluster.get("kind", "?")
    tightest = cluster.get("max_sim", 0.0)
    if arm == "merge_proposed":
        return (f"{members} notes from one source at {tightest:.4f} — merge staged "
                f"for confirmation, not applied")
    if arm == "redistilled":
        return (f"{members} notes from distinct sources at {tightest:.4f} — "
                f"re-distilled from source under a new pass version")
    if arm == "deferred":
        return (f"{members} notes from distinct sources at {tightest:.4f} — "
                f"re-distilling needs the enrichment pass, which is off")
    return f"{kind} — {cluster.get('why', 'no automatic action')}"


# ── arm one: duplicates stage a proposal ───────────────────────────────────

def build_merge_proposal(vault_path, cluster: dict) -> dict:
    """The mutation set a merge would make, described and not applied.

    Shaped exactly like `dream.py`'s dedup proposal — `stage`, `kind`, `paths`,
    `summary`, `mutations` — so `dream_confirm.confirm()` applies it through the
    same path a human already uses. The value of that is not tidiness: it means
    an auto-staged merge and a hand-staged one are indistinguishable in the
    revert log, so there is one thing to audit rather than two.

    The first member survives and the rest are superseded. First by path order,
    which is arbitrary but stated — this module does not know which copy anything
    else links to, and the design says the same thing about near-copies. That is
    exactly why the result is a proposal.
    """
    vault_path = Path(vault_path)
    members = sorted(cluster.get("members", []))
    if len(members) < 2:
        raise CorrectionError(
            f"a merge needs at least two notes; got {members}")

    keeper, rest = members[0], members[1:]
    mutations = []
    for rel in rest:
        path = vault_path / rel
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SourceUnavailable(
                f"{rel} cannot be read, so a merge cannot be described: {exc}"
            ) from exc
        mutations.append([str(path), _mark_superseded(raw, keeper)])

    return {
        "stage": MERGE_STAGE,
        "kind": "merge",
        "paths": [str(vault_path / m) for m in members],
        "summary": (f"{len(members)} notes share a source and are "
                    f"{cluster.get('max_sim', 0):.4f} similar — propose keeping "
                    f"{keeper} and superseding the rest"),
        "mutations": mutations,
    }


_FRONTMATTER = re.compile(r"\A---[ \t\r]*\n(.*?)\n---[ \t\r]*\n", re.S)


def _mark_superseded(raw: str, keeper: str) -> str:
    """Set `status: superseded` and point at the survivor.

    Never deletes and never rewrites the body. The design is explicit that a
    superseded memory is rank-penalized rather than removed, and that its text
    stays in git at the capture commit — so the whole of this change is two
    frontmatter keys.
    """
    m = _FRONTMATTER.match(raw)
    if not m:
        return ("---\nstatus: superseded\nsupersedes: " + keeper + "\n---\n\n"
                + raw.lstrip("\n"))
    head, rest = m.group(1), raw[m.end():]
    lines = [ln for ln in head.split("\n")
             if not ln.startswith(("status:", "supersedes:"))]
    lines += ["status: superseded", f"supersedes: {keeper}"]
    return "---\n" + "\n".join(lines) + "\n---\n" + rest


# ── arm two: collapsed clusters re-distill, behind the revert log ──────────

def redistill(vault_path, revert_log, run_id: str, cluster: dict, distiller,
              *, version: str) -> str:
    """Re-enrich every member from its own source, under a new pass version.

    `distiller` is `(rel, raw, source) -> new_body`, injected rather than
    imported. The real one calls the enrichment pass; a test passes a
    deterministic function, which is the only way this arm can be verified at all
    without spending a model call per run.

    Every write goes through `record_and_apply`, one call for the whole cluster,
    so `revert(run_id, entry_id)` restores every member's pre-image byte for
    byte. One call rather than one per note because a half-corrected cluster is
    worse than an uncorrected one: the notes that moved are now at a different
    pass version than the notes that did not, and the ledger reads that as work
    finished.

    Raises SourceUnavailable rather than writing anything if any member's source
    cannot be read. The whole justification for this arm is that the original
    survives; a re-distillation with no original to distill from is the pass
    inventing a memory.
    """
    vault_path = Path(vault_path)
    members = sorted(cluster.get("members", []))
    if not members:
        raise CorrectionError("a cluster with no members cannot be re-distilled")

    provenance = cluster.get("provenance") or {}
    mutations = []
    for rel in members:
        units = provenance.get(rel) or []
        if not units:
            raise SourceUnavailable(
                f"{rel} records no source, so there is nothing to re-distill it "
                f"from. A cluster with an unprovenanced member is classified "
                f"`unknown` and should never have reached this arm.")
        path = vault_path / rel
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SourceUnavailable(f"{rel} cannot be read: {exc}") from exc

        body = distiller(rel, raw, units)
        if not body or not body.strip():
            raise SourceUnavailable(
                f"re-distilling {rel} produced nothing. An empty result is not a "
                f"shorter memory, it is a lost one.")
        mutations.append((path, _stamp_version(body, version)))

    return revert_log.record_and_apply(run_id, "correction_redistill", mutations)


def _stamp_version(body: str, version: str) -> str:
    """Record which pass version wrote this body.

    Without the stamp the coverage ledger still sees the note at the old version,
    so the next cycle finds it stale, re-distills it, and finds it stale again —
    a loop that spends a model call per note per night forever. The stamp is what
    makes the re-run land rather than repeat.
    """
    m = _FRONTMATTER.match(body)
    stamp = [f"enriched_by: {version}"]
    if not m:
        return "---\n" + "\n".join(stamp) + "\n---\n\n" + body.lstrip("\n")
    head, rest = m.group(1), body[m.end():]
    lines = [ln for ln in head.split("\n") if not ln.startswith("enriched_by:")]
    return "---\n" + "\n".join(lines + stamp) + "\n---\n" + rest


# ── arm three: a persistent trend asks for a person ────────────────────────

def is_persistent(readings: list, *, up_is_bad: bool,
                  cycles: int = TREND_CYCLES) -> bool:
    """Whether a meter has moved the wrong way `cycles` times running.

    Movement, not level. A meter sitting high is a corpus that was always like
    that; a meter climbing is a corpus becoming like that, and only the second is
    evidence about what the pass is currently doing.

    Needs `cycles + 1` readings to see `cycles` movements, and says False rather
    than guessing when it has fewer — the same cold-start refusal
    `check_stage_anomaly` makes, for the same reason: an alarm that fires on a
    fresh history is an alarm nobody keeps listening to.
    """
    if cycles < 1 or len(readings) < cycles + 1:
        return False
    recent = readings[-(cycles + 1):]
    for before, after in zip(recent, recent[1:]):
        if up_is_bad and after <= before:
            return False
        if not up_is_bad and after >= before:
            return False
    return True


def propose_prompt_change(vault_path, run_id: str, trends: list,
                          *, now: float | None = None) -> Path:
    """Write the self-improvement proposal a person reads.

    A file, deliberately. The design's words are that dreaming "proposes the
    voice or prompt change, and a supervised session lands it" — so this arm's
    entire output is prose addressed to a human, and it touches no memory.

    Written outside `record_and_apply` because it is not a mutation of the
    corpus: the revert log's job is undoing changes to memories, and adding a
    document nobody's memory depends on would put noise in the audit trail that
    matters.
    """
    vault_path = Path(vault_path)
    now = time.time() if now is None else now
    stamp = time.strftime("%Y-%m-%d", time.gmtime(now))
    out = vault_path / "desk/scratch" / run_id / PROPOSAL_DIRNAME / \
        f"{stamp}-prompt-change.md"

    lines = [
        "---",
        "title: Enrichment is drifting — a prompt change is proposed",
        "kind: report",
        "status: proposed",
        f"created: {stamp}",
        f"run_id: {run_id}",
        "---",
        "",
        "# Enrichment is drifting",
        "",
        f"{len(trends)} meter(s) have moved the wrong way for "
        f"{TREND_CYCLES} cycles running. That is a trend rather than a night, "
        "and a trend in these numbers is a property of the prompt rather than "
        "of the corpus.",
        "",
        "Nothing has been changed. This arm of the correction loop writes a "
        "proposal and stops, because a pass that rewrites its own prompt on the "
        "strength of its own output has no outside check left in it.",
        "",
        "## What moved",
        "",
        "| meter | readings, oldest first | bad direction |",
        "|---|---|---|",
    ]
    for t in trends:
        readings = ", ".join(f"{r:.4f}" for r in t.get("readings", []))
        direction = "rising" if t.get("up_is_bad") else "falling"
        lines.append(f"| {t.get('meter', '?')} | {readings} | {direction} |")

    lines += [
        "",
        "## What a supervised session should look at",
        "",
        "The enrichment prompt's instructions about voice, and whether they are "
        "narrowing what a memory is allowed to sound like. The four meters "
        "cannot tell a corpus that genuinely covers one subject from a corpus "
        "being rewritten into one voice; that distinction is what a person is "
        "for.",
        "",
        "The lever of last resort is a second model in rotation. It stays a last "
        "resort, and it is waiting on these meters to demand it.",
        "",
    ]
    atomic_write(out, "\n".join(lines))
    return out


# ── the stage ──────────────────────────────────────────────────────────────

def stage_correction(vault_path, *, revert_log=None, run_id: str = "",
                     distiller=None, version: str = "",
                     enrich_enabled: bool = False,
                     trends: list | None = None) -> "StageResult":
    """One cycle of the correction loop, as a dream stage.

    Reports in the digest's own numbers rather than printing, like every other
    stage in `dream_stages`. A cycle that found nothing and a cycle that could
    not look are different rows, because the whole value of the meters is being
    able to tell those apart.
    """
    from dream_stages import StageResult  # noqa: E402 (circular at import time)

    res = StageResult(stage="correction")
    try:
        report = work_ledger.clusters()
    except work_ledger.LedgerUnavailable as exc:
        res.unavailable = str(exc)
        return res

    for msg in report.get("unavailable") or []:
        res.notes.append(msg)

    for cluster in report.get("clusters") or []:
        res.considered += 1
        arm = plan_action(cluster, enrich_enabled=enrich_enabled)
        action = Action(kind=arm, cluster_kind=cluster.get("kind", ""),
                        members=list(cluster.get("members", [])),
                        reason=_reason_for(cluster, arm))
        if arm == "merge_proposed":
            try:
                action.proposal = build_merge_proposal(vault_path, cluster)
            except CorrectionError as exc:
                action.kind, action.reason = "review_only", str(exc)
                res.skipped += 1
                res.notes.append(action.digest_line())
                continue
            res.enqueued += 1
        elif arm == "redistilled":
            if revert_log is None or distiller is None or not version:
                action.kind = "deferred"
                action.reason = ("re-distilling needs a revert log, a distiller "
                                 "and a pass version; the caller supplied "
                                 "neither all three nor none")
                res.skipped += 1
                res.notes.append(action.digest_line())
                continue
            try:
                action.entry_id = redistill(vault_path, revert_log, run_id,
                                            cluster, distiller, version=version)
            except CorrectionError as exc:
                action.kind, action.reason = "review_only", str(exc)
                res.skipped += 1
                res.notes.append(action.digest_line())
                continue
            res.written += len(action.members)
        else:
            res.skipped += 1
        res.notes.append(action.digest_line())

    for t in trends or []:
        if is_persistent(t.get("readings", []), up_is_bad=t.get("up_is_bad", True)):
            direction = "rising" if t.get("up_is_bad", True) else "falling"
            res.notes.append(
                f"trend: {t.get('meter', '?')} has been {direction} for "
                f"{TREND_CYCLES} cycles — a proposal is owed, and nothing has "
                f"been changed")

    return res


def main(argv: list) -> int:
    """Report what the loop would do, without doing any of it.

    A dry run is the only command-line entry point on purpose. The two arms that
    write are called from the dreaming pass, which owns the run id and the revert
    log; a person wanting to see the state should be able to see it without
    acquiring either.
    """
    vault = os.environ.get("MEMORY_VAULT_PATH", "")
    try:
        report = work_ledger.clusters()
    except work_ledger.LedgerUnavailable as exc:
        print(f"clusters unavailable: {exc}", file=sys.stderr)
        return 1
    out = []
    for cluster in report.get("clusters") or []:
        arm = plan_action(cluster, enrich_enabled=False)
        out.append(Action(kind=arm, cluster_kind=cluster.get("kind", ""),
                          members=list(cluster.get("members", [])),
                          reason=_reason_for(cluster, arm)).as_dict())
    print(json.dumps({"vault": vault, "would": out}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
