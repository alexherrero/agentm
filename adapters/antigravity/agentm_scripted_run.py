#!/usr/bin/env python3
"""Record an agy conversation a script opened, for the nightly lesson compile.

A script that shells out to `agy -p` (a cross-model code review, a prose pass,
the compile's own digests) sets AGENTM_AGY_RECALL=off. agentm's agy plugin then
runs this instead of the recall at SessionStart: it adds the conversation's id
to `<engine state>/lesson-compile/scripted-agy.jsonl` and nothing to the
conversation. The compile skips a listed conversation while it holds one
request, so a scripted call is never read as a session, and a conversation
someone carries on by hand is still read from its second request on.

It prints nothing (the handler answers agy with `{}` itself) and never fails a
run: any error is swallowed. Stdlib only. Not part of the vendored crickets
adapter beside it; agentm owns this file.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

LEDGER = "scripted-agy.jsonl"


def state_dir() -> Path:
    # The same rule as engine_state.engine_state_dir(), which this plugin copy cannot import.
    override = os.environ.get("AGENTM_STATE_DIR", "").strip()
    base = Path(override).expanduser() if override else Path.home() / ".local" / "state" / "agentm"
    return base / "lesson-compile"


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        conversation = payload.get("conversationId") if isinstance(payload, dict) else None
        if not isinstance(conversation, str) or not conversation.strip():
            return 0
        d = state_dir()
        d.mkdir(parents=True, exist_ok=True)
        row = {"conversation": conversation.strip(),
               "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        with open(d / LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:  # a scripted run must never fail on bookkeeping
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
