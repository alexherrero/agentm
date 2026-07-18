#!/usr/bin/env python3
"""vault_lint — read-only lint over MemoryVault entries (V4 #33).

Validates every *agent-shaped* entry against the `save.py` frontmatter schema,
resolves `[[wikilinks]]` vault-wide, checks supersede integrity, and flags
schema drift. It is **strictly read-only** — it never writes to a memory entry
or mutates the vault. It surfaces candidate fixes for operator review (A3
permeable boundary, DC-1); auto-repair is V5-5.

Agent-shaped = a file whose YAML frontmatter carries the core trio
`kind` + `status` + `created` (DC-3). The operator's intermixed free-form
personal notes (no such frontmatter) are skipped, not flagged.

Schema source of truth is `save.py` — this module imports its validators
(`_validate_kebab`, `_GROUP_SEGMENT`, …) + `FRONTMATTER_FIELD_ORDER` /
`REQUIRED_FRONTMATTER_FIELDS`, so the two can't drift (DC-2).

v1 covers `save.py`-shaped entries + vault-wide wikilink resolution. The
idea-incubator `_summary.md` + `Ideas.md` bespoke shapes follow different
conventions and are skipped (DC-4) — see the `_EXCLUDE_DIRS` walk filter.

Stdlib-only. Cross-platform.

CLI:
    python3 vault_lint.py [--vault PATH] [--format json|text] [--scope SCOPE]
    # SCOPE ∈ {all, always-load, projects, personal}; default all.

(The `audit` report mode lands in V4 #33 task 2.)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import save  # noqa: E402  (schema source of truth — same skill dir)
import arc_registry  # noqa: E402  (2026-07-18 arc-as-metadata convention)

# Directories that are NOT memory-entry trees — skipped during the walk.
# `_idea-incubator` is deferred to a follow-up (DC-4, bespoke shape); `_meta`
# holds machine files (repos.json, cursors); `_harness` holds per-project plan
# state (PLAN.md/progress.md — not entries); `_inbox`/`_dream-staging` are
# transient staging areas. `_archive` (any depth) holds retired entries —
# recall.py and frontmatter_validator.py already skip it; L7 closes the gap
# where vault_lint.py was the one walker still descending into it.
_EXCLUDE_DIRS = frozenset({"_idea-incubator", "_meta", "_harness", "_inbox", "_dream-staging", "_archive"})

# Core frontmatter trio that marks a file as an agent-shaped entry (DC-3).
_CORE_TRIO = ("kind", "status", "created")

# Sanctioned anchor-file slugs (leading-underscore by convention) — project /
# domain index files + idea-incubator summaries. Exempt from the kebab-slug
# check; they're anchors, not regular save.py-created entries.
_ANCHOR_SLUGS = frozenset({"_index", "_summary"})

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

# --scope → directory subset under the vault root.
_SCOPE_DIRS = {
    "all": ["personal", "projects"],
    "always-load": ["personal/_always-load"],
    "projects": ["projects"],
    "personal": ["personal"],
}


# -----------------------------------------------------------------------------
# Data shapes
# -----------------------------------------------------------------------------

@dataclass
class Finding:
    check_id: str
    severity: str  # "error" | "warn" | "info"
    entry_path: str  # relative to vault root (POSIX)
    message: str
    suggestion: str

    def to_dict(self) -> dict:
        return {
            "check_id": self.check_id,
            "severity": self.severity,
            "entry_path": self.entry_path,
            "message": self.message,
            "suggestion": self.suggestion,
        }


@dataclass
class Entry:
    """A parsed agent-shaped entry."""
    path: Path
    rel: str            # POSIX path relative to vault root
    frontmatter: dict   # ordered: key -> raw string value
    fm_keys: list       # keys in file order
    body: str


@dataclass
class VaultModel:
    """The corpus the checks run against."""
    vault: Path
    entries: list = field(default_factory=list)      # list[Entry] — IN the lint scope
    skipped: int = 0                                  # non-entry files skipped (in scope)
    slugs: set = field(default_factory=set)           # linted-entry slugs
    by_slug: dict = field(default_factory=dict)       # slug -> Entry
    # Link-target index is VAULT-WIDE (every .md, incl. excluded-from-lint dirs
    # like _idea-incubator) — a wikilink to a real file must resolve even when
    # that file isn't itself linted. Excluding a dir from schema-linting ≠
    # excluding it as a valid link target.
    link_stems: set = field(default_factory=set)      # filename stems, vault-wide
    link_paths: set = field(default_factory=set)      # rel posix paths (no .md), vault-wide


# -----------------------------------------------------------------------------
# Minimal frontmatter parser (stdlib — no PyYAML per ADR 0001)
# -----------------------------------------------------------------------------

def parse_frontmatter(text: str) -> tuple[Optional[dict], list, str]:
    """Return (frontmatter dict | None, key-order list, body).

    Frontmatter is the leading `---\\n … \\n---` block. Returns (None, [], text)
    when absent. Values are kept as raw trimmed strings (the checks interpret
    them). Tags `[a, b]` and booleans stay as their raw string form.
    """
    if not text.startswith("---"):
        return None, [], text
    # Find the closing fence on its own line.
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, [], text
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None, [], text
    fm: dict = {}
    order: list = []
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        key = key.strip()
        if not key:
            continue
        fm[key] = value.strip()
        order.append(key)
    body = "\n".join(lines[end + 1:])
    return fm, order, body


# -----------------------------------------------------------------------------
# Corpus build
# -----------------------------------------------------------------------------

def _iter_md_files(vault: Path, scope: str):
    roots = _SCOPE_DIRS.get(scope, _SCOPE_DIRS["all"])
    for root_rel in roots:
        root = vault / root_rel
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune excluded dirs in-place so os.walk doesn't descend.
            dirnames[:] = [d for d in dirnames if d not in _EXCLUDE_DIRS]
            for fn in filenames:
                if fn.endswith(".md"):
                    yield Path(dirpath) / fn


def _obsidian_root(vault: Path) -> Path:
    """Return the enclosing Obsidian vault root (the dir containing `.obsidian/`),
    walking up from `vault` (bounded). Obsidian resolves wikilinks against the
    WHOLE vault, so an AgentMemory entry can link to a note outside AgentMemory
    (e.g. `Ideas.md` at the Obsidian root). Falls back to `vault` if no
    `.obsidian/` is found within 4 levels."""
    cur = vault.resolve()
    for _ in range(5):
        if (cur / ".obsidian").is_dir():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return vault


def _index_link_targets(vault: Path, model: VaultModel) -> None:
    """Index EVERY .md file in the enclosing Obsidian vault as a valid wikilink
    target (stem + relative path). Obsidian-root-wide — schema-linting stays
    AgentMemory-only, but link RESOLUTION must see the whole Obsidian vault so
    cross-vault references (e.g. `[[Ideas]]`) don't false-positive."""
    root = _obsidian_root(vault)
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip Obsidian's config dir; not a note source.
        dirnames[:] = [d for d in dirnames if d != ".obsidian"]
        for fn in filenames:
            if not fn.endswith(".md"):
                continue
            p = Path(dirpath) / fn
            model.link_stems.add(p.stem)
            try:
                model.link_paths.add(p.relative_to(root).with_suffix("").as_posix())
            except ValueError:
                pass


