<p align="center">
  <img src="https://raw.githubusercontent.com/alexherrero/agentm/main/assets/agent-m/banner-1600.png" alt="AgentM — the agent harness you wished you had">
</p>

<p align="center"><em>The agent harness that remembers your work — so you get the assistant you actually wanted.</em></p>

<p align="center">
  <a href="https://github.com/alexherrero/agentm/actions/workflows/ci-all.yml"><img src="https://img.shields.io/github/actions/workflow/status/alexherrero/agentm/ci-all.yml?branch=main&style=for-the-badge&label=CI&labelColor=0a0a0a&logo=github&logoColor=f4efe6" alt="CI"></a>
  <a href="https://github.com/alexherrero/agentm/releases/latest"><img src="https://img.shields.io/github/v/release/alexherrero/agentm?label=LATEST&labelColor=0a0a0a&logo=github&logoColor=f4efe6&style=for-the-badge" alt="Latest release"></a>
  <a href="https://github.com/alexherrero/agentm/blob/main/LICENSE"><img src="https://img.shields.io/badge/CODE-Apache--2.0-f4efe6?labelColor=0a0a0a&style=for-the-badge" alt="Code license: Apache-2.0"></a>
  <a href="https://github.com/alexherrero/agentm/blob/main/LICENSE-CONTENT"><img src="https://img.shields.io/badge/DOCS-CC--BY--4.0-f4efe6?labelColor=0a0a0a&style=for-the-badge" alt="Docs license: CC-BY-4.0"></a>
</p>

<p align="center"><sub>Works with Claude Code + Antigravity — <a href="https://github.com/alexherrero/agentm/wiki/Compatibility">see compatibility</a></sub></p>

**AgentM** gives your coding agent a permanent memory. It writes what it learns — about you, and about each project — as plain Markdown notes in a folder you own: no hidden database, nothing you can't read or edit yourself. Memory splits into two tenants — personal notes on how you like to work, and a per-project record of decisions and open threads — so a fresh session can pick up exactly where the last one left off. It brings the right notes back at the right moment, and looks after the collection over time so it gets better instead of messier.

Said differently, AgentM pairs a phase-gated harness with that memory and a self-improvement pass — it dreams and learns between sessions. Its customization system extends those abilities through plugins that enable long-running, nuanced development workflows, automated project management, and more. Its opinion system is designed to layer in personas focused on a specific kind of work — activation is the next build slice.

AgentM works best paired with [`crickets`](https://github.com/alexherrero/crickets) — the toolkit of plugins (capabilities, skills, hooks, and sub-agents) that make it even more useful.

## 🚀 Get started

AgentM installs alongside your coding agent — Claude Code or Antigravity — so you can be up and running in a few minutes. [See requirements](Compatibility).

Install AgentM and crickets with the recommended configuration and a Google Drive vault:

```bash
# Point the vault at a Google Drive folder named "Agent" (the recommended default)
export MEMORY_VAULT_PATH="<your-google-drive>/Agent"

# Install AgentM for every project on this machine (user scope) + verification hooks
bash ~/agentm/install.sh --hooks --scope user

# Add the crickets plugins (Claude Code + Antigravity)
curl -fsSL https://raw.githubusercontent.com/alexherrero/crickets/main/bootstrap.sh | bash
```

Step-by-step, with the vault and hooks explained: [Install machine-wide (recommended)](Install-Machine-Wide). More on the available configurations [here](Supported-Configurations).

## 📖 Learn more

The [wiki](https://github.com/alexherrero/agentm/wiki) covers everything there is to know about AgentM. A few links to get you started.

- [Why we built it](Explanation) — the problem, the solution and the reason.
- [Architecture](Architecture) — how it's made.
- [Reference](Reference) — fields, flags, schemas and more.
- [What we built, when](Completed-Features) — the combined build timeline for AgentM and crickets, plus the roadmap-era-to-release-tag decoder.

## 🗂️ Browse by kind

The memory vault itself is browsable by kind — every note groups into a generated Map of Content (MOC) alongside its siblings of the same kind (`fix`, `pattern`, `decision`, and dozens more), so you can scan every entry of a kind without a search. See [MOC generator](MOC-Generator) for how the pages are built, and [Kind-taxonomy registry](Kind-Taxonomy-Registry) for the kinds it recognizes.

---

> [!NOTE]
> **Latest release: [v8.1.0](https://github.com/alexherrero/agentm/releases/tag/v8.1.0).** The overnight loop no longer grades its own homework — an unattended run's "done" call now comes from a real, independent check instead of the run's own say-so. Alongside it: the health scorecard runs locally instead of committing itself back on a schedule, unattended dispatch can get past its permission prompts, and the reference docs read plainer.
