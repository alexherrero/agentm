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


class StripHtmlTagsTests(unittest.TestCase):
    def test_tags_stripped_and_entities_unescaped(self) -> None:
        self.assertEqual(fl._strip_html_tags("<p>Hello &amp; welcome</p>"), "Hello & welcome")

    def test_script_and_style_content_dropped_not_just_tags(self) -> None:
        html_text = "<html><head><style>.x{color:red}</style></head><body><script>var x=1;</script>Real text</body></html>"
        result = fl._strip_html_tags(html_text)
        self.assertEqual(result, "Real text")

    def test_whitespace_collapsed(self) -> None:
        self.assertEqual(fl._strip_html_tags("a   \n\n  b"), "a b")


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
        class _FakePage:
            def goto(self, url, timeout=None, wait_until=None):
                pass

            def inner_text(self, selector):
                return "rendered visible text"

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
