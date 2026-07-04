# Single-repo (vault-less) state mode

Why the harness can run on one repo with no Obsidian / Google Drive / vault dependency — and the resolution model that decides where a phase write lands. The design is in the [Memory-storage seam](memory-storage-seam); for the operator steps, see [Run without a vault](Run-Without-A-Vault).

## Why this mode exists

Before this work, harness state was vault-resident: `read_state_file` / `write_state_file` resolved `PLAN.md`, `progress.md`, and friends under `<vault>/projects/<slug>/_harness/`, and a write with no vault configured hard-raised. That made the harness unusable on a machine without a mounted vault — you could neither signal which mode a repo wanted nor write any phase state.

Single-repo mode removes the vault as a hard dependency. It is strictly **additive and gated**: when a vault is present and the repo is not in local mode, the vault write path behaves byte-for-byte as before. Local mode only changes behaviour for repos that opt into it.

## The state-resolution model

Where a phase write lands is decided by **two on-host configuration layers** — no vault is ever consulted to resolve the mode, because configuration is on-host only; the vault holds data, never config:

- **The repo-local marker**: `<repo>/.harness/.project-mode` is the optional per-repo override. When it reads `local`, state I/O routes to `<repo>/.harness/<file>` and never raises for a missing vault. It lives in the repo so it is reachable on a vault-less machine, and it **wins when present**.
- **The device-level default**: `state_mode` in `<install-prefix>/.agentm-config.json`, either `"local"` or `"backend"` (`"backend"` was formerly named `"vault"`). This says how agentm runs on this machine, read vault-free. `install.sh --local-state` writes it at install time; `scripts/agentm_config.py --state-mode local` (or `--state-mode backend`) flips it afterwards without re-running the installer. It is consulted when no repo-local marker is set; absent, the default is backend (vault-backed, via the active storage backend).

> [!NOTE]
> **V5-6 (shipped 2026-06-18) — `state_mode: vault` now aliases to `state_mode: backend` at read time.** The non-local value was renamed from `"vault"` to `"backend"` in the routing-plane de-vaulting (V5-6, task 3). Existing `state_mode: vault` entries in device config and `.harness/.project-mode` markers are aliased to `"backend"` transparently at read time — no operator action required; the underlying file is not rewritten. The `"local"` value and its semantics are unchanged. Decision: [Memory-storage seam](memory-storage-seam).

Precedence is deterministic: an explicit repo-local `.project-mode=local` marker wins over the device-level `state_mode`. There is **no in-vault marker layer** — an earlier design that read a marker from `<vault>/_harness/` was removed, because configuration never lives in the vault. The mode is also never *inferred* from a missing `vault_path`: a transiently-unreachable vault must not split-brain the mode.

## Why explicit opt-in, not auto-detect

The entry point is an explicit `--local-state` flag (plus the `--state-mode` setter and a `/setup` prompt), not an automatic "no vault ⇒ local" inference. A transiently-unreachable vault — Google Drive not mounted yet at session start — must not silently flip a vault-backed repo into local mode, because that splits state across two stores. Explicit opt-in is the safe signal; the read path still degrades gracefully for reads.

## What this mode does not do

It makes local mode *reachable and writable* without a vault — it does not migrate state between local and vault stores. The existing `migrate-harness-to-vault.sh` owns migration (and hard-requires a vault). Single-repo mode only signals the mode and routes writes.

## Related

- [Memory-storage seam — On-host state-mode config](memory-storage-seam) — why the mode is configured on-host only, never in the vault, and never inferred from a missing `vault_path`.
- [Run without a vault](Run-Without-A-Vault) — the operator recipe for enabling single-repo local state.
- [Installer CLI](Installer-CLI) — the `--local-state` flag and the `agentm_config.py --state-mode` setter.
- [Project config](Project-Config) — how `register()` degrades when no vault is present.
- [Vault write protocol](Vault-Write-Protocol) — the per-vault mutex that guards *vault*-mode writes; repo-local writes are partitioned by construction and take the atomic writer with no mutex.
