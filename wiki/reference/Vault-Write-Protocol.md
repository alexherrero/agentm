# Vault write protocol

The memory engine writes a Drive-synced vault safely when two or more agent sessions run at once. You must follow three operator habits to keep it safe. The *why* is the [Memory-storage seam design](memory-storage-seam). This page is the lookup.

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

Implementation occurs in `scripts/vault_lock.py`. This script provides `atomic_write`, `content_hash`, and `vault_mutex`. The file is vendored byte-identically into `harness/skills/memory/scripts/vault_lock.py`. The `vault-lock` mode of `check-vendored-parity` holds the files identical. CONS-1 merged the former standalone `check-vault-lock-parity.sh` into this one config-driven gate.

## What acquires the lock

- **Shared-vault writes** acquire the one per-vault mutex. These include `PLAN.md` / `progress.md` / `features.json`, the repo registry, and every `/memory save` / `/memory evolve` entry.
- **Replace-style shared files** additionally pass a **content-hash CAS** (`expected_hash`). The write re-reads and re-hashes the file inside the lock. The write aborts with `ConcurrentModificationError` if the content changed under it. Callers then re-read and retry.
- **Repo-local state** (`.harness/` in a checkout, the promotion cursor) takes the atomic writer for the `fsync` but **no mutex**. It is partitioned by construction. It is never in the synced vault.

## Operator habits that keep it safe

1. **Pin the vault "Available offline."** You mark the vault root *Available offline* in Google Drive / Finder. This ensures its files are always materialized. A *dataless* read (Drive streaming a file on demand) can stall an agent. In the worst case, it causes an `EDEADLK` hang. This is R4 rule 5 and the real `claude-code#40783` bite. Pinning removes the stall.
2. **Don't leave an agent-owned file open and dirty in Obsidian.** Obsidian's auto-merge / "file changed on disk" popup is an out-of-band writer. The mutex cannot see this writer. It can clobber an agent write. It can also resurrect stale bytes (Hazard #2). You close `PLAN.md` / `progress.md` in Obsidian before a `/work` session. Alternatively, you let the agent own them while it runs.
3. **Name cross-cutting captures uniquely under `_inbox/`.** You use `<timestamp>-<pid>-<slug>.md`. This ensures two sessions writing the inbox at once land disjoint files. They avoid racing one name. This ownership-partitioning convention keeps real contention ≈0. Different writers touch different files.

## When a conflict still happens

The `conflict-merger-session-start` hook sweeps the vault on session boot. It **surfaces** (never deletes) four Drive conflict-naming families. These families are `(conflicted copy …)`, `[Conflict]`, `Copy of …`, and `… (N).ext`. It only flags numbered duplicates when the un-numbered base co-exists. The hook also surfaces the DriveFS `lost_and_found/` dump. Drive never notifies about this dump. Each conflict is reported with its inferred base. You hand-merge them in Obsidian or via `diff`. A surfaced conflict in a vault-backed harness file requires action. You merge it. Then you re-run `/work` from the affected repo.

## See also

- [Memory-storage seam — The vault-write protocol](memory-storage-seam) — You find the full rationale, the five R4 rules, and the re-audit triggers here.
- [Run without a vault](Run-Without-A-Vault) — You use the repo-local state mode here. It is partitioned. It needs no mutex.
- [CI gates](CI-Gates) · [How-to](How-To) · [Reference](Reference) · [Home](Home)
