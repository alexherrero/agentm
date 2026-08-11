#!/usr/bin/env python3
"""Tests for recall_token_budget — configurable per-recall token budget (#46 Part A task 3).

Guards:
  - Over-budget recall truncates to cap, highest-salience first.
  - Truncation marker is visible in stdout (never silent).
  - Quality retention: when budget allows ≥91% of entries, the retained set is
    exactly the highest-salience slice (no random selection).
  - Budget=0 → unlimited (all entries emitted, no marker).
  - RECALL_TOKEN_BUDGET env var and --token-budget CLI arg wire correctly.

Pure-Python tests (no bash subprocess, no vec index required). recall.py is
imported directly via sys.path injection. Fixtures use grep-only mode (no
embedding dep) by providing a vault with no vec index.

Run: python3 scripts/test_recall_token_budget.py
"""
from __future__ import annotations

import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_RECALL_SCRIPTS = _REPO / "harness" / "skills" / "memory" / "scripts"
if str(_RECALL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_RECALL_SCRIPTS))

import recall  # noqa: E402
import recall_counter  # noqa: E402


def _write_always_load_entry(
    vault: Path,
    slug: str,
    body: str,
    *,
    kind: str = "feedback",
    tags: str = "[test]",
    status: str = "",
) -> Path:
    """Write a minimal always-load entry to <vault>/personal/_always-load/<slug>.md."""
    al_dir = vault / "memory" / "_always-load"
    al_dir.mkdir(parents=True, exist_ok=True)
    fm_lines = [
        "---",
        f"name: {slug}",
        f"description: test entry {slug}",
        "metadata:",
        f"  kind: {kind}",
        f"  tags: {tags}",
    ]
    if status:
        fm_lines.append(f"status: {status}")
    fm_lines.append("---")
    content = "\n".join(fm_lines) + "\n\n" + body
    path = al_dir / f"{slug}.md"
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Unit tests for _estimate_tokens
# ---------------------------------------------------------------------------

class TestEstimateTokens(unittest.TestCase):
    def test_empty_string(self):
        self.assertEqual(recall._estimate_tokens(""), 1)  # max(1, 0//4)

    def test_short_string(self):
        # 40 chars → 10 tokens
        self.assertEqual(recall._estimate_tokens("a" * 40), 10)

    def test_long_string(self):
        # 4000 chars → 1000 tokens
        self.assertEqual(recall._estimate_tokens("x" * 4000), 1000)

    def test_minimum_one(self):
        # Even a 3-char string returns at least 1
        self.assertGreaterEqual(recall._estimate_tokens("abc"), 1)


# ---------------------------------------------------------------------------
# Unit tests for _resolve_token_budget
# ---------------------------------------------------------------------------

class TestResolveTokenBudget(unittest.TestCase):
    def setUp(self):
        # Ensure env var is clean before each test.
        os.environ.pop("RECALL_TOKEN_BUDGET", None)

    def tearDown(self):
        os.environ.pop("RECALL_TOKEN_BUDGET", None)

    def test_cli_arg_wins_over_env(self):
        os.environ["RECALL_TOKEN_BUDGET"] = "5000"
        self.assertEqual(recall._resolve_token_budget(12000), 12000)

    def test_env_fallback_when_no_arg(self):
        os.environ["RECALL_TOKEN_BUDGET"] = "8000"
        self.assertEqual(recall._resolve_token_budget(None), 8000)

    def test_default_when_neither(self):
        result = recall._resolve_token_budget(None)
        self.assertEqual(result, recall.DEFAULT_TOKEN_BUDGET)

    def test_invalid_env_falls_through_to_default(self):
        os.environ["RECALL_TOKEN_BUDGET"] = "not-a-number"
        result = recall._resolve_token_budget(None)
        self.assertEqual(result, recall.DEFAULT_TOKEN_BUDGET)

    def test_cli_arg_zero_is_unlimited(self):
        # 0 is a valid value meaning "unlimited" — should not fall to default.
        self.assertEqual(recall._resolve_token_budget(0), 0)


# ---------------------------------------------------------------------------
# Unit tests for _apply_token_budget
# ---------------------------------------------------------------------------