def build_model(vault: Path, scope: str = "all") -> VaultModel:
    """Walk the vault, parse agent-shaped entries, index slugs + link targets.
    Read-only."""
    vault = Path(vault)
    model = VaultModel(vault=vault)
    _index_link_targets(vault, model)
    for path in _iter_md_files(vault, scope):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm, order, body = parse_frontmatter(text)
        # Agent-entry gate (DC-3): require the core trio.
        if not fm or not all(k in fm for k in _CORE_TRIO):
            model.skipped += 1
            continue
        rel = path.relative_to(vault).as_posix()
        entry = Entry(path=path, rel=rel, frontmatter=fm, fm_keys=order, body=body)
        model.entries.append(entry)
        slug = fm.get("slug", "").strip() or path.stem
        model.slugs.add(slug)
        model.slugs.add(path.stem)  # also resolve by filename stem
        # Index by_slug by BOTH slug and stem — must match `slugs`'s key-space so
        # check_supersede's "still active" lookup resolves a stem reference too.
        model.by_slug.setdefault(slug, entry)
        model.by_slug.setdefault(path.stem, entry)
    return model


# -----------------------------------------------------------------------------
# Checks — each (entry, model) -> list[Finding]
# -----------------------------------------------------------------------------

def check_required_fields(entry: Entry, model: VaultModel) -> list:
    out = []
    for fname in save.REQUIRED_FRONTMATTER_FIELDS:
        if fname not in entry.frontmatter:
            out.append(Finding(
                "required-field", "error", entry.rel,
                f"missing required frontmatter field `{fname}`",
                f"add `{fname}: <value>` to the frontmatter (locked order: "
                f"{', '.join(save.FRONTMATTER_FIELD_ORDER)})",
            ))
    return out


