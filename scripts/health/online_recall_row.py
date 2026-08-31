#!/usr/bin/env python3
"""The Online recall section of the health scorecard.

One question, answered where the rest of the health numbers live: **during real
work, does recall put enough in front of the model to answer what was asked?**

Everything here follows the scorecard's existing rule — render honestly as
unmeasured rather than fabricate a number — extended to cover three ways this
particular measurement can be absent or misleading:

* **STALE.** The artifact is old enough that it describes traffic nobody is
  running any more. A number from a fortnight ago is not wrong, but presenting
  it beside today's checks implies it is current.
* **MUTE.** Too few judged turns for an interval that survives repeated
  reading. The rate still prints, and the interval says it excludes nothing —
  which is the measurement at that sample size.
* **UNVALIDATED.** No independent human labels stand behind the judge. This one
  does not clear on its own and is not a temporary state; see below.

# Why UNVALIDATED is permanent until something changes

The plan expected a κ against ~150 operator labels. That number does not exist
and will not from this data. Unaided labelling proved error-prone — the
operator marked a turn `sufficient` without noticing that no June-dated plan was
among three retrieved plans — and once the judge's reasoning is shown to fix
that, the labels are no longer independent, so κ is not computable from them.

What exists instead is a machine panel (Sonnet, Gemini, Fable) whose labels the
operator reviewed and accepted. That is a good label set and it is not human
ground truth: two of the three graders are the same model family as the judge
being scored. The section says so on every render rather than carrying a flag
that someone might one day clear by mistake.
"""
from __future__ import annotations

import json
import pathlib
import re
import time
from typing import Optional

import always_valid as av
import panel as panel_mod

# Beyond this the artifact describes traffic that has moved on.
STALE_AFTER_DAYS = 14

VALIDATION_NOTE = (
    "machine panel only — three graders, two of them the same model family as "
    "the judge being scored. No independent human labels stand behind this "
    "number, and none are obtainable from the collected data: unaided labels "
    "were error-prone and aided ones are not independent."
)


def _age_days(path: pathlib.Path, *, now: float = None) -> Optional[float]:
    if not path.exists():
        return None
    now = now if now is not None else time.time()
    return (now - path.stat().st_mtime) / 86400.0


# A gap the judge located in the conversation rather than in the notes —
# "what 'both' refers to", "what 'it' refers to". No retrieval system could
# have supplied these, so counting them as recall failures overstates the
# failure rate. Measured at 33 of 67 insufficiency verdicts, which moved the
# headline from 13.0% to 22.7%.
_UNRETRIEVABLE = re.compile(r"\brefers? to\b|\bconversation\b|\breferent\b",
                            re.I)


def unretrievable(reasons: dict) -> set:
    """Turn ids whose gap no note could have filled."""
    return {tid for tid, v in (reasons or {}).items()
            if v.get("verdict") == "insufficient"
            and any(_UNRETRIEVABLE.search(w) for w in (v.get("why") or []))}


