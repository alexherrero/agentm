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
    "group: memory",
    "slug: {slug}",
    "always_load: true",
]


def _clean(slug: str) -> list:
    return [line.replace("{slug}", slug) for line in _CLEAN_FM]


class _Vault:
    def __enter__(self) -> Path:
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        (self.root / "memory" / "_always-load").mkdir(parents=True)
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
            _write(v, "memory/_always-load/foo.md", _clean("foo"))
            model, findings = _lint(v)
            self.assertEqual(len(model.entries), 1)
            self.assertEqual(findings, [], _ids(findings))

    def test_free_form_note_skipped(self):
        with _Vault() as v:
            # No frontmatter trio -> operator personal note -> skipped.
            (v / "memory" / "my-note.md").write_text(
                "# Just my thoughts\n\nno frontmatter here\n", encoding="utf-8")
            model, findings = _lint(v)
            self.assertEqual(len(model.entries), 0)
            self.assertEqual(model.skipped, 1)
            self.assertEqual(findings, [])

    def test_partial_frontmatter_skipped(self):
        with _Vault() as v:
            # Has `kind` but not the full trio -> not an agent entry.
            _write(v, "memory/p.md", ["kind: note"])
            model, findings = _lint(v)
            self.assertEqual(len(model.entries), 0)
            self.assertEqual(model.skipped, 1)

    def test_excluded_dirs_skipped(self):
        with _Vault() as v:
            _write(v, "memory/_idea-incubator/x.md", _clean("x"))
            _write(v, "_meta/y.md", _clean("y"))
            model, findings = _lint(v)
            self.assertEqual(len(model.entries), 0)

    def test_archive_dir_skipped(self):
        # L7: vault_lint.py was the one walker (unlike recall.py /
        # frontmatter_validator.py) that still descended into _archive/.
        with _Vault() as v:
            _write(v, "memory/_archive/old.md", _clean("old"))
            _write(v, "desk/projects/_archive/proj/notes/z.md", _clean("z"))
            model, findings = _lint(v)
            self.assertEqual(len(model.entries), 0)

    def test_opinions_dir_skipped(self):
        # Accumulate-loop supplement lanes (reflect._save_candidate_to_opinions)
        # carry the full agent-shaped trio but a bespoke schema — timestamp
        # `created:`, no updated/tags/group — so without the exclusion every
        # lane entry floods the lint with false findings. And a served
        # supplement is text the agent reads as its own standards: no lint
        # stage may touch it (same rationale as dream.py's exclusion).
        with _Vault() as v:
            _write(v, "memory/_opinions/plain-english/lane-entry.md", [
                "kind: opinion-supplement",
                "status: proposed",
                "created: 2026-07-25T10:00:00+00:00",
                "slug: lane-entry",
                "opinion: plain-english",
            ])
            (v / "memory" / "_opinions" / "plain-english.md").write_text(
                "---\nkind: opinion-supplement\nstatus: promoted\n---\n\nServed.\n",
                encoding="utf-8")
            model, findings = _lint(v)
            self.assertEqual(len(model.entries), 0)
            self.assertEqual(findings, [], _ids(findings))


class TestSupplementsUnderCrystallized(unittest.TestCase):
    def test_supplements_under_crystallized_skipped(self):
        # Filing-v2 part 3 folds the lanes into memory/crystallized/; the
        # exclusion follows the kind, and a crystallized memory beside them lints.
        with _Vault() as v:
            _write(v, "memory/crystallized/plain-english/lane-entry.md", [
                "kind: opinion-supplement",
                "status: proposed",
                "created: 2026-07-25T10:00:00+00:00",
                "slug: lane-entry",
                "opinion: plain-english",
            ])
            (v / "memory" / "crystallized" / "plain-english.md").write_text(
                "---\nkind: opinion-supplement\nstatus: promoted\n---\n\nServed.\n",
                encoding="utf-8")
            _write(v, "memory/crystallized/distilled.md", _clean("distilled"))
            model, findings = _lint(v)
            self.assertEqual([e.rel for e in model.entries], ["memory/crystallized/distilled.md"])
            self.assertEqual(findings, [], _ids(findings))


