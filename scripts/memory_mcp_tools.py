#!/usr/bin/env python3
"""MCP tool implementations for the agentm memory engine.

Registers four tools on a FastMCP instance:
  memory_search  — semantic + keyword search with deleted-entry filtering
  memory_recall  — budgeted, phase-aware entry bundle (phase_recall differentiator)
  memory_append  — write a new entry with idempotency-key deduplication
  memory_forget  — soft-delete (status flip + deleted_at; file NEVER unlinked)

Call register_tools(mcp) to attach all four tools to a FastMCP server instance.
This module has no side effects at import time — safe for in-process test clients.

Requires Python >=3.10.  Imports: harness_memory, vault_lock (in scripts/ on
PYTHONPATH), PyYAML, and the memory toolkit via importlib.util.
"""
from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

import harness_memory
import vault_lock

# ── toolkit loader ────────────────────────────────────────────────────────────

def _load_toolkit(module_name: str, rel_path: str):
    """Load a toolkit module from harness/skills/memory/scripts/ by path."""
    root = Path(__file__).parent.parent  # repo root
    mod_path = root / "harness" / "skills" / "memory" / "scripts" / rel_path
    spec = importlib.util.spec_from_file_location(module_name, mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_recall = _load_toolkit("_agentm_recall", "recall.py")
_save = _load_toolkit("_agentm_save", "save.py")

# ── helpers ───────────────────────────────────────────────────────────────────

def _require_vault() -> Path:
    """Return the configured vault path or raise with a clear remedy."""
    vault = harness_memory.vault_path()
    if vault is None:
        raise RuntimeError(
            "Memory vault is not configured. "
            "Set MEMORY_VAULT_PATH env var or add vault_path to "
            "~/.claude/.agentm-config.json."
        )
    return vault


def _parse_frontmatter(content: str) -> dict:
    """Parse YAML frontmatter from `---`…`---` block.  Returns {} if absent."""
    if not content.startswith("---"):
        return {}
    end = content.find("\n---", 3)
    if end < 0:
        return {}
    fm_text = content[3:end].strip()
    return yaml.safe_load(fm_text) or {}


def _replace_frontmatter(content: str, new_fm: dict) -> str:
    """Rebuild the `---`…`---` frontmatter block, preserving the body."""
    if not content.startswith("---"):
        raise ValueError("Entry has no frontmatter block")
    end = content.find("\n---", 3)
    if end < 0:
        raise ValueError("Entry frontmatter block is not closed")
    # body_after starts at the char after the closing `---` line.
    body_after = content[end + 4:]
    fm_yaml = yaml.dump(new_fm, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return f"---\n{fm_yaml}---{body_after}"


def _idem_tag(key: str) -> str:
    """Return a valid kebab-case tag encoding an idempotency key."""
    h = hashlib.sha256(key.encode()).hexdigest()[:16]
    return f"idem-{h}"


def _find_by_idem_tag(vault: Path, tag: str) -> Optional[dict]:
    """Walk the vault for an entry carrying `tag`.  Returns path dict or None."""
    for md in vault.rglob("*.md"):
        try:
            content = md.read_text(errors="replace")
        except OSError:
            continue
        fm = _parse_frontmatter(content)
        if tag in (fm.get("tags") or []):
            return {
                "id": str(md.relative_to(vault)),
                "slug": fm.get("slug", md.stem),
            }
    return None


_SAFE_SEGMENT_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


def _validate_path_segment(value: str, field: str) -> None:
    """Raise ValueError if `value` is not safe to use as a filesystem path segment."""
    if not value or not _SAFE_SEGMENT_RE.match(value):
        raise ValueError(
            f"Invalid {field!r} value {value!r}: must start with alphanumeric "
            "and contain only alphanumeric, hyphen, or underscore characters. "
            "Path separators and dots are not allowed."
        )


def _make_slug(text: str, max_len: int = 48) -> str:
    """Generate a kebab-case slug from `text`."""
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug.strip())
    slug = re.sub(r"-+", "-", slug).strip("-")[:max_len]
    return slug or "entry"


def _get_snippet(content: str, max_chars: int = 200) -> str:
    """Return the text body snippet from an entry (strips frontmatter)."""
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end >= 0:
            return content[end + 4:].strip()[:max_chars]
    return content.strip()[:max_chars]


# ── tool registration ─────────────────────────────────────────────────────────

def register_tools(mcp) -> None:
    """Register all four memory tools on `mcp` (a FastMCP instance)."""

    @mcp.tool()
    def memory_search(
        query: str,
        scope: str = "all",
        project: Optional[str] = None,
        kind: Optional[str] = None,
        limit: int = 20,
        include_deleted: bool = False,
        cursor: Optional[str] = None,
    ) -> dict:
        """Search memory entries by semantic + keyword similarity.

        Returns a hit-list of entries sorted by relevance score.  Deleted
        entries (status: deleted) are excluded by default; set
        include_deleted=True to include them.  The cursor field is an opaque
        pagination token (reserved for v1.1; always null in v1).
        """
        vault = _require_vault()
        import io
        over_k = max(limit * 2, 40)  # over-fetch to compensate for filtering
        try:
            raw_hits = _recall.query(vault=vault, query_text=query, k=over_k,
                                     stderr=io.StringIO())
        except Exception as exc:
            # Graceful: vec or grep failure → empty result set (don't crash the server)
            import logging
            logging.getLogger("agentm.mcp").warning("memory_search: recall.query failed: %s", exc)
            raw_hits = []

        results = []
        for hit in raw_hits:
            if len(results) >= limit:
                break
            entry_path = vault / hit["path"]
            if not entry_path.is_file():
                continue
            try:
                content = entry_path.read_text(errors="replace")
            except OSError:
                continue
            fm = _parse_frontmatter(content)
            status = fm.get("status", "active")
            if not include_deleted and status == "deleted":
                continue
            if kind and fm.get("kind") != kind:
                continue
            results.append({
                "id": hit["path"],
                "slug": hit["slug"],
                "score": round(float(hit.get("combined", 0.0)), 4),
                "status": status,
                "kind": fm.get("kind"),
                "tags": fm.get("tags") or [],
                "snippet": _get_snippet(content),
            })

        return {"results": results, "total": len(results), "cursor": None}

    @mcp.tool()
    def memory_recall(
        context: str,
        phase: str,
        project: Optional[str] = None,
        budget_tokens: int = 4000,
    ) -> str:
        """Return a budgeted, phase-aware memory bundle.

        Uses phase_recall() to assemble the highest-priority entries for the
        given phase + project, trimmed to budget_tokens.  Each entry in the
        bundle carries authority (durable/volatile), volatility
        (stable/changing), and provenance (source path) annotations.

        context is accepted for API symmetry with future streaming use; it is
        not used in v1 dispatch.
        """
        _require_vault()
        return harness_memory.phase_recall(
            phase=phase,
            project=project,
            budget=budget_tokens,
        )

    @mcp.tool()
    def memory_append(
        content: str,
        kind: str,
        project: Optional[str] = None,
        title: Optional[str] = None,
        tags: Optional[list] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        """Append a new memory entry.

        When idempotency_key is provided and an entry with that key already
        exists, returns the existing entry with deduplicated=true instead of
        writing a second copy.  The key is stored as a hashed tag so it
        survives across re-imports.
        """
        vault = _require_vault()

        # Engine-side path-traversal validation.  MCP annotations are host hints,
        # not enforcement — these checks are the real boundary.
        _validate_path_segment(kind, "kind")
        if project:
            _validate_path_segment(project, "project")

        # Idempotency: check before writing.
        if idempotency_key:
            itag = _idem_tag(idempotency_key)
            existing = _find_by_idem_tag(vault, itag)
            if existing:
                return {**existing, "deduplicated": True}

        slug = _make_slug(title or content[:60])
        actual_tags: list[str] = list(tags or [])
        if idempotency_key:
            actual_tags.append(_idem_tag(idempotency_key))

        # Resolve group: personal by default; projects/<project> if given.
        group = "personal"
        if project:
            projects_seg = (
                "projects" if (vault / "projects").is_dir() else "personal-projects"
            )
            group = f"{projects_seg}/{project}"

        written = _save.save_entry(
            vault_path=vault,
            kind=kind,
            slug=slug,
            body=content,
            group=group,
            tags=actual_tags or None,
        )
        # Defense-in-depth: confirm written path is strictly inside the vault.
        try:
            written.resolve().relative_to(vault.resolve())
        except ValueError:
            written.unlink(missing_ok=True)
            raise ValueError(
                f"save_entry wrote outside the vault root — rejected: {written}"
            )
        return {
            "id": str(written.relative_to(vault)),
            "slug": slug,
            "deduplicated": False,
        }

    @mcp.tool()
    def memory_forget(
        id: str,
        reason: Optional[str] = None,
    ) -> dict:
        """Soft-delete a memory entry.

        Flips status to 'deleted' and stamps deleted_at.  The backing file is
        NEVER unlinked — soft-delete is a hard acceptance criterion for v1
        because synced-vault clients (Google Drive) may hold a locally-cached
        copy; an unlink creates a resurrection race.  Hard-delete is an
        explicit non-goal in the design.

        Calling memory_forget on an already-deleted entry is idempotent
        (returns already_deleted=true).
        """
        vault = _require_vault()

        # Security: reject path traversal attempts.
        try:
            entry_path = (vault / id).resolve()
            entry_path.relative_to(vault.resolve())
        except (ValueError, OSError):
            raise ValueError(f"id {id!r} escapes the vault root — rejected")

        if not entry_path.is_file():
            raise FileNotFoundError(f"Entry not found: {id!r}")

        content = entry_path.read_text()
        fm = _parse_frontmatter(content)

        if fm.get("status") == "deleted":
            return {"id": id, "status": "deleted", "already_deleted": True}

        fm["status"] = "deleted"
        fm["deleted_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if reason:
            fm["delete_reason"] = reason

        new_content = _replace_frontmatter(content, fm)
        vault_lock.atomic_write(entry_path, new_content)

        return {"id": id, "status": "deleted", "already_deleted": False}
