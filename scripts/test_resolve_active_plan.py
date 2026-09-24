#!/usr/bin/env python3
"""Contract tests for `resolve_active_plan` (V5-10 part 1, task 2).

`resolve_active_plan` picks the `(plan, progress)` filename pair a worker session
owns, with precedence **explicit arg → worktree-local `.harness/active-plan`
marker → legacy singleton `PLAN.md`**. The load-bearing guard (V5-10 Risk #7): a
*present* but unresolvable marker **raises** — it never silently degrades to the
singleton, which would mis-bind a worker to another worker's plan.

Run directly:

    python3 scripts/test_resolve_active_plan.py
"""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import harness_memory as hm  # noqa: E402

# Sandbox AGENTM_INSTALL_PREFIX module-wide so `_read_project_mode()`'s config
# fallback never reads the operator's real ~/.claude/.agentm-config.json (which
# could set state_mode=local and divert `read_state_file` off the vault). Mirrors
# test_harness_memory_named_plans.py's module-level sandbox.
_TEST_INSTALL_PREFIX = tempfile.mkdtemp(prefix="agentm-test-active-plan-prefix-")


def setUpModule() -> None:  # noqa: N802 — unittest convention
    os.environ["AGENTM_INSTALL_PREFIX"] = _TEST_INSTALL_PREFIX


def tearDownModule() -> None:  # noqa: N802
    os.environ.pop("AGENTM_INSTALL_PREFIX", None)
    shutil.rmtree(_TEST_INSTALL_PREFIX, ignore_errors=True)


_NAMED = ("PLAN-foo.md", "progress-foo.md")
_SINGLETON = ("PLAN.md", "progress.md")


