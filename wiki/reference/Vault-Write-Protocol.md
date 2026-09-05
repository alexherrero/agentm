# Vault write protocol

The memory engine writes a Drive-synced vault safely when two or more agent sessions run at once. You must follow three operator habits to keep it safe. The *why* is the [Memory-storage seam design](memory-storage-seam). This page is the lookup.

## ⚡ Quick Reference

| Property | Value | Why |
|---|---|---|
| Lock location | `~/.cache/agentm/locks/<sha256(realpath vault)>/lock` | **Outside** the synced vault — a lock inside Drive would itself sync/conflict (R4 rule 1) |
| Lock mechanism | `mkdir`/`O_EXCL` dir + mtime heartbeat (no PID) | `fcntl` is undefined over synced FS; no PID survives a crash/another machine (R4 rule 3) |
| Heartbeat / stale / timeout | touch every 5 s · stale at 10 s · acquire-timeout ≈10 s | short/rare writes block briefly; stale-takeover recovers a crashed writer (DC-6) |
| CAS currency | **content hash (sha256)**, not mtime | Drive re-downloads rewrite mtimes (R4 rule 4) |
| Atomic write | bytes → `<path>.<pid>.<uuid4>.tmp` (same dir) → `fsync` → `os.replace` | the sync layer never sees a torn file; bytes-mode keeps LF byte-exact; the temp name is unique per writer so concurrent writers of one target never rename the same name |
| Durability barrier | plain `fsync`, **not** `F_FULLFSYNC` | the cloud copy is the backstop; we need each snapshot *internally consistent*, not crash-durable (DC-5) |
| Scope | one machine, N≥2 writers | cross-device exclusion is impossible on Drive — locks are local-only by design |

Implementation occurs in `scripts/vault_lock.py`. This script provides `atomic_write`, `content_hash`, and `vault_mutex`. The file is vendored byte-identically into `harness/skills/memory/scripts/vault_lock.py`. The `vault-lock` mode of `check-vendored-parity` holds the files identical. CONS-1 merged the former standalone `check-vault-lock-parity.sh` into this one config-driven gate.

## What acquires the lock

- **Shared-vault writes** acquire the one per-vault mutex. These include `PLAN.md` / `progress.md` / `features.json`, the repo registry, and every `/memory save` / `/memory evolve` entry.
- **Replace-style shared files** additionally pass a **content-hash CAS** (`expected_hash`). The write re-reads and re-hashes the file inside the lock. The write aborts with `ConcurrentModificationError` if the content changed under it. Callers then re-read and retry.
- **Repo-local state** (`.harness/` in a checkout, the promotion cursor) takes the atomic writer for the `fsync` but **no mutex**. It is partitioned by construction. It is never in the synced vault.

## Write-time stamps and the volume gate

A write that names a memory type carries four stamps — a record kind never carries them. `save_entry()` fills these in when you omit them:

| Stamp | Default | Set explicitly by |
|---|---|---|
| `lifecycle` | the contract's `default_lifecycle` (`active` unless `standards/storage-rules.md` names another) | `save --always-load`, which stamps `pinned` instead |
| `source` | `conversation` | `capture.py` (`operator-direct`, or `external-fetch` with a `source_url`), `save.py`'s CLI entry (`operator-direct`), `ingest.py` (`external-fetch`) |
| `filing_confidence` | `high` | `low` for a contract-default type, a demoted MEDIUM-confidence mined candidate, or a `memory_capture` file |
| `trust` | mapped from `source` through the contract's `sources` map | never set directly |

`trust` is the newest of the four (filing v2's write path, task 5). It's a property of the transport, never of how plausible the content reads: `operator-direct` and `conversation` read `trusted`; `external-fetch` and `email` read `untrusted`.

A write routed through `filing_engine.apply()` — the step behind `capture.py` and `reflect.py`'s mined candidates — asks the volume gate first: the contract's `thresholds.daily_write_cap` (200 by default, `0` disables it) against how many memories the corpus already holds for the day the write counts under — the day the arriving note's own `captured` stamp names when the caller set one, wall clock otherwise, so the gate and the writes-per-day reading never disagree about which day a write belongs to. Past the cap the write fails with a message naming the count, the cap, and the file to raise it in. A repeat that would only reinforce a note already home never reaches the gate. A direct `save_entry()` call — `/memory save`, an ingest write — bypasses the gate entirely: the cap targets unreviewed, automated volume.

See [Memory daemon reference](Memory-Daemon) for the contract's own vocabulary (which of these keys the daemon and the Python writer each actually read) and the daemon's matching gate on its own capture path.

## Operator habits that keep it safe

1. **Pin the vault "Available offline."** You mark the vault root *Available offline* in Google Drive / Finder. This ensures its files are always materialized. A *dataless* read (Drive streaming a file on demand) can stall an agent. In the worst case, it causes an `EDEADLK` hang. This is R4 rule 5 and the real `claude-code#40783` bite. Pinning removes the stall.
2. **Don't leave an agent-owned file open and dirty in Obsidian.** Obsidian's auto-merge / "file changed on disk" popup is an out-of-band writer. The mutex cannot see this writer. It can clobber an agent write. It can also resurrect stale bytes (Hazard #2). You close `PLAN.md` / `progress.md` in Obsidian before a `/work` session. Alternatively, you let the agent own them while it runs.
3. **Trust the writer's retry.** Filing v2's write path moved every capture off `_inbox/` onto its class directory. Two sessions can now legitimately want the same settled slug. `capture.py` decides a destination and then writes it; if a concurrent writer lands on that name first, it decides again against the disk. A matching twin gets reinforced; a genuine namesake gets the filing engine's `~dup` mark instead. The `save_entry()` writer re-checks that the target still doesn't exist, inside its mutex, right before writing — the loser of that race gets a `FileExistsError` and retries. You name nothing by hand.

## When a conflict still happens

The `conflict-merger-session-start` hook sweeps the vault on session boot. It **surfaces** (never deletes) four Drive conflict-naming families. These families are `(conflicted copy …)`, `[Conflict]`, `Copy of …`, and `… (N).ext`. It only flags numbered duplicates when the un-numbered base co-exists. The hook also surfaces the DriveFS `lost_and_found/` dump. Drive never notifies about this dump. Each conflict is reported with its inferred base. You hand-merge them in Obsidian or via `diff`. A surfaced conflict in a vault-backed harness file requires action. You merge it. Then you re-run `/work` from the affected repo.

## See also

- [Memory-storage seam — The vault-write protocol](memory-storage-seam) — You find the full rationale, the five R4 rules, and the re-audit triggers here.
- [Run without a vault](Run-Without-A-Vault) — You use the repo-local state mode here. It is partitioned. It needs no mutex.
- [Review flagged memories](Review-Flagged-Memories) — working the low-confidence and flagged notes these stamps produce.
- [CI gates](CI-Gates) · [How-to](How-To) · [Reference](Reference) · [Home](Home)