class TestExcludeDirsParity(unittest.TestCase):
    """The three vault walkers keep deliberate standalone copies of the
    exclude set (same-dir convention — see moc_generator.py's precedent
    note), so nothing enforces the mirror at runtime. These pins make the
    drift deterministic to catch: a dir added to one list can't silently go
    missing from the others (the exact drift that left vault_lint.py walking
    _opinions/ after dream.py excluded it)."""

    def test_dream_mirrors_vault_lint_plus_own_extras(self):
        import dream
        self.assertEqual(dream._EXCLUDE_DIRS,
                         vl._EXCLUDE_DIRS | {"_dream", ".obsidian"})

    def test_frontmatter_validator_mirrors_vault_lint_exactly(self):
        import frontmatter_validator
        self.assertEqual(frontmatter_validator._EXCLUDE_DIRS, vl._EXCLUDE_DIRS)

    def test_staging_and_lane_dirs_are_actually_excluded(self):
        """The two pins above assert the three lists AGREE; they say nothing
        about what is in them. Removing a directory from all three at once
        keeps them mutually consistent and passes both — found by mutation-
        testing the crystallization trigger, where dropping
        `_crystallize-staging` everywhere went entirely undetected.

        So assert the content too, for the directories whose exclusion is a
        stated guarantee rather than an incidental default: staged/queued
        machine artifacts and the served opinion lanes. `_opinions` and
        `_crystallize-staging` are the ones a walker must never touch — a
        served supplement is text the agent reads as its own standards, and a
        staged candidate is deliberately not a note.

        Worth naming honestly: for `_crystallize-staging` this is currently
        defense-in-depth, not a live hazard. All three walkers match `*.md`
        only and candidates are `*.json`, and two of the three never walk the
        vault root at all. The exclusion protects against a future change to
        either of those facts, and this test protects the exclusion."""
        import dream
        import frontmatter_validator
        # "scratch", not "desk/scratch": these sets are matched per path
        # segment, so a two-segment entry would match nothing.
        for name in ("_crystallize-staging", "_opinions", "_inbox", "scratch"):
            for mod, dirs in (
                ("vault_lint", vl._EXCLUDE_DIRS),
                ("dream", dream._EXCLUDE_DIRS),
                ("frontmatter_validator", frontmatter_validator._EXCLUDE_DIRS),
            ):
                with self.subTest(dir=name, module=mod):
                    self.assertIn(name, dirs, f"{mod} no longer excludes {name}/")


