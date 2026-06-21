#!/usr/bin/env python3
"""governs_resolver — the design-governance registry + resolver (agentm AG Phase 2).

The substrate half of the AG-track grounding loop (design-doc §6): given a target
source file (or an area name), answer *"which living design governs this?"* by
reading the local repo's `wiki/designs/` frontmatter. The crickets
`find_governing_design.py` bridge (C3) targets this module's IO/exit-code
contract, exactly as `find_capability.py` targets `capability_resolver.py`.

Public API (the module contract; the CLI shim in `agentm-governs.sh` wraps it):

    resolve_governing_design(target, *, root=None, include_proposed=False) -> dict
        Resolve which design governs `target` (a repo-relative file path OR a
        known `area:` value). Returns:
            {
              "governed": bool,
              "design":   str | None,   # repo-relative path to the design
              "area":     str | None,   # the governing design's area
              "reason":   str,          # "governed" | "greenfield" | "overlap" | "error"
            }
        Most-specific governs:-pattern wins; an exact-specificity tie between two
        designs is fail-loud "overlap" (design=None), never a guess — designs must
        not overlap (area-taxonomy.md no-overlap rule). Never raises — any internal
        error collapses to {…, reason: "error"} (fail-safe, like capability_resolver).
        "greenfield" is the clean no-match default.

    build_index(root=None, *, include_proposed=False) -> list[GovernsEntry]
        Low-level: scan `wiki/designs/**/*.md`, return one GovernsEntry per
        (pattern) a launched design declares in `governs:`. `root` overrides the
        repo root (tests inject a temp dir). By default only `status: launched`
        designs participate (the live truth the §6.6 status filter selects);
        `include_proposed=True` also indexes `status: proposed` designs.

The frontmatter convention this resolves (AG Phase 2 — see
wiki/reference/Design-Frontmatter.md):
- `governs:`  list of repo-relative path globs / dir-prefixes a design owns.
- `area:`     taxonomy bucket (foundations · agentm · memory · experience ·
              opinions · personas).
- `scope:`    altitude (arc › feature › sub-feature › tweak) — metadata.
- `shape:`    Axis-A SHAPE for *primitives* (skill/hook/agent/…) — NOT carried
              by design docs; documented but never read here.
- `kind:`     artifact type (design | research) — metadata.

Design constraints (mirror capability_resolver.py, all non-negotiable):
- Capability/area-keyed: caller names a target; resolver finds the design.
- One-directional: reads frontmatter as DATA; never imports design or plugin
  code (designs are markdown — there is nothing to import).
- Fail-safe: unavailable/greenfield is the safe default; never raises on absence.
- Bounded: a single index scan of `wiki/designs/`; no recursion into arbitrary
  trees, no network, no third-party deps.

Stdlib-only. Cross-platform via pathlib.

CLI / exit-code contract (the bridge targets this):

    governs_resolver.py [--json] [--root DIR] [--include-proposed] <file-or-area>

    On a governed target:   prints the repo-relative design path to stdout,
                            exit 0.
    On a greenfield target: prints nothing to stdout (a note to stderr),
                            exit 1.
    On a usage error:       usage to stderr, exit 2.
    --json:                 print the full result dict to stdout instead of the
                            bare path; exit code is unchanged (0 governed /
                            1 greenfield / 2 usage).
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from dataclasses import dataclass
from pathlib import Path

# ── data types ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class GovernsEntry:
    """One (pattern → design) claim parsed from a design's `governs:` list."""
    pattern: str        # a repo-relative path glob / dir-prefix
    design: str         # repo-relative path to the governing design
    area: str | None    # the design's area: taxonomy value
    status: str | None  # the design's status: (launched | proposed | …)


# ── frontmatter reader (stdlib-only, no PyYAML) ────────────────────────────────