def check_kebab_case(entry: Entry, model: VaultModel) -> list:
    out = []
    fm = entry.frontmatter
    for fname in ("kind", "slug"):
        val = fm.get(fname, "")
        if fname == "slug" and val in _ANCHOR_SLUGS:
            continue  # sanctioned anchor-file name (_index / _summary)
        if val and not save._KEBAB_SEGMENT.match(val):
            out.append(Finding(
                "kebab-case", "error", entry.rel,
                f"`{fname}: {val}` is not kebab-case (^[a-z0-9-]+$)",
                f"rename `{fname}` to kebab-case",
            ))
    grp = fm.get("group", "")
    if grp and not save._GROUP_SEGMENT.match(grp):
        out.append(Finding(
            "kebab-case", "error", entry.rel,
            f"`group: {grp}` is not a valid group path (^[a-z0-9-]+(/[a-z0-9-]+)*$)",
            "use one or more kebab-case segments joined by /",
        ))
    # tags: `[a, b, c]` → each kebab.
    tags_raw = fm.get("tags", "")
    for t in _parse_tags(tags_raw):
        if not save._KEBAB_SEGMENT.match(t):
            out.append(Finding(
                "kebab-case", "error", entry.rel,
                f"tag `{t}` is not kebab-case",
                f"rename tag `{t}` to kebab-case",
            ))
    return out


def check_field_order(entry: Entry, model: VaultModel) -> list:
    # The present known fields must appear in the locked relative order.
    known = [k for k in entry.fm_keys if k in save.FRONTMATTER_FIELD_ORDER]
    expected = [k for k in save.FRONTMATTER_FIELD_ORDER if k in known]
    if known != expected:
        return [Finding(
            "field-order", "warn", entry.rel,
            f"frontmatter fields out of locked order: {known} (expected {expected})",
            "reorder frontmatter to the locked order: "
            + ", ".join(save.FRONTMATTER_FIELD_ORDER),
        )]
    return []


def check_slug_filename(entry: Entry, model: VaultModel) -> list:
    slug = entry.frontmatter.get("slug", "").strip()
    if slug and slug != entry.path.stem:
        return [Finding(
            "slug-filename", "warn", entry.rel,
            f"`slug: {slug}` doesn't match filename stem `{entry.path.stem}`",
            f"rename the file to `{slug}.md` or fix the `slug` field",
        )]
    return []


def check_dates(entry: Entry, model: VaultModel) -> list:
    out = []
    fm = entry.frontmatter
    created = fm.get("created", "").strip()
    updated = fm.get("updated", "").strip()
    for fname, val in (("created", created), ("updated", updated)):
        if val and not _DATE_RE.match(val):
            out.append(Finding(
                "date-format", "error", entry.rel,
                f"`{fname}: {val}` is not a YYYY-MM-DD date",
                f"set `{fname}` to a YYYY-MM-DD date",
            ))
    if _DATE_RE.match(created) and _DATE_RE.match(updated) and updated < created:
        out.append(Finding(
            "date-format", "warn", entry.rel,
            f"`updated` ({updated}) is before `created` ({created})",
            "fix `updated` to be on or after `created`",
        ))
    return out


