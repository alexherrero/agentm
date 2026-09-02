#!/usr/bin/env python3
"""Gate: no note enters the corpus carrying a vocabulary value the contract
does not know.

The census that motivated filing-v2 found the enum-membership rule was the one
gate the contract never had: the type-XOR-kind rule held at zero violations
while `kind:` drifted to 38 in-use values against a 32-value register. The
values themselves were never checked — only their exclusivity was.

This gate closes that hole with **set-ratchet semantics**, because the live
corpus carries known legacy drift a flat hard-fail would block unrelated work
on (the same reason `check-kind-taxonomy` is advisory):

  A recorded baseline names every currently-tolerated offender — (path, value)
  pairs, not a count, so a swap (one fixed, one newly broken) cannot hide
  inside a stable total. Any offender **not in the baseline fails the
  battery**: enforcement on new writes is immediate. Offenders that disappear
  ratchet the baseline down automatically; it never grows. The corpus
  migration (filing-v2 part 3) drains the baseline to empty and flips this
  gate's check-all invocation to `--strict`, where the baseline is ignored and
  any violation fails.

Retired values (the deprecations map has a replacement) are deliberately not
violations here — they are migration-pending by definition, and counting them
would make this gate red until part 3 for reasons already known and mapped.

The cross-vocabulary **collision** rule (no word legal as both a memory type
and a record kind) lives in the contract's own parser, in Go, and fires on
every parse. `--self-test` proves both rules executable without touching the
live corpus: a collision fixture must be refused by the daemon, and a scratch
vault with an unregistered value must be caught by the audit.

Usage:
  python3 scripts/check-vocabulary-membership.py --self-test   # fixture proof, no vault
  python3 scripts/check-vocabulary-membership.py               # ratchet vs baseline
  python3 scripts/check-vocabulary-membership.py --strict      # baseline ignored

Environment:
  MEMORY_VAULT_PATH              the corpus root; unset → corpus mode skips (CI has no vault)
  AGENTM_VOCAB_BASELINE          baseline path override (tests); default
                                 ~/.local/state/agentm/vocabulary-membership-baseline.json
  AGENTMD                        the daemon binary the contract is asked through

Exit:
  0  no new offenders (or corpus mode skipped; or self-test passed)
  1  a new offender appeared, or --strict found any violation, or self-test failed
  2  setup error (contract unavailable)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "harness" / "skills" / "memory" / "scripts"))

import kind_registry  # noqa: E402
import storage_rules  # noqa: E402
from storage_rules import StorageRulesError  # noqa: E402

_DEFAULT_BASELINE = Path.home() / ".local" / "state" / "agentm" / "vocabulary-membership-baseline.json"

# A block whose two registers share a word. The Go validator must refuse it —
# this fixture is what keeps that refusal an executable CI fact rather than a
# remembered one.
_COLLISION_BLOCK = """\
# collision fixture