def _parse_frontmatter(text: str) -> dict:
    """Parse the leading `--- … ---` YAML frontmatter into a dict.

    Supports exactly what design frontmatter uses: scalar `key: value`, inline
    lists `key: [a, b]`, and block lists (`key:` then indented `- item` lines).
    Returns {} when there is no frontmatter. Never raises.
    """
    try:
        if not text.startswith("---"):
            return {}
        lines = text.splitlines()
        # first line is the opening fence; find the closing one
        if lines and lines[0].strip() != "---":
            return {}
        end = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end = i
                break
        if end is None:
            return {}

        data: dict = {}
        i = 1
        while i < end:
            raw = lines[i]
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                i += 1
                continue
            if ":" not in stripped:
                i += 1
                continue
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()
            if val == "":
                # possible block list: collect following indented `- item` lines
                items: list[str] = []
                j = i + 1
                while j < end:
                    nxt = lines[j]
                    if nxt.strip().startswith("- "):
                        items.append(_unquote(nxt.strip()[2:].strip()))
                        j += 1
                    elif nxt.strip() == "":
                        j += 1
                    else:
                        break
                data[key] = items if items else ""
                i = j
                continue
            if val.startswith("[") and val.endswith("]"):
                inner = val[1:-1].strip()
                data[key] = [_unquote(x.strip()) for x in inner.split(",") if x.strip()]
            else:
                data[key] = _unquote(val)
            i += 1
        return data
    except Exception:
        return {}


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def _as_list(v) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()]
    if isinstance(v, str) and v.strip():
        return [v.strip()]
    return []


# ── index builder ──────────────────────────────────────────────────────────────

def _repo_root(root: Path | None) -> Path:
    if root is not None:
        return Path(root)
    # scripts/ is a direct child of the repo root
    return Path(__file__).resolve().parent.parent


def build_index(root: Path | None = None, *,
                include_proposed: bool = False) -> list[GovernsEntry]:
    """Scan `wiki/designs/` and return the governs:→design registry.

    Returns an empty list on any I/O error (the resolver then reports
    "greenfield" for every target). Only `status: launched` designs participate
    by default; `include_proposed=True` also indexes `status: proposed`.
    """
    repo = _repo_root(root)
    designs_dir = repo / "wiki" / "designs"
    entries: list[GovernsEntry] = []
    try:
        if not designs_dir.is_dir():
            return []
        allowed = {"launched"} | ({"proposed"} if include_proposed else set())
        for md in sorted(designs_dir.rglob("*.md")):
            try:
                text = md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fm = _parse_frontmatter(text)
            governs = _as_list(fm.get("governs"))
            area = fm.get("area") if isinstance(fm.get("area"), str) else None
            # A design participates in governance if it declares governs: (file
            # resolution) OR area: (area-only roots like shared/foundations, which
            # govern no code but must be reachable by area — area-taxonomy.md).
            if not governs and not area:
                continue
            status = fm.get("status") if isinstance(fm.get("status"), str) else None
            if status not in allowed:
                continue
            try:
                design_rel = md.relative_to(repo).as_posix()
            except ValueError:
                design_rel = md.as_posix()
            if governs:
                for pat in governs:
                    entries.append(GovernsEntry(pat, design_rel, area, status))
            else:
                # area-only: an empty pattern never matches a file (_specificity
                # returns -1), so it affects area lookup / designs_in only.
                entries.append(GovernsEntry("", design_rel, area, status))
    except Exception:
        return []
    return entries


# ── matching ────────────────────────────────────────────────────────────────--

def _specificity(pattern: str, target: str) -> int:
    """Return a match-specificity score for `pattern` against `target`, or -1.

    Higher = more specific (longer matching pattern wins). Match forms:
      - exact:       target == pattern
      - dir-prefix:  target startswith pattern + "/"
      - glob:        fnmatch(target, pattern)  (and pattern/** style)
    """
    p = pattern.strip().strip("/")
    t = target.strip().strip("/")
    if not p:
        return -1
    if t == p:
        return len(p)
    if t.startswith(p + "/"):
        return len(p)
    if any(ch in p for ch in "*?[") and (
        fnmatch.fnmatch(t, p) or fnmatch.fnmatch(t, p.rstrip("/*") + "/*")
    ):
        return len(p.rstrip("/*"))
    return -1