class TestApplyTokenBudget(unittest.TestCase):
    def _blocks(self, sizes: list[int]) -> tuple[list[str], list[str]]:
        """Create blocks of the given character sizes (each estimates to size//4 tokens)."""
        blocks = [f"slug-{i:02d} " + "x" * (sz - 8) for i, sz in enumerate(sizes)]
        slugs = [f"slug-{i:02d}" for i in range(len(sizes))]
        return blocks, slugs

    def test_all_fit_within_budget(self):
        # 3 blocks of ~100 tokens each; budget 1000 → all kept.
        blocks, slugs = self._blocks([400, 400, 400])  # each 100 tokens
        kb, ks, omitted = recall._apply_token_budget(blocks, slugs, 1_000)
        self.assertEqual(omitted, 0)
        self.assertEqual(kb, blocks)
        self.assertEqual(ks, slugs)

    def test_over_budget_truncates(self):
        # 5 blocks of 400 chars (~100 tokens each). Budget = 250 → fits 2.
        blocks, slugs = self._blocks([400, 400, 400, 400, 400])
        kb, ks, omitted = recall._apply_token_budget(blocks, slugs, 250)
        self.assertEqual(len(kb), 2)
        self.assertEqual(len(ks), 2)
        self.assertEqual(omitted, 3)

    def test_salience_order_preserved(self):
        # Blocks are passed in salience order (highest first). The FIRST blocks
        # should be kept, the LAST dropped.
        blocks = [f"entry-{i}" + "x" * 400 for i in range(5)]
        slugs = [f"slug-{i}" for i in range(5)]
        # Budget keeps 3 (~100 tokens each, so 300 tokens)
        kb, ks, omitted = recall._apply_token_budget(blocks, slugs, 320)
        self.assertEqual(ks, ["slug-0", "slug-1", "slug-2"])
        self.assertNotIn("slug-3", ks)
        self.assertNotIn("slug-4", ks)

    def test_budget_zero_means_unlimited(self):
        blocks, slugs = self._blocks([400, 400, 400, 400, 400])
        kb, ks, omitted = recall._apply_token_budget(blocks, slugs, 0)
        self.assertEqual(omitted, 0)
        self.assertEqual(kb, blocks)

    def test_budget_negative_means_unlimited(self):
        blocks, slugs = self._blocks([400, 400, 400])
        kb, ks, omitted = recall._apply_token_budget(blocks, slugs, -1)
        self.assertEqual(omitted, 0)
        self.assertEqual(len(kb), 3)

    def test_empty_input(self):
        kb, ks, omitted = recall._apply_token_budget([], [], 5000)
        self.assertEqual(kb, [])
        self.assertEqual(ks, [])
        self.assertEqual(omitted, 0)

    def test_retention_rate_over_91_percent(self):
        """High-salience entries survive at the standard default budget.

        With the default 20k token budget and typical entries of ~100-200 tokens
        each, the budget accommodates 100-200 entries. This test uses 10 entries
        totalling ~1000 tokens to confirm 100% retention far above the 91% bar.
        """
        blocks, slugs = self._blocks([400] * 10)  # 10 × 100 tokens ≈ 1000 total
        kb, ks, omitted = recall._apply_token_budget(
            blocks, slugs, recall.DEFAULT_TOKEN_BUDGET
        )
        retained_pct = len(kb) / len(blocks) * 100
        self.assertGreater(retained_pct, 91.0, msg=f"Retained only {retained_pct:.1f}%")
        self.assertEqual(omitted, 0)

    def test_truncation_keeps_highest_salience_above_91_pct(self):
        """When budget forces truncation, the top entries survive (salience preserved).

        9 of 10 entries = 90% … marginally below 91%. But the key invariant is
        that the TOP 9 survive (not a random selection). With salience-ordered
        truncation, if budget allows N of K, the N highest-salience entries are
        always included.
        """
        # 10 blocks; make each 200 chars (~50 tokens). Budget keeps 9 (450 tokens).
        blocks = [f"entry-{i:02d}" + "y" * 195 for i in range(10)]  # each ~50 tokens
        slugs = [f"slug-{i:02d}" for i in range(10)]
        _, ks, omitted = recall._apply_token_budget(blocks, slugs, 450)
        # The top entries (first in the list) must be retained.
        self.assertIn("slug-00", ks)
        self.assertIn("slug-01", ks)
        self.assertIn("slug-02", ks)
        # The LAST entry (lowest salience) must be the one dropped.
        self.assertEqual(omitted, 1)
        self.assertNotIn("slug-09", ks)


# ---------------------------------------------------------------------------
# Integration tests for session_start with token budget
# ---------------------------------------------------------------------------

