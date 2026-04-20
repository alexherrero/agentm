# Codex CLI adapter research

**Status:** working document for Task 7b. Delete once Task 7b lands.
**Date:** 2026-04-19
**Subject:** OpenAI Codex CLI (the `codex` command shipped by OpenAI in 2025), not the deprecated 2021 model.

The five research questions below map 1:1 to Task 7a's verification criterion in `.harness/PLAN.md`.

## (a) Instruction-loading order

Codex reads `AGENTS.md` — same convention as Antigravity.

**Load order** (precedence: later files override earlier ones, concatenated into the system prompt at session start):

1. Global: `$CODEX_HOME/AGENTS.override.md` → `$CODEX_HOME/AGENTS.md`
   ($CODEX_HOME defaults to `~/.codex/`)
2. Project: walk from git root down to `cwd`; at each directory, load `AGENTS.override.md` then `AGENTS.md`
3. Fallback: if no `AGENTS.md` found, `project_doc_fallback_filenames` from config.toml (defaults include `CLAUDE.md`, `GEMINI.md`, `.cursorrules`)

**Limits:**

- `project_doc_max_bytes` defaults to 32 KiB; content beyond is truncated.
- Files loaded **once per session start** (SessionStart event fires afterwards); no live reload on file change.
- Markdown, no frontmatter required. Override files are conventional for user-local tweaks that shouldn't be committed.

**Source:** Codex docs, "Agents and instructions" page (codex.openai.com/docs — path inspected during research).

**Implication for harness:** our repo-root `AGENTS.md` loads in Codex without adaptation. No separate `rules/harness.md` file needed (unlike Antigravity).

## (b) Custom slash commands / prompts

**Custom prompts are DEPRECATED.** Previously, `~/.codex/prompts/*.md` defined custom slash commands invoked as `/prompts:name` with `description` + `argument-hint` frontmatter and `$1`–`$9` / `$ARGUMENTS` substitution. This mechanism was removed in favor of **skills** (see §c).

**Built-in slash commands that collide with harness phase names:**

- `/plan` — built-in Codex command (enters plan mode; different semantics from harness /plan)
- `/review` — built-in Codex command (adversarial review; overlaps but not identical)
- Also built-in: `/init`, `/skills`, `/agent`, `/plugins`, `/compact`, `/clear`, `/new`, `/resume`, `/fork`

**Implication for harness:** phase entrypoints must be **skills** (invoked via `$skill-name` mention or via `/skills`) rather than slash commands. Skill names must avoid the built-in namespace — the harness will use prefixed names like `harness-plan`, `harness-review` to make conflict-free.

Open question #1 (below) discusses whether to rename all six phase skills uniformly or only the two that collide.

## (c) Skills

Codex skills are the primary user-extensibility mechanism.

**Location (precedence, later overrides earlier):**

1. System: `/etc/codex/skills/`
2. Admin: `/etc/codex/skills/` (admin tier)
3. User: `$HOME/.agents/skills/` — **note the plural `.agents/`, distinct from Antigravity's `.agent/` singular**
4. Project root / parents / repo-local: `.agents/skills/` (walked like `AGENTS.md`)

**File layout per skill:**

```
.agents/skills/<skill-name>/
├── SKILL.md              (required — frontmatter: name, description)
├── agents/openai.yaml    (optional — Codex-specific config)
├── scripts/              (optional — executable helpers)
├── references/           (optional — reference material for the skill to load)
└── assets/               (optional — binary assets)
```

**`SKILL.md` frontmatter:**

```yaml
---
name: <skill-name>
description: <one-line description, matched against user prompts for implicit invocation>
---
```

Matches the harness's existing skill format (same as Claude Code and Antigravity).

**Optional `agents/openai.yaml`:**

```yaml
display_name: <human-readable>
allow_implicit_invocation: true|false
default_prompt: <prompt template>
dependencies:
  tools:
    - <MCP tool requirement>
```

**Invocation paths:**

1. `/skills` slash command — lists and invokes
2. `$skill-name` mention in chat — direct dispatch
3. Implicit — if `allow_implicit_invocation: true`, Codex matches `description` against user intent and auto-invokes

**Implication for harness:** skills map cleanly. Our six phase entrypoints + four sub-agents + `dependabot-fixer` = eleven skills total.

## (d) Subagents

Codex has a **separate subagent primitive** distinct from skills.

**Location:** `~/.codex/agents/` (user) and `<repo>/.codex/agents/` (project). **NOT `.agents/skills/`.** Different directory, different format.

**Format:** TOML files (one per subagent).

```toml
name = "<subagent-name>"
description = "<purpose>"
developer_instructions = "<system prompt>"

# Optional:
model = "<model-id>"
model_reasoning_effort = "low|medium|high"
sandbox_mode = "read-only|workspace-write|danger-full-access"
mcp_servers = ["<mcp-server-name>", ...]

[skills.config]
# skill-specific config
```

**Dispatch semantics:**

