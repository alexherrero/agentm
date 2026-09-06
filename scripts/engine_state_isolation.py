#!/usr/bin/env python3
"""engine_state_isolation.py — one temporary engine state dir per test.

`engine_state.engine_state_dir()` answers `$AGENTM_STATE_DIR` if it is set
and `~/.local/state/agentm` if it is not. A test that writes engine state
without setting the variable therefore writes into the developer's own state
directory, and every later test in the same process reads what the last one
left there. The battery's runner isolates each test from outside, so the leak
is invisible under `check-all.sh` and appears only on a hand run — which is
how four `test_inbox_triage` tests came to fail for anyone running the file
directly while CI stayed green (PLAN-source-and-hygiene, task 3).

Two ways in. One line at the bottom of a module governs every test in it:

    from engine_state_isolation import isolate_module
    isolate_module(globals())

and a suite that wants the directory by name asks for it in `setUp`:

    def setUp(self):
        self.state = isolate_engine_state(self)

Both restore the environment afterwards, including the case where a variable
was not set before — so a suite run under a battery that sets the variable
from outside gets its own directory and hands the outer one back.

`isolate_module` wraps `TestCase.run` rather than injecting a `setUp`,
because a class whose own `setUp` forgets to call `super().setUp()` would
silently skip an injected one, and that is the failure this helper exists to
make impossible. Wrapping `run` covers `setUp`, the test, `tearDown` and
every registered cleanup.
"""
from __future__ import annotations

import contextlib
import os
import tempfile
import unittest
from pathlib import Path

# Every variable that moves engine state or cache off its default. Keep this
# list beside `engine_state.py`'s own resolution order: a variable that lands
# there and not here is a leak this helper silently fails to close.
GOVERNED = ("AGENTM_STATE_DIR", "XDG_CACHE_HOME")

_WRAPPED = "_agentm_engine_state_isolated"


def _apply(base: Path) -> dict:
    """Point the governed variables at `base`; return what they held.

    The directories are created, because a real deployment's are: a test that
    writes `<state>/<something>` with a plain `mkdir()` works against
    `~/.local/state/agentm` and would fail against a path whose parent does
    not exist yet. Isolation should change which directory a test writes to,
    not whether it has one."""
    previous = {name: os.environ.get(name) for name in GOVERNED}
    state, cache = base / "state", base / "cache"
    state.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    os.environ["AGENTM_STATE_DIR"] = str(state)
    os.environ["XDG_CACHE_HOME"] = str(cache)
    return previous


def _restore(previous: dict) -> None:
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@contextlib.contextmanager
def isolated_engine_state():
    """A fresh engine state and cache directory for the enclosed block."""
    with tempfile.TemporaryDirectory(prefix="agentm-engine-state-") as tmp:
        previous = _apply(Path(tmp))
        try:
            yield Path(tmp) / "state"
        finally:
            _restore(previous)


def isolate_engine_state(testcase: unittest.TestCase, *, root: "Path | str | None" = None) -> Path:
    """Point the engine's state and cache at a fresh directory for one test.

    Returns the state directory. `root` reuses a directory the test already
    owns (its own `TemporaryDirectory`, say) rather than making a second one;
    without it, a temporary directory is created and removed on cleanup.
    """
    if root is None:
        tmp = tempfile.TemporaryDirectory(prefix="agentm-engine-state-")
        testcase.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
    else:
        base = Path(root)
        base.mkdir(parents=True, exist_ok=True)
    previous = _apply(base)
    testcase.addCleanup(_restore, previous)
    return base / "state"


def isolate_class(cls: type) -> type:
    """Give every test in `cls` its own engine state. Idempotent."""
    if getattr(cls, _WRAPPED, False) and _WRAPPED in cls.__dict__:
        return cls
    inner = cls.run

    def run(self, result=None):
        with isolated_engine_state():
            return inner(self, result)

    cls.run = run
    setattr(cls, _WRAPPED, True)
    return cls


def isolate_module(namespace: dict) -> list:
    """Give every test in every `TestCase` the module defines its own engine
    state. Pass `globals()` from the bottom of the module, below the classes
    and above the `unittest.main()` guard. Returns the class names governed,
    so a suite can assert its own coverage."""
    governed = []
    for name, value in list(namespace.items()):
        if isinstance(value, type) and issubclass(value, unittest.TestCase):
            # Only classes this module defines. A `TestCase` imported from
            # elsewhere belongs to the module that defined it, and wrapping it
            # here would isolate it twice, or in a module that never asked.
            if value.__module__ != namespace.get("__name__"):
                continue
            isolate_class(value)
            governed.append(name)
    return sorted(governed)
