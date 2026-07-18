#!/usr/bin/env python3
"""ingest.py — `/memory ingest <url|file>`, capture part 2
(`designs/friday/agentm-capture.md`, capture-article-ingestion plan).

Stores the fetched/read content as ONE full-document note, and ALWAYS also
splits it into small, source-stamped, reading-order-linked chunk notes for
retrieval via `chunking.py`'s existing `chunk_text()`. Neither form replaces
the other -- this is the only capture path that produces more than one note
per item.

Write path: both the document note and every chunk note go through
`save.py`'s `save_entry()` -- these are permanent-memory writes from the
start (unlike capture part 1's `_inbox/`-only staging writer), each
individually validated, indexed, and one-per-file atomic via `save_entry`'s
own `vault_lock.vault_mutex` + `vec_index.enqueue`.

Group: every note this command writes carries `group: personal` (the
design's own "a few older notes use a different group: name... we treat
those as legacy" note, and `save_entry`'s existing convention -- confirmed
against the plan's grounding, not the design overview prose's looser
"filed under personal/domains/<topic>/" phrasing, which `save_entry`'s
group/kind/slug path formula has no way to reproduce literally without a
per-topic group value that would violate the flat `group: personal`
convention every other kind in this vault uses). Topic is instead threaded
through the slug (`<topic>-<title-slug>`, so same-topic notes sort
together) and a `tags` entry -- the design's actual intent (discoverable-
by-topic) without inventing a new directory layer.
"""
from __future__ import annotations

import argparse
import html.parser
import re
import socket
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from chunking import chunk_text  # noqa: E402
from save import save_entry  # noqa: E402

_USER_AGENT = "agentm-ingest/1.0"
_FETCH_TIMEOUT_SEC = 15
_INGEST_KIND = "domain-reference"
_URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


class FetchError(RuntimeError):
    """A clean fetch/read failure. ingest.py does not retry or handle
    paywalls -- that resilience is the ingest sweep's job (capture part 3);
    this command fails loudly instead."""


@dataclass(frozen=True)
class IngestResult:
    success: bool
    needs_confirmation: bool = False
    suggested_topic: "str | None" = None
    title: "str | None" = None
    document: "Path | None" = None
    chunks: "list[Path]" = field(default_factory=list)
    topic: "str | None" = None
    error: "str | None" = None


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _looks_like_url(source: str) -> bool:
    return _URL_SCHEME_RE.match(source) is not None


def fetch_url(url: str) -> str:
    """Best-effort single GET (stdlib urllib, mirrors forward_learning.py's
    default_fetcher pattern) -- raises FetchError on any network failure
    rather than degrading to an empty result, since an operator running
    `/memory ingest` needs an explicit failure, not a silent no-op."""
    req = Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urlopen(req, timeout=_FETCH_TIMEOUT_SEC) as resp:
            status = getattr(resp, "status", 200)
            if status >= 400:
                raise FetchError(f"fetch failed: HTTP {status}")
            return resp.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, socket.timeout, OSError) as e:
        raise FetchError(f"fetch failed: {e}") from e


def read_source(source: str) -> "tuple[str, str | None, str | None]":
    """(raw_text, source_url_or_None, source_fetched_iso_or_None)."""
    if _looks_like_url(source):
        fetched = _iso_now()
        return fetch_url(source), source, fetched
    path = Path(source).expanduser()
    if not path.is_file():
        raise FetchError(f"not a URL and not a file: {source}")
    return path.read_text(encoding="utf-8"), None, None


