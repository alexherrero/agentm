<!-- mode: reference -->
# Repo layout

The top-level layout of agentm on disk. For *why* this shape, see [How the pieces fit](How-The-Pieces-Fit), the [AgentM HLD](agentm-hld), and the [Foundations HLD](agentm-foundations-hld).

## ⚡ Quick Reference

| Question | Answer |
|---|---|
| Where does a phase spec live? | Not in agentm — the phase loop (`/setup` `/plan` `/work` `/review` `/release` `/bugfix`) ships in the crickets **developer-workflows** plugin since the V5 unbundling (the [AgentM HLD](agentm-hld)). agentm owns the durable state substrate the phases run on, not the specs. |
| Where does an adapter live? | [`adapters/<host>/`](https://github.com/alexherrero/agentm/tree/main/adapters) — `claude-code`, `antigravity` (the two supported hosts). |
| Where does the install scaffold live? | [`templates/`](https://github.com/alexherrero/agentm/tree/main/templates) — state files, hooks, wiki scaffold. |
| Where does the test infra live? | [`scripts/`](https://github.com/alexherrero/agentm/tree/main/scripts) — **never propagated to target projects**. |
| Where does this wiki get copied from on install? | Nowhere. Target projects get `templates/wiki/` (an empty scaffold), not this one — see the [Foundations HLD](agentm-foundations-hld). |
| Where do personal customizations live? | [`crickets`](https://github.com/alexherrero/crickets) — the sibling toolkit repo (since v2.0.0 / [Foundations HLD](agentm-foundations-hld)). |
| Where does the shared install plumbing live? | [`lib/install/`](https://github.com/alexherrero/agentm/tree/main/lib/install) — byte-identical to `crickets/lib/install/`; synced via `scripts/sync-lib.sh`, parity-gated in CI. |
| Where does the vault-less mode signal live? | Two on-host layers — the device-level `state_mode` in `<install-prefix>/.agentm-config.json` and the higher-precedence per-repo `<repo>/.harness/.project-mode`. See [Single-repo state mode](Single-Repo-State-Mode). |

## Top-level layout

```
agentm/
├── install.sh                 # POSIX installer (bash)
├── install.ps1                # Windows installer (PowerShell 7+)
├── README.md                  # the pitch + install instructions
├── AGENTS.md                  # universal agent entry point
├── CLAUDE.md                  # Claude Code entry (links back to AGENTS.md)
├── CHANGELOG.md               # Keep-a-Changelog format; written by crickets' ship-release skill
├── LICENSE                    # Apache-2.0 (code)
├── LICENSE-CONTENT            # CC-BY-4.0 (docs, prompts, prose)
├── NOTICE                     # Apache attribution notice + license map
├── TRADEMARK.md               # brand policy for the "agentm" name
├── harness/                   # canonical specs (source of truth)
│   ├── agents/                # canonical sub-agent specs (see roster below)
│   ├── skills/                # canonical skill specs (see roster below)
│   ├── principles.md          # the design calls behind the harness
│   ├── documentation.md       # the wiki + GitHub Projects/Issues convention
│   ├── hooks.md               # hook design (PostToolUse / PreCompact / SessionStart)
│   ├── telemetry.md           # telemetry signals + thresholds
│   └── verification.md        # deterministic-gate definitions
├── adapters/                  # per-host shims that point at harness/ specs
│   ├── claude-code/           # commands + agents + skills  (→ .claude/)
│   ├── antigravity/           # workflows + skills + rules   (→ .agents/)
│   └── gemini/                # dropped host (v2.4.0) — vestigial dir, see Compatibility
├── lib/                       # shared install plumbing (byte-identical to crickets/lib/)
│   └── install/               # cp_managed, cp_user, ensure_boundary_src, sync_managed_parents
├── templates/                 # what install.sh drops into a target project
│   ├── PLAN.md, features.json, progress.md, init.sh, verify.{sh,ps1}
│   ├── hooks/                 # hook scripts + settings-fragment JSON (bash + pwsh)
│   ├── scripts/               # cross-review.{sh,ps1}, telemetry.sh, etc.
│   └── wiki/                  # the wiki scaffold installed into target projects
├── scripts/                   # test infra — NEVER propagated by install.sh
│   ├── smoke-install-{bash.sh,pwsh.ps1}
│   ├── check-parity.sh, check-references.py, check-wiki.py
│   ├── check-lib-parity.sh    # byte-identity gate for lib/install/
│   ├── check-no-pii.sh        # PII regex scanner (gitleaks also gates CI)
│   ├── sync-lib.sh            # one-shot lib/install/ sync agentm → ../crickets
│   └── validate-adapters.py
├── wiki/                      # THIS wiki — dogfood docs for the harness repo itself
│   ├── Home.md, _Sidebar.md, architecture.yml
│   └── how-to/ reference/ architecture/ designs/ explanation/ decisions/
└── .github/workflows/
    ├── tests-linux.yml, tests-mac.yml, tests-windows.yml   # CI (never propagated)
    └── wiki-sync.yml                                        # (also shipped as a template)
```

## The supported adapters

Since the V5 unbundling (the [AgentM HLD](agentm-hld)) each adapter ships only agentm's *own* surfaces — the phase-gated dev loop and the review sub-agents moved to the crickets developer-workflows / code-review plugins, so there is nothing to parity-check there (their absence is pinned by `scripts/test_devloop_slim_retired.py`). [`scripts/check-parity.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/check-parity.sh) asserts what remains matches across hosts.

| Adapter | Ships (agentm's own surfaces) |
|---|---|
| `adapters/claude-code/` | the `recent-wiki-changes` utility command (`.claude/commands/`) · the `doctor` skill (`.claude/skills/doctor/`) |
| `adapters/antigravity/` | the always-on rules — operating contract + vault context (`.agents/rules/{harness,agentmemory-context}.md`); the `workflows/` + `skills/` dirs were removed in the slim |

A third directory, `adapters/gemini/`, remains in the tree but is **not a supported host** — Gemini CLI was dropped in v2.4.0 ([Compatibility](Compatibility)). Its removal is pending reconciliation.

**Canonical sub-agents** (`harness/agents/`): `adapt-evaluator`, `memory-idea-researcher` — the memory-engine pair. The review sub-agents (`explorer`, `adversarial-reviewer`, `adversarial-reviewer-cross`) and `documenter` are crickets-provided (code-review / developer-workflows / wiki-maintenance plugins) since the V5 unbundling (the [AgentM HLD](agentm-hld)).

**Canonical skills** (`harness/skills/`): `design`, `doctor`, `memory`, `wiki-author`. `ship-release` is provided by crickets' `releasing-conventions` plugin (graceful-skip if crickets is not paired), recommended by name like `dependabot-fixer` / `pii-scrubber`. Wiki authoring's `diataxis-author` is provided the same way by crickets' `wiki-maintenance` plugin.

## Related

- [How the pieces fit](How-The-Pieces-Fit) — how phases / adapters / templates / scripts interact.
- [Installer CLI](Installer-CLI) — flags and the owned-vs-managed tree.
- [CI gates](CI-Gates) — what each workflow proves.
- [Compatibility](Compatibility) — the supported hosts and dropped hosts.
- [Foundations HLD — Documentation convention](agentm-foundations-hld) — why this wiki is never installed into target projects.
