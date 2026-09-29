#!/usr/bin/env python3
"""capture.py — the front door that files a capture at its class directory
(`designs/friday/agentm-capture.md`, capture-front-door plan task 2).

`memory_append` (save.py's `save_entry`) writes straight to permanent
memory at high confidence, standing behind the type its caller named.
This module is the second front door, for captures arriving with nobody
standing behind a type. It hands the note to `filing_engine.decide()` at
LOW confidence, always — the contract picks the class, `kind="idea"` is
the only hint a caller can offer — and `filing_engine.apply()` writes it
there through that same `save_entry`, marked `status: unfiled` at
`filing_confidence: low` (filing v2, the write path). The metadata is
the inbox. Nothing here stages in a directory: the retired
`memory/_inbox/` is read by the ingest sweep while a legacy one exists,
but the only thing still writing to it is the phone Capture project's
Drive connector, whose instructions live outside this repo.

Write path: decide, then write, with no single lock held across both.
`save_entry` takes `vault_lock.vault_mutex` for the write itself and
refuses a target a concurrent writer reached first, raising
`FileExistsError`. This module catches that and decides again against
the disk, so the newcomer is seen — a twin to reinforce, or a namesake
to settle past with the `~dup` mark — rather than clobbered. That
refuse-and-retry guard is what makes the unlocked gap between the two
steps safe. Multiple transports still write concurrently (the Drive
connector, the Obsidian Web Clipper, this module, the ingest sweep) and
Data Integrity is a named Quality Attribute of the capture design, so
the loser of a race loses loudly rather than overwriting. (The earlier
resolve-then-write-under-one-mutex version shipped without its mutex; a
retroactive /review before the release cut found two concurrent callers
resolving the same free slug would silently overwrite one candidate
with the other. Fixed before release, never in a tagged version.)

Every call returns a `CaptureResult` — success or failure is always
explicit, never a silent drop (the design's own reliability contract:
"The system alerts you immediately if a capture fails").
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from vault_lock import LockTimeout  # noqa: E402
from volume_gate import VolumeCapRefused  # noqa: E402

_KNOWN_KINDS = ("capture", "idea")


@dataclass(frozen=True)
class CaptureResult:
    success: bool
    path: "Path | None" = None
    slug: "str | None" = None
    error: "str | None" = None
    # True when the write-time dedup guard (auto-org part 3 task 2) matched
    # an existing candidate already home by exact content fingerprint and
    # reinforced it (occurrences + updated bump) instead of writing a new
    # file — `path`/`slug` then name the EXISTING candidate.
    deduplicated: bool = False
    # True when the capture found its source's card already home — the same
    # source and the same title — and updated it in place (`path` names it).
    updated: bool = False
    # True when the capture was an idea that already has a card in
    # `personal/ideas/`, and was added to it under `## Added by capture`
    # instead of being filed (`path` names the card).
    appended: bool = False


def _iso(now: datetime) -> str:
    """Format `now` as full ISO8601 — mirrors reflect.py's `_utcnow_iso()`
    shape. A chat-surface caller's estimate of `now` gets corrected later
    by the ingest sweep's `captured:` re-stamp (capture part 3); this
    module always writes whatever clock time it's given (the real one by
    default, an injected one in tests)."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.replace(microsecond=0).isoformat()


def _slugify(content: str, *, now: datetime) -> str:
    """A timestamp-based default slug when the caller doesn't supply one —
    unique enough in practice that the collision path below is a rare
    resend/race, not the common case."""
    return f"capture-{now.strftime('%Y%m%dT%H%M%S')}"