class _TitleAndTextExtractor(html.parser.HTMLParser):
    """Minimal HTML -> (title, text): pulls `<title>` text and strips every
    other tag, dropping `<script>`/`<style>` bodies entirely. Deliberately
    thin -- not a readability algorithm; the design scopes ingestion as
    "store what you fetched", not content-extraction heuristics."""

    _BLOCK_TAGS = frozenset({"p", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li"})

    _HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

    def __init__(self) -> None:
        super().__init__()
        self._title_parts: list[str] = []
        self._heading_parts: list[str] = []
        self._text_parts: list[str] = []
        self._in_title = False
        self._in_first_heading = False
        self._heading_done = False
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style"):
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self._BLOCK_TAGS:
            if tag in self._HEADING_TAGS and not self._heading_done and not self._heading_parts:
                self._in_first_heading = True
            self._text_parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self._HEADING_TAGS and self._in_first_heading:
            self._in_first_heading = False
            self._heading_done = True

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self._title_parts.append(data)
        else:
            if self._in_first_heading:
                self._heading_parts.append(data)
            self._text_parts.append(data)

    @property
    def title(self) -> "str | None":
        t = "".join(self._title_parts).strip()
        if t:
            return t
        # No <title> element (a document-wrapper-less fragment, e.g. one an
        # upstream readability extractor already trimmed to <article>) --
        # fall back to the first heading tag's text, the de facto title in
        # that shape of markup.
        h = "".join(self._heading_parts).strip()
        return h or None

    @property
    def text(self) -> str:
        raw = "".join(self._text_parts)
        paragraphs = [re.sub(r"[ \t]+", " ", p.strip()) for p in re.split(r"\n\s*\n", raw)]
        return "\n\n".join(p for p in paragraphs if p)


_HTML_TAG_PAIR_RE = re.compile(r"<([a-zA-Z][a-zA-Z0-9-]*)(?:\s[^<>]*)?>.*?</\1\s*>", re.DOTALL)


def _looks_like_html(text: str) -> bool:
    """Full-document sniff (fast path) plus a fragment fallback: a real
    matching open/close tag pair anywhere in the first 4000 chars. A
    retroactive /review found the full-document-only check misclassified
    HTML fragments (`<article><h1>...</h1></article>`, no `<html>`/`<body>`/
    `<title>` wrapper) as plain text, leaving raw markup in saved notes.
    Requires a matching close tag (not just `<word>`) so this vault's own
    angle-bracket placeholder convention (`<url-or-file>`, `<path>`) in
    plain-text/markdown docs never false-positives -- placeholders have no
    closing tag to match."""
    head = text[:512].lower()
    if "<html" in head or "<body" in head or "<title" in head:
        return True
    return bool(_HTML_TAG_PAIR_RE.search(text[:4000]))


def extract_title_and_text(raw: str) -> "tuple[str | None, str]":
    """(title, plain_text). HTML content is tag-stripped; plain text /
    markdown passes through unmodified except for a first-non-empty-line
    title guess (a leading markdown `#` is stripped)."""
    if _looks_like_html(raw):
        parser = _TitleAndTextExtractor()
        parser.feed(raw)
        return parser.title, parser.text
    title = None
    for line in raw.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            title = stripped
            break
    return title, raw


def _slugify(text: str) -> str:
    out: list[str] = []
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-") or "untitled"


def ingest(
    vault_path: "Path | str",
    source: str,
    *,
    topic: "str | None" = None,
    raw_content: "str | None" = None,
    source_url: "str | None" = None,
    source_fetched: "str | None" = None,
) -> IngestResult:
    """Ingest one URL or file: one full-document note (body = the extracted
    text, verbatim -- byte-for-byte reproducible modulo save_entry's own
    trailing-newline normalization) + N chunk notes (chunk body + a
    reading-order nav footer + a backlink to the document), `kind:
    domain-reference`, `group: personal`.

    When `topic` is omitted, returns a suggestion WITHOUT writing anything
    (`needs_confirmation=True`) -- the design's "the agent suggests a
    title-based slug for you to confirm" is a real confirmation step, not
    an auto-accept; re-invoke with `--topic <slug>` (the suggestion or an
    override) to actually write.

    `raw_content`, when given, skips `read_source()`/`fetch_url()` entirely
    -- `source` becomes an opaque identifier only (used for title-slug
    fallback and log/error messages). Used by the automated ingest sweep
    (capture part 3): it fetches once at staging time, stores the text on
    the originating `_inbox/` candidate, and calls this function again at
    promotion time (a later cycle) with the already-fetched text instead
    of re-fetching. `source_url`/`source_fetched` should be passed
    alongside `raw_content` to preserve the ORIGINAL fetch's provenance
    (the sweep's staging timestamp, not this call's own time) -- omitted,
    both fields are simply absent from the written frontmatter, same as
    an ordinary local-file ingest.
    """
    if raw_content is not None:
        raw = raw_content
    else:
        try:
            raw, source_url, source_fetched = read_source(source)
        except FetchError as e:
            return IngestResult(success=False, error=str(e))

    title, text = extract_title_and_text(raw)
    if not text.strip():
        return IngestResult(success=False, error="fetched/read content is empty")

    title_slug = _slugify(title) if title else _slugify(Path(source).stem if not source_url else "untitled")

    if not topic:
        return IngestResult(success=False, needs_confirmation=True, suggested_topic=title_slug, title=title)

    doc_slug = f"{topic}-{title_slug}" if title_slug != topic else title_slug
    group = "personal"
    tags = [topic]

    chunks = chunk_text(text)
    chunk_slugs = [f"{doc_slug}-chunk-{i}" for i in range(len(chunks))]

    # Pre-flight: refuse to write anything if any target slug already
    # exists, rather than discovering the collision partway through the
    # N+1-file write sequence. A retroactive /review found the prior
    # version had no pre-check and no rollback: a mid-sequence
    # FileExistsError left the document note and every chunk written
    # before it permanently orphaned on disk while reporting
    # success=False -- the caller had no way to know memory was actually
    # written. save_entry()'s target formula is vault/group/kind/slug.md
    # with always_load always False here, so this mirrors it exactly.
    vault = Path(vault_path)
    all_slugs = [doc_slug, *chunk_slugs]
    existing = [s for s in all_slugs if (vault / group / _INGEST_KIND / f"{s}.md").exists()]
    if existing:
        return IngestResult(
            success=False,
            error=f"slug(s) already exist, nothing written: {', '.join(existing)}",
        )

    written: list[Path] = []
    try:
        doc_path = save_entry(
            vault_path, _INGEST_KIND, doc_slug, text,
            group=group, tags=tags, source_url=source_url, source_fetched=source_fetched,
        )
        written.append(doc_path)

        chunk_paths: list[Path] = []
        for i, chunk_body in enumerate(chunks):
            nav = []
            if i > 0:
                nav.append(f"[[{chunk_slugs[i - 1]}]] (previous)")
            if i < len(chunks) - 1:
                nav.append(f"[[{chunk_slugs[i + 1]}]] (next)")
            nav_line = f" · {' · '.join(nav)}" if nav else ""
            body = f"{chunk_body}\n\n---\n\nFrom [[{doc_slug}]]{nav_line}"
            chunk_path = save_entry(
                vault_path, _INGEST_KIND, chunk_slugs[i], body,
                group=group, tags=tags, source_url=source_url, source_fetched=source_fetched,
            )
            written.append(chunk_path)
            chunk_paths.append(chunk_path)
    except (FileNotFoundError, FileExistsError, ValueError) as e:
        # The pre-flight check above closes the common case (a stale
        # collision), but a write can still fail after it (a concurrent
        # writer, a disk error) -- roll back whatever this call itself
        # wrote rather than leave a partial ingest permanently on disk.
        for p in written:
            try:
                p.unlink()
            except OSError:
                pass
        return IngestResult(success=False, error=str(e))

    return IngestResult(success=True, document=doc_path, chunks=chunk_paths, topic=topic, title=title)


def _parse_args(argv: "list[str]") -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="memory-ingest",
        description=(
            "Ingest a web page or file into MemoryVault: one intact full-document "
            "note plus reading-order-linked chunk notes for retrieval. Canonical "
            "Python implementation behind /memory ingest (see SKILL.md)."
        ),
    )
    parser.add_argument("source", help="a URL or a local file path")
    parser.add_argument("--vault-path", help="vault root (default: $MEMORY_VAULT_PATH env var)")
    parser.add_argument("--topic", help="topic slug (kebab-case); omit to get a suggestion first")
    return parser.parse_args(argv[1:])


def _resolve_vault(cli_arg: "str | None") -> "Path | None":
    import os
    if cli_arg:
        p = Path(cli_arg)
        return p if p.is_dir() else None
    env = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if env:
        p = Path(env).expanduser()
        return p if p.is_dir() else None
    return None


def main(argv: "list[str] | None" = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv)
    vault = _resolve_vault(args.vault_path)
    if vault is None:
        print("[ingest] no vault resolved — pass --vault-path or configure MEMORY_VAULT_PATH", file=sys.stderr)
        return 2

    result = ingest(vault, args.source, topic=args.topic)

    if result.needs_confirmation:
        print(f"suggested topic: {result.suggested_topic}")
        if result.title:
            print(f"title: {result.title}")
        print(f"re-run with --topic {result.suggested_topic} to confirm (or pass a different --topic)")
        return 0
    if not result.success:
        print(f"[ingest] failed: {result.error}", file=sys.stderr)
        return 1

    print(f"ingested: {result.document}")
    for c in result.chunks:
        print(f"  chunk: {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
