<!-- mode: index -->
# Reference

Look up the exact details of the AgentM harness — the install flags, config schemas, detection rules, vault checks, and the log of shipped work. These pages tell you *what things are*; the [How-to](How-To) pages cover *how to do* a task, and [Architecture](Architecture) covers *how it's built*.

## Install & layout

- **[Installer CLI](Installer-CLI)** — the flags and prerequisites for `install.sh` / `install.ps1`, and which files each one owns.
- **[Supported configurations](Supported-Configurations)** — the install-scope, vault-storage, and state-mode choices, with a pointer to each.
- **[Migration tool](Migration-Tool)** — the tool that moves an older per-project install to user scope, and what it records.
- **[Repo layout](Repo-Layout)** — the top-level directory map, and how the host adapters line up.
- **[Compatibility](Compatibility)** — the supported hosts and operating systems.

## Detection & config

- **[Detection rules](Detection-Rules)** — the built-in rules that spot what a project needs, and why each one fires.
- **[Project config](Project-Config)** — the `project.json` schema that turns features on and off.
- **[Auto-orchestration config](Auto-Orchestration-Config)** — every setting behind auto-orchestration (the toggles, thresholds, and cooldowns) and its default.

## Memory & vault

- **[AgentMemory context payload](AgentMemory-Context-Payload)** — the paste-anywhere memory payload, and what's in it.
- **[Vault lint checks](Vault-Lint-Checks)** — the read-only checks the vault audit runs, and what each one looks for.
- **[Note relatedness signals](Note-Relatedness-Signals)** — the signals used to suggest links between notes.
- **[Vault write protocol](Vault-Write-Protocol)** — how the vault stays safe when more than one session writes at once, and the habits that help.
- **[Process seam](Process-Seam)** — the small read-only interface another process uses to reach memory, instead of calling the engine directly.
- **[Storage seam](Storage-Seam)** — the interface the engine uses to read and write storage, and its two backends: the plain device-local store and the synced vault.

## Plugin capabilities

- **[Capability resolver](Capability-Resolver)** — how the harness works out which capabilities are available, on each host.
- **[Design governance](Design-Governance)** — the frontmatter convention that ties a design to what it governs, and the resolver behind it.

## CI & shipped work

- **[CI gates](CI-Gates)** — what each CI workflow proves, and the script behind it.
- **[Completed features](Completed-Features)** — the log of shipped work, newest first.

## See also

[How-to](How-To) · [Architecture](Architecture) · [Home](Home)
