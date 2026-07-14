#!/usr/bin/env python3
"""Unit coverage for scripts/health/health_score.py (R1.8).

Run directly:
    cd scripts && python3 -m unittest test_health_score
"""
from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE / "health") not in sys.path:
    sys.path.insert(0, str(_HERE / "health"))

import health_score  # noqa: E402


def _rec(axis, check, passed, weight=1.0, suite="s"):
    return {"suite": suite, "axis": axis, "check": check, "pass": passed, "weight": weight}


class TestScoreAxis(unittest.TestCase):
    def test_all_pass_scores_100(self):
        score, live, dark = health_score.score_axis([_rec("x", "a", True), _rec("x", "b", True)])
        self.assertEqual(score, 100.0)
        self.assertEqual(live, 2)
        self.assertEqual(dark, 0)

    def test_all_fail_scores_0(self):
        score, live, dark = health_score.score_axis([_rec("x", "a", False)])
        self.assertEqual(score, 0.0)

    def test_weighted_mix(self):
        score, _, _ = health_score.score_axis([_rec("x", "a", True, weight=2.0), _rec("x", "b", False, weight=1.0)])
        self.assertAlmostEqual(score, 100.0 * 2 / 3)

    def test_dark_checks_excluded_from_denominator(self):
        records = [_rec("x", "a", True, weight=1.0), {"suite": "s", "axis": "x", "check": "b", "pass": None, "dark": True, "weight": 5.0}]
        score, live, dark = health_score.score_axis(records)
        self.assertEqual(score, 100.0)
        self.assertEqual(live, 1)
        self.assertEqual(dark, 1)

    def test_empty_list_scores_0(self):
        score, live, dark = health_score.score_axis([])
        self.assertEqual((score, live, dark), (0.0, 0, 0))


class TestComputeScorecard(unittest.TestCase):
    def test_health_index_excludes_unrepresented_families(self):
        records = [_rec("memory persist+recall", "a", True)]
        sc = health_score.compute_scorecard(records)
        self.assertEqual(sc["health_index"], 100.0)

    def test_health_index_weighted_across_families(self):
        records = [
            _rec("memory persist+recall", "a", True),   # weight 25, score 100
            _rec("efficiency", "b", False),              # weight 10, score 0
        ]
        sc = health_score.compute_scorecard(records)
        self.assertAlmostEqual(sc["health_index"], (25 * 100 + 10 * 0) / 35, places=2)

    def test_unknown_axis_excluded_and_reported(self):
        records = [_rec("not-a-real-family", "a", True)]
        sc = health_score.compute_scorecard(records)
        self.assertEqual(sc["health_index"], 0.0)
        self.assertIn("not-a-real-family", sc["unknown_axes"])

    def test_no_records_at_all_zeroes_index(self):
        sc = health_score.compute_scorecard([])
        self.assertEqual(sc["health_index"], 0.0)


