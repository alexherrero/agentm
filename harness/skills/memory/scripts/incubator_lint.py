#!/usr/bin/env python3
"""incubator_lint — read-only lint for the two bespoke idea-ledger shapes
(agentm #278, closing the V4 #33 DC-4 deferral).

`vault_lint.py` validates *agent-shaped* entries against the `save.py`
frontmatter schema. The idea ledger deliberately doesn't use that schema, so
DC-4 skipped it and left a follow-up. This module is that follow-up: the two
bespoke shapes get their own rules rather than being forced into the
save.py-shaped ones.

Two surfaces, both read-only:

1. **`_idea-incubator/<slug>/`** (Tier 2) — four file roles, keyed off the
   filename, each with its own `kind`:

       _index.md      kind: idea-incubator            (the required anchor)
       _summary.md    kind: idea-incubator-summary    (operator-facing distillation)
       research-*.md  kind: idea-incubator-research
       runbook-*.md   kind: idea-incubator-runbook

   Every one of them carries the same five-field core — `kind` · `status` ·
   `slug` · `created` · `updated` — and nothing else is universal. Notably
   *absent*: `tags` and `group`, which `save.py` requires and most incubator
   files simply don't have.

2. **`Ideas.md`** (Tier 1) — a heading index living at the *Obsidian* root,
   outside the vault. It has no frontmatter at all, by design, so nothing
   here checks any. Entries are `## YYYY-MM-DD: <Title>`, with a
   strikethrough form for dismissed ones.

Why these rules and not the documented ones — the corpus is the source of
truth here, per the issue's own instruction. Two places where the shipped
docs describe something the real files don't do:

* `idea-incubator-summary-doc.md` (an always-load convention) prescribes a
  five-section `_summary.md`: Research scope / Key findings / Recommendations
  / Open questions / Confidence level. Exactly one of the five real summaries
  uses it. The other four are free-form prose under an `# … — operator
  summary` H1. Linting the five-section shape would flag 4 of 5 real files,
  so this module does not check body structure at all. The prescription
  reads as aspirational, not as the contract.
* `ideas_incubator.py`, the skeleton generator, writes `kind: idea` /
  `status: incubating` / `research_budget_wall_time_sec` into
  `personal/_idea-incubator/`. No real file uses any of that: the live tree
  is at the *vault root*, uses `kind: idea-incubator`, and spells the budget
  keys `research_budget_seconds` / `_fetches` / `_tokens`. The rules below
  follow the files. (The generator drift is real and worth its own fix; it
  is deliberately not fixed here — this module is read-only and does not
  rewrite history in the ledger.)

The `_idea-incubator` entry in `vault_lint._EXCLUDE_DIRS` (and dream.py's,
and frontmatter_validator.py's) is deliberately left in place. That exclusion
means "not a save.py-shaped entry tree," which is still exactly true — these
checks are a *separate pass* over a separate root, not a re-admission into
the generic one. Removing it from one of the three lists would also break
test_vault_lint.py's three-way parity pins.

Stdlib-only. Cross-platform. Never writes to any file it reads.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from vault_lint import Finding, parse_frontmatter  # noqa: E402  (same skill dir)

# The directory name the ledger lives under. Searched for at the vault root
# AND one level down: the live vault keeps it at the root, while
# ideas_incubator.py still writes to `personal/_idea-incubator`, so both
# layouts have to resolve.
INCUBATOR_DIRNAME = "_idea-incubator"

# `kind` values, keyed by the file role the filename declares. Every real
# file in the ledger matches one of these four roles.
_KIND_BY_ANCHOR = {
    "_index": "idea-incubator",
    "_summary": "idea-incubator-summary",
}
_KIND_BY_PREFIX = (
    ("research-", "idea-incubator-research"),
    ("runbook-", "idea-incubator-runbook"),
)

# The five fields every incubator file carries. Deliberately NOT save.py's
# REQUIRED_FRONTMATTER_FIELDS — `tags` and `group` are absent from most of
# the real corpus and are not part of this shape's contract.
CORE_FIELDS = ("kind", "status", "slug", "created", "updated")

# Observed `status` values. Checked as a warn, not an error: the ledger is a
# thinking surface and a genuinely new state is a plausible addition, not a
# defect. Extend as the ledger grows.
KNOWN_STATUSES = frozenset({
    "research-pending", "research-partial", "research-complete",
    "promoted-to-design", "deprioritized", "spec-ready",
})

# Statuses meaning the idea has left the incubator. Exempt from the
# "every dir needs a _summary.md" rule: the convention's stated purpose is
# to show future readers where the summary will land, and for an idea that
# already graduated to a design there is no landing left to signpost.
TERMINAL_STATUSES = frozenset({"promoted-to-design"})

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

# `## 2026-05-20: Some title`
_IDEAS_HEADING_RE = re.compile(r"^\d{4}-\d{2}-\d{2}:\s+\S")
# `## ~~2026-05-20: Some title~~ — Dismissed 2026-05-24` — the deliberate
# strikethrough form for a retired entry. Accepted, not flagged.
_IDEAS_DISMISSED_RE = re.compile(r"^~~\d{4}-\d{2}-\d{2}:\s+.*~~")


# -----------------------------------------------------------------------------
# Discovery
# -----------------------------------------------------------------------------

def find_incubator_roots(vault: Path) -> list:
    """Return every `_idea-incubator/` root under `vault` (root level first,
    then one level down). Both layouts are live — see INCUBATOR_DIRNAME."""
    roots = []
    top = vault / INCUBATOR_DIRNAME
    if top.is_dir():
        roots.append(top)
    try:
        children = sorted(p for p in vault.iterdir() if p.is_dir())
    except OSError:
        return roots
    for child in children:
        if child.name == INCUBATOR_DIRNAME:
            continue
        nested = child / INCUBATOR_DIRNAME
        if nested.is_dir():
            roots.append(nested)
    return roots


def _expected_kind(stem: str) -> Optional[str]:
    if stem in _KIND_BY_ANCHOR:
        return _KIND_BY_ANCHOR[stem]
    for prefix, kind in _KIND_BY_PREFIX:
        if stem.startswith(prefix):
            return kind
    return None


def resolve_ideas_path(vault: Path, arg_path: Optional[str] = None) -> Optional[Path]:
    """Ideas.md location: arg → $IDEAS_SURFACE_PATH → the vault's parent.

    Mirrors `ideas_surface._resolve_ideas_path`; returns None when the file
    doesn't exist, because a vault with no Tier-1 surface is a valid setup
    and must not produce findings. Never caches a path literal.

    The parent-directory fallback is gated on that parent actually being the
    enclosing Obsidian vault (it contains `.obsidian/`). Ideas.md genuinely
    lives OUTSIDE the memory vault, so the fallback has to reach up a level —
    but reaching up unconditionally means a vault under, say, the system temp
    dir would adopt any unrelated `Ideas.md` sitting beside it as its Tier-1
    surface. Every scratch-vault test and verify-* script creates exactly that
    shape, and one stray fixture file then leaks into all of them. An explicit
    arg or $IDEAS_SURFACE_PATH is an operator statement and needs no such
    corroboration.
    """
    if arg_path:
        p = Path(arg_path).expanduser()
        return p if p.is_file() else None
    env = os.environ.get("IDEAS_SURFACE_PATH", "").strip()
    if env:
        p = Path(env).expanduser()
        return p if p.is_file() else None
    parent = Path(vault).parent
    if not (parent / ".obsidian").is_dir():
        return None
    p = parent / "Ideas.md"
    return p if p.is_file() else None


# -----------------------------------------------------------------------------
# Per-file checks
# -----------------------------------------------------------------------------

def _check_file(path: Path, rel: str, fm: Optional[dict]) -> list:
    out = []
    if fm is None:
        return [Finding(
            "incubator-frontmatter", "error", rel,
            "no frontmatter block — every idea-incubator file carries one",
            "add a frontmatter block with " + ", ".join(f"`{f}`" for f in CORE_FIELDS),
        )]

    for fname in CORE_FIELDS:
        if fname not in fm:
            out.append(Finding(
                "incubator-core-field", "error", rel,
                f"missing required frontmatter field `{fname}`",
                f"add `{fname}: <value>` (the incubator core is "
                + ", ".join(CORE_FIELDS) + ")",
            ))

    kind = fm.get("kind", "").strip()
    expected = _expected_kind(path.stem)
    if expected is None:
        out.append(Finding(
            "incubator-file-role", "warn", rel,
            f"filename `{path.name}` matches no known incubator role "
            "(_index / _summary / research-* / runbook-*)",
            "rename the file to one of the four roles, or add the new role to "
            "incubator_lint.py's _KIND_BY_ANCHOR / _KIND_BY_PREFIX",
        ))
    elif kind and kind != expected:
        out.append(Finding(
            "incubator-kind-role", "error", rel,
            f"`kind: {kind}` doesn't match the file role — `{path.name}` "
            f"must be `{expected}`",
            f"set `kind: {expected}`, or rename the file to match `{kind}`",
        ))

    status = fm.get("status", "").strip()
    if status and status not in KNOWN_STATUSES:
        out.append(Finding(
            "incubator-status", "warn", rel,
            f"`status: {status}` is not a recognized incubator status",
            "use one of " + ", ".join(sorted(KNOWN_STATUSES))
            + ", or add this one to incubator_lint.py's KNOWN_STATUSES if it's "
              "a genuine new state",
        ))

    created = fm.get("created", "").strip()
    updated = fm.get("updated", "").strip()
    for fname, val in (("created", created), ("updated", updated)):
        if val and not _DATE_RE.match(val):
            out.append(Finding(
                "incubator-date", "error", rel,
                f"`{fname}: {val}` is not a YYYY-MM-DD date",
                f"set `{fname}` to a YYYY-MM-DD date",
            ))
    if _DATE_RE.match(created) and _DATE_RE.match(updated) and updated < created:
        out.append(Finding(
            "incubator-date", "warn", rel,
            f"`updated` ({updated}) is before `created` ({created})",
            "fix `updated` to be on or after `created`",
        ))

    # research-*.md / runbook-*.md point back at their incubator by dir name.
    if expected in ("idea-incubator-research", "idea-incubator-runbook"):
        back = fm.get("incubator", "").strip()
        dirname = path.parent.name
        if not back:
            out.append(Finding(
                "incubator-backref", "error", rel,
                "missing `incubator:` back-reference",
                f"add `incubator: {dirname}`",
            ))
        elif back != dirname:
            out.append(Finding(
                "incubator-backref", "error", rel,
                f"`incubator: {back}` doesn't match the enclosing directory "
                f"`{dirname}`",
                f"set `incubator: {dirname}`, or move the file under `{back}/`",
            ))
    return out


def _check_dir(idea_dir: Path, rel_of, files: dict) -> list:
    """Cross-file checks within one `<slug>/` directory."""
    out = []
    name = idea_dir.name
    index_fm = files.get("_index")
    summary_fm = files.get("_summary")

    if index_fm is None:
        out.append(Finding(
            "incubator-anchor", "error", rel_of(idea_dir),
            "incubator directory has no `_index.md` anchor",
            f"add `_index.md` with `kind: idea-incubator` and `slug: {name}`",
        ))

    index_status = (index_fm or {}).get("status", "").strip()

    if summary_fm is None:
        if index_status not in TERMINAL_STATUSES:
            out.append(Finding(
                "incubator-summary-missing", "warn", rel_of(idea_dir),
                "no `_summary.md` — the idea-incubator-summary-doc convention "
                "requires one in every incubator dir, as a research-pending "
                "placeholder until research completes",
                # Deliberately slug-free: build_report collapses findings that
                # share a message and then prints only the first one's
                # suggestion, so a slug-specific string here would show one
                # directory's slug as the fix for all of them.
                "add `_summary.md` with `kind: idea-incubator-summary`, the "
                "incubator's own slug, and the current status",
            ))
    elif index_fm is not None:
        i_slug = index_fm.get("slug", "").strip()
        s_slug = summary_fm.get("slug", "").strip()
        if i_slug and s_slug and i_slug != s_slug:
            out.append(Finding(
                "incubator-slug-agreement", "error", rel_of(idea_dir / "_summary.md"),
                f"`slug: {s_slug}` disagrees with `_index.md`'s `slug: {i_slug}`",
                f"set both to the same slug (the directory is `{name}`)",
            ))
        s_status = summary_fm.get("status", "").strip()
        if index_status and s_status and index_status != s_status:
            out.append(Finding(
                "incubator-status-agreement", "warn", rel_of(idea_dir / "_summary.md"),
                f"`status: {s_status}` disagrees with `_index.md`'s "
                f"`status: {index_status}`",
                "reconcile the two — usually `_index.md` is the stale one, since "
                "research updates the summary first",
            ))
    return out


# -----------------------------------------------------------------------------
# Ideas.md (Tier 1)
# -----------------------------------------------------------------------------

def check_ideas_surface(ideas_path: Path, resolves) -> list:
    """Lint the Tier-1 `Ideas.md` heading index. No frontmatter check — the
    file deliberately has none."""
    out = []
    try:
        text = ideas_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [Finding(
            "ideas-unreadable", "error", ideas_path.name,
            f"could not read the Ideas surface: {exc}",
            "check the path resolved by $IDEAS_SURFACE_PATH",
        )]

    rel = ideas_path.name
    for heading in re.findall(r"^##\s+(.*)$", text, re.M):
        h = heading.strip()
        if _IDEAS_HEADING_RE.match(h) or _IDEAS_DISMISSED_RE.match(h):
            continue
        out.append(Finding(
            "ideas-heading", "warn", rel,
            f"entry heading `## {h[:60]}` isn't `## YYYY-MM-DD: <Title>`",
            "rename the heading to `## YYYY-MM-DD: <Title>` (or the "
            "`## ~~YYYY-MM-DD: <Title>~~ — Dismissed YYYY-MM-DD` form)",
        ))

    for m in _WIKILINK_RE.finditer(text):
        target = m.group(1).split("|", 1)[0].strip()
        if INCUBATOR_DIRNAME not in target:
            continue
        if not resolves(target):
            out.append(Finding(
                "ideas-incubator-link", "error", rel,
                f"link `[[{target}]]` doesn't resolve to any incubator file",
                "fix the target, or create the note it points at",
            ))
    return out


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------

def lint_incubator(vault: Path, resolves, *, ideas_path: Optional[str] = None) -> tuple:
    """Lint the idea ledger. Returns `(files_checked, findings)`.

    `resolves(target) -> bool` is the wikilink resolver, injected so this
    module reuses vault_lint's Obsidian-root-wide (and alias-aware) link
    index rather than rebuilding one.
    """
    vault = Path(vault)
    findings = []
    checked = 0

    for root in find_incubator_roots(vault):
        def rel_of(p: Path) -> str:
            try:
                return p.relative_to(vault).as_posix()
            except ValueError:
                return p.as_posix()

        for idea_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            per_role: dict = {}
            for md in sorted(idea_dir.rglob("*.md")):
                try:
                    text = md.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                checked += 1
                fm, _order, body = parse_frontmatter(text)
                rel = rel_of(md)
                findings.extend(_check_file(md, rel, fm))
                if fm is not None and md.parent == idea_dir:
                    per_role[md.stem] = fm
                for m in _WIKILINK_RE.finditer(body if fm is not None else text):
                    target = m.group(1).split("|", 1)[0].strip()
                    if not target:
                        continue
                    if not resolves(target):
                        findings.append(Finding(
                            "incubator-wikilink", "error", rel,
                            f"wikilink `[[{target}]]` doesn't resolve to any file "
                            "in the vault",
                            f"fix the target, create the `{target}` note, or "
                            "remove the link",
                        ))
            findings.extend(_check_dir(idea_dir, rel_of, per_role))

    ideas = resolve_ideas_path(vault, ideas_path)
    if ideas is not None:
        checked += 1
        findings.extend(check_ideas_surface(ideas, resolves))

    return checked, findings
