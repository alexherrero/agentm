<!-- mode: index -->
# Reference

Lookup-oriented technical detail for the Agent M harness — CLI flags, config schemas, detection rules, lint checks, and the shipped-work log. These pages document *what is*, exactly; the [How-to](How-To) pages cover *how to do* a task and [Architecture](Architecture) covers *how it's built*.

## Install & layout

- **[Installer CLI](Installer-CLI)** — flags, prerequisites, and the ownership table for `install.sh` / `install.ps1`.
- **[Migration tool](Migration-Tool)** — `migrate-to-user-scope.{sh,ps1}`, the 4-state matrix, and the `.agentm-migrate-record.json` schema.
- **[Repo layout](Repo-Layout)** — the top-level directory map and the adapter parity table.
- **[Compatibility](Compatibility)** — supported hosts, the OS matrix, and the adapter contract.

## Detection & config

- **[Detection rules](Detection-Rules)** — the built-in rules and what each attaches a rationale to.
- **[Project config](Project-Config)** — the `project.json` enablement-block schema.
- **[Auto-orchestration config](Auto-Orchestration-Config)** — every config key + default (toggles · thresholds · cooldowns) and the `auto-orchestration-state.json` shape.

## Memory & vault

- **[AgentMemory context payload](AgentMemory-Context-Payload)** — the canonical paste-anywhere payload's sections.
- **[Vault lint checks](Vault-Lint-Checks)** — the read-only `vault_lint.py` checks: id · severity · what each checks · suggested-fix shape.
- **[Note relatedness signals](Note-Relatedness-Signals)** — the signals + thresholds `notes_link_discovery.py` scores on.
- **[Vault write protocol](Vault-Write-Protocol)** — the lock path, content-hash CAS, atomic-writer, and the pin-offline / partitioning operator habits for N≥2 concurrent writers.
- **[Process seam](Process-Seam)** — the read-only client a *process* calls instead of reaching into the engine: the three functions, their signatures, and degrade contracts.
- **[Storage seam](Storage-Seam)** — the verbs the engine calls instead of touching files: the seven-verb `StorageBackend` contract, the `Locator`/`Info`/`Capabilities` types, and its two concrete backends — `DeviceLocalBackend` (plain markdown) and `VaultBackend` (the synced vault wrap) — held to one conformance contract (V5-1 parts 1–4).

## Plugin capabilities

- **[Capability resolver](Capability-Resolver)** — the `capability_available` / `capability_resolve` / `build_registry` public API, the four reason codes, the Claude Code and Antigravity read paths, merge semantics, and the CLI shim exit codes.

## CI & shipped work

- **[CI gates](CI-Gates)** — what each CI workflow proves and the script behind it.
- **[Completed features](Completed-Features)** — the reverse-chronological log of shipped work.

## See also

[How-to](How-To) · [Architecture](Architecture) · [Home](Home)