def compute(panel_path: pathlib.Path, *, crossing_path: pathlib.Path = None,
            reasons_path: pathlib.Path = None,
            retired_arms: tuple = ("lexical",),
            now: float = None) -> dict:
    """Read the artifacts and say what can honestly be claimed from them."""
    if not panel_path.exists():
        return {"state": "ABSENT",
                "note": "no online-recall run has been recorded — the section "
                        "is empty rather than zero, because zero would read as "
                        "a measurement"}

    age = _age_days(panel_path, now=now)
    data = json.loads(panel_path.read_text(encoding="utf-8"))
    rows = data.get("rows") or []
    for r in rows:
        r["_label"] = panel_mod.coalesce(r)["label"]
    labels = [r["_label"] for r in rows]

    excluded: dict = {}
    keep = list(rows)

    reasons = {}
    if reasons_path and reasons_path.exists():
        reasons = json.loads(reasons_path.read_text(encoding="utf-8"))
    gone = unretrievable(reasons)
    if gone:
        before = len(keep)
        keep = [r for r in keep if r["id"] not in gone]
        excluded["gap_not_retrievable"] = before - len(keep)

    if any("arm" in r for r in keep):
        arm_dropped = sum(1 for r in keep if r.get("arm") in retired_arms)
        if arm_dropped:
            keep = [r for r in keep if r.get("arm") not in retired_arms]
            excluded["retired_retrieval_arm"] = arm_dropped
        arm_known = True
    else:
        # The field is absent, not empty. Silently skipping the filter would
        # let a reader think no turn ran on a retired arm, when the truth is
        # that nobody recorded which arm any of them ran on — 9 of 90 did, in
        # the run this artifact came from.
        arm_known = False

    scored_raw = [v for v in labels if v in ("sufficient", "insufficient")]
    scored = [r["_label"] for r in keep
              if r["_label"] in ("sufficient", "insufficient")]
    suff = sum(1 for v in scored if v == "sufficient")

    out = {
        "state": "STALE" if (age is not None and age > STALE_AFTER_DAYS)
                 else "OK",
        "artifact_age_days": round(age, 1) if age is not None else None,
        "turns": len(rows),
        "not_an_information_need": sum(1 for v in labels if v == "n/a"),
        "scored": len(scored),
        "scored_before_exclusions": len(scored_raw),
        "excluded": excluded or None,
        "excluded_note": (
            "turns removed because recall could not have served them: a gap "
            "the judge located in the conversation rather than in any note, "
            "and turns from a retrieval arm no longer in production. Counting "
            "them as failures moved the headline nine points."
            if excluded else None),
        "retrieval_arm_recorded": arm_known,
        "validation": "UNVALIDATED",
        "validation_note": VALIDATION_NOTE,
    }
    if not arm_known:
        out["arm_note"] = (
            "this artifact does not record which retrieval arm each turn ran "
            "on, so turns from a retired arm could not be excluded. In the run "
            "it came from, 9 of 90 were lexical-arm — a configuration no longer "
            "in production. The figure is that much pessimistic.")
    if age is not None and age > STALE_AFTER_DAYS:
        out["stale_note"] = (
            f"the last run is {age:.0f} days old, past the {STALE_AFTER_DAYS}-"
            f"day line. Recall and the traffic have both moved since; this "
            f"describes what was true then.")

    if not scored:
        out["note"] = ("no turn produced a sufficiency verdict — nothing to "
                       "report, and a zero would be a statement about the "
                       "judge rather than about recall")
        return out

    out.update(av.report(suff, len(scored)))
    if out.get("ci_uninformative"):
        out["state"] = "MUTE" if out["state"] == "OK" else out["state"]

    # Agreement between the graders — the only κ this data can support.
    pairs = [(r.get("claude"), r.get("gemini")) for r in rows
             if r.get("claude") and r.get("gemini")]
    if len(pairs) >= 10:
        import agreement as ag
        k = ag.cohen_kappa([x for x, _ in pairs], [y for _, y in pairs])
        out["grader_kappa"] = k["kappa"]
        out["grader_kappa_ci"] = k["kappa_ci"]
        out["grader_kappa_note"] = (
            "between two graders, not against a person. It says the two agree "
            "more than chance, not that either is right.")

    if crossing_path and crossing_path.exists():
        cx = json.loads(crossing_path.read_text(encoding="utf-8"))
        q = (cx.get("summary") or {}).get("crossing", {}).get("quadrants")
        if q:
            out["quadrants"] = q
            placed = sum(q.values())
            out["quadrant_note"] = (
                f"over {placed} turns where both axes were decided. `ignored` "
                f"came out 0 — but only {q.get('served', 0) + q.get('ignored', 0)}"
                f" turns had sufficient context at all, so that corner could "
                f"not have held many.")
    return out


def render(section: dict) -> list:
    """The section as scorecard markdown."""
    state = section.get("state")
    if state == "ABSENT":
        return ["## Online recall ⚪", "",
                f"**Not yet measured.** {section['note']}", ""]

    mark = {"OK": "🟢", "STALE": "🟡", "MUTE": "⚪"}.get(state, "⚪")
    lines = [f"## Online recall {mark}", ""]

    if "rate" in section:
        lines.append(
            f"**{section['rate']:.1%}** of turns that needed information had "
            f"enough of it — {section['successes']} of {section['scored']}.")
        lines.append("")
        lines.append("| | |")
        lines.append("|---|---|")
        ci = section.get("always_valid_ci")
        if section.get("ci_uninformative"):
            lines.append(f"| interval | **none — sample too small** "
                         f"(±{section['always_valid_radius']:.2f}) |")
        elif ci:
            lines.append(f"| interval (always-valid) | {ci[0]:.1%} – {ci[1]:.1%} |")
        band = section.get("drift_band")
        if band:
            lines.append(f"| instrument drift | ±{section['drift_points']:.0%} "
                         f"→ {band[0]:.1%} – {band[1]:.1%} |")
        lines.append(f"| turns judged | {section['turns']} "
                     f"({section['not_an_information_need']} needed no "
                     f"information) |")
        exc = section.get("excluded") or {}
        for k, v in exc.items():
            lines.append(f"| excluded — {k.replace('_', ' ')} | {v} |")
        if section.get("grader_kappa") is not None:
            lines.append(f"| grader agreement (κ) | {section['grader_kappa']} |")
        if section.get("artifact_age_days") is not None:
            lines.append(f"| last run | {section['artifact_age_days']:.0f} "
                         f"days ago |")
        lines.append("")
    else:
        lines += [f"**Not measurable.** {section.get('note', '')}", ""]

    if section.get("excluded_note"):
        lines += ["> [!NOTE]", f"> {section['excluded_note']}", ""]
    if section.get("arm_note"):
        lines += ["> [!NOTE]", f"> {section['arm_note']}", ""]
    if section.get("stale_note"):
        lines += ["> [!WARNING]", f"> **STALE.** {section['stale_note']}", ""]
    if section.get("ci_uninformative"):
        lines += ["> [!NOTE]", f"> {section['ci_note']}", ""]
    lines += ["> [!IMPORTANT]",
              f"> **UNVALIDATED.** {section['validation_note']}", ""]
    return lines
