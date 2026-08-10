#!/usr/bin/env python3
"""check-memory-root-consistency — the two roots must agree about where memory lives.

Two consumers read the kernel config and mean different things by it. The Go
daemon indexes `plugins.obsidian-vault.vault_path` as a whole tree and expresses
where it *writes* through `daemon.spaces`. The Python stack derives its entire
layout — personal/, projects/, _meta/, _moc/, _briefs/ — from
`plugins.obsidian-vault.memory_root`.

Nothing structural forces those two answers to agree, and when they disagreed on
2026-08-10 the corpus forked: the daemon kept writing to `Agent/personal/` while
the Python reflection hooks wrote a shadow `personal/` at the vault root, same
slugs, different bodies. Both halves looked healthy in isolation. That is the
failure this gate exists to make impossible to reintroduce quietly.

The invariant: `daemon.spaces["memory"]` must live at or beneath `memory_root`.

Absent keys are not failures. An install that never set `memory_root` has a
memory root equal to the vault root, and every space is trivially beneath it —
that is the pre-cutover topology and it is still correct.

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

    failures: list[str] = []
    for name, raw in sorted(spaces.items()):
        if not isinstance(raw, str):
            failures.append(f'  {_SPACES_KEY}["{name}"] is not a string')
            continue
        space = _norm(raw)
        if not memory_root:
            # Memory root is the vault root — every space is beneath it.
            continue
        if space == memory_root or space.startswith(memory_root + "/"):
            continue
        failures.append(
            f'  {_SPACES_KEY}["{name}"] = "{space}" is outside memory_root '
            f'"{memory_root}"'
        )

    if not failures:
        where = memory_root or "(vault root)"
        print(f"check-memory-root-consistency: clean (memory_root {where}; "
              f"{len(spaces)} space(s) beneath it)")
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
