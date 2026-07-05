#!/usr/bin/env python3
"""check_model_bump.py — the model-upgrade regression trigger
(PLAN-r3-uplift-scoring task 4 / R3.2b).

The harness's own principle ("re-audit the harness whenever the underlying
model ships a new version" — harness/principles.md) applied here as an
automated trigger: when the default model configured for a session bumps,
the D-⑫ probe battery re-runs outside its normal weekly cadence, instead of
waiting for the schedule to catch up.

`model_bumped()` is a pure function — no I/O, no live model calls — so it's
testable with a scripted config-diff (Task 4's own verification #3: "a
scripted config-diff test, not a live model swap"). The CLI wrapper persists
the last-seen model to scripts/health/probes/last-probed-model.txt and, on a
detected bump, invokes `trigger_fn` (default: run_live.main with
trigger="model-upgrade") — injectable so a caller (or a test) never has to
make a live model call to exercise the trigger path.

Usage:
    python3 scripts/health/probes/check_model_bump.py --current-model <model>
Exit:
    0  ran (bump detected and battery triggered, or no bump — both are success)
    1  the triggered battery itself failed (non-zero from trigger_fn)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
LAST_MODEL_PATH = HERE / "last-probed-model.txt"


def model_bumped(previous: str | None, current: str) -> bool:
    """True iff `current` differs from `previous`. A `previous` of None
    (first run ever, no state file yet) is NOT a bump — there's nothing to
    compare against, so the first run just seeds the state file."""
    if previous is None:
        return False
    return previous.strip() != current.strip()


def read_last_probed_model() -> str | None:
    if not LAST_MODEL_PATH.is_file():
        return None
    text = LAST_MODEL_PATH.read_text(encoding="utf-8").strip()
    return text or None


def write_last_probed_model(model: str) -> None:
    LAST_MODEL_PATH.write_text(model.strip() + "\n", encoding="utf-8")


def main(argv: list[str] | None = None, *,
         trigger_fn: Callable[[], int] | None = None,
         state_reader: Callable[[], str | None] = read_last_probed_model,
         state_writer: Callable[[str], None] = write_last_probed_model) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--current-model", required=True, help="the currently configured default model")
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    previous = state_reader()
    bumped = model_bumped(previous, args.current_model)

    if bumped:
        print(f"check-model-bump: default model changed ({previous!r} -> {args.current_model!r}) "
              f"— triggering the D-12 probe battery outside its weekly cadence", file=sys.stderr)
        if trigger_fn is None:
            try:
                from . import run_live as _run_live  # package context
            except ImportError:
                sys.path.insert(0, str(HERE))
                import run_live as _run_live  # type: ignore  # script context
            trigger_fn = lambda: _run_live.main(["--model", args.current_model, "--trigger", "model-upgrade"])
        rc = trigger_fn()
        if rc != 0:
            print(f"check-model-bump: triggered battery exited {rc}", file=sys.stderr)
            state_writer(args.current_model)
            return 1
    elif previous is None:
        print(f"check-model-bump: no prior state — seeding {args.current_model!r} (routine cadence only)",
              file=sys.stderr)
    else:
        print(f"check-model-bump: no model change ({previous!r} == {args.current_model!r}) — routine cadence only",
              file=sys.stderr)

    state_writer(args.current_model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
