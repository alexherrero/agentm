# Orchestration bridge reference

The orchestration bridge — `phase_dispatch()` in [`scripts/harness_memory.py`](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py) — is the single write-capable entry point a plugin uses to fire an auto-orchestration chain through the kernel. It is the V5-5 `[LC-3]` counterpart to the read-only process seam: the seam crosses the memory↔process boundary for reads; the bridge crosses it for writes (always through the kernel, never directly to the state file).

## ⚡ Quick Reference

| | |
|---|---|
| **Entry point** | `phase_dispatch(*, phase, project_root=None, dry_run=False)` |
| **CLI** | `python3 scripts/harness_memory.py phase-dispatch <phase> [--project-root DIR] [--dry-run]` |
| **Valid phases** | `post-work`, `post-release` (locked in `_BRIDGE_PHASES` at line 1576) |
| **Return** | `int` — always `0` (non-blocking, graceful-skip) |

> [!IMPORTANT]
> **Write-capable, kernel-single-writer.** The bridge fires a chain that writes the auto-orchestration state file, but that write happens **inside the kernel** (`orchestration_phase.py` → `auto_orchestration.ao.save_state()`). The bridge never touches `auto-orchestration-state.json` directly. The kernel is the sole writer ([LC-2]).

## Contract properties

| Property | Value |
|---|---|
| **Non-blocking** | Always returns 0. A failed orchestration chain never wedges a phase. |
| **Graceful-skip** | Returns 0 with no side-effects when the vault is absent or the memory toolkit is not installed. |
| **Kernel-single-writer** | All state writes go through `ao.save_state()` inside the kernel, never through the bridge. |
| **One-way direction** | Plugins call in; the kernel never calls back out through the bridge ([LC-8]). The toolkit scripts (`harness/skills/memory/scripts/`) must not import `harness_memory` — enforced by the `check-process-seam-import-direction` gate. |

## `phase_dispatch(*, phase, project_root=None, dry_run=False)`

Fire a named phase chain through the kernel core.

| Parameter | Type | Detail |
|---|---|---|
| `phase` | `str` | Must be a member of `_BRIDGE_PHASES`. `ValueError` on any other value. |
| `project_root` | `str \| None` | Project root for session-marker and state-dir resolution. Defaults to cwd. |
| `dry_run` | `bool` | Print the resolved dispatch plan without executing. Default `False`. |

| Condition | Result |
|---|---|
| Vault absent / no `MEMORY_VAULT_PATH` | Returns 0, no side-effects. |
| Memory toolkit not installed | Returns 0, no side-effects. |
| `phase` not in `_BRIDGE_PHASES` | Raises `ValueError` — a caller bug, distinct from the graceful-skip paths. |
| Normal execution | Fires the chain, records state (kernel-side), returns 0. |
| `dry_run=True` | Prints the resolved plan as JSON (status `"dry-run"`, no writes), returns 0. |

### `post-work` chain

Fires after a `/work` phase session ends. The chain discovers the session's start-marker file (`.harness/session-id-<id>.start`):

| Marker state | Result |
|---|---|
| None found | `status: no-session` — graceful no-op. |
| Multiple found | `status: ambiguous-session` — concurrency-safe no-op. |
| Exactly one | Runs the reflect chain scoped to that session's transcript (`--route` flag). |

### `post-release` chain

Fires after a `/release` phase session ends. Runs `index_skills.py` then `discover_skills.py` to refresh the skill surfaces.

## CLI

```bash
# Dry-run — print the dispatch plan without executing
python3 scripts/harness_memory.py phase-dispatch post-work --project-root . --dry-run

# Fire the post-release chain
python3 scripts/harness_memory.py phase-dispatch post-release --project-root .
```

The CLI `choices=` mirrors `_BRIDGE_PHASES`, so only valid phase names are accepted at the argument-parser layer.

## Related

- [Auto-orchestration](Auto-Orchestration) — why the push surface exists and the three trigger owners (V5-5 section).
- [Process seam](Process-Seam) — the read-only sibling for memory↔process reads.
- [Memory↔process seam](Memory-Process-Seam) — the one-way dependency philosophy this bridge extends to writes.
- [CI gates](CI-Gates) — `check-process-seam-import-direction` (enforces the one-way LC-8 direction, V5-5 bridge extension) and `verify-phases` (session-marker scenario integration checks).
- [ADR 0011 — V5 unbundling](0011-v5-unbundling-dev-loop) — the unbundling that prompted this bridge (DC-1 deferred slice).
