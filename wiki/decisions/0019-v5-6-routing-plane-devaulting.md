<!-- mode: decision -->
# ADR 0019 — V5-6 routing-plane de-vaulting: `resolve_project`, `repo_registry`, and `state_mode` route through the storage seam

> [!NOTE]
> **Status:** accepted
> **Date:** 2026-06-18

## Context

[ADR 0018](0018-v5-3-storage-cutover.md) — V5-3 — completed the data-plane cutover: `harness_state_dir`, `read_state_file`, `write_state_file`, `phase_recall`, and `resolve_documenter_context` became device-local only. The kernel's *routing* layer — slug-to-path resolution, the cross-repo index, and the state-mode resolver — was explicitly left vault-coupled in V5-3 (that ADR's "which parts of the kernel's vault-touching surface area are 'state I/O' (removed) vs. 'routing and indexing' (retained)" call). The three retained mechanisms all held a `vault_path()` call in their hot path:

- `resolve_project` / `_vault_projects_dir` (`harness_memory.py:318,339`) — built `vault_path() / "projects" / slug / …` paths directly.
- `repo_registry` (`repo_registry.py`) — hardcoded `<vault>/_meta/repos.json` via a `registry_path(vault_path) -> Path` entry-point.
- `_read_config_state_mode` (`harness_memory.py:228`) — stored the non-local mode value as the string `"vault"`, coupling the conceptual category to a concrete store name.

A fresh install with only the `device-local` backend could write harness state (V5-3), but could not host a project (slug resolution), register a repo (repo_registry), or read harness-state-mode config (state_mode was `"vault"` or `"local"` — no `"backend"` value existed). V5-6 closes all three.

**Open questions this decision resolves:**

- When should `resolve_project` / `_vault_projects_dir` stop building `vault_path() / …` paths and speak `Locator`s instead?
- Should `repo_registry` ride the active backend or keep its own device-local file?
- What is the right non-local value for `state_mode` now that vault is a plugin concern, not a kernel concept?
- How do existing `state_mode: vault` entries in operator device configs survive the rename without operator action?
- How do the existing gate scripts extend to enforce the routing-layer invariants going forward?

## Decision

### 1. `resolve_project` / `_vault_projects_dir` speak `Locator`s (task 1)

`_vault_projects_dir` signature changes from `(vault: Path) -> Path` to `(backend: StorageBackend) -> Locator`. It calls `backend.resolve("projects")` (with `"personal-projects"` fallback via `backend.exists`). `resolve_project` is fully rewritten: it lazy-imports `backend_selection.select_backend()` (circular-import guard — `backend_selection` imports `harness_memory`), then calls `_vault_projects_dir(backend)` and returns `{slug, project_locator, backend, project_root, layout}` — `project_locator` is a `Locator`, not a `Path`. Callers that previously received a `vault_path: Path` receive a `project_locator: Locator` and use seam verbs (`backend.read` / `backend.write`).

**Behavior-preserving (LC-1):** on the `obsidian-vault` backend, `backend.resolve("projects", slug)` → `Locator("projects/slug")` maps to `<vault>/projects/slug` — the same bytes at the same path as before V5-6 (proved by the LC-7 parallel-run assertion before vault-shaped path construction was removed).

### 2. `repo_registry` rides the active backend (task 2, LC-4)

The hardcoded `<vault>/_meta/repos.json` path is replaced by `registry_locator(backend: StorageBackend) -> Locator`, which calls `backend.resolve("_meta", "repos.json")`. On `obsidian-vault` this resolves to the same path (behavior-preserving, LC-1); on `device-local` it resolves to `~/.agentm/memory/_meta/repos.json`. All five public functions (`read_registry`, `write_registry`, `register_repo`, `unregister_repo`, `list_repos`) take `backend: StorageBackend` instead of `vault_path: Path`.

`write_registry` performs an explicit content-hash CAS check before `backend.write()`. `_mutate_registry` no longer holds `vault_mutex` directly — `VaultBackend.write()` holds it internally; the CAS retry loop handles cross-device races.

### 3. `state_mode: vault` aliases to `state_mode: backend` at read time (task 3, LC-5)

