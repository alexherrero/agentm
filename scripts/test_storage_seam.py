#!/usr/bin/env python3
"""Contract tests for `storage_seam` (V5-1 part 1/5) — the memory↔storage seam.

`storage_seam` is the interface the memory engine will call instead of touching
files directly. These tests pin its *contract*, three ways:

  - **Per-verb behavior against an in-memory fixture backend** (`_MemoryBackend`).
    That the seven verbs are fully implementable over a plain dict — no
    filesystem in sight — is the proof the interface carries no FS assumption.
    The fixture is deliberately minimal here; part 3 promotes it into the shared
    conformance suite every real backend runs against.
  - **The Locator type** — normalization, root-confinement (no ``..`` escape),
    and the namespace operations (`child`/`name`/`parts`) that make it usable
    *without* being a ``pathlib.Path``.
  - **The named-backend registry** — two protocol names register independently
    and resolve to distinct backends; an unregistered name resolves *as absent*
    (``get`` → ``None``, never a raise) — the signal part 5's fail-loud guard
    reads — while bad registrations (empty name, silent duplicate, a non-backend)
    fail loud. Every case registers into a *fresh* ``BackendRegistry()`` so
    nothing leaks across tests or into the module-default ``ss.registry``.

`NoPathCrossesSeam` is the behavioral mirror of the static
`check-storage-seam-no-path-leak` gate: it asserts every verb actually returns
the seam's own types, never a ``Path``. `PathLeakGate` then exercises the gate
script itself (clean repo passes; a Path-returning fixture verb fails; internal
Path use is ignored) — the same gate-meta-test shape as `test_process_seam`'s
`ImportDirectionGate`.

Run directly:

    python3 scripts/test_storage_seam.py
"""
from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import storage_seam as ss  # noqa: E402


class _MemoryBackend(ss.StorageBackend):
    """A dict-backed backend — the contract proven with no filesystem at all.

    ``mtime`` advances off an internal monotonic counter (not a wall clock) so
    "a re-write bumps mtime" is deterministically testable — the affordance
    ``changed-since`` reads in task 3.
    """

    def __init__(self) -> None:
        self._files: dict[str, str] = {}
        self._dirs: set[str] = {""}  # root always exists
        self._mtimes: dict[str, float] = {}
        self._clock = 0.0

    def _tick(self) -> float:
        self._clock += 1.0
        return self._clock

    def _register_ancestors(self, locator: ss.Locator) -> None:
        parts = locator.parts
        for i in range(1, len(parts)):
            self._dirs.add("/".join(parts[:i]))

    @property
    def capabilities(self) -> ss.Capabilities:
        return ss.Capabilities(concurrent_writers=True)

    def resolve(self, *parts: str) -> ss.Locator:
        return ss.Locator("/".join(str(p) for p in parts))

    def read(self, locator: ss.Locator) -> str:
        try:
            return self._files[locator.key]
        except KeyError:
            raise FileNotFoundError(locator.key)

    def write(self, locator: ss.Locator, content: str) -> ss.Locator:
        self._files[locator.key] = content
        self._mtimes[locator.key] = self._tick()
        self._register_ancestors(locator)
        return locator

    def list(self, locator: ss.Locator) -> list[ss.Locator]:
        prefix = locator.key + "/" if locator.key else ""
        children: set[str] = set()
        for k in (*self._files, *self._dirs):
            if not k or k == locator.key or not k.startswith(prefix):
                continue
            children.add(prefix + k[len(prefix):].split("/")[0])
        return [ss.Locator(c) for c in sorted(children)]

    def exists(self, locator: ss.Locator) -> bool:
        return locator.key in self._files or locator.key in self._dirs

    def info(self, locator: ss.Locator) -> ss.Info:
        k = locator.key
        if k in self._files:
            content = self._files[k]
            return ss.Info(
                locator=locator,
                is_dir=False,
                size=len(content.encode("utf-8")),
                mtime=self._mtimes[k],
            )
        if k in self._dirs:
            return ss.Info(locator=locator, is_dir=True, size=0, mtime=self._mtimes.get(k, 0.0))
        raise FileNotFoundError(k)

    def mkdir(self, locator: ss.Locator) -> ss.Locator:
        self._dirs.add(locator.key)
        self._mtimes.setdefault(locator.key, self._tick())
        self._register_ancestors(locator)
        return locator


