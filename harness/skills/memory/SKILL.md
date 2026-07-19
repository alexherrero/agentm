---
name: memory
description: File-based, agent-curated permanent memory layer that lives inside the user's existing Obsidian vault. Captures durable preferences / workflows / fixes via a reflection sidecar; recalls relevant entries automatically into every new prompt via SessionStart + UserPromptSubmit hooks; adapts the agent's behavior over time without explicit configuration. The goal is compound learning — each conversation makes the next one better, because the agent never forgets what already happened.
kind: skill
supported_hosts: [claude-code, antigravity]
version: 0.1.0
install_scope: project
---

# memory — permanent agent memory via Obsidian-vault-folder + reflection sidecar

The first toolkit skill that integrates with the user's own personal note-taking surface (Obsidian) rather than maintaining a separate agent-only vault. The skill exposes four sub-commands (`save` / `evolve` / `reflect` / `search`); recall is hook-driven rather than user-invoked (SessionStart + UserPromptSubmit hooks shipped alongside in the [recall-loop part](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/memoryvault/parts/recall-loop.md)).

**Position vs. built-in agent memory** (Claude memories, vendor-specific context features): built-in memory is per-platform, opaque, lossy, and not composable across tools. MemoryVault is file-based + version-control-friendly + human-inspectable. The user can open Obsidian + read or edit any captured entry directly; the agent's memory + the user's notes coexist in the same place.

**Position vs. session transcripts**: Claude Code transcripts capture everything that happened. MemoryVault captures what's worth keeping forever — the durable preferences / workflows / fixes that make Day 2 better than Day 1.

## When to reach for which sub-command

