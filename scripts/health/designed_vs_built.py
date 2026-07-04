#!/usr/bin/env python3
"""designed_vs_built.py — the designed-vs-built ledger aggregator (R2.6).

Turns the AG build's unenumerated "103 designed-not-built items" headline
(`ALIGNMENT-ROADMAP.md:15`) into a real, re-derivable per-capability registry.
Walks `wiki/designs/*.md` in both agentm and crickets, and for each design
classifies its governed capability into exactly one of three buckets:

    built              — the design declares `governs:` target(s) that exist
                          on disk, and the body carries no `[PENDING-IMPL]`
                          marker.
    designed-not-built — either (a) the body carries one or more
                          `[PENDING-IMPL]` markers (one ledger item per
                          marker, regardless of `status:`), or (b) no
                          governed target resolves on disk and `status` is
                          not `launched`.
    wiki-tracked        — `status: launched` but no governed target resolves
                          on disk. `status: launched` means "wiki-tracked",
                          never "built", on its own (agentmDesigns#11) — this
                          bucket exists so that distinction survives the
                          count instead of silently folding into "built".

Stdlib-only (no pyyaml — the frontmatter parser below handles only the
`key: value` / `key: [a, b]` / block-list shapes the design corpus actually
uses, not general YAML).

Usage:
    python3 scripts/health/designed_vs_built.py [--agentm-root PATH]
                                                  [--crickets-root PATH]
                                                  [--format markdown|json]
                                                  [--jsonl-out FILE]
Exit:
    0  always, once the corpora are walked (a missing crickets sibling is a
       warning, not a failure — the nightly workflow may run agentm-only).
    2  setup error (agentm root has no wiki/designs/).
"""
from __future__ import annotations

import argparse
import glob as globmod
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent  # agentm repo root (scripts/health/../..)

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_PENDING_MARKER = "[PENDING-IMPL]"
_MAX_SNIPPET_LEN = 160

# Only real design docs carry `kind: design` frontmatter — nav/index pages
# (Designs.md, _Sidebar.md, ...) either have no frontmatter block at all or a
# different `kind`, so filtering on this is more robust than a filename list.
_DESIGN_KIND = "design"


