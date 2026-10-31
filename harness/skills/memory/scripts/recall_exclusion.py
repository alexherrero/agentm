"""The recall exclusion rule: which projects' notes a session must not be served.

agentm-vault § Projects and tasks, amended 2026-10-31. A project's
`project.yaml` can carry `recall_excludes:`, a list of project slugs, and a
session working on that project gets no recall hit from them. A session works on
a project when its folder, or the main checkout of the git worktree it is in, is
under one of the project's `code_paths` or under the project's own folder in the
vault. The rule is a wall, not a demotion: a project built in a clean room must
not see the work it may not derive from, however relevant a search finds it.

Everything else that reads a binding degrades to "unbound". This module does
not. A rule that cannot be read comes back as `problem`, and the caller withholds
rather than serves, because a session that silently lost its wall looks exactly
like a session that never had one. A folder no readable project claims is
unknown only while some `project.yaml` will not read at all, since that file may
be the one that claims it. A folder nobody can name may be a covered session, so
it is held to every project's rule.

The Go side keeps the same rule in `daemon/internal/projectbind/exclusion.go`.
Standard library only.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import NamedTuple, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import session_binding  # noqa: E402

_READ_FAILURE = "could not read it"

# What `check-project-yaml` holds a rule's entries to. Checked here too because
# an entry such as `completed/port` names a folder that exists, so the rule would
# read as sound and match no note.
_SLUG_SHAPE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class Exclusion(NamedTuple):
    slugs: tuple = ()
    problem: Optional[str] = None

    def hides(self, rel: str, label=None) -> bool:
        """Whether a note belongs to an excluded project: it sits under
        `projects/<slug>/` or `projects/completed/<slug>/`, at any depth so a
        memory root's prefix does not matter, or its `project:` names one."""
        if not self.slugs:
            return False
        want = {s.lower() for s in self.slugs}
        if isinstance(label, str) and label.strip().strip("\"'").lower() in want:
            return True
        parts = str(rel).replace("\\", "/").lower().split("/")
        for i in range(len(parts) - 2):
            if parts[i] != "projects":
                continue
            if parts[i + 1] in want:
                return True
            if parts[i + 1] == "completed" and i + 3 < len(parts) and parts[i + 2] in want:
                return True
        return False


NONE = Exclusion()


class _Rule(NamedTuple):
    slug: str
    codes: tuple
    excludes: tuple
    problem: Optional[str]


def _project_exists(vault_root: Path, slug: str) -> bool:
    return ((vault_root / "projects" / slug).is_dir()
            or (vault_root / "projects" / "completed" / slug).is_dir())


def _read_rules(vault_root: Path) -> list:
    """Every project's rule. A folder that will not open is a rule that could not
    be read, so nothing here may swallow one the way a glob does."""
    rules, seen = [], set()
    root = vault_root / "projects"
    # Live projects first, so a finished copy never shadows the live one.
    for parent in (root, root / "completed"):
        try:
            folders = sorted(parent.iterdir())
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError as e:
            rules.append(_Rule(parent.name, (), (), f"{_READ_FAILURE}: {e}"))
            continue
        for folder in folders:
            if not folder.is_dir():
                continue
            try:
                # Decoded leniently: the reader wants the keys, and a stray byte
                # in a title is not a failure to read the rule.
                text = (folder / "project.yaml").read_bytes().decode("utf-8", "replace")
            except FileNotFoundError:
                continue
            except OSError as e:
                if folder.name not in seen:
                    seen.add(folder.name)
                    rules.append(_Rule(folder.name, (), (), f"{_READ_FAILURE}: {e}"))
                continue
            parsed = session_binding.parse_project_yaml(text)
            slug = parsed.slug or folder.name
            if slug in seen:
                continue
            seen.add(slug)
            problem = None
            if parsed.excludes_malformed:
                problem = "`recall_excludes` is not a list of project slugs"
            for name in parsed.excludes:
                if problem is not None:
                    break
                if not _SLUG_SHAPE.match(name):
                    problem = f"`recall_excludes` entry {name!r} is not a project slug"
                elif not _project_exists(vault_root, name):
                    problem = f"`recall_excludes` names {name!r}, and the vault has no such project"
            codes = tuple(c for c in map(session_binding._real, parsed.code_paths) if c is not None)
            rules.append(_Rule(slug, codes, tuple(parsed.excludes), problem))
    return rules


