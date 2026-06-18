#!/usr/bin/env python3
# adapt_skills.py — adapt-don't-import workflow (plan #7b task 4).
#
# Pass 1 (this script): parse diff files from discover_skills.py; extract
# candidate patterns; score against the 6-rule rubric; enrich each candidate
# with GitHub metadata (stars / last_commit / archived / license) + trust-
# worthiness signals (operator-editable trusted-sources whitelist + cross-
# citation count across the configured discovery sources); write enriched
# candidate payloads as JSON under <vault>/_meta/skill-discovery-cache/
# adapt-state/<source-slug>/<pattern-slug>.json.
#
# Pass 2 (sub-agent — see `agents/adapt-evaluator.md`): reads each enriched
# candidate JSON; outputs final HIGH/MEDIUM/LOW classification + adaptation
# notes; writes the final watchlist entry to
# <vault>/personal/_skill-watchlist/<source-slug>/<pattern-slug>.md.
#
# Adapt-don't-import contract (locked from plan #7b PLAN.md):
#   - Never writes to `crickets/skills/<x>/SKILL.md`.
#   - Only writes to `_skill-watchlist/` (review surface) — task 5's
#     /memory watchlist command lets the operator promote / dismiss / defer.
#   - Sub-agent is read-only with a tightly-scoped write allowlist.
#
# Locked design calls:
#   - 6-rule rubric (cumulative scoring): see _score_candidate() below.
#   - GitHub API: unauthenticated requests (60/hr unauth rate limit; v1
#     accepts this — heavy-volume scans exit gracefully on 403).
#   - Trustworthiness whitelist auto-seeds with a curated set of orgs the
#     operator approved (anthropics, google, microsoft, hashicorp, etc.).
#   - Pure-Python Pass 1 + LLM sub-agent Pass 2 — operator decision; lets
#     the heuristic do enrichment + the sub-agent do semantic judgment.
#   - Idempotent: state file tracks (source-slug, pattern-slug) pairs
#     already evaluated; re-runs skip them.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


_FETCH_TIMEOUT_SEC = 10
_USER_AGENT = "crickets-adapt-skills/0.1 (+local; stdlib-urllib)"

# Default trusted-sources whitelist seeded on first run. Operator edits
# this file in Obsidian to add/remove orgs. The signal is "patterns from
# these orgs get +1 trustworthiness".
_DEFAULT_TRUSTED_ORGS = [
    "anthropics",
    "anthropic",
    "google",
    "googleworkspace",
    "googlecloudplatform",
    "microsoft",
    "vercel",
    "hashicorp",
    "openai",
    "cloudflare",
    "github",
    "supabase",
    "redis",
    "kubernetes",
    "docker",
    "pytorch",
    "huggingface",
    "modelcontextprotocol",
]


def _resolve_vault_path(arg_path: str | None) -> Path:
    if arg_path:
        return Path(arg_path).expanduser()
    env_path = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    raise ValueError(
        "vault path required: pass --vault-path or set MEMORY_VAULT_PATH"
    )


def _cache_root(vault: Path) -> Path:
    return vault / "_meta" / "skill-discovery-cache"


def _adapt_state_root(vault: Path) -> Path:
    return _cache_root(vault) / "adapt-state"


def _trusted_sources_path(vault: Path) -> Path:
    return vault / "personal" / "trusted-sources.md"


def _skill_watchlist_root(vault: Path) -> Path:
    return vault / "personal" / "_skill-watchlist"


def _personal_skills_root(vault: Path) -> Path:
    return vault / "personal-skills"


