#!/usr/bin/env python3
"""Unit tests for scripts/vault_orphan_census.py (task 193, step 1).

One fixture vault pins each counting rule the census shares with Obsidian's
graph: which frontmatter values draw an edge, that code spans don't, that a
dangling link is an edge and an attachment link isn't, that the ignore filters
drop their notes' edges, and which of several same-named notes a bare name
lands on. The CLI tests pin that the census never writes the vault.

Run directly:

    python3 scripts/test_vault_orphan_census.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import vault_orphan_census as voc  # noqa: E402

try:
    import yaml
except ImportError:  # pragma: no cover - pyyaml is a declared dependency
    yaml = None


# The live vault's two filters, one of each form: a '/regex/' and a path prefix.
_APP_JSON = {"userIgnoreFilters": ["/(^|\\/)resources(\\/|$)/", "agent/desk/scratch"]}

_NOTES = {
    # rule 4: a quoted wikilink in frontmatter draws an edge; an unquoted one is a
    # nested list to YAML and draws nothing; a wikilink under a nested key draws nothing
    "fm/quoted.md": '---\nrelated: "[[quoted-target]]"\n---\nbody\n',
    "fm/quoted-target.md": "plain\n",
    "fm/unquoted.md": "---\nrelated: [[unquoted-target]]\n---\nbody\n",
    "fm/unquoted-target.md": "plain\n",
    "fm/nested-key.md": '---\nmeta:\n  link: "[[nested-target]]"\n---\nbody\n',
    "fm/nested-target.md": "plain\n",
    # code spans, fences and %%comments%% hide their links
    "code/spans.md": ("inline `[[code-target]]` here\n\n```\n[[code-target]]\n```\n\n"
                      "%%[[code-target]]%%\n"),
    "code/code-target.md": "plain\n",
    # a link to a note that doesn't exist is a ghost node, so an edge
    "ghost/dangling.md": "see [[no-such-note]]\n",
    # a link to an attachment resolves but is not an edge
    "attach/embeds.md": "![[pic.png]] and [the pdf](paper.pdf)\n",
    "attach/pic.png": "",
    "attach/paper.pdf": "",
    # the ignore filters drop a note and every edge to or from it
    "ign/to-resources.md": "see [[resources/ref]]\n",
    "resources/ref.md": "plain\n",
    "ign/to-scratch.md": "see [[agent/desk/scratch/pad]]\n",
    "agent/desk/scratch/pad.md": "plain\n",
    # a bare name shared by three notes lands on the one in the linking note's folder,
    # even though z/dup.md has the shorter path
    "amb/x/linker.md": "see [[dup]]\n",
    "amb/x/dup.md": "plain\n",
    "amb/y/dup.md": "plain\n",
    "z/dup.md": "plain\n",
    # a bare name that is also a vault-root path is that path, before any name match
    "top.md": "plain\n",
    "deep/top.md": "plain\n",
    # an edge both ways keeps both ends out of the orphans
    "pair/one.md": "see [[pair/two|the other]]\n",
    "pair/two.md": "plain\n",
    # dot-folders are outside the universe
    ".trash/gone.md": "see [[pair/one]]\n",
}


def _build_vault(root: Path, notes=_NOTES, app_json=_APP_JSON) -> Path:
    for rel, text in notes.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    if app_json is not None:
        (root / ".obsidian").mkdir(exist_ok=True)
        (root / ".obsidian" / "app.json").write_text(json.dumps(app_json), encoding="utf-8")
    return root


def _listing(root: Path):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


@unittest.skipIf(yaml is None, "PyYAML not installed")
class TestCountingRules(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = _build_vault(Path(cls._tmp.name))
        cls.ix = voc.VaultIndex(cls.root)
        cls.recs = voc.census(cls.ix, yaml, git_dates={})

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def rec(self, path):
        return self.recs[path]

    def test_quoted_frontmatter_wikilink_is_an_edge(self) -> None:
        self.assertEqual(self.rec("fm/quoted.md")["out_md"], ["fm/quoted-target.md"])
        self.assertFalse(self.rec("fm/quoted.md")["graph_orphan"])
        self.assertFalse(self.rec("fm/quoted-target.md")["graph_orphan"])
        self.assertEqual(self.rec("fm/quoted.md")["related_shape"], "single-string")

    def test_unquoted_frontmatter_wikilink_is_not_an_edge(self) -> None:
        r = self.rec("fm/unquoted.md")
        self.assertEqual(r["out_md"], [])
        self.assertTrue(r["graph_orphan"])
        self.assertTrue(self.rec("fm/unquoted-target.md")["graph_orphan"])
        self.assertEqual(r["related_shape"], "nested-list")
        # reported as invisible, pointing at the note it would have linked
        self.assertEqual([(e["why"], e["target"]) for e in r["invisible_fm"]],
                         [("nested-list", "fm/unquoted-target.md")])

    def test_wikilink_under_a_nested_key_is_not_an_edge(self) -> None:
        self.assertEqual(self.rec("fm/nested-key.md")["out_md"], [])
        self.assertTrue(self.rec("fm/nested-target.md")["graph_orphan"])

    def test_code_spans_fences_and_comments_hide_links(self) -> None:
        r = self.rec("code/spans.md")
        self.assertEqual((r["out_md"], r["out_unresolved"]), ([], 0))
        self.assertTrue(r["graph_orphan"])
        self.assertTrue(self.rec("code/code-target.md")["graph_orphan"])

    def test_dangling_link_is_a_graph_edge(self) -> None:
        r = self.rec("ghost/dangling.md")
        self.assertEqual(r["out_unresolved"], 1)
        self.assertEqual(r["out_unresolved_examples"], ["no-such-note"])
        self.assertFalse(r["graph_orphan"])
        self.assertTrue(r["ghost_only"])

    def test_attachment_link_is_not_an_edge(self) -> None:
        r = self.rec("attach/embeds.md")
        self.assertEqual(r["out_attach"], 2)
        self.assertEqual((r["out_md"], r["out_unresolved"]), ([], 0))
        self.assertTrue(r["graph_orphan"])
        self.assertNotIn("attach/pic.png", self.recs)

    def test_regex_filter_drops_its_target(self) -> None:
        r = self.rec("ign/to-resources.md")
        self.assertEqual(r["out_md"], ["resources/ref.md"])
        self.assertEqual(r["out_md_ignored"], ["resources/ref.md"])
        self.assertTrue(r["graph_orphan"])
        self.assertFalse(r["full_orphan"])
        self.assertTrue(r["hidden_by_ignore"])
        ref = self.rec("resources/ref.md")
        self.assertTrue(ref["is_ignored"])
        self.assertFalse(ref["graph_orphan"])        # an ignored note isn't drawn at all

    def test_prefix_filter_drops_its_target(self) -> None:
        self.assertTrue(self.rec("agent/desk/scratch/pad.md")["is_ignored"])
        self.assertTrue(self.rec("ign/to-scratch.md")["graph_orphan"])
        self.assertTrue(self.rec("ign/to-scratch.md")["hidden_by_ignore"])

    def test_ambiguous_stem_resolves_to_the_same_folder_note(self) -> None:
        r = self.rec("amb/x/linker.md")
        self.assertEqual(r["out_md"], ["amb/x/dup.md"])
        self.assertTrue(r["ambiguous"])
        self.assertFalse(self.rec("amb/x/dup.md")["graph_orphan"])
        self.assertTrue(self.rec("amb/y/dup.md")["graph_orphan"])
        self.assertTrue(self.rec("z/dup.md")["graph_orphan"])

    def test_without_a_same_folder_note_the_shortest_path_wins(self) -> None:
        self.assertEqual(voc.resolve(self.ix, "dup", "pair/one.md"), ("z/dup.md", True))

    def test_a_vault_root_path_wins_over_a_same_folder_name(self) -> None:
        self.assertEqual(voc.resolve(self.ix, "top", "deep/other.md"), ("top.md", False))
        self.assertEqual(voc.resolve(self.ix, "Deep/Top.md", "pair/one.md"), ("deep/top.md", False))

    def test_path_link_with_label_is_an_edge_both_ways(self) -> None:
        self.assertEqual(self.rec("pair/one.md")["out_md"], ["pair/two.md"])
        self.assertEqual(self.rec("pair/two.md")["in_md"], ["pair/one.md"])
        self.assertFalse(self.rec("pair/two.md")["graph_orphan"])

    def test_dot_folders_are_outside_the_universe(self) -> None:
        self.assertNotIn(".trash/gone.md", self.recs)
        self.assertEqual(self.rec("pair/one.md")["in_md"], [])

    def test_totals_count_the_fixture(self) -> None:
        t = dict(voc.totals(self.recs))
        self.assertEqual(t["notes"], 22)
        self.assertEqual(t["ignored notes (match a filter)"], 2)
        orphans = sorted(p for p, r in self.recs.items() if r["graph_orphan"])
        self.assertEqual(orphans, [
            "amb/y/dup.md", "attach/embeds.md", "code/code-target.md", "code/spans.md", "deep/top.md",
            "fm/nested-key.md", "fm/nested-target.md", "fm/unquoted-target.md", "fm/unquoted.md",
            "ign/to-resources.md", "ign/to-scratch.md", "top.md", "z/dup.md",
        ])
        self.assertEqual(t["graph_orphan (Obsidian graph: ghosts = edges, filters ON)"], 13)
        self.assertEqual(t["hidden_by_ignore (graph_orphan and not full_orphan)"], 2)
        self.assertEqual(t["notes whose only graph edges are dangling ghost links"], 1)

    def test_no_app_json_means_no_filters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _build_vault(Path(tmp), app_json=None)
            recs = voc.census(voc.VaultIndex(root), yaml, git_dates={})
        self.assertFalse(any(r["is_ignored"] for r in recs.values()))
        self.assertFalse(recs["ign/to-resources.md"]["graph_orphan"])


@unittest.skipIf(yaml is None, "PyYAML not installed")
class TestCli(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.vault = _build_vault(base / "vault")
        self.before = _listing(self.vault)

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = voc.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_out_inside_the_vault_is_refused(self) -> None:
        code, _out, err = self.run_main(["--vault", str(self.vault), "--out", str(self.vault / "agent" / "c")])
        self.assertEqual(code, 2)
        self.assertIn("inside the vault", err)
        self.assertEqual(_listing(self.vault), self.before)

    def test_out_writes_jsonl_and_summary_outside_the_vault(self) -> None:
        out_dir = Path(self._tmp.name) / "report"
        code, out, _err = self.run_main(["--vault", str(self.vault), "--out", str(out_dir)])
        self.assertEqual(code, 0)
        self.assertIn("22 notes, 13 graph orphans", out)
        rows = [json.loads(l) for l in (out_dir / "census.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 22)
        self.assertEqual([r["path"] for r in rows], sorted(r["path"] for r in rows))
        summary = (out_dir / "census-summary.md").read_text(encoding="utf-8")
        self.assertIn("| graph_orphan (Obsidian graph: ghosts = edges, filters ON) | 13 |", summary)
        self.assertEqual(_listing(self.vault), self.before)

    def test_without_out_prints_the_summary_and_writes_nothing(self) -> None:
        code, out, _err = self.run_main(["--vault", str(self.vault)])
        self.assertEqual(code, 0)
        self.assertIn("## T1 totals", out)
        self.assertIn("## T5 graph_orphan by writer", out)
        self.assertEqual(_listing(self.vault), self.before)

    def test_default_vault_is_the_resolved_one(self) -> None:
        import harness_memory
        with mock.patch.object(harness_memory, "vault_path", return_value=self.vault) as vp:
            code, out, _err = self.run_main([])
        vp.assert_called_once_with()
        self.assertEqual(code, 0)
        self.assertIn("Vault `%s`; " % self.vault, out)

    def test_no_resolvable_vault_exits_2(self) -> None:
        import harness_memory
        with mock.patch.object(harness_memory, "vault_path", return_value=None):
            code, _out, err = self.run_main([])
        self.assertEqual(code, 2)
        self.assertIn("no vault found", err)


if __name__ == "__main__":
    unittest.main()
