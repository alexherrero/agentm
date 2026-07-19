#!/usr/bin/env python3
"""Unit tests for `forward_learning.py` — the approved-source forward-
learning pipeline (AG Wave E experience plan, task 1).

`forward_learning.py` lives in `harness/skills/memory/scripts/` (same
cross-dir import pattern as `test_dream.py` / `test_revert_log.py`).

Covers (plan task 1 verification):
  - a dry run against a fixture source set produces watchlist entries
    classified HIGH/MEDIUM/LOW
  - red-test: zero auto-adoption — no file OUTSIDE personal/_watchlist/**
    and _meta/forward-learning-cache/** changes as a result of a scan
  - LOW-scored candidates are dropped, never written to the watchlist
  - no configured sources (opt-in, absent config) -> scan finds nothing,
    writes nothing
  - the watermark (per-source last_scan) advances after a scan
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import forward_learning as fl  # noqa: E402


class _ForwardLearningTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir()

    def _write_sources(self, sources: list) -> None:
        path = self.vault / fl.SOURCES_CONFIG_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"sources": sources}), encoding="utf-8")

    def _snapshot(self) -> dict:
        return {
            str(p.relative_to(self.vault)): p.read_bytes()
            for p in sorted(self.vault.rglob("*"))
            if p.is_file()
        }


def _fixture_fetcher(candidates_by_slug: dict):
    def fetcher(source):
        return candidates_by_slug.get(source.slug, [])
    return fetcher


class DryRunFixtureSourceSetTests(_ForwardLearningTestBase):
    def setUp(self) -> None:
        super().setUp()
        self._write_sources(
            [
                {"slug": "high-src", "kind": "idea", "type": "feed", "url": "https://example.com/high", "trusted": True},
                {"slug": "medium-src", "kind": "pattern", "type": "repo", "url": "https://example.com/medium", "trusted": False},
                {"slug": "low-src", "kind": "reference", "type": "web", "url": "https://example.com/low", "trusted": False},
            ]
        )
        self.fetcher = _fixture_fetcher(
            {
                # trusted (+1) + substantive body >=80 chars (+1) + mentions
                # its own slug (existing tag, +1) = score 3 -> HIGH
                "high-src": [
                    fl.Candidate(
                        slug="high-src",
                        title="A genuinely new technique",
                        body=(
                            "high-src describes a genuinely new technique worth adopting, "
                            "with enough detail here to clear the substantiveness floor."
                        ),
                        url="https://example.com/high/1",
                    )
                ],
                # not trusted, substantive body (+1), mentions its own slug (+1) = score 2 -> MEDIUM
                "medium-src": [
                    fl.Candidate(
                        slug="medium-src",
                        title="An interesting pattern",
                        body=(
                            "medium-src has an interesting pattern that might be worth "
                            "recording for later, though it is not clearly a fit yet."
                        ),
                        url="https://example.com/medium/1",
                    )
                ],
                # not trusted, body too short, no tag match = score 0 -> LOW, dropped
                "low-src": [
                    fl.Candidate(slug="low-src", title="Nothing much", body="tiny", url="https://example.com/low/1")
                ],
            }
        )

    def test_produces_watchlist_entries_classified_high_medium_low(self) -> None:
        pre_snapshot = self._snapshot()
        result = fl.run_forward_learning(self.vault, fetcher=self.fetcher, now=1_700_000_000.0)

        self.assertEqual(result.sources_scanned, 3)
        self.assertEqual(result.candidates_seen, 3)
        self.assertEqual(len(result.written), 2)  # HIGH + MEDIUM only
        self.assertEqual(result.dropped_low, 1)

        tiers = set()
        for path in result.written:
            fm_text = path.read_text(encoding="utf-8")
            self.assertIn("evaluator_classification:", fm_text)
            if "evaluator_classification: HIGH" in fm_text:
                tiers.add("HIGH")
            elif "evaluator_classification: MEDIUM" in fm_text:
                tiers.add("MEDIUM")
        self.assertEqual(tiers, {"HIGH", "MEDIUM"})

        # zero auto-adoption: every changed/new path is under _watchlist/ or
        # the forward-learning cache — nothing else in the vault moved.
        post_snapshot = self._snapshot()
        changed_paths = set(post_snapshot) - set(pre_snapshot)
        changed_paths |= {p for p in pre_snapshot if pre_snapshot.get(p) != post_snapshot.get(p)}
        for rel in changed_paths:
            self.assertTrue(
                rel.startswith(str(fl.WATCHLIST_REL)) or rel.startswith(str(fl.STATE_REL.parent)),
                f"unexpected write outside the watchlist/cache: {rel}",
            )

    def test_low_scored_candidate_is_never_written(self) -> None:
        fl.run_forward_learning(self.vault, fetcher=self.fetcher, now=1_700_000_000.0)
        low_dir = self.vault / fl.WATCHLIST_REL / "low-src"
        self.assertFalse(low_dir.exists())

    def test_watermark_advances_after_scan(self) -> None:
        state_before = fl._load_state(self.vault)
        self.assertEqual(state_before, {})
        fl.run_forward_learning(self.vault, fetcher=self.fetcher, now=1_700_000_000.0)
        state_after = fl._load_state(self.vault)
        for slug in ("high-src", "medium-src", "low-src"):
            self.assertIn("last_scan", state_after[slug])


class CrossScanDedupTests(_ForwardLearningTestBase):
    """A real gap found live on PLAN-dormant-wake task 4's first supervised
    run: a big feed's items all cleared MEDIUM on every trusted source, and
    with no per-item memory a re-scan re-wrote the exact same items every
    time. _candidate_identity + the per-source `seen` list fix this."""

    def test_rescan_does_not_rewrite_already_seen_candidate(self) -> None:
        self._write_sources([{"slug": "src", "kind": "idea", "type": "web", "url": "https://example.com/src", "trusted": True}])
        candidate = fl.Candidate(
            slug="src", title="A finding", body="A substantive body that clears the 80-char floor easily, see.", url="https://example.com/src/article-1"
        )
        fetcher = _fixture_fetcher({"src": [candidate]})

        first = fl.run_forward_learning(self.vault, fetcher=fetcher, now=1_700_000_000.0)
        self.assertEqual(len(first.written), 1)
        self.assertEqual(first.already_seen, 0)

        second = fl.run_forward_learning(self.vault, fetcher=fetcher, now=1_700_000_100.0)
        self.assertEqual(len(second.written), 0)
        self.assertEqual(second.already_seen, 1)

    def test_a_genuinely_new_item_from_the_same_source_is_written(self) -> None:
        self._write_sources([{"slug": "src", "kind": "idea", "type": "web", "url": "https://example.com/src", "trusted": True}])
        old = fl.Candidate(slug="src", title="Old", body="Old but substantive enough body content here, yes indeed.", url="https://example.com/src/1")
        new = fl.Candidate(slug="src", title="New", body="New but substantive enough body content here, yes indeed.", url="https://example.com/src/2")

        fl.run_forward_learning(self.vault, fetcher=_fixture_fetcher({"src": [old]}), now=1_700_000_000.0)
        result = fl.run_forward_learning(self.vault, fetcher=_fixture_fetcher({"src": [old, new]}), now=1_700_000_100.0)

        self.assertEqual(result.already_seen, 1)  # old
        self.assertEqual(len(result.written), 1)  # new only
        self.assertIn("New", result.written[0].read_text(encoding="utf-8"))

    def test_whole_page_candidate_dedupes_by_content_not_url(self) -> None:
        """A non-feed candidate's url always equals source.url -- dedup by
        content hash means the SAME page content is skipped on a re-scan,
        but genuinely CHANGED content (same URL) is written again."""
        self._write_sources([{"slug": "page-src", "kind": "idea", "type": "web", "url": "https://example.com/page", "trusted": True}])
        same_body = fl.Candidate(slug="page-src", title="page-src", body="The page content, substantive enough to clear the floor easily.", url="https://example.com/page")

        first = fl.run_forward_learning(self.vault, fetcher=_fixture_fetcher({"page-src": [same_body]}), now=1_700_000_000.0)
        self.assertEqual(len(first.written), 1)

        # Re-fetch returns byte-identical content -- skipped.
        second = fl.run_forward_learning(self.vault, fetcher=_fixture_fetcher({"page-src": [same_body]}), now=1_700_000_100.0)
        self.assertEqual(second.already_seen, 1)
        self.assertEqual(len(second.written), 0)

        # The page's content genuinely changed (same URL) -- written again.
        changed_body = fl.Candidate(slug="page-src", title="page-src", body="Completely different page content, also clears the floor.", url="https://example.com/page")
        third = fl.run_forward_learning(self.vault, fetcher=_fixture_fetcher({"page-src": [changed_body]}), now=1_700_000_200.0)
        self.assertEqual(third.already_seen, 0)
        self.assertEqual(len(third.written), 1)

    def test_seen_list_is_capped(self) -> None:
        self._write_sources([{"slug": "src", "kind": "idea", "type": "web", "url": "https://example.com/src", "trusted": True}])
        for i in range(fl._SEEN_CAP + 50):
            candidate = fl.Candidate(slug="src", title=f"item-{i}", body="x" * 100, url=f"https://example.com/src/{i}")
            fl.run_forward_learning(self.vault, fetcher=_fixture_fetcher({"src": [candidate]}), now=1_700_000_000.0 + i)
        state = fl._load_state(self.vault)
        self.assertLessEqual(len(state["src"]["seen"]), fl._SEEN_CAP)


class CandidateIdentityTests(unittest.TestCase):
    def test_distinct_permalink_uses_url(self) -> None:
        source = fl.Source(slug="s", kind="idea", type="feed", url="https://example.com/feed", trusted=True)
        candidate = fl.Candidate(slug="s", title="t", body="b", url="https://example.com/article-1")
        self.assertEqual(fl._candidate_identity(candidate, source), "https://example.com/article-1")

    def test_whole_page_fallback_uses_content_hash(self) -> None:
        source = fl.Source(slug="s", kind="idea", type="web", url="https://example.com/page", trusted=True)
        candidate = fl.Candidate(slug="s", title="s", body="the page body", url="https://example.com/page")
        identity = fl._candidate_identity(candidate, source)
        self.assertTrue(identity.startswith("content:"))

    def test_same_content_same_identity_different_content_different_identity(self) -> None:
        source = fl.Source(slug="s", kind="idea", type="web", url="https://example.com/page", trusted=True)
        a = fl.Candidate(slug="s", title="s", body="body one", url="https://example.com/page")
        b = fl.Candidate(slug="s", title="s", body="body one", url="https://example.com/page")
        c = fl.Candidate(slug="s", title="s", body="body two", url="https://example.com/page")
        self.assertEqual(fl._candidate_identity(a, source), fl._candidate_identity(b, source))
        self.assertNotEqual(fl._candidate_identity(a, source), fl._candidate_identity(c, source))


class ParseFeedCapTests(unittest.TestCase):
    def test_feed_items_capped_to_max_per_scan(self) -> None:
        source = fl.Source(slug="big-feed", kind="idea", type="feed", url="https://example.com/feed", trusted=True)
        items = "".join(
            f"<item><title>Item {i}</title><description>Body {i}</description><link>https://example.com/{i}</link></item>"
            for i in range(fl._MAX_FEED_ITEMS_PER_SCAN + 30)
        )
        body = f'<?xml version="1.0"?><rss version="2.0"><channel>{items}</channel></rss>'.encode()
        candidates = fl._parse_feed(body, source)
        self.assertEqual(len(candidates), fl._MAX_FEED_ITEMS_PER_SCAN)
        self.assertEqual(candidates[0].title, "Item 0")  # newest-first convention preserved


class NoSourcesConfiguredTests(_ForwardLearningTestBase):
    def test_no_config_finds_nothing_writes_no_watchlist_entries(self) -> None:
        result = fl.run_forward_learning(self.vault, fetcher=_fixture_fetcher({}))
        self.assertEqual(result.sources_scanned, 0)
        self.assertEqual(result.written, [])
        # An (empty) cache state write is expected and allowed; no watchlist
        # dir is ever created when there was nothing to scan.
        self.assertFalse((self.vault / fl.WATCHLIST_REL).exists())


class MalformedSourceEntryTests(_ForwardLearningTestBase):
    def test_malformed_source_entry_is_skipped_not_fatal(self) -> None:
        self._write_sources(
            [
                {"slug": "bad", "kind": "not-a-real-kind", "type": "feed", "url": "https://example.com"},
                {"slug": "good", "kind": "idea", "type": "web", "url": "https://example.com/ok", "trusted": True},
            ]
        )
        sources = fl.load_sources(self.vault)
        self.assertEqual([s.slug for s in sources], ["good"])


class DefaultFetcherGracefulDegradationTests(_ForwardLearningTestBase):
    def test_unreachable_url_returns_empty_not_raises(self) -> None:
        source = fl.Source(slug="x", kind="idea", type="web", url="http://127.0.0.1:1/nope", trusted=False)
        candidates = fl.default_fetcher(source)
        self.assertEqual(candidates, [])


_RSS_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Test Feed</title>
<item><title>First post &amp; more</title><description>&lt;p&gt;Body one&lt;/p&gt;</description><link>https://example.com/1</link></item>
<item><title>Second post</title><description>Body two here</description><link>https://example.com/2</link></item>
</channel></rss>"""