def capture(
    vault_path: "Path | str",
    content: str,
    *,
    kind: str = "capture",
    slug: "str | None" = None,
    source: "str | None" = None,
    surface: "str | None" = None,
    tags: "list[str] | None" = None,
    instructions: "str | None" = None,
    source_url: "str | None" = None,
    source_id: "str | None" = None,
    transport: "str | None" = None,
    type_hint: "str | None" = None,
    why: "str | None" = None,
    project: "str | None" = None,
    now: "datetime | None" = None,
    lock_timeout: float = 10.0,
) -> CaptureResult:
    """File one candidate at the class directory the contract routes its type
    to, as an `unfiled` note at low filing confidence (filing v2, the write
    path). A plain capture takes the contract's default type; `kind="idea"`
    files as `type: idea`. The metadata is the inbox: `status: unfiled` is
    what the enrichment pass and the ingest sweep drain, `filing_confidence:
    low` is what the needs-review reading selects on. Nothing stages in a
    directory any more. Never raises on a write failure — returns a
    `CaptureResult` with `success=False` and the error message instead, so
    a caller (the MCP tool, the CLI verb) always has an explicit outcome to
    relay back to the operator.

    `source` here is the caller's surface tag (`cli`, `mcp`, `clipper`, a
    connector) and is written as `via:`; the contract's `source:` field
    carries the transport — `external-fetch` for a link, `operator-direct`
    otherwise.

    `instructions` is the security-boundary field (task 5's invariant):
    this function stores exactly the string it's given here, verbatim,
    from this call's own explicit argument — it never inspects or parses
    `content` to derive one. A caller that populates `instructions` from
    anything other than the operator's own capture-time text breaks that
    invariant at the call site, not here; this function's contract is
    simply "store what you were handed, nothing inferred."

    An exact repeat reinforces the note already home (occurrences + updated
    bump, no new file) unless the arrival carries act-relevant metadata the
    twin lacks — a `source_url` (the ingest sweep's trigger) or an
    `instructions` string — in which case it files fresh beside it. A twin
    that is no longer live (a tombstone, a superseded note) is never a
    reinforce target.

    `transport` overrides the contract `source:` this write files under, and
    with it the `trust:` the contract's sources table stamps. It exists for the
    mail door: a mailed card is `source: email`, which the contract maps to
    `trust: untrusted`, and the door must not be the thing that decides its own
    trust level — the contract is. Absent, the transport is derived as it always
    was (`external-fetch` for a link, `operator-direct` otherwise), so every
    existing caller writes exactly what it wrote before.

    `type_hint` is the memory type the caller has been *told* — not guessed. It
    exists for the inbox review pass (agentm-vault plan 16), where the operator
    reads a card and says what it is, and the destination has to be their answer
    rather than a default the command picked. Absent, routing is what it always
    was: `idea` for `kind="idea"`, and the contract's default otherwise. The
    value still goes through the filing engine, so a type the contract does not
    know is routed by the contract's own rule and not by this argument.

    `why` is the reason the thing was worth keeping, when the caller has one to
    carry. `project` names the project a capture belongs to. Both are plain
    frontmatter; neither can cause anything to happen, which is what separates
    them from `instructions`.

    `source_id` is the registry identity (`<namespace>:<ref>`) of the unit this
    came from. A capture whose source — that identity, else its `source_url` —
    and title match an active card already home updates that card in place
    rather than filing a second one (`update_same_source`).

    `lock_timeout` passes through to the vault mutex the writer takes.
    """
    if kind not in _KNOWN_KINDS:
        return CaptureResult(success=False, error=f"unknown kind {kind!r}; expected one of {_KNOWN_KINDS}")
    if not content or not content.strip():
        return CaptureResult(success=False, error="content must be non-empty")

    try:
        vault = Path(vault_path)
        if not vault.is_dir():
            return CaptureResult(success=False, error=f"vault path does not exist: {vault}")

        now = now or datetime.now(timezone.utc)
        resolved_slug = _kebab(slug or _slugify(content, now=now))
        import dedup_guard  # same skill dir
        import filing_engine  # same skill dir

        title = content.strip().splitlines()[0].strip()[:120]
        if (type_hint or ("idea" if kind == "idea" else None)) == "idea":
            # An idea that already has a card adds to it (agentm-vault § Capture,
            # amended 2026-09-28); a new idea is filed as before.
            import idea_cards  # same skill dir
            how = transport or ("external-fetch" if source_url else "operator-direct")
            label = " · ".join(x for x in (how, f"via {source}" if source else "", source_url or "") if x)
            card = idea_cards.add_to_matching_card(vault, content, slug=_kebab(slug) if slug else "",
                                                   title=title, source=label, day=now.date().isoformat(),
                                                   lock_timeout=lock_timeout)
            if card is not None:
                return CaptureResult(success=True, path=card, slug=card.stem, appended=True)
        updated = update_same_source(vault, content, title=title, source_id=source_id,
                                     source_url=source_url, why=why, tags=tags, now=now,
                                     lock_timeout=lock_timeout)
        if updated is not None:
            return CaptureResult(success=True, path=updated, slug=updated.stem, updated=True)
        extra = {"captured": _iso(now), "via": source, "surface": surface,
                 "instructions": instructions, "why": why, "project": project,
                 "source_id": source_id if source_key(source_id, None)[0] == "source_id" else None}
        # Decide, then write; when a concurrent writer lands on the settled
        # name between the two, decide again against the disk — the next
        # pass sees the newcomer (a twin to reinforce, or a namesake to
        # settle past with a grown name). The writer's own guard under
        # its mutex is what makes the loser lose loudly instead of clobbering.
        for _attempt in range(64):
            decision = filing_engine.decide(
                vault, title=title, body=content, slug=resolved_slug,
                type_hint=type_hint or ("idea" if kind == "idea" else None),
                confidence="LOW",
                source=transport or ("external-fetch" if source_url else "operator-direct"),
            )
            if decision.op == "noop":
                twin = vault / decision.dest_rel
                arriving_adds_metadata = (
                    (source_url and not dedup_guard.has_frontmatter_field(twin, "source_url"))
                    or (instructions and not dedup_guard.has_frontmatter_field(twin, "instructions"))
                )
                if _reinforceable(twin) and not arriving_adds_metadata:
                    dedup_guard.reinforce(twin, today=now.date().isoformat())
                    return CaptureResult(success=True, path=twin, slug=twin.stem, deduplicated=True)
                # Files fresh beside the twin: the engine's own settling, asked
                # with a fingerprint that matches nothing so the occupied name
                # yields a grown name rather than the twin itself.
                import card_shape  # same skill dir
                import hashlib
                dest, _flags = filing_engine._settle_dest(
                    vault, decision.class_dir, resolved_slug, "",
                    words=card_shape.kebab(f"{title} {content}").split("-"),
                    digest=hashlib.sha256(content.encode("utf-8")).hexdigest())
                decision.op, decision.dest_rel, decision.related = "add", dest, None
            try:
                written = filing_engine.apply(
                    vault, decision, body=content, tags=list(tags or []), title=title,
                    source_url=source_url, status="unfiled", extra=extra,
                )
            except FileExistsError:
                continue
            return CaptureResult(success=True, path=written, slug=written.stem)
        return CaptureResult(success=False, error="could not settle a free name: the vault is being written faster than this capture can decide")
    except OSError as e:
        return CaptureResult(success=False, error=f"write failed: {e}")
    except LockTimeout as e:
        return CaptureResult(success=False, error=f"vault busy: {e}")
    except ValueError as e:
        # The writer refused a field that would have broken the note — a tag
        # that is not kebab-case, a slug the rule does not admit. Nothing was
        # written, and the caller hears why.
        return CaptureResult(success=False, error=f"refused: {e}")
    except VolumeCapRefused as e:
        # The gate's own words, verbatim: the count, the cap, the edit that
        # raises it. A refused capture is an outcome the caller sees, never
        # a note that quietly did not appear.
        return CaptureResult(success=False, error=str(e))


