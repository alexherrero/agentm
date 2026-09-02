"""Suite-wide hermetic guards.

Every test runs with `$AGENTM_STATE_DIR` pointed at its own fresh temporary
directory, so no test — present or future — can read or write the machine's
real engine state at `~/.local/state/agentm` by forgetting to override it,
and no test sees another's engine-state leftovers. The vault already has this
discipline via `$MEMORY_VAULT_PATH` fixtures; the engine state dir
(filing-v2 part 2a) gets it here, once, for the whole suite. Function-scoped
on purpose: a shared session directory would make state-dir tests
order-dependent. A test that genuinely needs a specific state dir sets the
variable inside its own scope and wins (monkeypatch restores this default
afterward either way).
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _hermetic_engine_state_dir(tmp_path_factory, monkeypatch):
    state = tmp_path_factory.mktemp("engine-state")
    monkeypatch.setenv("AGENTM_STATE_DIR", str(state))
    yield state
