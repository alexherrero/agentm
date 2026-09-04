#!/usr/bin/env python3
"""scripts/migrate/corpus_migration_3.py — the part-3 router, on a synthetic vault.

Every expected number below is written by hand from the fixture, never
recomputed by the engine: the fixture holds exactly the notes listed in
`_build`, and the assertions say what the router must decide about each.

Pins: the dry run writes nothing into the corpus (tree checksum before ==
after) and reports counts that reconcile with the fixture; the expired cohort
is scoped as designed (`inbox` vs `mined`); memories route by type through the
deprecations map with line-surgical edits and the lifecycle / provenance /
confidence stamps; a promoted inbox note whose target survives is filed
superseded, one whose target is gone routes as active; opinion supplements
land in crystallized/ keeping their kind; records and frontmatter-less files
are held; exact twins are marked, basename clashes renamed; the purge refuses
a missing or wrong count and, confirmed, removes exactly the manifest; a
dissolved directory's generated index goes with it; a second dry run after
the applies finds nothing left to route.

Needs the daemon binary (the filing contract's only parser): $AGENTMD, or
`agentmd` on PATH, or a Go toolchain to build one — otherwise skipped.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
for p in (_HERE, _REPO / "harness" / "skills" / "memory" / "scripts", _HERE / "migrate"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import storage_rules  # noqa: E402

_BUILD_DIR = None


def setUpModule():
    global _BUILD_DIR
    if os.environ.get("AGENTMD", "").strip():
        storage_rules.DAEMON_BIN = os.environ["AGENTMD"].strip()
        return
    found = shutil.which("agentmd")
    if found:
        os.environ["AGENTMD"] = found
        storage_rules.DAEMON_BIN = found
        return
    if shutil.which("go") is None:
        raise unittest.SkipTest("no agentmd and no go toolchain; set $AGENTMD to a built binary")
    _BUILD_DIR = tempfile.TemporaryDirectory(prefix="agentmd-build-")
    binary = Path(_BUILD_DIR.name) / "agentmd"
    subprocess.run(["go", "build", "-o", str(binary), "./cmd/agentmd"], cwd=str(_REPO / "daemon"), check=True)
    os.environ["AGENTMD"] = str(binary)
    storage_rules.DAEMON_BIN = str(binary)


def tearDownModule():
    if _BUILD_DIR is not None:
        _BUILD_DIR.cleanup()


import corpus_migration_3 as cm  # noqa: E402

RULES = _REPO / "daemon" / "internal" / "rules" / "storage-rules.default.md"
EXPIRED_TAIL = 'status: expired\nretired_because: "presumed injected"\n'


def _note(path: Path, fm: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{fm}---\n\n{body}\n", encoding="utf-8")


def _build(tmp: Path) -> Path:
    """The fixture: a nested vault whose memory root holds every population."""
    root = tmp / "Vault"
    (root / ".obsidian").mkdir(parents=True)
    v = root / "Agent"
    m = v / "memory"
    for cls in ("semantic", "procedural", "episodic", "entities", "crystallized", "mocs"):
        _note(m / cls / "_index.md", f"kind: dir-index\nstatus: active\nslug: {cls}-index\n", f"# {cls}")
    # inbox: two expired (one auto-miner retirement, one miner expiry without the marker)
    _note(m / "_inbox" / "exp-retired.md", "type: preference\nslug: exp-retired\nmining_confidence: LOW\n" + EXPIRED_TAIL, "junk one")
    _note(m / "_inbox" / "exp-plain.md", "type: workflow\nslug: exp-plain\nstatus: expired\nexpired_at: 2026-08-01\n", "junk two")
    # inbox live: kind-as-type drift, promoted with a surviving target, promoted with a gone target, active preference
    _note(m / "_inbox" / "wf-unfiled.md", "kind: workflow\nstatus: unfiled\nslug: wf-unfiled\nmining_confidence: MEDIUM\n", "a repeatable workflow")
    _note(m / "_inbox" / "wf-promoted-kept.md", "type: workflow\nstatus: promoted\nslug: wf-promoted-kept\npromoted_to: personal/workflow/wf-target.md\n", "promoted body kept")
    _note(m / "_inbox" / "wf-promoted-gone.md", "type: workflow\nstatus: promoted\nslug: wf-promoted-gone\npromoted_to: personal/workflow/nowhere.md\n", "promoted body gone")
    _note(m / "_inbox" / "pref-active.md", "type: preference\nstatus: active\nslug: pref-active\nmining_confidence: HIGH\n", "a real preference")
    _note(m / "workflow" / "wf-target.md", "type: workflow\nstatus: active\nslug: wf-target\n", "the promotion target")
    # legacy dirs: retired value in the dir name, one expired (mined) + one live; a twin of an inbox note; a basename clash
    _note(m / "preferences" / "legacy-exp.md", "type: preferences\nslug: legacy-exp\n" + EXPIRED_TAIL, "old junk")
    _note(m / "preferences" / "legacy-live.md", "type: preferences\nstatus: active\nslug: legacy-live\ncreated: 2026-06-01\n", "a real preference")
    _note(m / "preference" / "clash.md", "type: preference\nstatus: active\nslug: clash\ncreated: 2026-07-01\n", "clash body one")
    _note(m / "idea" / "clash.md", "type: idea\nstatus: active\nslug: clash\ncreated: 2026-07-02\n", "clash body two")
    # dated bucket
    _note(m / "2026" / "04" / "conv.md", "type: convention\nstatus: active\nslug: conv\n", "a convention")
    # archive: expired mined + one live archived
    _note(m / "_archive" / "_index.md", "kind: dir-index\nstatus: active\nslug: archive-index\n", "# archive")
    _note(m / "_archive" / "preferences" / "arch-exp.md", "type: preference\nslug: arch-exp\n" + EXPIRED_TAIL, "archived junk")
    _note(m / "_archive" / "preferences" / "arch-live.md", "type: preference\nstatus: active\nslug: arch-live\n", "archived but kept")
    # opinions: one expired, one live; kind kept
    _note(m / "_opinions" / "good" / "op-exp.md", "kind: opinion-supplement\nstatus: expired\nopinion: good\nslug: op-exp\n", "## good\nold supplement")
    _note(m / "_opinions" / "good" / "op-live.md", "kind: opinion-supplement\nstatus: active\nopinion: good\nslug: op-live\n", "## good\nlive supplement")
    # external: a record (held) and a memory (routed, provenance-tagged)
    _note(v / "external" / "primos" / "_index.md", "kind: project-index\nstatus: active\nslug: primos-index\n", "# primos")
    _note(v / "external" / "primos" / "analysis" / "a1.md", "kind: analysis\nstatus: active\nslug: a1\n", "an analysis record")
    _note(v / "external" / "primos" / "note.md", "type: reference\nstatus: active\nslug: ext-ref\n", "a fetched fact")
    _note(v / "external" / "primos" / "decisions" / "d1.md", "kind: decision\nstatus: active\nslug: d1\n", "a project decision")
    # vault archive: no frontmatter — held
    (v / "_vault-archive" / "history").mkdir(parents=True)
    (v / "_vault-archive" / "history" / "old.md").write_text("> [!WARNING] history\n", encoding="utf-8")
    # stray root file — held
    _note(m / "trusted-sources.md", "kind: dir-index\nstatus: active\nslug: trusted\n", "config-ish")
    # A Drive `Icon` artefact (the live name carries a trailing carriage
    # return, which Windows cannot spell — the walker skips the prefix) survives
    # the walk untouched and goes with its directory when that dissolves.
    (m / "_inbox" / "Icon").write_bytes(b"")
    return v


def _tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(root).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def _run(vault: Path, *args: str, report: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, MEMORY_VAULT_PATH=str(vault), AGENTM_STATE_DIR=str(report / "state"))
    return subprocess.run([sys.executable, str(_HERE / "migrate" / "corpus_migration_3.py"),
                           "--rules", str(RULES), "--report-dir", str(report), *args],
                          env=env, capture_output=True, text=True)


def _rows(vault: Path, **kw) -> dict:
    rules = storage_rules.load_file(RULES)
    rows = cm.plan(vault, rules, purge_scope=kw.get("purge_scope", "mined"))
    return {r.rel: r for r in rows}


class DryRunIsTheDefaultAndWritesNothing(unittest.TestCase):
    def test_zero_corpus_writes_and_a_reconciled_report(self):
        with tempfile.TemporaryDirectory() as td:
            vault = _build(Path(td))
            before = _tree_hash(vault.parent)
            report = Path(td) / "report"
            r = _run(vault, report=report)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertEqual(_tree_hash(vault.parent), before)
            runs = list(report.iterdir())
            self.assertEqual(len(runs), 1)
            self.assertTrue((runs[0] / "dispositions.csv").is_file())
            self.assertTrue((runs[0] / "purge-manifest.csv").is_file())
            self.assertTrue((runs[0] / "summary.md").is_file())
            self.assertTrue((runs[0] / "needs-review.md").is_file())
            # 22 notes: inbox 6 · legacy 5 (workflow/wf-target, preferences×2, preference/clash,
            # idea/clash) · dated 1 · archive 3 (index + 2) · opinions 2 · external 3 (index + 2)
            # · vault-archive 1 · stray 1 · plus the external decision record = 23. The six class shells are not a population.
            self.assertIn("Notes considered: **23**", r.stdout)


class DispositionsFollowTheDesign(unittest.TestCase):
    def test_every_population_lands_where_the_design_says(self):
        with tempfile.TemporaryDirectory() as td:
            vault = _build(Path(td))
            rows = _rows(vault)
            # the expired cohort, scope `mined`
            self.assertEqual(rows["memory/_inbox/exp-retired.md"].disposition, "purge")
            self.assertEqual(rows["memory/_inbox/exp-plain.md"].disposition, "purge")  # inbox: always the cohort
            self.assertEqual(rows["memory/preferences/legacy-exp.md"].disposition, "purge")
            self.assertEqual(rows["memory/_archive/preferences/arch-exp.md"].disposition, "purge")
            # opinions are never purged: expired supplements are archived, kind kept
            op = rows["memory/_opinions/good/op-exp.md"]
            self.assertEqual((op.disposition, op.dest, op.kind_after, op.lifecycle, op.status_after),
                             ("route", "memory/crystallized/good/op-exp.md", "opinion-supplement", "archived", "expired"))
            live_op = rows["memory/_opinions/good/op-live.md"]
            self.assertEqual((live_op.dest, live_op.lifecycle), ("memory/crystallized/good/op-live.md", "active"))
            # kind-as-type drift collapses onto `type:`; status map; confidence from the miner
            wf = rows["memory/_inbox/wf-unfiled.md"]
            self.assertEqual((wf.disposition, wf.dest, wf.field_before, wf.type_after, wf.status_after,
                              wf.lifecycle, wf.source, wf.filing_confidence),
                             ("route", "memory/procedural/wf-unfiled.md", "kind", "workflow", "unfiled",
                              "active", "conversation", "medium"))
            # promoted: target survives → superseded by it; target gone → active
            kept = rows["memory/_inbox/wf-promoted-kept.md"]
            # the target is itself a legacy-dir note that routes: point at where it lands
            self.assertEqual((kept.lifecycle, kept.superseded_by, kept.status_after),
                             ("superseded", "memory/procedural/wf-target.md", "active"))
            gone = rows["memory/_inbox/wf-promoted-gone.md"]
            self.assertEqual((gone.lifecycle, gone.superseded_by), ("active", ""))
            self.assertIn("promoted-target-gone", gone.flags)
            # legacy dir name is the old type; deprecations collapse it
            leg = rows["memory/preferences/legacy-live.md"]
            self.assertEqual((leg.disposition, leg.dest, leg.value_before, leg.type_after),
                             ("route", "memory/semantic/legacy-live.md", "preferences", "preference"))
            # dated, archive-live, external memory
            self.assertEqual(rows["memory/2026/04/conv.md"].dest, "memory/semantic/conv.md")
            arch = rows["memory/_archive/preferences/arch-live.md"]
            self.assertEqual((arch.dest, arch.lifecycle), ("memory/semantic/arch-live.md", "archived"))
            ext = rows["external/primos/note.md"]
            self.assertEqual((ext.dest, ext.source), ("memory/semantic/note.md", "external-fetch"))
            # holds and index drops
            self.assertEqual(rows["external/primos/analysis/a1.md"].disposition, "hold")
            # a project record whose kind the deprecations map would turn into a memory type stays put
            self.assertEqual(rows["external/primos/decisions/d1.md"].disposition, "hold")
            self.assertEqual(rows["external/primos/_index.md"].disposition, "drop-index")
            self.assertEqual(rows["memory/_archive/_index.md"].disposition, "drop-index")
            self.assertEqual(rows["_vault-archive/history/old.md"].disposition, "hold")
            self.assertEqual(rows["memory/trusted-sources.md"].disposition, "hold")
            # the class shells are not in any population
            self.assertNotIn("memory/semantic/_index.md", rows)

    def test_inbox_scope_keeps_the_designs_literal_cohort(self):
        with tempfile.TemporaryDirectory() as td:
            vault = _build(Path(td))
            rows = _rows(vault, purge_scope="inbox")
            self.assertEqual(rows["memory/_inbox/exp-retired.md"].disposition, "purge")
            leg = rows["memory/preferences/legacy-exp.md"]
            self.assertEqual((leg.disposition, leg.lifecycle, leg.status_after), ("route", "archived", "expired"))
            self.assertIn("expired-out-of-scope", leg.flags)

    def test_all_expired_scope_takes_the_supplements_too(self):
        with tempfile.TemporaryDirectory() as td:
            vault = _build(Path(td))
            rows = _rows(vault, purge_scope="all-expired")
            self.assertEqual(rows["memory/_opinions/good/op-exp.md"].disposition, "purge")
            self.assertEqual(rows["memory/_opinions/good/op-live.md"].disposition, "route")
            self.assertEqual(rows["memory/preferences/legacy-exp.md"].disposition, "purge")

    def test_supplements_already_home_are_kept_unless_all_expired(self):
        """After the archive phase the lanes sit under crystallized/; the walk
        reaches them so a later `all-expired` purge can take an expired
        supplement, and touches nothing else there."""
        with tempfile.TemporaryDirectory() as td:
            vault = _build(Path(td))
            lane = vault / "memory" / "crystallized" / "done"
            _note(lane / "home-exp.md", "kind: opinion-supplement\nstatus: expired\nopinion: done\nslug: home-exp\n", "old")
            _note(lane / "home-live.md", "kind: opinion-supplement\nstatus: proposed\nopinion: done\nslug: home-live\n", "live")
            _note(vault / "memory" / "crystallized" / "distilled.md", "type: workflow\nstatus: active\nslug: distilled\n", "a lesson")
            rows = _rows(vault)
            self.assertEqual(rows["memory/crystallized/done/home-exp.md"].disposition, "keep")
            self.assertEqual(rows["memory/crystallized/done/home-live.md"].disposition, "keep")
            self.assertNotIn("memory/crystallized/distilled.md", rows)  # a flat memory is not a population
            rows = _rows(vault, purge_scope="all-expired")
            self.assertEqual(rows["memory/crystallized/done/home-exp.md"].disposition, "purge")
            self.assertEqual(rows["memory/crystallized/done/home-live.md"].disposition, "keep")

    def test_twins_are_marked_and_basename_clashes_renamed(self):
        with tempfile.TemporaryDirectory() as td:
            vault = _build(Path(td))
            rows = _rows(vault)
            # exact twins: inbox pref-active and legacy-live share a body; the older wins
            a, b = rows["memory/_inbox/pref-active.md"], rows["memory/preferences/legacy-live.md"]
            self.assertEqual((b.lifecycle, b.superseded_by), ("active", ""))
            self.assertEqual((a.lifecycle, a.superseded_by), ("superseded", "memory/semantic/legacy-live.md"))
            self.assertIn("exact-twin", a.flags)
            # basename clash inside semantic/: the older keeps the name
            one, two = rows["memory/preference/clash.md"], rows["memory/idea/clash.md"]
            self.assertEqual(one.dest, "memory/semantic/clash.md")
            self.assertEqual(two.dest, "memory/semantic/clash~dup.md")
            self.assertIn("basename-clash", two.flags)


class ALaterPassSettlesAgainstWhatIsAlreadyHome(unittest.TestCase):
    """The daemon keeps capturing between passes. A re-capture of a memory an
    earlier pass filed (same basename, same body) is filed as its twin,
    superseded by the note already home; a namesake with a different body is
    a basename clash. Neither is an overwrite, and neither aborts the pass."""

    def test_twin_and_namesake_of_a_filed_note(self):
        with tempfile.TemporaryDirectory() as td:
            vault = _build(Path(td))
            home = vault / "memory" / "semantic"
            _note(home / "pref-active.md", "type: preference\nstatus: active\nslug: pref-active\nlifecycle: active\n", "a real preference")
            _note(home / "conv.md", "type: convention\nstatus: active\nslug: conv\n", "a DIFFERENT convention")
            rows = _rows(vault)
            twin = rows["memory/_inbox/pref-active.md"]
            self.assertEqual((twin.disposition, twin.dest, twin.lifecycle, twin.superseded_by),
                             ("route", "memory/semantic/pref-active~dup.md", "superseded", "memory/semantic/pref-active.md"))
            self.assertIn("exact-twin", twin.flags)
            namesake = rows["memory/2026/04/conv.md"]
            self.assertEqual(namesake.dest, "memory/semantic/conv~dup.md")
            self.assertIn("basename-clash", namesake.flags)
            r = _run(vault, "--apply", "--phase", "route", report=Path(td) / "report")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertTrue((home / "pref-active~dup.md").is_file())
            self.assertEqual((home / "pref-active.md").read_text(encoding="utf-8").count("a real preference"), 1)
            self.assertTrue((home / "conv~dup.md").is_file())
            self.assertIn("a DIFFERENT convention", (home / "conv.md").read_text(encoding="utf-8"))


class DestinationsAreSettledOnce(unittest.TestCase):
    """Names are settled in one pass, winners first, against the rows and the
    disk together — so a third note whose natural name is already `~dup`
    cannot collide with a renamed clash loser, and a twin's `superseded_by`
    is read from its winner's final name, never from a slot another note
    took later (the pre-tag review's two confirmed cases)."""

    def test_a_natural_dup_name_never_collides_with_a_renamed_loser(self):
        with tempfile.TemporaryDirectory() as td:
            vault = _build(Path(td))
            m = vault / "memory"
            _note(m / "_inbox" / "foo.md", "type: preference\nstatus: active\nslug: foo\ncreated: 2026-01-01\n", "body X")
            _note(m / "idea" / "foo.md", "type: idea\nstatus: active\nslug: foo\ncreated: 2026-02-01\n", "body Y")
            _note(m / "preferences" / "foo~dup.md", "type: preferences\nstatus: active\nslug: foo-dup\ncreated: 2026-03-01\n", "body Z")
            rows = _rows(vault)
            dests = [r.dest for r in rows.values() if r.disposition == "route"]
            self.assertEqual(len(dests), len(set(dests)), sorted(dests))
            self.assertEqual(rows["memory/_inbox/foo.md"].dest, "memory/semantic/foo.md")
            self.assertEqual(rows["memory/idea/foo.md"].dest, "memory/semantic/foo~dup.md")
            self.assertEqual(rows["memory/preferences/foo~dup.md"].dest, "memory/semantic/foo~dup2.md")
            r = _run(vault, "--apply", "--phase", "route", report=Path(td) / "report")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            for name in ("foo.md", "foo~dup.md", "foo~dup2.md"):
                self.assertTrue((m / "semantic" / name).is_file(), name)

    def test_a_twins_pointer_follows_its_winners_final_name(self):
        with tempfile.TemporaryDirectory() as td:
            vault = _build(Path(td))
            m = vault / "memory"
            # B is A's exact twin (same body) and is older, so B wins; C is an older
            # namesake of B with a different body, so B is renamed — A must follow it.
            _note(m / "_inbox" / "bar.md", "type: preference\nstatus: active\nslug: bar\ncreated: 2026-05-01\n", "same body")
            _note(m / "idea" / "bar.md", "type: idea\nstatus: active\nslug: bar\ncreated: 2026-04-01\n", "same body")
            _note(m / "preferences" / "bar.md", "type: preferences\nstatus: active\nslug: bar\ncreated: 2026-03-01\n", "another body")
            rows = _rows(vault)
            a, b, c = rows["memory/_inbox/bar.md"], rows["memory/idea/bar.md"], rows["memory/preferences/bar.md"]
            self.assertEqual(c.dest, "memory/semantic/bar.md")
            self.assertEqual(b.dest, "memory/semantic/bar~dup.md")
            self.assertEqual((a.lifecycle, a.superseded_by), ("superseded", b.dest))
            self.assertNotEqual(a.superseded_by, c.dest)


class ApplyPhases(unittest.TestCase):
    def test_route_then_archive_then_purge(self):
        with tempfile.TemporaryDirectory() as td:
            vault = _build(Path(td))
            report = Path(td) / "report"
            r = _run(vault, "--apply", "--phase", "route", report=report)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            wf = (vault / "memory/procedural/wf-unfiled.md").read_text(encoding="utf-8")
            self.assertIn("type: workflow\n", wf)
            self.assertNotIn("kind: workflow", wf)
            self.assertIn("lifecycle: active\n", wf)
            self.assertIn("source: conversation\n", wf)
            self.assertIn("filing_confidence: medium\n", wf)
            self.assertTrue(wf.endswith("a repeatable workflow\n"))
            kept = (vault / "memory/procedural/wf-promoted-kept.md").read_text(encoding="utf-8")
            self.assertIn("status: active\n", kept)
            self.assertIn("lifecycle: superseded\n", kept)
            self.assertIn("superseded_by: memory/procedural/wf-target.md\n", kept)
            self.assertTrue((vault / "memory/procedural/wf-target.md").is_file())
            self.assertTrue((vault / "memory/semantic/clash.md").is_file())
            self.assertTrue((vault / "memory/semantic/clash~dup.md").is_file())
            self.assertTrue((vault / "memory/semantic/note.md").is_file())
            self.assertIn("source: external-fetch\n", (vault / "memory/semantic/note.md").read_text(encoding="utf-8"))
            # dissolved: legacy dirs without expired residue, the dated bucket; kept: inbox (expired cohort), external (held records)
            self.assertFalse((vault / "memory/idea").exists())
            self.assertFalse((vault / "memory/preference").exists())
            self.assertFalse((vault / "memory/2026").exists())
            self.assertTrue((vault / "memory/_inbox/exp-retired.md").is_file())
            self.assertTrue((vault / "external/primos/analysis/a1.md").is_file())
            self.assertTrue((vault / "external/primos/decisions/d1.md").is_file())
            self.assertTrue((vault / "external/primos/_index.md").is_file())  # still holds a record

            r = _run(vault, "--apply", "--phase", "archive", report=report)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertTrue((vault / "memory/crystallized/good/op-exp.md").is_file())
            self.assertIn("lifecycle: archived\n", (vault / "memory/crystallized/good/op-exp.md").read_text(encoding="utf-8"))
            self.assertIn("kind: opinion-supplement\n", (vault / "memory/crystallized/good/op-live.md").read_text(encoding="utf-8"))
            self.assertIn("lifecycle: archived\n", (vault / "memory/semantic/arch-live.md").read_text(encoding="utf-8"))
            self.assertFalse((vault / "memory/_opinions").exists())
            self.assertTrue((vault / "memory/_archive/preferences/arch-exp.md").is_file())  # purge cohort waits

            # the purge: refused without the count, refused with the wrong count —
            # under `all-expired` the moved supplement makes the cohort five
            r = _run(vault, "--apply", "--phase", "purge", report=report)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("purge refused", r.stderr)
            r = _run(vault, "--apply", "--phase", "purge", "--confirm-count", "3", report=report)
            self.assertNotEqual(r.returncode, 0)
            self.assertTrue((vault / "memory/_inbox/exp-retired.md").is_file())
            r = _run(vault, "--apply", "--phase", "purge", "--purge-scope", "all-expired", "--confirm-count", "4", report=report)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("holds 5", r.stderr)
            self.assertTrue((vault / "memory/crystallized/good/op-exp.md").is_file())
            r = _run(vault, "--apply", "--phase", "purge", "--purge-scope", "all-expired", "--confirm-count", "5", report=report)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            for rel in ("memory/_inbox/exp-retired.md", "memory/_inbox/exp-plain.md",
                        "memory/preferences/legacy-exp.md", "memory/_archive/preferences/arch-exp.md",
                        "memory/crystallized/good/op-exp.md"):
                self.assertFalse((vault / rel).exists(), rel)
            self.assertTrue((vault / "memory/crystallized/good/op-live.md").is_file())  # the lane keeps its live supplement
            self.assertFalse((vault / "memory/_inbox").exists())
            self.assertFalse((vault / "memory/_archive").exists())      # its index went with it
            self.assertFalse((vault / "memory/preferences").exists())
            manifests = sorted(report.glob("*-purge/purge-manifest.csv"))
            self.assertEqual(len(manifests), 1)
            self.assertEqual(manifests[0].read_text(encoding="utf-8").count("\n"), 6)  # header + 5

            # a second dry run finds nothing left to route or purge
            r = _run(vault, report=report)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertNotIn("| route |", r.stdout)
            self.assertNotIn("| purge |", r.stdout)
            self.assertIn("| hold |", r.stdout)  # the held records remain, reported


if __name__ == "__main__":
    unittest.main()
