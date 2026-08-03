#!/usr/bin/env python3
"""Unit tests for harness/skills/memory/scripts/incubator_lint.py (agentm #278).

Lives in scripts/ so CI's `cd scripts && python3 -m unittest discover` runs it,
matching test_vault_lint.py's placement.

The fixtures are transcribed from the REAL vault's idea ledger as observed on
2026-08-02, not from the shipped docs — the two disagree, and the corpus is
what this lint has to be correct about. Concretely: `_summary.md` carries
exactly `kind`/`status`/`slug`/`created`/`updated` and no `tags`/`group`, and
its `slug` is the incubator slug rather than the filename stem. Both would be
errors under save.py's schema; both are correct here. Expected findings are
hand-written literals, never recomputed with the module's own tables.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import incubator_lint as il  # noqa: E402
import vault_lint as vl  # noqa: E402

# Transcribed verbatim from the live vault (2026-08-02).
_REAL_INDEX = """\
---
kind: idea-incubator
status: research-complete
slug: doom-llm-npcs
surfaced_at: 2026-06-07
created: 2026-06-07
updated: 2026-06-07
tags: [idea, fun-project, doom]
group: _idea-incubator/doom-llm-npcs
---

# Doom + local Gemma NPCs
"""

_REAL_SUMMARY = """\
---
kind: idea-incubator-summary
status: research-complete
slug: doom-llm-npcs
created: 2026-06-07
updated: 2026-06-07
---

# Doom + local Gemma NPCs — operator summary
"""

_REAL_RESEARCH = """\
---
kind: idea-incubator-research
status: research-complete
slug: research-feasibility
incubator: doom-llm-npcs
created: 2026-06-07
updated: 2026-06-07
tags: [doom, llm]
group: _idea-incubator/doom-llm-npcs
---

# Feasibility
"""


class _Vault:
    """A scratch vault with the ledger at the ROOT, matching the live layout.

    The vault is nested one level down (`<tmp>/Obsidian/Agent`) exactly as the
    real one is, so that `vault.parent` — where Ideas.md resolves to — stays
    inside the temp dir. A flat `<tmp>` vault would resolve Ideas.md to the
    shared system temp dir, leaking one test's Ideas.md into every other.

    $IDEAS_SURFACE_PATH is cleared for the same reason: an operator's real
    Ideas.md must never be picked up by the suite.
    """

    def __enter__(self) -> Path:
        self._td = tempfile.TemporaryDirectory()
        self._prev = os.environ.pop("IDEAS_SURFACE_PATH", None)
        self.root = Path(self._td.name) / "Obsidian" / "Agent"
        (self.root / "personal").mkdir(parents=True)
        (self.root / "projects").mkdir(parents=True)
        # The real layout: the memory vault sits inside the Obsidian vault, and
        # Ideas.md sits at that Obsidian root beside `.obsidian/`. The marker is
        # what makes the Ideas.md fallback trust this parent at all.
        (self.root.parent / ".obsidian").mkdir(exist_ok=True)
        return self.root

    def __exit__(self, *exc):
        if self._prev is not None:
            os.environ["IDEAS_SURFACE_PATH"] = self._prev
        self._td.cleanup()


def _write(vault: Path, rel: str, content: str) -> Path:
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _seed_clean(vault: Path) -> None:
    """A complete, conforming incubator directory."""
    _write(vault, "_idea-incubator/doom-llm-npcs/_index.md", _REAL_INDEX)
    _write(vault, "_idea-incubator/doom-llm-npcs/_summary.md", _REAL_SUMMARY)
    _write(vault, "_idea-incubator/doom-llm-npcs/research-feasibility.md", _REAL_RESEARCH)


def _lint(vault: Path, *, resolves=lambda t: True):
    return il.lint_incubator(vault, resolves)


def _ids(findings, severity=None):
    return sorted(f.check_id for f in findings
                  if severity is None or f.severity == severity)


class TestRealShapesAreClean(unittest.TestCase):
    """The observed corpus must lint clean. If these fail, the rules drifted
    away from the files they describe."""

    def test_real_incubator_dir_has_no_findings(self):
        with _Vault() as v:
            _seed_clean(v)
            count, findings = _lint(v)
            self.assertEqual(findings, [], _ids(findings))
            self.assertEqual(count, 3)

    def test_summary_without_tags_or_group_is_clean(self):
        """save.py requires tags + group; this shape deliberately omits both."""
        with _Vault() as v:
            _seed_clean(v)
            _, findings = _lint(v)
            self.assertNotIn("incubator-core-field", _ids(findings))

    def test_summary_slug_need_not_match_filename_stem(self):
        """`slug: doom-llm-npcs` in a file named `_summary.md` is correct —
        vault_lint's slug-filename check would wrongly flag it."""
        with _Vault() as v:
            _seed_clean(v)
            _, findings = _lint(v)
            self.assertEqual([f for f in findings if "slug" in f.check_id], [])


