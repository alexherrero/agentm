<p align="center">
  <img src="https://raw.githubusercontent.com/alexherrero/agentm/main/assets/agent-m/banner-1600.png" alt="AgentM — The structural backend harness you wished you had">
</p>

<p align="center"><em>The agent harness that remembers your work — so you get the assistant you actually wanted.</em></p>

<p align="center">
  <a href="https://github.com/alexherrero/agentm/actions/workflows/ci-all.yml"><img src="https://img.shields.io/github/actions/workflow/status/alexherrero/agentm/ci-all.yml?branch=main&style=for-the-badge&label=CI&labelColor=0a0a0a&logo=github&logoColor=f4efe6" alt="CI"></a>
  <a href="https://github.com/alexherrero/agentm/releases/latest"><img src="https://img.shields.io/github/v/release/alexherrero/agentm?label=LATEST&labelColor=0a0a0a&logo=github&logoColor=f4efe6&style=for-the-badge" alt="Latest release"></a>
  <a href="https://github.com/alexherrero/agentm/blob/main/LICENSE"><img src="https://img.shields.io/badge/CODE-Apache--2.0-f4efe6?labelColor=0a0a0a&style=for-the-badge" alt="Code license: Apache-2.0"></a>
  <a href="https://github.com/alexherrero/agentm/blob/main/LICENSE-CONTENT"><img src="https://img.shields.io/badge/DOCS-CC--BY--4.0-f4efe6?labelColor=0a0a0a&style=for-the-badge" alt="Docs license: CC-BY-4.0"></a>
</p>

<p align="center"><sub>Works with Claude Code + Antigravity — <a href="https://github.com/alexherrero/agentm/wiki/Compatibility">see compatibility</a></sub></p>

**AgentM** is built to learn how you work — and, in time, to know it better than you do. It helps you remember: the decisions, the open threads, the conventions, the step you always forget before a release. It knows **how** the work should be done — it is opinionated, with a sound answer ready even for the things you didn't think to ask. And when it hits something it doesn't know, it works it out and keeps what it learns for the next time.

Said differently, AgentM combines a custom harness with persistent memory and self-improvement — it dreams and learns between sessions. Its customization system extends those abilities through plugins that enable long-running, nuanced development workflows, automated project management, and more. And its opinion system can layer in personas focused on a specific kind of work, so you can leave them to it while you focus on the task at hand.

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

More on the available configurations [here](Supported-Configurations).

## 📖 Learn more

The [wiki](https://github.com/alexherrero/agentm/wiki) covers everything there is to know about AgentM. A few links to get you started.

- [Why we built it](Explanation) — the problem, the solution and the reason.
- [Architecture](Architecture) — how it's made.
- [Reference](Reference) — fields, flags, schemas and more.

---

> [!NOTE]
> **Latest release: [v5.10.0](https://github.com/alexherrero/agentm/releases/tag/v5.10.0).** Your plans, roadmap, and progress now show up at the start of every session, whether you keep them on this device or in a synced vault. Earlier versions could miss them when the vault was synced.
