---
name: design
description: Human-facing design pipeline that walks the user through a locked 10-section design-doc template, gates on human approval (Status draft → review → final), then translates the approved design into structural parts and generates a PLAN.md per part for the harness's `/work` + `/review` flow to execute. Published designs surface in `wiki/Home.md` as the canonical "Why we built X" entry point.
kind: skill
supported_hosts: [claude-code, antigravity]
version: 0.1.0
install_scope: project
---

# design — human-facing design pipeline → agent execution handoff

A skill that runs the **front** of a project: the design phase a human cares about. Walks the user through a precise 10-section design-doc template, gates on human approval, then hands the approved design off to the harness's `/work` + `/review` flow as one or more `PLAN.md` files (one per structural part).

**Position vs. the harness's `/plan`:** `/plan` expects a brief and emits tasks — good for "I know what I want, give me a task breakdown." This skill starts *earlier*: it walks you through assembling a real design doc in a specific structured shape, then uses that same shape to drive the breakdown. Reach for `/design` when the problem is ambiguous, has multiple stakeholders, or needs Quality Attributes / Operations thinking before code starts. Reach for `/plan` when the design is already settled.

## Design artifacts live with the design (mockups, handoffs, validators)

A design doc is rarely the only artifact. Visual mockups (HTML/images), design handoff docs, prototype validators, and reference screenshots — **including ones produced by an external visual pass (Antigravity / Gemini), which write to scratch dirs by default** — must be saved **alongside the design doc** under `.harness/designs/` the moment they exist (e.g. mockups in `.harness/designs/mockups/`, with a `designs/README.md` naming the **canonical** mockup + any intentional divergences). Never leave them in a scratch dir.

Then **check the built UI against the canonical mockup** at design/build time and again at `/release`: the deterministic build gate verifies structure + contrast, **not** visual fidelity, so a human-or-screenshot comparison against the mock is a separate, required check. *(Why this is a rule: on one project the external mockups + handoffs sat in a scratch dir, were never saved, and the build silently drifted from the mock — a hero panel ended up stacked under the title instead of beside it — until the operator eyeballed the live site days later.)*

## Sub-commands

The skill exposes three sub-commands, dispatched by the host's slash-command surface:

| Sub-command | Inputs | Outputs | Status gate |
|---|---|---|---|
| `/design author` | Slug + Visibility | Design doc at `wiki/explanation/designs/<slug>.md` (published) or `.harness/designs/<slug>.md` (confidential) | Drives `draft → review → final` |
| `/design translate` | A `Status: final` design doc | Structural-part files at `<doc-dir>/parts/<part-slug>.md` | Requires `Status: final` (hard gate) |
| `/design sequence` | A populated `<doc-dir>/parts/` directory | One `PLAN.md` per part (first activated; rest queued) | Requires `Status: final` |

**Status `launched`** is set by the harness `/release` flow when the last part's PLAN.md completes — the design skill does not transition to `launched` directly.

### `/design author`

Walks a human through assembling a design doc in the 10-section template. Two modes:

- **Authoring** (new doc or `Status: draft`): bootstrap a new file or resume an in-progress draft. Section-by-section interactive flow. Saves after each section so partial drafts survive session interruption.
- **Review pass** (`Status: review`): walk an author-marked-ready doc and approve / revise / skip per section. Drives `review → final`.

`/design author` is the **only** sub-command that transitions Status (`draft → review → final`). It never advances past `final` — `Status: launched` is set by the harness `/release` flow when the last queued part's `PLAN.md` completes.

#### Inputs

The skill accepts (in any order):

- **`<slug>`** — short identifier used in the filename (e.g. `context-vault`, `learn-skill`, `kill-switch-v2`). Required for new docs; inferred from the path for resume.
- **`--visibility {confidential|published}`** *(optional, defaults to `confidential` on new docs)* — controls the output path:
  - `confidential` → `.harness/designs/<slug>.md` (gitignored, machine-local)
  - `published` → `wiki/explanation/designs/<slug>.md` (committed; surfaces in `wiki/Home.md` + `_Sidebar.md` as the canonical "Why we built X" entry point per the locked design call)
- **`--resume <path>`** *(optional)* — explicitly resume an existing doc at the given path. Without this flag, the skill checks both `.harness/designs/<slug>.md` and `wiki/explanation/designs/<slug>.md`; if exactly one exists, resumes that; if both exist, asks the human which.

#### Step-by-step flow

**Step 1 — Bootstrap (new doc only).** If the target path doesn't exist:

1. Confirm the title with the human (default: derive from slug → title case).
2. Copy the template at `crickets/skills/design/templates/design-doc.md` (or the equivalent project-local path if the skill is installed) to the target path.
3. Prefill frontmatter:
   - `title` ← confirmed title
   - `status: draft`
   - `visibility` ← caller's choice
   - `author` ← read from `.git/config` via `Read` tool, parsing the `[user]` block's `name = ...` line. If `.git/config` unreadable or `name` absent, prompt the human for their name.
   - `contributors: []`
   - `created` ← today's UTC date in `YYYY-MM-DD`
   - `updated` ← same as `created` initially
   - `last_major_revision` ← same as `created` initially
   - `prd` / `project` left empty unless the human provides on bootstrap
4. Seed the Document History table with the initial-draft row: `| <today> | Initial draft created via /design author. | draft |`
5. Confirm bootstrap to the human with the path + ready-to-walk message.

If the target path already exists, skip step 1 and proceed to walk-sections; the doc's existing Status determines the mode (Authoring vs. Review pass).

**Step 2 — Walk sections.** For each top-level section in template order (Context → Design → Alternatives Considered → Dependencies → Migrations → Technical Debt & Risks → Quality Attributes → Project management → Operations):

1. Read the current state of the section from the file via `Read`.
2. If the section is empty (only contains the italic prompt + HTML comments): present the prompt to the human, ask `"What goes in this section?"`, and accept multi-line response (paste or typed).
3. If the section already has content (resume case): present the existing content, ask `"Keep / Edit / Replace?"`.
4. Write the human's response into the section via `Edit`, replacing the italic prompt + HTML comments with the actual content. The italic prompt + comments are author-facing scaffolding; once filled, they don't render in the final doc.
5. Optionally read the section back via `Read` and confirm to the human.
6. Update the `updated` frontmatter field after each section save.

Sub-sections inside Design (Overview / Infrastructure / Detailed Design) and Project management (Work estimates / Documentation Plan / Launch Plans) and Operations (SLAs / Monitoring and Alerting / Logging Plan / Rollback Strategy) get prompted as individual children of their parent section, in template order.

**Step 3 — Quality Attributes drill-down.** Quality Attributes is the section with the strictest discipline. Walk each of the 11 sub-attrs in template order: Security → Reliability → Data Integrity → Privacy → Scalability → Latency → Abuse → Accessibility → Testability → Internationalization & Localization → Compliance.

For each sub-attr, prompt:

> `"<attr>: Does this design have <attr> concerns? Describe them, or 'N/A' with one sentence on why."`