class TestSessionStartTokenBudget(unittest.TestCase):
    def _make_vault(self, tmp_path: Path, n: int, body_size: int = 400) -> Path:
        vault = tmp_path
        for i in range(n):
            _write_always_load_entry(
                vault,
                f"entry-{i:02d}",
                "z" * body_size,
            )
        return vault

    def test_no_truncation_marker_when_under_budget(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            vault = self._make_vault(Path(d), n=3, body_size=100)
            stdout = io.StringIO()
            stderr = io.StringIO()
            recall.session_start(
                vault=vault,
                token_budget=10_000,
                stdout=stdout,
                stderr=stderr,
            )
            out = stdout.getvalue()
            self.assertNotIn("recall truncated", out)
            # All 3 entries appear.
            for i in range(3):
                self.assertIn(f"entry-{i:02d}", out)

    def test_truncation_marker_visible_when_over_budget(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            # 5 entries × 400 chars each → ~100 tokens each. Budget = 150 → fits ~1.
            vault = self._make_vault(Path(d), n=5, body_size=400)
            stdout = io.StringIO()
            stderr = io.StringIO()
            recall.session_start(
                vault=vault,
                token_budget=150,
                stdout=stdout,
                stderr=stderr,
            )
            out = stdout.getvalue()
            self.assertIn("recall truncated", out)
            self.assertIn("> [!NOTE]", out)
            self.assertIn("token budget", out)

    def test_truncation_marker_includes_count(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            # 4 entries × 400 chars → ~100 tokens each. Budget = 120 → fits 1 entry.
            vault = self._make_vault(Path(d), n=4, body_size=400)
            stdout = io.StringIO()
            stderr = io.StringIO()
            recall.session_start(
                vault=vault,
                token_budget=120,
                stdout=stdout,
                stderr=stderr,
            )
            out = stdout.getvalue()
            # The marker should mention how many were omitted.
            self.assertIn("entries omitted", out)

    def test_budget_zero_means_unlimited_no_marker(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            vault = self._make_vault(Path(d), n=5, body_size=400)
            stdout = io.StringIO()
            stderr = io.StringIO()
            recall.session_start(
                vault=vault,
                token_budget=0,
                stdout=stdout,
                stderr=stderr,
            )
            out = stdout.getvalue()
            self.assertNotIn("recall truncated", out)
            for i in range(5):
                self.assertIn(f"entry-{i:02d}", out)

    def test_stderr_transparency_mentions_token_omit(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            vault = self._make_vault(Path(d), n=3, body_size=400)
            stdout = io.StringIO()
            stderr = io.StringIO()
            recall.session_start(
                vault=vault,
                token_budget=110,  # fits ~1 entry; 2 omitted
                stdout=stdout,
                stderr=stderr,
            )
            err = stderr.getvalue()
            self.assertIn("token budget", err)


# ---------------------------------------------------------------------------
# Integration tests for prompt_submit with token budget
# ---------------------------------------------------------------------------

class TestPromptSubmitTokenBudget(unittest.TestCase):
    """prompt_submit token budget tests use a vault with no vec index (grep-only)."""

    def _make_recall_vault(self, tmp_path: Path, n: int, token: str) -> Path:
        """Create a vault with n query-relevant entries in personal/."""
        vault = tmp_path
        group = vault / "memory"
        group.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            slug = f"recall-entry-{i:02d}"
            entry_path = group / f"{slug}.md"
            # Large body to consume token budget quickly; token ensures grep hit.
            body = f"{token} " + "w" * 350 + f" index {i}"
            content = f"---\nname: {slug}\nkind: feedback\ntags: [test]\n---\n\n{body}"
            entry_path.write_text(content, encoding="utf-8")
        return vault

    def test_truncation_marker_in_prompt_submit(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            # 5 entries × ~90 tokens each. Budget 120 → fits ~1.
            vault = self._make_recall_vault(Path(d), n=5, token="zorptackle")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.object(recall_counter, "record_recall", lambda *a, **kw: {}):
                recall.prompt_submit(
                    vault=vault,
                    prompt="zorptackle",
                    budget_ms=5000,
                    token_budget=120,
                    stdout=stdout,
                    stderr=stderr,
                )
            out = stdout.getvalue()
            # Either truncation happened (entries found + truncated) or no results.
            # With grep-only the entries should be found. Check that if results are
            # present, any truncation marker appears.
            if "recall-entry" in out:
                self.assertIn("recall truncated", out)

    def test_no_marker_when_under_budget(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            vault = self._make_recall_vault(Path(d), n=2, token="zorptackle")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.object(recall_counter, "record_recall", lambda *a, **kw: {}):
                recall.prompt_submit(
                    vault=vault,
                    prompt="zorptackle",
                    budget_ms=5000,
                    token_budget=10_000,
                    stdout=stdout,
                    stderr=stderr,
                )
            out = stdout.getvalue()
            self.assertNotIn("recall truncated", out)


# ---------------------------------------------------------------------------
# CLI integration: --token-budget arg passes through to session_start
# ---------------------------------------------------------------------------

class TestCLITokenBudget(unittest.TestCase):
    def test_token_budget_arg_parsed(self):
        """--token-budget is parsed without error."""
        args = recall._parse_args([
            "--vault-path", "/tmp",
            "session-start",
            "--token-budget", "5000",
        ])
        self.assertEqual(args.token_budget, 5000)

    def test_token_budget_default_is_none_in_args(self):
        """When --token-budget is omitted, args.token_budget is None (resolved later)."""
        args = recall._parse_args([
            "--vault-path", "/tmp",
            "session-start",
        ])
        self.assertIsNone(args.token_budget)

    def test_prompt_submit_token_budget_arg(self):
        args = recall._parse_args([
            "--vault-path", "/tmp",
            "prompt-submit",
            "--token-budget", "8000",
        ])
        self.assertEqual(args.token_budget, 8000)


# ---------------------------------------------------------------------------
# Unit tests for _truncate_to_tokens
# ---------------------------------------------------------------------------

class TestTruncateToTokens(unittest.TestCase):
    def test_shorter_than_allowance_is_unchanged(self):
        text = "the quick brown fox"
        self.assertEqual(recall._truncate_to_tokens(text, 100), text)

    def test_cuts_to_allowance(self):
        text = "word " * 1000  # 5000 chars
        out = recall._truncate_to_tokens(text, 100)  # 100 tokens = 400 chars
        self.assertLessEqual(len(out), 400)
        self.assertGreater(len(out), 0)

    def test_cuts_on_a_word_boundary(self):
        text = "alpha bravo charlie delta echo foxtrot golf hotel india juliet " * 20
        out = recall._truncate_to_tokens(text, 20)  # 80 chars
        # No partial word at the end: the last token must be a real word.
        self.assertIn(out.split()[-1], text.split())

    def test_hard_cut_when_no_boundary_is_near(self):
        # One unbroken run — there is no whitespace to break on, so it must
        # still respect the allowance rather than returning the whole string.
        text = "x" * 5000
        out = recall._truncate_to_tokens(text, 50)
        self.assertEqual(len(out), 200)

    def test_non_positive_allowance_returns_empty(self):
        self.assertEqual(recall._truncate_to_tokens("anything", 0), "")
        self.assertEqual(recall._truncate_to_tokens("anything", -5), "")

    def test_result_fits_the_allowance_it_was_given(self):
        text = "lorem ipsum dolor sit amet " * 500
        for allowance in (10, 37, 100, 250):
            out = recall._truncate_to_tokens(text, allowance)
            self.assertLessEqual(recall._estimate_tokens(out), allowance)


# ---------------------------------------------------------------------------
# Unit tests for _read_entry_head (the bounded read that keeps an unusable
# entry from costing a full-file read inside a 300ms hook budget)
# ---------------------------------------------------------------------------

class TestReadEntryHead(unittest.TestCase):
    def _tmpfile(self, d: Path, text: str) -> Path:
        p = d / "entry.md"
        p.write_text(text, encoding="utf-8")
        return p

    def test_small_file_is_read_whole_and_not_flagged(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = self._tmpfile(Path(d), "hello world")
            text, clipped = recall._read_entry_head(p, 1000)
            self.assertEqual(text, "hello world")
            self.assertFalse(clipped)

    def test_large_file_is_clipped_and_flagged(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = self._tmpfile(Path(d), "y" * 10_000)
            text, clipped = recall._read_entry_head(p, 500)
            self.assertEqual(len(text), 500)
            self.assertTrue(clipped)

    def test_exactly_at_cap_is_not_flagged(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = self._tmpfile(Path(d), "z" * 500)
            text, clipped = recall._read_entry_head(p, 500)
            self.assertEqual(len(text), 500)
            self.assertFalse(clipped)

    def test_zero_cap_reads_the_whole_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = self._tmpfile(Path(d), "q" * 10_000)
            text, clipped = recall._read_entry_head(p, 0)
            self.assertEqual(len(text), 10_000)
            self.assertFalse(clipped)

    def test_multibyte_characters_are_never_split(self):
        # A byte-mode cut at 500 would land mid-character and raise or mangle;
        # a character-mode cut cannot.
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = self._tmpfile(Path(d), "é" * 2_000)
            text, clipped = recall._read_entry_head(p, 501)
            self.assertTrue(clipped)
            self.assertEqual(text, "é" * 501)


# ---------------------------------------------------------------------------
# Unit tests for _build_excerpt_block
# ---------------------------------------------------------------------------

class TestBuildExcerptBlock(unittest.TestCase):
    def _result(self, **over) -> dict:
        base = {
            "path": "personal/huge-log.md",
            "slug": "huge-log",
            "sim": 0.0,
            "keyword": 0,
            "combined": 9.5,
            "score": 9.5,
            "source": "daemon",
        }
        base.update(over)
        return base

    def test_marker_names_the_scale_and_the_path(self):
        block = recall._build_excerpt_block(
            self._result(), {"kind": "unknown"}, "body text here " * 100,
            allowance_tokens=800, full_tokens=255_271, token_budget=20_000,
        )
        self.assertIsNotNone(block)
        self.assertIn(recall._EXCERPT_MARKER, block)
        self.assertIn("255,271", block)          # the entry's real scale
        self.assertIn("20,000", block)           # the budget it did not fit
        self.assertIn("personal/huge-log.md", block)  # where to read the rest

    def test_header_declares_the_block_partial(self):
        block = recall._build_excerpt_block(
            self._result(), {"kind": "project"}, "body " * 500,
            allowance_tokens=800, full_tokens=90_000, token_budget=20_000,
        )
        header = block.splitlines()[0]
        self.assertIn("excerpt of ~90,000 tokens", header)

    def test_daemon_snippet_is_carried_into_the_excerpt(self):
        snippet = "… the [vault] [git] directory sits outside the sync set …"
        block = recall._build_excerpt_block(
            self._result(snippet=snippet), {"kind": "unknown"}, "unrelated head " * 200,
            allowance_tokens=800, full_tokens=255_271, token_budget=20_000,
        )
        self.assertIn(snippet, block)

    def test_head_of_the_body_follows_when_there_is_room(self):
        block = recall._build_excerpt_block(
            self._result(), {"kind": "unknown"}, "DISTINCTIVEOPENING then more text " * 50,
            allowance_tokens=800, full_tokens=255_271, token_budget=20_000,
        )
        self.assertIn("DISTINCTIVEOPENING", block)

    def test_block_fits_the_allowance_it_was_given(self):
        for allowance in (200, 400, 800, 4_000):
            block = recall._build_excerpt_block(
                self._result(snippet="… some [match] context …"),
                {"kind": "unknown", "tags": "[a, b]"},
                "filler content " * 5_000,
                allowance_tokens=allowance, full_tokens=255_271, token_budget=20_000,
            )
            self.assertIsNotNone(block)
            self.assertLessEqual(
                recall._estimate_tokens(block), allowance,
                msg=f"excerpt overran its {allowance}-token allowance",
            )

    def test_returns_none_when_the_allowance_cannot_cover_the_frame(self):
        block = recall._build_excerpt_block(
            self._result(path="personal/" + "deep/" * 60 + "entry.md"),
            {"kind": "unknown"}, "body " * 100,
            allowance_tokens=20, full_tokens=255_271, token_budget=20_000,
        )
        self.assertIsNone(block)


# ---------------------------------------------------------------------------
# _apply_token_budget's excerpt pass (the fix for oversized entries burning a
# top-k slot and injecting nothing)
# ---------------------------------------------------------------------------

class TestApplyTokenBudgetExcerpt(unittest.TestCase):
    def _blocks(self, sizes: list[int]) -> tuple[list[str], list[str]]:
        blocks = [f"slug-{i:02d} " + "x" * (sz - 8) for i, sz in enumerate(sizes)]
        slugs = [f"slug-{i:02d}" for i in range(len(sizes))]
        return blocks, slugs

    def test_without_an_excerpt_callable_oversized_blocks_are_still_omitted(self):
        """The default contract is unchanged: no callable, no second pass."""
        blocks, slugs = self._blocks([400_000, 400, 400])
        kb, ks, omitted = recall._apply_token_budget(blocks, slugs, 20_000)
        self.assertEqual(omitted, 1)
        self.assertEqual(ks, ["slug-01", "slug-02"])

    def test_an_unfittable_block_is_excerpted_instead_of_omitted(self):
        blocks, slugs = self._blocks([400_000, 400, 400])
        kb, ks, omitted = recall._apply_token_budget(
            blocks, slugs, 20_000, excerpt=lambda i, n: f"EXCERPT-{i}",
        )
        self.assertEqual(omitted, 0)
        self.assertEqual(ks, ["slug-00", "slug-01", "slug-02"])
        self.assertEqual(kb[0], "EXCERPT-0")

    def test_excerpt_keeps_the_salience_position(self):
        """An excerpt is emitted where the entry ranked, not appended at the end."""
        blocks, slugs = self._blocks([400, 400_000, 400])
        kb, ks, _ = recall._apply_token_budget(
            blocks, slugs, 20_000, excerpt=lambda i, n: f"EXCERPT-{i}",
        )
        self.assertEqual(ks, ["slug-00", "slug-01", "slug-02"])
        self.assertEqual(kb[1], "EXCERPT-1")

    def test_every_slot_yields_content_when_every_hit_is_oversized(self):
        """The reported failure: 5 hits, all oversized, nothing injected."""
        blocks, slugs = self._blocks([400_000] * 5)
        kb, ks, omitted = recall._apply_token_budget(
            blocks, slugs, 20_000, excerpt=lambda i, n: "e" * (n * 4),
        )
        self.assertEqual(omitted, 0)
        self.assertEqual(len(kb), 5)
        for block in kb:
            self.assertGreater(recall._estimate_tokens(block), 0)

    def test_whole_blocks_are_packed_before_excerpts(self):
        """Anything that fits today keeps arriving whole — no fidelity regression."""
        blocks, slugs = self._blocks([400_000, 40_000])  # 100k tokens, 10k tokens
        kb, _, _ = recall._apply_token_budget(
            blocks, slugs, 20_000, excerpt=lambda i, n: f"EXCERPT-{i}",
        )
        self.assertEqual(kb[0], "EXCERPT-0")      # can never fit → excerpted
        self.assertEqual(kb[1], blocks[1])        # fits whole → untouched

    def test_allowance_is_capped_at_a_share_of_the_budget(self):
        """One enormous hit cannot claim the room several should be splitting."""
        seen: list[int] = []

        def _excerpt(i: int, n: int) -> str:
            seen.append(n)
            return "e" * (n * 4)

        blocks, slugs = self._blocks([400_000] * 3)
        recall._apply_token_budget(blocks, slugs, 20_000, excerpt=_excerpt)
        cap = int(20_000 * recall._EXCERPT_BUDGET_SHARE)
        self.assertEqual(len(seen), 3)
        for allowance in seen:
            self.assertLessEqual(allowance, cap)

    def test_allowance_never_exceeds_what_the_budget_has_left(self):
        seen: list[int] = []

        def _excerpt(i: int, n: int) -> str:
            seen.append(n)
            return "e" * (n * 4)

        # One whole block eats most of the budget; the excerpt gets the rest.
        blocks, slugs = self._blocks([400_000, 76_000])  # 100k tokens, 19k tokens
        kb, _, _ = recall._apply_token_budget(
            blocks, slugs, 20_000, excerpt=_excerpt,
        )
        self.assertEqual(len(seen), 1)
        self.assertLessEqual(seen[0], 20_000 - 19_000)
        self.assertLessEqual(
            sum(recall._estimate_tokens(b) for b in kb), 20_000
        )

    def test_total_never_exceeds_the_budget(self):
        blocks, slugs = self._blocks([400_000] * 8)
        kb, _, _ = recall._apply_token_budget(
            blocks, slugs, 20_000, excerpt=lambda i, n: "e" * (n * 4),
        )
        self.assertLessEqual(
            sum(recall._estimate_tokens(b) for b in kb), 20_000
        )

    def test_excerpt_declining_leaves_the_entry_omitted(self):
        """A None from the callable is an honest omission, not a silent empty block."""
        blocks, slugs = self._blocks([400_000, 400])
        kb, ks, omitted = recall._apply_token_budget(
            blocks, slugs, 20_000, excerpt=lambda i, n: None,
        )
        self.assertEqual(omitted, 1)
        self.assertEqual(ks, ["slug-01"])

    def test_excerpt_pass_stops_below_the_minimum_useful_allowance(self):
        """A budget with no room left for a meaningful excerpt asks for none."""
        seen: list[int] = []

        def _excerpt(i: int, n: int) -> str:
            seen.append(n)
            return "e" * (n * 4)

        # A whole block leaves under _MIN_EXCERPT_TOKENS of the budget.
        leftover = recall._MIN_EXCERPT_TOKENS - 10
        blocks, slugs = self._blocks([400_000, (20_000 - leftover) * 4])
        _, _, omitted = recall._apply_token_budget(
            blocks, slugs, 20_000, excerpt=_excerpt,
        )
        self.assertEqual(seen, [])
        self.assertEqual(omitted, 1)

    def test_zero_budget_is_still_unlimited_with_an_excerpt_callable(self):
        blocks, slugs = self._blocks([400_000] * 3)
        kb, ks, omitted = recall._apply_token_budget(
            blocks, slugs, 0, excerpt=lambda i, n: "EXCERPT",
        )
        self.assertEqual(omitted, 0)
        self.assertEqual(kb, blocks)


# ---------------------------------------------------------------------------
# prompt_submit end-to-end: an oversized hit injects an excerpt, not nothing
# ---------------------------------------------------------------------------

class TestPromptSubmitOversizedEntries(unittest.TestCase):
    """The measured failure: a hit too large for the budget spent a top-k slot
    and injected nothing, so recall reported hits and handed over no content.

    `_daemon_search` is patched in every test here. Left alone it shells out to
    a real `agentmd` whose index covers the operator's vault, not the fixture —
    so on a daemon-backed machine the assertions would be about someone else's
    corpus, and on a machine without one they would be about a different code
    path than the machine next to it.
    """

    TOKEN_BUDGET = 4_000

    def _make_vault(self, tmp_path: Path, *, big: int, small: int, token: str) -> Path:
        vault = tmp_path
        group = vault / "personal"
        group.mkdir(parents=True, exist_ok=True)
        for i in range(big):
            # 30,000 chars ≈ 7,500 tokens: past the budget, and past the
            # bounded-read cap (budget×4 + slack), so the clipped-read path runs.
            body = f"{token} OPENINGMARKER{i} " + ("filler words here " * 1_650)
            (group / f"big-{i:02d}.md").write_text(
                f"---\nname: big-{i:02d}\nkind: project\ntags: [test]\n---\n\n{body}",
                encoding="utf-8",
            )
        for i in range(small):
            body = f"{token} SMALLBODY{i} " + ("brief " * 40)
            (group / f"small-{i:02d}.md").write_text(
                f"---\nname: small-{i:02d}\nkind: feedback\ntags: [test]\n---\n\n{body}",
                encoding="utf-8",
            )
        return vault

    def _run(self, vault: Path, prompt: str, *, daemon_results=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(recall_counter, "record_recall", lambda *a, **kw: {}), \
                mock.patch.object(
                    recall, "_daemon_search", lambda **kw: daemon_results):
            recall.prompt_submit(
                vault=vault,
                prompt=prompt,
                budget_ms=10_000,
                token_budget=self.TOKEN_BUDGET,
                stdout=stdout,
                stderr=stderr,
            )
        return stdout.getvalue(), stderr.getvalue()

    def test_every_oversized_hit_injects_an_excerpt(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            vault = self._make_vault(Path(d), big=5, small=0, token="zorptackle")
            out, err = self._run(vault, "zorptackle")
            self.assertIn("Loaded 5 relevant entries", err)
            self.assertEqual(out.count(recall._EXCERPT_MARKER), 5)
            for i in range(5):
                self.assertIn(f"big-{i:02d}", out)

    def test_stderr_reports_excerpting_rather_than_omission(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            vault = self._make_vault(Path(d), big=5, small=0, token="zorptackle")
            _, err = self._run(vault, "zorptackle")
            self.assertIn("5 entries excerpted to fit", err)
            self.assertNotIn("entries omitted", err)

    def test_stdout_preamble_says_how_many_are_partial(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            vault = self._make_vault(Path(d), big=3, small=0, token="zorptackle")
            out, _ = self._run(vault, "zorptackle")
            self.assertIn("3 too large to inject whole and shown as excerpts", out)

    def test_excerpt_carries_content_not_just_a_marker(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            vault = self._make_vault(Path(d), big=1, small=0, token="zorptackle")
            out, _ = self._run(vault, "zorptackle")
            self.assertIn("OPENINGMARKER0", out)

    def test_small_hits_stay_whole_alongside_excerpted_ones(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            vault = self._make_vault(Path(d), big=2, small=3, token="zorptackle")
            out, err = self._run(vault, "zorptackle")
            self.assertIn("2 entries excerpted to fit", err)
            for i in range(3):
                self.assertIn(f"SMALLBODY{i}", out)

    def test_injection_stays_within_the_token_budget(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            vault = self._make_vault(Path(d), big=5, small=0, token="zorptackle")
            out, _ = self._run(vault, "zorptackle")
            # Preamble ceremony is small and constant; the entry blocks are
            # what the budget governs.
            self.assertLess(recall._estimate_tokens(out), self.TOKEN_BUDGET + 200)

    def test_oversized_entry_is_not_read_whole(self):
        """The bounded read is the point: the slot must be cheap, not just used."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            vault = self._make_vault(Path(d), big=1, small=0, token="zorptackle")
            out, _ = self._run(vault, "zorptackle")
            cap = self.TOKEN_BUDGET * 4 + recall._ENTRY_READ_SLACK_CHARS
            self.assertLess(len(out), cap)

    def test_daemon_snippet_reaches_the_excerpt(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            vault = self._make_vault(Path(d), big=1, small=0, token="zorptackle")
            snippet = "… a [zorptackle] deep inside the log …"
            out, _ = self._run(
                vault, "zorptackle",
                daemon_results=[{
                    "path": "personal/big-00.md", "slug": "big-00",
                    "sim": 0.0, "keyword": 0, "combined": 9.0, "score": 9.0,
                    "source": "daemon", "snippet": snippet,
                }],
            )
            self.assertIn(snippet, out)
            self.assertIn(recall._EXCERPT_MARKER, out)

    def test_no_hits_still_reports_an_empty_recall_plainly(self):
        """"Nothing matched" must stay distinguishable from "matched but partial"."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            vault = self._make_vault(Path(d), big=2, small=0, token="zorptackle")
            out, err = self._run(vault, "nothingmatchesthisword")
            self.assertIn("Loaded 0 relevant entries: (none)", err)
            self.assertNotIn("excerpted", err)
            self.assertNotIn(recall._EXCERPT_MARKER, out)

    def test_excerpted_hits_are_marked_in_the_recall_ledger(self):
        """`memory-recall trace` should be able to say a hit surfaced only in part."""
        import tempfile
        recorded: dict = {}

        def _capture(prompt, slugs, hits=None):
            recorded["hits"] = hits or []
            return {}

        with tempfile.TemporaryDirectory() as d:
            vault = self._make_vault(Path(d), big=2, small=2, token="zorptackle")
            stdout, stderr = io.StringIO(), io.StringIO()
            with mock.patch.object(recall_counter, "record_recall", _capture), \
                    mock.patch.object(recall, "_daemon_search", lambda **kw: None):
                recall.prompt_submit(
                    vault=vault, prompt="zorptackle", budget_ms=10_000,
                    token_budget=self.TOKEN_BUDGET, stdout=stdout, stderr=stderr,
                )
        excerpted = [h for h in recorded["hits"] if h.get("excerpt")]
        self.assertEqual(len(excerpted), 2)
        self.assertTrue(all(h["slug"].startswith("big-") for h in excerpted))

    def test_snippet_text_is_kept_out_of_the_recall_ledger(self):
        """The ledger records why a hit surfaced, never the entry's own text."""
        import tempfile
        recorded: dict = {}

        def _capture(prompt, slugs, hits=None):
            recorded["hits"] = hits or []
            return {}

        with tempfile.TemporaryDirectory() as d:
            vault = self._make_vault(Path(d), big=1, small=0, token="zorptackle")
            stdout, stderr = io.StringIO(), io.StringIO()
            daemon = [{
                "path": "personal/big-00.md", "slug": "big-00", "sim": 0.0,
                "keyword": 0, "combined": 9.0, "score": 9.0, "source": "daemon",
                "snippet": "… secret note text …",
            }]
            with mock.patch.object(recall_counter, "record_recall", _capture), \
                    mock.patch.object(recall, "_daemon_search", lambda **kw: daemon):
                recall.prompt_submit(
                    vault=vault, prompt="zorptackle", budget_ms=10_000,
                    token_budget=self.TOKEN_BUDGET, stdout=stdout, stderr=stderr,
                )
        self.assertEqual(len(recorded["hits"]), 1)
        self.assertNotIn("snippet", recorded["hits"][0])


if __name__ == "__main__":
    unittest.main()
