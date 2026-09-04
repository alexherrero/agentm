<!-- mode: reference -->
# Memory MCP tools reference

> [!IMPORTANT]
> The server documented on this page is **retired**. Its launchd job
> (`com.agentm.memory-server`) was stopped and its plist archived when the Go
> daemon took over port 7821. It had no live caller — nothing in `settings.json`
> or the recall hooks ever pointed at it. The Python source remains in the tree,
> frozen.
>
> For the surface that runs today, see [Memory daemon (agentmd)](Memory-Daemon).

## ⚡ Quick Reference

| Tool | Required | Optional | Returns |
|---|---|---|---|
| `memory_search` | `query: str` | `scope`, `project`, `kind`, `limit`, `include_deleted`, `cursor` | `{results: [...], total, cursor}` |
| `memory_append` | `content: str`, `kind: str` | `project`, `title`, `tags`, `idempotency_key` | `{id, slug, deduplicated}` |
| `memory_capture` | `content: str` | `kind`, `title`, `tags`, `instructions`, `source_url` | `{success: true, id, slug, deduplicated}` or `{success: false, error}` |
| `memory_forget` | `id: str` | `reason` | `{id, status: "deleted", already_deleted}` |

> [!NOTE]
> You cannot use a fifth tool, `memory_recall`. It was retired in R0.9 / agentmEngine#2. It delegated to a V5-3 stub. This stub always returned an empty bundle regardless of input. It had no live caller.

You must include `Authorization: Bearer <token>` on your request for all tools. The server binds to `127.0.0.1:7821`. Tool names use snake_case with no dots. This ensures compatibility with OpenAI-family hosts.

---

## `memory_search`

You can search memory entries by semantic and keyword similarity. This uses `recall.query` under the hood.

| Param | Type | Default | Notes |
|---|---|---|---|
| `query` | `str` | required | Natural-language search query |
| `scope` | `str` | `"all"` | Search scope |
| `project` | `str \| null` | `null` | Restrict to a project |
| `kind` | `str \| null` | `null` | Filter by entry `kind` |
| `limit` | `int` | `20` | Max results |
| `include_deleted` | `bool` | `false` | Include soft-deleted entries |
| `cursor` | `str \| null` | `null` | Reserved for v1.1 — the tool always returns `cursor: null` in v1; there is no pagination yet |

**Returns:** `{results: [...], total, cursor}`. Each result provides `{id, slug, score, status, kind, tags, snippet}`. The `snippet` is a 200-char body excerpt. It is not the full body. You will not find a `title`, `created_at`, or `updated_at` field on a result.

**Pagination:** Pagination is not implemented in v1. You will always receive a `null` value for `cursor` on the way out. This happens regardless of how many results exist beyond `limit`.

---

## `memory_append`

You can write a new memory entry. This operation is idempotent on `idempotency_key`. It routes through `save.save_entry`.

| Param | Type | Default | Notes |
|---|---|---|---|
| `content` | `str` | required | Memory content (Markdown) |
| `kind` | `str` | required | Entry kind |
| `project` | `str \| null` | `null` | Groups the entry under the project's own space — the vault-root `Projects/<project>` when it holds the project (filing-v2 part 2b), else `desk/projects/<project>` — instead of `memory` |
| `title` | `str \| null` | `null` | Falls back to a slug of `content[:60]` when omitted — there is no separate `body` param, `content` is the whole entry |
| `tags` | `list[str] \| null` | `null` | Optional labels |
| `idempotency_key` | `str \| null` | `null` | Deduplicates concurrent writes; stored as a hashed tag |

**Returns:** `{id, slug, deduplicated}`. You will not find `title`, `status`, or `created_at` in the response.

You might provide an `idempotency_key` that already exists. In this case, the server returns the existing entry with `deduplicated: true`. It does not write a second copy. You will not see HTTP-status branching in the tool itself. The dedup hit is just a flag on the normal return shape. It is not a distinct 200-vs-409 response.

A second, independent mechanism can also set `deduplicated: true` — even when you pass no `idempotency_key` at all. `save_entry` runs a write-time content-fingerprint check: an exact hash of the entry body against the vault's existing notes. An exact match reinforces that note instead of writing a new one — its `occurrences` count and `updated` stamp bump; no new file appears. On this path the returned `slug` is the **existing** note's real slug, not the one your `title` / `content` would have generated. This check is exact-fingerprint-only. No near-duplicate heuristic runs synchronously on this path — a near-match is left for the weekly cluster pass instead. It also only reinforces a note whose `status` is `active`; an expired, deleted, superseded, or curated always-load note is never a reinforce target, and your write proceeds normally against those.

---

## `memory_capture`

You use `memory_capture` to record an unreviewed candidate — the second front door alongside `memory_append`, and unlike it, this tool never writes at full, reviewed confidence. Since filing v2's write path, it files the candidate at the class directory the filing contract routes its type to and marks it `status: unfiled` at low filing confidence: the metadata is the inbox. Use it for anything that hasn't been reviewed yet:

