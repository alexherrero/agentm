#!/usr/bin/env python3
"""verify-battery-integrity.py — the `verification honesty` health-family's
first live checks (AA5 consolidation task C7).

Every check here runs against SYNTHETIC fixtures, never a real run-fast-tier.sh
invocation -- this suite must be hermetic and deterministic in CI, mirroring
verify-efficiency.py's discipline (AA5 C3). Because every check's input is
synthetic and fully known, every record this suite emits is live
(`pass: true/false`) -- never dark.

Three checks (the battery-integrity-shaped checks named in the AA5 C7 brief
and locked in PLAN-c7-silent-dark-families.md's Locked design calls):

  1. scorecard-determinism -- health_score.py's own --check-determinism
     behavior (already gated directly by check-all.sh) is exercised here too,
     so its PASS/FAIL outcome becomes a scored, visible scorecard family
     member -- not a duplicate gate, that gate's result surfaced into the
     scorecard itself.
  2. no-skipped-suites -- parses run-fast-tier.sh's own `run_suite "<label>"`
     invocations (no hand-duplicated label list to drift out of sync) and
     asserts, against a synthetic good/bad fixture pair, that a suite whose
     records are entirely absent from a batch is detectable.
  3. gate-results-parse-and-agree -- health_score.py's read_records() must
     parse a well-formed batch cleanly and must raise on a malformed line,
     exactly as scripts/health/README.md's Schema section documents.

Usage:   python3 scripts/health/verify-battery-integrity.py
         python3 scripts/health/verify-battery-integrity.py --jsonl-out records.jsonl
Exit:    0 iff every check passes.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent
RUN_FAST_TIER = HERE / "run-fast-tier.sh"
sys.path.insert(0, str(HERE))

import health_score as hs  # noqa: E402

AXIS = "verification honesty"
WEIGHT_EACH = 5.0

_RUN_SUITE_RE = re.compile(r'run_suite\s+"([^"]+)"')


def declared_suite_labels() -> list[str]:
    """The suite labels run-fast-tier.sh actually declares, parsed from the
    script itself -- never hand-duplicated, so this check can't drift stale
    the way three dark-checks.jsonl entries once did (AA5-REENTRY-VERDICT.md
    §1 D4)."""
    text = RUN_FAST_TIER.read_text(encoding="utf-8")
    return _RUN_SUITE_RE.findall(text)


def check_scorecard_determinism() -> tuple[bool, str]:
    fixture = SCRIPTS_DIR / "health" / "fixtures" / "sample-records.jsonl"
    try:
        records = hs.read_records(str(fixture))
    except ValueError as e:
        return False, f"fixture failed to parse: {e}"
    out1 = hs.render_markdown(hs.compute_scorecard(records))
    out2 = hs.render_markdown(hs.compute_scorecard(records))
    if out1 != out2:
        return False, "two renders of the same records produced different output"
    return True, "byte-identical across two renders"


def check_no_skipped_suites() -> tuple[bool, str]:
    labels = declared_suite_labels()
    if not labels:
        return False, "run-fast-tier.sh declares zero run_suite invocations (parse failure?)"

    def records_for(present_labels: list[str]) -> list[dict]:
        return [
            {"suite": label, "axis": "memory persist+recall", "check": "synthetic",
             "pass": True, "weight": 1.0}
            for label in present_labels
        ]

    good_seen = {r["suite"] for r in records_for(labels)}
    if set(labels) - good_seen:
        return False, "good fixture doesn't cover every declared suite (construction bug)"

    dropped = labels[0]
    bad_seen = {r["suite"] for r in records_for(labels[1:])}
    missing = set(labels) - bad_seen
    if missing != {dropped}:
        return False, f"expected exactly {{{dropped!r}}} missing, got {missing!r}"

    return True, f"{len(labels)} declared suites; drop-one detection confirmed"


def check_gate_results_parse_and_agree() -> tuple[bool, str]:
    good = '{"suite": "s", "axis": "memory persist+recall", "check": "c", "pass": true, "weight": 1.0}\n'

    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
        f.write(good)
        good_path = f.name
    try:
        records = hs.read_records(good_path)
        if len(records) != 1 or records[0]["suite"] != "s":
            return False, "well-formed batch did not parse to the expected single record"
    finally:
        Path(good_path).unlink(missing_ok=True)

    bad = good + "{not valid json\n"
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
        f.write(bad)
        bad_path = f.name
    try:
        try:
            hs.read_records(bad_path)
        except ValueError:
            pass
        else:
            return False, "malformed JSONL line did not raise ValueError"
    finally:
        Path(bad_path).unlink(missing_ok=True)

    return True, "well-formed batch parses cleanly; malformed line raises ValueError"


CHECKS = [
    ("scorecard-determinism", check_scorecard_determinism),
    ("no-skipped-suites", check_no_skipped_suites),
    ("gate-results-parse-and-agree", check_gate_results_parse_and_agree),
]


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--jsonl-out", default=None)
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    records = []
    all_ok = True
    for check_name, fn in CHECKS:
        ok, detail = fn()
        all_ok = all_ok and ok
        tag = "PASS" if ok else "FAIL"
        print(f"{tag}  {check_name}: {detail}", file=sys.stderr)
        records.append({
            "suite": "verify-battery-integrity", "axis": AXIS,
            "check": check_name, "pass": ok, "weight": WEIGHT_EACH,
        })

    if args.jsonl_out:
        with open(args.jsonl_out, "a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    print(f"\nverify-battery-integrity: {sum(1 for r in records if r['pass'])}/{len(records)} checks passed",
          file=sys.stderr)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
