#!/usr/bin/env python3
"""check-personas.py — assert personas/ integrity for every file under personas/.

Asserts two built invariants for each persona manifest (ADR 0016 DC-4),
plus four activation-axis shape checks (agentm-persona-activation.md):

  1. requires ⊆ substrate-native — every entry in `requires:` must resolve to
     the stem of a file in `scripts/` (as `<stem>.py` or `<stem>.sh`). This
     mechanically holds the agentm-no-hard-dep-on-crickets invariant: a requires:
     entry that names a crickets capability (e.g. "developer-workflows") has no
     file under scripts/ and will be rejected.

  2. No always-load — a persona manifest must not declare `always_load: true`
     (or the hyphenated form `always-load: true`). Personas are activated on
     demand; an always-load persona would inflate the per-call token floor
     (issue #46).

  3. `tier:`, if present, is one of T0/T1/T2/T3/T4 (the model+effort scale).

  4. `modes:`, if present, is a list drawn from {sub-agent, interactive,
     loop, goal} — the launch modes a persona supports.

  5. `triggers:`, if present, is a list of non-empty strings (a workflow-step
     name or a cue string; well-formed shape only — no semantic check against
     real phase names, since that grammar co-evolves with the resolver).

  6. `opinions:`, if present, is a list of strings — shape only. Resolution
     to a real `opinions/<name>.md` stays a runtime, graceful concern (an
     unmet binding degrades at adoption time, mirroring `enhances:`); this
     gate never fails a build over an opinion name that doesn't (yet)
     resolve.

Usage:
    python3 scripts/check-personas.py [--root DIR]

    --root DIR   repo root (default: parent of this script's directory).

Exit codes:
    0  all personas pass every invariant
    1  one or more violations found
    2  setup error (personas/ not found, or YAML parse failure)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:
    print("check-personas: pyyaml not installed — run: pip install pyyaml",
          file=sys.stderr)
    raise SystemExit(2)

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---(?:\n|$)", re.DOTALL)

# Both spellings of the always-load key to guard against.
_ALWAYS_LOAD_KEYS = ("always_load", "always-load")

# The four activation axes (agentm-persona-activation.md).
_VALID_TIERS = {"T0", "T1", "T2", "T3", "T4"}
_VALID_MODES = {"sub-agent", "interactive", "loop", "goal"}


def _parse_frontmatter(path: Path) -> dict | None:
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        print(f"check-personas: {path.name}: no YAML frontmatter (--- ... ---)",
              file=sys.stderr)
        return None
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        print(f"check-personas: {path.name}: invalid YAML frontmatter: {exc}",
              file=sys.stderr)
        return None
    if not isinstance(fm, dict):
        print(f"check-personas: {path.name}: frontmatter is not a mapping",
              file=sys.stderr)
        return None
    return fm


def _is_substrate_native(entry: str, scripts: Path) -> bool:
    """True iff scripts/<entry>.py or scripts/<entry>.sh exists."""
    return (scripts / f"{entry}.py").exists() or (scripts / f"{entry}.sh").exists()


def _check_one(path: Path, scripts: Path) -> list[str]:
    """Return a list of violation strings for a single persona file."""
    fm = _parse_frontmatter(path)
    if fm is None:
        return [f"{path.name}: unparseable frontmatter (see stderr)"]

    violations: list[str] = []

    # Assert kind: persona.
    if fm.get("kind") != "persona":
        violations.append(
            f"{path.name}: kind is {fm.get('kind')!r}, expected 'persona'"
        )

    # Assert requires ⊆ substrate-native.
    requires = fm.get("requires") or []
    if not isinstance(requires, list):
        violations.append(f"{path.name}: requires: is not a list")
    else:
        for entry in requires:
            if not isinstance(entry, str):
                violations.append(
                    f"{path.name}: requires: entry {entry!r} is not a string"
                )
            elif not _is_substrate_native(entry, scripts):
                violations.append(
                    f"{path.name}: requires: {entry!r} is not substrate-native "
                    f"(no scripts/{entry}.py or scripts/{entry}.sh)"
                )

    # Assert no always-load.
    for key in _ALWAYS_LOAD_KEYS:
        if fm.get(key) is True:
            violations.append(
                f"{path.name}: {key}: true — personas must be on-demand, "
                "never always-load (ADR 0016 DC-4)"
            )

    # --- activation axes (agentm-persona-activation.md) — shape only ---

    if "tier" in fm:
        tier = fm.get("tier")
        if tier not in _VALID_TIERS:
            violations.append(
                f"{path.name}: tier: {tier!r} — must be one of "
                f"{sorted(_VALID_TIERS)}"
            )

    if "modes" in fm:
        modes = fm.get("modes")
        if not isinstance(modes, list) or not modes:
            violations.append(f"{path.name}: modes: must be a non-empty list")
        else:
            for m in modes:
                if m not in _VALID_MODES:
                    violations.append(
                        f"{path.name}: modes: {m!r} — must be one of "
                        f"{sorted(_VALID_MODES)}"
                    )

    if "triggers" in fm:
        triggers = fm.get("triggers")
        if not isinstance(triggers, list):
            violations.append(f"{path.name}: triggers: is not a list")
        else:
            for t in triggers:
                if not isinstance(t, str) or not t.strip():
                    violations.append(
                        f"{path.name}: triggers: entry {t!r} is not a non-empty string"
                    )

    if "opinions" in fm:
        opinions = fm.get("opinions")
        if not isinstance(opinions, list):
            violations.append(f"{path.name}: opinions: is not a list")
        else:
            for o in opinions:
                if not isinstance(o, str) or not o.strip():
                    violations.append(
                        f"{path.name}: opinions: entry {o!r} is not a non-empty string"
                    )

    return violations


def _main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    args = argv[1:]
    while args:
        if args[0] in ("--root", "-r") and len(args) >= 2:
            root = Path(args[1])
            args = args[2:]
        elif args[0].startswith("--root="):
            root = Path(args[0][len("--root="):])
            args = args[1:]
        else:
            print(f"check-personas: unknown argument: {args[0]}", file=sys.stderr)
            return 2

    personas_dir = root / "personas"
    scripts_dir = root / "scripts"

    if not personas_dir.is_dir():
        print(f"check-personas: personas/ not found under {root} — nothing to check.")
        return 0

    if not scripts_dir.is_dir():
        print(f"check-personas: scripts/ not found under {root}", file=sys.stderr)
        return 2

    persona_files = sorted(personas_dir.glob("*.md"))
    if not persona_files:
        print("check-personas: no *.md files under personas/ — nothing to check.")
        return 0

    all_violations: list[str] = []
    for path in persona_files:
        all_violations.extend(_check_one(path, scripts_dir))

    if all_violations:
        print("check-personas: FAIL", file=sys.stderr)
        for v in all_violations:
            print(f"  {v}", file=sys.stderr)
        print("", file=sys.stderr)
        print("  Persona invariants (ADR 0016 DC-4 + agentm-persona-activation.md):", file=sys.stderr)
        print("    requires: — every entry must be a scripts/ stem (.py or .sh)", file=sys.stderr)
        print("    always_load — personas are on-demand; never set to true", file=sys.stderr)
        print(f"    tier: — one of {sorted(_VALID_TIERS)}", file=sys.stderr)
        print(f"    modes: — a list drawn from {sorted(_VALID_MODES)}", file=sys.stderr)
        print("    triggers: — a list of non-empty strings", file=sys.stderr)
        print("    opinions: — a list of non-empty strings (shape only)", file=sys.stderr)
        return 1

    n = len(persona_files)
    print(f"check-personas: {n} persona{'s' if n != 1 else ''} — clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
