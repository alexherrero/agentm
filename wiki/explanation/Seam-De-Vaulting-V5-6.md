# Seam de-vaulting — routing layer (V5-6)

> [!NOTE]
> **Status: complete** (2026-06-18)
> Plan: `.harness/PLAN.md` (V5-6 — Seam De-Vaulting, routing layer onto the storage seam)
> Decision: [ADR 0019](../decisions/0019-v5-6-routing-plane-devaulting)

The third and final leg of the V5 de-vaulting arc: re-plumb the kernel's routing/index mechanisms so they speak `Locator`s to the V5-1 storage seam instead of building `vault_path() / …` filesystem paths directly. After this plan, a fresh install with only the `device-local` backend can host a project, its harness state, and the repo registry without needing an Obsidian/GDrive vault.

**Three-leg de-vaulting arc:**

| Leg | Plan | Surface | Status |
|---|---|---|---|
| Data plane | V5-3 | `harness_state_dir` / `read_state_file` / `write_state_file` / `phase_recall` / `resolve_documenter_context` | Shipped (v5.5.0, ADR 0018) |
| Config plane | V5-7 (partial) | `agentm_config`, `vault_path()` fail-loud guard | Partially shipped |
| Routing plane | V5-6 (this) | `resolve_project` / `_vault_projects_dir` — **task 1 shipped**; `repo_registry` — **task 2 shipped**; `state_mode` — **task 3 shipped**; **gate extensions + conformance suite — task 4 shipped**; **ADR + docs — task 5 shipped** | **Complete** |

## Intent

Make the kernel's routing layer backend-agnostic. Three mechanisms hard-code `vault_path()` path construction today; all three route through the seam after this plan:

- `resolve_project` / `_vault_projects_dir` in `harness_memory.py` — project-slug-to-path resolution.
- `repo_registry` in `repo_registry.py` — the `_meta/repos.json` cross-repo index.
- `state_mode` resolver / `resolve_documenter_context` — the harness-state-location selector and its documenter-context reader.

Behavior-preserving (LC-1): on the `obsidian-vault` backend every mechanism resolves to identical bytes in identical locations. A per-thread parallel-run assertion proves this before vault-shaped path construction is removed.

## Design

### Resolve_project / _vault_projects_dir (task 1)

`harness_memory.py:339` (`resolve_project`) and `:318` (`_vault_projects_dir`) currently build paths as `vault_path() / "projects" / slug / …`. After this plan they call `backend.resolve("projects", slug, …)` and callers that received a `Path` receive a `Locator` and use seam verbs (`backend.read` / `backend.write`). Slug producers (`vault_project`, `detect_project`) are confirmed vault-agnostic (LC-2) and unchanged.

### Repo_registry onto the seam (task 2)

`repo_registry.py` currently hardcodes `<vault>/_meta/repos.json` at line 68. All five public functions (`read_registry`, `write_registry`, `register_repo`, `unregister_repo`, `list_repos`) re-point to address `_meta/repos.json` through the active backend. On `obsidian-vault` the file lands in the same place; on `device-local` it lives on-device. The `vault_path` parameter threaded through registry functions is replaced by active-backend resolution.

### state_mode vault→backend alias (task 3)

`_read_config_state_mode` at `harness_memory.py:228` is renamed: the non-local state-mode value is `backend` rather than `vault`. Existing `state_mode: vault` entries in device config and `.harness/.project-mode` markers **alias to `backend` at read time** — a one-line mapping, no rewrite, no operator action required (LC-5). `resolve_documenter_context` inherits correct routing by construction once reads go through the seam.

### Gate extensions + conformance suite (task 4)

- `check-storage-seam-no-path-leak.sh` extended: fails if `resolve_project`, `_vault_projects_dir`, or repo_registry read/write functions return `pathlib.Path`.
- `check-process-seam-import-direction.sh` extended: verifies de-vaulted mechanisms import the seam but never a capability plugin (LC-8).
- `storage_conformance.py` extended: routing layer parameterized over both concrete backends — `resolve_project` + `repo_registry` + harness-state resolution must produce identical outcomes on both backends for the same slug/key.

## Implementation trace

### Task 1 — Namespace de-vaulting: `resolve_project` / `_vault_projects_dir` (shipped)

`harness_memory._vault_projects_dir` signature changed from `(vault: Path) -> Path` to `(backend: StorageBackend) -> Locator`. The function now calls `backend.resolve(_VAULT_PROJECTS_REL_NEW)` (the `"projects"` segment) and `backend.resolve(_VAULT_PROJECTS_REL_LEGACY)` (the `"personal-projects"` fallback), using `backend.exists()` to pick the active namespace. Returns a `Locator` instead of a `Path` — the no-path-leak gate constraint.

`harness_memory.resolve_project` completely rewritten. The return dict now carries `{slug, project_locator, backend, project_root, layout}` instead of the old `{slug, vault_path, project_root, layout}`. The backend is obtained via a lazy `import backend_selection as _bs; backend = _bs.select_backend()` to avoid the circular-import that a top-level import would cause (since `backend_selection` itself imports `harness_memory`).

