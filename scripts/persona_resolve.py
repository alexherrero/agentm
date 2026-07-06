#!/usr/bin/env python3
"""persona_resolve — the `adopt(persona, mode)` pipeline (agentm-persona-
activation.md, AG Wave B leader 4/5, persona-tier build-part 3).

Six steps, five implemented here (the sixth — realizing the launch mode —
is host-specific and belongs to the per-host launch compiler, not this
module): **select** a persona's manifest, **gate** it through
`check-personas.py`'s invariants, **load** its body (on demand — this
module IS the on-demand load, there is no separate step), **resolve** its
three bindings through their own resolvers (`tier:` -> the model+effort
scale, `opinions:` -> `opinion_resolver.opinion_resolve`, `enhances:`/
`requires:` -> `capability_resolver.capability_resolve`), and **compose**
the result. All three resolvers are pure, one-way, and never raise, so a
missing binding degrades gracefully and adoption still completes.

Public API:

    resolve_tier(tier: str) -> dict | None
        The T0-T4 -> {model, effort} scale from the design's own Design
        section. A placeholder for the real model+effort-routing resolver
        (agentm-model-effort-routing.md's own build, not a Wave-B item) —
        this is the declared five-rung contract that resolver will
        eventually own; `tier:` binds to it either way, per the design's
        own risk note ("the resolver call degrades gracefully if the
        scale's runtime enforcement isn't fully wired yet"). `None` for an
        unknown tier — never raises.

    adopt(name, mode, *, root=None) -> dict
        Runs select -> gate -> resolve -> compose. Returns:
            {
              "adopted": bool,
              "reason": "adopted" | "gate-failed" | "not-found" | "error",
              "name": str,
              "mode": str,
              "stance": str | None,        # the manifest body
              "tier_binding": dict | None,
              "opinion_bindings": {name: <opinion_resolve result>},
              "capability_bindings": {name: <capability_resolve result>},
              "violations": [str, ...],    # gate failures, if any
            }
        Never raises. A gate-failing manifest is never adopted (`adopted`
        is False, `reason` is "gate-failed", no bindings are resolved).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import opinion_resolver  # noqa: E402
import capability_resolver  # noqa: E402

# The five-rung model+effort ladder (agentm-persona-activation.md's Design
# section, "Binding resolution" — a placeholder for the real
# model-effort-routing resolver, which is a separate, not-yet-built design).
_TIER_SCALE: dict[str, dict[str, str]] = {
    "T0": {"model": "cheapest", "effort": "low"},
    "T1": {"model": "opusplan", "effort": "medium"},
    "T2": {"model": "strongest", "effort": "high"},
    "T3": {"model": "strongest", "effort": "max"},
    "T4": {"model": "strongest", "effort": "max+orchestration"},
}


def resolve_tier(tier: str) -> Optional[dict]:
    """T0-T4 -> {"model": ..., "effort": ...}, or None for an unknown tier.
    Never raises."""
    return _TIER_SCALE.get(tier)


def _load_check_personas():
    """Dynamically load check-personas.py (hyphenated filename) — the same
    importlib pattern its own test file uses. Cached on first call."""
    if getattr(_load_check_personas, "_cached", None) is not None:
        return _load_check_personas._cached  # type: ignore[attr-defined]
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("check_personas", here / "check-personas.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    _load_check_personas._cached = mod  # type: ignore[attr-defined]
    return mod


def _empty_result(name: str, mode: str, reason: str, violations: Optional[list] = None) -> dict:
    return {
        "adopted": False,
        "reason": reason,
        "name": name,
        "mode": mode,
        "stance": None,
        "tier_binding": None,
        "opinion_bindings": {},
        "capability_bindings": {},
        "violations": violations or [],
    }


def adopt(name: str, mode: str, *, root: Optional[Path] = None) -> dict:
    """Select -> gate -> resolve -> compose. Never raises."""
    try:
        repo_root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
        personas_dir = repo_root / "personas"
        manifest_path = personas_dir / f"{name}.md"

        # --- select ---
        if not manifest_path.is_file():
            return _empty_result(name, mode, "not-found")

        check_personas = _load_check_personas()
        fm = check_personas._parse_frontmatter(manifest_path)
        if fm is None:
            return _empty_result(name, mode, "error")

        # --- gate --- (check-personas.py's own per-file validation, so the
        # gate and the CI check are provably the same rule, never two
        # copies that can drift).
        violations = check_personas._check_one(manifest_path, repo_root / "scripts")
        if violations:
            return _empty_result(name, mode, "gate-failed", violations)

        # --- load (on demand) --- the body, read alongside the frontmatter.
        text = manifest_path.read_text(encoding="utf-8")
        # Frontmatter is the leading `--- ... ---` block; the stance is
        # everything after it.
        stance = text
        m = check_personas._FRONTMATTER_RE.match(text)
        if m:
            stance = text[m.end():].strip()

        # --- resolve bindings (one-way, never-raise resolvers) ---
        tier_binding = resolve_tier(fm["tier"]) if "tier" in fm else None

        opinion_bindings = {
            o: opinion_resolver.opinion_resolve(o, root=repo_root)
            for o in (fm.get("opinions") or [])
        }

        # `requires:` is NOT re-resolved through capability_resolver here —
        # check-personas.py's gate (above) already enforces it names a real
        # scripts/ stem (agentm substrate), a different, already-satisfied
        # invariant from "resolves to an installed crickets capability."
        # Only `enhances:` names an actual crickets capability to bind
        # through capability_resolver — soft, absent degrades gracefully.
        capability_bindings = {
            cap: capability_resolver.capability_resolve(cap)
            for cap in (fm.get("enhances") or [])
        }

        # --- compose ---
        return {
            "adopted": True,
            "reason": "adopted",
            "name": name,
            "mode": mode,
            "stance": stance,
            "tier_binding": tier_binding,
            "opinion_bindings": opinion_bindings,
            "capability_bindings": capability_bindings,
            "violations": [],
        }
    except Exception:
        return _empty_result(name, mode, "error")