class TestCoreFields(unittest.TestCase):
    def test_missing_core_field_is_error(self):
        with _Vault() as v:
            _seed_clean(v)
            _write(v, "_idea-incubator/doom-llm-npcs/_summary.md",
                   _REAL_SUMMARY.replace("updated: 2026-06-07\n", ""))
            _, findings = _lint(v)
            hits = [f for f in findings if f.check_id == "incubator-core-field"]
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].severity, "error")
            self.assertIn("updated", hits[0].message)

    def test_no_frontmatter_is_error(self):
        with _Vault() as v:
            _seed_clean(v)
            _write(v, "_idea-incubator/doom-llm-npcs/research-bare.md", "# no fm\n")
            _, findings = _lint(v)
            self.assertIn("incubator-frontmatter", _ids(findings, "error"))


class TestKindRole(unittest.TestCase):
    def test_summary_carrying_index_kind_is_error(self):
        with _Vault() as v:
            _seed_clean(v)
            _write(v, "_idea-incubator/doom-llm-npcs/_summary.md",
                   _REAL_SUMMARY.replace("kind: idea-incubator-summary",
                                         "kind: idea-incubator"))
            _, findings = _lint(v)
            hits = [f for f in findings if f.check_id == "incubator-kind-role"]
            self.assertEqual(len(hits), 1)
            self.assertIn("idea-incubator-summary", hits[0].message)

    def test_research_prefix_expects_research_kind(self):
        with _Vault() as v:
            _seed_clean(v)
            _write(v, "_idea-incubator/doom-llm-npcs/research-feasibility.md",
                   _REAL_RESEARCH.replace("kind: idea-incubator-research",
                                          "kind: idea-incubator-runbook"))
            _, findings = _lint(v)
            self.assertIn("incubator-kind-role", _ids(findings, "error"))

    def test_runbook_prefix_is_a_known_role(self):
        """A real `runbook-plex-to-jellyfin.md` exists in the live vault."""
        with _Vault() as v:
            _seed_clean(v)
            _write(v, "_idea-incubator/doom-llm-npcs/runbook-migrate.md",
                   _REAL_RESEARCH.replace("kind: idea-incubator-research",
                                          "kind: idea-incubator-runbook")
                                 .replace("slug: research-feasibility",
                                          "slug: runbook-migrate"))
            _, findings = _lint(v)
            self.assertEqual(findings, [], _ids(findings))

    def test_unrecognized_filename_role_is_warn_not_error(self):
        with _Vault() as v:
            _seed_clean(v)
            _write(v, "_idea-incubator/doom-llm-npcs/related-obsidian.md",
                   _REAL_RESEARCH.replace("slug: research-feasibility",
                                          "slug: related-obsidian"))
            _, findings = _lint(v)
            self.assertIn("incubator-file-role", _ids(findings, "warn"))
            self.assertNotIn("incubator-file-role", _ids(findings, "error"))


class TestBackref(unittest.TestCase):
    def test_incubator_backref_must_match_dir(self):
        with _Vault() as v:
            _seed_clean(v)
            _write(v, "_idea-incubator/doom-llm-npcs/research-feasibility.md",
                   _REAL_RESEARCH.replace("incubator: doom-llm-npcs",
                                          "incubator: home-server-cluster"))
            _, findings = _lint(v)
            hits = [f for f in findings if f.check_id == "incubator-backref"]
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].severity, "error")

    def test_missing_backref_is_error(self):
        with _Vault() as v:
            _seed_clean(v)
            _write(v, "_idea-incubator/doom-llm-npcs/research-feasibility.md",
                   _REAL_RESEARCH.replace("incubator: doom-llm-npcs\n", ""))
            _, findings = _lint(v)
            self.assertIn("incubator-backref", _ids(findings, "error"))

    def test_anchor_files_need_no_backref(self):
        with _Vault() as v:
            _seed_clean(v)
            _, findings = _lint(v)
            self.assertNotIn("incubator-backref", _ids(findings))


