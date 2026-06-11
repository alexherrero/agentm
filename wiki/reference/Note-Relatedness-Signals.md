# Note relatedness signals reference

The signals `notes_link_discovery.py` scores when it looks for related-but-unlinked pairs among your **personal** notes (the corpus outside `AgentMemory/` + `.obsidian/`). The audit never mutates a personal note — it surfaces candidate links for operator review (A3). Suggestions are strictly personal↔personal; an `AgentMemory/` entry is never a source or a target (DC-2).

## ⚡ Quick Reference

| Question | Answer |
|---|---|
| What runs the audit? | `harness/skills/memory/scripts/notes_link_discovery.py` (the relatedness engine + report writer). |
| How do I see suggestions? | `python3 harness/skills/memory/scripts/notes_link_discovery.py --format text` (or `--format json`). |
| Which notes are in the corpus? | Personal notes only — the Obsidian vault **excluding `AgentMemory/`, `.obsidian/`, `.trash`, `.git`** (DC-2). |
| What are the two signals? | **TF-IDF** content overlap (lexical) + **embedding** cosine (semantic, opt-in via `--embeddings`). Folder + date proximity are weak context. |
| Does the audit ever edit a note? | Not by default — read-only / surface-only (DC-1). The opt-in `--apply` flag is the one exception: it writes the safe suggestions into a marked `## Related` section, backup-first + idempotent (you directed it; A3 satisfied). |
| Where do outputs live? | Report → `<vault>/_meta/notes-links-<date>.md`; embedding cache → `<vault>/_meta/notes-embeddings.json`. Both under the agent-controlled vault, never beside a personal note, never the AgentMemory `vec-index.db`. |
| How do I run the report? | See [Find missing note links](Find-Missing-Note-Links). |
| Related pages | [Find missing note links](Find-Missing-Note-Links) |

## Signals

The personal-notes corpus has no usable graph signal — only a handful of ~390 notes carry tags, one has a `[[wikilink]]`, and frontmatter is just `title` / `created` / `updated`. So tags, links, and frontmatter fields are **dead signals** here; relatedness is content-based.

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

The corpus is ~390 notes (≈76k candidate pairs); the TF-IDF inverted index compares only term-sharing notes, a document-frequency band drops per-note noise and ubiquitous boilerplate, and the embedding pass is a bounded O(n²) cosine over cached vectors. The report deduplicates: a pair found by both signals appears once (in the TF-IDF section, flagged `✓ also semantically related`); the embedding section lists only the pairs TF-IDF missed.

## Related

- [Find missing note links](Find-Missing-Note-Links) — the operator recipe that runs the audit and reads the report.