def check_placeholder_values(entry: Entry, model: VaultModel) -> list:
    # An unfilled template option-list left in frontmatter, e.g.
    # `status: active | resolved | superseded`.
    out = []
    for k, v in entry.frontmatter.items():
        if " | " in v:
            out.append(Finding(
                "placeholder-value", "warn", entry.rel,
                f"`{k}: {v}` looks like an unfilled template option-list (`a | b | c`)",
                f"replace `{k}` with the single chosen value",
            ))
    return out


def check_schema_drift(entry: Entry, model: VaultModel) -> list:
    out = []
    for k in entry.fm_keys:
        if k not in save.FRONTMATTER_FIELD_ORDER:
            out.append(Finding(
                "schema-drift", "warn", entry.rel,
                f"unknown frontmatter key `{k}` (not in the locked schema)",
                f"remove `{k}` or confirm it's an intentional schema addition",
            ))
    return out


def _wikilink_resolves(target: str, model: VaultModel) -> bool:
    """Obsidian-faithful resolution: a path-style `[[dir/name]]` resolves by
    exact relative path; a bare `[[name]]` resolves by filename stem anywhere
    in the vault. Strips an optional `.md` and a leading `#`/`^` anchor."""
    t = target.split("#", 1)[0].split("^", 1)[0].strip()
    t = t.replace("\\", "").strip().strip("/")  # drop markdown-escaped brackets (\]])
    if t.endswith(".md"):
        t = t[:-3]
    if not t:
        return True  # pure anchor link ([[#heading]]) — not a file ref
    if "/" in t:
        return t in model.link_paths or t.rsplit("/", 1)[-1] in model.link_stems
    return t in model.link_stems


def check_wikilinks(entry: Entry, model: VaultModel) -> list:
    out = []
    for m in _WIKILINK_RE.finditer(entry.body):
        target = m.group(1).split("|", 1)[0].strip()  # strip [[target|alias]]
        if not target:
            continue
        if not _wikilink_resolves(target, model):
            out.append(Finding(
                "wikilink-resolution", "error", entry.rel,
                f"wikilink `[[{target}]]` doesn't resolve to any file in the vault",
                f"fix the target, create the `{target}` note, or remove the link",
            ))
    return out


def check_supersede(entry: Entry, model: VaultModel) -> list:
    out = []
    sup = entry.frontmatter.get("supersedes", "").strip()
    if not sup:
        return out
    # `supersedes` may be a path or a slug; resolve by stem/slug.
    target_slug = Path(sup).stem if ("/" in sup or sup.endswith(".md")) else sup
    if target_slug not in model.slugs:
        out.append(Finding(
            "supersede-integrity", "error", entry.rel,
            f"`supersedes: {sup}` doesn't resolve to any entry",
            "fix the supersedes reference or remove it",
        ))
        return out
    target = model.by_slug.get(target_slug)
    if target is not None:
        tstatus = target.frontmatter.get("status", "").strip()
        if tstatus == "active":
            out.append(Finding(
                "supersede-integrity", "warn", entry.rel,
                f"supersedes `{target_slug}` but that entry's status is still `active`",
                f"set `{target_slug}`'s status to `superseded`",
            ))
    return out


def check_arc_registry(entry: Entry, model: VaultModel) -> list:
    # arc: is optional (most entries carry none) — only checked when present.
    arc = entry.frontmatter.get("arc", "").strip()
    if not arc:
        return []
    if not arc_registry.is_kebab(arc):
        return [Finding(
            "arc-registry", "error", entry.rel,
            f"`arc: {arc}` is not kebab-case (^[a-z0-9-]+$)",
            "rename `arc` to kebab-case",
        )]
    if not arc_registry.is_known(arc):
        return [Finding(
            "arc-registry", "error", entry.rel,
            f"`arc: {arc}` is not a recognized arc slug",
            f"add `{arc}` to arc_registry.py's KNOWN_ARCS, or fix the typo "
            "against an existing arc slug",
        )]
    return []