def _always_load_root(vault: Path) -> Path:
    return vault / "personal" / "_always-load"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _seed_trusted_sources(target: Path) -> bool:
    """Write the default trusted-orgs whitelist if missing. Returns True if seeded."""
    if target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Trusted source orgs",
        "#",
        "# Used by the adapt-skills workflow to flag '+1 trustworthiness' on",
        "# candidates whose source repo owner matches an entry here. Edit",
        "# freely in Obsidian: one org-slug per non-comment line. Lines",
        "# starting with `#` are comments; blank lines ignored. Org slugs",
        "# match the GitHub URL's owner component (case-insensitive).",
        "#",
        "# Seeded on first install with operator-approved defaults.",
        "",
    ]
    for org in _DEFAULT_TRUSTED_ORGS:
        lines.append(org)
    # Write bytes to guarantee LF-only line endings on all platforms
    # (matches save.py + ideas_surface.py convention). Path.write_text's
    # default newline-translation on Windows would convert \n → \r\n,
    # breaking downstream regex/grep parsers that expect LF.
    content = ("\n".join(lines).rstrip() + "\n").encode("utf-8")
    target.write_bytes(content)
    return True


def _parse_trusted_sources(path: Path) -> set[str]:
    """Read trusted-orgs whitelist; return a lowercased set of org-slugs."""
    if not path.exists():
        return set()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.add(s.lower())
    return out


# ── Diff parsing ───────────────────────────────────────────────────────────


def _discover_diff_files(vault: Path, only_source: str | None = None) -> list[Path]:
    """Find every diff-*.md file under the discovery cache."""
    root = _cache_root(vault)
    if not root.exists():
        return []
    out: list[Path] = []
    for source_dir in sorted(root.iterdir()):
        if not source_dir.is_dir():
            continue
        if only_source and source_dir.name != only_source:
            continue
        for p in sorted(source_dir.glob("diff-*.md")):
            if p.is_file():
                out.append(p)
    return out


