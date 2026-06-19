<!-- mode: decision -->
# ADR 0020 — Backend-aware harness state: route `harness_state_dir` / `read_state_file` / `write_state_file` through the storage seam

> [!NOTE]
> **Status:** accepted
> **Date:** 2026-06-19

## Context

[ADR 0018](0018-v5-3-storage-cutover.md) (V5-3 storage cutover, DC-1) made the three kernel state functions — `harness_state_dir`, `read_state_file`, `write_state_file` — **device-local only**: they always targeted `<project_root>/.harness/`, never a vault path. The reasoning was that embedding vault I/O in the kernel was a V4 transitional arrangement and the V5-9 MCP server provides a clean process boundary for vault-side *recall* (DC-2/DC-3).

That reasoning was sound for **recall** (read-only context bundles assembled at phase start). It was **wrong for harness execution state** (`PLAN.md`, `progress.md`, `ROADMAP.md`, the active-plan binding). Those are the durable, cross-device working set a synced vault exists to hold. DC-1 conflated the two: it device-localized *state* on the strength of an argument that only applied to *recall*.

The concrete failure: a project explicitly configured `storage.backend=vault` (e.g. `dev-setup`) keeps its plan/roadmap/progress in `<vault>/projects/<slug>/_harness/`. After V5-3, every state read resolved `<repo>/.harness/` instead — which holds no plan — so the session-start hook surfaced nothing, `/queue-status-lite` saw an empty board, and the operator's roadmap was invisible at session start despite being correctly filed in the vault.

