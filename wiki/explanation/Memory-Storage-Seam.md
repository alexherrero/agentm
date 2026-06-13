# The memory↔storage seam

Why the memory engine reads and writes its state through a small interface of *verbs* — `resolve`, `read`, `write`, `list`, `exists` (+ `info`, `mkdir`) — instead of touching the filesystem directly, and why those verbs hand back the seam's own opaque locator type rather than a `pathlib.Path`. The seam is the second of the two introduced in the V5 unbundling: the [memory↔process seam](Memory-Process-Seam) faces *up* (a process calls the engine); this one faces *down* (the engine calls its storage). This note explains the shape the contract took. For the verb-by-verb contract, see [Storage seam](Storage-Seam).

> [!NOTE]
> **Contract only — part 1 of 5.** What ships today is the *abstract* contract: the verbs as an abstract `StorageBackend`, the `Locator`/`Info`/`Capabilities` types, and the gate that keeps a `Path` from crossing it. **No concrete backend ships yet.** The device-local backend (part 2), the conformance suite (part 3), the vault wrap (part 4), and backend selection + fail-loud (part 5) are forthcoming on the same plan. Everything below about *backends*, *selection*, and the *engine cutover* describes the contract those parts will conform to — not behavior that exists in `main`.

## What the seam is for

The V5 unbundling repositioned agentm as a *storage-agnostic* memory engine: its default backing becomes device-local (`~/.agentm/memory/`), and the Obsidian vault becomes a backing it reaches through an interface rather than the one filesystem layout it was born assuming. (The full architecture is the design note [Memory-OS Architecture](memory-os-architecture).) That repositioning created a question it did not answer: *how* does the engine read its markdown state without hard-coding "the vault is a directory tree on this disk"?

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
  a backend  (device-local — part 2 · vault wrap — part 4)
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
- **`Info`** carries `mtime` (epoch seconds) as its load-bearing field. That is a *deliberate* granularity choice: the `changed-since` incremental feed (part 3) reads mtime rather than maintaining a content-hash log — the lean v1 floor over a heavier mechanism.
- **`Capabilities`** is four booleans, all defaulting to the conservative `False`: `concurrent_writers`, `conflict_files`, `encryption`, `sync`. A backend *declares* what it can promise; selection and fail-loud (part 5) will *read* these. It is a dataclass precisely so the set can grow without breaking callers.

And the v1 currency is **text** — `read` returns `str`, `write` takes `str` — because the engine's state is markdown. A bytes channel is a named future extension, not a v1 obligation. This keeps the contract small enough to be obviously correct, which matters disproportionately for a module four more parts will build against.

One more property the contract only *declares*, deferring the work: a filesystem backend's `write` is specified to compose the existing [vault write protocol](Vault-Write-Protocol) (the V5-0 `atomic_write` + content-hash CAS + `vault_mutex`) rather than reinventing write-safety. The abstract contract here states the shape — "the write is durable and atomic, and the returned locator round-trips through `read`" — but the actual composition lands with the concrete backends in parts 2 and 4, not in this abstract ABC.

## What this seam does *not* touch

The seam sits strictly *below* the engine's frozen public API. `recall` / `reflect` / `save` / `evolve` and the five memory hooks (the DC-7 surface) are byte-unchanged by this work; the storage seam never widens that surface. It is an *internal* refactor boundary — how the engine reaches its bytes — not a new public capability. A process calling the engine sees no difference; only the engine's own state access is rerouted through the verbs (and that rerouting — the engine cutover — is itself a later part of the plan).

## Related

- [Storage seam](Storage-Seam) — the verb-by-verb reference (signatures, the `Locator`/`Info`/`Capabilities` types, degrade contracts).
- [Memory↔process seam](Memory-Process-Seam) — the *other* seam: why a process talks to the engine through a small stable client. Same "small stable interface, gate-enforced" pattern, facing the opposite direction.
- [CI gates](CI-Gates) — the `check-storage-seam-no-path-leak` gate enforcing the no-`Path` rule.
- [Memory-OS Architecture (V5)](memory-os-architecture) — the design note that introduced the storage-agnostic repositioning and the two-seam shape.
- [Vault write protocol](Vault-Write-Protocol) — the write-safety the filesystem backends will compose (parts 2 / 4), not reinvent.