def _slugify(text: str, max_len: int = 50) -> str:
    """Kebab-case slug; alnum runs joined by `-`; truncated."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    slug = "-".join(words)[:max_len].rstrip("-")
    return slug or "unnamed"


def parse_candidates_from_diff(diff_text: str, *, source_slug: str) -> list[dict]:
    """Parse a diff file into candidate patterns.

    Each candidate is a list-item, sub-heading, or section. Returns a list
    of dicts: {pattern_slug, title, body, source_section}.

    Strategy:
      - Markdown headings (#, ##, ###) start a new candidate; body collects
        subsequent non-blank lines until the next heading or blank-section.
      - Top-level list items (`- foo` / `* foo`) are also candidates,
        especially in awesome-list-style sources where each bullet is a
        skill / tool pointer.
      - Lines that are pure URLs or pure heading-references get attached to
        the most recent candidate, not their own.
    """
    candidates: list[dict] = []
    current: dict | None = None
    current_section: str = ""

    def _flush():
        nonlocal current
        if current and current["body"].strip():
            current["title"] = current["title"].strip()[:120]
            current["body"] = current["body"].strip()
            current["pattern_slug"] = _slugify(current["title"])
            current["source_section"] = current_section
            candidates.append(current)
        current = None

    for line in diff_text.splitlines():
        stripped = line.strip()
        if not stripped:
            # Blank line — preserve in body if we're inside a candidate.
            if current:
                current["body"] += "\n"
            continue

        # Heading: candidate title.
        m_h = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if m_h:
            _flush()
            level = len(m_h.group(1))
            title = m_h.group(2)
            current_section = title if level <= 2 else current_section
            current = {"title": title, "body": ""}
            continue

        # Top-level list item (- or *).
        m_li = re.match(r"^[-*]\s+(.+)$", stripped)
        if m_li:
            _flush()
            title = m_li.group(1)
            # Strip wrapping bold/link syntax for cleaner title.
            t = re.sub(r"^\*+|\*+$", "", title).strip()
            t = re.sub(r"^\[([^\]]+)\].*$", r"\1", t)
            current = {"title": t, "body": title}
            continue

        # Continuation of an existing candidate's body.
        if current is not None:
            current["body"] += line + "\n"
        # else: lines outside any candidate are ignored.

    _flush()
    return candidates


# ── GitHub metadata enrichment ─────────────────────────────────────────────


def _fetch_github_api(path: str) -> dict | None:
    """GET https://api.github.com<path> with stdlib urllib. Returns parsed
    JSON or None on any error / rate-limit / network failure."""
    url = f"https://api.github.com{path}"
    req = Request(url, headers={
        "User-Agent": _USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    try:
        with urlopen(req, timeout=_FETCH_TIMEOUT_SEC) as resp:
            if getattr(resp, "status", 200) != 200:
                return None
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body)
    except (HTTPError, URLError, socket.timeout, json.JSONDecodeError):
        return None
    except Exception:  # pragma: no cover
        return None


def _extract_github_link(text: str) -> tuple[str, str] | None:
    """Extract (owner, repo) from any GitHub URL in a text blob."""
    m = re.search(
        r"github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)(?:[/)\s.]|$)",
        text,
    )
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    # Strip trailing `.git`.
    repo = re.sub(r"\.git$", "", repo)
    return owner, repo


def enrich_github_metadata(
    candidate: dict,
    *,
    skip_network: bool = False,
) -> dict:
    """Add GitHub metadata to a candidate if its body links a GitHub repo.

    Adds keys: github_owner, github_repo, github_stars, github_archived,
    github_last_commit_iso, github_license, github_html_url. All values
    None when the lookup fails / no GitHub link present / skip_network set.
    """
    enriched = dict(candidate)
    link = _extract_github_link(candidate.get("body", "") + " " + candidate.get("title", ""))
    if not link:
        enriched.update({
            "github_owner": None, "github_repo": None,
            "github_stars": None, "github_archived": None,
            "github_last_commit_iso": None, "github_license": None,
            "github_html_url": None,
        })
        return enriched
    owner, repo = link
    enriched.update({
        "github_owner": owner,
        "github_repo": repo,
        "github_html_url": f"https://github.com/{owner}/{repo}",
    })
    if skip_network:
        enriched.update({
            "github_stars": None, "github_archived": None,
            "github_last_commit_iso": None, "github_license": None,
        })
        return enriched
    data = _fetch_github_api(f"/repos/{owner}/{repo}")
    if not data:
        enriched.update({
            "github_stars": None, "github_archived": None,
            "github_last_commit_iso": None, "github_license": None,
        })
        return enriched
    enriched["github_stars"] = data.get("stargazers_count")
    enriched["github_archived"] = data.get("archived", False)
    enriched["github_last_commit_iso"] = data.get("pushed_at")
    license_block = data.get("license") or {}
    enriched["github_license"] = (license_block or {}).get("spdx_id")
    return enriched


# ── Trustworthiness signals ────────────────────────────────────────────────


def trustworthiness_signal(
    candidate: dict,
    *,
    trusted_orgs: set[str],
    cross_citation_count: int = 0,
) -> dict:
    """Compute trustworthiness signals (does NOT decide HIGH/MED/LOW)."""
    owner = (candidate.get("github_owner") or "").lower()
    from_trusted_org = owner in trusted_orgs if owner else False
    stars = candidate.get("github_stars") or 0
    archived = bool(candidate.get("github_archived"))
    last_commit = candidate.get("github_last_commit_iso")
    activity_recent = False
    if last_commit:
        try:
            lc = datetime.fromisoformat(last_commit.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - lc).days
            activity_recent = age_days < 365  # Active in last year
        except (ValueError, AttributeError):
            pass
    license_spdx = candidate.get("github_license")
    return {
        "from_trusted_org": from_trusted_org,
        "trusted_org_match": owner if from_trusted_org else None,
        "high_stars": stars >= 500,
        "low_stars": 0 < stars < 50,
        "archived_warning": archived,
        "activity_recent": activity_recent,
        "permissive_license": license_spdx in (
            "MIT", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause",
            "ISC", "MPL-2.0",
        ) if license_spdx else None,
        "cross_citation_count": cross_citation_count,
    }


# ── 6-rule scoring rubric ──────────────────────────────────────────────────


def _existing_skill_names(vault: Path) -> set[str]:
    """Names of skills already indexed (from personal-skills/<repo>/*.md)."""
    out: set[str] = set()
    root = _personal_skills_root(vault)
    if not root.exists():
        return out
    for p in root.rglob("*.md"):
        out.add(p.stem.lower())
    return out


def _always_load_keywords(vault: Path) -> set[str]:
    """Extract a keyword bag from _always-load/ entry slugs + tags."""
    out: set[str] = set()
    root = _always_load_root(vault)
    if not root.exists():
        return out
    for p in root.glob("*.md"):
        out.update(re.findall(r"[a-z]+", p.stem.lower()))
    return out


_EXPERIMENTAL_FLAGS = {
    "experimental", "wip", "hack", "workaround", "deprecated",
    "alpha", "beta", "unstable", "broken", "abandoned",
}
_AGENT_BUILDING_KEYWORDS = {
    "agent", "skill", "hook", "workflow", "claude", "antigravity",
    "gemini", "mcp", "memory", "context", "automation", "ci", "release",
}
_PRIMITIVE_KEYWORDS = {
    "mcp-server", "mcp", "hook", "skill", "primitive", "subagent",
    "sub-agent", "rule", "command",
}
_CROSS_VENDOR_PROPRIETARY = {
    "windsurf", "cursor", "codex", "jetbrains", "intellij",
    "pycharm", "vscode",  # vscode itself fine but as a target it's not this dev-env
}


def _score_candidate(candidate: dict, *, vault: Path) -> dict:
    """Apply the 6-rule rubric. Returns {score, rules_fired, confidence}."""
    text = (candidate.get("title", "") + " " + candidate.get("body", "")).lower()
    score = 0
    rules: list[str] = []

    # Rule 1: mentions a tool/skill the operator doesn't have.
    existing = _existing_skill_names(vault)
    title_slug = _slugify(candidate.get("title", ""))
    if title_slug and title_slug not in existing:
        # Heuristic: title is a thing-y name (not just a sentence).
        if len(title_slug) <= 40 and "-" in title_slug or len(title_slug) <= 20:
            score += 1
            rules.append("R1:new-tool-not-yet-owned")

    # Rule 2: matches existing _always-load keywords (complements a convention).
    al_kw = _always_load_keywords(vault)
    text_words = set(re.findall(r"[a-z]+", text))
    overlap = al_kw & text_words
    if len(overlap) >= 2:
        score += 1
        rules.append(f"R2:complements-convention({len(overlap)} kw overlap)")

    # Rule 3: section context is agent-building / dev-env.
    if any(k in text for k in _AGENT_BUILDING_KEYWORDS):
        score += 1
        rules.append("R3:agent-building-context")

    # Rule 4: names a primitive (MCP server / hook / skill / etc.).
    if any(re.search(rf"\b{re.escape(k)}\b", text) for k in _PRIMITIVE_KEYWORDS):
        score += 1
        rules.append("R4:names-primitive")

    # Rule 5: flagged as experimental / WIP / hack → -1.
    if any(f in text for f in _EXPERIMENTAL_FLAGS):
        score -= 1
        rules.append("R5:experimental-flag")

    # Rule 6: cross-vendor proprietary → -2 (lower priority for this dev-env).
    if any(p in text for p in _CROSS_VENDOR_PROPRIETARY):
        score -= 2
        rules.append("R6:cross-vendor-proprietary")

    if score >= 3:
        confidence = "HIGH"
    elif score >= 1:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"
    return {
        "rubric_score": score,
        "rubric_rules_fired": rules,
        "rubric_confidence": confidence,
    }


# ── State + idempotency ────────────────────────────────────────────────────


def _adapt_state_file(vault: Path) -> Path:
    return _adapt_state_root(vault) / "evaluated.json"


def _load_adapt_state(vault: Path) -> dict:
    sf = _adapt_state_file(vault)
    if not sf.exists():
        return {"evaluated": []}
    try:
        return json.loads(sf.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"evaluated": []}


def _save_adapt_state(vault: Path, state: dict) -> None:
    sf = _adapt_state_file(vault)
    sf.parent.mkdir(parents=True, exist_ok=True)
    tmp = sf.with_suffix(sf.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, sf)


def _candidate_key(source_slug: str, pattern_slug: str, diff_basename: str) -> str:
    """Stable key for the (source, pattern, diff-date) tuple."""
    return f"{source_slug}/{diff_basename}/{pattern_slug}"


# ── Top-level orchestration ────────────────────────────────────────────────


def _read_diff_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _compute_cross_citations(
    candidate: dict, all_diff_texts: dict[str, str]
) -> int:
    """Count how many distinct sources mention this candidate's GitHub link
    OR its title (case-insensitive substring). Excludes the source the
    candidate came from."""
    own = candidate.get("source_slug", "")
    needle_link = ""
    if candidate.get("github_html_url"):
        needle_link = candidate["github_html_url"].lower()
    needle_title = candidate.get("title", "").lower()
    if len(needle_title) < 6:
        needle_title = ""  # Too generic.
    count = 0
    for source_slug, text in all_diff_texts.items():
        if source_slug == own:
            continue
        haystack = text.lower()
        if needle_link and needle_link in haystack:
            count += 1
            continue
        if needle_title and needle_title in haystack:
            count += 1
    return count


def adapt_skills(
    *,
    vault: Path,
    only_source: str | None = None,
    skip_network: bool = False,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict:
    """Run Pass 1 over cached diff files; emit enriched candidate JSONs.

    Returns summary: {evaluated_count, written_count, skipped_count,
    high_count, medium_count, low_count, errors}.

    `limit`, when set (>0), caps the number of *newly-evaluated* candidates
    per run — the GitHub-enrichment cost lives in this loop, so the
    auto-orchestration idle chain passes a small limit to bound each pass.
    Already-evaluated candidates (skipped via the idempotency set) don't
    count against the limit.
    """
    if not vault.exists() or not vault.is_dir():
        raise FileNotFoundError(
            f"vault path does not exist or is not a directory: {vault}"
        )

    # Seed trusted-sources on first run.
    ts_path = _trusted_sources_path(vault)
    trusted_seeded = _seed_trusted_sources(ts_path)
    trusted_orgs = _parse_trusted_sources(ts_path)

    diff_files = _discover_diff_files(vault, only_source=only_source)
    state = _load_adapt_state(vault)
    evaluated_set = set(state.get("evaluated", []))

    summary = {
        "vault": str(vault),
        "trusted_sources_seeded": trusted_seeded,
        "diff_files_scanned": len(diff_files),
        "evaluated_count": 0,
        "written_count": 0,
        "skipped_count": 0,
        "high_count": 0,
        "medium_count": 0,
        "low_count": 0,
        "errors": 0,
        "dry_run": dry_run,
        "skip_network": skip_network,
        "limit": limit,
        "limit_reached": False,
        "candidates": [],
    }

    if not diff_files:
        return summary

    # Normalize the limit: None or <=0 means "no cap".
    cap = limit if (isinstance(limit, int) and limit > 0) else None

    # Pre-read all diff texts once (for cross-citation count).
    all_diff_texts: dict[str, str] = {}
    for p in diff_files:
        source_slug = p.parent.name
        # Concatenate per-source (multiple dated diffs share the source slug).
        text = _read_diff_text(p)
        all_diff_texts[source_slug] = all_diff_texts.get(source_slug, "") + "\n" + text

    # Walk each diff; parse candidates; enrich; score; write JSON.
    for diff_file in diff_files:
        source_slug = diff_file.parent.name
        diff_basename = diff_file.stem
        diff_text = _read_diff_text(diff_file)
        if not diff_text.strip():
            continue
        candidates = parse_candidates_from_diff(diff_text, source_slug=source_slug)
        for cand in candidates:
            key = _candidate_key(source_slug, cand["pattern_slug"], diff_basename)
            if key in evaluated_set:
                summary["skipped_count"] += 1
                continue
            # Bound per-run work (idle-chain cost control): stop after `cap`
            # newly-evaluated candidates. Already-evaluated ones (skipped above)
            # don't consume the budget, so a re-run resumes on fresh candidates.
            if cap is not None and summary["evaluated_count"] >= cap:
                summary["limit_reached"] = True
                break
            cand["source_slug"] = source_slug
            cand["source_diff"] = diff_file.name
            cand["discovered_at"] = _utcnow_iso()
            # GitHub enrichment.
            try:
                cand = enrich_github_metadata(cand, skip_network=skip_network)
            except Exception as e:  # pragma: no cover
                cand["github_enrich_error"] = str(e)
                summary["errors"] += 1
            # Cross-citations (uses pre-read corpus).
            xcite = _compute_cross_citations(cand, all_diff_texts)
            # Trust signals.
            trust = trustworthiness_signal(
                cand, trusted_orgs=trusted_orgs, cross_citation_count=xcite,
            )
            cand["trust_signals"] = trust
            # 6-rule scoring.
            cand.update(_score_candidate(cand, vault=vault))
            summary["evaluated_count"] += 1
            if cand["rubric_confidence"] == "HIGH":
                summary["high_count"] += 1
            elif cand["rubric_confidence"] == "MEDIUM":
                summary["medium_count"] += 1
            else:
                summary["low_count"] += 1
            # Write candidate JSON for sub-agent to pick up (unless dry-run).
            if not dry_run:
                target_dir = _adapt_state_root(vault) / source_slug
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / f"{cand['pattern_slug']}.json"
                target.write_text(
                    json.dumps(cand, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
                evaluated_set.add(key)
                summary["written_count"] += 1
                summary["candidates"].append({
                    "pattern_slug": cand["pattern_slug"],
                    "source_slug": source_slug,
                    "confidence": cand["rubric_confidence"],
                    "score": cand["rubric_score"],
                    "rules_fired": cand["rubric_rules_fired"],
                    "json_path": str(target.relative_to(vault)),
                })
            else:
                summary["candidates"].append({
                    "pattern_slug": cand["pattern_slug"],
                    "source_slug": source_slug,
                    "confidence": cand["rubric_confidence"],
                    "score": cand["rubric_score"],
                    "rules_fired": cand["rubric_rules_fired"],
                })

        # Cap reached inside the inner loop — stop walking further diff files.
        if summary["limit_reached"]:
            break

    if not dry_run:
        state["evaluated"] = sorted(evaluated_set)
        state["last_run"] = _utcnow_iso()
        _save_adapt_state(vault, state)

    return summary


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="memory-adapt-skills",
        description=(
            "Pass 1 of the adapt-don't-import workflow (plan #7b task 4). "
            "Walks cached diff files from discover_skills.py; parses "
            "candidate patterns; applies 6-rule rubric + GitHub metadata + "
            "trustworthiness signals; writes enriched candidate JSONs for "
            "the adapt-evaluator sub-agent (Pass 2) to judge before any "
            "watchlist entry is created."
        ),
    )
    parser.add_argument(
        "--vault-path", default=None,
        help="MemoryVault root (default: $MEMORY_VAULT_PATH env var). Required.",
    )
    parser.add_argument(
        "--source", default=None,
        help="limit to a single source-slug (e.g. anthropics-anthropic-cookbook)",
    )
    parser.add_argument(
        "--skip-network", action="store_true",
        help="skip GitHub API enrichment (offline / testing / rate-limit-safe)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="evaluate candidates without writing JSON files or updating state",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="cap newly-evaluated candidates per run (bounds GitHub-enrichment "
             "cost; the idle orchestration chain passes a small limit). "
             "Already-evaluated candidates don't count. Default: no cap.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        vault = _resolve_vault_path(args.vault_path)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if not vault.exists() or not vault.is_dir():
        print(
            f"ERROR: vault path does not exist or is not a directory: {vault}",
            file=sys.stderr,
        )
        return 1
    summary = adapt_skills(
        vault=vault,
        only_source=args.source,
        skip_network=args.skip_network,
        dry_run=args.dry_run,
        limit=args.limit,
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary["errors"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