# ── the same outside source updates its note ─────────────────────────────────
#
# agentm-vault § Capture, amended 2026-09-28 (the operator's ruling 6b): a
# capture of something already home updates that note in place — the path, the
# links and the slug stay, the body is replaced, git keeps the old wording.
# "The same" is the same source and the same title, because one source yields
# several memories (an article's facts share its address) and each is its own
# note. The Go door (`daemon/internal/capture/samesource.go`) keeps the same rule.

_REGISTRY_IDENTITY = re.compile(r"^[a-z][a-z0-9_.-]*:\S+$")
_CLASSES = ("semantic", "procedural", "episodic", "entities", "crystallized")


def source_key(source_id: "str | None", source_url: "str | None") -> tuple:
    """`(field, value)` a capture's source is found by: a registry identity
    (`<namespace>:<ref>`, never a `session:` id and never a bare word), else the
    page's address; `(None, None)` when it names neither."""
    sid = (source_id or "").strip()
    if _REGISTRY_IDENTITY.match(sid) and not sid.lower().startswith("session:"):
        return "source_id", sid
    url = (source_url or "").strip()
    return ("source_url", url) if url else (None, None)


def _card_title(entries, rest: str) -> str:
    import card_shape  # same skill dir
    title = card_shape.scalar(card_shape.raw_value(entries, "title"))
    if title:
        return title.strip()
    body = rest.split("\n", 1)[1] if rest.startswith("---") else rest
    first = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
    return first[:120]