_ATOM_SAMPLE = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Test Atom</title>
<entry><title>Atom post</title><summary>Atom body</summary><link href="https://example.com/atom1" rel="alternate"/></entry>
</feed>"""

_MALFORMED_XML = b"<?xml version=\"1.0\"?><rss><channel><item><title>unterminated"

_EMPTY_FEED = b'<?xml version="1.0"?><rss version="2.0"><channel><title>Empty</title></channel></rss>'


class LooksLikeFeedTests(unittest.TestCase):
    def test_rss_detected(self) -> None:
        self.assertTrue(fl._looks_like_feed(_RSS_SAMPLE))

    def test_atom_detected(self) -> None:
        self.assertTrue(fl._looks_like_feed(_ATOM_SAMPLE))

    def test_html_not_detected(self) -> None:
        self.assertFalse(fl._looks_like_feed(b"<!DOCTYPE html><html><body>hi</body></html>"))

    def test_leading_whitespace_tolerated(self) -> None:
        self.assertTrue(fl._looks_like_feed(b"\n\n  " + _RSS_SAMPLE))


class ParseFeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = fl.Source(slug="test-feed", kind="idea", type="feed", url="https://example.com/feed", trusted=True)

    def test_rss_items_parsed(self) -> None:
        candidates = fl._parse_feed(_RSS_SAMPLE, self.source)
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].title, "First post & more")  # entity-unescaped
        self.assertEqual(candidates[0].body, "Body one")  # HTML-in-description stripped
        self.assertEqual(candidates[0].url, "https://example.com/1")
        self.assertEqual(candidates[0].slug, "test-feed")
        self.assertEqual(candidates[1].title, "Second post")

    def test_atom_entries_parsed(self) -> None:
        candidates = fl._parse_feed(_ATOM_SAMPLE, self.source)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].title, "Atom post")
        self.assertEqual(candidates[0].body, "Atom body")
        self.assertEqual(candidates[0].url, "https://example.com/atom1")

    def test_malformed_xml_returns_empty_not_raises(self) -> None:
        self.assertEqual(fl._parse_feed(_MALFORMED_XML, self.source), [])

    def test_no_items_returns_empty(self) -> None:
        self.assertEqual(fl._parse_feed(_EMPTY_FEED, self.source), [])

    def test_item_missing_link_falls_back_to_source_url(self) -> None:
        body = b'<rss><channel><item><title>No link</title><description>text</description></item></channel></rss>'
        candidates = fl._parse_feed(body, self.source)
        self.assertEqual(candidates[0].url, self.source.url)


class ParseFeedCategoryFilterTests(unittest.TestCase):
    """A real refinement request: general lab news/announcements weren't
    useful, only the research-tagged posts were -- OpenAI's RSS carries
    real <category> values (Research/Publication alongside Company/
    Product/Story) that make this filterable without a new dependency."""

    _RSS_MIXED_CATEGORIES = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>Research post</title><description>A real research finding, quite substantive.</description>
  <link>https://example.com/r1</link><category>Research</category><category>Safety</category></item>
<item><title>Product post</title><description>A product launch announcement, not research at all.</description>
  <link>https://example.com/p1</link><category>Product</category></item>
<item><title>Publication post</title><description>A published paper summary here.</description>
  <link>https://example.com/pub1</link><category>Publication</category></item>
</channel></rss>"""

    def test_no_category_filter_returns_everything(self) -> None:
        source = fl.Source(slug="s", kind="idea", type="feed", url="https://example.com/feed", trusted=True)
        candidates = fl._parse_feed(self._RSS_MIXED_CATEGORIES, source)
        self.assertEqual(len(candidates), 3)

    def test_category_filter_keeps_only_matching_items(self) -> None:
        source = fl.Source(
            slug="s", kind="idea", type="feed", url="https://example.com/feed", trusted=True,
            categories=("research", "publication"),
        )
        candidates = fl._parse_feed(self._RSS_MIXED_CATEGORIES, source)
        titles = {c.title for c in candidates}
        self.assertEqual(titles, {"Research post", "Publication post"})

    def test_category_filter_is_case_insensitive(self) -> None:
        source = fl.Source(
            slug="s", kind="idea", type="feed", url="https://example.com/feed", trusted=True,
            categories=("RESEARCH",),
        )
        candidates = fl._parse_feed(self._RSS_MIXED_CATEGORIES, source)
        self.assertEqual([c.title for c in candidates], ["Research post"])

    def test_category_filter_matching_nothing_returns_empty(self) -> None:
        source = fl.Source(
            slug="s", kind="idea", type="feed", url="https://example.com/feed", trusted=True,
            categories=("nonexistent-category",),
        )
        self.assertEqual(fl._parse_feed(self._RSS_MIXED_CATEGORIES, source), [])

    def test_load_sources_reads_categories_from_config(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            path = vault / fl.SOURCES_CONFIG_REL
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"sources": [
                {"slug": "s", "kind": "idea", "type": "feed", "url": "https://example.com", "categories": ["Research", "Publication"]}
            ]}), encoding="utf-8")
            sources = fl.load_sources(vault)
        self.assertEqual(sources[0].categories, ("research", "publication"))

    def test_load_sources_defaults_categories_to_empty(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            path = vault / fl.SOURCES_CONFIG_REL
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"sources": [
                {"slug": "s", "kind": "idea", "type": "feed", "url": "https://example.com"}
            ]}), encoding="utf-8")
            sources = fl.load_sources(vault)
        self.assertEqual(sources[0].categories, ())


