# Feature: single-repo (vault-less) state mode

> [!NOTE]
> **Status:** pending
> **Plan:** `.harness/PLAN.md` tasks 2 (vault-less write path + repo-local marker) + 3 (`--local-state` entry point + register-in-local-mode).

Run the harness on a single repo with no Obsidian / GDrive / vault dependency. You opt in explicitly — an `install.sh --local-state` flag or a `/setup` prompt — and from then on every phase write lands in `<repo>/.harness/` instead of routing through a memory vault. This page is the *why* and the resolution model; for the steps, see [Run the harness without a vault](Run-Without-A-Vault).

## Why this mode exists

Before this work, harness state was vault-resident: `read_state_file` / `write_state_file` resolved `PLAN.md`, `progress.md`, and friends under `<vault>/projects/<slug>/_harness/`, and a write with no vault configured hard-raised `ValueError`. That made the harness unusable on a machine without a mounted vault — you could neither signal which mode a repo wanted nor write any phase state.

Single-repo mode removes the vault as a hard dependency. It is strictly **additive and gated**: when a vault is present and the repo is not in local mode, the vault write path behaves byte-for-byte as before. Local mode only changes behavior for repos that opt into it.

## The state-resolution model

Where a phase write lands is decided by **two on-host configuration layers** — no vault is ever consulted to resolve the mode (locked DC-8: configuration is on-host only; the vault holds data, never config):

- **The repo-local marker** — `<repo>/.harness/.project-mode`. The optional **per-repo override**. When it reads `local`, state I/O routes to `<repo>/.harness/<file>` and never raises for a missing vault. It lives in the repo so it is reachable on a vault-less machine. Wins when present.
- **The device-level default** — `state_mode` (`"local"` | `"vault"`) in `<install-prefix>/.agentm-config.json`. This is "how agentm runs on this machine," read vault-free. Consulted when no repo-local marker is set. Absent ⇒ vault default.

Precedence is deterministic: an explicit repo-local `.project-mode=local` marker wins over the device-level `state_mode`. There is **no in-vault marker layer** — the earlier design that read a `.project-mode` marker from `<vault>/_harness/` was removed; configuration never lives in the vault. The mode is also never inferred from a missing `vault_path` (a transiently-unreachable vault must not split-brain the mode). These are locked design calls (see Related, DC-2 / DC-8).

## Why explicit opt-in, not auto-detect

The entry point is an explicit `--local-state` flag (plus a `/setup` prompt), not an automatic "no vault ⇒ local" inference. A transiently-unreachable vault — GDrive not mounted yet at session start — must not silently flip a vault-backed repo into local mode, because that splits state across two stores. Explicit opt-in is the safe signal; the read path still degrades gracefully for reads.

## What this mode does not do

It makes local mode *reachable and writable* without a vault — it does not migrate state between local and vault stores. The existing `migrate-harness-to-vault.sh` owns migration (and hard-requires a vault). Single-repo mode only signals the mode and routes writes.

## Related

- [Run the harness without a vault](Run-Without-A-Vault) — the operator recipe for enabling single-repo local state.
- [Installer CLI reference](Installer-CLI) — the `--local-state` flag.
- [Project config reference](Project-Config) — how `project_config.py register()` degrades when no vault is present.
- [Repo layout reference](Repo-Layout) — where `<repo>/.harness/.project-mode` sits on disk.

<!-- DC-2 (repo-local marker precedence), DC-3 (explicit opt-in; never infer mode from a missing vault), and DC-8 (configuration is on-host only — device-level `state_mode` + per-repo marker; the in-vault marker layer was removed) are locked in the Hardening I plan (`.harness/PLAN.md`). Cross-link to an ADR here once one lands; none exists yet. -->
