#!/usr/bin/env python3
"""vault_project — read/write the MemoryVault project slug for this harness install.

Used by the harness's auto-context dispatcher (`scripts/harness_memory.py`) to
know which `projects/<slug>/` directory to recall from + save into. (Legacy
v4.0.x and earlier wrote to `personal-projects/<slug>/`; V4 #26 / v4.1.0+
renames the vault folder. The dispatcher transparently handles both paths
during the transition window.)

Fallback chain for `read_vault_project()`:

    1. Explicit `vault_project` field in `.harness/project.json`
    2. `.harness/project.json` github.repo field (extract `<repo>` from `<owner>/<repo>`)
    3. `git remote get-url origin` → strip `.git` → basename
    4. None (no signal — caller should graceful-skip)

Stdlib-only. Cross-platform via pathlib + subprocess. No third-party deps
(per agentm ADR + crickets ADR 0007 D7).

Usage from another module:

    from vault_project import read_vault_project, write_vault_project
    slug = read_vault_project(Path("/path/to/project"))
    if slug is None:
        ...  # graceful-skip
    else:
        ...  # use slug

Usage from CLI:

    python3 scripts/vault_project.py read [project_root]
    python3 scripts/vault_project.py write <slug> [project_root]

CLI exits 0 + prints slug on success; exits 1 + prints nothing on no-signal.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Optional


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

def read_vault_project(project_root: Path) -> Optional[str]:
    """Return the MemoryVault project slug for this project, or None.

    Walks the fallback chain. Returns None only when no signal is available.
    """
    project_root = Path(project_root)
    data = _load_project_json(project_root)

    # Tier 1: explicit vault_project field
    if isinstance(data, dict):
        explicit = data.get("vault_project")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()

    # Tier 2: github.repo field — extract `<repo>` from `<owner>/<repo>`
    if isinstance(data, dict):
        github = data.get("github")
        if isinstance(github, dict):
            repo_field = github.get("repo")
            if isinstance(repo_field, str) and repo_field.strip():
                # `owner/repo` → `repo`; tolerate bare `repo`
                slug = repo_field.strip().rsplit("/", 1)[-1]
                slug = _strip_git_suffix(slug)
                if slug:
                    return slug

    # Tier 3: git remote get-url origin → strip .git → basename
    origin = _git_origin_url(project_root)
    if origin:
        slug = _slug_from_origin_url(origin)
        if slug:
            return slug

    # No signal
    return None


def write_vault_project(project_root: Path, slug: str) -> Path:
    """Write the vault_project field into `.harness/project.json`.

    Merge-preserves all existing fields. Atomic (write tmp → rename). Creates
    the file (and `.harness/` directory) if absent.

    Returns the absolute path to the written project.json.
    """
    project_root = Path(project_root)
    if not isinstance(slug, str) or not slug.strip():
        raise ValueError("slug must be a non-empty string")
    slug = slug.strip()

    harness_dir = project_root / ".harness"
    harness_dir.mkdir(parents=True, exist_ok=True)

    target = harness_dir / "project.json"
    data = _load_project_json(project_root) or {}
    if not isinstance(data, dict):
        raise ValueError(
            f"existing {target} is not a JSON object — cannot merge-preserve"
        )

    data["vault_project"] = slug

    tmp = target.with_suffix(".json.tmp")
    # ensure_ascii=False keeps unicode-friendly slugs readable;
    # newline="\n" via write_bytes avoids Windows CRLF auto-conversion.
    payload = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    tmp.write_bytes(payload)
    tmp.replace(target)
    return target


# -----------------------------------------------------------------------------
# Internals
# -----------------------------------------------------------------------------

def _load_project_json(project_root: Path) -> Optional[dict]:
    target = project_root / ".harness" / "project.json"
    if not target.is_file():
        return None
    try:
        with target.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _git_origin_url(project_root: Path) -> Optional[str]:
    """Return the `origin` remote URL, or None if not a git repo / no origin."""
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    url = (result.stdout or "").strip()
    return url or None


def _slug_from_origin_url(url: str) -> Optional[str]:
    """Extract repo slug from a git origin URL.

    Handles:
      - HTTPS form: ``https://<host>/owner/repo.git``
      - HTTPS without .git suffix
      - SCP/SSH form: ``user@<host>:owner/repo.git``
      - ssh:// protocol form
      - file:// protocol form for local clones
    """
    if not url:
        return None
    s = url.strip()
    # Drop optional trailing slash
    while s.endswith("/"):
        s = s[:-1]
    # SSH form has `:` between host and path: git@host:owner/repo.git
    # Split on `/` and `:` — last token is the slug stem.
    # Normalize by replacing `:` with `/` then splitting on `/`.
    s_norm = s.replace(":", "/")
    last = s_norm.rsplit("/", 1)[-1]
    last = _strip_git_suffix(last)
    return last or None


def _strip_git_suffix(s: str) -> str:
    if s.endswith(".git"):
        return s[:-4]
    return s


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: vault_project.py {read|write} [args...]", file=sys.stderr)
        return 2

    cmd = argv[1]
    if cmd == "read":
        root = Path(argv[2]) if len(argv) >= 3 else Path.cwd()
        slug = read_vault_project(root)
        if slug is None:
            return 1
        print(slug)
        return 0
    if cmd == "write":
        if len(argv) < 3:
            print("usage: vault_project.py write <slug> [project_root]", file=sys.stderr)
            return 2
        slug = argv[2]
        root = Path(argv[3]) if len(argv) >= 4 else Path.cwd()
        path = write_vault_project(root, slug)
        print(str(path))
        return 0

    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
