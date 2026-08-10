#!/usr/bin/env python3
# index_skills.py — personal-skills auto-indexer (plan #7b task 1).
#
# Walks SKILL.md files across configured source paths and writes one
# pointer entry per skill to MemoryVault/personal-skills/<repo>/<skill>.md.
# Pointer entries are short (frontmatter + auto-extracted summary + link
# back to source SKILL.md) so the agent picks them up via recall without
# drowning out hand-curated entries.
#
# Why an indexer rather than per-skill hand-curated entries:
#   - SKILL.md content already exists; duplicating it by hand is wasteful.
#   - As skills evolve (version bump, description tweak), the index can
#     auto-refresh; hand-curated entries would silently drift.
#   - The agent learns "we have a /design author skill" without an
#     operator-tax explanation in every session.
#
# Idempotent contract:
#   - If a personal-skills entry exists AND its skill_version + description
#     match the current SKILL.md → no-op (no file rewrite, no vec-index
#     enqueue, no `updated:` bump).
#   - Otherwise → overwrite the entry, bump `updated:`, enqueue vec-index
#     upsert.
#
# This is robotic — uses Write-overwrite directly rather than going through
# /memory evolve (which is for human-curated entries with audit trails).
# The source SKILL.md is the authoritative version; the index entry is a
# derivative.
#
# Repo-name resolution (auto-mode):
#   1. CLI arg `--repo-name <slug>` paired with the skill-path wins.
#   2. Otherwise walk up from the skill-path: find the first ancestor
#      containing `.git/` or `AGENTS.md` → use its basename.
#   3. Fall back to `unknown-repo` if nothing matched (with stderr warning).
#
# Skill-path defaults are deliberately empty — the indexer requires at
# least one --skill-path or MEMORY_SKILL_PATHS env entry. Operators in
# practice will configure this in `~/.config/crickets/memory.yml` or
# via the installer (which knows the toolkit's own skill paths).

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


_KEBAB_RE = re.compile(r"^[a-z0-9-]+$")


def _today_iso() -> str:
    return date.today().isoformat()


def _resolve_vault_path(arg_path: str | None) -> Path:
    """Resolve vault path: arg → MEMORY_VAULT_PATH env → error."""
    if arg_path:
        return Path(arg_path).expanduser()
    env_path = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    raise ValueError(
        "vault path required: pass --vault-path or set MEMORY_VAULT_PATH"
    )


def _resolve_skill_paths(arg_paths: list[str] | None) -> list[Path]:
    """Resolve skill source paths: CLI args + MEMORY_SKILL_PATHS env (colon-sep)."""
    paths: list[Path] = []
    seen: set[Path] = set()
    if arg_paths:
        for p in arg_paths:
            resolved = Path(p).expanduser().resolve()
            if resolved not in seen:
                paths.append(resolved)
                seen.add(resolved)
    env = os.environ.get("MEMORY_SKILL_PATHS", "").strip()
    if env:
        for p in env.split(":"):
            p = p.strip()
            if not p:
                continue
            resolved = Path(p).expanduser().resolve()
            if resolved not in seen:
                paths.append(resolved)
                seen.add(resolved)
    return paths


def _normalize_repo_name(name: str) -> str:
    """Coerce a directory basename into the kebab-case vault convention.

    Lowercases, replaces runs of non-alphanumerics with `-`, trims leading/
    trailing dashes. Returns `unknown-repo` if input yields nothing.
    """
    lowered = name.lower()
    kebab = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return kebab or "unknown-repo"


def _detect_repo_name(skill_md_path: Path) -> str:
    """Walk up from a SKILL.md file; return kebab-normalized basename of first .git/AGENTS.md ancestor."""
    cur = skill_md_path.parent.resolve()
    while cur != cur.parent:
        if (cur / ".git").exists() or (cur / "AGENTS.md").exists():
            return _normalize_repo_name(cur.name)
        cur = cur.parent
    return "unknown-repo"