- **Explicit only** — user must request dispatch (no implicit matching like skills have).
- **Inherited-but-separate context** — subagent starts with its own context window but inherits workspace state. This is **NOT** a fresh-context guarantee like Claude Code's sub-agents. Closer to Antigravity's skill semantics.
- Config knobs: `agents.max_threads = 6`, `agents.max_depth = 1`, `agents.job_max_runtime_seconds = 1800`.

**Key capability that skills lack:** `sandbox_mode = "read-only"` — enforces read-only filesystem at the subagent level. This is the closest Codex gets to Claude Code's tool restriction semantics.

**Implication for harness:** the choice between routing the four sub-agents (`explorer`, `adversarial-reviewer`, `adversarial-reviewer-cross`, `documenter`) to Codex skills vs. Codex subagents is the central design question. See Open Question #2.

## (e) Hooks

Codex has a hooks system with syntax nearly identical to Claude Code's.

**Enable flag (required):**

```toml
# ~/.codex/config.toml OR <repo>/.codex/config.toml
[features]
codex_hooks = true
```

**Hooks definition:**

```json
// ~/.codex/hooks.json OR <repo>/.codex/hooks.json
{
  "hooks": {
    "EventName": [
      {
        "matcher": "<tool-name-or-pattern>",
        "hooks": [
          {"type": "command", "command": "<shell-command>", "timeout": 600}
        ]
      }
    ]
  }
}
```

**Supported events:**

- `SessionStart` — matchers: `startup` (fresh session), `resume` (resumed session)
- `PreToolUse` — matcher: **`Bash` only** (no Write/Edit matchers — critical limitation)
- `PostToolUse` — matcher: **`Bash` only**
- `UserPromptSubmit`
- `Stop`

**Disabled on Windows.**

**Key gap vs. Claude Code:**

Claude Code's harness ships a `PostToolUse` hook with matcher `Write|Edit` that triggers `verify.sh` (typecheck/lint/test on every file write). Codex's PostToolUse only matches `Bash`, so this pattern does not port.

**Workaround options:**

1. **Stop hook** — run verification once per turn's end (not per-write). Cheaper per-session, misses early-feedback loop.
2. **Rely on skill body** — the `harness-work` skill's prompt instructs the agent to run gates itself after implementing. Same pattern Antigravity uses (no hook surface at all).
3. **Document the gap** — tell Codex users that automated per-write verification is unavailable and advise the manual `./scripts/verify.sh` path.

**Recommendation:** option 2 + 3. The skill body already directs gate-running; the README discloses the per-write hook gap as a known divergence. We avoid shipping `hooks.json` in the adapter to keep the install surface minimal. Users who want Stop-based verification can opt in via the README.

## Layout proposal for Task 7b

Every file listed below is created by Task 7b, copied by `install.sh` to the target project at the indicated destination.

```
adapters/codex/
├── README.md                                   # Adapter docs: mapping table, rename rationale, hook-gap note
├── skills/                                     # copied to target's .agents/skills/
│   ├── harness-setup/SKILL.md                  # phase entrypoint (prefixed for uniformity)
│   ├── harness-plan/SKILL.md                   # phase entrypoint (collides w/ built-in /plan — renamed)
│   ├── harness-work/SKILL.md                   # phase entrypoint
│   ├── harness-review/SKILL.md                 # phase entrypoint (collides w/ built-in /review — renamed)
│   ├── harness-release/SKILL.md                # phase entrypoint
│   ├── harness-bugfix/SKILL.md                 # phase entrypoint
│   └── dependabot-fixer/SKILL.md               # project skill (unchanged from Claude Code)
└── agents/                                     # copied to target's .codex/agents/
    ├── explorer.toml                           # sub-agent (sandbox_mode = read-only)
    ├── adversarial-reviewer.toml               # sub-agent (sandbox_mode = read-only)
    ├── adversarial-reviewer-cross.toml         # sub-agent (sandbox_mode = workspace-write)
    └── documenter.toml                         # sub-agent (sandbox_mode = workspace-write)
```

**Destinations in target project:**

- `adapters/codex/skills/*` → target's `.agents/skills/` (plural `.agents/`)
- `adapters/codex/agents/*.toml` → target's `.codex/agents/` (singular `.codex/`)

No `rules/` dir — AGENTS.md at repo root is read directly by Codex.

**Install.sh wiring needed (Task 7b):**

```bash
# After the .agent/ block for Antigravity, add:
if [[ -d "$HARNESS_ROOT/adapters/codex/skills" ]]; then
  cp_managed_dir "$HARNESS_ROOT/adapters/codex/skills" "$TARGET/.agents/skills"
fi
if [[ -d "$HARNESS_ROOT/adapters/codex/agents" ]]; then
  cp_managed_dir "$HARNESS_ROOT/adapters/codex/agents" "$TARGET/.codex/agents"
fi
```

