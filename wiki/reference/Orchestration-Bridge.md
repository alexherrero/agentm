# Orchestration bridge reference

`phase_dispatch()` in [`scripts/harness_memory.py`](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py) is the orchestration bridge. You use this single write-capable entry point from a plugin. It fires an auto-orchestration chain through the kernel. It is the V5-5 `[LC-3]` counterpart to the read-only process seam. The seam crosses the memory↔process boundary for reads. The bridge crosses it for writes. The bridge routes writes through the kernel. You never write directly to the state file.

## ⚡ Quick Reference

| | |
|---|---|
| **Entry point** | `phase_dispatch(*, phase, project_root=None, dry_run=False)` |
| **CLI** | `python3 scripts/harness_memory.py phase-dispatch <phase> [--project-root DIR] [--dry-run]` |
| **Valid phases** | `post-work`, `post-release` (locked in the `_BRIDGE_PHASES` frozenset in `harness_memory.py`) |
| **Return** | `int` — always `0` (non-blocking, graceful-skip) |

> [!IMPORTANT]
> **Write-capable, kernel-single-writer.** The bridge fires a chain. This chain writes the auto-orchestration state file. The write happens **inside the kernel** (`orchestration_phase.py` → `auto_orchestration.ao.save_state()`). The bridge never touches `auto-orchestration-state.json` directly. The kernel is the sole writer ([LC-2]).

## Contract properties

| Property | Value |
|---|---|
| **Non-blocking** | Always returns 0. A failed orchestration chain never wedges a phase. |
| **Graceful-skip** | Returns 0 with no side-effects when the vault is absent or the memory toolkit is not installed. |
| **Kernel-single-writer** | All state writes go through `ao.save_state()` inside the kernel, never through the bridge. |
| **One-way direction** | Plugins call in; the kernel never calls back out through the bridge ([LC-8]). The toolkit scripts (`harness/skills/memory/scripts/`) must not import `harness_memory` — enforced by the `check-one-way-imports` gate's `lc8-bridge` rule (CONS-1 merged the former standalone `check-process-seam-import-direction` script into this config-driven checker). |

## `phase_dispatch(*, phase, project_root=None, dry_run=False)`

You fire a named phase chain through the kernel core.

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

This chain fires after a `/work` phase session ends. The chain discovers the session's start-marker file (`.harness/session-id-<id>.start`):

| Marker state | Result |
|---|---|
| None found | `status: no-session` — graceful no-op. |
| Multiple found | `status: ambiguous-session` — concurrency-safe no-op. |
| Exactly one | Runs the reflect chain scoped to that session's transcript (`--route` flag). |

### `post-release` chain

This chain fires after a `/release` phase session ends. It runs `index_skills.py`. It then runs `discover_skills.py`. These runs refresh the skill surfaces.

## CLI

```bash
# Dry-run — print the dispatch plan without executing
python3 scripts/harness_memory.py phase-dispatch post-work --project-root . --dry-run

# Fire the post-release chain
python3 scripts/harness_memory.py phase-dispatch post-release --project-root .
```

The CLI `choices=` setting mirrors `_BRIDGE_PHASES`. The argument-parser layer only accepts valid phase names.

## Related

- [Auto-orchestration](Auto-Orchestration) — This explains why the push surface exists. You find the three trigger owners in the V5-5 section.
- [Process seam](Process-Seam) — This is the read-only sibling for memory↔process reads.
- [Memory↔process seam](Memory-Process-Seam) — The bridge extends this one-way dependency philosophy to writes.
- [CI gates](CI-Gates) — The `check-one-way-imports` gate includes the `lc8-bridge` rule. This rule enforces the one-way LC-8 direction. This is a V5-5 bridge extension. CONS-1 merged the former standalone `check-process-seam-import-direction` script into this checker. You run `verify-phases` for session-marker scenario integration checks.
- [AgentM HLD — V5 unbundling](agentm-hld) — This unbundling prompted this bridge. It represents a DC-1 deferred slice.
