# How to ingest an article

> [!NOTE]
> **Status: implemented** — shipped by `PLAN-capture-article-ingestion.md` (FRIDAY ladder feature 3, capture part 2 of 3).
> **Goal:** Turn a web page or a local file into a searchable, chunked memory — one intact full-document note plus small, reading-order-linked chunks for retrieval.
> **Prereqs:** A resolvable vault (`--vault-path` or `$MEMORY_VAULT_PATH`). No new dependency — fetching a URL uses stdlib `urllib` only.

`/memory ingest` is the explicit, human-invoked door into article ingestion — you name the URL or file yourself. It writes straight to permanent memory, the same trust level as `memory_append`: you named the source, so there's no staging step. (Contrast the automated ingest sweep, which fetches forwarded links on its own schedule and stages first — see [Capture from your phone](Capture-From-Your-Phone).)

## Steps

1. **Run it against a URL or a file:**

   ```bash
   python3 harness/skills/memory/scripts/ingest.py https://example.com/some-article --vault-path <path>
   ```

   or a local file:

   ```bash
   python3 harness/skills/memory/scripts/ingest.py ./notes/draft.md --vault-path <path>
   ```

2. **Omit `--topic` the first time.** Without a topic, the command extracts a title-based slug and stops without writing anything — a real confirmation step, not an auto-accept (`ingest()`, `harness/skills/memory/scripts/ingest.py:222-260`):

   ```
   suggested topic: the-quiet-discipline-of-paragraph-breaks
   title: The Quiet Discipline of Paragraph Breaks
   re-run with --topic the-quiet-discipline-of-paragraph-breaks to confirm (or pass a different --topic)
   ```

3. **Confirm with `--topic`.** Re-run with the suggested slug (or your own):

   ```bash
   python3 harness/skills/memory/scripts/ingest.py https://example.com/some-article --vault-path <path> --topic typography
   ```

   This writes two things, always together, never one without the other: one full-document note (`kind: domain-reference`, the complete extracted text, byte-for-byte reproducible) and a set of chunk notes (one per `chunking.py`'s `chunk_text()` output), each linked to its immediate reading-order neighbors and back to the full document.

4. **HTML is tag-stripped; plain text passes through unmodified.** A fetched web page's markup is reduced to plain text via a minimal parser (`extract_title_and_text()`, `ingest.py:196-209`) — scripts and styles are dropped entirely; everything else becomes plain text. A local `.md` or `.txt` file is stored exactly as you wrote it.

5. **Every note is tagged and slug-prefixed by topic, but shares the flat `group: personal` every other kind of entry in this vault uses** — same-topic ingests sort together by filename even though there's no dedicated per-topic folder.

## Verify

- `IngestBasicsTests`, `FullDocumentNoteTests`, `ChunkNoteTests`, `TopicSuggestionTests`, `GroupCorrectnessTests`, and `HtmlExtractionTests` (`scripts/test_ingest.py`) cover the full behavior — 17 tests, two local fixtures (`scripts/fixtures/ingest/`), no live-network dependency.
- `test_raw_content_skips_fetch_and_preserves_provenance` proves the `raw_content=`/`source_url=`/`source_fetched=` parameters the automated sweep uses to re-invoke this same function at promotion time without re-fetching.

## Troubleshooting

- **A slug collision.** `ingest()` refuses to write anything if any target slug already exists — checked before any file is touched, not discovered partway through (`ingest.py:283-299`). Pick a different `--topic`, or confirm the existing entry is really what you meant.
- **A fetch fails (dead link, network error).** Returns `success: false` with the underlying error; nothing is written. No retry, no paywall handling here — that resilience is the automated sweep's job, not this command's.

## See also

- [Capture from your phone](Capture-From-Your-Phone) — the automated, staged counterpart to this explicit door.
- [Memory MCP tools reference](Memory-MCP-Tools) — field-level detail for the sibling `memory_capture`/`memory_search`/`memory_append`/`memory_forget` tools.
- `wiki/designs/agentm-capture.md` — the full design.
