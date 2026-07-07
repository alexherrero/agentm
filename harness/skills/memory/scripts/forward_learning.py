#!/usr/bin/env python3
"""forward_learning.py — the approved-source forward-learning pipeline
(AG Wave E experience plan, task 1).

Generalizes the existing import-watchlist shape — `adapt_skills.py`
(deterministic rubric enrichment) -> `adapt-evaluator` sub-agent (HIGH/
MEDIUM/LOW judgment) -> `watchlist_review.py` (operator promote/dismiss/
defer) — beyond skills to ideas, patterns, and references, per
`wiki/designs/agentm-experience-and-dreaming.md`'s framing: "find -> screen
-> surface", generalized. Sources are feeds, named repositories, and the
web, operator-configured (opt-in by design — an empty or absent sources
config means this pipeline finds nothing and writes nothing).

Deliberately thin v1 (mirrors the dreaming plan's own "thin first,
calibrate, graduate" precedent): unlike the skill pipeline's two-pass split
(a deterministic Pass 1 that enriches, then a separate `adapt-evaluator`
LLM sub-agent that judges), this module does BOTH in one deterministic
pass — a rubric directly produces the HIGH/MEDIUM/LOW tier, no sub-agent
dispatch. This keeps a scan fully offline-testable (a dry run against a
fixture source set must be red-test-verifiable without a live agent call)
and matches the calibration-era posture the design's own locked call #6
(on the sibling dreaming plan) established for this wave: ship the
deterministic floor first, graduate to a semantic judge once the
deterministic pass has real dogfood evidence. NOT built here: an
LLM-judged Pass 2 (mirroring `adapt-evaluator`) — a natural v2 graduation,
not a silent shortcut.

Reuses `watchlist_review.py`'s operator review surface — the SAME CLI
(list / review / promote / dismiss / defer) now scans BOTH
`personal/_skill-watchlist/` (skills, untouched) and the new
`personal/_watchlist/` (ideas/patterns/references, this module's output) —
one review surface for both, per the design's "generalizes this same
shape" framing. See `watchlist_review.py`'s `_watchlist_roots`.

Contract: this module writes ONLY under `personal/_watchlist/**` (MEDIUM/
HIGH candidates; LOW is dropped, never written) and
`_meta/forward-learning-cache/**` (source watermarks). It never adopts a
finding anywhere else — the whole point of "surfaced, never auto-adopted".

Public surface:

    Source(slug, kind, type, url, trusted=False)
        One operator-configured approved source. `kind` is "idea" |
        "pattern" | "reference" (mirrors the design's three named
        categories); `type` is "feed" | "repo" | "web" (informational —
        this thin v1's `default_fetcher` treats every type the same way,
        a single stdlib-urllib GET).

    Candidate(slug, title, body, url)
        One raw item a fetcher returned for a source, pre-scoring.

    load_sources(vault_path) -> list[Source]
        Reads the operator's sources config
        (`_meta/forward-learning-sources.json`). Missing file -> `[]`
        (opt-in: no config means no sources means no scan).

    run_forward_learning(vault_path, *, fetcher=None, now=None) -> ScanResult
        One scan pass over every configured source: fetch candidates
        (`fetcher` defaults to `default_fetcher`, injectable for tests so a
        scan is fully offline-deterministic), score each with the
        deterministic rubric, write MEDIUM/HIGH to the watchlist (LOW
        dropped), advance each source's watermark. Returns a summary.

CLI: `python3 forward_learning.py --vault-path <path>`.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from vault_lock import atomic_write  # noqa: E402

__all__ = [
    "Source",
    "Candidate",
    "ScanResult",
    "load_sources",
    "default_fetcher",
    "run_forward_learning",
    "main",
]

_USER_AGENT = "agentm-forward-learning/1.0"
_FETCH_TIMEOUT_SEC = 10

SOURCES_CONFIG_REL = Path("_meta") / "forward-learning-sources.json"
STATE_REL = Path("_meta") / "forward-learning-cache" / "state.json"
WATCHLIST_REL = Path("personal") / "_watchlist"

VALID_KINDS = ("idea", "pattern", "reference")
VALID_TYPES = ("feed", "repo", "web")

# Score thresholds mirror adapt_skills.py's own rubric numbers (>=3 HIGH,
# >=1 MEDIUM, else LOW) — not re-derived, reused deliberately so the two
# pipelines' tiers mean the same thing to an operator reviewing both.
HIGH_THRESHOLD = 3
MEDIUM_THRESHOLD = 1

# A candidate body shorter than this reads as a stub link, not a real find.
_SUBSTANTIVE_BODY_MIN_CHARS = 80


@dataclass(frozen=True)
class Source:
    slug: str
    kind: str
    type: str
    url: str
    trusted: bool = False


@dataclass(frozen=True)
class Candidate:
    slug: str
    title: str
    body: str
    url: str


@dataclass
class _ScoredCandidate:
    candidate: Candidate
    source: Source
    score: int
    rules_fired: list = field(default_factory=list)

    @property
    def tier(self) -> str:
        if self.score >= HIGH_THRESHOLD:
            return "HIGH"
        if self.score >= MEDIUM_THRESHOLD:
            return "MEDIUM"
        return "LOW"


@dataclass
class ScanResult:
    sources_scanned: int
    candidates_seen: int
    written: list  # list of Path — one per watchlist entry actually written
    dropped_low: int


# -----------------------------------------------------------------------------
# Sources config + watermark state
# -----------------------------------------------------------------------------

def load_sources(vault_path: Path) -> list:
    path = Path(vault_path) / SOURCES_CONFIG_REL
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    sources = []
    for entry in data.get("sources", []):
        kind = entry.get("kind")
        type_ = entry.get("type")
        if kind not in VALID_KINDS or type_ not in VALID_TYPES:
            continue  # malformed entry — skip, don't fail the whole scan
        sources.append(
            Source(
                slug=entry["slug"],
                kind=kind,
                type=type_,
                url=entry["url"],
                trusted=bool(entry.get("trusted", False)),
            )
        )
    return sources


def _state_path(vault_path: Path) -> Path:
    return Path(vault_path) / STATE_REL


def _load_state(vault_path: Path) -> dict:
    path = _state_path(vault_path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(vault_path: Path, state: dict) -> None:
    atomic_write(_state_path(vault_path), json.dumps(state, indent=2, sort_keys=True))


# -----------------------------------------------------------------------------
# Fetching (stdlib-only, mirrors adapt_skills.py / discover_skills.py's
# urllib pattern; graceful on any network failure — never blocks the scan)
# -----------------------------------------------------------------------------

def default_fetcher(source: Source) -> list:
    """Best-effort single GET of `source.url`, wrapped as one Candidate.
    Thin v1: does not parse RSS/Atom feeds or crawl a repo tree — every
    source type gets the same one-candidate-per-fetch treatment. Returns
    `[]` on any network error (timeouts/4xx/5xx never fail a scan, same
    graceful-degradation posture as `adapt_skills.py`'s GitHub enrichment)."""
    req = Request(source.url, headers={"User-Agent": _USER_AGENT})
    try:
        with urlopen(req, timeout=_FETCH_TIMEOUT_SEC) as resp:
            if getattr(resp, "status", 200) >= 400:
                return []
            body = resp.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, socket.timeout, OSError):
        return []
    return [Candidate(slug=source.slug, title=source.slug, body=body, url=source.url)]


