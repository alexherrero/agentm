<!--
  This README mirrors the wiki Home (wiki/Home.md): the opener, Get started,
  Learn more, and the latest-release note are kept in sync with it. The wiki
  Home is canonical; the README adds only the repo-local sections below it
  (Contributing, License). Convention: harness/documentation.md § "Home.md and
  the repo README". Wiki-internal links here are written as full
  https://github.com/alexherrero/agentm/wiki/<Page> URLs (the README renders on
  the repo page, not inside the wiki).
-->
<p align="center">
  <img src="assets/agent-m/banner-1600.png" alt="AgentM — The structural backend harness you wished you had">
</p>

<p align="center"><em>The agent harness that remembers your work — so you get the assistant you actually wanted.</em></p>

<!--
  Badge convention (plan #15 task 6 v2) — apply uniformly across the brand-system:
    labelColor = 0a0a0a (ink, brand)
    color      = auto (semantic green/red on CI; semver-colored on release)
                 OR f4efe6 (paper) for state-less metadata (e.g. LICENSE)
    style      = for-the-badge (brutalist, ALL CAPS, sharp corners — matches banner motif)
    logo       = github (logoColor f4efe6) on CI + release badges
  CI badge points at the dedicated `ci-all.yml` aggregator workflow which waits
  for the 3 per-OS workflows on the same commit and reports a combined status.
  This insulates the badge from other apps' check suites (e.g. installed GitHub Apps
  that queue but never complete checks). Compatibility lives at wiki/reference/Compatibility.md.
  Mirrored on the Crickets README via task 7. Documented in PLAN.md task 7.
-->

<p align="center">
  <a href="https://github.com/alexherrero/agentm/actions/workflows/ci-all.yml"><img src="https://img.shields.io/github/actions/workflow/status/alexherrero/agentm/ci-all.yml?branch=main&style=for-the-badge&label=CI&labelColor=0a0a0a&logo=github&logoColor=f4efe6" alt="CI"></a>
  <a href="https://github.com/alexherrero/agentm/releases/latest"><img src="https://img.shields.io/github/v/release/alexherrero/agentm?label=LATEST&labelColor=0a0a0a&logo=github&logoColor=f4efe6&style=for-the-badge" alt="Latest release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/CODE-Apache--2.0-f4efe6?labelColor=0a0a0a&style=for-the-badge" alt="Code license: Apache-2.0"></a>
  <a href="LICENSE-CONTENT"><img src="https://img.shields.io/badge/DOCS-CC--BY--4.0-f4efe6?labelColor=0a0a0a&style=for-the-badge" alt="Docs license: CC-BY-4.0"></a>
</p>

<p align="center"><sub>Works with Claude Code + Antigravity — <a href="https://github.com/alexherrero/agentm/wiki/Compatibility">see compatibility</a></sub></p>

**AgentM** is built to learn how you work — and, in time, to know it better than you do. It helps you remember: the decisions, the open threads, the conventions, the step you always forget before a release. It knows **how** the work should be done — it is opinionated, with a sound answer ready even for the things you didn't think to ask. And when it hits something it doesn't know, it works it out and keeps what it learns for the next time.

Said differently, AgentM combines a custom harness with persistent memory and self-improvement — it dreams and learns between sessions. Its customization system extends those abilities through plugins that enable long-running, nuanced development workflows, automated project management, and more. And its opinion system can layer in personas focused on a specific kind of work, so you can leave them to it while you focus on the task at hand.

AgentM works best paired with [`crickets`](https://github.com/alexherrero/crickets) — the toolkit of plugins (capabilities, skills, hooks, and sub-agents) that make it even more useful.

## 🚀 Get started

AgentM installs alongside your coding agent — Claude Code or Antigravity — so you can be up and running in a few minutes. [See requirements](https://github.com/alexherrero/agentm/wiki/Compatibility).

Install AgentM and crickets with the recommended configuration and a Google Drive vault:

```bash
# Point the vault at a Google Drive folder named "Agent" (the recommended default)
export MEMORY_VAULT_PATH="<your-google-drive>/Agent"

# Install AgentM for every project on this machine (user scope) + verification hooks
bash ~/agentm/install.sh --hooks --scope user

# Add the crickets plugins (Claude Code + Antigravity)
curl -fsSL https://raw.githubusercontent.com/alexherrero/crickets/main/bootstrap.sh | bash
```

More on the available configurations [here](https://github.com/alexherrero/agentm/wiki/Supported-Configurations).

## 📖 Learn more

The [wiki](https://github.com/alexherrero/agentm/wiki) covers everything there is to know about AgentM. A few links to get you started.

- [Why we built it](https://github.com/alexherrero/agentm/wiki/Explanation) — the problem, the solution and the reason.
- [Architecture](https://github.com/alexherrero/agentm/wiki/Architecture) — how it's made.
- [Reference](https://github.com/alexherrero/agentm/wiki/Reference) — fields, flags, schemas and more.

> [!NOTE]
> **Latest release: [v5.10.0](https://github.com/alexherrero/agentm/releases/tag/v5.10.0).** Your plans, roadmap, and progress now show up at the start of every session, whether you keep them on this device or in a synced vault. Earlier versions could miss them when the vault was synced.

---

## Contributing

Self-tested on every push by three per-OS workflows (Linux, Mac, Windows) running in parallel. Run the same deterministic battery locally with `bash scripts/check-all.sh`. Details and the full invariant list in [CONTRIBUTING.md](CONTRIBUTING.md).

## License

AgentM is multi-licensed so each layer carries the license that fits it:

| Layer | What it covers | License |
|---|---|---|
| **Code** | `.py`, `.sh`, `.ps1`, and configuration logic | [Apache-2.0](LICENSE) |
| **Content** | docs, prompts, agent instructions, skill / command / workflow definitions, wiki, and other prose (`.md`) | [CC-BY-4.0](LICENSE-CONTENT) |
| **Names & logos** | the "agentm" / "AgentM" name and brand artwork | Trademark — see [TRADEMARK.md](TRADEMARK.md) |

**Boundary rule:** prompt or instruction text embedded as a string literal inside a code file (e.g. a prompt inside a `.py` script) is **content** (CC-BY-4.0), even though it lives in a code file.

Both licenses permit commercial use and derivative works; both require attribution. See [NOTICE](NOTICE) for the attribution string.