CHECKS = (
    check_required_fields,
    check_kebab_case,
    check_field_order,
    check_slug_filename,
    check_dates,
    check_placeholder_values,
    check_schema_drift,
    check_wikilinks,
    check_supersede,
    check_arc_registry,
)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _parse_tags(raw: str) -> list:
    raw = raw.strip()
    if not raw or raw == "[]":
        return []
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    return [t.strip() for t in raw.split(",") if t.strip()]


def lint_model(model: VaultModel) -> list:
    """Run every check over every entry. Returns a flat list[Finding]."""
    findings = []
    for entry in model.entries:
        for chk in CHECKS:
            findings.extend(chk(entry, model))
    return findings


def lint_vault(vault: Path, scope: str = "all") -> tuple[VaultModel, list]:
    model = build_model(vault, scope)
    return model, lint_model(model)


# -----------------------------------------------------------------------------
# Audit report (task 2) — group findings into a skimmable operator-review doc
# -----------------------------------------------------------------------------

_SEV_ORDER = ("error", "warn", "info")


def _counts(findings: list) -> tuple[int, int, int]:
    return (
        sum(1 for f in findings if f.severity == "error"),
        sum(1 for f in findings if f.severity == "warn"),
        sum(1 for f in findings if f.severity == "info"),
    )


def build_report(model: VaultModel, findings: list, *, today: str) -> str:
    """Render an operator-review markdown audit report. Findings are grouped by
    severity → check → identical-message, so high-count patterns (e.g. an
    unknown `domain` key across 30 entries) collapse to one line + an entry
    list rather than 30 repeated lines. Suggestions are advisory — nothing is
    applied."""
    errs, warns, infos = _counts(findings)
    out = [
        f"# MemoryVault lint audit — {today}",
        "",
        f"**Summary:** {errs} error · {warns} warn · {infos} info across "
        f"{len(model.entries)} entries ({model.skipped} non-entry file(s) skipped).",
        "",
        "> Read-only audit. Each finding has a suggested fix — apply at your "
        "discretion; nothing here was changed automatically.",
        "",
    ]
    if not findings:
        out.append("Clean — no findings. 🎉")
        return "\n".join(out) + "\n"

    sev_label = {"error": "Errors", "warn": "Warnings", "info": "Info"}
    for sev in _SEV_ORDER:
        sev_findings = [f for f in findings if f.severity == sev]
        if not sev_findings:
            continue
        out.append(f"## {sev_label[sev]} ({len(sev_findings)})")
        out.append("")
        # Group by check_id (stable order of first appearance).
        checks: dict = {}
        for f in sev_findings:
            checks.setdefault(f.check_id, []).append(f)
        for check_id, items in checks.items():
            out.append(f"### `{check_id}` ({len(items)})")
            # Sub-group by identical message → collapse repeats.
            by_msg: dict = {}
            for f in items:
                by_msg.setdefault(f.message, []).append(f)
            for msg, group in by_msg.items():
                if len(group) == 1:
                    g = group[0]
                    out.append(f"- `{g.entry_path}` — {g.message}")
                    out.append(f"  → {g.suggestion}")
                else:
                    out.append(f"- **{len(group)}×** {msg}")
                    out.append(f"  → {group[0].suggestion}")
                    paths = [g.entry_path for g in group]
                    shown = ", ".join(f"`{p}`" for p in paths[:8])
                    more = f" (+{len(paths) - 8} more)" if len(paths) > 8 else ""
                    out.append(f"  Entries: {shown}{more}")
            out.append("")
    return "\n".join(out) + "\n"


def default_report_path(vault: Path, today: str) -> Path:
    return vault / "_meta" / f"vault-lint-{today}.md"


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _resolve_vault(arg: Optional[str]) -> Path:
    if arg:
        return Path(arg).expanduser()
    env = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if env:
        return Path(env).expanduser()
    raise FileNotFoundError("no vault path — set --vault or MEMORY_VAULT_PATH")