class StripHtmlTagsTests(unittest.TestCase):
    def test_tags_stripped_and_entities_unescaped(self) -> None:
        self.assertEqual(fl._strip_html_tags("<p>Hello &amp; welcome</p>"), "Hello & welcome")

    def test_script_and_style_content_dropped_not_just_tags(self) -> None:
        html_text = "<html><head><style>.x{color:red}</style></head><body><script>var x=1;</script>Real text</body></html>"
        result = fl._strip_html_tags(html_text)
        self.assertEqual(result, "Real text")

    def test_whitespace_collapsed(self) -> None:
        self.assertEqual(fl._strip_html_tags("a   \n\n  b"), "a b")


class ExtractMainContentTests(unittest.TestCase):
    """A real quality issue found live reviewing task 4's second run: a
    site's global nav (present on every page) dominated a whole-body
    excerpt, burying the actual article. <main>/<article> extraction fixes
    it -- verified against real Anthropic/DeepMind pages before writing
    these hermetic fixtures."""

    def test_main_tag_preferred_over_whole_body(self) -> None:
        html_text = (
            "<html><body>"
            "<nav>Home About Research Careers Skip to content Nav nav nav nav nav nav nav nav nav</nav>"
            "<main>The real article abstract goes here, this is what matters.</main>"
            "<footer>Copyright footer nav links</footer>"
            "</body></html>"
        )
        result = fl._extract_main_content(html_text)
        self.assertEqual(result, "The real article abstract goes here, this is what matters.")

    def test_article_tag_used_when_no_main(self) -> None:
        html_text = "<html><body><nav>Nav stuff</nav><article>The real content.</article></body></html>"
        result = fl._extract_main_content(html_text)
        self.assertEqual(result, "The real content.")

    def test_main_preferred_over_article_when_both_present(self) -> None:
        html_text = "<html><body><main>Main content wins.</main><article>Article content loses.</article></body></html>"
        result = fl._extract_main_content(html_text)
        self.assertEqual(result, "Main content wins.")

    def test_neither_tag_returns_none(self) -> None:
        self.assertIsNone(fl._extract_main_content("<html><body>Just a body, no landmarks.</body></html>"))

    def test_empty_main_falls_through_to_none(self) -> None:
        self.assertIsNone(fl._extract_main_content("<html><body><main></main></body></html>"))


