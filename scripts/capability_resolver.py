#!/usr/bin/env python3
"""capability_resolver — the plugin-capability registry + resolver (agentm V5-8).

The runtime half of crickets' `enhances:` soft-composition: aggregates
installed plugins' declared `capabilities:` into a capability→provider registry
and answers *"is this plugin capability available, optionally at a version range?"*

Public API (the module contract; the CLI shim in `agentm capability` wraps it):

    capability_available(name, *, version=None) -> bool
        True iff a provider for `name` is installed + enabled and its version
        satisfies `version` (when given). False on any absence — no raise (LC-4).

    capability_resolve(name) -> dict
        Richer form: {"available": bool, "provider": str|None,
                      "version": str|None, "reason": str}
        Reasons: "available" | "no-provider" | "provider-not-installed" |
                 "version-mismatch". The last two are diagnostics; callers that
                 only need the boolean should use capability_available.

    build_registry(root=None) -> dict
        Low-level: build and return the raw capability→ProviderEntry map.
        `root` overrides the user home directory (used by tests with temp dirs).

Design constraints (V5-8, all non-negotiable):
- LC-2  Capability-keyed (caller names the capability; resolver finds provider).
- LC-3  Version matching is a single range check, not a solver
        (enhances ∩ requires = ∅). Stub in Task 1; implemented in Task 2.
- LC-4  Unavailable is the safe default; the resolver never raises on absence.
- LC-6  No agentm substrate → all capabilities resolve to "unavailable" (safe).
- One-directional: reads manifests as data, never imports plugin code.

Host read paths (confirmed by spike M7, 2026-06-15):
- Claude Code : ~/.claude/plugins/known_marketplaces.json
                → <installLocation>/.claude-plugin/marketplace.json (capabilities/enhances)
                + ~/.claude/plugins/installed_plugins.json (enabled set + version)
- Antigravity : ~/.gemini/config/plugins/<name>/capabilities.json (sidecar, V5-8 spike fix)
                + ~/.gemini/config/import_manifest.json (enabled set)

Stdlib-only. No third-party deps. Cross-platform via pathlib.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# ── data types ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ProviderEntry:
    """One plugin's claim to provide a set of capabilities."""
    plugin: str          # plugin slug / name
    version: str | None  # declared version from the marketplace manifest
    installed: bool      # True = enabled on the current host


# capability → the BEST provider (first installed wins; first declared wins for
# not-installed entries). The registry holds at most one entry per capability
# (the resolver is not a solver — LC-3).
Registry = dict[str, ProviderEntry]


# ── host-state readers ────────────────────────────────────────────────────────

