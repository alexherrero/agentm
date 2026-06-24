# Storage seam reference

The memory↔storage contract ([`scripts/storage_seam.py`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_seam.py)) — the small interface of *verbs* the memory engine calls instead of touching the filesystem directly. A backend implements the verbs; the engine consumes the seam's own `Locator` type and so learns no filesystem assumption. This page documents the **abstract contract plus its two concrete backends** (V5-1 parts 1–4 of 5): the seven verbs as an abstract `StorageBackend`, the three value types, the `conflict_strategy` slot, the named-backend registry, the three-tier source/derived taxonomy, the no-`Path`-leak gate, the [`DeviceLocalBackend`](#the-devicelocalbackend) that implements all of it over plain markdown (since part 2), the backend-agnostic conformance suite that holds both backends to one objective contract (since part 3), and — since part 4 — the [`VaultBackend`](#the-vaultbackend) that wraps the synced vault write path behind the same seam. For *why* the seam is shaped this way, see [Memory↔storage seam](Storage-Seam-Concepts).

> [!NOTE]
> **Parts 1–4 shipped: the contract, two concrete backends, and the conformance gate.** The abstract `StorageBackend`, the `Locator`/`Info`/`Capabilities` types, the `conflict_strategy` slot, the [`BackendRegistry`](#the-backendregistry), and the [three-tier taxonomy](#the-tier-taxonomy) (`Tier` / `TierLayout` / `DerivedMaintenance`) shipped in part 1; the [`DeviceLocalBackend`](#the-devicelocalbackend) — the fresh-install default, plain markdown under `~/.agentm/memory/` — ships in part 2 and registers under `device-local` in the default [`registry`](#registry-module-default); the backend-agnostic conformance suite ships in part 3 and runs both backends against the shared contract — it is **not** a new `check-*` gate, it rides the existing cross-OS `[T]` unit-test step; the [`VaultBackend`](#the-vaultbackend) ships in part 4, wrapping the synced vault write path (the full V5-0 `vault_mutex` + content-hash CAS + `atomic_write` stack) behind the seam and registering under `vault`. **Part 5 — backend selection — is now landing task-by-task.** Task 1 (shipped) adds the [`storage.backend` config key](#the-storagebackend-config-key) plus the [resolver](#the-selection-resolver) ([`scripts/backend_selection.py`](https://github.com/alexherrero/agentm/blob/main/scripts/backend_selection.py)) that maps config → a registered backend instance via [`registry.get`](#the-backendregistry). Task 2 (shipped) adds the [`Capabilities`](#the-capabilities-type)-read — the kernel reads the selected instance's four-boolean `.capabilities` through the resolver (read-and-surface only; capability-request *matching* — a consumer declaring what it *needs* — is V5-7; **V5-7 Part 1 (shipped):** `select_backend` gains `required: Capabilities | None = None` and raises [`CapabilityMismatchError`](#capabilitymismatcherror) on an unmet requirement). Task 3 (shipped) replaces the placeholder raise with the polished [**fail-loud guard**](#the-fail-loud-guard): a named [`StorageSelectionError`](#the-fail-loud-guard) whose install-the-plugin message names the exact missing backend and lists the currently-registered alternatives, proven by a negative test that there is **no silent `device-local` fall-back**. Task 4 (shipped) adds the read-only [`doctor` storage preview](#the-doctor-storage-check) — `python3 scripts/backend_selection.py --doctor` previews the selected backend and its plugin-installed state, rendering the **byte-identical** install-the-plugin message before any memory operation could refuse. **Part 5 — selection, the fail-loud guard, and the doctor preview — is complete; it did *not* include the engine cutover.** That step landed in **V5-3 (v5.5.0, [ADR 0018](memory-storage-seam))**: `harness_state_dir` / `read_state_file` / `write_state_file` / `phase_recall` / `resolve_documenter_context` in `harness_memory.py` now route exclusively to device-local state; the vault backend is no longer in the kernel's call path. **No index or abstract-promotion ships** — `DerivedMaintenance` remains an abstract class with **no concrete subclass** (`reindex`/`changed_since` are *reserved names*), and the actual derived index lands in V6. The `_MemoryBackend` used in the contract tests is a fixture, not a shipped backend. Anything this page marks *(V6)* is a forward reference, not current behavior.

## ⚡ Quick Reference

| Verb | Signature | Returns | Raises |
|---|---|---|---|
| `resolve` | `resolve(*parts)` | `Locator` — a backend-relative handle (`resolve()` = backend root) | `InvalidLocatorError` on a `..` segment |
| `read` | `read(locator)` | `str` — text content at `locator` | `FileNotFoundError` if absent |
| `write` | `write(locator, content)` | `Locator` — the locator written (round-trips through `read`) | backend's choice |
| `list` | `list(locator)` | `list[Locator]` — immediate children (`[]` if empty) | backend's choice on a non-existent dir |
| `exists` | `exists(locator)` | `bool` — file *or* directory present | — |
| `info` | `info(locator)` | `Info` — metadata (carries `mtime`) | raises if absent |
| `mkdir` | `mkdir(locator)` | `Locator` — the directory (idempotent) | — |
| `capabilities` | property → `Capabilities` | the four-boolean descriptor the backend declares | — |

> [!IMPORTANT]
> **No `pathlib.Path` crosses the seam.** Every verb operates on and returns the seam's own `Locator` (or `Info`), never a `pathlib.Path` — that is what keeps a filesystem assumption from reaching the engine. A filesystem backend uses `Path` *internally* (`root / key`); only handing one *back* is the violation, enforced statically by [`check-storage-seam-no-path-leak`](CI-Gates). Text (`str`) is the v1 currency; a bytes channel is a deliberate future extension.

## The `Locator` type

A place in *some* backend's namespace — the seam's own opaque locator, a frozen `dataclass`. Deliberately **not** a `pathlib.Path`: it carries a normalized, backend-relative key and exposes only namespace operations, never filesystem I/O. Frozen and hashable, so a backend may use it as a dict key (the in-memory fixture does).

| Member | Type | Detail |
|---|---|---|
| `key` | `str` | The normalized backend-relative key. The root locator is `""`. Normalized at construction via `normalize_key`. |
| `parts` | `property → tuple[str, ...]` | The key's segments; `()` for the root. |
| `name` | `property → str` | The final segment; `""` for the root. |
| `child(*parts)` | `→ Locator` | Derive a sub-locator by appending `parts` (each normalized, no escape). |
| `__str__` | `→ str` | The bare `key`. |

### Normalization + root-confinement (`normalize_key`)

Every `Locator` is canonicalized at construction — however built (direct, via `resolve`, via `child`) — by `normalize_key(key) -> str`:

| Input segment | Result |
|---|---|
| empty (`""`) or `.` | dropped |
| leading `/` (absolute-looking) | silently relativized — the empty leading segment is dropped; a locator is *always* backend-relative |
| `..` | **rejected** — raises `InvalidLocatorError`; the seam has no upward-traversal semantics |
| anything else | kept, rejoined with `/` |

This is the safety property: a key can never address outside the backend root.

## The `Info` type

Metadata about a locator — the `info` verb's return, a frozen `dataclass`.

| Field | Type | Detail |
|---|---|---|
| `locator` | `Locator` | The locator described. |
| `is_dir` | `bool` | Whether it's a directory. |
| `size` | `int` | Bytes (`0` for a directory). |
| `mtime` | `float` | Modification time, epoch seconds. **The `changed_since` granularity** — the [incremental-feed op](#derivedmaintenance) reserved here keys on it, the *lean* v1 choice over a content-hash log. |

## The `Capabilities` type

What a backend can promise — the per-backend descriptor a backend declares, a frozen `dataclass`. Four booleans, all defaulting to the conservative floor (`False`). The contract (part 1) only *defines* them and both built-ins already declare their values — device-local all-`False`, vault `concurrent_writers`/`sync`/`conflict_files` `True`. Part 5 adds the **read side** (task 2, shipped): the [resolver](#the-selection-resolver)'s `select_backend` returns the selected backend *instance* ([`backend_selection.py#L90`](https://github.com/alexherrero/agentm/blob/main/scripts/backend_selection.py#L90)), whose `.capabilities` property the kernel reads directly to learn what it can promise — proven by `TestCapabilitiesRead` ([`scripts/test_backend_selection.py#L143`](https://github.com/alexherrero/agentm/blob/main/scripts/test_backend_selection.py#L143)), which reads all four booleans off both built-ins through the selection surface and pins the four-key shape as a superset (additive growth of new keys is allowed; a rename/removal breaks the pin). This is read-and-surface only — capability-request *matching* (a consumer declaring what it *needs* and selection honoring it) is **V5-7**. **V5-7 Part 1 (shipped):** `select_backend` now accepts an optional `required: Capabilities | None = None` keyword parameter; when provided, it runs a subset check after resolving the backend and raises [`CapabilityMismatchError`](#capabilitymismatcherror) (a `StorageSelectionError` subclass) if any `True` requirement is unmet. `required=None` (default) preserves prior behavior. A dataclass so the set can grow.

| Field | Default | Meaning |
|---|---|---|
| `concurrent_writers` | `False` | Safe under more than one writer process. |
| `conflict_files` | `False` | The backend may surface conflict copies (e.g. a sync layer's "(conflicted copy)" files) the engine must tolerate. |
| `encryption` | `False` | Content is encrypted at rest by the backend. |
| `sync` | `False` | The backend's tree is replicated by an external sync layer — the property that makes a SQLite index *on* it a corruption pattern. This is the flag the [`Tier.syncs`](#the-tier-taxonomy) policy ties to: the local-index tier is pinned never-sync precisely because a replicated database file corrupts. |

## The `StorageBackend` ABC

The abstract interface a backend conforms to. A concrete backend registers under a protocol name (`device-local`, `vault`) via the [`BackendRegistry`](#the-backendregistry) and implements every verb. Concrete backends are out of scope for this part; the registry they will plug into ships here.

### `resolve(*parts) -> Locator`

Make a backend-relative locator from path `parts`. `resolve()` with no parts is the backend root. The naming verb: it produces the seam's locator type, the engine's only handle on storage.

### `read(locator) -> str`

Return the text content at `locator`.

| Condition | Result |
|---|---|
| Content present | The text as `str`. |
| Nothing there | Raises `FileNotFoundError` — distinct from `InvalidLocatorError` (a malformed key, a caller bug). |

### `write(locator, content) -> Locator`

Write text `content` at `locator`; return the locator written. The contract: the write is **durable and atomic**, and the returned locator **round-trips through `read`**.

> [!NOTE]
> A *filesystem* backend composes the [vault write protocol](Vault-Write-Protocol) here (V5-0 `atomic_write` + content-hash CAS + `vault_mutex`) rather than reinventing write-safety. The abstract contract only declares the shape; the composition lands with the concrete backends (parts 2 / 4), not in this ABC.

### `list(locator) -> list[Locator]`

List the immediate children of `locator` as locators.

| Condition | Result |
|---|---|
| Empty directory | `[]`. |
| Non-existent directory | Pinned to **return `[]`, never raise** by the part-3 conformance suite (the cross-backend `list`-on-absent contract). |
| Otherwise | The immediate children as `Locator`s, never paths. |

### `exists(locator) -> bool`

Whether anything (file *or* directory) is present at `locator`.

### `info(locator) -> Info`

Return `Info` for `locator` (raises if absent). Carries `mtime` — the granularity [`changed_since`](#derivedmaintenance) reads.

### `mkdir(locator) -> Locator`

Ensure a directory exists at `locator`; return it. **Idempotent** — calling it on an existing directory is not an error.

### `capabilities -> Capabilities` (property)

What this backend promises — see [Capabilities](#the-capabilities-type).

### `conflict_strategy -> str` (property)

How this backend reconciles divergent concurrent writes — a *named* policy, distinct from the [`Capabilities`](#the-capabilities-type) booleans. Where those describe *what the backend can promise*, `conflict_strategy` *names the reconciliation policy* selection (part 5) reads to decide how to treat a conflict. Unlike `capabilities` (abstract — every backend must declare it), this is a **concrete** property ([`scripts/storage_seam.py#L203`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_seam.py#L203)) defaulting to the conservative floor `"none"` (last write wins — nothing to reconcile), mirroring how the `Capabilities` booleans default safe.

| Strategy | Meaning | Who declares it |
|---|---|---|
| `"none"` | Last write wins; nothing to reconcile. **The inherited floor** — a backend that doesn't override it gets this. | `StorageBackend` default; [`DeviceLocalBackend`](#the-devicelocalbackend) inherits it (single machine). |
| `"whole-file"` | The whole file is the conflict unit — a *named* policy [part-5 selection](#the-backendregistry) reads, **not** an executor; agentm ships no automated whole-file merger, only the detect-and-notice machinery (`detect_conflict_files` + the `conflict-merger` SessionStart hook) that surfaces each conflict/base pair for operator-by-hand resolution. | The synced [`VaultBackend`](#the-vaultbackend) (part 4) overrides the floor to this ([`storage_vault.py#L160`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_vault.py#L160)). |
| _(later)_ | An iCloud numbered-suffix or a CRDT line-level strategy (design §6). | Reserved — the slot is always present and always answerable. |

## `InvalidLocatorError`

A `ValueError` subclass raised by `normalize_key` (and therefore `Locator` construction / `resolve`) when a key escaped or malformed its backend-relative namespace — today, a `..` segment. It signals a *caller bug* (an unsafe key), kept deliberately distinct from the absent-data degrade a backend reports for a missing `read` (`FileNotFoundError`).

## The `BackendRegistry`

A hand-rolled name→backend registry ([`scripts/storage_seam.py#L282`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_seam.py#L282)) — the fsspec named-protocol pattern, mirrored, importing neither fsspec nor any DB. A backend registers under a **protocol name** (`device-local`, `vault`); selection (part 5) resolves a configured name against the registry to choose a backend. The registry stores backend **classes**, not instances — selection instantiates the chosen one.

The contract that matters for part 5's fail-loud: **a miss is reported as absent, never raised.** `get` returns `None` and `in` returns `False` for an unregistered name. The registry reports absence; the *caller* turns absence into a loud failure if the configured backend doesn't exist. That split — absence here, fail-loud there — mirrors the seam's absent-vs-corrupt stance (a missing `read` degrades to `FileNotFoundError`; only a malformed key raises).

| Member | Signature | Returns | Raises |
|---|---|---|---|
| `register` | `register(protocol, backend, *, clobber=False)` | `None` | `TypeError` if `backend` isn't a concrete `StorageBackend` subclass; `ProtocolError` on an empty name or a duplicate (unless `clobber=True`) |
| `get` | `get(protocol)` | `type[StorageBackend] | None` — the registered class, or `None` if absent | — (a miss is *not* an error) |
| `__contains__` | `protocol in registry` | `bool` — whether `protocol` is registered | — |
| `protocols` | `protocols()` | `tuple[str, ...]` — the registered names, sorted | — |

> [!IMPORTANT]
> **`register` fails loud on a bad registration, but a *miss* is absence, not an error.** A bad registration is a programming bug, surfaced immediately: an empty protocol name or a silent duplicate raises `ProtocolError`; a non-backend, the abstract `StorageBackend` base itself, or a backend *instance* (rather than a class) raises `TypeError`. Refusing a silent duplicate keeps one backend from shadowing another by accident. Resolving an unregistered name is the opposite case — it returns `None`, the signal part 5 reads to decide whether the absence is fatal.

`BackendRegistry` instances are independent — no shared global state — so a fresh `BackendRegistry()` can be registered into without leaking across callers (the contract tests rely on this for hermeticity).

### `registry` (module default)

A process-wide default `BackendRegistry` instance ([`scripts/storage_seam.py#L347`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_seam.py#L347)) the real backends register into. As of part 4 it holds **two** entries — `device-local`, registered at import of [`storage_device_local`](#the-devicelocalbackend), and `vault`, registered at import of [`storage_vault`](#the-vaultbackend) ([`storage_vault.py#L241`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_vault.py#L241)). Both are stored as classes, not instances; selection (part 5) instantiates the chosen one.

| Protocol | Registered class | Since |
|---|---|---|
| `device-local` | [`DeviceLocalBackend`](#the-devicelocalbackend) | part 2 |
| `vault` | [`VaultBackend`](#the-vaultbackend) | part 4 (now) |

## Backend selection (part 5)

> [!NOTE]
> **Landing task-by-task.** This section describes the **selection + fail-loud** part of V5-1. **Task 1 (shipped):** the `storage.backend` config field, [`choose_protocol`](#the-selection-resolver), and [`select_backend`](#the-selection-resolver) in [`scripts/backend_selection.py`](https://github.com/alexherrero/agentm/blob/main/scripts/backend_selection.py). **Task 2 (shipped):** the [capabilities-read](#the-capabilities-type) — the kernel reads the selected backend's four-boolean `.capabilities` through `select_backend`'s returned instance (read-and-surface only; matching lands in V5-7). **V5-7 Task 1 (shipped):** `select_backend` gains a `required: Capabilities | None = None` parameter — when set, raises [`CapabilityMismatchError`](#capabilitymismatcherror) if the selected backend doesn't satisfy every `True` requirement. **V5-7 Part 2 (shipped):** `_doctor_main` gains a `--requires <CAPS>` flag — validates names, calls `select_backend(required=...)`, and prints `PASS`/`FAIL` with the outcome (see [`The doctor storage check`](#the-doctor-storage-check)). **Task 3 (shipped):** the polished [fail-loud guard](#the-fail-loud-guard) — a named [`StorageSelectionError`](#the-fail-loud-guard) replaces the placeholder raise, names the missing plugin, lists the registered alternatives, and is proven to never silently fall back to `device-local`. **Task 4 (shipped):** the read-only [`doctor` storage preview](#the-doctor-storage-check) — previews the selected backend + plugin-installed state and renders the byte-identical install-the-plugin message before any memory operation could refuse. **The engine cutover was *not* part 5** — selection resolves *which* backend to use and refuses a misconfigured one; routing the engine's public API through the resolved backend shipped later in **V5-3 (v5.5.0)**.

Part 5 adds the **read side** of the seam: a config key naming the chosen backend and a resolver that turns that name into a registered backend instance via [`registry.get`](#the-backendregistry). The resolver module is deliberately *not* named `storage_*.py` and uses no seam-verb name (`choose_protocol` / `select_backend`, never `resolve`/`read`/`write`/…), so the [`check-storage-seam-no-path-leak`](CI-Gates) gate (scoped to `scripts/storage_*.py`) does not scan it even though it handles `Path`.

### The `storage.backend` config key

A new operator-facing field on the on-host `.agentm-config.json`, set via the [config CLI](Installer-CLI#config-cli--agentm_configpy) — see the [forthcoming row](Installer-CLI#config-cli--agentm_configpy) there and the how-to [Choose a storage backend](Choose-A-Storage-Backend).

| Aspect | Detail |
|---|---|
| Field | `storage.backend` on `.agentm-config.json` (the same on-host config that holds `plugins.obsidian-vault.vault_path` / `state_mode` — V5-7 tasks 1+2 shipped: `vault_path()` reads the plugin-namespaced key first, falling back to the legacy flat key; `--vault-path` writes the plugin key + `storage.backend=vault`; the vault holds data; config is on-host only). V5-7 task 3 (shipped): `choose_protocol` lost its `vault_root` parameter; implicit vault inference removed; vault is now selected only via explicit `storage.backend` (step 1) or `$MEMORY_VAULT_PATH` env var (step 2) in the 3-step chain below. |
| Value | A registered [protocol name](#registry-module-default) — `device-local`, `vault`, or a plugin-provided name. |
| Default | _Unset._ Absent → the resolver picks a backend from the existing config (see the resolution chain). |
| Who reads it | The selection resolver (below), via `_configured_backend` ([`backend_selection.py#L54`](https://github.com/alexherrero/agentm/blob/main/scripts/backend_selection.py#L54)) — which reuses `agentm_config`'s own prefix-resolution + reader, so the read is the same file the `--storage-backend` setter writes. The config CLI only stores it; it does **not** validate that the named backend is registered ([`agentm_config.py#L151`](https://github.com/alexherrero/agentm/blob/main/scripts/agentm_config.py#L151) validates non-empty only) — that is the [fail-loud guard](#the-fail-loud-guard)'s job at resolve time. |

### The selection resolver

The resolver ([`scripts/backend_selection.py`](https://github.com/alexherrero/agentm/blob/main/scripts/backend_selection.py)) maps config → a registered backend instance. It sits **above** the seam (DC-7): it imports `agentm_config`, `harness_memory`, the two concrete backend modules (so they self-register at import), and `storage_seam.registry` — but the seam itself never imports it.

| Aspect | Detail |
|---|---|
| Module | `scripts/backend_selection.py` — deliberately not `storage_*.py` (keeps it out of the [no-`Path`-leak gate](CI-Gates)'s glob, even though the resolver handles `Path`). |
| `choose_protocol(*, install_prefix=None) -> str` | Resolves the protocol *name* per the 3-step chain below, no instantiation ([`#L71`](https://github.com/alexherrero/agentm/blob/main/scripts/backend_selection.py#L71)). **V5-7 task 3 (shipped):** `vault_root` parameter removed; implicit vault inference from a config-supplied `vault_path` removed. Vault is now selected only via explicit `storage.backend` config (step 1) or `$MEMORY_VAULT_PATH` env var (step 2). `vault_path()` reads `plugins.obsidian-vault.vault_path` first, then the legacy flat key (V5-7 task 1, shipped). |
| `select_backend(*, install_prefix=None, device_local_root=None, vault_lock_root=None, required=None) -> StorageBackend` | Resolves the protocol, then instantiates via `registry.get` ([`#L90`](https://github.com/alexherrero/agentm/blob/main/scripts/backend_selection.py#L90)). The `*_root` args are injection points so tests never touch the operator's real home / cache / vault. **V5-7 Part 1:** the optional `required: Capabilities \| None` parameter — when provided, runs a subset check after resolving the backend and raises [`CapabilityMismatchError`](#capabilitymismatcherror) if any `True` requirement is unmet; `None` (default) preserves prior behavior. |
| How it instantiates | `storage_seam.registry.get(<protocol>)` → the registered class → instantiate ([`#L111`](https://github.com/alexherrero/agentm/blob/main/scripts/backend_selection.py#L111)). `vault` is constructed with the vault root resolved internally from `plugins.obsidian-vault.vault_path` (`backend_cls(vault_root, lock_root=…)`); `device-local` with its optional root; an explicitly-configured third-party backend with the V5-1 minimal no-arg constructor. (`vault_root` as an external `choose_protocol` parameter was removed in V5-7 task 3.) |

The resolution chain (config → backend), first hit wins:

<!-- V5-7 tasks 1+2 shipped: vault_path() reads plugins.obsidian-vault.vault_path first before falling back to the flat vault_path key; --vault-path writes the plugin-namespaced key + storage.backend=vault. V5-7 task 3 shipped: implicit vault-selection step (former precedence 2) removed from choose_protocol; former precedence 3 env-var step retained. V5-8 (31f1ba9): chain is 3-step (explicit config → env-var → device-local). -->
| Precedence | Condition | Resolved backend |
|---|---|---|
| 1 (wins) | `storage.backend` is set in the install config | The named backend (via `registry.get`); the [fail-loud guard](#the-fail-loud-guard) fires if it isn't registered. |
| 2 | `$MEMORY_VAULT_PATH` env var is set (non-empty) | `vault` — explicit env-based escape hatch; setting the env var IS selecting vault. |
| 3 | No `storage.backend`, no `$MEMORY_VAULT_PATH` — fresh install or config not yet set | `device-local` (the bare-markdown floor under `~/.agentm/memory/`). |

> [!NOTE]
> **V5-7 task 3 (shipped):** vault selection is now always explicit — `choose_protocol` no longer infers `vault` from a config-supplied `vault_path`. An operator migrating from a pre-V5-7 implicit setup must run `agentm config --vault-path <dir>` once (which writes `storage.backend=vault` automatically) or use the `$MEMORY_VAULT_PATH` env override. Both paths proven by [`scripts/test_backend_selection.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_backend_selection.py).

### The fail-loud guard

> [!IMPORTANT]
> **Refuse, never demote.** If config names a `storage.backend` whose [`registry.get()`](#the-backendregistry) returns `None` (the plugin isn't installed), the resolver **raises [`StorageSelectionError`](#storageselectionerror) and refuses the memory operation** — it does **not** silently fall back to `device-local`. Silent demotion is the one failure that could orphan or mis-write an operator's configured store, so the seam forbids it. This is the loud half of the [registry's absence-vs-fail-loud split](#the-backendregistry): the registry *reports* absence (`get` → `None`); the resolver *decides* it is fatal.

As of **part-5 task 3** (and the silent-demotion hardening that followed), `select_backend` raises a dedicated [`StorageSelectionError`](#storageselectionerror) (no longer the task-1 placeholder `RuntimeError`) on any of **four** refusals. Two are raised by `select_backend` itself once a protocol is chosen; the other two are raised earlier by `_configured_backend` ([`backend_selection.py#L54`](https://github.com/alexherrero/agentm/blob/main/scripts/backend_selection.py#L54)) — the reader of the explicit `storage.backend` value — which refuses rather than let a corrupt config silently drop the selection and demote to `device-local`:

| Refusal | What raises it | Message shape |
|---|---|---|
| Unregistered backend | `select_backend` — `storage.backend` names a backend whose [`registry.get()`](#the-backendregistry) returns `None` | `storage backend '<name>' is configured (storage.backend) but no installed plugin registers it. Install the plugin that provides the '<name>' backend, or set storage.backend to an installed backend (currently registered: <sorted, comma-joined, or "(none)">).` — built by the `_install_plugin_message` helper, [`backend_selection.py`](https://github.com/alexherrero/agentm/blob/main/scripts/backend_selection.py). |
| `vault` with no seed | `select_backend` — `vault` is selected (rule 1 or 2) but no `vault_path` resolves to seed it | The same `StorageSelectionError` family — refuse rather than guess a vault root. |
| Config present but unparseable | `_configured_backend` — the config file exists but is malformed JSON (or not a JSON object) while it would otherwise carry a selection | Names the unreadable config file path. A corrupt config that drops an explicit selection is the orphan-the-vault failure the never-demote invariant forbids, so the reader deliberately does **not** reuse [`agentm_config._read_config`](https://github.com/alexherrero/agentm/blob/main/scripts/agentm_config.py)'s tolerant read (which collapses *missing* and *present-but-unreadable* into the same `None`): file **absent** → `None` (genuinely fresh, continue the chain); file **present-but-unparseable** → raise. |
| `storage.backend` not a non-empty string | `_configured_backend` — the key is present but its value is not a non-empty string (e.g. a list/number from corruption or a stray hand-edit) | Refuses the malformed value. The setter only ever writes a non-empty string and `--unset` removes the key, so a non-string/empty value is never a legitimate write — treating it as "unset" would be the same silent demotion. |

> [!NOTE]
> **Proven by a negative test.** `test_no_silent_device_local_fallback` (in the `TestFailLoud` class, [`scripts/test_backend_selection.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_backend_selection.py)) asserts the load-bearing invariant: on an unregistered backend the resolver **raises**, does **not** return a `DeviceLocalBackend`, and **never even constructs one** — the device-local root stays uncreated. The class also pins the unregistered-backend raise, that the install message lists the registered alternatives, and the `vault`-without-`vault_path` companion guard. The guard's *full* bite arrives once V5-3 deletes the built-in `vault` backend (until then `vault` is always registered); the refusal machinery itself is shipped now.
>
> The `_install_plugin_message` helper is deliberately factored out so the [`doctor` preview](#the-doctor-storage-check) (task 4, shipped — reuses it at [`backend_selection.py#L293`](https://github.com/alexherrero/agentm/blob/main/scripts/backend_selection.py#L293)) renders a byte-identical message before any memory operation could refuse.

### The `doctor` storage check

`doctor` (the install-health skill) gains a **storage-backend preview** (task 4, shipped) — the operator's read-only way to see what selection *would* resolve before any memory operation could refuse. Invocable as `python3 scripts/backend_selection.py --doctor` (the `_doctor_main` CLI entry, [`backend_selection.py#L623`](https://github.com/alexherrero/agentm/blob/main/scripts/backend_selection.py#L623)) and read by the `doctor` skill's structural check `4d`. It reports the selected backend, where the selection came from, whether its plugin is registered, and that the `device-local` root is writable — and it resolves all of this **without constructing a backend** (`storage_preview`, [`backend_selection.py#L255`](https://github.com/alexherrero/agentm/blob/main/scripts/backend_selection.py#L255)), because construction would `mkdir` the device-local root.

**V5-7 Part 2 (shipped):** `_doctor_main` gains a `--requires <CAPS>` flag — comma-separated capability names (e.g. `--requires concurrent_writers,encryption`). When present, the flag validates all names against `dataclasses.fields(Capabilities)` before touching any backend (unknown names → error on stderr, exit 1, no backend constructed); then calls `select_backend(required=Capabilities(**{name: True for name in requested}))` and prints `PASS: backend '<protocol>' satisfies required capabilities: <caps>` (exit 0) or `FAIL: <CapabilityMismatchError message>` (exit 1). `StorageSelectionError` (plugin not installed, no vault path, corrupt config) also prints `FAIL` and exits 1. The three injection params `device_local_root`, `vault_lock_root`, and `vault_plugin_scripts` are forwarded to `select_backend` for test isolation. Proven by `TestDoctorRequires` (5 cases: unknown field, mismatch, satisfied cap with vault fixture, multiple unknown fields, no-flag backward compat) in [`scripts/test_backend_selection.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_backend_selection.py).

| Field | Type | Detail |
|---|---|---|
| `status` | `str` | `"ok"` / `"warn"` / `"fail"` — the row's verdict; drives the `--doctor` exit code (`1` only on `"fail"`). |
| `protocol` | `Optional[str]` | The resolved protocol name, or `None` when the explicit-selection read itself failed loud. |
| `line` | `str` | The single status line `doctor` prints — `storage [OK]/[WARN]/[FAIL] …`. |

| Status / exit | When | Note |
|---|---|---|
| `ok` / `0` | Selected backend registered and ready — `vault` seeded from a resolved `vault_path`, a writable `device-local` root, or a registered third-party protocol. | — |
| `warn` / `0` | `device-local` selected but its root is not writable (`_root_is_writable`, [`#L240`](https://github.com/alexherrero/agentm/blob/main/scripts/backend_selection.py#L240)). | Preventive, never build-blocking. |
| `fail` / `1` | Unregistered plugin; **or** `vault` selected with no resolvable `vault_path`; **or** the config file exists but is unparseable / names a non-string `storage.backend`. When `--requires` is passed: any unsatisfied capability or unknown capability name. | The unregistered-plugin line reuses the **same** `_install_plugin_message` ([`#L293`](https://github.com/alexherrero/agentm/blob/main/scripts/backend_selection.py#L293)) the [fail-loud guard](#the-fail-loud-guard) raises ([`#L196`](https://github.com/alexherrero/agentm/blob/main/scripts/backend_selection.py#L196)), so the preview and the live refusal are **byte-identical**. |

Proven by `TestStoragePreview` (6 cases, [`scripts/test_backend_selection.py#L324`](https://github.com/alexherrero/agentm/blob/main/scripts/test_backend_selection.py#L324)) for the standard preview path, and `TestDoctorRequires` (5 cases) for the `--requires` path. See the how-to [Choose a storage backend](Choose-A-Storage-Backend#what-okwarnfail-mean) for the operator-facing flow.

## `ProtocolError`

A `ValueError` subclass ([`scripts/storage_seam.py#L272`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_seam.py#L272)) raised by `BackendRegistry.register` on an empty or duplicate protocol name. Registering badly is a *programming* error, surfaced loudly — kept deliberately distinct from a registry *miss*, which is not an error at all (`get` → `None`).

## `StorageSelectionError`

A `RuntimeError` subclass ([`scripts/backend_selection.py`](https://github.com/alexherrero/agentm/blob/main/scripts/backend_selection.py)) raised by the [selection resolver](#the-selection-resolver) when the configured backend **cannot be produced** — the loud half of the [registry's absence-vs-fail-loud split](#the-backendregistry). It lives on the resolver (above the seam, in `backend_selection.__all__`), not in `storage_seam`, because turning a registry *miss* into a *fatal* refusal is the resolver's decision, not the registry's. Raised in **four** cases (see the [fail-loud guard](#the-fail-loud-guard)) across two functions. `select_backend` raises it once a protocol is chosen: when `storage.backend` names a backend whose [`registry.get()`](#the-backendregistry) returns `None` (the plugin isn't installed), or when `vault` is selected with no `vault_path` to seed it. `_configured_backend` — the reader of the explicit `storage.backend` value — raises it earlier: when the config file is present but unparseable (so a corrupt config cannot silently drop the selection and demote to `device-local`), or when `storage.backend` is present but not a non-empty string. Catching this — rather than papering over it with a silent `device-local` fall-back — is how the [`doctor` preview](#the-doctor-storage-check) (task 4, shipped) surfaces the install-the-plugin message before any memory operation can refuse (the V5-3 engine cutover, [ADR 0018](memory-storage-seam), then pointed the kernel at device-local exclusively). The preview does not *raise* the error — it converts each `StorageSelectionError` case into a `"fail"` row carrying the same message ([`storage_preview`](#the-doctor-storage-check), [`backend_selection.py#L255`](https://github.com/alexherrero/agentm/blob/main/scripts/backend_selection.py#L255)).

## `CapabilityMismatchError`

A `StorageSelectionError` subclass ([`scripts/backend_selection.py`](https://github.com/alexherrero/agentm/blob/main/scripts/backend_selection.py)) raised by [`select_backend`](#the-selection-resolver) when a caller passes `required=<Capabilities>` and the selected backend's `.capabilities` does not satisfy every `True` requirement. Shipped in **V5-7 Part 1**.

Constructor takes `(protocol: str, unsatisfied: list[str])` — the backend protocol name and the list of unsatisfied capability field names. `str(e)` produces a message naming both. Exposed in `backend_selection.__all__` alongside `StorageSelectionError`. Never raised when `required=None` (the default) — so existing call sites that omit `required` are unaffected.

| Aspect | Detail |
|---|---|
| Superclass | `StorageSelectionError` → `RuntimeError` |
| Constructor | `(protocol: str, unsatisfied: list[str])` |
| `str(e)` | Names the backend protocol and the unsatisfied fields by name. |
| Proven by | `TestCapabilityMatching` (8 new tests) in [`scripts/test_backend_selection.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_backend_selection.py). |

## The Tier taxonomy

The memory state splits across three tiers, distinguished by *who owns the truth* (`source` vs `derived`) and *whether an external sync layer may replicate the tree* (`syncs`). The taxonomy is **reserved in this part**: it designates the tiers and their sync/derived policy so the V6 vector/SQLite index lands on a contract that already exists. No index is built and no abstract is promoted here. For *why* the local index is pinned never-sync, see [Memory↔storage seam § The three tiers](Storage-Seam-Concepts#the-three-tiers-and-why-the-index-never-syncs).

### `Tier`

The three-tier enum ([`scripts/storage_seam.py#L350`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_seam.py#L350)). Two properties carry the policy; the one hard line is that **only `LOCAL_INDEX` is never-sync**.

| Member | Value | `.syncs` | `.derived` | What it holds |
|---|---|---|---|---|
| `SOURCE` | `"source"` | `True` | `False` | The synced, *authoritative* markdown the engine persists — the truth the others rebuild from. |
| `SHARED_ABSTRACTS` | `"shared-abstracts"` | `True` | `True` | Derived-but-portable summaries/abstractions; *may* sync (useful everywhere, rebuildable if a sync drops them). |
| `LOCAL_INDEX` | `"local-index"` | **`False`** | `True` | The V6 vector/SQLite index — device-local, **never** synced (a replicated database file is a corruption pattern). |

| Property | Returns | Detail |
|---|---|---|
| `.syncs` | `bool` | Whether an external sync layer may replicate this tier's tree. `True` for `SOURCE` and `SHARED_ABSTRACTS`; **`False` only for `LOCAL_INDEX`** — ties to [`Capabilities.sync`](#the-capabilities-type). |
| `.derived` | `bool` | Whether the tier is rebuildable from `SOURCE` (so never authoritative). `True` for both derived tiers; `False` only for `SOURCE`. |

### `TierLayout`

A frozen `dataclass` ([`scripts/storage_seam.py#L397`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_seam.py#L397)) designating a root [`Locator`](#the-locator-type) per tier. The defaults are tier-named placeholders a concrete backend (parts 2 / 4) overrides with its real roots — typically a synced-vault root for `source`/`shared_abstracts` and a device-local cache root for `local_index`. No tree is created — this is placement, not I/O.

| Member | Type | Detail |
|---|---|---|
| `source` | `Locator` | Root for the `SOURCE` tier. Default `Locator("source")`. |
| `shared_abstracts` | `Locator` | Root for the `SHARED_ABSTRACTS` tier. Default `Locator("shared-abstracts")`. |
| `local_index` | `Locator` | Root for the `LOCAL_INDEX` tier. Default `Locator("local-index")`. |
| `never_sync_root` | `property → Locator` | Returns the `local_index` root — the one tree that must never be replicated by sync. |
| `root_for(tier)` | `→ Locator` | The root locator designated for a given [`Tier`](#tier). |

> [!IMPORTANT]
> **The three roots must be distinct.** `__post_init__` raises `ValueError` if any two tier roots collide — so a derived tier can never overwrite the source it rebuilds from. The `source`/`shared_abstracts` placement is structurally separate from the `local_index` placement.

### `DerivedMaintenance`

An abstract base ([`scripts/storage_seam.py#L435`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_seam.py#L435)) naming the two derived-tier operations.

> [!NOTE]
> **Named / reserved — built in V6.** Both methods are abstract, so the class cannot be instantiated and **no implementation ships**: there is deliberately no concrete subclass in the module (a [scope guard](#the-scope-guard) asserts it). `reindex`/`changed_since` are *reserved names*, not working operations.

| Method | Signature | Detail |
|---|---|---|
| `reindex` | `reindex(tier: Tier) -> None` | Full rebuild of a derived `tier` from `SOURCE`. |
| `changed_since` | `changed_since(mtime: float) -> list[Locator]` | The *incremental feed* — source locators whose [`Info.mtime`](#the-info-type) is newer than `mtime`, so an incremental reindex touches only what moved. |

### The scope guard

The contract-only stance is enforced executably, not just by convention, in [`scripts/test_storage_seam.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_storage_seam.py): `DerivedMaintenance` cannot be instantiated (its `__abstractmethods__` are `{reindex, changed_since}`), the module exports **no concrete `DerivedMaintenance` subclass**, and the module imports **no DB / index / vector-framework library**. Together these are the structural proof that the index is *named*, not *built*, in this part.

## The `DeviceLocalBackend`

The first **concrete** `StorageBackend` ([`scripts/storage_device_local.py`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_device_local.py)) — the fresh-install default: plain markdown under `~/.agentm/memory/`, user-owned, no vault, no Drive, no service. It implements the [seven verbs](#the-storagebackend-abc) and the [`capabilities`](#the-capabilities-type) descriptor against the local filesystem, and registers under the `device-local` protocol name in the default [`registry`](#registry-module-default) at import. Bare markdown is the floor; a database is something a *plugin* may offer, never the kernel default. For *why* this is the floor, see [Memory↔storage seam § The first concrete backend](Storage-Seam-Concepts#the-first-concrete-backend-the-bare-markdown-floor).

> [!NOTE]
> **Engine cutover landed in V5-3 (v5.5.0).** `DeviceLocalBackend` works as a storage backend; the kernel's state functions (`harness_state_dir` / `read_state_file` / `write_state_file` / `phase_recall` / `resolve_documenter_context`) now route exclusively to device-local, completing the cutover ADR 0013 DC-6 deferred. See [ADR 0018](memory-storage-seam).

| Aspect | Detail |
|---|---|
| Protocol name | `device-local` (the module's `PROTOCOL` constant, [`storage_device_local.py#L53`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_device_local.py#L53)). Registered at import — the class, not an instance. |
| Root | `~/.agentm/memory/` (`Path.home()` + `.agentm/memory`). Created on construction — that *is* its "first use", so the root locator always resolves to a real directory. **Injectable** via `root=` so tests never touch the operator's real home. |
| `capabilities` | The single-machine floor as a positive statement: `concurrent_writers=False`, `conflict_files=False`, `encryption=False`, `sync=False`. |
| `conflict_strategy` | Inherits the seam floor `"none"` — on one machine there is nothing to reconcile. |

### How the verbs map to the filesystem

A locator maps to a path by joining its normalized parts under the root (`root / *locator.parts`). Because `Locator` rejects `..` and a leading slash at construction ([`InvalidLocatorError`](#invalidlocatorerror)), the join can never escape the root. `Path` is used **internally only** — every verb returns the seam's `Locator` / `Info`, never a `Path` (the [`check-storage-seam-no-path-leak`](CI-Gates) gate enforces this statically).

| Verb | Device-local behavior |
|---|---|
| `resolve(*parts)` | Builds a `Locator` from the joined parts. |
| `read(locator)` | `read_bytes().decode("utf-8")` — **byte-exact**, no newline translation, so content round-trips with the atomic writer. Missing path raises `FileNotFoundError` natively ([`#L109`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_device_local.py#L109)). |
| `write(locator, content)` | Composes [`vault_lock.atomic_write`](Vault-Write-Protocol) (temp + fsync + rename) — **never** an open-and-truncate, so a crash leaves prior bytes intact. Parent dirs created if absent ([`#L115`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_device_local.py#L115)). |
| `list(locator)` | Children sorted by key; `[]` if the path is absent or a file (pinned by the part-3 conformance suite). |
| `exists(locator)` | `True` if a file *or* directory is present. |
| `info(locator)` | `Info` carrying `mtime` and `size` (`0` for a directory); `stat()` raises `FileNotFoundError` if absent. |
| `mkdir(locator)` | Idempotent (`parents=True, exist_ok=True`). |

> [!IMPORTANT]
> **Crash-safe by composition, not reinvention.** `write` routes through the V5-0 [`atomic_write`](Vault-Write-Protocol) primitive rather than opening the target for truncation. Device-local needs **none** of the `vault_mutex` / content-hash CAS stack the synced [`VaultBackend`](#the-vaultbackend) layers on — it is single-machine, so the atomic file swap is sufficient. It also ships **no** sync, derived-index (`_index`), or conflict-merger machinery; an AST-based scope guard in [`scripts/test_storage_device_local.py`](https://github.com/alexherrero/agentm/blob/main/scripts/test_storage_device_local.py) asserts the module defines no such code and imports no DB / index library. Those concerns ride with the [vault backend](#the-vaultbackend) and the V6 index.

### Module exports (`storage_device_local`)

`storage_device_local.__all__` is `DeviceLocalBackend`, `PROTOCOL`. Importing the module has the side effect of registering `DeviceLocalBackend` under `device-local` in the seam's default [`registry`](#registry-module-default). Like the seam module it ships no `python -m` entrypoint — it is consumed in-process.

## The `VaultBackend`

The second **concrete** `StorageBackend` ([`scripts/storage_vault.py`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_vault.py)) — today's Obsidian/GDrive vault, reached *through the seam* rather than via the engine's old direct file access. It implements the [seven verbs](#the-storagebackend-abc) against the resolved per-project vault root and registers under the `vault` protocol name in the default [`registry`](#registry-module-default) at import ([`storage_vault.py#L241`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_vault.py#L241)). It is the synced, multi-writer counterweight to the device-local floor: where device-local declares the all-`False` floor, the vault backend declares the positive profile and overrides the conflict floor. For *why* the vault sits behind a seam at all, see [Memory↔storage seam](Storage-Seam-Concepts#what-the-seam-is-for).

> [!NOTE]
> **Engine cutover landed in V5-3 (v5.5.0).** `VaultBackend` implements the seam contract and is still registered under `vault` for selection. The kernel, however, no longer invokes it directly: as of V5-3 the kernel state functions are device-local only, and the vault backend is a plugin concern. The never-orphan invariant proves the wrap still reaches the same on-disk bytes the old path did — the wrap is conserved, not removed. See [ADR 0018](memory-storage-seam).

| Aspect | Detail |
|---|---|
| Protocol name | `vault` (the module's `PROTOCOL` constant, [`storage_vault.py#L89`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_vault.py#L89)). Registered at import — the class, not an instance. |
| Root | The resolved per-project vault path — a **required** ctor arg (the vault has no universal default, unlike device-local's `~/.agentm/memory/`). Created on construction (idempotent), so the root locator always resolves to a real directory ([`storage_vault.py#L104`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_vault.py#L104)). |
| `lock_root` | Injectable ctor arg for the `vault_mutex` local lock base; `None` → `vault_lock`'s default (`~/.cache/agentm/locks`). Tests inject a temp dir so the real cache and the operator's live vault are never touched. |
| `capabilities` | The synced, multi-writer profile — the positive contrast to device-local's floor: `concurrent_writers=True` (the mutex makes N writers safe), `conflict_files=True` (DriveFS surfaces "(conflicted copy)" siblings the engine must tolerate), `sync=True` (GDrive replicates the tree), `encryption=False` (no encryption at rest) ([`storage_vault.py#L139`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_vault.py#L139)). |
| `conflict_strategy` | Overrides the seam's `"none"` floor to `"whole-file"` ([`storage_vault.py#L160`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_vault.py#L160)) — see the [strategy note below](#a-named-policy-not-an-auto-merger). |

> [!IMPORTANT]
> <a name="a-named-policy-not-an-auto-merger"></a>**`"whole-file"` is a *named policy*, not an auto-merger.** The string names the reconciliation unit that [part-5 selection](#the-backendregistry) reads — it does **not** make agentm merge conflicts. The backend ships no merge machinery of its own. When GDrive sync diverges (two devices write the same file while offline), DriveFS materializes a "(conflicted copy)" sibling rather than losing a write; the existing detect-and-notice machinery — `harness_memory.detect_conflict_files` + the `conflict-merger` SessionStart hook — surfaces each conflict/base pair, and **resolution stays operator-by-hand judgment**. A line-level auto-merge would be a future CRDT strategy, not this. Device-local still inherits `"none"` (one machine, nothing to reconcile).

### How the verbs map to the vault filesystem

Like device-local, a locator maps to a path by joining its normalized parts under the root (`root.joinpath(*locator.parts)`), and the `..`/leading-slash rejection at `Locator` construction keeps the join confined to the root. `Path` is used **internally only**; every verb returns the seam's `Locator` / `Info` (the [`check-storage-seam-no-path-leak`](CI-Gates) gate enforces this). The load-bearing difference is `write`.

| Verb | Vault behavior |
|---|---|
| `read(locator)` | `read_bytes().decode("utf-8")` — **byte-exact**, no newline translation, so content round-trips with the atomic writer ([`storage_vault.py#L167`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_vault.py#L167)). |
| `write(locator, content)` | Composes the **full V5-0 stack**: serialize fleet-local writers on the one per-vault [`vault_mutex`](Vault-Write-Protocol), then land via a content-hash-CAS-guarded [`atomic_write`](Vault-Write-Protocol). The mutex serializes agentm sessions against each other; the CAS catches a *non-mutex* writer (GDrive sync / another device) landing between the pre-write read and the rename — the cross-device hazard device-local does not have ([`storage_vault.py#L173`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_vault.py#L173)). |
| `resolve` / `list` / `exists` / `info` / `mkdir` | Same shape as [device-local](#how-the-verbs-map-to-the-filesystem); the conformance suite holds both to the identical contract. |

> [!IMPORTANT]
> **The write composes `vault_lock` primitives, never the engine — DC-7 preserved.** `write` imports `vault_mutex`, `content_hash`, and `atomic_write` from [`vault_lock`](Vault-Write-Protocol) directly, so the seam never reaches up into the memory engine module. The CAS step (`_cas_atomic_write`) re-reads the target under the mutex and raises `ConcurrentModificationError` if its content changed since the pre-write read — the V5-0 `safe_write_replace_style` discipline, recomposed from primitives.

### Conformance + the never-orphan invariant

The vault backend is held to the **same objective contract** as device-local. `VaultConformance` ([`scripts/test_storage_conformance.py#L123`](https://github.com/alexherrero/agentm/blob/main/scripts/test_storage_conformance.py#L123)) runs the wrap through all 9 universal conformance checks over a scratch vault — including the byte-exact LF round-trip on the Windows runner — and skips only the derived-layer cases (the vault has no derived layer). `VaultRunConformanceReport` ([`#L140`](https://github.com/alexherrero/agentm/blob/main/scripts/test_storage_conformance.py#L140)) emits the per-backend pass report.

<a name="the-never-orphan-invariant"></a>`VaultNeverOrphanInvariant` ([`scripts/test_storage_vault.py#L360`](https://github.com/alexherrero/agentm/blob/main/scripts/test_storage_vault.py#L360)) proves the wrap **moves no data**: an old-way write (the `write_state_file` vault branch) is read back through the seam, a seam write is read back the old way, and both reach the same bytes at the same on-disk path. The wrap is a new *door* onto the existing vault, not a migration.

### Module exports (`storage_vault`)

`storage_vault.__all__` is `VaultBackend`, `PROTOCOL`. Importing the module registers `VaultBackend` under `vault` in the seam's default [`registry`](#registry-module-default). Like the other backend modules it ships no `python -m` entrypoint — it is consumed in-process.

## Module exports

`storage_seam.__all__` is the public surface: `InvalidLocatorError`, `normalize_key`, `Locator`, `Info`, `Capabilities`, `StorageBackend`, `ProtocolError`, `BackendRegistry`, `registry`, `Tier`, `TierLayout`, `DerivedMaintenance`. No `DerivedMaintenance` implementation and no `python -m` entrypoint — unlike the [process seam](Process-Seam), this module is consumed *in-process* by the engine, so it ships as an importable contract only. The two concrete backends that implement `StorageBackend` live in their own modules — see [`DeviceLocalBackend`](#the-devicelocalbackend) and [`VaultBackend`](#the-vaultbackend).

`backend_selection.__all__` (the resolver module above the seam) exports: `StorageSelectionError`, `CapabilityMismatchError` (V5-7 Part 1), `choose_protocol`, `select_backend`, `StoragePreview`, `storage_preview`. `Capabilities` is also imported (from `storage_seam`) so callers can construct a requirement descriptor without a separate import.

## Routing layer (V5-6)

> [!NOTE]
> **Status: complete** (V5-6, 2026-06-18). All five tasks shipped: `resolve_project` / `_vault_projects_dir` (task 1), `repo_registry` (task 2), `state_mode` vault→backend alias (task 3), gate extensions + conformance suite (task 4), ADR + docs (task 5). Decision: [ADR 0019](memory-storage-seam).

The third de-vaulting leg re-plumbs the kernel's routing/index mechanisms so they speak `Locator`s to this seam instead of building `vault_path() / …` filesystem paths directly. Three mechanisms are in scope:

| Mechanism | File | Status | Detail |
|---|---|---|---|
| `resolve_project` / `_vault_projects_dir` | `harness_memory.py:318,339` | **Shipped (task 1)** | `_vault_projects_dir(backend: StorageBackend) -> Locator`; `resolve_project` returns `{slug, project_locator, backend, project_root, layout}` — `project_locator` is a `Locator`, not a `Path`. `process_seam.py:161` uses `project_locator.key`. |
| `repo_registry` read/write | `repo_registry.py` | **Shipped (task 2)** | `registry_path(vault_path) -> Path` replaced by `registry_locator(backend: StorageBackend) -> Locator` ([`#L77`](https://github.com/alexherrero/agentm/blob/main/scripts/repo_registry.py#L77)). All five public functions take `backend: StorageBackend`. `_mutate_registry` no longer holds `vault_mutex` — `VaultBackend.write()` handles it; CAS retry loop handles cross-device races. On `obsidian-vault` resolves to identical path (LC-1, proven by LC-7 parallel-run test at [`test_harness_memory.py#L2265`](https://github.com/alexherrero/agentm/blob/main/scripts/test_harness_memory.py#L2265)). |
| `state_mode` resolver | `harness_memory.py:228` | **Shipped (task 3)** | `"vault"` aliases to `"backend"` at read time in both `_read_config_state_mode` and `_read_project_mode`; `agentm_config` normalizes `"vault"` → `"backend"` at write time; CLI `--state-mode` help surfaces the deprecation note — no operator action required. |

**Behavior-preserving invariant (LC-1):** on the `obsidian-vault` backend every mechanism resolves to the same bytes at the same path before and after. The task 1 parallel-run assertion confirmed byte-identical resolution for `resolve_project` / `_vault_projects_dir` before vault-shaped path construction was removed.

**Gate extensions (task 4 — shipped):**
- `check-storage-seam-no-path-leak.py` — Pass 2 (V5-6): scans `harness_memory.py` + `repo_registry.py` for seven named routing functions (`resolve_project`, `_vault_projects_dir`, `registry_locator`, `read_registry`, `write_registry`, `register_repo`, `unregister_repo`, `list_repos`) that return `pathlib.Path`. Driven by `ROUTING_FUNCTIONS`, `_ROUTING_FILENAMES`, `_routing_files()`, and an updated `_scan_source(names=…)` kwarg.
- `check-process-seam-import-direction.sh` — LC-8 block: scans the same two files for any `import storage_vault` / `from storage_vault import` and fails loud — a routing mechanism may import the seam, never a capability plugin.
- `storage_conformance.py` extended: `check_routing_repo_registry(make_backend)` proves the register/list/unregister cycle on any conforming backend; gated by `run_conformance(include_routing=False)` (default off, explicit opt-in); `RoutingConformanceReport` in `test_storage_conformance.py` runs `include_routing=True` on both backends.

For the intent and three-leg arc context, see [Seam-De-Vaulting-V5-6](../explanation/Seam-De-Vaulting-V5-6).

## Related

- [Memory↔storage seam](Storage-Seam-Concepts) — why the seam exists, why the engine never holds a path, and the graceful design choices behind the lean types.
- [Process seam](Process-Seam) — the *other* seam's reference: the read-only client a process calls. Same pattern, opposite direction.
- [Vault write protocol](Vault-Write-Protocol) — the `atomic_write` primitive `DeviceLocalBackend.write` composes, and the `vault_mutex` + content-hash CAS stack [`VaultBackend.write`](#the-vaultbackend) adds on top.
- [CI gates](CI-Gates) — the `check-storage-seam-no-path-leak` gate that enforces the no-`Path`-leak rule.
- [Memory-OS Architecture (V5)](agentm-hld) — the design context: storage-agnostic engine, device-local default, vault-behind-the-seam.