The non-local harness-state mode value is renamed from `"vault"` to `"backend"`. The transition is a **one-line alias at read time** in both `_read_config_state_mode` and `_read_project_mode`: any `"vault"` value in `~/.agentm-config.json` or `<repo>/.harness/.project-mode` is returned as `"backend"` without rewriting the file. The canonical value in `agentm_config._STATE_MODES` is `"backend"`; `"vault"` is retained as a deprecated CLI alias and normalized to `"backend"` at write time. No operator action is required.

`resolve_documenter_context` closes by construction (LC-6) — it already returned `None` after V5-3, so no change was needed.

### 4. Gate extensions + conformance suite prove the routing invariants go forward (task 4)

- `check-storage-seam-no-path-leak.py` — Pass 2: scans `harness_memory.py` and `repo_registry.py` for the eight named routing functions; fails if any return `pathlib.Path`.
- `check-process-seam-import-direction.sh` — LC-8 block: scans the same two files for `import storage_vault` / `from storage_vault import`; fails if a routing mechanism imports the capability plugin instead of the seam's abstract types.
- `storage_conformance.py` — `check_routing_repo_registry(make_backend)` proves the register/list/unregister cycle on any conforming backend; gated by `run_conformance(include_routing=False)` (default off); `RoutingConformanceReport` exercises both `DeviceLocalBackend` and `VaultBackend`.

## Consequences

**Positive:**

- A fresh install with only the `device-local` backend can now host a project, register a repo, and run the full phase workflow without an Obsidian vault. The three-leg de-vaulting arc (data plane V5-3, config plane V5-7 partial, routing plane V5-6) is complete.
- `state_mode: vault` — present in every operator's `~/.agentm-config.json` written before V5-6 — continues to work transparently. The alias requires zero operator action.
- The routing layer is backend-agnostic: swapping the backing store (device-local ↔ vault) does not require routing-function changes — the backend is injected at call time.
- Two gate scripts now enforce the routing invariants: no `Path` leaks out of routing functions; routing mechanisms never import capability plugins. Regressions fail loudly in CI.

**Negative:**

- Callers of `resolve_project` that previously received a `vault_path: Path` receive a `project_locator: Locator` and must use seam verbs. The interface change is breaking for direct callers (there were two: `process_seam.py` and `harness_memory.py`'s own callers `_invoke_toolkit_save` and `offer_save` — both updated in task 1).
- `repo_registry` functions that previously took `vault_path: Path` now take `backend: StorageBackend`. Callers updated: `project_config.is_registered`, `project_config.register`, CLI commands.
- The no-path-leak gate now fails on a routing function that returns `Path` — any future helper that returns a `Path` from `harness_memory.py` or `repo_registry.py` must either rename to avoid the routing function name set or return a `Locator`.

**Load-bearing assumptions with re-audit triggers:**

- *`VaultBackend.write()` holds `vault_mutex` internally.* `_mutate_registry` removed its direct `vault_mutex` hold on this assumption. Re-audit if `VaultBackend.write()` is ever refactored to not hold the mutex internally.
- *The `"local"` state-mode value and its routing behavior are unchanged.* The alias logic only normalizes `"vault"` → `"backend"`. Re-audit if `"local"` ever needs a rename.
- *Cross-machine `repo_registry` coherence is device-specific (`root_path` is device-local).* This is a pre-existing property deferred to V8. Re-audit when V8 multi-agent / cross-machine support is designed.
- *`process_seam.py` callers of `resolve_project` use `project_locator.key` as the target string.* Re-audit if `process_seam.py:161` is refactored to use a different field.

## Related

- [ADR 0013 — Memory↔storage seam: backend selection fails loud](0013-storage-seam-fail-loud-selection.md) — the V5-1 seam that V5-6 routing wires into.
- [ADR 0018 — V5-3 storage cutover: device-local is canonical](0018-v5-3-storage-cutover.md) — the data-plane cutover V5-6 completes at the routing layer.
- [Seam-De-Vaulting-V5-6 explanation](../explanation/Seam-De-Vaulting-V5-6) — intent and per-task implementation trace.
- [Storage-Seam reference § Routing layer](../reference/Storage-Seam#routing-layer-v5-6) — the reference detail for the routing mechanisms.
- [ADR 0009 — On-host state-mode config](0009-on-host-state-mode-config) — the original `state_mode` decision; V5-6 amends the non-local value from `"vault"` to `"backend"`.
