#!/usr/bin/env python3
"""Unit tests for harness/skills/memory/scripts/vault_lint.py (V4 #33).

The lint lives in the memory skill dir but its tests live here in scripts/ so CI
(`cd scripts && python3 -m unittest discover -p 'test_*.py'`) runs them. We add
the skill scripts dir to sys.path to import the module under test.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL_SCRIPTS = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import vault_lint as vl  # noqa: E402
import save  # noqa: E402


def _write(vault: Path, rel: str, fm_lines: list, body: str = "body\n") -> Path:
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    content = "---\n" + "\n".join(fm_lines) + "\n---\n\n" + body
    p.write_text(content, encoding="utf-8")
    return p


_CLEAN_FM = [
    "kind: convention",
    "status: active",
    "created: 2026-05-19",
    "updated: 2026-05-19",
    "tags: [dev-flow, docs]",
    "group: personal",
    "slug: {slug}",
    "always_load: true",
]


def _clean(slug: str) -> list:
    return [line.replace("{slug}", slug) for line in _CLEAN_FM]


class _Vault:
    def __enter__(self) -> Path:
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        (self.root / "personal" / "_always-load").mkdir(parents=True)
        return self.root

    def __exit__(self, *exc):
        self._td.cleanup()


def _lint(vault: Path):
    model, findings = vl.lint_vault(vault)
    return model, findings


def _ids(findings, severity=None):
    return sorted(f.check_id for f in findings if severity is None or f.severity == severity)


class TestGateAndParse(unittest.TestCase):
    def test_clean_entry_no_findings(self):
        with _Vault() as v:
            _write(v, "personal/_always-load/foo.md", _clean("foo"))
            model, findings = _lint(v)
            self.assertEqual(len(model.entries), 1)
            self.assertEqual(findings, [], _ids(findings))

    def test_free_form_note_skipped(self):
        with _Vault() as v:
            # No frontmatter trio -> operator personal note -> skipped.
            (v / "personal" / "my-note.md").write_text(
                "# Just my thoughts\n\nno frontmatter here\n", encoding="utf-8")
            model, findings = _lint(v)
            self.assertEqual(len(model.entries), 0)
            self.assertEqual(model.skipped, 1)
            self.assertEqual(findings, [])

    def test_partial_frontmatter_skipped(self):
        with _Vault() as v:
            # Has `kind` but not the full trio -> not an agent entry.
            _write(v, "personal/p.md", ["kind: note"])
            model, findings = _lint(v)
            self.assertEqual(len(model.entries), 0)
            self.assertEqual(model.skipped, 1)

    def test_excluded_dirs_skipped(self):
        with _Vault() as v:
            _write(v, "personal/_idea-incubator/x.md", _clean("x"))
            _write(v, "_meta/y.md", _clean("y"))
            model, findings = _lint(v)
            self.assertEqual(len(model.entries), 0)

    def test_archive_dir_skipped(self):
        # L7: vault_lint.py was the one walker (unlike recall.py /
        # frontmatter_validator.py) that still descended into _archive/.
        with _Vault() as v:
            _write(v, "personal/_archive/old.md", _clean("old"))
            _write(v, "projects/_archive/proj/notes/z.md", _clean("z"))
            model, findings = _lint(v)
            self.assertEqual(len(model.entries), 0)


class TestChecks(unittest.TestCase):
    def test_required_field_missing(self):
        with _Vault() as v:
            fm = [l for l in _clean("a") if not l.startswith("tags:")]
            _write(v, "personal/_always-load/a.md", fm)
            _, findings = _lint(v)
            self.assertIn("required-field", _ids(findings, "error"))

    def test_kebab_case_kind_and_tag(self):
        with _Vault() as v:
            fm = _clean("b")
            fm[0] = "kind: Bad_Kind"
            fm[4] = "tags: [Bad_Tag, ok]"
            _write(v, "personal/_always-load/b.md", fm)
            _, findings = _lint(v)
            kebab = [f for f in findings if f.check_id == "kebab-case"]
            self.assertGreaterEqual(len(kebab), 2)

    def test_field_order(self):
        with _Vault() as v:
            fm = _clean("c")
            fm[0], fm[1] = fm[1], fm[0]  # swap kind/status
            _write(v, "personal/_always-load/c.md", fm)
            _, findings = _lint(v)
            self.assertIn("field-order", _ids(findings, "warn"))

    def test_slug_filename_mismatch(self):
        with _Vault() as v:
            _write(v, "personal/_always-load/d.md", _clean("not-d"))
            _, findings = _lint(v)
            self.assertIn("slug-filename", _ids(findings, "warn"))

    def test_bad_date(self):
        with _Vault() as v:
            fm = _clean("e")
            fm[2] = "created: 2026/05/19"
            _write(v, "personal/_always-load/e.md", fm)
            _, findings = _lint(v)
            self.assertIn("date-format", _ids(findings, "error"))

    def test_updated_before_created(self):
        with _Vault() as v:
            fm = _clean("f")
            fm[3] = "updated: 2026-05-01"  # before created 2026-05-19
            _write(v, "personal/_always-load/f.md", fm)
            _, findings = _lint(v)
            self.assertTrue(any(f.check_id == "date-format" and f.severity == "warn" for f in findings))

    def test_placeholder_value(self):
        with _Vault() as v:
            fm = _clean("g")
            fm[1] = "status: active | resolved | superseded"
            _write(v, "personal/_always-load/g.md", fm)
            _, findings = _lint(v)
            self.assertIn("placeholder-value", _ids(findings, "warn"))

    def test_schema_drift_unknown_key(self):
        with _Vault() as v:
            fm = _clean("h") + ["mystery: value"]
            _write(v, "personal/_always-load/h.md", fm)
            _, findings = _lint(v)
            self.assertIn("schema-drift", _ids(findings, "warn"))

    def test_wikilink_resolution(self):
        with _Vault() as v:
            _write(v, "personal/_always-load/real-slug.md", _clean("real-slug"))
            _write(v, "personal/_always-load/linker.md", _clean("linker"),
                   body="see [[real-slug]] and [[ghost]]\n")
            _, findings = _lint(v)
            wl = [f for f in findings if f.check_id == "wikilink-resolution"]
            self.assertEqual(len(wl), 1)  # only [[ghost]] is broken
            self.assertIn("ghost", wl[0].message)

    def test_wikilink_path_style_and_excluded_targets(self):
        with _Vault() as v:
            # A real target inside an EXCLUDED-from-lint dir is still a valid link target.
            _write(v, "personal/_idea-incubator/cluster/_index.md", _clean("idx"))
            _write(v, "personal/_always-load/k.md", _clean("k"),
                   body="see [[_idea-incubator/cluster/_index]] and [[nope/missing]]\n")
            _, findings = _lint(v)
            wl = [f for f in findings if f.check_id == "wikilink-resolution"]
            self.assertEqual(len(wl), 1)  # path to _idea-incubator resolves; nope/missing doesn't
            self.assertIn("nope/missing", wl[0].message)

    def test_wikilink_resolves_against_obsidian_root(self):
        # Wikilinks resolve against the WHOLE Obsidian vault, not just AgentMemory.
        with tempfile.TemporaryDirectory() as td:
            obs = Path(td)
            (obs / ".obsidian").mkdir()
            (obs / "Ideas.md").write_text("# Ideas\n", encoding="utf-8")  # outside AgentMemory
            vault = obs / "AgentMemory"
            (vault / "personal" / "_always-load").mkdir(parents=True)
            _write(vault, "personal/_always-load/m.md", _clean("m"),
                   body="see [[Ideas#some heading]] and [[ghost-note]]\n")
            _, findings = vl.lint_vault(vault)
            wl = [f for f in findings if f.check_id == "wikilink-resolution"]
            self.assertEqual(len(wl), 1)  # [[Ideas]] resolves at the root; ghost-note doesn't
            self.assertIn("ghost-note", wl[0].message)

    def test_supersede_dangling(self):
        with _Vault() as v:
            fm = _clean("newer") + ["supersedes: nonexistent-slug"]
            _write(v, "personal/_always-load/newer.md", fm)
            _, findings = _lint(v)
            self.assertIn("supersede-integrity", _ids(findings, "error"))

    def test_supersede_target_still_active(self):
        with _Vault() as v:
            _write(v, "personal/_always-load/old.md", _clean("old"))  # status active
            fm = _clean("new2") + ["supersedes: old"]
            _write(v, "personal/_always-load/new2.md", fm)
            _, findings = _lint(v)
            self.assertTrue(any(
                f.check_id == "supersede-integrity" and f.severity == "warn" for f in findings))

    def test_supersede_by_stem_when_slug_differs(self):
        # Regression (adversarial review 2026-05-29): target referenced by FILE
        # STEM while its frontmatter slug differs. The dangling check uses the
        # stem+slug union; the "still active" warn must resolve by stem too.
        with _Vault() as v:
            fm_target = _clean("real-old")  # slug=real-old, status active
            _write(v, "personal/_always-load/oldfile.md", fm_target)  # stem=oldfile != slug
            fm = _clean("newer3") + ["supersedes: oldfile"]  # references by stem
            _write(v, "personal/_always-load/newer3.md", fm)
            _, findings = _lint(v)
            sup = [f for f in findings if f.check_id == "supersede-integrity"]
            # NOT flagged dangling (stem resolves) AND the still-active warn fires.
            self.assertTrue(any(f.severity == "warn" for f in sup), [f.message for f in sup])
            self.assertFalse(any(f.severity == "error" for f in sup), [f.message for f in sup])


class TestCalibration(unittest.TestCase):
    """Real-world calibrations surfaced by the live-vault dogfood."""

    def test_index_anchor_slug_not_flagged(self):
        with _Vault() as v:
            fm = _clean("_index")
            fm[0] = "kind: project-index"
            _write(v, "projects/foo/_index.md", fm)
            _, findings = _lint(v)
            kebab = [f for f in findings if f.check_id == "kebab-case"]
            self.assertEqual(kebab, [], [f.message for f in kebab])

    def test_deep_group_path_allowed(self):
        with _Vault() as v:
            fm = _clean("a-decision")
            fm[5] = "group: projects/agent-m-v4/decisions"
            _write(v, "personal/_always-load/a-decision.md", fm)
            _, findings = _lint(v)
            self.assertFalse(any(f.check_id == "kebab-case" and "group" in f.message for f in findings))
        # save.py's validator accepts it too (single source of truth).
        save._validate_group("projects/agent-m-v4/decisions")  # must not raise


class TestAuditReport(unittest.TestCase):
    def _model(self, n_entries=3):
        m = vl.VaultModel(vault=Path("/x"))
        m.entries = [object()] * n_entries
        return m

    def test_groups_identical_findings(self):
        findings = [
            vl.Finding("schema-drift", "warn", "a.md",
                       "unknown frontmatter key `domain` (not in the locked schema)", "remove `domain`"),
            vl.Finding("schema-drift", "warn", "b.md",
                       "unknown frontmatter key `domain` (not in the locked schema)", "remove `domain`"),
            vl.Finding("wikilink-resolution", "error", "c.md",
                       "wikilink `[[ghost]]` doesn't resolve to any file in the vault", "fix it"),
        ]
        r = vl.build_report(self._model(), findings, today="2026-05-29")
        self.assertIn("# MemoryVault lint audit — 2026-05-29", r)
        self.assertIn("**Summary:** 1 error · 2 warn · 0 info", r)
        self.assertIn("## Errors (1)", r)
        self.assertIn("## Warnings (2)", r)
        self.assertIn("**2×**", r)            # the two domain findings collapsed
        self.assertIn("`a.md`", r)            # entry list present
        self.assertIn("[[ghost]]", r)         # unique finding shown individually

    def test_clean_report(self):
        r = vl.build_report(self._model(), [], today="2026-05-29")
        self.assertIn("Clean — no findings", r)

    def test_audit_writes_only_the_report(self):
        with _Vault() as v:
            _write(v, "personal/_always-load/a.md", _clean("a"))
            before = {p: p.read_bytes() for p in v.rglob("*.md")}
            with tempfile.TemporaryDirectory() as outdir:
                out = Path(outdir) / "report.md"
                rc = vl.main(["--audit", "--vault", str(v), "--out", str(out)])
                self.assertEqual(rc, 0)
                self.assertTrue(out.is_file())
            # The vault's entries are byte-for-byte unchanged (read-only).
            after = {p: p.read_bytes() for p in v.rglob("*.md")}
            self.assertEqual(before, after)

    def test_audit_default_path_under_meta(self):
        with _Vault() as v:
            _write(v, "personal/_always-load/a.md", _clean("a"))
            rc = vl.main(["--audit", "--vault", str(v)])
            self.assertEqual(rc, 0)
            reports = list((v / "_meta").glob("vault-lint-*.md"))
            self.assertEqual(len(reports), 1)


class TestSchemaPin(unittest.TestCase):
    """DC-2: the lint reuses save.py's schema; pin save's builder to the constant."""

    def test_lint_uses_save_required_fields(self):
        # The required-field check iterates save.REQUIRED_FRONTMATTER_FIELDS.
        # heat_pin is written by the heat policy only (not by _build_frontmatter),
        # so it is optional alongside supersedes.
        self.assertEqual(
            set(save.REQUIRED_FRONTMATTER_FIELDS),
            set(save.FRONTMATTER_FIELD_ORDER) - save._OPTIONAL_FIELDS,
        )

    def test_build_frontmatter_emits_locked_order(self):
        fm = save._build_frontmatter(
            kind="k", group="personal", slug="s", tags=["a"],
            always_load=False, supersedes="some/path.md",
        )
        keys = [line.split(":", 1)[0] for line in fm.splitlines()
                if ":" in line and not line.startswith("---")]
        # heat_pin is written by the heat policy only, source_url/source_fetched
        # only by capture/ingest callers (capture-front-door plan task 1),
        # fingerprint only by callers that pass one (wave-c-diagnostics),
        # lifecycle_tier only by callers that pass one (V6-1), and
        # derived_from only by callers that pass one (V6-4) -- none of the
        # six is emitted by this default call; compare against the subset
        # that always is.
        expected = tuple(
            f for f in save.FRONTMATTER_FIELD_ORDER
            if f not in ("heat_pin", "source_url", "source_fetched",
                         "fingerprint", "lifecycle_tier", "derived_from")
        )
        self.assertEqual(tuple(keys), expected)

    def test_build_frontmatter_emits_fingerprint_when_provided(self):
        fm = save._build_frontmatter(
            kind="failure-incident", group="personal", slug="s", tags=[],
            always_load=False, supersedes=None, fingerprint="abc123",
        )
        keys = [line.split(":", 1)[0] for line in fm.splitlines()
                if ":" in line and not line.startswith("---")]
        expected = tuple(
            f for f in save.FRONTMATTER_FIELD_ORDER
            if f not in ("heat_pin", "source_url", "source_fetched",
                         "supersedes", "lifecycle_tier", "derived_from")
        )
        self.assertEqual(tuple(keys), expected)
        self.assertIn("fingerprint: abc123", fm)


if __name__ == "__main__":
    unittest.main()