def _parse_skill_frontmatter(skill_md: Path) -> dict[str, str]:
    """Parse YAML frontmatter from a SKILL.md file.

    Returns a flat dict of string-valued fields. Lists (e.g. `supported_hosts`)
    are kept as the raw YAML-list string (we don't need to interpret them
    structurally for the index entry; we only echo a few fields back).

    Raises ValueError if the file is missing or has no frontmatter block.
    """
    if not skill_md.exists():
        raise ValueError(f"SKILL.md not found: {skill_md}")
    text = skill_md.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"no opening --- frontmatter in {skill_md}")
    end_idx = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        raise ValueError(f"no closing --- frontmatter in {skill_md}")
    out: dict[str, str] = {}
    for line in lines[1:end_idx]:
        if not line.strip() or line.strip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        out[key.strip()] = val.strip()
    return out


def _extract_summary(skill_md: Path, max_chars: int = 600) -> str:
    """Pull a short summary from the SKILL.md body (after frontmatter).

    Prefers the first paragraph after the H1 (skipping blank lines + the
    H1 itself). Falls back to the frontmatter `description:` field if no
    body paragraph found.
    """
    text = skill_md.read_text(encoding="utf-8")
    lines = text.splitlines()
    # Skip past frontmatter.
    body_start = 0
    if lines and lines[0].strip() == "---":
        for i, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                body_start = i + 1
                break
    body = lines[body_start:]
    # Skip H1 if present + leading blank lines.
    idx = 0
    while idx < len(body) and not body[idx].strip():
        idx += 1
    if idx < len(body) and body[idx].lstrip().startswith("# "):
        idx += 1
        while idx < len(body) and not body[idx].strip():
            idx += 1
    # Collect the first paragraph (until next blank line or H2/H3).
    para: list[str] = []
    while idx < len(body):
        line = body[idx]
        if not line.strip():
            if para:
                break
            idx += 1
            continue
        if line.lstrip().startswith("#"):
            break
        para.append(line.strip())
        idx += 1
    summary = " ".join(para).strip()
    if not summary:
        return ""
    if len(summary) > max_chars:
        summary = summary[:max_chars].rstrip() + "…"
    return summary


def _build_entry_content(
    *,
    repo_name: str,
    skill_name: str,
    skill_version: str,
    description: str,
    supported_hosts: str,
    source_path: str,
    summary: str,
) -> str:
    """Build the full markdown content (frontmatter + body) for the index entry."""
    today = _today_iso()
    fm = (
        "---\n"
        "kind: skill-pointer\n"
        "status: active\n"
        f"created: {today}\n"
        f"updated: {today}\n"
        "tags: [skill, personal-skills, auto-indexed]\n"
        f"group: personal-skills/{repo_name}\n"
        f"slug: {skill_name}\n"
        "always_load: false\n"
        f"source_path: {source_path}\n"
        f"source_repo: {repo_name}\n"
        f"skill_version: {skill_version}\n"
        f"last_indexed: {today}\n"
        "---\n"
    )
    body_parts = [
        f"# {skill_name} (skill pointer)\n",
        f"Auto-indexed entry pointing at `{source_path}`. Refreshed by "
        f"`/memory index-skills`; do not edit by hand — changes will be "
        f"overwritten on the next index run.\n",
        f"**Repo:** `{repo_name}`  ",
        f"**Version:** {skill_version}  ",
        f"**Hosts:** {supported_hosts or '(unspecified)'}\n",
        "## Description\n",
        f"{description}\n",
    ]
    if summary:
        body_parts.append("## Summary (extracted from SKILL.md)\n")
        body_parts.append(f"{summary}\n")
    body = "\n".join(body_parts).rstrip("\n") + "\n"
    return fm + "\n" + body


