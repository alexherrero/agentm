# Storage seam reference

The memory↔storage contract ([`scripts/storage_seam.py`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_seam.py)) — the small interface of *verbs* the memory engine calls instead of touching the filesystem directly. A backend implements the verbs; the engine consumes the seam's own `Locator` type and so learns no filesystem assumption. This page documents the **abstract contract plus its two concrete backends** (V5-1 parts 1–4 of 5): the seven verbs as an abstract `StorageBackend`, the three value types, the `conflict_strategy` slot, the named-backend registry, the three-tier source/derived taxonomy, the no-`Path`-leak gate, the [`DeviceLocalBackend`](#the-devicelocalbackend) that implements all of it over plain markdown (since part 2), the backend-agnostic conformance suite that holds both backends to one objective contract (since part 3), and — since part 4 — the [`VaultBackend`](#the-vaultbackend) that wraps the synced vault write path behind the same seam. For *why* the seam is shaped this way, see [Memory↔storage seam](Memory-Storage-Seam).

> [!NOTE]
> **Parts 1–4 shipped: the contract, two concrete backends, and the conformance gate.** The abstract `StorageBackend`, the `Locator`/`Info`/`Capabilities` types, the `conflict_strategy` slot, the [`BackendRegistry`](#the-backendregistry), and the [three-tier taxonomy](#the-tier-taxonomy) (`Tier` / `TierLayout` / `DerivedMaintenance`) shipped in part 1; the [`DeviceLocalBackend`](#the-devicelocalbackend) — the fresh-install default, plain markdown under `~/.agentm/memory/` — ships in part 2 and registers under `device-local` in the default [`registry`](#registry-module-default); the backend-agnostic conformance suite ships in part 3 and runs both backends against the shared contract — it is **not** a new `check-*` gate, it rides the existing cross-OS `[T]` unit-test step; the [`VaultBackend`](#the-vaultbackend) ships in part 4, wrapping the synced vault write path (the full V5-0 `vault_mutex` + content-hash CAS + `atomic_write` stack) behind the seam and registering under `vault`. Still forthcoming: backend selection + fail-loud (part 5) — reading [`Capabilities`](#the-capabilities-type) + [`conflict_strategy`](#conflict_strategy---str-property) + the registry's resolve-as-absent to choose a backend and fail loud on a configured-but-uninstalled one. **No index or abstract-promotion ships** — `DerivedMaintenance` remains an abstract class with **no concrete subclass** (`reindex`/`changed_since` are *reserved names*), and the actual derived index lands in V6. **The engine is not yet wired to the seam** — `recall`/`reflect`/`save`/`evolve` and the five hooks are byte-unchanged; routing the engine's public API through a chosen backend is part 5. The `_MemoryBackend` used in the contract tests is a fixture, not a shipped backend. Anything this page marks *(part 5)* or *(V6)* is a forward reference, not current behavior.

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

What a backend can promise — the per-backend descriptor a backend declares, a frozen `dataclass`. Four booleans, all defaulting to the conservative floor (`False`). Selection and fail-loud (part 5) *read* these; the contract only *defines* them. A dataclass so the set can grow.

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

## `ProtocolError`

A `ValueError` subclass ([`scripts/storage_seam.py#L272`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_seam.py#L272)) raised by `BackendRegistry.register` on an empty or duplicate protocol name. Registering badly is a *programming* error, surfaced loudly — kept deliberately distinct from a registry *miss*, which is not an error at all (`get` → `None`).

## The Tier taxonomy

The memory state splits across three tiers, distinguished by *who owns the truth* (`source` vs `derived`) and *whether an external sync layer may replicate the tree* (`syncs`). The taxonomy is **reserved in this part**: it designates the tiers and their sync/derived policy so the V6 vector/SQLite index lands on a contract that already exists. No index is built and no abstract is promoted here. For *why* the local index is pinned never-sync, see [Memory↔storage seam § The three tiers](Memory-Storage-Seam#the-three-tiers-and-why-the-index-never-syncs).

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

The first **concrete** `StorageBackend` ([`scripts/storage_device_local.py`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_device_local.py)) — the fresh-install default: plain markdown under `~/.agentm/memory/`, user-owned, no vault, no Drive, no service. It implements the [seven verbs](#the-storagebackend-abc) and the [`capabilities`](#the-capabilities-type) descriptor against the local filesystem, and registers under the `device-local` protocol name in the default [`registry`](#registry-module-default) at import. Bare markdown is the floor; a database is something a *plugin* may offer, never the kernel default. For *why* this is the floor, see [Memory↔storage seam § The first concrete backend](Memory-Storage-Seam#the-first-concrete-backend-the-bare-markdown-floor).

> [!NOTE]
> **The engine does not use this backend yet.** Constructing a `DeviceLocalBackend` and calling its verbs works, but the live memory engine (`recall`/`reflect`/`save`/`evolve` + the five hooks) is byte-unchanged and still accesses its state the way it does today. The fresh-engine cutover — routing the public API through `device-local` — is part 5.

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

The second **concrete** `StorageBackend` ([`scripts/storage_vault.py`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_vault.py)) — today's Obsidian/GDrive vault, reached *through the seam* rather than via the engine's old direct file access. It implements the [seven verbs](#the-storagebackend-abc) against the resolved per-project vault root and registers under the `vault` protocol name in the default [`registry`](#registry-module-default) at import ([`storage_vault.py#L241`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_vault.py#L241)). It is the synced, multi-writer counterweight to the device-local floor: where device-local declares the all-`False` floor, the vault backend declares the positive profile and overrides the conflict floor. For *why* the vault sits behind a seam at all, see [Memory↔storage seam](Memory-Storage-Seam#what-the-seam-is-for).

> [!NOTE]
> **The engine does not use this backend yet.** Constructing a `VaultBackend` and calling its verbs works, and the [never-orphan invariant](#the-never-orphan-invariant) proves it reaches the same on-disk bytes the engine's old direct path does — but the live engine (`recall`/`reflect`/`save`/`evolve` + the five hooks) is byte-unchanged and does not yet route through it. Choosing the vault backend behind the public API is part 5.

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

## Related

- [Memory↔storage seam](Memory-Storage-Seam) — why the seam exists, why the engine never holds a path, and the graceful design choices behind the lean types.
- [Process seam](Process-Seam) — the *other* seam's reference: the read-only client a process calls. Same pattern, opposite direction.
- [Vault write protocol](Vault-Write-Protocol) — the `atomic_write` primitive `DeviceLocalBackend.write` composes, and the `vault_mutex` + content-hash CAS stack [`VaultBackend.write`](#the-vaultbackend) adds on top.
- [CI gates](CI-Gates) — the `check-storage-seam-no-path-leak` gate that enforces the no-`Path`-leak rule.
- [Memory-OS Architecture (V5)](memory-os-architecture) — the design context: storage-agnostic engine, device-local default, vault-behind-the-seam.
