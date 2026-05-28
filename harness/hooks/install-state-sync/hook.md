---
name: install-state-sync
description: "SessionStart hook that detects drift between recorded settings.json fragments and current source SHAs; re-merges divergent fragments silently. Covers source-mode (live propagation of operator edits in source clone) AND release-mode (--update fragment refresh). Per V4 #30 plan #22 task 6."
kind: hook
supported_hosts: [claude-code]
version: 0.1.0
install_scope: user
---

# install-state-sync — settings fragment digest probe + re-merge

## What it does

On every SessionStart, walks the `fragments` list in `<install-prefix>/.agentm-install-state.json`. For each tracked fragment:

1. Compute the SHA256 of the source file at the recorded `path`.
2. Compare to the SHA256 recorded in install-state.
3. If they match: no-op (idempotent).
4. If they differ: re-merge the fragment into `<install-prefix>/settings.json` via `merge-settings-fragment.py` + update the recorded SHA in install-state.

Both install modes are covered by the same hook:

- **Source mode** (operator has `~/Antigravity/<repo>/` clone): the recorded fragment path points at the clone (e.g. `~/Antigravity/crickets/hooks/<name>/settings-fragment-bash.json`). Operator edits the fragment in the clone → SHA drifts → re-merge picks up the edit on the next session start. **Auto-stay-in-sync** by default per the locked FOLLOWUPS semantics (2026-05-27) — no flag, no manual step.

- **Release mode** (release-install operator; no clones): the recorded path points at the install-prefix copy (e.g. `<install-prefix>/share/agentm/hooks/<name>/settings-fragment-bash.json`). When `agentm-update` refreshes the copy, the SHA drifts → re-merge applies the new fragment.

## Design rationale — why one hook for both modes

The merge-vs-skip decision is purely SHA-based; install mode (source vs release) doesn't change the contract. Tracking the recorded SHA + recomputing on every session start gives the same semantics in both modes:
- Source-mode operator never thinks about it (edits propagate next session).
- Release-mode operator runs `agentm-update`; new fragments take effect next session.
- The hook is the single point of truth for "did this fragment change?"

## Non-blocking + graceful-skip

- **Exit 0 always.** Hook failures (missing fragment, malformed JSON, merge error) emit a one-line stderr notice but never freeze session start.
- **Graceful-skip:** if `<install-prefix>/.agentm-install-state.json` is absent (pre-V4 #30 install) or has no `fragments` field, the hook exits 0 silently. Operators upgrading from v4.2.x see zero behavior change until they run the new installer.
- **Stdout suppressed in hook mode** (`--quiet`) so settings.json stay clean from JSON noise; transparency comes from stderr re-merge notice when work happens.

## How it's invoked

Registered on `SessionStart` (matcher `.*` — fires on startup/resume/clear/compact). Settings fragment:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "bash .claude/hooks/install-state-sync/install-state-sync.sh",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

Both bash + pwsh variants invoke the same Python helper (`scripts/install_state_sync.py`) — there's no platform-specific divergence in the SHA + merge logic.

## When does the recorded SHA get set?

The installer records SHAs at install time. When `install_state.persist_install_state()` is called with `fragments=[{path, sha256}, ...]`, the SHAs are baked into install-state.json. The hook then sees the recorded SHAs and detects drift on subsequent session starts.

Task 8 (lib/install parity) wires the installer to populate `fragments` automatically as part of the hook-install dispatch. Until task 8, this hook is a no-op when running against installs that pre-date `fragments` tracking.

## Cross-references

- `scripts/install_state.py` — the install-state schema (now supports `fragments` field per task 6)
- `scripts/install_state_sync.py` — the hook's Python helper
- `scripts/merge-settings-fragment.py` — invoked via subprocess to actually merge
- V4 #30 plan #22 task 6 + DC-8 (settings.json fragments stay as copies; this hook keeps them current)
- FOLLOWUPS entry 2026-05-27 — auto-stay-in-sync default-on
