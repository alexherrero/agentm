# Repo layout reference

Top-level layout of agentic-harness on disk. For the *why* of this shape, see [How the pieces fit](How-The-Pieces-Fit), [ADR 0001](0001-phase-gated-workflow), and [ADR 0002](0002-documentation-convention).

## ⚡ Quick Reference

| Question | Answer |
|---|---|
| Where does a phase spec live? | [`harness/phases/`](https://github.com/alexherrero/agentic-harness/tree/main/harness/phases) — one canonical `.md` per phase |
| Where does an adapter live? | [`adapters/<tool>/`](https://github.com/alexherrero/agentic-harness/tree/main/adapters) — claude-code, antigravity, codex, gemini |
| Where does the install scaffold live? | [`templates/`](https://github.com/alexherrero/agentic-harness/tree/main/templates) — state files, hooks, wiki scaffold |
| Where does the test infra live? | [`scripts/`](https://github.com/alexherrero/agentic-harness/tree/main/scripts) — **never propagated to target projects** |
| Where does this wiki get copied from on install? | Nowhere. Target projects get `templates/wiki/` (empty scaffold), not this one. See [ADR 0002](0002-documentation-convention). |

## 📁 Top-level layout

```
agentic-harness/
├── install.sh                 # POSIX installer (bash)
├── install.ps1                # Windows installer (PowerShell 7+)
├── README.md                  # the pitch + install instructions
├── AGENTS.md                  # universal agent entry point
├── CLAUDE.md                  # Claude Code entry (links back to AGENTS.md)
├── CHANGELOG.md               # Keep-a-Changelog format; written by ship-release
├── LICENSE                    # MIT
├── harness/                   # canonical specs (source of truth)
│   ├── phases/                # 01-setup .. 05-release + bugfix pipeline
│   ├── agents/                # canonical sub-agent specs (explorer, adversarial-reviewer, documenter)
│   ├── skills/                # canonical skill specs (dependabot-fixer, ship-release)
│   ├── pipelines/             # bugfix pipeline spec
│   ├── principles.md          # design calls behind the harness
│   ├── documentation.md       # wiki convention
│   ├── hooks.md               # hook design (PostToolUse / PreCompact / SessionStart)
│   ├── telemetry.md           # telemetry signals + thresholds
│   └── verification.md        # deterministic-gate definitions
├── adapters/                  # per-tool shims that point at harness/ specs
│   ├── claude-code/           # .claude/commands + .claude/agents + .claude/skills
│   ├── antigravity/           # .agent/workflows + .agent/skills + .agent/rules
│   ├── codex/                 # .agents/skills + .codex/agents
│   └── gemini/                # .gemini/commands + .gemini/agents + settings.json
├── templates/                 # what install.sh drops into a target project
│   ├── PLAN.md, features.json, progress.md, init.sh, verify.{sh,ps1}, known-migrations.md
│   ├── hooks/                 # hook scripts + settings-fragment JSON (bash + pwsh)
│   ├── scripts/               # cross-review.{sh,ps1}, telemetry.sh, etc.
│   └── wiki/                  # Diátaxis scaffold (tutorials, how-to, reference, explanation)
├── scripts/                   # test infra — NEVER propagated by install.sh
│   ├── smoke-install-{bash.sh,pwsh.ps1}
│   ├── check-integrity-{bash.sh,pwsh.ps1}
│   ├── check-parity.sh
│   ├── check-syntax.{sh,ps1}
│   ├── check-references.py
│   ├── check-wiki.py
│   └── validate-adapters.py
├── wiki/                      # THIS wiki — dogfood docs for the harness repo itself
│   ├── Home.md, _Sidebar.md, .diataxis
│   └── tutorials/, how-to/, reference/, explanation/
└── .github/workflows/
    ├── tests-linux.yml, tests-mac.yml, tests-windows.yml   # CI (never propagated)
    └── wiki-sync.yml                                        # (also shipped as a template)
```

## 🎨 The four adapters

Every adapter ships the same canonical set of phase commands, sub-agents, and skills. Their *shape* differs per tool, but the names and jobs match. [`scripts/check-parity.sh`](https://github.com/alexherrero/agentic-harness/blob/main/scripts/check-parity.sh) asserts this.

| Adapter | Phase commands | Sub-agents | Skills |
|---|---|---|---|
| `adapters/claude-code/` | `.claude/commands/*.md` | `.claude/agents/*.md` | `.claude/skills/*/SKILL.md` |
| `adapters/antigravity/` | `.agent/workflows/*.md` | (via skills) | `.agent/skills/*/SKILL.md` |
| `adapters/codex/` | (skills double as phases) | `.codex/agents/*.toml` | `.agents/skills/*/SKILL.md` |
| `adapters/gemini/` | `.gemini/commands/*.toml` | `.gemini/agents/*.md` | (reuses codex skills) |

Canonical sub-agents: `explorer`, `adversarial-reviewer`, `documenter`.
Canonical skills: `dependabot-fixer`, `ship-release`.

## Related

- [How the pieces fit](How-The-Pieces-Fit) — narrative of how phases / adapters / templates / scripts interact.
- [Installer CLI reference](Installer-CLI) — flags and owned-vs-managed tree.
- [CI gates reference](CI-Gates) — what each workflow proves.
- [ADR 0002](0002-documentation-convention) — why this wiki is never installed into target projects.