class LooksLikeJsShellTests(unittest.TestCase):
    def test_spa_shell_detected(self) -> None:
        shell = "<html><body><div id='root'></div></body></html>" + (" " * 3000)
        self.assertTrue(fl._looks_like_js_shell(shell))

    def test_real_content_page_not_flagged(self) -> None:
        real = "<html><body>" + ("<p>Real article content, quite a lot of it.</p>" * 20) + "</body></html>"
        self.assertFalse(fl._looks_like_js_shell(real))

    def test_tiny_page_not_flagged_too_small_to_judge(self) -> None:
        self.assertFalse(fl._looks_like_js_shell("<html></html>"))


class RenderWithPlaywrightTests(unittest.TestCase):
    def test_playwright_unavailable_returns_none(self) -> None:
        with mock.patch.object(fl, "_PLAYWRIGHT_AVAILABLE", False):
            self.assertIsNone(fl._render_with_playwright("https://example.com"))

    def test_playwright_available_and_succeeds(self) -> None:
        class _FakeLocator:
            def count(self):
                return 0

        class _FakePage:
            def goto(self, url, timeout=None, wait_until=None):
                pass

            def inner_text(self, selector):
                return "rendered visible text"

            def content(self):
                return "<html><body>rendered visible text</body></html>"

            def locator(self, selector):
                return _FakeLocator()  # no main/article/[role=main] in this fake -- falls back to body

        class _FakeBrowser:
            def new_page(self, user_agent=None):
                return _FakePage()

            def close(self):
                pass

        class _FakeChromium:
            def launch(self):
                return _FakeBrowser()

        class _FakePlaywrightCtx:
            def __enter__(self):
                ctx = mock.Mock()
                ctx.chromium = _FakeChromium()
                return ctx

            def __exit__(self, *exc):
                return False

        with mock.patch.object(fl, "_PLAYWRIGHT_AVAILABLE", True), \
             mock.patch.object(fl, "sync_playwright", lambda: _FakePlaywrightCtx(), create=True):
            result = fl._render_with_playwright("https://example.com")
        self.assertEqual(result, "rendered visible text")

    def test_playwright_available_but_render_raises_returns_none(self) -> None:
        def _raising_sync_playwright():
            raise RuntimeError("browser not installed")

        with mock.patch.object(fl, "_PLAYWRIGHT_AVAILABLE", True), \
             mock.patch.object(fl, "sync_playwright", _raising_sync_playwright, create=True):
            self.assertIsNone(fl._render_with_playwright("https://example.com"))