def _entry_needs_refresh(
    target: Path, *, skill_version: str, description: str
) -> bool:
    """True if the entry is missing OR its skill_version/description differ.

    Conservative: any parse error → treat as needs-refresh (we'll overwrite
    + the fresh write fixes whatever was broken).
    """
    if not target.exists():
        return True
    try:
        existing = target.read_text(encoding="utf-8")
    except OSError:
        return True
    # Cheap line-scan for the two fields we care about.
    existing_version = ""
    existing_description = ""
    for line in existing.splitlines():
        if line.startswith("skill_version:"):
            existing_version = line.split(":", 1)[1].strip()
        elif line.startswith("## Description"):
            # Description body is the next non-blank line.
            continue
    # Description-match check: the description body lives between the
    # `## Description\n\n` line and the next `##` line. Use a regex to
    # find it without a full markdown parse.
    desc_match = re.search(
        r"^## Description\s*\n+([^\n]+)", existing, flags=re.MULTILINE
    )
    if desc_match:
        existing_description = desc_match.group(1).strip()
    if existing_version != skill_version:
        return True
    if existing_description != description:
        return True
    return False


def index_one_skill(
    skill_md: Path,
    *,
    vault: Path,
    repo_name: str | None = None,
) -> dict[str, str]:
    """Index a single SKILL.md → personal-skills/<repo>/<skill-name>.md.

    Returns a status dict: {"action": "written" | "skipped" | "error",
    "target": <path>, "repo": <repo>, "skill": <skill-name>,
    "reason": <optional>}.
    """
    skill_md = skill_md.resolve()
    fm = _parse_skill_frontmatter(skill_md)
    skill_name = fm.get("name", "").strip()
    if not skill_name or not _KEBAB_RE.match(skill_name):
        return {
            "action": "error",
            "target": "",
            "repo": "",
            "skill": "",
            "reason": (
                f"SKILL.md at {skill_md} has missing or non-kebab `name:` field"
            ),
        }
    if not repo_name:
        repo_name = _detect_repo_name(skill_md)
    else:
        # Explicit --repo-name still passes through normalization — the
        # vault layout is kebab-only by convention, regardless of source.
        repo_name = _normalize_repo_name(repo_name)
    if not _KEBAB_RE.match(repo_name):
        # _normalize_repo_name yields kebab or "unknown-repo"; if we still
        # fail here, something pathological happened.
        return {
            "action": "error",
            "target": "",
            "repo": repo_name,
            "skill": skill_name,
            "reason": (
                f"resolved repo name {repo_name!r} failed kebab normalization"
            ),
        }
    description = fm.get("description", "").strip()
    skill_version = fm.get("version", "").strip() or "unknown"
    supported_hosts = fm.get("supported_hosts", "").strip()
    summary = _extract_summary(skill_md)

    target = (
        vault
        / "personal-skills"
        / repo_name
        / f"{skill_name}.md"
    )

    if not _entry_needs_refresh(
        target,
        skill_version=skill_version,
        description=description,
    ):
        return {
            "action": "skipped",
            "target": str(target),
            "repo": repo_name,
            "skill": skill_name,
            "reason": "unchanged (same skill_version + description)",
        }

    content = _build_entry_content(
        repo_name=repo_name,
        skill_name=skill_name,
        skill_version=skill_version,
        description=description,
        supported_hosts=supported_hosts,
        source_path=str(skill_md),
        summary=summary,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content.encode("utf-8"))

    return {
        "action": "written",
        "target": str(target),
        "repo": repo_name,
        "skill": skill_name,
        "reason": "",
    }


