#!/usr/bin/env python3
"""storage_rules.py — the Python side of the filing contract.

`standards/storage-rules.md` decides where a memory goes and what shape it takes,
and it is read at runtime rather than compiled in: filing behaviour changes by
editing markdown, with no recompile and no release.

**This module does not parse that file.** The parser lives in the daemon, in Go —
`daemon/internal/rules` — and this asks it: `agentmd rules --json`, once per run
rather than once per note. One parser, one source of truth. A second
implementation in Python would be a second thing to drift, and the design's whole
claim is that a type added to the rules exists everywhere at once.

What lives here is the Python-side logic the daemon has no reason to carry: how
to read a note's own vocabulary while the corpus is half-migrated, and the hash
watch that makes a rules edit loud in the nightly digest.

**Fail-closed comes through unchanged.** When the daemon cannot resolve or parse
a contract it exits non-zero with the reason, and this raises `StorageRulesError`
rather than guessing — which halts filing, leaves notes `unfiled`, and puts the
parse failure in the digest. The one thing it never does is proceed on a default
it made up. A missing binary is the same condition for the same reason: a machine
with no daemon has no contract to file against, and inventing one is worse than
waiting.

Usage:
    python3 storage_rules.py --check     # ask, and report what came back
    python3 storage_rules.py --show      # the parsed contract as JSON
    python3 storage_rules.py --hash      # the contract's content hash

Exit:
    0  the contract resolves and parses
    1  it does not — filing is halted, and the reason is on stderr
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# The daemon binary, resolved the same way `recall.py` resolves it for the search
# fast path: a bare name on PATH, overridable. `AGENTMD` is the override a test
# or a CI step points at a freshly built binary.
DAEMON_BIN = os.environ.get("AGENTMD", "").strip() or "agentmd"

# How long to wait. Reading and parsing one small file needs milliseconds; the
# budget is generous because it is a hang detector, not a benchmark.
_TIMEOUT_SECONDS = 15

# The three classes filing may write into. The other three are derived and
# rebuildable, and the passes that build them are the only things that write
# there. Mirrored from `rules.ObservationalClasses` / `rules.DerivedClasses`
# rather than read over the wire: the observational/derived split is a structural
# property of the layout, not a tunable, and the Go validator already refuses a
# contract that names a different six.
OBSERVATIONAL_CLASSES = ("semantic", "procedural", "episodic")
DERIVED_CLASSES = ("entities", "crystallized", "mocs")


class StorageRulesError(Exception):
    """The filing contract is unavailable — unresolvable, unparseable, or
    unreachable.

    Raised rather than returned, and never swallowed into a fallback: filing
    halts, notes wait as `unfiled`, and the digest names the failure.
    """


class ContractViolation(Exception):
    """A note's frontmatter breaks the contract in a way no default can paper over."""


class StorageRules:
    """One filing contract, as the daemon reported it."""

    def __init__(self, data: dict):
        self._data = data

    # ── the registers ──────────────────────────────────────────────────────

    def classes(self) -> dict:
        """`{class_name: one-line meaning}` for the six retrieval classes."""
        return dict(self._data.get("classes") or {})

    def memory_types(self) -> frozenset:
        """The enum enrichment assigns to a memory's `type:` field."""
        return frozenset(self._data.get("memory_types") or ())

    def record_kinds(self) -> frozenset:
        """Infrastructure record shapes, carried in `kind:`. Not memories."""
        return frozenset(self._data.get("record_kinds") or ())

    def default_type(self) -> str:
        """What an unlabelled capture lands as."""
        return self._data.get("default_type") or ""

    def routing(self) -> dict:
        """`{memory_type: destination}` — where a type of memory is filed."""
        return dict(self._data.get("routing") or {})

    def thresholds(self) -> dict:
        """Tunables the filing passes read (sizes, ceilings, confidence bars)."""
        return dict(self._data.get("thresholds") or {})

    def deprecations(self) -> dict:
        """`{retired_value: replacement}` — the collapse map, mechanical."""
        return dict(self._data.get("deprecations") or {})

    def model_exempt_spaces(self) -> list:
        """Spaces no background model pass may read."""
        return list(self._data.get("model_exempt_spaces") or [])

    def contract_exempt_spaces(self) -> list:
        """Spaces whose files are documents rather than memories."""
        return list(self._data.get("contract_exempt_spaces") or [])

    def warrants(self) -> dict:
        """`{memory_type: {query_class, nearest, why_not}}` — the growth rule's
        evidence. A type added to `memory_types` carries one; the gate checks it
        in the same diff."""
        return dict(self._data.get("warrants") or {})

    # ── provenance ─────────────────────────────────────────────────────────

    @property
    def source(self) -> str:
        """Where the contract was read from."""
        return self._data.get("source") or ""

    @property
    def is_packaged_default(self) -> bool:
        """True when the copy embedded in the daemon won — which means an edit to
        the operator's own rules file is not taking effect, because there isn't
        one."""
        return bool(self._data.get("is_packaged_default"))

    # ── derived views ──────────────────────────────────────────────────────

    def known_values(self) -> frozenset:
        """Every value either register recognizes. The union is what a taxonomy
        audit compares a live corpus against."""
        return self.memory_types() | self.record_kinds()

    def resolve_deprecated(self, value: str):
        """The replacement for a retired value, or None if it is not retired.

        Returns None for a value that is already current — callers distinguish
        "nothing to do" from "unmappable" by testing membership in
        `known_values()` themselves."""
        return self.deprecations().get(value)

    def content_hash(self) -> str:
        """The contract's content hash — `rules_hash` in a memory's frontmatter.

        Computed by the daemon over the block's parsed content, so rewording the
        prose or reflowing the YAML does not invalidate every judgment in the
        corpus, and changing what the block says does."""
        return self._data.get("hash") or ""

    def as_dict(self) -> dict:
        """The contract, for display and for tests."""
        return dict(self._data)


