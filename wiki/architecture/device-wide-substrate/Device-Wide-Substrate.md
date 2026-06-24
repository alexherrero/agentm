<!-- mode: index -->
# Device-Wide Substrate

_Installed per repo, but stateful per machine — one vault and one on-host config serve every project, with a per-repo escape hatch when a machine has no vault._

The harness installs into each project, but the memory it reads and writes is **device-wide**: a single vault and a single on-host config file (`.agentm-config.json`) serve every repo on the machine. That is what lets a lesson learned in one project surface in the next, and why "where does state live" is answered once per device rather than once per repo.

## How it works

Two device-level artifacts plus a per-repo override decide where any phase write lands:

| Artifact | Scope | Decides |
|---|---|---|
| `.agentm-config.json` | device | the default `state_mode` (`vault` or `local`) + vault path |
| `<vault>/` | device | the durable store, shared across all projects |
| `<repo>/.harness/.project-mode` | per-repo | overrides the device default for one repo |

The default is **vault mode**: state routes through the device vault. A machine with no Obsidian/Drive vault opts into **local mode** (`install.sh --local-state`), and every phase write lands in `<repo>/.harness/` instead — no vault required. The per-repo `.project-mode` marker wins over the device default, so a single repo can diverge without flipping the machine.

## How it fits

- **[AgentMemory](AgentMemory)** — the vault this layer makes device-wide. The substrate is *where*; AgentMemory is *what*.
- **[Phases](Phases)** — every phase read/write resolves through this layer to either the vault or the repo-local directory.
- **[Host adapters](Host-Adapters)** — each host reads the same on-host config, so state resolution is identical wherever the harness runs.

## See also

Detail:

- [Installer CLI](Installer-CLI) — the `--local-state` flag and the `agentm_config.py` state-mode setter.
- [Project config](Project-Config) · [Repo layout](Repo-Layout) — the config fields and where the `.project-mode` marker sits.
- [Single-repo state mode](Single-Repo-State-Mode) — the vault-vs-local resolution model.
- [Run without a vault](Run-Without-A-Vault) — the recipe for opting a machine into local mode.

Designs:

- [Device-Wide Architecture](agentm-foundations-hld) — the full model behind the device-wide substrate.

[Architecture](Architecture) · [Designs](Designs) · [Home](Home)