# -----------------------------------------------------------------------------
# The deterministic rubric (generalizes adapt_skills.py's 6-rule shape)
# -----------------------------------------------------------------------------

def _score_candidate(candidate: Candidate, source: Source, *, existing_tags: set) -> tuple:
    """Returns (score, rules_fired). Deterministic, offline, no network."""
    score = 0
    rules = []

    if source.trusted:
        score += 1
        rules.append("trusted_source")

    if len(candidate.body.strip()) >= _SUBSTANTIVE_BODY_MIN_CHARS:
        score += 1
        rules.append("substantive_body")

    body_lower = candidate.body.lower()
    if any(tag.lower() in body_lower for tag in existing_tags):
        score += 1
        rules.append("complements_existing_convention")

    return score, rules


# -----------------------------------------------------------------------------
# Watchlist write (reuses personal/_watchlist/ — the generalized sibling of
# personal/_skill-watchlist/; watchlist_review.py scans both)
# -----------------------------------------------------------------------------

def _slugify(text: str) -> str:
    out = []
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-") or "item"


def _write_watchlist_entry(
    vault_path: Path, scored: "_ScoredCandidate", *, now_iso: str
) -> Path:
    source = scored.source
    candidate = scored.candidate
    item_slug = _slugify(candidate.title or candidate.slug)
    entry_dir = Path(vault_path) / WATCHLIST_REL / source.slug
    entry_path = entry_dir / f"{item_slug}.md"

    excerpt = candidate.body.strip()[:400]
    content = (
        "---\n"
        f"kind: {source.kind}\n"
        "status: pending-review\n"
        f"created: {now_iso}\n"
        f"updated: {now_iso}\n"
        f"source_slug: {source.slug}\n"
        f"source_url: {candidate.url}\n"
        f"source_type: {source.type}\n"
        f"evaluator_classification: {scored.tier}\n"
        f"rubric_score: {scored.score}\n"
        f"rubric_rules_fired: {json.dumps(scored.rules_fired)}\n"
        "---\n"
        f"# {candidate.title}\n\n"
        f"{excerpt}\n\n"
        f"Source: {candidate.url}\n"
    )
    atomic_write(entry_path, content)
    return entry_path


