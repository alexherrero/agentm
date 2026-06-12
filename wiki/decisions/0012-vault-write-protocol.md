# ADR 0012 — The vault-write protocol (R4 "Phase-0" concurrency floor)

> [!NOTE]
> **Status:** accepted
> **Date:** 2026-06-12

## Context

The memory engine writes its durable state into a Google-Drive-synced Obsidian vault: the per-project `_harness/` files (`PLAN.md` / `progress.md` / `features.json`), the registry and config files, and every `/memory save` / `/memory evolve` entry. Through v4, those writes assumed **one writer**. The V4 #26 concurrency primitives (`safe_write_replace_style` with an **mtime-based** compare-and-swap, plus a `detect_conflict_files` sweep that matched only the `(conflicted copy` substring) were a first pass, but they had three gaps that make them unsafe the moment a second agent session runs: writes were `tmp.write_text(...)` → `os.replace` with **no `fsync`** and **no mutex**, the CAS keyed on **mtime** (which Drive re-download mangles), and the janitor saw only one of Drive's several conflict-naming families.

V5's direction is **concurrent agents — one Claude Code session per sibling repo** — writing the *same* shared vault at the same time. That is the trigger. Before N≥2 writers is safe, the vault needs a write-safety floor: mutual exclusion that does not itself corrupt the synced tree, a CAS currency that survives Drive's re-downloads, and an atomic writer the sync layer can never catch mid-flight. The spec is the R4 concurrency research (vault: `[[research-concurrent-vault-writes]]`) — its **five hard rules** and **hazards table** — and `ROADMAP-AgentMemoryV5` § V5-0.

This is deliberately **only the floor**. The singleton MCP broker and the local SQLite-WAL write-intent journal (§ V5-9 / "Phase 1") are *not* built here — but when they are, they must route their writes through *this* library as writer #2. The library is therefore the prerequisite, not the broker.

**Open questions this decision resolves:**

- Where do the lock and any sidecar state live, given the vault itself is synced by Drive?
- What is the CAS currency now that mtime is known-unreliable on a re-downloading sync layer?
- One global lock, or per-file locks? And what actually keeps contention near zero?
- How does the most-exposed write path — the `/memory` skill scripts, which cannot import top-level `scripts/` — get the same protocol without a forked, drifting copy?
- How much of `harness_memory.py` is allowed to change, given the memory engine's public contracts must stay frozen?

## Decision

### 1. Phase-0 floor only — advisory lock + content-hash CAS + fsync'd atomic writer + janitor + guidance (DC-1)

Build exactly the write-safety floor: a per-vault advisory mutex, a content-hash CAS, one canonical fsync'd atomic writer, the broadened conflict-janitor, and operator pin-offline / ownership-partitioning guidance. **Not** the singleton broker (V5-9), the local SQLite-WAL journal, a `GIT_DIR`-outside-vault history sidecar, or `age` encryption-at-rest.

**Why not build the broker now:** R4 sequences those as Phase 1 / V5-9 / optional. Phase 0 is the urgent floor that unblocks N≥2 writers, and the broker must itself route through this library — so the library is the dependency the broker waits on, not the reverse. Shipping the floor alone keeps the diff reviewable and the cutover an expand→contract, never a flag day.

### 2. One per-vault advisory mutex on a **local, non-synced** path; ownership-partitioning keeps real contention ≈0 (DC-2, DC-6)

A single global mutex per vault guards every shared-vault write. It is a lock **directory** created with `os.mkdir` (atomic / `O_EXCL`), living **outside the vault** at `~/.cache/agentm/locks/<sha256(realpath(vault))>/lock` (`XDG_CACHE_HOME` honored; `realpath` so symlink aliases to one physical vault collide on one lock). Liveness is the lockdir's own **mtime heartbeat** — touched every `stale`/2 seconds by a daemon thread, **no PIDs written** — and a heartbeat older than `stale` (10 s) is taken over. Acquisition is **bounded block-with-backoff** to `timeout` (≈10 s) then `LockTimeout`. Cleanup is `atexit` + main-thread `SIGINT`/`SIGTERM` handlers.