class LocatorType(unittest.TestCase):
    """The seam's own locator — opaque, normalized, root-confined; not a Path."""

    def test_root_is_empty_key(self) -> None:
        self.assertEqual(ss.Locator().key, "")
        self.assertEqual(ss.Locator("").parts, ())
        self.assertEqual(ss.Locator().name, "")

    def test_normalization_drops_noise_segments(self) -> None:
        self.assertEqual(ss.Locator("a//b/./c/").key, "a/b/c")
        self.assertEqual(ss.normalize_key("/leading/slash"), "leading/slash")

    def test_parts_and_name(self) -> None:
        loc = ss.Locator("projects/agentm/_harness/PLAN.md")
        self.assertEqual(loc.parts, ("projects", "agentm", "_harness", "PLAN.md"))
        self.assertEqual(loc.name, "PLAN.md")

    def test_child_appends_and_normalizes(self) -> None:
        self.assertEqual(ss.Locator("a").child("b", "c").key, "a/b/c")
        self.assertEqual(ss.Locator().child("x").key, "x")  # leading slash relativized

    def test_double_dot_is_rejected_everywhere(self) -> None:
        # The safety property: a locator can never traverse upward out of its root.
        with self.assertRaises(ss.InvalidLocatorError):
            ss.Locator("a/../b")
        with self.assertRaises(ss.InvalidLocatorError):
            ss.normalize_key("..")
        with self.assertRaises(ss.InvalidLocatorError):
            ss.Locator("a").child("..")

    def test_invalid_locator_error_is_a_valueerror(self) -> None:
        # A caller bug — kept in the ValueError family, distinct from absent-data.
        self.assertTrue(issubclass(ss.InvalidLocatorError, ValueError))

    def test_locator_is_hashable_and_frozen(self) -> None:
        loc = ss.Locator("a/b")
        self.assertEqual({loc: 1}[ss.Locator("a/b")], 1)  # hashable + value-equal
        with self.assertRaises(Exception):
            loc.key = "mutated"  # frozen


class SeamVerbs(unittest.TestCase):
    """Each of the seven verbs, against the in-memory fixture backend."""

    def setUp(self) -> None:
        self.b = _MemoryBackend()

    def test_resolve_makes_a_locator_from_parts(self) -> None:
        loc = self.b.resolve("projects", "agentm", "PLAN.md")
        self.assertIsInstance(loc, ss.Locator)
        self.assertEqual(loc.key, "projects/agentm/PLAN.md")
        self.assertEqual(self.b.resolve().key, "")  # no parts → root

    def test_write_then_read_round_trips(self) -> None:
        loc = self.b.resolve("notes", "a.md")
        written = self.b.write(loc, "# hello\n")
        self.assertIsInstance(written, ss.Locator)
        self.assertEqual(written, loc)  # write returns the locator written
        self.assertEqual(self.b.read(loc), "# hello\n")

    def test_read_missing_raises_filenotfound(self) -> None:
        # The absent-data signal — distinct from InvalidLocatorError (a bad key).
        with self.assertRaises(FileNotFoundError):
            self.b.read(self.b.resolve("nope.md"))

    def test_exists_tracks_writes(self) -> None:
        loc = self.b.resolve("x.md")
        self.assertFalse(self.b.exists(loc))
        self.b.write(loc, "data")
        self.assertTrue(self.b.exists(loc))

    def test_list_returns_immediate_children(self) -> None:
        self.b.write(self.b.resolve("d", "a.md"), "1")
        self.b.write(self.b.resolve("d", "b.md"), "2")
        self.b.write(self.b.resolve("e.md"), "3")
        root_children = self.b.list(self.b.resolve())
        self.assertEqual({c.key for c in root_children}, {"d", "e.md"})
        d_children = self.b.list(self.b.resolve("d"))
        self.assertEqual({c.key for c in d_children}, {"d/a.md", "d/b.md"})
        self.assertTrue(all(isinstance(c, ss.Locator) for c in d_children))

    def test_info_reports_size_and_kind(self) -> None:
        loc = self.b.resolve("a.md")
        self.b.write(loc, "abcde")
        info = self.b.info(loc)
        self.assertIsInstance(info, ss.Info)
        self.assertFalse(info.is_dir)
        self.assertEqual(info.size, 5)
        self.assertEqual(info.locator, loc)

    def test_info_mtime_advances_on_rewrite(self) -> None:
        # The changed-since basis (task 3): a later write yields a later mtime.
        loc = self.b.resolve("a.md")
        self.b.write(loc, "v1")
        first = self.b.info(loc).mtime
        self.b.write(loc, "v2")
        second = self.b.info(loc).mtime
        self.assertGreater(second, first)

    def test_info_missing_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.b.info(self.b.resolve("ghost"))

    def test_mkdir_is_idempotent_and_marks_a_dir(self) -> None:
        loc = self.b.resolve("newdir")
        made = self.b.mkdir(loc)
        self.assertIsInstance(made, ss.Locator)
        self.assertTrue(self.b.exists(loc))
        self.assertTrue(self.b.info(loc).is_dir)
        self.assertEqual(self.b.mkdir(loc), loc)  # idempotent — no raise