# ── public resolver API ────────────────────────────────────────────────────────

def resolve_governing_design(target: str, *, root: Path | None = None,
                             include_proposed: bool = False) -> dict:
    """Resolve which design governs `target` (a repo-relative path OR an area name).

    See the module docstring for the return shape. Never raises.
    """
    try:
        target = (target or "").strip()
        if not target:
            return {"governed": False, "design": None, "area": None,
                    "reason": "greenfield"}

        entries = build_index(root, include_proposed=include_proposed)
        if not entries:
            return {"governed": False, "design": None, "area": None,
                    "reason": "greenfield"}

        # Area-name resolution: an exact match against a known area: value.
        areas = {e.area for e in entries if e.area}
        if target in areas:
            matches = sorted(
                {(e.design, e.area) for e in entries if e.area == target}
            )
            design, area = matches[0]
            return {"governed": True, "design": design, "area": area,
                    "reason": "governed"}

        # Path resolution: most-specific governs: pattern wins; an exact tie
        # between *different* designs is fail-loud "overlap", not a guess
        # (area-taxonomy.md no-overlap rule — designs must not overlap).
        norm = target.replace("\\", "/")
        matches: list[tuple[int, str, str | None]] = []  # (spec, design, area)
        for e in entries:
            spec = _specificity(e.pattern, norm)
            if spec >= 0:
                matches.append((spec, e.design, e.area))
        if not matches:
            return {"governed": False, "design": None, "area": None,
                    "reason": "greenfield"}
        max_spec = max(m[0] for m in matches)
        top = {(d, a) for s, d, a in matches if s == max_spec}
        if len(top) > 1:
            return {"governed": False, "design": None, "area": None,
                    "reason": "overlap"}
        design, area = next(iter(top))
        return {"governed": True, "design": design, "area": area,
                "reason": "governed"}
    except Exception:
        return {"governed": False, "design": None, "area": None,
                "reason": "error"}


def designs_in(area: str, *, root: Path | None = None,
               include_proposed: bool = False) -> list[str]:
    """Return the repo-relative paths of every design declaring `area:` == `area`.

    The area-keyed accessor (area-taxonomy.md) the SessionStart paths-only inject
    and the index page use. Sorted, de-duplicated; `[]` on no match. Never raises.
    """
    try:
        entries = build_index(root, include_proposed=include_proposed)
        return sorted({e.design for e in entries if e.area == area})
    except Exception:
        return []


# ── CLI (entry point for the agentm-governs shim) ──────────────────────────────

def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="governs_resolver.py",
        description="Resolve the living design that governs a file or area.",
        add_help=True,
    )
    ap.add_argument("target", nargs="?", help="repo-relative file path or area name")
    ap.add_argument("--json", action="store_true",
                    help="print the full result dict instead of the bare path")
    ap.add_argument("--root", type=Path, default=None,
                    help="repo root override (default: parent of scripts/)")
    ap.add_argument("--include-proposed", action="store_true",
                    help="also index status: proposed designs (default: launched only)")
    try:
        args = ap.parse_args(argv[1:])
    except SystemExit:
        return 2
    if not args.target:
        print("usage: governs_resolver.py [--json] [--root DIR] "
              "[--include-proposed] <file-or-area>", file=sys.stderr)
        return 2

    result = resolve_governing_design(
        args.target, root=args.root, include_proposed=args.include_proposed
    )
    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif result["governed"]:
        print(result["design"])
    else:
        print(f"governs_resolver: no design governs '{args.target}' "
              f"({result['reason']})", file=sys.stderr)
    return 0 if result["governed"] else 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