Accept responses in two shapes:
- **Substantive content**: write into the sub-section as-is.
- **N/A response**: must take the form `"N/A: <one-sentence rationale>"`. If the human just says `"N/A"` without rationale, push back: `"N/A needs a one-sentence reason. Why doesn't this design have <attr> concerns?"`

The discipline is non-negotiable per the locked design call — forcing conscious N/A catches ops blind spots early. If the human resists giving an N/A rationale across multiple attrs, the skill flags it: `"Most designs have at least one concern in <Reliability | Security | Testability>; want to revisit?"`

**Step 4 — Alternatives Considered.** Single-section prompt:

> `"What other approaches did you consider? Why did you reject each?"`

Default behavior pushes for at least one alternative. Only accept `"N/A: only one viable approach (<one-sentence justify>)"` if the human explicitly confirms after one push-back. Most designs have an alternative worth recording.

**Step 5 — Save + Status lifecycle.** After all sections are filled:

1. Present a summary of the doc to the human (sections filled, sub-attrs marked N/A vs. described, total length).
2. Ask the human to pick one:
   - `"Ready for review (Status → review)"` — inline approval; locks the doc as `Status: review` for a future per-section review pass.
   - `"Keep drafting"` — stay in draft; return to walk-sections for any section worth revisiting.
   - `"Stop and resume later"` — save current state; `/design author --resume <path>` picks up where you left off.
   - **`"Hand off for external review"`** — NEW in v0.8.1. Generate a transfer-context file + handoff prompt for an external editor (Antigravity-Gemini workflow). Doc stays `Status: draft`; resume via `/design author <slug> --resume-external-review` after the external pass completes. See `#### External-review handoff` section below for the full flow.
3. On `Ready for review`: transition `status: draft → review`. Append to Document History: `| <date> | Author signaled ready for review. | review |`. Tell the human the doc is now in review and the next `/design author <slug>` invocation will run the review-pass flow.
4. On `Keep drafting`: stay in draft; return to walk-sections.
5. On `Stop and resume later`: save current state + reminder.
6. On `Hand off for external review`: invoke the external-review-handoff flow (see section below).

**Step 6 — Review pass support.** If `/design author` is invoked on a `Status: review` doc:

1. Announce the review-pass mode: `"This doc is Status: review. Walking sections for approve / revise / skip — or hand off for external review."`.
2. Ask the human to pick one (BEFORE walking sections):
   - **Inline review** (default) — proceed with the section-by-section approve/revise/skip walk per the original flow.
   - **`"Hand off for external review"`** — NEW in v0.8.1. Generate transfer-context + handoff prompt for the whole doc (rather than per-section). Doc stays `Status: review`; resume via `/design author <slug> --resume-external-review` after the external pass.
3. **Inline review flow** (if picked): for each section (Context → Design → ... → Operations) and each sub-attr inside Quality Attributes: present the current content and prompt `"Approve / Revise / Skip?"`.
   - `Approve`: section passes review unchanged. Track the approval count.
   - `Revise`: open the section for edits via the same walk-sections flow as Step 2 (accept new content, replace).
   - `Skip`: move to next section without judgment. Track skip count.
4. **External review flow** (if picked): invoke the external-review-handoff flow (see section below). On resume, present the diff + change-summary log instead of walking per-section; ask the human whether the externally-revised doc is ready for final approval.
5. After review (either flow): present a summary. If any section was Skipped or Revised, ask: `"Some sections still need attention. Continue review pass? Or transition to final anyway?"`.
6. On approval to finalize: ask explicit `"Approve as final? This locks the doc and unblocks /design translate."`. On `yes`: transition `status: review → final`. Append to Document History: `| <date> | Approved as final via /design author review pass. | final |`. Update `last_major_revision` to today.

After `Status: final` is set, `/design author` refuses further invocations on the same doc — finalized docs are immutable until a `/design translate` run promotes them into structural parts (or a human manually edits the file + reverts Status to `review`, which is an escape hatch documented in the troubleshooting section of the how-to).

#### External-review handoff (alternative to inline block-by-block review — added in v0.8.1)

The block-by-block approve/revise/skip flow works for short docs but gets tedious on long ones (~7000+ word designs take ~30 min to walk inline). Operators who prefer Antigravity's native inline-comment UI + Gemini-applies-comments pattern can hand off to that workflow.

**When the option is offered**: at three skill points:

- `/design author` Step 5 — alternative to "Ready for review" inline transition.
- `/design author` Step 6 — alternative to per-section approve/revise/skip walk.
- `/design translate` Step 4 — alternative to inline reshape commands (see `/design translate` body below).

**What the skill does on handoff**:

1. **Write pre-handoff snapshot** at `<target-doc>.pre-handoff-<YYYYMMDDhhmmss>.md` — full copy of the doc as it stood when the operator picked "Hand off". Used by Claude on resume to diff against the externally-revised version. Cleaned up after resume completes.
2. **Generate transfer-context file** at `<project>/.harness/transfer/<doc-slug>-<YYYYMMDDhhmmss>.md` from the template at `crickets/skills/design/templates/transfer-context.md`. Fill placeholders:
   - `DOC_TITLE`, `DOC_TYPE` (= `design-doc`), absolute path to target doc, snapshot path, lock state.
   - **`OPERATOR_INTENT_PARAGRAPH`** — 1-2 paragraphs the skill generates from the active design's Objective + parent context (what this doc is trying to achieve, where it sits in the larger plan/roadmap, what success looks like after the revision pass).
   - **`RECENT_DECISIONS_BULLETS`** — extracted from the target doc's Document History (most recent 3-5 rows) + parent design's "Locked design calls" section if applicable + recent ADRs touching this scope.
   - **`INLINED-CONVENTIONS-dev-flow`** — static expansion from a known list (paragraph-long Status:[x] narratives / ✅⬜ charts / link blocks / locked design calls section / CHANGELOG + ADR shapes / coordinated cross-repo release order / wake-on-CI / NEVER append Co-Authored-By trailer / etc.).
   - **`INLINED-GUARDRAILS-FOR-design-doc`** — static expansion from the design-doc-specific guardrails list (10-section template lock; 11 QA sub-attrs N/A-with-rationale discipline; Status lifecycle; Visibility routing; Document History append-only).
3. **Output handoff prompt** for the operator to take to Antigravity:

   ```
   External-review handoff ready. Take to Antigravity:

     1. Open the target doc: <absolute path to design doc>
     2. Open the transfer context: <absolute path to .harness/transfer/<slug>-<ts>.md>
     3. Add inline comments using Antigravity's native comment UI wherever
        you want changes. The transfer context tells Gemini what conventions
        to honor + what's locked.
     4. When done commenting, ask Gemini: "apply my comments per the
        transfer context". Gemini revises the doc + writes a change-summary
        log at <target-doc>.diff.md.
     5. Return to Claude Code and say "review complete on <slug>" (or run
        `/design author <slug> --resume-external-review`). Claude will diff
        against the pre-handoff snapshot and surface findings.

   Pre-handoff snapshot saved at <snapshot path> — Claude uses this on
   resume to detect what changed.
   ```