class NoPathCrossesSeam(unittest.TestCase):
    """Behavioral mirror of the static gate: verbs return the seam's types, never a Path."""

    def setUp(self) -> None:
        self.b = _MemoryBackend()
        self.loc = self.b.resolve("d", "a.md")
        self.b.write(self.loc, "x")
        self.b.mkdir(self.b.resolve("d"))

    def test_no_verb_returns_a_path(self) -> None:
        results = [
            self.b.resolve("a"),
            self.b.read(self.loc),
            self.b.write(self.loc, "y"),
            self.b.list(self.b.resolve("d")),
            self.b.exists(self.loc),
            self.b.info(self.loc),
            self.b.mkdir(self.b.resolve("d")),
        ]
        for r in results:
            self.assertNotIsInstance(r, Path)

    def test_verbs_return_the_seam_types(self) -> None:
        self.assertIsInstance(self.b.resolve("a"), ss.Locator)
        self.assertIsInstance(self.b.read(self.loc), str)
        self.assertIsInstance(self.b.write(self.loc, "y"), ss.Locator)
        listing = self.b.list(self.b.resolve("d"))
        self.assertIsInstance(listing, list)
        self.assertTrue(all(isinstance(c, ss.Locator) for c in listing))
        self.assertIsInstance(self.b.exists(self.loc), bool)
        self.assertIsInstance(self.b.info(self.loc), ss.Info)
        self.assertIsInstance(self.b.mkdir(self.b.resolve("d")), ss.Locator)


class Descriptors(unittest.TestCase):
    """The Info record and the four-boolean Capabilities descriptor."""

    def test_capabilities_default_to_conservative_floor(self) -> None:
        caps = ss.Capabilities()
        self.assertFalse(caps.concurrent_writers)
        self.assertFalse(caps.conflict_files)
        self.assertFalse(caps.encryption)
        self.assertFalse(caps.sync)

    def test_capabilities_fields_are_settable(self) -> None:
        caps = ss.Capabilities(concurrent_writers=True, sync=True)
        self.assertTrue(caps.concurrent_writers)
        self.assertTrue(caps.sync)
        self.assertFalse(caps.encryption)

    def test_backend_declares_capabilities(self) -> None:
        self.assertIsInstance(_MemoryBackend().capabilities, ss.Capabilities)

    def test_info_carries_mtime(self) -> None:
        info = ss.Info(locator=ss.Locator("a"), is_dir=False, size=3, mtime=12.0)
        self.assertEqual(info.mtime, 12.0)