Between V5-3 and now, [ADR 0019](0019-v5-6-routing-plane-devaulting.md) (V5-6) shipped the **routing plane**: `resolve_project` returns `{slug, project_locator, backend, project_root, layout}`, where `backend` is the selected `StorageBackend` (the obsidian-vault plugin's `VaultBackend` when `storage.backend=vault` resolves, else a `DeviceLocalBackend`, else `None`). The seam needed to route state correctly now exists — DC-1 simply predates it.

This ADR amends ADR 0018 DC-1 only. DC-2 (`phase_recall` → `""`), DC-3 (`resolve_documenter_context` → `None`), DC-4/DC-5 (the `vault_path()` fail-loud guard vs. the `$MEMORY_VAULT_PATH` graceful-skip), DC-6 (routing layer retained), and DC-7 (the `personal/` rename) all stand unchanged.

**Open questions this decision resolves:**

- Should harness *execution state* follow the same device-local rule V5-3 applied to *recall*, or route through the V5-6 backend?
- What is the discriminator that decides vault vs. device-local — the live backend, the legacy `vault_path` resolution, or the config?
- How does state routing interact with the `.project-mode=local` / `state_mode=local` opt-out (ADR 0009, #44 DC-2/DC-8)?
- How does the change still **gracefully degrade on a machine with no vault** (the load-bearing property DC-1's device-local floor provided for free)?
- Does routing writes through the backend preserve the V5-0 vault-write safety stack?
- The session-start hook hardcodes `progress.md` to the device-local `.harness/` — does that need to change too?

## Decision

### 1. The three state functions are backend-aware, not device-local-only (DC-1)

`harness_state_dir`, `read_state_file`, and `write_state_file` consult the active backend carried in the resolution:

- **synced backend present** (`resolution["backend"].capabilities.sync is True`, plus a `project_locator`) → state targets `<vault>/projects/<slug>/_harness/<file>`, reached through the backend verbs. `harness_state_dir` returns the resolved vault directory; `read_state_file` calls `backend.read(...)`; `write_state_file` calls `backend.write(...)`.
- **otherwise** (no backend, a device-local `sync=False` backend, a backend missing a `root`, or a `.project-mode=local` opt-out) → state stays device-local at `<project_root>/.harness/<file>`, exactly as DC-1 left it.

A single private helper, `_state_backend_target(resolution)`, encodes the discriminator and returns either `(backend, harness_locator, backend_root)` or `None`; all three public functions branch on it, so the routing rule lives in one place.

**Why route state, not keep it device-local:** harness execution state is the working set a synced vault is *for* — the operator edits a plan on one machine and continues on another; `/queue-status-lite` and the session-start hook must see the same board the vault holds. DC-1's device-local floor silently partitioned that state per-checkout, which is the bug this fixes.

**Why not revert ADR 0018 wholesale:** V5-3's recall/documenter separation (DC-2/DC-3) and the fail-loud guard (DC-4/DC-5) are correct and orthogonal. Only DC-1 — the *state-dir* device-local hardcode — was the error. Reverting more would re-couple the kernel to vault *recall*, which the V5-9 MCP server now owns. This amendment is deliberately surgical.

**Why duck-typed on `capabilities.sync`, not `isinstance(backend, VaultBackend)`:** `harness_memory.py` must not import the obsidian-vault plugin — the process-seam import-direction gate forbids it, and the plugin isn't installed on every host. The backend advertises `capabilities.sync`; that flag *is* the contract ("this backend replicates across devices"). A backend that can't answer its capabilities is treated as untrusted and degrades to device-local rather than risking a wrong-tree write.

### 2. The discriminator is the live synced backend, never the legacy `vault_path` key (DC-2)

Routing keys off `resolution["backend"].capabilities.sync`, **not** a `vault_path` entry in the resolution and **not** `harness_memory.vault_path()`. A resolution that carries only a stale `vault_path` key (the pre-V5-6 shape) with no `backend` reads and writes device-local.

**Why not the `vault_path` key / `vault_path()`:** [ADR 0019](0019-v5-6-routing-plane-devaulting.md) removed `vault_path` from the resolution shape, and V5-7 removed implicit vault *selection* from a config-supplied `vault_path` (a configured path is not the same as a chosen backend). Honoring a bare path would resurrect that implicit inference and route state to a vault the selection chain deliberately did not pick — the exact split-brain ADR 0018 DC-4/DC-5 guard against. The backend object is the single source of truth for "is a synced store actually active right now."

### 3. The `.project-mode=local` opt-out beats a synced backend (DC-3)

`_state_backend_target` checks `_read_project_mode(resolution) == "local"` **first** and returns `None` (device-local) when set — before it ever looks at the backend. The repo-local `<project_root>/.harness/.project-mode` marker and the device-level `state_mode=local` config both win over a synced backend.

**Why local wins:** this preserves the #44 DC-2/DC-8 contract — a per-repo or per-device opt-out is an explicit operator choice to keep one checkout's state off the shared vault (a throwaway worktree, a machine that shouldn't sync). A backend being *available* must not override an operator's *deliberate* opt-out. Local is the authoritative override, the backend is the default.

### 4. Graceful degradation is preserved through `resolve_project` (DC-4)

On a machine with no vault, the chain degrades without special-casing: `select_backend` resolves `device-local` (a `sync=False` backend) on a fresh install, and `resolve_project` catches a raising `select_backend` (e.g. `storage.backend=vault` configured but the vault gone → `StorageBackendNotInstalledError` per ADR 0018 DC-4) and returns `backend=None`. Either way `_state_backend_target` returns `None` and state falls to `<project_root>/.harness/`.

**Why this satisfies "degrades on a machine with no vault":** the degradation is a property of the discriminator (`backend is None or not capabilities.sync → device-local`), not a separate code path that could rot. There is no configuration under which a vault-absent machine routes state anywhere but device-local. The device-local floor DC-1 provided unconditionally is now provided *conditionally on the absence of a synced backend* — same floor, reached through the seam.

**Why not raise when the vault is configured-but-absent:** the loud failure already lives upstream in `vault_path()` (ADR 0018 DC-4) for the durable `storage.backend=vault` commitment. `read_state_file` additionally swallows a per-file `FileNotFoundError` from the backend to `""` (a missing plan is "no plan," not an error) and logs any other backend read error to stderr before degrading — a state read must never crash session boot.

### 5. Writes route through `backend.write()`, preserving the V5-0 safety stack (DC-5)

`write_state_file` on a synced backend calls `backend.write(locator, content)`, which composes the full V5-0 vault-write protocol: `vault_mutex` (fleet-local writer serialization) + content-hash CAS (catches a non-mutex writer — Drive sync, another device — landing between read and rename) + atomic temp→fsync→rename. The device-local branch keeps the lighter `atomic_write`-only path (each checkout owns its `.harness/`; nothing to lock).

**Why not raw path I/O at the resolved vault path:** writing the vault directly with `Path.write_text` would bypass the mutex and CAS, reintroducing the torn-write and lost-update hazards [ADR 0012](0012-vault-write-protocol.md) exists to prevent, and would leak a vault `Path` across the seam in violation of the no-path-leak gate's routing-function contract. Routing through the verb keeps the one canonical write discipline.

### 6. The session-start hook co-locates `progress.md` with the resolved plan (DC-6)

`harness-context-session-start.sh` / `.ps1` derived `progress.md` from a hardcoded `<event_cwd>/.harness/` (V5-3 device-local). When `list-plans` (which routes through `harness_state_dir`) resolves `PLAN.md` to the vault, the hardcoded device-local `progress.md` does not exist, so the locked DC-7 singleton-injection branch — which requires *both* files present — silently skipped, and the operator's state stayed invisible even with DC-1 fixed.

The fix: when a singleton `PLAN_PATH` is resolved, derive `progress.md` as its sibling (`dirname "$PLAN_PATH"/progress.md`), so it co-locates with whatever `_harness/` the bridge resolved (vault or device-local); fall back to the device-local path only when no plan was resolved. Both the bash and PowerShell twins change in lockstep (parity rule). The locked DC-7 4-line output block is unchanged — it already prints the full resolved paths.

**Why co-locate rather than re-resolve in the hook:** the hook is a thin shell shim; the resolution authority is `harness_state_dir` in Python, reached via `list-plans`. `progress.md` is by construction a sibling of `PLAN.md` in the same `_harness/`, so taking `dirname` of the resolved plan reuses the Python resolution without duplicating the backend logic in shell.

## Consequences

**Positive**

- **Vault-configured projects surface their state again.** A `storage.backend=vault` project reads and writes `PLAN.md` / `progress.md` / `ROADMAP.md` / the active-plan binding from the vault. The session-start hook injects the vault paths and `/queue-status-lite` sees the real board — the V5-3 regression is closed.
- **Cross-device continuity is restored** for harness execution state, which is the synced vault's purpose. Editing a plan on one machine and continuing on another works again.
- **Degradation is structural, not a special case.** No-vault and vault-absent-but-configured machines route state device-local via the same discriminator that routes vault machines to the vault. One rule, both directions.
- **Write safety is preserved.** Vault writes flow through `vault_mutex` + CAS + atomic_write; the no-path-leak gate stays green (the state functions are not routing-functions under the gate and may return `Path`, while the actual vault I/O goes through the backend verb).

**Negative**

- **State location now depends on backend selection, which depends on config + environment.** A project's state lives in the vault only when a synced backend is actually selected (`storage.backend=vault`, or `$MEMORY_VAULT_PATH` set). A machine where the vault is configured only via the inert config `vault_path` key but `storage.backend` is unset and `$MEMORY_VAULT_PATH` is unexported will resolve device-local — correct per V5-7, but a foot-gun for an operator who expects the config `vault_path` alone to route. (This is the `MEMORY_VAULT_PATH` non-login-shell wiring gap tracked as dev-setup roadmap DS-2.)
- **A stale device-local `.harness/PLAN.md` is now shadowed by the vault copy** on a synced-backend machine. If both exist, the vault wins; the device-local copy becomes dead. No automated cleanup ships here — the device-local copy is harmless but can mislead a `cat <repo>/.harness/PLAN.md`.
- **The kernel is re-coupled to the backend for state** (though not to the *plugin* — duck-typed). DC-1's "kernel has no vault state I/O" property is partially walked back: the kernel has no *recall* I/O (DC-2/DC-3 stand) but does route *state* through the injected backend.

**Load-bearing assumptions (with re-audit triggers)**

- **`capabilities.sync` means "route harness state here."** The discriminator equates a synced backend with "the right home for execution state." **Re-audit trigger:** a future backend that is `sync=True` but is *not* an appropriate state home (e.g. an encrypted-blob or object-store backend where `_harness/<file>` paths don't make sense) — then the discriminator needs a more specific capability than `sync`.
- **`resolve_project` always swallows a raising `select_backend` to `backend=None`.** DC-4's degradation relies on this. **Re-audit trigger:** any change that lets a backend-selection error propagate out of `resolve_project` — then a vault-absent-but-configured machine would crash session boot instead of degrading.
- **The `project_locator` is `projects/<slug>` and `_harness/` is its child.** `harness_state_dir` builds `backend_root / *project_locator.child("_harness").parts`. **Re-audit trigger:** a vault layout change that moves `_harness/` out from under `projects/<slug>/` — then update `_state_backend_target`.
- **`list-plans` is the hook's only resolution authority.** DC-6's co-location assumes `PLAN_PATH` comes from `harness_state_dir` via `list-plans`. **Re-audit trigger:** the hook gaining a second plan-discovery path that doesn't route through the bridge — then `progress.md` co-location could point at the wrong dir.

## Related

- [ADR 0018 — V5-3 storage cutover](0018-v5-3-storage-cutover.md) — **amended by this ADR** (DC-1 only; see the Amendment 2026-06-19 in that ADR). DC-2 through DC-7 stand.
- [ADR 0019 — V5-6 routing-plane de-vaulting](0019-v5-6-routing-plane-devaulting.md) — shipped the `resolve_project` → `{backend, project_locator, …}` shape this ADR routes through.
- [ADR 0013 — The memory↔storage seam](0013-storage-seam-fail-loud-selection.md) — the seam, capabilities, and the no-path-leak gate whose routing-function contract this change respects.
- [ADR 0012 — The vault-write protocol](0012-vault-write-protocol.md) — the `vault_mutex` + CAS + atomic_write stack DC-5 preserves by routing writes through `backend.write()`.
- [ADR 0009 — On-host state-mode config](0009-on-host-state-mode-config.md) — the `state_mode` / `.project-mode` opt-out DC-3 honors above a synced backend.
- [Single-repo state mode](../explanation/Single-Repo-State-Mode.md) — operator-facing state-mode explanation.