4. **Pause the skill** — return control to the operator. The doc's Status is unchanged (still `draft` if invoked at Step 5; still `review` if invoked at Step 6). Resume happens on the operator's next invocation.

**Resume flow** (`/design author <slug> --resume-external-review` or natural language "review complete on <slug>"):

1. Verify expected files exist: revised target doc + `<target-doc>.diff.md` change-summary log + pre-handoff snapshot. If any missing, refuse with a clear error pointing the operator to re-do or cancel the handoff.
2. **Diff revised doc against pre-handoff snapshot** — use `Read` on both + present a unified diff to the operator. Highlight: applied changes; any modifications to "Recent decisions to honor"; any modifications to frontmatter (which should be flagged in the change-summary log if expected).
3. **Read the change-summary log** — surface Gemini's per-comment narrative + the "Suggestions" section (adjacent issues Gemini noticed but didn't apply).
4. **Ask the operator**: `"Accept all changes / iterate further (another external pass) / discard the handoff entirely?"`.
   - **Accept**: clean up snapshot + transfer-context files (move to `.harness/transfer/_archive/` for audit trail; can be GC'd at 30 days). Continue the original flow (Step 5 transition to `review` if invoked at Step 5; final-approval prompt if invoked at Step 6).
   - **Iterate**: regenerate transfer-context file with updated "Recent decisions to honor" (including what was applied in the previous round); re-run handoff.
   - **Discard**: restore from pre-handoff snapshot; doc returns to its pre-handoff state; archive the failed handoff for audit; continue with inline flow.

**Transfer artifacts cleanup**: on successful resume + acceptance, move snapshot + transfer-context + diff-log to `.harness/transfer/_archive/` with the doc-slug + timestamp preserved in the filenames. Archive entries get GC'd at 30 days by the same idle-hook pass that cleans crash-recovery markers (post-MemoryVault). Until that lands, manual cleanup is operator-side.

**Why this design**: leans on Antigravity-native primitives (inline-comment UI + Gemini-applies pattern) rather than inventing a comment marker we'd parse. The transfer-context file is the load-bearing handoff artifact — it gives Gemini everything it needs without requiring access to the Claude Code conversation. Pre-handoff snapshot + diff-on-resume is the safety net against silent drift (Gemini may revise things the operator didn't comment on; the diff surfaces them).

#### Status transition summary

| Transition | Trigger | Set by |
|---|---|---|
| (none) → `draft` | Bootstrap on new doc | `/design author` (Step 1) |
| `draft → review` | Human signals "ready for review" | `/design author` (Step 5) |
| `review → final` | Human explicitly approves the reviewed doc | `/design author` (Step 6) |
| `final → launched` | Last queued part's PLAN.md completes | Harness `/release` (task 5 of plan #6) |
| `review → draft` (rare) | Human manually edits Status field after a stalled review | Manual — escape hatch |

The skill never transitions backwards. Going `review → draft` is operator-driven.

#### Tool allowlist

**`Read, Write, Edit, Glob, Grep` only.** No `Bash`. The `git config user.name` read in Step 1 is via `Read` on `.git/config` + string parsing — not via `git config` shell invocation.

If `Read` on `.git/config` fails (file unreadable, no `[user]` block, missing `name` key), the skill prompts the human for their name as a fallback. Never silently leaves the `author:` field blank.

#### Hard gates

`/design author` is the **only** sub-command that transitions `Status`. `/design translate` and `/design sequence` will refuse to run on non-`final` docs with a clear error message pointing the user back to `/design author` to complete the review pass.

#### Worked example

Walking a 3-section synthetic design end-to-end ("add a `/foo` command").

**Invocation:** `/design author foo-command --visibility published`

**Step 1 (bootstrap):**

> Agent: "Confirming title — default is 'Foo Command' from the slug. Use that, or different?"
> Human: "Use that."
> Agent: *(copies template, prefills frontmatter, writes file at `wiki/explanation/designs/foo-command.md` with Status: draft + Document History initial row)*
> Agent: "Bootstrap done. Doc at `wiki/explanation/designs/foo-command.md`, Status: draft. Ready to walk sections — starting with Context/Objective."

**Step 2 (walk: Context/Objective):**