class _BackendA(_MemoryBackend):
    """A distinct concrete backend — the registry resolves a protocol to *this* class."""


class _BackendB(_MemoryBackend):
    """A second distinct concrete backend — proves two protocols resolve apart."""


class BackendRegistryContract(unittest.TestCase):
    """The hand-rolled name→backend registry — register, resolve-as-absent, fail-loud inputs.

    Every test registers into a *fresh* ``BackendRegistry()``; nothing leaks
    across cases or into the process-wide default ``ss.registry`` (asserted
    present, never mutated). The registry stores backend *classes*, not
    instances — selection (part 5) instantiates the chosen one.
    """

    def setUp(self) -> None:
        self.reg = ss.BackendRegistry()

    def test_two_protocols_register_and_resolve_to_distinct_backends(self) -> None:
        # The core task-2 verification: two names, two backends, kept apart.
        self.reg.register("device-local", _BackendA)
        self.reg.register("vault", _BackendB)
        self.assertIs(self.reg.get("device-local"), _BackendA)
        self.assertIs(self.reg.get("vault"), _BackendB)
        self.assertIsNot(self.reg.get("device-local"), self.reg.get("vault"))

    def test_unregistered_name_resolves_as_absent(self) -> None:
        # The signal part 5's fail-loud guard reads: a miss is None, never a raise.
        self.assertIsNone(self.reg.get("nope"))

    def test_contains_reflects_registration(self) -> None:
        self.assertNotIn("device-local", self.reg)
        self.reg.register("device-local", _BackendA)
        self.assertIn("device-local", self.reg)

    def test_protocols_lists_registered_names_sorted(self) -> None:
        self.reg.register("vault", _BackendB)
        self.reg.register("device-local", _BackendA)
        self.assertEqual(self.reg.protocols(), ("device-local", "vault"))

    def test_duplicate_registration_raises(self) -> None:
        # A silent shadow is a footgun — a duplicate without clobber fails loud.
        self.reg.register("device-local", _BackendA)
        with self.assertRaises(ss.ProtocolError):
            self.reg.register("device-local", _BackendB)

    def test_clobber_allows_intentional_override(self) -> None:
        self.reg.register("device-local", _BackendA)
        self.reg.register("device-local", _BackendB, clobber=True)
        self.assertIs(self.reg.get("device-local"), _BackendB)

    def test_empty_protocol_name_is_rejected(self) -> None:
        with self.assertRaises(ss.ProtocolError):
            self.reg.register("", _BackendA)

    def test_register_rejects_non_backend(self) -> None:
        # Registering a non-StorageBackend is a programming error, surfaced loudly:
        # an unrelated class, the abstract base itself, and an instance (not a class).
        with self.assertRaises(TypeError):
            self.reg.register("bad", object)  # not a StorageBackend subclass
        with self.assertRaises(TypeError):
            self.reg.register("abstract", ss.StorageBackend)  # the ABC — nothing to instantiate
        with self.assertRaises(TypeError):
            self.reg.register("instance", _BackendA())  # an instance, not the class

    def test_protocol_error_is_a_valueerror(self) -> None:
        # A bad registration stays in the ValueError family, distinct from a miss.
        self.assertTrue(issubclass(ss.ProtocolError, ValueError))

    def test_module_default_registry_exists(self) -> None:
        # The process-wide default the real backends (parts 2/4) register into;
        # asserted without mutating it, so the contract suite stays hermetic.
        self.assertIsInstance(ss.registry, ss.BackendRegistry)


