#!/usr/bin/env python3
"""Structural lints for the documentation convention (the crickets conventions `documentation` domain).

Walks a wiki tree and enforces the mode-based layout + per-mode page
discipline of the six-section frame (How-to · Reference · Architecture ·
Designs · Explanation · Operational).

Rules (hard = blocking under --strict; soft = always warn-only):

  (a) Every content .md lives under one of the taxonomy folders
      (how-to/, reference/, architecture/, designs/, explanation/,
      operational/). Structural pages (Home.md,
      _Sidebar.md, _Footer.md, README.md) are exempt.         [hard]
  (b) Tutorials and how-tos have a `> [!NOTE]` mode-declaration block
      in the first 25 lines with the required fields:
        tutorial: Goal, Time, Prereqs
        how-to:   Goal, Prereqs                               [hard]
  (c) Tutorials have a `## What you learned` H2 and ≥1 numbered-step
      H2 (e.g. `## Step 1 — ...` or `## 1. ...`).             [hard]
  (d) How-tos have a `## Steps` H2 or an inline numbered list, and no
      heading named Rationale / Why / Background / Context.   [hard]
  (e) Reference pages open with either a `## ⚡ Quick Reference`
      block, a table, or a fenced code block within 25 lines of the
      H1.                                                     [hard]
  (g) Filenames match `[A-Za-z0-9][A-Za-z0-9-]*\\.md` and basenames
      are globally unique across the wiki.                    [hard]
  (h) Every wiki-internal markdown link resolves to a known page
      (by basename — the GitHub Wiki URL convention).         [hard]
  (i) Orphan check: tutorials link to ≥1 reference or how-to;
      how-tos link to ≥1 reference; reference pages are linked from
      ≥1 other page (structural pages count).                 [hard]
  (j) _Sidebar.md references every non-structural content page — it is the
      complete sitemap (and, being present on every wiki page, the orphan
      guarantee). Home.md is CURATED: it is NOT required to list every page;
      completeness lives in the sidebar.                      [hard]
  (k) Word-count ceilings:
        tutorial ≤ 1200 words
        how-to   ≤ 600 words
        explanation ≤ 2000 words
        reference: unbounded here (rule p adds a prose-word ceiling)  [soft]
  (l) The repo-root README.md, governed as a wiki-adjacent page when
      include_readme is on (the default): every relative-path markdown
      link resolves to an existing file under the repo root. Opt out
      with --no-readme or {"include_readme": false} in
      <repo-root>/.diataxis.json.                             [hard]
  (p) Shape axis — a reference page should be lookup-shaped, an explanation
      page should read as prose. Warns when an explanation is table-dominated
      (a fact-dump; fenced diagrams / code don't count) or a reference is mostly
      prose / over a prose-word ceiling. The combined plugin-reference hybrid
      (## Architecture + ## Reference) is exempt.                       [soft]

Usage:
  python3 scripts/check-wiki.py               # warn, exit 0
  python3 scripts/check-wiki.py --strict      # exit 1 if any hard issue
  python3 scripts/check-wiki.py --root PATH   # check a different wiki
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# cwd-relative default: this gate is bundled in the wiki-maintenance plugin and
# runs from an arbitrary repo's root, so its own file location says nothing about
# the repo under check. --root overrides; repo_root (for rule l) is wiki_root.parent.
DEFAULT_WIKI = Path("wiki")

_FOLDER_MODE = {
    "how-to": "how-to",
    "reference": "reference",
    "architecture": "index",
    "designs": "explanation",
    "explanation": "explanation",
    "operational": "how-to",
}
MODE_DIRS = tuple(_FOLDER_MODE)
STRUCTURAL_BASENAMES = {"Home", "_Sidebar", "_Footer", "README"}
WORD_CAPS = {"tutorial": 1200, "how-to": 600, "explanation": 2000}
BANNED_HOWTO_HEADINGS = {"rationale", "why", "background", "context"}

# Shape axis (rule p) — soft warns. A reference page should be lookup-shaped
# (tables / quick-ref); an explanation page should read as prose. Thresholds are
# calibrated against the live wikis so only a genuine misfile warns.
REFERENCE_WORD_CAP = 900        # prose words on a reference page (word_count already excludes tables/fences)
EXPLANATION_TABLE_MAX = 0.50    # explanation with >50% table lines (of table+prose) → warn (fenced diagrams/code don't count against it)
REFERENCE_LOOKUP_MIN = 0.12     # reference with <12% table/code lines → warn (reference-as-narrative)
SHAPE_MIN_LINES = 12            # skip pages too small to judge a shape

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
NOTE_BLOCK_START_RE = re.compile(r"^>\s*\[!NOTE\]\s*$", re.IGNORECASE)
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
FENCE_RE = re.compile(r"^\s*```")
FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*\.md$")
NUMBERED_STEP_TITLE_RE = re.compile(r"^(Step\s+\d+|\d+\.)", re.IGNORECASE)
WHAT_LEARNED_RE = re.compile(r"^what\s+you('ve)?\s*learned", re.IGNORECASE)
AMENDMENT_DATE_RE = re.compile(r"^Amendment\s+(\d{4}-\d{2}-\d{2})\s*$", re.IGNORECASE)
STATUS_RE = re.compile(r"\*\*Status:\*\*\s*(\w+)", re.IGNORECASE)
MODE_HINT_RE = re.compile(r"<!--\s*mode:\s*(tutorial|how-to|reference|explanation|index)\s*-->", re.IGNORECASE)


@dataclass
class Issue:
    path: Path
    line: int
    rule: str
    message: str
    severity: str  # "hard" | "soft"


def emit(issues: list[Issue], path: Path, line: int, rule: str, msg: str,
         soft: bool = False) -> None:
    issues.append(Issue(path, line, rule, msg, "soft" if soft else "hard"))


# ── classification ─────────────────────────────────────────────────────────

def is_structural(path: Path) -> bool:
    return path.stem in STRUCTURAL_BASENAMES


def mode_for(path: Path, wiki_root: Path) -> str | None:
    """Return the page's Diátaxis mode, or None if structural / misfiled."""
    if is_structural(path):
        return None
    try:
        rel = path.relative_to(wiki_root)
    except ValueError:
        return None
    if not rel.parts:
        return None
    return _FOLDER_MODE.get(rel.parts[0])


