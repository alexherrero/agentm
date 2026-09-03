#!/usr/bin/env python3
"""check-memory-root-consistency — the two roots must agree about where memory lives.

Two consumers read the kernel config and mean different things by it. The Go
daemon indexes `plugins.obsidian-vault.vault_path` as a whole tree and expresses
where it *writes* through `daemon.spaces`. The Python stack derives its layout
from `plugins.obsidian-vault.memory_root` plus the space names in
`plugins.obsidian-vault.spaces`.

Nothing structural forces those two answers to agree, and twice on 2026-08-10
they did not. First the roots disagreed: the daemon kept writing to
`Agent/personal/` while the Python reflection hooks wrote a shadow `personal/`
at the vault root — same slugs, different bodies, 102 files. Then, during the
stage-2 migration, the *names* disagreed while both roots were correct, and the
runner re-seeded a config and five inbox entries into an `Agent/memory/` that
was not yet the real one. Both halves looked healthy in isolation both times.

Two invariants, because the first one alone did not catch the second failure:

1. Every `daemon.spaces` value lives at or beneath `memory_root`.
2. Where both stacks name a space, they must resolve it to the same directory.
   `daemon.spaces["memory"] = Agent/memory` sits beneath memory_root `Agent`
   perfectly well while the Python stack writes `Agent/personal`. Containment is
   not agreement.

Absent keys are not failures. An install that never set `memory_root` has a
memory root equal to the vault root, and every space is trivially beneath it —
that is the pre-cutover topology and it is still correct. A space the Python
side does not name falls through to its built-in default and is not compared.

Usage:
    python3 scripts/check-memory-root-consistency.py [--config PATH]

Exit 0 when consistent (or when there is nothing to check), 1 when the two
disagree, 2 on a malformed config.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_VAULT_PATH_KEY = "plugins.obsidian-vault.vault_path"
_MEMORY_ROOT_KEY = "plugins.obsidian-vault.memory_root"
_SPACES_KEY = "daemon.spaces"
_PY_SPACES_KEY = "plugins.obsidian-vault.spaces"
# The one space that lives BESIDE memory_root by design (filing-v2 2b).
_ROOT_PROJECTS_SIBLING = "Projects"


def _norm(rel: str) -> str:
    """Normalize to a bare relative POSIX prefix, matching both readers."""
    return rel.strip().replace("\\", "/").strip("/")


def _default_config_path() -> Path:
    prefix = os.environ.get("AGENTM_INSTALL_PREFIX", "").strip()
    base = Path(os.path.expanduser(prefix)) if prefix else Path.home() / ".claude"
    return base / ".agentm-config.json"


def check(config_path: Path) -> int:
    if not config_path.is_file():
        # Nothing configured is not a violation — a fresh clone has no config.
        return 0
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        print(f"check-memory-root-consistency: cannot read {config_path}: {exc}",
              file=sys.stderr)
        return 2
    if not isinstance(data, dict):
        print(f"check-memory-root-consistency: {config_path} is not a JSON object",
              file=sys.stderr)
        return 2

    raw_root = data.get(_MEMORY_ROOT_KEY)
    memory_root = _norm(raw_root) if isinstance(raw_root, str) else ""

    spaces = data.get(_SPACES_KEY)
    if not isinstance(spaces, dict) or not spaces:
        # The daemon falls back to its own defaults; nothing to cross-check.
        return 0

    py_spaces_raw = data.get(_PY_SPACES_KEY)
    py_spaces = py_spaces_raw if isinstance(py_spaces_raw, dict) else {}

    failures: list[str] = []
    for name, raw in sorted(spaces.items()):
        if not isinstance(raw, str):
            failures.append(f'  {_SPACES_KEY}["{name}"] is not a string')
            continue
        space = _norm(raw)
        if not memory_root:
            # Memory root is the vault root — every space is beneath it.
            continue
        if name == "projects" and space == _ROOT_PROJECTS_SIBLING:
            # Filing-v2 2b: the project space is the vault-root Projects/, a
            # SIBLING of memory_root by design — the one space allowed outside
            # it. Agreement still has to hold: the Python side must name the
            # same sibling (its memory-root-relative form is ../Projects), or
            # leave the key to its default, which is that form.
            py_raw = py_spaces.get(name)
            if py_raw is None or _norm(str(py_raw)) == "../" + _ROOT_PROJECTS_SIBLING:
                continue
            failures.append(
                f'  {_SPACES_KEY}["{name}"] = "{space}" (the vault-root sibling) but '
                f'{_PY_SPACES_KEY}["{name}"] = "{py_raw}" — the two halves disagree'
            )
            continue
        if not (space == memory_root or space.startswith(memory_root + "/")):
            failures.append(
                f'  {_SPACES_KEY}["{name}"] = "{space}" is outside memory_root '
                f'"{memory_root}"'
            )
            continue

        # Containment is not agreement. `daemon.spaces["memory"] = Agent/memory`
        # sits beneath memory_root `Agent` perfectly well while the Python stack
        # writes to `Agent/personal`, and the corpus forks with both halves
        # looking healthy. That is not hypothetical: the stage-2 migration
        # produced exactly that fork for fourteen minutes on 2026-08-10, and the
        # containment rule above passed throughout. Compare the names.
        py_raw = py_spaces.get(name)
        if not isinstance(py_raw, str):
            continue  # unset on the Python side means "use the built-in default"
        py_full = _norm(f"{memory_root}/{_norm(py_raw)}")
        if py_full != space:
            failures.append(
                f'  space "{name}": the daemon writes "{space}" but the Python '
                f'stack writes "{py_full}" ({_PY_SPACES_KEY}["{name}"] = '
                f'"{_norm(py_raw)}" under memory_root "{memory_root}")'
            )

    if not failures:
        where = memory_root or "(vault root)"
        agreed = sum(1 for n in spaces if isinstance(py_spaces.get(n), str))
        print(f"check-memory-root-consistency: clean (memory_root {where}; "
              f"{len(spaces)} space(s) beneath it, {agreed} name-matched "
              f"against the Python stack)")
        return 0

    print("check-memory-root-consistency: the daemon and the Python stack "
          "disagree about where memory lives", file=sys.stderr)
    for line in failures:
        print(line, file=sys.stderr)
    print(
        "\nA space outside memory_root means the daemon writes memories to one "
        "tree while the Python reflection hooks write them to another. Both "
        "halves look healthy on their own; the corpus silently forks. Fix the "
        "config so every space sits beneath memory_root "
        f"(set it with `agentm_config.py --memory-root <rel>`), or clear "
        "memory_root if the vault root really is the memory root.",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", metavar="PATH", default=None,
                    help="kernel config to check (default: "
                         "$AGENTM_INSTALL_PREFIX/.agentm-config.json → "
                         "~/.claude/.agentm-config.json)")
    args = ap.parse_args(argv)
    path = Path(args.config) if args.config else _default_config_path()
    return check(path)


if __name__ == "__main__":
    sys.exit(main())