class ThreeTierContract(unittest.TestCase):
    """The source/derived tier taxonomy — three tiers, the local index never-sync."""

    def test_exactly_three_tiers_named(self) -> None:
        self.assertEqual(
            {t.value for t in ss.Tier},
            {"source", "shared-abstracts", "local-index"},
        )

    def test_local_index_is_the_only_never_sync_tier(self) -> None:
        # The one hard line: the local index never syncs; the other two may.
        self.assertFalse(ss.Tier.LOCAL_INDEX.syncs)
        self.assertTrue(ss.Tier.SOURCE.syncs)
        self.assertTrue(ss.Tier.SHARED_ABSTRACTS.syncs)

    def test_source_is_authoritative_the_others_derived(self) -> None:
        # SOURCE is the truth reindex rebuilds from; both other tiers are derived.
        self.assertFalse(ss.Tier.SOURCE.derived)
        self.assertTrue(ss.Tier.SHARED_ABSTRACTS.derived)
        self.assertTrue(ss.Tier.LOCAL_INDEX.derived)

    def test_layout_roots_are_distinct(self) -> None:
        layout = ss.TierLayout()
        keys = {layout.source.key, layout.shared_abstracts.key, layout.local_index.key}
        self.assertEqual(len(keys), 3)

    def test_layout_rejects_colliding_roots(self) -> None:
        # source + shared-abstracts placement must stay distinct from local-index —
        # else a derived tier could overwrite the source it rebuilds from.
        with self.assertRaises(ValueError):
            ss.TierLayout(source=ss.Locator("x"), local_index=ss.Locator("x"))

    def test_never_sync_root_is_the_local_index(self) -> None:
        layout = ss.TierLayout()
        self.assertEqual(layout.never_sync_root, layout.local_index)

    def test_root_for_maps_each_tier(self) -> None:
        layout = ss.TierLayout()
        self.assertEqual(layout.root_for(ss.Tier.SOURCE), layout.source)
        self.assertEqual(layout.root_for(ss.Tier.SHARED_ABSTRACTS), layout.shared_abstracts)
        self.assertEqual(layout.root_for(ss.Tier.LOCAL_INDEX), layout.local_index)

    def test_reindex_and_changed_since_are_named(self) -> None:
        # The two derived-tier ops are reserved on the contract — named for V6.
        self.assertTrue(hasattr(ss.DerivedMaintenance, "reindex"))
        self.assertTrue(hasattr(ss.DerivedMaintenance, "changed_since"))


class NoIndexBuilt(unittest.TestCase):
    """Scope guard: this part *names* reindex/changed-since but builds no index.

    Two structural assertions: the derived-maintenance ops are abstract (so the
    contract ships no implementation — "named, not built" is enforced, not just
    documented), and the contract module imports no database / index / framework
    library (the "import neither fsspec nor any DB" constraint, made executable).
    The import check is AST-based on purpose: the module's own docstrings mention
    "SQLite" and "vector index", so a substring scan would false-positive — only
    a real ``import`` statement counts.
    """

    # DB / index / framework modules the contract must not import — the floor is
    # bare markdown (no fsspec, no SQL engine, no vector store).
    _FORBIDDEN_IMPORTS = frozenset(
        {
            "fsspec",
            "sqlite3",
            "pysqlite3",
            "sqlalchemy",
            "duckdb",
            "chromadb",
            "lancedb",
            "faiss",
            "annoy",
            "hnswlib",
            "numpy",
            "pandas",
        }
    )

    def test_derived_maintenance_is_abstract(self) -> None:
        # Cannot instantiate — the structural proof that no implementation ships.
        with self.assertRaises(TypeError):
            ss.DerivedMaintenance()

    def test_reindex_and_changed_since_are_abstractmethods(self) -> None:
        self.assertEqual(
            ss.DerivedMaintenance.__abstractmethods__,
            frozenset({"reindex", "changed_since"}),
        )

    def test_contract_module_ships_no_concrete_derived_maintenance(self) -> None:
        # No reindex/changed-since implementation in the contract module itself.
        concrete = [
            v
            for v in vars(ss).values()
            if isinstance(v, type)
            and issubclass(v, ss.DerivedMaintenance)
            and v is not ss.DerivedMaintenance
        ]
        self.assertEqual(concrete, [], f"unexpected concrete subclass(es): {concrete}")

    def test_contract_module_imports_no_db_or_index_library(self) -> None:
        src = Path(ss.__file__).read_text(encoding="utf-8")
        imported: set[str] = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        leaked = imported & self._FORBIDDEN_IMPORTS
        self.assertEqual(leaked, set(), f"contract module imports forbidden lib(s): {leaked}")


