#!/usr/bin/env python3
"""forward_learning.py — the approved-source forward-learning pipeline
(AG Wave E experience plan, task 1).

Generalizes the existing import-watchlist shape — `adapt_skills.py`
(deterministic rubric enrichment) -> `adapt-evaluator` sub-agent (HIGH/
MEDIUM/LOW judgment) -> `watchlist_review.py` (operator promote/dismiss/
defer) — beyond skills to ideas, patterns, and references, per
`wiki/designs/agentm-experience-and-dreaming.md`'s framing: "find -> screen
-> surface", generalized. Sources are feeds, named repositories, and the
web, operator-configured (opt-in by design — an empty or absent sources
config means this pipeline finds nothing and writes nothing).

Deliberately thin v1 (mirrors the dreaming plan's own "thin first,
calibrate, graduate" precedent): unlike the skill pipeline's two-pass split
(a deterministic Pass 1 that enriches, then a separate `adapt-evaluator`
LLM sub-agent that judges), this module does BOTH in one deterministic
pass — a rubric directly produces the HIGH/MEDIUM/LOW tier, no sub-agent
dispatch. This keeps a scan fully offline-testable (a dry run against a
fixture source set must be red-test-verifiable without a live agent call)
and matches the calibration-era posture the design's own locked call #6
(on the sibling dreaming plan) established for this wave: ship the
deterministic floor first, graduate to a semantic judge once the
deterministic pass has real dogfood evidence. NOT built here: an
LLM-judged Pass 2 (mirroring `adapt-evaluator`) — a natural v2 graduation,
not a silent shortcut.

Reuses `watchlist_review.py`'s operator review surface — the SAME CLI
(list / review / promote / dismiss / defer) now scans BOTH
`personal/_skill-watchlist/` (skills, untouched) and the new
`personal/_watchlist/` (ideas/patterns/references, this module's output) —
one review surface for both, per the design's "generalizes this same
shape" framing. See `watchlist_review.py`'s `_watchlist_roots`.

Contract: this module writes ONLY under `personal/_watchlist/**` (MEDIUM/
HIGH candidates; LOW is dropped, never written) and
`_meta/forward-learning-cache/**` (source watermarks). It never adopts a
finding anywhere else — the whole point of "surfaced, never auto-adopted".

Public surface:

    Source(slug, kind, type, url, trusted=False)
        One operator-configured approved source. `kind` is "idea" |
        "pattern" | "reference" (mirrors the design's three named
        categories); `type` is "feed" | "repo" | "web" — still a single
        stdlib-urllib GET regardless of `type` (informational, not a fetch
        selector), but `default_fetcher` now auto-detects the *response*
        shape: an RSS/Atom body parses into one Candidate per item/entry
        (stdlib `xml.etree.ElementTree`, no new dependency); an HTML body
        that looks like a client-rendered SPA shell (near-empty visible
        text on an otherwise substantial page) retries once through a
        headless-Chromium render if Playwright is installed, else degrades
        to whatever the plain fetch returned — never raises, never blocks
        a scan. Playwright is an OPTIONAL dependency (not in
        `requirements.txt`'s default install — a real browser-binary
        download is too heavy to impose on every install for a capability
        no source has needed yet); see `_render_with_playwright`'s
        docstring for the manual opt-in install command.

    Candidate(slug, title, body, url)
        One raw item a fetcher returned for a source, pre-scoring.

    load_sources(vault_path) -> list[Source]
        Reads the operator's sources config
        (`_meta/forward-learning-sources.json`). Missing file -> `[]`
        (opt-in: no config means no sources means no scan).

    run_forward_learning(vault_path, *, fetcher=None, now=None) -> ScanResult
        One scan pass over every configured source: fetch candidates
        (`fetcher` defaults to `default_fetcher`, injectable for tests so a
        scan is fully offline-deterministic), score each with the
        deterministic rubric, write MEDIUM/HIGH to the watchlist (LOW
        dropped), advance each source's watermark. Returns a summary.

CLI: `python3 forward_learning.py --vault-path <path>`.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import socket
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from vault_lock import atomic_write  # noqa: E402

# Optional dependency (NOT in requirements.txt's default install — see
# _render_with_playwright's docstring). Lazy, guarded import: every other
# capability in this module stays fully usable with Playwright absent.
try:
    from playwright.sync_api import sync_playwright  # type: ignore
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False

__all__ = [
    "Source",
    "Candidate",
    "ScanResult",
    "load_sources",
    "default_fetcher",
    "run_forward_learning",
    "main",
]

_USER_AGENT = "agentm-forward-learning/1.0"
_FETCH_TIMEOUT_SEC = 10

SOURCES_CONFIG_REL = Path("_meta") / "forward-learning-sources.json"
STATE_REL = Path("_meta") / "forward-learning-cache" / "state.json"
WATCHLIST_REL = Path("personal") / "_watchlist"

VALID_KINDS = ("idea", "pattern", "reference")
VALID_TYPES = ("feed", "repo", "web")

# Score thresholds mirror adapt_skills.py's own rubric numbers (>=3 HIGH,
# >=1 MEDIUM, else LOW) — not re-derived, reused deliberately so the two
# pipelines' tiers mean the same thing to an operator reviewing both.
HIGH_THRESHOLD = 3
MEDIUM_THRESHOLD = 1

# A candidate body shorter than this reads as a stub link, not a real find.
_SUBSTANTIVE_BODY_MIN_CHARS = 80


@dataclass(frozen=True)
class Source:
    slug: str
    kind: str
    type: str
    url: str
    trusted: bool = False
    # Optional feed-category allowlist (case-insensitive) -- some feeds tag
    # each item (OpenAI's RSS ships real <category> values including
    # "Research"/"Publication" alongside "Company"/"Product"/"Story").
    # Empty tuple (default) means no filtering -- every parsed item passes.
    # Ignored for non-feed sources (there is nothing to filter on a single
    # whole-page candidate).
    categories: tuple = ()
    # Optional listing-page extraction (a site with no RSS but a real
    # per-item link structure, e.g. anthropic.com/research or
    # deepmind.google/research/publications -- individual research posts
    # each with their own URL, just not exposed as a feed). When set to a
    # URL path prefix (e.g. "/research/"), default_fetcher extracts every
    # `<a href>` under that prefix instead of treating the whole page as
    # one candidate, then fetches each linked page individually for its
    # own title + excerpt. Empty string (default) disables listing mode.
    link_prefix: str = ""


@dataclass(frozen=True)
class Candidate:
    slug: str
    title: str
    body: str
    url: str


@dataclass
class _ScoredCandidate:
    candidate: Candidate
    source: Source
    score: int
    rules_fired: list = field(default_factory=list)

    @property
    def tier(self) -> str:
        if self.score >= HIGH_THRESHOLD:
            return "HIGH"
        if self.score >= MEDIUM_THRESHOLD:
            return "MEDIUM"
        return "LOW"


@dataclass
class ScanResult:
    sources_scanned: int
    candidates_seen: int
    written: list  # list of Path — one per watchlist entry actually written
    dropped_low: int
    already_seen: int = 0  # candidates skipped: identical identity surfaced on a prior scan


# -----------------------------------------------------------------------------
# Sources config + watermark state
# -----------------------------------------------------------------------------

def load_sources(vault_path: Path) -> list:
    path = Path(vault_path) / SOURCES_CONFIG_REL
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    sources = []
    for entry in data.get("sources", []):
        kind = entry.get("kind")
        type_ = entry.get("type")
        if kind not in VALID_KINDS or type_ not in VALID_TYPES:
            continue  # malformed entry — skip, don't fail the whole scan
        raw_categories = entry.get("categories") or []
        sources.append(
            Source(
                slug=entry["slug"],
                kind=kind,
                type=type_,
                url=entry["url"],
                trusted=bool(entry.get("trusted", False)),
                categories=tuple(str(c).lower() for c in raw_categories),
                link_prefix=str(entry.get("link_prefix") or ""),
            )
        )
    return sources


def _state_path(vault_path: Path) -> Path:
    return Path(vault_path) / STATE_REL


def _load_state(vault_path: Path) -> dict:
    path = _state_path(vault_path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(vault_path: Path, state: dict) -> None:
    atomic_write(_state_path(vault_path), json.dumps(state, indent=2, sort_keys=True))


# -----------------------------------------------------------------------------
# Fetching (stdlib-only, mirrors adapt_skills.py / discover_skills.py's
# urllib pattern; graceful on any network failure — never blocks the scan)
# -----------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")

# A JS-shell tell: a substantial page (real markup, not a 404 stub) whose
# stripped visible text is still tiny -- the content only exists after
# client-side rendering the plain GET can't see. Both thresholds are
# deliberately loose (a real, sparse server-rendered page still clears
# them easily); this is a cheap heuristic, not a certainty.
_JS_SHELL_MIN_PAGE_BYTES = 2000
_JS_SHELL_MAX_VISIBLE_CHARS = 500


def _strip_html_tags(text: str) -> str:
    """Rough visible-text extraction: drop script/style bodies, strip
    remaining tags, unescape entities, collapse whitespace. Not a real
    HTML parser -- good enough for the JS-shell heuristic and for turning
    an RSS <description>'s embedded HTML into plain text."""
    text = _SCRIPT_STYLE_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _looks_like_js_shell(page_text: str) -> bool:
    """True when a substantial page's visible text is suspiciously thin --
    the signature of an SPA root div the plain fetch can't render into."""
    if len(page_text) < _JS_SHELL_MIN_PAGE_BYTES:
        return False  # too small a page to judge either way -- not a shell, just sparse
    return len(_strip_html_tags(page_text)) < _JS_SHELL_MAX_VISIBLE_CHARS


