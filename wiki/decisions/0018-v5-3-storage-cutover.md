<!-- mode: decision -->
# ADR 0018 — V5-3 storage cutover: delete vault backend from kernel, device-local is canonical

> [!NOTE]
> **Status:** accepted
> **Date:** 2026-06-18

## Context

[ADR 0013](0013-storage-seam-fail-loud-selection.md) — the V5-1 storage seam — established the backend interface, shipped selection and fail-loud refusal, but was explicit that **the engine cutover was a separate, later step** (DC-6): the kernel functions `read_state_file` / `write_state_file` / `harness_state_dir` / `phase_recall` / `resolve_documenter_context` were left byte-unchanged, still reading and writing vault state the old way. Two negative consequences in that ADR were named-pending-V5-3:

- *"The fail-loud guard mostly sleeps until V5-3. Until the built-in vault backend is removed, both built-ins register at import, so the guard only fires for a genuinely-unregistered third-party name."*
- *"The engine cutover is still pending — selection resolving a backend is not the same as the engine using it."*

V5-3 is that cutover. It also ships the long-planned `personal-private/` → `personal/` vault-area rename that ADR 0010's first re-audit trigger specified (see ADR 0010 Amendment 2026-06-18 below).

**Open questions this decision resolves:**

- When should the kernel's state functions switch from vault-routing to device-local-only?
- What is the right failure mode when `storage.backend=vault` is configured but the vault is gone?
- Where exactly should the fail-loud guard live — at selection time (`select_backend()`) or at the kernel choke point (`vault_path()`)?
- Does the `$MEMORY_VAULT_PATH` env override get the same loud-failure treatment as the config-file branch?
- Which parts of the kernel's vault-touching surface area are "state I/O" (removed) vs. "routing and indexing" (retained)?
- When should the `personal-private/` → `personal/` rename ship?

## Decision

### 1. `harness_state_dir`, `read_state_file`, `write_state_file` are device-local only (DC-1)

After V5-3, `harness_state_dir(resolution)` always returns `<project_root>/.harness/` — never a vault path. `read_state_file` and `write_state_file` read and write only from that device-local directory. The prior vault-routing tier (checking `<vault>/projects/<slug>/_harness/<filename>` before `<project>/.harness/<filename>`) is deleted; the legacy-warn deprecation path that fired on a device-local read alongside a vault copy is deleted with it.

**Why device-local only now:** the V5-9 MCP memory server provides a clean process boundary for vault-side recall; embedding vault I/O in the kernel was always a V4 transitional arrangement, not a design destination. Removing it completes the unbundling ADR 0011 set in motion. The one-warn-per-session-per-file deprecation was the signal that operators had migrated; deleting it closes the migration.

**Why not an expand→contract cutover:** the vault-backed harness-state path (`.harness/` → `<vault>/_harness/`) was already deprecated in V4.1.0 (v0.3 HLD entry) and behind the `.project-mode=vault` / `state_mode=vault` guard. Operators on `local` mode (the V5 default and the only mode any new install starts in) were already on device-local paths. A two-phase cutover would protect zero live operators while adding weeks of transitional complexity.

### 2. `phase_recall` always returns `""` — context is now V5-9 MCP (DC-2)

`phase_recall(phase, project, ...)` validates the phase name (raises `ValueError` for unknown phases, preserving the existing caller contract) and returns `""`. It no longer reaches into the vault to assemble a recall context bundle.

**Why the empty-string return, not a removal:** `phase_recall` is part of the frozen DC-7 process-seam surface; processes that call it get a no-op result, not an import error or AttributeError. The V5-9 MCP server (`agentm recall`) replaces the recall capability at the process boundary; callers opt in to the new surface rather than having the old one yanked.

**Why not preserve vault reads under a flag:** the vault reads were always "load this context before the phase starts," which is exactly what the MCP server's SessionStart hook does — only richer and without coupling the kernel to a vault path. Adding a flag would be a half-measure that kept the vault coupling alive.

### 3. `resolve_documenter_context` always returns `None` (DC-3)

`resolve_documenter_context(slug)` returns `None` unconditionally. `documenter_context(slug)` still functions (it calls `resolve_documenter_context` and gets `None` → returns `("", 1)`, the existing vault-unreachable exit code), so callers already handling the `rc=1` path continue to work.

**Why None not removal:** same frozen-surface reasoning as DC-2. `rc=1` is the documented vault-unavailable signal — callers on the crickets documenter already fall back to repo-local context on that code. This change makes the V4 fallback path the only path, consistently.

### 4. `vault_path()` raises `StorageBackendNotInstalledError` when `storage.backend=vault` + no vault (DC-4)

The fail-loud guard moves upstream into `vault_path()` itself, making it load-bearing: if `storage.backend=vault` is set in the install config but no vault path resolves, `vault_path()` raises `StorageBackendNotInstalledError` before returning `None`. Every call site that would have silently received `None` and continued with a device-local fallback now gets a loud, actionable error naming the misconfiguration and the fix.

