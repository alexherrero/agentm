<!-- mode: reference -->
# Memory MCP tools reference

## ⚡ Quick Reference

| Tool | Required | Optional | Returns |
|---|---|---|---|
| `memory_search` | `query: str` | `scope`, `project`, `kind`, `limit`, `include_deleted`, `cursor` | `{results: [...], total, cursor}` |
| `memory_append` | `content: str`, `kind: str` | `project`, `title`, `tags`, `idempotency_key` | `{id, slug, deduplicated}` |
| `memory_forget` | `id: str` | `reason` | `{id, status: "deleted", already_deleted}` |

> [!NOTE]
> A fourth tool, `memory_recall`, was retired (R0.9 / agentmEngine#2) — it delegated to a V5-3 stub that always returned an empty bundle regardless of input, and had no live caller.

All tools require `Authorization: Bearer <token>` on the request. The server binds `127.0.0.1:7821`; tool names use snake_case (no dots — compatibility with OpenAI-family hosts).

---

## `memory_search`

Search memory entries by semantic + keyword similarity (`recall.query` under the hood).

| Param | Type | Default | Notes |
|---|---|---|---|
| `query` | `str` | required | Natural-language search query |
| `scope` | `str` | `"all"` | Search scope |
| `project` | `str \| null` | `null` | Restrict to a project |
| `kind` | `str \| null` | `null` | Filter by entry `kind` |
| `limit` | `int` | `20` | Max results |
| `include_deleted` | `bool` | `false` | Include soft-deleted entries |
| `cursor` | `str \| null` | `null` | Reserved for v1.1 — the tool always returns `cursor: null` in v1; there is no pagination yet |

**Returns:** `{results: [...], total, cursor}`. Each result: `{id, slug, score, status, kind, tags, snippet}` — `snippet` is a 200-char body excerpt, not the full body; there is no `title`, `created_at`, or `updated_at` field on a result.

**Pagination:** not implemented in v1 — `cursor` is always `null` on the way out, regardless of how many results exist beyond `limit`.

---

## `memory_append`

Write a new memory entry. Idempotent on `idempotency_key` (routes through `save.save_entry`).

| Param | Type | Default | Notes |
|---|---|---|---|
| `content` | `str` | required | Memory content (Markdown) |
| `kind` | `str` | required | Entry kind |
| `project` | `str \| null` | `null` | Groups the entry under `projects/<project>` instead of `personal` |
| `title` | `str \| null` | `null` | Falls back to a slug of `content[:60]` when omitted — there is no separate `body` param, `content` is the whole entry |
| `tags` | `list[str] \| null` | `null` | Optional labels |
| `idempotency_key` | `str \| null` | `null` | Deduplicates concurrent writes; stored as a hashed tag |

**Returns:** `{id, slug, deduplicated}`. There is no `title`, `status`, or `created_at` in the response.

If an entry with the same `idempotency_key` already exists, the server returns the existing entry with `deduplicated: true` instead of writing a second copy — there is no HTTP-status branching in the tool itself (the dedup hit is just a flag on the normal return shape, not a distinct 200-vs-409 response).

---

## `memory_forget`

Soft-delete a memory entry.

| Param | Type | Notes |
|---|---|---|
| `id` | `str` | Memory ID from `memory_search` or `memory_append` |
| `reason` | `str \| null` | Optional; stamped into the entry's frontmatter as `delete_reason` |

**Returns:** `{id, status: "deleted", already_deleted}`. `deleted_at` is stamped into the vault entry's frontmatter but is **not** returned to the caller.

Calling `memory_forget` on an already-deleted entry is idempotent — it returns `already_deleted: true` rather than erroring or re-stamping.

### Soft-delete contract

The backing file is **never unlinked.** The server flips `status → deleted` and stamps `deleted_at` in the file's frontmatter. Consequences:

- The entry is excluded from all tool responses unless `include_deleted: true` is passed to `memory_search`.
- The full audit trail is preserved; an operator can un-delete by flipping `status` back in the vault directly.
- No resurrection race: a status flip propagates to the sync client as a content update, not a delete — safe under Google Drive / Dropbox sync.

---

## Error codes

| HTTP / exception | Meaning |
|---|---|
| 401 | Missing or invalid bearer token |
| 403 | Origin validation failed (DNS-rebinding defense — do not set `Origin:` in host config) |
| `FileNotFoundError` (`memory_forget`) | Memory ID does not exist — this is a plain Python exception, not a distinguished HTTP 404; no status-code mapping exists in the source for this case. |

There is no idempotency-key-reuse-with-different-content check anywhere in the tool code: `memory_append` dedupes purely on the idempotency-key tag matching, regardless of whether the new `content` differs from what's already stored. No 409 code path exists for this.
