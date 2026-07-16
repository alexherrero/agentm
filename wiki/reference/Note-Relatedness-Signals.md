# Note relatedness signals reference

The `notes_link_discovery.py` script scores signals. It finds related-but-unlinked pairs among your **personal** notes. This corpus includes everything outside the vault root and `.obsidian/`. The audit never mutates a personal note. It surfaces candidate links for you to review (A3). Suggestions are strictly personal↔personal. A vault-root entry is never a source or a target (DC-2).

## ⚡ Quick Reference

| Question | Answer |
|---|---|
| What runs the audit? | `harness/skills/memory/scripts/notes_link_discovery.py` (the relatedness engine + report writer). |
| How do I see suggestions? | `python3 harness/skills/memory/scripts/notes_link_discovery.py --format text` (or `--format json`). |
| Which notes are in the corpus? | Personal notes only — the Obsidian vault **excluding the vault root folder, `.obsidian/`, `.trash/`, `.git/`** (DC-2). |
| What are the two signals? | **TF-IDF** content overlap (lexical) + **embedding** cosine (semantic, opt-in via `--embeddings`). Folder + date proximity are weak context. |
| Does the audit ever edit a note? | Not by default — read-only / surface-only (DC-1). The opt-in `--apply` flag is the one exception: it writes the safe suggestions into a marked `## Related` section, backup-first + idempotent (you directed it; A3 satisfied). |
| Where do outputs live? | Report → `<vault>/_meta/notes-links-<date>.md`; embedding cache → `<vault>/_meta/notes-embeddings.json`. Both under the agent-controlled vault, never beside a personal note, never the AgentMemory `vec-index.db`. |
| How do I run the report? | See [Find missing note links](Find-Missing-Note-Links). |
| Related pages | [Find missing note links](Find-Missing-Note-Links) |

## Signals

Your personal-notes corpus lacks a usable graph signal. Only a handful of the ~390 notes carry tags. Only one note contains a `[[wikilink]]`. The frontmatter only holds `title`, `created`, and `updated`. Tags, links, and frontmatter fields are **dead signals** here. The script calculates relatedness entirely from the content.

| Signal | Role | Notes |
|---|---|---|
| TF-IDF term overlap (title + body) | Primary (always on) | Lowercase alphanumeric tokens ≥3 chars; English + Spanish stopwords stripped; HTML/CSS/`#hex`/image-embed/URL markup stripped before tokenizing (many notes are clipped web content); title terms double-weighted; sublinear-tf × IDF, L2-normalized, cosine-scored via an inverted index. |
| Embedding cosine (title + lede) | Secondary (opt-in `--embeddings`) | Each note embedded with the local BGE model (`embed.py`), full pairwise cosine. Catches same-topic/person/event pairs with little shared vocabulary — including cross-language pairs TF-IDF structurally can't see. Graceful-skips to TF-IDF-only when `sentence-transformers` is absent. |
| Folder context | Secondary | Same-folder pairs are labelled `same folder` in the report (navigational grouping); cross-folder pairs show `A ⇄ B`. |
| Date proximity | Available | `created` / `updated` are parsed into the corpus but not scored in v1. |
| Existing `[[wikilinks]]` | Exclusion only | A pair already linked (by stem or relative path, either direction) is never re-suggested. |
| Tags | Dead | ~no notes carry tags — no signal in this corpus. |
| Frontmatter fields | Dead | Only `title` / `created` / `updated` present — no signal. |

## Thresholds

| Flag | Default | Meaning |
|---|---|---|
| `--min-score` | `0.18` | TF-IDF cosine floor. Pairs below it are dropped. |
| `--embed-min-score` | `0.70` | Embedding cosine floor (BGE similarity runs hot — ~0.3–0.5 even for unrelated prose — so the semantic threshold sits well above the lexical one). |
| `--top` | `40` | Per-signal shortlist cap (`0` = all). Keeps the report a high-signal shortlist, not noise. |

The corpus contains ~390 notes. This creates ≈76k candidate pairs. The TF-IDF inverted index compares only term-sharing notes. A document-frequency band drops per-note noise and ubiquitous boilerplate. The embedding pass runs a bounded O(n²) cosine over cached vectors. The report deduplicates matches. A pair found by both signals appears only once. It displays in the TF-IDF section. The report flags it with `✓ also semantically related`. The embedding section lists only the pairs TF-IDF missed.

## Related

- [Find missing note links](Find-Missing-Note-Links) — You use this operator recipe to run the audit and read the report.
