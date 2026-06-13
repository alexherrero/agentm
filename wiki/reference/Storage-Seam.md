# Storage seam reference

The memory↔storage contract ([`scripts/storage_seam.py`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_seam.py)) — the small interface of *verbs* the memory engine calls instead of touching the filesystem directly. A backend implements the verbs; the engine consumes the seam's own `Locator` type and so learns no filesystem assumption. This page documents the **abstract contract that ships today** (V5-1 part 1 of 5): the seven verbs as an abstract `StorageBackend`, the three value types, the named-backend registry, and the no-`Path`-leak gate. For *why* the seam is shaped this way, see [Memory↔storage seam](Memory-Storage-Seam).

> [!NOTE]
> **Contract only — no concrete backend ships here.** The abstract `StorageBackend`, the `Locator`/`Info`/`Capabilities` types, and the [`BackendRegistry`](#the-backendregistry) all ship now; **no concrete backend does.** The device-local backend (part 2), the conformance suite (part 3), the vault wrap (part 4), and selection + fail-loud (part 5) are forthcoming. The `StorageBackend` below is abstract; the `_MemoryBackend` used in tests is a fixture, not a shipped backend. The registry holds backend *classes* but ships with none registered into the default `registry` — the two real backends register under their protocol names in parts 2 / 4. Anything this page marks *(part N)* is a forward reference, not current behavior.

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
| `mtime` | `float` | Modification time, epoch seconds. **The `changed-since` granularity** (the incremental feed named in part 3) — the *lean* v1 choice over a content-hash log. |

## The `Capabilities` type

What a backend can promise — the per-backend descriptor a backend declares, a frozen `dataclass`. Four booleans, all defaulting to the conservative floor (`False`). Selection and fail-loud (part 5) *read* these; the contract only *defines* them. A dataclass so the set can grow.

| Field | Default | Meaning |
|---|---|---|
| `concurrent_writers` | `False` | Safe under more than one writer process. |
| `conflict_files` | `False` | The backend may surface conflict copies (e.g. a sync layer's "(conflicted copy)" files) the engine must tolerate. |
| `encryption` | `False` | Content is encrypted at rest by the backend. |
| `sync` | `False` | The backend's tree is replicated by an external sync layer — the property that makes a SQLite index *on* it a corruption pattern (why the local index is designated never-sync in part 3). |

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
| Non-existent directory | The backend's choice of empty-or-raise — **pinned by the conformance suite** (part 3). |
| Otherwise | The immediate children as `Locator`s, never paths. |

### `exists(locator) -> bool`

Whether anything (file *or* directory) is present at `locator`.

### `info(locator) -> Info`

Return `Info` for `locator` (raises if absent). Carries `mtime` — the granularity `changed-since` reads (part 3).

### `mkdir(locator) -> Locator`

Ensure a directory exists at `locator`; return it. **Idempotent** — calling it on an existing directory is not an error.

### `capabilities -> Capabilities` (property)

What this backend promises — see [Capabilities](#the-capabilities-type).

## `InvalidLocatorError`

A `ValueError` subclass raised by `normalize_key` (and therefore `Locator` construction / `resolve`) when a key escaped or malformed its backend-relative namespace — today, a `..` segment. It signals a *caller bug* (an unsafe key), kept deliberately distinct from the absent-data degrade a backend reports for a missing `read` (`FileNotFoundError`).

## The `BackendRegistry`

A hand-rolled name→backend registry ([`scripts/storage_seam.py#L247`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_seam.py#L247)) — the fsspec named-protocol pattern, mirrored, importing neither fsspec nor any DB. A backend registers under a **protocol name** (`device-local`, `vault`); selection (part 5) resolves a configured name against the registry to choose a backend. The registry stores backend **classes**, not instances — selection instantiates the chosen one.

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

A process-wide default `BackendRegistry` instance ([`scripts/storage_seam.py#L312`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_seam.py#L312)) the real backends register into. It ships **empty** in this part — the device-local backend (part 2) and the vault wrap (part 4) register under their protocol names when they arrive.

## `ProtocolError`

A `ValueError` subclass ([`scripts/storage_seam.py#L237`](https://github.com/alexherrero/agentm/blob/main/scripts/storage_seam.py#L237)) raised by `BackendRegistry.register` on an empty or duplicate protocol name. Registering badly is a *programming* error, surfaced loudly — kept deliberately distinct from a registry *miss*, which is not an error at all (`get` → `None`).

## Module exports

`storage_seam.__all__` is the public surface: `InvalidLocatorError`, `normalize_key`, `Locator`, `Info`, `Capabilities`, `StorageBackend`, `ProtocolError`, `BackendRegistry`, `registry`. No concrete backend, and no `python -m` entrypoint — unlike the [process seam](Process-Seam), this module is consumed *in-process* by the engine, so it ships as an importable contract only.

## Related

- [Memory↔storage seam](Memory-Storage-Seam) — why the seam exists, why the engine never holds a path, and the graceful design choices behind the lean types.
- [Process seam](Process-Seam) — the *other* seam's reference: the read-only client a process calls. Same pattern, opposite direction.
- [CI gates](CI-Gates) — the `check-storage-seam-no-path-leak` gate that enforces the no-`Path`-leak rule.
- [Memory-OS Architecture (V5)](memory-os-architecture) — the design context: storage-agnostic engine, device-local default, vault-behind-the-seam.
