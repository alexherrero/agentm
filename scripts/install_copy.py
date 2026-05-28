#!/usr/bin/env python3
"""install_copy — SHA256-aware copy primitive for release-mode install.

Release mode of V4 #30 plan #22 task 5. When the operator does NOT have
source-clone canonical paths, install copies the customizations subset
from a downloaded release tarball (or another pre-extracted source) into
the install prefix. Mirrors what source-mode symlinks do, but with byte
copies.

Idempotency model:
- Each customization file's SHA256 is compared between source + target.
- Same SHA            → no-op ("skipped")
- Source SHA differs, target SHA matches a previously-recorded install
  SHA in install-state.json (or is absent locally) → safe to update
  ("updated")
- Source SHA differs, target SHA also differs from the previously-recorded
  install SHA → local divergence detected; skip with warn ("conflicts").
  Operator can re-run with --force to override.

Conflict policy is the conservative one: NEVER silently overwrite a file
that has been edited by the operator post-install. The operator may have
hand-edited a settings fragment, a hook script, etc. — the safe default
is skip-with-warn; --force is the explicit override.

Stdlib-only (ADR 0001). Per V4 #30 plan #22 task 5.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Optional


_BUF_SIZE = 65536


def _sha256(path: Path) -> str:
    """Return hex SHA256 of a file's contents. Empty string if absent."""
    if not path.is_file():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(_BUF_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _iter_files(source_dir: Path) -> list[Path]:
    """Walk source_dir recursively; return all regular files (sorted)."""
    out: list[Path] = []
    if not source_dir.is_dir():
        return out
    for p in sorted(source_dir.rglob("*")):
        if p.is_file() and not p.name.startswith("."):
            out.append(p)
    return out


def copy_customizations(
    source_dir: Path | str,
    install_prefix: Path | str,
    *,
    install_state: Optional[dict] = None,
    force: bool = False,
) -> dict:
    """Copy all customizations from `source_dir` into `install_prefix`.

    `source_dir` is the root of a pre-extracted release tarball (or a
    source-clone for testing). Layout under source_dir mirrors what lands
    under install_prefix — each file's source-relative path becomes the
    install-relative path.

    `install_state` (optional) is the previously-persisted state dict from
    install_state.py read_install_state(). If provided, its `installed_shas`
    map is consulted to detect operator-local divergence:

        - If install_state["installed_shas"][rel] == current_target_sha:
          target hasn't been edited since last install → safe to update.
        - Else: divergence; skip with warn (unless --force).

    First-install (no install_state, or no installed_shas entry): copy
    unconditionally + record SHA in returned `installed_shas`.

    Returns: {
        "created":   [...rel paths newly installed],
        "updated":   [...rel paths overwritten with new content],
        "skipped":   [...rel paths byte-identical (no change)],
        "conflicts": [...rel paths with local divergence (skip-with-warn)],
        "installed_shas": {rel: sha256, ...},   # SHAs of files actually in target
    }
    """
    source = Path(source_dir)
    prefix = Path(install_prefix)
    prefix.mkdir(parents=True, exist_ok=True)

    prior_shas: dict[str, str] = {}
    if install_state and isinstance(install_state.get("installed_shas"), dict):
        prior_shas = install_state["installed_shas"]

    result: dict[str, list[str]] = {
        "created": [], "updated": [], "skipped": [], "conflicts": [],
    }
    new_shas: dict[str, str] = {}

    for src_file in _iter_files(source):
        rel = str(src_file.relative_to(source))
        target = prefix / rel
        src_sha = _sha256(src_file)
        tgt_sha = _sha256(target) if target.exists() else ""

        if not target.exists():
            # Fresh install — create
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, target)
            result["created"].append(rel)
            new_shas[rel] = src_sha
            continue

        if src_sha == tgt_sha:
            # Byte-identical → no-op
            result["skipped"].append(rel)
            new_shas[rel] = src_sha
            continue

        # SHAs differ. Check if target diverged from prior install.
        prior = prior_shas.get(rel, "")
        if prior and tgt_sha == prior:
            # Target unchanged since last install → safe to update
            shutil.copy2(src_file, target)
            result["updated"].append(rel)
            new_shas[rel] = src_sha
        elif force:
            # Operator opted in to override divergence
            shutil.copy2(src_file, target)
            result["updated"].append(rel)
            new_shas[rel] = src_sha
        else:
            # Operator-local divergence — skip with warn
            result["conflicts"].append(rel)
            # Keep recording the CURRENT target SHA so future install_state
            # snapshots match reality (not the prior install)
            new_shas[rel] = tgt_sha

    result["installed_shas"] = new_shas
    return result


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SHA256-aware copy of customizations into install prefix.",
    )
    parser.add_argument("source_dir", help="root of pre-extracted source (or release tarball)")
    parser.add_argument("install_prefix", help="install destination (e.g. ~/.claude)")
    parser.add_argument(
        "--prior-state",
        default=None,
        help="optional install_state.json from previous install (for divergence detection)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite locally-divergent files (default: skip with warn)",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    source = Path(os.path.expanduser(args.source_dir))
    prefix = Path(os.path.expanduser(args.install_prefix))

    state = None
    if args.prior_state:
        state_path = Path(os.path.expanduser(args.prior_state))
        if state_path.is_file():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                state = None

    result = copy_customizations(source, prefix, install_state=state, force=args.force)
    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    if result["conflicts"]:
        print(
            f"[install_copy] {len(result['conflicts'])} divergent file(s) skipped; "
            f"re-run with --force to overwrite",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