_LISTING_HTML = b"""<html><body>
<nav><a href="/about">About</a><a href="/research/team/alignment">Team</a></nav>
<a href="/research/paper-one">Paper One</a>
<a href="/research/paper-two"><span>Paper</span> <span>Two</span></a>
<a href="/research/paper-one">Paper One (duplicate link)</a>
<a href="/research/">Self link</a>
<a href="/research/page/2">Pagination</a>
<a href="https://external.com/other">External, not under prefix</a>
</body></html>"""


class ExtractLinksTests(unittest.TestCase):
    def test_matching_links_extracted_with_text(self) -> None:
        links = fl._extract_links(_LISTING_HTML.decode(), "/research/", "https://example.com/research/")
        urls = [u for u, _ in links]
        self.assertIn("https://example.com/research/paper-one", urls)
        self.assertIn("https://example.com/research/paper-two", urls)

    def test_nested_tags_in_anchor_text_concatenated(self) -> None:
        links = fl._extract_links(_LISTING_HTML.decode(), "/research/", "https://example.com/research/")
        by_url = dict(links)
        self.assertEqual(by_url["https://example.com/research/paper-two"], "Paper Two")

    def test_team_and_pagination_excluded(self) -> None:
        links = fl._extract_links(_LISTING_HTML.decode(), "/research/", "https://example.com/research/")
        urls = [u for u, _ in links]
        self.assertFalse(any("team" in u for u in urls))
        self.assertFalse(any("page" in u for u in urls))

    def test_self_link_excluded(self) -> None:
        links = fl._extract_links(_LISTING_HTML.decode(), "/research/", "https://example.com/research/")
        urls = [u for u, _ in links]
        self.assertNotIn("https://example.com/research/", urls)

    def test_out_of_prefix_link_excluded(self) -> None:
        links = fl._extract_links(_LISTING_HTML.decode(), "/research/", "https://example.com/research/")
        urls = [u for u, _ in links]
        self.assertFalse(any("external.com" in u for u in urls))
        self.assertFalse(any("/about" in u for u in urls))

    def test_duplicate_url_deduped_first_occurrence_wins(self) -> None:
        links = fl._extract_links(_LISTING_HTML.decode(), "/research/", "https://example.com/research/")
        urls = [u for u, _ in links]
        self.assertEqual(urls.count("https://example.com/research/paper-one"), 1)
        by_url = dict(links)
        self.assertEqual(by_url["https://example.com/research/paper-one"], "Paper One")  # first, not "(duplicate link)"

    def test_no_matching_links_returns_empty(self) -> None:
        self.assertEqual(fl._extract_links("<html><body>no links here</body></html>", "/research/", "https://example.com/"), [])

    def test_malformed_html_never_raises(self) -> None:
        result = fl._extract_links("<html><body><a href='/research/x'>unterminated", "/research/", "https://example.com/")
        self.assertIsInstance(result, list)


