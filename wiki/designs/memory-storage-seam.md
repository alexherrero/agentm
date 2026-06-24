---
title: Memory↔Storage Seam (agentm)
status: launched
seeded: 2026-06-13
approved: 2026-06-21
kind: design
scope: arc
area: agentm/storage
governs:
  - scripts/storage_seam.py
  - scripts/backend_selection.py
  - scripts/harness_memory.py
  - scripts/repo_registry.py
  - scripts/storage_device_local.py
  - scripts/vault_lock.py
  - scripts/vault_backend_stub.py
  - scripts/storage_conformance.py
---

> This is the **living storage-seam design** for agentm. It absorbs six predecessor ADRs retired by the AG Phase 2 fold: **ADR 0009** (on-host state-mode config), **ADR 0012** (vault-write protocol), **ADR 0013** (seam and fail-loud selection), **ADR 0018** (V5-3 storage cutover, DC-1 reversed by ADR 0020), **ADR 0019** (V5-6 routing-plane de-vaulting), and **ADR 0020** (backend-aware harness state, the current truth for state routing). Decision history — rationale, why-not-the-alternative, re-audit triggers — is preserved in the **Amendment log** at the bottom. (AG Phase 2, 2026-06-21; see [Design governance](Design-Governance).)

# Memory↔Storage Seam

The storage seam is agentm's boundary between the memory engine and its backing store. It gives the engine a small, stable set of verbs (`resolve` / `read` / `write` / `list` / `exists` / `info` / `mkdir`) operating on opaque `Locator` handles so the backing store is swappable — the engine never holds a `pathlib.Path` into the store. Selection picks the configured backend on startup; fail-loud refusal prevents silent fall-back to device-local if the configured backend is unavailable.

## Current state (V5.10 / 2026-06-21)

| Layer | What shipped | Module |
|---|---|---|
| Seam interface | Verbs + `Locator` / `Capabilities` / `Info` + `BackendRegistry` | `storage_seam.py` |
| Device-local backend | `~/.agentm/memory/` markdown root; bytes-mode atomic writes | `storage_device_local.py` |
| Vault backend (plugin) | crickets `obsidian-vault` plugin — loaded on demand; kernel built-in **deleted** (V5-3, 2026-06-21) | `crickets/src/obsidian-vault/scripts/storage_vault.py` |
| Vault write protocol | `vault_mutex` + content-hash CAS + atomic `fsync→rename`; vendored to `/memory` skill | `vault_lock.py` |
| Backend selection | 3-step chain: `storage.backend` config → `$MEMORY_VAULT_PATH` env → device-local; fail-loud on misconfiguration; capability-request matching (`required=`) | `backend_selection.py` |
| Routing plane | `resolve_project` returns `{slug, project_locator, backend, …}` in `Locator`s; `repo_registry` rides the active backend; `state_mode: vault` → `backend` read-alias | `harness_memory.py`, `repo_registry.py` |
| Harness state I/O | Backend-aware: synced backend (`capabilities.sync=True`) → vault `_harness/`; else device-local `<project>/.harness/`. `.project-mode=local` opt-out wins over a synced backend | `harness_memory.py` |
| Gate enforcement | No-`Path`-leak (AST, return-type scan); no routing-import of capability plugins (LC-8); routing conformance suite | `check-storage-seam-no-path-leak.py`, `check-process-seam-import-direction.sh`, `storage_conformance.py` |

## Design

### 1. The seam interface — verbs + opaque `Locator`, never `Path`

`scripts/storage_seam.py` defines the seven seam verbs. They operate on, and return, the seam's own `Locator` type — an opaque, backend-relative key with only namespace operations (`child`, `name`, `parts`) — **never a `pathlib.Path`**. All I/O goes back through the verbs; the engine holds no filesystem assumption.

The no-`Path`-leak rule is gate-enforced: `check-storage-seam-no-path-leak.py` parses every `scripts/storage_*.py` file and flags any seam verb whose return annotation is a path type (however nested). A `Path` in a backend's body is fine; handing one back across the seam is the violation, and the return annotation is precisely where that surfaces.

