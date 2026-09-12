<!-- mode: reference -->
# Memory daemon (`agentmd`) reference

The resident Go process that watches the vault, maintains one FTS5 index, and serves two MCP tools. It is the only thing that runs git against the vault. Search runs lexical-only unless a local embedding model is installed, in which case one supervised child process gives it a second, dense-vector search mode — see [The embedder child](#the-embedder-child).

Source lives in [`daemon/`](https://github.com/alexherrero/agentm/tree/main/daemon). Design: [AgentM Rescope — Storage Topology](agentm-rescope-topology).

## ⚡ Quick reference

| | |
|---|---|
| Binary | `agentmd` — pure Go, builds with `CGO_ENABLED=0`, no cgo |
| Serves | `http://127.0.0.1:7821/mcp` (loopback only; non-local requests get 403) |
| MCP tools | `memory_search`, `memory_capture` |
| Index | one SQLite FTS5 file, outside the vault, deletable and rebuildable |
| Embedder | optional supervised `llama-server` child, EmbeddingGemma-300M — attach or spawn; `--no-embedder` stays lexical-only |
| Vault path | resolved at every start from `plugins.obsidian-vault.vault_path` |

```bash
agentmd serve
```

## Building it

Needs Go on the machine (`brew install go`). There is no vendored binary — the daemon is built from source in this repo:

```bash
cd daemon && CGO_ENABLED=0 go build -o ~/.local/bin/agentmd ./cmd/agentmd
```

`CGO_ENABLED=0` is the point rather than a precaution: it produces a static binary with no system SQLite dependency, which is what lets the same source serve any machine on the home network. Cross-compile by setting `GOOS` and `GOARCH` — `GOOS=linux GOARCH=arm64` builds for a NAS from the laptop.

## Installing it for good

One flag does the build and the launchd agent together, on macOS:

```bash
bash /path/to/agentm/install.sh --daemon
```

It builds `~/.local/bin/agentmd`, writes `~/Library/LaunchAgents/com.agentm.daemon.plist`, loads it, and then **verifies the daemon answers `/health` before returning** — a job launchd accepted and that immediately died on a held port is indistinguishable from a working one in `launchctl list`, which is how the retired daemon stayed "healthy" and wired to nothing for months.

`RunAtLoad` starts it at login and `KeepAlive` restarts it if it dies; `ThrottleInterval` bounds the retry rate so a broken install idles instead of spinning. The vault path is deliberately **not** written into the plist — it is resolved from the kernel config at every start, because a path baked into a plist is a cached literal that goes stale.

**You only need that flag once.** Once the agent exists, every later install or `--update` run rebuilds and reloads the daemon on its own, so refreshing the harness also refreshes the daemon. That matters because the binary is compiled from `daemon/` — without it, pulling new source leaves the old binary resident indefinitely with nothing saying so.

The refresh is deliberately non-fatal. A missing Go toolchain or a failed build prints a warning naming the fix and lets the install finish, because a project install should not die over the daemon, and a broken build must never take down a daemon that is currently working. The build goes to a sibling path and only replaces the live binary once it has succeeded.

The same run also fetches the embedder: `embeddinggemma-300M-Q8_0.gguf` (~330MB, one time) from `ggml-org/embeddinggemma-300M-GGUF`, to a temp path, SHA-256-verified, then moved into place — an install never loads an unverified or half-downloaded model. `llama-server` itself is not built here (it is a cgo project, which the daemon's static-Go constraint exists to avoid); if it is missing from `PATH` (macOS: `brew install llama.cpp`), the fetch still succeeds but every status surface reports the embedder off and searches run lexical-only until it is installed. Pass `--no-embedder` to skip the model fetch on purpose.

Pass `--no-daemon` to skip the refresh for one run; the daemon keeps whatever binary it has. Logs go to `~/Library/Logs/agentm/daemon.log`.

```bash
launchctl bootout gui/$(id -u)/com.agentm.daemon && rm ~/Library/LaunchAgents/com.agentm.daemon.plist
```

`install.sh --mcp-server` is retired and now refuses — it installed the Python FastMCP server, and a second agent on port 7821 would lose a race for the port and retry forever.

## Subcommands

| Command | What it does |
|---|---|
| `serve` | Watch, index, serve MCP, commit. Prints `listening http://…` once the index is caught up. |
| `search <terms>` | One-shot query against the index. `-k`, `-mode`, `-question`, `--after`, `--before`, `--json`. |
| `capture <text>` | One-shot capture. Reads stdin when given no argument. |
| `reindex` | Full reconcile. `--from-scratch` deletes the index first, proving it rebuilds from the files. |
| `status` | Ask a running daemon for its state. Exits 3 when anything is red. `--json` for the raw document. |
| `probe` | Run the round-trip self-probe now. Exits 3 on failure. |
| `gate corpus-write` | Ask whether a corpus-wide write job may start. Exits 0 to pass, 3 to refuse, 1 when it could not decide. |
| `classify` | Rank-penalty class counts over the live vault, printed beside the figures the measurement report established. |
| `rules` | Print the filing contract. `--json` serves it to anything that needs the taxonomy, `--file` parses one specific file, `--init <path>` seeds a vault from the embedded copy without ever overwriting one. |
| `meters` | The four diversity meters over the filed memory corpus. `--sample`, `--trigram-top`, `--window`, `--json`. |
| `graph` | The memory context graph, laid out deterministically and written as SVG. `--cap`, `--out`. |
| `clusters` | Which notes are too similar to be independent memories, and what kind of too-similar. `--threshold`, `--sample`, `--json`. |
| `embed` | Compute the vector arm's embeddings for in-scope notes. |
| `enrich` | Run the enrichment pass over the notes owed one. `--sample N --seed S` draws a reproducible random batch, `--dump` writes before/after pairs, `--yes` runs one batch without needing `daemon.enrich_enabled` set. |
| `ledger` | Ask what dreaming has already done, and what is pending. |
| `queue` | Show the pending-work queues, or record work owed. |
| `sources` | Ask whether a source has been mined, and watermark it. |
| `tiers` | Ask which model tier a dreaming job may run on. `--audit --job summarize --cheap MODEL [--judge MODEL] [--samples N] [--seed S] [--yes]` runs the audit that earns one. |
| `door` | Ask whether a write inside a project needs alignment. |
| `slop` | Score notes for template residue and novelty. `--threshold`, `--json`. |
| `completeness` | Sample enriched notes and split them into claims for grading. `--sample`, `--replicates`, `--json`. |
| `retire` | Stop and archive the orphaned pre-daemon memory server. |

Every subcommand accepts `--config`, `--vault`, `--index`, `--port`.

## The index

One FTS5 table, four columns, porter stemming:

```sql
CREATE VIRTUAL TABLE docs USING fts5(
  path UNINDEXED, title, meta, body, tokenize='porter unicode61');
```

BM25 column weights are `0.0 / 4.0 / 3.0 / 1.0`. `title` is the frontmatter title plus the filename stem with separators spaced out. `meta` carries `aliases` and `tags`. `body` is the frontmatter block followed by the note body, so `type: convention` stays searchable.

Both settings are measured rather than chosen. Porter stemming is worth +5.7 hit@5 and is the only tokenizer knob that moves hit@5 at all; the 4x title weight is worth +3.8 hit@1. The `meta` column measures as a no-op today because only 5.5% of the corpus has anything in it — it exists because dreaming's alias backfill lands next and needs somewhere to land.

The index file is not in the vault. It is a cache, and a database on a synced mount is a known corruption pattern. Delete it and the next start rebuilds it: 8,864 files in about 2.6 seconds, or 39ms for an unchanged corpus.

## The embedder child

Hybrid search is optional and additive: the daemon is still pure Go (`CGO_ENABLED=0`), and everything above works with no model installed at all. When one is, the daemon supervises exactly **one** child process — a `llama-server` running the pinned `embeddinggemma-300M-Q8_0.gguf` (768 dimensions, 2,048-token window) — never two. A cross-encoder reranker was built and bake-off-tested (`daemon/internal/rerank/`), but its rejection floor could not separate true answers from hard negatives on this corpus at any threshold; it is refuted, kept as quarantined research code behind an unpublished flag, and `agentmd serve` never spawns it. See [AgentM Hybrid Retrieval](agentm-hybrid-retrieval) for the measurement.

`agentmd serve` binds the child it starts to a fixed loopback port (`8901`) so a one-shot `agentmd search -mode hybrid` — the prompt-submit hook's own call shape — attaches to that same warm model instead of loading a fresh 330MB copy per query. `--embedder-url` overrides the attach target; `--no-embedder` (also an `install.sh` flag) skips the model download entirely and runs lexical-only, a fully supported configuration that says so on every status surface.

Liveness comes from the work, not from `/health`: a wedged `llama-server` answers `/health` with 200 while failing every real embedding, so three consecutive failed embeddings — not an HTTP code — condemn the child and trigger a restart with exponential backoff. `agentmd status` reports `embedder ok (warm) · <model> · N/M embedded` or `DEGRADED — hybrid off` with the reason; the same detail is on `/status` as `health.embedder`.

Notes longer than the window are split into overlapping chunks (the model's own byte budget, 1/10 overlap) rather than truncated — a note scores by its single best-matching chunk. The vector arm is scoped to `Agent/memory`, `Agent/desk`, `Agent/external`, `Agent/diagnostics` (the diagnostics space joined in filing-v2 part 2a — the digests and scorecards it holds lived under `desk` before the move and were already dense-retrievable), the vault-root `Projects/` (joined in filing-v2 part 2b — the project trees lived under `desk` before that merge and were already dense-retrievable too, so the move must not silently drop them from the vector arm), and the vault-root `Calendar/` (joined in filing-v2 remainders task 3 — its facet notes, day indexes and reviews were lexically indexed from the start, and the dense arm now reaches them too); `_vault-archive/` and the residual `_meta/` are never embedded.

## The rank penalty

Miner fragments are short and quote the operator's own words, so BM25 ranks them above the filed notes that answer the question. Demoting them is worth +3.75 points of R@5 at p = 0.0195, measured over six replicates per arm.

| Class | Weight | Detected by |
|---|---|---|
| `fragment` | 0.30 | body opens with a miner lead-in, `mining_confidence` in frontmatter, or a mid-word slug in a miner-filled directory |
| `fragment-promoted` | *none* | the same shapes, on a note whose status shows filing promoted it |
| `status` | 0.60 | status is `unfiled`, `inbox`, `superseded`, or `expired` |
| `staging` | 0.30 | a dream-staging proposal, which quotes both notes it is about |
| `space` | 0.30 | the note's first path segment is named in the contract's `dampened_spaces` |
| `artifact` | 0.30 | the note says `altitude: artifact` — dampening lifted for a question that asks for that shape |
| `lifecycle-dormant` | 0.30 | frontmatter `lifecycle: dormant` — silent past the axis's `dormant_after_days` |
| `lifecycle-archived` | 0.30 | frontmatter `lifecycle: archived` — also walled out of the default result set (see below) |
| `lifecycle-superseded` | 0.30 | frontmatter `lifecycle: superseded` — the axis is now the only carrier of supersession for a memory; also walled out of the default result set like archived (see below), lifted by `include_archived`, counted in `superseded_hidden` |
| `ingest-staged` | 0.30 | `status: ingest_staged` — a unit the ingest sweep fetched and has not yet promoted; walled out of the default result set like archived and superseded (see below), lifted by `include_archived`, counted in `staged_hidden` (`daemon/internal/note/classify.go:82-92,326-327`) |
| `durable` | *none* | the note never ages: `lifecycle_tier: durable`, `lifecycle: pinned`, `kind: failure-incident`, a `decisions/` path segment, or a contract-exempt space |

Four properties are load-bearing:

- **Strength does not matter.** A 125-point sweep produced four distinct outcomes, and every weight at or below 0.6 ranked identically. There is no tuning knob because there is nothing behind one.
- **Multiply over an over-fetch.** 200 rows are fetched, each score multiplied by the product of its classes' weights, then re-sorted. Re-ranking only the top k cannot promote the note the fragments were hiding.
- **Filing overrides shape.** `fragment-promoted` carries no weight, so a fragment-shaped note that filing promoted keeps its score. That protects 1,288 notes, including 229 of the 232 in `memory/preferences/` — the promotion pipeline promoted their bodies verbatim, so they look mined and are filed.
- **Never exclude.** A penalized note that is the best thing the corpus has still comes back first. Exclusion is what left recall returning nothing for four months.

### The archived, superseded, and staged wall

`lifecycle-archived`, `lifecycle-superseded`, and `ingest-staged` are the three classes that break the "never exclude" rule above, on purpose. An archived, superseded, or staged note leaves the default result set entirely — on disk, in the index, but invisible until you ask for it by name: `include_archived: true` on `memory_search`, `-include-archived` on `agentmd search`. One flag lifts all three walls together (`wallUnserved`, `daemon/internal/index/search.go:884-907`); there is no separate flag for staged units alone. Ask, and each comes back present and still demoted, never restored to parity with an active note. Every search outcome reports `archived_hidden`, `superseded_hidden`, and `staged_hidden`, the counts each wall kept out of that call's results, so an absence is visible rather than inferred.

### Space, altitude, and the two that are not penalties

`space` replaces a directory boundary, and the replacement is the point. Recall used to restrict itself to the memory root, which cured a real leak by amputation: 13% of top-5 results across 20 prompts fell outside `Agent/`, and *"what should I work on next"* returned two Church notes. But a note that cannot be returned at all cannot be returned when it is the only answer — and an invisible space is how this vault lost 9,786 notes once already. Dampening cures the same leak without hiding anything: a strong distinctive match still clears the multiplier, and a weak cosine neighbor does not.

`artifact` separates a note that states something durable from one that records a moment. A convention and a distilled meeting are both `type: workflow` and should not rank alike on a general question. When a question asks for the artifact shape, the dampening is **removed** rather than reversed into a boost. Every multiplier here is at or below 1.0 and the negative-IDF clamp depends on it, so a multiplier above 1.0 on a row whose score went negative would move that row up for being boosted.

The design makes `artifact` the default so `canonical` has to be earned. Capture writes that default on every new note (`DefaultAltitude`, `daemon/internal/capture/capture.go:140,416`) rather than leaving the field absent for this ranker to read as a fallback. The enrichment pass dropped `altitude` from its own response shape entirely (agentm-vault plan 04 — see [Enrichment](#enrichment) below) and no longer touches the field. No note in this corpus carries `altitude` yet, so reading an absent field as `artifact` would multiply all 15,824 rows. For the same clamp reason, that is not the no-op it looks like.

`durable` carries no weight and is not a penalty. It is the record of a decision, read by decay where the weights are not.

### Decay, and why it is off

Age is the one demotion this daemon computes and does not apply. `daemon.decay_enabled` defaults to false.

The curve is real and ported faithfully: full strength through six months of silence, half to a year, an eighth to three years, a sixteenth to five, and the sixteenth is a floor rather than a waypoint. The anchor is a genuine recall from `.lifecycle.json` — in the engine state directory since the memory-root trims (agentm-vault plan 05), with the memory root read as the fallback on a vault that hasn't moved yet (`note.NewAccessLog`'s `dirs` list, opened through `index.OpenWithSidecar` with `cfg.EngineStateDir`) — then `updated:`, then `created:`, then a `captured:` date the note claims itself. A filesystem timestamp never anchors. The type-collapse migration rewrote 9,899 notes' frontmatter in an afternoon, and an mtime-anchored curve would read the migrated corpus as brand new and the files it skipped as uniquely ancient.

What is missing is a corpus that can carry it. 89% of notes are under a month old, the oldest in the memory layer is 93 days against a first band at 182, and exactly five notes of 15,824 cross any band. Scored with decay as the only variable, R@5 fell from 0.781 to 0.750 — two questions lost, none gained.

One of those two is a precondition on ever turning it on: **for a temporal question, age is the signal rather than the noise.** *"When did I switch from Antigravity to Claude?"* is answered by a note written in February, and demoting it for being old is demoting it for being the answer. Nothing here reconciles a curve that ranks by staleness with a question class that ranks by antiquity.

There is no OR query rewrite. It read as the largest available win on one run; replicated six times it is +1.25 points at p = 0.46 and costs 18.8 points of correct rejection, because a search that never returns empty hands the agent five plausible notes and it names one. When a query matches nothing, `memory_search` says so and suggests fewer or different terms instead.

## `memory_search`

| Param | Type | Default | Notes |
|---|---|---|---|
| `query` | `str` | required | Two to five distinctive words. Always drives the lexical arms, regardless of `mode`. |
| `mode` | `str` | `and` | `and` — every term in one note (the original, still-default behavior). `fusion` — best 2-term subset wins, max-score across all subsets. `hybrid` — `fusion` plus a dense-vector arm, combined by reciprocal rank fusion. |
| `question` | `str` | — | The full natural-language question. Read only when `mode` is `hybrid`: the dense arm embeds this instead of `query`. Omit it and hybrid embeds `query` instead — never an error, just a weaker dense arm. |
| `k` | `int` | `5` | Capped at 50. |
| `after` | `str` | — | Capture date on or after. `YYYY-MM-DD` or RFC3339. |
| `before` | `str` | — | Capture date before. |
| `include_archived` | `bool` | `false` | Lifts the archived and superseded wall for this call — the note re-enters the result set, demoted like any other lifecycle class. |

Two more knobs exist in the code and are deliberately not in this table: a `-lex3` flag (widens `fusion`'s subset search from 2-term to 2- and 3-term) and a `rerank` mode (cross-encoder rerank with a score floor). Neither is in `memory_search`'s published schema and neither is requested by the prompt-submit hook — `lex3` missed its own recall floor by two questions, and `rerank` could not separate true answers from hard negatives at any threshold. Both stay in the tree as tested, working code reachable only from `agentmd search` directly: a refuted rung is still worth keeping when it costs nothing in production. See [AgentM Hybrid Retrieval](agentm-hybrid-retrieval).

Returns `{results, note, matched, archived_hidden, superseded_hidden, staged_hidden}`. Each result carries `path`, `score`, `raw_score`, `penalty`, `captured`, `captured_source`, and `snippet`. `score` is the penalized score and larger is better; `raw_score` is the value before demotion, so a penalty is visible rather than inferred from a number moving. `archived_hidden`, `superseded_hidden`, and `staged_hidden` are the counts each wall kept out of this call's results — present even at `0`, so an absence reads as measured rather than assumed.

`note` is set whenever the driver should know something — a rewritten query, or an empty result set.

## `memory_capture`

| Param | Type | Default | Notes |
|---|---|---|---|
| `text` | `str` | required | The fact, in plain prose. One concept per call. |
| `title` | `str` | derived | Weighted 4x in ranking, so a good one is worth writing. |
| `type` | `str` | `preference` | One of `preference`, `workflow`, `idea`, `fix`, `convention`, `reference`. |
| `summary` | `str` | — | One line: what this is and when it applies. Worth writing whenever the body runs past a paragraph. |
| `why` | `str` | — | Why this was kept — what was happening, and what it decides later. Only write it when you actually know; a guessed reason is never invented downstream either. This is also the judgment signal — see `status` below. |
| `importance` | `int` | — | 1-10. Written to both `importance` and `importance_proposed` — an operator's later edit to `importance` alone is what marks the value theirs from then on. |
| `related` | `[str]` | — | Notes this one sits beside, by slug or `[[wikilink]]`. Rendered as a quoted flow list of wikilinks. |
| `project`, `task` | `str` | — | The project slug and the task's verb-slug this was captured under, when the session has one. |
| `tags`, `aliases` | `[str]` | — | Both land in the `meta` column. |
| `source` | `str` | — | The transport the memory arrived by — one of the contract's `sources` vocabulary (`operator-direct`, `conversation`, `external-fetch`, `email`). Sets the trust tier. As of the provenance ruling (2026-09-06) it is transport only; where the material came from goes in `source_id` or `source_url` below. |
| `source_id` | `str` | — | A mined unit's registry identity, when the memory came from one. |
| `source_url` | `str` | — | A fetched page's address, when the memory came from one. |
| `space` | `str` | `memory` | Which configured space to write into. |

`status` is not a published param and cannot be set through this tool. It is derived from what the writer knew: a call that names a `type` and gives a `why` is a card someone judged, and it lands `active`; anything else lands `unfiled`, a candidate the nightly pass judges later (`knowingWriter`, `daemon/internal/capture/capture.go:161`; called from `Do` at `:316-339`). The wire-level `Request.Status` field still exists for callers that haven't moved off it, but a value that disagrees with the derivation is reported back in the response rather than silently obeyed or swallowed.

`instructions` is accepted on the wire (`Request.Instructions`) but is deliberately **not** published in this schema, for the same reason `probe` isn't: the ingest sweep executes a matching instruction under a fixed grammar, and a field a model can see is one it will eventually fill from note content — an execution path for whatever that content says. It reaches this door only from operator-typed surfaces: the CLI's `agentmd capture -instructions`, and the phone clipper. See [How to capture from your phone](Capture-From-Your-Phone).

Capture writes the file, then updates the index. No model call, no network, and it works offline — the mechanism that makes something exist and findable never waits on judgment. If the index write fails, the file is still on disk and the next reconcile pass picks it up; the response says so rather than inviting a retry that would write a duplicate. There used to be a second effect after the write — an eager enrichment pass fired out of band — and it is gone: capture no longer imports the enrichment package at all (`daemon/internal/capture/capture.go`), and the note waits `unfiled` for the nightly batch instead. See [Enrichment](#enrichment) below.

`agentmd capture` mirrors this schema on the CLI: `-type`, `-summary`, `-why`, `-importance`, `-related`, `-project`, `-task`, `-instructions`, alongside the pre-existing `-title`, `-tags`, `-aliases`, `-source*`, `-space`. There is no `-status` flag (`daemon/cmd/agentmd/main.go:518-534`).

## Configuration

Read from `~/.claude/.agentm-config.json`, overridable per-invocation by flags.

| Key | Default | Notes |
|---|---|---|
| `plugins.obsidian-vault.vault_path` | — | Required. `$MEMORY_ROOT` overrides it, and names the **memory** root (`<vault>/Agent` on the shipped layout): the daemon takes the configured `memory_root` off the export's end to find the vault root, and an export that does not end in it is a flat layout — both roots at once. `--vault` beats the export. **Deprecated:** `$MEMORY_VAULT_PATH` is the old name for the same variable, with the same memory-root meaning; the hooks and the runner still export it alongside `$MEMORY_ROOT`, and it is removed one release after 2026-09-11. |
| `daemon.spaces` | derived from `memory_root`: `{"memory": "<root>/memory", "projects": "Projects", "diagnostics": "<root>/diagnostics"}` | Space name to vault-relative directory. `diagnostics` joined the defaults in filing-v2 part 2a. `projects` points at the vault-root sibling `Projects/` as of filing-v2 part 2b — unprefixed, since it sits beside `<root>` (previously `<root>/desk/projects`). |
| `daemon.shard` | `date` | The fallback only — a typed note the filing contract's `routing` table names lands in that class directory regardless of this setting (see [Lifecycle, sources, and facets](#lifecycle-sources-and-facets)). `daemon.shard` governs only a note the contract can't place: `date` writes `<space>/<YYYY>/<MM>/<slug>.md`, `flat` writes `<space>/<slug>.md`. |
| `daemon.phone_paths` | `[]` | Vault-relative prefixes whose changes are attributed to the phone. |
| `daemon.reconcile_every` | `5m` | How often to re-walk the vault. |
| `daemon.port` | `7821` | |
| `daemon.index_path` | platform state dir | Rejected if it resolves inside the vault. |
| `daemon.unfiled_age_red` | `72h` | The oldest unfiled item's age that turns the queue red. |
| `daemon.unfiled_count_red` | `1000` | Size backstop. Deliberately far above an ordinary day. |
| `daemon.queue_baseline` | first run | Items captured before this are the inherited backlog. Recorded automatically if unset. |
| `daemon.health_every` | `15m` | How often thresholds are evaluated and the probe runs if due. |
| `daemon.probe_every` | `24h` | How often the self-probe runs. |
| `daemon.probe_budget` | `10s` | How long one round trip may take before it counts as failed. |
| `plugins.autonomy.email_to` | — | Where alerts go. Shared with the daily email that carries the morning note. |
| `plugins.autonomy.email_smtp_url` | — | `smtp://[user[:password]@]host[:port]`. Both keys required, or the channel skips. |
| `plugins.autonomy.email_from` | `email_to` | For relays that need a domain-verified sender. |

`daemon.spaces` and `daemon.shard` are the seam the `Agent/memory` + `Agent/desk` migration turned on. Moving to that layout was an edit to these two keys, not a rewrite — which is why the defaults derive from `memory_root` instead of naming directories as literals, with one deliberate exception: `projects` is now the unprefixed literal `Projects`, the vault-root sibling filing-v2 part 2b moved it to (the Python stack names the same sibling as `../Projects`, relative to its own root).

## The loud queue

Filing is asynchronous, so the queue is meant to be busy — what must never happen again is a queue that stops draining and says nothing. The previous system's inbox reached 4,933 items in silence.

`agentmd status` reports five things and exits 3 if any of them is red:

```
agentmd 0.1.0-dev · up 14h
RED
  vault    /path/to/vault
  queue    4411 awaiting a judgment · oldest 4d1h old (red past 3d old, or 1000 items)
           of which 4349 inherited (captured before 2026-08-10, oldest 28d22h) — reported, not paged about
  index    9159 documents · last pass 41s ago        (red past 15m0s)
  git      degraded: not a repository
           no undo for a bad write, and `agentmd gate corpus-write` refuses
  probe    ok 3h0m0s ago (round trip 11ms) · memory/2026/08/agentm-self-probe-….md
```

**The thresholds are age-dominant.** Under a standing daily ingest, fifty fresh unfiled items every morning is an ordinary Tuesday; the oldest unfiled item being three days old means filing stalled. The count threshold is a backstop at a thousand — at fifty a day it takes twenty dead days to reach, by which point age has been red for seventeen of them, so it fires on its own only when a producer wrote thousands of items at once.

**The queue is `unfiled` or `inbox` notes no enrichment has judged.** `confidence_set = 0` narrows it past status alone (`awaitingJudgment`, `daemon/internal/index/index.go:645`): a card enrichment judged and left below the floor keeps `status: unfiled`, but it has been through the pass and is listed for you in [needs review](Review-Flagged-Memories) rather than counted here — the same reason `superseded` and `expired` are excluded. Counting an already-judged card would put one retired years ago at the head of the queue and leave the age threshold red permanently.

**The inherited backlog is reported and does not page.** The first status read against the real vault was 4,349 unfiled items, the oldest 29 days old. Both numbers are true and neither is news: the design already decided that pile is rank-penalized and drained by dreaming later, and their dates come from filesystem mtime, which a sync client can rewrite wholesale. So the daemon records a **queue baseline** on its first run — items captured before it are the backlog it inherited. The total, the inherited count, the backlog's own age, and the baseline date are on every status surface; only the part captured after the baseline is measured against the thresholds. A four-day-old item captured after the baseline pages even when the backlog is thousands deep, which is what keeps the split from being a mute button. Set `daemon.queue_baseline` to move the line by hand; delete `queue-baseline.json` in the state directory to re-record it.

**Degraded git is reported and does not page.** It blocks the corpus-write gate below, and it is on every status surface, but the vault is not a repository until the git-transport migration runs and a daily email about a deferred migration teaches its reader to ignore the channel.

On red, the daemon emails through the operator's own relay — once per calendar day for the same set of conditions, and again when a different one goes red. With no relay configured the channel is a silent skip, said once at startup rather than discovered at 3am. Credentials are never sent over a connection that did not negotiate TLS: if the relay offers no `STARTTLS`, a URL carrying a password refuses to send rather than downgrading to a plaintext login.

## The self-probe

Once a day the daemon proves the round trip on itself. It captures a synthetic note **over its own MCP surface**, asks for it back with two nonces, and records the result where `status` reads it.

- The **alias nonce** appears only in frontmatter, so finding it can only be answered from the `meta` column. That is the sideways question — the note's prose does not contain the word being searched for.
- The **body nonce** appears only in the prose.
- The whole trip must finish inside `daemon.probe_budget`.

The note is marked `probe: self-probe` in frontmatter. Everything that must not count a synthetic note in a measurement reads that marker — `agentmd classify --json` carries a `probe` field per row and the summary counts them apart. **Not a path rule:** capture shards by date, so a probe written on the 31st and one written the next morning live in different directories, and any location-based exclusion would quietly stop excluding.

The current probe note stays in the vault so the round trip has an artifact anyone can look at; the previous one is retired on the next successful run. A failed probe's note is left in place as evidence. Probe commits carry `origin: self-probe`.

```bash
agentmd probe
```

Runs it now, through the same code path as the daily schedule.

## The filing contract

`standards/storage-rules.md` decides where a memory goes and what shape it takes.
The daemon reads this file at runtime instead of compiling it in. You write the
contract in markdown, and the daemon follows it directly. Changing where a type
routes, retiring a value, or moving a threshold is an edit to that file, and it
takes effect on the next capture without a recompile or a release.

**The daemon is the only thing that parses that file.** Capture validates a
caller's type against it, the MCP tool schema publishes its enum, and the Python
batch layer asks over `agentmd rules --json`, once per run rather than once per
note. A second parser would be a second thing to drift, and the whole claim is
that a type added to the rules exists everywhere at once.

Resolution takes the first source that exists: `$AGENTM_STORAGE_RULES`, then the
vault's own `standards/storage-rules.md`, then the copy embedded in the binary.
The embedded copy is what keeps the taxonomy defined in a checkout with no vault
— a fresh clone, a CI run, a unit test.

### Absence falls through; corruption halts

A missing rules file is not an error, so resolution moves on to the next source.
A file that is present and will not parse halts filing and never falls back. The
halt is what stops a model improvising around a malformed rule, which is how you
get filing that looks fine and is wrong.

The halt is deliberately narrow, matched to what a broken contract actually
endangers:

| Still works | Stops |
|---|---|
| Search — it does not read the taxonomy | Filing. Nothing is promoted, merged, expired or re-typed |
| Capture with no type — it lands untyped and `unfiled`, which is the state filing drains anyway | Capture that *names* a type. Validating the claim is precisely what is unavailable, so it is refused with the parse error attached |
| The index, the watcher, the committer, the probe | `agentmd gate corpus-write`, which refuses — a job that decides where thousands of memories belong should not decide it by guessing |

Because search and untyped capture keep working, the halt would otherwise go
unnoticed. It is reported in three places, each one a surface somebody reads: the
`filing` line on `agentmd status`, shown red with the parse error and the remedy;
the `check-storage-rules` CI gate; and the nightly dreaming digest. `agentmd
status` also counts the typed captures the halt has refused since boot, which is
what makes a client failing every write visible rather than silent.

**A fix is picked up live.** The daemon re-reads the contract on each health
pass, so correcting the file returns it to `OK` without a restart. Capture does
not pay for that re-read: it reads a held pointer and never parses the file, which
is what keeps it inside its sub-100ms budget.

### What the block carries

The machine-readable core is a fenced `storage-rules` block; the prose around it
is what the enrichment prompt reads. Two registers divide the vocabulary. A note
carries one field or the other, never both.

| Register | Field | Holds |
|---|---|---|
| `memory_types` | `type` | The six values a memory carries. Something that *asserts* — a preference, a convention, a fact, a recipe, a fix, an idea. Growth is braked by the warrant rule. |
| `record_kinds` | `kind` | Shapes a record carries. Something that *records* — a nightly brief, a telemetry row, a session trace, an index page. Not memories, so they carry no type at all. |

`deprecations` maps each retired value to its replacement, which is what makes a
collapse mechanical rather than a judgment repeated thousands of times.
`rules_hash` is computed over the block's parsed content rather than its raw text.
Rewording the prose around it therefore leaves every judgment in the corpus
standing, while changing what the block says marks them stale — identifiable, and
queued for a later re-filing pass rather than corrected on the spot.

### Lifecycle, sources, and facets

[AgentM Filing v2](agentm-filing-v2) adds three
vocabularies to the block
(`daemon/internal/rules/storage-rules.default.md:258-287`). You declare
each one; the daemon validates it exactly like `memory_types` and
`record_kinds` above — a malformed value halts filing, by name. At the time
these were added, none had a runtime reader: the table below was what the
contract *named*, not what anything *did* with it. Filing v2's write path
(2026-09-04) gave two of the three a reader — `lifecycle` and `sources`,
detailed below. The calendar (filing v2 part 5, same date) gave `facets`
a reader too, on the Python side: `calendar_facets.py`'s own `facets()`
function reads the registry through `storage_rules.rules().facets()`
before writing a facet note — see [AgentM Filing v2 § The
calendar](agentm-filing-v2#the-calendar) for the register itself.

| Key | Field | Values | Holds |
|---|---|---|---|
| `lifecycle` | `lifecycle` | `pinned`, `active`, `dormant`, `archived`, `superseded` | The aging axis a memory declares in frontmatter. `default_lifecycle` (`active`) becomes required the moment `lifecycle` names any value — the same rationale as the unconditionally-required `default_type`, on a narrower trigger. |
| `sources` | `source` | `operator-direct`, `conversation` → trusted; `external-fetch`, `email` → untrusted | The provenance vocabulary, stamped in a memory's `source:` field. Trust is a property of the transport, never the content. |
| `facets` | — | `meetings`, `correspondence`, `docs`, `diary` | The calendar's standing per-day registry. `calendar_facets.py` reads it (Python side); the Go daemon's own `IsFacet` (below) still has no caller. |

The `lifecycle` field is a different key from the pre-existing
`lifecycle_tier: durable` marker in [the rank penalty](#the-rank-penalty)
above — a different field for a different, already-wired purpose (decay
exemption).

The same design added three calendar values to `record_kinds`
(`storage-rules.default.md:210-212`):

- `calendar-facet`
- `day-index`
- `calendar-review`

All three are in active use: `calendar_facets.py` writes `calendar-facet`,
`calendar_index.py` writes `day-index`, and the dreaming binary's `calendar`
job (see [§ The dreaming binary, `agentmdream`](#the-dreaming-binary-agentmdream))
writes `calendar-review` — `calendar_rollups.py` wrote it before the
takeover (filing v2 part 6, 2026-09-05) retired that script.

The `routing` table now sends an `idea` to `memory/semantic`, previously
`desk` (`storage-rules.default.md:175`). At the time this reclassification
shipped, capture did not read `routing`, and no file moved because of it.
The `ClassFor` function (`rules.go:569`) was the only reader of `routing`
at that earlier time, and only for the `agentmd graph` command — every
`type: idea` note classified as `semantic` for a `memory/`-prefixed
destination (`rules.go:578`) instead of nothing at all.

Filing v2's write path closed that gap. The `Capturer.Do` method
(`daemon/internal/capture/capture.go`) now calls
`classDir(contract, contractErr, noteType, spaceDir)` and does read
`routing`. `classDir` is checked before falling back to `daemon.shard` —
class routing takes priority over the shard setting. A typed note lands
directly in the class `routing` names for its type: `memory/semantic` for
an `idea`, alongside `preference` and `convention`.

`classDir` returns empty in exactly three cases:

- An untyped request
- A type the routing table doesn't name
- A halted contract

In every one of those cases it falls through to `daemon.shard`. The
`renderNote` function stamps `lifecycle: active` on every note
`capture.go` writes — a hardcoded literal; the Python writer below reads
the contract's `default_lifecycle` instead — `filing_confidence: high` when the caller
named the type, and `filing_confidence: low` when the contract's default
type was used. The `status: unfiled` default predates this change and no
longer tells the whole story: the capture-writers plan replaced the flat
default with a derivation — `active` when the writer named a type and gave
a `why`, `unfiled` otherwise — see [`memory_capture`](#memory_capture)
above. Filing v2's write path (task 5) adds a fourth stamp beside these three: `trust`,
from the caller's `source` tag through the contract's `sources` map.
`trustTier` (`capture.go`) reads `contract.Sources[source]` directly
rather than through the `SourceTier` method below, and treats any
URL-shaped source as `untrusted` even when the contract names nothing for
it; a source that is neither recognized nor URL-shaped earns no stamp at
all. Every write through this path asks [the volume
gate](#the-volume-gate) first, below.

Validation sits beside the existing checks in `validate()`
(`daemon/internal/rules/rules.go:387-429`): kebab-case and no duplicates for
each list, `default_lifecycle` checked against `lifecycle`'s own values, and
`sources` checked against a closed, deliberately two-value tier set
(`SourceTiers`, `rules.go:140`) — a finer trust ladder would be precision a
write-time check cannot honestly deliver. `Rules` exposes the read side as
`IsLifecycle`, `SourceTier`, `IsFacet` (`rules.go:471`, `:483`, `:489`) —
still with no caller of their own; the trust stamp above reads the
`Sources` map directly instead. `StorageRules` mirrors the same read side
in Python as `lifecycles()`, `default_lifecycle()`, `sources()`,
`facets()` (`harness/skills/memory/scripts/storage_rules.py:117`, `:122`,
`:126`, `:131`), and three of those four now have real callers:
`save_entry` (`save.py`) reads `default_lifecycle()` to default a
memory-type write's `lifecycle`; both `save_entry`'s own trust stamp and
`filing_engine.transport()` read `sources()`; and `calendar_facets.py`'s
`facets()` function reads the registry before writing a facet note (the
calendar, filing v2 part 5). `lifecycles()` stays uncalled on both sides,
and `facets()` still has no caller on the Go side — `rules.go`'s
`IsFacet` above is unchanged by this. All four keys are
optional-when-absent, so a pre-v2 rules file keeps parsing while the
migration runs — the "absence falls through" rule this section already
names.

### The volume gate

A fourth threshold joined the block alongside the pre-existing
`thresholds.low_confidence`: `thresholds.daily_write_cap`, the cap filing
v2's write path (task 4) puts on how many memories the *unreviewed* front
doors may add in one day — the capture front door and reflect's mined
candidates, plus the daemon's own native capture path. The default is 200
when the contract names none, grounded in the live corpus on 2026-09-04:
the busiest day on record wrote 110, the 30-day median was 12, so 200 sits
above every real day and well below what an earlier over-capture flood
did. `0` disables the gate; `AGENTM_DAILY_WRITE_CAP` overrides it for a
test or an emergency.

`Capturer.Do` (`daemon/internal/capture/capture.go`) checks the gate before
class routing, counting the index's own `CapturedSince`
(`daemon/internal/index/index.go`) — memory-space documents captured at or
after the start of today — against `dailyWriteCap`. Past the cap it
refuses with a message naming the count, the cap, and the file to edit,
and counts the refusal on an in-process `capped` counter
(`Capturer.RefusedByVolume`) that is not yet on `agentmd status` or the
health surface, unlike its sibling `RefusedCaptures`. On the Python side,
`filing_engine.apply()` — the write step behind `capture.py` and
`reflect.py`'s routing — calls `volume_gate.check()` first, which walks
the class directories counting each note's `captured` (else `created`)
date against the same cap. `apply()` passes `check()` the day the
arriving note's own `captured` stamp names when the caller set one, wall
clock otherwise (`filing_engine._write_day()`) — a fix (filing v2, the
calendar, 2026-09-04) for the gate and the writes-per-day reading
disagreeing about which day a write belonged to at UTC midnight, the
same gap that could let a flood in progress find the door open. A note
that reinforces one already home never reaches the gate in either
language, so retrying a settled capture is always safe.

**The gate does not cover every writer.** `save_entry()` itself carries no
gate call — a direct `/memory save` (`memory_append`), an ingest write, or
anything else that calls `save_entry()` without going through
`filing_engine.apply()` first lands regardless of the day's count. The
gate is scoped to where the design found the actual failure mode:
unreviewed, automated volume, not a deliberate save.

The corpus scorecard's writes-per-day reading (see [Read the nightly
scorecards](Read-The-Nightly-Scorecards)) is the Python-side count,
reported daily with a week-over-week trend.

## Enrichment

`agentmd enrich` runs as a nightly batch only. It used to have a second
trigger — eager, firing out of band just after a capture committed — and
that retired with the capture-writers plan: capture no longer imports the
`enrich` package at all, `Capturer.SetEnrichPass` and `Pass.Wait` are gone,
and the trigger enum collapsed to the one value the batch always was
(`Trigger`, `daemon/internal/enrich/pass.go:38-53`). `daemon.enrich_enabled`
now gates only the standing nightly behaviour, never a per-capture model
call. See [`templates/jobs/enrich-nightly.yaml`](#the-runner-and-a-refused-manifest)
below for how the batch is scheduled.

Eligibility no longer reads `status`: what used to refuse any note that
wasn't `unfiled` is gone (`Eligibility.Statuses` removed; the check now at
`daemon/internal/enrich/pregates.go:69-92`), because status says whether a
note was judged, not whether it has been through this pass. What the pass
reads instead is `PassDepth`, from the note's own `enriched_at` and
`enriched_by` stamps (`pregates.go:133-141`): no `enriched_at` means the
whole pass is owed (`DepthDeep`), and so does an `enriched_at` stamped by a
pass version other than the current one — a prompt change re-owes the deep
pass to every note (agentm-vault plan 04), because a stamp from an older
prompt never answered what this one asks for: the neighbours, `related`,
`importance_proposed`. Only a stamp naming the current pass version is owed
the lighter pass a note that has moved since (`DepthLight`). A note
genuinely unchanged since its last pass is caught for free by the separate
fingerprint gate, keyed on the pass version, the rules hash, and the body
together (`Fingerprint.Check`, `pregates.go:298-307`).

### The refusal record

A card whose response a post-gate rejected is left exactly as it stood, so
it carries no stamp, so eligibility offers it again the next night. On
2026-09-11 nineteen cards did that in one run — seventeen refused by the
grounding judge, two by the alias-vocabulary gate — at two model calls and
roughly forty cents each, and the nightly job would have repeated it
indefinitely.

The refusal is written down instead, one JSON line per card in
`enrich-refusals.jsonl` beside the run record
(`Refusals`, `daemon/internal/enrich/refusals.go`). It is keyed by
`Fingerprint.Key` — literally the same function the fingerprint gate calls,
passed in rather than reimplemented — so pass version, rules hash and body
decide a standing refusal exactly as they decide an idempotent skip. The
`refusal` pre-gate reads it, sixth in the order, between the fingerprint and
the budget: after the fingerprint because that answers the commoner case,
and before the budget because the budget counts a call the moment it agrees
to one. A refused card comes back when its body changes, when the prompt
changes, or when `GatesVersion` changes — the last being a version over the
judge's own question, kept out of `PassVersion` deliberately so sharpening
the judge re-queues the refused set rather than re-owing the deep pass to
all eight thousand cards.

Only a rejection that named a claim is recorded (`Outcome.RefusedBy`, set
in `pass.go` only when the post-gate's error wraps `ErrNotEligible`). A
judge that could not answer, and a judge that rejected without naming a
claim, both return a plain error and leave no row — otherwise one lapsed
login would blacklist every card that hour touched. A card that later
enriches has its row dropped (`Refusals.Resolve`), and the file is
compacted to one row per card at the end of each run.

It is not in the card's own frontmatter, and not a ledger stage. The
frontmatter is read by `PassDepth`, which decides deep from light purely on
whether a stamp is there, and a refusal stored beside one would have to be
excluded by hand in every reader. The ledger's rebuild wipes a stage and
recovers it by walking the corpus, and a refusal has nothing in the corpus
to recover from — that absence is the whole reason it needs recording.

### The queue

The batch no longer asks for "every `unfiled` note." It walks the
directories the filing contract routes a memory type into, less the
derived classes it never owns — today `memory/semantic` and
`memory/procedural` — read from the contract rather than listed in the
binary (`enrichQueueDirs`, `daemon/cmd/agentmd/enrich_run.go:124-155`).
`memory/episodic` is never walked: no memory type routes there, and its
notes are session traces, not cards. Neither is `memory/_watchlist/`,
whose entries are `forward_learning.py`'s pending-review records. The
eligibility pre-gate adds a second refusal beside this: a note whose
`kind` is one of the contract's `record_kinds` — a session trace, a
directory index — is refused as a record rather than a card
(`Eligibility.IsRecordKind`, `pregates.go:49-54,80-83`). The queue itself
is a snapshot taken once per run (`enrichQueue`, `enrich_run.go:157-184`)
— a note captured mid-night waits for the next run rather than moving the
cursor underneath the one in progress. `--dry-run` sizes the night against
this same queue: how many cards are owed the deep pass, the light pass,
are unchanged at this pass, or unreadable, alongside the budget the run
would run under. `--sample` draws from the same queue, and the coverage
ledger's population (`pendingFor`) is the same queue too.

### The budget, and what stops a run

`DefaultBudget` (`daemon/internal/enrich/batch.go:142-150`) is the
operator's line: 2,000,000 tokens a night on either tier
(raised from 1,000,000 strong on 2026-09-11) (`StrongTokenLine`, `CheapTokenLine`, `usage.go:216-219`), a
250-call guard (`CallGuard`, `usage.go:220-224`) that counts every model call
— the faithfulness judge's included — a 3h30m time limit sized to the
02:00-06:00 window, and a fuse of five notes in a row whose model call
itself failed (not a note a post-gate rejected, which is the model
answering badly rather than not answering at all). All four are read
before the next note (`stop`, `batch.go:187-211`); the first one a run
hits ends it, and `BatchReport.StoppedBy` names which in words — "the
call guard (250 calls)", "the strong-tier token line (2,000,000 tokens)",
the time limit, or the fuse — alongside the `--after` cursor the next run
resumes from. `--max-calls`, `--strong-tokens`, and `--cheap-tokens` may
lower any of these lines and never raise them (`lowerOnly`,
`enrich_run.go:199-212`) — nothing run by hand or by schedule is entitled
to more than the operator said.

The token line counts what a call adds: its input, its cache writes and
its output (`Usage.Added`, `usage.go:41-55`, read by `overLine`,
`batch.go:290-297`). The cached prefix a call re-reads is left out. That
prefix is the same on every call, about 37,000 tokens of Claude Code's own
baseline, and a card reads it twice: once for the pass, once for its
faithfulness judge.

Measured on 2026-09-11, a card processed about 107,000 tokens, 74,000 of
them that prefix. When the line counted cache reads, it stopped a night
after nine cards of 181, and most of what it counted was that one repeated
prefix. The operator ruled that the line count what a call adds and kept
the numbers above. At the measured steady state a card adds about 33,000
tokens and costs about $0.39. The run's own totals still count every token
processed; see [Usage, printed and recorded](#usage-printed-and-recorded).

### What the call keeps out

Each call is a `claude -p` subprocess, isolated three ways in
`Caller.command` and `Caller.Call` (`daemon/internal/enrich/model.go:121-201`):

| Measure | How | What it keeps out |
|---|---|---|
| Hooks | `--settings '{"disableAllHooks":true}'` | this project's recall hooks, which would query the daemon and put the vault into the prompt that is rewriting it |
| MCP servers | `--strict-mcp-config --mcp-config '{"mcpServers":{}}'` | every MCP server the operator has configured, with its tool definitions |
| Working directory | a fresh temporary directory per call (`agentm-neutral-cwd-*`) | any `CLAUDE.md` or `AGENTS.md` above the daemon's own directory |

A missing measure fails silently, because the call still returns a
well-formed answer, so `model_test.go` asserts each one on the command
itself. The two MCP flags travel together: an empty `--mcp-config` on its
own is merged with the configured servers and replaces none of them. The
MCP measure also cut most of a call's baseline. Measured on 2026-09-11, one
trivial call carried 113,509 input tokens with the operator's servers
loaded and 30,783 without.

### Usage, printed and recorded

Every enrichment call now passes `--output-format json`
(`Caller.command`, `model.go:164`), and `usage.go` reads the envelope it
gets back: `usage` (input, cache read, cache write, output tokens) and
`total_cost_usd`. Output that is not the envelope is refused rather than
read as zero-cost text — a call the token line cannot count would
otherwise turn the budget off silently (`parseEnvelope`,
`usage.go:100-124`). An error envelope's `result` text — a lapsed login, an
exhausted allowance — is what the failure reports. A shared `Meter` adds
up every call by tier, the pass's and the faithfulness judge's alike
(`Meter`, `usage.go:135-212`; wired in `cmdEnrich`, `main.go:1402-1414,
1440`). The batch prints one line per call as it finishes
(`main.go:1404-1411`):

```text
call N · <rel · depth> · <model>/<tier> · N tokens (in N · cache read N · cache write N · out N · N against the line) · $N.NNNN
```

The judge's call carries `faithfulness judge` in place of the note, and a
call that failed ends with ` · failed`. `N tokens` is everything the call
processed, cache reads included (`Usage.Tokens`, `usage.go:35-39`), and
`N against the line` is the part the token line counts (`Usage.Added`).
Once the run ends, the batch prints one line per tier,
`<tier> tier: <reading> of the N-token line`, then
`night: N model call(s) of the N-call guard · <reading>`, each reading
carrying both figures (`main.go:1615-1628`). The last line the command
prints is a JSON object carrying `total_cost_usd` — the field the runner's
spend line reads (see [AgentM Runner](agentm-runner)) — so the report above
it and the spend line below it can never disagree. Each run also appends
one line to `enrich-runs.jsonl` in the engine state directory
(`newEnrichRun`/`appendEnrichRun`, `daemon/cmd/agentmd/enrich_run.go:68-122`),
which is what the morning note reads for its enrichment row. The record's
`tokens` counts cache reads too, and its per-tier `usage` keeps the four
counts apart.

### The tier table, and the audit that earns a cheap tier

Each depth routes through the same tier table `agentmd tiers` reads: the
deep pass is the table's `classify-unfiled` job, the light pass is
`summarize` (`enrichJobs`, `daemon/cmd/agentmd/tiers.go:132-135`) — names
from when the table's jobs were first named for dreaming, not enrichment.
`enrichRouter` reads the table once per run, so a qualification written
mid-night takes effect from the next night rather than the next note; a
table that will not load routes every depth strong, the table's own
answer to an unknown (`enrichRouter`, `tiers.go:143-157`). Every job
routes strong until an audit says otherwise, and the strong model
defaults to `opus` when `daemon.enrich_model` names none
(`DefaultStrongModel`, `model.go`; `strongModel`, `tiers.go:161-166`) —
session 3 ruled the deep pass strong, Opus (agentm-vault § Dreaming, Q4).
`agentmd tiers` resolves the same default, so the plain question answers
about the run that would happen.

The light pass is the job the audit measures first, because it may move a
card's `summary`, `tags`, `related` and `confidence` and nothing the
daemon ranks by. `agentmd tiers --audit --job summarize --cheap MODEL`
(`cmdTiersAudit`, `daemon/cmd/agentmd/tiers_audit.go`) draws a seeded
sample of the cards the current pass has already judged on the strong
tier — those stamped `enriched_by` with the running pass version under
the contract's class directories, walked by the same queue the batch uses
and filtered through its three free gates (`auditPool`) — and, for each
card, makes three calls through the enrichment `Caller`: the cheap tier
and the strong tier answer the light-pass prompt, rendered exactly as the
batch renders it, neighbours included; then the judge is shown the card,
the strong answer as the reference and the cheap answer on trial, and
returns `{"agree": bool, "reason": "one sentence"}`. Agreement is a
model's judgment, not a string comparison (the operator's ruling of
2026-09-11): "agree" means the two answers would file and rank the card
the same way — the same meaning in the summary, tags that name the same
things, a `related` set that overlaps on the neighbours that matter,
confidence on the same side of the filing floor — and not the same bytes
(`auditJudgeInstructions`). The judge is `claude-fable-5-1` unless
`--judge` names another; it may not be the cheap model on trial.

The strong tier is asked again rather than read from disk. The stamped
card holds a deep-shape answer that went through the post-gates, Compose
and `CarryProvenance`, so a field the gate stripped or the carry restored
would read as the strong tier's judgment when it was the gate's. The
comparison is between two models on one prompt, and both are asked it.

The cheap model comes from `--cheap`, else `daemon.cheap_model`, and the
command refuses to run with neither named rather than defaulting one: the
choice is the operator's at invocation (`planTierAudit`). A pinned job is
refused before anything is drawn (`tiers.CanAudit`, `audit.go:227`).

The command projects before it spends. Without `--yes` it prints the
pool, the draw and its seed, the call count (three per card) and an
estimated cost at the strong tier's measured cost per call — read from
the strong tier's usage in `enrich-runs.jsonl` when it holds calls, else
the 2026-09-11 figures of about 18,000 tokens and $0.20 per call — and
stops; `--json` emits the same projection as one object. With `--yes` it
runs, prints a line per call as `agentmd enrich` does, then the report:
judged, agreed, the rate, every disagreement with the judge's reason,
usage per tier with the judge on its own line, and the verdict. A verdict
that meets the pre-registered bar — 90% agreement over at least 25 judged
samples (`tiers.MinAgreement`, `tiers.MinSamples`) — is saved to
`model-tiers.json` in the engine state directory with the judge named on
the qualification, and `enrichRouter` routes `summarize` cheap from the
next run; one that does not saves nothing, and the report's `why` says
what fell short. A tier or judge that could not be reached takes its
sample out of the rate rather than counting either way, and the report
names each such sample with what failed and why, up to five — a lapsed
login fails every call the same way, and the first live run could only
say a tier could not be reached; five excluded samples in a row stop the
run, the batch's own fuse (`tiers.Audit`, `audit.go:260`;
`tiers.MaxFailuresInARow`). The per-call line carries a failed call's
reason for the same reason. Every run, saved or not, appends one line to
`tier-audits.jsonl` beside `enrich-runs.jsonl` (`appendTierAudit`), for
the morning note to read later. `--samples` defaults to 30 — the floor
plus a margin for excluded calls — and `--seed S` redraws the same cards.

### Sequential, decided

`RunBatch` does not fan out, even though `daemon.enrich_concurrency` still
bounds `Pass.Run` (`batch.go:146-156`). The cursor stays one answer — what
a deferred run resumes with `--after` — and the token line and call guard
are read before each note, so a sequential run overshoots the operator's
line by at most the one note in flight, where N in flight would overshoot
by N. A steady-state night is under fifty notes, which one at a time
finishes inside the 02:00-06:00 window with hours to spare; only a prompt
change re-owes the whole corpus, and that batch runs by hand.

The prompt is one string with two shapes (agentm-vault § Dreaming), the
shape named on the last line of the message. Besides the type enum and the
voice specification, `BuildPrompt` (`daemon/internal/enrich/prompt.go:129-174`)
renders two more inputs: the contract's importance rubric — the prose under
`## Importance` in `standards/storage-rules.md`, read at call time via
`Rules.ImportanceRubric` (`daemon/internal/rules/rules.go:153-159`,
`proseSection`, `rules.go:168-190`) — and up to five neighbours, each an id,
a title and a summary. Both are inputs like the card itself and sit outside
the prompt hash: editing the rubric changes what the next deep pass proposes
without re-owing a single already-judged card. The neighbours come from the
daemon's own lexical search over the card's title and tags, top five,
excluding the card itself, a derived class, a space no background model may
read, and any neighbour whose title or summary carries a credential shape
(`enrichNeighbours`, `daemon/cmd/agentmd/enrich_run.go:218-262`).

What it does once it runs: add to a card rather than rewrite it. The card's
own text is the evidence and stays exactly where the session left it; the
deep pass's judgment lands in the frontmatter above it and, for prose worth
adding, in a dated `## Added by dreaming (YYYY-MM-DD)` section below it
(`Compose`, `daemon/internal/enrich/compose.go:134-180`) — any heading the
model wrote inside that section steps down to `###`, so the section's own
boundary stays the only `## ` it contains and a later deep pass can find and
replace just its own section, keeping anything the operator wrote below it.
Compose refuses to write a composition that would change one byte of what
the session wrote.

The response may carry `title`, `slug`, `type`, `summary`, `tags`,
`aliases`, `related`, `importance_proposed`, `body` and `confidence`
(`Response`, `daemon/internal/enrich/schema.go:33-64`). `altitude` is gone
from the shape entirely — capture still writes the `artifact` default, but
enrichment no longer reads or writes the field (see [the rank
penalty](#the-rank-penalty) above). `why` is never asked for, and a response
that offers one anyway has it stripped before the strict decode rather than
failing the whole call over a field that was never going to land
(`strippedFields`, `schema.go:66-76`). `related` may only name ids from the
neighbours the prompt offered — Compose keeps only those
(`relatedIDs`, `compose.go:102-122`) and renders them as the quoted wikilink
flow list the capture door already writes; an id the model invented is
silently dropped rather than refusing the note. An empty `body` is a fine
answer, and the usual one (`Schema.Validate`, `schema.go:157-177`). The
light pass only ever moves `summary`, `tags`, `related` and `confidence`;
it moves `title` and `type` at or above the floor and never proposes
`importance`; and it leaves the body exactly as it was.

The prompt asks for `body` only when the card and a neighbour, between
them, already state something the card alone does not (`prompt.go:73-80`).
Every sentence must be traceable to a sentence in one of them, with
nothing inferred, which is the same bar the grounding judge below
enforces. The first supervised batch (2026-09-11) ran under an earlier
wording that asked for a connection the card does not make, and the judge
refused three cards of four on sentences the model had been invited to
write.

`aliases` stay empty unless the note itself contains the other name: an
acronym it spells out, a compound identifier it carries, or a name it says
the thing is also called (`aliasRuleBatch`, `prompt.go:104-109`). The rule
names two cases that do not count, a rewording of the title and the
filename; the fourth card that first batch refused had offered its
filename. The alias post-gate refuses a write whose alias the note cannot
account for (`Aliases`, `daemon/internal/enrich/aliases.go:31`). Under the
new wording the next three cards were all written, and none was refused.

`VerdictFor` (`daemon/internal/enrich/render.go:180-200`) is what a
judgment decides about where a card stands. At or above the contract's
floor a card lands `active` at `filing_confidence: high`. Below it the card
stays `unfiled` — fully indexed, rank-penalized, and listed for you in
[Review flagged memories](Review-Flagged-Memories) rather than dropped. A
*second* verdict below the floor — a card that already carried an
enrichment stamp and was already `unfiled` — sinks it to
`lifecycle: dormant` with a `lifecycle_since` date, journaled and named in
the morning note; a `pinned` card and the two rule types, `preference` and
`convention`, never sink, and stay `unfiled` and listed instead.
`FilingConfidenceFor` stamps the categorical twin of the confidence number
every other writer already shares, two bands on purpose: the floor is the
one judgment this pass makes about its own number, and a third band would
be a threshold nobody measured. The needs-review reading selects on
`filing_confidence` without knowing what floor produced it.

`CarryProvenance` (`daemon/internal/enrich/carry.go:36`) copies every
capture-record and review-mark field the composed note doesn't already
set — `source`, `lifecycle`, `captured`, `created`, `via`, `source_url`,
`source_fetched`, `surface`, `instructions`, `review_flags`, `related`,
`trust`, `why`, `project`, `task`, `importance`, `importance_proposed`
(`carriedFields`, `carry.go:13-17`) — from the note as it stood before
enrichment. `filing_confidence` is deliberately excluded from that list:
the pass re-judges it, which is how an unfiled capture actually clears the
needs-review reading rather than carrying its old low stamp forward
unread. `why` rides the carry list and is never written by the pass itself
— no pass was in the room, so a `why` it invented would read exactly like
a real one.

Two guards sit beside the plain carry. `carryImportance` (`carry.go:74-85`)
keeps an `importance` the operator edited: capture writes `importance` and
`importance_proposed` equal, so a note where they *differ* is a note
someone edited by hand, and that value survives a pass untouched — the
pass's own reading lands in `importance_proposed` instead. `carryEvidence`
(`carry.go:93-99`) restores the note's `## Evidence` block verbatim on the
rare composition that would otherwise drop it — insurance beside the
byte-for-byte guarantee above, since the block quotes the note's source
material and the pass was never entitled to rewrite or drop it.

A note with no `lifecycle` of its own starts `active` — an enriched note is
an auto-filed note either way, the same default a fresh write gets.
`main.go`'s `cmdEnrich` is the one caller: it reads the note as it stood,
calls `Compose` with the response, the stamp, the pass depth and the
neighbours offered, and `Compose` calls `CarryProvenance` on the
frontmatter it renders before the write applies (`main.go:1477-1496`).

Two gates retired with the rewrite they existed to check. The
token-preservation post-gate (`tokens.go`) held a rewrite to keep every
identifier its source had; the deep pass no longer rewrites, so Compose's
byte-for-byte refusal above is the stronger form of what it checked. The
faithfulness judge's completeness half — whether a rewrite left something
out, sampled and scored for the scorecard — retired for the same reason:
the card's own text is carried byte for byte now, so nothing of it can go
missing. What the judge still checks, on every note, is the other
direction: whether the title, the summary, or the added prose asserts
anything the card and its neighbours did not (`Grounding`,
`daemon/internal/enrich/grounding.go`).

The judge's source is the card the enricher was shown, not a shorter version
of it. It used to be the body alone, with the frontmatter stripped, while the
enricher was handed the whole card and told every claim must trace to it — so
a proposal naming the card's own `source_id`, `lifecycle` or `captured` date
was refused for asserting what the judge could not see. Three of the nineteen
refusals on 2026-09-11 were exactly that. `judgeSource` now keeps the
frontmatter, less the fields the pass writes itself
(`passWrittenFields`, `render.go`): on a card the pass has already enriched,
its `title`, `summary` and `tags` are its previous answer, and handing those
back as source would let one pass's hallucination ground the next pass's
restatement of it. A card with no enrichment stamp has no previous pass, so
all of its frontmatter is the capture's and all of it is evidence.

`daemon.enrich_sample_rate`, which
governed how often the retired half sampled, is no longer read; a config
that still carries the key is harmless.

## The dreaming binary, `agentmdream`

The second Go binary the design names, built beside `agentmd` by `install.sh`. Where `agentmd` stays resident, `agentmdream` runs one pass and exits — under a dual gate: enough time has to have passed since the last *applying* pass (`-every`; the flag itself still defaults to 168h if left unset, but the scheduled job now passes `-every 12h` explicitly — see below) **and** something has to have happened since (captures in the index, genuine recalls in the recall history). Only an applying pass (`-apply`) moves that clock — plus the class populations the trend compares against and the pass version the re-classification diff keys on; a report-only pass records its own stamp instead and leaves all three where the last applying pass put them, so running one by hand never pushes the next real pass back (agentm-vault plan 04, task 4 — three hand-run report passes on 2026-09-05/06 had each reset the clock, and the maps and the copy collapse sat frozen for a week behind them). Twelve hours is the number the scheduled job actually passes; the design's own text still says twenty-four. The runner's own `02:00–06:00` window is what actually makes the pass run once a night, so `-every` only has to clear the two gaps a bare day could be confused by. The binary starts after the enrichment batch, which takes anywhere from minutes to its three-and-a-half-hour limit, so two nights' passes can start as little as about nineteen hours apart — a literal `-every 24h` would skip whichever night started earlier than the one before. Twelve hours is shorter than that gap and longer than any one night, so no night is skipped and a hand-run `-apply` in the afternoon is still refused. A second start while one is already running is refused (exit 3) by a lock compatible with `vault_lock.py` (mkdir + heartbeat + a stale window, pid takeover of a dead holder). Every mutation is journaled — intent, then applied, then skipped — fsynced before it happens, so a crash resumes from that journal by hash instead of losing or repeating work. Report-only by default; `-apply` makes the writes.

| | |
|---|---|
| Binary | `agentmdream` — built beside `agentmd` by `install.sh` |
| Subcommands | `run`, `status`, `journal`, `version` |
| Gate | elapsed ≥ `-every` since the last *applying* pass (flag default 168h; the scheduled job passes 12h) **and** activity since then |
| Lock | mkdir + heartbeat, stale-window pid takeover; a second start exits 3 |
| Journal | fsynced intent → applied → skipped, hash-checked resume after a crash |
| Default mode | report-only (decides and prints, and records its own `LastReport` stamp without moving the gate's clock); `-apply` writes and moves the clock |
| Triggered by | `templates/jobs/dreaming.yaml`, through the runner |
| Last-pass report | `<engine state dir>/dreaming/last-report.json`, left by every completed pass; a refused or not-due start leaves the previous file — the [morning note](#the-morning-note)'s *What ran* section reads it |

```bash
"$HOME/.local/bin/agentmdream" run -every 12h -apply   # the applying pass the runner schedules nightly
agentmdream status                                       # the last pass, the gate's answer now, the lock
agentmdream journal -tail 20                              # the mutation journal, newest last
```

`run`'s other flags: `-force` (skip the gate and run now), `-pace <duration>` (sleep between mutations, for tests), `-cap <n>` (the automatic-demotion cap for this pass), `-reclassify` (run the sampled re-classification diff this pass even if the filing-pass version hasn't changed), `-json` (emit the report as JSON). The morning note shows a pass written since the night window opened as a table, one row per job; see [the morning note](#the-morning-note).

### Its jobs, in order

| Job | What it does |
|---|---|
| `lifecycle` | A memory silent past `dormant_after_days` (365) sinks to `dormant`; the next genuine recall lifts it back. A dormant memory past `archive_after_days` (1825) becomes an archive candidate — named in the pass's report and never moved by this job. You archive one by hand with `lifecycle_transitions.py --vault <memory-root> set <rel> archived`. |
| `copies` | Content-identical families collapse into the earliest note; every other copy is marked `lifecycle: superseded` + `superseded_by: <canonical>`, never deleted; `status` is untouched. |
| `refile` | A memory whose `type:` the contract routes elsewhere moves under the same basename; a stale `near-duplicate` flag whose twin is gone gets cleared. |
| `promote` | Reads every session trace's `## Captured` and `## Candidates` sections (agentm-vault plan 04, task 4); the recall hook's own `## Recalled` list is basenames, not judgments, and promote no longer reads it. A `## Candidates` line three or more distinct traces carry becomes a semantic candidate at `memory/semantic/candidate-<first-words>.md` — `status: unfiled`, no `why`, `derived_from` naming the traces — for the next enrichment batch to judge; capped at 10 new candidates a pass. A `## Captured` link three traces carry already has a card and is only reported. Nothing is ever written to `crystallized/`, which holds model syntheses made at a task's close or on request. |
| `calendar` | Writes the daily register's weekly and monthly reviews. |
| `mocs` | One map of content per memory type, created at `moc_min_members` (5), split past `moc_split_at` (40), flagged `stale: true` past `moc_stale_after_days` (90). |
| `dates` | Additive relative-date glosses (`last week (the week of 2026-08-24)`) in notes older than `date_gloss_after_days` (30) — never a rewrite, never inside a fence. |

Then three checks that write nothing: a vocabulary audit (every `type:`/`kind:` against the contract's own registers), trend flags (writes doubling week over week, a day at the cap, a class growing by half since the last pass), and a sampled re-classification diff (`reclassify_sample`, 30 notes) whenever the filing-pass version has changed since the last pass, or on `-reclassify`.

### The takeover (2026-09-05)

The binary ran report-only beside the Python `dream.py` cycle through an overlap window, with a daily divergence review comparing the two. The one review agreed on every surface, and the operator flipped `-apply` in `templates/jobs/dreaming.yaml` the same day. Since then, `dream.py` no longer runs the suffix-backlog drain, the calendar rollups, or the lifecycle policy's own sinking and lifting. Since agentm-vault plan 04 it applies and stages nothing at all; what it still does is in [the Python cycle beside it](#the-python-cycle-beside-it) below. Rolling back is report-only mode — drop `-apply` — since the Python lanes it replaced are gone.

### The Python cycle beside it

`dream.py` is the third step of the night. It follows the enrichment batch and this binary (`templates/jobs/dream.yaml`: `schedule: daily`, window `02:00-06:00`, order 3, shipped `dry_run: true`). It reads, reports and proposes, and it changes no note.

| | |
|---|---|
| Command | `python3 harness/skills/memory/scripts/dream.py [--vault-path <memory-root>] [--run-id <id>]`; `--batch-cap` and `--no-auto-apply` are accepted and ignored, so an older manifest still runs |
| Stages, in order | the corpus meters (entries, connectivity, browse surface); the filing contract, fail-closed (a block that will not parse halts every stage after it, and the digest says "Filing is halted" and why); lint, as a report (`lint_repairable_count` counts the mis-cased links `/memory lint --apply` would repair); possible twins (dedup at 0.92 similarity); shared keys (contradiction triage: same `slug:`, different body); proposed facets (a diary label on three or more days in thirty); the enrichment breaker's status and the correction loop |
| Findings file | `<engine state dir>/dreaming/review-proposals.json` — `twins`, `same_key`, `facets`, read by the needs-review map |
| Cycle report | `<engine state dir>/dreaming/python-cycle.json`, beside this binary's `last-report.json`, for the morning note |
| Run digest | `<engine state dir>/dream-runs/<run_id>/digest.md`, with the findings under "For you to judge" |
| Regenerated | `memory/mocs/needs-review.md`, with the twins, shared keys and facets as sections of their own |
| Model calls | none |

Nothing applies a finding. You merge a twin or supersede one by hand, and you register a facet with an edit to the contract. See [Review flagged memories](Review-Flagged-Memories). The `verify-dreaming` gate guards the wiring (see [CI gates](CI-Gates)).

### Parity as a recording

`scripts/fixtures/dreaming-parity/expected.json` was recorded from the Python producers, clock pinned, before they retired. The Go tests reproduce it — including the calendar reviews, byte for byte — and [`scripts/check-dreaming-parity.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/check-dreaming-parity.sh) guards it in the local battery and in CI (see [CI gates](CI-Gates)). The recording can't be re-recorded: the Python producers it was taken from are gone, so a changed decision from here is a deliberate edit to the recording, made on purpose.

Promote's own half is one such deliberate edit (agentm-vault plan 04, task 4): the recording captured the retired Python pass's crystallized digest, and promote no longer writes one, so that half of the check is narrowed away — keeping it would mean checking against behavior the pass doesn't have any more. The recording's `promote` key is left as recorded, unread; the lifecycle and copies halves still check byte for byte against it.

## The runner, and a refused manifest

The local scheduler (`scripts/agentm-runner.sh` → `scripts/runner/cli.py`; design: [AgentM Runner](agentm-runner)) that fires `agentmdream` and every other `.harness/jobs/*.yaml` manifest on its own cadence. One malformed manifest used to stop every job in the cycle, with a launchd-log traceback as the only trace. `load_manifests_lenient` (`scripts/runner/manifest.py`) now keeps every manifest that loads and names each one it refuses, and the cycle runs whatever loaded rather than aborting.

The cycle's own account — what loaded, what was refused and why, what ran — lands at `~/.cache/agentm/runner/last-cycle.json` after every run. Four surfaces read it:

| Surface | What it shows |
|---|---|
| Session brief | `⚠ runner refused N manifest(s): <name>, <name>, … (every other job still runs; see ~/.cache/agentm/runner/last-cycle.json)` |
| Morning note | `Did not run last night: <step> (<reason>)`, the reason taken from that step's outcome in the last cycle (see [the morning note](#the-morning-note)) |
| Doctor | A `runner-cycle` row — `FAIL` naming the refused files when the last cycle refused any (even though the other jobs in that cycle still ran), `OK` with the loaded/ran counts otherwise, `UNVERIFIED` when no cycle has run yet |
| `agentm-runner run --strict` | The old all-or-nothing load, on demand: exits 3 on the first refused manifest and runs nothing |

A plain (non-`--strict`) cycle exits 3 only when nothing loaded at all; refusing some manifests while the rest load and run is exit 0.

## The morning note

The last step of the night, and the page you read the next morning. `morning_note.py` reads what the other nightly steps left behind and writes one note. The daily email sends it, and the session-start line shows its first section. See [Read the morning note and the nightly scorecard](Read-The-Nightly-Scorecards) for how to read it.

| | |
|---|---|
| Command | `python3 harness/skills/memory/scripts/morning_note.py [--vault-path <memory-root>]`; without the flag, `$MEMORY_ROOT`, then the memory root the daemon reports |
| Scheduled by | `templates/jobs/morning-note.yaml`: `schedule: daily`, `lookback: 3d`, window `02:00-06:00`, order 5 (after the corpus scorecard at 4), `tier: T2`, `dry_run: false` |
| Writes | `<memory-root>/diagnostics/morning/YYYY-MM-DD.md`, dated by local time, and a copy at `latest_morning_note.md` beside it; a `diagnostics` space the daemon reports takes the place of `diagnostics/` |
| Frontmatter | `title`, `kind: report`, `date`, `headline` (*What ran* in one line with a count of the lists that need you, JSON-quoted), `generated_by: morning_note.py` |
| Last night | everything since the most recent opening of the night window, 02:00 local, at or before the run |
| Model calls | none |

### What it carries

Each section is left out when it has nothing to say. When *What ran*, *What needs you* and *Spend* are all empty, the note says `Nothing ran last night and nothing needs you.` instead. Line by line:

| Line | What it says | Read from |
|---|---|---|
| What ran · enrichment | `N judged · N filed active · N below the floor · N sank · N calls · N tokens against the line · <model>`, then `N failed` and `Stopped by <reason>` when present; a note that sank also counts below the floor. Then `N card(s) stand refused at this pass`, with `, N of them skipped free rather than judged again` when the run declined any — silence means none stand, and the headline carries `, N refused` beside the judged count | `<engine state dir>/enrich-runs.jsonl`, the runs since the opening; the numbers are the run's own `refusals_open` and `refused` |
| What ran · the binary | the pass's mode, outcome and gate reason, then one table row per job (lifecycle, copies, refile, promote, calendar, mocs, dates); `ran, and its gate held; the last pass was N ago` when the runner started it and the gate held | `<engine state dir>/dreaming/last-report.json`, when written since the opening |
| What ran · the Python cycle | possible twins, shared keys, proposed facets, and the orphan, contradiction and mis-cased-link counts from lint; `filing is halted` with the parse error when the contract did not parse | `<engine state dir>/dreaming/python-cycle.json`, when written since the opening |
| What ran · did not run | `Did not run last night: <step> (<reason>)` for enrichment, the dreaming binary, the Python cycle or the corpus scorecard | the runner's per-job markers and `~/.cache/agentm/runner/last-cycle.json` |
| What needs you | a count and the first five of: unfiled notes the batch judged below the floor (they carry `enriched_at`), possible twins, shared keys, proposed facets, the binary's archive candidates, and what sank in the last seven days; then a link to `[[needs-review]]` | the needs-review reading, `dreaming/review-proposals.json`, `last-report.json`, the lifecycle journal |
| The corpus | one line: class populations, `N awaiting a judgment, the oldest <age>`, `coverage N of M stamped at this pass`, and a link to the day's corpus scorecard when it exists; `not measured (<reason>)` when the daemon does not answer. When the ledger answers 0 eligible over a corpus that holds cards, the note reads coverage once more after a 2-second pause, since the first read can land while the daemon is still reconciling cards the batch just rewrote (`_coverage`, `COVERAGE_REREAD_PAUSE`); a second 0 prints `coverage not measured (the ledger answered 0 eligible twice over a corpus of N cards — read it again)`. An empty corpus reads `0 of 0`, once | the class directories, `agentmd status`, `agentmd ledger --pending --limit 0`, `diagnostics/health/` |
| Spend | `Last night:` tokens added against the line, per tier as `<tier> N of <line>`, then tokens processed, calls against the 250-call guard, and dollars, always held to the operator's numbers whatever a run lowered; `Seven days:` tokens added and processed, and dollars, across the week's runs; `Sessions, the last day:` when the rollup recorded any | `enrich-runs.jsonl`; `~/.cache/agentm/telemetry/rollup.db`, opened read-only |

A step counts as run when its runner marker finished since the opening, or when its own record is from tonight, so a batch run by hand inside the window reports as ran. For a step that did not run, the reason is the one its outcome carries in the runner's last cycle:

- `disabled`
- `dry run`
- `outside-window 02:00-06:00`
- `not-due`
- `missed-beyond-lookback`
- `watchdog-stop`
- `budget-ceiling`
- `exited N`

A step missing from the last cycle reads `not registered` when the runner has no marker for it. It reads `no cycle has reported it` when it has one.

### Who reads it

Three surfaces read the note:

| Reader | What it shows |
|---|---|
| Session-start line | `[agentm] Morning — <headline> (written <age>)`; once the newest note is two days old (`--deadman-days` or `$AGENTM_DIGEST_DEADMAN_DAYS` changes the two), `[agentm] ⚠ Morning note — none in N days (last: <date>); the night has stopped finishing — see runner.` It reads `latest_morning_note.md`, or the newest dated note when the copy is missing, and reads the digest ladder only when no morning note exists |
| Daily email | the whole note, frontmatter aside, under the subject `AgentM morning — <headline>`, when this morning's or yesterday's note exists; the newest digest otherwise. `templates/jobs/observability-email-daily.yaml` runs at order 6, after the note |
| On-device notification | the session-start line without its `[agentm] ` prefix |

The session-start hook passes no path, so `resolve_vault()` (`scripts/health/session_brief.py`) reads the config. It joins `plugins.obsidian-vault.memory_root` onto `plugins.obsidian-vault.vault_path` when that directory exists. That is how the line finds `Agent/diagnostics/morning/` on a nested layout.

## The derived indexes

Three tables come out of the capture transaction, and all three are caches. They
rebuild from the markdown, none of them is authoritative, and deleting any of
them costs a reconcile pass rather than data. That is not a slogan here: a test
deletes all three, rebuilds, and compares — and a companion corrupts rows and
proves the rebuild repairs them rather than preserving the damage.

| index | key | carries | what it buys |
|---|---|---|---|
| `chunks` | `(doc_id, chunk_idx)` | `header_path`, content | a focused note stops losing to a long document on term-frequency mass |
| `links` | `source_id` → `resolved` | link text, surrounding context | one-hop graph expansion in both directions at lookup cost |
| `entities` | `(entity_uri, doc_id)` | — | every note mentioning an issue or repository, without a scan |

All three were added additively, with no `SchemaVersion` bump. A bump discards
the whole index file, and the expensive half of rebuilding it is the re-embed,
which none of this touches.

### Two kinds of chunking, and why both

The tempting reading is that header chunking replaces the window chunking that
came before it. It does not, and treating them as alternatives would regress a
measured fix.

`ChunkText` splits by byte budget with overlap, sized to the embedder's context
window. It exists because 562 of 9,473 notes exceed that window and used to lose
everything past their head. It is about what the model can read.

Header chunking splits by markdown heading so a match points at a section rather
than a file — the fix for a 38KB design document taking all five top slots from a
1.1KB focused note. It is about what a person asked for.

A long section blows the window whatever its headings say, so the second does not
subsume the first. The split runs at two levels: header first, then window-split
any section still over budget, with every resulting row carrying the header path
of the section it came from. One table, one contiguous `chunk_idx` space. A note
with no headings — 94% of this corpus — produces exactly what `ChunkText` has
always produced, byte for byte.

The chunk budget is fixed rather than read from the live embedder. The chunk
table is a retrieval structure and has to stay stable across a model swap, or
every swap silently re-cuts every note and a `<path>#<n>` reference stops meaning
what it meant. The vector arm re-chunks to its own live window when it embeds;
that is where model-specific sizing belongs.

### Links, and what happens when one dangles

Both forms are read, because the corpus writes both: Obsidian produces wikilinks
and everything generated produces markdown links. Supporting one format would
miss half the link graph while appearing complete, which is worse than supporting
neither.

A target resolves by longest matching path suffix, with proximity breaking a tie.
A target written with more path than a bare name is more specific, and between two
equally specific candidates a link far more often means the sibling than the
far-away file with the same name.

**An unresolved target is recorded, not dropped.** A dangling link is a fact about
the corpus, and a table that discarded them would make any later pass over them
blind. The stub-synthesis stage that read them retired in agentm-vault plan 04;
the record stays, and `work_ledger.dangling_targets()` still reads it.

Links inside fenced code are skipped. A link in a code block is a sample, and
indexing it would connect a page to whatever its examples happen to mention.

### Entities, before any `person` type exists

Issue, qualified issue, repository, commit and changelist references are pulled
out by regex and keyed by a namespaced URI, so `issue:owner/repo#123` can never
collide with `repo:owner/repo`. This is what makes an entity timeline addressable
today: every note mentioning something is one lookup away. The entity-rollup stage
that summarized from that set retired in agentm-vault plan 04, and the lookup
stands on its own. No type is registered, so the taxonomy's growth rule is
untouched.

Most of the work here is refusing false positives. `#1` is as often a list marker
as a reference, so a bare issue needs two digits; `#todo` is a tag. `a/b` is a
path far more often than a repository, so the host is required. An all-digit run
is a date or a count rather than a commit, so a hash needs a hex letter, and
seven characters is where git abbreviates.

### Aliases

Derived at capture from the note's own text, and nothing is invented. A model
reading a note paraphrases the note, and the gap that hurts retrieval is between
the note and a future question rather than between the note and a restatement of
itself — model-written aliases measured −3.85 R@5 and are not used.

Two channels. Acronyms are read in both directions and kept only when the
expansion's word initials actually spell the acronym, so an ordinary
parenthetical drags nothing in with it. Compound identifiers are decomposed, so
`idx_timestamp_desc` also indexes as `idx`, `timestamp` and `desc` — the class of
token an embedder mangles and a tokenizer splits differently from how a question
asks for it. snake_case and camelCase are decomposed anywhere; kebab-case only
inside a code span, because a hyphen is ordinary English punctuation.

The list is capped, because the alias column ranks above body and is therefore
scarce rather than free. Sorting happens before the cap, so which aliases survive
is a property of the note rather than of the order the regexes ran in.

## The corpus-write gate

```bash
agentmd gate corpus-write
```

**No corpus-wide write job — migration, backfill, reclassification, dreaming's future drain — may start unless this passes.** The job asks; nobody has to remember.

| Exit | Meaning |
|---|---|
| 0 | Pass. The verdict carries `head`, the commit the job would be reverted to. |
| 3 | Refused. `reasons[]` names the code, the detail, and the remedy. |
| 1 | Could not decide — an unknown gate name, an unreadable config. Also a refusal. |

It refuses on two conditions, which mean the same thing:

- `git-degraded` — the vault is not a repository, or has no commits. There is nothing to revert to.
- `uncommitted-changes` — the worktree already carries edits, so undoing the job and undoing whatever else is in flight would be one command. Let the daemon commit them: within a second of the last write for an ordinary file, or on the next reconcile pass for a tracked file under a dot directory. The one case it will not act on by itself is a file under a dot directory that git does not track yet — commit that one yourself, or add it to `.gitignore`. See [What gets committed](#what-gets-committed).

There is no override flag. What is being checked is whether an undo exists at all, and a gate with a `--force` is a gate that documents the thing it was meant to prevent.

**Every corpus-wide write job in the repo asks it.** Dry runs do not, since they write nothing:

| Job | Gated path |
|---|---|
| `alias_backfill.py` | `run`, `reapply` — `revert` deliberately exempt |
| `recall.py heat-policy` | `--apply` (promotes and demotes across the corpus) |
| `sweep_junk_preferences.py` | `--apply` (archives a cohort) |
| `migrate_arcs.py` | `--apply` on all three subcommands |

`revert` is exempt on purpose: gating the undo on there being an undo is the one arrangement that could strand the corpus.

The call itself is [`corpus_gate.py`](https://github.com/alexherrero/agentm/blob/main/scripts/corpus_gate.py), which runs the binary and relays its verdict without re-deriving it — a second opinion in Python would be a second dialect of the gate. It fails closed on a refusal, on an undecidable answer, on a missing binary, and on a zero exit that does not name the gate (`agentmd` is a bare name on `PATH`, so an unrelated program exiting 0 must not read as permission). It is vendored byte-identically into `harness/skills/memory/scripts/` because the LC-8 bridge rule forbids kernel toolkit scripts importing back into `scripts/`; `check-vendored-parity.sh corpus-gate` keeps the two in step.

## The meters, and what they measure

Four numbers say whether the corpus is converging on itself. Enrichment rewrites
every memory it touches, so the risk it carries is that a corpus of distinct
memories slowly becomes a corpus of one voice saying similar things. These are how
that would be noticed.

| Meter | Bad direction | What it sees |
|---|---|---|
| trigram concentration | rising | the same phrases recurring across notes |
| moving-average TTR | falling | vocabulary narrowing within a sliding window |
| pairwise cosine similarity | rising | every note drifting toward every other |
| nearest-neighbour dispersion | falling | clusters tightening before the average moves |

The last one is the earliest signal. Convergence starts locally — a few notes
tighten around each other while the corpus-wide average is still steady — so the
nearest-neighbour distribution moves first.

The two embedding meters **refuse to run** when the dense arm is absent, rather
than returning zero. Zero dispersion is what a perfectly converged corpus looks
like and zero similarity is what a perfectly diverse one looks like, so a missing
embedder would report either "everything is fine" or "the corpus has collapsed"
depending which number you read. Neither would be true, and refusing is the only
answer that cannot be misread.

### What they measure, and what they deliberately do not

The population is the **filed live memory corpus**: the configured memory space,
`status: active`, excluding `_inbox`, `_archive`, `scratch`, `_shelf` and
`_opinions` (`MeterExcludedDirs`). Two filters rather than one, because neither
is sufficient alone. Status alone still admits hundreds of inbox notes carrying
`active` from a mining pass that never reconciled them. Directory names alone
still admit unfiled captures sitting in the memory space.

`MeterExcludedNested` adds a second cut, scoped to `crystallized`: the class's
lane subdirectories (`crystallized/<opinion>/`) are out of the population while
its own flat files stay in. Filing-v2 part 3 moved the `_opinions`
mined-supplement lanes there, and the reasoning above about `_opinions` carries
over unchanged — a lane entry is still `reflect.py`'s mined material awaiting
promotion, not a filed memory, whatever directory holds it now. A flat
`crystallized/*.md` file is a crystallized memory itself and stays in the
population.

This is narrower than the vector arm's scope, deliberately. `EmbedScope` covers
`memory`, `desk` and `external` so that retrieval can reach the gold set's
answers, which is a fact about scoring retrieval rather than about which notes
enrichment writes. When the meters used it, their window was 79% raw captures and
dreaming's own staged proposal files — and because the inbox accumulates
near-identical mined clippings, the bias ran one way: similarity read high for a
reason that had nothing to do with enrichment.

The window is the most recent notes rather than the whole corpus, because the
question is whether what is being written *now* is converging. A uniform sample
across five years would mix a month of drift into sixty months of history and
report that almost nothing had changed.

## Clusters, and the correction they feed

`meters` says how much the corpus is converging. `clusters` says **where**, which
is what an action needs — the correction loop works in order of severity, and
severity belongs to a specific cluster rather than to a corpus-wide number.

Notes are grouped by single linkage above a cosine threshold, then classified from
provenance alone. The classification is deterministic and involves no model call:
whether two notes came from one source is a fact recorded in their frontmatter,
and asking a model to guess at it would put a judgement call underneath an action
that rewrites files.

| Kind | Means | What may act on it |
|---|---|---|
| `duplicate` | every member shares a provenance unit | a merge proposal in the nightly digest, applied by a person by hand |
| `collapsed` | every member has provenance, no two share any | re-distillation from source |
| `mixed` | some share, some do not | nothing |
| `unknown` | a member records no provenance | nothing |

Two of the four decline to answer, and that is the point. A `mixed` cluster has
both problems and one fix for neither — re-distilling the shared pair from their
common source produces two notes from one source, which the merge arm then finds
again. An `unknown` cluster is a finding about metadata rather than about notes.

**Provenance is compared exactly.** The live corpus's only two clusters are
`DeepSeek-OCR` against `DeepSeek-OCR-2`, and `kimi-code` against `kimi-cli` — four
upstream projects, two pairs. Any prefix or substring comparison calls each pair a
single source, which makes them duplicates, which proposes a merge that would
supersede one of two real memories. The cheaper-looking comparison is the one that deletes
things.

Single linkage means A and C can land in one cluster through a B close to both,
even where A and C are not close to each other. That is right for reporting — five
notes collapsed onto one pattern are one finding, not ten pair findings — and
wrong if membership is read as "these are interchangeable". So every cluster
carries `min_sim`, the loosest pair anywhere in it, and `chained` when that falls
below the threshold.

### The threshold

`--threshold` defaults to 0.95, and the number was measured rather than chosen. On
the filed corpus the pairwise distribution runs median 0.43, p90 0.60, maximum
0.96 — so 0.95 sits at the extreme tail and selects a handful of notes. A number
picked by intuition would have been 0.90, which on a wider population swept nine
notes in ten into a cluster.

It is a flag rather than a constant because a number that decides what gets
rewritten belongs where a person can see it move.

## The memory context graph

`graph` walks the link graph and draws it, using d3-force's three forces with
d3's own constants — so the picture is recognisably the one Obsidian would draw.
Initialization is phyllotaxis rather than random, which makes "faithful to
Obsidian" and "the same picture twice" the same implementation instead of two
goals in tension.

Two spellings of one link target produce two rows in the index, so edges are
deduplicated before layout. `--cap` bounds the node count by keeping the
highest-degree nodes, and the report says how many it dropped rather than quietly
drawing a subset.

## The slop detector, and the question it does not ask

`agentmd slop` scores a note for template residue and novelty. It exists because
enrichment writes prose, and prose written to a template reads fine one note at a
time.

**It does not ask whether a note is worth keeping.** Three rubrics in this arc
asked exactly that and all three failed their own calibration — κ = 0.189, then
0.349, then 0.286 — and the reason is the same each time. Whether a note earns
its place turns on what the operator wants their vault to hold, which is private
to them and not recoverable from the text. Two graders cannot converge on a
question only one of them can answer.

So the detector scores what is visible in the note: how much of it is boilerplate
the template supplied, and how far its language sits from everything else. A low
score is a note worth *looking at*, not a note to delete. The design's review band
and narrow auto-expire band were never built, and the staging machinery they were
meant to sit on retired in agentm-vault plan 04, so nothing acts on the score.

The length floor is an AND-gate, never a rule on its own. A short note is not
slop — this corpus is full of short dense references that are exactly what a
memory should be — so shortness only ever compounds another signal.

## Completeness, and why it survived where the others did not

`agentmd completeness` samples enriched notes, splits each into claims, and hands
the pairs to a grader that asks one question: **did the rewrite drop something the
source said?**

That question is answerable by two people looking at the same two pieces of text,
which is the whole reason it survived calibration where "is this worth keeping?"
did not. The rubric is frozen before the sample is drawn, and it lives at
`scripts/health/fixtures/completeness-v1/RUBRIC.md`.

One rule is worth naming because an earlier rubric lacked it: grade against what
the source *actually says*. A rewrite that faithfully reports a truncated source
has not lost anything, and a grader that does not know this marks every damaged
note as an enrichment failure.

The check that needs no human at all is the gutted-note fixture: a note with
material deliberately removed must score lower than its source, asserted in both
directions. That one runs in CI.

## Watching, and what actually guarantees correctness

Two mechanisms, and only one is the guarantee.

The filesystem notifier makes an edit visible in under a second when it fires. It cannot be relied on: the vault sits on a cloud-sync mount where events are dropped and coalesced, and on macOS each watched directory costs a file descriptor.

The periodic reconcile pass is the guarantee. It walks the vault, compares mtime and size against the index, and adds, updates, or drops whatever disagrees. On an unchanged corpus it is a stat-and-compare. Both paths finish by committing — a change the notifier missed would otherwise be indexed and never committed, which is the half of the vault with no undo.

`agentmd status` reports how many directories were actually watched, how many could not be, and what the last pass did.

## Git

Every change is committed with an `origin:` trailer naming where it came from: `capture` for the daemon's own writes, `phone` for anything under `daemon.phone_paths`, `self-probe` for the daily round-trip check, `local-edit` for everything else.

### Sharing the repository with your own git

The daemon is not the only git client in the vault — you run `git` there too — so it speaks git's own concurrency protocol rather than assuming it is alone. Every index-mutating operation holds `.git/index.lock` (the same file C-git takes) for its whole read-modify-write span, and writes the index by temp-file-and-rename so a concurrent `git status` never reads a torn file. go-git does neither on its own; the daemon adds both, which is what stops a daemon commit cycle that overlaps your `git rm` from silently discarding your staging — the clobber that fired twice during the 2026-08-11 rehoming pass.

When something else holds the lock, the daemon waits up to 10 seconds with backoff, then skips the cycle with a WARN naming the lock and its age, and retries on the next debounce or reconcile. It never steals the lock: a lock that will not clear is either a live git operation or a crashed one, and both deserve a human look. If the daemon logs that warning repeatedly and no git command is running, the lock is stale — remove it by hand.

### What gets committed

**Whatever git reports dirty**, not whatever the indexer accepted. Those are different questions and conflating them was a real defect: until 2026-08-10 an event had to be markdown to reach the commit path, while `agentmd gate corpus-write` refused on anything `git status` could see. Every non-markdown file fell in the gap — written, never committed, permanently dirty, gate shut, with no override to get past it.

So indexing and committing are now separate decisions. Only markdown is indexed, because FTS5 has no use for a PNG. Any change to a tracked tree wakes the committer, which then asks git what is actually dirty and commits that.

`.gitignore` is the policy surface. The daemon holds no second opinion about which files belong in history — which is also where the older question of whether runtime state belongs in the undo story is answered, file by file, by a list you already edit and version.

One rule sits above `.gitignore`, and it is a safety rail rather than a policy: **a file under a dot directory is committed only if git already tracks it.** Drive stages every upload through `.tmp.driveupload`, and that churn peaked above 1,400 files during the git-transport cutover; a vault whose ignore list is missing or wrong would otherwise write all of it permanently into history. Trackedness is the test rather than a list of directory names, because it puts the line where intent already is — `.obsidian/app.json` is tracked because someone chose to version it, so the daemon maintains it, while `.tmp.driveupload/3700.md` is untracked because nobody chose anything. The cost, accepted: a genuinely new file under a dot directory has to be committed by hand once, after which the daemon keeps it current.

Dot directories never wake the committer either, so a tracked file under one is picked up by the reconcile pass rather than within a second. A long sync would otherwise keep resetting the debounce and starve the very commit this exists to make.

A file large enough to be surprising is committed and **logged as a warning**, not skipped. Skipping was considered and rejected: a skipped file stays dirty, and a dirty worktree shuts the gate, which is the defect this design removes. Committing is also the recoverable direction — `.gitignore` plus `git rm --cached` undoes it with the control you already use, whereas a gate held shut by a file the daemon refuses to touch has no lever at all.

## Capture dates

`captured` is immutable: it records an event in the daemon's own life, and the shard a note is born into is the one it dies in. The daemon reads it from frontmatter `captured`, then frontmatter `date`, then the filesystem mtime, and every result reports which one it used.

On the current corpus 8,845 of 8,864 notes carry neither field, so their dates come from mtime. Once a date is recorded the index keeps it, so editing a note does not move its capture date and a sync client rewriting mtimes cannot shift the temporal bounds. `after`/`before` are exact for anything the daemon captured and a good approximation for everything older.

## Registering it

```bash
claude mcp add --transport http agentmemory http://127.0.0.1:7821/mcp
```

Remove any existing `agentmemory` entry first — before this daemon, that name resolved to the stock filesystem server pointed at the vault's parent directory, which was a coincidence of naming rather than a relationship.

## Who can reach it

Loopback only, and not by binding alone. Three checks run on every request, including `/health` and `/status`:

1. The peer address must be loopback.
2. The `Host` header must name a loopback address. A DNS-rebinding request carries the attacker's hostname here, because that is what the browser was told to connect to.
3. An `Origin` header, if present, must be loopback. A native client sends none; a browser always does.

The reason all three exist is that the first is not sufficient. The daemon listens on a fixed port for as long as the machine is up, so a page the operator visits can make his browser issue requests to `127.0.0.1` — and those arrive with a loopback peer address like any other. Cross-origin refusals return 403.

There is no bearer token, on purpose. It would gate other processes running as the operator, and any such process can already read the vault files directly.

## Related

- [AgentM Rescope — Storage Topology](agentm-rescope-topology) — the daemon's design.
- [AgentM Runner](agentm-runner) — the scheduler design behind the runner section above: the manifest schema, the job template contract, and the launchd/cron triggers.
- [AgentM Rescope — The Memory Engine](agentm-rescope-memory) — layout, frontmatter, capture doctrine.
- [AgentM Hybrid Retrieval](agentm-hybrid-retrieval) — the recall ladder that added the embedder child, the search modes, and their measurements.
- [Vault write protocol](Vault-Write-Protocol) — the caller-facing shape of the same write-time stamps and gate refusal.
- [Review flagged memories](Review-Flagged-Memories) — working the needs-review page this page's enrichment stamps feed.
- [Read the morning note and the nightly scorecard](Read-The-Nightly-Scorecards) — reading the note the morning-note section above describes.
- [CI gates](CI-Gates) — `check-daemon` runs the battery below.
