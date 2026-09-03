"""Promoting a task into a project, without dragging the workbench across.

`desk/tasks/<slug>/` is the workbench for everything that is not a project —
single-session investigations, follow-ups, anything with a progress log. A
complex task can hold the same shape a project does. The difference is
authorship of the container, not the contents: only the operator creates a
project, and that declaration is what the door is built on.

When a task matures into a project, the project documents are authored **fresh**
and the original task directory is **preserved** as a completed execution log.

# Why fresh rather than moved

A workbench is a record of how the thinking went — false starts, notes to self,
the question asked three different ways. A project's root is its visible face.
Moving the first into the second produces a project whose face is somebody's
scratch paper, and loses the execution log at the same time, because the log was
the thing that got moved.

Nothing moves here for the same reason nothing moves anywhere else in this
design: a path is an address, and addresses that churn break every link that
pointed at them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

TASKS_ROOT = "desk/tasks"
PROJECTS_ROOT = "desk/projects"

# Filing-v2 2b: the project space is the vault-root `Projects/`, a SIBLING of
# the memory root this module writes under. A new project lands there when
# that space exists (discovered, never conjured); a flat scratch vault keeps
# the memory-root layout. The write path is memory-root-relative (the write
# seam joins it onto the vault path); the link is vault-root-relative, which
# is what Obsidian resolves.
ROOT_PROJECTS_DIRNAME = "Projects"


def _root_projects_dir(vault):
    """The vault-root `Projects/` space, discovered never conjured (filing-v2
    2b). Flat layout: `<memory-root>/Projects`. Nested layout — the memory
    root sits inside an Obsidian vault, witnessed by `.obsidian/` at the
    parent and none at the memory root itself: the sibling
    `<vault-root>/Projects`. A memory root at the top of its own vault has no
    sibling, whatever directory named `Projects` sits beside it (its parent
    is the operator's home or a sync folder, where one is common and is not
    the vault's). None when no root space exists."""
    vault = Path(vault)
    flat = vault / "Projects"
    if flat.is_dir():
        return flat
    parent = vault.parent
    if (parent / ".obsidian").is_dir() and not (vault / ".obsidian").is_dir():
        sibling = parent / "Projects"
        if sibling.is_dir():
            return sibling
    return None


def project_dir_for(vault_path, project: str) -> tuple:
    """(memory-root-relative write path, wikilink target) for a new project."""
    vault_path = Path(vault_path)
    root = _root_projects_dir(vault_path)
    if root is not None:
        rel = ROOT_PROJECTS_DIRNAME if root == vault_path / ROOT_PROJECTS_DIRNAME else f"../{ROOT_PROJECTS_DIRNAME}"
        return f"{rel}/{project}", f"{ROOT_PROJECTS_DIRNAME}/{project}"
    return f"{PROJECTS_ROOT}/{project}", f"{PROJECTS_ROOT}/{project}"

# What a promoted task leaves behind in its workbench, so somebody opening the
# old directory learns where the work went rather than finding a dead end.
PROMOTED_MARKER = "promoted-to.md"


@dataclass
class Promotion:
    """One task becoming a project."""

    task: str
    project: str
    # Documents is what the agent authors fresh at the new project's root:
    # filename to body. Empty is allowed and means the promotion made the
    # container and nothing else, which is a real intermediate state.
    documents: dict = field(default_factory=dict)

    @property
    def task_dir(self) -> str:
        return f"{TASKS_ROOT}/{self.task}"

    @property
    def project_dir(self) -> str:
        return f"{PROJECTS_ROOT}/{self.project}"


@dataclass
class PromotionResult:
    project_dir: str
    written: list = field(default_factory=list)
    # Preserved names the workbench that stayed exactly where it was. Named
    # rather than assumed: the claim this whole function makes is that the task
    # directory survives, and a result that did not say which one would leave
    # nothing to check.
    preserved: str = ""

    def as_dict(self) -> dict:
        return {
            "project_dir": self.project_dir,
            "written": self.written,
            "preserved": self.preserved,
        }


def promote(vault_path, promotion: Promotion, *, write=None, exists=None) -> PromotionResult:
    """Author a project fresh from a matured task, preserving the workbench.

    Refuses rather than overwrites when a document already exists at the new
    project's root. A promotion that clobbered one would be doing exactly what
    the project door exists to require alignment for, and doing it in the one
    operation whose whole point is that nothing is dragged across.
    """
    vault_path = Path(vault_path)
    writer = write or _default_write
    present = exists or (lambda rel: (vault_path / rel).exists())

    if not promotion.task or not promotion.project:
        raise ValueError("a promotion needs both a task and a project slug")
    if not present(promotion.task_dir):
        raise ValueError(
            f"{promotion.task_dir} does not exist; a promotion turns a workbench "
            f"that already holds the work into a project, and there is nothing "
            f"here to promote"
        )

    project_dir, project_link = project_dir_for(vault_path, promotion.project)
    res = PromotionResult(project_dir=project_dir)
    for name, body in sorted(promotion.documents.items()):
        rel = f"{project_dir}/{name}"
        if present(rel):
            raise ValueError(
                f"{rel} already exists. A promotion authors fresh; replacing a "
                f"document at a project's root is exactly what the door requires "
                f"alignment for, and doing it here would hide that behind an "
                f"operation named for creating something"
            )
        writer(vault_path / rel, body)
        res.written.append(rel)

    # The workbench is left exactly where it is, with a pointer forward so
    # somebody opening it later learns where the work went.
    marker = f"{promotion.task_dir}/{PROMOTED_MARKER}"
    if not present(marker):
        writer(vault_path / marker, _marker_body(promotion, project_link))
        res.written.append(marker)
    res.preserved = promotion.task_dir
    return res


def _marker_body(promotion: Promotion, project_link: str) -> str:
    return "\n".join([
        "---",
        "type: reference",
        "status: superseded",
        f"title: {promotion.task} became a project",
        "---",
        "",
        f"This workbench matured into [[{project_link}]].",
        "",
        "It is kept as the execution log — the false starts and the notes to "
        "self are the record of how the thinking went, and the project's root "
        "was authored fresh rather than dragged across from here.",
        "",
    ])


def _default_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