**Why opaque `Locator`, not `Path`:** if a verb returned a `Path`, the leak would be silent and total — downstream code would treat it as a real handle and swapping backends would quietly stop working. The vocabulary mirrors [fsspec](https://filesystem-spec.readthedocs.io/)'s method names and named-protocol registry, but imports nothing external. Bare markdown is the floor.

### 2. Backend registry and selection: fail-loud, never demotes

Selection (`scripts/backend_selection.py`) maps the on-device install config to a concrete, registered backend via a three-step chain:

1. Explicit `storage.backend` value in `.agentm-config.json` → that protocol name.
2. `$MEMORY_VAULT_PATH` env var set (non-empty) → `vault` (per-session escape hatch).
3. Else → `device-local` (fresh-install default).

When the configured backend **cannot be produced**, `select_backend` raises `StorageSelectionError` and **never** falls back silently to `device-local`. Three refusal cases:

- The named backend's plugin is unregistered (`registry.get → None`, vault excluded since it loads on-demand) → `_install_plugin_message` naming the missing backend and the installed alternatives.
- `vault` selected but the obsidian-vault plugin is undiscoverable → `_install_obsidian_vault_message`.
- `vault` selected but no `vault_path` configured to seed it → loud error.
- Install config corrupt or `storage.backend` present-but-not-a-string → loud error.

**Why never demote:** a silent fall-back to `device-local` is the single failure that mis-writes or orphans the vault. A loud refusal forces the operator to fix the install or change the config — always the correct outcome.

**Capability-request matching (V5-7):** `select_backend(required=Capabilities(...))` raises `CapabilityMismatchError` (a `StorageSelectionError` subclass) if the resolved backend doesn't satisfy every `True` flag. `required=None` (default) is backward-compatible. `--requires` in `_doctor_main` is the pre-flight surface, sharing the code path with the runtime guard.

**`storage_preview` mirrors `select_backend`:** the `doctor` storage check calls `storage_preview()` — never-raising, never-mutating — which reuses the resolver's code path and error messages, so the preview an operator sees is byte-identical to the runtime refusal.

**Import cycle guard (§2b DC-4):** `backend_selection.py` lazy-imports `harness_memory` only at call time inside `resolve_project` / `select_backend`, never at module top-level. This is the one guarded lazy import this design permits; no other cross-seam top-level cycles are allowed.

**Vault plugin discovery (post-V5-3):** the kernel `storage_vault.py` was deleted in V5-3 (the kernel built-in) and replaced by the crickets `obsidian-vault` plugin. `_load_vault_plugin_backend` discovers the plugin via `$OBSIDIAN_VAULT_SCRIPTS` override → sibling checkout → plugin-cache path, execs `scripts/storage_vault.py` in the plugin dir, and returns the `VaultBackend` class. The registry slot is always empty at entry (no built-in pre-registers it); the `finally` block pops whatever the plugin registered, leaving the slot empty on exit.

### 3. Backend-aware harness state (ADR 0020 — current truth)

`harness_state_dir`, `read_state_file`, and `write_state_file` are **backend-aware**. A single private helper, `_state_backend_target(resolution)`, encodes the routing rule:

- **Synced backend active** (`resolution["backend"].capabilities.sync is True` + `project_locator` present) → state targets `<vault>/projects/<slug>/_harness/<file>` via the backend verbs.
- **Otherwise** (no backend, `sync=False`, missing root, or `.project-mode=local` opt-out) → state stays at `<project_root>/.harness/<file>`.

The `.project-mode=local` opt-out is checked first and wins over a synced backend — a per-repo or per-device opt-out is an explicit operator choice to keep that checkout's state off the shared vault.

**Graceful degradation:** on a machine with no vault, `select_backend` resolves `device-local` (`sync=False`) or `resolve_project` returns `backend=None` — either way `_state_backend_target` returns `None` and state falls to `<project_root>/.harness/`. This degradation is structural (a property of the discriminator), not a special code path.

**Writes through the seam:** `write_state_file` on a synced backend calls `backend.write(locator, content)`, composing the full V5-0 vault-write protocol: `vault_mutex` + content-hash CAS + atomic `fsync→rename`. The device-local branch keeps the lighter `atomic_write`-only path.

**Why route via `capabilities.sync`, not `isinstance(backend, VaultBackend)`:** `harness_memory.py` must not import the obsidian-vault plugin (the process-seam import-direction gate forbids it). The `capabilities.sync` flag is the contract; a backend that can't answer its capabilities degrades to device-local.

**Session-start hook co-location (ADR 0020 DC-6):** `harness-context-session-start.sh/.ps1` derives `progress.md` as a sibling of the resolved `PLAN_PATH` (taking `dirname`), so it co-locates with whatever `_harness/` the bridge resolved (vault or device-local). This ensures the locked DC-7 4-line output block fires when both files are present at the vault path.

### 4. Vault write protocol (ADR 0012 — V5-0 concurrency floor)

`scripts/vault_lock.py` is the one canonical write library: `vault_mutex` (advisory lock-dir outside the vault at `~/.cache/agentm/locks/<sha256(realpath(vault))>/lock`; heartbeat-liveness; bounded-block-with-backoff) + `content_hash` (sha256 CAS currency) + `atomic_write` (bytes-mode `fsync→rename`). It is vendored byte-identically to `harness/skills/memory/scripts/vault_lock.py`, enforced by `check-vault-lock-parity.sh`.

Key decisions:
- **Lock lives outside the vault** (never synced by Drive — a lock inside a synced tree is itself synced, which defeats mutual exclusion).
- **CAS keys on content hash, not mtime** (Drive re-downloads rewrite mtimes, producing false CAS matches; sha256 of bytes is the only reliable currency).
- **One global lock per vault, not per-file** (ownership-partitioning keeps real contention near zero; per-file locking adds machinery for contention that partitioning already prevents).
- **Plain `fsync`, not `F_FULLFSYNC`** (the cloud copy is the durability backstop; plain `fsync` before rename keeps each uploaded snapshot internally consistent at the right cost).
- **Cross-device mutual exclusion is out of scope** (impossible on Drive; out-of-band writers — another device, Obsidian itself — are the Phase-1 broker's problem).

### 5. State-mode configuration (ADRs 0009 + 0019)

The state mode (vault-backed vs. device-local) is an on-host configuration:

- **Device-level:** `state_mode` key in `.agentm-config.json` (set by `install.sh --local-state` or `agentm_config --state-mode`).
- **Per-repo override:** `<project>/.harness/.project-mode` marker — higher precedence than the device default.
- **Neither:** vault-first, guarded by a `ValueError` so a missing/unreachable vault fails loudly.

**`state_mode: vault` → `backend` read-alias (§2b, ADR 0019 task 3):** the canonical non-local value is `"backend"`. Any `"vault"` value in config or per-repo marker is returned as `"backend"` at read time **without rewriting the file**. `"vault"` is retained as a deprecated CLI alias and normalized to `"backend"` at write time. No operator action required.

**Why on-host, not in-vault:** you cannot read a marker out of a store you do not have. Configuration must be reachable on a vault-less machine. `.agentm-config.json` is already read vault-free.

**Why explicit, not inferred from `vault_path == null`:** a null `vault_path` is ambiguous between never-configured and transiently-unreachable (Drive not yet mounted). Inferring local from absence causes a transiently-unreachable vault to silently split state across two stores — the V4 #35 split-brain class.

### 6. Routing plane (ADR 0019 — V5-6)

`resolve_project` returns `{slug, project_locator, backend, project_root, layout}`:
- `project_locator` is a `Locator`, not a `Path`.
- `backend` is the resolved `StorageBackend` (vault plugin or device-local).
- `_vault_projects_dir(backend: StorageBackend) -> Locator` calls `backend.resolve("projects")`.

`repo_registry` functions take `backend: StorageBackend` and call `backend.resolve("_meta", "repos.json")` for the registry locator. On vault this resolves to `<vault>/_meta/repos.json` (same bytes as before V5-6); on device-local to `~/.agentm/memory/_meta/repos.json`.

**LC-8 gate (import direction):** `check-process-seam-import-direction.sh` scans `harness_memory.py` and `repo_registry.py` for `import storage_vault` / `from storage_vault import` — the routing layer must never import capability plugins.

**Routing conformance suite:** `storage_conformance.py`'s `check_routing_repo_registry(make_backend)` proves the register/list/unregister cycle on any conforming backend; exercised by `RoutingConformanceReport` against both `DeviceLocalBackend` and `vault_backend_stub.VaultBackend`.

### 7. Gate summary

| Gate | What it checks |
|---|---|
| `check-storage-seam-no-path-leak` | Return types of seam verbs in `storage_*.py` — no `Path` escapes |
| `check-process-seam-import-direction` (LC-8) | `harness_memory.py`, `repo_registry.py` never import `storage_vault` |
| `check-vault-lock-parity` | `vault_lock.py` byte-identical to vendored skill copy |
| `storage_conformance` | Universal verb battery + routing contract on any backend |

## §2b In-body resolutions

These are the four explicit resolutions the fold-plan required to land in this design body before `migrate-adr.py --apply` retires the source ADRs.

| Tag | Resolution |
|---|---|
| **DC-1 → 0020** | The current truth for `harness_state_dir` / `read_state_file` / `write_state_file` is **ADR 0020**: backend-aware, not device-local-only. ADR 0018 DC-1 (device-local only) was reversed by ADR 0020. See §3 above. |
| **DC-4 import** | "No top-level cycle; one guarded lazy import at `resolve_project`." `backend_selection.py` lazy-imports `harness_memory` only inside call bodies, never at module top-level. All other cross-seam top-level imports are forbidden. See §2 above. |
| **`state_mode`** | The canonical non-local value is `"backend"`. `"vault"` is a deprecated read-alias (normalized at read time, no file rewrite needed). See §5 above. |
| **`storage_vault` DELETE** | The kernel `storage_vault.py` was deleted in V5-3 (this commit, 2026-06-21). The vault backend now lives exclusively in the crickets `obsidian-vault` plugin. Tests use `vault_backend_stub.VaultBackend`. See §2 ("Vault plugin discovery") above. |

**Cross-design pointer:** ADR 0018's "re-audit if `personal-private/` → `personal/` rename ships" trigger fired in V5-3. See [ADR 0010](agentm-foundations-hld) (vault taxonomy, folded into the Foundations HLD).

## Amendment log

This log preserves the decision history from the six retired ADRs. Each entry records the original decision, why-not-the-alternative, re-audit triggers, and any later amendments. Entries appear in chronological order (earliest first); the most recent (0020) is current truth.

---

### 2026-06-03 — ADR 0009: On-host state-mode config (Hardening I)

**Decision:** State mode (vault vs. local) is configured on-host only — `state_mode` in `.agentm-config.json`, with an optional per-repo `<repo>/.harness/.project-mode` marker override. No configuration lives in the vault.

**Why not in-vault markers:** a marker inside the vault is structurally unreachable on a vault-less machine — the exact machine the vault-less feature targets. The retired design read `.project-mode` from `<vault>/_harness/` (deleted); the `harness_state_mode` registry field (write-only, never read by any resolution path) was deleted as dead weight.

**Why not overload the existing `mode` key:** `mode` means install mode (`source` vs. `release`) — a different axis. Conflating "how this harness was installed" with "where it writes state" would require every reader to know which bits mean what.

**Why explicit, not inferred from `vault_path == null`:** a null `vault_path` is ambiguous between never-set and transiently-unreachable (Drive not yet mounted). Silent inference causes the V4 #35 split-brain class; an explicit setting is the only one that distinguishes "I chose local" from "my vault is late to mount."

**Resolution order (DC-2):** (1) repo-local `<repo>/.harness/.project-mode` marker; (2) device `state_mode` in `.agentm-config.json`; (3) neither → vault-first, guarded `ValueError`.

**Re-audit triggers:** `.agentm-config.json` fragments across multiple files; `vault_path == null` ambiguity is resolved (inference becomes safe); multi-vault-per-device becomes a real use case; per-repo marker proves to be the common path.

---

### 2026-06-12 — ADR 0012: Vault write protocol (V5-0 concurrency floor)

**Decision:** Ship the write-safety floor for N≥2 concurrent vault writers: per-vault advisory mutex (lock-dir outside vault, mtime heartbeat, bounded-block-with-backoff), content-hash CAS (replacing mtime CAS), atomic `fsync→rename` writer, broadened conflict-janitor, and operator pin-offline / ownership-partitioning guidance. Not the singleton MCP broker, SQLite-WAL journal, or encryption-at-rest.

**Why not build the broker now:** R4 sequences those as Phase 1 / V5-9 / optional. The floor unblocks N≥2 writers and is the prerequisite the broker imports — shipping the floor first keeps each diff reviewable and the cutover an expand→contract.

**Why one global lock, not per-file:** writes are short and rare, so one lock suffices. Ownership-partitioning (agents write different slugs → different files) already keeps contention near zero; per-file locking adds machinery for contention that doesn't exist.

**Why content-hash over mtime:** Drive re-downloads rewrite mtimes, producing false "changed" and false "unchanged" CAS verdicts. sha256 of bytes is the only reliable currency.

**Why plain `fsync`, not `F_FULLFSYNC`:** the cloud copy is the durability backstop; plain `fsync` before rename ensures each uploaded snapshot is internally consistent at the right cost for short markdown writes. `fsync ≠ durable` on macOS is a documented limit, not a bug.

**Why vendored, not imported cross-tree (DC-9):** the `/memory` skill scripts are self-contained by construction (three install scopes; top-level `scripts/` is not installed into target prefixes). Byte-identity gate (`check-vault-lock-parity`) makes drift a hard CI failure.

**Re-audit triggers:** a write path ever approaches seconds (grows the stale window); top-level `scripts/` ever ships into target prefixes (collapse the vendored copy to an import); vault runs on a non-synced/local-only backend (re-evaluate `F_FULLFSYNC`); a second machine ever becomes a writer (broker becomes mandatory).

---

### 2026-06-13 — ADR 0013: Memory↔storage seam (V5-1)

**Decision:** Ship the seam interface (`Locator`-based verbs in `storage_seam.py`), the device-local backend, fail-loud selection (`StorageSelectionError` — never demote), and the `doctor` preview sharing the resolver's code path. The engine cutover (live read/write via the seam) was deferred to V5-3.

**Why opaque `Locator`, not `Path`:** a path-returning verb leaks a filesystem assumption silently and totally. The opaque handle means "swap the backend, the engine doesn't notice" holds by construction.

**Why the no-`Path`-leak rule is AST-gated, not grepped:** a filesystem backend legitimately writes `Path` all through its body. Only handing one back across the seam is the violation; the return annotation is precisely where that shows up.

**Why `registry.get → None` is absence, not corruption:** a registry miss is an absent backend (not a malformed key — that's `ProtocolError` / `TypeError` immediately). Folding a raise into `get` would scatter the fail-loud policy across every lookup; concentrating it in selection keeps "what a miss means" in one place.

**Why never demote:** a silent fall-back to `device-local` is the single failure that mis-writes or orphans the vault. A loud refusal forces the operator to fix the install — always the correct outcome for a misconfiguration.

**Amendment — 2026-06-18 (V5-7): capability-request matching extends DC-4** (same fail-loud principle). `select_backend(required=Capabilities(...))` raises `CapabilityMismatchError` on mismatch. Silent downgrade, partial satisfaction, and return-a-warning were all rejected for the same reason DC-4 carries: a wrong-but-running engine is worse than a stopped one. `--requires` in `_doctor_main` extends DC-5's preview/runtime shared path.

**Re-audit triggers:** a deployment needs a missing backend to degrade rather than block (add a config-gated soft mode); text is no longer the v1 currency (widen the verb contract deliberately); the V6 index needs change-detection finer than mtime survives.

---

### 2026-06-18 — ADR 0018: V5-3 storage cutover

**Decisions:** (DC-1 — reversed by ADR 0020, see §3 and the §2b block) make state functions device-local only *(reversed)*. (DC-2) `phase_recall → ""` (context now V5-9 MCP). (DC-3) `resolve_documenter_context → None`. (DC-4) `vault_path()` raises `StorageBackendNotInstalledError` when `storage.backend=vault` + no vault accessible — load-bearing fail-loud guard. (DC-5) `$MEMORY_VAULT_PATH` env override is a graceful-skip (per-session escape hatch, not a durable commitment). (DC-6) Routing/index layer retained (`resolve_project`, `_vault_projects_dir`, `repo_registry`, etc. — probes for vault identity, not state reads). (DC-7) `personal-private/` → `personal/` rename shipped in lockstep with V5-3.

**Why DC-4 in `vault_path()`, not only in `select_backend()`:** the guard fires on every call path that reaches vault resolution, not only through explicit selection. Also avoids circular import (`harness_memory.py` does not import `backend_selection.py`).

**Why DC-2 empty-string return, not removal:** `phase_recall` is part of the frozen process-seam surface; callers get a no-op result, not an import error. V5-9 MCP replaces the recall capability at the process boundary.

**DC-1 amendment — 2026-06-19 (ADR 0020 reverses DC-1):** making state functions device-local only conflated *recall* (read-only context bundles, correctly device-local / MCP-owned) with *execution state* (`PLAN.md` / `progress.md` / `ROADMAP.md`, which a synced vault exists to hold). See ADR 0020 / §3.

**Re-audit triggers:** V5-9 MCP server is removed (restore a minimal recall path in the kernel); any installer auto-writes `storage.backend=vault` without ensuring a vault path; a harness state dir move (update `harness_state_dir` and callers); a proposal to write state through the routing layer (route through the seam instead).

**Cross-design pointer:** ADR 0018 DC-7's `personal-private/` → `personal/` rename fires ADR 0010's first load-bearing assumption re-audit trigger — see [ADR 0010 — Vault internal taxonomy](agentm-foundations-hld).

---

### 2026-06-18 — ADR 0019: V5-6 routing-plane de-vaulting

**Decisions:** (task 1) `resolve_project` / `_vault_projects_dir` speak `Locator`s: signature change from `(vault: Path) -> Path` to `(backend: StorageBackend) -> Locator`; `resolve_project` returns `{slug, project_locator, backend, project_root, layout}`. (task 2) `repo_registry` rides the active backend: `registry_locator(backend) -> Locator`, all five public functions take `backend: StorageBackend`. (task 3) `state_mode: vault` → `"backend"` read-alias at both `_read_config_state_mode` and `_read_project_mode`; canonical value is `"backend"`; no file rewrite. (task 4) Gate extensions: no-`Path`-leak Pass 2 on routing functions; LC-8 block on routing-to-plugin imports; `storage_conformance.py` routing suite.

**Why not preserve `vault_path: Path` in the resolve_project return value:** V5-7 removed implicit vault selection from a bare `vault_path` config key (a configured path ≠ a chosen backend). Honoring a bare path would resurrect implicit inference and route state to a vault the selection chain deliberately did not pick — the exact split-brain ADR 0018 DC-4/DC-5 guard against.

**Why `state_mode: vault` → `"backend"` at read time, not file rewrite:** every operator's config written before V5-6 carries `state_mode: vault`; a file rewrite would require operator action for zero functional change. The alias makes the rename transparent.

**Re-audit triggers:** `VaultBackend.write()` is refactored to not hold `vault_mutex` internally (update `_mutate_registry`); `"local"` state-mode value ever needs a rename; V8 multi-agent / cross-machine support is designed (cross-machine registry coherence is device-specific by assumption today).

---

### 2026-06-19 — ADR 0020: Backend-aware harness state (current truth for DC-1)

**Decision:** Reverse ADR 0018 DC-1. The three kernel state functions (`harness_state_dir`, `read_state_file`, `write_state_file`) are backend-aware: synced-backend active → vault `_harness/`; otherwise → device-local `<project>/.harness/`. Single discriminator: `resolution["backend"].capabilities.sync`. `.project-mode=local` opt-out wins over a synced backend.

**Why reverse DC-1:** ADR 0018 DC-1 conflated *recall* (read-only context bundles — correctly device-local / MCP-owned, per DC-2/DC-3) with *execution state* (`PLAN.md` / `progress.md` / `ROADMAP.md`, which the synced vault exists to hold across devices). The concrete failure: a `storage.backend=vault` project's plan was invisible at session start because the SessionStart hook read the empty device-local `.harness/` instead of the vault's `_harness/`.

**Why `capabilities.sync`, not `isinstance(backend, VaultBackend)`:** `harness_memory.py` must not import the obsidian-vault plugin (process-seam import-direction gate). The `capabilities.sync` flag is the contract; the implementation detail of which plugin provides it is invisible to the kernel.

**Why the discriminator is the live backend, not the `vault_path` key:** ADR 0019 removed `vault_path` from the resolution shape; V5-7 removed implicit vault selection from a config-supplied `vault_path`. Honoring a bare path would resurrect the implicit inference ADR 0018 DC-4/DC-5 guard against. The backend object is the single source of truth for "is a synced store active."

**Why writes go through `backend.write()`, not raw path I/O:** writing the vault directly would bypass `vault_mutex` + CAS + `atomic_write`, reintroducing the torn-write and lost-update hazards ADR 0012 exists to prevent.

**Re-audit triggers:** a future backend is `sync=True` but not an appropriate state home (add a more specific capability than `sync`); any change lets a backend-selection error propagate out of `resolve_project` (crash session boot instead of degrading); vault layout moves `_harness/` out from under `projects/<slug>/`; the hook gains a second plan-discovery path that doesn't route through the bridge.
