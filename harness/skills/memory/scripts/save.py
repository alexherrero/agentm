#!/usr/bin/env python3
# save.py — canonical /memory save primitive.
#
# Writes a markdown entry to MemoryVault with YAML frontmatter.
# Used by:
#   - Claude Code hooks (plan #7a part 3 reflection sidecar)
#   - Operator-debug (manual `python3 save.py ...` invocation)
#   - Smoke install fixture tests
#
# The agent-driven `/memory save` skill body (see SKILL.md) uses the
# Write tool directly to produce byte-identical entry files; this
# script is the parallel Python implementation that hooks + tests use.
#
# v0.9.0+ — gemini-cli host removed per ROADMAP item #15.
# Embedding integration deferred to plan #7a part 1 task 4.

from __future__ import annotations

import argparse
import os
import json
import re
import sys
from datetime import date
from pathlib import Path

# vault_lock.py is a byte-identical vendored sibling in THIS scripts/ dir
# (DC-9): top-level scripts/vault_lock.py is NOT on sys.path in a real install,
# so the memory skill carries its own copy; scripts/check-vault-lock-parity.sh
# enforces byte-identity between the two. Inject this dir so the sibling import
# resolves however save.py is invoked (subprocess or imported-by-hook). Mirrors
# recall.py's own sys.path injection.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from vault_lock import vault_mutex  # noqa: E402
from storage_device_local import DeviceLocalBackend  # noqa: E402

# Validation regexes (must match the skill body's documented contracts).
_KEBAB_SEGMENT = re.compile(r"^[a-z0-9-]+$")
_SLUG_SEGMENT = re.compile(r"^[a-z0-9-]+(?:~dup[0-9]*)?$")
# Group is a vault subdirectory path: one or more kebab segments joined by `/`.
# (Widened V4 #33: the live vault uses deep groups like
# `projects/<slug>/decisions` — the prior single-sub-segment regex was behind
# reality. Backward-compatible: 1- and 2-segment groups still match.)
_GROUP_SEGMENT = re.compile(r"^[a-z0-9-]+(/[a-z0-9-]+)*$")

# Filing-v2 2b: the project space is the vault-root `Projects/`, a SIBLING of
# the memory root save.py is handed. A group value must stay lowercase kebab,
# so a root-space note carries `group: projects/<slug>/…` — the historical
# form — and this first segment maps onto the directory. Discovered, never
# conjured: the sibling is used only when it exists; a flat scratch vault
# keeps the space inside its own tree.
_ROOT_SPACE_GROUP = "projects"
_ROOT_SPACE_DIRNAME = "Projects"


def _root_projects_dir(vault):
    """The vault-root `Projects/` space, discovered never conjured (filing-v2
    2b). Flat layout: `<memory-root>/Projects`. Nested layout — the memory
    root sits inside an Obsidian vault, witnessed by `.obsidian/` at the
    parent and none at the memory root itself: the sibling
    `<vault-root>/Projects`. A memory root at the top of its own vault has no
    sibling, whatever directory named `Projects` sits beside it (its parent
    is the operator's home or a sync folder, where one is common and is not
    the vault's). None when no root space exists. Both rungs match the
    directory's exact case."""
    vault = Path(vault)
    flat = vault / "Projects"
    if _is_dir_exact(flat):
        return flat
    parent = vault.parent
    if (parent / ".obsidian").is_dir() and not (vault / ".obsidian").is_dir():
        sibling = parent / "Projects"
        if _is_dir_exact(sibling):
            return sibling
    return None


def _is_dir_exact(path):
    """`path` is a directory whose name matches exactly — on a case-insensitive
    filesystem `Projects/` would otherwise answer for the V4-era `projects/`."""
    try:
        return path.is_dir() and any(p.name == path.name for p in path.parent.iterdir())
    except OSError:
        return False