| You want to... | Reach for |
|---|---|
| Capture a specific preference / workflow / fix manually right now | `/memory save` |
| Stage a casual thought, link, or idea for later triage — not yet reviewed | `/memory capture` |
| Read a web page or file into memory as a full document plus retrieval chunks | `/memory ingest` |
| Replace an existing entry with a corrected version (preserving audit trail) | `/memory evolve` |
| Run reflection over the current session transcript on demand (or a specified transcript path) | `/memory reflect` |
| Mine the full historical transcript backlog (`~/.claude/projects/*/`) with dry-run preview + resume-safe batching | `/memory reflect corpus` |
| Search the vault for entries matching a query (when auto-recall via UserPromptSubmit hook didn't pull what you wanted) | `/memory search` |
| Refresh the auto-indexed `personal-skills/` pointers (after a SKILL.md change, or on a fresh install) | `/memory index-skills` |
| Manually trigger the internet skill-discovery scan (cadence-checked by default via the idle hook) | `/memory discover-skills` |
| Run the adapt-don't-import workflow over discovered patterns (Python rubric → enriched JSONs → LLM sub-agent judgment → watchlist entries) | `/memory adapt-skills` |
| Review pending entries in `_skill-watchlist/` — promote / dismiss / defer | `/memory watchlist` |
| Bulk-triage the `_inbox/` backlog — promote (reinforced) / merge (near-duplicate) / expire (stale) | `/memory inbox --bulk-review` |
| See what the heat-based always-load policy would demote or promote (never applies without `--apply`) | `/memory heat-policy` |
| Check the vault for orphans, broken links, contradictions, and a per-note quality score — on demand (the weekly dreaming cycle also runs this automatically) | `/memory lint` |

Auto-recall happens via the [SessionStart + UserPromptSubmit hooks](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/memoryvault/parts/recall-loop.md) — operators don't invoke a recall command directly. Reflection happens automatically via Stop + idle hooks too; the manual `/memory reflect` is for one-off runs against arbitrary transcripts.

## Sub-commands

### `/memory save`

Synchronously writes a markdown entry to MemoryVault. File write returns immediately (<50ms target); embedding + vec-index update are async and deferred (the actual embedding integration lands in task 4 of plan #7a part 1; until then the embedding step is a no-op stub that logs `"embedding queued (deferred to task 4)"`).

#### Invocation

```
/memory save <kind> <slug> [--group <group>] [--always-load] [--vault-path <path>]
                            [--tags <tag1,tag2,...>] [--supersedes <old-path>]
```

| Arg | Required | Default | Meaning |
|---|---|---|---|
| `<kind>` | yes | — | Entry kind (preference / workflow / fix / domain-reference / idea / etc.). Subdir name under the chosen group. |
| `<slug>` | yes | — | Kebab-case identifier; filename stem. Validated as `^[a-z0-9-]+$`. |
| `--group <group>` | no | `personal-private` | Memory group: `personal-private` / `personal-skills` / `projects/<project-slug>`. |
| `--always-load` | no | false | Routes to `MemoryVault/personal-private/_always-load/<slug>.md` and sets `always_load: true` frontmatter — entry gets injected at SessionStart per the recall-loop part. Overrides `--group` (always lands in `_always-load`). |
| `--vault-path <path>` | no | from config | Absolute path to the MemoryVault folder. Resolution order: `--vault-path` arg > `MEMORY_VAULT_PATH` env var > `~/.config/crickets/memory.yml` `vault_path:` key > error. |
| `--tags <tag1,tag2>` | no | empty list | Comma-separated tags; written to `tags:` frontmatter list. |
| `--supersedes <old-path>` | no | empty | Path to an existing entry this new entry supersedes. Sets `supersedes:` frontmatter; **does NOT archive the old entry** — that's `/memory evolve`'s job (task 3). `--supersedes` is for cross-link-without-archive cases. |

The entry body (free-form markdown after the YAML frontmatter) comes from stdin OR an interactive prompt. The agent following the skill body asks for the body content if not piped.

#### Step-by-step flow

**Step 1 — Resolve vault path.** Walk the resolution order: `--vault-path` arg → `MEMORY_VAULT_PATH` env var → `~/.config/crickets/memory.yml` (`vault_path:` key). If none found, halt with `"No vault path resolved. Set --vault-path, MEMORY_VAULT_PATH, or ~/.config/crickets/memory.yml vault_path: <path>."`. Verify the resolved path exists and is a directory; halt otherwise with a clear error.

**Step 2 — Validate inputs.** `<kind>` and `<slug>` must match `^[a-z0-9-]+$` (kebab-case). `--group` (if provided) must match `^[a-z0-9-]+(/[a-z0-9-]+)?$` (kebab-case; one optional `/<project-slug>` segment for `projects/<slug>`). Tags (if provided) each must match `^[a-z0-9-]+$`. Halt on any validation failure with a clear error pointing at the offending arg.

**Step 3 — Compute target path.** Two cases:

- `--always-load` set: `<vault-path>/personal-private/_always-load/<slug>.md`
- `--always-load` not set: `<vault-path>/<group>/<kind>/<slug>.md` (with `<group>` defaulting to `personal-private`)

Create parent directories if they don't exist (via Write tool's implicit dir creation, or explicit `Glob` + `Write` for clarity).

**Step 4 — Collision check.** If the target path already exists, halt with `"Entry already exists at <path>. Use /memory evolve to supersede the existing entry, or pick a different slug."`. Never overwrite an existing entry from `/memory save` — that's `/memory evolve`'s job.

**Step 5 — Build frontmatter.** Construct YAML frontmatter with these fields:

```yaml
---
kind: <kind>
status: active
created: <today UTC, YYYY-MM-DD>
updated: <today UTC, YYYY-MM-DD>
tags: [<tag1>, <tag2>, ...]   # empty list [] if no --tags
group: <group-or-personal-private>
slug: <slug>
always_load: <true-if-flag-set-else-false>
supersedes: <old-path-if-flag-set>   # omit field entirely if no --supersedes
---
```

Frontmatter field order is locked above for deterministic diffs. The `always_load` field is always present (true or false) so the recall hooks can grep for it without ambiguity.

**Step 6 — Compose entry content.** Prepend the frontmatter to the body content. Final shape:

```
---
<frontmatter>
---

<body markdown>
```

Trailing newline at EOF.

**Step 7 — Write file.** Use the Write tool to create the file at the computed target path. Verify the write succeeded (Write tool returns success/error). File write is synchronous; on success, the file is on disk before the next step.

**Step 8 — Queue async embedding + vec-index update.** Until task 4 of this plan lands (embedding integration), this step is a no-op stub: log `"embedding queued (deferred to task 4)"` and continue. After task 4 ships, this step kicks off an async embedding call to the configured provider (Anthropic API default; local sentence-transformers fallback per `memory.use_api_embeddings`) and writes the embedding to `MemoryVault/_meta/vec-index.db` via sqlite-vec. The async-ness ensures file write is never blocked by network or vec-index latency.

**Step 9 — Return confirmation.** Report success to the operator with:

```
Saved entry to <relative-path-from-vault-root>/<slug>.md
  kind:  <kind>
  group: <group>
  tags:  <tags-or-(none)>
  flags: <always-load-and-supersedes-if-set>
```

Plus the deferred-embedding note: `"(Embedding deferred to plan #7a part 1 task 4; index will populate on next reindex.)"`.

#### Tool allowlist

**`Read, Write, Edit, Glob, Grep`** — no Bash. The skill body never invokes shell commands directly. The agent following this body uses Write to create the entry file; Read to check config files for vault-path resolution; Glob to discover existing parent directories. Python scripts under `skills/memory/scripts/` (added in this task + tasks 3 + 4) handle the heavy lifting when invoked from Claude Code hooks (which run as standalone scripts, not as skill bodies) — see `Tool allowlist` section below for the skill-vs-script split.

#### Hard gates

- **Collision check is non-negotiable** — `/memory save` never overwrites. Operators wanting to replace an entry use `/memory evolve` (task 3).
- **Vault path must resolve** — no implicit fallback to `cwd` or `~`; explicit error if no resolution path succeeds. Prevents accidental writes to wrong directories.
- **Kind / slug / group validation** is strict — kebab-case only. Catches typos before they create unparseable directory structures.

#### Worked example

Saving a dev-flow convention as an `_always-load` entry:

**Invocation:**

```
/memory save preferences paragraph-long-status-narratives \
  --always-load \
  --tags dev-flow,status-reports,locked-design-call \
  --vault-path ~/Library/CloudStorage/GoogleDrive-<account>/My\ Drive/Obsidian/MemoryVault
```

(With the entry body provided via stdin or the agent prompting interactively.)

**Step 1** — `--vault-path` provided → resolved to the synced Obsidian vault root. Exists + is a directory. ✓

**Step 2** — `kind=preferences` ✓; `slug=paragraph-long-status-narratives` ✓; `--always-load` set → group ignored. Tags `[dev-flow, status-reports, locked-design-call]` each match `^[a-z0-9-]+$` ✓.

**Step 3** — `--always-load` set → target path: `<vault>/personal-private/_always-load/paragraph-long-status-narratives.md`. Parent dir created if absent.

**Step 4** — Collision check: path does not exist → proceed.

**Step 5** — Build frontmatter:

```yaml
---
kind: preferences
status: active
created: 2026-05-17
updated: 2026-05-17
tags: [dev-flow, status-reports, locked-design-call]
group: personal-private
slug: paragraph-long-status-narratives
always_load: true
---
```

**Step 6** — Compose with body:

```
---
<frontmatter>
---

Status:[x] task closeouts in the active plan file (`.harness/PLAN.md` or `PLAN-<name>.md`) must be paragraph-long narratives,
not just checkmarks. The next session's context is whatever the closeout captures —
so capture everything that matters: files changed, design calls, scope adjustments,
CI per-OS times, manual verification scenarios, negative-test results when relevant.
```

**Step 7** — Write file at the target path. ✓

**Step 8** — Log `"embedding queued (deferred to task 4)"`.

**Step 9** — Report:

```
Saved entry to personal-private/_always-load/paragraph-long-status-narratives.md
  kind:  preferences
  group: personal-private (overridden by --always-load → _always-load)
  tags:  dev-flow, status-reports, locked-design-call
  flags: always_load

(Embedding deferred to plan #7a part 1 task 4; index will populate on next reindex.)
```

#### Failure modes

- **Missing vault path** — all 3 resolution paths fail → halt with clear next-step text pointing at the 3 resolution options.
- **Vault path exists but is not a directory** — halt with `"<path>: not a directory."` and exit; never write to a file path treated as a vault root.
- **Invalid kind / slug / group / tags** — halt with the specific arg + the expected kebab-case shape; never coerce or silently lowercase.
- **Target file exists** — halt with the `/memory evolve` recommendation; never overwrite from `/memory save`.
- **Write tool fails** (disk full, permission denied, etc.) — surface the Write tool's error verbatim + a hint that the entry was NOT saved.

#### Anti-patterns

`/memory save` must NOT:

- **Overwrite an existing entry** — Step 4 collision check is the hard gate.
- **Block on embedding** — Step 8 is async-deferred; file write must complete before any network call.
- **Coerce slugs** — operator provides exact slug; validator rejects, never auto-lowercases or strips characters.
- **Default vault path to `cwd` or `~`** — explicit resolution chain; no implicit defaults that could write to wrong directories.
- **Modify Document History or other special files** in the vault — `/memory save` is a single-file-write primitive; cross-file ops are for `/memory evolve` (task 3) + the reflection sidecar (plan #7a part 3).

#### Python-side script (`scripts/save.py`)

In parallel with the agent-driven skill body, this part also ships a standalone Python script at `crickets/skills/memory/scripts/save.py` that implements the same save logic for **hooks** (which run as standalone Python scripts, not as skill bodies) + **operator-debug** (manual `python3 ...` invocation) + **testing** (deterministic fixture tests that don't require an agent).

The script exposes:

- **Function**: `save_entry(vault_path, kind, slug, body, *, group="personal-private", always_load=False, tags=None, supersedes=None) -> pathlib.Path` — the canonical save primitive. Returns the absolute path written. Raises `FileExistsError` on collision; `ValueError` on validation failure; `FileNotFoundError` if vault path doesn't exist.
- **CLI entry point**: `python3 save.py <kind> <slug> --vault-path <path> [--group <g>] [--always-load] [--tags <t1,t2>] [--supersedes <p>] [--body-file <path-or-->]` — same flag semantics as the skill body's `/memory save` invocation. `--body-file -` reads from stdin.

Both paths (skill-body Write + script-CLI Python) produce byte-identical entry files for the same inputs. Future hooks (plan #7a part 3's reflection sidecar) import + call `save_entry()` directly. The skill body's documented flow is operator-facing; the script is hook-facing; they don't compete.

### `/memory capture`

The second front door beside `/memory save` — `designs/friday/agentm-capture.md`'s capture-from-anywhere front door. `/memory save` writes straight to permanent memory; `/memory capture` stages a candidate to `<vault>/personal/_inbox/` instead, for the automated triage system to promote, merge, or expire later. Use `save` for an explicit, deliberate write you already know the destination for; use `capture` for a casual thought, a link, or an idea that hasn't been reviewed yet.

Canonical implementation: `scripts/capture.py`'s `capture()` function, writing via `vault_lock.atomic_write` directly (genuinely atomic, unlike `reflect.py`'s own inbox writer) — never through `save_entry`, since `_inbox` fails `save_entry`'s kebab-case `kind` validation by design (the standing convention that keeps staged items structurally distinct from validated permanent-memory destinations).

#### Invocation

```
python3 skills/memory/scripts/capture.py "<content>" \
  [--vault-path <path>] [--kind capture|idea] [--slug <slug>] \
  [--source <transport>] [--surface <device>] [--tags <t1> <t2> ...] \
  [--instructions <text>] [--source-url <url>]
```

| Flag | Use case |
|---|---|
| `content` (positional) | The captured text — a thought, or a link plus a note. |
| `--kind` | `capture` (default — a thought, link, or note) or `idea` (routes toward the ideas ledger once that fold exists). |
| `--slug` | Override the default timestamp-based slug (`capture-YYYYMMDDTHHMMSS`). A collision on either the default or an explicit slug appends a numeric suffix (`-1`, `-2`, …) rather than overwriting — the inbox is a raw-capture surface where a resend landing twice is an accepted, designed-for case. |
| `--source` | The transport, e.g. `cli`, `clipper`. Defaults to `cli` when invoked from the command line. |
| `--surface` | The device, e.g. `phone`, `desktop`. |
| `--tags` | Zero or more kebab-case tags. |
| `--instructions` | An operator-typed action to run after the candidate is absorbed, e.g. "add to my ideas ledger". **Security invariant, mechanically enforced**: this field carries only the literal text passed to this flag — never text parsed or inferred from `content`. A captured article whose body contains something that reads like an instruction ("ignore previous instructions...") never gains authority; that text is inert data. |
| `--source-url` | The link this capture is about, if any — marks the candidate for the ingest sweep's full processing once that sweep exists (capture design part 3). |

#### The candidate shape

A capture's frontmatter carries `kind`, `status: inbox`, `created`, `captured` (both full ISO8601 timestamps — `created` mirrors `save.py`'s convention, `captured` is the exact capture moment; a chat-surface caller's estimate of `captured` gets corrected later by the ingest sweep's re-stamp), `slug`, and optionally `source`, `surface`, `tags`, `source_url`, `instructions`. The staging folder is excluded from ordinary recall by default — a captured note becomes recallable only after the triage system promotes it.

#### The `memory_capture` MCP tool

The same door, callable by any connected MCP host: `memory_capture(content, kind="capture", title=None, tags=None, instructions=None, source_url=None) -> {success, id, slug}` or `{success: false, error}` on failure — a capture is never silently dropped. Registered alongside `memory_search` / `memory_append` / `memory_forget` in `scripts/memory_mcp_tools.py`.

#### Failure modes (graceful)

- **Unknown `kind`** → returns `success: false` with the specific error; nothing written.
- **Empty `content`** → returns `success: false`; nothing written.
- **Vault not resolved** → the CLI exits 2 with a clear remedy (`--vault-path` or `$MEMORY_VAULT_PATH`); the MCP tool raises via the shared `_require_vault()` path every other tool uses.
- **Write failure** (disk full, permissions) → returns `success: false` with the underlying error message — never a silent drop.

#### Anti-patterns

- **Don't populate `instructions` from anything other than this call's own explicit argument.** A caller that derives it from `content` (or any fetched/untrusted text) breaks the capture design's trust-boundary invariant at the call site.
- **Don't choose a destination.** `/memory capture` has no `--group`/project parameter by design — every write lands in `_inbox/`; only the triage/ingestion machinery (a later capture part) ever promotes it elsewhere.

### `/memory ingest`

Capture part 2 — `designs/friday/agentm-capture.md`'s article-ingestion door, sibling to `/memory capture`. Where `capture` stages a candidate for later triage, `ingest` reads a web page or file right now and writes it straight into permanent memory as two forms that always ship together: one intact full-document note (the complete original text, unmodified) and a set of small, reading-order-linked chunk notes for retrieval. Neither form replaces the other — this is the only capture-family door that produces more than one note per item.

Canonical implementation: `scripts/ingest.py`'s `ingest()` function. Both the document note and every chunk go through `save_entry()` (unlike `/memory capture`'s direct `_inbox/`-only write) — these are permanent-memory writes from the start, each individually validated and queued for search indexing.

#### Invocation

```
python3 skills/memory/scripts/ingest.py <url-or-file> \
  [--vault-path <path>] [--topic <slug>]
```

| Flag | Use case |
|---|---|
| `<url-or-file>` (positional) | A URL (fetched via a single stdlib `urllib` GET, no HTTP dependency) or a local file path. |
| `--topic` | The topic slug (kebab-case) notes for this ingest are tagged and slug-prefixed with. **Omit it to get a suggestion first** — the command extracts a title-based slug and stops without writing anything; re-run with `--topic <suggested-or-your-own>` to actually ingest. This is a real confirmation step, not an auto-accept. |
| `--vault-path` | Same resolution chain as `/memory save`: arg > `MEMORY_VAULT_PATH` env > error. |

#### What gets written

- **The full-document note** (`kind: domain-reference`, `group: personal`) — the complete extracted text, verbatim. HTML content is tag-stripped (a lightweight `<title>`/paragraph extractor, not a readability algorithm — scripts and styles are dropped, everything else becomes plain text); a local plain-text or markdown file passes through unmodified.
- **The chunk notes** (same `kind`/`group`) — one per `chunking.py`'s `chunk_text()` output, each carrying a footer that links back to the full document and to its immediate reading-order neighbors (previous/next only — the first chunk has no "previous," the last has no "next," so the chain never cycles).
- **Every note** carries `source_url`/`source_fetched` when the source was a URL (omitted for local files), and a `tags: [<topic>]` entry. Slugs are `<topic>-<title-slug>` for the document and `<topic>-<title-slug>-chunk-N` for each chunk, so same-topic ingests sort together even though they share the flat `group: personal` every other kind in this vault uses (the design's own "filed under `personal/domains/<topic>/`" phrasing describes the *intent* — discoverable by topic — which this achieves through slug-prefixing and tagging rather than a new per-topic directory layer `save_entry`'s `group`/`kind`/`slug` path formula has no clean way to produce without breaking the flat-`group`-per-kind convention every other entry follows).

#### Failure modes (graceful)

- **Fetch/read failure** (bad URL, network error, missing file) → returns `success: false` with the underlying error; nothing written. No retry, no paywall handling — that resilience is the ingest sweep's job (capture part 3), not this command's.
- **Empty content** → returns `success: false`; nothing written.
- **Vault not resolved** → the CLI exits 2 with a clear remedy.

#### Anti-patterns

- **Don't skip the topic-confirmation step in scripted/agent use.** A caller that always guesses `--topic` up front without surfacing the suggestion defeats the design's "the agent suggests a title-based slug for you to confirm" intent — treat the no-`--topic` suggestion as a real prompt, not dead code.
- **Don't retune `chunk_text()`'s parameters here.** This command calls the existing chunking function unmodified; chunk-size tuning is out of this door's scope.

### `/memory evolve`

Atomic archive-and-replace primitive that prevents memory rot when preferences change. The old entry gets archived with `status: superseded`; the new entry takes the old entry's place (in-place by default; renamed slot via `--new-slug` for evolutions that also rename). Both files cross-link via `supersedes` / `superseded_by` frontmatter so the supersession graph is queryable. Recall filters skip `status: superseded` entries by default (documented contract — implemented in the recall engine, plan #7a part 2).

#### Invocation

```
/memory evolve <old-path> <reason> [--new-slug <slug>] [--body-file <path-or-->]
                                    [--vault-path <path>]
```

| Arg | Required | Default | Meaning |
|---|---|---|---|
| `<old-path>` | yes | — | Relative path from vault root to the entry being superseded (e.g. `personal-private/preferences/foo.md`). |
| `<reason>` | yes | — | Free-text rationale recorded in the archive's `superseded_reason` frontmatter. Captures WHY this evolution happened — important for audit-trail review. |
| `--new-slug <slug>` | no | (same slug as old) | If set, the new entry lands at `<old-parent-dir>/<new-slug>.md` (renamed evolution). If absent, the new entry takes the old entry's slot (in-place evolution; same slug). Validated as `^[a-z0-9-]+$`. |
| `--body-file <path-or-->` | no | `-` (stdin) | Path to file with the new entry body, or `-` to read from stdin. |
| `--vault-path <path>` | no | from config | Same resolution chain as `/memory save`: `--vault-path` arg > `MEMORY_VAULT_PATH` env > config (deferred) > error. |

#### Step-by-step flow

**Step 1 — Resolve vault path + validate.** Same resolution chain as `/memory save`. `<old-path>` must exist relative to vault root + be a file. `<reason>` must be non-empty. `--new-slug` (if provided) must be kebab-case.

**Step 2 — Read old entry.** Parse YAML frontmatter + body from `<old-path>`. The old frontmatter must have `status: active` — refuse to evolve already-superseded or resolved entries (escape hatch: operator can edit `status` manually if they really want to evolve a superseded entry, but the skill doesn't do it).

**Step 3 — Compute target paths.**

- **Archive path**: `MemoryVault/personal-private/_archive/<original-relative-path>.<YYYYMMDD>.md`. Example: `personal-private/preferences/foo.md` → `personal-private/_archive/personal-private/preferences/foo.md.20260517.md`. (The double-`personal-private` reflects preserving the original path structure under the archive prefix so the relationship is reconstructable.)
- **New entry path**: if `--new-slug` set → `<old-parent-dir>/<new-slug>.md`; else → same as `<old-path>` (in-place).

**Step 4 — Build archive content.** Take the old entry's frontmatter + body, then update the archive's frontmatter:

- `status: active` → `status: superseded`
- Add `superseded_by: <relative-path-from-vault-to-new-entry>`
- Add `superseded_at: <ISO-8601 UTC timestamp>` (YYYY-MM-DDThh:mm:ssZ)
- Add `superseded_reason: <reason>` (escaped for YAML safety)
- Preserve all other fields verbatim (kind, created, tags, group, etc.)

The body stays unchanged in the archive — the archive is a point-in-time snapshot.

**Step 5 — Build new entry content.** Frontmatter inherits relevant fields from old (kind, group, tags) but resets:

- `status: active`
- `created: <today UTC>` (today; the new entry's creation date is the evolution date)
- `updated: <today UTC>`
- `slug: <new-slug-or-old-slug>`
- `supersedes: <relative-path-from-vault-to-archive>` ← cross-link back
- `always_load`: inherited from old (operator can override by re-running with explicit args if they want to change it; default is "inherit").

The new entry's body comes from `--body-file` (or stdin via `-`).

**Step 6 — Atomic-ish write sequence.** Two-phase write:

1. **Write archive** at `<archive-path>` (create + content via `Write` tool). This is purely additive — no state change to existing files yet.
2. **Replace old entry**:
   - **In-place case** (`<new-path>` == `<old-path>`): `Write` tool overwrites `<old-path>` with new content. Filesystem-atomic at the syscall level on APFS / ext4 / NTFS-same-volume.
   - **Renamed case** (`<new-path>` != `<old-path>`): `Write` tool creates `<new-path>` with new content + `Glob`/`Read`-confirms old still there + delete old via a future filesystem op (skill body uses `Write` to create new + leaves the old in place as a transitional state — until the recall filter starts skipping `_archive/`-rooted paths, leaving the old entry temporarily doesn't pollute recall). Operator can clean up the old entry's location manually after verifying the new entry.

The Python-side script (`scripts/evolve.py`) does both write + unlink atomically via `os.rename` + temp-file pattern — see the Python-side script section below.

**Step 7 — Queue async vec-index update for both files.** Until task 4 lands, this step is a no-op stub: log `"vec-index update queued for: <new-path>, <archive-path> (deferred to task 4)"`. After task 4, both files get re-indexed: archive is marked status:superseded (so recall filters skip it) + new entry is freshly indexed.

**Step 8 — Return confirmation.**

```
Evolved entry:
  old: <relative-old-path> → archived to <relative-archive-path>
  new: <relative-new-path>
  reason: <reason>
  status: old=superseded, new=active
```

#### Tool allowlist

**`Read, Write, Edit, Glob, Grep`** — no Bash. The skill body uses `Read` for the old entry, `Write` for both archive + new entry, `Glob` for confirming paths. The atomic-rename guarantee in the Python-side script comes from `os.rename()` (called within `scripts/evolve.py`, which is dispatched from hooks but not from the skill body).

#### Hard gates

- **Old entry must exist + have `status: active`** — refuse to evolve missing entries or already-superseded entries (escape hatch is manual frontmatter edit).
- **`<reason>` is non-empty + non-trivial** — single-word reasons like `"updated"` accepted but flagged in the confirmation output ("consider a more descriptive reason for the audit trail").
- **Archive path collision check** — if `<archive-path>` already exists (rare; would happen if you evolved the same entry twice on the same day), append `-N` suffix until unique.
- **Slug rename validation** — `--new-slug` must be kebab-case; matches `/memory save`'s slug discipline.
- **No evolve on `_always-load/` entries via slug rename** — `--new-slug` doesn't apply to entries in `_always-load/`; those evolve in place only (operator can manually move + edit if they really want to rename an always-load entry). Skill body refuses with a clear error.

#### Worked example

In-place evolution of a stale preference:

**Invocation:**

```
/memory evolve personal-private/preferences/paragraph-long-status-narratives.md \
  "Switched preference: now want short bullet lists per task closeout, not paragraphs" \
  --body-file -
```

(With the new body piped via stdin.)

**Step 1** — vault path resolved; old path exists + readable; reason non-empty. ✓

**Step 2** — Read old entry, parse frontmatter:

```yaml
kind: preferences
status: active
created: 2026-05-17
updated: 2026-05-17
tags: [dev-flow, status-reports, locked-design-call]
group: personal-private
slug: paragraph-long-status-narratives
always_load: true
```

Status is `active` ✓ → can evolve.

**Step 3** — Compute paths:
- Archive: `personal-private/_archive/personal-private/preferences/paragraph-long-status-narratives.md.20260517.md`
- New entry: `personal-private/preferences/paragraph-long-status-narratives.md` (in-place; same slug)

**Step 4** — Build archive content. Old frontmatter updated:

```yaml
kind: preferences
status: superseded
created: 2026-05-17
updated: 2026-05-17
tags: [dev-flow, status-reports, locked-design-call]
group: personal-private
slug: paragraph-long-status-narratives
always_load: true
superseded_by: personal-private/preferences/paragraph-long-status-narratives.md
superseded_at: 2026-05-17T23:45:00Z
superseded_reason: 'Switched preference: now want short bullet lists per task closeout, not paragraphs'
```

Body unchanged in archive.

**Step 5** — Build new entry content:

```yaml
kind: preferences
status: active
created: 2026-05-17
updated: 2026-05-17
tags: [dev-flow, status-reports, locked-design-call]
group: personal-private
slug: paragraph-long-status-narratives
always_load: true
supersedes: personal-private/_archive/personal-private/preferences/paragraph-long-status-narratives.md.20260517.md
```

New body (from stdin).

**Step 6** — Write archive → overwrite old path with new content.

**Step 7** — Log `"vec-index update queued for: ... (deferred to task 4)"`.

**Step 8** — Confirm:

```
Evolved entry:
  old: personal-private/preferences/paragraph-long-status-narratives.md
       → archived to personal-private/_archive/personal-private/preferences/paragraph-long-status-narratives.md.20260517.md
  new: personal-private/preferences/paragraph-long-status-narratives.md (in-place)
  reason: Switched preference: now want short bullet lists per task closeout, not paragraphs
  status: old=superseded, new=active
```

#### Failure modes

- **Old path missing** — halt with `"<old-path>: not found relative to vault root."`.
- **Old path is not a file** — halt with type error.
- **Old entry frontmatter unparseable** — halt with `"<old-path>: frontmatter invalid; manual fix needed before evolve."`. Don't try to auto-repair.
- **Old status not `active`** — halt with `"<old-path>: status is <status>, not active. Cannot evolve a non-active entry. Manual fix path: edit status field if you really want to evolve it."`.
- **Archive collision (same-day re-evolve)** — append `-N` suffix until unique (`.20260517.md` → `.20260517-2.md`); continue without halting.
- **`--new-slug` on `_always-load/` entry** — halt with `"Cannot rename _always-load/ entries via --new-slug; evolve in place only."`.
- **Partial-failure recovery**: if step 6 fails between archive-write and new-entry-write, the operator sees both files present (archive with superseded frontmatter + old at its original path still with active frontmatter). Recovery: read the archive, decide whether to commit or revert; manual fix path documented in troubleshooting.

#### Anti-patterns

`/memory evolve` must NOT:

- **Modify the old entry's body** — only frontmatter additions; body is the point-in-time snapshot.
- **Skip the archive** — never replace old with new directly; the archive is the audit trail.
- **Coerce or auto-rename slugs** — `--new-slug` is explicit operator intent.
- **Evolve `status: superseded` entries silently** — refuse with clear error; the supersession graph must be traversable from the active state outward, not the other way.
- **Inherit `created` from old** — the new entry's created date is the evolution date (the new entry didn't exist before).

#### Python-side script (`scripts/evolve.py`)

Canonical implementation at `crickets/skills/memory/scripts/evolve.py`. Same patterns as `save.py`:

- **Function**: `evolve_entry(vault_path, old_path, new_body, reason, *, new_slug=None) -> tuple[Path, Path]` — returns `(new_entry_path, archive_path)`. Raises `FileNotFoundError` (old missing), `ValueError` (validation, status≠active, frontmatter unparseable), `FileExistsError` (archive collision after max retries).
- **CLI entry point**: `python3 evolve.py <old-path> <reason> [--new-slug <slug>] [--body-file <p-or-->] [--vault-path <p>]`.
- **Atomic-ish write sequence**: writes archive first (additive); writes new entry second; both via `write_bytes()` for LF-only output. Best-effort atomicity at the syscall level for individual file ops; multi-file consistency relies on the operator detecting + recovering from partial failures (documented in failure modes above).
- **Frontmatter parsing**: uses `yaml.safe_load` (PyYAML required — already a toolkit dep via `validate-manifests.py`).
- **LF-only output**: same convention as `save.py` — `target.write_bytes(content.encode("utf-8"))` to bypass platform-specific line-ending translation. Critical for cross-platform Obsidian-synced markdown.

Both paths (skill-body `Write` + script-CLI Python) produce byte-identical files for the same inputs.

### `/memory reflect`

User-invokable trigger for the reflection sidecar — mines a Claude Code session transcript for durable candidate entries + surfaces them for triage. Two parallel mining passes: the **3-category mine** (Successful Workflows / User Preferences / Fixes & Workarounds) produces MemoryVault candidates; the **idea-candidate mine** (follow-ups / future projects / research candidates) produces ideas for the future idea-ledger (plan #7a part 4 will subscribe + persist these).

The same mining logic runs automatically via the Stop-event hook (after every session, lands in plan #7a part 3 task 3) + the idle-time hook (after N minutes idle, also recovers crashed sessions, lands in task 4). `/memory reflect` is the manual trigger for on-demand runs against arbitrary transcripts (re-running over an old session, testing the mining heuristic, dogfooding new patterns before they land in `reflect.py`).

#### Invocation shape

```
/memory reflect [--session <path>] [--memory-only | --idea-only] [--summary]
```

| Arg | Required | Default | Meaning |
|---|---|---|---|
| `--session <path>` | no | current Claude Code session transcript at `~/.claude/projects/<repo>/<session-id>.jsonl` | Absolute path to the JSONL transcript to mine. |
| `--memory-only` | no | false | Emit only the 3-category memory candidates (skip idea pass). Mutually exclusive with `--idea-only`. |
| `--idea-only` | no | false | Emit only the idea candidates (skip memory pass). |
| `--summary` | no | false | Prepend a 1-line summary (messages processed + candidate counts per pass) before the per-candidate output. |

#### Step-by-step flow

**Step 1 — Resolve transcript path.** If `--session` is set, use it directly. Otherwise look up the current Claude Code session transcript at `~/.claude/projects/<repo-dir-as-slug>/<session-id>.jsonl` where `<repo-dir-as-slug>` is the current working directory's path with `/` replaced by `-` and a leading `-`. Verify the resolved path exists; halt with `"Transcript not found: <path>"` if missing.

**Step 2 — Call the canonical mining module.** The deterministic implementation lives at `skills/memory/scripts/reflect.py`. Invoke via the dispatched Python script (operator path) or follow the agent-driven flow below (Claude Code in-session path). Both produce the same set of categorized candidates — the Python script's regex catalog is intentionally simple + tunable (Tech Debt #7); agent-driven runs can apply semantic judgment beyond what regex catches.

**Step 3 — 3-category mine.** Scan user + assistant messages for:

- **Successful Workflows** — tool-use sequences repeated 3+ times, multi-step procedures the user approved of. Look for: agent ran command sequence → user said "good" / "ship it" / no correction → same sequence reappears later.
- **User Preferences** — explicit user statements: "always X" / "never Y" / "I prefer Z" / "use X not Y" / "don't include Y". HIGH-confidence (explicit user signal). Also: user corrected the agent ("no, that's wrong" / "you should have X") → MEDIUM-confidence.
- **Fixes & Workarounds** — error/bug + nearby resolution: "fixed by X" / "resolved by Y" / "workaround: Z" / "the bug was caused by W". MEDIUM-confidence.

**Step 4 — Idea-candidate mine.** Scan for forward-looking statements: "we should also" / "later we could" / "follow-up:" / "could be its own project" / "as a follow-up". Each candidate gets a 2-sentence summary. Idea-ledger persistence is deferred to plan #7a part 4; this part just surfaces the candidates as in-memory objects.

**Step 5 — Score each candidate (instrumentation per Tech Debt #7).** For every candidate produced in steps 3-4, attach:

- `category`: `preferences` / `workflow` / `fix` / `idea`
- `confidence`: `HIGH` / `MEDIUM` / `LOW`
- `slug` suggestion (kebab-case)
- `title` (1-line)
- `body` (markdown ready for `/memory save`)
- `rationale` (1 sentence — which pattern matched, why it surfaced)
- `excerpts` (verbatim transcript snippets supporting the candidate)
- `occurrences` (match count)

The rationale + excerpts + occurrences fields are the load-bearing surface for [Use-The-Memory-Skill troubleshooting](Use-The-Memory-Skill.md) + the future `/memory inspect` command — they let the operator audit "why did this candidate get categorized this way?".

**Step 6 — Tri-modal routing** (lands in plan #7a part 3 task 5 — for now, this step displays candidates grouped by confidence without auto-saving anything).

- `HIGH` candidates → auto-save via `/memory save <category> <slug>` (task 5 wires the actual save call).
- `MEDIUM` candidates → interactive review prompt with options: approve (save as-is) / edit (modify body or slug, then save) / reject (drop) / skip (defer to next session) / supersede-existing-X (call `/memory evolve` against the named existing entry). Task 5 builds this prompt UX.
- `LOW` candidates → save to `MemoryVault/personal-private/_inbox/<slug>.md` for batch triage later (task 5 wires the `_inbox/` write).

`memory.review_mode: silent` toggle (lands with task 5) auto-approves MEDIUM candidates without prompting — useful for non-interactive sessions or operators who trust the heuristic.

**Step 7 — Return confirmation.** Display a summary:

```
Reflection complete. Mined <N> memory candidates + <M> idea candidates:
  preferences: <high-count> HIGH / <medium-count> MEDIUM / <low-count> LOW
  workflow:    <high-count> HIGH / <medium-count> MEDIUM / <low-count> LOW
  fix:         <high-count> HIGH / <medium-count> MEDIUM / <low-count> LOW
  ideas:       <total>

Routed:
  <H> saved automatically (HIGH)
  <M> sent to interactive review (MEDIUM)
  <L> queued in _inbox/ (LOW)
```

(Until task 5 ships, the "Routed" section reads `<deferred to plan #7a part 3 task 5>` and no actual save happens — the candidates print to stdout for review only.)

#### `/memory reflect corpus` — historical-pass mode (plan #7b task 2)

Batched paced walk over **all historical transcripts** at `~/.claude/projects/*/<session>.jsonl` (or `$MEMORY_TRANSCRIPT_ROOT`). Wraps the same mining + tri-modal-routing pipeline as the single-transcript path, plus skip-resume state-file management for safe interruption.

#### Invocation shape

```
python3 ~/Antigravity/crickets/skills/memory/scripts/reflect.py corpus \
  [--projects-root <dir>] [--vault-path <path>] \
  [--batch-size N] [--max-batches M] \
  [--execute] [--reset] [--route-mode auto|silent|interactive]
```

| Arg | Required | Default | Meaning |
|---|---|---|---|
| `--projects-root <dir>` | no | `$MEMORY_TRANSCRIPT_ROOT` or `~/.claude/projects` | Recursive walk root. |
| `--vault-path <path>` | yes¹ | `$MEMORY_VAULT_PATH` env | MemoryVault root — state file + inbox writes land here. ¹Required via flag or env. |
| `--batch-size N` | no | 10 | Sessions per batch (state saved each session; summary line printed every N sessions). |
| `--max-batches M` | no | unlimited | Stop after M batches — scout mode. State preserved for resume. |
| `--execute` | no | **off (dry-run default)** | Actually write entries + update state file. Without this flag, runs in dry-run mode (counts + estimates only). |
| `--reset` | no | off | Ignore existing state file (re-process everything). Combine with `--execute` to actually re-write. |
| `--route-mode <m>` | no | `auto` | MEDIUM-confidence routing. Default `auto` (→ `_inbox/`) is appropriate for historical-pass volume — interactive prompting per-candidate isn't practical at scale. |

#### Why dry-run by default

The first historical pass over hundreds of sessions can emit thousands of LOW-confidence candidates → most land in `_inbox/`. Dry-run lets the operator see scope first (counts + estimates) before committing to writes. Pattern: `corpus → review summary → corpus --execute → bulk-triage _inbox/`. Mitigates the firehose risk flagged in plan #7b PLAN.md Risks.

#### State file shape

`<vault>/_meta/transcript-reflection-state.json`:

```json
{
  "schema_version": 1,
  "sessions": {
    "<repo-slug>/<session-id>": {
      "processed_at": "2026-05-20T17:30:00+00:00",
      "message_count": 47,
      "memory_count": 3,
      "idea_count": 1,
      "status": "done",
      "transcript_path": "/absolute/path/to/session.jsonl"
    }
  }
}
```

Atomic writes via tempfile + rename — Ctrl-C mid-write can't leave a half-written state. State writes happen **after every session** (not every batch), so resume granularity is single-session.

#### Resume + interruption

- Re-running without `--reset` skips sessions with `status: "done"` in the state file.
- Ctrl-C at any time is safe — the in-progress session may have partial inbox entries from successful sub-writes, but the session itself is not marked `done` so it'll be re-processed on next run (potential for inbox-entry duplication; operator can dedupe during bulk-review).
- `--reset` clears the in-memory skip list but **does not delete the state file** — the next save_state call overwrites it with the new run's progress. To fully wipe, delete the file manually before re-running.

#### Anti-patterns

- **Don't run `corpus --execute` without first running dry-run.** First-pass scope is unpredictable; the dry-run estimate keeps the operator in control of how much `_inbox/` churn they're committing to.
- **Don't run `corpus` against `~/.claude/projects/` without setting `--max-batches` for the first execute run.** Even with the dry-run preview, scout-mode (e.g. `--max-batches 1 --batch-size 5`) is the safer escalation — process 5 sessions, eyeball the inbox output, decide whether to continue.
- **Don't combine `corpus` with `interactive` route-mode.** Historical-pass volume means hundreds of prompts. The hook-style `auto` mode (default) routes MEDIUM → `_inbox/` for later batch triage via `/memory inbox --bulk-review` (see its own section below).

#### Canonical Python implementation

The agent's in-context mining (steps 3-4 above) follows the same heuristic as `skills/memory/scripts/reflect.py`. Operators can invoke the Python script directly for scripting / dogfooding / hook execution:

```bash
python3 ~/Antigravity/crickets/skills/memory/scripts/reflect.py \
  ~/.claude/projects/<repo>/<session-id>.jsonl \
  --summary
```

Output: one JSON record per line (`{"pass": "memory", "category": ..., "confidence": ..., "slug": ..., ...}` or `{"pass": "idea", ...}`). The Stop-event hook (task 3) + idle-time hook (task 4) invoke this script — the skill body's in-session path is for interactive review surfaces where agent semantic judgment adds value beyond regex.

#### Failure modes (graceful)

- **Transcript not found** → halt step 1 with clear next-step ("set --session to a valid path, or run inside a Claude Code session for default-path resolution").
- **Malformed JSONL lines in transcript** (Claude Code crashed mid-write, partial JSON line) → skipped silently; mining continues on the remaining lines.
- **Empty transcript** → exit 0 with `Reflection complete. Mined 0 memory candidates + 0 idea candidates.` summary; no-op.
- **No candidates found** → emit summary line with all-zero counts; no per-candidate output; exit 0.
- **`reflect.py` not installed at expected path** (agent has Bash but the script is missing) → fall back to the in-context agent-driven flow above. The skill body is self-contained; the Python script is an optimization.

#### Anti-patterns

- **Don't auto-save HIGH-confidence candidates without showing them first.** Even HIGH-confidence patterns can be false positives (e.g., user said "always X" but meant only in this specific context). Display before saving.
- **Don't save candidates to `_inbox/` without the rationale + excerpts.** The whole point of `_inbox/` is batch triage later; without the supporting context the operator can't decide.
- **Don't dedup aggressively across reflections.** If the same pattern appears in two sessions, count it as 2 occurrences (not 1 with `seen=true`). The aggregate-occurrences count drives confidence routing.
- **Don't write to the idea-ledger directly from this skill.** Idea-candidate persistence is plan #7a part 4's scope. This skill emits candidates as in-memory objects + lets the future `/memory ideas` skill subscribe.

> [!NOTE]
> **Tri-modal routing implementation status**: plan #7a part 3 task 5 wires the actual HIGH→auto-save / MEDIUM→interactive-review / LOW→_inbox/ branches. Tasks 1-2 (this commit) ship the mining + skill body; tasks 3-4 add Stop + idle triggers; task 5 closes the routing loop; task 6 adds crash-recovery markers; task 7 documents the full surface.

### `/memory promote`

Graduates an `_idea-incubator/<slug>/` entry to a real project at `projects/<slug>/` + annotates the corresponding `Ideas.md` section + recalculates vec-index entries. Plan #7a part 4 ships this body + the canonical Python implementation at `skills/memory/scripts/ideas_promote.py`.

#### Invocation shape

```
/memory promote idea <slug> [--ideas-path <path>] [--mode <silent|interactive|auto>]
```

| Arg | Required | Default | Meaning |
|---|---|---|---|
| `<slug>` | yes | — | Existing incubator slug under `personal-private/_idea-incubator/<slug>/`. |
| `--ideas-path <path>` | no | `$IDEAS_SURFACE_PATH` env or `~/Obsidian/Ideas.md` | Override Ideas.md location. |
| `--mode <m>` | no | `interactive` (or `$MEMORY_REVIEW_MODE`) | Permeable-boundary mode for the Ideas.md annotation write. `silent` pre-approves; explicit user-typed promotion typically passes `silent` since the user already requested the operation. |

#### Step-by-step flow

**Step 1 — Resolve vault path** via the chain `--vault-path` arg → `MEMORY_VAULT_PATH` env. Halt with clear next-step on failure.

**Step 2 — Verify incubator entry exists** at `<vault>/personal-private/_idea-incubator/<slug>/`. If missing, halt with `"incubator entry not found: <path> (check slug; list with ls _idea-incubator/)"`. If a `projects/<slug>/` already exists, halt to avoid clobber — operator picks a new slug or removes the existing.

**Step 3 — Move the directory.** `shutil.move(_idea-incubator/<slug>, projects/<slug>)`. Cross-filesystem-safe (uses copy + delete fallback). Atomic at the OS level for same-FS moves.

**Step 4 — Recalculate vec-index entries.** For each `.md` file under the new `projects/<slug>/` location: enqueue `op: delete` for the old path (`personal-private/_idea-incubator/<slug>/...`) + `op: upsert` for the new path with re-embedded text. Operator runs `python3 vec_index.py drain --vault-path <vault>` (or future idle-hook drain) to actually process. Graceful-skip if `vec_index` module missing.

**Step 5 — Annotate Ideas.md section.** Find the section whose wikilink references `_idea-incubator/<slug>/_index.md`; append `→ promoted YYYY-MM-DD to personal-private/projects/<slug>/` annotation right after the wikilink line. **Permeable-boundary check fires here** (Ideas.md is outside MemoryVault — the A3 helper `confirm_write_outside_memoryvault()` confirms via `--mode` resolution). If denied, the move + vec-index recalc already happened — the operator can manually annotate; the return value indicates "ideas_annotation: denied".

**Step 6 — Return confirmation.** Display:

```
Promoted <slug> to personal-private/projects/<slug>/
  incubator_dir → moved
  vec_index: <stats>
  ideas_annotation: written | denied | section_not_found
```

If section_not_found: the operator can manually annotate (the auto-search assumed the wikilink format from the original `_idea-incubator/<slug>/_index.md` reference; if the operator edited the section format, the regex won't find it).

#### `/memory promote gc` — garbage collection

Variant subcommand for the 6-month GC sweep:

```
python3 ~/Antigravity/crickets/skills/memory/scripts/ideas_promote.py gc \
  --vault-path <vault> [--gc-months 6]
```

Walks `_idea-incubator/<slug>/` dirs, computes age from `_index.md` `updated:` frontmatter (falls back to file mtime if absent). Entries older than `gc_months × 30` days get an interactive **Keep / Archive / Delete** prompt:

```
────────────────────────────────────────────────────────────────────────
Incubator entry idle: <slug> (<N> days since last update)
────────────────────────────────────────────────────────────────────────
Action: [k]eep (defer) / [a]rchive / [d]elete (default: k):
```

- **Keep**: `_index.md` mtime touched (entry exits the GC window; re-evaluated in 6 months).
- **Archive**: moves to `_idea-incubator/_archive/<slug>/` (preserves history, excludes from active recall).
- **Delete**: `rm -rf` the dir (irreversible — vec-index entries pointing at deleted paths become orphans on next drain).
- **Default (non-TTY or empty input)**: Keep — locked design call B1.i is *never silent deletion*; without operator confirmation the entry stays.

#### Failure modes (graceful)

- **Slug not found** → halt step 2 with the actual path that was checked.
- **Target collision** (`projects/<slug>/` exists) → halt with operator next-step.
- **Cross-filesystem move** → falls back to copy+delete via shutil.move; slow but correct.
- **vec-index unavailable** → recalc step is no-op + returns `{"skipped": -1}` stat; operator runs reindex later.
- **Ideas.md missing** → ideas_annotation = "no_ideas_file"; promotion otherwise succeeds.
- **A3 boundary denied** for Ideas.md write → ideas_annotation = "denied"; promotion otherwise succeeds (operator can manually annotate).

#### Anti-patterns

- **Don't pick the same slug as an existing projects/<slug>/.** Pre-check would help here but the operator typed the slug; we halt rather than guess.
- **Don't run GC in batch / non-interactive contexts without `--mode silent`.** Default GC behavior defaults every prompt to Keep when stdin isn't a TTY, which is correct (never silent deletion), but means non-TTY runs do nothing. For batch GC with explicit pre-approval, the operator runs the gc subcommand interactively.

### `/memory index-skills`

Walks `SKILL.md` files across configured source paths (`crickets/skills/`, `agentm/.claude/skills/`, any extra repos the operator adds) and writes one `kind: skill-pointer` entry per skill to `MemoryVault/personal-skills/<repo>/<skill-name>.md`. The agent then picks these up via the normal recall hooks — surfacing *"we have a `/design author` skill"* without the operator re-mentioning it. Plan #7b task 1 ships this body + the canonical Python implementation at `skills/memory/scripts/index_skills.py`.

#### Invocation shape

```
/memory index-skills [--skill-path <dir>...] [--vault-path <path>] [--repo-name <slug>]
```

| Arg | Required | Default | Meaning |
|---|---|---|---|
| `--skill-path <dir>` | yes¹ | — | Skill source directory to walk. Repeatable. ¹Required unless `MEMORY_SKILL_PATHS` env var (colon-separated) supplies at least one path. |
| `--vault-path <path>` | no | `$MEMORY_VAULT_PATH` env | MemoryVault root. Halts if neither arg nor env resolves. |
| `--repo-name <slug>` | no | auto-detected | Explicit repo-slug for ALL discovered skills. Overrides the auto-detection walk (which finds the first ancestor containing `.git/` or `AGENTS.md`). Useful when sources don't sit under a git repo. Kebab-normalized regardless of input. |

#### Step-by-step flow

**Step 1 — Resolve vault path** via `--vault-path` arg → `MEMORY_VAULT_PATH` env. Halt with clear next-step on failure.

**Step 2 — Resolve skill paths** via `--skill-path` args + `MEMORY_SKILL_PATHS` env (colon-separated, deduplicated). Halt with `"no skill paths configured"` if neither produced any path.

**Step 3 — Discover SKILL.md files.** For each skill path: accept either `<root>/SKILL.md` (root is a skill dir) or `<root>/<skill-name>/SKILL.md` (canonical toolkit layout). Recursion depth is **exactly one level** — `SKILL.md` is a top-of-skill-dir convention, not a free-floating marker; deeper recursion would surface false positives.

**Step 4 — For each SKILL.md, parse frontmatter** (locked fields: `name`, `description`, `version`, `supported_hosts`). Halt the single skill (not the whole run) if `name` is missing or non-kebab.

**Step 5 — Extract a body summary.** First paragraph after the H1 (skipping leading blanks); falls back to empty if no body paragraph. Capped at 600 chars to keep pointer entries small.

**Step 6 — Resolve repo-name.** Either: (a) explicit `--repo-name` (normalized to kebab); (b) walk up from the SKILL.md path until an ancestor with `.git/` or `AGENTS.md` matches → use that basename, normalized to kebab; (c) `unknown-repo` fallback.

**Step 7 — Idempotency check.** Target path is `<vault>/personal-skills/<repo>/<skill-name>.md`. If it already exists AND its `skill_version` + description body match the current SKILL.md → record as `skipped`. Otherwise → proceed to write.

**Step 8 — Write the entry.** Locked frontmatter shape:

```yaml
---
kind: skill-pointer
status: active
created: <today UTC>
updated: <today UTC>
tags: [skill, personal-skills, auto-indexed]
group: personal-skills/<repo>
slug: <skill-name>
always_load: false
source_path: <absolute path to source SKILL.md>
source_repo: <repo>
skill_version: <version from source frontmatter>
last_indexed: <today UTC>
---
```

Body shape: title `# <skill-name> (skill pointer)` + an "auto-indexed; do not edit by hand" note + a metadata block (repo / version / hosts) + the description from frontmatter + the extracted summary.

**Step 9 — Enqueue vec-index upsert** with embed text `<slug> skill (from <repo>)\n\n<description>\n\n<summary[:300]>`. Graceful-skip on any enqueue failure — the file write is the contract; embedding is best-effort.

#### Failure modes (graceful)

- **No `--skill-path` + no env** → exit 1 with the actionable next-step.
- **Vault path unresolved or not a directory** → exit 1.
- **A single SKILL.md fails to parse** → that skill is recorded as `action: error` in the per-skill results dict, but the rest of the run continues. Overall exit is 2 if any skill errored, 0 otherwise.
- **Auto-detected repo name has non-kebab characters** → normalized via lowercasing + non-alnum → `-`. `My_Cool-Repo` → `my-cool-repo`.

#### Output shape

The CLI prints a JSON summary to stdout (suitable for piping into `jq`):

```json
{
  "written": 3,
  "skipped": 5,
  "errors": 0,
  "results": [
    {"action": "written", "target": "...", "repo": "crickets", "skill": "memory", "reason": ""},
    {"action": "skipped", "target": "...", "repo": "crickets", "skill": "design", "reason": "unchanged (same skill_version + description)"}
  ]
}
```

#### Anti-patterns

- **Don't hand-edit `personal-skills/<repo>/<skill>.md` entries** — the next index run overwrites your changes if `skill_version` or description shifted. Hand-curate at the source SKILL.md instead.
- **Don't add personal-skills entries via `/memory save`** — the layout is reserved for the indexer's output. Operator-curated skill notes go in `personal-private/` with a `[[skill-pointer:<skill>]]` cross-link to the auto-indexed pointer.
- **Don't use `/memory evolve` against an auto-indexed entry** — evolve is for human-curated entries. The indexer is the source of truth for skill-pointer entries; just re-run `/memory index-skills` after a SKILL.md change.

### `/memory discover-skills`

Internet skill-discovery scan (plan #7b task 3). Fetches a curated set of "skill-shaped pattern" sources from the internet on a configurable cadence; caches each fetch as a dated snapshot; diffs against the previous snapshot; emits "new content since last scan" candidate signals for the adapt-don't-import workflow (task 4) to evaluate. **Never writes to `crickets/skills/`** — adoption decisions are gated by the watchlist review pattern in task 5.

Auto-fires from the idle-time hook (`memory-reflect-idle`) with `--cadence-check` so the scan self-throttles to the configured cadence (default 7 days). Manual invocation is also supported.

#### Invocation shape

```
python3 ~/Antigravity/crickets/skills/memory/scripts/discover_skills.py \
  [--vault-path <path>] [--cadence-days N] [--cadence-check] \
  [--dry-run] [--max-sources N]
```

| Arg | Required | Default | Meaning |
|---|---|---|---|
| `--vault-path <path>` | yes¹ | `$MEMORY_VAULT_PATH` env | MemoryVault root — whitelist + cache + state land here. ¹Required via flag or env. |
| `--cadence-days N` | no | `7` (or `$MEMORY_SKILL_DISCOVERY_CADENCE_DAYS`) | Minimum days between scans. Used with `--cadence-check`. |
| `--cadence-check` | no | off | Skip the fetch entirely if `last_scan` was within the cadence window. Used by the idle-hook to avoid hammering URLs on every idle fire. |
| `--dry-run` | no | off | List sources that would be scanned without actually fetching. |
| `--max-sources N` | no | unlimited | Limit to first N sources from the whitelist (scout / testing mode). |

#### Source whitelist

Lives at `<vault>/personal-private/skill-discovery-sources.md` — operator-editable markdown. Format: `#`-prefixed comment lines, blank lines ignored, one URL per non-comment line. **Order matters** (sources scanned top-to-bottom; `--max-sources` truncates against this order).

First-ever scan auto-seeds the file with the operator's confirmed v1 set in exactly this order:

1. **Anthropic Cookbook** — `https://raw.githubusercontent.com/anthropics/anthropic-cookbook/main/README.md`
2. **awesome-claude-code** — `https://raw.githubusercontent.com/hesreallyhim/awesome-claude-code/main/README.md`
3. **awesome-mcp-servers** — `https://raw.githubusercontent.com/punkpeye/awesome-mcp-servers/main/README.md`
4. **awesome-llm-apps** — `https://raw.githubusercontent.com/Shubhamsaboo/awesome-llm-apps/main/README.md`

Operator edits the file in Obsidian to add, remove, or reorder sources. Auto-seed only happens once; subsequent runs read the file as-is.

#### Cache layout

Per-source cache lives under `<vault>/_meta/skill-discovery-cache/`:

```
<vault>/_meta/skill-discovery-cache/
├── state.json                                # last_scan + per-source last_fetch/status
├── <source-slug>/
│   ├── 2026-05-21.md                        # full snapshot of the URL response on this date
│   ├── diff-2026-05-21.md                   # added lines vs. previous snapshot (task 4 consumes this)
│   └── ...
```

`<source-slug>` is derived from the URL: `<owner>-<repo>` for GitHub raw URLs (kebab-normalized), or `url-<8-char-hex>` for non-GitHub sources.

#### Step-by-step flow

**Step 1 — Resolve vault path** via `--vault-path` arg → `MEMORY_VAULT_PATH` env. Halt with clear error on failure.

**Step 2 — Resolve cadence** via `--cadence-days` arg → `$MEMORY_SKILL_DISCOVERY_CADENCE_DAYS` env → default 7.

**Step 3 — Load (or auto-seed) the source whitelist.** If `<vault>/personal-private/skill-discovery-sources.md` is missing, write the default 4-URL seed (operator-confirmed v1 order) + log `whitelist_seeded: true` in the summary. Otherwise read URLs in file order.

**Step 4 — Cadence check** (when `--cadence-check` is set). Load `state.json`; if `last_scan` is within `cadence_days × 86400 seconds`, skip the fetch entirely + return `cadence_skipped: true` in summary.

**Step 5 — Per-source fetch loop.** For each URL (in whitelist file order, truncated to `--max-sources` if given):

  a. `urllib.request` GET with 10s timeout + `User-Agent: crickets-skill-discovery/0.1`.
  b. On 200: write `<source-slug>/<YYYY-MM-DD>.md` snapshot; compute diff against most-recent prior-day snapshot; write `diff-<YYYY-MM-DD>.md` if diff non-empty; update state.json with `{url, last_fetch, last_status: 200, last_snapshot, last_diff_chars}`.
  c. On 4xx/5xx/timeout/DNS-failure: log to state.json (`{last_attempt, last_status, last_error}`) but don't overwrite snapshot/diff. Continue to next source.
  d. First-ever fetch of a source: prev snapshot doesn't exist, so diff = full content (everything is "new").

**Step 6 — Save state + return summary.** Atomic state.json write (tempfile + rename). Summary JSON to stdout: `{vault, whitelist, whitelist_seeded, total_sources, cadence_days, dry_run, cadence_skipped, sources: [...], fetched, errors, skipped_dry_run}`.

#### Idle-hook integration

`memory-reflect-idle` (the existing orphan-recovery hook from plan #7a part 3 task 4) extends to call `discover_skills.py --cadence-check` at the end of its run. Graceful-skip when `MEMORY_VAULT_PATH` is unset or `discover_skills.py` is absent. The cadence-check means the hook can fire frequently (every SessionStart) without hammering URLs.

#### Failure modes (graceful)

- **Vault path unresolved** → exit 1 with actionable error.
- **Whitelist file missing** → auto-seed with v1 defaults; first scan proceeds with the seeded URLs.
- **Whitelist empty (all comments)** → `total_sources: 0`; no fetches; no state update.
- **Network failure (timeout / DNS / 5xx)** → per-source recorded as `action: error`; other sources continue; summary exit code 2 (any errors).
- **4xx HTTP** → per-source `action: error` with `status_code: <4xx>`; no snapshot overwrite.
- **State.json corrupted** → silently reset to empty state (next save overwrites with clean JSON).
- **Same-day re-run** → snapshot for today gets overwritten; diff compares to most-recent **prior-day** snapshot (excluding today). Same-day re-runs without a prior-day baseline treat everything as new.

#### Anti-patterns

- **Don't run `discover-skills` without `--cadence-check` in idle-hook contexts.** Hooks fire frequently; without the cadence guard the same URLs get fetched many times per day.
- **Don't add to the whitelist URLs that aren't the markdown content you want to scan.** The fetcher does no HTML parsing — it expects markdown-shaped sources (READMEs, awesome-lists, raw `.md` files). HTML pages get cached as raw HTML; the diff still works but downstream consumers (task 4) assume markdown content.
- **Don't shipping-pipe `discover-skills` output to `/memory save`.** The cache + diff are intermediate artifacts — task 4 (adapt-don't-import) is the authorized consumer; auto-saving the raw diffs would defeat the whole adapt-don't-import design.

> [!NOTE]
> **Adapt-don't-import workflow status**: plan #7b task 4 (this sibling sub-command `/memory adapt-skills`) wires the actual evaluation of these cached diffs into `_skill-watchlist/` entries; task 5 ships the `/memory watchlist` review command.

### `/memory adapt-skills`

Adapt-don't-import workflow (plan #7b task 4). **Two-pass architecture**: Pass 1 (deterministic Python) walks the diff files from `/memory discover-skills`, parses candidate patterns, applies a 6-rule rubric, enriches with GitHub metadata + trustworthiness signals; Pass 2 (LLM sub-agent — `adapt-evaluator`) reads each enriched candidate + cross-references the operator's vault + renders the final HIGH/MEDIUM/LOW judgment + writes the watchlist entry. **Never forks** into `crickets/skills/`. Operator reviews via `/memory watchlist` (task 5).

#### Two-pass invocation shape

**Pass 1 — Python pipeline** (deterministic; safe to run repeatedly):

```
python3 ~/Antigravity/crickets/skills/memory/scripts/adapt_skills.py \
  [--vault-path <path>] [--source <slug>] [--skip-network] [--dry-run]
```

| Arg | Required | Default | Meaning |
|---|---|---|---|
| `--vault-path <path>` | yes¹ | `$MEMORY_VAULT_PATH` env | MemoryVault root. ¹Required via flag or env. |
| `--source <slug>` | no | all sources | Limit Pass 1 to a single source-slug (e.g. `anthropics-anthropic-cookbook`). |
| `--skip-network` | no | off | Skip GitHub API enrichment (offline / rate-limited contexts). |
| `--dry-run` | no | off | Evaluate without writing JSON files or updating state. |

**Pass 2 — sub-agent dispatch** (one-shot judgment per candidate):

The Claude Code agent following this skill body dispatches the `adapt-evaluator` sub-agent with the caller-supplies-inline-rubric prompt documented in [`agents/adapt-evaluator.md`](../../agents/adapt-evaluator.md). The sub-agent walks each enriched JSON, judges, and writes the watchlist entry. Operator-side, both passes typically chain: `adapt-skills.py` then sub-agent dispatch.

#### Pass 1: 6-rule rubric

Cumulative scoring against each candidate's text:

| Rule | Signal | Score |
|---|---|---|
| **R1** | Names a tool/skill the operator doesn't already have (cross-checks `personal-skills/` index) | +1 |
| **R2** | Keyword-matches existing `_always-load/` content (complements a convention) | +1 |
| **R3** | Section context is agent-building / dev-env / meta-improvement | +1 |
| **R4** | Names an MCP server / hook / skill primitive (high-relevance for this dev-env) | +1 |
| **R5** | Flagged as experimental / WIP / hack / deprecated / abandoned | –1 |
| **R6** | Cross-vendor proprietary (cursor / windsurf / codex / etc.) | –2 |

Thresholds: **3+ = HIGH** (passed to Pass 2 for judgment), **1-2 = MEDIUM** (also passed), **≤0 = LOW** (dropped before Pass 2 unless `--include-low` future flag added).

#### Pass 1: enrichment payload

For each candidate that clears the rubric, Pass 1 enriches with:

1. **GitHub metadata** (unauthenticated API; graceful-skip on rate-limit or no-github-link):
   - `github_owner`, `github_repo`, `github_stars`, `github_archived`, `github_last_commit_iso`, `github_license` (SPDX), `github_html_url`
2. **Trustworthiness signals**:
   - `from_trusted_org`: matches against operator-editable whitelist at `<vault>/personal-private/trusted-sources.md` (auto-seeded with curated defaults: anthropics / google / microsoft / hashicorp / etc.)
   - `cross_citation_count`: how many of the 4 discovery sources reference this candidate (independent-validation signal)
   - `high_stars` (≥500) / `low_stars` (<50) / `archived_warning` / `activity_recent` (committed in last 365d) / `permissive_license` (MIT / Apache-2.0 / BSD / ISC / MPL)
3. **Rubric verdict**: `rubric_score`, `rubric_rules_fired`, `rubric_confidence`

Output: one JSON per candidate at `<vault>/_meta/skill-discovery-cache/adapt-state/<source-slug>/<pattern-slug>.json`. State file at `adapt-state/evaluated.json` tracks (source, pattern, diff-date) tuples for idempotent re-runs.

#### Pass 2: sub-agent judgment

Caller dispatches `adapt-evaluator` (see [`agents/adapt-evaluator.md`](../../agents/adapt-evaluator.md)). The sub-agent:

1. **Reads** each enriched candidate JSON.
2. **Cross-references** the operator's vault (`personal-skills/` / `personal-private/_always-load/` / `projects/<repo>/conventions.md`) for fit.
3. **Classifies** with semantic judgment (HIGH / MEDIUM / LOW) — overrides Pass 1's rubric verdict when context warrants.
4. **Writes** the watchlist entry to `<vault>/personal-private/_skill-watchlist/<source-slug>/<pattern-slug>.md` (HIGH + MEDIUM only; LOW dropped silently).

Watchlist entry shape locked in [`agents/adapt-evaluator.md`](../../agents/adapt-evaluator.md) under "Watchlist entry shape".

#### Trusted-sources whitelist

`<vault>/personal-private/trusted-sources.md` — operator-editable in Obsidian. Auto-seeds on first Pass 1 run with: anthropics, anthropic, google, googleworkspace, googlecloudplatform, microsoft, vercel, hashicorp, openai, cloudflare, github, supabase, redis, kubernetes, docker, pytorch, huggingface, modelcontextprotocol. Operator edits freely; one org-slug per non-comment line; case-insensitive match against GitHub URL owner.

#### Failure modes (graceful)

- **No diff files in cache** → `diff_files_scanned: 0`; exit 0; no-op.
- **All candidates already evaluated** → `written_count: 0` + `skipped_count: N`; exit 0.
- **GitHub API rate-limited or down** → per-candidate `github_*` fields = null; rubric + trustworthiness signals still compute; sub-agent has reduced enrichment but still functions.
- **Diff file unreadable** → `errors += 1`; other candidates continue.
- **Sub-agent dispatch fails** (e.g. Claude Code unavailable) → enriched JSONs stay on disk; operator can re-dispatch later.

#### Anti-patterns

- **Don't run Pass 2 (sub-agent) without first inspecting Pass 1's JSON output** in unfamiliar territory. The JSON is the operator's verification surface for "is the rubric scoring sensibly?" — if R1/R5/R6 are firing on the wrong candidates, tune the rule constants in `adapt_skills.py` before paying the LLM cost.
- **Don't promote a watchlist entry directly to `crickets/skills/`.** Use `/memory watchlist promote` (task 5) → operator decides; the workflow's whole point is that adoption is operator-explicit, not agent-driven.
- **Don't bypass the rubric** by setting `--include-low` or hand-editing the evaluated.json state — the rubric is the deterministic gate that bounds the surface the sub-agent has to judge. Bypassing it makes Pass 2 expensive without value.

> [!NOTE]
> **Sub-agent budget**: Pass 2 has no hard token cap (operator dispatch is one-shot, bounded by operator attention). For batch dispatch (idle-hook in a future task), a `--limit N` flag caps how many candidates each idle pass evaluates — default 5.

### `/memory watchlist`

Review pending entries in `<vault>/personal-private/_skill-watchlist/` — the output of `/memory adapt-skills`. Three actions per entry: **promote** (mark ready for operator's manual fork to `crickets/skills/<x>/`), **dismiss** (archive to `_skill-watchlist/_archive/`), **defer** (snooze with a `deferred_until` date). Plan #7b task 5 ships the body + the canonical Python implementation at `skills/memory/scripts/watchlist_review.py`.

**Adapt-don't-import contract enforcement**: this sub-command **never writes** to `crickets/skills/<x>/`. Promote is annotation-only — it marks the entry `status: promoted` + adds a `promoted_at` timestamp; the operator then manually authors the actual skill in a separate session. Adoption-by-agent is architecturally prevented.

#### Invocation shape

```
python3 ~/Antigravity/crickets/skills/memory/scripts/watchlist_review.py \
  [list | review | promote <source-slug> <pattern-slug> | \
   dismiss <source-slug> <pattern-slug> | \
   defer <source-slug> <pattern-slug> --until YYYY-MM-DD [--reason "<text>"]] \
  [--vault-path <path>]
```

| Sub-command | Use case |
|---|---|
| `list` | JSON list of pending entries (source-slug + pattern-slug + status + classification). Useful for piping into other tools or just eyeballing the backlog. |
| `review` (default) | Interactive walk-through — prompts per entry with `[p]romote / [d]ismiss / [f]efer / (default skip)`. Non-TTY stdin defaults all prompts to skip (never silent action — same contract as `ideas_promote.py gc`). |
| `promote <source> <pattern>` | Mark a specific entry promoted. Operator-typed slugs (autocomplete via `list` first). |
| `dismiss <source> <pattern>` | Archive a specific entry to `_archive/`. |
| `defer <source> <pattern>` | Snooze with `--until YYYY-MM-DD` + optional `--reason`. |

#### Action semantics (locked)

- **promote** → frontmatter `status: promoted` + `promoted_at: <iso ts>` + `updated: <today>`; removes `deferred_until` / `defer_reason` / `dismissed_at` if present. Entry stays in place — the operator's manual fork happens outside this script.
- **dismiss** → frontmatter annotated with `status: dismissed` + `dismissed_at: <iso ts>`; then **moved** to `<vault>/personal-private/_skill-watchlist/_archive/<source-slug>/<pattern-slug>.md` (collision-safe `-N` suffix if needed). Preserves the audit trail; future passes can re-surface via direct file access.
- **defer** → frontmatter `status: deferred` + `deferred_until: <iso date>` + optional `defer_reason`; removes `dismissed_at` / `promoted_at`. Entry stays in place; future list operations can filter `deferred_until` to surface only re-eligible entries.
- **skip** (default for non-TTY + unrecognized input) → no change; entry stays in pending-review state for next pass.

#### Interactive flow

```
────────────────────────────────────────────────────────────────────────
Watchlist entry: anthropics-anthropic-cookbook/some-pattern
  status:        pending-review
  classification: HIGH
  source_url:    https://github.com/anthropics/some-pattern
  github_stars:  1247
  trusted_org:   true
  rubric_score:  4
────────────────────────────────────────────────────────────────────────
Action: [p]romote / [d]ismiss / [f] defer (default: skip)
```

On `f` (defer): a secondary prompt asks for `defer until (YYYY-MM-DD; blank = default <today+30d>)`. Blank input or invalid date falls back to the 30-day default.

#### Failure modes (graceful)

- **No entries in `_skill-watchlist/`** → `[watchlist] no pending entries` to stderr; exit 0.
- **Non-TTY stdin** → defaults every prompt to skip; emits `interactive mode requested but stdin is not a TTY; defaulting all prompts to skip (never silent action)`; exit 0.
- **Entry not found** for promote/dismiss/defer specific-slug commands → exit 1 with the actual path that was checked.
- **Invalid `--until` date** for defer → exit 1 with `--until must be ISO date YYYY-MM-DD`.
- **Archive collision** on dismiss → file goes to `<pattern-slug>-1.md`, `-2.md`, etc.

#### Anti-patterns

- **Don't run `review` in batch / non-interactive contexts.** Default-to-skip is the safety net; if you actually want batch action, use the specific-slug subcommands (`promote` / `dismiss` / `defer`) which are deterministic + scriptable.
- **Don't auto-promote based on rubric_score alone.** The whole point of the watchlist is the operator's judgment on top of the rubric — auto-promotion bypasses the adapt-don't-import architectural guarantee.
- **Don't `rm -rf` the `_archive/` directory.** It's the audit trail for "we considered this and dismissed it" — useful when the same pattern resurfaces from a different source later (cross-citation count goes up).

### `/memory inbox`

Bulk-triage `<vault>/personal/_inbox/` — the long-promised follow-up named as far back as the original MemoryVault design docs, never built until now. Reflection routes MEDIUM/LOW-confidence candidates here and stops; nothing moves them out of `status: inbox` on its own. `/memory inbox --bulk-review` scans every still-untriaged entry and proposes exactly one of three dispositions per entry, canonical implementation at `skills/memory/scripts/inbox_triage.py`:

- **promote** — an entry reinforced by repeated occurrence (`mining_occurrences` at or above a threshold) or a real content match across 3+ inbox entries graduates to the same canonical destination reflect.py's own HIGH-confidence path writes to (`<vault>/personal/<kind>/<slug>.md`).
- **merge** — a near-duplicate PAIR (similarity above threshold, not part of a larger reinforcing cluster) is proposed for merge — reuses `dream.py`'s own `difflib`-based dedup stage against the inbox pool, not a second similarity implementation.
- **expire** — stale and unreinforced past a TTL (default 90 days since the entry's own `created` timestamp) is proposed for in-place archival (`status: expired` — never a physical delete or move).

Every proposal stages through the exact `_dream-staging/<run_id>/` contract dreaming already built (`dream_confirm.py`'s `list_pending` / `confirm` / `auto_apply_batch` run unmodified against an inbox-triage run).

**Auto-apply is now the default (operator ruling, 2026-07-11, second pass).** The first-ever run shipped confirm-gated: every disposition proposed against the pre-existing backlog — 1,565 notes across 635 proposals — stayed pending for an explicit human confirm, regardless of kind. The operator personally reviewed and confirmed that entire first-run backlog with zero errors, then directed that confirm-gating retire going forward. **Every disposition — promote, merge, and expire alike — now auto-applies by default**, regardless of whether the source entry predates or postdates the first-run **cutover marker** (`_meta/inbox-triage-cutover.json`, stamped once, never overwritten). The cutover marker still exists and still labels which era an expire proposal's source entry belongs to (`inbox_expire` vs `inbox_expire_backlog`), but that label is now informational only — it no longer gates whether a proposal applies. Pass `--no-auto-apply` to fall back to propose-only (nothing applies until an explicit `--confirm`).

#### Invocation shape

```
python3 ~/Antigravity/crickets/skills/memory/scripts/inbox_triage.py \
  --vault-path <path> \
  [--bulk-review | --list | --confirm <index> --run-id <id> | --reject <index> --run-id <id>] \
  [--run-id <id>] [--non-interactive] [--no-auto-apply] \
  [--batch-cap <n>] [--expire-ttl-days <n>] [--promote-occurrence-threshold <n>]
```

| Flag | Use case |
|---|---|
| `--bulk-review` (default) | Scan (+ auto-apply every eligible proposal — promote/merge/expire alike), then interactively review whatever's still pending (only ever the batch-cap remainder) — `[c]onfirm / [r]eject / [s]kip` per proposal. Non-TTY stdin defaults every prompt to skip (never silent action — same contract `watchlist_review.py` already established). |
| `--list` | JSON dump of pending proposals for the most recent run (or `--run-id`) — index/stage/kind/paths/summary/status. |
| `--confirm <index> --run-id <id>` | Confirm one proposal directly (scriptable, no prompt) — for inspecting/applying a specific proposal by hand. |
| `--reject <index> --run-id <id>` | Reject one proposal directly — patches every path it touches `status: triage_rejected`, so a later fresh scan never re-proposes it. |
| `--run-id <id>` (with `--bulk-review`) | Resume reviewing an existing run instead of starting a fresh scan. |
| `--non-interactive` | Scan (+ auto-apply) only; skip the review loop — for hooks/scripting. |
| `--no-auto-apply` | Propose only; skip the confirm-free auto-apply step entirely, for every disposition — everything stays pending for an explicit `--confirm` / interactive review. |

#### Action semantics (locked)

- **confirm** → applies the proposal's mutations through `revert_log.RevertLog.record_and_apply` — never a direct write. Fully undoable via `RevertLog(vault_path).revert(run_id, entry_id)`.
- **reject** → every path the proposal touches gets `status: triage_rejected` + a timestamp, a direct frontmatter patch (matches `watchlist_review.py`'s own promote/dismiss/defer precedent of annotating outside the revert-log). Stops the entry from being re-proposed by a later scan.
- **skip** (default for non-TTY + unrecognized input) → no change; the proposal stays pending for a later review pass.
- Auto-applied proposals — promote, merge, and expire alike — need no operator action at all — they already applied through the identical `record_and_apply` path a manual confirm uses, and are just as revertible. A proposal only stays pending past a run if the run's `--batch-cap` was smaller than the number of eligible proposals, or `--no-auto-apply` was passed.

#### Failure modes (graceful)

- **No pending proposals** → `[inbox-triage] no pending proposals` to stderr; exit 0.
- **Non-TTY stdin** → defaults every prompt to skip; emits `interactive review requested but stdin is not a TTY; defaulting all prompts to skip (never silent action)`; exit 0.
- **Promote collision** (the canonical destination already exists) → the entry is skipped this run, never overwritten — same guard `save.save_entry()`'s own `FileExistsError` enforces, checked up front since the staged mutation bypasses that call.
- **A proposal's source file was already resolved** (a different, overlapping proposal was confirmed first, or the entry was rejected) → silently dropped from the review walk rather than re-offered or erroring.
- **`--confirm` / `--reject` without `--run-id`** → exit 1 with the specific flag that needs it.

#### Anti-patterns

- **Don't raise `--expire-ttl-days` or `--promote-occurrence-threshold` to make a run "quieter."** A large digest over real content is the intended shape when the backlog is large, not a bug.
- **Don't reach for `--no-auto-apply` as the normal mode.** Auto-apply is the default as of 2026-07-11's second ruling; `--no-auto-apply` is an explicit opt-out for someone who specifically wants to inspect proposals before they apply, not the standing posture.
- **Don't run `--bulk-review` in batch / non-interactive contexts expecting to hand-pick dispositions.** It already auto-applies everything eligible; use `--confirm` / `--reject` with an explicit `--run-id` + index only when you want to intervene on one specific proposal.

### `/memory heat-policy`

Evaluates the heat-based always-load curation policy: entries in `_always-load/` with zero recall hits across enough recorded sessions are demotion candidates; entries elsewhere that have gotten hot enough are promotion candidates. Part G of ROADMAP #46, canonical implementation at `skills/memory/scripts/heat_policy.py` (`run_policy()`), exposed as a `recall.py` sub-command — this row closes the gap E6's dashboard inventory found: the verb has existed and worked since Part G shipped, but was never listed here.

#### Invocation

```
python3 harness/skills/memory/scripts/recall.py heat-policy [--apply] [--vault-path <path>]
python3 harness/skills/memory/scripts/recall.py heat-pin <slug> [--vault-path <path>]
```

- **`heat-policy`** — dry-run by default: reports demotion/promotion candidates to stderr without moving anything. Pass `--apply` to actually move files (patches `always_load` frontmatter + relocates the entry). A safety floor (`MIN_ALWAYS_LOAD`) always keeps a minimum number of always-load entries regardless of how many candidates qualify; `heat_pin: true` entries are never demoted.
- **`heat-pin <slug>`** — pins a specific entry to always-load (`heat_pin: true`), restoring it if a prior run had demoted it. Use this for an entry you want kept warm regardless of recall frequency.

#### Failure modes (graceful)

- **Not enough recorded sessions yet** → reports `too_early: true` in the result; no demotions considered until the cold-session floor is met.
- **No vault resolved** → same resolution failure as every other `recall.py` sub-command (`--vault-path` / `MEMORY_VAULT_PATH`).

### `/memory lint`

Reports vault-wide rot the write-time guards can't catch on their own: orphans (notes with zero links in either direction — the same pool `dream.py`'s link-improvement backfill already drains directly, reported here for visibility rather than as a second feed), broken wikilinks, supersede-chain contradictions (a cycle, a fork where two entries both claim to supersede the same note, or a `status: superseded` note with no `supersedes:` backing it anywhere), a kind outside the known-kinds registry, and a per-note quality score (frontmatter completeness + link presence + `lifecycle.compute_decay_score`'s staleness axis). Canonical implementation at `skills/memory/scripts/lint.py` (`run_lint()`), composing `vault_lint.py`'s structural check suite, `graph_snapshot.orphans()`, and a new composite score — auto-organization part 3, task 7.

**One thing on this list gets fixed automatically, everything else is surfaced only.** A mis-cased wikilink (`[[Wrong-Case]]` where `[[Correct-Case]]` is the sole vault-wide case-insensitive match, no alias, no anchor) auto-corrects and revert-logs — a string fact, not a judgment call. An unresolved link with no unique case-insensitive match, a contradiction, an out-of-registry kind, and every orphan stay exactly as reported; deciding what to do about them is an editorial call this engine never makes for you.

`dream.py`'s weekly cycle runs the identical engine automatically via `_stage_lint()`, applying the same auto-repair through the standard revert-logged auto-apply path (`dream_confirm.AUTO_APPLY_STAGES` — `wikilink_repair` mutations only). The plan's own verification requirement holds by construction: `/memory lint` and the weekly stage share one code path, so they can't drift.

#### Invocation

```
python3 harness/skills/memory/scripts/lint.py [--vault-path <path>] [--apply]
```

- **(no flags)** — read-only report to stdout: finding counts by severity, orphan/contradiction counts, the mean quality score, then every finding.
- **`--apply`** — writes the auto-repairable mis-cased-wikilink fixes directly to disk. A CLI-only convenience for inspecting the result immediately; the weekly cycle does NOT use this flag — it applies through `revert_log.record_and_apply` instead, so those repairs stay undoable the same way every other auto-apply stage's mutations are.

#### Failure modes (graceful)

- **No vault resolved** → same resolution failure as every other verb (`--vault-path` / `MEMORY_VAULT_PATH`); exit 2.
- **A scoring failure on one note** (e.g. a decay-score lookup error) → best-effort; that note's freshness axis falls back to `1.0` rather than failing the whole report.

#### Anti-patterns

- **Don't treat a nonzero finding count as a failure to fix immediately.** Most findings (orphans, contradictions, unknown kinds) are informational — the engine surfaces them because an editorial call is needed, not because something is broken.
- **Don't reach for `--apply` as part of automation expecting it to auto-repair everything.** It only ever touches the narrow, deterministic mis-cased-wikilink case; nothing else this verb reports is ever auto-resolved by design.

### `/memory search`

> [!NOTE]
> **Status**: stub. Full body lands in plan #7a **part 2** (`recall-loop`) where the recall engine is built. See `.harness/designs/memoryvault/queued-plans/recall-loop.PLAN.md` for the queued plan.

Manual semantic query against the vault — the read primitive that complements `/memory save`'s write. Useful when the automatic recall (via UserPromptSubmit hook) didn't surface what you wanted, or when you want to inspect what's in the vault without re-loading via a session prompt.

Calls the same recall engine the UserPromptSubmit hook uses: sqlite-vec primary + grep + frontmatter alongside, merge results via the locked rank-merge formula (`sim × 0.7 + keyword × 0.3`), dedup, return top-K (K=5 default).

**Planned invocation shape** (subject to refinement in plan #7a part 2):

```
/memory search <query> [--group <group>] [--include-inbox] [--top-k <N>]
```

Default behavior: query against all groups; exclude `_inbox/`; return top-5 results.

## Concurrent-write safety (operator guidance)

Every `/memory save` + `/memory evolve` write goes through the **vault-write protocol** (V5-0 / [ADR 0012](../../wiki/decisions/0012-vault-write-protocol.md)) so N≥2 agent sessions on one machine can write the shared Drive-synced vault without lost updates or torn files. You don't invoke it — it sits beneath the writes — but three habits keep it effective:

1. **Pin the vault "Available offline."** A dataless read (Drive streaming a not-yet-materialized file) can stall an agent, in the worst case an `EDEADLK` hang. Mark the vault root *Available offline* in Drive/Finder so files are always local.
2. **Don't leave an agent-owned file open and dirty in Obsidian.** Obsidian's auto-merge / "changed on disk" popup is an out-of-band writer the mutex can't see — close `PLAN.md` / `progress.md` before a `/work` run, or let the agent own them while it works.
3. **Name cross-cutting `_inbox/` captures uniquely** as `<timestamp>-<pid>-<slug>.md`, so two sessions writing the inbox at once land disjoint files instead of racing one name (ownership-partitioning — different writers touch different files, so real contention stays ≈0).

Mechanics, for reference: the advisory mutex is a `mkdir` lockdir at `~/.cache/agentm/locks/<sha256(realpath vault)>/lock` — **outside** the synced vault (a lock inside Drive would itself sync/conflict); replace-style shared files re-check a **content hash (sha256) CAS** inside the lock and abort-and-retry on a concurrent edit (including your own Obsidian hand-edits); writes land via temp→`fsync`→rename. Full table + the conflict-janitor's four marker families: [Vault write protocol](../../wiki/reference/Vault-Write-Protocol.md).

## Tool allowlist

**`Read, Write, Edit, Glob, Grep`** — no Bash. Same restriction as `/design` skill. The skill body never invokes shell commands directly; Python scripts under `skills/memory/scripts/` (added in tasks 2-4) handle the heavy lifting (file ops via Python's `os` / `pathlib`; embedding API calls via Python `requests` or vendor SDK; sqlite-vec via the `sqlite-vec` Python wheel).

Python-side scripts can use whatever they need (network for embedding API, filesystem for vec-index, subprocess for `pip install <pkg>` on first invocation if needed) — the allowlist restriction is on the SKILL.md body itself, not the dispatched scripts.

## Host scope

`supported_hosts: [claude-code, antigravity]` — `gemini-cli` excluded per [ROADMAP item #15](https://github.com/alexherrero/agentm/blob/main/.harness/ROADMAP.md) (Gemini-CLI host removal, shipped in toolkit v0.9.0). The memory skill was the first new skill to ship post-#15-decision (in v0.8.x scaffold) with the two-host scope from day 1; v0.9.0 then swept all other customizations to match. See [ADR 0006](../../wiki/explanation/decisions/0006-gemini-cli-host-removal.md) for the host-scope-reduction rationale.

See the [parent design's 2026-05-16 Document History row](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/memoryvault.md#document-history) for the host-scope correction rationale that triggered this.

## Cross-references

- **Parent design**: [MemoryVault — permanent agent memory](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/memoryvault.md) — the canonical "Why we built this" wiki entry point per the locked design call from plan #6
- **Part 1 plan**: `write-primitives` (active at `.harness/PLAN.md`) — ships `save` + `evolve` bodies (tasks 2 + 3) + embedding integration (task 4) + partial how-to (task 5)
- **Queued parts**: `recall-loop` (part 2) / `reflection-and-recovery` (part 3) / `idea-ledger` (part 4) / `seed-pass` (part 5) / `discovery-mining` (part 6 = plan #7b)
- **Conventions**: shipping pattern follows [`design` skill's multi-sub-command body shape](https://github.com/alexherrero/crickets/blob/main/skills/design/SKILL.md) — the first toolkit skill with multiple sub-commands handing off between each other
- **External-review handoff**: applies to design + plan refinement passes; lands via [crickets v0.8.1](https://github.com/alexherrero/crickets/releases/tag/v0.8.1) + [agentm v2.3.1](https://github.com/alexherrero/agentm/releases/tag/v2.3.1) shipped 2026-05-16

## Status

This skill is **stub-shipped** as of v0.9.0 (plan #7a part 1, task 1). All 4 sub-commands have documented shape + planned invocation but no functional implementation yet. The 4 sub-commands fill in across plan #7a tasks 2-5 + the 5 queued parts (recall-loop, reflection-and-recovery, idea-ledger, seed-pass, discovery-mining) which complete the v0.9.0 → v0.9.2 sequence.

Until full bodies land, invoking any of the 4 sub-commands will surface a "Stub — full implementation lands in <task/part X>. See parent design at <link>." message rather than executing the operation.