class TestDirectoryLevel(unittest.TestCase):
    def test_missing_index_anchor_is_error(self):
        with _Vault() as v:
            _write(v, "_idea-incubator/orphan/_summary.md",
                   _REAL_SUMMARY.replace("doom-llm-npcs", "orphan"))
            _, findings = _lint(v)
            self.assertIn("incubator-anchor", _ids(findings, "error"))

    def test_missing_summary_is_warn(self):
        with _Vault() as v:
            _write(v, "_idea-incubator/blog-author/_index.md",
                   _REAL_INDEX.replace("doom-llm-npcs", "blog-author")
                              .replace("status: research-complete",
                                       "status: research-pending"))
            _, findings = _lint(v)
            hits = [f for f in findings if f.check_id == "incubator-summary-missing"]
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].severity, "warn")

    def test_promoted_idea_is_exempt_from_missing_summary(self):
        """`promoted-to-design` means the idea left the incubator — there is no
        landing spot left to signpost, so the convention doesn't apply."""
        with _Vault() as v:
            _write(v, "_idea-incubator/agentm-git-backed-vault/_index.md",
                   _REAL_INDEX.replace("doom-llm-npcs", "agentm-git-backed-vault")
                              .replace("status: research-complete",
                                       "status: promoted-to-design"))
            _, findings = _lint(v)
            self.assertNotIn("incubator-summary-missing", _ids(findings))

    def test_slug_disagreement_between_index_and_summary_is_error(self):
        with _Vault() as v:
            _seed_clean(v)
            _write(v, "_idea-incubator/doom-llm-npcs/_summary.md",
                   _REAL_SUMMARY.replace("slug: doom-llm-npcs", "slug: doom-npcs"))
            _, findings = _lint(v)
            self.assertIn("incubator-slug-agreement", _ids(findings, "error"))

    def test_status_disagreement_is_warn(self):
        """The live vault's home-server-cluster has exactly this drift:
        _index says research-pending, _summary says research-partial."""
        with _Vault() as v:
            _seed_clean(v)
            _write(v, "_idea-incubator/doom-llm-npcs/_index.md",
                   _REAL_INDEX.replace("status: research-complete",
                                       "status: research-pending"))
            _write(v, "_idea-incubator/doom-llm-npcs/_summary.md",
                   _REAL_SUMMARY.replace("status: research-complete",
                                         "status: research-partial"))
            _, findings = _lint(v)
            hits = [f for f in findings if f.check_id == "incubator-status-agreement"]
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].severity, "warn")


class TestStatusVocabulary(unittest.TestCase):
    def test_every_observed_status_is_accepted(self):
        """Hand-written from the live corpus — all six must lint clean."""
        for status in ("research-pending", "research-partial", "research-complete",
                       "promoted-to-design", "deprioritized", "spec-ready"):
            with self.subTest(status=status), _Vault() as v:
                _seed_clean(v)
                _write(v, "_idea-incubator/doom-llm-npcs/research-feasibility.md",
                       _REAL_RESEARCH.replace("status: research-complete",
                                              f"status: {status}"))
                _, findings = _lint(v)
                self.assertNotIn("incubator-status", _ids(findings))

    def test_unknown_status_is_warn_not_error(self):
        with _Vault() as v:
            _seed_clean(v)
            _write(v, "_idea-incubator/doom-llm-npcs/research-feasibility.md",
                   _REAL_RESEARCH.replace("status: research-complete",
                                          "status: totally-made-up"))
            _, findings = _lint(v)
            self.assertIn("incubator-status", _ids(findings, "warn"))
            self.assertNotIn("incubator-status", _ids(findings, "error"))


class TestDates(unittest.TestCase):
    def test_non_iso_date_is_error(self):
        with _Vault() as v:
            _seed_clean(v)
            _write(v, "_idea-incubator/doom-llm-npcs/_summary.md",
                   _REAL_SUMMARY.replace("created: 2026-06-07", "created: 06/07/2026"))
            _, findings = _lint(v)
            self.assertIn("incubator-date", _ids(findings, "error"))

    def test_updated_before_created_is_warn(self):
        with _Vault() as v:
            _seed_clean(v)
            _write(v, "_idea-incubator/doom-llm-npcs/_summary.md",
                   _REAL_SUMMARY.replace("updated: 2026-06-07", "updated: 2026-05-01"))
            _, findings = _lint(v)
            self.assertIn("incubator-date", _ids(findings, "warn"))


class TestBodyStructureIsNotChecked(unittest.TestCase):
    """The docs prescribe a five-section `_summary.md`; 4 of the 5 real
    summaries don't use it. Checking it would flag real, good files."""

    def test_free_form_summary_body_is_clean(self):
        with _Vault() as v:
            _seed_clean(v)
            _write(v, "_idea-incubator/doom-llm-npcs/_summary.md",
                   _REAL_SUMMARY + "\n**Can you do it?** Yes — as a fun demo.\n")
            _, findings = _lint(v)
            self.assertEqual(findings, [], _ids(findings))


