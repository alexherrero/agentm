---
name: diataxis-author
description: Author + maintain a Diátaxis-style wiki for any repo. Live authoring guidance (mode selection + template-fill + filename style), ongoing drift detection + repair, one-shot migration of legacy audience-based wikis to the four-mode (tutorials / how-to / reference / explanation) discipline, and single-page mode classification with sub-agent fallback on ambiguous cases. Reads operator conventions from AgentMemory `_always-load/diataxis-*.md`; offers to capture judgment calls back as new conventions (operator-confirmed via permeable-boundary helper). Dispatches the existing `documenter` sub-agent for mechanical-write work; never auto-forks into wiki/ without preview. Subsumes the predecessor `migrate-to-diataxis` skill (harness-side) per ROADMAP #13. Hosts: Claude Code + Antigravity (`gemini-cli` removed in v0.9.0 per ADR 0006).
kind: skill
supported_hosts: [claude-code, antigravity]
version: 0.1.0
install_scope: project
---

# diataxis-author — author + maintain a Diátaxis wiki for any repo

The second major skill in `crickets` (after `memory`). Encodes the operator's Diátaxis discipline from [agentm ADR 0004 — Diátaxis Documentation Spec](https://github.com/alexherrero/agentm/blob/main/wiki/explanation/decisions/0004-diataxis-documentation-spec.md) into proactive authoring guidance + ongoing drift detection + repair + one-shot migration, with per-repo overrides via `wiki/.diataxis-conventions.md` and global conventions stored in AgentMemory (`_always-load/diataxis-*.md`). Designed via [crickets's design skill](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/diataxis-author.md) — second real dogfood of plan #6's `/design author` after MemoryVault closed.

**Position vs. `check-wiki.py` strict validator**: validators catch violations after-the-fact; diataxis-author provides **proactive** guidance at write time (template selection, mode classification, filename style). Both layers complement: skill prevents drift at write time; `check-wiki.py` catches drift at commit time + during `/diataxis check`.

**Position vs. harness's `documenter` sub-agent**: documenter is the **mechanical write worker** (Diátaxis-aware writes per ADR 0004 §4); diataxis-author is the **operator-facing orchestration + guidance surface**. Skill dispatches documenter for splits + heavy writes — same separation as `/memory adapt-skills` (orchestration) vs. `adapt-evaluator` (sub-agent worker) from plan #7b task 4.