def root_space_dir(vault: Path) -> Path:
    """Where `projects/…` groups land: the vault-root sibling when present,
    else `<vault>/Projects` (flat layout)."""
    root = _root_projects_dir(vault)
    return root if root is not None else vault / _ROOT_SPACE_DIRNAME


def group_target_dir(vault: Path, group: str) -> Path:
    """The directory a group value addresses, on either generation."""
    first, _, rest = group.partition("/")
    if first == _ROOT_SPACE_GROUP:
        base = root_space_dir(vault)
        return base / rest if rest else base
    return vault / group


def _target_path(
    vault: Path, group: str, vocabulary_field: str, value: str, slug: str,
    *, always_load: bool,
) -> Path:
    """The one target-path formula. `save_entry()` calls this with the
    vocabulary it has already resolved; `entry_target_path()` below resolves
    first and then calls it, so a caller asking where a note WILL go and the
    writer that puts it there can never answer differently."""
    if always_load:
        return vault / "memory" / "_always-load" / f"{slug}.md"
    return group_target_dir(vault, group) / _class_segment(vocabulary_field, value) / f"{slug}.md"


def entry_target_path(
    vault_path: "Path | str",
    kind: str,
    slug: str,
    *,
    group: str = "memory",
    always_load: bool = False,
) -> Path:
    """Where `save_entry()` would write this note, without writing it.

    For a caller that has to know the destination up front — a pre-flight
    collision check across a multi-note write, a dry run — and must not
    re-derive the path itself. The formula moved once already: filing v2 put a
    memory type at the class the routing table sends it to, so the old
    `vault/group/<kind>/slug.md` shape now holds only for a record kind. A
    pre-flight that had hardcoded the old shape went on checking a directory
    nothing writes to, silently, for as long as its test suite did not ask.

    `kind` resolves through the contract exactly as `save_entry` resolves it,
    so a retired value answers for its replacement. Raises the same
    `ValueError` for a value the contract does not carry at all.
    """
    vocabulary_field, value, _ = _resolve_vocabulary(kind)
    return _target_path(
        Path(vault_path), group, vocabulary_field, value, slug, always_load=always_load,
    )

# Locked frontmatter field order — the schema source of truth shared with
# `vault_lint.py` (V4 #33 DC-2: the lint reuses this so the two can't drift).
# `_build_frontmatter` below emits fields in this exact order; a test pins them.
# `altitude` sits beside `status` because the two are the note's own account of
# itself — what stage of life it is in, and how durable a claim it makes. Both
# are always emitted; every field after them is caller-supplied or optional.
#
# The vocabulary field is spelled `kind` here and emitted as `type` for a memory
# — one slot, and `_resolve_vocabulary` decides which name it takes. Listing both
# would imply a note could carry both, which is the one thing the contract
# forbids.
FRONTMATTER_FIELD_ORDER: tuple[str, ...] = (
    "kind", "status", "altitude", "created", "updated", "tags", "arc", "group", "slug",
    "source_url", "source_fetched",
    "fingerprint", "occurrences", "always_load", "supersedes", "lifecycle_tier",
    "derived_from", "heat_pin",
    # Filing-v2 (the write path): the aging axis, the provenance transport, and
    # the write-time judgment's confidence — stamped by the filing engine on
    # every auto-filed note; optional, so a hand-written entry stays complete.
    "lifecycle", "source", "filing_confidence",
    # A capture's own record (the write path, task 2): how it arrived and what
    # the operator said at capture time; and the engine's review marks (task
    # 3): the flags the needs-review reading selects on and the note they
    # point at. All optional.
    "via", "captured", "surface", "instructions", "review_flags", "related",
    # How far to trust where the note came from (task 5): the contract's
    # `sources` map, read once at write time so no reader needs the contract.
    "trust",
)
# Required fields = every field except the optional ones.
# `fingerprint` stays structurally optional in the frontmatter contract, but
# since auto-org part 3 task 1 `save_entry()` auto-computes a content hash
# (fingerprint.compute_fingerprint) whenever the caller doesn't pass one —
# an explicit caller value (the diagnostics recall ladder's semantic incident
# join key, wave-c-diagnostics) always wins over the auto-computed hash.
# `lifecycle_tier` (V6-1, agentm-memory-index.md) is likewise optional: absent
# means "volatile" by default (lifecycle.py applies kind/path-based overrides
# for the decay-exempt categories regardless of this field being set).
# `derived_from` (V6-4, agentm-memory-index.md): a provenance edge naming the
# source entries a consolidated/derived entry was synthesized from — the
# sources are never deleted or superseded by consolidation, so this is a
# comma-joined list, not a single supersedes-shaped path. Optional; absent
# means the entry wasn't derived from anything (the common case).
# `arc` (2026-07-18, the arc-as-metadata convention, agentm-memory-system.md):
# names the temporal wave of work a decisions/designs entry belongs to (a
# V5/V6/V7/V8 roadmap wave, architecture-governance, a lettered AG build wave,
# …), validated against arc_registry.py. Optional: most entries carry no arc.
# `altitude` is emitted on every new entry and REQUIRED on none. Those are not in
# tension: the default is what absence means, so a note written before the field
# existed is complete without it — and requiring it would have turned every note
# in the corpus into a lint error to make a point the default already makes.
_OPTIONAL_FIELDS = frozenset({
    "source_url", "source_fetched", "fingerprint", "occurrences", "supersedes",
    "lifecycle_tier", "derived_from", "heat_pin", "arc", "altitude",
    "lifecycle", "source", "filing_confidence",
    "via", "captured", "surface", "instructions", "review_flags", "related", "trust",
})
REQUIRED_FRONTMATTER_FIELDS: tuple[str, ...] = tuple(
    f for f in FRONTMATTER_FIELD_ORDER if f not in _OPTIONAL_FIELDS
)