**Why in `vault_path()`, not in `select_backend()`:** placing the guard in `vault_path()` means it fires on *every call path* that reaches vault resolution, not only through the explicit `select_backend()` selection chain. It also avoids a circular import — `harness_memory.py` does not import `backend_selection.py` (that direction crosses the process seam). The choke-point location is the correct one per the "one policy, one place" principle of ADR 0013 DC-3.

**Why `StorageBackendNotInstalledError` defined in `harness_memory.py`:** to avoid circular imports, the error class lives in the module where it is raised, not in `backend_selection.py`. `backend_selection.py` can catch it by importing `harness_memory` (which it already does); the reverse direction would introduce a cycle.

### 5. `$MEMORY_VAULT_PATH` env override remains a graceful-skip (DC-5)

The `$MEMORY_VAULT_PATH` branch of `vault_path()` is not subject to the fail-loud guard. An env override that points to a non-existent directory returns `None` silently. The guard fires only on the config-file branch (`storage.backend=vault` in `.agentm-config.json`).

**Why the asymmetry:** `$MEMORY_VAULT_PATH` is a per-session escape hatch — set at session start, not a durable config commitment. A Drive mount not yet visible at session start is the paradigmatic case: the env var may be set in a startup file but the volume may not have appeared yet. Silent-skip preserves the "try if available" semantics the escape hatch was designed for. A config-file `storage.backend=vault` is a durable commitment that *must* be honored or loudly refused; a transient env override is not.

### 6. The routing/index layer is retained (DC-6)

`resolve_project`, `_vault_projects_dir`, slug-resolution (`_normalize_slug`), `repo_registry`, `detect_project`, `agentm_config` — the full LC-1 surface — is unchanged. These functions resolve *identities* (which project is this? what vault does this device have?) rather than reading or writing *state*. They continue to work even when no vault-backed state is being read or written, and they are the correct primitives for the V5-9 MCP server and the V5-3 fail-loud guard to call.

**Why retain: not every vault-touching function is state I/O.** The routing layer's vault calls are *probes* (does a vault exist? where is it?) not state reads. Deleting them would break the fail-loud guard itself (which needs `vault_path()` to probe) and orphan the MCP server's slug-resolution.

### 7. `personal-private/` → `personal/` rename shipped in lockstep with V5-3 (DC-7)

The vault top-level area `personal-private/` renames to `personal/` in the same release. All three rename targets (vault on-disk, `_ALWAYS_LOAD_REL` constant in `harness_memory.py`, and every functional path reference in `recall.py`, `heat_policy.py`, `vault_probe.py`, `memory_mcp_tools.py`, and the scripts tree) ship in a single coordinated commit per LC-5 of the V5-3 plan. This closes ADR 0010's first load-bearing assumption re-audit trigger.

**Why in lockstep:** a multi-commit rename that leaves `personal-private` in some constants and `personal` in others, even briefly in `main`, would force a gap window where vault reads silently miss the always-load entries. The three-part coordinated change — vault directory, kernel constant, functional refs — makes the cutover atomic from the operator's perspective.

## Consequences

**Positive**

- **The kernel has no vault state I/O.** The unbundling ADR 0011 set out to make the vault a swappable backing; V5-3 completes that separation at the kernel level. `harness_memory.py` now holds only device-local state ops and identity/routing.
- **The fail-loud guard is load-bearing.** A machine configured `storage.backend=vault` with no vault accessible fails loud on `vault_path()` — not silently, not at `select_backend()` only, but at every call site that would have produced a wrong `None`. The ADR 0013 negative consequence "the guard mostly sleeps until V5-3" is resolved.
- **The vault-backed harness state deprecation is closed.** The one-warn-per-session-per-file path is deleted; any remaining vault-only operator can read the error and re-run `agentm_config --vault-path`. No operator was on a vault-exclusive path that would have silently split state.
- **`personal-private/` → `personal/` is done.** The shorter name lands alongside the cutover, reducing friction for the remaining vault-touching surface.

**Negative**

- **`phase_recall` is a no-op until V5-9 MCP is installed.** Callers that relied on `phase_recall` for context (phase-start recall bundles) get empty strings if they haven't migrated to the MCP surface. The correct migration is `agentm recall <phase>` via the V5-9 MCP server.
- **`resolve_documenter_context` / `documenter-context` CLI returns empty.** The crickets `documenter` sub-agent, `wiki-author`, and `diataxis-author` skills all handle `rc=1` with a graceful skip — they already had vault-unreachable fallback paths. The MCP context surface is richer and doesn't require the bundle to be pre-assembled at the CLI call.
- **The vault-backed harness state path is unrecoverable without a data-migration step.** Operators who still have live `<vault>/_harness/` state and were relying on the kernel to read it must run `migrate-harness-to-vault.sh --rollback` or copy files to `<project>/.harness/` manually. No automated migration ships in V5-3 (the V4.1.0 migration tooling covered the forward path; a rollback aid for the backward path would be a follow-on).