def _looks_like_feed(body: bytes) -> bool:
    """Sniff the first non-whitespace bytes for an XML/RSS/Atom root --
    cheaper and more reliable than a Content-Type header (feeds are
    inconsistently served as application/xml, application/rss+xml,
    text/xml, or even text/html in the wild)."""
    head = body.lstrip()[:200].lower()
    return head.startswith(b"<?xml") or b"<rss" in head or b"<feed" in head


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _feed_item_text(elem) -> tuple:
    """(title, body_text, link, categories) for one RSS <item> or Atom
    <entry> -- namespace-agnostic (matches by local tag name), since RSS
    is unprefixed and Atom is fully namespaced and real-world feeds are
    not always consistent about declaring `xmlns:content`/`xmlns:atom`."""
    title, body_raw, link = "", "", ""
    categories = []
    for child in elem:
        local = _local_name(child.tag)
        if local == "title" and not title:
            title = (child.text or "").strip()
        elif local in ("description", "summary", "content", "encoded") and not body_raw:
            body_raw = (child.text or "").strip()
        elif local == "link":
            href = child.get("href")  # Atom: <link href="..."/>
            if href and not link:
                link = href
            elif child.text and not link:  # RSS: <link>url</link>
                link = child.text.strip()
        elif local == "category":
            # RSS: <category>Research</category>; Atom: <category term="Research"/>
            val = (child.get("term") or child.text or "").strip()
            if val:
                categories.append(val)
    body_text = _strip_html_tags(body_raw) if "<" in body_raw else body_raw
    return title, body_text, link, tuple(categories)