**Position vs. predecessor `migrate-to-diataxis` (harness-side)**: subsumed (plan #13 part 4). v1 ships the predecessor with a deprecation notice; follow-up harness release removes the file entirely after dogfood confirms diataxis-author is the better surface.

## Operator convention read path (V4 #35)

The global Diátaxis conventions this skill honors (`_always-load/diataxis-*.md` — filename style, mode-classification thresholds, page-length norms) are read through the **shared `documenter-context` resolver**, NOT by globbing the vault directly:

```bash
python3 scripts/harness_memory.py documenter-context --slug "<slug>" --format json
# → parse `.operator_conventions[]` for the diataxis-* entries
```

This keeps the read pattern uniform across all three doc-touching primitives (this skill, `wiki-author`, and the `documenter` sub-agent): one resolver is the single source of truth, so a future move to per-project conventions (`projects/<slug>/wiki-style/*.md`, already surfaced by the resolver) needs no skill-side change.

**Graceful-skip (same contract as the other two primitives):** on **rc 1** (vault unreachable) proceed with the built-in defaults + per-repo `wiki/.diataxis-conventions.md` only — emit `[diataxis-author] vault unreachable; using built-in + per-repo conventions` on stderr; never hard-fail. On **rc 2** (slug unregistered) the operator-global `_always-load/` conventions still resolve.

The full read-side wiring lands with part 5 (`agentmemory-docs-release`); this section locks the *path* (through the resolver) so part 5 doesn't reintroduce a direct vault glob.

## When to reach for which sub-command

| You want to... | Reach for |
|---|---|
| Author a new wiki page with template + mode selection + filename style | `/diataxis author <slug>` |
| Detect drift across the wiki (mode-mixed pages, stale cross-refs, template-shape drift, convention drift) | `/diataxis check [--strict]` |
| Apply suggested fixes for detected drift, interactively | `/diataxis repair` |
| One-shot migrate a legacy audience-based wiki to the four-mode Diátaxis layout | `/diataxis migrate` |
| Classify a single page's mode (operator-debug; sub-agent dispatches here for ambiguous cases) | `/diataxis classify <file>` |

## Sub-commands

### `/diataxis author <slug>`

Live authoring guidance — operator invokes when starting a new wiki page. Skill resolves the mode (explicit `--mode` flag, or inferred from `--intent <sentence>` via `classify.py`, or operator prompt), loads the right Diátaxis template from `templates/<mode>.md`, applies the operator's filename style, computes target path `<wiki-root>/<mode-dir>/<filename>.md`, refuses if target exists (operator picks a different slug), and writes the skeleton. Operator edits in their editor; skill doesn't write further content after the skeleton.

#### Invocation shape

```
/diataxis author <slug> [--mode <tutorial|how-to|reference|explanation>]
                        [--intent "<one sentence>"]
                        [--filename-style <CamelCase-With-Dashes|kebab-case|snake_case>]
                        [--wiki-root <path>]
                        [--overwrite]
```

| Arg | Required | Default | Meaning |
|---|---|---|---|
| `<slug>` | yes | — | Page slug (e.g. "Install foo into a project"). Words extracted via alphanumeric splitting; filename style applied. |
| `--mode <m>` | yes¹ | — | Diátaxis mode. ¹Required unless `--intent` provided (then `classify.py` infers); halts with operator-prompt if neither given. |
| `--intent "<text>"` | no | — | One-sentence intent statement. Passed to `classify.py` as classification input; mode inferred if heuristic confidence ≥0.7. Sub-agent dispatch triggered if below threshold. |
| `--filename-style <s>` | no | `CamelCase-With-Dashes` | Filename style. Three options: `CamelCase-With-Dashes` (default) / `kebab-case` / `snake_case`. AgentMemory always-load entry override planned for part 5. |
| `--wiki-root <path>` | no | `./wiki` | Wiki root directory. Auto-detected as `<cwd>/wiki`; override for cross-repo invocations. |
| `--overwrite` | no | off | Allow overwriting an existing target. Default: refuse + ask operator to pick a different slug. |

#### Step-by-step flow

**Step 1 — Resolve mode.** If `--mode` set, use it directly. If `--intent` set, call `classify.py` on the intent text + check `needs_subagent` flag; halt + prompt operator for explicit `--mode` if classifier confidence is below threshold. Otherwise halt with "ERROR: --mode or --intent required".

**Step 2 — Validate inputs.** Mode in `{tutorial, how-to, reference, explanation}`; filename style in valid set; wiki root exists + is a directory.

**Step 3 — Compute target path.** Apply filename style to slug → base name. Mode-to-directory mapping: `tutorial → tutorials/` (plural per Diátaxis convention; only this mode is pluralized), `how-to → how-to/`, `reference → reference/`, `explanation → explanation/`. Target = `<wiki-root>/<mode-dir>/<base>.md`.

**Step 4 — Collision check.** If target exists + `--overwrite` not set, halt with `"target already exists: <path>; pick a different slug or pass --overwrite"`.

**Step 5 — Load template.** Read `templates/<mode>.md` (ships in this part). Halt if missing (shouldn't happen post-install; indicates broken install).

**Step 6 — Create parent dirs + write.** `mkdir -p` for the mode-dir; write template content via `write_bytes` (LF-only line endings — Windows portability per `save.py` + `adapt_skills.py` convention).

**Step 7 — Return confirmation.** JSON: `{action: "authored", target: <path>, mode: <mode>, template: <path>, filename_style: <style>, filename: <base.md>}`. Operator opens the target file in their editor + fills in placeholders.

#### Examples

```bash
# Explicit mode
python3 ~/Antigravity/crickets/skills/diataxis-author/scripts/author.py \
  "Install crickets into a project" --mode how-to
# → wiki/how-to/Install-Agent-Toolkit-Into-A-Project.md

# Infer mode from intent
python3 ~/Antigravity/crickets/skills/diataxis-author/scripts/author.py \
  "CLI reference for the installer" --intent "lookup table for all install.sh flags"
# → wiki/reference/Cli-Reference-For-The-Installer.md  (inferred: reference)

# Kebab-case filename style
python3 ~/Antigravity/crickets/skills/diataxis-author/scripts/author.py \
  "Tutorial 3 — Advanced things" --mode tutorial --filename-style kebab-case
# → wiki/tutorials/tutorial-3-advanced-things.md
```

#### Failure modes (graceful)

- **Missing wiki root** → exit 1 with `"wiki root not found: <path>; pass --wiki-root or cd into a project with a wiki/ dir"`.
- **Target collision** (page exists) → exit 1 with operator next-step.
- **Mode + intent both missing** → exit 1 with `"--mode or --intent required"`.
- **Intent classification ambiguous** (confidence < threshold) → exit 1 with classifier output + prompt to disambiguate via explicit `--mode`.
- **Template missing** → exit 1 with broken-install indication.
- **Invalid mode** → exit 1 with valid options listed.

#### Anti-patterns

- **Don't pass `--overwrite` casually.** The collision refusal is intentional — re-using a slug across modes (e.g. how-to + explanation with the same name) is usually a sign you meant to split a mode-mixed page. Use `/diataxis classify` to verify.
- **Don't write skill body content beyond the skeleton.** The skill emits the template + lets the operator fill in. Auto-writing body content would defeat the "live authoring guidance" promise.
- **Don't bypass `--filename-style`.** The default `CamelCase-With-Dashes` matches the operator's convention from the canonical wikis (agentm + crickets + dev-setup). AgentMemory override (planned part 5) lets you tune per-repo.

### `/diataxis check [--strict]`

Drift detection — wraps `scripts/check-wiki.py` (harness-side) as a subprocess + adds 3 operational skill-side heuristics (mode-mixed + ambiguous-mode page detection / stale cross-references / template-shape drift) + 1 stub heuristic (convention drift; full wiring lands part 5 alongside AgentMemory). Outputs a structured JSON report grouped by rule. Non-zero exit on findings. Graceful-skip when check-wiki.py absent → in-skill heuristic-only mode + clear stderr warning.

#### Invocation shape

```
/diataxis check [--strict] [--wiki-root <path>] [--check-wiki-py <path>]
```

| Arg | Required | Default | Meaning |
|---|---|---|---|
| `--wiki-root <path>` | no | `./wiki` | Wiki root directory. |
| `--strict` | no | off | Pass `--strict` to check-wiki.py + escalate skill-side warnings to errors. |
| `--check-wiki-py <path>` | no | auto-detect sibling clone | Explicit path to check-wiki.py for non-standard layouts. |

#### Step-by-step flow

**Step 1 — Resolve wiki root + check-wiki.py path.** Auto-detect check-wiki.py at `<toolkit>/../agentm/scripts/check-wiki.py` or `~/Antigravity/agentm/scripts/check-wiki.py`.

**Step 2 — Run check-wiki.py subprocess** (if found). Parse `<path>:<line>: <rule>: <msg>` line format from stdout into Finding objects with `rule: check-wiki/<rule>`. Status: `ran` on success; `skipped-absent` if not found; `skipped-error` on subprocess failure (timeout, non-zero exit not from clean run, OSError).

**Step 3 — Walk wiki pages** under each mode-dir, skipping structural pages (Home.md, _Sidebar.md, README.md).

**Step 4 — Apply 4 skill-side heuristics per page**:

- `diataxis/mode-mixed` — calls `classify.py`; flags when `mode_mixed: true` OR `needs_subagent: true` (latter catches the penalty-masked case where how-to score drops below 0.5 due to rationale-section penalty but the page is still mode-mixed semantically).
- `diataxis/stale-xref` — extracts wiki-style markdown links; resolves target stem; flags when no matching page exists.
- `diataxis/template-drift` — page lives in mode-dir X but classify.py says it's mode Y with confidence ≥0.7. Suggested fix: move file or rewrite body.
- `diataxis/convention-drift` — v1 stub (always None); part 5 wires AgentMemory read-side to compare against operator-defined conventions (via the `documenter-context` resolver per the *Operator convention read path (V4 #35)* section above — not a direct vault glob).

**Step 5 — Aggregate + emit report.** Findings grouped by rule with counts. JSON output to stdout; non-zero exit on findings.

#### Examples

```bash
# Run against the toolkit's own wiki
python3 ~/Antigravity/crickets/skills/diataxis-author/scripts/check.py \
  --wiki-root ~/Antigravity/crickets/wiki
# Strict mode (escalate warnings)
python3 ~/Antigravity/crickets/skills/diataxis-author/scripts/check.py \
  --wiki-root ~/Antigravity/crickets/wiki --strict
# Custom check-wiki.py path
python3 ~/Antigravity/crickets/skills/diataxis-author/scripts/check.py \
  --wiki-root /path/to/project/wiki \
  --check-wiki-py /path/to/check-wiki.py
```

#### Failure modes (graceful)

- **No wiki root** → exit 1 with actionable error.
- **check-wiki.py absent** → `check_wiki_status: skipped-absent`; skill heuristics still run; warning on stderr.
- **check-wiki.py subprocess error** (timeout / OS error) → `check_wiki_status: skipped-error`; warning + heuristics-only fallback.
- **Empty wiki** → 0 findings; exit 0.
- **No mode-dir pages found** → skill heuristics emit 0 findings; exit 0 if check-wiki also clean.

#### Anti-patterns

- **Don't rely on `/diataxis check` to catch every Diátaxis violation.** check-wiki.py is the strict validator; the skill heuristics catch additional drift signals (mode-mixed, template-drift). For pre-commit gating, run `check-wiki.py --strict` directly (it's the canonical CI gate). `/diataxis check` is for **interactive audit + drift surfacing**, not the gate.
- **Don't pipe `/diataxis check` output to `/memory save`.** The JSON report is an interactive surface; persistence belongs in the wiki itself (operator decides what to act on via `/diataxis repair`).
- **Don't tune the skill-side heuristic thresholds per-invocation.** Defaults are tuned to v1 fixtures; operator-level tuning belongs in `classify.py` constants + future AgentMemory always-load entries (part 5).

### `/diataxis repair`

Interactive fix-application for drift detected by `/diataxis check`. Per finding: presents the suggested fix + operator approves (`a`) / edits (`e`) / rejects (`r`) / skips (default). Preview-first; never silent. Mode-mixed splits dispatch `documenter` sub-agent for the mechanical write work (the first consumer of `documenter` as worker per locked design call Q3). Same pattern as `/memory watchlist review` + `ideas_promote.py gc`'s never-silent-action contract.

#### Invocation shape

```
/diataxis repair [--wiki-root <path>] [--findings <json-path>] [--limit N] [--stub]
```

| Arg | Required | Default | Meaning |
|---|---|---|---|
| `--wiki-root <path>` | no | `./wiki` | Wiki root directory. |
| `--findings <path>` | no | inline check.py run | JSON file with findings (output of check.py). If omitted, runs check.py inline. |
| `--limit N` | no | unlimited | Cap on findings to review per invocation. Useful for batched repair: do N today, more tomorrow. |
| `--stub` | no | off | `documenter` sub-agent dispatches return no-op marker instead of invoking. Used by CI smoke tests to avoid live LLM calls. |

#### Step-by-step flow

**Step 1 — Resolve wiki root + load findings.** If `--findings <path>` set, parse JSON. Otherwise run `check.run_check()` inline.

**Step 2 — TTY check.** If stdin is not a TTY, default ALL prompts to skip + emit warning. Same never-silent-action contract as `ideas_promote.py gc`.

**Step 3 — Per-finding loop** (capped by `--limit N` if set):

1. Display finding (`──`-separated card with rule + file + severity + msg + suggested fix).
2. Prompt `Action: [a]pply / [e]dit / [r]eject / (default: skip)`.
3. Operator chooses; default = skip on non-TTY or empty input.

**Step 4 — Apply repair per rule** (when action == `apply`):

- `diataxis/template-drift` → preview a `git mv` from current path to the suggested mode-dir path. v1 emits the preview ONLY (operator runs git mv manually); v2 may add an `--auto-apply` flag.
- `diataxis/mode-mixed` → dispatch `documenter` sub-agent for the actual split. CLI emits the dispatch marker; the calling skill body (or operator interactive context) handles the dispatch. In `--stub` mode, returns canned marker without invoking.
- `diataxis/stale-xref` → record finding only; operator manually rewrites the link target (right target is judgment; no auto-fix in v1).
- `diataxis/convention-drift` → v1 stub; full handling lands in part 5.
- `check-wiki/*` → record finding only; operator manually addresses each (check-wiki rules surface known violations from the validator).

**Step 5 — Emit summary.** Per-action counts (applied / edited / rejected / skipped / errors) + per-finding results array. JSON to stdout.

#### Examples

```bash
# Interactive review against ./wiki
python3 ~/Antigravity/crickets/skills/diataxis-author/scripts/repair.py
# Use a pre-recorded findings file (replay analysis)
python3 ~/Antigravity/crickets/skills/diataxis-author/scripts/check.py --wiki-root wiki > findings.json
python3 ~/Antigravity/crickets/skills/diataxis-author/scripts/repair.py --findings findings.json
# CI smoke-safe with sub-agent stub + small limit
python3 ~/Antigravity/crickets/skills/diataxis-author/scripts/repair.py --stub --limit 3
```

#### Failure modes (graceful)

- **Non-TTY stdin** → defaults all prompts to skip; emits warning; exit 0 with all findings marked skipped.
- **No findings** → emits `no findings to repair`; exit 0.
- **Findings JSON malformed** → exit 1 with parse error.
- **`--limit N` reached mid-walk** → prints `reached --limit N; X findings unreviewed`; exit 0.

#### Anti-patterns

- **Don't apply mode-mixed splits without operator review of the proposed split.** The sub-agent suggests; the operator decides. `--stub` mode in CI exists precisely to avoid silent sub-agent dispatch.
- **Don't run `/diataxis repair` in batch mode (non-TTY).** The never-silent-action contract makes it a no-op; use specific-rule sub-commands or operator-driven scripting if you need batch action.
- **Don't auto-apply template-drift moves** without verifying the operator's intent. A page that "looks like" a tutorial but lives in `reference/` may be intentionally cross-cutting (especially for ADRs that reference how-tos). v1 emits preview only; v2 may add auto-apply behind explicit flag.

> [!NOTE]
> **Sub-agent budget**: `--limit N` (no hard default) caps how many findings each interactive pass processes. For batched contexts (future idle-hook auto-repair, out of scope for v1), default would land at 3-5 to bound sub-agent dispatches.

### `/diataxis repair`

> [!NOTE]
> **Status**: stub. Full body lands in plan #13 **part 3** (`check-repair`). See the [check-repair part](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/diataxis-author/parts/check-repair.md) for the locked design.

Interactive fix-application for drift detected by `/diataxis check`. Per finding: present suggested fix (cross-ref rewrite / mode reclassification / template realignment / split-mode-mixed-into-N-pages) + operator approves / edits / rejects. Pattern matches `/memory watchlist review`'s interactive flow. Mode-mixed splits dispatch `documenter` sub-agent (the mechanical-write worker). All file modifications preview-first; never silent.

**Planned invocation shape** (subject to refinement in plan #13 part 3):

```
/diataxis repair [--mode <m>] [--limit N] [--stub]
```

### `/diataxis migrate`

One-shot migration of legacy audience-based wikis (`development/` + `operational/` + `design/` + `architecture/`) to the four-mode Diátaxis layout. Subsumes the harness's predecessor [`migrate-to-diataxis`](https://github.com/alexherrero/agentm/blob/main/harness/skills/migrate-to-diataxis.md) skill — same contract: preview-first, deterministic classification by heading shape per ADR 0004, `git mv` for blame preservation, mode-mixed pages flagged for human split (delegates to `/diataxis repair` for the actual split work). Auto-seeds `wiki/.diataxis` marker + `wiki/.diataxis-conventions.md` (per-repo overrides) post-migration. **Never commits** — operator stages + commits manually after reviewing diff (single-commit safety net via operator-driven commit boundary).

#### Invocation shape

```
/diataxis migrate [--wiki-root <path>] [--preview | --execute | --yes] [--skip-precheck]
```

| Arg | Required | Default | Meaning |
|---|---|---|---|
| `--wiki-root <path>` | no | `./wiki` | Wiki root directory. |
| `--preview` | no | default behavior (no flag = preview + hint) | Emit preview only; never touch filesystem. Exit 0. |
| `--execute` | no | off | Apply migration. |
| `--yes` | no | off | Synonym for `--execute` (matches predecessor convention). |
| `--skip-precheck` | no | off | Skip clean-tree + already-migrated preconditions. **Testing-fixture only**; operators should never use this. |

#### Preconditions (matches predecessor)

Before any filesystem change, all of these must be true:

1. **Clean working tree** — `git status --porcelain` must be empty. Abort with clear error otherwise.
2. **`wiki/` directory exists** at the repo root.
3. **`wiki/.diataxis` marker does NOT exist** — if present, migration already ran; abort with "already migrated".
4. **At least one legacy mode-dir exists** — one of `development/`, `operational/`, `design/`, `architecture/` must be present. If none, this is already a non-audience layout; abort with "nothing to migrate".

#### Classification rules

Applied in order; first match wins:

| Rule | Pattern | Target |
|---|---|---|
| **ADR** | H1 matches `# ADR \d{4}:` OR path under `decisions/` with `NNNN-*.md` | `explanation/decisions/<basename>` |
| **Status page** | NOTE block in first 25 lines with `**Status:**` + `**Plan:**` | `explanation/<basename>` |
| **Tutorial** | `## Step N —` heading + `## What you learned` + `## Next` | `tutorials/<basename>` (plural per Diátaxis convention) |
| **How-to** | `## Steps` H2 OR ≥3 numbered imperative steps in first 40 lines, AND no `## Rationale\|Why\|Background\|Context` | `how-to/<basename>` |
| **Reference** | `## ⚡ Quick Reference` or `## Quick Reference` in first 20 lines OR ≥60% table lines | `reference/<basename>` |
| **Mode-mixed (flag)** | ≥2 of {how-to, reference, explanation} fire with competing strength | **Flag for human split** — page stays at old path; preview emits `NEEDS HUMAN SPLIT`. Operator runs `/diataxis repair` after migration for the split. |
| **Explanation (default)** | Anything else | `explanation/<basename>` |

Classification is deterministic (no LLM, no random sampling, no wall-clock).

#### Step-by-step flow

**Step 1 — Precondition check** (skipped under `--skip-precheck`). Abort with clear error on any failure.

**Step 2 — Walk wiki/** for all `.md` files, excluding structural pages (`Home.md`, `_Sidebar.md`, `README.md`).

**Step 3 — Classify each page** by applying the rules above; collect mode-mixed pages for human-split flagging.

**Step 4 — Compute new paths** preserving basename. ADRs stay under `explanation/decisions/`. Mode-mixed pages get `new_path = None` (no move).

**Step 5 — Emit preview** to stdout. `MOVES` + `NEEDS HUMAN SPLIT` + `POST-MIGRATION` sections.

**Step 6 — Apply (if `--execute` or `--yes`)**:

1. `git mv` each page from old to new path. Using `git mv` is what lets git detect the rename so `git log --follow` preserves blame.
2. Create `wiki/.diataxis` marker (empty file).
3. Auto-seed `wiki/.diataxis-conventions.md` with detected conventions.
4. **Do not commit.** Operator stages + commits manually after reviewing diff.

**Step 7 — Final summary** with per-stat counts + NEXT-steps for operator (git status review + blame spot-check + manual split via `/diataxis repair` + check-wiki strict run + commit).

#### Examples

```bash
# Default: preview only (no flag) + hint
python3 ~/Antigravity/crickets/skills/diataxis-author/scripts/migrate.py
# Explicit preview-only (preview-first contract)
python3 ~/Antigravity/crickets/skills/diataxis-author/scripts/migrate.py --preview
# Apply the migration
python3 ~/Antigravity/crickets/skills/diataxis-author/scripts/migrate.py --execute
# Skip prompts (synonym)
python3 ~/Antigravity/crickets/skills/diataxis-author/scripts/migrate.py --yes
```

#### Failure modes (graceful)

- **Dirty working tree** → exit 1 with "git status --porcelain non-empty; commit or stash first".
- **Already migrated** (`.diataxis` exists) → exit 1 with "already migrated; remove marker to re-run".
- **No legacy dirs** → exit 1 with "nothing to migrate; use /diataxis author for fresh projects".
- **Missing wiki root** → exit 1 with operator next-step.
- **`git mv` fails** (file moved already, permission denied) → error logged per-file; migration continues with other files; final summary reports `errors: N`; exit 2 if any errors.

#### Anti-patterns

- **Don't run `--skip-precheck` outside CI smoke tests.** It bypasses critical safety checks (clean tree + already-migrated guard); production use without these has caused real losses during the predecessor's dogfood era.
- **Don't `git commit` immediately after migration.** Review the diff first; spot-check `git log --follow` on a couple of moved pages to verify blame preserved; only then commit. The single-commit safety net (entire migration in one commit) only works if you actually examine the migration before committing.
- **Don't hand-edit `.diataxis-conventions.md` immediately**. Let the auto-seeded version run for a session or two so you can spot which conventions actually need overriding vs. which the defaults handle well. Premature editing here defeats the convention-evolution learning loop.

> [!NOTE]
> **Predecessor relationship**: harness's [`migrate-to-diataxis.md`](https://github.com/alexherrero/agentm/blob/main/harness/skills/migrate-to-diataxis.md) is being deprecated alongside this part's ship (plan #13 part 4). Predecessor stays in the harness through v1 dogfood window (operators with mid-flight installs can keep using it if needed); follow-up harness PATCH release removes the predecessor file entirely once `/diataxis migrate` proves out.

### `/diataxis classify <file>`

Single-page mode classification — operator-debug surface + the `diataxis-evaluator` sub-agent's primary invocation surface for ambiguous cases. Takes a file path; returns mode + confidence + per-mode scores + rationale + `needs_subagent` flag. Tier-1 (deterministic Python heuristic in `classify.py`) handles clear cases via regex + heading-shape rules from ADR 0004. Tier-2 (`diataxis-evaluator` sub-agent — operational from this part) handles ambiguous mode-mixed pages where heuristic scoring is tight (default confidence threshold 0.7).

#### Invocation shape

```
/diataxis classify <file> [--threshold N] [--no-subagent] [--stub]
```

| Arg | Required | Default | Meaning |
|---|---|---|---|
| `<file>` | yes | — | Path to the wiki page to classify. |
| `--threshold N` | no | `0.7` | Confidence threshold below which Tier-1 emits `needs_subagent: true`. |
| `--no-subagent` | no | off | Never set `needs_subagent: true`; return Tier-1 result even if ambiguous. Operator-debug + scripting. |
| `--stub` | no | off | When sub-agent dispatch would fire, emit `needs_subagent: true` marker without actually invoking sub-agent. Used by CI smoke tests to avoid live LLM calls. |

#### Step-by-step flow

**Step 1 — Read file.** Halt with `"ERROR: page not found"` if missing.

**Step 2 — Strip frontmatter.** If file starts with `---`, parse YAML frontmatter (best-effort tolerant); else treat whole file as body.

**Step 3 — Score 4 modes** via the heuristic engine in `classify.py`:

- **Tutorial**: `## Step N — ...` heading + `## What you learned` + `## Next` → 0.95; partial signals 0.5-0.75.
- **How-to**: `## Steps` section → 0.85; ≥3 numbered imperative steps in first 40 lines → 0.65; penalty 50% if `## Rationale|Why|Background|Context` sections detected (mode-mixed signal).
- **Reference**: `## ⚡ Quick Reference` heading near top → 0.8; ≥60% table lines → 0.9.
- **Explanation**: default mode; `1.0 - max(other 3)`; bumped to 0.85 if ADR-shape (`> [!NOTE]` block with `Status:` line) detected.

**Step 4 — Pick winning mode** (highest score). Mode-mixed flag = true if ≥1 other mode within 0.2 of winner AND above 0.5.

**Step 5 — Decide on sub-agent dispatch.** `needs_subagent: true` when (a) winning score < `--threshold`, OR (b) `mode_mixed: true`. Default threshold 0.7.

**Step 6 — Return classification.** JSON: `{mode, confidence, rationale, mode_mixed, needs_subagent, scores: {tutorial, how-to, reference, explanation}}` + (when caller dispatches) `suggested_split: [{mode, body_section_ranges}]`.

#### Sub-agent dispatch (Tier 2)

When `needs_subagent: true`, the caller (typically `/diataxis author --intent` or future `/diataxis check` / `/diataxis repair` from part 3) dispatches the `diataxis-evaluator` sub-agent with the caller-supplies-inline-rubric pattern documented in [`agents/diataxis-evaluator.md`](../../agents/diataxis-evaluator.md). Sub-agent has **zero filesystem write scope** (allowlist Read/Glob/Grep/WebFetch only) — returns classification decision; caller acts on it.

The CLI itself doesn't dispatch sub-agents directly — it returns the marker + lets the calling skill body handle dispatch. Same pattern as `adapt_skills.py` from plan #7b task 4.

#### Examples

```bash
# Clear tutorial page → mode: tutorial, confidence: 0.95
python3 ~/Antigravity/crickets/skills/diataxis-author/scripts/classify.py \
  wiki/tutorials/01-Getting-Started.md

# Ambiguous mode-mixed page → needs_subagent: true
python3 ~/Antigravity/crickets/skills/diataxis-author/scripts/classify.py \
  wiki/some/ambiguous.md
# → {mode: "explanation", confidence: 0.575, mode_mixed: true, needs_subagent: true, ...}

# Force Tier-1-only via --no-subagent (operator-debug)
python3 ~/Antigravity/crickets/skills/diataxis-author/scripts/classify.py \
  wiki/some/ambiguous.md --no-subagent
# → {needs_subagent: false, ...} (Tier-1 verdict regardless of confidence)

# CI smoke-safe: --stub avoids actual sub-agent dispatch
python3 ~/Antigravity/crickets/skills/diataxis-author/scripts/classify.py \
  wiki/some/ambiguous.md --stub
# → {needs_subagent: true, dispatched_subagent: false, ...}
```

#### Failure modes (graceful)

- **File not found** → exit 1 with the actual path checked.
- **File empty** → emit `{mode: "explanation", confidence: 0.3, rationale: "empty page; default to explanation"}` (stub-style page; reasonable default).
- **Frontmatter malformed** → ignore frontmatter; classify body only.
- **All 4 modes score 0** → emit `explanation` with confidence based on `1.0 - max(others) = 1.0` (perfect default — no positive signal for any other mode).

#### Anti-patterns

- **Don't use `--no-subagent` in automation.** The `needs_subagent` flag is the signal to escalate; bypassing it loses information. `--no-subagent` is operator-debug only.
- **Don't tune `--threshold` per-invocation.** The default 0.7 is tuned to v1 fixtures; operator-level threshold should be set globally via AgentMemory always-load entry (part 5 wires this).
- **Don't dispatch the sub-agent without passing classify.py's Tier-1 output as rubric context.** The sub-agent's caller-supplies-inline-rubric contract expects the heuristic verdict + per-mode scores so it can validate or override. Bare dispatch wastes the deterministic signal.

## Tool allowlist

**`Read, Write, Edit, Glob, Grep, Bash`** — `Bash` is required for the `check-wiki.py` subprocess invocation in `/diataxis check` (part 3) + `git mv` invocations in `/diataxis migrate` (part 4). Python scripts under `skills/diataxis-author/scripts/` (added in parts 2-5) handle the deterministic heavy lifting (mode classification heuristics, link rewriting, template loading). Sub-agent dispatch happens via the agent's standard task delegation; the skill body itself doesn't shell out to other agents.

Python-side scripts can use whatever they need (network for ADR 0004 cross-reference via WebFetch if any; subprocess for git + check-wiki.py; filesystem for everything else) — the allowlist restriction is on the SKILL.md body itself, not the dispatched scripts.

## Host scope

`supported_hosts: [claude-code, antigravity]` — `gemini-cli` excluded per [ROADMAP item #15](https://github.com/alexherrero/agentm/blob/main/.harness/ROADMAP.md) (Gemini-CLI host removal, shipped in toolkit v0.9.0 / ADR 0006). Same scope as the sibling `memory` skill.

## Cross-references

- **Parent design**: [diataxis-author](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/diataxis-author.md) — the canonical "Why we built this" entry point per the locked design call from plan #6.
- **Diátaxis spec source**: [agentm ADR 0004 — Diátaxis Documentation Spec](https://github.com/alexherrero/agentm/blob/main/wiki/explanation/decisions/0004-diataxis-documentation-spec.md) — the canonical convention this skill enforces.
- **Predecessor (being subsumed)**: [agentm `migrate-to-diataxis` skill](https://github.com/alexherrero/agentm/blob/main/harness/skills/migrate-to-diataxis.md) — one-shot migration skill that `/diataxis migrate` ports + extends. Ships deprecation notice in plan #13 part 4.
- **Sibling sub-agent**: [`diataxis-evaluator`](https://github.com/alexherrero/crickets/blob/main/agents/diataxis-evaluator.md) — read-only sub-agent for ambiguous mode classification. Dispatched from `/diataxis classify` (operational from part 2) + `/diataxis repair` mode-mixed splits (operational from part 3).
- **Sibling skill (orchestration pattern)**: [`memory`](https://github.com/alexherrero/crickets/blob/main/skills/memory/SKILL.md) — `/memory adapt-skills` + `adapt-evaluator` is the orchestration-skill + worker-sub-agent pattern this skill mirrors.
- **External worker**: [`documenter` sub-agent (harness-side)](https://github.com/alexherrero/agentm/blob/main/harness/agents/documenter.md) — Diátaxis-aware mechanical-write worker. Repurposed: dispatched from `/diataxis repair` mode-mixed splits (part 3) + existing harness `/release` direct dispatch (part 5 transitions via skill-presence check).
- **Validator complement**: [`scripts/check-wiki.py`](https://github.com/alexherrero/agentm/blob/main/scripts/check-wiki.py) — strict-mode validator the skill wraps for `/diataxis check`.

## Status

This skill is **stub-shipped** as of v0.11.0-pre (plan #13 part 1). All 5 sub-commands have documented shape + planned invocation but no functional implementation yet. The 5 sub-commands fill in across plan #13 parts 2-5:

- **Part 2** (`author-classify`): `/diataxis author` + `/diataxis classify` + `diataxis-evaluator` operational flow + 4 templates.
- **Part 3** (`check-repair`): `/diataxis check` + `/diataxis repair` + `documenter` dispatch as worker.
- **Part 4** (`migrate-subsume`): `/diataxis migrate` + harness predecessor deprecation notice.
- **Part 5** (`agentmemory-docs-release`): AgentMemory read + write integration + new how-to + new ADR 0008 + paired release v0.11.0 + v2.4.3 + plan close-out.

Re-audit triggers (per design doc Tech Debt + Risks): mode-classification false-positive rate (parent §1); convention drift across operator's three Diátaxis wikis (parent §2); `documenter` dispatch transition correctness (parent §3); AgentMemory write-back fatigue (parent §4).