```storage-rules
classes:
  semantic: a
  procedural: b
  episodic: c
  entities: d
  crystallized: e
  mocs: f
memory_types: [preference, workflow]
default_type: preference
routing: {preference: memory/semantic, workflow: memory/procedural}
record_kinds: [brief, workflow]
deprecations: {}
thresholds: {}
```
"""


def _offenders(audit: dict) -> set:
    """The violation set: unrecognized values plus malformed ones.

    `retired` is excluded on purpose — the deprecations map already names its
    replacement, so it is migration-pending, not unknown.
    """
    return {(path, value) for path, value in audit["unrecognized"]} | \
           {(path, value) for path, value in audit["malformed"]}


class BaselineCorrupt(Exception):
    """The recorded baseline exists but cannot be read.

    Absence falls through (a genuine first run records); corruption halts —
    the same doctrine the rules parser holds, for the same reason. Treating a
    corrupt baseline as a first run would silently re-baseline whatever the
    corpus holds at that moment, which is exactly the adversarial shape the
    ratchet exists to catch: new violations landing at the same time the
    state file goes unreadable. (Caught by adversarial review.)
    """


def _load_baseline(path: Path) -> set | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {(str(p), str(v)) for p, v in raw.get("offenders", [])}
    except (OSError, ValueError, TypeError) as exc:
        raise BaselineCorrupt(
            f"{path}: {exc}. The ratchet's guarantee rests on this file — "
            f"restore it from the vault repo's history, or delete it "
            f"deliberately to accept a fresh baseline, then re-run.") from exc


def _write_baseline(path: Path, offenders: set) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"offenders": sorted([list(pair) for pair in offenders])}, indent=2) + "\n",
        encoding="utf-8")


def run_corpus_check(vault: Path, baseline_path: Path, *, strict: bool) -> int:
    audit = kind_registry.audit(vault)
    current = _offenders(audit)
    retired = len(audit["retired"])

    print(f"check-vocabulary-membership: {audit['total_files']} notes scanned, "
          f"{len(current)} unregistered-value offender(s), {retired} retired-value "
          f"note(s) awaiting the migration")

    if strict:
        if current:
            print("\nSTRICT: every offender fails once the migration has drained the corpus:")
            for path, value in sorted(current):
                print(f"  {path}: {value!r}")
            return 1
        return 0

    baseline = _load_baseline(baseline_path)
    if baseline is None:
        _write_baseline(baseline_path, current)
        print(f"baseline recorded at {baseline_path} — {len(current)} legacy offender(s) "
              f"tolerated until the corpus migration drains them; anything new fails from "
              f"the next run on")
        return 0

    new = current - baseline
    if new:
        print(f"\nFAIL: {len(new)} offender(s) not in the recorded baseline — a new write "
              f"carried a value neither register knows:")
        for path, value in sorted(new):
            print(f"  {path}: {value!r}")
        print("\nRegister the value (with its warrant, if a type), map it in "
              "`deprecations`, or fix the note. The baseline never grows.")
        return 1

    if current != baseline:
        _write_baseline(baseline_path, current)
        print(f"ratcheted down: {len(baseline)} → {len(current)} tolerated offender(s)")
    return 0


def run_self_test() -> int:
    """Prove both rules fire, against fixtures, with no live corpus involved."""
    failures = []

    # 1. The collision rule: the contract's own parser refuses a shared word.
    with tempfile.TemporaryDirectory() as td:
        fixture = Path(td) / "collision.md"
        fixture.write_text(_COLLISION_BLOCK, encoding="utf-8")
        try:
            storage_rules.load_file(fixture)
            failures.append("collision fixture PARSED — the both-registers rule is not firing")
        except StorageRulesError as exc:
            if "both" not in str(exc):
                failures.append(f"collision fixture refused, but not by the collision rule: {exc}")

    # 2. The membership rule: an unregistered value is caught by the audit.
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td)
        notes = vault / "memory" / "semantic"
        notes.mkdir(parents=True)
        (notes / "stray.md").write_text(
            "---\nkind: definitely-not-registered\n---\n\nbody\n", encoding="utf-8")
        audit = kind_registry.audit(vault)
        if ("memory/semantic/stray.md", "definitely-not-registered") not in _offenders(audit):
            failures.append("an unregistered value in a scratch vault was not caught by the audit")

    if failures:
        for f in failures:
            print(f"self-test FAIL: {f}")
        return 1
    print("check-vocabulary-membership: self-test OK — collision refused by the parser, "
          "unregistered value caught by the audit")
    return 0


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description="vocabulary membership gate (set-ratchet)")
    parser.add_argument("--self-test", action="store_true",
                        help="prove the collision + membership rules on fixtures; no vault needed")
    parser.add_argument("--strict", action="store_true",
                        help="ignore the baseline; any violation fails (post-migration mode)")
    args = parser.parse_args(argv)

    try:
        if args.self_test:
            return run_self_test()

        # The bare invocation is the battery's: prove the rules fire on
        # fixtures first, then ratchet the live corpus when one resolves.
        code = run_self_test()
        if code != 0:
            return code

        vault = os.environ.get("MEMORY_VAULT_PATH", "").strip()
        if not vault or not Path(vault).is_dir():
            print("check-vocabulary-membership: MEMORY_VAULT_PATH unset or not a directory — "
                  "corpus mode skipped (the self-test half runs in CI regardless)")
            return 0
        baseline = Path(os.environ.get("AGENTM_VOCAB_BASELINE", "").strip() or _DEFAULT_BASELINE)
        return run_corpus_check(Path(vault), baseline, strict=args.strict)
    except BaselineCorrupt as exc:
        print(f"check-vocabulary-membership: HALT — the baseline is corrupt, and "
              f"corruption halts where absence falls through: {exc}", file=sys.stderr)
        return 2
    except StorageRulesError as exc:
        print(f"check-vocabulary-membership: SETUP — the filing contract is unavailable: {exc}",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