def _render_text(model: VaultModel, findings: list) -> str:
    errs = sum(1 for f in findings if f.severity == "error")
    warns = sum(1 for f in findings if f.severity == "warn")
    infos = sum(1 for f in findings if f.severity == "info")
    out = [
        f"vault-lint: {errs} error · {warns} warn · {infos} info "
        f"across {len(model.entries)} entries ({model.skipped} non-entry files skipped)",
        "",
    ]
    for f in findings:
        out.append(f"  [{f.severity}] {f.check_id}  {f.entry_path}")
        out.append(f"      {f.message}")
        out.append(f"      -> {f.suggestion}")
    if not findings:
        out.append("  clean — no findings.")
    return "\n".join(out) + "\n"


def main(argv: Optional[list] = None) -> int:
    # Windows stdout defaults to cp1252, which can't encode `→` etc. Force UTF-8
    # (best-effort) so the CLI's stdout never UnicodeEncodeErrors. The report
    # FILE is already written encoding="utf-8" — this is only for stdout.
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    p = argparse.ArgumentParser(prog="vault_lint", description="Read-only MemoryVault lint (V4 #33).")
    p.add_argument("--vault", default=None, help="vault root (else MEMORY_VAULT_PATH)")
    p.add_argument("--format", choices=("json", "text"), default="text")
    p.add_argument("--scope", choices=tuple(_SCOPE_DIRS), default="all")
    p.add_argument("--audit", action="store_true",
                   help="write a grouped operator-review report (to --out or <vault>/_meta/vault-lint-<date>.md)")
    p.add_argument("--out", default=None, help="audit report output path (with --audit)")
    p.add_argument("--check-freshness", action="store_true",
                   help="report the vec-index freshness ratio (R1.4) — surfaces a dead drain "
                        "(agentmExperience#0) without a full lint pass")
    args = p.parse_args(argv)
    try:
        vault = _resolve_vault(args.vault)
    except FileNotFoundError as e:
        print(f"vault_lint: {e}", file=sys.stderr)
        return 2
    if not vault.is_dir():
        print(f"vault_lint: vault not found: {vault}", file=sys.stderr)
        return 2

    if args.check_freshness:
        # Same directory as vec_index.py (the memory skill's scripts/); a bare
        # import resolves via sys.path[0] set to this file's directory,
        # matching every other same-dir import already in this skill.
        import vec_index
        inv = vec_index.find_drifted_entries(vault)
        up_to_date, drifted, not_indexed = len(inv["up_to_date"]), len(inv["drifted"]), len(inv["not_indexed"])
        total = up_to_date + drifted + not_indexed
        ratio = (up_to_date / total) if total else 1.0
        if args.format == "json":
            print(json.dumps({
                "up_to_date": up_to_date, "drifted": drifted, "not_indexed": not_indexed,
                "ratio": ratio,
            }))
        else:
            print(f"vault-lint freshness: {up_to_date}/{total} up-to-date (ratio {ratio:.2f}) "
                  f"— {drifted} drifted, {not_indexed} not-indexed")
            if total and ratio < 0.8:
                print("  WARN: freshness below floor (0.80) — the vec-index drain may be dead. "
                      "Run `python3 vec_index.py full-sync --rebuild` then `... drain` to catch up.")
        # Advisory, like the rest of this tool (exit 0 always) — the printed
        # WARN is the visibility fix; this never fails a build on its own.
        return 0

    model, findings = lint_vault(vault, args.scope)

    if args.audit:
        today = date.today().isoformat()
        report = build_report(model, findings, today=today)
        out_path = Path(args.out).expanduser() if args.out else default_report_path(vault, today)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        errs, warns, infos = _counts(findings)
        print(f"vault-lint audit: {errs} error / {warns} warn / {infos} info "
              f"across {len(model.entries)} entries -> {out_path}")
        return 0

    if args.format == "json":
        print(json.dumps({
            "entries": len(model.entries),
            "skipped": model.skipped,
            "findings": [f.to_dict() for f in findings],
        }, indent=2, ensure_ascii=False))
    else:
        print(_render_text(model, findings), end="")
    # Exit 0 always — the lint reports; it never fails a build (it's advisory).
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
