# Feature: single-repo (vault-less) state mode

> [!NOTE]
> **Status:** pending
> **Plan:** `.harness/PLAN.md` tasks 2 (vault-less write path + repo-local marker) + 3 (`--local-state` entry point + register-in-local-mode).

Run the harness on a single repo with no Obsidian / GDrive / vault dependency. You opt in explicitly — an `install.sh --local-state` flag or a `/setup` prompt — and from then on every phase write lands in `<repo>/.harness/` instead of routing through a memory vault. This page is the *why* and the resolution model; for the steps, see [Run the harness without a vault](Run-Without-A-Vault).

## Why this mode exists

Before this work, harness state was vault-resident: `read_state_file` / `write_state_file` resolved `PLAN.md`, `progress.md`, and friends under `<vault>/projects/<slug>/_harness/`, and a write with no vault configured hard-raised `ValueError`. That made the harness unusable on a machine without a mounted vault — you could neither signal which mode a repo wanted nor write any phase state.

Single-repo mode removes the vault as a hard dependency. It is strictly **additive and gated**: when a vault is present and the repo is not in local mode, the vault write path behaves byte-for-byte as before. Local mode only changes behavior for repos that opt into it.

## The state-resolution model

Two signals decide where a phase write lands:

- **The repo-local marker** — `<repo>/.harness/.project-mode`. When it reads `local`, state I/O routes to `<repo>/.harness/<file>` and never raises for a missing vault. This marker is vault-independent by design (the older in-vault marker at `<vault>/projects/<slug>/_harness/.project-mode` is unreachable on a vault-less machine).
- **The vault** — when present and the repo is not in local mode, the existing vault-resident path is used unchanged.

Marker precedence is deterministic: an explicit repo-local `.project-mode=local` marker wins over the in-vault marker (the operator opted in locally). This is a locked design call (see Related, DC-2); document it so a vault user is not surprised when a repo-local marker overrides their vault.

## Why explicit opt-in, not auto-detect

The entry point is an explicit `--local-state` flag (plus a `/setup` prompt), not an automatic "no vault ⇒ local" inference. A transiently-unreachable vault — GDrive not mounted yet at session start — must not silently flip a vault-backed repo into local mode, because that splits state across two stores. Explicit opt-in is the safe signal; the read path still degrades gracefully for reads.

## What this mode does not do

It makes local mode *reachable and writable* without a vault — it does not migrate state between local and vault stores. The existing `migrate-harness-to-vault.sh` owns migration (and hard-requires a vault). Single-repo mode only signals the mode and routes writes.

## Related

- [Run the harness without a vault](Run-Without-A-Vault) — the operator recipe for enabling single-repo local state.
- [Installer CLI reference](Installer-CLI) — the `--local-state` flag.
- [Project config reference](Project-Config) — how `project_config.py register()` degrades when no vault is present.
- [Repo layout reference](Repo-Layout) — where `<repo>/.harness/.project-mode` sits on disk.

<!-- DC-2 (repo-local marker precedence) and DC-3 (explicit `--local-state` opt-in) are locked in the Hardening I plan (`.harness/PLAN.md`). Cross-link to an ADR here once one lands; none exists yet. -->