# -----------------------------------------------------------------------------
# The check-storage-seam-no-path-leak.py gate, via subprocess.
# -----------------------------------------------------------------------------

_GATE = _HERE / "check-storage-seam-no-path-leak.py"


def _run_gate(root: Path | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(_GATE)]
    if root is not None:
        cmd += ["--root", str(root)]
    return subprocess.run(cmd, capture_output=True, text=True)


class PathLeakGate(unittest.TestCase):
    """The static enforcement — a verb's return annotation must never be a Path.

    Pure-Python AST gate, so these run on every OS (no bash skip). Fixtures land
    under ``<root>/scripts/storage_*.py`` — the gate's scanned glob — pointed at
    via ``--root``.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "scripts").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, name: str, src: str) -> None:
        (self.root / "scripts" / name).write_text(src, encoding="utf-8")

    def test_gate_passes_on_live_repo(self) -> None:
        # The real scripts/storage_seam.py returns only Locator/Info/str/bool —
        # so the live tree is clean. Doubles as an always-on contract regression.
        proc = _run_gate()
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_gate_passes_on_clean_fixture(self) -> None:
        self._write(
            "storage_clean.py",
            "class B:\n"
            "    def resolve(self, *parts) -> 'Locator': ...\n"
            "    def read(self, loc) -> str: ...\n",
        )
        proc = _run_gate(self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_gate_ignores_internal_path_use(self) -> None:
        # The robustness claim a line-grep can't make: Path *inside* a verb whose
        # return is a non-path type is fine — only the return annotation matters.
        self._write(
            "storage_fsish.py",
            "from pathlib import Path\n"
            "class B:\n"
            "    def read(self, loc) -> str:\n"
            "        p = Path('root') / 'key'\n"
            "        return p.read_text()\n",
        )
        proc = _run_gate(self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_gate_fails_on_bare_path_return(self) -> None:
        self._write(
            "storage_evil.py",
            "from pathlib import Path\n"
            "class B:\n"
            "    def read(self, loc) -> Path: ...\n",
        )
        proc = _run_gate(self.root)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("storage_evil.py", proc.stderr)
        self.assertIn("read", proc.stderr)

    def test_gate_fails_on_nested_path_return(self) -> None:
        # list[Path] — nesting a line-grep for `-> Path` would miss.
        self._write(
            "storage_nested.py",
            "from pathlib import Path\n"
            "class B:\n"
            "    def list(self, loc) -> list[Path]: ...\n",
        )
        proc = _run_gate(self.root)
        self.assertEqual(proc.returncode, 1, proc.stdout)

    def test_gate_fails_on_union_path_return(self) -> None:
        self._write(
            "storage_union.py",
            "from pathlib import Path\n"
            "class B:\n"
            "    def info(self, loc) -> Path | None: ...\n",
        )
        proc = _run_gate(self.root)
        self.assertEqual(proc.returncode, 1, proc.stdout)

    def test_gate_fails_on_qualified_pathlib(self) -> None:
        self._write(
            "storage_qualified.py",
            "import pathlib\n"
            "class B:\n"
            "    def write(self, loc, c) -> pathlib.Path: ...\n",
        )
        proc = _run_gate(self.root)
        self.assertEqual(proc.returncode, 1, proc.stdout)

    def test_gate_ignores_non_storage_files(self) -> None:
        # The glob scopes scanning to the seam surface; an engine module with a
        # Path-returning helper named `read` is not the seam and not scanned.
        self._write(
            "engine.py",
            "from pathlib import Path\n"
            "class B:\n"
            "    def read(self, loc) -> Path: ...\n",
        )
        proc = _run_gate(self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_gate_missing_root_is_setup_error(self) -> None:
        proc = _run_gate(self.root / "does-not-exist")
        self.assertEqual(proc.returncode, 2, proc.stdout)


if __name__ == "__main__":
    unittest.main()
