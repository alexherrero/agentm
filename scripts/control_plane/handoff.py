#!/usr/bin/env python3
"""handoff.py — wires the `/handoff-pack` machine-readable tier/model label
into control-plane dispatch (PLAN-autonomy-control-plane task 4).

Every `dispatch.DispatchResult` already carries `{tier, model_id, effort}`
(task 2's `resolve_dispatch_classification()`). This module converts a
batch of dispatched results into crickets' `HandoffEntry` objects and,
optionally, a real handoff pack on disk — so a downstream consumer (a
future `/work` escalation tripwire, or this arc's own morning report)
reads the SAME structured label crickets' `handoff_pack.py` already
established (`LABEL_SCHEMA_KEYS = ("tier", "model_id", "effort")`), never a
bespoke parallel format this plan invents on its own.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_CRICKETS_HANDOFF_REL = Path("src") / "tokens" / "scripts"

_handoff_module = None
_handoff_loaded = False


def _candidate_handoff_dirs() -> list[Path]:
    candidates = []
    env_dir = os.environ.get("CRICKETS_SCRIPTS_DIR", "").strip()
    if env_dir:
        candidates.append(Path(os.path.expanduser(env_dir)))
    candidates.append(Path.home() / "Antigravity" / "crickets" / _CRICKETS_HANDOFF_REL)
    return candidates


def _find_handoff_dir() -> "Path | None":
    for candidate in _candidate_handoff_dirs():
        if (candidate / "handoff_pack.py").is_file():
            return candidate
    return None


def load_handoff_pack_module():
    """Return crickets' handoff_pack module, loaded once and cached. None
    if crickets is unresolvable (graceful-return; callers degrade to
    skipping the handoff pack, never to inventing their own label shape)."""
    global _handoff_module, _handoff_loaded
    if _handoff_loaded:
        return _handoff_module
    _handoff_loaded = True
    d = _find_handoff_dir()
    if d is None:
        _handoff_module = None
        return None
    spec = importlib.util.spec_from_file_location("crickets_handoff_pack_bridge", d / "handoff_pack.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["crickets_handoff_pack_bridge"] = module
    spec.loader.exec_module(module)
    _handoff_module = module
    return module


def _reset_cache_for_tests() -> None:
    global _handoff_module, _handoff_loaded
    _handoff_module = None
    _handoff_loaded = False


def dispatch_result_label(result) -> dict:
    """The machine-readable `{tier, model_id, effort}` label for a dispatched
    result — matches crickets' `handoff_pack.LABEL_SCHEMA_KEYS` exactly, by
    construction (not by convention two modules could drift apart on)."""
    return {"tier": result.tier, "model_id": result.model_id, "effort": result.effort}


def dispatch_result_to_handoff_entry(result, *, prompt_text: str = ""):
    """Convert one `DispatchResult` into crickets' `HandoffEntry`. Returns
    `None` if crickets is unresolvable."""
    module = load_handoff_pack_module()
    if module is None:
        return None
    return module.HandoffEntry(
        title=result.name, prompt_text=prompt_text,
        tier=result.tier, model_id=result.model_id, effort=result.effort,
    )


def build_fleet_handoff_pack(results: list, session_outputs: dict, dest_dir: "str | Path") -> "dict | None":
    """Build a real handoff pack (via crickets' `build_handoff_pack`) for a
    batch of dispatched fleet `results`. Returns the written manifest dict,
    or `None` if crickets is unresolvable (graceful-skip, not an error --
    a missing handoff pack must never block a fleet run)."""
    module = load_handoff_pack_module()
    if module is None:
        return None
    entries = [dispatch_result_to_handoff_entry(r) for r in results]
    entries = [e for e in entries if e is not None]
    return module.build_handoff_pack(entries, session_outputs, Path(dest_dir))
