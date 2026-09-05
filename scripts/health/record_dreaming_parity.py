#!/usr/bin/env python3
"""record_dreaming_parity.py — record the Python dreaming layer's decisions over
the parity fixture (`scripts/fixtures/dreaming-parity/vault`), clock pinned.

Filing v2 part 6 (task 4). The Go dreaming binary ports the Python jobs one
by one, and the plan's rule is that parity is asserted against RECORDED
Python outputs — never against expectations the new code recomputes. This
script is the recorder: it runs the three Python producers over the fixture
corpus with the clock pinned and writes `expected.json` beside it.

    python3 scripts/health/record_dreaming_parity.py            # print
    python3 scripts/health/record_dreaming_parity.py --write    # re-record

`scripts/test_dreaming_parity.py` fails when the Python layer's output no
longer matches the recording; `daemon/internal/dreaming/parity_test.go`
fails when the Go port does not. Re-record only on a deliberate change to a
producer — a rewritten fixture during the port is a re-audit trigger.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
_SCRIPTS = _REPO / "harness" / "skills" / "memory" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

FIXTURE = _REPO / "scripts" / "fixtures" / "dreaming-parity"
PIN = "2026-09-05"
NOW = PIN + "T09:00:00+00:00"


class _Rules:
    """The packaged thresholds, without reading any machine's contract."""

    def thresholds(self):
        return {"dormant_after_days": 365, "archive_after_days": 1825}

    def lifecycles(self):
        return ["pinned", "active", "dormant", "archived", "superseded"]


def record(fixture: Path = FIXTURE) -> dict:
    import consolidate
    import dream
    import lifecycle_transitions as lt
    from crystallize import CrystallizationDigest, _render_body

    work = Path(tempfile.mkdtemp(prefix="dreaming-parity-"))
    try:
        vault = work / "vault"
        shutil.copytree(fixture / "vault", vault)
        os.environ["AGENTM_STATE_DIR"] = str(work / "state")
        out: dict = {"pin": PIN}

        pol = lt.policy_pass(vault, now=NOW, rules=_Rules(), apply=False)
        out["lifecycle"] = pol.as_dict()

        entries = dream._iter_entries(vault)
        loaded = dream._load(entries)
        families = []
        for p in dream._stage_suffix_backlog_drain(vault, entries, loaded):
            families.append({
                "canonical": p.paths[0], "copies": p.paths[1:], "summary": p.summary,
                "after": {str(Path(path).relative_to(vault)).replace("\\", "/"): content for path, content in p.mutations},
            })
        out["copies"] = families

        episodic = sorted(
            str(p.relative_to(vault)).replace("\\", "/")
            for p in (vault / "memory" / "episodic").rglob("*.md")
        )
        recurring = consolidate.find_recurring_targets(vault, episodic)
        promote = {}
        for target, sources in sorted(recurring.items()):
            n = len(sources)
            digest = CrystallizationDigest(
                question=f"What recurring reference to {target!r} appears across episodic entries?",
                investigation=(f"{n} episodic entries reference {target!r}:\n" + "\n".join(f"- {p}" for p in sources)),
                findings=(f"{target!r} recurs across {n} distinct entries (recurrence floor: {consolidate.MIN_RECURRENCE}), "
                          "a deterministic signal that this is durable, not incidental."),
                lessons=(f"Promoted episodic -> semantic (V6-4). The consolidated entry is durable (decay-exempt) and carries a "
                         f"derived_from provenance edge back to its {n} sources; none of those sources were deleted or modified."),
                open_threads="",
            )
            promote[target] = {"sources": sources, "slug": consolidate._consolidated_slug(target), "body": _render_body(digest)}
        out["promote"] = promote

        # The calendar rollups (task 5's takeover): the review texts, byte for
        # byte, for a closed week and the month it sits in — rendered from the
        # register in the fixture, nothing written.
        import calendar_rollups
        out["calendar"] = {
            "week": {"2026-W35": calendar_rollups.render_week(vault, 2026, 35)},
            "month": {"2026-08": calendar_rollups.render_month(vault, 2026, 8)},
        }
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None) -> int:
    write = "--write" in (argv or sys.argv[1:])
    out = record()
    text = json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True) + "\n"
    if write:
        (FIXTURE / "expected.json").write_text(text, encoding="utf-8")
        print(f"recorded {FIXTURE / 'expected.json'}")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
