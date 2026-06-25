# Vault write protocol

How the memory engine writes a Drive-synced vault safely when two or more agent sessions run at once, and the three operator habits that keep it safe. The *why* is the [Memory-storage seam design](memory-storage-seam); this page is the lookup.

## ⚡ Quick Reference

| Property | Value | Why |
|---|---|---|
| Lock location | `~/.cache/agentm/locks/<sha256(realpath vault)>/lock` | **Outside** the synced vault — a lock inside Drive would itself sync/conflict (R4 rule 1) |
| Lock mechanism | `mkdir`/`O_EXCL` dir + mtime heartbeat (no PID) | `fcntl` is undefined over synced FS; no PID survives a crash/another machine (R4 rule 3) |
| Heartbeat / stale / timeout | touch every 5 s · stale at 10 s · acquire-timeout ≈10 s | short/rare writes block briefly; stale-takeover recovers a crashed writer (DC-6) |
| CAS currency | **content hash (sha256)**, not mtime | Drive re-downloads rewrite mtimes (R4 rule 4) |
| Atomic write | bytes → `<path>.tmp` (same dir) → `fsync` → `os.replace` | the sync layer never sees a torn file; bytes-mode keeps LF byte-exact |
| Durability barrier | plain `fsync`, **not** `F_FULLFSYNC` | the cloud copy is the backstop; we need each snapshot *internally consistent*, not crash-durable (DC-5) |
| Scope | one machine, N≥2 writers | cross-device exclusion is impossible on Drive — locks are local-only by design |

Implementation: `scripts/vault_lock.py` (`atomic_write`, `content_hash`, `vault_mutex`), vendored byte-identically into `harness/skills/memory/scripts/vault_lock.py` and held identical by the `check-vault-lock-parity` gate.

## What acquires the lock

- **Shared-vault writes** — `PLAN.md` / `progress.md` / `features.json`, the repo registry, and every `/memory save` / `/memory evolve` entry — acquire the one per-vault mutex.
- **Replace-style shared files** additionally pass a **content-hash CAS** (`expected_hash`): the write re-reads and re-hashes inside the lock and aborts with `ConcurrentModificationError` if the content changed under it. Callers re-read and retry.
- **Repo-local state** (`.harness/` in a checkout, the promotion cursor) takes the atomic writer for the `fsync` but **no mutex** — it is partitioned by construction, never in the synced vault.

## Operator habits that keep it safe

1. **Pin the vault "Available offline."** In Google Drive / Finder, mark the vault root *Available offline* so its files are always materialized. A *dataless* read (Drive streaming a file on demand) can stall an agent — in the worst case an `EDEADLK` hang (R4 rule 5; the real `claude-code#40783` bite). Pinning removes the stall.
2. **Don't leave an agent-owned file open and dirty in Obsidian.** Obsidian's auto-merge / "file changed on disk" popup is an out-of-band writer the mutex cannot see — it can clobber an agent write or resurrect stale bytes (Hazard #2). Close `PLAN.md` / `progress.md` in Obsidian before a `/work` session, or let the agent own them while it runs.
3. **Name cross-cutting captures uniquely under `_inbox/`.** Use `<timestamp>-<pid>-<slug>.md` so two sessions writing the inbox at once land disjoint files instead of racing one name. This is the ownership-partitioning convention that keeps real contention ≈0 — different writers touch different files.

## When a conflict still happens

The `conflict-merger-session-start` hook sweeps the vault on session boot and **surfaces** (never deletes) four Drive conflict-naming families — `(conflicted copy …)`, `[Conflict]`, `Copy of …`, and `… (N).ext` (numbered duplicates, flagged only when the un-numbered base co-exists) — plus the DriveFS `lost_and_found/` dump that Drive never notifies about. Each is reported with its inferred base for hand-merge in Obsidian or via `diff`. A surfaced conflict in a vault-backed harness file means: merge it, then re-run `/work` from the affected repo.

## See also

- [Memory-storage seam — The vault-write protocol](memory-storage-seam) — the full rationale, the five R4 rules, and the re-audit triggers.
- [Run without a vault](Run-Without-A-Vault) — the repo-local state mode, which is partitioned and needs no mutex.
- [CI gates](CI-Gates) · [How-to](How-To) · [Reference](Reference) · [Home](Home)