def _same_source_card(vault: Path, field: str, value: str, title: str) -> "Path | None":
    import card_shape  # same skill dir
    want = _kebab(card_shape.kebab(title))
    if not want:
        return None
    found = []
    for cls in _CLASSES:
        for p in sorted((vault / "memory" / cls).glob("*.md")):
            try:
                parsed = card_shape.split_note(p.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
            if parsed is None:
                continue
            entries, rest = parsed
            value_of = lambda k: (card_shape.scalar(card_shape.raw_value(entries, k)) or "").strip()  # noqa: E731
            if not value_of("type") or value_of("kind"):
                continue
            if value_of(field) != value or _kebab(card_shape.kebab(_card_title(entries, rest))) != want:
                continue
            if value_of("lifecycle").lower() in ("superseded", "archived") or value_of("superseded_by") \
                    or value_of("status").lower() == "superseded":
                continue
            found.append(p)
    return found[0] if found else None


def update_same_source(vault: Path, content: str, *, title: str, source_id=None, source_url=None,
                       why=None, tags=None, now: "datetime | None" = None,
                       lock_timeout: float = 10.0) -> "Path | None":
    """Update the card this capture is again, in place, and return its path;
    None when there is none. The body is replaced and its fingerprint follows;
    `updated`, the source fields, and a `why` or tags the capture carries are
    set; every other field — the operator's `importance`, `created`, the
    enrichment stamps — stays as it was. The card is a candidate again, since
    nothing has judged the new body: `status: unfiled`, `filing_confidence: low`."""
    field, value = source_key(source_id, source_url)
    if field is None:
        return None
    import card_shape  # same skill dir
    from fingerprint import compute_fingerprint  # same skill dir
    from vault_lock import atomic_write, vault_mutex
    now = now or datetime.now(timezone.utc)
    with vault_mutex(vault, timeout=lock_timeout):
        target = _same_source_card(vault, field, value, title)
        if target is None:
            return None
        entries, rest = card_shape.split_note(target.read_text(encoding="utf-8"))
        body = content.strip() + "\n"
        if compute_fingerprint(rest.split("\n", 1)[1] if "\n" in rest else "") == compute_fingerprint(body):
            # An exact resend is a reinforcement, not an update: the write-time
            # dedup guard bumps the note already home (auto-org part 3 task 2).
            return None
        sets = {"status": "unfiled", "filing_confidence": "low", "updated": now.date().isoformat(),
                field: card_shape.quote(value) if ": " in value or value[:1] in "\"'[{&*!|>%@`#" else value,
                "fingerprint": compute_fingerprint(body)}
        if field == "source_id" and (source_url or "").strip():
            sets["source_url"] = source_url.strip()
        if why and why.strip():
            sets["why"] = card_shape.quote(why.strip())
        if tags:
            sets["tags"] = "[" + ", ".join(card_shape.quote(t) for t in tags) + "]"
        for key, rendered in sets.items():
            if card_shape.raw_value(entries, key) is not None or key != "fingerprint":
                entries = card_shape.set_value(entries, key, rendered)
        text = card_shape.reorder(card_shape.join_note(entries, "---\n\n" + body))
        atomic_write(target, text)
    return target


def _kebab(slug: str) -> str:
    """The writer's slug contract is kebab-case; a timestamped default slug
    (`capture-20260718T120000`) and a caller's free-form slug both fold to it."""
    return re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-") or "capture"


_DEAD_STATUSES = frozenset({"expired", "deleted", "superseded", "archived", "promoted", "ingest_duplicate"})


def _reinforceable(twin: Path) -> bool:
    """A live note only. A tombstone the triage or the ingest sweep left in
    place, or a note a later one superseded, keeps its record and never
    absorbs a fresh capture."""
    import dedup_guard  # same skill dir
    status = dedup_guard._file_status(twin)
    if status in _DEAD_STATUSES:
        return False
    try:
        return "lifecycle: superseded" not in twin.read_text(encoding="utf-8").split("\n---\n", 1)[0]
    except (OSError, UnicodeDecodeError):
        return False


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="memory-capture",
        description=(
            "Capture a thought, link, or idea into MemoryVault, filed at the "
            "class its type routes to as `status: unfiled`. Canonical Python "
            "implementation behind "
            "/memory capture (see SKILL.md)."
        ),
    )
    parser.add_argument("content", help="the captured text (a thought, or a link + note)")
    parser.add_argument("--vault-path", help="vault root (default: $MEMORY_ROOT env var)")
    parser.add_argument("--kind", choices=_KNOWN_KINDS, default="capture")
    parser.add_argument("--slug", help="override the default timestamp-based slug")
    parser.add_argument("--source", help="the transport, e.g. 'cli', 'clipper'")
    parser.add_argument("--surface", help="the device/surface, e.g. 'phone', 'desktop'")
    parser.add_argument("--tags", nargs="*", default=None)
    parser.add_argument("--instructions", help="an operator-typed action to run after absorb")
    parser.add_argument("--source-url", help="the link this capture is about, if any")
    return parser.parse_args(argv[1:])