class ExtractTitleTests(unittest.TestCase):
    def test_title_tag_preferred(self) -> None:
        html_text = "<html><head><title>The Title</title></head><body><h1>The Heading</h1></body></html>"
        self.assertEqual(fl._extract_title(html_text), "The Title")

    def test_falls_back_to_h1_when_no_title(self) -> None:
        html_text = "<html><body><h1>The Heading</h1></body></html>"
        self.assertEqual(fl._extract_title(html_text), "The Heading")

    def test_no_title_or_h1_returns_empty(self) -> None:
        self.assertEqual(fl._extract_title("<html><body>just text</body></html>"), "")


class FetchListingCandidatesTests(unittest.TestCase):
    """default_fetcher's listing-page path, with urlopen mocked (no real
    network) -- each linked item gets its own individually-fetched
    candidate rather than the whole listing page collapsing to one."""

    def _mock_response(self, body: bytes, status: int = 200):
        resp = mock.MagicMock()
        resp.status = status
        resp.read.return_value = body
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        return resp

    def test_listing_source_yields_one_candidate_per_link(self) -> None:
        source = fl.Source(
            slug="listing-src", kind="idea", type="web", url="https://example.com/research/",
            trusted=True, link_prefix="/research/",
        )
        item_page = b"<html><head><title>Paper One</title></head><body><main>Abstract text here.</main></body></html>"

        def fake_urlopen(req, timeout=None):
            return self._mock_response(_LISTING_HTML if req.full_url == source.url else item_page)

        with mock.patch.object(fl, "urlopen", side_effect=fake_urlopen):
            candidates = fl.default_fetcher(source)

        self.assertGreaterEqual(len(candidates), 2)
        titles = {c.title for c in candidates}
        self.assertIn("Paper One", titles)

    def test_listing_page_unreachable_returns_empty(self) -> None:
        source = fl.Source(
            slug="listing-src", kind="idea", type="web", url="https://example.com/research/",
            trusted=True, link_prefix="/research/",
        )
        with mock.patch.object(fl, "urlopen", side_effect=fl.URLError("nope")):
            self.assertEqual(fl.default_fetcher(source), [])