class ResolveActivePlanPrecedence(unittest.TestCase):
    """Each precedence branch, plus the loud-error guard that makes a dangling
    marker fail instead of silently running the singleton."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="agentm-active-plan-")
        self.root = Path(self._tmp)
        self.vault = self.root / "vault"
        self.proj = self.root / "repo"
        # V5-3: plan files live in device-local .harness/, not vault/_harness/.
        self.harness = self.proj / ".harness"
        self.harness.mkdir(parents=True)
        self.resolution = {
            "vault_path": self.vault,
            "project_root": self.proj,
            "slug": "fixture",
        }

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    # --- fixture helpers ---

    def _write_plan(self, filename: str, body: str = "Status: in-progress\n") -> None:
        (self.harness / filename).write_text(body, encoding="utf-8")

    def _write_marker(self, text: str) -> Path:
        marker = self.proj / ".harness" / "active-plan"
        marker.write_text(text, encoding="utf-8")
        return marker

    # --- branch 1: explicit arg wins ---

    def test_explicit_arg_resolves_named_pair(self) -> None:
        self.assertEqual(
            hm.resolve_active_plan(self.resolution, plan_arg="foo"), _NAMED
        )

    def test_explicit_arg_accepts_filename_and_stem_forms(self) -> None:
        for arg in ("PLAN-foo.md", "PLAN-foo"):
            with self.subTest(arg=arg):
                self.assertEqual(
                    hm.resolve_active_plan(self.resolution, plan_arg=arg), _NAMED
                )

    def test_explicit_singleton_arg_resolves_singleton(self) -> None:
        for arg in ("", "PLAN", "PLAN.md"):
            with self.subTest(arg=arg):
                self.assertEqual(
                    hm.resolve_active_plan(self.resolution, plan_arg=arg), _SINGLETON
                )

    def test_explicit_arg_beats_marker(self) -> None:
        # A marker binds to bar (whose plan file is intentionally absent), but the
        # explicit arg names foo → foo wins AND the marker is never validated:
        # explicit precedence short-circuits before the marker branch.
        self._write_marker("bar")
        self.assertEqual(
            hm.resolve_active_plan(self.resolution, plan_arg="foo"), _NAMED
        )

    def test_explicit_arg_unsafe_raises_valueerror(self) -> None:
        for bad in ("../etc", "a/b", "..", "x\\y"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    hm.resolve_active_plan(self.resolution, plan_arg=bad)

    # --- branch 2: worktree-local marker, validated ---

    def test_valid_marker_resolves_named_pair(self) -> None:
        self._write_plan("PLAN-foo.md")
        self._write_marker("foo")
        self.assertEqual(hm.resolve_active_plan(self.resolution), _NAMED)

    def test_marker_filename_form_resolves(self) -> None:
        self._write_plan("PLAN-foo.md")
        self._write_marker("PLAN-foo.md")
        self.assertEqual(hm.resolve_active_plan(self.resolution), _NAMED)

    def test_marker_present_but_plan_absent_raises(self) -> None:
        self._write_marker("foo")  # no PLAN-foo.md in _harness/
        with self.assertRaises(hm.ActivePlanError):
            hm.resolve_active_plan(self.resolution)

    def test_marker_present_but_plan_empty_raises(self) -> None:
        self._write_plan("PLAN-foo.md", body="   \n")  # whitespace-only
        self._write_marker("foo")
        with self.assertRaises(hm.ActivePlanError):
            hm.resolve_active_plan(self.resolution)

    def test_marker_blank_raises(self) -> None:
        self._write_marker("   \n")  # present but empty → dangling binding
        with self.assertRaises(hm.ActivePlanError):
            hm.resolve_active_plan(self.resolution)

    def test_marker_unsafe_slug_raises(self) -> None:
        self._write_marker("../escape")
        with self.assertRaises(hm.ActivePlanError):
            hm.resolve_active_plan(self.resolution)

    # --- branch 3: legacy singleton default ---

    def test_no_arg_no_marker_resolves_singleton(self) -> None:
        self.assertEqual(hm.resolve_active_plan(self.resolution), _SINGLETON)

    def test_resolution_is_read_only(self) -> None:
        # Reader only — resolving the singleton must not create the marker file.
        hm.resolve_active_plan(self.resolution)
        self.assertFalse((self.proj / ".harness" / "active-plan").exists())


class ResolveActivePlanCLI(unittest.TestCase):
    """The `resolve-active-plan` CLI verb (V5-10 part 1) — the bash-reachable
    wrapper the crickets development-lifecycle bridge shells to so phase specs can
    target named plans without reimplementing resolution.

    Hermetic via **local mode** (`.harness/.project-mode = local`): resolution
    never touches a real vault, and `harness_state_dir` returns the repo-local
    `.harness/`, so the emitted paths are deterministic. The vault-mode dir
    branch is `harness_state_dir`'s own contract (covered in test_harness_memory).
    """

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="agentm-rap-cli-")
        self.proj = Path(self._tmp) / "repo"
        self.harness = self.proj / ".harness"
        self.harness.mkdir(parents=True)
        # Local mode: the repo-local .harness/ is the canonical state home, so
        # resolution is vault-free + hermetic (DC-2). harness_state_dir → here.
        (self.harness / ".project-mode").write_text("local\n", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _run(self, *cli_args: str, root: Path | None = None) -> tuple[int, str, str]:
        """Invoke `main()` for the verb with stdout/stderr captured."""
        out, err = io.StringIO(), io.StringIO()
        argv = [
            "resolve-active-plan",
            "--project-root", str(root or self.proj),
            *cli_args,
        ]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = hm.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def _pair(self, *names: str) -> str:
        return "\t".join(str(self.harness / n) for n in names)

    # --- happy paths: the tab-separated pair (LC-3) ---

    def test_bare_emits_singleton_pair(self) -> None:
        rc, out, _ = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), self._pair("PLAN.md", "progress.md"))

    def test_named_emits_named_pair(self) -> None:
        rc, out, _ = self._run("--plan", "foo")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), self._pair("PLAN-foo.md", "progress-foo.md"))

    def test_named_accepts_filename_form(self) -> None:
        rc, out, _ = self._run("--plan", "PLAN-foo.md")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), self._pair("PLAN-foo.md", "progress-foo.md"))

    def test_valid_marker_emits_named_pair(self) -> None:
        # No --plan → the worktree active-plan marker binds the pair.
        (self.harness / "PLAN-foo.md").write_text(
            "Status: in-progress\n", encoding="utf-8"
        )
        (self.harness / "active-plan").write_text("foo\n", encoding="utf-8")
        rc, out, _ = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), self._pair("PLAN-foo.md", "progress-foo.md"))

    # --- loud errors: exit 2, never a silent singleton fallback (Risk #7) ---

    def test_dangling_marker_exits_loud_no_singleton_fallback(self) -> None:
        # Marker binds to foo but PLAN-foo.md is absent → must NOT run PLAN.md.
        (self.harness / "active-plan").write_text("foo\n", encoding="utf-8")
        rc, out, err = self._run()
        self.assertEqual(rc, 2)
        self.assertEqual(out.strip(), "")          # emits no pair at all
        self.assertNotIn("PLAN.md", out)           # never the singleton
        self.assertIn("active-plan", err)

    def test_unsafe_plan_slug_exits_loud(self) -> None:
        rc, out, err = self._run("--plan", "../etc")
        self.assertEqual(rc, 2)
        self.assertEqual(out.strip(), "")
        self.assertIn("unsafe plan name", err)

    # --- V5-3: bare .harness/ always resolves to the singleton (no vault-mode dead end) ---

    def test_bare_project_resolves_singleton_v5_3(self) -> None:
        # V5-3: harness_state_dir always returns <root>/.harness/ — no vault needed.
        # A bare project with .harness/ (no PLAN.md) resolves the singleton pair.
        bare = Path(self._tmp) / "bare"
        (bare / ".harness").mkdir(parents=True)
        rc, out, _ = self._run(root=bare)
        self.assertEqual(rc, 0)
        self.assertIn("PLAN.md", out)
        self.assertIn("progress.md", out)

    # --- reader only ---

    def test_resolve_is_read_only(self) -> None:
        self._run("--plan", "foo")
        self.assertFalse((self.harness / "active-plan").exists())

    # --- the tracker beside the pair (agentm-vault plan 09) ---

    def test_with_tracker_appends_the_tracker_beside_the_pair(self) -> None:
        rc, out, _ = self._run("--plan", "foo", "--with-tracker")
        self.assertEqual(rc, 0)
        self.assertEqual(
            out.strip(), self._pair("PLAN-foo.md", "progress-foo.md", "tracker-foo.md")
        )

    def test_without_the_flag_the_output_is_still_the_pair(self) -> None:
        rc, out, _ = self._run("--plan", "foo")
        self.assertEqual(rc, 0)
        self.assertEqual(len(out.strip().split("\t")), 2)


class ResolveActivePlanTaskLayout(unittest.TestCase):
    """The task layout (agentm-vault plans 09 and 15).

    On a synced backend every plan is a task at `tasks/<name>/plan.md`, with the
    tracker beside it. Nothing else is read there: a flat pair in a stray
    retired state directory is never a plan, a singleton name is a bare call,
    and slug safety holds. A project with no vault keeps the repo-local pair.
    """

    def setUp(self) -> None:
        from storage_seam import Locator
        from vault_backend_stub import VaultBackend

        self._tmp = tempfile.mkdtemp(prefix="agentm-task-layout-")
        root = Path(self._tmp)
        self.vault = root / "vault"
        self.proj = root / "repo"
        (self.proj / ".harness").mkdir(parents=True)
        self.project_dir = self.vault / "projects" / "fixture"
        self.project_dir.mkdir(parents=True)
        self.resolution = {
            "backend": VaultBackend(root=self.vault, lock_root=root / "locks"),
            "project_locator": Locator("projects/fixture"),
            "project_root": self.proj,
            "slug": "fixture",
        }

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_task(self, slug: str = "foo", body: str = "# plan\n") -> Path:
        task = self.project_dir / "tasks" / slug
        task.mkdir(parents=True, exist_ok=True)
        (task / "plan.md").write_text(body, encoding="utf-8")
        return task

    def _write_stray_flat_pair(self, slug: str = "foo") -> Path:
        # The retired per-project state directory, come back with a flat pair
        # in it — the shape a stray writer or an old charter left behind.
        stray = self.project_dir / "_harness"
        stray.mkdir(parents=True, exist_ok=True)
        (stray / f"PLAN-{slug}.md").write_text("# a stray flat plan\n", encoding="utf-8")
        (stray / "PLAN.md").write_text("# a stray singleton\n", encoding="utf-8")
        return stray

    def _write_marker(self, text: str) -> None:
        (self.proj / ".harness" / "active-plan").write_text(text, encoding="utf-8")

    def test_the_task_layout_resolves_the_task_directory(self) -> None:
        task = self._write_task()
        active = hm.resolve_active_plan(self.resolution, plan_arg="foo")
        self.assertEqual(active.layout, "task")
        self.assertEqual(
            hm.active_plan_paths(self.resolution, plan_arg="foo"),
            (task / "plan.md", task / "progress.md", task / "tracker.md"),
        )

    def test_a_stray_flat_pair_is_never_read(self) -> None:
        stray = self._write_stray_flat_pair()
        task = self._write_task()
        plan, progress, tracker = hm.active_plan_paths(self.resolution, plan_arg="foo")
        self.assertEqual((plan, progress, tracker),
                         (task / "plan.md", task / "progress.md", task / "tracker.md"))
        for path in (plan, progress, tracker):
            self.assertNotEqual(path.parent, stray)

    def test_a_name_no_task_carries_is_placed_even_beside_a_stray_flat_pair(self) -> None:
        self._write_stray_flat_pair()
        active = hm.resolve_active_plan(self.resolution, plan_arg="foo")
        self.assertEqual((active.layout, active.slug), ("task", "001-foo"))
        self.assertEqual(Path(active[0]), self.project_dir / "tasks" / "001-foo" / "plan.md")

    def test_a_marker_bound_to_a_task_validates(self) -> None:
        task = self._write_task()
        self._write_marker("foo\n")
        active = hm.resolve_active_plan(self.resolution)
        self.assertEqual(active.layout, "task")
        self.assertEqual(Path(active[0]), task / "plan.md")

    def test_a_marker_bound_to_no_task_raises_even_with_a_stray_flat_pair(self) -> None:
        self._write_stray_flat_pair()
        self._write_marker("foo\n")
        with self.assertRaises(hm.ActivePlanError) as caught:
            hm.resolve_active_plan(self.resolution)
        self.assertIn("tasks/foo/plan.md", str(caught.exception))
        self.assertNotIn("PLAN-foo.md", str(caught.exception))

    def test_an_unsafe_slug_is_refused(self) -> None:
        self._write_task()
        for bad in ("../etc", "a/b", "..", "x\\y"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    hm.resolve_active_plan(self.resolution, plan_arg=bad)
        self._write_marker("../escape")
        with self.assertRaises(hm.ActivePlanError):
            hm.resolve_active_plan(self.resolution)

    def test_a_singleton_name_is_a_bare_call(self) -> None:
        self._write_stray_flat_pair()
        for arg in (None, "PLAN", "PLAN.md", ""):
            with self.subTest(arg=arg):
                with self.assertRaises(hm.TaskNameRequired):
                    hm.resolve_active_plan(self.resolution, plan_arg=arg)

    def test_a_device_local_harness_keeps_the_repo_local_pair(self) -> None:
        # No synced backend: a tasks/ directory in the repo is not a task.
        (self.proj / "tasks" / "foo").mkdir(parents=True)
        (self.proj / "tasks" / "foo" / "plan.md").write_text("# plan\n", encoding="utf-8")
        local = {"project_root": self.proj, "slug": "fixture"}
        active = hm.resolve_active_plan(local, plan_arg="foo")
        self.assertEqual((active, active.layout, active.tracker),
                         (_NAMED, "local", "tracker-foo.md"))
        self.assertEqual(
            hm.active_plan_paths(local, plan_arg="foo"),
            (self.proj / ".harness" / "PLAN-foo.md", self.proj / ".harness" / "progress-foo.md",
             self.proj / ".harness" / "tracker-foo.md"),
        )

    def test_the_repo_local_singleton_carries_its_tracker(self) -> None:
        local = {"project_root": self.proj, "slug": "fixture"}
        active = hm.resolve_active_plan(local)
        self.assertEqual(active, _SINGLETON)
        self.assertEqual((active.layout, active.tracker), ("local", "tracker.md"))


# ── Plan-name contract — golden vectors shared with the crickets twin ───────────
# These two tables are duplicated VERBATIM in the crickets fallback's test suite
# (crickets/scripts/test_resolve_plan.py, class PlanNameContractParity) so the
# standalone fallback normalizer can't drift from this authority. That's the
# disposition of the 2026-06-13 adversarial audit (finding ML2): no cross-repo
# import edge (DC-2), just one table asserted on both sides. Change a row here →
# change it there. `PLAN-PLAN.md` (singleton on both) and `foo\x00` (unsafe on
# both) are the two rows that encode the drifts the audit caught.
_PLAN_NAME_VECTORS = [
    ("", ("PLAN.md", "progress.md")),
    ("   ", ("PLAN.md", "progress.md")),
    ("PLAN", ("PLAN.md", "progress.md")),
    ("PLAN.md", ("PLAN.md", "progress.md")),
    ("PLAN-PLAN", ("PLAN.md", "progress.md")),
    ("PLAN-PLAN.md", ("PLAN.md", "progress.md")),
    ("foo", ("PLAN-foo.md", "progress-foo.md")),
    ("PLAN-foo", ("PLAN-foo.md", "progress-foo.md")),
    ("PLAN-foo.md", ("PLAN-foo.md", "progress-foo.md")),
    ("  PLAN-foo.md  ", ("PLAN-foo.md", "progress-foo.md")),
    ("my-plan", ("PLAN-my-plan.md", "progress-my-plan.md")),
]

_PLAN_SLUG_SAFETY = [
    ("foo", True), ("foo-bar", True), ("foo.bar", True), ("v2", True),
    (".", False), ("..", False), ("a/b", False), ("a\\b", False), ("foo\x00", False),
]


class PlanNameContractParity(unittest.TestCase):
    """The (name → pair) + slug-safety contract the crickets fallback must match.

    agentm is the authority (crickets delegates to it when a clone is present);
    these vectors pin the meaning so the standalone-crickets fallback can't drift.
    """

    def test_name_to_pair_golden_vectors(self) -> None:
        for name, expected in _PLAN_NAME_VECTORS:
            slug = hm._normalize_plan_name(name)
            self.assertEqual(hm._plan_pair(slug), expected, f"name={name!r}")

    def test_slug_safety_golden_vectors(self) -> None:
        for slug, expected in _PLAN_SLUG_SAFETY:
            self.assertEqual(hm._is_safe_plan_slug(slug), expected, f"slug={slug!r}")


class TaskPlacementAndLookup(unittest.TestCase):
    """A project that keeps its plans in numbered tasks (agentm-vault plan 10,
    task 7).

    Every project on a synced backend keeps its plans in a `tasks/` full of
    `NNN-<verb-slug>/` directories. Three things hold: a new plan lands in a new
    numbered task; a task is found by its own name or by the verb slug inside
    it, and two tasks sharing a verb slug are refused by name; and a bare call,
    which has no singleton to answer with, exits 4 rather than naming a file that
    will never exist. None of it depends on what else the project directory
    holds (agentm-vault plan 15).
    """

    def setUp(self) -> None:
        from storage_seam import Locator
        from vault_backend_stub import VaultBackend

        self._tmp = tempfile.mkdtemp(prefix="agentm-task-placement-")
        root = Path(self._tmp)
        self.vault = root / "vault"
        self.proj = root / "repo"
        (self.proj / ".harness").mkdir(parents=True)
        self.project_dir = self.vault / "projects" / "fixture"
        self.harness = self.project_dir / "_harness"
        self.tasks = self.project_dir / "tasks"
        self.tasks.mkdir(parents=True)  # migrated: tasks/ exists, _harness/ does not
        self.resolution = {
            "backend": VaultBackend(root=self.vault, lock_root=root / "locks"),
            "project_locator": Locator("projects/fixture"),
            "project_root": self.proj,
            "slug": "fixture",
        }

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _task(self, name: str, body: str = "# plan\n") -> Path:
        task = self.tasks / name
        task.mkdir(parents=True, exist_ok=True)
        (task / "plan.md").write_text(body, encoding="utf-8")
        return task

    def _run(self, *cli_args: str) -> tuple:
        out, err = io.StringIO(), io.StringIO()
        argv = ["resolve-active-plan", "--project-root", str(self.proj), *cli_args]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with unittest.mock.patch.object(hm, "resolve_project",
                                            return_value=self.resolution):
                rc = hm.main(argv)
        return rc, out.getvalue(), err.getvalue()

    # --- placement: a new plan lands in a new numbered task ---

    def test_a_new_plan_takes_the_first_number_on_an_empty_project(self) -> None:
        plan, progress, tracker = hm.active_plan_paths(
            self.resolution, plan_arg="build-the-brief")
        self.assertEqual(plan, self.tasks / "001-build-the-brief" / "plan.md")
        self.assertEqual(progress, self.tasks / "001-build-the-brief" / "progress.md")
        self.assertEqual(tracker, self.tasks / "001-build-the-brief" / "tracker.md")

    def test_a_new_plan_takes_the_next_free_number(self) -> None:
        self._task("001-first")
        self._task("007-seventh")
        self._task("003-third")
        active = hm.resolve_active_plan(self.resolution, plan_arg="eighth")
        self.assertEqual((active.layout, active.slug), ("task", "008-eighth"))

    def test_placement_writes_nothing(self) -> None:
        before = sorted(p.name for p in self.tasks.iterdir())
        hm.active_plan_paths(self.resolution, plan_arg="build-the-brief")
        self.assertEqual(sorted(p.name for p in self.tasks.iterdir()), before)
        self.assertFalse((self.tasks / "001-build-the-brief").exists())

    def test_a_number_the_operator_typed_is_not_prefixed_again(self) -> None:
        active = hm.resolve_active_plan(self.resolution, plan_arg="042-build-the-brief")
        self.assertEqual(active.slug, "042-build-the-brief")

    def test_a_stray_retired_state_directory_does_not_stop_placement(self) -> None:
        # The retired directory came back once, seventeen hours after the move,
        # and turned placement off; it no longer decides anything.
        self.harness.mkdir(parents=True)
        active = hm.resolve_active_plan(self.resolution, plan_arg="build-the-brief")
        self.assertEqual((active.layout, active.slug), ("task", "001-build-the-brief"))

    def test_a_repo_with_no_vault_keeps_the_repo_local_pair(self) -> None:
        local = {"project_root": self.proj, "slug": "fixture"}
        active = hm.resolve_active_plan(local, plan_arg="build-the-brief")
        self.assertEqual((active.layout, active[0]), ("local", "PLAN-build-the-brief.md"))

    # --- lookup: both forms of a task's name ---

    def test_a_task_is_found_by_its_own_name(self) -> None:
        task = self._task("042-build-the-brief")
        active = hm.resolve_active_plan(self.resolution, plan_arg="042-build-the-brief")
        self.assertEqual((Path(active[0]), active.slug),
                         (task / "plan.md", "042-build-the-brief"))

    def test_a_task_is_found_by_the_verb_slug_inside_it(self) -> None:
        task = self._task("042-build-the-brief")
        active = hm.resolve_active_plan(self.resolution, plan_arg="build-the-brief")
        self.assertEqual((Path(active[0]), active.slug),
                         (task / "plan.md", "042-build-the-brief"))

    def test_both_forms_find_the_same_task(self) -> None:
        self._task("042-build-the-brief", body="# the brief plan\n")
        by_name = hm.active_plan_paths(self.resolution, plan_arg="042-build-the-brief")
        by_slug = hm.active_plan_paths(self.resolution, plan_arg="build-the-brief")
        self.assertEqual(by_name, by_slug)
        self.assertEqual(by_name[0].read_text(encoding="utf-8"), "# the brief plan\n")

    def test_a_duplicate_slug_is_refused_by_name(self) -> None:
        self._task("012-build-the-brief")
        self._task("043-build-the-brief")
        with self.assertRaises(hm.ActivePlanError) as caught:
            hm.resolve_active_plan(self.resolution, plan_arg="build-the-brief")
        message = str(caught.exception)
        self.assertIn("012-build-the-brief", message)
        self.assertIn("043-build-the-brief", message)

    def test_a_duplicate_slug_still_resolves_by_the_numbered_form(self) -> None:
        # The refusal is the ambiguity, not the pair: naming one is unambiguous.
        self._task("012-build-the-brief")
        task = self._task("043-build-the-brief")
        active = hm.resolve_active_plan(self.resolution, plan_arg="043-build-the-brief")
        self.assertEqual(Path(active[0]), task / "plan.md")

    def test_an_unnumbered_task_directory_is_still_found_by_its_name(self) -> None:
        task = self._task("build-the-brief")
        active = hm.resolve_active_plan(self.resolution, plan_arg="build-the-brief")
        self.assertEqual(Path(active[0]), task / "plan.md")

    def test_a_blank_task_plan_is_placed_rather_than_resolved(self) -> None:
        # An empty plan.md is not a task, the same rule the flat layout applies;
        # on a migrated project the slug is then placed, not answered flat. The
        # placement is the directory that is already there: a second one beside
        # it would make the verb slug name two tasks, refused from then on.
        self._task("042-build-the-brief", body="   \n")
        self.assertIsNone(hm.task_paths(self.resolution, "build-the-brief"))
        active = hm.resolve_active_plan(self.resolution, plan_arg="build-the-brief")
        self.assertEqual((active.layout, active.slug), ("task", "042-build-the-brief"))

    def test_a_task_directory_with_no_plan_yet_is_reused(self) -> None:
        # An interrupted /plan leaves a tracker and no plan.
        (self.tasks / "050-foo").mkdir()
        (self.tasks / "050-foo" / "tracker.md").write_text("---\n---\n", encoding="utf-8")
        for name in ("foo", "050-foo"):
            with self.subTest(name=name):
                active = hm.resolve_active_plan(self.resolution, plan_arg=name)
                self.assertEqual(active.slug, "050-foo")
        self.assertEqual(sorted(p.name for p in self.tasks.iterdir()), ["050-foo"])

    def test_a_numbered_name_that_would_share_a_number_or_verb_slug_is_refused(self) -> None:
        self._task("042-bar")
        for name, why in (("042-baz", "its number"), ("999-bar", "its verb slug")):
            with self.subTest(name=name):
                with self.assertRaisesRegex(hm.ActivePlanError, why):
                    hm.resolve_active_plan(self.resolution, plan_arg=name)
                rc, out, _err = self._run("--plan", name)
                self.assertEqual((rc, out), (2, ""))
        active = hm.resolve_active_plan(self.resolution, plan_arg="043-qux")
        self.assertEqual(active.slug, "043-qux")

    def test_a_drive_qualified_name_is_unsafe(self) -> None:
        # `D:evil` joined onto a Windows vault root is drive-relative and leaves it.
        for name in ("D:evil", "c:x", "a:b"):
            with self.subTest(name=name):
                self.assertFalse(hm._is_safe_plan_slug(name))
                rc, out, _err = self._run("--plan", name)
                self.assertEqual((rc, out), (2, ""))

    # --- the bare call: exit 4 ---

    def test_a_bare_call_refuses_on_a_migrated_project(self) -> None:
        with self.assertRaises(hm.TaskNameRequired) as caught:
            hm.resolve_active_plan(self.resolution)
        self.assertIn("fixture", str(caught.exception))

    def test_a_bare_call_answers_exit_4_with_nothing_on_stdout(self) -> None:
        rc, out, err = self._run()
        self.assertEqual(rc, 4)
        self.assertEqual(out, "")
        self.assertIn("numbered tasks", err)

    def test_a_named_call_still_answers_0_and_the_line(self) -> None:
        self._task("042-build-the-brief")
        rc, out, err = self._run("--plan", "build-the-brief", "--with-tracker")
        self.assertEqual((rc, err), (0, ""))
        task = self.tasks / "042-build-the-brief"
        self.assertEqual(out.strip().split("\t"),
                         [str(task / "plan.md"), str(task / "progress.md"),
                          str(task / "tracker.md")])

    def test_a_duplicate_slug_answers_2_not_4(self) -> None:
        # Exit 4 means "name the task"; an ambiguous name is the loud refusal the
        # marker contract already owns, so it keeps 2 and never degrades to 4.
        self._task("012-build-the-brief")
        self._task("043-build-the-brief")
        rc, out, err = self._run("--plan", "build-the-brief")
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("043-build-the-brief", err)

    def test_a_bare_call_still_answers_4_beside_a_stray_retired_directory(self) -> None:
        # The live defect of 2026-09-22: the overnight job's pack recreated the
        # directory, and a bare call on agentm answered its singleton.
        self.harness.mkdir(parents=True)
        (self.harness / "PLAN.md").write_text("# a stray singleton\n", encoding="utf-8")
        rc, out, err = self._run()
        self.assertEqual((rc, out), (4, ""))
        self.assertIn("numbered tasks", err)


if __name__ == "__main__":
    unittest.main()
