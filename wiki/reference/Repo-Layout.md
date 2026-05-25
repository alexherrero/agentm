# Repo layout reference

Top-level layout of agentm on disk. For the *why* of this shape, see [How the pieces fit](How-The-Pieces-Fit), [ADR 0001](0001-phase-gated-workflow), and [ADR 0002](0002-documentation-convention).

## ⚡ Quick Reference

| Question | Answer |
|---|---|
| Where does a phase spec live? | [`harness/phases/`](https://github.com/alexherrero/agentm/tree/main/harness/phases) — one canonical `.md` per phase |
| Where does an adapter live? | [`adapters/<tool>/`](https://github.com/alexherrero/agentm/tree/main/adapters) — claude-code, antigravity, gemini |
| Where does the install scaffold live? | [`templates/`](https://github.com/alexherrero/agentm/tree/main/templates) — state files, hooks, wiki scaffold |
| Where does the test infra live? | [`scripts/`](https://github.com/alexherrero/agentm/tree/main/scripts) — **never propagated to target projects** |
| Where does this wiki get copied from on install? | Nowhere. Target projects get `templates/wiki/` (empty scaffold), not this one. See [ADR 0002](0002-documentation-convention). |
| Where do personal customizations live? | [`crickets`](https://github.com/alexherrero/crickets) — sibling repo (since v2.0.0 / ADR 0006). Skills, sub-agents, hooks, MCP servers, slash commands, bundles, etc. |
| Where does the shared install plumbing live? | [`lib/install/`](https://github.com/alexherrero/agentm/tree/main/lib/install) — byte-identical to `crickets/lib/install/`. Sync via `scripts/sync-lib.sh`; CI gates parity. |

## 📁 Top-level layout

```
agentm/
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
│   ├── skills/                # canonical skill specs (doctor, migrate-to-diataxis) — see ADR 0006
│   ├── pipelines/             # bugfix pipeline spec
│   ├── principles.md          # design calls behind the harness
│   ├── documentation.md       # wiki convention
│   ├── hooks.md               # hook design (PostToolUse / PreCompact / SessionStart)
│   ├── telemetry.md           # telemetry signals + thresholds
│   └── verification.md        # deterministic-gate definitions
├── adapters/                  # per-tool shims that point at harness/ specs
│   ├── claude-code/           # .claude/commands + .claude/agents + .claude/skills
│   ├── antigravity/           # .agent/workflows + .agent/skills + .agent/rules
│   └── gemini/                # .gemini/commands + .gemini/agents + settings.json (shared .agents/skills delivered by installer)
├── lib/                       # shared install plumbing (byte-identical to crickets/lib/) — see ADR 0006
│   └── install/               # cp_managed, cp_user, ensure_boundary_src, sync_managed_parents (bash + pwsh)
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
│   ├── check-lib-parity.sh    # byte-identity gate for lib/install/
│   ├── check-no-pii.sh        # PII regex scanner (also gated in CI via gitleaks-action)
│   ├── sync-lib.sh            # one-shot lib/install/ sync agentm → ../crickets
│   └── validate-adapters.py
├── wiki/                      # THIS wiki — dogfood docs for the harness repo itself
│   ├── Home.md, _Sidebar.md, .diataxis
│   └── tutorials/, how-to/, reference/, explanation/
└── .github/workflows/
    ├── tests-linux.yml, tests-mac.yml, tests-windows.yml   # CI (never propagated)
    └── wiki-sync.yml                                        # (also shipped as a template)
```

## 🎨 The three adapters

Every adapter ships the same canonical set of phase commands, sub-agents, and skills. Their *shape* differs per tool, but the names and jobs match. [`scripts/check-parity.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/check-parity.sh) asserts this.

| Adapter | Phase commands | Sub-agents | Skills |
|---|---|---|---|
| `adapters/claude-code/` | `.claude/commands/*.md` | `.claude/agents/*.md` | `.claude/skills/*/SKILL.md` |
| `adapters/antigravity/` | `.agent/workflows/*.md` | (via skills) | `.agent/skills/*/SKILL.md` |
| `adapters/gemini/` | `.gemini/commands/*.toml` | `.gemini/agents/*.md` | reads `.agents/skills/*/SKILL.md` (delivered by `install.sh` per Agent Skills standard) |

Canonical sub-agents: `explorer`, `adversarial-reviewer`, `adversarial-reviewer-cross`, `documenter`.
Canonical skills: `dependabot-fixer`, `doctor`, `migrate-to-diataxis`, `ship-release`.

## Related

- [How the pieces fit](How-The-Pieces-Fit) — narrative of how phases / adapters / templates / scripts interact.
- [Installer CLI reference](Installer-CLI) — flags and owned-vs-managed tree.
- [CI gates reference](CI-Gates) — what each workflow proves.
- [ADR 0002](0002-documentation-convention) — why this wiki is never installed into target projects.