**Why the lock lives outside the vault** (R4 rule 1): a lockfile *inside* a Drive-synced tree would itself be synced — replicated, conflicted, and re-downloaded — which is exactly the corruption a mutex exists to prevent. Locks are machine-local by nature; cross-device mutual exclusion is impossible on Drive (R4's honest-limits list) and is explicitly out of scope.

**Why `mkdir`/mtime-heartbeat, not `fcntl(F_SETLK)` or a PID file** (R4 rule 3): POSIX advisory `fcntl` locks behave undefined over network/synced filesystems; PIDs are meaningless across machines and go stale on crash. An atomic `mkdir` with a self-describing mtime heartbeat needs no PID, and stale-takeover is a clean `os.rmdir` because the lockdir holds no files.

**Why one global lock, not per-file** (DC-2): R4 — writes are short and rare, so one lock suffices; per-file locking adds machinery for contention that **ownership-partitioning** already removes. Agents write different slugs → different files → no real contention; the mutex only has to defend against two writers hitting the *same* target's temp path.

**Why bounded block + stale-takeover** (DC-6): brief blocking is fine for sub-millisecond markdown writes; bounding prevents a wedged holder from deadlocking the fleet (Hazard #7); stale-takeover recovers a crashed writer without PIDs.

### 3. CAS keys on a **content hash (sha256)**, migrated additively from mtime (DC-3)

`safe_write_replace_style` gained `expected_hash`; `expected_mtime` keeps working but is documented-deprecated. When `expected_hash` is supplied, the writer re-reads the target inside the lock, re-hashes, and raises `ConcurrentModificationError` on mismatch. CAS is applied to **replace-style shared files** only; per-slug entry writes are race-free by partition (the mutex defends torn writes; CAS is unnecessary), and append-only `progress.md` natural-merges.

**Why content-hash over mtime** (R4 rule 4): Drive re-downloads rewrite mtimes, so an mtime CAS yields false "changed" *and* false "unchanged" verdicts. A sha256 of the bytes is the only currency that means "the content I read is still the content on disk."

**Why additive, not a hard swap:** preserves the `harness_memory.py` public contract and gives any out-of-tree caller a deprecation path rather than a breaking change.

### 4. Every vault write lands via one canonical writer: temp(same dir) → **fsync** → rename — plain `fsync`, not `F_FULLFSYNC` (DC-5)

A single `atomic_write(path, content)` is the *only* temp+rename implementation: it writes **bytes** (`content.encode("utf-8")`, never `write_text`) to `<path>.tmp` in the same directory, `os.fsync`s the temp fd, then `os.replace`s into place. All scattered inline copies were removed and routed here.

**Why bytes-mode:** `save.py` / `evolve.py` rely on byte-level writes to keep Obsidian-synced markdown byte-identical across Mac/Linux/Windows (the V4 Windows-CI LF-only fix). A centralized `write_text` would silently reintroduce CRLF translation — so the canonical writer is bytes-by-contract, asserted by a no-CRLF test.

**Why plain `fsync`, not `F_FULLFSYNC`** (DC-5): on macOS `fsync` flushes to the device but is not a full barrier; `F_FULLFSYNC` is the durable-barrier syscall but is costly and reserved for critical writes. R4 rule 2's note is explicit — the **cloud copy is the durability backstop**, so the property we actually need is that each *uploaded snapshot is internally consistent* (never torn), which plain `fsync(tmp)` before rename guarantees at the right cost for short markdown writes. `fsync ≠ durable` on macOS is accepted as a documented limit, not a bug.

### 5. A focused Python-only library — `scripts/vault_lock.py` — **vendored byte-identically** into the `/memory` skill, guarded by a parity gate (DC-4, DC-9)

The protocol is one module, `scripts/vault_lock.py` (`atomic_write`, `content_hash`, `vault_mutex`, `LockTimeout`, `ConcurrentModificationError`) — stdlib-only, independently testable, and exactly what the future V5-9 broker imports. The `/memory save` + `evolve` scripts get a **byte-identical vendored copy** at `harness/skills/memory/scripts/vault_lock.py`, enforced sha256-identical by a new gate (`check-vault-lock-parity.sh`, mirroring `check-lib-parity`).

**Why Python-only:** every vault *write* happens in Python (`harness_memory.py` + the `/memory` scripts); the shell hooks only read/detect, so they need nothing from this library.

**Why a new module, not folded into `harness_memory.py`:** that file is already ~1575 lines; a focused module is independently unit-proven and is the clean import surface for the later broker/CLI ("route through the same locked write library").

**Why vendor + gate, not a cross-tree import** (DC-9): the memory-skill scripts are self-contained by construction — the memory hooks resolve them across three install scopes (`.claude/skills/…` → `<prefix>/skills/…` → clone fallback), and top-level `scripts/` is **not** installed into target prefixes, so a `sys.path` import to `scripts/vault_lock.py` would `ImportError` in the two scopes that matter. Vendoring a co-located sibling is the only mechanism that survives all three (matching the existing `vec_index.py` "duplicate to avoid cross-script import coupling" idiom). **Why not a hand-trimmed minimal copy:** that guarantees divergence of a security-critical primitive; the byte-identity gate preserves DC-4 as "one *logical* library, enforced identical across two physical homes."

### 6. The write **internals** of `harness_memory.py` and the `/memory` scripts are edited; the public contracts and the five memory hooks are not (DC-7)

V5-0 rewires the write *path* — that is the whole point — but the memory engine's public API is frozen: recall / reflect / save / evolve keep their signatures and observable behavior (just safer underneath), and the five memory hooks (`conflict-merger-session-start`, `memory-recall-prompt-submit`, `memory-recall-session-start`, `memory-reflect-idle`, `memory-reflect-stop`) plus the `adapt-evaluator` / `memory-idea-researcher` agents stay byte-unchanged.

**Why this is safe to relax for the write path only:** the prior dev-loop-slim ([ADR 0011](0011-v5-unbundling-dev-loop.md)) held a blanket "memory engine byte-untouched" invariant; V5-0 narrows that to "public contracts untouched" *only* for the write internals it exists to harden. The proof is executable: `verify-memory-roundtrip.sh` plus the recall/reflect and conflict-merger hook tests stay green. The conflict-janitor's `.sh` hook *was* edited — but only to surface the broadened sweep (§7), with its observable contract (exit 0, `[conflict-merger]` notice, graceful-skip) preserved and proven by its original tests staying green: a broadening, not a contract change.

### 7. Broaden the conflict-janitor to the full R4 marker set + the DriveFS `lost_and_found/`; ship pin-offline + ownership-partitioning guidance (R4 rule 5)

`detect_conflict_files` (one centralized function, so both hook paths broaden at once) now matches **four** marker families — `(conflicted copy …)`, `[Conflict]`, `Copy of …`, and the `… (N).ext` numbered-duplicate family (the last only when the de-numbered base co-exists, so a standalone year-like name is never a phantom) — and additionally sweeps the DriveFS `lost_and_found/` dump (`~/Library/Application Support/Google/DriveFS/lost_and_found/`), which Drive never notifies about. The janitor **surfaces, never deletes**.

Operator guidance ships alongside (in the `/memory` skill and the [Vault Write Protocol](Vault-Write-Protocol.md) reference page): **pin the vault "Available offline"** so a dataless read can't stall an agent (R4 rule 5 — the EDEADLK / dataless-read bite); **don't leave agent-owned files open-dirty in Obsidian**, whose auto-merge popup is an out-of-band writer the mutex cannot see (Hazard #2); and the **`_inbox/` unique-naming convention** (`<timestamp>-<pid>-<slug>.md`) so cross-cutting captures are partitioned and never collide.

**Why surface, not auto-merge:** automated 3-way body merges are a Phase-1 optional (R4); a janitor that only reports cannot itself lose data, so precision (a few false "duplicates" surfaced) is the right bias when the alternative risks a missed real conflict.

## Consequences

**Positive**

- **The vault is write-safe at N≥2 single-machine writers.** The executable proof is the engine-level concurrency test (8 threads, barrier-released, racing one shared file → exactly one writer's payload survives, zero `.tmp` remnants) plus the `/memory save`/`evolve` concurrency suite. This is the floor that unblocks the concurrent-agents direction and is the hard precede for V5-10.
- **One logical writer, enforced.** `grep` proves the only temp+fsync+rename lives in `vault_lock.atomic_write`; the parity gate proves the two physical copies never drift. New write sites have exactly one correct path to call.
- **The CAS now means what it says.** A content-hash mismatch is a *true* concurrent-modification signal — including the operator hand-editing a shared file in Obsidian mid-write, which now correctly aborts-and-retries instead of silently losing one update.
- **The library is broker-ready.** V5-9's singleton daemon and any future CLI import `vault_lock` unchanged — the seam the roadmap asks for is already a self-contained module.

**Negative**

- **A vendored copy carries an eternal parity tax.** `vault_lock.py` now lives in two places kept identical only by a gate. Accepted because the install layout leaves no import path (DC-9); the gate makes drift a hard CI failure, not a silent risk.
- **CAS thrash under heavy operator hand-editing.** If the operator edits a shared file in Obsidian during an agent write, CAS aborts and the caller must re-read/re-apply. This is correct (no lost update), but a caller without bounded retry could livelock — callers retry with a bound.
- **`fsync ≠ durable` on macOS is a documented limit, not a fix.** A power-loss in the sub-millisecond window between rename and the cloud upload can lose the *latest* write; the cloud copy backstops everything older. Acceptable for the threat model (concurrency, not crash-durability); revisit only if a crash-durability requirement appears.

**Load-bearing assumptions (with re-audit triggers)**

- **Writes stay short and rare**, so one global lock and a 10 s stale window are ample. **Re-audit trigger:** a write path ever approaches seconds (very large files, slow Drive materialization) — then the stale window and heartbeat cadence must grow, or a writer could be wrongly taken over and two writers could overlap.
- **Top-level `scripts/` is not installed into target prefixes**, which is the entire reason the skill scripts vendor rather than import. **Re-audit trigger:** the install layout ever ships `scripts/` into prefixes — then collapse the vendored copy back to an import and retire the parity gate (DC-9's explicit trigger).
- **The cloud copy is the durability backstop**, making plain `fsync` the right barrier. **Re-audit trigger:** the vault ever runs on a non-synced / local-only backend (the V5-1 device-local default) — then re-evaluate whether `F_FULLFSYNC` or an explicit journal is warranted.
- **Cross-device mutual exclusion remains out of scope** because it is impossible on Drive and the chat-surfaces-read-only rule keeps cloud-side writers out. **Re-audit trigger:** a second machine ever becomes a *writer* — then Phase-0's local-only locks are insufficient and the broker (V5-9) becomes mandatory, not optional.
- **Scaffolding decays with the model.** **Re-audit trigger:** the underlying model ships a new major version — re-audit the whole protocol (the operator's standing harness-maintenance principle).

## Related

- [ADR 0009 — On-host state-mode config](0009-on-host-state-mode-config.md) — the on-host (vault-redirected vs repo-local) state mode whose **vault** branch this protocol hardens; the repo-local branch is partitioned by construction and takes the atomic writer without a mutex.
- [ADR 0010 — Vault internal taxonomy](0010-vault-internal-taxonomy.md) — the vault layout the ownership-partitioning convention (per-slug subtrees, `_inbox/` unique-naming) builds on to keep contention ≈0.
- [ADR 0011 — V5 unbundling: slim the dev loop](0011-v5-unbundling-dev-loop.md) — established the "memory engine is what only agentm provides" framing and the byte-untouched invariant this ADR narrows (DC-7) for the write path.
- [Vault Write Protocol](Vault-Write-Protocol.md) — the operator-facing reference: the lock path, the CAS currency, the pin-offline rule, and the partitioning conventions.
- [Memory-OS Architecture (V5)](memory-os-architecture) — the V5 design this floor unblocks; the V5-9 broker and V5-10 must route their writes through this library.
- The R4 concurrency-write research (vault: `[[research-concurrent-vault-writes]]`) — the five hard rules + hazards table this protocol adopts verbatim; and `ROADMAP-AgentMemoryV5` § V5-0 (this floor) / § V5-9 (the broker that depends on it).
- The V4 #26 concurrency primitives (`safe_write_replace_style`, `detect_conflict_files` in `harness_memory.py`) — the mtime-CAS + single-marker first pass this supersedes by extension: additive `expected_hash`, the four-family janitor, fsync, and the mutex beneath.
