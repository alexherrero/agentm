---
name: wiki-author
description: "Update or create a Diátaxis wiki page for the current repo (or another registered repo). Triggers when the operator says 'update the wiki', 'document this in the wiki', 'add this to the wiki', 'create a wiki page for X', or 'update <repo>'s wiki'. Resolves target repo via cwd (default) or explicit repo name (cross-repo via repo_registry from V4 #30 plan 1). Dispatches the documenter sub-agent for the structural edit with preview-before-write. Defers to crickets' wiki-maintenance diataxis-author skill for mode selection when available (graceful-skip if crickets is not installed). Honors per-repo .diataxis-conventions.md override. Does NOT auto-generate content — agent gathers context from the conversation; the skill handles dispatch + write contract."
kind: skill
supported_hosts: [claude-code]
version: 0.1.0
install_scope: user
---

# wiki-author — operator-facing dispatcher for wiki edits

Auto-fires on operator phrases like *"update the wiki with what we just shipped"* or *"add this to the wiki"*. Resolves which Diátaxis mode page to update or create, then dispatches the **`documenter` sub-agent** (the write-executor, hard-boundary-scoped to `wiki/**` per [ADR 0004 Amendment 2026-05-27](../../../wiki/explanation/decisions/0004-diataxis-documentation-spec.md#amendment-2026-05-27)). Operator confirms every write via preview-before-write gate.

## Triggers and non-triggers

This skill auto-activates on **imperative wiki-write phrases**. The trigger set is intentionally narrow to avoid false-positives in unrelated conversation.

### Trigger phrases (5/5 expected to fire)

| Phrase pattern | Resolution |
|---|---|
| *"update the wiki"* / *"update the wiki for this repo"* | cwd-relative; documenter picks the most-relevant page from the conversation diff |
| *"document this in the wiki"* / *"add this to the wiki"* | cwd-relative; mode picked from content kind (refactor → explanation; new command → reference; bugfix walkthrough → how-to) |
| *"create a wiki page for X"* / *"add a wiki page about X"* | cwd-relative; ask operator for mode if not derivable from X |
| *"update {repo-slug}'s wiki"* — explicit cross-repo | resolves target via `python3 scripts/repo_registry.py list`; writes to `<root>/wiki/` |
| *"what's the latest in {repo-slug}'s wiki"* — read-only query | redirects operator to `/recent-wiki-changes --repo {slug}` slash command (separate surface) |

### Non-triggers (5/5 expected NOT to fire)

| Phrase | Why not |
|---|---|
| *"the wiki page mentions..."* | descriptive, not imperative |
| *"I saw it on Wikipedia"* | unrelated topic |
| *"the wiki/ tree"* | descriptive code reference |
| *"wiki articles vs documentation"* | discussion, not write request |
| *"docs in wiki/explanation"* | path reference, not a write request |

If operator's phrasing is ambiguous (e.g. *"add this somewhere"*), do NOT fire — ask the operator to clarify or use an explicit phrase.

## Resolution: cwd vs cross-repo

1. **Default = cwd-relative.** If operator says *"update the wiki"* from inside a registered repo, target is `<cwd-root>/wiki/`.
2. **Explicit cross-repo via slug.** If phrase names another slug (*"update sherwood's wiki"*), resolve via `repo_registry.list_repos()`:
   ```bash
   python3 scripts/repo_registry.py list | python3 -c "
   import json, sys
   d = json.load(sys.stdin)
   for r in d.get('repos', []):
       if r.get('slug') == 'sherwood':
           print(r.get('root_path'))
           break
   "
   ```
3. **Cwd outside any registered repo + no explicit slug.** Refuse with actionable error:
   > "Current directory isn't a registered repo. Register first: `python3 scripts/repo_registry.py register <slug> --root <path>`. Or specify the target: *update {slug}'s wiki*."
4. **`MEMORY_VAULT_PATH` unset OR registry empty.** Falls back to cwd-only mode. Cross-repo phrases refuse with: "Cross-repo writes need a vault-backed repo registry (V4 #30 plan 1). Run `bash install.sh --scope user` to set up, OR work in cwd."

## Dispatch contract

After resolution, this skill ALWAYS:

1. **Loads the per-repo `.diataxis-conventions.md` override** if present in the target repo root. Operator-locked conventions take precedence over global defaults.
2. **Loads the doc-write-time context bundle (V4 #35)** for the resolved target repo:
   ```bash
   python3 scripts/harness_memory.py documenter-context --slug "<resolved-target-slug>" --format text
   ```
   `<resolved-target-slug>` is the cwd project's slug in cwd mode (`python3 scripts/vault_project.py read .`) or the explicitly-named slug in cross-repo mode. The bundle carries *operator conventions to honor + project decisions to respect + locked design calls to NOT re-litigate*. **Graceful-skip:** on **rc 1** (vault unreachable) emit `[wiki-author] vault unreachable; proceeding without vault context` and continue with pre-v4.6.0 behavior. On **rc 2** (target not registered) the bundle still carries operator-global `_always-load/` conventions — use them. This routes through the same resolver the documenter sub-agent uses, so the operator's settled calls surface uniformly across both surfaces.
3. **Determines Diátaxis mode** for the write target:
   - **Update existing page**: preserves the page's existing mode (don't cross-mode mix; that fails `check-wiki.py --strict`).
   - **Create new page**: derives mode from the phrase ("how-to for X" → how-to dir; "reference for X" → reference dir); if ambiguous, asks the operator interactively. Defers to crickets' `wiki-maintenance` `diataxis-author` skill if available for richer mode selection (graceful-skip when crickets is not installed).
4. **Drafts the structural edit + emits a unified diff preview** to the operator before writing — **and surfaces the step-2 context bundle alongside the diff** so the operator sees *here's what's locked + what we've decided before* next to the proposed change. Per-write gate (every cross-repo edit gates on approval).
5. **On operator approval**: dispatches the `documenter` sub-agent with the resolved target + draft content. Documenter performs the actual file write under its hard-boundary scope (per [documenter spec](../../agents/documenter.md#cross-repo-write-contract-v4-30-plan-2--2026-05-27)).
6. **Cross-references**: ADR 0004 Amendment 2026-05-27 (preview-before-write + .diataxis-conventions override + repo_registry resolution); documenter sub-agent spec (write-scope hard boundary + V4 #35 pre-flight); crickets' `wiki-maintenance` `diataxis-author` skill (mode selection + per-repo conventions, when crickets is installed).

## What this skill does NOT do

- **Does NOT auto-generate content** — the agent invoking this skill gathers context from the conversation (the diff just landed, the design call locked, the bug-fix narrative); the skill's job is dispatch + write contract, not content generation. Content generation is V5 / V6 territory.
- **Does NOT write outside `wiki/**`** — documenter's hard-boundary scope is `wiki/**` + a few specific files; this skill never relaxes that.
- **Does NOT batch writes without per-write approval** — even when multiple pages need updates from the same conversation, each cross-repo write gates on operator confirmation.
- **Does NOT trigger on read-only wiki queries** — those route to the `/recent-wiki-changes` slash command (added V4 #30 plan 2 task 7).

## When NOT to invoke this skill

- Operator is reading the wiki (descriptive language, not imperative).
- Operator is asking about wiki structure or conventions (route to ADR 0004 / Diátaxis docs instead).
- Operator wants to write to a NON-wiki file (PLAN.md, progress.md, AGENTS.md, source code) — those are off-limits for documenter; refuse + route the operator to the appropriate surface (the harness `/work` phase, manual edit, etc.).

## Per-host adapter

This skill is `supported_hosts: [claude-code]` for v0.1.0. The trigger semantics rely on Claude Code's skill-description matching; other hosts (e.g. Antigravity) would need their own trigger mechanism (slash commands, explicit invocation, etc.). Future versions may extend to `supported_hosts: [claude-code, antigravity]` once Antigravity's skill-triggering semantics stabilize.