class DefaultFetcherFeedAndRenderIntegrationTests(unittest.TestCase):
    """default_fetcher's response-shape auto-detection, with urlopen mocked
    (no real network) -- proves the RSS/JS-shell paths wire together
    correctly, distinct from the pure-function unit tests above."""

    def _mock_response(self, body: bytes, status: int = 200):
        resp = mock.MagicMock()
        resp.status = status
        resp.read.return_value = body
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        return resp

    def test_feed_response_yields_multiple_candidates(self) -> None:
        source = fl.Source(slug="feed-src", kind="idea", type="feed", url="https://example.com/feed", trusted=True)
        with mock.patch.object(fl, "urlopen", return_value=self._mock_response(_RSS_SAMPLE)):
            candidates = fl.default_fetcher(source)
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].title, "First post & more")

    def test_js_shell_html_falls_back_to_playwright_when_available(self) -> None:
        source = fl.Source(slug="spa-src", kind="idea", type="web", url="https://example.com/spa", trusted=True)
        shell_html = ("<html><body><div id='root'></div></body></html>" + (" " * 3000)).encode("utf-8")

        with mock.patch.object(fl, "urlopen", return_value=self._mock_response(shell_html)), \
             mock.patch.object(fl, "_render_with_playwright", return_value="the real rendered article text"):
            candidates = fl.default_fetcher(source)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].body, "the real rendered article text")

    def test_js_shell_html_degrades_gracefully_when_playwright_unavailable(self) -> None:
        source = fl.Source(slug="spa-src", kind="idea", type="web", url="https://example.com/spa", trusted=True)
        shell_html = ("<html><body><div id='root'></div></body></html>" + (" " * 3000)).encode("utf-8")

        with mock.patch.object(fl, "urlopen", return_value=self._mock_response(shell_html)), \
             mock.patch.object(fl, "_render_with_playwright", return_value=None):
            candidates = fl.default_fetcher(source)
        # Degrades to the plain-fetch body (stripped) rather than raising or returning [].
        self.assertEqual(len(candidates), 1)

    def test_ordinary_html_page_never_calls_playwright(self) -> None:
        source = fl.Source(slug="normal-src", kind="idea", type="web", url="https://example.com/page", trusted=True)
        real_html = ("<html><body>" + ("<p>Real article content, quite a lot of it.</p>" * 20) + "</body></html>").encode()

        with mock.patch.object(fl, "urlopen", return_value=self._mock_response(real_html)), \
             mock.patch.object(fl, "_render_with_playwright") as render_mock:
            candidates = fl.default_fetcher(source)
        render_mock.assert_not_called()
        self.assertEqual(len(candidates), 1)
        self.assertNotIn("<p>", candidates[0].body)  # tags stripped from the final body


class CliTests(_ForwardLearningTestBase):
    def test_main_no_vault_path_errors(self) -> None:
        import os

        prev = os.environ.pop("MEMORY_VAULT_PATH", None)
        try:
            rc = fl.main([])
        finally:
            if prev is not None:
                os.environ["MEMORY_VAULT_PATH"] = prev
        self.assertEqual(rc, 1)

    def test_main_smoke_run_no_sources(self) -> None:
        rc = fl.main(["--vault-path", str(self.vault)])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