# A real feed can carry a full archive (OpenAI's news RSS returned 1039
# items on first fetch, 2026-07-19 live test) -- feeds are conventionally
# newest-first, so capping to the first N here is a "recent items" filter,
# not an arbitrary truncation. Cross-scan dedup (_candidate_identity, the
# per-source `seen` list in run_forward_learning) is what actually keeps a
# steady-state daily scan small; this cap only bounds the very first scan
# of a source with deep history.
_MAX_FEED_ITEMS_PER_SCAN = 20


def _parse_feed(body: bytes, source: Source) -> list:
    """Parse an RSS 2.0 or Atom body into one Candidate per <item>/<entry>,
    capped to the first `_MAX_FEED_ITEMS_PER_SCAN` *matching* items (feeds
    are conventionally newest-first). When `source.categories` is
    non-empty, an item is kept only if at least one of its own
    `<category>` values case-insensitively matches -- the cap counts
    matches, not raw items scanned, so a category filter doesn't starve
    itself against a feed where the wanted tag is a small minority.
    Returns `[]` on any parse failure or if no items/entries were found --
    the caller falls back to whole-body treatment, never raises."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []

    wanted = {c.lower() for c in source.categories}
    candidates = []
    for elem in root.iter():
        if len(candidates) >= _MAX_FEED_ITEMS_PER_SCAN:
            break
        if _local_name(elem.tag) not in ("item", "entry"):
            continue
        title, body_text, link, item_categories = _feed_item_text(elem)
        if not (title or body_text):
            continue
        if wanted and not (wanted & {c.lower() for c in item_categories}):
            continue
        candidates.append(
            Candidate(slug=source.slug, title=title or source.slug, body=body_text, url=link or source.url)
        )
    return candidates


def _render_with_playwright(
    url: str, *, timeout_sec: int = _FETCH_TIMEOUT_SEC, want_html: bool = False
) -> "Optional[str]":
    """Best-effort headless-Chromium render of `url`. Returns the rendered
    page's visible body text by default, or its full rendered HTML when
    `want_html=True` (needed to extract real `<a href>` elements a JS-
    rendered page's initial markup never carries — a listing page's
    individual item links only exist post-render). Returns `None` on any
    failure -- Playwright not installed, browser binary not installed,
    navigation timeout, or any other error. Never raises; the caller
    degrades to whatever the plain GET already returned.

    Optional dependency, deliberately NOT in requirements.txt's default
    install (a browser-binary download is too heavy to impose on every
    agentm install for a capability no configured source needed at v1 —
    2026-07-19 inventory, PLAN-dormant-wake task 2; confirmed genuinely
    needed the same day once DeepMind's publications listing turned out to
    be JS-only, task 4 follow-up). Manual opt-in:

        python3 -m pip install --user playwright
        python3 -m playwright install chromium
    """
    if not _PLAYWRIGHT_AVAILABLE:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(user_agent=_USER_AGENT)
                page.goto(url, timeout=timeout_sec * 1000, wait_until="networkidle")
                if want_html:
                    result = page.content()
                else:
                    # Prefer the semantic content landmark over the whole
                    # <body> -- a real site's global nav/header/footer chrome
                    # (present on every page) otherwise dominates the text,
                    # burying the actual article behind thousands of nav-menu
                    # characters (found live, DeepMind's publication pages:
                    # <main> alone is ~1.5K focused chars vs ~6K for <body>,
                    # nearly all of it repeated site-wide nav).
                    result = None
                    for selector in ("main", "article", "[role=main]"):
                        locator = page.locator(selector)
                        if locator.count() > 0:
                            result = locator.first.inner_text()
                            break
                    if result is None:
                        result = page.inner_text("body")
            finally:
                browser.close()
        return result
    except Exception:
        return None


# ── listing-page extraction (no feed, but a real per-item link structure) ──
# anthropic.com/research and deepmind.google/research/publications both
# have zero RSS/Atom but list individual, distinctly-URLed research posts.
# Set `link_prefix` on a Source to switch default_fetcher from whole-page
# treatment to: extract every matching link, fetch each one individually.

# A link's first path segment right after `link_prefix` gets excluded when
# it matches one of these -- generic pagination/section markers found on
# real listing pages (DeepMind's own `/research/publications/page/2/`;
# Anthropic's `/research/team/...` sub-section, not a paper). A small,
# explainable allowlist-of-exclusions rather than per-site bespoke parsing.
_LISTING_EXCLUDE_FIRST_SEGMENT = {"page", "team"}


class _LinkExtractor(HTMLParser):
    """Collects (absolute_url, anchor_text) for every `<a href>` whose
    resolved path starts with `link_prefix` and isn't excluded. Anchor text
    across nested tags (e.g. `<a><span>Title</span></a>`) is concatenated --
    HTMLParser delivers handle_data per text node, not per element."""

    def __init__(self, link_prefix: str, base_url: str):
        super().__init__()
        self.link_prefix = link_prefix
        self.base_url = base_url
        self.links: "list[tuple[str, str]]" = []
        self._current_href: "Optional[str]" = None
        self._current_text: "list[str]" = []

    def _is_wanted(self, href: str) -> bool:
        absolute = urljoin(self.base_url, href)
        path = urlparse(absolute).path
        if not path.startswith(self.link_prefix):
            return False
        remainder = path[len(self.link_prefix):].strip("/")
        if not remainder:
            return False  # the listing page's own self-link
        first_segment = remainder.split("/", 1)[0]
        return first_segment not in _LISTING_EXCLUDE_FIRST_SEGMENT

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href and self._is_wanted(href):
            self._current_href = urljoin(self.base_url, href)
            self._current_text = []

    def handle_data(self, data):
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._current_href is not None:
            text = " ".join("".join(self._current_text).split())
            self.links.append((self._current_href, text))
            self._current_href = None
            self._current_text = []


def _extract_links(html_text: str, link_prefix: str, base_url: str) -> "list[tuple[str, str]]":
    """[(absolute_url, anchor_text), ...] for every matching link, in
    document order, deduped by URL (first occurrence wins -- a listing page
    sometimes wraps both a thumbnail and a title in separate anchors to the
    same article). Malformed HTML never raises -- HTMLParser is lenient by
    design; any construction error here degrades to no links found."""
    parser = _LinkExtractor(link_prefix, base_url)
    try:
        parser.feed(html_text)
    except Exception:
        return []
    seen_urls = set()
    out = []
    for url, text in parser.links:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        out.append((url, text))
    return out


_MAIN_TAG_RE = re.compile(r"<main[^>]*>(.*?)</main>", re.DOTALL | re.IGNORECASE)
_ARTICLE_TAG_RE = re.compile(r"<article[^>]*>(.*?)</article>", re.DOTALL | re.IGNORECASE)


def _extract_main_content(html_text: str) -> "Optional[str]":
    """The stripped text of `<main>` or `<article>` (first match, in that
    order), or `None` if neither tag is present. A real page's global
    nav/header/footer chrome (present on every page site-wide) otherwise
    dominates a whole-body extraction -- found live on DeepMind's
    publication pages: `<main>` alone is ~1.5K focused chars vs ~6K for
    the whole body, nearly all of it repeated site-wide nav. Naive
    non-greedy regex, not a real HTML parser -- correct for the (very
    common) non-nested case, and `<main>`/nested-`<main>` is invalid HTML
    per spec anyway, so this holds for any spec-conformant page."""
    for pattern in (_MAIN_TAG_RE, _ARTICLE_TAG_RE):
        m = pattern.search(html_text)
        if m:
            text = _strip_html_tags(m.group(1))
            if text:
                return text
    return None


def _fetch_page_text(url: str) -> "tuple[str, str]":
    """(title, body_text) for one page: a plain GET, a JS-shell retry
    through Playwright when available, tags stripped either way. Title
    comes from `<title>`, falling back to the first `<h1>`. Body prefers
    `<main>`/`<article>` content over the whole page (`_extract_main_content`
    -- Playwright's own render path applies the equivalent selector-based
    preference, see `_render_with_playwright`). Empty strings on any fetch
    failure -- the caller drops a candidate with no title and no body
    rather than raising."""
    req = Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urlopen(req, timeout=_FETCH_TIMEOUT_SEC) as resp:
            if getattr(resp, "status", 200) >= 400:
                return "", ""
            raw = resp.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, socket.timeout, OSError):
        return "", ""

    title = _extract_title(raw)

    if _looks_like_js_shell(raw):
        rendered = _render_with_playwright(url)
        if rendered:
            return title, rendered.strip()

    return title, _extract_main_content(raw) or _strip_html_tags(raw)


_TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)
_H1_TAG_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.DOTALL | re.IGNORECASE)


def _extract_title(html_text: str) -> str:
    for pattern in (_TITLE_TAG_RE, _H1_TAG_RE):
        m = pattern.search(html_text)
        if m:
            title = _strip_html_tags(m.group(1))
            if title:
                return title
    return ""


def _fetch_listing_candidates(source: Source) -> list:
    """One Candidate per link found under `source.link_prefix` on the
    listing page (each fetched individually for its own title + excerpt),
    capped to `_MAX_FEED_ITEMS_PER_SCAN`. [] on any failure to even load
    the listing page -- default_fetcher's own graceful-degradation
    contract, never raises."""
    req = Request(source.url, headers={"User-Agent": _USER_AGENT})
    try:
        with urlopen(req, timeout=_FETCH_TIMEOUT_SEC) as resp:
            if getattr(resp, "status", 200) >= 400:
                return []
            raw = resp.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, socket.timeout, OSError):
        return []

    html_text = raw
    if _looks_like_js_shell(raw):
        rendered_html = _render_with_playwright(source.url, want_html=True)
        if rendered_html:
            html_text = rendered_html
        # else: Playwright absent or the render failed -- fall through and
        # try extraction against the plain (likely JS-shell, likely
        # link-less) HTML anyway; an empty result degrades to [] below,
        # never a crash.

    links = _extract_links(html_text, source.link_prefix, source.url)[:_MAX_FEED_ITEMS_PER_SCAN]

    candidates = []
    for url, anchor_text in links:
        title, body = _fetch_page_text(url)
        if not (title or anchor_text or body):
            continue
        candidates.append(Candidate(slug=source.slug, title=title or anchor_text or source.slug, body=body, url=url))
    return candidates


_GITHUB_API_REPOS_RE = re.compile(r"^https://api\.github\.com/(orgs|users)/[^/]+/repos", re.IGNORECASE)


def _looks_like_github_api_repos_url(url: str) -> bool:
    """True for a GitHub REST API org/user repo-listing endpoint. A source
    pointed directly at one of these (rather than scraping the HTML org
    page, whose per-repo sub-links -- /forks, /issues, /pulls, /stargazers
    -- all match a naive link-prefix filter and would multiply one repo
    into several near-duplicate candidates) gets clean, structured, one-
    candidate-per-repo results instead."""
    return bool(_GITHUB_API_REPOS_RE.match(url))


def _parse_github_api_repos(body: bytes, source: Source) -> list:
    """One Candidate per repo from a GitHub org/user repos API response
    (already sorted newest-first when the source URL requests that, e.g.
    `?sort=created&direction=desc`), capped to `_MAX_FEED_ITEMS_PER_SCAN`.
    [] on any parse failure -- malformed JSON, an error envelope instead of
    a list (rate-limited, org not found), never raises."""
    try:
        repos = json.loads(body)
    except json.JSONDecodeError:
        return []
    if not isinstance(repos, list):
        return []  # GitHub's error shape is a dict ({"message": "..."}), not a list
    candidates = []
    for repo in repos[:_MAX_FEED_ITEMS_PER_SCAN]:
        if not isinstance(repo, dict):
            continue
        name = repo.get("name") or ""
        description = repo.get("description") or ""
        html_url = repo.get("html_url") or source.url
        if not name:
            continue
        candidates.append(Candidate(slug=source.slug, title=name, body=description, url=html_url))
    return candidates


def default_fetcher(source: Source) -> list:
    """Best-effort single GET of `source.url`. Response-shape auto-detect:
    a GitHub API repos-listing URL parses into one Candidate per repo
    (`_parse_github_api_repos`); `source.link_prefix` set -> listing-page
    extraction, one Candidate per linked item (`_fetch_listing_candidates`);
    an RSS/Atom body parses into one Candidate per item/entry
    (`_parse_feed`); an HTML body that looks like a client-rendered SPA
    shell (`_looks_like_js_shell`) retries once through a headless render
    when Playwright is installed, else degrades to the plain-fetch text.
    Returns `[]` on any network error (timeouts/4xx/5xx never fail a scan,
    same graceful-degradation posture as `adapt_skills.py`'s GitHub
    enrichment)."""
    if _looks_like_github_api_repos_url(source.url):
        req = Request(source.url, headers={"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"})
        try:
            with urlopen(req, timeout=_FETCH_TIMEOUT_SEC) as resp:
                if getattr(resp, "status", 200) >= 400:
                    return []
                return _parse_github_api_repos(resp.read(), source)
        except (HTTPError, URLError, socket.timeout, OSError):
            return []

    if source.link_prefix:
        return _fetch_listing_candidates(source)

    req = Request(source.url, headers={"User-Agent": _USER_AGENT})
    try:
        with urlopen(req, timeout=_FETCH_TIMEOUT_SEC) as resp:
            if getattr(resp, "status", 200) >= 400:
                return []
            raw = resp.read()
    except (HTTPError, URLError, socket.timeout, OSError):
        return []

    if _looks_like_feed(raw):
        feed_candidates = _parse_feed(raw, source)
        if feed_candidates:
            return feed_candidates
        # parsed as XML but yielded no items/entries -- fall through to
        # whole-body treatment rather than silently returning nothing

    body = raw.decode("utf-8", errors="replace")

    if _looks_like_js_shell(body):
        rendered = _render_with_playwright(source.url)
        if rendered:
            body = rendered
        # else: Playwright absent or the render itself failed -- degrade to
        # the plain-fetch body (likely near-empty; the rubric's own
        # substantiveness-floor rule already scores that LOW rather than
        # this function needing to guess or raise)

    # Strip markup for the watchlist excerpt's sake (_write_watchlist_entry
    # truncates candidate.body to 400 chars for the entry preview -- raw
    # HTML there was mostly closing/opening tags, not readable content). A
    # no-op on an already-plain-text Playwright render (no tags present).
    body = _strip_html_tags(body)

    return [Candidate(slug=source.slug, title=source.slug, body=body, url=source.url)]


# -----------------------------------------------------------------------------
# The deterministic rubric (generalizes adapt_skills.py's 6-rule shape)
# -----------------------------------------------------------------------------

def _score_candidate(candidate: Candidate, source: Source, *, existing_tags: set) -> tuple:
    """Returns (score, rules_fired). Deterministic, offline, no network."""
    score = 0
    rules = []

    if source.trusted:
        score += 1
        rules.append("trusted_source")

    if len(candidate.body.strip()) >= _SUBSTANTIVE_BODY_MIN_CHARS:
        score += 1
        rules.append("substantive_body")

    body_lower = candidate.body.lower()
    if any(tag.lower() in body_lower for tag in existing_tags):
        score += 1
        rules.append("complements_existing_convention")

    return score, rules


# -----------------------------------------------------------------------------
# Watchlist write (reuses personal/_watchlist/ — the generalized sibling of
# personal/_skill-watchlist/; watchlist_review.py scans both)
# -----------------------------------------------------------------------------

def _slugify(text: str) -> str:
    out = []
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-") or "item"


def _write_watchlist_entry(
    vault_path: Path, scored: "_ScoredCandidate", *, now_iso: str
) -> Path:
    source = scored.source
    candidate = scored.candidate
    item_slug = _slugify(candidate.title or candidate.slug)
    entry_dir = Path(vault_path) / WATCHLIST_REL / source.slug
    entry_path = entry_dir / f"{item_slug}.md"

    excerpt = candidate.body.strip()[:400]
    content = (
        "---\n"
        f"kind: {source.kind}\n"
        "status: pending-review\n"
        f"created: {now_iso}\n"
        f"updated: {now_iso}\n"
        f"source_slug: {source.slug}\n"
        f"source_url: {candidate.url}\n"
        f"source_type: {source.type}\n"
        f"evaluator_classification: {scored.tier}\n"
        f"rubric_score: {scored.score}\n"
        f"rubric_rules_fired: {json.dumps(scored.rules_fired)}\n"
        "---\n"
        f"# {candidate.title}\n\n"
        f"{excerpt}\n\n"
        f"Source: {candidate.url}\n"
    )
    atomic_write(entry_path, content)
    return entry_path


# -----------------------------------------------------------------------------
# The scan
# -----------------------------------------------------------------------------

# Per-source cross-scan dedup (2026-07-19, PLAN-dormant-wake task 4 — a real
# gap surfaced live on the first supervised run: a big RSS feed's ~1000
# items all cleared MEDIUM on every trusted source, and with no per-item
# memory a re-scan would re-surface and re-write the exact same items
# forever). Bounded so a source's `seen` list can't grow without limit.
_SEEN_CAP = 500


def _candidate_identity(candidate: Candidate, source: Source) -> str:
    """A stable per-candidate identity for cross-scan dedup.

    A feed item's `url` is its own real permalink (distinct per article) --
    dedup by that directly. A whole-page candidate's `url` is always
    `source.url` itself (default_fetcher's single-candidate fallback), so
    the same URL recurring is meaningless -- the page's *content* is what
    changes between scans, not its address -- dedup by a content hash
    instead."""
    if candidate.url and candidate.url != source.url:
        return candidate.url
    return "content:" + hashlib.sha256(candidate.body.encode("utf-8", errors="replace")).hexdigest()[:16]


def _existing_tags(vault_path: Path) -> set:
    """A cheap 'complements existing conventions' signal: every source
    slug + kind already seen in the vault's forward-learning watermark
    state, plus each configured source's own slug — thin v1's stand-in for
    a real corpus-tag index (avoids a heavy recall.py dependency here)."""
    tags = set()
    for source in load_sources(vault_path):
        tags.add(source.slug)
        tags.add(source.kind)
    return tags


def run_forward_learning(
    vault_path: Path, *, fetcher: Optional[Callable] = None, now: Optional[float] = None
) -> ScanResult:
    vault_path = Path(vault_path)
    fetcher = fetcher or default_fetcher
    now = now if now is not None else time.time()
    now_iso = datetime.fromtimestamp(now, tz=timezone.utc).replace(microsecond=0).isoformat()

    sources = load_sources(vault_path)
    state = _load_state(vault_path)
    tags = _existing_tags(vault_path)

    written = []
    candidates_seen = 0
    dropped_low = 0
    already_seen = 0

    for source in sources:
        source_state = state.setdefault(source.slug, {})
        seen = set(source_state.get("seen", []))
        new_seen_order = list(source_state.get("seen", []))  # preserve insertion order for the cap trim

        candidates = fetcher(source)
        candidates_seen += len(candidates)
        for candidate in candidates:
            identity = _candidate_identity(candidate, source)
            if identity in seen:
                already_seen += 1
                continue
            seen.add(identity)
            new_seen_order.append(identity)

            score, rules = _score_candidate(candidate, source, existing_tags=tags)
            scored = _ScoredCandidate(candidate=candidate, source=source, score=score, rules_fired=rules)
            if scored.tier == "LOW":
                dropped_low += 1
                continue
            written.append(_write_watchlist_entry(vault_path, scored, now_iso=now_iso))

        source_state["last_scan"] = now_iso
        source_state["seen"] = new_seen_order[-_SEEN_CAP:]

    _save_state(vault_path, state)

    return ScanResult(
        sources_scanned=len(sources),
        candidates_seen=candidates_seen,
        written=written,
        dropped_low=dropped_low,
        already_seen=already_seen,
    )


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _resolve_vault_path(arg_vault_path: Optional[str]) -> Optional[Path]:
    import os

    if arg_vault_path:
        return Path(arg_vault_path).expanduser()
    env_path = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    return Path(env_path).expanduser() if env_path else None


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Run one forward-learning scan pass.")
    parser.add_argument("--vault-path", help="MemoryVault root (overrides MEMORY_VAULT_PATH env var)")
    args = parser.parse_args(argv)

    vault = _resolve_vault_path(args.vault_path)
    if vault is None or not vault.exists():
        print("ERROR: no vault path resolved (set --vault-path or MEMORY_VAULT_PATH)", file=sys.stderr)
        return 1

    result = run_forward_learning(vault)
    print(
        f"forward-learning: {result.sources_scanned} source(s), "
        f"{result.candidates_seen} candidate(s) seen, {result.already_seen} already-seen "
        f"(skipped), {len(result.written)} written to the watchlist, {result.dropped_low} dropped (LOW)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