# ── asking the daemon ──────────────────────────────────────────────────────

def _ask(args: list) -> dict:
    """Run `agentmd rules --json` and return what it said.

    Every failure mode converges on one exception, because every one of them
    means the same thing to a caller: there is no contract to file against.
    """
    binary = shutil.which(DAEMON_BIN) or DAEMON_BIN
    cmd = [binary, "rules", "--json"] + args
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=_TIMEOUT_SECONDS)
    except FileNotFoundError as exc:
        raise StorageRulesError(
            f"the filing contract is unavailable: {DAEMON_BIN} is not on PATH. "
            f"The daemon is what reads `standards/storage-rules.md`; without it "
            f"there is no contract to file against. Set $AGENTMD to a built "
            f"binary, or install the daemon."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise StorageRulesError(
            f"the filing contract is unavailable: {DAEMON_BIN} did not answer "
            f"within {_TIMEOUT_SECONDS}s."
        ) from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or "no reason given"
        raise StorageRulesError(detail)

    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise StorageRulesError(
            f"{DAEMON_BIN} answered with something that is not JSON: {exc}"
        ) from exc


def load(*, vault_path=None) -> StorageRules:
    """Resolve the contract through the daemon. Raises when there isn't one."""
    args = []
    if vault_path:
        args += ["--vault", str(vault_path)]
    return StorageRules(_ask(args))


def load_file(path) -> StorageRules:
    """Parse one specific rules file, through the daemon. Used by tests."""
    return StorageRules(_ask(["--file", str(path)]))


# ── reading a note's vocabulary ────────────────────────────────────────────
#
# The field is `type`, not `kind` — `type` is the one field the Open Knowledge
# Format requires, and renaming during the collapse costs nothing. But a corpus
# of sixteen thousand notes does not rename atomically, so every reader has to
# tolerate both while the migration runs. One accessor does that, and it is the
# only place either field name is spelled.

def note_type(frontmatter: dict):
    """What this note says it is — `type` when present, else `kind`.

    Returns None when the note carries neither, which is legitimate: a capture
    that has not been through a filing judgment yet has no type, and a great many
    non-memory files have no reason to carry one.

    Raises `ContractViolation` when a note carries **both**. Two fields that can
    disagree about what a note is will eventually disagree, and a reader that
    silently prefers one is how a file starts lying about itself.
    """
    written_type = str(frontmatter.get("type") or "").strip()
    written_kind = str(frontmatter.get("kind") or "").strip()
    if written_type and written_kind:
        raise ContractViolation(
            f"a note carries both `type: {written_type}` and `kind: {written_kind}`. "
            f"A note carries one or the other: `type` for a memory, `kind` for a "
            f"record."
        )
    return written_type or written_kind or None


def is_memory(frontmatter: dict) -> bool:
    """True when this note asserts something, as opposed to recording something.

    Memories carry a `type` from the six; records carry a `kind`. A note carrying
    neither is not yet either — an unjudged capture — and answers False.
    """
    value = note_type(frontmatter)
    return value is not None and value in memory_types()


# ── module-level convenience, for the consumers ────────────────────────────

_CACHE = None


def rules(*, refresh: bool = False) -> StorageRules:
    """The resolved contract, cached for the process.

    Cached because the enum consumers ask per note, and spawning a subprocess
    16,000 times in a lint pass is not a design. `refresh=True` re-asks, which is
    what a long-running pass does when the watcher sees the rules file change.
    """
    global _CACHE
    if _CACHE is None or refresh:
        _CACHE = load()
    return _CACHE


def memory_types() -> frozenset:
    return rules().memory_types()


def record_kinds() -> frozenset:
    return rules().record_kinds()


def known_values() -> frozenset:
    return rules().known_values()


def enrichment_schema_enum() -> list:
    """The `type` enum an enrichment output schema constrains against.

    A sorted list rather than a set, because a JSON Schema `enum` is an ordered
    array and a stable order keeps a prompt's cache key stable. Part 4 consumes
    this; it exists here so the schema can never carry its own copy of the six.
    """
    return sorted(memory_types())


def content_hash() -> str:
    return rules().content_hash()


# ── the eligibility gate ───────────────────────────────────────────────────
#
# The design states this rule in the strongest terms it uses anywhere: background
# model passes never read an exempt space. Enrichment skips it, dreaming never
# sends it to a model, no batch call includes it — "enforced as a path rule in
# the eligibility gate rather than as a convention."
#
# So it is a function that refuses, and it exists before the pass that would
# violate it. This repo has already shipped a criterion whose reader never
# arrived; a privacy boundary written after the thing it bounds is the same bet
# with a much worse loss.
#
# The contract is parsed once, in Go, and asked for once per run. The check
# itself is a path-prefix test applied locally, because a per-note subprocess for
# a string comparison would be absurd — and `test_eligibility_parity.py` drives
# the same table through both implementations so the two cannot drift.

def in_space(rel, spaces) -> bool:
    """Whether a vault-relative path sits in one of `spaces`.

    Matched on the first path segment, case-insensitively. A space is a top-level
    directory: matching deeper would let a folder named `personal` anywhere in
    the tree inherit a rule written about the operator's own, and macOS treats
    `Personal/` and `personal/` as one directory, so a case-sensitive rule would
    be a hazard rather than a precision.
    """
    if not spaces:
        return False
    rel = str(rel).replace("\\", "/")
    if rel.startswith("./"):
        rel = rel[2:]
    first = rel.split("/", 1)[0].lower()
    return any(first == str(s).strip().strip("/").lower() for s in spaces)


def may_read_with_model(rel) -> bool:
    """The eligibility gate's path rule, for every background pass.

    Foreground recall is deliberately not covered. The operator reading their own
    notes in their own session is the operator reading their own notes; what this
    bars is the machinery that runs unattended.
    """
    return not in_space(rel, rules().model_exempt_spaces())


def is_contract_exempt(rel) -> bool:
    """Whether a path's files are documents rather than memories, so a missing
    `type` or `status` there is the expected state rather than a finding."""
    return in_space(rel, rules().contract_exempt_spaces())


# ── the hash watch ─────────────────────────────────────────────────────────
#
# A rules edit has to be loud, because the one failure a validator cannot catch
# is an edit that is valid and wrong. The guard is announcement: the next nightly
# pass says the rules changed, and says how many memories were judged under the
# old ones. That turns re-filing from a guess into a queue with a length.

_WATCH_RELATIVE = "_meta/storage-rules-state.json"


def hash_watch(memory_root, *, current=None, record: bool = True) -> dict:
    """Compare the current rules hash against the last one seen, and record it.

    Returns `{"current", "previous", "changed", "first_run"}`. The state file
    lives under the memory root's `_meta/` rather than in the index, because it
    is one of the few things a corpus rescan cannot rebuild — no note records
    which rules version the *previous* run read.

    `record=False` reads without writing, which is what a dry run wants.
    """
    current = current or rules().content_hash()
    state_path = Path(memory_root) / _WATCH_RELATIVE

    previous = None
    if state_path.is_file():
        try:
            previous = json.loads(state_path.read_text(encoding="utf-8")).get("rules_hash")
        except (OSError, ValueError, AttributeError):
            # A corrupt watch file loses the comparison for one cycle. It never
            # halts anything: this is bookkeeping, not the contract.
            previous = None

    result = {
        "current": current,
        "previous": previous,
        "changed": previous is not None and previous != current,
        "first_run": previous is None,
    }

    if record and previous != current:
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(
                json.dumps({"rules_hash": current, "previous_rules_hash": previous},
                           indent=2) + "\n",
                encoding="utf-8")
        except OSError:
            pass
    return result


# ── CLI ────────────────────────────────────────────────────────────────────

def _parse_args(argv: list) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="the Python side of the filing contract")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", nargs="?", const="", metavar="PATH",
                       help="ask for the contract (or for PATH); exit 1 when there isn't one")
    group.add_argument("--show", action="store_true", help="print the contract as JSON")
    group.add_argument("--hash", action="store_true", help="print the contract's content hash")
    parser.add_argument("--vault-path", default=None,
                        help="vault root to resolve against (overrides the daemon's own)")
    return parser.parse_args(argv)


def main(argv: list) -> int:
    args = _parse_args(argv)
    try:
        loaded = load_file(args.check) if args.check else load(vault_path=args.vault_path)
    except StorageRulesError as exc:
        print(f"storage-rules: FAIL — {exc}", file=sys.stderr)
        return 1

    if args.show:
        print(json.dumps(loaded.as_dict(), indent=2, sort_keys=True))
        return 0
    if args.hash:
        print(loaded.content_hash())
        return 0

    where = "the daemon's embedded default" if loaded.is_packaged_default else loaded.source
    print(f"storage-rules: OK — {where}")
    print(f"  memory types : {', '.join(sorted(loaded.memory_types()))}")
    print(f"  default type : {loaded.default_type()}")
    print(f"  record kinds : {len(loaded.record_kinds())} registered")
    print(f"  deprecations : {len(loaded.deprecations())} retired values mapped")
    print(f"  rules_hash   : {loaded.content_hash()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
