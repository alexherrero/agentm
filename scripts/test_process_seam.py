#!/usr/bin/env python3
"""Contract tests for `process_seam` (V5-4 task 1) — the memory↔process client seam.

`process_seam` is the small, **read-only**, **graceful-no-op** view a *process*
(the crickets developer-workflows phases today; the V5-9 MCP server tomorrow)
calls instead of reaching into the memory engine. These tests pin its contract:

  - `recall_here`     — phase+project recall string; ``""`` when memory is absent;
                        an unknown phase degrades to the "work" default, never raises;
                        the reserved ``query`` arg is a documented no-op.
  - `offer_save_here` — advisory-only ([LC-2]): returns enriched save *candidates*
                        and **never persists**. Proven by the no-writes assertion.
  - `state_path`      — vault-backed when memory is present; **degrades to repo-local
                        ``<project_root>/.harness/`` ([LC-3])**, never ``None``; but a
                        corrupt/dangling ``active-plan`` marker **propagates loudly**
                        (V5-10 Risk #7) rather than degrading.

The load-bearing test is `SeamIsReadOnly`: every engine write entry point is
monkeypatched to raise if called, and a filesystem content snapshot is asserted
byte-identical before/after exercising all three functions — making "the seam is
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


class RecallHere(_SeamFixture):
    """`recall_here` — recall string, absent degrade, phase guard, query no-op."""

    def test_absent_vault_returns_empty_string(self) -> None:
        self.assertEqual(seam.recall_here(self._ctx(phase="work")), "")

    def test_present_vault_returns_empty_v5_3(self) -> None:
        # V5-3: phase_recall always returns ""; vault data in context is handled
        # by the V5-9 MCP memory server, not by the kernel's recall path.
        self._set_vault()
        self._seed_always_load()
        out = seam.recall_here(self._ctx(phase="work"))
        self.assertEqual(out, "")

    def test_unknown_phase_degrades_to_work_never_raises_v5_3(self) -> None:
        # An unknown phase must not raise — it should degrade gracefully to "".
        # V5-3: both known and unknown phases return "" (vault backend removed).
        self._set_vault()
        self._seed_always_load()
        work = seam.recall_here(self._ctx(phase="work"))
        bogus = seam.recall_here(self._ctx(phase="not-a-real-phase"))
        self.assertEqual(work, "")
        self.assertEqual(bogus, "")

    def test_missing_phase_defaults_to_work_v5_3(self) -> None:
        # V5-3: all modes return ""; phase defaulting to "work" still works.
        self._set_vault()
        self._seed_always_load()
        out = seam.recall_here(self._ctx())  # context carries no phase
        self.assertEqual(out, "")

    def test_reserved_query_is_a_noop(self) -> None:
        # `query` is reserved/forward-compat — it must neither filter nor error.
        self._set_vault()
        self._seed_always_load()
        without = seam.recall_here(self._ctx(phase="work"))
        with_query = seam.recall_here(
            self._ctx(phase="work"), query="some semantic search terms"
        )
        self.assertEqual(with_query, without)

    def test_none_context_does_not_raise(self) -> None:
        # No context → process cwd; memory absent → "". Never raises on None.
        self.assertEqual(seam.recall_here(None), "")


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
        self._set_vault()
        cand = {"kind": "decision", "slug": "x", "body": "b"}
        out = seam.offer_save_here(self._ctx(), cand)
        self.assertEqual(len(out), 1)
        enriched = out[0]
        self.assertEqual(enriched["project"], _SLUG)
        self.assertEqual(enriched["kind"], "decision")  # original keys preserved
        self.assertEqual(enriched["body"], "b")
        self.assertIsNotNone(enriched["target"])
        self.assertIn(_SLUG, enriched["target"])  # resolved vault target

    def test_phase_passed_through_onto_candidate(self) -> None:
        self._set_vault()
        out = seam.offer_save_here(self._ctx(phase="review"), {"body": "b"})
        self.assertEqual(out[0]["phase"], "review")

    def test_non_dict_candidate_wrapped_as_body(self) -> None:
        self._set_vault()
        out = seam.offer_save_here(self._ctx(), "free-text candidate")
        self.assertEqual(out[0]["body"], "free-text candidate")
        self.assertEqual(out[0]["project"], _SLUG)

    def test_caller_candidate_not_mutated(self) -> None:
        # Enrichment copies — the caller's dict is never mutated in place.
        self._set_vault()
        cand = {"kind": "decision", "slug": "x", "body": "b"}
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

    def test_vault_mode_resolves_device_local_v5_3(self) -> None:
        # V5-3: vault mode no longer routes state to the vault — always device-local.
        self._set_vault()
        self.assertEqual(
            seam.state_path(self._ctx(), "plan"), self.harness / "PLAN.md"
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
            seam.recall_here(self._ctx(phase="work"))
            seam.offer_save_here(
                self._ctx(phase="work"), {"kind": "decision", "slug": "x", "body": "b"}
            )
            seam.state_path(self._ctx(), "plan")
            seam.state_path(self._ctx(), "progress")

            # Absent memory — the degrade paths.
            self._unset_vault()
            seam.recall_here(self._ctx(phase="work"))
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

    def test_recall_here_absent_exits_zero_empty(self) -> None:
        rc, out, _ = self._run("recall-here", "--cwd", str(self.repo), "--phase", "work")
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")

    def test_recall_here_present_exits_zero_empty_v5_3(self) -> None:
        # V5-3: phase_recall always returns ""; vault context via V5-9 MCP server.
        self._set_vault()
        self._seed_always_load()
        rc, out, _ = self._run("recall-here", "--cwd", str(self.repo), "--phase", "work")
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")

    def test_state_path_emits_path_exit_zero(self) -> None:
        self._local_mode()
        rc, out, _ = self._run("state-path", "plan", "--cwd", str(self.repo))
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), str(self.harness / "PLAN.md"))

    def test_offer_save_here_present_emits_json(self) -> None:
        self._set_vault()
        body = self.root / "body.txt"
        body.write_text("a decision body", encoding="utf-8")
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
# Task 2 — the check-process-seam-import-direction.sh gate via subprocess (POSIX)
# -----------------------------------------------------------------------------

_IMPORT_GATE = _HERE / "check-process-seam-import-direction.sh"


def _run_import_gate(root: Path | None = None) -> subprocess.CompletedProcess:
    cmd = ["bash", str(_IMPORT_GATE)]
    if root is not None:
        cmd += ["--root", str(root)]
    return subprocess.run(cmd, capture_output=True, text=True)


@unittest.skipIf(os.name == "nt", "bash gate — POSIX only")
class ImportDirectionGate(unittest.TestCase):
    """The LC-4 one-way invariant — *memory never imports the process*.

    Subprocess tests against `check-process-seam-import-direction.sh`. The import
    verb is assembled at runtime so this file never carries the contiguous string
    the gate greps for (it's an excluded ``test_*.py`` regardless, but this keeps
    the fixtures unambiguous). Fixtures land under ``scripts/`` — one of the gate's
    scanned automation surfaces — pointed at via ``--root``.
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
        proc = _run_import_gate()
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_gate_passes_on_clean_fixture(self) -> None:
        (self.root / "scripts" / "engine.py").write_text(
            "import harness_memory as hm\n", encoding="utf-8"
        )
        proc = _run_import_gate(self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_gate_fails_on_engine_back_edge(self) -> None:
        # The mandatory negative test: a memory-engine module importing the seam.
        (self.root / "scripts" / "harness_memory.py").write_text(
            f"{self.IMPORT}\n", encoding="utf-8"
        )
        proc = _run_import_gate(self.root)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("harness_memory.py", proc.stderr)

    def test_gate_fails_on_from_import_form(self) -> None:
        (self.root / "scripts" / "phase_runner.py").write_text(
            f"{self.FROM}\n", encoding="utf-8"
        )
        proc = _run_import_gate(self.root)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("phase_runner.py", proc.stderr)

    def test_gate_ignores_test_files(self) -> None:
        # The seam's contract tests import it by design — not a back-edge.
        (self.root / "scripts" / "test_seam_thing.py").write_text(
            f"{self.IMPORT}\n", encoding="utf-8"
        )
        proc = _run_import_gate(self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_gate_ignores_the_seam_module_itself(self) -> None:
        # process_seam.py is the module; it imports the engine, not itself. The
        # basename exclusion means a self-shaped match never trips the gate.
        (self.root / "scripts" / "process_seam.py").write_text(
            f"{self.IMPORT}\n", encoding="utf-8"
        )
        proc = _run_import_gate(self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_false_positive_guard_process_seam_helper(self) -> None:
        # `process_seam_helper` is a different module — the regex's trailing
        # word-boundary class must not mistake it for a seam import.
        (self.root / "scripts" / "engine.py").write_text(
            f"{self.IMPORT}_helper\n", encoding="utf-8"
        )
        proc = _run_import_gate(self.root)
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
        proc = _run_import_gate(self.root)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("harness_memory.py", proc.stderr)

    def test_lc8_fails_on_routing_file_from_import_form(self) -> None:
        # LC-8: from-import form of the capability plugin in a routing file.
        (self.root / "scripts" / "repo_registry.py").write_text(
            "from " + "storage_vault import VaultBackend\n", encoding="utf-8"
        )
        proc = _run_import_gate(self.root)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("repo_registry.py", proc.stderr)

    def test_lc8_passes_on_non_routing_files(self) -> None:
        # A non-routing file importing storage_vault is fine (e.g. tests);
        # the LC-8 check is scoped to the routing mechanism files only.
        (self.root / "scripts" / "some_other_module.py").write_text(
            "import " + "storage_vault\n", encoding="utf-8"
        )
        proc = _run_import_gate(self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