def _safe_json(path: Path) -> dict | list | None:
    """Read + parse JSON from `path`; return None on any error (graceful)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_claude_code(root: Path) -> Registry:
    """Build registry from Claude Code's ~/.claude/plugins/ state.

    Reads every registered marketplace's manifest to discover all declared
    capabilities (installed + uninstalled), then cross-references the
    installed_plugins.json to mark which providers are actually enabled.
    """
    base = root / ".claude" / "plugins"
    known_mp = base / "known_marketplaces.json"
    installed_path = base / "installed_plugins.json"

    raw_known = _safe_json(known_mp)
    raw_installed = _safe_json(installed_path)
    if not isinstance(raw_known, dict) or not isinstance(raw_installed, dict):
        return {}

    # enabled: {plugin_slug → version} (take the first/most-recent install)
    enabled: dict[str, str] = {}
    for key, installs in raw_installed.get("plugins", {}).items():
        slug = key.split("@", 1)[0]
        if isinstance(installs, list) and installs:
            enabled[slug] = installs[0].get("version") or ""

    registry: Registry = {}

    for mp_name, mp_info in raw_known.items():
        install_loc = mp_info.get("installLocation", "")
        if not install_loc:
            continue
        mp_path = Path(install_loc) / ".claude-plugin" / "marketplace.json"
        data = _safe_json(mp_path)
        if not isinstance(data, dict):
            continue

        for plugin in data.get("plugins", []):
            slug = plugin.get("name", "")
            if not slug:
                continue
            version = plugin.get("version") or enabled.get(slug)
            is_installed = slug in enabled
            actual_version = enabled.get(slug) if is_installed else version

            for cap in plugin.get("capabilities", []):
                if not cap:
                    continue
                # Installed provider wins; among installed providers, first wins.
                existing = registry.get(cap)
                if existing is None:
                    registry[cap] = ProviderEntry(slug, actual_version, is_installed)
                elif not existing.installed and is_installed:
                    # Promote: installed provider supersedes an uninstalled one.
                    registry[cap] = ProviderEntry(slug, actual_version, True)

    return registry


def _read_antigravity(root: Path) -> Registry:
    """Build registry from Antigravity's ~/.gemini/config/ state.

    Antigravity has no marketplace registry (agy has no marketplace concept);
    plugins are installed individually by path. The capabilities.json sidecar
    (emitted alongside plugin.json by the crickets Antigravity generator since
    V5-8 spike, 2026-06-15) carries the capabilities/enhances for installed
    plugins only.
    """
    config = root / ".gemini" / "config"
    import_manifest = config / "import_manifest.json"
    plugin_dir = config / "plugins"

    raw_manifest = _safe_json(import_manifest)
    if not isinstance(raw_manifest, dict):
        return {}

    enabled: set[str] = {
        e["name"] for e in raw_manifest.get("imports", [])
        if isinstance(e, dict) and e.get("name")
    }

    registry: Registry = {}

    for name in enabled:
        sidecar = plugin_dir / name / "capabilities.json"
        data = _safe_json(sidecar)
        if not isinstance(data, dict):
            continue
        version = data.get("version")
        for cap in data.get("capabilities", []):
            if not cap:
                continue
            if cap not in registry:
                registry[cap] = ProviderEntry(name, version, True)

    return registry


# ── registry builder ──────────────────────────────────────────────────────────

def build_registry(root: Path | None = None) -> Registry:
    """Aggregate installed plugins' declared capabilities into a registry.

    Tries Claude Code's state first, then Antigravity's. The two are mutually
    exclusive in practice (distinct state dirs) but this falls through cleanly
    when one is absent, so the resolver works on either host without host
    detection. Merges: Claude Code entries win over Antigravity entries when
    both are present (expected to be identical in co-installed setups).

    `root` overrides the home directory (tests inject a temp dir).
    Returns an empty dict on any I/O error — the resolver then reports
    "unavailable" for every capability (LC-4 / LC-6).
    """
    if root is None:
        root = Path.home()
    root = Path(root)

    registry: Registry = {}

    try:
        cc = _read_claude_code(root)
        for cap, entry in cc.items():
            if cap not in registry or (not registry[cap].installed and entry.installed):
                registry[cap] = entry
    except Exception:
        pass

    try:
        ag = _read_antigravity(root)
        for cap, entry in ag.items():
            if cap not in registry or (not registry[cap].installed and entry.installed):
                registry[cap] = entry
    except Exception:
        pass

    return registry


# ── public resolver API ───────────────────────────────────────────────────────

def capability_resolve(name: str, *, version: str | None = None,
                       registry: Registry | None = None) -> dict:
    """Resolve whether plugin capability `name` is available.

    Returns:
        {
          "available": bool,
          "provider":  str | None,   # plugin slug
          "version":   str | None,   # provider's declared version
          "reason":    str,          # "available" | "no-provider" |
                                     # "provider-not-installed" | "version-mismatch"
        }

    `version` is a version range string (e.g. ">= 1.2"); Task 2 implements the
    range check — in Task 1 it is accepted but not yet evaluated (any version
    satisfies if the provider is installed).

    `registry` may be injected (tests, callers that already built it).
    Never raises; returns `no-provider` on any internal error (LC-4).
    """
    try:
        reg = registry if registry is not None else build_registry()
        entry = reg.get(name)

        if entry is None:
            return {"available": False, "provider": None,
                    "version": None, "reason": "no-provider"}

        if not entry.installed:
            return {"available": False, "provider": entry.plugin,
                    "version": entry.version, "reason": "provider-not-installed"}

        if version is not None:
            # Version-range matching: Task 2 stub.
            # Imported here to avoid a circular dep if version_match lives in
            # the same package later; the call is guarded so Task-1 tests that
            # pass version=None never reach this branch.
            from capability_version_match import satisfies  # noqa: PLC0415
            if not satisfies(entry.version, version):
                return {"available": False, "provider": entry.plugin,
                        "version": entry.version, "reason": "version-mismatch"}

        return {"available": True, "provider": entry.plugin,
                "version": entry.version, "reason": "available"}
    except Exception:
        return {"available": False, "provider": None,
                "version": None, "reason": "no-provider"}


def capability_available(name: str, *, version: str | None = None,
                         registry: Registry | None = None) -> bool:
    """True iff plugin capability `name` is available at the optional `version` range.

    The boolean surface; `capability_resolve` carries the full reason for
    callers that need to log or branch on why. Never raises (LC-4).
    """
    return capability_resolve(name, version=version, registry=registry)["available"]


# ── CLI (entry point for the agentm capability shim) ─────────────────────────

def _main(argv: list[str]) -> int:
    """CLI used by the `agentm capability` shim (Task 3).

    Exit codes match the probe's contract:
      0  capability available
      1  capability unavailable
      2  usage error
    """
    if len(argv) < 2 or len(argv) > 3:
        print("usage: capability_resolver.py <capability> [<version-range>]",
              file=sys.stderr)
        return 2
    name = argv[1]
    ver = argv[2] if len(argv) == 3 else None
    result = capability_resolve(name, version=ver)
    return 0 if result["available"] else 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
