#!/usr/bin/env python3
"""Gate: the filing contract parses, and the taxonomy has not grown unwarranted.

`standards/storage-rules.md` steers the filing engine at runtime, which makes it
the design's best property and its newest failure surface. A typo in it is not a
crash — a model handed a malformed rule improvises around it, and the filing that
results looks fine and is wrong. So the file gets its own gate, and the gate is
the deterministic half of the fail-closed arrangement: a block that will not
parse fails CI here before it ever reaches a nightly run.

Two assertions.

  **parse** — the packaged default parses and validates, always; and the live
  vault instance does too when a vault resolves. A vault that has no rules file
  is not a failure (absence falls through to the packaged default by design); a
  vault whose rules file is corrupt is.

  **growth rule** — a diff that adds a value to `memory_types` carries its
  warrant in the same diff: the query class that needs it, the nearest existing
  type, and why that one does not fit. Fifty-five values accumulated in the old
  taxonomy because every addition was individually defensible and nothing ever
  asked whether the set still cohered. This is the brake that asks.

This gate **fails the battery**. That is the difference between it and
`check-kind-taxonomy`, which is advisory by design because the live corpus has
known data-quality problems a hard gate would block unrelated work on. The rules
file has no such excuse: it is one file, and it is the one everything reads.

Usage:
  python3 scripts/check-storage-rules.py
  python3 scripts/check-storage-rules.py --base main   # growth rule vs. a ref
  python3 scripts/check-storage-rules.py --no-growth-rule

Exit:
  0  the contract parses and the taxonomy is warranted
  1  a rules file failed to parse, or a type was added without a warrant
  2  setup error (the packaged default is missing)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO / "harness" / "skills" / "memory" / "scripts"))

import storage_rules  # noqa: E402
from storage_rules import StorageRulesError  # noqa: E402

# The contract the repo ships, which the daemon embeds. The gate reads it from
# source rather than from the embedded copy so that a diff to this file is what
# the growth rule diffs — the embedded copy is a build artifact of it.
PACKAGED_DEFAULT = _REPO / "daemon" / "internal" / "rules" / "storage-rules.default.md"


def _resolved_vault() -> Path | None:
    """The vault root, or None when this machine has no vault attached."""
    try:
        import harness_memory
    except ImportError:
        return None
    try:
        raw = harness_memory.vault_path()
    except Exception:
        return None
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_dir() else None


def check_parse() -> list[str]:
    """Both rules files parse. Absence of the vault one is fine; corruption is not."""
    failures: list[str] = []

    if not PACKAGED_DEFAULT.is_file():
        return [f"the packaged default is missing: {PACKAGED_DEFAULT}"]
    try:
        packaged = storage_rules.load_file(PACKAGED_DEFAULT)
        print(f"  packaged default : OK  ({len(packaged.memory_types())} memory types, "
              f"{len(packaged.record_kinds())} record kinds, hash {packaged.content_hash()})")
    except StorageRulesError as exc:
        failures.append(f"packaged default: {exc}")
        return failures

    vault = _resolved_vault()
    if vault is None:
        print("  vault instance   : SKIP (no vault resolves on this machine)")
        return failures

    live = vault / "standards" / "storage-rules.md"
    if not live.is_file():
        print(f"  vault instance   : SKIP (none at {live} — falls through to the default)")
        return failures
    try:
        parsed = storage_rules.load_file(live)
    except StorageRulesError as exc:
        failures.append(f"vault instance: {exc}")
        return failures

    print(f"  vault instance   : OK  ({len(parsed.memory_types())} memory types, "
          f"{len(parsed.record_kinds())} record kinds, hash {parsed.content_hash()})")
    if parsed.content_hash() != packaged.content_hash():
        print("  note: the vault instance and the packaged default differ — the vault "
              "wins at runtime, which is the point of the arrangement.")
    return failures


def _git_show(ref: str, rel: str) -> str | None:
    """A file's contents at a ref, or None when it did not exist there."""
    result = subprocess.run(["git", "show", f"{ref}:{rel}"], cwd=_REPO,
                            capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else None


def _default_base() -> str | None:
    """The ref to diff against — the merge base with the default branch."""
    for candidate in ("origin/main", "main"):
        probe = subprocess.run(["git", "rev-parse", "--verify", "--quiet", candidate],
                               cwd=_REPO, capture_output=True, text=True)
        if probe.returncode != 0:
            continue
        base = subprocess.run(["git", "merge-base", "HEAD", candidate], cwd=_REPO,
                              capture_output=True, text=True)
        if base.returncode == 0 and base.stdout.strip():
            return base.stdout.strip()
    return None


def check_growth_rule(base: str | None) -> list[str]:
    """A type added to `memory_types` carries its warrant in the same diff."""
    rel = str(PACKAGED_DEFAULT.relative_to(_REPO)).replace("\\", "/")
    base = base or _default_base()
    if base is None:
        print("  growth rule      : SKIP (no base ref to diff against)")
        return []

    previous_text = _git_show(base, rel)
    if previous_text is None:
        print(f"  growth rule      : SKIP (the rules file is new as of {base[:8]})")
        return []

    # The contract at the base ref goes through the same parser as the current
    # one — a temp file, because the parser reads files rather than strings and
    # having two ways in is how the two drift.
    with tempfile.NamedTemporaryFile("w", suffix=".md", encoding="utf-8", delete=False) as handle:
        handle.write(previous_text)
        previous_path = handle.name
    try:
        previous = storage_rules.load_file(previous_path)
    except StorageRulesError as exc:
        # A broken file at the base ref is history's problem, not this diff's.
        print(f"  growth rule      : SKIP (the rules file at {base[:8]} does not parse: {exc})")
        return []
    finally:
        os.unlink(previous_path)

    current = storage_rules.load_file(PACKAGED_DEFAULT)
    added = sorted(current.memory_types() - previous.memory_types())
    if not added:
        print("  growth rule      : OK  (no memory type added in this diff)")
        return []

    warrants = current.warrants()
    failures = []
    for name in added:
        warrant = warrants.get(name)
        if not warrant:
            failures.append(
                f"`{name}` was added to memory_types with no warrant. A type is added "
                f"when a query class needs to rank by it, and not otherwise — record "
                f"the query class, the nearest existing type, and why that one does "
                f"not fit, in the same edit."
            )
        else:
            print(f"  growth rule      : OK  (`{name}` warranted — "
                  f"{warrant['query_class']}, not {warrant['nearest']})")
    return failures


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="the filing contract's own gate")
    parser.add_argument("--base", default=None,
                        help="git ref to diff the taxonomy against (default: merge-base with main)")
    parser.add_argument("--no-growth-rule", action="store_true",
                        help="check the parse only")
    args = parser.parse_args(argv)

    print("check-storage-rules:")
    failures = check_parse()
    if failures:
        # The growth rule reads the same file; there is nothing to say about a
        # taxonomy that does not parse.
        for line in failures:
            print(f"\nFAIL: {line}", file=sys.stderr)
        return 1

    if not args.no_growth_rule:
        failures += check_growth_rule(args.base)

    if failures:
        for line in failures:
            print(f"\nFAIL: {line}", file=sys.stderr)
        return 1
    print("check-storage-rules: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