Callers updated:

- `process_seam.py` line 161: `enriched["target"]` now uses `project_locator.key` instead of `str(vault_path)`.
- `harness_memory.py` callers `_invoke_toolkit_save` and `offer_save` that previously used `_vault_projects_dir(vault).name` were updated to inline segment detection.
- `memory_mcp_tools.py` line 263: segment detection inlined (no longer calls `_vault_projects_dir`).

Tests in `TestVaultProjectsDir` and `TestResolveProject` updated to use the `Locator` return type and mock-based backend injection.

**Behavior-preserving (LC-1):** on the `obsidian-vault` backend, `backend.resolve("projects", slug)` → `Locator("projects/slug")` maps to `<vault>/projects/slug` — the same bytes at the same path as the pre-V5-6 `vault_path` field.

### Task 2 — `repo_registry` onto the seam (shipped)

`repo_registry.py` fully rewritten. The old `registry_path(vault_path) -> Path` entry-point is replaced by `registry_locator(backend: StorageBackend) -> Locator` ([`repo_registry.py#L77`](https://github.com/alexherrero/agentm/blob/main/scripts/repo_registry.py#L77)), which calls `backend.resolve(*_REGISTRY_PARTS)` — on the `obsidian-vault` backend this resolves to `<vault>/_meta/repos.json` (behavior-preserving, LC-1); on `device-local` it resolves to `~/.agentm/memory/_meta/repos.json`.

`_vault_or_none()` replaced by `_backend_or_none()` ([`#L87`](https://github.com/alexherrero/agentm/blob/main/scripts/repo_registry.py#L87)) — lazy-imports `backend_selection.select_backend()` to avoid the circular import a top-level import would cause. All five public functions (`read_registry`, `write_registry`, `register_repo`, `unregister_repo`, `list_repos`) now take `backend: StorageBackend` instead of `vault_path: Path`.

`write_registry` ([`#L126`](https://github.com/alexherrero/agentm/blob/main/scripts/repo_registry.py#L126)) does an explicit content-hash CAS check before delegating to `backend.write()`. `_mutate_registry` ([`#L198`](https://github.com/alexherrero/agentm/blob/main/scripts/repo_registry.py#L198)) no longer holds `vault_mutex` directly — `VaultBackend.write()` handles it internally; the CAS retry loop handles cross-device correctness. The `vault_mutex` removal is captured in a doc comment at `#L211`.

`project_config.is_registered` takes `backend=None` instead of `vault_path=None` ([`project_config.py#L151`](https://github.com/alexherrero/agentm/blob/main/scripts/project_config.py#L151)). CLI graceful-skip fires when `select_backend()` raises (backend plugin unavailable), not merely when `MEMORY_VAULT_PATH` is unset.

**Tests:** 9 new/rewritten tests in `TestRepoRegistry` ([`test_harness_memory.py#L2110`](https://github.com/alexherrero/agentm/blob/main/scripts/test_harness_memory.py#L2110)) using the `DeviceLocalBackend` pattern. `TestRepoRegistryCLI.test_list_skipped_when_backend_unavailable` ([`#L2297`](https://github.com/alexherrero/agentm/blob/main/scripts/test_harness_memory.py#L2297)) replaces `test_list_skipped_when_no_vault`. LC-7 parallel-run test at `#L2265` confirms `VaultBackend` `registry_locator` maps to `<vault>/_meta/repos.json` — same on-disk path as before V5-6. 20/20 `check-all.sh`.

### Task 3 — `state_mode` vault→backend alias + `resolve_documenter_context` (shipped)

`harness_memory._read_config_state_mode` ([`harness_memory.py#L228`](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py#L228)) now maps `"vault"` → `"backend"` at read time via a one-line guard at [`#L258-L260`](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py#L258). No config rewrite, no operator migration — existing `state_mode: vault` entries in `~/.agentm-config.json` continue to resolve correctly (LC-5).

`harness_memory._read_project_mode` ([`#L511`](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py#L511)) applies the same alias for the per-repo `.harness/.project-mode` marker at [`#L536-L537`](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py#L536): a file containing the string `"vault"` now returns `"backend"`. The device-level fallback path at `#L540` delegates to `_read_config_state_mode`, which already normalizes.

`agentm_config._STATE_MODES` ([`agentm_config.py#L51`](https://github.com/alexherrero/agentm/blob/main/scripts/agentm_config.py#L51)) adds `"backend"` as the canonical value; `"vault"` is retained as a deprecated CLI alias. `agentm_config.cmd_set_state_mode` ([`#L125`](https://github.com/alexherrero/agentm/blob/main/scripts/agentm_config.py#L125)) normalizes `"vault"` → `"backend"` at write time at [`#L142-L144`](https://github.com/alexherrero/agentm/blob/main/scripts/agentm_config.py#L142) and the CLI `--state-mode` help text at [`#L244-L245`](https://github.com/alexherrero/agentm/blob/main/scripts/agentm_config.py#L244) surfaces the deprecation note.

`resolve_documenter_context` ([`harness_memory.py#L1030`](https://github.com/alexherrero/agentm/blob/main/scripts/harness_memory.py#L1030)) closes LC-6 by construction — it already returns `None` after V5-3, so no change was needed.

**Tests:** 3 new tests in `TestReadConfigStateMode` ([`test_harness_memory.py#L1299`](https://github.com/alexherrero/agentm/blob/main/scripts/test_harness_memory.py#L1299)): `test_reads_backend` (#L1299), `test_reads_vault_aliases_to_backend` (#L1304), and `test_vault_and_backend_produce_identical_resolution` (#L1310 — the dedicated alias-equivalence test required by plan). 2 updated tests in `TestAgentmConfig` ([`test_agentm_config.py#L338`](https://github.com/alexherrero/agentm/blob/main/scripts/test_agentm_config.py#L338)): `test_set_state_mode_backend_writes_field_rc0` and `test_set_state_mode_vault_normalizes_to_backend` (#L345). `verify-phases` green (phase lifecycle exercises `state_mode`). 20/20 `check-all.sh`.

### Task 4 — Gate extensions + conformance suite for routing layer (shipped)

**`check-storage-seam-no-path-leak.py` — Pass 2 (V5-6):** A second scan pass is added that targets the two routing files (`harness_memory.py` and `repo_registry.py`) and checks seven named routing functions (`resolve_project`, `_vault_projects_dir`, `registry_locator`, `read_registry`, `write_registry`, `register_repo`, `unregister_repo`, `list_repos`) for any `pathlib.Path` return annotation. The scan is driven by a new `ROUTING_FUNCTIONS` set, a `_ROUTING_FILENAMES` constant, a `_routing_files()` collector, and an updated `_scan_source(names=…)` kwarg. Two new tests in `PathLeakGate` cover the negative (a Path-returning routing function fails) and positive (non-routing helpers in routing files are not caught) cases.

**`check-process-seam-import-direction.sh` — LC-8 block:** A new scan block (LC-8) checks `harness_memory.py` and `repo_registry.py` for any `import storage_vault` or `from storage_vault import` statement and fails loudly if found — enforcing that a routing mechanism may import the seam but never a capability plugin. Three new tests in `ImportDirectionGate`: import-form failure, from-import-form failure, and positive test that non-routing files are not caught.

**`storage_conformance.py` — routing checks:** `check_routing_repo_registry(make_backend)` proves the `repo_registry` register/list/unregister cycle works on any conforming backend. Added to a new `ROUTING_CHECKS` tuple, surfaced as `test_routing_repo_registry()` on `ConformanceSuite`, and gated by a new `include_routing=False` kwarg on `run_conformance()`. Three new tests in `RoutingConformanceReport` in `test_storage_conformance.py` exercise `run_conformance(include_routing=True)` on both `DeviceLocalBackend` and `VaultBackend`.

**Verification:** 20/20 `check-all.sh` PASS.

### Task 5 — ADR amendment + docs refresh (shipped)

ADR 0019 created ([`wiki/decisions/0019-v5-6-routing-plane-devaulting.md`](../decisions/0019-v5-6-routing-plane-devaulting.md)) recording the three-leg de-vaulting arc completion, all four locked design calls (LC-1/4/5/6/7/8), and the load-bearing assumptions with re-audit triggers. `Decisions.md` and `decisions/_Sidebar.md` updated.

`wiki/designs/device-wide-architecture.md` v1.0 lifecycle entry updated from *pending* to *2026-06-18 complete* with all four task commit SHAs and ADR 0019 cross-link.

`wiki/reference/Storage-Seam.md` routing layer NOTE block updated from *in progress* to *complete* with ADR 0019 link.

`wiki/explanation/Single-Repo-State-Mode.md` "V5-6 pending" callout updated to "shipped 2026-06-18"; `state_mode` value description updated from `"local" | "vault"` to `"local" | "backend"` with backward-compat note.

**Verification:** `check-wiki --strict` green.

## Notes

- **Out of scope:** V5-6 narrative-shed (docs/prose identity rewrite — separate plan), PM slim (gated on crickets github-projects plugin), `auto_orchestration` 3-way split (V5-5), `agentm_config` / `detect_project` / `vault_project` slug-resolution (already vault-agnostic), V5-7 full config model.
- **Kernel stays the OS map (LC-1):** routing mechanisms are not moved to a plugin — they remain kernel-resident, now speaking `Locator`s.
- **One-way import direction (LC-8):** de-vaulted mechanisms may import the seam, never a capability plugin.
- **Related:** [Storage-Seam reference](../reference/Storage-Seam), [Single-Repo-State-Mode explanation](Single-Repo-State-Mode), [ADR 0018 — V5-3 storage cutover](../decisions/0018-v5-3-storage-cutover.md).
