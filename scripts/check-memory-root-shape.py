#!/usr/bin/env python3
"""Gate: the memory root has the shape the memory-root trims left it in.

agentm-vault plan 05 ruled the shape of the agent's half of the vault:

  agent/        holds `diagnostics/` and `memory/` (and `archive/` once plan
                11 creates it) and nothing loose — no engine JSON, no `_meta/`,
                no `_dream/`, no `desk/`.
  memory/       holds only the class directories the filing contract routes
                into — nothing loose, no pen, no watchlist, no settings file.
  standards/    holds the four rule files and the voice library:
                storage-rules.md · user-preferences.md ·
                security-and-secret-governance.md · moc-standards.md · voice/.

The gate reads the live vault, resolved at runtime (never a literal), and
knows three states:

  pre-migration  the retired locations still hold their files and none of the
                 new ones exist — the moves have not run yet. Reported, exit 0:
                 the migration is a deploy step (`scripts/migrate/
                 memory_root_trims.py --apply`, under quiesce), and a gate that
                 failed every build until the operator's deploy ran would gate
                 nothing.
  migrated       nothing retired remains — the shape is enforced in full.
  mixed          both — a half-applied move, or a writer that recreated a
                 retired path. Named item by item; exit 1.

`Home.md` is allowed loose in `agent/` until plan 07 retires it. `.DS_Store`,
Drive's `Icon` files and the `.rename-vault-root-complete` marker are ignored.

Usage:
  python3 scripts/check-memory-root-shape.py                 # the resolved memory root
  python3 scripts/check-memory-root-shape.py --memory-root DIR
Exit: 0 clean (or nothing to check, or pre-migration); 1 on a finding.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TOOLKIT = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for _p in (str(_HERE), str(_TOOLKIT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import vault_layout  # noqa: E402

CLASSES = ("semantic", "procedural", "episodic", "entities", "crystallized", "mocs")
AGENT_DIRS = {"diagnostics", "memory", "archive"}
AGENT_LOOSE_ALLOWED = {"Home.md"}  # plan 07 retires it
IGNORABLE = {".DS_Store", "Icon\r", "Icon", ".rename-vault-root-complete", ".gitkeep",
             ".card-backfill-complete"}  # the card backfill's marker (agentm-vault plan 06)
STANDARDS_SET = ("storage-rules.md", "user-preferences.md",
                 "security-and-secret-governance.md", "moc-standards.md")
FEATURE_ITEMS = ("_watchlist", "_skill-watchlist", "auto-orchestration-config.md",
                 "skill-discovery-sources.md", "trusted-sources.md")


def _legacy_items(root: Path) -> list[str]:
    """Retired locations that still exist, spelled memory-root-relative."""
    out = []
    for rel in ("memory/_always-load", "_meta", "_dream", "desk", "memory/memory",
                ".heat.json", ".lifecycle.json"):
        if (root / rel).exists():
            out.append(rel)
    for name in FEATURE_ITEMS:
        if (root / "memory" / name).exists():
            out.append(f"memory/{name}")
    for cand in vault_layout.voice_dir_candidates(root)[len(vault_layout.standards_dir_candidates(root)):]:
        if cand.is_dir():
            out.append(str(cand))
    for s in vault_layout.standards_dir_candidates(root):
        if (s / "forward-learning-sources.json").is_file():
            out.append(str(s / "forward-learning-sources.json"))
    return out


def _new_items(root: Path) -> list[str]:
    out = []
    s = vault_layout.standards_dir(root)
    for name in ("user-preferences.md", "security-and-secret-governance.md", "voice"):
        if (s / name).exists():
            out.append(str(s / name))
    feature = vault_layout.feature_state_dir(root)
    for name in FEATURE_ITEMS + ("forward-learning-sources.json",):
        if (feature / name).exists():
            out.append(str(feature / name))
    return out


def _shape_findings(root: Path) -> list[str]:
    findings = []
    if not root.is_dir():
        return [f"memory root {root} is not a directory"]
    for p in sorted(root.iterdir()):
        if p.name in IGNORABLE:
            continue
        if p.is_dir():
            if p.name not in AGENT_DIRS:
                findings.append(f"agent/ holds an extra directory: {p.name}/")
        elif p.name not in AGENT_LOOSE_ALLOWED:
            findings.append(f"agent/ holds a loose file: {p.name}")
    memory = root / "memory"
    if memory.is_dir():
        for p in sorted(memory.iterdir()):
            if p.name in IGNORABLE:
                continue
            if p.is_dir():
                if p.name not in CLASSES:
                    findings.append(f"memory/ holds a non-class directory: {p.name}/")
            else:
                findings.append(f"memory/ holds a loose file: {p.name}")
    else:
        findings.append("memory/ is missing")
    for p in root.rglob("*.json"):
        if "diagnostics" in p.relative_to(root).parts:
            continue  # scorecards and migration reports are records, not engine state
        findings.append(f"engine JSON under agent/: {p.relative_to(root)}")
    s = vault_layout.standards_dir(root)
    for name in STANDARDS_SET:
        if not (s / name).is_file():
            findings.append(f"standards/ is missing {name}")
    if not (s / "voice").is_dir():
        findings.append("standards/ is missing the voice library voice/")
    return findings


def check(root: Path, out=sys.stdout) -> int:
    legacy = _legacy_items(root)
    new = _new_items(root)
    if legacy and not new:
        print(f"check-memory-root-shape: pre-migration — {len(legacy)} retired location(s) still in "
              "place and none of the new ones; run scripts/migrate/memory_root_trims.py (dry run "
              "first, --apply under quiesce)", file=out)
        for item in legacy:
            print(f"  pending: {item}", file=out)
        return 0
    findings = _shape_findings(root)
    if legacy:
        for item in legacy:
            findings.append(f"retired location still present after the move: {item}")
    if findings:
        print(f"check-memory-root-shape: {len(findings)} finding(s) under {root}", file=out)
        for f in findings:
            print(f"  {f}", file=out)
        return 1
    print(f"check-memory-root-shape: clean — {root} has the trimmed shape", file=out)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--memory-root", default=None,
                    help="memory root to check (default: $MEMORY_ROOT, else the configured one)")
    args = ap.parse_args(argv)
    root = Path(args.memory_root) if args.memory_root else vault_layout.env_memory_root()
    if root is None:
        try:
            import harness_memory as hm  # noqa: E402
            root = hm.memory_root()
        except ImportError:
            root = None
    if root is None or not Path(root).is_dir():
        print("check-memory-root-shape: no memory root resolves; nothing to check")
        return 0
    return check(Path(root))


if __name__ == "__main__":
    raise SystemExit(main())
