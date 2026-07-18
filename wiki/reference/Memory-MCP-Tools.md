<!-- mode: reference -->
# Memory MCP tools reference

## ⚡ Quick Reference

| Tool | Required | Optional | Returns |
|---|---|---|---|
| `memory_search` | `query: str` | `scope`, `project`, `kind`, `limit`, `include_deleted`, `cursor` | `{results: [...], total, cursor}` |
| `memory_append` | `content: str`, `kind: str` | `project`, `title`, `tags`, `idempotency_key` | `{id, slug, deduplicated}` |
| `memory_capture` | `content: str` | `kind`, `title`, `tags`, `instructions`, `source_url` | `{success: true, id, slug}` or `{success: false, error}` |
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
| `project` | `str \| null` | `null` | Groups the entry under `projects/<project>` instead of `personal` |
| `title` | `str \| null` | `null` | Falls back to a slug of `content[:60]` when omitted — there is no separate `body` param, `content` is the whole entry |
| `tags` | `list[str] \| null` | `null` | Optional labels |
| `idempotency_key` | `str \| null` | `null` | Deduplicates concurrent writes; stored as a hashed tag |

**Returns:** `{id, slug, deduplicated}`. You will not find `title`, `status`, or `created_at` in the response.

You might provide an `idempotency_key` that already exists. In this case, the server returns the existing entry with `deduplicated: true`. It does not write a second copy. You will not see HTTP-status branching in the tool itself. The dedup hit is just a flag on the normal return shape. It is not a distinct 200-vs-409 response.

---

## `memory_capture`

You can stage a candidate — a thought, a link, or an idea — for later triage. This is the second front door beside `memory_append`. It writes to `personal/_inbox/` only. It never writes to permanent memory. Use it for anything that has not been reviewed yet: a phone capture, a chat aside, a link worth remembering. Use `memory_append` instead when you already know the explicit, deliberate destination.

| Param | Type | Default | Notes |
|---|---|---|---|
| `content` | `str` | required | The captured text |
| `kind` | `str` | `"capture"` | `"capture"` (a thought, link, or note) or `"idea"` (routes to the ideas ledger) |
| `title` | `str \| null` | `null` | When given, becomes the slug base. When omitted, `capture.py` generates a timestamp-based slug (`capture-<UTC timestamp>`) instead of slugging `content` |
| `tags` | `list[str] \| null` | `null` | Optional labels |
| `instructions` | `str \| null` | `null` | An operator-typed action to run after triage |
| `source_url` | `str \| null` | `null` | The link this candidate is about, if any — marks it for the future ingest sweep |

There is no `project` or destination parameter. `memory_capture` never chooses its own destination — only the triage/ingestion machinery promotes a candidate out of `_inbox/` later.

`instructions` is a security boundary. The server stores only the string you pass in this call's own `instructions` argument, verbatim. It never parses or extracts an instruction out of `content`. A fetched article's body, or a pasted link's page text, is untrusted data — a phrase inside it that looks like an instruction is inert. This is a locked, adversarially-tested invariant of the capture design, not an incidental behavior.

**Returns:** `{success: true, id, slug}` on success. `{success: false, error}` on failure. A capture is never silently dropped — the server always returns an explicit outcome, and a write failure surfaces as `error` rather than an exception or a partial result.

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

The tool code lacks an idempotency-key-reuse-with-different-content check anywhere. `memory_append` dedupes purely on the idempotency-key tag matching. It ignores whether the new `content` differs from what is already stored. You will not find a 409 code path for this.
