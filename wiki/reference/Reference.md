<!-- mode: index -->
# Reference

Look up the exact details of the AgentM harness — the install flags, config schemas, detection rules, vault checks, and the log of shipped work. These pages tell you *what things are*; the [How-to](How-To) pages cover *how to do* a task, and [Architecture](Architecture) covers *how it's built*.

## Install & layout

| Page | What it covers |
|---|---|
| [Installer CLI](Installer-CLI) | The flags and prerequisites for `install.sh` / `install.ps1`, and which files each owns. |
| [Supported configurations](Supported-Configurations) | The install-scope, vault-storage, and state-mode choices, with a pointer to each. |
| [Migration tool](Migration-Tool) | The tool that moves an older per-project install to user scope, and what it records. |
| [Repo layout](Repo-Layout) | The top-level directory map, and how the host adapters line up. |
| [Compatibility](Compatibility) | The supported hosts and operating systems. |

## Detection & config

| Page | What it covers |
|---|---|
| [Detection rules](Detection-Rules) | The built-in rules that spot what a project needs, and why each one fires. |
| [Project config](Project-Config) | The `project.json` schema that turns features on and off. |
| [Auto-orchestration config](Auto-Orchestration-Config) | Every setting behind auto-orchestration — the toggles, thresholds, and cooldowns — and its default. |

## Memory & vault

| Page | What it covers |
|---|---|
| [AgentMemory context payload](AgentMemory-Context-Payload) | The paste-anywhere memory payload, and what's in it. |
| [Vault lint checks](Vault-Lint-Checks) | The read-only checks the vault audit runs, and what each one looks for. |
| [Note relatedness signals](Note-Relatedness-Signals) | The signals used to suggest links between notes. |
| [Vault write protocol](Vault-Write-Protocol) | How the vault stays safe when more than one session writes at once, and the habits that help. |
| [Process seam](Process-Seam) | The small read-only interface another process uses to reach memory, instead of calling the engine directly. |
| [Storage seam](Storage-Seam) | The interface the engine uses to read and write storage, and its two backends: the device-local store and the synced vault. |
| [Orchestration bridge](Orchestration-Bridge) | The read-only bridge that surfaces orchestration state to another process. |
| [Queue status lite](Queue-Status-Lite) | The read-only coordinator dashboard that lists every active plan and its status. |
| [Memory MCP tools](Memory-MCP-Tools) | The memory-engine tool surface exposed over MCP. |

## Plugin capabilities

| Page | What it covers |
|---|---|
| [Capability resolver](Capability-Resolver) | How the harness works out which capabilities are available, on each host. |
| [Design governance](Design-Governance) | The frontmatter convention that ties a design to what it governs, and the resolver behind it. |
| [Persona tier schema](persona-tier-schema) | The `kind: persona` manifest fields and the `check-personas` gate that enforces them. |

## CI & shipped work

| Page | What it covers |
|---|---|
| [CI gates](CI-Gates) | What each CI workflow proves, and the script behind it. |
| [GitHub Projects sync](GitHub-Projects-Sync) | The board-sync surface agentm exposes — the config it reads, when phases emit updates, and the graceful-skip. |
| [PII Guardrail](PII) | What to keep out of the public repo, and how the scan enforces it. |
| [Completed features](Completed-Features) | The log of shipped work, newest first. |
| [Known issues](Known-Issues) | Fixed gotchas worth knowing before you hit them — non-obvious repro conditions, environmental dependencies, surprising interactions. |

## See also

[How-to](How-To) · [Architecture](Architecture) · [Home](Home)