**Load-bearing assumptions (with re-audit triggers)**

- **V5-9 MCP server is the recall replacement.** The kernel's `phase_recall` → `""` is safe only because the MCP server provides equivalent context through the process boundary. **Re-audit trigger:** the V5-9 MCP server is removed or its recall tool is removed — then `phase_recall` is a no-op with no substitute, which would silently degrade all recall-dependent phases. If that happens, restore a minimal recall path in the kernel.
- **`storage.backend=vault` in config is always a durable commitment.** The fail-loud guard fires for config-file `vault` + no vault. This assumption holds as long as the config file is an explicit operator action, not auto-populated. **Re-audit trigger:** any installer or migration tool that auto-writes `storage.backend=vault` into `.agentm-config.json` without also ensuring a vault path is accessible — then the guard would fire on a correct install.
- **The device-local `.harness/` path is stable.** All state I/O now converges on `<project_root>/.harness/`. **Re-audit trigger:** a move of the harness state dir (e.g. to `<project_root>/.agentm/`) — then update `harness_state_dir` and all callers.
- **The routing/index layer (LC-1) remains read-only with respect to state.** `resolve_project`, `_vault_projects_dir`, etc. probe for vault identity but do not write state. **Re-audit trigger:** any proposal to write state through the routing layer — route that through the seam instead.

## Amendment — 2026-06-19 (ADR 0020 reverses DC-1)

**DC-1 is reversed by [ADR 0020](0020-backend-aware-harness-state.md).** DC-1 made `harness_state_dir` / `read_state_file` / `write_state_file` device-local *only* — always `<project_root>/.harness/`, never a vault path. That choice broke the surfacing of synced harness state: on a machine with a synced backend active (`storage.backend=vault`), the operator's PLAN.md / ROADMAP.md / progress.md live in `<vault>/projects/<slug>/_harness/`, but the kernel's state functions read the empty device-local `.harness/` instead — so the SessionStart hook never injected them.

ADR 0020 makes those three functions **backend-aware**: they route through the storage seam to `<vault>/projects/<slug>/_harness/` when a *live synced backend* is active (discriminated by `backend.capabilities.sync`, not by the presence of a `vault_path` config key), and **gracefully degrade** to device-local `<project_root>/.harness/` when no synced backend resolves (vault absent, fresh install, or a `.harness/.project-mode=local` opt-out). This is not a return to the pre-V5-3 vault-routing tier DC-1 deleted: it routes through the V5-6 `resolve_project` seam rather than the old `vault_path()` probe-and-prefer ordering, and the `.project-mode=local` opt-out still wins over a synced backend.

**DC-2 through DC-7 stand unchanged.** `phase_recall` → `""` (DC-2), `resolve_documenter_context` → `None` (DC-3), the `vault_path()` fail-loud guard (DC-4), the `$MEMORY_VAULT_PATH` graceful-skip (DC-5), the retained routing/index layer (DC-6), and the `personal-private/` → `personal/` rename (DC-7) are all untouched. Only the device-local-only state-I/O decision (DC-1) is reversed; the load-bearing assumption *"the device-local `.harness/` path is stable / all state I/O converges on `<project_root>/.harness/`"* is the one ADR 0020 updates.

## Related

- [ADR 0020 — Backend-aware harness state](0020-backend-aware-harness-state.md) — reverses DC-1: state I/O is backend-aware again (synced backend → vault, else device-local), routed through the V5-6 seam. See the Amendment above.
- [ADR 0013 — The memory↔storage seam](0013-storage-seam-fail-loud-selection.md) — established the seam and explicitly deferred the engine cutover (DC-6); this ADR closes that deferral. ADR 0013's two "negative" V5-3 bullets are resolved by this ADR.
- [ADR 0011 — V5 unbundling](0011-v5-unbundling-dev-loop.md) — set the direction of "memory engine is what only agentm provides"; V5-3 completes the kernel-side separation.
- [ADR 0010 — Vault internal taxonomy](0010-vault-internal-taxonomy.md) — its first re-audit trigger (the `personal-private/` → `personal/` rename) fires in this release; see the Amendment 2026-06-18 in that ADR.
- [ADR 0009 — On-host state-mode config](0009-on-host-state-mode-config.md) — the `state_mode` field + `$MEMORY_VAULT_PATH` env override contract that the fail-loud guard (DC-4/DC-5) distinguishes between.
- [Memory↔storage seam](../explanation/Memory-Storage-Seam.md) — the narrative explanation; updated at V5-3 to reflect the engine cutover is complete.
- [Single-repo state mode](../explanation/Single-Repo-State-Mode.md) — the operator-facing state mode explanation.
