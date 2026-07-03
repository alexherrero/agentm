<!-- mode: reference -->
# Memory MCP tools reference

## ⚡ Quick Reference

| Tool | Required | Optional | Returns |
|---|---|---|---|
| `memory_search` | `query: str` | `top_k`, `include_deleted`, `cursor` | Array of memory objects |
| `memory_append` | `title: str`, `body: str` | `tags`, `idempotency_key` | `{id, status: "active"}` |
| `memory_forget` | `id: str` | — | `{id, status: "deleted", deleted_at}` |

> [!NOTE]
> A fourth tool, `memory_recall`, was retired (R0.9 / agentmEngine#2) — it delegated to a V5-3 stub that always returned an empty bundle regardless of input, and had no live caller.

All tools require `Authorization: Bearer <token>` on the request. The server binds `127.0.0.1:7821`; tool names use snake_case (no dots — compatibility with OpenAI-family hosts).

---

## `memory_search`

Search the vault by semantic similarity.

| Param | Type | Default | Notes |
|---|---|---|---|
| `query` | `str` | required | Natural-language search query |
| `top_k` | `int` | `5` | Max results |
| `include_deleted` | `bool` | `false` | Include soft-deleted entries |
| `cursor` | `str \| null` | `null` | Opaque pagination cursor from a prior response |

**Returns:** array ordered by descending similarity score. Each entry: `{id, title, body, score, tags, status, created_at, updated_at}`.

**Pagination:** if more results exist, the response includes `next_cursor`. Pass it back as `cursor` to page forward. An absent or null `next_cursor` means the result set is exhausted.

---

## `memory_append`

Write a new memory entry. Idempotent on `idempotency_key`.

| Param | Type | Default | Notes |
|---|---|---|---|
| `title` | `str` | required | Short summary (used as the vault filename stem) |
| `body` | `str` | required | Memory content (Markdown) |
| `tags` | `list[str]` | `[]` | Optional labels |
| `idempotency_key` | `str \| null` | `null` | Deduplicates concurrent writes |

**Returns:** `{id, title, status: "active", created_at}`.

If an entry with the same `idempotency_key` already exists in the current session, the server returns the existing entry unchanged (HTTP 200, not 409).

---

## `memory_forget`

Soft-delete a memory entry.

| Param | Type | Notes |
|---|---|---|
| `id` | `str` | Memory ID from `memory_search` or `memory_append` |

**Returns:** `{id, status: "deleted", deleted_at: <iso8601>}`.

### Soft-delete contract

The backing file is **never unlinked.** The server flips `status → deleted` and stamps `deleted_at`. Consequences:

- The entry is excluded from all tool responses unless `include_deleted: true` is passed to `memory_search`.
- The full audit trail is preserved; an operator can un-delete by flipping `status` back in the vault directly.
- No resurrection race: a status flip propagates to the sync client as a content update, not a delete — safe under Google Drive / Dropbox sync.

---

## Error codes

| HTTP | Meaning |
|---|---|
| 401 | Missing or invalid bearer token |
| 403 | Origin validation failed (DNS-rebinding defense — do not set `Origin:` in host config) |
| 404 | Memory ID does not exist |
| 409 | Idempotency key reused with different content |
