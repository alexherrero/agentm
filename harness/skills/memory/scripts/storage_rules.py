#!/usr/bin/env python3
"""storage_rules.py — the runtime-read filing contract.

`standards/storage-rules.md` is authoritative for filing. This module resolves
that file, extracts its fenced machine-readable block, parses it, and validates
its shape. Everything downstream — the frontmatter validator, the kind registry,
lint, and (from part 4) enrichment's output schema — reads its enums from here
rather than from a hardcoded list of its own, so a type added to the rules file
exists everywhere at once and a type absent from it exists nowhere.

The point of the arrangement is that filing behaviour changes by editing
markdown. No recompile, no release: the rules take effect on the next read.

Resolution order — first source that exists wins:

  1. ``$AGENTM_STORAGE_RULES`` — an explicit path, for tests and one-off runs.
  2. ``<vault>/standards/storage-rules.md`` — the live instance. Two probes,
     because `$MEMORY_VAULT_PATH` names the *memory root* and the vault root is
     its parent in the split layout (`<vault>/Agent/` holds memory, `<vault>/
     standards/` holds the rules). A flat vault, where the two are the same
     directory, is probed first.
  3. The packaged default beside this skill. This is the seed a vault instance
     is created from, and it is what makes the enums exist in a checkout with no
     vault attached — CI, a fresh clone, a unit test.

**Absence falls through; corruption halts.** A source that is not there is not
an error, and resolution moves on. A source that *is* there and whose block will
not parse, or parses to the wrong shape, raises `StorageRulesError` and never
falls back to the next source — falling back would be exactly the "model reading
a malformed rule improvises around it" failure the fail-closed design exists to
prevent. `load()` reports which source won, so a caller running on the packaged
default can say so rather than implying it read the operator's rules.

The block is fenced as ```` ```storage-rules ```` and its body is YAML. Required
keys are `classes`, `memory_types`, `record_kinds`, `routing`, `thresholds` and
`deprecations`; `warrants` is required to be present but may be empty. Two
registers carry the taxonomy:

  memory_types   the six values enrichment assigns, growth-rule-braked. A note
                 in one of the three observational classes carries one, in its
                 `type:` field.
  record_kinds   infrastructure record shapes — briefs, telemetry, the *-index
                 family, personas, maps of content. These are not memories, so
                 they carry no `type` at all; their `kind:` field names their
                 shape. Registered here so the set is still closed and still
                 braked, rather than growing free-form as `kind:` always has.

A note carries `type` **or** `kind`, never both. That is checked by the
frontmatter validator, not here — this module owns the vocabulary, not its use.

Usage:
    python3 storage_rules.py --check              # resolve, parse, validate
    python3 storage_rules.py --check PATH         # check one specific file
    python3 storage_rules.py --show               # print the parsed block
    python3 storage_rules.py --hash               # print the block content hash

Exit:
    0  the rules resolve and parse
    1  a resolved rules file failed to parse or failed shape validation
    2  setup error (PyYAML missing, an explicit path that does not exist)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# The packaged default lives beside the skill rather than in the repo's
# templates/ tree, so it travels with the skill wherever the skill is installed
# and needs no cross-tree path assumption to find.
PACKAGED_DEFAULT = _SCRIPTS_DIR.parent / "storage-rules.default.md"

# The relative location of the rules file inside a vault. Both probes are run
# against the resolved memory root: the flat layout first, then the split one.
_VAULT_RELATIVE = ("standards/storage-rules.md", "../standards/storage-rules.md")

_BLOCK_RE = re.compile(
    r"^```storage-rules[ \t]*\r?\n(?P<body>.*?)^```[ \t]*(?:\r?\n|\Z)",
    re.MULTILINE | re.DOTALL,
)

_KEBAB = re.compile(r"^[a-z0-9-]+$")

# Every key the block must carry. `warrants` must be present but may be empty —
# it starts empty and fills as types are added under the growth rule.
_REQUIRED_KEYS = ("classes", "memory_types", "record_kinds", "routing",
                  "thresholds", "deprecations", "warrants")

# The three classes enrichment may file into. The other three are derived and
# rebuildable, and enrichment can never write there (parent design, "Class
# membership"). Pinned here rather than read from the block: the observational/
# derived split is a structural property of the layout, not a tunable.
OBSERVATIONAL_CLASSES = ("semantic", "procedural", "episodic")
DERIVED_CLASSES = ("entities", "crystallized", "mocs")


class StorageRulesError(Exception):
    """A resolved rules file will not parse, or parsed to the wrong shape.

    Raised rather than returned, and never swallowed into a fallback: filing
    halts, notes wait as `unfiled`, and the digest names the failure.
    """


class StorageRules:
    """One parsed rules file, plus where it came from."""

    def __init__(self, data: dict, *, source: Path, is_packaged_default: bool,
                 block_text: str):
        self._data = data
        self.source = source
        self.is_packaged_default = is_packaged_default
        self._block_text = block_text

    # ── the registers ──────────────────────────────────────────────────────

    def classes(self) -> dict[str, str]:
        """`{class_name: one-line meaning}` for the six retrieval classes."""
        return dict(self._data["classes"])

    def memory_types(self) -> frozenset[str]:
        """The enum enrichment assigns to a memory's `type:` field."""
        return frozenset(self._data["memory_types"])

    def record_kinds(self) -> frozenset[str]:
        """Infrastructure record shapes, carried in `kind:`. Not memories."""
        return frozenset(self._data["record_kinds"])

    def routing(self) -> dict[str, str]:
        """`{memory_type: destination}` — where a type of memory is filed."""
        return dict(self._data["routing"])

    def thresholds(self) -> dict:
        """Tunables the filing passes read (sizes, ceilings, confidence bars)."""
        return dict(self._data["thresholds"])

    def deprecations(self) -> dict[str, str]:
        """`{retired_value: replacement}` — the collapse map, mechanical."""
        return dict(self._data["deprecations"])

    def warrants(self) -> dict[str, dict]:
        """`{memory_type: {query_class, nearest, why_not}}` — the growth rule's
        evidence. A type added to `memory_types` carries one; the gate checks
        it in the same diff."""
        return dict(self._data.get("warrants") or {})

    # ── derived views ──────────────────────────────────────────────────────

    def known_values(self) -> frozenset[str]:
        """Every value either register recognizes. The union is what a taxonomy
        audit compares a live corpus against."""
        return self.memory_types() | self.record_kinds()

    def resolve_deprecated(self, value: str) -> str | None:
        """The replacement for a retired value, or None if it is not retired.

        Returns None for a value that is already current — callers distinguish
        "nothing to do" from "unmappable" by testing membership in
        `known_values()` themselves."""
        return self.deprecations().get(value)

    def content_hash(self) -> str:
        """The block's content hash — `rules_hash` in a memory's frontmatter.

        Over the block's *parsed* content, canonically serialized, not its raw
        text: reformatting the YAML or editing the prose around it must not
        invalidate every judgment in the corpus. Changing what the block says
        must."""
        canonical = json.dumps(self._data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    def as_dict(self) -> dict:
        """The parsed block, for display and for tests."""
        return dict(self._data)


# ── parsing ────────────────────────────────────────────────────────────────

def extract_block(text: str, *, origin: str) -> str:
    """Pull the fenced `storage-rules` block body out of a rules file."""
    match = _BLOCK_RE.search(text)
    if match is None:
        raise StorageRulesError(
            f"{origin}: no ```storage-rules fenced block found. The machine-"
            f"readable core is what every consumer reads; prose alone is not a "
            f"rules file."
        )
    return match.group("body")


def parse_block(body: str, *, origin: str) -> dict:
    """Parse the block body as YAML and validate its shape."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise StorageRulesError(
            f"{origin}: PyYAML is not installed, so the rules block cannot be "
            f"parsed. Run `pip install pyyaml`."
        ) from exc

    try:
        data = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        raise StorageRulesError(f"{origin}: the rules block is not valid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise StorageRulesError(
            f"{origin}: the rules block parsed to {type(data).__name__}, not a mapping."
        )

    _validate_shape(data, origin=origin)
    return data


def _validate_shape(data: dict, *, origin: str) -> None:
    """Every required key present, and each one the right shape and vocabulary.

    Shape validation is as load-bearing as the parse. A block that is valid YAML
    but names a class that does not exist, or routes a type nowhere, is a
    malformed rule the model would otherwise be handed to interpret.
    """
    missing = [k for k in _REQUIRED_KEYS if k not in data]
    if missing:
        raise StorageRulesError(
            f"{origin}: the rules block is missing required key(s): {', '.join(missing)}"
        )

    for key in ("classes", "routing", "thresholds", "deprecations"):
        if not isinstance(data[key], dict):
            raise StorageRulesError(
                f"{origin}: `{key}` must be a mapping, got {type(data[key]).__name__}."
            )
    for key in ("memory_types", "record_kinds"):
        if not isinstance(data[key], list):
            raise StorageRulesError(
                f"{origin}: `{key}` must be a list, got {type(data[key]).__name__}."
            )
    if data.get("warrants") is not None and not isinstance(data["warrants"], dict):
        raise StorageRulesError(
            f"{origin}: `warrants` must be a mapping or empty, got "
            f"{type(data['warrants']).__name__}."
        )

    expected_classes = set(OBSERVATIONAL_CLASSES) | set(DERIVED_CLASSES)
    declared_classes = set(data["classes"])
    if declared_classes != expected_classes:
        missing_c = sorted(expected_classes - declared_classes)
        extra_c = sorted(declared_classes - expected_classes)
        detail = []
        if missing_c:
            detail.append(f"missing {', '.join(missing_c)}")
        if extra_c:
            detail.append(f"unknown {', '.join(extra_c)}")
        raise StorageRulesError(
            f"{origin}: `classes` must name exactly the six retrieval classes "
            f"({'; '.join(detail)}). A class is a directory, and a directory is "
            f"close to permanent — adding one is a design change, not a rules edit."
        )

    types = data["memory_types"]
    kinds = data["record_kinds"]
    for label, values in (("memory_types", types), ("record_kinds", kinds)):
        for value in values:
            if not isinstance(value, str) or not _KEBAB.match(value):
                raise StorageRulesError(
                    f"{origin}: `{label}` entry {value!r} is not kebab-case."
                )
        if len(set(values)) != len(values):
            raise StorageRulesError(f"{origin}: `{label}` contains duplicate entries.")

    overlap = set(types) & set(kinds)
    if overlap:
        raise StorageRulesError(
            f"{origin}: {', '.join(sorted(overlap))} appears in both `memory_types` "
            f"and `record_kinds`. A value is a memory type or a record kind, never "
            f"both — the two registers are what keep `type` and `kind` from meaning "
            f"the same thing."
        )

    unrouted = sorted(set(types) - set(data["routing"]))
    if unrouted:
        raise StorageRulesError(
            f"{origin}: memory type(s) {', '.join(unrouted)} have no `routing` "
            f"entry. A type with nowhere to go files nowhere."
        )
    stray_routes = sorted(set(data["routing"]) - set(types))
    if stray_routes:
        raise StorageRulesError(
            f"{origin}: `routing` names {', '.join(stray_routes)}, which is not a "
            f"memory type."
        )

    known = set(types) | set(kinds)
    bad_targets = sorted(
        {v for v in data["deprecations"].values() if v not in known}
    )
    if bad_targets:
        raise StorageRulesError(
            f"{origin}: `deprecations` maps to unknown value(s): "
            f"{', '.join(bad_targets)}. A collapse map that points at a value no "
            f"register carries is not mechanical."
        )
    still_live = sorted(set(data["deprecations"]) & known)
    if still_live:
        raise StorageRulesError(
            f"{origin}: {', '.join(still_live)} is listed in `deprecations` and is "
            f"also still registered. A value is retired or current, not both."
        )

    warrants = data.get("warrants") or {}
    for name, warrant in warrants.items():
        if not isinstance(warrant, dict):
            raise StorageRulesError(
                f"{origin}: warrant for {name!r} must be a mapping."
            )
        for field in ("query_class", "nearest", "why_not"):
            if not str(warrant.get(field) or "").strip():
                raise StorageRulesError(
                    f"{origin}: warrant for {name!r} is missing `{field}`."
                )


# ── resolution ─────────────────────────────────────────────────────────────

def candidate_paths(*, vault_path: Path | str | None = None) -> list[tuple[Path, bool]]:
    """The resolution chain, in order, as `(path, is_packaged_default)` pairs.

    Every candidate is returned whether or not it exists — `load()` walks the
    list and takes the first that does. Exposed so tests and the digest can show
    what was probed rather than only what was found.
    """
    chain: list[tuple[Path, bool]] = []

    explicit = os.environ.get("AGENTM_STORAGE_RULES", "").strip()
    if explicit:
        chain.append((Path(explicit).expanduser(), False))

    root = vault_path if vault_path is not None else os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if root:
        base = Path(root).expanduser()
        for rel in _VAULT_RELATIVE:
            chain.append(((base / rel).resolve(), False))

    chain.append((PACKAGED_DEFAULT, True))
    return chain


def load(*, vault_path: Path | str | None = None) -> StorageRules:
    """Resolve, read, parse and validate the rules. Raises on corruption.

    Absence falls through to the next source. Corruption does not: the first
    source that exists is the one that has to parse.
    """
    probed: list[str] = []
    for path, is_default in candidate_paths(vault_path=vault_path):
        probed.append(str(path))
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError) as exc:
            raise StorageRulesError(f"{path}: cannot be read: {exc}") from exc
        block = extract_block(text, origin=str(path))
        data = parse_block(block, origin=str(path))
        return StorageRules(data, source=path, is_packaged_default=is_default,
                            block_text=block)

    raise StorageRulesError(
        "no storage-rules file found, and the packaged default is missing. "
        "Probed: " + "; ".join(probed)
    )


def load_file(path: Path | str) -> StorageRules:
    """Parse one specific rules file. Used by the gate and by tests."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise StorageRulesError(f"{path}: no such file") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise StorageRulesError(f"{path}: cannot be read: {exc}") from exc
    block = extract_block(text, origin=str(path))
    data = parse_block(block, origin=str(path))
    return StorageRules(data, source=path,
                        is_packaged_default=(path.resolve() == PACKAGED_DEFAULT.resolve()),
                        block_text=block)


# ── module-level convenience, for the consumers ────────────────────────────

_CACHE: StorageRules | None = None


def rules(*, refresh: bool = False) -> StorageRules:
    """The resolved rules, cached for the process.

    Cached because the enum consumers ask for it per note, and re-reading the
    file 16,000 times in a lint pass is pointless. `refresh=True` re-reads, which
    is what a long-running daemon does when the watcher sees the file change.
    """
    global _CACHE
    if _CACHE is None or refresh:
        _CACHE = load()
    return _CACHE


def memory_types() -> frozenset[str]:
    return rules().memory_types()


def record_kinds() -> frozenset[str]:
    return rules().record_kinds()


def known_values() -> frozenset[str]:
    return rules().known_values()


def enrichment_schema_enum() -> list[str]:
    """The `type` enum an enrichment output schema constrains against.

    A sorted list rather than a set, because a JSON Schema `enum` is an ordered
    array and a stable order keeps a prompt's cache key stable. Part 4 consumes
    this; it exists here so the schema can never carry its own copy of the six.
    """
    return sorted(memory_types())


def content_hash() -> str:
    return rules().content_hash()


# ── the hash watch ─────────────────────────────────────────────────────────
#
# A rules edit has to be loud, because the one failure a validator cannot catch
# is an edit that is valid and wrong. The guard is announcement: the next nightly
# pass says the rules changed, and says how many memories were judged under the
# old ones. That turns re-filing from a guess into a queue with a length.

_WATCH_RELATIVE = "_meta/storage-rules-state.json"


def hash_watch(memory_root: Path | str, *, current: str | None = None,
               record: bool = True) -> dict:
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

def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="the runtime-read filing contract")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", nargs="?", const="", metavar="PATH",
                       help="resolve (or check PATH) and validate; exit 1 on failure")
    group.add_argument("--show", action="store_true", help="print the parsed block as JSON")
    group.add_argument("--hash", action="store_true", help="print the block content hash")
    parser.add_argument("--vault-path", default=None,
                        help="memory root to probe (overrides MEMORY_VAULT_PATH)")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
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

    where = "packaged default" if loaded.is_packaged_default else "vault"
    print(f"storage-rules: OK — {loaded.source} ({where})")
    print(f"  memory types : {', '.join(sorted(loaded.memory_types()))}")
    print(f"  record kinds : {len(loaded.record_kinds())} registered")
    print(f"  deprecations : {len(loaded.deprecations())} retired values mapped")
    print(f"  rules_hash   : {loaded.content_hash()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
