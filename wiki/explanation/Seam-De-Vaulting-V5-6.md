# Seam de-vaulting — routing layer (V5-6)

> [!NOTE]
> **Status: pending**
> Plan: `.harness/PLAN.md` (V5-6 — Seam De-Vaulting, routing layer onto the storage seam)
> Created: 2026-06-18

The third and final leg of the V5 de-vaulting arc: re-plumb the kernel's routing/index mechanisms so they speak `Locator`s to the V5-1 storage seam instead of building `vault_path() / …` filesystem paths directly. After this plan, a fresh install with only the `device-local` backend can host a project, its harness state, and the repo registry without needing an Obsidian/GDrive vault.

**Three-leg de-vaulting arc:**

| Leg | Plan | Surface | Status |
|---|---|---|---|
| Data plane | V5-3 | `harness_state_dir` / `read_state_file` / `write_state_file` / `phase_recall` / `resolve_documenter_context` | Shipped (v5.5.0, ADR 0018) |
| Config plane | V5-7 (partial) | `agentm_config`, `vault_path()` fail-loud guard | Partially shipped |
| Routing plane | V5-6 (this) | `resolve_project` / `_vault_projects_dir` — **task 1 shipped**; `repo_registry`, `state_mode` — pending | In progress |

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

_Tasks 2–5 filled by /work once they ship._

## Notes

- **Out of scope:** V5-6 narrative-shed (docs/prose identity rewrite — separate plan), PM slim (gated on crickets github-projects plugin), `auto_orchestration` 3-way split (V5-5), `agentm_config` / `detect_project` / `vault_project` slug-resolution (already vault-agnostic), V5-7 full config model.
- **Kernel stays the OS map (LC-1):** routing mechanisms are not moved to a plugin — they remain kernel-resident, now speaking `Locator`s.
- **One-way import direction (LC-8):** de-vaulted mechanisms may import the seam, never a capability plugin.
- **Related:** [Storage-Seam reference](../reference/Storage-Seam), [Single-Repo-State-Mode explanation](Single-Repo-State-Mode), [ADR 0018 — V5-3 storage cutover](../decisions/0018-v5-3-storage-cutover.md).
