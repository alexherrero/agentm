#!/usr/bin/env python3
"""Contract tests for `process_seam` (V5-4 task 1) — the memory↔process client seam.

`process_seam` is the small, **read-only**, **graceful-no-op** view a *process*
(the crickets developer-workflows phases today; the V5-9 MCP server tomorrow)
calls instead of reaching into the memory engine. These tests pin its contract:

  - `offer_save_here` — advisory-only ([LC-2]): returns enriched save *candidates*
                        and **never persists**. Proven by the no-writes assertion.
  - `state_path`      — vault-backed when memory is present; **degrades to repo-local
                        ``<project_root>/.harness/`` ([LC-3])**, never ``None``; but a
                        corrupt/dangling ``active-plan`` marker **propagates loudly**
                        (V5-10 Risk #7) rather than degrading.

R0.9 (agentmEngine#2): a third function, `recall_here`, was retired (dead — it
delegated to a V5-3 stub that always returned "", no live crickets caller).
`RecallHereRetired` pins that it stays gone.

The load-bearing test is `SeamIsReadOnly`: every engine write entry point is
monkeypatched to raise if called, and a filesystem content snapshot is asserted
byte-identical before/after exercising both functions — making "the seam is
read-only" an executable claim, not a comment.

Run directly:

    python3 scripts/test_process_seam.py
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import harness_memory as hm  # noqa: E402
import process_seam as seam  # noqa: E402

# Sandbox AGENTM_INSTALL_PREFIX module-wide so the config fallback used by
# `vault_path()` and `_read_project_mode()` never reads the operator's real
# ~/.claude/.agentm-config.json. With it sandboxed, "no MEMORY_VAULT_PATH"
# deterministically means "no vault" and "no .project-mode marker"
# deterministically means "vault mode". Mirrors test_resolve_active_plan.py.
_TEST_INSTALL_PREFIX = tempfile.mkdtemp(prefix="agentm-test-process-seam-prefix-")

# Deterministic Tier-1 project slug — resolved from `.harness/project.json`'s
# `vault_project` field, so no git remote is needed (hermetic).
_SLUG = "seam-fixture"


def setUpModule() -> None:  # noqa: N802 — unittest convention
    os.environ["AGENTM_INSTALL_PREFIX"] = _TEST_INSTALL_PREFIX


def tearDownModule() -> None:  # noqa: N802
    os.environ.pop("AGENTM_INSTALL_PREFIX", None)
    shutil.rmtree(_TEST_INSTALL_PREFIX, ignore_errors=True)


class _SeamFixture(unittest.TestCase):
    """A repo whose slug resolves via Tier-1 ``project.json`` plus a sidecar vault.

    Each test opts into present / absent memory with ``_set_vault()`` /
    ``_unset_vault()``; MEMORY_VAULT_PATH is the single gate (the vault dirs exist
    on disk either way, so the env var alone toggles availability).
    """

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="agentm-process-seam-")
        self.root = Path(self._tmp)
        self.repo = self.root / "repo"
        self.harness = self.repo / ".harness"
        self.harness.mkdir(parents=True)
        # Tier-1 slug — deterministic + vault-free.
        (self.harness / "project.json").write_text(
            json.dumps({"vault_project": _SLUG}) + "\n", encoding="utf-8"
        )
        self.vault = self.root / "vault"
        self.vault_harness = self.vault / "projects" / _SLUG / "_harness"
        self.vault_harness.mkdir(parents=True)
        self._prev_vault = os.environ.get("MEMORY_VAULT_PATH")
        self._unset_vault()  # default posture: memory absent

    def tearDown(self) -> None:
        if self._prev_vault is None:
            os.environ.pop("MEMORY_VAULT_PATH", None)
        else:
            os.environ["MEMORY_VAULT_PATH"] = self._prev_vault
        shutil.rmtree(self._tmp, ignore_errors=True)

    # --- memory presence toggles (env is the single gate) ---

    def _set_vault(self) -> None:
        os.environ["MEMORY_VAULT_PATH"] = str(self.vault)

    def _unset_vault(self) -> None:
        os.environ.pop("MEMORY_VAULT_PATH", None)

    def _dl_select(self):
        """Patch select_backend → DeviceLocalBackend for present-vault tests.

        With MEMORY_VAULT_PATH set, select_backend resolves the vault backend and
        (post-R0.4 fail-loud) raises on CI where the obsidian-vault plugin is
        absent. These tests exercise seam mechanics, not backend selection —
        the propagation behavior has its own dedicated coverage.
        """
        import storage_device_local as _sdl
        return unittest.mock.patch(
            "backend_selection.select_backend",
            return_value=_sdl.DeviceLocalBackend(self.root / "device_local"),
        )

    def _seed_always_load(self, body: str = "- a test convention\n") -> None:
        """Give `phase_recall` something to return: one always-load entry."""
        al = self.vault / "personal" / "_always-load"
        al.mkdir(parents=True, exist_ok=True)
        (al / "test-conv.md").write_text(body, encoding="utf-8")

    def _local_mode(self) -> None:
        (self.harness / ".project-mode").write_text("local\n", encoding="utf-8")

    def _ctx(self, **kw) -> dict:
        ctx = {"cwd": str(self.repo)}
        ctx.update(kw)
        return ctx


class RecallHereRetired(_SeamFixture):
    """R0.9: `recall_here` was retired (dead — delegated to a V5-3 stub that
    always returned "", no live crickets caller). Pin that it stays gone."""

    def test_recall_here_not_present(self) -> None:
        self.assertFalse(hasattr(seam, "recall_here"))


class OfferSaveHere(_SeamFixture):
    """`offer_save_here` — advisory enrichment, absent/empty degrade, no mutation."""

    def test_empty_candidate_returns_empty_list(self) -> None:
        self._set_vault()
        for empty in (None, "", {}):
            with self.subTest(candidate=empty):
                self.assertEqual(seam.offer_save_here(self._ctx(), empty), [])

    def test_absent_vault_returns_empty_list(self) -> None:
        cand = {"kind": "decision", "slug": "x", "body": "b"}
        self.assertEqual(seam.offer_save_here(self._ctx(), cand), [])

    def test_present_enriches_with_project_and_target(self) -> None:
        # Needs a working backend. Use DeviceLocalBackend via AGENTM_DEVICE_LOCAL_ROOT
        # so the test works in CI without the obsidian-vault plugin (V5-6 de-vaulting).
        # MEMORY_VAULT_PATH stays set for is_available(); select_backend() is patched
        # to return a DeviceLocalBackend pointed at the temp location.
        self._set_vault()
        import storage_device_local as _sdl
        dl_backend = _sdl.DeviceLocalBackend(self.root / "device_local")
        cand = {"kind": "decision", "slug": "x", "body": "b"}
        with unittest.mock.patch("backend_selection.select_backend", return_value=dl_backend):
            out = seam.offer_save_here(self._ctx(), cand)
        self.assertEqual(len(out), 1)
        enriched = out[0]
        self.assertEqual(enriched["project"], _SLUG)
        self.assertEqual(enriched["kind"], "decision")  # original keys preserved
        self.assertEqual(enriched["body"], "b")
        self.assertIsNotNone(enriched["target"])
        self.assertIn(_SLUG, enriched["target"])  # resolved seam target key

    def test_phase_passed_through_onto_candidate(self) -> None:
        self._set_vault()
        with self._dl_select():
            out = seam.offer_save_here(self._ctx(phase="review"), {"body": "b"})
        self.assertEqual(out[0]["phase"], "review")

    def test_non_dict_candidate_wrapped_as_body(self) -> None:
        self._set_vault()
        with self._dl_select():
            out = seam.offer_save_here(self._ctx(), "free-text candidate")
        self.assertEqual(out[0]["body"], "free-text candidate")
        self.assertEqual(out[0]["project"], _SLUG)

    def test_caller_candidate_not_mutated(self) -> None:
        # Enrichment copies — the caller's dict is never mutated in place.
        self._set_vault()
        cand = {"kind": "decision", "slug": "x", "body": "b"}
        with self._dl_select():
            seam.offer_save_here(self._ctx(), cand)
        self.assertNotIn("project", cand)
        self.assertNotIn("target", cand)


class StatePath(_SeamFixture):
    """`state_path` — vault-backed / repo-local degrade / loud-fail propagation."""

    def test_invalid_which_raises_valueerror(self) -> None:
        with self.assertRaises(ValueError):
            seam.state_path(self._ctx(), "bogus")

    def test_local_mode_resolves_repo_local(self) -> None:
        self._local_mode()
        self.assertEqual(seam.state_path(self._ctx(), "plan"), self.harness / "PLAN.md")
        self.assertEqual(
            seam.state_path(self._ctx(), "progress"), self.harness / "progress.md"
        )

    def test_vault_mode_no_vault_degrades_repo_local(self) -> None:
        # [LC-3]: vault mode (no .project-mode marker) + no MEMORY_VAULT_PATH →
        # repo-local <project_root>/.harness/, never None.
        self._unset_vault()
        self.assertEqual(seam.state_path(self._ctx(), "plan"), self.harness / "PLAN.md")

    def test_vault_mode_resolves_vault_when_synced(self) -> None:
        # ADR 0020 (reverses V5-3 DC-1): vault mode + a live synced backend routes
        # state into <vault>/projects/<slug>/_harness/. V5-3 deleted the kernel
        # storage_vault.py, so we mock select_backend to return a vault stub
        # (same approach as test_present_enriches_with_project_and_target).
        import unittest.mock
        from vault_backend_stub import VaultBackend

        self._set_vault()
        vault_backend = VaultBackend(root=self.vault)
        with unittest.mock.patch(
            "backend_selection.select_backend", return_value=vault_backend
        ):
            self.assertEqual(
                seam.state_path(self._ctx(), "plan"), self.vault_harness / "PLAN.md"
            )

    def test_named_plan_via_context(self) -> None:
        self._local_mode()
        self.assertEqual(
            seam.state_path(self._ctx(plan="foo"), "plan"), self.harness / "PLAN-foo.md"
        )
        self.assertEqual(
            seam.state_path(self._ctx(plan="foo"), "progress"),
            self.harness / "progress-foo.md",
        )

    def test_dangling_marker_propagates_never_degrades(self) -> None:
        # A present-but-blank active-plan marker is a loud-fail safety property
        # (V5-10 Risk #7), NOT the absent-memory degrade. state_path must
        # propagate the ActivePlanError, never swallow it to a singleton path.
        self._local_mode()
        (self.harness / "active-plan").write_text("   \n", encoding="utf-8")
        with self.assertRaises(hm.ActivePlanError):
            seam.state_path(self._ctx(), "plan")

    def test_unsafe_plan_arg_propagates_valueerror(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsafe plan name"):
            seam.state_path(self._ctx(plan="../escape"), "plan")

    def test_read_only_no_marker_created(self) -> None:
        self._local_mode()
        seam.state_path(self._ctx(), "plan")
        self.assertFalse((self.harness / "active-plan").exists())


class SeamIsReadOnly(_SeamFixture):
    """The load-bearing read-only proof — LC-2 + the V5-4 "seam is read-only" claim.

    Two complementary tripwires:
      1. every engine write entry point is monkeypatched to raise if reached;
      2. a filesystem content snapshot (vault + repo) is asserted byte-identical
         before/after exercising all three functions across present + absent memory.
    A miss in (1) is still caught by (2): an actual file mutation fails the snapshot.
    """

    # Every public/private write entry point in the engine. The seam must reach
    # none of them — patched to raise so a regression that adds a write fails loud.
    _WRITE_FNS = (
        "offer_save",
        "write_state_file",
        "_write_repo_local_state_file",
        "safe_write_replace_style",
        "_invoke_toolkit_save",
        "write_cursor",
        "write_vault_project",
        "atomic_write",
    )

    def _install_tripwires(self) -> list:
        import vault_lock

        reached: list[str] = []

        def _boom(label: str):
            def _raiser(*_a, **_k):
                reached.append(label)
                raise AssertionError(
                    f"process_seam reached write path {label!r} — it must be read-only"
                )

            return _raiser

        self._restores: list[tuple] = []
        for name in self._WRITE_FNS:
            if hasattr(hm, name):
                self._restores.append((hm, name, getattr(hm, name)))
                setattr(hm, name, _boom(f"harness_memory.{name}"))
        # The canonical write primitive, in case any path bypasses the hm binding.
        if hasattr(vault_lock, "atomic_write"):
            self._restores.append((vault_lock, "atomic_write", vault_lock.atomic_write))
            vault_lock.atomic_write = _boom("vault_lock.atomic_write")
        return reached

    def _remove_tripwires(self) -> None:
        for obj, name, orig in getattr(self, "_restores", []):
            setattr(obj, name, orig)

    def _snapshot(self, *roots: Path) -> dict:
        """Map each file under `roots` to its content hash — catches create,
        modify, and (by absence) delete."""
        snap: dict[str, str] = {}
        for root in roots:
            root = Path(root)
            if not root.exists():
                continue
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    rel = f"{root.name}/{p.relative_to(root)}"
                    snap[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        return snap

    def test_no_write_path_reached_and_fs_unchanged(self) -> None:
        # Seed all fixtures BEFORE the snapshot so it brackets only seam calls.
        self._seed_always_load()
        reached = self._install_tripwires()
        try:
            before = self._snapshot(self.vault, self.repo)

            # Present memory — exercise every function.
            self._set_vault()
            with self._dl_select():
                seam.offer_save_here(
                    self._ctx(phase="work"), {"kind": "decision", "slug": "x", "body": "b"}
                )
                seam.state_path(self._ctx(), "plan")
                seam.state_path(self._ctx(), "progress")

            # Absent memory — the degrade paths.
            self._unset_vault()
            seam.offer_save_here(self._ctx(), {"body": "b"})
            seam.state_path(self._ctx(), "plan")

            after = self._snapshot(self.vault, self.repo)
        finally:
            self._remove_tripwires()

        self.assertEqual(reached, [], f"seam reached write path(s): {reached}")
        self.assertEqual(before, after, "seam mutated the filesystem — not read-only")


class CLIShim(_SeamFixture):
    """The thin `python -m` entrypoint ([LC-1]) — shells out to the same functions.

    Always exits 0 on the graceful-no-op paths so a process never wedges on a
    memory-absent seam.
    """

    def _run(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = seam.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_recall_here_subcommand_retired(self) -> None:
        # argparse rejects the unknown subcommand via parser.error() -> SystemExit(2).
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                seam.main(["recall-here", "--cwd", str(self.repo), "--phase", "work"])
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("invalid choice", err.getvalue())

    def test_state_path_emits_path_exit_zero(self) -> None:
        self._local_mode()
        rc, out, _ = self._run("state-path", "plan", "--cwd", str(self.repo))
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), str(self.harness / "PLAN.md"))

    def test_offer_save_here_present_emits_json(self) -> None:
        self._set_vault()
        body = self.root / "body.txt"
        body.write_text("a decision body", encoding="utf-8")
        with self._dl_select():
            rc, out, _ = self._run(
                "offer-save-here", "--cwd", str(self.repo),
                "--kind", "decision", "--slug", "x", "--body-file", str(body),
            )
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["project"], _SLUG)
        self.assertEqual(data[0]["body"], "a decision body")

    def test_offer_save_here_absent_emits_empty_json(self) -> None:
        body = self.root / "body.txt"
        body.write_text("b", encoding="utf-8")
        rc, out, _ = self._run(
            "offer-save-here", "--cwd", str(self.repo),
            "--kind", "decision", "--slug", "x", "--body-file", str(body),
        )
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), [])


# -----------------------------------------------------------------------------
# Task 2 — the check-one-way-imports.py back-edge rules via subprocess
#
# Was check-process-seam-import-direction.sh (a standalone bash script) —
# CONS-1 converged it onto Python, merging it into check-one-way-imports.py
# alongside the opinion/capability resolver rules. Same three invariants
# (process-seam back-edge, LC-8 storage_vault, LC-8 bridge), same fixture
# shapes; invoked with sys.executable instead of bash. The AST-based port
# also naturally handles the process_seam_helper false-positive case (an AST
# import node's module name is compared for exact equality, not a substring
# match), so the old regex's dedicated word-boundary class is no longer needed.
# -----------------------------------------------------------------------------

_IMPORT_GATE = _HERE / "check-one-way-imports.py"


def _run_import_gate(root: Path | None = None, rule: str | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(_IMPORT_GATE)]
    if root is not None:
        cmd += ["--root", str(root)]
    if rule is not None:
        cmd += ["--rule", rule]
    return subprocess.run(cmd, capture_output=True, text=True)


class ImportDirectionGate(unittest.TestCase):
    """The LC-4 one-way invariant — *memory never imports the process*.

    Subprocess tests against check-one-way-imports.py's back-edge rules
    (process-seam / lc8-storage-vault / lc8-bridge). The import verb is
    assembled at runtime so this file never carries the contiguous string
    the gate scans for (it's an excluded ``test_*.py`` regardless, but this
    keeps the fixtures unambiguous). Fixtures land under ``scripts/`` — one
    of the gate's scanned automation surfaces — pointed at via ``--root``.

    No longer POSIX-only (was bash-driven, skipped on Windows) — the merged
    gate is a `sys.executable` invocation, which works on every CI OS.
    """

    IMPORT = "import " + "process_seam"                  # the bare-import back-edge
    FROM = "from " + "process_seam import state_path"    # the from-import back-edge

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "scripts").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_gate_passes_on_live_repo(self) -> None:
        # The live repo's only seam importers are excluded by construction: its own
        # `test_*.py` suite and the module itself. So the real tree is one-way → rc 0.
        proc = _run_import_gate(rule="process-seam")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_gate_passes_on_clean_fixture(self) -> None:
        (self.root / "scripts" / "engine.py").write_text(
            "import harness_memory as hm\n", encoding="utf-8"
        )
        proc = _run_import_gate(self.root, rule="process-seam")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_gate_fails_on_engine_back_edge(self) -> None:
        # The mandatory negative test: a memory-engine module importing the seam.
        (self.root / "scripts" / "harness_memory.py").write_text(
            f"{self.IMPORT}\n", encoding="utf-8"
        )
        proc = _run_import_gate(self.root, rule="process-seam")
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("harness_memory.py", proc.stderr)

    def test_gate_fails_on_from_import_form(self) -> None:
        (self.root / "scripts" / "phase_runner.py").write_text(
            f"{self.FROM}\n", encoding="utf-8"
        )
        proc = _run_import_gate(self.root, rule="process-seam")
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("phase_runner.py", proc.stderr)

    def test_gate_ignores_test_files(self) -> None:
        # The seam's contract tests import it by design — not a back-edge.
        (self.root / "scripts" / "test_seam_thing.py").write_text(
            f"{self.IMPORT}\n", encoding="utf-8"
        )
        proc = _run_import_gate(self.root, rule="process-seam")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_gate_ignores_the_seam_module_itself(self) -> None:
        # process_seam.py is the module; it imports the engine, not itself. The
        # basename exclusion means a self-shaped match never trips the gate.
        (self.root / "scripts" / "process_seam.py").write_text(
            f"{self.IMPORT}\n", encoding="utf-8"
        )
        proc = _run_import_gate(self.root, rule="process-seam")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_false_positive_guard_process_seam_helper(self) -> None:
        # `process_seam_helper` is a different module — the AST module-name
        # equality check must not mistake it for a seam import.
        (self.root / "scripts" / "engine.py").write_text(
            f"{self.IMPORT}_helper\n", encoding="utf-8"
        )
        proc = _run_import_gate(self.root, rule="process-seam")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_gate_missing_root_is_setup_error(self) -> None:
        proc = _run_import_gate(self.root / "does-not-exist")
        self.assertEqual(proc.returncode, 2, proc.stdout)

    def test_lc8_fails_on_routing_file_import_form(self) -> None:
        # LC-8: harness_memory.py importing storage_vault (a capability plugin)
        # is a forbidden routing→plugin dependency.
        (self.root / "scripts" / "harness_memory.py").write_text(
            "import " + "storage_vault\n", encoding="utf-8"
        )
        proc = _run_import_gate(self.root, rule="lc8-storage-vault")
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("harness_memory.py", proc.stderr)

    def test_lc8_fails_on_routing_file_from_import_form(self) -> None:
        # LC-8: from-import form of the capability plugin in a routing file.
        (self.root / "scripts" / "repo_registry.py").write_text(
            "from " + "storage_vault import VaultBackend\n", encoding="utf-8"
        )
        proc = _run_import_gate(self.root, rule="lc8-storage-vault")
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("repo_registry.py", proc.stderr)

    def test_lc8_passes_on_non_routing_files(self) -> None:
        # A non-routing file importing storage_vault is fine (e.g. tests);
        # the LC-8 check is scoped to the routing mechanism files only.
        (self.root / "scripts" / "some_other_module.py").write_text(
            "import " + "storage_vault\n", encoding="utf-8"
        )
        proc = _run_import_gate(self.root, rule="lc8-storage-vault")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_bridge_lc8_fails_on_toolkit_back_edge(self) -> None:
        # V5-5 LC-8 bridge: a kernel toolkit script importing harness_memory is
        # a forbidden back-edge. The check scans harness/skills/memory/scripts/.
        toolkit_dir = self.root / "harness" / "skills" / "memory" / "scripts"
        toolkit_dir.mkdir(parents=True)
        (toolkit_dir / "auto_orchestration.py").write_text(
            "import " + "harness_memory\n", encoding="utf-8"
        )
        proc = _run_import_gate(self.root, rule="lc8-bridge")
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("harness_memory", proc.stderr)

    def test_bridge_lc8_fails_on_toolkit_from_import_form(self) -> None:
        # from-import form of harness_memory in a toolkit script is also forbidden.
        toolkit_dir = self.root / "harness" / "skills" / "memory" / "scripts"
        toolkit_dir.mkdir(parents=True)
        (toolkit_dir / "orchestration_phase.py").write_text(
            "from " + "harness_memory import phase_dispatch\n", encoding="utf-8"
        )
        proc = _run_import_gate(self.root, rule="lc8-bridge")
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("orchestration_phase.py", proc.stderr)

    def test_bridge_lc8_ignores_test_files_in_toolkit(self) -> None:
        # test_*.py files in the toolkit dir are excluded — they import the bridge
        # by design (that is what they test).
        toolkit_dir = self.root / "harness" / "skills" / "memory" / "scripts"
        toolkit_dir.mkdir(parents=True)
        (toolkit_dir / "test_auto_orchestration.py").write_text(
            "import " + "harness_memory\n", encoding="utf-8"
        )
        proc = _run_import_gate(self.root, rule="lc8-bridge")
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
