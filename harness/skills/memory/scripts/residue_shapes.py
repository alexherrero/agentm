#!/usr/bin/env python3
"""The residue shapes — what the retired writers left in the memory classes.

Residue is what the retired miner and the retired skill-discovery ingest wrote
and nobody ever read: a tool tally, a `User stated:` or `Fix observed:` cut from
mid-sentence, a one-line repo blurb, an opinion supplement stranded at
`proposed`, a `~dup` twin. Session 2 ruled six non-overlapping manifests by
count on 2026-09-06, and 627 notes were purged as landing group 02.

This module holds the shapes and nothing else. It reads notes, classifies them,
and returns counts; it deletes nothing and imports neither the purge lane nor
anything that writes. That separation is deliberate and load-bearing: the purge
lane is operator-only — `test_no_automated_caller` holds that no module of the
memory skill, the dreaming layer, the runner or the hooks may import it — while
the corpus scorecard has to count residue every night. Both read their shapes
from here, so the standing count cannot drift from the thing that selects, and
the nightly scorecard still cannot reach a delete.

The predicates were calibrated against the ruled rows rather than guessed: under
`CLAIM_ORDER` they reproduce every row of B-F and 291 of manifest A's 292.
"""
from __future__ import annotations

import re
from pathlib import Path

_TALLY_RE = re.compile(
    r"(?:tool was invoked|invoked the `[^`]+` tool|tool invoked|tool used)\s+\d+\s+times"
    r"|invoked \d+ times"
    r"|\d+ invocations of the `[^`]+` tool"
    r"|`[^`]+` tool was called \d+ times"
    r"|tool[-_ ]use frequency threshold"
    r"|mining[- ]stub",
    re.I,
)
_DUP_RE = re.compile(r"~dup\d*\.md$")


def _is_tally(rel, fm, body):
    return bool(_TALLY_RE.search(f"{fm.get('title', '')}\n{fm.get('summary', '')}\n{body}"))


def _is_skill_blurb(rel, fm, body):
    """The skill-discovery ingest's one-line repo and paper blurbs.

    The discriminator is the ingest's own scoring stamp, not `source:
    external-fetch` — the research corpus arrived through the same fetch path
    and carries the same source, but nothing scored it. Selecting on the source
    swept nine research notes, the frozen gold set's whole `research-corpus`
    stratum, into the purge on 2026-09-09; they were restored from the purge's
    baseline commit and the predicate narrowed to the stamp only the ingest
    writes. A research note carries authored `aliases:` and a reasoned body; a
    blurb carries a rubric score.
    """
    if "evaluator_classification" not in fm and "rubric_score" not in fm:
        return False
    tags = fm.get("tags") or ""
    if not isinstance(tags, str):
        tags = " ".join(str(t) for t in tags)
    return "skill-discovery" in tags or str(fm.get("source") or "") == "external-fetch"


def _is_fix_fragment(rel, fm, body):
    return bool(re.search(r"[Ff]ix observed", f"{fm.get('title', '')}\n{body}"))


def _is_opinion_supplement(rel, fm, body):
    return str(fm.get("kind") or "") == "opinion-supplement"


def _is_user_stated(rel, fm, body):
    return "User stated:" in f"{fm.get('title', '')}\n{body}"


def _is_dup_twin(rel, fm, body):
    return bool(_DUP_RE.search(rel))


# letter -> (title, the count ruled on 2026-09-06, claimable class dirs, predicate)
POPULATIONS = {
    "A": ("tool-tallies", 292, ("procedural", "semantic"), _is_tally),
    "D": ("skill-discovery-blurbs", 116, ("semantic",), _is_skill_blurb),
    "C": ("fix-observed-fragments", 21, ("procedural", "crystallized"), _is_fix_fragment),
    "E": ("opinion-supplements", 32, ("crystallized",), _is_opinion_supplement),
    "B": ("user-stated-fragments", 107, ("semantic",), _is_user_stated),
    "F": ("dup-twins", 4, ("semantic",), _is_dup_twin),
}
# A path belongs to the first population that claims it, so the counts add and
# nothing is ruled twice — the rule the ruled manifests were rendered under.
CLAIM_ORDER = ("A", "D", "C", "E", "B", "F")


def classify(vault: "Path | str") -> dict:
    """{relative path -> letter} for every note a population claims today."""
    import lifecycle_transitions as lt  # same skill dir; imported here to keep this module leaf-ish
    from filing_engine import _frontmatter  # same skill dir

    vault = Path(vault)
    out = {}
    for p in lt.memory_notes(vault):
        rel = p.relative_to(vault).as_posix()
        parts = Path(rel).parts
        cls = parts[1] if len(parts) > 1 else ""
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm, body = _frontmatter(text)
        for letter in CLAIM_ORDER:
            _, _, dirs, pred = POPULATIONS[letter]
            if cls in dirs and pred(rel, fm, body):
                out[rel] = letter
                break
    return out


def counts(vault: "Path | str") -> "dict | None":
    """Residue per shape, or None when the vault has no memory/.

    Two status values ride along with the six populations: `proposed`, which no
    writer sets since the accumulate loop retired, and `deleted`, which is not
    in the vocabulary at all — one note carried it and went on its own manifest.
    """
    import lifecycle_transitions as lt  # same skill dir
    from filing_engine import _frontmatter  # same skill dir

    vault = Path(vault)
    if not (vault / "memory").is_dir():
        return None
    out = {title: 0 for (title, _, _, _) in POPULATIONS.values()}
    out["status: proposed"] = 0
    out["status: deleted"] = 0
    for letter in classify(vault).values():
        out[POPULATIONS[letter][0]] += 1
    for note in lt.memory_notes(vault):
        try:
            fm, _ = _frontmatter(note.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        status = str(fm.get("status") or "").strip().lower()
        if status in ("proposed", "deleted"):
            out[f"status: {status}"] += 1
    return out
