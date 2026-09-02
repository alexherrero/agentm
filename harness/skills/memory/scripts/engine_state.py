#!/usr/bin/env python3
"""engine_state.py — where the memory family keeps machine state.

Filing-v2 part 2a moved machine state out of the vault: caches, cursors,
pointer files, journals — everything a corpus scan cannot rebuild but no
human browses in Obsidian. The vault is the knowledge surface both audiences
read; this directory is the engine's own, and it is a **git repository** (the
2a migration initializes it, the runner commits it on the vault's own
cadence), so the durability the old `_meta/` files got from vault history
moved here with them.

This is a deliberate, tiny vendored copy of `scripts/harness_memory.py`'s
`engine_state_dir()` — the memory-skill scripts don't carry `scripts/` on
their path, and a fifteen-file sys.path dance is worse than four duplicated
lines pinned by a parity test (`scripts/test_engine_state_parity.py`, the
house pattern for exactly this seam).

`$AGENTM_STATE_DIR` is the per-invocation override tests and CI use — the
same contract `$MEMORY_VAULT_PATH` holds for the vault. Creation is the
caller's mkdir, not this resolver's side effect.
"""
from __future__ import annotations

import os
from pathlib import Path


def engine_state_dir() -> Path:
    override = os.environ.get("AGENTM_STATE_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".local" / "state" / "agentm"
