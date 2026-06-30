<!-- mode: index -->
# How-to

Task-focused recipes for running the AgentM harness — from a first install through tuning the memory layer and keeping the vault healthy. New here? Start with the tutorial, then come back for the specific task you're on.

## Start here

1. [Your first install](01-First-Install) — fresh clone to a healthy installed scratch project in ~5 minutes.
2. [Install into a project](Install-Into-Project) — add the harness scaffold to an existing repo.

## What's here

**Install & configure**

- **[Your first install](01-First-Install)** — the end-to-end first run, two repos + a vault.
- **[Install into a project](Install-Into-Project)** — add the scaffold to an existing repo.
- **[Configure a new project](Configure-A-New-Project)** — the first-session detect → propose → approve → persist flow.
- **[Update an installed harness](Update-Installed-Harness)** — pull a newer harness version into a project that already has one.
- **[Use per-project install](Use-Per-Project-Install)** — when to keep `--scope project` instead of migrating to user scope.
- **[Run without a vault](Run-Without-A-Vault)** — operate the harness with no MemoryVault configured.
- **[Choose a storage backend](Choose-A-Storage-Backend)** — select the memory engine's storage backend and confirm its plugin is installed.
- **[Back the vault with Google Drive](Back-The-Vault-With-Drive)** — back up + sync the vault across devices with Drive (the simple mode).
- **[Set up Obsidian on the vault](Use-Obsidian-With-The-Vault)** — the optional Obsidian configuration over either transport.

**Run the loop**

- **[Cut a release](Cut-A-Release)** — tag, changelog, and GitHub release via the `ship-release` skill.

**Tune & maintain the memory**

- **[Use auto-context in phases](Use-Auto-Context-In-Harness-Phases)** — recall budgets, save modes, and confidence thresholds per phase.
- **[Tune auto-orchestration](Tune-Auto-Orchestration)** — the toggles, thresholds, and cooldowns behind the SessionStart briefing and idle chain.
- **[Audit the vault](Audit-The-Vault)** — run the read-only vault lint and apply suggested fixes.
- **[Find missing note links](Find-Missing-Note-Links)** — run the link-discovery audit and add the suggested `[[wikilinks]]`.
- **[Use AgentMemory in any agent](Use-AgentMemory-In-Any-Agent)** — read the vault (read-only) from Claude.ai · Gemini · ChatGPT · Antigravity.

## See also

[Reference](Reference) · [Architecture](Architecture) · [Home](Home)
