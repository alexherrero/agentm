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
| `enrich` | Run the enrichment pass over the unfiled queue. `--sample N --seed S` draws a reproducible random batch, `--dump` writes before/after pairs, `--yes` runs one batch without arming the eager trigger. |
| `ledger` | Ask what dreaming has already done, and what is pending. |
| `queue` | Show the pending-work queues, or record work owed. |
| `sources` | Ask whether a source has been mined, and watermark it. |
| `tiers` | Ask which model tier a dreaming job may run on. |
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

Notes longer than the window are split into overlapping chunks (the model's own byte budget, 1/10 overlap) rather than truncated — a note scores by its single best-matching chunk. The vector arm is scoped to `Agent/memory`, `Agent/desk`, `Agent/external`, `Agent/diagnostics` (the diagnostics space joined in filing-v2 part 2a — the digests and scorecards it holds lived under `desk` before the move and were already dense-retrievable), and the vault-root `Projects/` (joined in filing-v2 part 2b — the project trees lived under `desk` before that merge and were already dense-retrievable too, so the move must not silently drop them from the vector arm); `_vault-archive/` and the residual `_meta/` are never embedded.

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
| `lifecycle-superseded` | 0.30 | frontmatter `lifecycle: superseded` — a separate field from the pre-existing `status: superseded` value in the `status` row above; a note can carry either, or both |
| `durable` | *none* | the note never ages: `lifecycle_tier: durable`, `lifecycle: pinned`, `kind: failure-incident`, a `decisions/` path segment, or a contract-exempt space |

Four properties are load-bearing:

- **Strength does not matter.** A 125-point sweep produced four distinct outcomes, and every weight at or below 0.6 ranked identically. There is no tuning knob because there is nothing behind one.
- **Multiply over an over-fetch.** 200 rows are fetched, each score multiplied by the product of its classes' weights, then re-sorted. Re-ranking only the top k cannot promote the note the fragments were hiding.
- **Filing overrides shape.** `fragment-promoted` carries no weight, so a fragment-shaped note that filing promoted keeps its score. That protects 1,288 notes, including 229 of the 232 in `memory/preferences/` — the promotion pipeline promoted their bodies verbatim, so they look mined and are filed.
- **Never exclude.** A penalized note that is the best thing the corpus has still comes back first. Exclusion is what left recall returning nothing for four months.

### The archived wall

`lifecycle-archived` is the one class that breaks the "never exclude" rule above, on purpose. An archived note leaves the default result set entirely — on disk, in the index, but invisible until you ask for the archive by name: `include_archived: true` on `memory_search`, `-include-archived` on `agentmd search`. Ask, and it comes back present and still demoted, never restored to parity with an active note. Every search outcome reports `archived_hidden`, the count the wall kept out of that call's results, so an absence is visible rather than inferred.

### Space, altitude, and the two that are not penalties

`space` replaces a directory boundary, and the replacement is the point. Recall used to restrict itself to the memory root, which cured a real leak by amputation: 13% of top-5 results across 20 prompts fell outside `Agent/`, and *"what should I work on next"* returned two Church notes. But a note that cannot be returned at all cannot be returned when it is the only answer — and an invisible space is how this vault lost 9,786 notes once already. Dampening cures the same leak without hiding anything: a strong distinctive match still clears the multiplier, and a weak cosine neighbor does not.

`artifact` separates a note that states something durable from one that records a moment. A convention and a distilled meeting are both `type: workflow` and should not rank alike on a general question. When a question asks for the artifact shape, the dampening is **removed** rather than reversed into a boost. Every multiplier here is at or below 1.0 and the negative-IDF clamp depends on it, so a multiplier above 1.0 on a row whose score went negative would move that row up for being boosted.

The design makes `artifact` the default so `canonical` has to be earned. That default lives in the enrichment pass, which assigns the field, rather than acting as a fallback here for its absence. No note in this corpus carries `altitude` yet, so reading an absent field as `artifact` would multiply all 15,824 rows. For the same clamp reason, that is not the no-op it looks like.

`durable` carries no weight and is not a penalty. It is the record of a decision, read by decay where the weights are not.

### Decay, and why it is off

Age is the one demotion this daemon computes and does not apply. `daemon.decay_enabled` defaults to false.

The curve is real and ported faithfully: full strength through six months of silence, half to a year, an eighth to three years, a sixteenth to five, and the sixteenth is a floor rather than a waypoint. The anchor is a genuine recall from `<memory root>/.lifecycle.json`, then `updated:`, then `created:`, then a `captured:` date the note claims itself. A filesystem timestamp never anchors. The type-collapse migration rewrote 9,899 notes' frontmatter in an afternoon, and an mtime-anchored curve would read the migrated corpus as brand new and the files it skipped as uniquely ancient.

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
| `include_archived` | `bool` | `false` | Lifts the archived wall for this call — an archived note re-enters the result set, demoted like any other lifecycle class. |

Two more knobs exist in the code and are deliberately not in this table: a `-lex3` flag (widens `fusion`'s subset search from 2-term to 2- and 3-term) and a `rerank` mode (cross-encoder rerank with a score floor). Neither is in `memory_search`'s published schema and neither is requested by the prompt-submit hook — `lex3` missed its own recall floor by two questions, and `rerank` could not separate true answers from hard negatives at any threshold. Both stay in the tree as tested, working code reachable only from `agentmd search` directly: a refuted rung is still worth keeping when it costs nothing in production. See [AgentM Hybrid Retrieval](agentm-hybrid-retrieval).

Returns `{results, note, matched, archived_hidden}`. Each result carries `path`, `score`, `raw_score`, `penalty`, `captured`, `captured_source`, and `snippet`. `score` is the penalized score and larger is better; `raw_score` is the value before demotion, so a penalty is visible rather than inferred from a number moving. `archived_hidden` is the count the archived wall kept out of this call's results — present even at `0`, so an absence reads as measured rather than assumed.

`note` is set whenever the driver should know something — a rewritten query, or an empty result set.

## `memory_capture`

| Param | Type | Default | Notes |
|---|---|---|---|
| `text` | `str` | required | The fact, in plain prose. One concept per call. |
| `title` | `str` | derived | Weighted 4x in ranking, so a good one is worth writing. |
| `type` | `str` | `preference` | One of `preference`, `workflow`, `idea`, `fix`, `convention`, `reference`. |
| `status` | `str` | `unfiled` | `active` when the operator asked for it in the conversation. |
| `tags`, `aliases` | `[str]` | — | Both land in the `meta` column. |
| `source` | `str` | — | URL or message-id for anything ingested. |
| `space` | `str` | `memory` | Which configured space to write into. |

Capture writes the file, then updates the index. No model call, no network, and it works offline — the mechanism that makes something exist and findable never waits on judgment. If the index write fails, the file is still on disk and the next reconcile pass picks it up; the response says so rather than inviting a retry that would write a duplicate.

## Configuration

Read from `~/.claude/.agentm-config.json`, overridable per-invocation by flags.

| Key | Default | Notes |
|---|---|---|
| `plugins.obsidian-vault.vault_path` | — | Required. `$MEMORY_VAULT_PATH` overrides it. |
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
| `plugins.autonomy.email_to` | — | Where alerts go. Shared with the daily digest email. |
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
  queue    4411 unfiled · oldest 4d1h old            (red past 3d old, or 1000 items)
           of which 4349 inherited (captured before 2026-08-10, oldest 28d22h) — reported, not paged about
  index    9159 documents · last pass 41s ago        (red past 15m0s)
  git      degraded: not a repository
           no undo for a bad write, and `agentmd gate corpus-write` refuses
  probe    ok 3h0m0s ago (round trip 11ms) · memory/2026/08/agentm-self-probe-….md
```

**The thresholds are age-dominant.** Under a standing daily ingest, fifty fresh unfiled items every morning is an ordinary Tuesday; the oldest unfiled item being three days old means filing stalled. The count threshold is a backstop at a thousand — at fifty a day it takes twenty dead days to reach, by which point age has been red for seventeen of them, so it fires on its own only when a producer wrote thousands of items at once.

**The queue is `unfiled` and `inbox` only.** `superseded` and `expired` are rank-penalized for a different reason and are not waiting on anything. Counting them would put a note retired years ago at the head of the queue and leave the age threshold red permanently.

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
type was used. The `status: unfiled` default predates this change. Filing
v2's write path (task 5) adds a fourth stamp beside these three: `trust`,
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

`agentmd enrich` rewrites a note the writer filed unsure about into one
it's judged: a title, tags, aliases, a confidence number, and — since
filing v2's write path — a categorical twin of that number every other
writer already shares. `FilingConfidenceFor`
(`daemon/internal/enrich/render.go`) stamps `filing_confidence: high` at
or above the same 0.6 floor `StatusFor` uses to decide `active` vs
`unfiled`, `low` below it — two bands on purpose, since the floor is the
one judgment this pass makes about its own number and a third band would
be a threshold nobody measured. The needs-review reading (see [Review
flagged memories](Review-Flagged-Memories)) selects on this field without
knowing what floor produced it.

`CarryProvenance` (`daemon/internal/enrich/carry.go`) copies every
capture-record and review-mark field the rewritten note doesn't already
set — `source`, `lifecycle`, `captured`, `via`, `source_url`,
`source_fetched`, `surface`, `instructions`, `review_flags`, `related`,
`trust` — from the note as it stood before enrichment. `filing_confidence`
is deliberately excluded from that list: the pass re-judges it, which is
how an unfiled capture actually clears the needs-review reading rather
than carrying its old low stamp forward unread. A note with no `lifecycle`
of its own starts `active` — an enriched note is an auto-filed note
either way, the same default a fresh write gets. `main.go`'s `cmdEnrich`
is the one caller, threading the pre-rewrite note's text through
`CarryProvenance` before the write applies.

## The dreaming binary, `agentmdream`

The second Go binary the design names, built beside `agentmd` by `install.sh`. Where `agentmd` stays resident, `agentmdream` runs one pass and exits — under a dual gate: enough time has to have passed since the last pass (`-every`, 168h by default) **and** something has to have happened since (captures in the index, genuine recalls in the recall history). A second start while one is already running is refused (exit 3) by a lock compatible with `vault_lock.py` (mkdir + heartbeat + a stale window, pid takeover of a dead holder). Every mutation is journaled — intent, then applied, then skipped — fsynced before it happens, so a crash resumes from that journal by hash instead of losing or repeating work. Report-only by default; `-apply` makes the writes.

| | |
|---|---|
| Binary | `agentmdream` — built beside `agentmd` by `install.sh` |
| Subcommands | `run`, `status`, `journal`, `version` |
| Gate | elapsed ≥ `-every` (168h default) **and** activity since the last pass |
| Lock | mkdir + heartbeat, stale-window pid takeover; a second start exits 3 |
| Journal | fsynced intent → applied → skipped, hash-checked resume after a crash |
| Default mode | report-only (decides and prints); `-apply` writes |
| Triggered by | `templates/jobs/dreaming.yaml`, through the runner |

```bash
"$HOME/.local/bin/agentmdream" run -every 168h -apply   # the applying pass the runner schedules daily
agentmdream status                                        # the last pass, the gate's answer now, the lock
agentmdream journal -tail 20                               # the mutation journal, newest last
```

`run`'s other flags: `-force` (skip the gate and run now), `-pace <duration>` (sleep between mutations, for tests), `-cap <n>` (the automatic-demotion cap for this pass), `-reclassify` (run the sampled re-classification diff this pass even if the filing-pass version hasn't changed), `-json` (emit the report as JSON).

### Its jobs, in order

| Job | What it does |
|---|---|
| `lifecycle` | A memory silent past `dormant_after_days` (365) sinks to `dormant`; the next genuine recall lifts it back. A dormant memory past `archive_after_days` (1825) becomes an archive candidate — named for the confirm surface here, never moved by this job itself. |
| `copies` | Content-identical families collapse into the earliest note; every other copy is marked `status: superseded` + `supersedes:`, never deleted. |
| `refile` | A memory whose `type:` the contract routes elsewhere moves under the same basename; a stale `near-duplicate` flag whose twin is gone gets cleared. |
| `promote` | A target three or more distinct episodic notes link becomes `memory/crystallized/consolidated-<slug>.md`, carrying `consolidated_from` and `derived_from`. |
| `calendar` | Writes the daily register's weekly and monthly reviews. |
| `mocs` | One map of content per memory type, created at `moc_min_members` (5), split past `moc_split_at` (40), flagged `stale: true` past `moc_stale_after_days` (90). |
| `dates` | Additive relative-date glosses (`last week (the week of 2026-08-24)`) in notes older than `date_gloss_after_days` (30) — never a rewrite, never inside a fence. |

Then three checks that write nothing: a vocabulary audit (every `type:`/`kind:` against the contract's own registers), trend flags (writes doubling week over week, a day at the cap, a class growing by half since the last pass), and a sampled re-classification diff (`reclassify_sample`, 30 notes) whenever the filing-pass version has changed since the last pass, or on `-reclassify`.

### The takeover (2026-09-05)

The binary ran report-only beside the Python `dream.py` cycle through an overlap window, with a daily divergence review comparing the two. The one review agreed on every surface, and the operator flipped `-apply` in `templates/jobs/dreaming.yaml` the same day. Since then, `dream.py` no longer runs the suffix-backlog drain, the calendar rollups, or the lifecycle policy's own sinking and lifting — it reads the lifecycle axis and reports what it sees, stages archive proposals for the confirm surface, and runs the stages the binary doesn't carry: lint repair, compression, the artifact shelf, inbox triage, the needs-review MOC, insights, and the rest of the confirm-gated proposals. Rolling back is report-only mode — drop `-apply` — since the Python lanes it replaced are gone.

### Parity as a recording

`scripts/fixtures/dreaming-parity/expected.json` was recorded from the Python producers, clock pinned, before they retired. The Go tests reproduce it — including the calendar reviews, byte for byte — and [`scripts/check-dreaming-parity.sh`](https://github.com/alexherrero/agentm/blob/main/scripts/check-dreaming-parity.sh) guards it in the local battery and in CI (see [CI gates](CI-Gates)). The recording can't be re-recorded: the Python producers it was taken from are gone, so a changed decision from here is a deliberate edit to the recording, made on purpose.

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
the corpus, and it is what the stub synthesis reads later; a table that discarded
them would make that pass blind.

Links inside fenced code are skipped. A link in a code block is a sample, and
indexing it would connect a page to whatever its examples happen to mention.

### Entities, before any `person` type exists

Issue, qualified issue, repository, commit and changelist references are pulled
out by regex and keyed by a namespaced URI, so `issue:owner/repo#123` can never
collide with `repo:owner/repo`. This is what makes an entity timeline addressable
today: every note mentioning something is one lookup away, and the rollup that
eventually summarizes it is built from that set rather than from a directory scan.
No type is registered, so the taxonomy's growth rule is untouched.

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
| `duplicate` | every member shares a provenance unit | a merge, staged for a person |
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
single source, which makes them duplicates, which stages a merge, which supersedes
one of two real memories. The cheaper-looking comparison is the one that deletes
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
score is a note worth *looking at*, not a note to delete. The narrow auto-expire
band stays confirm-gated.

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
- [AgentM Rescope — The Memory Engine](agentm-rescope-memory) — layout, frontmatter, capture doctrine.
- [AgentM Hybrid Retrieval](agentm-hybrid-retrieval) — the recall ladder that added the embedder child, the search modes, and their measurements.
- [Vault write protocol](Vault-Write-Protocol) — the caller-facing shape of the same write-time stamps and gate refusal.
- [Review flagged memories](Review-Flagged-Memories) — working the needs-review page this page's enrichment stamps feed.
- [CI gates](CI-Gates) — `check-daemon` runs the battery below.
