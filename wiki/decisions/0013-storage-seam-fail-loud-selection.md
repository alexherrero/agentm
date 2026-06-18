# ADR 0013 — The memory↔storage seam: backend selection fails loud, never demotes

> [!NOTE]
> **Status:** accepted
> **Date:** 2026-06-13

## Context

The V5 unbundling repositioned `agentm` as a *storage-agnostic* memory engine: its default backing becomes device-local plain markdown (`~/.agentm/memory/`), and the existing Drive-synced Obsidian vault becomes one backing among several, reached through an interface rather than the one filesystem layout the engine was born assuming. [ADR 0011](0011-v5-unbundling-dev-loop.md) framed that repositioning ("the memory engine is what only agentm provides"); [ADR 0012](0012-vault-write-protocol.md) hardened the *vault write path* underneath it. Neither answered the structural question this work resolves: **how does the engine reach its markdown state without hard-coding "the vault is a directory tree on this disk", and how does it choose — and refuse — a backing by name?**

V5-1 is that seam, shipped as a five-part arc (parts 1–4 build the contract + two concrete backends + a conformance suite; part 5 ships selection). The trigger is the same one behind ADR 0012 — concurrent agents, one Claude Code session per sibling repo — plus the V5 direction of a fresh install working with **zero setup** (no vault to mount, no Drive to authenticate, no database to provision). A device-local default satisfies zero-setup; a seam makes the vault swappable behind it; selection picks between them. The hazard selection introduces is the reason this is ADR-worthy: a misconfigured or uninstalled backend must **never** silently degrade to device-local, because a silent demotion is exactly what mis-writes or orphans an operator's live vault.

**Open questions this decision resolves:**

- How does the engine read and write its markdown state without baking one filesystem layout into every call?
- What does the engine hold as a handle — a real path, or something that cannot leak a filesystem assumption?
- When a configured backend can't be produced (plugin uninstalled, `vault` selected with no `vault_path`, config corrupt), does selection guess, demote, or refuse?
- Where does the "what does a registry miss *mean*" policy live — in the lookup, or in selection?

## Decision

### 1. The engine reaches storage through a small interface of verbs that hand back an opaque `Locator`, never a `Path` (DC-1)

`scripts/storage_seam.py` defines the verbs a storage layer actually needs — `resolve` / `read` / `write` / `list` / `exists` (+ `info` / `mkdir`) — and they operate on, and return, the seam's own `Locator` type, **never** a `pathlib.Path`. A `Locator` is an opaque, backend-relative key exposing only namespace operations (`child`, `name`, `parts`); all I/O goes back through the verbs. So the engine, holding only `Locator` values, learns *no* filesystem assumption — it cannot `open()` a locator, join it against a disk path, or tell whether the bytes live on this device or elsewhere. That is exactly the property that lets a backend be swapped without the engine noticing.

