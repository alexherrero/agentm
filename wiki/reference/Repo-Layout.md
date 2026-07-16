<!-- mode: reference -->
# Repo layout

This page shows the top-level layout of agentm on disk. You can read [How the pieces fit](How-The-Pieces-Fit) to understand *why* the project uses this shape. The [AgentM HLD](agentm-hld) and the [Foundations HLD](agentm-foundations-hld) also explain these design choices.

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
├── CONTRIBUTING.md             # contribution guidelines
├── CHANGELOG.md               # Keep-a-Changelog format; written by crickets' ship-release skill
├── LICENSE                    # Apache-2.0 (code)
├── LICENSE-CONTENT            # CC-BY-4.0 (docs, prompts, prose)
├── NOTICE                     # Apache attribution notice + license map
├── TRADEMARK.md               # brand policy for the "agentm" name
├── requirements.txt            # Python deps for scripts/ + harness/
├── harness/                   # canonical specs (source of truth)
│   ├── agents/                # canonical sub-agent specs (see roster below)
│   ├── skills/                # canonical skill specs (see roster below) — design, doctor, memory, wiki-author, console
│   ├── hooks/                 # canonical hook specs (harness-context, memory-recall x2, memory-reflect x2)
│   ├── plugins/                # example-plugin — a reference plugin skeleton
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
├── install/                    # host-specific install artifacts (e.g. com.agentm.memory-server.plist, macOS launchd)
├── opinions/                   # the request-by-name Opinion registry (9 named opinion docs)
├── personas/                   # persona manifests (architect, brain, designer, engineer, maintainer, …)
├── assets/                     # brand/logo assets
├── templates/                 # what install.sh drops into a target project
│   ├── PLAN.md, features.json, progress.md, init.sh, verify.{sh,ps1}
│   ├── hooks/                 # hook scripts + settings-fragment JSON (bash + pwsh)
│   ├── scripts/               # cross-review.{sh,ps1}, telemetry.sh, etc.
│   └── wiki/                  # the wiki scaffold installed into target projects
├── scripts/                   # test infra — NEVER propagated by install.sh
│   ├── smoke-install-{bash.sh,pwsh.ps1}
│   ├── check-parity.sh, check-references.py, check-wiki.py
│   ├── check-vendored-parity.sh # CONS-1 merge of the former check-lib-parity.sh + 4 siblings; `lib` mode is lib/install/'s byte-identity gate
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

Each adapter ships only agentm's *own* surfaces. This changed in the V5 unbundling. You can read about this in the [AgentM HLD](agentm-hld). The phase-gated dev loop moved to the crickets developer-workflows plugin. The review sub-agents moved to the crickets code-review plugin. You do not need to parity-check those features here. The `scripts/test_devloop_slim_retired.py` script pins their absence. You can run [`scripts/check-parity.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/check-parity.sh) to assert that the remaining features match across hosts.

| Adapter | Ships (agentm's own surfaces) |
|---|---|
| `adapters/claude-code/` | the `recent-wiki-changes` utility command (`.claude/commands/`) · the `doctor` skill (`.claude/skills/doctor/`) |
| `adapters/antigravity/` | the always-on rules — operating contract + vault context (`.agents/rules/{harness,agentmemory-context}.md`); the `workflows/` + `skills/` dirs were removed in the slim |

A third directory, `adapters/gemini/`, remains in the tree. This is **not a supported host**. Agentm dropped the Gemini CLI in v2.4.0. You can read about this in [Compatibility](Compatibility). The project keeps this directory pending reconciliation.

**Canonical sub-agents** (`harness/agents/`): These are `adapt-evaluator` and `memory-idea-researcher`. They act as the memory-engine pair. The crickets plugins provide the review sub-agents (`explorer`, `adversarial-reviewer`, `adversarial-reviewer-cross`) and `documenter`. These plugins are code-review, developer-workflows, and wiki-maintenance. This separation happened in the V5 unbundling. You can read the [AgentM HLD](agentm-hld) for more details.

**Canonical skills** (`harness/skills/`): These include `design`, `doctor`, `memory`, `wiki-author`, and `console`. The crickets `releasing-conventions` plugin provides `ship-release`. Agentm gracefully skips this if you do not pair crickets. Agentm recommends it by name. This matches the recommendation style for `dependabot-fixer` and `pii-scrubber`. The crickets `wiki-maintenance` plugin provides `diataxis-author` for wiki authoring in the exact same way.

## Related

- [How the pieces fit](How-The-Pieces-Fit) shows how phases, adapters, templates, and scripts interact.
- [Installer CLI](Installer-CLI) explains flags and the owned-vs-managed tree.
- [CI gates](CI-Gates) lists what each workflow proves.
- [Compatibility](Compatibility) lists the supported hosts and dropped hosts.
- [Foundations HLD — Documentation convention](agentm-foundations-hld) explains why you never install this wiki into target projects.