def _today_iso() -> str:
    """Today's date in YYYY-MM-DD UTC."""
    return date.today().isoformat()


# Altitude is the axis ranking dampens on: `canonical` states something durable,
# `artifact` records a moment. The default is `artifact`, so a note earns
# `canonical` rather than assuming it.
ALTITUDES = ("artifact", "canonical")
DEFAULT_ALTITUDE = "artifact"


def _trust_tier(source: str) -> "str | None":
    """The tier the contract's `sources` map gives a transport, or None for
    a transport the contract does not name. Trust is a property of the
    transport, never of how plausible the content reads."""
    try:
        import storage_rules  # function-local, as every contract read in this module is
        return storage_rules.rules().sources().get(source) or None
    except Exception:
        return None


def _default_lifecycle() -> str:
    """The contract's `default_lifecycle`, or `active` when no contract answers."""
    try:
        import storage_rules  # function-local, as every contract read in this module is
        return storage_rules.rules().default_lifecycle() or "active"
    except Exception:
        return "active"


def _resolve_vocabulary(value: str) -> tuple:
    """Decide whether `value` names a memory type or a record kind.

    Returns `(field_name, resolved_value, note_or_None)` where `field_name` is
    "type" or "kind". A note carries one or the other, never both: `type` for
    something that asserts, `kind` for something that records.

    A retired value is migrated to its replacement rather than refused. That is
    what the deprecation map is for — the collapse is meant to be mechanical, and
    a writer that rejected `domain-reference` would break the ingest path to make
    a point the map already makes. The migration is reported back to the caller
    so it shows up rather than happening quietly.

    A value the contract does not carry at all is refused: it is neither a memory
    type nor a record kind, and guessing which would be inventing vocabulary.
    """
    try:
        import storage_rules
    except ImportError:  # pragma: no cover - same-dir sibling
        return "kind", value, None

    try:
        rules = storage_rules.rules()
    except storage_rules.StorageRulesError as exc:
        raise ValueError(
            f"cannot file {value!r} — the filing contract is unavailable, so there "
            f"is no vocabulary to check it against: {exc}"
        ) from exc

    note = None
    replacement = rules.resolve_deprecated(value)
    if replacement is not None:
        note = f"{value!r} is retired; filed as {replacement!r}"
        value = replacement

    if value in rules.memory_types():
        return "type", value, note
    if value in rules.record_kinds():
        return "kind", value, note

    raise ValueError(
        f"{value!r} is in neither register in the storage rules. Add it to "
        f"`standards/storage-rules.md` — a memory type if it asserts something, a "
        f"record kind if it records something — or use one of: "
        f"{', '.join(sorted(rules.memory_types()))}"
    )