**Why opaque, not a `Path`:** if a verb returned a `Path`, the leak would be silent and total — downstream code would treat it as a real handle and "swap the backend, the engine doesn't notice" would quietly stop being true. The vocabulary deliberately mirrors [fsspec](https://filesystem-spec.readthedocs.io/)'s method names and named-protocol registry, a well-trodden "one interface, many filesystems" shape — but the seam imports neither fsspec nor any database. Bare markdown is the floor.

### 2. The no-`Path`-leak rule is gate-enforced, AST-based, scoped to `storage_*.py` (DC-2)

`check-storage-seam-no-path-leak.py` parses each `scripts/storage_*.py` file and inspects every seam verb's *return annotation*, flagging any path type however nested (`Path`, `list[Path]`, `Path | None`, `Optional[Path]`, `pathlib.`-qualified, `os.PathLike`). It is the structural sibling of the process seam's `check-process-seam-import-direction` gate.

**Why the return annotation specifically, not a line-grep:** a filesystem backend legitimately writes `Path` all through its body (`root / key`); a grep for "Path" would drown in false positives. Only *handing one back* is the violation, and the return annotation is precisely where that shows up. The scan is scoped to `storage_*.py` so `test_*.py` fixtures (which construct a `Path`-returning backend to test the gate itself) are out of scope. It rides `check-all.sh` as the 18th gate — see [CI gates](CI-Gates).

### 3. A registry miss is *absence* (`get → None`), not an error; selection is where absence becomes fail-loud (DC-3)

A backend is chosen by *name*: it registers into `BackendRegistry` under a protocol name (`device-local`, `vault`), and selection resolves a configured name against the registry. Resolving an unregistered name does **not** raise — `registry.get` returns `None`. Deciding what that `None` *means* is selection's job, and keeping the decision there is the point.

**Why absence is not corruption:** the seam draws this distinction everywhere. A missing file degrades to `FileNotFoundError`; only a malformed key (a `..` escape) raises `InvalidLocatorError`. The registry extends that stance to *naming* — an unregistered protocol is absent, not malformed. Folding a raise into `get` would scatter the fail-loud policy across every lookup; leaving `get` honest about absence concentrates it in one place. Registering *badly*, by contrast, is a programming bug surfaced immediately — empty/duplicate name → `ProtocolError`; a non-backend → `TypeError`.

### 4. Selection refuses loudly with `StorageSelectionError` — it never demotes to device-local (DC-4)

The selection resolver (`scripts/backend_selection.py`) maps the on-device install config to a concrete, registered backend via the chain: an explicit `storage.backend` config value wins; else an existing `vault_path` selects the built-in `vault`, seeded byte-identically from `harness_memory.vault_path()`; else (fresh install, no vault) `device-local`. When the configured backend **can't be produced**, selection raises `StorageSelectionError` and **never** falls back silently. Three refusal cases:

- The named backend's plugin is **uninstalled** (`registry.get → None`, the part-1 resolve-as-absent signal) → raise, with a message naming the missing backend and the currently-registered alternatives.
- `vault` is selected but there is **no `vault_path`** to seed it → raise (a configuration error, surfaced not guessed).
- The install config exists but is **corrupt/unreadable**, or `storage.backend` is present-but-not-a-non-empty-string → raise. The resolver deliberately does **not** reuse `agentm_config._read_config`, whose tolerant contract collapses "file missing" and "file present but unreadable" into the same `None` — that collapse is itself a silent-demotion hole, so `_configured_backend` distinguishes the cases by hand.

**Why never demote:** a silent fall-back to `device-local` is the single failure that mis-writes or orphans the vault — an operator with a configured-but-uninstalled `vault` plugin would have the engine quietly start a fresh empty device-local store while their real memory sits untouched and ignored. A loud refusal forces the operator to fix the install (or change the config), which is always the correct outcome for a misconfiguration. This is the symmetric reflex to ADR 0012's "surface, never auto-merge" janitor and the seam's "reject a `..` key" rule: the failure that would otherwise hide is made loud at the moment it happens.

### 5. The `doctor` storage preview shares the resolver's code path so it can never drift from the runtime guard (DC-5)

`doctor`'s storage check invokes `backend_selection.storage_preview()` — a read-only, never-raising snapshot mapping the resolution to an `[OK]` / `[WARN]` / `[FAIL]` row. It reuses the resolver's own `_install_plugin_message` and resolution chain rather than reimplementing them, so the preview an operator sees in `doctor` is byte-identical to what the guard raises at runtime. Same idiom as the `check-worktree-slug` probe sharing the slug-resolution code.

### 6. V5-1 ships selection + refusal only — **not** the engine cutover (DC-6)

After part 5, selection can *resolve which* backend a config would use and refuse a misconfiguration — but the live `recall` / `reflect` / `save` / `evolve` and the five memory hooks are **byte-unchanged** and still access state the old way. Pointing the engine's public API at the selected backend (the **engine cutover**) is a separate, later step beyond V5-1; the derived index lands in V6.

**Why defer the cutover:** the selection-and-refusal contract is proven on its own — in tests, in `doctor` — before the live read/write path moves. Decoupling "decide and refuse" from "actually route" keeps each diff reviewable and makes the eventual cutover an expand→contract, never a flag day. It also means this ADR's fail-loud guarantee is in `main` and observable (via `doctor`) well before it gates real writes.

## Consequences

**Positive**

- **A misconfigured backend can never silently corrupt or orphan the vault.** The worst outcome is a loud, actionable refusal naming the missing plugin and the installed alternatives — never a quiet fresh-empty-store demotion.
- **The engine holds no filesystem assumption**, so the backend is swappable by name with the engine none the wiser — the seam's entire reason to exist, made structural by the opaque `Locator` and the AST gate.
- **One policy, one place.** "What a registry miss means" lives only in selection; the registry stays honest about absence and every lookup site inherits the same fail-loud without re-stating it.
- **The guard and its preview cannot drift.** `doctor`'s preview and the runtime refusal share a code path, so an operator's pre-flight check is exactly the runtime behavior.

**Negative**

- **A loud refusal is a hard stop, not a graceful degrade.** An operator whose `vault` plugin is uninstalled gets an error rather than a running (if wrong) engine. This is the intended trade — for memory state, a wrong-but-running engine is worse than a stopped one — but it means backend config errors block startup until fixed.
- **The fail-loud guard mostly sleeps until V5-3.** Until the built-in `vault` backend is removed (V5-3), both built-ins register at import, so `registry.get → None` only fires for a genuinely-unregistered third-party name. The guard is correct and tested now, but its primary blast radius (a configured-but-uninstalled `vault`) arrives later. ***[Resolved in V5-3 / ADR 0018]:** the guard is now load-bearing — `vault_path()` raises `StorageBackendNotInstalledError` when `storage.backend=vault` is configured + no vault accessible.*
- **The engine cutover is still pending**, so the seam's swap-the-backend property is proven but not yet *exercised* by the live engine. Selection resolving a backend is not the same as the engine using it. ***[Resolved in V5-3 / ADR 0018]:** `harness_state_dir`, `read_state_file`, `write_state_file`, `phase_recall`, and `resolve_documenter_context` now all use device-local paths exclusively; the kernel no longer routes state through the vault.*

**Load-bearing assumptions (with re-audit triggers)**

- **Text is the v1 currency** (`read → str`, `write` takes `str`) because the engine's state is markdown. **Re-audit trigger:** a backend needs a bytes or binary channel — then widen the verb contract deliberately, never reach past it.
- **`mtime` is the incremental-feed granularity** (`Info.mtime`, the future `changed_since`), chosen lean over a content-hash log. **Re-audit trigger:** the V6 index needs change-detection finer than mtime survives on a synced backend — then add a hash log as a separate tier concern.
- **The local index never syncs** (the three-tier `source` / `shared-abstracts` / `local-index` taxonomy, reserved before the index exists). **Re-audit trigger:** the V6 index lands and the tier roots must stay distinct — the `TierLayout` guard already enforces this structurally; revisit only if a derived tier ever needs to live on its source.
- **A wrong-but-running memory engine is worse than a stopped one**, which is the whole justification for fail-loud over graceful-degrade. **Re-audit trigger:** a deployment appears where memory is genuinely optional and a missing backend should degrade rather than block — then the refusal would need a config-gated soft mode (it has none today, by design).
- **Scaffolding decays with the model.** **Re-audit trigger:** the underlying model ships a new major version — re-audit the whole seam (the operator's standing harness-maintenance principle).

## Related

- [ADR 0011 — V5 unbundling: slim the dev loop](0011-v5-unbundling-dev-loop.md) — established the "memory engine is what only agentm provides" framing this seam realizes.
- [ADR 0012 — The vault-write protocol](0012-vault-write-protocol.md) — the write-safety floor the `vault` backend composes; this ADR's "never demote" is the selection-side sibling of 0012's "surface, never auto-merge".
- [ADR 0009 — On-host state-mode config](0009-on-host-state-mode-config.md) — the vault-vs-repo-local state mode whose vault branch the seam wraps.
- [Memory↔storage seam](Memory-Storage-Seam) — the narrative explanation: the verbs, the opaque locator, the three tiers, the two concrete backends.
- [Storage seam](Storage-Seam) — the verb-by-verb reference (signatures, `Locator` / `Info` / `Capabilities`, the `BackendRegistry` surface).
- [Memory↔process seam](Memory-Process-Seam) — the *other* V5 seam, facing up; same "small stable interface, gate-enforced" pattern.
- [CI gates](CI-Gates) — the `check-storage-seam-no-path-leak` gate enforcing the no-`Path` rule.
- [Choose a storage backend](Choose-A-Storage-Backend) — the operator-facing how-to for setting `storage.backend` and reading the `doctor` preview.