> Agent: "Context/Objective. *What problem does this solve, and why now?*"
> Human: "Users want a quick way to invoke the `foo` action without going through the menu. Now because the feature shipped underspecified and people are confused."
> Agent: *(Edits the file; replaces italic prompt + HTML comments in the Objective sub-section with the human's response)*
> Agent: "Got it. Next: Context/Background."

**Step 2 (walk: Context/Background):**

> Agent: "Context/Background. *History, related work, current state.*"
> Human: "We shipped `foo` in v2.1.0 via the menu only. ADR 0023 chose menu-first as a UX call; this design revisits that for keyboard-driven users."
> Agent: *(writes; presents next prompt)*

**Step 2 (walk: Design/Overview):**

> Agent: "Design/Overview. *The shape of the design in 1-3 paragraphs.*"
> Human: "A new slash command `/foo` that triggers the same action as the menu item. Lives in commands/. Reuses the existing foo-handler with no logic changes — pure plumbing."
> Agent: *(writes; continues to Infrastructure → Detailed Design → Alternatives → Dependencies → ... walking each in order)*

**Step 3 (Quality Attributes drill-down — example for Security):**

> Agent: "Security: *Does this design have Security concerns? Describe them, or 'N/A' with one sentence on why.*"
> Human: "N/A"
> Agent: "N/A needs a one-sentence reason. Why doesn't this design have Security concerns?"
> Human: "N/A: pure UI plumbing; reuses existing handler with no new permissions, no new data paths, no new attack surface."
> Agent: *(writes "N/A: pure UI plumbing; ..." into the Security sub-section)*
> Agent: "Reliability: *Does this design have Reliability concerns? ...*"

*(continues through all 11 sub-attrs)*

**Step 5 (save + lifecycle):**

> Agent: "All sections filled. Summary: 10 sections / 7 sub-attrs described + 4 marked N/A with rationale / 1 alternative captured. Ready for review? (Status → review) Or keep drafting?"
> Human: "Ready for review."
> Agent: *(transitions Status: draft → review, appends to Document History, updates `updated` field)*
> Agent: "Doc transitioned to Status: review. Next invocation of `/design author foo-command` runs the review pass. When you're ready to approve, the pass concludes with `/design translate` becoming available."

**Later — review pass (Step 6):**

> *(human invokes `/design author foo-command` later)*
> Agent: "This doc is Status: review. Walking sections for approve / revise / skip. — Context/Objective: <content>. Approve / Revise / Skip?"
> Human: "Approve."
> Agent: "Context/Background: <content>. Approve / Revise / Skip?"
> *(continues through all sections)*
> Agent: "Review pass complete: 10 sections approved, 1 revised, 0 skipped. Approve as final? This locks the doc and unblocks `/design translate`."
> Human: "Approve as final."
> Agent: *(transitions Status: review → final, appends Document History, updates last_major_revision)*
> Agent: "Doc is Status: final. Run `/design translate foo-command` next to split into structural parts."

#### Failure modes

- **Template not found** — if `crickets/skills/design/templates/design-doc.md` is unreadable, the skill halts with a clear error and a hint to verify the toolkit install. Never falls back to a degraded inline template.
- **Existing doc with malformed frontmatter** — if resume hits a doc whose frontmatter doesn't parse as YAML, the skill halts with `"<path>: frontmatter invalid; manual fix needed before resume"`. Doesn't try to auto-repair.
- **Existing doc with unknown Status value** — Status must be one of `draft|review|final|launched`. Anything else halts with an error.
- **Session interrupted mid-section** — the doc is saved after each section, so partial state survives. Resume picks up at the first incomplete section. If the user wrote half a section and the session died mid-Edit, the half-written state may be present — the resume prompt presents what's there and asks `"Keep / Edit / Replace?"`.
- **Quality Attributes N/A loop** — if the human N/A's all 11 sub-attrs without rationale across multiple prompts, the skill surfaces: `"All 11 Quality Attributes marked N/A; this is unusual. Want to revisit, or confirm the design genuinely has no quality-attribute concerns?"`. Doesn't block, but documents the unusual choice in the Document History.

#### Anti-patterns

`/design author` must not:

- **Silently transition Status.** Every transition is announced and confirmed.
- **Skip Quality Attributes prompts.** Even on resume, any sub-attr without content is prompted.
- **Auto-publish a confidential doc.** Visibility transitions (`confidential → published`) require explicit human change of the frontmatter field + a fresh `/design author` invocation.
- **Edit Status backwards.** The skill never moves `final → review` or `review → draft`. The escape hatch is manual: human edits the Status field directly.
- **Lose work on session crash.** Saves are per-section, not batched. `commit-on-stop` (from crickets's base hooks, plan #4) provides additional safety net if the session crashes mid-Edit.

### `/design translate`

Consumes a `Status: final` design doc and produces N structural-part files at `<doc-dir>/parts/<part-slug>.md`. Each part is a focused implementation slice — one chunk small enough to ship as a single PLAN.md cycle in the next stage (`/design sequence`). The skill never changes the parent design's Status; translate is read-mostly with respect to the parent (only appends to Document History).

#### Inputs

- **`<slug-or-path>`** — design doc slug or full path. The skill resolves either `.harness/designs/<slug>.md` (confidential) or `wiki/explanation/designs/<slug>.md` (published); if both exist, asks the human which.
- **`--allow-large-design`** *(optional)* — bypasses the cap-of-6 soft warning when a design legitimately splits into more than six parts. Rarely needed; if you reach for it twice in a row, that's signal to split the design itself into multiple docs upstream.

#### Step 1 — Preconditions check

The skill **refuses to run** on non-`final` docs. Read the parent doc's frontmatter; check `status`:

- `status: final` → continue.
- `status: draft` → halt with `"<path>: Status is 'draft', not 'final'. Run /design author <slug> to complete authoring + review pass first, then re-run /design translate."`
- `status: review` → halt with `"<path>: Status is 'review', not 'final'. The review pass is incomplete. Run /design author <slug> to walk approve/revise/skip and finalize."`
- `status: launched` → halt with `"<path>: Status is 'launched'. The design's parts have already been generated + executed; translating again would orphan the existing parts/. If you need to revise, edit the parent design + manually revert status to 'final' + re-run translate; document the change in Document History."`
- any other / missing value → halt with `"<path>: Status field invalid or missing; expected 'final'."`

The refusal contract is non-negotiable per the locked design call #3 from PLAN.md. `/design translate` is the first hard gate downstream of `/design author`; bypassing it loses the human-approval signal that makes the rest of the workflow trustworthy.

Additionally check: the parent doc's `## Design / ### Detailed Design` section must have substantive content (not just the italic prompt + HTML comments from the template). If Detailed Design is empty, halt with `"<path>: Detailed Design has no content; design too sparse to translate. Author at least one Detailed Design subsection before re-running."`

#### Step 2 — Read the design

Read the full parent design file via `Read`. Parse the 10 sections; identify:

- The `## Design / ### Detailed Design` subsections (these drive the part-split heuristic in Step 3).
- The `## Dependencies` content (each part may inherit a subset).
- The `## Quality Attributes` sub-attrs (extract part-specific concerns for each part's Verification).
- The `## Operations` sub-sections (extract part-specific ops concerns).
- The `## Project management / ### Work estimates` content (informs the per-part `estimated_scope` field).

Hold this in memory for Step 3.

#### Step 3 — Propose part split

The skill proposes a split using these heuristics:

- **Default rule**: one part per top-level subsection of Detailed Design.
- **Grouping rule**: tightly-coupled subsections may be grouped into one part when:
  - The downstream subsection only makes sense given the upstream one (e.g. an Infrastructure choice that the Security model is conditional on).
  - The subsections share a single deployable unit (one schema migration + one API surface + one runbook = one part, not three).
- **Splitting rule**: a single subsection may be split into multiple parts if its scope is genuinely larger than one PLAN.md cycle can absorb.
- **Cap**: ~6 parts max. More than 6 triggers a soft warning: `"This design proposes <N> parts (>6). Consider splitting the design itself into multiple documents — that's usually a clearer story than a megadesign with too many parts. Re-run with --allow-large-design to override."` Soft warning; operator can override with the flag.

For each proposed part, populate these fields:

- **Slug** — short kebab-case identifier (e.g. `foundations`, `command-surface`, `rollout`). Used as filename.
- **Title** — h1 for the part file (e.g. "Foundations: data model + access layer").
- **Scope** — 1-2 paragraphs lifted from the source Detailed Design subsections this part covers.
- **Dependencies** — list of other part slugs this part depends on (empty list for foundational parts).
- **Verification criteria** — extracted from the parent's Quality Attributes / Operations / Documentation Plan rows that apply to this part's scope. Each criterion is one falsifiable claim.
- **Estimated scope** — `S` (one short `/work` session), `M` (multiple sessions, single PLAN.md), `L` (large; consider further-splitting).

#### Step 4 — Human review of split

Present the proposed split as a table:

```
Proposed split for <parent-slug> (Status: final):

| # | Slug | Title | Dependencies | Est. | Source sections |
|---|------|-------|--------------|------|-----------------|
| 1 | foundations | Foundations: data model + access layer | (none) | M | Detailed Design §1, §2 |
| 2 | command-surface | Command surface + status display | depends on #1 | S | Detailed Design §3 |
| 3 | rollout | Rollout: feature flag + telemetry | depends on #1, #2 | S | Detailed Design §4 + Operations §Monitoring |

Total: 3 parts. Approve / Reshape / Cancel?
```

Accept human responses:

- **`Approve`** / **`Approved split`** → continue to Step 5.
- **`Reshape`** → enter reshape sub-loop:
  - `merge <slug-a> <slug-b>` — combine two parts into one; agent proposes merged Title / Scope / Verification; human re-confirms.
  - `split <slug>` — split one part into two; agent proposes the split lines; human re-confirms.
  - `rename <old-slug> <new-slug>` — rename without changing structure.
  - `reorder <slug-list>` — change ordering (used by Step 5 for filename numbering hints; doesn't affect dependencies).
  - After each reshape op, re-present the table; loop until `Approve`.
- **`Hand off for external review`** — NEW in v0.8.1. Alternative to inline reshape commands. Generate a transfer-context file describing the proposed split + reshape options + parent design context; operator takes to Antigravity, comments on the split inline, asks Gemini to apply; revised split comes back to Claude for diff-review + Approve / Cancel decision. See `/design author`'s `#### External-review handoff` section for the mechanics — same template, same handoff prompt pattern, same resume flow. The handoff target doc for translate is a temporary `proposed-split-<slug>-<ts>.md` file at `.harness/transfer/` (the split table + per-part metadata in markdown form); operator's comments revise the split; on resume Claude reads the revised split + presents Approve/Cancel.
- **`Cancel`** → halt without writing any files. Parent doc unmodified.

The reshape loop is the human's primary lever — the agent's proposed split is a starting point, not the answer. Re-audit at retrospective (#12) if override rate exceeds 50% across real designs.

#### Step 5 — Write part files

For each approved part, write `<doc-dir>/parts/<part-slug>.md` (creating the `parts/` subdir if absent). File shape:

```yaml
---
# Inherited from parent design
title: <part-specific title>
status: draft
visibility: <inherited>
author: <inherited>
contributors: <inherited>
created: <today UTC>
updated: <today UTC>
last_major_revision: <today UTC>
prd: <inherited from parent>
project: <inherited from parent>

# New part-specific fields
parent_design: ../<parent-slug>.md
part_slug: <slug>
dependencies: [<other-part-slugs>]
estimated_scope: S|M|L
---

# <Title>

## Scope

<1-2 paragraphs lifted from parent's Detailed Design subsection(s)>

## Dependencies

<List with rationale; e.g. "depends on foundations: needs the data model + access layer to exist before the command surface can wire up.">

## Verification criteria

<Bulleted falsifiable claims extracted from parent's Quality Attributes / Operations / Documentation Plan rows that apply to this part.>

## Parent design

This part implements one slice of [<parent-title>](../<parent-slug>.md) (`Status: final`). See the parent for Context, Alternatives Considered, Quality Attributes overview, and Operations strategy. Mid-execution changes to this part's scope must be appended to the parent's Document History.
```

If a part file already exists at the target path (re-running translate on a previously-translated design), present the human with a diff + ask `"<path> already exists. Overwrite / Keep existing / Cancel?"` — never silently clobber.

#### Step 6 — Update parent doc Document History

After all part files are written, append one row to the parent's Document History table:

```
| <today UTC> | Translated to N parts via /design translate: <comma-separated part slugs>. | final |
```

Note: the parent's `Status` stays `final` — translate doesn't change Status. Only `/design author` (draft → review → final) and the harness `/release` (final → launched) do.

Also bump the parent's `updated` frontmatter field to today.

#### Tool allowlist

**`Read, Write, Edit, Glob, Grep`** — no `Bash`. Same allowlist as `/design author`. Operations:

- `Read` — parent design + frontmatter parse + check existing part files for the overwrite-prompt case.
- `Glob` — discover the `parts/` subdir state (existing files for re-run safety).
- `Write` — create new part files.
- `Edit` — append to parent's Document History table; bump `updated` field.

#### Hard gates

`/design translate` REFUSES to run on:

1. Non-`final` Status (draft, review, launched, or invalid).
2. Empty `## Design / ### Detailed Design` section (design too sparse).
3. Existing `<doc-dir>/parts/` with conflicting files when re-running, unless the human approves overwrite per file.

These refusals are the contract layer between `/design author` (which produces the parent doc) and `/design sequence` (which consumes parts/). Bypassing them loses traceability between the human-approved design and the executed plans.

#### Worked example

Synthetic 3-part design walked end-to-end.

**Input:** a `Status: final` design at `wiki/explanation/designs/foo-system.md` with three Detailed Design subsections:

1. **Data model + access layer** — schema, repository pattern, queries
2. **Command surface** — `/foo` slash command + status display widget
3. **Rollout** — feature flag wiring + telemetry events

**Invocation:** `/design translate foo-system`

**Step 1 (preconditions):**

> Agent: *(Reads frontmatter; status=final ✓; Detailed Design has 3 subsections ✓)*
> Agent: "Preconditions met. Proceeding."

**Step 2 (read design):** *(internal — reads full file, indexes sections)*

**Step 3 (propose split):** Agent proposes 3 parts using the default one-per-subsection rule.

**Step 4 (human review):**

> Agent: ```
> Proposed split for foo-system (Status: final):
>
> | # | Slug | Title | Dependencies | Est. | Source sections |
> |---|------|-------|--------------|------|-----------------|
> | 1 | foundations | Foundations: data model + access layer | (none) | M | Detailed Design §1 |
> | 2 | ui | UI: command + status display | depends on #1 | S | Detailed Design §2 |
> | 3 | rollout | Rollout: feature flag + telemetry | depends on #1, #2 | S | Detailed Design §3 + Operations §Monitoring |
>
> Total: 3 parts. Approve / Reshape / Cancel?
> ```
> Human: "Reshape: rename ui command-surface"
> Agent: *(re-presents table with `ui` → `command-surface`)*
> Human: "Approve"

**Step 5 (write part files):** Agent writes 3 files:

```
wiki/explanation/designs/foo-system/
├── foo-system.md                       (the parent — unchanged except Document History)
└── parts/
    ├── foundations.md                  (Status: draft, dependencies: [], estimated_scope: M)
    ├── command-surface.md              (Status: draft, dependencies: [foundations], estimated_scope: S)
    └── rollout.md                      (Status: draft, dependencies: [foundations, command-surface], estimated_scope: S)
```

Each part file has the inherited frontmatter + part-specific frontmatter fields + h1 Title + Scope + Dependencies + Verification criteria + pointer back to parent.

**Step 6 (update parent):**

> Agent: *(Edits parent's Document History table to append):*
> ```
> | 2026-05-15 | Translated to 3 parts via /design translate: foundations, command-surface, rollout. | final |
> ```
> Agent: "Translate complete. 3 part files at wiki/explanation/designs/foo-system/parts/. Run `/design sequence foo-system` next to generate PLAN.md per part."

#### Failure modes

- **Parent doc unreadable** — halt with explicit path + error; don't fall back to partial state.
- **Parent doc has malformed YAML frontmatter** — halt with `"<path>: frontmatter invalid; manual fix needed before translate."` Don't auto-repair.
- **Detailed Design has subsections but they're all empty** — halt with `"<path>: Detailed Design subsections exist but contain no content. Design needs more substance before parts can be derived."`
- **Human types Cancel during reshape loop** — halt cleanly; parent doc untouched; no partial part files written.
- **Re-run after manual deletion of some part files** — translate detects the gap (parts/ has some files, missing others); presents the gap; asks whether to regenerate the missing ones or treat them as intentionally-deleted (skip in the new split).
- **Re-run after parent design revision** — operator-driven flow: human edits parent + appends to Document History; re-runs `/design translate`. The skill diffs proposed split against existing parts/, presents the delta, and asks per-file: overwrite / keep / delete.

#### Anti-patterns

`/design translate` must not:

- **Silently transition parent Status.** Translate appends to Document History but never changes Status.
- **Skip the human-review step.** Even if the proposed split is "obviously correct" by the heuristic, the human gets the table + approval prompt every time. The split is a hand-off; agents don't unilaterally decide what counts as a part.
- **Generate >6 parts without the override flag.** The soft cap is the design-too-big signal; respect it.
- **Clobber existing part files silently.** Always diff + prompt on re-run.
- **Lose the parent → parts link.** Every part file carries `parent_design:` frontmatter; recovery on lost link should be possible by re-running translate against the parent.

### `/design sequence`

Consumes a populated `<doc-dir>/parts/` directory (output of `/design translate`) and generates one `PLAN.md` per part using the harness's existing `templates/PLAN.md` shape. The first part (in topological order) activates at `<project>/.harness/PLAN.md`; subsequent parts queue at `<project>/.harness/designs/<doc-slug>/queued-plans/<part-slug>.PLAN.md`. The skill produces **draft** plans — the human typically runs the harness's `/plan` against each PLAN.md to refine task decomposition before `/work` starts on a part.

`/design sequence` is the bridge between the design phase (this skill, stages 1–4) and the execution phase (harness `/work` + `/review`, stage 5). After `/design sequence` runs, the design hand-off is complete; the rest is the harness's normal flow.

#### Inputs

- **`<slug-or-path>`** — design doc slug or full path. Same resolution as `/design translate`: either `.harness/designs/<slug>.md` or `wiki/explanation/designs/<slug>.md`.
- **`--force-replace`** *(optional)* — replaces an existing `.harness/PLAN.md` even when its `Status` is `in-progress`. Rarely needed; only use when the existing active plan is known-orphaned (e.g. abandoned hand-authored plan from a different effort). Default behavior refuses with a clear error.

#### Step 1 — Preconditions check

The skill refuses to run on:

- **Non-`final` parent Status** — same gate as `/design translate`. Refuse with `"<path>: Status is <current>, not 'final'. Run /design author <slug> to finalize, then re-run /design sequence."` (Each non-final state has its own state-specific error message, mirroring translate's.)
- **Missing `parts/` directory** — refuse with `"<doc-dir>/parts/ does not exist. Run /design translate <slug> first to generate structural parts."`
- **Empty `parts/` directory** — refuse with `"<doc-dir>/parts/ contains zero part files. /design translate either failed or was Cancelled. Re-run /design translate."`
- **Invalid part file frontmatter** — any part file with malformed YAML or missing required fields (`part_slug`, `dependencies`, `estimated_scope`) refuses with the path + missing field. Don't auto-repair.
- **Existing in-progress active plan** — if `<project>/.harness/PLAN.md` exists and parses with `Status: in-progress`, refuse with `"<project>/.harness/PLAN.md is in-progress; sequencing would clobber it. Close the active plan (Status: done) first, or pass --force-replace if the plan is known-orphaned."`

Reading `.harness/PLAN.md` requires the project root to be discoverable. The skill resolves project root by walking up from cwd looking for `.harness/`; if not found, refuses with `"No .harness/ directory found in cwd or ancestors. Sequence must run from inside a harness-installed project."`

#### Step 2 — Topological sort

Read all part files in `<doc-dir>/parts/*.md`. For each, parse `dependencies:` from frontmatter. Build a directed acyclic graph where an edge `A → B` means "B depends on A".

**Sort algorithm**: Kahn's algorithm (BFS-based topological sort) with **deterministic tie-breaking by alphabetical part slug** within the same dependency level. Determinism matters: re-running sequence on the same parts/ dir must produce identical ordering — otherwise queued-plans/ order changes across re-runs and that's confusing for operators.

**Cycle detection**: if any cycle exists in the graph, the topological sort halts with `"Dependency cycle detected: <cycle path joined by ' → '>. Edit parts/ files to break the cycle and re-run."` Common cycle case: human accidentally added a `dependencies:` entry that points back to a downstream part. The skill prints the smallest cycle it finds (the first one Kahn's algorithm fails on).

**Missing-dependency detection**: if a part's `dependencies:` references a slug that doesn't exist in parts/, refuse with `"Part '<part-slug>' depends on '<missing-slug>' which does not exist in parts/. Either remove the dependency or create the missing part."`

The output of Step 2 is an **ordered list** of part slugs: first → last, with the first being topologically first (no dependencies, or alphabetically-earliest among unblocked parts).

#### Step 3 — Generate PLAN.md per part

For each part in topological order, generate a `PLAN.md` derived from the part file + parent design. Mapping from part fields to harness PLAN.md template shape:

| Harness PLAN.md section | Source in design/part |
|---|---|
| Title (`# Plan: <title>`) | `<part title> (from design <doc-slug>)` |
| `Status:` | `draft` (harness PLAN.md Status; means human refinement pending — distinct from design doc's `final` Status) |
| `Created:` | today UTC |
| `Roadmap item:` | Cross-ref to parent design doc path + parent's `project:` field if set |
| `Brief:` | Part's `## Scope` content (1-2 paragraphs) |
| `## Goal` | Derived from part's `## Verification criteria` rephrased as user-visible outcomes |
| `## Constraints` | Parent design's Quality Attribute concerns that apply to this part (extracted from QA sub-sections whose content references the part's scope) |
| `## Out of scope` | Explicit non-goals: other part slugs ("see `<other-slug>.PLAN.md` for that part") + items parent design's Out of scope mentions |
| `## Tasks` | DRAFT decomposition from part's Detailed Design source content. Each task is one `### N. <title>` with placeholder What + Verification (single-sentence). **Note in the body**: human typically runs `/plan` against this generated PLAN.md to refine the task list before `/work` starts. |
| `## Risks / open questions` | Parent's Technical Debt & Risks + part-specific concerns (extracted from part's Scope if it mentions risks) |
| `## Verification strategy` | Part's `## Verification criteria` verbatim |
| `## How to resume` | Standard harness boilerplate (read this file's task list, find next `[ ]`, then read most recent progress.md entries) |
| `## Locked design calls` | Pointer back to parent design doc + the key locked decisions extracted from parent's Alternatives Considered (one-liners) |

Additional frontmatter fields (beyond the harness PLAN.md defaults) for traceability:

```yaml
parent_design_doc: <relative path from .harness/ to design doc>
parent_part_slug: <part_slug from the part file>
```

These fields let the harness `/release` extension (task 5) match a completed PLAN.md back to its source part for plan promotion + design Status transition.

#### Step 4 — Write to harness `.harness/`

The skill writes to the **target project's** `.harness/` directory (cwd-rooted; same convention as the harness's own `/plan` skill).

- **First part** (topologically first): write to `<project>/.harness/PLAN.md`. If a non-in-progress PLAN.md already exists (Status: `done` or `draft`), overwrite. If `Status: in-progress` exists, the preconditions check in Step 1 already caught it — no clobber path here.
- **Subsequent parts** (topologically 2nd through Nth): write to `<project>/.harness/designs/<doc-slug>/queued-plans/<part-slug>.PLAN.md`. Create the `designs/<doc-slug>/queued-plans/` subdirs if absent.
- **Existing queued plans on re-run**: if `<project>/.harness/designs/<doc-slug>/queued-plans/` already has files (re-running sequence after edits), present a diff per file and ask `"<part-slug>.PLAN.md exists. Overwrite / Keep existing / Cancel?"` — never silent clobber.

**Manual-promotion fallback (until task 5 ships):** v0.8.0 of this skill ships the WRITE side only. The promotion logic — where harness `/release` moves the next queued plan to `.harness/PLAN.md` when the active plan hits `Status: done` — lands in task 5 of plan #6 (agentm v2.3.0). **Until v2.3.0 is installed alongside, operators promote manually:**

```bash
# After the active plan completes (Status: done):
# 1. Archive the completed plan per the dev-flow convention (archive/, not a flat .harness/ path — Consolidation arc ruling 6)
mkdir -p .harness/archive
mv .harness/PLAN.md .harness/archive/PLAN.archive.YYYYMMDD-<part-slug>.md

# 2. Promote the next queued plan to active
mv .harness/designs/<doc-slug>/queued-plans/<next-part-slug>.PLAN.md .harness/PLAN.md

# 3. Verify Status field is 'draft' or 'in-progress' in the new active plan
```

The skill body documents this fallback explicitly + flags it as temporary. Once v2.3.0 ships, `/release` will auto-promote (task 5 of this plan); the manual `mv` becomes legacy and can be deleted from operator runbooks.

#### Step 5 — Update parent doc Document History

Append a row to the parent design's Document History table:

```
| <today UTC> | Sequenced into <N> plans via /design sequence; first plan active at .harness/PLAN.md (<first-part-slug>), <N-1> queued at .harness/designs/<doc-slug>/queued-plans/. | final |
```

Parent Status stays `final` — sequence doesn't transition Status. Only `/design author` (draft → review → final) and the harness `/release` extension (final → launched, task 5) do.

Also bump parent's `updated` frontmatter field to today.

#### Tool allowlist

**`Read, Write, Edit, Glob, Grep`** — no `Bash`. Same allowlist as `/design author` and `/design translate`. Operations:

- `Read` — all part files; parent design (for Document History context + Quality Attributes / Technical Debt extraction); existing `<project>/.harness/PLAN.md` for in-progress check; harness's `templates/PLAN.md` for shape reference.
- `Glob` — discover `parts/` files + existing `queued-plans/` state on re-run.
- `Write` — new `.harness/PLAN.md` + queued plan files.
- `Edit` — append parent design Document History; bump parent's `updated` field.

The `mv` commands documented in the manual-promotion fallback are operator-side shell commands, NOT skill operations — the skill never invokes Bash.

#### Hard gates

`/design sequence` REFUSES to run on:

1. Non-`final` parent Status (same as `/design translate`).
2. Missing or empty `parts/` directory (must run translate first).
3. Invalid frontmatter in any part file (malformed YAML or missing required fields).
4. Dependency cycle in parts (refuse with cycle path).
5. Missing dependency target (part references a slug that doesn't exist).
6. Existing `.harness/PLAN.md` with `Status: in-progress` (unless `--force-replace` flag).

#### Worked example

Three-part design with dependencies showing topological sort + queueing.

**Input:** parts/ directory from `/design translate` on `foo-system`:

```
wiki/explanation/designs/foo-system/parts/
├── foundations.md         (dependencies: [],                       estimated_scope: M)
├── command-surface.md     (dependencies: [foundations],            estimated_scope: S)
└── rollout.md             (dependencies: [foundations, command-surface], estimated_scope: S)
```

Parent design at `wiki/explanation/designs/foo-system.md`, `Status: final`.

**Invocation:** `/design sequence foo-system`

**Step 1 (preconditions):**

> Agent: *(Reads parent frontmatter → Status: final ✓; parts/ exists with 3 files ✓; frontmatter on each part parses ✓; `.harness/PLAN.md` exists with Status: done from a prior plan — overwrite allowed)*
> Agent: "Preconditions met. Proceeding."

**Step 2 (topological sort):**

> Agent: *(Builds graph. foundations has 0 in-edges; sort selects it first. After removing foundations, command-surface has 0 in-edges, rollout has 1 (command-surface). Alphabetical tie-break between {command-surface} → command-surface second. After removing it, rollout has 0 in-edges → third.)*
> Agent: "Topological order: foundations → command-surface → rollout."

**Step 3 (generate PLAN.md per part):**

> Agent: *(For each part, builds a PLAN.md from harness's templates/PLAN.md shape:)*
> - **foundations.PLAN.md**: Brief from foundations.md's Scope; Goal from its Verification criteria; Constraints from parent's Security + Reliability + Data Integrity QA sub-sections (extracted as part-relevant); Tasks draft 3 placeholder tasks ("schema migration", "repository pattern impl", "query layer tests"); Verification strategy verbatim from part's Verification criteria; Locked design calls cross-ref parent + extract 2 Alternative Considered rejection rationales as one-liners.
> - **command-surface.PLAN.md**: similar shape, Brief from command-surface.md, depends-on-foundations noted in Out of scope.
> - **rollout.PLAN.md**: similar, Brief from rollout.md, depends-on-both noted.

**Step 4 (write to harness `.harness/`):**

> Agent: *(Writes:)*
> ```
> <project>/.harness/PLAN.md                                          (foundations — active)
> <project>/.harness/designs/foo-system/queued-plans/command-surface.PLAN.md  (queued)
> <project>/.harness/designs/foo-system/queued-plans/rollout.PLAN.md           (queued)
> ```

**Step 5 (update parent Document History):**

> Agent: *(Edits parent design's Document History table:)*
> ```
> | 2026-05-15 | Sequenced into 3 plans via /design sequence; first plan active at .harness/PLAN.md (foundations), 2 queued at .harness/designs/foo-system/queued-plans/. | final |
> ```
> Agent: "Sequence complete. 3 plans generated. foundations.PLAN.md is active. Run `/plan` to refine task decomposition, then `/work` to start on the first task. When foundations completes (Status: done), promote the next plan: `mv .harness/designs/foo-system/queued-plans/command-surface.PLAN.md .harness/PLAN.md` (until v2.3.0 of agentm ships and auto-promotion lands)."

**Alternate case (tie-breaking demo):** suppose two parts (`alpha` and `bravo`) both have `dependencies: []`. After Step 2:

> Agent: *(Both alpha + bravo have 0 in-edges. Alphabetical tie-break: alpha first → active; bravo queued.)*
> Result: `.harness/PLAN.md` = alpha; `queued-plans/bravo.PLAN.md` queued.

Deterministic — re-running sequence produces identical ordering.

#### Failure modes

- **Parent doc unreadable** — halt with explicit path + error.
- **Part file unreadable** — halt with the path; don't proceed with partial sequence.
- **Cycle in part dependencies** — refuse with the smallest cycle path; operator edits parts/ files to break the cycle.
- **Missing dependency target** — refuse with the part + missing-slug; operator either removes the dependency or creates the missing part.
- **`.harness/PLAN.md` in-progress without `--force-replace`** — refuse with clear remediation (close the active plan or use the flag).
- **No `.harness/` in cwd or ancestors** — refuse with "must run from inside a harness-installed project" + hint to install harness via `agentm/install.sh`.
- **Re-run after parts/ edits** — the skill diffs proposed plans against existing queued plans; presents per-file overwrite/keep/cancel. Existing active PLAN.md is preserved unless `--force-replace`.

#### Anti-patterns

`/design sequence` must not:

- **Silently transition parent Status.** Only Document History append; Status stays `final`.
- **Skip topological sort.** Even for a single-part design, the sort runs (trivially) — never short-circuit.
- **Use non-deterministic ordering.** Alphabetical tie-break is documented and must be preserved across runs.
- **Clobber an in-progress `.harness/PLAN.md`.** The `--force-replace` flag is the explicit operator override; default behavior refuses.
- **Auto-promote queued plans.** That's task 5's harness `/release` extension. Sequence only writes; promotion lands elsewhere.
- **Generate non-draft PLAN.md Status.** All generated plans start `Status: draft` so the human runs `/plan` to refine before `/work`. The skill produces a starting point, not the final task list.

## Authoring conventions (operator-locked, 2026-06-09)

Learned from a full operator review cycle (the crickets `continuous-integration` design — two review rounds + a hardening arc). The per-section detail lives in the template's HTML comments; the narrative + before/after examples live in the operator's vault overlay (`<vault>/projects/_global/wiki-style/2026-06-09-design-doc-prose.md`). **Read both at authoring time.** The short version:

1. **Structure is sacred.** Never change a design doc's 10-section structure without asking the operator first. "Update to current reality" = content-only.
2. **Titles are plain:** "<Topic> Design" — no em-dash headline subtitles.
3. **Objective: 3–4 plain sentences, hard cap.** No jargon; overflow belongs in Background.
4. **Background: max 3 paragraphs** — why it's needed · the environment/constraints that drive the decisions · the cost/effort realities.
5. **Overview reads cold:** a reader who doesn't know the codebase gets the shape; gloss internal names on first use.
6. **Infrastructure is platform-first + charts:** the platform/framework → a jobs/components table → a triggers table → the guarantees. Deep mechanics go to Detailed Design.
7. **Classify honestly:** deployment is not CI; a job goes in the design that owns its concern (cross-link, don't co-document).
8. **Name the LLM as the actor** wherever behavior rides automation inputs/outputs (polling CI, reading logs, closing out on green).
9. **Quality Attributes / Operations:** walk every sub-section, but the doc keeps only the real ones — omit N/A stubs. CI/automation designs check supply-chain pinning under Security.
10. **Project management for shipped systems:** no work estimates; Launch Plans = just the dates when passed; Documentation Plan lists **all** wiki pages documenting the system.
11. **Risks carry re-audit triggers; paid-down risks come out** of the list (a one-line lead note records the paydown + date). A live incident that proves a section's claim is recorded **in that section** as its proof point.
12. **Document History: one row per day** — consolidate same-day entries into a single row telling that day's story (Status = the day's end state).

## Tool allowlist

**`Read, Write, Edit, Glob, Grep` only.** No `Bash`, no `NotebookEdit`. The skill's job is file authorship + structured edits — not shell invocation. Bash invocation, if needed downstream, comes from the harness `/work` phase during stage 5 (per-part execution).

## File conventions

- **Template:** `crickets/skills/design/templates/design-doc.md` (ships with the skill). The `/design author` flow copies this template into the target project as the starting point for a new design.
- **Confidential designs:** `.harness/designs/<slug>.md` — gitignored, machine-local; not committed to a public repo. Use for early exploration, internal-only designs.
- **Published designs:** `wiki/explanation/designs/<slug>.md` — committed; surfaces in `wiki/Home.md` + `wiki/_Sidebar.md` as the canonical "Why we built X" entry point per ADR 0004 (lands in task 6 of plan #6).
- **Parts:** `<doc-dir>/parts/<part-slug>.md` — same dir as the parent design doc, in a `parts/` subdir.
- **Queued plans:** `.harness/designs/<doc-slug>/queued-plans/<part-slug>.PLAN.md` — waiting in the wings until harness `/release` promotes the next one.

## Status lifecycle

| State | Set by | Meaning |
|---|---|---|
| `draft` | `/design author` (initial creation) | Authoring in progress; not ready for review. |
| `review` | `/design author` (human signals readiness) | Author thinks it's done; awaiting human approval. |
| `final` | `/design author` (explicit human approval) | Approved. **Hard gate:** `/design translate` and `/design sequence` only run on `Status: final`. |
| `launched` | Harness `/release` (last queued part's PLAN.md hits `Status: done`) | All structural parts shipped. The design's full execution arc is complete. |

`Status` is set by the skill; users don't edit it by hand.

## When to reach for it

- **Use `/design`** when: the problem is ambiguous, multiple stakeholders need to align, the change has cross-cutting Quality Attributes (security/reliability/scalability/etc.) or Operations (SLAs/monitoring/rollback) concerns that need explicit thinking before code starts, or you want a canonical "Why we built X" wiki entry point.
- **Use `/plan` instead** when: the problem is already well-scoped, you can name the verification criteria in one sentence per task, and the change is fully contained to code (no ops / no rollout complexity).
- **Use both** when: you're standing up something substantial that will ship in multiple parts. Run `/design author` → `/design translate` → `/design sequence` once; then `/work` cycles through each part's PLAN.md tasks.

## Cross-references

- [Use-The-Design-Skill](../../wiki/how-to/Use-The-Design-Skill.md) — practical recipe with three worked scenarios *(lands in task 6 of plan #6)*
- [ADR 0004 — Design skill design](../../wiki/explanation/decisions/0004-design-skill.md) — locked design calls + rationale + consequences *(lands in task 6 of plan #6)*
- Template: [`templates/design-doc.md`](templates/design-doc.md) — the 10-section structure all `/design author` flows write against
- The dev loop's `/work` / `/release` execute the per-part PLAN.md files generated by `/design sequence`
