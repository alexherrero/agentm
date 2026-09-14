"""The session's binding: which vault project, and which task, a session works on.

agentm-vault § Projects and tasks: every card and trace carries `project:` and
`task:`, stamped from the binding the harness already knows. The repo's
`.harness/project.json` names the vault project, and `.harness/active-plan`
names the task. A directory with no `project.json` is not bound to a project,
and what it writes carries neither field. A git remote is never read: a card
stamped with a project that has no vault space is worse than an unstamped one.

The hooks learn a session's directory from what the host recorded — the
UserPromptSubmit payload's `cwd`, or the `cwd` a transcript's lines carry — so a
batch run over old transcripts stamps each with its own session's binding, not
the directory the batch happens to run in.

Standard library only. Every reader degrades to unbound rather than raising.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple, Optional

# A transcript's first lines carry the session's `cwd`; nothing past the head is
# read, so a long transcript costs no more than a short one.
TRANSCRIPT_SCAN_LINES = 50


class Binding(NamedTuple):
    project: Optional[str] = None
    task: Optional[str] = None


UNBOUND = Binding()


def _component(value) -> Optional[str]:
    """A value that is one path component, else None."""
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v or v in (".", "..") or "/" in v or "\\" in v or "\x00" in v:
        return None
    return v


def task_slug(raw: str) -> Optional[str]:
    """The task a `.harness/active-plan` marker names: the bare slug, with the
    `PLAN-` prefix and `.md` suffix a marker may carry removed. The same reading
    `harness_memory.resolve_active_plan` gives the marker."""
    s = (raw or "").strip()
    if s.endswith(".md"):
        s = s[: -len(".md")]
    if s.startswith("PLAN-"):
        s = s[len("PLAN-"):]
    if not s or s == "PLAN":
        return None
    return _component(s)


def read_binding(directory) -> Binding:
    """The binding of a session that ran in `directory`."""
    if not directory:
        return UNBOUND
    harness = Path(directory) / ".harness"
    try:
        data = json.loads((harness / "project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return UNBOUND
    if not isinstance(data, dict):
        return UNBOUND
    project = _component(data.get("vault_project"))
    if project is None:
        github = data.get("github")
        repo = github.get("repo") if isinstance(github, dict) else None
        if isinstance(repo, str) and repo.strip():
            name = repo.strip().rsplit("/", 1)[-1]
            if name.endswith(".git"):
                name = name[: -len(".git")]
            project = _component(name)
    if project is None:
        return UNBOUND
    try:
        marker = (harness / "active-plan").read_text(encoding="utf-8")
    except OSError:
        marker = ""
    lines = marker.strip().splitlines()
    return Binding(project, task_slug(lines[0]) if lines else None)


def transcript_cwd(transcript) -> Optional[str]:
    """The directory a transcript's session ran in, from its first lines."""
    try:
        with open(transcript, encoding="utf-8", errors="replace") as fh:
            for n, line in enumerate(fh):
                if n >= TRANSCRIPT_SCAN_LINES:
                    break
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                cwd = entry.get("cwd") if isinstance(entry, dict) else None
                if isinstance(cwd, str) and cwd.strip():
                    return cwd
    except OSError:
        return None
    return None


def for_transcript(transcript, fallback=None) -> Binding:
    """The binding of the directory a transcript's session ran in, else of
    `fallback` when the transcript records none."""
    cwd = transcript_cwd(transcript)
    if cwd:
        return read_binding(cwd)
    return read_binding(fallback) if fallback else UNBOUND


def stamps(binding: Binding) -> dict:
    """The frontmatter fields a binding stamps: only the ones it has."""
    return {k: v for k, v in (("project", binding.project), ("task", binding.task)) if v}