def discover_skill_md_files(skill_path: Path) -> list[Path]:
    """Find every SKILL.md under a skill-search root.

    Acceptable structures:
      <root>/<skill-name>/SKILL.md   (toolkit's canonical layout)
      <root>/SKILL.md                (root itself is a skill dir)
    """
    skill_path = skill_path.resolve()
    if not skill_path.exists():
        return []
    found: list[Path] = []
    if skill_path.is_file() and skill_path.name == "SKILL.md":
        return [skill_path]
    if not skill_path.is_dir():
        return []
    # First check root-as-skill-dir.
    if (skill_path / "SKILL.md").exists():
        found.append(skill_path / "SKILL.md")
    # Then walk one level deep (canonical layout: <root>/<skill>/SKILL.md).
    # We don't recurse deeper — SKILL.md is a top-of-skill-dir convention,
    # not a free-floating marker.
    for child in skill_path.iterdir():
        if child.is_dir() and (child / "SKILL.md").exists():
            found.append(child / "SKILL.md")
    # Deduplicate (root-as-skill + child-as-skill can collide in edge cases).
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in found:
        if p not in seen:
            unique.append(p)
            seen.add(p)
    return sorted(unique)


def index_skills(
    skill_paths: list[Path],
    *,
    vault: Path,
    repo_name: str | None = None,
) -> dict[str, object]:
    """Index every SKILL.md across the given source paths.

    Returns a summary: {"written": N, "skipped": M, "errors": K,
    "results": [<per-skill status dicts>]}.
    """
    if not vault.exists() or not vault.is_dir():
        raise FileNotFoundError(
            f"vault path does not exist or is not a directory: {vault}"
        )
    if not skill_paths:
        return {"written": 0, "skipped": 0, "errors": 0, "results": []}

    results: list[dict[str, str]] = []
    for skill_path in skill_paths:
        skill_mds = discover_skill_md_files(skill_path)
        if not skill_mds:
            print(
                f"warning: no SKILL.md found under {skill_path}",
                file=sys.stderr,
            )
            continue
        for skill_md in skill_mds:
            try:
                result = index_one_skill(
                    skill_md, vault=vault, repo_name=repo_name
                )
            except Exception as e:
                result = {
                    "action": "error",
                    "target": "",
                    "repo": repo_name or "",
                    "skill": "",
                    "reason": f"{type(e).__name__}: {e}",
                }
            results.append(result)

    written = sum(1 for r in results if r["action"] == "written")
    skipped = sum(1 for r in results if r["action"] == "skipped")
    errors = sum(1 for r in results if r["action"] == "error")
    return {
        "written": written,
        "skipped": skipped,
        "errors": errors,
        "results": results,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="memory-index-skills",
        description=(
            "Walk SKILL.md across configured skill paths; write one "
            "skill-pointer entry per skill to MemoryVault/personal-skills/."
            " Idempotent — unchanged entries are skipped."
        ),
    )
    parser.add_argument(
        "--skill-path",
        action="append",
        default=None,
        help=(
            "skill source directory to walk (repeatable). Falls back to "
            "MEMORY_SKILL_PATHS env (colon-separated) if no --skill-path "
            "given. Required: at least one source must resolve."
        ),
    )
    parser.add_argument(
        "--vault-path",
        default=None,
        help=(
            "MemoryVault root (overrides MEMORY_VAULT_PATH env var). "
            "Required if env var is unset."
        ),
    )
    parser.add_argument(
        "--repo-name",
        default=None,
        help=(
            "explicit repo-slug to use for ALL discovered skills (overrides "
            "auto-detection from .git/AGENTS.md ancestor walk). Useful when "
            "skill paths don't sit under a git repo."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        vault = _resolve_vault_path(args.vault_path)
        if not vault.exists() or not vault.is_dir():
            print(
                f"ERROR: vault path does not exist or is not a directory: "
                f"{vault}",
                file=sys.stderr,
            )
            return 1
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    skill_paths = _resolve_skill_paths(args.skill_path)
    if not skill_paths:
        print(
            "ERROR: no skill paths configured. Pass --skill-path (repeatable) "
            "or set MEMORY_SKILL_PATHS env (colon-separated).",
            file=sys.stderr,
        )
        return 1

    summary = index_skills(
        skill_paths,
        vault=vault,
        repo_name=args.repo_name,
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary["errors"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
