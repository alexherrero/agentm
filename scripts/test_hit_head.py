#!/usr/bin/env python3
"""A hit is the card's readable head, and the three surfaces print the same one.

Before this a hit was a path, a score and a 24-token snippet. A session asking
what it had found got an address and a fragment and had to open five files to
learn what any of them were. The design's answer is the card's own readable
fields, in the card's own order — and the claim that makes it worth doing is
that `memory_search` over MCP, `agentmd search` at a terminal and
`/memory search` in the shell all show the same thing.

That claim is what this file tests, and it is tested by *comparing the three*
rather than by asserting each one separately against a list written here. Three
assertions against one expectation is three chances to drift together; one
comparison is what actually holds them in agreement.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "harness" / "skills" / "memory" / "scripts"))

# The eleven the design names: the head, then the address and the evidence.
HEAD = ("title", "type", "kind", "summary", "importance", "status", "lifecycle",
        "project")

CARD = """---
title: The zorbulax subsystem
type: reference
summary: What zorbulax is and why it keeps coming up.
why: the room's reasoning, which a hit must never carry
importance: 7
status: active
lifecycle: dormant
project: agentm
created: 2026-01-02
---

Zorbulax is the subsystem that does the thing.
"""


def agentmd() -> "str | None":
    b = os.environ.get("AGENTMD", "").strip()
    return b if b and Path(b).exists() else None


@unittest.skipIf(agentmd() is None, "needs a built agentmd ($AGENTMD)")
class TheHeadOnEverySurface(unittest.TestCase):
    """One vault, one card, one query, three surfaces."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.vault = root / "agent"
        (cls.vault / "memory" / "semantic").mkdir(parents=True)
        (cls.vault / "memory" / "semantic" / "zorbulax.md").write_text(
            CARD, encoding="utf-8")
        cls.index = str(root / "index.db")
        subprocess.run([agentmd(), "reindex", "--vault", str(cls.vault),
                        "--index", cls.index],
                       capture_output=True, text=True, timeout=120)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _search_json(self):
        proc = subprocess.run(
            [agentmd(), "search", "-json", "-k", "3", "-surface", "measure",
             "-vault", str(self.vault), "-index", self.index, "zorbulax"],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rows = json.loads(proc.stdout).get("results") or []
        self.assertTrue(rows, f"the fixture never indexed: {proc.stdout[:200]}")
        return rows[0]

    def test_the_eleven_fields_are_there(self):
        hit = self._search_json()
        for field in HEAD:
            if field == "kind":
                continue  # this card is a memory, so it carries `type`
            self.assertIn(field, hit, f"the head is missing `{field}`")
        self.assertIn("path", hit)
        self.assertIn("score", hit)
        self.assertIn("snippet", hit)

    def test_a_hit_never_carries_the_body_or_why_as_a_field(self):
        """The two exclusions the design is explicit about. `why` is the room's
        reasoning written for the operator; the body is what the surface opens
        the file for, and five bodies is the whole budget the prompt hook has.

        As *fields*. See the next test for the part of this that is not clean.
        """
        hit = self._search_json()
        self.assertNotIn("why", hit)
        self.assertNotIn("body", hit)
        self.assertNotIn("Zorbulax is the subsystem that does the thing",
                         json.dumps({k: v for k, v in hit.items() if k != "snippet"}))

    def test_the_snippet_can_quote_frontmatter_including_why(self):
        """**A gap between the constraint and the design, recorded rather than
        decided.**

        The constraint says a hit never returns the body or `why`. The design's
        own hit shape ends "…then the address and the evidence", and the
        evidence is the snippet — FTS5's excerpt around the matched region. The
        frontmatter is deliberately indexed ("real query surface: *what's my
        convention for X* hits `kind: convention`"), so a snippet can quote it,
        `why:` included. On this fixture it does.

        Both readings cannot hold. Under the strict one the snippet would have
        to go, which contradicts the design listing it. Under the field reading
        — the one implemented — a hit carries no `why` and no `body`, and the
        snippet is evidence that may incidentally quote either.

        Pinned as the second, with the tension named, because a silent choice
        between two readings of a non-relaxable constraint is the kind of thing
        that should be visible to whoever reads this next.
        """
        hit = self._search_json()
        self.assertIn("snippet", hit)
        self.assertIn("the room's reasoning", hit["snippet"],
                      "the fixture no longer exercises the case this documents")

    def test_the_head_is_the_cards_order(self):
        """`title` first, `path` after the head. The order is the order the
        operator reads a note in, which is what makes a hit list read like a
        list of notes rather than like a query result."""
        proc = subprocess.run(
            [agentmd(), "search", "-json", "-k", "1", "-surface", "measure",
             "-vault", str(self.vault), "-index", self.index, "zorbulax"],
            capture_output=True, text=True, timeout=120)
        row = json.loads(proc.stdout)["results"][0]
        keys = list(row)
        self.assertEqual(keys[0], "title")
        self.assertLess(keys.index("summary"), keys.index("path"))
        self.assertLess(keys.index("path"), keys.index("score"))

    def test_the_cli_and_the_json_agree(self):
        """The comparison, not two assertions against one expectation."""
        hit = self._search_json()
        proc = subprocess.run(
            [agentmd(), "search", "-k", "1", "-surface", "measure",
             "-vault", str(self.vault), "-index", self.index, "zorbulax"],
            capture_output=True, text=True, timeout=120)
        text = proc.stdout
        self.assertIn(hit["title"], text)
        self.assertIn(hit["summary"], text)
        self.assertIn(hit["type"], text)
        self.assertIn(f"importance {hit['importance']}", text)
        self.assertIn(hit["lifecycle"], text)
        self.assertIn(hit["path"], text)

    def test_the_shell_surface_renders_the_same_head(self):
        """The third surface: `/memory search --heads`, which is Python's
        `format_head` over a daemon row.

        This feeds it *the daemon's actual row* — the one the JSON surface just
        returned — rather than spawning the CLI against this scratch vault. Not
        a convenience: `--heads` asks the daemon, and the daemon answers for the
        vault its own kernel config names, so a subprocess pointed at a
        temporary vault searches the wrong corpus and comes back empty. The test
        that did that skipped, and a skip here is a measurement of nothing
        dressed as a pass.

        What can actually drift is two renderers in two languages over one row,
        and that is what this compares.
        """
        hit = self._search_json()
        proc = subprocess.run(
            [agentmd(), "search", "-k", "1", "-surface", "measure",
             "-vault", str(self.vault), "-index", self.index, "zorbulax"],
            capture_output=True, text=True, timeout=120)
        go_text = proc.stdout

        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_recall3", _REPO / "harness" / "skills" / "memory" / "scripts" / "recall.py")
        recall = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(recall)
        py_text = recall.format_head(hit)

        for field in ("title", "summary", "type", "lifecycle"):
            self.assertIn(hit[field], py_text, f"the Python head dropped `{field}`")
            self.assertIn(hit[field], go_text, f"the Go head dropped `{field}`")
        self.assertIn(f"importance {hit['importance']}", py_text)
        self.assertIn(f"importance {hit['importance']}", go_text)
        self.assertIn(hit["path"], py_text)
        self.assertIn(hit["path"], go_text)

    def test_heads_never_exits_quiet(self):
        """`--heads` printed nothing and exited 0 when the daemon answered `[]`
        while the in-process engine had a row: the loop took the daemon's empty
        list, and the `(no results)` guard was satisfied by the *other* arm's
        results. Silence that reads as success is the shape of bug task zero
        spent its whole length removing from the retrieval gate.

        Runs against a vault the daemon is not configured for, which is exactly
        the condition that produced the silence.
        """
        env = dict(os.environ, MEMORY_ROOT=str(self.vault),
                   PATH=str(Path(agentmd()).parent) + os.pathsep + os.environ["PATH"])
        proc = subprocess.run(
            [sys.executable,
             str(_REPO / "harness" / "skills" / "memory" / "scripts" / "recall.py"),
             "query", "zorbulax", "-k", "1", "--heads"],
            capture_output=True, text=True, timeout=180, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        self.assertTrue(proc.stdout.strip(),
                        "`--heads` exited 0 having printed nothing at all")

    def test_lifecycle_active_is_not_printed_but_dormant_is(self):
        """`active` is what every filing stamps, so printing it on every hit
        hides the `dormant` a reader actually needs to see."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_recall", _REPO / "harness" / "skills" / "memory" / "scripts" / "recall.py")
        recall = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(recall)
        self.assertIn("dormant", recall.format_head(
            {"title": "T", "path": "p.md", "lifecycle": "dormant"}))
        self.assertNotIn("active", recall.format_head(
            {"title": "T", "path": "p.md", "lifecycle": "active"}))


class TheHeadRendererAlone(unittest.TestCase):
    """The formatter, without a daemon — so CI covers the shape."""

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_recall2", _REPO / "harness" / "skills" / "memory" / "scripts" / "recall.py")
        self.recall = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.recall)

    def test_a_note_with_no_title_falls_back_to_its_stem(self):
        """The first line of a hit has to say what the thing is."""
        out = self.recall.format_head({"path": "memory/semantic/a-fact.md"})
        self.assertTrue(out.startswith("a-fact"))

    def test_a_bare_note_renders_without_a_row_of_blanks(self):
        out = self.recall.format_head({"path": "p.md", "title": "T"})
        self.assertNotIn(" · ", out)

    def test_the_facets_read_in_the_cards_order(self):
        out = self.recall.format_head({
            "title": "T", "path": "p.md", "type": "reference", "importance": 7,
            "status": "unfiled", "lifecycle": "dormant", "project": "agentm"})
        row = [ln for ln in out.splitlines() if " · " in ln][0]
        self.assertLess(row.index("reference"), row.index("importance 7"))
        self.assertLess(row.index("importance 7"), row.index("unfiled"))
        self.assertLess(row.index("unfiled"), row.index("dormant"))
        self.assertLess(row.index("dormant"), row.index("project agentm"))

    def test_a_record_shows_its_kind_where_a_memory_shows_its_type(self):
        self.assertIn("session-trace", self.recall.format_head(
            {"title": "T", "path": "p.md", "kind": "session-trace"}))

    def test_the_head_field_list_matches_the_go_side(self):
        """Two languages, one vocabulary. The Go struct is what fills these and
        this list is what carries them through the hook, so they have to name
        the same things."""
        go = (_REPO / "daemon" / "internal" / "index" / "search.go").read_text(
            encoding="utf-8")
        for field in self.recall.HEAD_FIELDS:
            self.assertIn(f'json:"{field},omitempty"', go,
                          f"the Go hit has no `{field}`")


if __name__ == "__main__":
    unittest.main()