class TestIdeasSurface(unittest.TestCase):
    def _ideas(self, vault: Path, text: str) -> Path:
        p = vault.parent / "Ideas.md"
        p.write_text(text, encoding="utf-8")
        return p

    def test_conforming_headings_are_clean(self):
        with _Vault() as v:
            _seed_clean(v)
            self._ideas(v, "# Ideas\n\n## 2026-05-20: Home cloud + self-hosted VPN\n\nBody.\n")
            _, findings = _lint(v)
            self.assertEqual(findings, [], _ids(findings))

    def test_dismissed_strikethrough_heading_is_accepted(self):
        """The live Ideas.md has exactly one of these; it is deliberate."""
        with _Vault() as v:
            _seed_clean(v)
            self._ideas(v, "# Ideas\n\n## ~~2026-05-20: Unraid GitHub Actions runner~~ "
                           "— Dismissed 2026-05-24\n\nBody.\n")
            _, findings = _lint(v)
            self.assertEqual(findings, [], _ids(findings))

    def test_malformed_heading_is_warn(self):
        with _Vault() as v:
            _seed_clean(v)
            self._ideas(v, "# Ideas\n\n## Some Untitled Idea\n\nBody.\n")
            _, findings = _lint(v)
            self.assertIn("ideas-heading", _ids(findings, "warn"))

    def test_no_frontmatter_is_not_flagged(self):
        """Ideas.md deliberately carries none — it lives outside the vault."""
        with _Vault() as v:
            _seed_clean(v)
            self._ideas(v, "# Ideas\n\n## 2026-05-20: A thing\n\nBody.\n")
            _, findings = _lint(v)
            self.assertEqual([f for f in findings if "frontmatter" in f.check_id], [])

    def test_broken_incubator_link_is_error(self):
        with _Vault() as v:
            _seed_clean(v)
            self._ideas(v, "# Ideas\n\n## 2026-05-20: A thing\n\n"
                           "**Deep research:** [[_idea-incubator/ghost/_summary]]\n")
            _, findings = _lint(v, resolves=lambda t: False)
            self.assertIn("ideas-incubator-link", _ids(findings, "error"))

    def test_absent_ideas_file_produces_no_findings(self):
        with _Vault() as v:
            _seed_clean(v)
            _, findings = _lint(v)
            self.assertEqual(findings, [], _ids(findings))

    def test_ideas_beside_a_non_obsidian_parent_is_ignored(self):
        """A scratch vault must not adopt an unrelated Ideas.md sitting next to
        it — that is how one fixture file leaked into every other suite's
        scratch vault and produced phantom findings."""
        with _Vault() as v:
            _seed_clean(v)
            self._ideas(v, "# Ideas\n\n## Not A Conforming Heading\n\nBody.\n")
            (v.parent / ".obsidian").rmdir()
            _, findings = _lint(v)
            self.assertEqual(findings, [], _ids(findings))

    def test_explicit_ideas_path_needs_no_obsidian_marker(self):
        """An operator naming the file outright is authoritative on its own."""
        with _Vault() as v:
            _seed_clean(v)
            ideas = self._ideas(v, "# Ideas\n\n## Not A Conforming Heading\n\nBody.\n")
            (v.parent / ".obsidian").rmdir()
            _, findings = il.lint_incubator(v, lambda t: True,
                                            ideas_path=str(ideas))
            self.assertIn("ideas-heading", _ids(findings, "warn"))


class TestWikilinks(unittest.TestCase):
    def test_unresolvable_body_link_is_error(self):
        with _Vault() as v:
            _seed_clean(v)
            _write(v, "_idea-incubator/doom-llm-npcs/_summary.md",
                   _REAL_SUMMARY + "\nSee [[nowhere-at-all]].\n")
            _, findings = _lint(v, resolves=lambda t: t != "nowhere-at-all")
            hits = [f for f in findings if f.check_id == "incubator-wikilink"]
            self.assertEqual(len(hits), 1)
            self.assertIn("nowhere-at-all", hits[0].message)

    def test_frontmatter_is_not_scanned_for_wikilinks(self):
        """`group: _idea-incubator/...` is not a link; only the body is scanned."""
        with _Vault() as v:
            _seed_clean(v)
            _, findings = _lint(v, resolves=lambda t: False)
            self.assertEqual([f for f in findings
                              if f.check_id == "incubator-wikilink"], [])