- A thought or idea
- A phone capture
- A chat aside
- A link worth remembering

Use `memory_append` instead when you already know the explicit, deliberate destination.

There is no `project` or destination parameter. `memory_capture` never chooses its own destination beyond the type it names or defaults to — the filing contract's routing table decides the class directory, and the triage/ingestion machinery still promotes a candidate to reviewed confidence later.

| Param | Type | Default | Notes |
|---|---|---|---|
| `content` | `str` | required | The captured text |
| `kind` | `str` | `"capture"` | `"capture"` (a thought, link, or note) or `"idea"` (routes to the ideas ledger) |
| `title` | `str \| null` | `null` | When given, becomes the slug base. When omitted, `capture.py` generates a timestamp-based slug (`capture-<UTC timestamp>`) instead of slugging `content` |
| `tags` | `list[str] \| null` | `null` | Optional labels |
| `instructions` | `str \| null` | `null` | An operator-typed action to run after triage |
| `source_url` | `str \| null` | `null` | The link this candidate is about, if any — marks it for the future ingest sweep |

`instructions` is a security boundary. The server stores only the string you pass in this call's own `instructions` argument, verbatim. It never parses or extracts an instruction out of `content`. A fetched article's body, or a pasted link's page text, is untrusted data — a phrase inside it that looks like an instruction is inert. This is a locked, adversarially-tested invariant of the capture design, not an incidental behavior.

**Returns:** `{success: true, id, slug, deduplicated}` on success. `{success: false, error}` on failure. A capture is never silently dropped — the server always returns an explicit outcome, and a write failure surfaces as `error` rather than an exception or a partial result.

The filing engine runs a corpus check during capture. A `deduplicated: true` result means the check matched an existing, live, not-yet-reviewed candidate and reinforced it instead of filing a new one. The engine refuses the match — and files a fresh candidate instead, under its `~dup` mark if a different note already uses the target slug — when your capture carries a `source_url` or `instructions` value the matched candidate lacks, so a link resend's ingest-sweep trigger is never silently lost to a plain-text duplicate.

Since filing v2's write path (task 4), a capture can also fail because the vault already wrote its cap for the day: `{success: false, error: "capture refused: N memories already written today ... daily cap is 200 ..."}`, naming the count, the cap, and the file that raises it. A lock timeout resolves with a retry; a cap refusal doesn't — an exact repeat still reinforces a twin already home even with the gate shut, but a genuinely new capture waits for tomorrow, or for an edit to `thresholds.daily_write_cap`.

Every capture also carries a `trust` tier you never set directly: a call with no `source_url` stamps `operator-direct`, a call with one stamps `external-fetch`, and the contract's `sources` map turns the first into `trusted` and the second into `untrusted`. The tier depends only on the transport tag `capture.py` uses, never the surface you called from — an agent calling `memory_capture` mid-conversation stamps `operator-direct`, same as a human typing at the CLI.

---

## `memory_forget`

You can soft-delete a memory entry.

| Param | Type | Notes |
|---|---|---|
| `id` | `str` | Memory ID from `memory_search` or `memory_append` |
| `reason` | `str \| null` | Optional; stamped into the entry's frontmatter as `delete_reason` |

**Returns:** `{id, status: "deleted", already_deleted}`. The server stamps `deleted_at` into the vault entry's frontmatter. It does **not** return this value to the caller.

You can call `memory_forget` on an already-deleted entry. This operation is idempotent. It returns `already_deleted: true`. It does not error. It does not re-stamp the entry.

### Soft-delete contract

The server **never** unlinks the backing file. It flips `status → deleted`. It stamps `deleted_at` in the file's frontmatter. You should expect the following consequences:

- The server excludes the entry from all tool responses. You must pass `include_deleted: true` to `memory_search` to see it.
- The system preserves the full audit trail. An operator can un-delete an entry. They do this by flipping `status` back in the vault directly.
- You will not encounter a resurrection race. A status flip propagates to the sync client as a content update. It is not a delete. This is safe under Google Drive / Dropbox sync.

---

## Error codes

| HTTP / exception | Meaning |
|---|---|
| 401 | Missing or invalid bearer token |
| 403 | Origin validation failed (DNS-rebinding defense — do not set `Origin:` in host config) |
| `FileNotFoundError` (`memory_forget`) | Memory ID does not exist — this is a plain Python exception, not a distinguished HTTP 404; no status-code mapping exists in the source for this case. |

The tool code lacks an idempotency-key-reuse-with-different-content check anywhere. `memory_append` dedupes purely on the idempotency-key tag matching. It ignores whether the new `content` differs from what is already stored. You will not find a 409 code path for this. This gap is specific to the `idempotency_key` path — the separate write-time content-fingerprint guard both tools also run (see above) decides purely from an exact hash of the body itself, so it can never reinforce a note whose content actually differs.