def resolve_mode(path: Path, wiki_root: Path) -> str | None:
    """Page mode: an explicit `<!-- mode: X -->` hint wins (for a page whose folder
    default doesn't fit — a tutorial in how-to/, a reference table in explanation/);
    otherwise the folder default (mode_for)."""
    if is_structural(path):
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return mode_for(path, wiki_root)
    m = MODE_HINT_RE.search(text)
    return m.group(1).lower() if m else mode_for(path, wiki_root)


# ── parsing helpers ────────────────────────────────────────────────────────

def parse_headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """Return (lineno, level, title) for each heading, skipping code fences."""
    heads: list[tuple[int, int, str]] = []
    in_fence = False
    for i, line in enumerate(lines, 1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = HEADING_RE.match(line)
        if m:
            heads.append((i, len(m.group(1)), m.group(2).strip()))
    return heads


def find_note_block(lines: list[str], window: int = 25) -> tuple[int, str] | None:
    """Return (start_lineno, block_body) for the first > [!NOTE] block
    that starts in the first `window` lines, or None.
    """
    for i, line in enumerate(lines[:window], 1):
        if NOTE_BLOCK_START_RE.match(line):
            body_lines = [line]
            for nxt in lines[i:]:
                if nxt.startswith(">"):
                    body_lines.append(nxt)
                else:
                    break
            return i, "\n".join(body_lines)
    return None


def extract_wiki_links(text: str) -> list[tuple[int, str, str]]:
    """Yield (lineno, label, href) for every markdown link that looks like
    a wiki-internal page reference (no slash, no scheme)."""
    out: list[tuple[int, str, str]] = []
    for m in LINK_RE.finditer(text):
        href = m.group(2)
        if "/" in href or href.startswith(("http://", "https://", "#", "mailto:")):
            continue
        line_no = text[:m.start()].count("\n") + 1
        page = href.split("#", 1)[0]
        if page.endswith(".md"):
            page = page[:-3]
        if page:
            out.append((line_no, m.group(1), page))
    return out


def content_shape(lines: list[str]) -> tuple[int, int, int]:
    """(table_lines, fence_lines, prose_lines), ignoring blanks and headings.
    Tables are lookup; fenced blocks are code or diagrams (neutral — an
    explanation may legitimately illustrate with them); everything else is prose."""
    table = fence = prose = 0
    in_fence = False
    for line in lines:
        if FENCE_RE.match(line):
            in_fence = not in_fence
            fence += 1
            continue
        if in_fence:
            fence += 1
            continue
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if TABLE_ROW_RE.match(line):
            table += 1
        else:
            prose += 1
    return table, fence, prose


def is_combined_plugin_page(heads: list[tuple[int, int, str]]) -> bool:
    """The combined plugin-reference shape — a reference page with both an
    ## Architecture and a ## Reference H2 (prose context + lookup tables). A
    deliberate prose+table hybrid, exempt from the reference shape warnings."""
    h2s = {title.strip().lower() for _ln, lvl, title in heads if lvl == 2}
    return {"architecture", "reference"} <= h2s


def word_count(text: str) -> int:
    """Rough word count, ignoring fenced code blocks and table rows."""
    kept: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence or TABLE_ROW_RE.match(line):
            continue
        kept.append(line)
    stripped = re.sub(r"[`*_\[\]()<>#|]", " ", " ".join(kept))
    return len(stripped.split())


# ── rules ──────────────────────────────────────────────────────────────────

def rule_a_location(p: Path, wiki_root: Path, issues: list[Issue]) -> None:
    if is_structural(p):
        return
    try:
        rel = p.relative_to(wiki_root)
    except ValueError:
        return
    if not rel.parts or rel.parts[0] not in MODE_DIRS:
        top = rel.parts[0] if rel.parts else "(root)"
        emit(issues, p, 1, "a",
             f"page is not under a mode dir ({'/'.join(MODE_DIRS)}); "
             f"is under '{top}'")


def rule_b_mode_block(p: Path, mode: str | None, lines: list[str],
                      issues: list[Issue]) -> None:
    if mode not in ("tutorial", "how-to"):
        return
    blk = find_note_block(lines)
    if blk is None:
        emit(issues, p, 1, "b",
             f"{mode} page missing `> [!NOTE]` mode block in first 25 lines")
        return
    start, body = blk
    required = ("Goal", "Time", "Prereqs") if mode == "tutorial" else ("Goal", "Prereqs")
    for field in required:
        if f"**{field}:**" not in body:
            emit(issues, p, start, "b",
                 f"{mode} mode block missing required field `**{field}:**`")


def rule_c_tutorial_shape(p: Path, mode: str | None,
                          heads: list[tuple[int, int, str]],
                          issues: list[Issue]) -> None:
    if mode != "tutorial":
        return
    h2_titles = [t for _, lvl, t in heads if lvl == 2]
    if not any(NUMBERED_STEP_TITLE_RE.match(t) for t in h2_titles):
        emit(issues, p, 1, "c",
             "tutorial has no numbered-step H2 (expected `## Step N — ...` or `## N. ...`)")
    if not any(WHAT_LEARNED_RE.match(t) for t in h2_titles):
        emit(issues, p, 1, "c",
             "tutorial missing `## What you learned` H2")


def rule_d_howto_shape(p: Path, mode: str | None,
                       heads: list[tuple[int, int, str]],
                       lines: list[str], issues: list[Issue]) -> None:
    if mode != "how-to":
        return
    h2_titles = [(ln, t) for ln, lvl, t in heads if lvl == 2]
    has_steps_h2 = any(t.strip().lower() == "steps" for _, t in h2_titles)
    has_numbered_step_h2 = any(NUMBERED_STEP_TITLE_RE.match(t) for _, t in h2_titles)
    has_numbered_list = any(re.match(r"^\s*1\.\s", line) for line in lines)
    if not (has_steps_h2 or has_numbered_step_h2 or has_numbered_list):
        emit(issues, p, 1, "d",
             "how-to has no `## Steps` heading or numbered-step list")
    for ln, lvl, title in heads:
        if title.strip().lower() in BANNED_HOWTO_HEADINGS:
            emit(issues, p, ln, "d",
                 f"how-to has banned heading `## {title}` — push rationale to explanation instead")


def rule_e_reference_shape(p: Path, mode: str | None, lines: list[str],
                           heads: list[tuple[int, int, str]],
                           issues: list[Issue]) -> None:
    if mode != "reference":
        return
    # The combined plugin-page standard (## Architecture + ## Reference) leads with
    # architecture context, then puts the reference tables under ## Reference — a
    # first-class hybrid, exempt from the open-with-a-table rule.
    if is_combined_plugin_page(heads):
        return
    h1_line = next((ln for ln, lvl, _ in heads if lvl == 1), 1)
    window = lines[h1_line:h1_line + 25]
    has_table = any(TABLE_ROW_RE.match(ln) for ln in window)
    has_quick_ref = any("Quick Reference" in ln for ln in window)
    has_fence = any(FENCE_RE.match(ln) for ln in window)
    if not (has_table or has_quick_ref or has_fence):
        emit(issues, p, h1_line, "e",
             "reference page should open with a ⚡ Quick Reference / table / "
             "code block within 25 lines of the H1")


def rule_g_filename(p: Path, issues: list[Issue]) -> None:
    if is_structural(p):
        return
    if not FILENAME_RE.match(p.name):
        emit(issues, p, 1, "g",
             f"filename `{p.name}` is not CamelCase-With-Dashes.md")


def rule_g_unique(all_paths: list[Path], wiki_root: Path,
                  issues: list[Issue]) -> None:
    stems: dict[str, list[Path]] = {}
    for p in all_paths:
        if is_structural(p):
            continue
        stems.setdefault(p.stem.lower(), []).append(p)
    for stem, paths in stems.items():
        if len(paths) > 1:
            others = [p.relative_to(wiki_root).as_posix() for p in paths]
            for p in paths:
                emit(issues, p, 1, "g",
                     f"basename `{stem}` not unique (case-insensitive — GitHub Wiki "
                     f"resolves links by basename and collides on case); collides with {others}")


def rule_h_links_resolve(p: Path, text: str, known_stems: set[str],
                         issues: list[Issue]) -> None:
    for line_no, _label, page in extract_wiki_links(text):
        if page not in known_stems:
            emit(issues, p, line_no, "h",
                 f"wiki-internal link to `{page}` does not resolve to a known page")


def rule_i_orphan(modes: dict[Path, str | None],
                  link_graph: dict[str, set[str]],
                  stem_to_mode: dict[str, str],
                  issues: list[Issue]) -> None:
    inbound: dict[str, set[str]] = {}
    for src_stem, outs in link_graph.items():
        for tgt in outs:
            inbound.setdefault(tgt, set()).add(src_stem)

    for p, mode in modes.items():
        if mode is None:
            continue
        out_stems = link_graph.get(p.stem, set())
        out_modes = {stem_to_mode[s] for s in out_stems if s in stem_to_mode}
        if mode == "tutorial" and not ({"reference", "how-to"} & out_modes):
            emit(issues, p, 1, "i",
                 "tutorial does not link to any reference or how-to page")
        elif mode == "how-to" and "reference" not in out_modes:
            emit(issues, p, 1, "i",
                 "how-to does not link to any reference page")
        elif mode == "reference":
            incoming = inbound.get(p.stem, set())
            if not incoming:
                emit(issues, p, 1, "i",
                     "reference page is not linked from any other page (orphan)")


def rule_j_home_sidebar(wiki_root: Path, modes: dict[Path, str | None],
                        issues: list[Issue]) -> None:
    """Home.md and the root _Sidebar.md must exist. Every content page must be
    reachable within **2 levels** of the sitemap: listed in the root _Sidebar.md OR
    in a per-folder _Sidebar.md (GH Wiki renders the nearest one, so a section's own
    sidebar is the second level). The union of all _Sidebar.md refs is the no-orphan
    guarantee. `Home` is **curated** — free to surface only what a reader acts on."""
    home = wiki_root / "Home.md"
    sidebar = wiki_root / "_Sidebar.md"
    for p in (home, sidebar):
        if not p.is_file():
            emit(issues, p, 1, "j", f"{p.name} is missing")
    if not sidebar.is_file():
        return

    all_refs: set[str] = set()
    for sb in wiki_root.rglob("_Sidebar.md"):
        all_refs |= {page for _, _, page in
                     extract_wiki_links(sb.read_text(encoding="utf-8", errors="replace"))}
    for p, mode in modes.items():
        if mode is None:
            continue
        if p.stem not in all_refs:
            emit(issues, sidebar, 1, "j",
                 f"`{p.stem}` (mode: {mode}) is referenced by no _Sidebar.md (root or "
                 f"per-folder); every page must be reachable within 2 levels of the sitemap")


def rule_k_word_count(p: Path, mode: str | None, text: str,
                      issues: list[Issue]) -> None:
    cap = WORD_CAPS.get(mode or "")
    if cap is None:
        return
    count = word_count(text)
    if count > cap:
        emit(issues, p, 1, "k",
             f"{mode} page is {count} words (soft ceiling {cap}); consider splitting",
             soft=True)


def rule_p_shape(p: Path, mode: str | None, lines: list[str], text: str,
                 heads: list[tuple[int, int, str]], issues: list[Issue]) -> None:
    """Shape axis (rule p) — a reference page should be lookup-shaped, an
    explanation page should read as prose. Soft warns that turn the shape
    contract from author discipline into a machine check. The combined
    plugin-reference hybrid is exempt from the reference warnings."""
    if mode not in ("reference", "explanation"):
        return
    table, fence, prose = content_shape(lines)

    if mode == "explanation":
        # explanation-as-lookup: dominated by TABLES (a fact-dump). Fenced diagrams
        # and code examples are fine in an explanation, so they don't count.
        denom = table + prose
        if denom >= SHAPE_MIN_LINES and table / denom > EXPLANATION_TABLE_MAX:
            emit(issues, p, 1, "p",
                 f"explanation page is {round(table / denom * 100)}% tables — an "
                 "explanation should read as prose; move the lookup material to a "
                 "reference page", soft=True)
        return

    # mode == "reference"
    if is_combined_plugin_page(heads):
        return
    lookup = table + fence
    total = lookup + prose
    if total >= SHAPE_MIN_LINES and lookup / total < REFERENCE_LOOKUP_MIN:
        emit(issues, p, 1, "p",
             f"reference page is {round(prose / total * 100)}% prose — a reference "
             "should be lookup-shaped (tables / quick-ref); move the narrative to "
             "an explanation page", soft=True)
    prose_words = word_count(text)
    if prose_words > REFERENCE_WORD_CAP:
        emit(issues, p, 1, "p",
             f"reference page has {prose_words} words of prose (soft ceiling "
             f"{REFERENCE_WORD_CAP}) — a reference should be terse; move the "
             "narrative to an explanation page or split", soft=True)


# ── repo-root doc governance (rule l) ───────────────────────────────────────

EXTERNAL_LINK_PREFIXES = ("http://", "https://", "mailto:", "tel:", "#")


def extract_relative_links(text: str) -> list[tuple[int, str, str]]:
    """Yield (lineno, label, path) for markdown links that are relative
    filesystem paths — not external URLs (http/https/mailto/tel) and not
    pure in-page anchors. A trailing #anchor is stripped from the path."""
    out: list[tuple[int, str, str]] = []
    for m in LINK_RE.finditer(text):
        href = m.group(2).strip()
        if not href or href.startswith(EXTERNAL_LINK_PREFIXES):
            continue
        path_part = href.split("#", 1)[0]
        if not path_part:
            continue
        line_no = text[:m.start()].count("\n") + 1
        out.append((line_no, m.group(1), path_part))
    return out


def rule_l_root_doc_links(doc_path: Path, repo_root: Path,
                          issues: list[Issue]) -> None:
    """Every relative-path markdown link in a governed root doc must resolve
    to an existing file relative to the repo root (rule l)."""
    text = doc_path.read_text(encoding="utf-8", errors="replace")
    for line_no, _label, rel in extract_relative_links(text):
        if not (repo_root / rel).exists():
            emit(issues, doc_path, line_no, "l",
                 f"relative link `{rel}` does not resolve to a file under the repo root")


def collect_root_doc_issues(repo_root: Path) -> list[Issue]:
    """Run the governed-root-doc rules (today: the README, rule l).
    A missing README is a silent no-op."""
    issues: list[Issue] = []
    readme = repo_root / "README.md"
    if readme.is_file():
        rule_l_root_doc_links(readme, repo_root, issues)
    return issues


def resolve_include_readme(repo_root: Path, cli_include: bool) -> bool:
    """Whether to govern the repo-root README. Defaults to True; a CLI
    --no-readme (cli_include=False) or {"include_readme": false} in
    <repo-root>/.diataxis.json opts out. A malformed config is ignored."""
    if not cli_include:
        return False
    cfg = repo_root / ".diataxis.json"
    if cfg.is_file():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return True
        if isinstance(data, dict) and data.get("include_readme") is False:
            return False
    return True


# ── driver ─────────────────────────────────────────────────────────────────

def collect_issues(wiki_root: Path) -> list[Issue]:
    issues: list[Issue] = []
    all_paths = sorted(wiki_root.rglob("*.md"))
    modes: dict[Path, str | None] = {p: resolve_mode(p, wiki_root) for p in all_paths}
    stem_to_mode: dict[str, str] = {
        p.stem: m for p, m in modes.items() if m is not None
    }
    known_stems: set[str] = {p.stem for p in all_paths}
    link_graph: dict[str, set[str]] = {}

    for p in all_paths:
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        heads = parse_headings(lines)
        mode = modes[p]

        rule_a_location(p, wiki_root, issues)
        rule_g_filename(p, issues)
        rule_h_links_resolve(p, text, known_stems, issues)

        if not is_structural(p):
            rule_b_mode_block(p, mode, lines, issues)
            rule_c_tutorial_shape(p, mode, heads, issues)
            rule_d_howto_shape(p, mode, heads, lines, issues)
            rule_e_reference_shape(p, mode, lines, heads, issues)
            rule_k_word_count(p, mode, text, issues)
            rule_p_shape(p, mode, lines, text, heads, issues)

        out_stems = {page for _, _, page in extract_wiki_links(text)}
        link_graph[p.stem] = out_stems

    rule_g_unique(all_paths, wiki_root, issues)
    rule_i_orphan(modes, link_graph, stem_to_mode, issues)
    rule_j_home_sidebar(wiki_root, modes, issues)
    return issues


def format_path(p: Path, wiki_root: Path) -> str:
    try:
        return p.relative_to(wiki_root.parent).as_posix()
    except ValueError:
        return str(p)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Diátaxis wiki lint (ADR 0004). Structural checks only.",
    )
    ap.add_argument("--strict", action="store_true",
                    help="Exit 1 if any hard issue is found. Default exits 0 with warnings.")
    ap.add_argument("--root", type=Path, default=DEFAULT_WIKI,
                    help=f"Wiki root to check (default: {DEFAULT_WIKI}).")
    ap.add_argument("--no-readme", dest="include_readme", action="store_false",
                    default=True,
                    help="Skip governing the repo-root README.md (default: governed; "
                         "rule l checks its relative links resolve).")
    ap.add_argument("--jsonl-out", default=None,
                    help="append a {suite,axis,check,pass,weight} record "
                         "(docs+voice health axis, AA5 C7)")
    args = ap.parse_args()

    if not args.root.is_dir():
        print(f"check-wiki: --root {args.root} is not a directory", file=sys.stderr)
        return 2

    wiki_root = args.root.resolve()
    issues = collect_issues(wiki_root)
    if resolve_include_readme(wiki_root.parent, args.include_readme):
        issues += collect_root_doc_issues(wiki_root.parent)

    hard = [i for i in issues if i.severity == "hard"]
    soft = [i for i in issues if i.severity == "soft"]

    for i in sorted(issues, key=lambda x: (str(x.path), x.line, x.rule)):
        tag = ("ERROR" if args.strict else "WARN") if i.severity == "hard" else "WARN"
        print(f"{tag}: [{i.rule}] {format_path(i.path, wiki_root)}:{i.line}: {i.message}",
              file=sys.stderr)

    summary = (
        f"\ncheck-wiki: {len(hard)} structural issue(s), "
        f"{len(soft)} soft warning(s) under {wiki_root}."
    )
    print(summary, file=sys.stderr)

    exit_code = 1 if (args.strict and hard) else 0

    if args.jsonl_out:
        record = {
            "suite": "check-wiki", "axis": "docs+voice health",
            "check": "structural-cleanliness", "pass": exit_code == 0, "weight": 5,
        }
        with open(args.jsonl_out, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