def _class_segment(vocabulary_field: str, value: str) -> str:
    """The directory segment a note files under. Filing-v2 part 3 populated the
    six classes, so a memory type files into the class the contract routes it
    to (`preference` → `semantic/`, `workflow` → `procedural/`) rather than a
    type-named folder — the legacy layout the corpus migration dissolved and
    that this writer had kept regrowing. A record kind keeps its own folder.
    Without a contract to ask there is no class, and the type-named folder is
    the honest fallback rather than a guessed class."""
    if vocabulary_field != "type":
        return value
    try:
        import storage_rules
        class_dir = storage_rules.rules().routing().get(value)
    except Exception:
        class_dir = None
    return class_dir.rsplit("/", 1)[-1] if class_dir else value


# A slug is kebab-case, optionally carrying the `~dup` mark the filing engine
# and the corpus migration settle a namesake with (`<slug>~dup`, `~dup2`, …):
# two different notes that slug alike are two notes, and the mark is how the
# second keeps its name without pretending to be the first.
def _validate_kebab(value: str, arg_name: str) -> None:
    """Raise ValueError if `value` is not kebab-case (^[a-z0-9-]+(?:~dup[0-9]*)?$)."""
    rule = _SLUG_SEGMENT if arg_name == "slug" else _KEBAB_SEGMENT
    if not rule.match(value):
        raise ValueError(
            f"{arg_name} {value!r}: must be kebab-case ({rule.pattern})"
        )


def _validate_group(value: str) -> None:
    """Raise ValueError if `value` is not a valid group path."""
    if not _GROUP_SEGMENT.match(value):
        raise ValueError(
            f"group {value!r}: must be one or more kebab-case segments joined "
            f"by / (^[a-z0-9-]+(/[a-z0-9-]+)*$)"
        )


def _validate_tags(tags: list[str]) -> None:
    """Raise ValueError if any tag is not kebab-case."""
    for t in tags:
        if not _KEBAB_SEGMENT.match(t):
            raise ValueError(
                f"tag {t!r}: must be kebab-case (^[a-z0-9-]+$)"
            )


# A value written bare only when YAML reads it back as the same string: one
# token of word characters, dots, slashes, colons and dashes (a timestamp, a
# URL, a slug) that is not a YAML literal. Everything else is JSON-quoted, so
# an operator's verbatim instruction survives the round trip untouched.
_BARE_SCALAR = re.compile(r"^[A-Za-z0-9_./@:+-]+$")
_YAML_SPECIAL = {"true", "false", "yes", "no", "on", "off", "null", "~"}