No hooks.json or config.toml shipped (per Open Question #3). AGENTS.md at repo root already covers instruction loading.

**File count:** 7 SKILL.md + 4 TOML + 1 README.md = 12 files.

## Open questions

### Open question #1: Should all six phase skills be prefixed, or only the two that collide?

**Options:**

- **(A) All six prefixed** (`harness-setup`, `harness-plan`, `harness-work`, `harness-review`, `harness-release`, `harness-bugfix`) — uniform, easy to discover via `$harness-` prefix match.
- **(B) Only colliding ones prefixed** (`setup`, `harness-plan`, `work`, `harness-review`, `release`, `bugfix`) — shorter names where possible, irregular.

**Answer before Task 7b:** **(A) — all six prefixed.**

**Why:**
1. Consistency beats brevity. Users type `$harness-` and get tab-completion to all six phases.
2. Future-proof against new Codex built-ins. If Codex later adds `/setup` or `/work` as built-ins, we don't have to rename mid-stream.
3. Names still short enough to type.
4. `README.md` invocation examples read cleaner with a consistent prefix.

### Open question #2: Sub-agents as skills or as TOML subagents?

**Options:**

- **(A) All four as skills in `.agents/skills/`** — matches Antigravity's model, same file shape as the Claude Code adapter's dispatch target.
- **(B) All four as TOML subagents in `.codex/agents/`** — gets `sandbox_mode` enforcement (read-only for reviewers, workspace-write for documenter); closest Codex primitive to Claude Code's sub-agent semantics.
- **(C) Mixed** — reviewers as TOML subagents (read-only sandbox), documenter as skill (writes).

**Answer before Task 7b:** **(B) — all four as TOML subagents.**

**Why:**
1. **Sandbox enforcement is a real win.** `sandbox_mode = "read-only"` on `explorer` and both `adversarial-reviewer*` structurally prevents the reviewer from editing the code it's reviewing — no discipline-based mitigation needed. `documenter` gets `sandbox_mode = "workspace-write"` which still blocks writes outside the workspace.
2. **Closest match to Claude Code semantics.** Claude Code dispatches sub-agents with a tool allowlist; Codex TOML subagents achieve the equivalent via sandbox_mode + mcp_servers restriction. Skills have no such enforcement.
3. **Antigravity parity is not a constraint.** The Antigravity adapter uses skills only because Antigravity has no subagent primitive; Codex has one, so use it.
4. **Per-subagent model / reasoning-effort tuning.** TOML exposes `model` and `model_reasoning_effort` — useful for `adversarial-reviewer` (high effort) vs. `explorer` (fast).
5. **Two install targets is tolerable.** `install.sh` already juggles multiple adapter destinations; adding `.codex/agents/` alongside `.agents/skills/` is a small increment.

**Trade-off accepted:** two authoring formats (markdown skills + TOML subagents) and two install target directories. Mitigated by scoping: skills host phase entrypoints + the one project skill (`dependabot-fixer`); TOML subagents host the four narrow-contract sub-agents.

**Sandbox-mode assignments:**

| Subagent | sandbox_mode | Rationale |
|---|---|---|
| `explorer` | `read-only` | Read-only fan-out; no writes needed. |
| `adversarial-reviewer` | `read-only` | Reviewer must not mutate code under review. |
| `adversarial-reviewer-cross` | `workspace-write` | Shells out to `gemini` CLI; may need tmpfile for prompt staging. Revisit if `read-only` suffices. |
| `documenter` | `workspace-write` | Writes to `wiki/**` and `.harness/project.json`. |

### Open question #3: Do we ship `hooks.json` for Stop-based verification?

**Options:**

- **(A) Ship `.codex/hooks.json` with a Stop hook calling `./scripts/verify.sh`** — closer to Claude Code's behavior.
- **(B) Ship nothing; document the gap.** — simpler install surface.

**Answer before Task 7b:** **(B) — ship nothing.**

**Why:**
1. The `harness-work` SKILL.md already instructs the agent to run gates before marking a task complete (see `harness/phases/03-work.md` step 7).
2. Stop-based verification runs once per turn, which can conflict with the per-session gate cycle (user may have multiple turns before gate).
3. Per-user preference. Users who want automated verification can add it themselves via their user-local `~/.codex/hooks.json`.
4. Keeps `install.sh` simpler — no config.toml feature-flag wiring, no `.codex/` directory creation.

**Trade-off accepted:** Codex users get slightly less automation than Claude Code users. Documented in `adapters/codex/README.md` as a known divergence with a copy-pasteable opt-in snippet for users who want it.

## Summary for Task 7b implementation

- 12 files to create under `adapters/codex/` (7 SKILL.md + 4 TOML subagents + 1 README.md).
- 2 install.sh blocks to add (`cp_managed_dir` for `.agents/skills/` and `.codex/agents/`).
- Phase entrypoints + `dependabot-fixer` as skills; four sub-agents as TOML subagents with sandbox_mode enforcement.
- Phase skills all prefixed `harness-` for collision avoidance and consistency.
- No hooks shipped; README documents the per-write hook gap and provides an opt-in snippet.