def _parse_frontmatter(text: str) -> dict:
    """Minimal frontmatter parser for the three shapes the corpus uses.

    Handles `key: value` scalars, `key: [a, b, c]` bracket-lists, and
    `key:` followed by `  - item` block lists. Not a general YAML parser —
    good enough for `title:` / `status:` / `kind:` / `governs:`.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    lines = m.group(1).splitlines()
    data: dict = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.strip().startswith("#"):
            i += 1
            continue
        kv = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", line)
        if not kv:
            i += 1
            continue
        key, rest = kv.group(1), kv.group(2).split(" #", 1)[0].strip()
        if rest.startswith("[") and rest.endswith("]"):
            inner = rest[1:-1].strip()
            data[key] = [x.strip() for x in inner.split(",") if x.strip()] if inner else []
            i += 1
        elif rest == "":
            items = []
            j = i + 1
            while j < len(lines) and lines[j].startswith("  - "):
                items.append(lines[j][4:].strip())
                j += 1
            data[key] = items if items else ""
            i = j if items else i + 1
        else:
            data[key] = rest.strip('"').strip("'")
            i += 1
    return data


def _find_pending_markers(text: str) -> list[dict]:
    """One item per `[PENDING-IMPL]` occurrence: 1-based line + a short snippet."""
    items = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _PENDING_MARKER in line:
            snippet = line.strip()
            if len(snippet) > _MAX_SNIPPET_LEN:
                snippet = snippet[: _MAX_SNIPPET_LEN - 3] + "..."
            items.append({"line": lineno, "snippet": snippet})
    return items


def _governed_targets_exist(repo_root: Path, governs: list[str]) -> list[dict]:
    """Resolve each `governs:` entry against the repo root; report existence.

    A trailing `/**` or `/*` glob resolves via `glob`; a bare path is checked
    directly (covers both file targets and directory targets without a
    trailing glob).
    """
    resolved = []
    for target in governs:
        target = target.strip()
        if not target:
            continue
        has_glob = any(ch in target for ch in "*?[")
        if has_glob:
            matches = globmod.glob(str(repo_root / target), recursive=True)
            exists = bool(matches)
        else:
            exists = (repo_root / target).exists()
        resolved.append({"target": target, "exists": exists})
    return resolved


def classify_design(path: Path, repo_root: Path, repo_name: str) -> dict:
    """Classify one design file's governed capability + its PENDING-IMPL items.

    Returns a dict with the parsed `status`/`governs` plus a list of ledger
    `items` — one item per PENDING-IMPL marker if any exist, else exactly one
    item summarizing the design's own governed-capability classification.
    """
    text = path.read_text(encoding="utf-8")
    fm = _parse_frontmatter(text)
    status = fm.get("status", "") if isinstance(fm.get("status", ""), str) else ""
    governs_raw = fm.get("governs", [])
    governs = governs_raw if isinstance(governs_raw, list) else ([governs_raw] if governs_raw else [])

    design_id = f"{repo_name}/{path.stem}"
    pending = _find_pending_markers(text)
    targets = _governed_targets_exist(repo_root, governs)
    any_target_exists = any(t["exists"] for t in targets)

    items = []
    if pending:
        for p in pending:
            items.append({
                "design": design_id,
                "repo": repo_name,
                "capability": f"{path.stem}:L{p['line']}",
                "classification": "designed-not-built",
                "reason": "[PENDING-IMPL] marker",
                "detail": p["snippet"],
            })
    elif governs and any_target_exists:
        items.append({
            "design": design_id,
            "repo": repo_name,
            "capability": path.stem,
            "classification": "built",
            "reason": "governed target(s) exist on disk, no PENDING-IMPL markers",
            "detail": ", ".join(t["target"] for t in targets if t["exists"]),
        })
    elif status == "launched":
        items.append({
            "design": design_id,
            "repo": repo_name,
            "capability": path.stem,
            "classification": "wiki-tracked",
            "reason": "status: launched but no governed target resolves on disk "
                      "(status is wiki-tracked, not a built signal on its own)",
            "detail": ", ".join(governs) if governs else "(no governs targets declared)",
        })
    else:
        items.append({
            "design": design_id,
            "repo": repo_name,
            "capability": path.stem,
            "classification": "designed-not-built",
            "reason": "no governed target resolves on disk and status is not launched",
            "detail": ", ".join(governs) if governs else "(no governs targets declared)",
        })

    return {"design": design_id, "status": status, "governs": governs, "items": items}


def walk_designs(repo_root: Path, repo_name: str) -> list[dict]:
    designs_dir = repo_root / "wiki" / "designs"
    if not designs_dir.is_dir():
        return []
    out = []
    for path in sorted(designs_dir.glob("*.md")):
        fm = _parse_frontmatter(path.read_text(encoding="utf-8"))
        if fm.get("kind") != _DESIGN_KIND:
            continue  # nav/index pages (Designs.md, _Sidebar.md, ...) — not design docs
        out.append(classify_design(path, repo_root, repo_name))
    return out


def _default_crickets_root(agentm_root: Path) -> Path | None:
    candidate = agentm_root.parent / "crickets"
    return candidate if candidate.is_dir() else None


def build_registry(agentm_root: Path, crickets_root: Path | None) -> dict:
    designs = walk_designs(agentm_root, "agentm")
    warnings = []
    if crickets_root is not None and crickets_root.is_dir():
        designs += walk_designs(crickets_root, "crickets")
    else:
        warnings.append(
            "crickets root not found — agentm-only report "
            "(expected when running in a checkout without the crickets sibling)"
        )

    all_items = [item for d in designs for item in d["items"]]
    counts: dict[str, int] = {"built": 0, "designed-not-built": 0, "wiki-tracked": 0}
    for item in all_items:
        counts[item["classification"]] = counts.get(item["classification"], 0) + 1

    return {
        "designs": designs,
        "items": all_items,
        "counts": counts,
        "total_designs": len(designs),
        "total_items": len(all_items),
        "warnings": warnings,
    }


def render_markdown(registry: dict) -> str:
    lines = ["# Designed-vs-Built Ledger", ""]
    lines.append(
        f"**{registry['total_items']} ledger items** across **{registry['total_designs']} designs** "
        f"(agentm + crickets `wiki/designs/`)."
    )
    lines.append("")
    lines.append("| Classification | Count |")
    lines.append("|---|---:|")
    for key in ("built", "designed-not-built", "wiki-tracked"):
        lines.append(f"| {key} | {registry['counts'].get(key, 0)} |")
    for w in registry["warnings"]:
        lines.append("")
        lines.append(f"> [!WARNING]\n> {w}")
    lines.append("")
    lines.append("| Design | Repo | Capability | Classification | Reason |")
    lines.append("|---|---|---|---|---|")
    for item in registry["items"]:
        lines.append(
            f"| {item['design']} | {item['repo']} | {item['capability']} | "
            f"{item['classification']} | {item['reason']} |"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def to_check_records(registry: dict) -> list[dict]:
    """Render the registry as dashboard-schema check records (capability function axis).

    `built` items are live-pass records; `designed-not-built` and
    `wiki-tracked` items are dark checks (`pass: null, dark: true`) — they
    never reduce the axis denominator, consistent with the rest of the
    dashboard's dark-check convention (scripts/health/README.md).
    """
    records = []
    for item in registry["items"]:
        record = {
            "suite": "designed-vs-built",
            "axis": "capability function",
            "check": f"{item['design']}::{item['capability']}",
            "weight": 1.0,
        }
        if item["classification"] == "built":
            record["pass"] = True
        else:
            record["pass"] = None
            record["dark"] = True
        records.append(record)
    return records


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Aggregate the designed-vs-built ledger across both repos.")
    p.add_argument("--agentm-root", default=str(REPO), help="agentm repo root (default: this script's own repo)")
    p.add_argument("--crickets-root", default=None, help="crickets repo root (default: sibling ../crickets, if present)")
    p.add_argument("--format", choices=("markdown", "json"), default="markdown")
    p.add_argument("--jsonl-out", default=None, help="also write dashboard-schema check records to this path")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    agentm_root = Path(args.agentm_root).resolve()
    if not (agentm_root / "wiki" / "designs").is_dir():
        print(f"designed_vs_built: no wiki/designs/ under agentm root {agentm_root}", file=sys.stderr)
        return 2

    crickets_root = Path(args.crickets_root).resolve() if args.crickets_root else _default_crickets_root(agentm_root)
    registry = build_registry(agentm_root, crickets_root)

    if args.jsonl_out:
        records = to_check_records(registry)
        with open(args.jsonl_out, "w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, sort_keys=True) + "\n")

    if args.format == "json":
        print(json.dumps(registry, indent=2, sort_keys=True))
    else:
        print(render_markdown(registry), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
