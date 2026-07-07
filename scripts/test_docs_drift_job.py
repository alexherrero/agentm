#!/usr/bin/env python3
"""Tests for docs_drift_job.py (PLAN-wave-e-scheduled-surfaces task 2,
agentm-side delegator) + the docs-drift.yaml job manifest shape.

Run directly: `cd scripts && python3 -m unittest test_docs_drift_job -v`
Auto-discovered by `python3 -m unittest discover -p 'test_*.py'` (check-all.sh).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from runner import manifest

_SCRIPTS = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


job = _load("docs_drift_job_agentm", _SCRIPTS / "docs_drift_job.py")


class TestSiblingResolution(unittest.TestCase):
    def test_env_override_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "src" / "wiki" / "scripts" / "wiki_watch_cycle.py"
            fake.parent.mkdir(parents=True)
            fake.write_text("# fake\n")
            old = os.environ.get("CRICKETS_REPO_ROOT")
            os.environ["CRICKETS_REPO_ROOT"] = tmp
            try:
                found = job.find_crickets_wiki_watch_cycle()
            finally:
                if old is None:
                    os.environ.pop("CRICKETS_REPO_ROOT", None)
                else:
                    os.environ["CRICKETS_REPO_ROOT"] = old
            self.assertEqual(found, fake)

    def test_sibling_checkout_found_on_this_machine(self):
        # This repo's documented layout is ~/Antigravity/{agentm,crickets} siblings.
        found = job.find_crickets_wiki_watch_cycle()
        if found is None:
            self.skipTest("crickets sibling checkout not present in this environment")
        self.assertTrue(found.is_file())


class TestGracefulSkip(unittest.TestCase):
    def test_missing_sibling_exits_zero(self):
        original = job.find_crickets_wiki_watch_cycle
        job.find_crickets_wiki_watch_cycle = lambda: None
        try:
            rc = job.main(["--repo", "."])
            self.assertEqual(rc, 0)
        finally:
            job.find_crickets_wiki_watch_cycle = original


class TestDelegation(unittest.TestCase):
    """Exercises the real crickets engine (skips if the sibling isn't checked
    out on this machine) to prove the composition this job relies on: drift
    surfaces as a candidate, and the wiki target is never written to — the
    engine only detects + plans, it never authors (that's the wiki-watch
    SKILL's job, not this bare cron delegator's)."""

    def setUp(self):
        found = job.find_crickets_wiki_watch_cycle()
        if found is None:
            self.skipTest("crickets sibling checkout not present in this environment")
        self.cyc = _load("crickets_wiki_watch_cycle_direct", found)
        cfg_path = found.parent / "wiki_watch_config.py"
        self.cfg = _load("crickets_wiki_watch_config_direct", cfg_path)

    def test_drift_surfaces_without_mutating_the_doc(self):
        with TemporaryDirectory() as td:
            wiki_target = Path(td) / "wiki"
            wiki_target.mkdir()
            stale_doc = wiki_target / "Some-Page.md"
            stale_doc.write_text("# Some Page\n\nstale content\n", encoding="utf-8")
            before = stale_doc.read_bytes()

            state_dir = Path(td) / "wiki-watch-state"
            run_config = self.cfg.RunConfig(watch_sources=["."], dispatch_mode="pr")

            report = self.cyc.run_cycle(
                "/fixture-repo", slug="fixture", enabled=True, run_config=run_config,
                wiki_target=str(wiki_target), state_dir=state_dir, token="fixture-sha",
                changed_paths=["PLAN.md"], gh_available=True, respect_cooldown=False,
                now=1000.0,
            )

            self.assertFalse(report.skipped, report.reason)
            paths = {c["path"]: c["recommendation"] for c in report.candidates}
            self.assertEqual(paths.get("PLAN.md"), "dispatch")  # drift surfaced
            self.assertEqual(stale_doc.read_bytes(), before)  # never mutated
            self.assertEqual(list(wiki_target.iterdir()), [stale_doc])  # nothing added either


class HealthPassSiblingDocsDriftManifestTests(unittest.TestCase):
    """Locks the schema shape of `.harness/jobs/docs-drift.yaml` (task 2). The
    manifest itself is gitignored (`.harness/` is machine-local) so it can't
    be asserted against directly in a portable test — this fixture mirrors
    its exact content so a regression in `manifest.py`'s parsing of this job
    shape still fails loud."""

    _DOCS_DRIFT_FIELDS = dict(
        schedule="daily",
        lookback="24h",
        command="python3 docs_drift_job.py --repo ..",
        tier="T2",
        dry_run=True,
    )

    def _write_job(self, jobs_dir: Path, name: str, **fields) -> Path:
        jobs_dir.mkdir(parents=True, exist_ok=True)
        body = dict(fields)
        lines = [f"{k}: {json.dumps(v)}" for k, v in body.items()]
        p = jobs_dir / f"{name}.yaml"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return p

    def test_docs_drift_manifest_shape_loads(self):
        with TemporaryDirectory() as td:
            jobs_dir = Path(td) / "jobs"
            self._write_job(jobs_dir, "docs-drift", **self._DOCS_DRIFT_FIELDS)
            jobs = manifest.load_manifests(jobs_dir)
            self.assertEqual(len(jobs), 1)
            j = jobs[0]
            self.assertEqual(j.name, "docs-drift")
            self.assertEqual(j.interval_seconds, 86400)
            self.assertEqual(j.lookback_seconds, 86400)
            self.assertEqual(j.tier, "T2")
            self.assertTrue(j.dry_run)
            self.assertEqual(j.command, "python3 docs_drift_job.py --repo ..")

    def test_t1_tier_is_rejected_for_this_job_shape(self):
        with TemporaryDirectory() as td:
            jobs_dir = Path(td) / "jobs"
            fields = dict(self._DOCS_DRIFT_FIELDS)
            fields["tier"] = "T1"
            self._write_job(jobs_dir, "docs-drift", **fields)
            with self.assertRaises(manifest.ManifestError):
                manifest.load_manifests(jobs_dir)


if __name__ == "__main__":
    unittest.main()