# -----------------------------------------------------------------------------
# The scan
# -----------------------------------------------------------------------------

def _existing_tags(vault_path: Path) -> set:
    """A cheap 'complements existing conventions' signal: every source
    slug + kind already seen in the vault's forward-learning watermark
    state, plus each configured source's own slug — thin v1's stand-in for
    a real corpus-tag index (avoids a heavy recall.py dependency here)."""
    tags = set()
    for source in load_sources(vault_path):
        tags.add(source.slug)
        tags.add(source.kind)
    return tags


def run_forward_learning(
    vault_path: Path, *, fetcher: Optional[Callable] = None, now: Optional[float] = None
) -> ScanResult:
    vault_path = Path(vault_path)
    fetcher = fetcher or default_fetcher
    now = now if now is not None else time.time()
    now_iso = datetime.fromtimestamp(now, tz=timezone.utc).replace(microsecond=0).isoformat()

    sources = load_sources(vault_path)
    state = _load_state(vault_path)
    tags = _existing_tags(vault_path)

    written = []
    candidates_seen = 0
    dropped_low = 0

    for source in sources:
        candidates = fetcher(source)
        candidates_seen += len(candidates)
        for candidate in candidates:
            score, rules = _score_candidate(candidate, source, existing_tags=tags)
            scored = _ScoredCandidate(candidate=candidate, source=source, score=score, rules_fired=rules)
            if scored.tier == "LOW":
                dropped_low += 1
                continue
            written.append(_write_watchlist_entry(vault_path, scored, now_iso=now_iso))
        state.setdefault(source.slug, {})["last_scan"] = now_iso

    _save_state(vault_path, state)

    return ScanResult(
        sources_scanned=len(sources),
        candidates_seen=candidates_seen,
        written=written,
        dropped_low=dropped_low,
    )


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _resolve_vault_path(arg_vault_path: Optional[str]) -> Optional[Path]:
    import os

    if arg_vault_path:
        return Path(arg_vault_path).expanduser()
    env_path = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    return Path(env_path).expanduser() if env_path else None


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Run one forward-learning scan pass.")
    parser.add_argument("--vault-path", help="MemoryVault root (overrides MEMORY_VAULT_PATH env var)")
    args = parser.parse_args(argv)

    vault = _resolve_vault_path(args.vault_path)
    if vault is None or not vault.exists():
        print("ERROR: no vault path resolved (set --vault-path or MEMORY_VAULT_PATH)", file=sys.stderr)
        return 1

    result = run_forward_learning(vault)
    print(
        f"forward-learning: {result.sources_scanned} source(s), "
        f"{result.candidates_seen} candidate(s) seen, {len(result.written)} written "
        f"to the watchlist, {result.dropped_low} dropped (LOW)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
