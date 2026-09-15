"""Suite-wide hermetic guards.

Every test runs with every variable `engine_state_isolation` governs pointed
at a fresh temporary directory of its own — the engine state directory, the
cache root, the recall ledger and the device-local root — so no test, present
or future, can read or write the machine's real `~/.local/state/agentm`,
`~/.cache/agentm` or `~/.agentm/memory` by forgetting to override them, and no
test sees another's leftovers. The vault already has this discipline via
`$MEMORY_ROOT` fixtures; the engine's directories get it here, once, for the
whole suite. Function-scoped on purpose: a shared session directory would
make state-dir tests order-dependent.

Which variables move is `engine_state_isolation.GOVERNED`'s to say. The
fixture once named two of the four itself, so from any suite that never asked
for isolation a pytest run reached the operator's `~/.agentm/memory/_meta`
and `~/.cache` by the same paths the battery's runner did. A test that
genuinely needs a specific directory sets the variable inside its own scope
and wins; the outer environment is put back when the test ends.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import engine_state_isolation  # noqa: E402


@pytest.fixture(autouse=True)
def _hermetic_engine_state(tmp_path_factory):
    base = tmp_path_factory.mktemp("engine-state")
    previous = engine_state_isolation.redirect(base)
    try:
        yield base / "state"
    finally:
        engine_state_isolation.restore(previous)