def _main_checkout_path(directory: Path) -> Optional[Path]:
    """`directory`'s twin in the main checkout when it sits in a linked git
    worktree. A worktree can live anywhere, so its path says nothing about the
    project; its `.git` file does, as `<common>/worktrees/<name>`. The common
    directory is `<clone>/.git`, or the repository itself when it is bare. A
    submodule's `.git` file points elsewhere and is left alone."""
    for top in (directory, *directory.parents):
        marker = top / ".git"
        if marker.is_dir():
            return None
        if not marker.exists():
            continue
        try:
            line = marker.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            return None
        if not line.startswith("gitdir:"):
            return None
        gitdir = Path(os.path.normpath(top / line[len("gitdir:"):].strip()))
        if gitdir.parent.name != "worktrees":
            return None
        main = gitdir.parent.parent
        if main.name == ".git":
            main = main.parent
        return main / directory.relative_to(top)
    return None


def _vault_folder_project(vault_root: Path, directory: Path) -> Optional[str]:
    try:
        parts = directory.relative_to(vault_root).parts
    except ValueError:
        return None
    if len(parts) >= 3 and parts[:2] == ("projects", "completed"):
        return parts[2]
    if len(parts) >= 2 and parts[0] == "projects" and parts[1] != "completed":
        return parts[1]
    return None


def _claiming_rules(rules: list, vault_root: Path, directory: Path) -> list:
    """The rule of every project a session in `directory` works on, for the
    checkout it is in and for the main checkout of a linked worktree. Every claim
    counts, unlike a card's stamp, which wants one project: a worktree inside one
    project's folder or a path two projects list must not lose a rule to whichever
    sorted first. A vault folder with no project.yaml is a project with no rule,
    so it is still a claim. Case is ignored because this filesystem ignores it,
    and a shell can stand in `~/code/Clean` for `~/code/clean`."""
    claims: dict = {}
    vault = Path(str(vault_root).lower())
    for candidate in (directory, _main_checkout_path(directory)):
        if candidate is None:
            continue
        candidate = Path(str(candidate).lower())
        for rule in rules:
            for code in rule.codes:
                code = Path(str(code).lower())
                if candidate == code or code in candidate.parents:
                    claims.setdefault(rule.slug, rule)
        slug = _vault_folder_project(vault, candidate)
        if slug:
            matched = [r for r in rules if r.slug.lower() == slug]
            for rule in matched or [_Rule(slug, (), (), None)]:
                claims.setdefault(rule.slug, rule)
    return list(claims.values())


def _every_rule(rules: list) -> Exclusion:
    """What a session nobody can place is held to: every rule there is."""
    for rule in rules:
        if rule.problem:
            return Exclusion(problem=f"project {rule.slug!r}: {rule.problem}")
    return Exclusion(slugs=tuple(sorted({s for r in rules for s in r.excludes})))


def for_session(directory, memory_root) -> Exclusion:
    """The exclusion a session in `directory` is under: what every project
    claiming the folder excludes, less those projects themselves, since a session
    is never walled out of its own notes. Never raises: anything that goes wrong
    reading the rule is a `problem`, which fails closed.

    A folder a readable project claims is not held up by another project's
    unreadable file. That file could only matter by adding a claim inside this
    one, and stopping every session's recall for one bad file costs more than
    that rarity is worth."""
    if not memory_root:
        return NONE
    try:
        vault_root = session_binding._vault_root(memory_root)
        if vault_root is None:
            return NONE
        vault_root = Path(vault_root).resolve()
        rules = _read_rules(vault_root)
        here = session_binding._real(str(directory)) if directory else None
        if here is None:
            return _every_rule(rules)
        claims = _claiming_rules(rules, vault_root, here)
        if not claims:
            for other in rules:
                if other.problem and other.problem.startswith(_READ_FAILURE):
                    return Exclusion(problem=f"project {other.slug!r}: {other.problem}")
            return NONE
        own = {r.slug.lower() for r in claims}
        slugs: list = []
        for rule in claims:
            if rule.problem:
                return Exclusion(problem=f"project {rule.slug!r}: {rule.problem}")
            slugs += [s for s in rule.excludes if s not in own and s not in slugs]
        return Exclusion(slugs=tuple(slugs))
    except Exception as e:  # noqa: BLE001 — a rule nobody could evaluate is not a clear session
        return Exclusion(problem=f"{type(e).__name__}: {e}")


def current_directory() -> Optional[str]:
    try:
        return os.getcwd()
    except OSError:
        return None