def _resolve_vault(cli_arg: "str | None") -> "Path | None":
    """arg → $MEMORY_ROOT. Deliberately does NOT import harness_memory:
    kernel toolkit scripts under harness/skills/memory/scripts/ are invoked
    as subprocesses by the harness_memory bridge and must never import it
    back (V5-5 LC-8 bridge extension, enforced by
    scripts/check-one-way-imports.py's lc8-bridge rule). The bridge — or any
    other caller — resolves `harness_memory.vault_path()` and exports it as
    $MEMORY_ROOT before invoking this script. Same convention the other
    toolkit scripts follow (`inbox_review._resolve` among them)."""
    if cli_arg:
        p = Path(cli_arg)
        return p if p.is_dir() else None
    env = (os.environ.get("MEMORY_ROOT") or os.environ.get("MEMORY_VAULT_PATH", "")).strip()
    if env:
        p = Path(env).expanduser()
        return p if p.is_dir() else None
    return None


def main(argv: "list[str] | None" = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv)
    vault = _resolve_vault(args.vault_path)
    if vault is None:
        print("[capture] no vault resolved — pass --vault-path or configure MEMORY_ROOT", file=sys.stderr)
        return 2
    result = capture(
        vault, args.content, kind=args.kind, slug=args.slug, source=args.source or "cli",
        surface=args.surface, tags=args.tags, instructions=args.instructions,
        source_url=args.source_url,
    )
    if result.success:
        print(f"captured: {result.path}")
        return 0
    print(f"[capture] failed: {result.error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