class TestChecks(unittest.TestCase):
    def test_required_field_missing(self):
        with _Vault() as v:
            fm = [l for l in _clean("a") if not l.startswith("tags:")]
            _write(v, "memory/_always-load/a.md", fm)
            _, findings = _lint(v)
            self.assertIn("required-field", _ids(findings, "error"))

    def test_kebab_case_kind_and_tag(self):
        with _Vault() as v:
            fm = _clean("b")
            fm[0] = "kind: Bad_Kind"
            fm[4] = "tags: [Bad_Tag, ok]"
            _write(v, "memory/_always-load/b.md", fm)
            _, findings = _lint(v)
            kebab = [f for f in findings if f.check_id == "kebab-case"]
            self.assertGreaterEqual(len(kebab), 2)

    def test_field_order(self):
        with _Vault() as v:
            fm = _clean("c")
            fm[0], fm[1] = fm[1], fm[0]  # swap kind/status
            _write(v, "memory/_always-load/c.md", fm)
            _, findings = _lint(v)
            self.assertIn("field-order", _ids(findings, "warn"))

    def test_slug_filename_mismatch(self):
        with _Vault() as v:
            _write(v, "memory/_always-load/d.md", _clean("not-d"))
            _, findings = _lint(v)
            self.assertIn("slug-filename", _ids(findings, "warn"))

    def test_bad_date(self):
        with _Vault() as v:
            fm = _clean("e")
            fm[2] = "created: 2026/05/19"
            _write(v, "memory/_always-load/e.md", fm)
            _, findings = _lint(v)
            self.assertIn("date-format", _ids(findings, "error"))

    def test_updated_before_created(self):
        with _Vault() as v:
            fm = _clean("f")
            fm[3] = "updated: 2026-05-01"  # before created 2026-05-19
            _write(v, "memory/_always-load/f.md", fm)
            _, findings = _lint(v)
            self.assertTrue(any(f.check_id == "date-format" and f.severity == "warn" for f in findings))

    def test_placeholder_value(self):
        with _Vault() as v:
            fm = _clean("g")
            fm[1] = "status: active | resolved | superseded"
            _write(v, "memory/_always-load/g.md", fm)
            _, findings = _lint(v)
            self.assertIn("placeholder-value", _ids(findings, "warn"))

    def test_schema_drift_unknown_key(self):
        with _Vault() as v:
            fm = _clean("h") + ["mystery: value"]
            _write(v, "memory/_always-load/h.md", fm)
            _, findings = _lint(v)
            self.assertIn("schema-drift", _ids(findings, "warn"))

    def test_wikilink_resolution(self):
        with _Vault() as v:
            _write(v, "memory/_always-load/real-slug.md", _clean("real-slug"))
            _write(v, "memory/_always-load/linker.md", _clean("linker"),
                   body="see [[real-slug]] and [[ghost]]\n")
            _, findings = _lint(v)
            wl = [f for f in findings if f.check_id == "wikilink-resolution"]
            self.assertEqual(len(wl), 1)  # only [[ghost]] is broken
            self.assertIn("ghost", wl[0].message)

    def test_wikilink_path_style_and_excluded_targets(self):
        with _Vault() as v:
            # A real target inside an EXCLUDED-from-lint dir is still a valid link target.
            _write(v, "memory/_idea-incubator/cluster/_index.md", _clean("idx"))
            _write(v, "memory/_always-load/k.md", _clean("k"),
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
            (vault / "memory" / "_always-load").mkdir(parents=True)
            _write(vault, "memory/_always-load/m.md", _clean("m"),
                   body="see [[Ideas#some heading]] and [[ghost-note]]\n")
            _, findings = vl.lint_vault(vault)
            wl = [f for f in findings if f.check_id == "wikilink-resolution"]
            self.assertEqual(len(wl), 1)  # [[Ideas]] resolves at the root; ghost-note doesn't
            self.assertIn("ghost-note", wl[0].message)

    def test_supersede_dangling(self):
        with _Vault() as v:
            fm = _clean("newer") + ["supersedes: nonexistent-slug"]
            _write(v, "memory/_always-load/newer.md", fm)
            _, findings = _lint(v)
            self.assertIn("supersede-integrity", _ids(findings, "error"))

    def test_supersede_target_still_active(self):
        with _Vault() as v:
            _write(v, "memory/_always-load/old.md", _clean("old"))  # status active
            fm = _clean("new2") + ["supersedes: old"]
            _write(v, "memory/_always-load/new2.md", fm)
            _, findings = _lint(v)
            self.assertTrue(any(
                f.check_id == "supersede-integrity" and f.severity == "warn" for f in findings))

    def test_supersede_by_stem_when_slug_differs(self):
        # Regression (adversarial review 2026-05-29): target referenced by FILE
        # STEM while its frontmatter slug differs. The dangling check uses the
        # stem+slug union; the "still active" warn must resolve by stem too.
        with _Vault() as v:
            fm_target = _clean("real-old")  # slug=real-old, status active
            _write(v, "memory/_always-load/oldfile.md", fm_target)  # stem=oldfile != slug
            fm = _clean("newer3") + ["supersedes: oldfile"]  # references by stem
            _write(v, "memory/_always-load/newer3.md", fm)
            _, findings = _lint(v)
            sup = [f for f in findings if f.check_id == "supersede-integrity"]
            # NOT flagged dangling (stem resolves) AND the still-active warn fires.
            self.assertTrue(any(f.severity == "warn" for f in sup), [f.message for f in sup])
            self.assertFalse(any(f.severity == "error" for f in sup), [f.message for f in sup])

    def test_supersede_cycle_detected(self):
        with _Vault() as v:
            fm_a = _clean("cycle-a") + ["supersedes: cycle-b"]
            fm_b = _clean("cycle-b") + ["supersedes: cycle-a"]
            _write(v, "memory/_always-load/cycle-a.md", fm_a)
            _write(v, "memory/_always-load/cycle-b.md", fm_b)
            _, findings = _lint(v)
            cycle = [f for f in findings if f.check_id == "supersede-cycle"]
            self.assertEqual(len(cycle), 2)  # reported from both members
            self.assertTrue(all(f.severity == "error" for f in cycle))

    def test_supersede_no_cycle_on_a_clean_chain(self):
        with _Vault() as v:
            _write(v, "memory/_always-load/head.md", _clean("head") + ["supersedes: mid"])
            fm_mid = _clean("mid")
            fm_mid[1] = "status: superseded"  # so the dangling-status check doesn't fire here
            _write(v, "memory/_always-load/mid.md", fm_mid + ["supersedes: tail"])
            fm_tail = _clean("tail")
            fm_tail[1] = "status: superseded"
            _write(v, "memory/_always-load/tail.md", fm_tail)
            _, findings = _lint(v)
            self.assertNotIn("supersede-cycle", _ids(findings))

    def test_supersede_fork_two_entries_claim_the_same_target(self):
        with _Vault() as v:
            fm_old = _clean("forked-old")
            fm_old[1] = "status: superseded"
            _write(v, "memory/_always-load/forked-old.md", fm_old)
            fm_a = _clean("forker-a") + ["supersedes: forked-old"]
            fm_b = _clean("forker-b") + ["supersedes: forked-old"]
            _write(v, "memory/_always-load/forker-a.md", fm_a)
            _write(v, "memory/_always-load/forker-b.md", fm_b)
            _, findings = _lint(v)
            fork = [f for f in findings if f.check_id == "supersede-fork"]
            self.assertEqual(len(fork), 2)  # reported from both claimants
            self.assertTrue(all(f.severity == "warn" for f in fork))

    def test_supersede_no_fork_with_a_single_claimant(self):
        with _Vault() as v:
            fm_old = _clean("single-old")
            fm_old[1] = "status: superseded"
            _write(v, "memory/_always-load/single-old.md", fm_old)
            _write(v, "memory/_always-load/single-new.md", _clean("single-new") + ["supersedes: single-old"])
            _, findings = _lint(v)
            self.assertNotIn("supersede-fork", _ids(findings))

    def test_dangling_supersession_status_with_no_backing_lineage(self):
        with _Vault() as v:
            fm = _clean("orphan-superseded")
            fm[1] = "status: superseded"
            _write(v, "memory/_always-load/orphan-superseded.md", fm)
            _, findings = _lint(v)
            dangling = [f for f in findings if f.check_id == "dangling-supersession"]
            self.assertEqual(len(dangling), 1)
            self.assertEqual(dangling[0].severity, "warn")

    def test_no_dangling_supersession_status_when_lineage_exists(self):
        with _Vault() as v:
            fm_old = _clean("backed-old")
            fm_old[1] = "status: superseded"
            _write(v, "memory/_always-load/backed-old.md", fm_old)
            _write(v, "memory/_always-load/backed-new.md", _clean("backed-new") + ["supersedes: backed-old"])
            _, findings = _lint(v)
            self.assertNotIn("dangling-supersession", _ids(findings))

    def test_kind_taxonomy_unknown_kind_flagged(self):
        with _Vault() as v:
            fm = _clean("mystery")
            fm[0] = "kind: totally-made-up-kind"
            _write(v, "memory/_always-load/mystery.md", fm)
            _, findings = _lint(v)
            kt = [f for f in findings if f.check_id == "kind-taxonomy"]
            self.assertEqual(len(kt), 1)
            self.assertEqual(kt[0].severity, "warn")

    def test_kind_taxonomy_known_kind_passes(self):
        with _Vault() as v:
            _write(v, "memory/_always-load/ordinary.md", _clean("ordinary"))  # kind: convention
            _, findings = _lint(v)
            self.assertNotIn("kind-taxonomy", _ids(findings))

    def _with_arc(self, slug: str, arc: str) -> list:
        # arc: sits between tags: and group: per save.FRONTMATTER_FIELD_ORDER
        # — inserted there so these tests don't also trip field-order.
        fm = _clean(slug)
        fm.insert(5, f"arc: {arc}")
        return fm

    def test_arc_registry_known_value_passes(self):
        with _Vault() as v:
            _write(v, "memory/_always-load/p.md", self._with_arc("p", "wave-a"))
            _, findings = _lint(v)
            self.assertNotIn("arc-registry", _ids(findings))

    def test_arc_registry_absent_arc_passes(self):
        # arc: is optional — most entries carry none, and that's not a finding.
        with _Vault() as v:
            _write(v, "memory/_always-load/q.md", _clean("q"))
            _, findings = _lint(v)
            self.assertNotIn("arc-registry", _ids(findings))

    def test_arc_registry_non_kebab_value_is_error(self):
        with _Vault() as v:
            fm = self._with_arc("r", "Not_Kebab")
            _write(v, "memory/_always-load/r.md", fm)
            _, findings = _lint(v)
            arc_findings = [f for f in findings if f.check_id == "arc-registry"]
            self.assertEqual(len(arc_findings), 1)
            self.assertEqual(arc_findings[0].severity, "error")

    def test_arc_registry_unrecognized_value_is_error(self):
        with _Vault() as v:
            fm = self._with_arc("s", "some-made-up-arc")
            _write(v, "memory/_always-load/s.md", fm)
            _, findings = _lint(v)
            arc_findings = [f for f in findings if f.check_id == "arc-registry"]
            self.assertEqual(len(arc_findings), 1)
            self.assertEqual(arc_findings[0].severity, "error")
            self.assertIn("some-made-up-arc", arc_findings[0].message)


class TestCalibration(unittest.TestCase):
    """Real-world calibrations surfaced by the live-vault dogfood."""

    def test_index_anchor_slug_not_flagged(self):
        with _Vault() as v:
            fm = _clean("_index")
            fm[0] = "kind: project-index"
            _write(v, "desk/projects/foo/_index.md", fm)
            _, findings = _lint(v)
            kebab = [f for f in findings if f.check_id == "kebab-case"]
            self.assertEqual(kebab, [], [f.message for f in kebab])

    def test_deep_group_path_allowed(self):
        with _Vault() as v:
            fm = _clean("a-decision")
            fm[5] = "group: desk/projects/agent-m-v4/decisions"
            _write(v, "memory/_always-load/a-decision.md", fm)
            _, findings = _lint(v)
            self.assertFalse(any(f.check_id == "kebab-case" and "group" in f.message for f in findings))
        # save.py's validator accepts it too (single source of truth).
        save._validate_group("desk/projects/agent-m-v4/decisions")  # must not raise


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
            _write(v, "memory/_always-load/a.md", _clean("a"))
            before = {p: p.read_bytes() for p in v.rglob("*.md")}
            with tempfile.TemporaryDirectory() as outdir:
                out = Path(outdir) / "report.md"
                rc = vl.main(["--audit", "--vault", str(v), "--out", str(out)])
                self.assertEqual(rc, 0)
                self.assertTrue(out.is_file())
            # The vault's entries are byte-for-byte unchanged (read-only).
            after = {p: p.read_bytes() for p in v.rglob("*.md")}
            self.assertEqual(before, after)

    def test_audit_default_path_under_diagnostics_lint(self):
        with _Vault() as v:
            _write(v, "memory/_always-load/a.md", _clean("a"))
            rc = vl.main(["--audit", "--vault", str(v)])
            self.assertEqual(rc, 0)
            reports = list((v / "diagnostics" / "lint").glob("vault-lint-*.md"))
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
            kind="k", group="memory", slug="s", tags=["a"],
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
        # `occurrences` is patch-only (dedup_guard.reinforce writes it on a
        # duplicate hit; _build_frontmatter never emits it) -- excluded here
        # alongside the caller-optional fields.
        expected = tuple(
            f for f in save.FRONTMATTER_FIELD_ORDER
            if f not in ("heat_pin", "source_url", "source_fetched",
                         "fingerprint", "occurrences", "lifecycle_tier", "derived_from", "arc")
        )
        self.assertEqual(tuple(keys), expected)

    def test_build_frontmatter_emits_fingerprint_when_provided(self):
        fm = save._build_frontmatter(
            kind="failure-incident", group="memory", slug="s", tags=[],
            always_load=False, supersedes=None, fingerprint="abc123",
        )
        keys = [line.split(":", 1)[0] for line in fm.splitlines()
                if ":" in line and not line.startswith("---")]
        expected = tuple(
            f for f in save.FRONTMATTER_FIELD_ORDER
            if f not in ("heat_pin", "source_url", "source_fetched",
                         "supersedes", "occurrences", "lifecycle_tier", "derived_from", "arc")
        )
        self.assertEqual(tuple(keys), expected)
        self.assertIn("fingerprint: abc123", fm)


if __name__ == "__main__":
    unittest.main()