class TestDiscovery(unittest.TestCase):
    def test_finds_ledger_at_vault_root(self):
        with _Vault() as v:
            _seed_clean(v)
            roots = il.find_incubator_roots(v)
            self.assertEqual([r.relative_to(v).as_posix() for r in roots],
                             ["_idea-incubator"])

    def test_finds_ledger_nested_under_personal(self):
        """ideas_incubator.py still writes to personal/_idea-incubator, so the
        nested layout has to resolve too."""
        with _Vault() as v:
            _write(v, "personal/_idea-incubator/doom-llm-npcs/_index.md", _REAL_INDEX)
            roots = il.find_incubator_roots(v)
            self.assertEqual([r.relative_to(v).as_posix() for r in roots],
                             ["personal/_idea-incubator"])

    def test_no_ledger_is_not_an_error(self):
        with _Vault() as v:
            count, findings = _lint(v)
            self.assertEqual(findings, [])
            self.assertEqual(count, 0)


class TestVaultLintIntegration(unittest.TestCase):
    def test_exclusion_sets_still_contain_idea_incubator(self):
        """The bespoke pass is SEPARATE — it must not re-admit the ledger into
        the save.py-schema walk, or into dreaming."""
        import dream
        import frontmatter_validator
        for mod, dirs in (("vault_lint", vl._EXCLUDE_DIRS),
                          ("dream", dream._EXCLUDE_DIRS),
                          ("frontmatter_validator", frontmatter_validator._EXCLUDE_DIRS)):
            with self.subTest(module=mod):
                self.assertIn("_idea-incubator", dirs)

    def test_ledger_findings_reach_lint_vault(self):
        with _Vault() as v:
            _write(v, "_idea-incubator/blog-author/_index.md",
                   _REAL_INDEX.replace("doom-llm-npcs", "blog-author")
                              .replace("status: research-complete",
                                       "status: research-pending"))
            model, findings = vl.lint_vault(v)
            self.assertIn("incubator-summary-missing",
                          sorted(f.check_id for f in findings))
            self.assertEqual(model.incubator_files, 1)

    def test_ledger_is_still_absent_from_the_entry_corpus(self):
        with _Vault() as v:
            _seed_clean(v)
            model, _ = vl.lint_vault(v)
            self.assertEqual(model.entries, [])

    def test_scoped_run_skips_the_ledger(self):
        with _Vault() as v:
            _write(v, "_idea-incubator/blog-author/_index.md",
                   _REAL_INDEX.replace("doom-llm-npcs", "blog-author")
                              .replace("status: research-complete",
                                       "status: research-pending"))
            _, findings = vl.lint_vault(v, scope="personal")
            self.assertEqual([f for f in findings
                              if f.check_id.startswith("incubator-")], [])


class TestAliasAwareResolution(unittest.TestCase):
    """Obsidian resolves `[[x]]` via `aliases:` too. Without this, 55 of the
    live vault's 72 wikilink findings were false positives."""

    def test_alias_target_resolves(self):
        with _Vault() as v:
            _write(v, "personal/notes/2026-07-05-docs-prose-style.md",
                   "---\nkind: convention\nstatus: active\ncreated: 2026-07-05\n"
                   "updated: 2026-07-05\ntags: [voice]\ngroup: personal\n"
                   "slug: 2026-07-05-docs-prose-style\n"
                   "aliases: [docs-prose-style]\n---\n\nBody.\n")
            model = vl.build_model(v)
            self.assertTrue(vl._wikilink_resolves("docs-prose-style", model))

    def test_unaliased_missing_target_still_fails(self):
        """Alias support must not turn the check into a rubber stamp."""
        with _Vault() as v:
            _write(v, "personal/notes/a.md",
                   "---\nkind: convention\nstatus: active\ncreated: 2026-07-05\n"
                   "updated: 2026-07-05\ntags: [x]\ngroup: personal\nslug: a\n"
                   "aliases: [alpha]\n---\n\nBody.\n")
            model = vl.build_model(v)
            self.assertTrue(vl._wikilink_resolves("alpha", model))
            self.assertFalse(vl._wikilink_resolves("beta", model))

    def test_alias_list_is_split_on_commas(self):
        with _Vault() as v:
            _write(v, "personal/notes/s.md",
                   "---\nkind: convention\nstatus: active\ncreated: 2026-07-05\n"
                   "updated: 2026-07-05\ntags: [x]\ngroup: personal\nslug: s\n"
                   "aliases: [scheduled-agentm-skills, scheduled-agentic-harness-skills]\n"
                   "---\n\nBody.\n")
            model = vl.build_model(v)
            self.assertTrue(vl._wikilink_resolves("scheduled-agentm-skills", model))
            self.assertTrue(
                vl._wikilink_resolves("scheduled-agentic-harness-skills", model))


if __name__ == "__main__":
    unittest.main()
