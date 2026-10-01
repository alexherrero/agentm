"""The session's binding: which vault project, and which task, a session works on.

agentm-vault § Projects and tasks: every card and trace carries `project:` and
`task:`, stamped from the binding the harness already knows. The project comes
from the session's working folder matched against the `code_paths` of every
`projects/*/project.yaml` — the longest prefix wins, so a worktree under a
repo's clone binds to the repo's project — and falls back to the repo's
`.harness/project.json` (amended 2026-09-28). `.harness/active-plan` names the
task. A directory neither names is not bound, and what it writes carries
neither field. A git remote is never read: a card stamped with a project that
has no vault space is worse than an unstamped one.

A `convention` or `preference` is stamped with no project unless its writer sets
one (`stamps_for_type`): a rule applies in every project, and a label would age
it with one project's activity.

The hooks learn a session's directory from what the host recorded — the
UserPromptSubmit payload's `cwd`, or the `cwd` a transcript's lines carry — so a
batch run over old transcripts stamps each with its own session's binding, not
the directory the batch happens to run in. A desktop session can open in a
scratch workspace and move into its repo later; the host then files the
transcript under the repo's folder, so when no `cwd` in the head binds, that
folder's name does (task 178 review, 2026-09-30).

Standard library only. Every reader degrades to unbound rather than raising.
"""
from __future__ import annotations

import json
import re
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


# The types that stay global: a rule applies in every project.
GLOBAL_TYPES = frozenset({"convention", "preference"})


def _project_yaml(path: Path) -> "tuple[Optional[str], list]":
    """`(slug, code_paths)` from a `project.yaml`, read line by line — the file's
    schema is flat (`check-project-yaml` holds it), and this module stays on the
    standard library."""
    slug, paths, in_paths = None, [], False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None, []
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[:1].isspace() and not line.startswith("-"):
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            in_paths = key == "code_paths" and value == ""
            if key == "slug":
                slug = _component(value.strip("'\""))
            elif key == "code_paths" and value.startswith("[") and value.endswith("]"):
                paths += [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
            continue
        item = line.strip()
        if in_paths and item.startswith("- "):
            paths.append(item[2:].strip().strip("'\""))
    return slug, paths


def _real(path: str) -> Optional[Path]:
    try:
        return Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return None


def project_for_directory(directory, vault_root) -> Optional[str]:
    """The vault project whose `project.yaml` `code_paths` hold `directory`: the
    longest matching path wins. None when no project claims it."""
    if not directory or not vault_root:
        return None
    here = _real(str(directory))
    if here is None:
        return None
    best, best_len = None, -1
    for yaml_path in sorted(Path(vault_root).joinpath("projects").glob("*/project.yaml")):
        slug, paths = _project_yaml(yaml_path)
        slug = slug or _component(yaml_path.parent.name)
        for raw in paths:
            code = _real(raw)
            if code is None:
                continue
            if (here == code or code in here.parents) and len(code.parts) > best_len:
                best, best_len = slug, len(code.parts)
    return best


def _vault_root(memory_root) -> Optional[Path]:
    if not memory_root:
        return None
    try:
        import vault_layout  # same skill dir
        return vault_layout.vault_root_candidates(memory_root)[0]
    except Exception:
        return Path(memory_root)


def read_binding(directory, memory_root=None) -> Binding:
    """The binding of a session that ran in `directory`: the project the vault's
    `project.yaml` files give it when `memory_root` is known, else the one its
    `.harness/project.json` names; the task from `.harness/active-plan`."""
    if not directory:
        return UNBOUND
    harness = Path(directory) / ".harness"
    project = project_for_directory(directory, _vault_root(memory_root))
    if project is not None:
        return Binding(project, _active_task(harness))
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
    return Binding(project, _active_task(harness))


def _active_task(harness: Path) -> Optional[str]:
    try:
        marker = (harness / "active-plan").read_text(encoding="utf-8")
    except OSError:
        return None
    lines = marker.strip().splitlines()
    return task_slug(lines[0]) if lines else None


def transcript_cwds(transcript) -> list:
    """Every distinct directory a transcript's first lines record, in order."""
    out = []
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
                if isinstance(cwd, str) and cwd.strip() and cwd not in out:
                    out.append(cwd)
    except OSError:
        return []
    return out


def transcript_cwd(transcript) -> Optional[str]:
    """The directory a transcript's session ran in, from its first lines."""
    cwds = transcript_cwds(transcript)
    return cwds[0] if cwds else None


def host_folder_name(path: str) -> str:
    """`path` spelled the way Claude Code names a transcript's folder: every
    character but a letter, a digit or a dash becomes a dash."""
    return re.sub(r"[^A-Za-z0-9-]", "-", path.rstrip("/\\"))


def project_for_transcript_folder(folder: str, vault_root) -> Optional[str]:
    """The vault project whose `code_paths` name the folder the host filed a
    transcript under — the path itself, or a dot-directory under it such as a
    worktree's `.claude/worktrees/<slot>`; the longest wins. None when no
    project claims it. A plain dash after the path is not taken: the folder name
    spells `/` and `-` alike, so `pixelcity-poc` would read as under `pixelcity`."""
    if not folder or not vault_root:
        return None
    best, best_len = None, -1
    for yaml_path in sorted(Path(vault_root).joinpath("projects").glob("*/project.yaml")):
        slug, paths = _project_yaml(yaml_path)
        slug = slug or _component(yaml_path.parent.name)
        for raw in paths:
            spellings = {str(Path(raw).expanduser())}
            real = _real(raw)
            if real is not None:
                spellings.add(str(real))
            for spelled in spellings:
                name = host_folder_name(spelled)
                if (folder == name or folder.startswith(name + "--")) and len(name) > best_len:
                    best, best_len = slug, len(name)
    return best


def for_transcript(transcript, fallback=None, memory_root=None) -> Binding:
    """The binding of the first directory in a transcript's head that binds to a
    project; else the project its folder names; else unbound when the head
    records a directory, and `fallback`'s binding when it records none."""
    cwds = transcript_cwds(transcript)
    for cwd in cwds:
        binding = read_binding(cwd, memory_root)
        if binding.project:
            return binding
    project = project_for_transcript_folder(Path(transcript).parent.name, _vault_root(memory_root))
    if project:
        return Binding(project, None)
    if cwds:
        return UNBOUND
    return read_binding(fallback, memory_root) if fallback else UNBOUND


def stamps(binding: Binding) -> dict:
    """The frontmatter fields a binding stamps: only the ones it has."""
    return {k: v for k, v in (("project", binding.project), ("task", binding.task)) if v}


def stamps_for_type(binding: Binding, note_type: "str | None") -> dict:
    """`stamps`, less both fields for a type that stays global."""
    if (note_type or "").strip().lower() in GLOBAL_TYPES:
        return {}
    return stamps(binding)