class TestReadRecords(unittest.TestCase):
    def test_reads_jsonl_from_path(self, ):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(_rec("x", "a", True)) + "\n\n")  # blank line must be skipped
            path = f.name
        try:
            records = health_score.read_records(path)
            self.assertEqual(len(records), 1)
        finally:
            Path(path).unlink()

    def test_invalid_json_raises_value_error(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("not json\n")
            path = f.name
        try:
            with self.assertRaises(ValueError):
                health_score.read_records(path)
        finally:
            Path(path).unlink()


class TestDarkChecksRendering(unittest.TestCase):
    def test_dark_check_appears_in_distinct_section_not_as_failure(self):
        records = [_rec("memory persist+recall", "a", True)]
        dark = [{"suite": "dark-registry", "axis": "capability function", "check": "runner", "pass": None, "dark": True, "weight": 1.0}]
        sc = health_score.compute_scorecard(records + dark)
        md = health_score.render_markdown(sc)
        self.assertIn("## Dark checks (designed, not built)", md)
        self.assertIn("runner", md)
        # a dark check must not depress the family's live Score/Checks count
        capability_row = [f for f in sc["families"] if f["axis"] == "capability function"][0]
        self.assertEqual(capability_row["live_count"], 0)
        self.assertEqual(capability_row["dark_count"], 1)

    def test_no_dark_section_when_no_dark_checks(self):
        records = [_rec("memory persist+recall", "a", True)]
        md = health_score.render_markdown(health_score.compute_scorecard(records))
        self.assertNotIn("Dark checks", md)

    def test_dark_checks_registry_file_loads_and_renders(self):
        # Count is NOT asserted here -- the registry is a living ledger of
        # designed-not-built capabilities (scripts/health/README.md's
        # "owning_plan" convention, AA5 consolidation task C3): it legitimately
        # shrinks to zero when every entry is flipped live or deleted as
        # stale, and grows again when a new capability is designed but not
        # yet built. This test only guards the real file's shape -- that it
        # loads without error and every record it does contain is a
        # well-formed dark check. The "renders in its own distinct section"
        # rendering behavior is proven with a synthetic record in
        # test_dark_check_appears_in_distinct_section_not_as_failure above,
        # so it doesn't need the real (possibly-empty) file to hold it.
        registry = _HERE / "health" / "dark-checks.jsonl"
        dark_records = health_score.read_records(str(registry))
        for r in dark_records:
            self.assertTrue(r.get("dark"))
            self.assertIsNone(r.get("pass"))
        sc = health_score.compute_scorecard(dark_records)
        self.assertEqual(sc["health_index"], 0.0)  # no live records, no family scored

    def test_bare_install_never_renders_fabricated_zero_score(self):
        # ROADMAP-TAIL-ADJUDICATIONS.md B3 / AA4 2026-07-08 fix: zero live
        # records anywhere (a true bare install -- only dark records, or no
        # records at all) must never render as a numeric "0.0/100" headline
        # with a red/green marker, which reads as a completed all-red run
        # rather than "nothing has run yet".
        registry = _HERE / "health" / "dark-checks.jsonl"
        dark_records = health_score.read_records(str(registry))
        sc = health_score.compute_scorecard(dark_records)
        self.assertEqual(sc["live_total"], 0)
        md = health_score.render_markdown(sc)
        self.assertNotIn("Health Index: 0.0/100", md)
        self.assertNotIn("🟢", md)
        self.assertNotIn("🔴", md)
        self.assertIn("not yet measured", md)

    def test_a_real_zero_score_still_renders_numerically(self):
        # Distinct from the bare-install case: a live record that actually
        # failed must still show as a real 0.0/100, not get swallowed by
        # the bare-install branch.
        records = [_rec("memory persist+recall", "a", False)]
        sc = health_score.compute_scorecard(records)
        self.assertEqual(sc["live_total"], 1)
        md = health_score.render_markdown(sc)
        self.assertIn("Health Index: 0.0/100", md)


class TestMechanicalUpliftRendering(unittest.TestCase):
    """PLAN-r3-uplift-scoring task 2 (R3.1b) — ablation records render as
    their own additive section, never folded into the Health Index."""

    def _ablation(self):
        return [
            {"subsystem": "vectors", "axis": "memory freshness+experience", "score_on": 100.0, "score_off": 66.67, "uplift": 33.33},
            {"subsystem": "gates", "axis": "capability function", "score_on": 100.0, "score_off": 0.0, "uplift": 100.0},
        ]

    def test_ablation_section_appears_with_uplift_values(self):
        records = [_rec("memory persist+recall", "a", True)]
        sc = health_score.compute_scorecard(records)
        md = health_score.render_markdown(sc, ablation_records=self._ablation())
        self.assertIn("## Mechanical uplift", md)
        self.assertIn("vectors", md)
        self.assertIn("33.33", md)
        self.assertIn("100.00", md)

    def test_no_ablation_section_when_absent(self):
        records = [_rec("memory persist+recall", "a", True)]
        md = health_score.render_markdown(health_score.compute_scorecard(records))
        self.assertNotIn("Mechanical uplift", md)

    def test_ablation_records_never_change_health_index(self):
        records = [_rec("memory persist+recall", "a", True), _rec("efficiency", "b", False)]
        sc = health_score.compute_scorecard(records)
        index_without = sc["health_index"]
        # Rendering with ablation records present must not alter the scorecard
        # dict itself — compute_scorecard never sees ablation records at all.
        health_score.render_markdown(sc, ablation_records=self._ablation())
        self.assertEqual(sc["health_index"], index_without)


class TestResolveHistoryPath(unittest.TestCase):
    """V8 proving Lane S, 2026-07-13: the ledger stops being a tracked repo
    file — resolve_history_path() picks the vault when one resolves, else a
    device-local fallback, mirroring console.py's resolve_vault_path()."""

    def test_uses_vault_when_resolved(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            got = health_score.resolve_history_path(vault_path_fn=lambda: vault)
            self.assertEqual(got, vault / "_meta" / "health" / "history.jsonl")

    def test_falls_back_to_device_cache_when_no_vault(self):
        got = health_score.resolve_history_path(vault_path_fn=lambda: None)
        self.assertEqual(got, Path.home() / ".cache" / "agentm" / "telemetry" / "health-history.jsonl")

    def test_falls_back_when_resolver_raises(self):
        def _boom():
            raise RuntimeError("no vault configured")
        got = health_score.resolve_history_path(vault_path_fn=_boom)
        self.assertEqual(got, Path.home() / ".cache" / "agentm" / "telemetry" / "health-history.jsonl")

    def test_append_and_read_round_trip_through_the_resolver(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            fn = lambda: vault  # noqa: E731
            records = [_rec("memory persist+recall", "a", True)]
            sc = health_score.compute_scorecard(records)
            resolved = health_score.resolve_history_path(vault_path_fn=fn)
            health_score.append_history_row(sc, ts=123, path=resolved)
            row = health_score.read_latest_history_row(path=health_score.resolve_history_path(vault_path_fn=fn))
            self.assertEqual(row["ts"], 123)


class TestRenderHtml(unittest.TestCase):
    def test_contains_health_index_and_family_table(self):
        records = [_rec("memory persist+recall", "a", True)]
        sc = health_score.compute_scorecard(records)
        out = health_score.render_html(sc)
        self.assertIn("Health Index: 100.0/100", out)
        self.assertIn("memory persist+recall", out)
        self.assertIn("<table>", out)

    def test_bare_install_never_renders_fabricated_zero_score(self):
        registry = _HERE / "health" / "dark-checks.jsonl"
        dark_records = health_score.read_records(str(registry))
        sc = health_score.compute_scorecard(dark_records)
        out = health_score.render_html(sc)
        self.assertIn("not yet measured", out)
        self.assertNotIn("Health Index: 0.0/100", out)

    def test_dark_checks_render_in_their_own_section(self):
        records = [_rec("memory persist+recall", "a", True)]
        dark = [{"suite": "dark-registry", "axis": "capability function", "check": "runner", "pass": None, "dark": True, "weight": 1.0}]
        sc = health_score.compute_scorecard(records + dark)
        out = health_score.render_html(sc)
        self.assertIn("Dark checks", out)
        self.assertIn("runner", out)

    def test_html_flag_writes_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            out_path = Path(td) / "scorecard.html"
            buf = io.StringIO()
            stdin_backup = sys.stdin
            sys.stdin = io.StringIO(json.dumps(_rec("memory persist+recall", "a", True)) + "\n")
            try:
                with redirect_stdout(buf):
                    rc = health_score.main(["--html-output", str(out_path)])
            finally:
                sys.stdin = stdin_backup
            self.assertEqual(rc, 0)
            self.assertTrue(out_path.is_file())
            self.assertIn("Health Index", out_path.read_text(encoding="utf-8"))


class TestDeterminism(unittest.TestCase):
    def test_two_runs_produce_identical_markdown(self):
        records = [_rec("memory persist+recall", "a", True), _rec("efficiency", "b", False)]
        out1 = health_score.render_markdown(health_score.compute_scorecard(records))
        out2 = health_score.render_markdown(health_score.compute_scorecard(records))
        self.assertEqual(out1, out2)


class TestMainCLI(unittest.TestCase):
    def test_main_renders_markdown_to_stdout(self):
        buf = io.StringIO()
        stdin_backup = sys.stdin
        sys.stdin = io.StringIO(json.dumps(_rec("memory persist+recall", "a", True)) + "\n")
        try:
            with redirect_stdout(buf):
                rc = health_score.main([])
        finally:
            sys.stdin = stdin_backup
        self.assertEqual(rc, 0)
        self.assertIn("Health Index", buf.getvalue())

    def test_main_no_records_exits_2(self):
        stdin_backup = sys.stdin
        sys.stdin = io.StringIO("")
        try:
            rc = health_score.main([])
        finally:
            sys.stdin = stdin_backup
        self.assertEqual(rc, 2)

    def test_check_determinism_flag_exits_0(self):
        buf = io.StringIO()
        stdin_backup = sys.stdin
        sys.stdin = io.StringIO(json.dumps(_rec("memory persist+recall", "a", True)) + "\n")
        try:
            with redirect_stdout(buf):
                rc = health_score.main(["--check-determinism"])
        finally:
            sys.stdin = stdin_backup
        self.assertEqual(rc, 0)

    def test_ablation_records_flag_renders_section_and_json(self):
        import tempfile
        ablation = [{"subsystem": "vectors", "axis": "memory freshness+experience", "score_on": 100.0, "score_off": 66.67, "uplift": 33.33}]
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(ablation[0]) + "\n")
            path = f.name
        try:
            buf = io.StringIO()
            stdin_backup = sys.stdin
            sys.stdin = io.StringIO(json.dumps(_rec("memory persist+recall", "a", True)) + "\n")
            try:
                with redirect_stdout(buf):
                    rc = health_score.main(["--ablation-records", path])
            finally:
                sys.stdin = stdin_backup
            self.assertEqual(rc, 0)
            self.assertIn("Mechanical uplift", buf.getvalue())

            buf2 = io.StringIO()
            sys.stdin = io.StringIO(json.dumps(_rec("memory persist+recall", "a", True)) + "\n")
            try:
                with redirect_stdout(buf2):
                    rc2 = health_score.main(["--ablation-records", path, "--format", "json"])
            finally:
                sys.stdin = stdin_backup
            self.assertEqual(rc2, 0)
            out = json.loads(buf2.getvalue())
            self.assertEqual(out["ablation_records"][0]["subsystem"], "vectors")
        finally:
            Path(path).unlink()

    def test_check_determinism_with_ablation_records_is_byte_identical(self):
        import tempfile
        ablation = [{"subsystem": "vectors", "axis": "memory freshness+experience", "score_on": 100.0, "score_off": 66.67, "uplift": 33.33}]
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(ablation[0]) + "\n")
            path = f.name
        try:
            stdin_backup = sys.stdin
            sys.stdin = io.StringIO(json.dumps(_rec("memory persist+recall", "a", True)) + "\n")
            try:
                rc = health_score.main(["--check-determinism", "--ablation-records", path])
            finally:
                sys.stdin = stdin_backup
            self.assertEqual(rc, 0)
        finally:
            Path(path).unlink()


if __name__ == "__main__":
    unittest.main()