def _build_frontmatter(
    *,
    kind: str,
    group: str,
    vocabulary_field: str = "kind",
    altitude: str = DEFAULT_ALTITUDE,
    slug: str,
    tags: list[str],
    always_load: bool,
    supersedes: str | None,
    source_url: str | None = None,
    source_fetched: str | None = None,
    fingerprint: str | None = None,
    lifecycle_tier: str | None = None,
    derived_from: list[str] | None = None,
    lifecycle: str | None = None,
    source: str | None = None,
    filing_confidence: str | None = None,
    status: str = "active",
    extra: dict | None = None,
    trust: str | None = None,
) -> str:
    """Build the locked-order YAML frontmatter for a memory entry.

    Field order is locked for deterministic diffs:
      kind / status / created / updated / tags / group / slug / source_url
      (omitted if None) / source_fetched (omitted if None) / fingerprint
      (omitted if None) / always_load / supersedes (omitted if None) /
      lifecycle_tier (omitted if None) / derived_from (omitted if None/empty).

    `source_url` / `source_fetched` (the capture design's provenance
    plumbing, `designs/friday/agentm-capture.md`) record where a captured or
    ingested note came from and when it was fetched. Both optional — omitted
    unless a caller passes them, so every existing entry kind's frontmatter
    is unaffected.

    `fingerprint` is the V6-11 recall-ladder join key (agentm-memory-index.md;
    wave-c-diagnostics): omitted unless a caller passes one, so every existing
    entry kind's frontmatter is unaffected.

    `lifecycle_tier` (V6-1, agentm-memory-index.md) is `"durable"` or
    `"volatile"`; omitted unless a caller passes one — absence defaults to
    volatile decay behavior in lifecycle.py, with kind/path-based overrides
    for the decay-exempt categories (error-history, architecture-decisions)
    applying regardless of whether this field is set.

    `derived_from` (V6-4, agentm-memory-index.md) is a list of vault-relative
    source paths a consolidated/derived entry was synthesized from — the
    provenance edge that lets an undo of a consolidation also identify what
    it was derived from. Comma-joined in the emitted YAML (a bracketed list,
    same shape as `tags`), omitted if None or empty.
    """
    today = _today_iso()
    # Build the tags list inline (`[]` if empty, `[a, b, c]` otherwise).
    tags_yaml = "[]" if not tags else "[" + ", ".join(tags) + "]"
    lines = [
        "---",
        f"{vocabulary_field}: {kind}",
        f"status: {status}",
        f"altitude: {altitude}",
        f"created: {today}",
        f"updated: {today}",
        f"tags: {tags_yaml}",
        f"group: {group}",
        f"slug: {slug}",
    ]
    if source_url:
        lines.append(f"source_url: {source_url}")
    if source_fetched:
        lines.append(f"source_fetched: {source_fetched}")
    if fingerprint:
        lines.append(f"fingerprint: {fingerprint}")
    lines.append(f"always_load: {'true' if always_load else 'false'}")
    if always_load:
        # The v2 lifecycle axis (filing-v2 part 1): always-load is what
        # `pinned` means — never decays, loads every session. Stamped here so
        # part 6's pinned loader retires the holding pen with a query, not a
        # migration.
        lines.append("lifecycle: pinned")
    if supersedes:
        lines.append(f"supersedes: {supersedes}")
    if lifecycle_tier:
        lines.append(f"lifecycle_tier: {lifecycle_tier}")
    if derived_from:
        lines.append("derived_from: [" + ", ".join(derived_from) + "]")
    # Filing-v2 write-time stamps. `always_load` already spelled the lifecycle
    # (`pinned`) above and wins; otherwise the engine's value is written.
    if lifecycle and not always_load:
        lines.append(f"lifecycle: {lifecycle}")
    if source:
        lines.append(f"source: {source}")
    if filing_confidence:
        lines.append(f"filing_confidence: {filing_confidence}")
    if trust:
        lines.append(f"trust: {trust}")
    # A capture's own record fields (the surface it came through, the operator's
    # verbatim instructions, the capture stamp) — written last, values quoted
    # as JSON strings when they carry anything YAML would misread.
    extra = extra or {}
    ordered = [k for k in FRONTMATTER_FIELD_ORDER if k in extra] + [k for k in extra if k not in FRONTMATTER_FIELD_ORDER]
    for key in ordered:
        value = extra[key]
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, (list, tuple)):
            text = "[" + ", ".join(str(v) for v in value) + "]"
        else:
            text = str(value)
            if not _BARE_SCALAR.match(text) or text.lower() in _YAML_SPECIAL:
                text = json.dumps(text)
        lines.append(f"{key}: {text}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def save_entry(
    vault_path: Path | str,
    kind: str,
    slug: str,
    body: str,
    *,
    group: str = "memory",
    always_load: bool = False,
    tags: list[str] | None = None,
    supersedes: str | None = None,
    source_url: str | None = None,
    source_fetched: str | None = None,
    fingerprint: str | None = None,
    lifecycle_tier: str | None = None,
    derived_from: list[str] | None = None,
    dedup_info: dict | None = None,
    lifecycle: str | None = None,
    source: str | None = None,
    filing_confidence: str | None = None,
    status: str = "active",
    extra: dict | None = None,
) -> Path:
    """Write a memory entry to the vault. Returns the absolute path written —
    or, when the write-time dedup guard fires (auto-org part 3 task 2), the
    path of the EXISTING entry the arriving note reinforced instead (no new
    file written; the existing note's `occurrences` count and `updated`
    stamp bumped).

    `dedup_info`, when a caller passes a dict, is the out-of-band signal for
    that reinforce path: this function sets `dedup_info["deduplicated"]`
    (True/False) and, on a reinforce, `dedup_info["existing_path"]`. Callers
    that track "files I actually created this run" for rollback (ingest.py)
    MUST consult it — unlinking a returned path that was a reinforce target
    would delete a pre-existing note.

    Raises:
        FileNotFoundError: if `vault_path` doesn't exist or isn't a directory.
        ValueError: if kind / slug / group / tags fail validation.
        FileExistsError: if the target path already exists (use /memory evolve
            to supersede; never overwrite from save).
    """
    vault = Path(vault_path)
    if not vault.exists():
        raise FileNotFoundError(f"vault path does not exist: {vault}")
    if not vault.is_dir():
        raise FileNotFoundError(f"vault path is not a directory: {vault}")

    _validate_kebab(kind, "kind")
    # Resolved before anything reads it, because it decides two things at once:
    # which frontmatter field the note carries, and — since the target path is
    # vault/group/<value>/slug.md — where the note lands. A retired value
    # migrating to its replacement therefore also moves where NEW notes of that
    # value are written, which is the collapse working rather than a side effect.
    vocabulary_field, kind, vocabulary_note = _resolve_vocabulary(kind)
    # The write-time stamps (filing v2, task 3) on every memory the writer
    # files, whoever the caller is: a caller that named the type stands behind
    # it (high confidence), the transport is a conversation unless the caller
    # says otherwise (the CLI says operator-direct, the ingest says
    # external-fetch), and the aging axis starts where the contract says. A
    # record keeps its own shape; an always-load rule never ages.
    if vocabulary_field == "type":
        if lifecycle is None and not always_load:
            lifecycle = _default_lifecycle()
        if source is None:
            source = "conversation"
        if filing_confidence is None:
            filing_confidence = "high"
        trust = _trust_tier(source)
    else:
        trust = None
    _validate_kebab(slug, "slug")
    _validate_group(group)
    tags = tags or []
    _validate_tags(tags)
    if lifecycle_tier is not None and lifecycle_tier not in ("durable", "volatile"):
        raise ValueError(
            f"lifecycle_tier {lifecycle_tier!r}: must be 'durable' or 'volatile' (or omitted)"
        )

    # V6-11 (agentm-memory-index.md): `failure-incident` is a reserved `kind`
    # value whose content is untrusted and potentially PII-bearing (a
    # stack trace, an error log excerpt) — a mandatory scrub the write
    # cannot skip, a persistence-boundary guard. Refuses loudly rather than
    # writing unscrubbed if the scrubber is somehow unavailable (it's a
    # pure-stdlib sibling module, so this should never actually fire; the
    # refuse-loud path exists so a future refactor can't silently reintroduce
    # an unscrubbed write path).
    if kind == "failure-incident":
        try:
            from privacy_scrub import scrub_pii  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                f"failure-incident write refused: privacy_scrub unavailable "
                f"({e}) — the mandatory scrub cannot be skipped"
            ) from e
        body = scrub_pii(body)

    # Auto-compute the content fingerprint when the caller didn't supply
    # one (auto-org part 3 task 1). An explicit caller-supplied value
    # always wins — the diagnostics recall ladder passes a semantic
    # incident join key, not a content hash, and must keep doing so.
    # Best-effort: a fingerprint failure must never block the write. Runs
    # after the failure-incident scrub above so the hash reflects the
    # scrubbed body actually written.
    if fingerprint is None:
        try:
            from fingerprint import compute_fingerprint  # type: ignore
            fingerprint = compute_fingerprint(body)
        except Exception as e:  # pragma: no cover
            print(f"warning: fingerprint computation failed: {e}", file=sys.stderr)

    # `dedup_info` still reports a verdict so callers keep their contract;
    # with the write-time guard gone it is always False here. A same-slug
    # collision still raises FileExistsError below — the evolve/consolidate
    # collision contracts depend on that error and are unaffected.
    if dedup_info is not None:
        dedup_info["deduplicated"] = False

    # Compute target path. --always-load overrides --group and still routes
    # to the legacy holding pen — deliberately, as a filing-v2 transition
    # state: the session-start loader reads standards/ ∪ this directory, and
    # part 6's lifecycle loader is what retires the pen by loading
    # `lifecycle: pinned` entries from their classes. The stamp below is
    # what makes that retirement a pure frontmatter query, not a move.
    target = _target_path(
        vault, group, vocabulary_field, kind, slug, always_load=always_load,
    )

    if target.exists():
        raise FileExistsError(
            f"entry already exists at {target}: use /memory evolve to "
            f"supersede the existing entry, or pick a different slug"
        )

    # (No explicit mkdir here: the write path's atomic_write creates the
    # parent directory itself, and a pre-created dir would be left behind
    # empty when the dedup guard below turns this save into a reinforce.)

    # Build content.
    fm = _build_frontmatter(
        kind=kind,
        vocabulary_field=vocabulary_field,
        group=group,
        slug=slug,
        tags=tags,
        always_load=always_load,
        supersedes=supersedes,
        source_url=source_url,
        source_fetched=source_fetched,
        fingerprint=fingerprint,
        lifecycle_tier=lifecycle_tier,
        derived_from=derived_from,
        lifecycle=lifecycle,
        source=source,
        filing_confidence=filing_confidence,
        status=status,
        extra=extra,
        trust=trust,
    )
    # Ensure body ends with single trailing newline.
    body_stripped = body.rstrip("\n")
    content = fm + "\n" + body_stripped + "\n"

    # V5-0 + V5-14: route the per-slug entry write through the one per-vault
    # advisory mutex + the storage seam's `write` verb (agentm-memory-index.md
    # / agentm-memory-system.md — entries now reach disk through the same
    # StorageBackend contract harness state already used, not a raw
    # atomic_write call). `DeviceLocalBackend(root=vault).write()` composes
    # the identical V5-0 primitives (temp(same dir)→fsync→rename, bytes-mode)
    # as before — same bytes on disk, routed through a seam verb instead of
    # calling the primitive directly. The mutex gives torn-write safety when
    # two writers race the same target (each writer's temp file carries its
    # own pid+uuid name, so the rename itself no longer races). This is a per-slug
    # CREATE (the FileExistsError guard above forbids overwrite), so
    # mutex-only — no CAS (DC-2: per-slug entry files are partitioned by
    # ownership).
    backend = DeviceLocalBackend(root=vault)
    locator = backend.resolve(*target.relative_to(vault).parts)
    with vault_mutex(vault):
        # Re-checked under the lock: the guard above ran before the mutex, and
        # two writers that both saw the name free would otherwise both write
        # it, the second silently over the first. The loser gets the same
        # FileExistsError and settles a new name.
        if target.exists():
            raise FileExistsError(f"entry already exists at {target}: a concurrent writer landed first")
        # The dedup guard's find+reinforce and the write share this one
        # critical section: two concurrent identical saves serialize here,
        # and the loser sees the winner (once indexed) rather than both
        # writing. Fails open: any guard error → the write proceeds
        # (favor false-negative; the weekly pass catches what this misses).
        # The write-time dedup guard went with the vector index: the
        # `entry_meta` table it looked a fingerprint up in was that index's
        # own, and no other fingerprint->path lookup exists in the tree.
        # Scanning 8k+ notes on every save is not an acceptable substitute,
        # so permanent-memory writes no longer dedup at write time and the
        # weekly cluster pass owns the duplicates this used to catch.
        # `capture.py`'s own inbox guard is unaffected — it scans a small
        # staging dir, never the index. See the amendment log in
        # wiki/designs/agentm-rescope-week1-experiment.md.
        backend.write(locator, content)

    return target


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="memory-save",
        description=(
            "Save a memory entry to MemoryVault. "
            "Canonical Python implementation behind /memory save (see SKILL.md)."
        ),
    )
    parser.add_argument("kind", help="entry kind (kebab-case)")
    parser.add_argument("slug", help="entry slug (kebab-case; filename stem)")
    parser.add_argument(
        "--vault-path",
        required=False,
        help="path to MemoryVault root (overrides MEMORY_VAULT_PATH env var)",
    )
    parser.add_argument(
        "--group",
        default="memory",
        help="memory group (default: personal)",
    )
    parser.add_argument(
        "--always-load",
        action="store_true",
        help=(
            "route to personal/_always-load/ + set always_load: true. "
            "Overrides --group."
        ),
    )
    parser.add_argument(
        "--tags",
        default="",
        help="comma-separated tags (kebab-case each)",
    )
    parser.add_argument(
        "--supersedes",
        default=None,
        help="path to entry this one supersedes (sets supersedes: frontmatter)",
    )
    parser.add_argument(
        "--fingerprint",
        default=None,
        help="V6-11 recall-ladder join key (sets fingerprint: frontmatter)",
    )
    parser.add_argument(
        "--lifecycle-tier",
        default=None,
        choices=("durable", "volatile"),
        help=(
            "V6-1 lifecycle classification (sets lifecycle_tier: frontmatter). "
            "Omit to default to volatile-decay behavior; kind: failure-incident "
            "and decisions/-path entries are always durable regardless."
        ),
    )
    parser.add_argument(
        "--body-file",
        default="-",
        help=(
            "path to file containing the entry body, or '-' to read from stdin "
            "(default: stdin)"
        ),
    )
    return parser.parse_args(argv)


