#!/usr/bin/env python3
"""wiki_publish_transform.py — publish-time transform for the GitHub Wiki
sync step (.github/workflows/wiki-sync.yml), run on the freshly-rsynced
wiki-repo checkout AFTER rsync and BEFORE commit.

Fixes two confirmed GitHub Wiki rendering gaps (dispatch findings F10/F11):

  F10 — GitHub Wiki does not render YAML frontmatter. Every design page's
        `--- title/status/governs... ---` block would otherwise display as
        raw text above the H1. The frontmatter is load-bearing IN-REPO
        (governs_resolver.py reads it), so it can't be removed at source —
        it is stripped here, at publish time only.

  F11 — GitHub Wiki serves pages at flat URLs
        (github.com/<owner>/<repo>/wiki/<Page-Name>), so a tree-relative
        asset reference that resolves fine in a git checkout (e.g.
        `diagrams/foo.svg` from architecture/Architecture.md) 404s once
        published. This rewrites relative, non-.md, non-http(s) asset
        references to the GitHub Wiki raw-asset URL:
        https://raw.githubusercontent.com/wiki/<owner>/<repo>/<path>
        (format confirmed live with curl against both agentm's and
        crickets' published wikis before this script was wired into CI).

Both transforms are idempotent — safe to run more than once on the same
tree (already-stripped frontmatter and already-absolute URLs are left
alone). Wiki-internal page links ([text](Other-Page.md)) and
[[wikilink]]-style references are never touched — those resolve fine via
GitHub's own page-name routing.

Usage:
  python3 wiki_publish_transform.py <wiki-repo-dir> --repo <owner>/<repo>
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Non-greedy, first-occurrence-only leading frontmatter block.
FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)

# A fenced code block delimiter line (``` or ~~~) — toggles fence state.
FENCE_RE = re.compile(r"^\s*(```|~~~)")

# ![alt](href "title")  or  [text](href "title"). [[wikilink]] has no
# parenthesized href, so it never matches this pattern.
LINK_RE = re.compile(r'(!?)\[([^\]]*)\]\(([^)\s]+)((?:\s+"[^"]*")?)\)')


def strip_frontmatter(text: str) -> str:
    """Remove a single leading YAML frontmatter block, if present. A no-op
    if the file has already been transformed (idempotent)."""
    return FRONTMATTER_RE.sub("", text, count=1)


def _is_external(href: str) -> bool:
    return href.startswith(("http://", "https://", "//", "mailto:", "#"))


def _raw_asset_url(owner: str, repo: str, page_path: Path, href: str,
                    wiki_root: Path) -> str | None:
    """Resolve a relative asset href against the page's own directory and
    return the GitHub Wiki raw-asset URL, or None if it doesn't need
    rewriting (already absolute, a .md page link, or escapes the wiki
    tree)."""
    href_no_frag = href.split("#", 1)[0]
    if not href_no_frag or _is_external(href):
        return None
    if href_no_frag.lower().endswith(".md"):
        return None  # wiki-internal page link — GH's own routing handles it

    base = wiki_root if href_no_frag.startswith("/") else page_path.parent
    target = (base / href_no_frag.lstrip("/")).resolve()
    try:
        rel = target.relative_to(wiki_root.resolve())
    except ValueError:
        return None  # escapes the wiki tree — leave alone, don't guess
    return f"https://raw.githubusercontent.com/wiki/{owner}/{repo}/{rel.as_posix()}"


def rewrite_asset_links(text: str, owner: str, repo: str, page_path: Path,
                         wiki_root: Path) -> str:
    """Rewrite relative non-.md asset references to absolute raw-asset
    URLs, skipping fenced code blocks (mirrors check-wiki.py's own
    fence-awareness — illustrative markup inside a fence is not a
    navigable reference)."""
    out_lines: list[str] = []
    in_fence = False
    for line in text.splitlines(keepends=True):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            out_lines.append(line)
            continue
        if in_fence:
            out_lines.append(line)
            continue

        def repl(m: "re.Match[str]") -> str:
            bang, alt, href, title = m.group(1), m.group(2), m.group(3), m.group(4)
            new_href = _raw_asset_url(owner, repo, page_path, href, wiki_root)
            if new_href is None:
                return m.group(0)
            return f"{bang}[{alt}]({new_href}{title})"

        out_lines.append(LINK_RE.sub(repl, line))
    return "".join(out_lines)


def transform_file(path: Path, owner: str, repo: str, wiki_root: Path) -> bool:
    """Transform one .md file in place. Returns True if it changed."""
    original = path.read_text(encoding="utf-8")
    text = strip_frontmatter(original)
    text = rewrite_asset_links(text, owner, repo, path, wiki_root)
    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wiki_dir", type=Path, help="the freshly-rsynced wiki-repo checkout")
    ap.add_argument("--repo", required=True, help="owner/repo, e.g. alexherrero/agentm")
    args = ap.parse_args(argv)

    if "/" not in args.repo:
        print(f"wiki_publish_transform: --repo must be owner/repo, got {args.repo!r}",
              file=sys.stderr)
        return 2
    owner, repo = args.repo.split("/", 1)

    wiki_root = args.wiki_dir.resolve()
    if not wiki_root.is_dir():
        print(f"wiki_publish_transform: not a directory: {wiki_root}", file=sys.stderr)
        return 2

    changed = 0
    total = 0
    for md in sorted(wiki_root.rglob("*.md")):
        if ".git" in md.parts:
            continue
        total += 1
        if transform_file(md, owner, repo, wiki_root):
            changed += 1

    print(f"wiki_publish_transform: {changed}/{total} file(s) transformed under {wiki_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
