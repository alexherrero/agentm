# The memory↔storage seam

Why the memory engine reads and writes its state through a small storage interface instead of touching files directly, and why, out of the box, that interface points at plain markdown on your own disk. This is the downward-facing one of the two seams the V5 unbundling introduced — the [memory↔process seam](Memory-Process-Seam) faces *up* (a process calls the engine), this one faces *down* (the engine calls its storage). For the verb-by-verb contract, see [Storage seam](Storage-Seam).

> [!NOTE]
> **All 5 parts complete (V5-1, shipped in v5.0.0).** Part 1 shipped the *abstract* contract: the verbs as an abstract `StorageBackend`, the `Locator`/`Info`/`Capabilities` types, the `conflict_strategy` slot, the named-backend registry, the three-tier source/derived taxonomy, and the gate that keeps a `Path` from crossing it. Part 2 added the **first concrete backend** — [device-local](Storage-Seam#the-devicelocalbackend), plain markdown under `~/.agentm/memory/`. Part 3 added the **backend-agnostic conformance suite** — the deterministic gate that holds every backend to one objective contract (the verb behaviors, the byte-identical LF-exact round-trip, the `list`-on-absent pin, and the gated derived-layer rebuildability invariant); it is **not** a new `check-*` gate — it rides the existing cross-OS `[T]` unit-test step, which is what makes the LF-exact case real on the Windows runner. Part 4 added the **second concrete backend** — [the vault wrap](Storage-Seam#the-vaultbackend), today's synced Obsidian/GDrive vault reached through the seam, with `write` composing the full V5-0 `vault_mutex` + content-hash CAS + `atomic_write` stack and a never-orphan invariant proving the wrap moves no data. Part 5 — the final part — shipped **backend selection + the fail-loud guard** (plus the `Capabilities`-read and a `doctor` storage preview): a `storage.backend` config key and a resolver (`scripts/backend_selection.py`) that maps config → a registered backend, refusing loudly with `StorageSelectionError` when the named backend's plugin is uninstalled (and never demoting to device-local). The fail-loud-never-demote decision is in the [Memory-storage seam design](memory-storage-seam). **One thing remains beyond V5-1: no derived index or abstract promotion ships yet** — the index lands in V6. Everything below about the *index* describes the contract V6 will conform to — not behavior that exists in `main`. The engine cutover shipped later in V5-3 (v5.5.0); the [Memory-storage seam design § 3](memory-storage-seam) is the canonical account of what it changed.

## What the seam is for

The V5 unbundling repositioned agentm as a *storage-agnostic* memory engine: its default backing becomes device-local (`~/.agentm/memory/`), and the Obsidian vault becomes a backing it reaches through an interface rather than the one filesystem layout it was born assuming. (The full architecture is the design note [Memory-OS Architecture](agentm-hld).) That repositioning created a question it did not answer: *how* does the engine read its markdown state without hard-coding "the vault is a directory tree on this disk"?

Two answers lead this page, because a storage-agnostic engine has to settle two things: what it reads *through*, and what it reads *by default*. The first answer is the interface below — a small set of verbs that lets the backing swap without the engine noticing. The second is the default that interface points at: **plain markdown files on the user's own disk**. That default is deliberate, not a placeholder. A memory engine should work the moment it's installed — no vault to mount, no Drive to authenticate, no database to provision — so the floor has to be the simplest thing that already satisfies "storage-agnostic," and bare `.md` files under `~/.agentm/memory/` are exactly that. Anything heavier — a synced vault, a database, a remote service — is a job a *plugin* takes on, never a cost the engine imposes on a fresh install. (The deep-dive on that floor, and the write-safety it does and doesn't need, is [The first concrete backend](#the-first-concrete-backend-the-bare-markdown-floor) below.)

The tempting answer is "the engine already knows it's files — let it keep calling `pathlib` and `open()`." That bakes one filesystem layout into every read and write the engine makes. Swapping the backing — to a device-local default, to a future bytes or remote channel — would then mean editing the engine itself, everywhere it touched a path. The seam is the deliberate alternative. It is a small interface (`scripts/storage_seam.py`) of the verbs a storage layer actually needs:

- **resolve** — "make me a handle for this place" (the naming verb; produces a `Locator`).
- **read / write** — "give me the text here" / "put this text here" (text is the v1 currency).
- **list / exists** — "what's under here?" / "is anything here?".
- **info / mkdir** — the two ergonomic verbs: metadata (carrying `mtime`) and idempotent directory creation.

The vocabulary deliberately mirrors [fsspec](https://filesystem-spec.readthedocs.io/)'s method names and its named-protocol registry pattern — a well-trodden public shape for "one interface, many filesystems" — but the seam imports neither fsspec nor any database. Bare markdown is the floor; the dependency is a convention borrowed, not a library taken on. A backend that needs an operation the verbs don't expose is a deliberate widening of the contract, not a quiet reach past it.

## Why the engine never holds a path

The verbs operate on, and return, the seam's own `Locator` type — **never** a `pathlib.Path`. This is the load-bearing rule of the whole seam.

A `Locator` is an opaque, backend-relative key. It exposes only namespace operations (`child`, `name`, `parts`) — never filesystem I/O. All reading and writing goes back through the verbs. So the engine, which only ever holds `Locator` values, learns *no filesystem assumption*: it cannot accidentally `open()` a locator, cannot join it against a disk path, cannot tell whether the bytes behind it live on this device or somewhere else. That is exactly the property that lets a backend be swapped without the engine noticing — the seam's entire reason to exist.

If a verb returned a `Path` instead, the leak would be silent and total: the engine would hold a real filesystem handle, code downstream would start treating it as one, and "swap the backend, the engine doesn't notice" would quietly stop being true. The locator is opaque *so that* it can't be mistaken for the thing it names.

```
  memory engine  (holds only Locator values — no filesystem handle)
        │  calls verbs
        ▼
  storage_seam.py  ── verbs return ──►  Locator / Info  (never pathlib.Path)
        │
        ▼
  a backend  (device-local — part 2 · vault wrap — part 4; both shipped)
     internally: root / key  ← Path lives HERE, never crosses up
```

A backend that *is* a filesystem will of course use `Path` internally — `root / key` is the natural implementation. That's fine. The rule is precise: a `Path` may live inside a backend; it may never be *handed back across the seam*. And because a rule that only lives in prose decays, it ships as an executable gate, not a guideline.

## Why that's gate-enforced

[`check-storage-seam-no-path-leak.py`](https://github.com/alexherrero/agentm/blob/main/scripts/check-storage-seam-no-path-leak.py) is the enforcement. It is the structural sibling of the process seam's `check-process-seam-import-direction` — same idea (a one-line invariant made executable), different invariant.

What makes it more than a grep is that it is *static and AST-based*. It parses each `scripts/storage_*.py` source file and inspects, for every seam **verb**, the verb's *return annotation* — flagging any path type referenced anywhere in it, however nested (`Path`, `list[Path]`, `Path | None`, `Optional[Path]`, a `pathlib.`-qualified or `os.PathLike` form). Targeting the return annotation specifically is the part a line-grep cannot reach: a filesystem backend legitimately writes `Path` all through its body (`root / key`), and a grep for "Path" would drown in those false positives. Only *handing one back* is the violation, and the return annotation is precisely where that shows up.

The scan is scoped to `scripts/storage_*.py` — the seam contract module today, and the concrete backend modules as they adopt the convention. `test_*.py` never matches that glob, so the conformance fixtures (which legitimately construct a `Path`-returning backend to *test the gate itself*) are out of scope. It exits `0` clean, `1` on a leak, `2` on a setup error, and rides the local battery (`check-all.sh`) as the 18th gate, grouped right after its process-seam sibling. See [CI gates](CI-Gates) for the row.

## Why these types, and why text

Three small types travel with the verbs, and each is deliberately lean:

- **`Locator`** is normalized at construction and **root-confined**: a leading slash is silently relativized (a locator is *always* backend-relative), and a `..` segment is rejected outright with `InvalidLocatorError`. The seam has no upward-traversal semantics — that is the safety property that keeps a key from ever escaping the backend root. The root locator is the empty string.
- **`Info`** carries `mtime` (epoch seconds) as its load-bearing field. That is a *deliberate* granularity choice: the `changed_since` incremental feed (named in [the three-tier contract](#the-three-tiers-and-why-the-index-never-syncs), built in V6) reads mtime rather than maintaining a content-hash log — the lean v1 floor over a heavier mechanism.
- **`Capabilities`** is four booleans, all defaulting to the conservative `False`: `concurrent_writers`, `conflict_files`, `encryption`, `sync`. A backend *declares* what it can promise; selection and fail-loud (part 5, shipped) *read* these. It is a dataclass precisely so the set can grow without breaking callers.

And the v1 currency is **text** — `read` returns `str`, `write` takes `str` — because the engine's state is markdown. A bytes channel is a named future extension, not a v1 obligation. This keeps the contract small enough to be obviously correct, which matters disproportionately for a module four more parts will build against.

One more property the contract only *declares*, deferring the work: a filesystem backend's `write` is specified to compose the existing [vault write protocol](Vault-Write-Protocol) (the V5-0 `atomic_write` + content-hash CAS + `vault_mutex`) rather than reinventing write-safety. The abstract contract here states the shape — "the write is durable and atomic, and the returned locator round-trips through `read`" — but the actual composition lands with the concrete backends in parts 2 and 4, not in this abstract ABC.

## The three tiers, and why the index never syncs

The seam's state is not one undifferentiated tree. It splits across three tiers, and the split exists to answer a single sharp question the V6 work will otherwise stumble on: *what is allowed to sync?* The taxonomy (`Tier`, `TierLayout`, `DerivedMaintenance` — the verb-level surface is in [Storage seam § The Tier taxonomy](Storage-Seam#the-tier-taxonomy)) is **reserved here**, before any index exists, precisely so the answer is settled in the contract rather than improvised when the index lands.

The three tiers fall along two axes — *who owns the truth* and *what may replicate*:

| Tier | Authority | Syncs? | Why |
|---|---|---|---|
| **source** | authoritative | yes | The markdown the engine persists. It is the truth; it syncs so every device has it. |
| **shared-abstracts** | derived | yes | Summaries rebuilt *from* source. Useful on every device and cheap to regenerate, so they may ride the sync layer — but they are never authoritative. |
| **local-index** | derived | **never** | The V6 vector/SQLite index. Device-local, full stop. |

**The one hard line is that the local index never syncs**, and it is worth being explicit about why, because it is the entire reason the tier exists as a distinct category. A vector or SQLite index is a single binary file mutated in place. An external sync layer (Dropbox, iCloud, Syncthing — the kind the [vault backend](Vault-Write-Protocol) sits behind) replicates such a file by copying bytes and resolving divergence with "last writer wins" or a "(conflicted copy)" duplicate. Neither is safe for a live database: a half-replicated index is a *corrupt* index, not a stale one. So the index is pinned device-local in the contract — each device rebuilds its own from the synced source. This is the same property the [`Capabilities.sync`](Storage-Seam#the-capabilities-type) flag describes from the backend's side; the tier taxonomy is where the engine's *own* layout commits to honoring it. The structural guard against getting it wrong is in `TierLayout`: the three roots must be distinct, so a derived tier can never be placed on top of the source it rebuilds from.

The source/derived distinction is the other half. Source is the one tier that is never `derived`; everything else is rebuildable from it. That is what makes the never-sync stance *affordable*: losing a device-local index costs nothing but CPU, because the source — which does sync — is sufficient to regenerate it. Derived data is disposable by construction; only the source is precious.

### Why name the maintenance ops now, before building them

`DerivedMaintenance` reserves two operation *names* — `reindex` (rebuild a derived tier from source) and `changed_since` (the incremental feed of source locators newer than a watermark) — but ships no implementation. The class is abstract; there is deliberately no concrete subclass, and a scope guard test asserts both that fact and that the module imports no database or index library. The index itself is V6.

Reserving the names without the bodies is a deliberate sequencing call, not an unfinished one. The V6 index is a large piece of work, and the riskier failure mode is not "we lack a reindex function" — it is "the index lands and *then* we discover the engine has no shaped place to call it from, so it bolts on awkwardly." Naming `reindex`/`changed_since` here means V6 plugs into an affordance the contract already anticipated: an incremental feed keyed on `mtime` (the lean granularity locked in over a content-hash log), a full rebuild that reads from source. The shape is decided while it is cheap to decide — in an abstract class with no callers — rather than under the pressure of a half-built index. The "named, not built" property is made *structural* (an un-instantiable abstract class) rather than left as a comment, so the boundary cannot quietly erode.

## Why a miss is absence, not an error

A backend is chosen by *name*. The seam mirrors fsspec's named-protocol registry (the same source as the verb vocabulary): a backend registers under a protocol name — `device-local`, `vault` — and selection later resolves a configured name against the registry to pick one. The `BackendRegistry` that holds that mapping ships now; the two real backends register into it in parts 2 and 4.

The load-bearing design choice is what the registry does on a *miss*. Resolving an unregistered name does **not** raise — `get` returns `None`. That is deliberate, and it is the same distinction the seam draws everywhere else: **absence is not corruption.** A missing file degrades to `FileNotFoundError`; only a malformed key (a `..` escape) raises `InvalidLocatorError`. The registry extends that stance to *naming* — an unregistered protocol is absent, not malformed. The registry's job is to *report* absence, not to decide what it means.

Deciding what it means is part 5's job, and keeping that decision *there* is the point. Selection reads the `None` and fails loud: if the configured backend doesn't exist, that is a fatal misconfiguration the operator must see, not a default to paper over. Folding the raise into `get` would scatter that policy across every lookup; leaving `get` honest about absence concentrates the fail-loud in one place. The split is absence here, fail-loud there.

Registering *badly*, by contrast, is a programming bug and is surfaced immediately — an empty or duplicate name raises `ProtocolError`, and a non-backend, the abstract base itself, or an instance-instead-of-a-class raises `TypeError`. Refusing a silent duplicate is the same reflex as rejecting a `..` key: the failure that would otherwise hide (one backend quietly shadowing another) is made loud at the moment it happens. A miss is the one case that is *not* a bug, and so the one case that does not raise.

## The first concrete backend: the bare-markdown floor

The contract above is an empty stage until something stands on it. The [device-local backend](Storage-Seam#the-devicelocalbackend) is the first thing that does — and the choice of *what* the first backend should be is itself a design statement, not an arbitrary "we had to pick one."

It is **plain markdown under `~/.agentm/memory/`**: a user-owned directory of `.md` files, with no service, no daemon, and no embedded database. This is the deliberate *floor*. The V5 unbundling's whole premise is that a memory engine should be storage-agnostic and should default to something a fresh install can use with zero setup — no vault to mount, no Drive to authenticate, no database to provision. Bare files on the user's own disk are the simplest thing that satisfies that, and the simplest thing is what the kernel should ship. Anything heavier — a SQLite or vector store, a synced vault, a remote service — is something a *plugin* may offer, never the default the engine assumes. (A database on a synced path is, specifically, a corruption pattern the [three-tier taxonomy](#the-three-tiers-and-why-the-index-never-syncs) already encodes as never-sync; making the default a database would bake that hazard into the floor.)

Two consequences of "single machine, plain files" shape the implementation, and both are *absences* worth naming:

- **It composes write-safety rather than reinventing it.** `write` routes through the V5-0 [`atomic_write`](Vault-Write-Protocol) primitive (temp file + fsync + rename) and never opens the target for truncation — so a crash mid-write leaves the prior bytes intact, never a half-written file. But device-local needs *none* of the `vault_mutex` / content-hash CAS machinery the synced [vault backend](Storage-Seam#the-vaultbackend) layers on: with one machine and one writer, there is no second process to coordinate with. The capability descriptor says so plainly — `concurrent_writers=False`, `sync=False` — and the [`conflict_strategy`](Storage-Seam#conflict_strategy---str-property) inherits the seam's floor `"none"`, because on one machine there is nothing to reconcile. The vault backend, which *does* sit behind a sync layer, is where the CAS stack earns its keep and where `"whole-file"` is declared — though `"whole-file"` only *names* the reconciliation unit selection reads; conflict resolution stays operator-by-hand (the `conflict-merger` hook detects and notices, it does not auto-merge).
- **It ships no sync, no derived index, no conflict merger.** Those belong to the synced backend and the V6 index, not here — device-local has no conflicts by construction. That this machinery is *absent* (not merely unused) is made structural: an AST-based scope guard in the backend's tests asserts the module defines no merger / reindex / `_index`-promotion code and imports no database or index library. The floor stays a floor; it cannot quietly accrete the heavier backend's concerns.

The `~/.agentm/memory/` path is not incidental. It is the home the operator-locked `AgentMemory → Agent` rename (V5-3) reconciles to, so the name is fixed by a decision made elsewhere in the arc — kept exactly as designed rather than chosen for this backend in isolation.

## The second concrete backend: the vault as a wrap, not a migration

The device-local floor is the *new* default; the [vault backend](Storage-Seam#the-vaultbackend) is the *existing* state given a new door. At V5-1 time, the engine still reached its synced Obsidian/GDrive vault by touching the filesystem directly. The vault backend re-presents that same vault — the same files, at the same paths — through the seam verbs instead. The design statement here is one of **conservation, not conversion**: wrapping the vault must not move, reshape, or migrate a single byte of an operator's live memory. It is a second way *in* to the existing tree so that V5-3 (v5.5.0) could point the kernel's state functions at device-local without ever touching the vault data itself.

That conservation is why the part ships a *never-orphan invariant* rather than trusting the wrap by inspection. The invariant writes the old way and reads it back through the seam, writes through the seam and reads it back the old way, and asserts both reach the same bytes at the same on-disk path. If the wrap had quietly relocated state — a different root, a normalized filename, a re-encoded line ending — the invariant would catch it. The wrap is provably a no-op on the data; only the *access path* changes.

What the vault backend does *add* over device-local is the write-safety the synced, multi-writer reality demands — and it adds it by **composition**, not reinvention. `write` serializes fleet-local agentm sessions on the one per-vault advisory mutex, then lands through a content-hash-CAS-guarded atomic write that catches a *non-mutex* writer (a GDrive sync, another device) slipping in between the pre-write read and the rename. This is the full V5-0 [vault write protocol](Vault-Write-Protocol) the abstract contract always specified a filesystem backend would compose — assembled here from the `vault_lock` primitives directly, so the seam never reaches up into the engine module it sits below.

The conflict posture is the one place the vocabulary invites a misreading worth heading off. The vault declares `conflict_strategy = "whole-file"` and `conflict_files = True` — but `"whole-file"` is a *name*, not a *merger*. agentm ships no automated whole-file (or any) merge. When Drive sync diverges, DriveFS materializes a "(conflicted copy)" sibling rather than losing a write; the existing `detect_conflict_files` + `conflict-merger` SessionStart machinery *surfaces* each conflict/base pair to the operator, and resolution stays human judgment. The string `"whole-file"` exists for part-5 selection to *read* when it decides how to treat a divergence — declaring the unit of reconciliation, not performing one. A line-level automatic merge would be a future CRDT strategy; it is deliberately not what shipped.

Like device-local, the vault backend *exists and works* as a storage backend. Part 5's selection still resolves it by name when `vault_path` is set (the `VaultBackend` is still registered), but the kernel no longer invokes it directly — the engine cutover that took it out of the kernel's call path shipped in V5-3. For what that cutover changed, see the [Memory-storage seam design § 3](memory-storage-seam), the canonical account.

## What this seam does *not* touch

The seam sits strictly *below* the engine's frozen public API. `recall` / `reflect` / `save` / `evolve` and the five memory hooks (the DC-7 surface) are byte-unchanged by V5-1; the storage seam never widens that surface. It is an *internal* refactor boundary — how the engine reaches its bytes — not a new public capability.

Part 2 does **not** change the public API, and neither does part 5. Part 5 narrows to selection + the fail-loud guard (plus the `Capabilities`-read and the `doctor` preview): it lets the kernel *resolve which* backend a config would use — `device-local` on a fresh install, `vault` when a `vault_path` exists, a named plugin when `storage.backend` is set — and refuse loudly when the named backend's plugin is uninstalled. The engine cutover was a separate, later step: it shipped in V5-3 (v5.5.0), not part 5. See the [Memory-storage seam design § 3](memory-storage-seam) for what it changed.

## Related

- [Storage seam](Storage-Seam) — the verb-by-verb reference (signatures, the `Locator`/`Info`/`Capabilities` types, the `BackendRegistry` surface, degrade contracts).
- [Memory↔process seam](Memory-Process-Seam) — the *other* seam: why a process talks to the engine through a small stable client. Same "small stable interface, gate-enforced" pattern, facing the opposite direction.
- [CI gates](CI-Gates) — the `check-storage-seam-no-path-leak` gate enforcing the no-`Path` rule.
- [Vault write protocol](Vault-Write-Protocol) — the `atomic_write` primitive the device-local backend composes for crash-safe writes (and the `vault_mutex` / CAS stack it deliberately does *not* need).
- [Memory-OS Architecture (V5)](agentm-hld) — the design note that introduced the storage-agnostic repositioning and the two-seam shape.
- [Vault write protocol](Vault-Write-Protocol) — the write-safety the filesystem backends will compose (parts 2 / 4), not reinvent.