def _resolve_vault_path(arg_vault_path: str | None) -> Path:
    """Resolve vault path per the documented chain.

    Order: --vault-path arg > MEMORY_VAULT_PATH env > error.
    (The third level — ~/.config/crickets/memory.yml — is deferred to a
    future task; documented in SKILL.md.)
    """
    if arg_vault_path:
        return Path(arg_vault_path).expanduser()
    env_path = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    raise FileNotFoundError(
        "No vault path resolved. Set --vault-path or the MEMORY_VAULT_PATH "
        "environment variable. (Config-file resolution path "
        "~/.config/crickets/memory.yml is documented but not yet "
        "implemented as of v0.9.0; tracked for a future task.)"
    )


def _read_body(body_file: str) -> str:
    """Read entry body from a file or stdin (when body_file == '-')."""
    if body_file == "-":
        return sys.stdin.read()
    return Path(body_file).expanduser().read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        vault = _resolve_vault_path(args.vault_path)
        body = _read_body(args.body_file)
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
        target = save_entry(
            vault_path=vault,
            kind=args.kind,
            slug=args.slug,
            body=body,
            group=args.group,
            always_load=args.always_load,
            tags=tags,
            supersedes=args.supersedes,
            fingerprint=args.fingerprint,
            lifecycle_tier=args.lifecycle_tier,
            source="operator-direct",
        )
    except (FileNotFoundError, FileExistsError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    # Stdout: just the absolute path written (script-pipeable).
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
