<!-- mode: reference -->
# Memory daemon (`agentmd`) reference

The resident Go process that watches the vault, maintains one FTS5 index, and serves two MCP tools. It is the only thing that runs git against the vault.

Source lives in [`daemon/`](https://github.com/alexherrero/agentm/tree/main/daemon). Design: [AgentM Rescope — Storage Topology](agentm-rescope-topology).

## ⚡ Quick reference

| | |
|---|---|
| Binary | `agentmd` — pure Go, builds with `CGO_ENABLED=0`, no cgo |
| Serves | `http://127.0.0.1:7821/mcp` (loopback only; non-local requests get 403) |
| MCP tools | `memory_search`, `memory_capture` |
| Index | one SQLite FTS5 file, outside the vault, deletable and rebuildable |
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
bash /path/to/agentm/install.sh --daemon <target-project>
```

It builds `~/.local/bin/agentmd`, writes `~/Library/LaunchAgents/com.agentm.daemon.plist`, loads it, and then **verifies the daemon answers `/health` before returning** — a job launchd accepted and that immediately died on a held port is indistinguishable from a working one in `launchctl list`, which is how the retired daemon stayed "healthy" and wired to nothing for months.

`RunAtLoad` starts it at login and `KeepAlive` restarts it if it dies; `ThrottleInterval` bounds the retry rate so a broken install idles instead of spinning. The vault path is deliberately **not** written into the plist — it is resolved from the kernel config at every start, because a path baked into a plist is a cached literal that goes stale.

Re-run the same command after pulling new daemon source: it rebuilds and reloads. Logs go to `~/Library/Logs/agentm/daemon.log`.

```bash
launchctl bootout gui/$(id -u)/com.agentm.daemon && rm ~/Library/LaunchAgents/com.agentm.daemon.plist
```

`install.sh --mcp-server` is retired and now refuses — it installed the Python FastMCP server, and a second agent on port 7821 would lose a race for the port and retry forever.

## Subcommands

| Command | What it does |
|---|---|
| `serve` | Watch, index, serve MCP, commit. Prints `listening http://…` once the index is caught up. |
| `search <terms>` | One-shot query against the index. `-k`, `--after`, `--before`, `--json`. |
| `capture <text>` | One-shot capture. Reads stdin when given no argument. |
| `reindex` | Full reconcile. `--from-scratch` deletes the index first, proving it rebuilds from the files. |
| `status` | Ask a running daemon for its state. |
| `classify` | Rank-penalty class counts over the live vault, printed beside the figures the measurement report established. |
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

## The rank penalty

Miner fragments are short and quote the operator's own words, so BM25 ranks them above the filed notes that answer the question. Demoting them is worth +3.75 points of R@5 at p = 0.0195, measured over six replicates per arm.

| Class | Weight | Detected by |
|---|---|---|
| `fragment` | 0.30 | body opens with a miner lead-in, `mining_confidence` in frontmatter, or a mid-word slug in a miner-filled directory |
| `fragment-promoted` | *none* | the same shapes, on a note whose status shows filing promoted it |
| `status` | 0.60 | status is `unfiled`, `inbox`, `superseded`, or `expired` |
| `staging` | 0.30 | a dream-staging proposal, which quotes both notes it is about |

Four properties are load-bearing:

- **Strength does not matter.** A 125-point sweep produced four distinct outcomes, and every weight at or below 0.6 ranked identically. There is no tuning knob because there is nothing behind one.
- **Multiply over an over-fetch.** 200 rows are fetched, each score multiplied by the product of its classes' weights, then re-sorted. Re-ranking only the top k cannot promote the note the fragments were hiding.
- **Filing overrides shape.** `fragment-promoted` carries no weight, so a fragment-shaped note that filing promoted keeps its score. That protects 1,288 notes, including 229 of the 232 in `personal/preferences/` — the promotion pipeline promoted their bodies verbatim, so they look mined and are filed.
- **Never exclude.** A penalized note that is the best thing the corpus has still comes back first. Exclusion is what left recall returning nothing for four months.

There is no OR query rewrite. It read as the largest available win on one run; replicated six times it is +1.25 points at p = 0.46 and costs 18.8 points of correct rejection, because a search that never returns empty hands the agent five plausible notes and it names one. When a query matches nothing, `memory_search` says so and suggests fewer or different terms instead.

## `memory_search`

| Param | Type | Default | Notes |
|---|---|---|---|
| `query` | `str` | required | Two to five distinctive words. Terms are ANDed. |
| `k` | `int` | `5` | Capped at 50. |
| `after` | `str` | — | Capture date on or after. `YYYY-MM-DD` or RFC3339. |
| `before` | `str` | — | Capture date before. |

Returns `{results, note, matched}`. Each result carries `path`, `score`, `raw_score`, `penalty`, `captured`, `captured_source`, and `snippet`. `score` is the penalized score and larger is better; `raw_score` is the value before demotion, so a penalty is visible rather than inferred from a number moving.

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
| `daemon.spaces` | `{"memory": "personal", "projects": "projects"}` | Space name to vault-relative directory. |
| `daemon.shard` | `date` | `date` writes `<space>/<YYYY>/<MM>/<slug>.md`; `flat` writes `<space>/<slug>.md`. |
| `daemon.phone_paths` | `[]` | Vault-relative prefixes whose changes are attributed to the phone. |
| `daemon.reconcile_every` | `5m` | How often to re-walk the vault. |
| `daemon.port` | `7821` | |
| `daemon.index_path` | platform state dir | Rejected if it resolves inside the vault. |

`daemon.spaces` and `daemon.shard` are the seam the later `Agent/memory` + `Agent/desk` migration turns on. Moving to that layout is an edit to these two keys, not a rewrite.

## Watching, and what actually guarantees correctness

Two mechanisms, and only one is the guarantee.

The filesystem notifier makes an edit visible in under a second when it fires. It cannot be relied on: the vault sits on a cloud-sync mount where events are dropped and coalesced, and on macOS each watched directory costs a file descriptor.

The periodic reconcile pass is the guarantee. It walks the vault, compares mtime and size against the index, and adds, updates, or drops whatever disagrees. On an unchanged corpus it is a stat-and-compare. Both paths commit what they find — a change the notifier missed would otherwise be indexed and never committed, which is the half of the vault with no undo.

`agentmd status` reports how many directories were actually watched, how many could not be, and what the last pass did.

## Git

Every change is committed with an `origin:` trailer naming where it came from: `capture` for the daemon's own writes, `phone` for anything under `daemon.phone_paths`, `local-edit` for everything else.

The vault is not yet a git repository, and the daemon will not create one — that migration is a deliberate later step. Until it runs, `serve` logs `git DEGRADED` at every start and each batch reports that it was indexed and not committed. There is no undo for a bad write until the vault becomes a repository.

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
- [CI gates](CI-Gates) — `check-daemon` runs the battery below.
