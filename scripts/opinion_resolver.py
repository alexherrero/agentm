#!/usr/bin/env python3
"""opinion_resolver — the request-by-name Opinion registry (agentm-opinion-registry.md).

Lets a tool or persona ask for an Opinion (`good`, `done`, `efficient`, …)
**by name** and get back the coded base folded with the learned supplement —
the piece the [opinions pillar](../wiki/designs/agentm-opinions-and-gates.md)
leaves `[PENDING-IMPL]`. Mirrors `governs_resolver.py`'s shape exactly: a pure
frontmatter scan (never imports design/plugin code — opinions are markdown,
there is nothing to import), fail-safe (never raises), always-latest (no
version pin).

Public API:

    build_index(root=None) -> dict[str, OpinionEntry]
        Scan `opinions/*.md` into a `name -> OpinionEntry` map (at most one
        base entry per name — a duplicate `name:` frontmatter value is a
        malformed install, not a merge; the first file wins, in sorted
        filename order, so the result is deterministic).

    opinion_resolve(name, *, root=None, supplement_dir=None) -> dict
        Resolve `name` to its composite. Returns:
            {
              "name":    str,
              "reason":  "served" | "base-only" | "no-opinion" | "error",
              "base":    str | None,   # the coded base's prose body
              "supplement": str | None,  # the learned-supplement body, if any
              "question": str | None,
              "implements": str | None,
              "composes": list[str],
            }
        `served` — base present, a learned supplement folded in.
        `base-only` — base present, no supplement yet (a bare install).
        `no-opinion` — unknown name; never raises, a soft consumer degrades
        to quiet absence, a hard consumer's `requires:` gate decides whether
        that's fatal.
        `error` — the scan collapsed; degrade, don't crash.

CLI / exit-code contract (mirrors governs_resolver.py / capability_resolver.py):

    agentm-opinion.sh [--json] [--root DIR] <name>

    served/base-only:  prints the composed body to stdout, exit 0.
    no-opinion:        prints nothing to stdout (a note to stderr), exit 1.
    error:             prints nothing to stdout (a note to stderr), exit 1.
    usage error:       usage to stderr, exit 2.
    --json:            print the full result dict to stdout instead of the
                       bare body; exit code is unchanged.

Stdlib-only. Cross-platform via pathlib. No third-party deps, no network,
no `import_module` / `__import__` / `exec` / `eval` (gate-enforced by
check-opinion-resolver-one-way.py — the resolver discovers opinions, it
never runs them).
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path


# ── data types ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class OpinionEntry:
    """One `opinions/<name>.md` coded base, parsed."""
    name: str
    question: str | None
    implements: str | None
    composes: list[str]
    serves: list[str]
    body: str
    path: Path


# ── frontmatter reader (stdlib-only, no PyYAML — mirrors governs_resolver.py) ─

def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """(frontmatter dict, body) — `({}, text)` when there is no frontmatter,
    or on any parse error (fail-safe, never raises)."""
    try:
        if not text.startswith("---"):
            return {}, text
        lines = text.splitlines()
        if lines[0].strip() != "---":
            return {}, text
        end = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end = i
                break
        if end is None:
            return {}, text
        body = "\n".join(lines[end + 1:]).strip("\n")

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
            if val.startswith("[") and val.endswith("]"):
                inner = val[1:-1].strip()
                data[key] = [_unquote(x.strip()) for x in inner.split(",") if x.strip()]
            else:
                data[key] = _unquote(val)
            i += 1
        return data, body
    except Exception:
        return {}, text


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


# ── index builder ────────────────────────────────────────────────────────────

def _repo_root(root: Path | None) -> Path:
    if root is not None:
        return Path(root)
    return Path(__file__).resolve().parent.parent


def build_index(root: Path | None = None) -> dict[str, OpinionEntry]:
    """Scan `opinions/*.md` into a `name -> OpinionEntry` map.

    Returns `{}` on any I/O error or a missing `opinions/` dir (fail-safe —
    the resolver then reports `no-opinion`/`error` for every name, never
    raises). At most one base entry per name: sorted-filename order, first
    write wins on a duplicate `name:` (a malformed install, not a merge).
    """
    try:
        opinions_dir = _repo_root(root) / "opinions"
        if not opinions_dir.is_dir():
            return {}
        index: dict[str, OpinionEntry] = {}
        for p in sorted(opinions_dir.glob("*.md")):
            try:
                text = p.read_text(encoding="utf-8")
            except OSError:
                continue
            fm, body = _parse_frontmatter(text)
            if fm.get("kind") != "opinion":
                continue
            name = fm.get("name")
            if not name or name in index:
                continue
            index[name] = OpinionEntry(
                name=name,
                question=fm.get("question"),
                implements=fm.get("implements"),
                composes=_as_list(fm.get("composes")),
                serves=_as_list(fm.get("serves")),
                body=body,
                path=p,
            )
        return index
    except Exception:
        return {}


# ── the learned supplement (vault, read-only from here) ─────────────────────

def _read_supplement(name: str, *, supplement_dir: Path | None) -> str | None:
    """The learned supplement lives in the memory backend's `opinions/` area
    (an `opinions/<name>.md` entry, written by Experience over time) —
    resolved through the storage seam in the real deployment; `supplement_dir`
    is the injectable override tests use to avoid touching a real vault.
    Never raises: an absent/unreadable supplement is simply `None` (the
    resolver reports `base-only`, not an error)."""
    if supplement_dir is None:
        return None
    try:
        p = Path(supplement_dir) / f"{name}.md"
        if not p.is_file():
            return None
        _, body = _parse_frontmatter(p.read_text(encoding="utf-8"))
        return body.strip() or None
    except Exception:
        return None


# ── the resolver ──────────────────────────────────────────────────────────────

def opinion_resolve(name: str, *, root: Path | None = None,
                     supplement_dir: Path | None = None) -> dict:
    """Resolve `name` to its composite. Never raises — see module docstring
    for the four `reason` values."""
    try:
        index = build_index(root)
    except Exception:
        return {"name": name, "reason": "error", "base": None, "supplement": None,
                "question": None, "implements": None, "composes": []}

    entry = index.get(name)
    if entry is None:
        return {"name": name, "reason": "no-opinion", "base": None, "supplement": None,
                "question": None, "implements": None, "composes": []}

    supplement = _read_supplement(name, supplement_dir=supplement_dir)
    reason = "served" if supplement else "base-only"
    return {
        "name": name,
        "reason": reason,
        "base": entry.body,
        "supplement": supplement,
        "question": entry.question,
        "implements": entry.implements,
        "composes": entry.composes,
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="opinion_resolver.py",
        description="Resolve an Opinion name to its base+supplement composite.",
    )
    p.add_argument("name", help="the Opinion name, e.g. 'good'")
    p.add_argument("--root", default=None, help="repo root (default: this repo)")
    p.add_argument("--json", action="store_true", help="print the full result dict")
    return p


def main(argv: list[str] | None = None) -> int:
    ns = _build_parser().parse_args(argv)
    root = Path(ns.root) if ns.root else None
    result = opinion_resolve(ns.name, root=root)

    if ns.json:
        print(json.dumps(result, indent=2))
    elif result["reason"] in ("served", "base-only"):
        print(result["base"])

    if result["reason"] in ("served", "base-only"):
        return 0
    if result["reason"] in ("no-opinion", "error"):
        print(f"opinion_resolver: {result['reason']}: {ns.name!r}", file=sys.stderr)
        return 1
    return 2  # unreachable — every reason is one of the above


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
