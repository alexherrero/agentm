#!/usr/bin/env python3
# recall.py — MemoryVault read loop.
#
# Provides the recall operations invoked by:
#   - the SessionStart hook (subcommand: session-start; plan #7a part 2 task 1).
#   - the UserPromptSubmit hook (subcommand: prompt-submit; plan #7a part 2
#     task 2 ships the scaffold, task 3 wires the recall engine).
#   - the operator-facing `query` subcommand (manual semantic search; plan
#     #7a part 2 task 3). Future `/memory search` sub-command in the memory
#     skill will be a thin wrapper around this.
#
# Recall engine (5-step algorithm per locked design call C2):
#   1. Tokenize query.
#   2. Embed query (api / local / stub) + sqlite-vec top-k by cosine sim.
#   3. Grep + frontmatter scan in parallel — keyword match count per entry,
#      filter `status: superseded`, exclude `_archive/` always + `_inbox/`
#      by default.
#   4. Merge: combined = sim × 0.7 + keyword × 0.3 (per design doc;
#      Tech Debt #7 — tune from real use).
#   5. Dedup against caller-provided path set (always-load), return top-K.
#
# All steps degrade gracefully:
#   - sqlite-vec missing / embedding mode unavailable → grep-only recall.
#   - Time budget exceeded → return whatever results gathered so far.
#   - Vault path unresolvable → exit 0 with no output (never blocks hooks).
#
# Vault resolution chain (matches save.py / vec_index.py):
#   1. --vault-path arg (highest priority; overrides env).
#   2. MEMORY_VAULT_PATH env var.
#   3. No fallback — return None (caller decides what to do).
#
# Hook-invocation graceful-skip contract:
#   - If MEMORY_VAULT_PATH unset OR vault doesn't exist → exit 0 with no
#     stdout; stderr "no vault configured" line is optional. The hook
#     never blocks session boot for missing config.
#   - If _always-load/ directory missing → exit 0 with "Loaded 0" line.
#   - If time budget exceeded mid-load → emit partial + warn + exit 0.

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

# embed.py + vec_index.py live in this same scripts/ dir — sys.path-injected
# import so the recall engine can pull in shared helpers (embedding modes,
# sqlite-vec connection open, EMBEDDING_DIM). Lazy imports inside functions
# so a missing dep doesn't break module-level load (mirrors vec_index.py).
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Hard time budgets per locked design call (plan #7a part 2):
#   SessionStart: 500ms
#   UserPromptSubmit: 300ms
SESSION_START_BUDGET_MS = 500
PROMPT_SUBMIT_BUDGET_MS = 300

# Default top-K per locked design call (plan #7a part 2 recall-loop).
DEFAULT_K = 5

# Default per-recall token budget (≈10% of a 200k Claude context).
# 0 = unlimited. Override via --token-budget CLI arg or RECALL_TOKEN_BUDGET env.
DEFAULT_TOKEN_BUDGET = 20_000

# Merge formula weights (per locked design call — recall-loop.md):
#   combined = sim × SIM_WEIGHT + keyword × KEYWORD_WEIGHT
# Tuned 2026-05-20 during plan #7a part 5 task 6 (seed-pass validation)
# against 47 seeded entries + 10 sample queries. The original 0.7/0.3
# values over-weighted unbounded keyword counts: 3 of 10 queries had the
# highest-sim target NOT at #1 because raw-count keyword matches in tangential
# entries (4-6 token overlaps on common words like "convention" / "phase" /
# "report") pushed them above the higher-sim true target by 0.1+ on the
# combined score. Re-tuned to sim-dominant 0.85/0.05 — semantic similarity
# carries the ranking; raw keyword count contributes as a small tiebreaker
# rather than a primary signal. All 10 sample queries now hit top-1 (or
# correctly miss for the off-vault query). See PLAN.md task 6 narrative for
# the full validation log.
SIM_WEIGHT = 0.85
KEYWORD_WEIGHT = 0.05

# V6-3 (PLAN-wave-e-v6-index task 5): Reciprocal Rank Fusion replaces the
# weighted-sum merge above as the live ranking formula. RRF combines ranked
# lists without needing their raw scores to be on comparable scales — each
# stream contributes 1/(RRF_K + rank) per entry (rank is 1-indexed), summed
# across streams. k=60 is the literature default (Cormack et al. 2009);
# kept as a named, tunable constant rather than inlined.
RRF_K = 60

# MemoryOS 4-level fallback cascade (memory-os-architecture-scan.md,
# already-logged prior art): hybrid (BM25+vector both live) -> dense
# (vector-only) -> lexical (BM25-only) -> sqlite (unranked metadata filter
# match only, when both ranked signals are empty).
_FALLBACK_HYBRID = "hybrid"
_FALLBACK_DENSE = "dense"
_FALLBACK_LEXICAL = "lexical"
_FALLBACK_SQLITE = "sqlite"

# Tencent abstraction-altitude (memory-os-architecture-scan.md): query the
# abstracted layer first, drill to raw on demand. Realized here as a rank
# boost for anchor-file entries (_index/_summary — the existing MOC
# convention vault_lint.py already recognizes as _ANCHOR_SLUGS), not a
# separate query phase — a lighter but faithful reading of "prefer the
# abstracted layer" for a single-pass ranked-merge architecture.
_ALTITUDE_ANCHOR_SLUGS = frozenset({"_index", "_summary"})
# Deliberately tiny relative to a real RRF score gap (~1/61 - 1/62 ~= 2.7e-4
# between adjacent top ranks): this must only break near-exact ties in favor
# of the abstracted layer, never override a genuine relevance difference —
# an earlier draft set this to 1/RRF_K (~0.0167) and it drowned out real
# signal outright (any anchor file with one incidental keyword match
# rocketed to #1 regardless of true relevance; caught by this task's own
# eval, scripts/health/eval_v6_retrieval.py, before it shipped).
_ALTITUDE_BOOST = 1e-6

# BM25 (Robertson/Sparck Jones) parameters — literature defaults.
BM25_K1 = 1.5
BM25_B = 0.75

# Suffix list for a deterministic, zero-dependency stemmer (not a full
# Porter stemmer — a bounded, explicit suffix-strip list per FABLE's
# "BM25 (+ stemming/synonyms)" ask). Longest suffixes first so "ies" strips
# before "es"/"s" would partially match it. Synonym expansion is an
# explicit, honest v0 gap — no synonym set exists yet to ground one in;
# fabricating a list blind would be worse than not having one.
_STEM_SUFFIXES = ("ing", "edly", "ed", "ies", "es", "ly", "s")
_STEM_MIN_STEM_LEN = 3


def _stem(token: str) -> str:
    for suffix in _STEM_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= _STEM_MIN_STEM_LEN:
            return token[: -len(suffix)]
    return token

# Path convention: always-load entries live under <vault>/personal/_always-load/.
# Group-scoped _always-load/ dirs (e.g. work-public/_always-load/) are reserved
# for future per-group recall; v0.1.0 hardwires personal/.
_ALWAYS_LOAD_REL = Path("personal") / "_always-load"

# Directories excluded from recall walks. _archive/ is always excluded
# (audit-trail content, never surfaced to the agent). _dream-staging/ is
# always excluded too (L1/F4 fix: dream.py already excludes it from its own
# source walk, but recall.py had no matching entry -- a bulk-review batch's
# proposal files, each embedding a full copy of a real note's content,
# were keyword-recall candidates until this line closed the gap). _inbox/
# is excluded by default but can be opted in via --include-inbox (raw
# unfiltered capture; surfacing in recall would inject low-quality
# candidate noise).
_EXCLUDE_DIR_NAMES = {"_archive", "_dream-staging"}
_INBOX_DIR_NAME = "_inbox"


def _is_inbox_path(rel_path: str) -> bool:
    """True if `_inbox` appears as a path component in a POSIX-style
    relative path string (as stored in `entry_meta.path`). A defense-in-
    depth backstop for `_vec_search`/`_vec_search_filtered`: those two
    query the vector index directly by rowid and have no path-walk to
    apply `_iter_entry_paths`'s own `_inbox` exclusion to (unlike
    `_bm25_search`/`_grep_search`, which already honor `include_inbox`).
    Found by a retroactive /review: nothing indexes `_inbox` content
    today (`capture.py`'s writer never calls `vec_index.enqueue`), but
    if anything ever did, an unfiltered vec-search result set would
    surface it in ordinary semantic recall with no exclusion at all."""
    return _INBOX_DIR_NAME in Path(rel_path).parts

# Tokenization for grep search: split on non-alphanumeric, lowercase, drop
# tokens shorter than _MIN_TOKEN_LEN. Skipping classical stopword filtering
# in v1 — keep tokenization simple + greppable; tune via real-use feedback.
_MIN_TOKEN_LEN = 3
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: len(chars) / 4 (standard approximation, ±20%).

    Good enough for budget-enforcement heuristics; not used for billing or
    exact reporting. Returns at least 1 to avoid zero-cost infinite loops.
    """
    return max(1, len(text) // 4)


# Always-load priority tiers (R0.8 / voice#0). `priority:` frontmatter maps
# to a sort rank consumed before the token budget: `high` first, unset/
# unrecognized in the middle, `low` last — so a large low-priority entry
# can't crowd out smaller higher-priority ones by alphabetical luck alone.
_ALWAYS_LOAD_PRIORITY_RANK = {"high": 0, "low": 2}
_ALWAYS_LOAD_DEFAULT_PRIORITY_RANK = 1


def _always_load_priority_rank(fm: dict[str, str]) -> int:
    """Map an always-load entry's `priority:` frontmatter to a sort rank."""
    value = (fm.get("priority") or "").strip().lower()
    return _ALWAYS_LOAD_PRIORITY_RANK.get(value, _ALWAYS_LOAD_DEFAULT_PRIORITY_RANK)


def _resolve_token_budget(arg_value: int | None) -> int:
    """Resolve token budget: --token-budget arg → RECALL_TOKEN_BUDGET env → default.

    Returns 0 for unlimited (0 or negative disables budget enforcement).
    """
    if arg_value is not None:
        return arg_value
    env_val = os.environ.get("RECALL_TOKEN_BUDGET", "").strip()
    if env_val:
        try:
            return int(env_val)
        except ValueError:
            pass
    return DEFAULT_TOKEN_BUDGET


def _apply_token_budget(
    blocks: list[str],
    slugs: list[str],
    token_budget: int,
) -> tuple[list[str], list[str], int]:
    """Fit blocks into the token budget (highest-salience/priority first).

    Walks blocks in order (caller must pass them pre-ordered, highest
    priority/salience first). R0.8 / voice#0: an oversized block is SKIPPED,
    not a hard stop — smaller entries later in the order can still fit after
    a large one overflows, rather than the entire tail being dropped at the
    first entry that doesn't fit. Returns (kept_blocks, kept_slugs,
    omitted_count); output order matches the input order (not a repack).

    If token_budget <= 0, returns all blocks unchanged (0 = unlimited).
    """
    if token_budget <= 0 or not blocks:
        return blocks, slugs, 0
    kept_blocks: list[str] = []
    kept_slugs: list[str] = []
    tokens_used = 0
    for block, slug in zip(blocks, slugs):
        est = _estimate_tokens(block)
        if tokens_used + est > token_budget:
            continue  # this one doesn't fit — a smaller later entry still might
        kept_blocks.append(block)
        kept_slugs.append(slug)
        tokens_used += est
    omitted = len(blocks) - len(kept_blocks)
    return kept_blocks, kept_slugs, omitted


def _resolve_vault_path(arg_vault_path: str | None) -> Path | None:
    """Resolve vault path per the chain: --vault-path → MEMORY_VAULT_PATH env → None.

    Returns None if no path resolves. Callers should treat None as
    "graceful-skip" — exit 0 with no output.
    """
    if arg_vault_path:
        return Path(arg_vault_path).expanduser()
    env_path = os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    return None


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Parse YAML frontmatter from a markdown file content string.

    Returns (frontmatter_dict, body). If no frontmatter present (no leading
    `---\\n`), returns ({}, content). Inline parser — handles the limited
    YAML subset that save.py / evolve.py write (string values, simple lists
    in `[a, b]` form). PyYAML is NOT a hook-time dependency.
    """
    if not content.startswith("---\n"):
        return {}, content
    end = content.find("\n---\n", 4)
    if end == -1:
        return {}, content
    fm_text = content[4:end]
    body = content[end + 5:]  # skip "\n---\n"
    fm: dict[str, str] = {}
    for line in fm_text.split("\n"):
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        # Strip surrounding quotes if present.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        fm[key] = value
    return fm, body


def _format_entry_for_injection(slug: str, fm: dict[str, str], body: str) -> str:
    """Format a single always-load entry for stdout injection.

    Format (markdown):
        ### <slug> (kind: <kind>, tags: <tags>)
        <body>

    Keeps the formatting minimal so the agent gets the entry's content
    without ceremony. Frontmatter is summarized in the header so the agent
    can see kind + tags without the full YAML.
    """
    kind = fm.get("kind", "unknown")
    tags = fm.get("tags", "")
    header = f"### {slug} (kind: {kind}"
    if tags and tags not in {"[]", ""}:
        header += f", tags: {tags}"
    header += ")"
    return f"{header}\n\n{body.strip()}"


def session_start(
    *,
    vault: Path | None,
    budget_ms: int = SESSION_START_BUDGET_MS,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    stdout=sys.stdout,
    stderr=sys.stderr,
) -> int:
    """Load _always-load/*.md entries; emit to stdout; transparency line to stderr.

    Returns exit code:
        0 — always, even on errors (graceful-skip contract).

    Errors are surfaced to stderr but never propagated to exit code. The hook
    contract is "never block session boot".
    """
    deadline = time.monotonic() + (budget_ms / 1000.0)

    if vault is None:
        # No vault configured. Exit 0 silently — session proceeds without memory.
        return 0
    if not vault.exists():
        print(
            f"[memory-recall-session-start] vault path not found: {vault} (skipping)",
            file=stderr,
        )
        return 0

    always_load_dir = vault / _ALWAYS_LOAD_REL
    if not always_load_dir.exists() or not always_load_dir.is_dir():
        # Vault exists but no always-load entries yet.
        print(
            "[memory-recall-session-start] Loaded 0 MemoryVault always-load entries",
            file=stderr,
        )
        return 0

    # Glob *.md (top-level only; _always-load/ is flat by convention — see
    # save.py's --always-load routing comment). This alphabetical order is
    # only the READ order for the time-budget walk below; the order entries
    # are token-budgeted in is priority-first (R0.8 / voice#0) — see the
    # sort after the read loop.
    candidates = sorted(always_load_dir.glob("*.md"))

    parsed_entries: list[tuple[str, dict[str, str], str]] = []  # (slug, fm, body)
    # A non-positive budget is interpreted as "deadline already exceeded".
    # Forces immediate overrun + partial-results path regardless of
    # platform-specific monotonic clock resolution (Windows + some
    # high-resolution-but-quantized environments could otherwise observe
    # `time.monotonic() == deadline` on the first iteration check with
    # budget=0, where the `>=` comparison might or might not trip).
    # Operators using zero/negative budgets in production is a category
    # error; this branch makes the degraded-graceful path deterministic
    # for smoke tests + signals "you set this too low" via the
    # transparency warning.
    overrun = budget_ms <= 0
    if overrun:
        # Skip the entire walk — record no slugs; transparency line will
        # report 0 loaded + warning. Equivalent to deadline tripping on
        # iteration 1.
        candidates = []

    for md_path in candidates:
        # Budget check before each file.
        if time.monotonic() >= deadline:
            overrun = True
            break
        try:
            content = md_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(
                f"[memory-recall-session-start] warning: unreadable entry {md_path.name}: {e}",
                file=stderr,
            )
            continue
        fm, body = _parse_frontmatter(content)
        # Filter superseded entries (defense-in-depth; supersession normally
        # moves entries to _archive/, but a stale _always-load/ entry could
        # have been flagged superseded without being moved).
        if fm.get("status") == "superseded":
            continue
        parsed_entries.append((md_path.stem, fm, body))

    # Priority-first ordering (R0.8 / voice#0): `priority: low` entries (the
    # heavy voice-style files) sort last, `priority: high` first, everything
    # else in the middle tier — alphabetical is the tiebreaker within a tier,
    # so ordering stays deterministic. This MUST run before the token budget
    # below: without it, a large low-priority entry that alphabetically
    # sorts early crowds out smaller higher-priority entries that sort
    # later — the exact failure this fixes (19 of 37 entries dropped,
    # including pii-guardrails-public-repo + vault-memory-overrides-default).
    parsed_entries.sort(key=lambda e: (_always_load_priority_rank(e[1]), e[0]))
    loaded_slugs = [slug for slug, _fm, _body in parsed_entries]
    blocks = [
        _format_entry_for_injection(slug, fm, body)
        for slug, fm, body in parsed_entries
    ]

    # Apply token budget: entries are now priority-ordered (see above).
    # Fit as many as possible within budget (skip-and-continue — see
    # _apply_token_budget); this is no longer a "highest N survive, tail
    # dropped" truncation, it's a best-fit pass over the priority order.
    blocks, loaded_slugs, token_budget_omitted = _apply_token_budget(
        blocks, loaded_slugs, token_budget
    )
    omitted_slugs = [
        slug for slug, _fm, _body in parsed_entries if slug not in loaded_slugs
    ]

    # Output assembly. Header gives the agent a clear "this is MemoryVault content" marker.
    if blocks or token_budget_omitted > 0:
        print("# MemoryVault — always-load entries", file=stdout)
        print("", file=stdout)
        print(
            "The following entries are loaded at every session start "
            "(durable preferences/workflows/fixes).",
            file=stdout,
        )
        print("", file=stdout)
        for i, block in enumerate(blocks):
            if i > 0:
                print("\n---\n", file=stdout)
            print(block, file=stdout)
        if token_budget_omitted > 0:
            if blocks:
                print("\n---\n", file=stdout)
            print(
                f"> [!NOTE] recall truncated: {token_budget_omitted} always-load "
                f"entries omitted to stay within token budget "
                f"(budget={token_budget:,} tokens estimated): "
                f"{', '.join(omitted_slugs)}",
                file=stdout,
            )

    # Transparency line on stderr (shown in hook logs, not agent context).
    slug_list = ", ".join(loaded_slugs) if loaded_slugs else "(none)"
    transparency = (
        f"[memory-recall-session-start] Loaded {len(loaded_slugs)} "
        f"MemoryVault always-load entries: {slug_list}"
    )
    if overrun:
        transparency += (
            f" (WARNING: {budget_ms}ms time budget exceeded; partial results — "
            f"{len(candidates) - len(loaded_slugs) - token_budget_omitted} entries skipped)"
        )
    if token_budget_omitted > 0:
        transparency += (
            f" (token budget: {token_budget_omitted} entries omitted; "
            f"budget={token_budget:,})"
        )
    print(transparency, file=stderr)
    return 0


def _read_prompt_from_stdin(stdin=sys.stdin) -> str | None:
    """Read the UserPromptSubmit JSON payload from stdin + extract `prompt`.

    Claude Code's UserPromptSubmit hook receives JSON like:
        {"hookEventName": "UserPromptSubmit", "prompt": "the user's text", ...}

    Returns the prompt string, or None on any parse failure (including empty
    stdin, malformed JSON, or missing `prompt` field). Caller treats None as
    "graceful-skip — exit 0 silently".

    The function does NOT raise on parse errors — it's the soft-failure layer
    that keeps the hook from ever blocking the user prompt.
    """
    try:
        raw = stdin.read()
    except Exception:  # pragma: no cover — stdin EOF or similar
        return None
    if not raw or not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    prompt = payload.get("prompt")
    if not isinstance(prompt, str):
        return None
    return prompt


def _collect_always_load_paths(vault: Path) -> set[str]:
    """Return the set of always-load entry paths (relative to vault root) for dedup.

    The UserPromptSubmit hook must NOT re-inject entries the SessionStart hook
    already loaded. Returns a set of vault-relative path strings (POSIX-style,
    matching the relative-path convention used in entry frontmatter).
    """
    always_load_dir = vault / _ALWAYS_LOAD_REL
    if not always_load_dir.exists():
        return set()
    out: set[str] = set()
    for md_path in always_load_dir.glob("*.md"):
        # Vault-relative POSIX path (consistent with save.py's path convention).
        rel = md_path.relative_to(vault).as_posix()
        out.add(rel)
    return out


def _tokenize(text: str) -> list[str]:
    """Tokenize text for keyword matching.

    Lowercases, extracts alphanumeric runs, drops tokens shorter than
    _MIN_TOKEN_LEN. Used for both query tokenization + entry-content
    tokenization (symmetric).
    """
    return [t for t in _TOKEN_PATTERN.findall(text.lower()) if len(t) >= _MIN_TOKEN_LEN]


def _iter_entry_paths(
    vault: Path,
    *,
    include_inbox: bool = False,
) -> list[Path]:
    """Walk vault, yield all *.md entry paths (subject to filtering invariants).

    Excludes:
      - `_archive/` and `_dream-staging/` subtrees (always — audit-trail /
        staging content, never surfaced to the agent).
      - `_inbox/` subtrees unless `include_inbox=True`.
      - Hidden directories (dirnames starting with `.`).

    Does NOT filter by frontmatter `status` (that happens at match time).
    Walks `<vault>/**` so all groups (`personal/`, `work-public/`,
    future per-group dirs) are covered uniformly.

    V5-14 (agentm-memory-index.md / agentm-memory-system.md): the walk
    routes through the storage seam's `list`/`info` verbs
    (`DeviceLocalBackend`, recursing manually — the seam's `list` is one
    directory level, not a recursive walk) rather than a raw `os.walk`.
    Still returns `Path` objects (Locator -> `vault.joinpath(*parts)`) so
    every downstream caller (`_grep_search`, deadline checks, `.relative_to`)
    is unchanged — the seam-routing is internal to this function.
    """
    out: list[Path] = []
    if not vault.exists():
        return out
    from storage_device_local import DeviceLocalBackend  # noqa: E402 (lazy, mirrors _vec_search's pattern)
    backend = DeviceLocalBackend(root=vault)

    def _walk(locator) -> None:
        try:
            children = backend.list(locator)
        except Exception:
            return
        for child in children:
            name = child.name
            try:
                info = backend.info(child)
            except FileNotFoundError:
                continue
            if info.is_dir:
                if name in _EXCLUDE_DIR_NAMES:
                    continue
                if name == _INBOX_DIR_NAME and not include_inbox:
                    continue
                if name.startswith("."):
                    continue
                _walk(child)
            elif name.endswith(".md"):
                out.append(vault.joinpath(*child.parts))

    _walk(backend.resolve())
    return out


def _grep_search(
    vault: Path,
    query_tokens: list[str],
    *,
    deadline: float | None = None,
    include_inbox: bool = False,
    filter_criteria: dict[str, str] | None = None,
) -> dict[str, int]:
    """Scan vault entries for keyword matches.

    Returns {relative_path_posix: keyword_match_count}. Each entry's score
    is the count of DISTINCT query tokens that appear (as substring) in the
    entry's searchable text (slug + tags + title + first 500 chars of body).
    Entries with `status: superseded` are filtered out.

    `filter_criteria` (from `parse_filter`) applies the same `--filter`
    predicate the SQL path uses — the grep fallback's equivalent of the
    joined `WHERE` clause, since a grep walk has no SQL to join against.

    If `deadline` is set and elapsed past it, the walk stops early and
    returns partial results (degraded-graceful).
    """
    if not query_tokens:
        return {}
    results: dict[str, int] = {}
    # V5-14: reads route through the seam's `read` verb rather than a raw
    # Path.read_text — same bytes-mode utf-8 decode DeviceLocalBackend.read
    # always did underneath, just called through the seam.
    from storage_device_local import DeviceLocalBackend  # noqa: E402
    backend = DeviceLocalBackend(root=vault)
    for md_path in _iter_entry_paths(vault, include_inbox=include_inbox):
        if deadline is not None and time.monotonic() >= deadline:
            break
        # Broad catch on file read: OSError for IO problems, UnicodeDecodeError
        # for invalid UTF-8 (rare but possible if an entry was hand-edited in
        # a non-UTF-8 editor or a cross-platform sync corrupted the encoding).
        # Skip the entry rather than crash the whole walk.
        try:
            content = backend.read(backend.resolve(*md_path.relative_to(vault).parts))
        except (OSError, UnicodeDecodeError):
            continue
        fm, body = _parse_frontmatter(content)
        if fm.get("status") == "superseded":
            continue
        if filter_criteria and not _entry_matches_filter(fm, filter_criteria):
            continue
        slug = md_path.stem
        tags = fm.get("tags", "")
        # Searchable text: slug + tags + first 500 chars of body. Keeps
        # the scoring cheap; tail content rarely changes search relevance
        # for short MemoryVault entries (typical entry < 2 KB).
        searchable = (slug + " " + tags + " " + body[:500]).lower()
        score = sum(1 for t in query_tokens if t in searchable)
        if score > 0:
            rel = md_path.relative_to(vault).as_posix()
            results[rel] = score
    return results


def _bm25_search(
    vault: Path,
    query_tokens: list[str],
    *,
    deadline: float | None = None,
    include_inbox: bool = False,
    filter_criteria: dict[str, str] | None = None,
) -> dict[str, float]:
    """BM25 lexical scoring (V6-3, PLAN-wave-e-v6-index task 5) — replaces
    `_grep_search`'s raw substring-count as the lexical stream RRF fuses.

    Same walk, same exclusions (`status: superseded`, `_archive/`, `--filter`
    criteria) as `_grep_search` — only the scoring changes: proper term-
    frequency saturation (k1) + document-length normalization (b) + inverse
    document frequency, computed over this query's own matched-candidate set
    (a single-walk approximation — a persistent corpus-wide IDF index is a
    separate future build, analogous to vec_index.py's sqlite-vec index;
    this keeps the same one-walk cost `_grep_search` already had). Query and
    document tokens are both stemmed (`_stem`) before matching.

    Returns {relative_path_posix: bm25_score}, score > 0 only.
    """
    if not query_tokens:
        return {}
    stemmed_query = [_stem(t) for t in query_tokens]

    try:
        import chunking  # type: ignore
    except ImportError:
        chunking = None  # type: ignore

    from storage_device_local import DeviceLocalBackend  # noqa: E402
    backend = DeviceLocalBackend(root=vault)

    # V6-10 (task 6): chunk-level term counts, scored per-chunk and
    # aggregated per-doc by max-passage (the doc's BM25 score is its best
    # chunk's score) — replaces the old fixed `body[:500]` window, which
    # silently missed any relevant passage past the first 500 characters
    # of a long entry (task 5's eval traced real recall gaps to exactly
    # this). Document frequency (df) below is still doc-level (does ANY
    # chunk of this doc contain the term), the standard IDF semantics —
    # only the per-doc term-frequency scoring is now chunk-max'd.
    doc_chunk_counts: dict[str, list[dict[str, int]]] = {}
    doc_chunk_lengths: dict[str, list[int]] = {}
    all_chunk_lengths: list[int] = []
    for md_path in _iter_entry_paths(vault, include_inbox=include_inbox):
        if deadline is not None and time.monotonic() >= deadline:
            break
        try:
            content = backend.read(backend.resolve(*md_path.relative_to(vault).parts))
        except (OSError, UnicodeDecodeError):
            continue
        fm, body = _parse_frontmatter(content)
        if fm.get("status") == "superseded":
            continue
        if filter_criteria and not _entry_matches_filter(fm, filter_criteria):
            continue
        slug = md_path.stem
        tags = fm.get("tags", "")
        prefix = (slug + " " + tags + " ").lower()
        chunks = chunking.chunk_text(body) if chunking is not None else [body[:500]]

        chunk_counts_list: list[dict[str, int]] = []
        chunk_lengths_list: list[int] = []
        doc_has_any_query_term = False
        for chunk in chunks:
            chunk_tokens = [
                _stem(t) for t in re.split(r"[^a-z0-9]+", (prefix + chunk).lower())
                if len(t) >= _MIN_TOKEN_LEN
            ]
            if any(t in chunk_tokens for t in stemmed_query):
                doc_has_any_query_term = True
            counts: dict[str, int] = {}
            for t in chunk_tokens:
                counts[t] = counts.get(t, 0) + 1
            chunk_counts_list.append(counts)
            chunk_lengths_list.append(len(chunk_tokens))

        if not doc_has_any_query_term:
            continue  # only score candidates that share at least one stemmed term
        rel = md_path.relative_to(vault).as_posix()
        doc_chunk_counts[rel] = chunk_counts_list
        doc_chunk_lengths[rel] = chunk_lengths_list
        # avgdl scope matches the pre-chunking implementation: computed over
        # the CANDIDATE set only (documents sharing >=1 stemmed query term),
        # not the whole corpus — a whole-corpus avgdl would be dominated by
        # the vault's many short single-paragraph notes, incorrectly
        # penalizing longer, more substantive candidates' chunk lengths
        # relative to a denominator they were never being compared against
        # before (caught by this task's own eval: it silently regressed
        # exactly the long-document case chunking was built to help).
        all_chunk_lengths.extend(chunk_lengths_list)

    if not doc_chunk_counts:
        return {}

    avgdl = sum(all_chunk_lengths) / len(all_chunk_lengths) if all_chunk_lengths else 1.0
    n_docs = len(doc_chunk_counts)

    # Document frequency per stemmed query term: how many DOCUMENTS (not
    # chunks) contain the term in at least one chunk — standard IDF.
    df: dict[str, int] = {}
    for term in set(stemmed_query):
        df[term] = sum(
            1 for chunk_list in doc_chunk_counts.values()
            if any(term in counts for counts in chunk_list)
        )

    results: dict[str, float] = {}
    for rel, chunk_counts_list in doc_chunk_counts.items():
        chunk_lengths_list = doc_chunk_lengths[rel]
        best_score = 0.0
        for counts, dl in zip(chunk_counts_list, chunk_lengths_list):
            dl = dl or 1
            score = 0.0
            for term in stemmed_query:
                tf = counts.get(term, 0)
                if tf == 0:
                    continue
                n_t = df.get(term, 0)
                # BM25 IDF (Robertson-Sparck-Jones, +1 smoothing to stay
                # non-negative when n_t is close to n_docs on a tiny set).
                idf = math.log((n_docs - n_t + 0.5) / (n_t + 0.5) + 1.0)
                score += idf * (tf * (BM25_K1 + 1)) / (tf + BM25_K1 * (1 - BM25_B + BM25_B * dl / avgdl))
            if score > best_score:
                best_score = score
        if best_score > 0:
            results[rel] = best_score
    return results


def _rrf_fuse(*rank_sources: dict[str, float], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal Rank Fusion over any number of {path: raw_score} dicts
    (higher raw_score = more relevant, within each source). Each source is
    independently rank-ordered (1-indexed, ties broken by path for
    determinism), then contributes 1/(k + rank) to that path's fused score.
    Paths absent from a source simply don't receive that source's term.

    Returns {path: fused_score}, empty if every source is empty.
    """
    fused: dict[str, float] = {}
    for source in rank_sources:
        if not source:
            continue
        ordered = sorted(source.items(), key=lambda kv: (-kv[1], kv[0]))
        for rank, (path, _raw) in enumerate(ordered, start=1):
            fused[path] = fused.get(path, 0.0) + 1.0 / (k + rank)
    return fused


def _metadata_filter_only(
    vault: Path,
    criteria: dict[str, str],
    *,
    deadline: float | None = None,
    include_inbox: bool = False,
) -> list[str]:
    """MemoryOS fallback level 4 (V6-3): an unranked `--filter`-only match,
    used only when neither BM25 nor vector search produced any candidate for
    a query that does carry filter criteria. No relevance ranking — just
    "these entries match the filter," the same degrade sqlite-only browsing
    would be without any query text at all.
    """
    if not criteria:
        return []
    from storage_device_local import DeviceLocalBackend  # noqa: E402
    backend = DeviceLocalBackend(root=vault)
    out: list[str] = []
    for md_path in _iter_entry_paths(vault, include_inbox=include_inbox):
        if deadline is not None and time.monotonic() >= deadline:
            break
        try:
            content = backend.read(backend.resolve(*md_path.relative_to(vault).parts))
        except (OSError, UnicodeDecodeError):
            continue
        fm, _ = _parse_frontmatter(content)
        if fm.get("status") == "superseded":
            continue
        if _entry_matches_filter(fm, criteria):
            out.append(md_path.relative_to(vault).as_posix())
    return out


# -----------------------------------------------------------------------------
# V6-11 hybrid --filter path (agentm-memory-index.md): a `--filter` expression
# compiles to one SQL WHERE over the entry_meta metadata table, joined with
# the vector MATCH, in a single query — replacing the grep-over-frontmatter
# pass for the filtered case. Grep stays the graceful fallback when
# sqlite-vec is absent, applying the same criteria as it walks.
# -----------------------------------------------------------------------------

class FilterError(ValueError):
    """A `--filter` expression is malformed or names an unknown key."""


# Filter key -> entry_meta column. `tag` is special-cased (checks membership
# in the JSON-array `tags` column, not an equality match).
_FILTER_FIELD_MAP: dict[str, str] = {
    "kind": "kind", "project": "project", "status": "status", "group": "group_name",
}


def parse_filter(expr: str | None) -> dict[str, str]:
    """"tag=security AND project=sherwood" -> {"tag": "security", "project":
    "sherwood"}. AND-only (per the design's own example); `{}` for an empty
    expression. Raises `FilterError` on a malformed clause or an unknown key
    — fail loud at parse time, not silently drop half the filter."""
    if not expr or not expr.strip():
        return {}
    criteria: dict[str, str] = {}
    for clause in re.split(r"(?i)\s+AND\s+", expr.strip()):
        clause = clause.strip()
        if not clause:
            continue
        if "=" not in clause:
            raise FilterError(f"malformed filter clause: {clause!r} (expected key=value)")
        key, _, value = clause.partition("=")
        key = key.strip().lower()
        value = value.strip().strip("'\"")
        if key != "tag" and key not in _FILTER_FIELD_MAP:
            raise FilterError(
                f"unknown filter key: {key!r} (supported: tag, "
                + ", ".join(_FILTER_FIELD_MAP) + ")"
            )
        criteria[key] = value
    return criteria


def _derive_project(group_value: str) -> str | None:
    """Mirrors vec_index._extract_meta_from_file's derivation — duplicated
    (not imported) because the two live in different script directories and
    this is three lines, not worth the cross-dir coupling."""
    if not group_value or not group_value.startswith("projects/"):
        return None
    parts = group_value.split("/")
    return parts[1] if len(parts) >= 2 and parts[1] else None


def _entry_matches_filter(fm: dict[str, str], criteria: dict[str, str]) -> bool:
    """Apply `criteria` (from `parse_filter`) to a grep-walked entry's
    frontmatter — the fallback path's equivalent of the SQL WHERE."""
    for key, value in criteria.items():
        if key == "tag":
            if f'"{value}"' not in fm.get("tags", "") and value not in fm.get("tags", ""):
                return False
        elif key == "project":
            if _derive_project(fm.get("group", "")) != value:
                return False
        else:
            field = "group" if key == "group" else key
            if fm.get(field) != value:
                return False
    return True


def _vec_search_filtered(
    vault: Path,
    query_text: str,
    criteria: dict[str, str],
    *,
    k: int,
    deadline: float | None = None,
    mode: str | None = None,
    stderr=sys.stderr,
) -> dict[str, float]:
    """Like `_vec_search`, but the SQL joins `entry_meta` and applies
    `criteria` as an additional `WHERE` — one query, not a post-filter."""
    try:
        from embed import EmbeddingUnavailable, embed_text  # type: ignore
        from vec_index import _open_index  # type: ignore
    except ImportError:
        return {}

    if deadline is not None and time.monotonic() >= deadline:
        return {}
    try:
        embedding = embed_text(query_text, mode=mode)
    except EmbeddingUnavailable as e:
        print(f"[recall.query] embedding unavailable: {e}", file=stderr)
        return {}
    except Exception as e:  # noqa: BLE001 — degraded-graceful catch-all
        print(f"[recall.query] embedding raised {type(e).__name__}: {e}", file=stderr)
        return {}
    if deadline is not None and time.monotonic() >= deadline:
        return {}

    conn = _open_index(vault)
    if conn is None:
        return {}
    try:
        emb_blob = json.dumps(embedding)
        where_parts: list[str] = []
        params: list = [emb_blob, k]
        for key, value in criteria.items():
            if key == "tag":
                where_parts.append("entry_meta.tags LIKE ?")
                params.append(f'%"{value}"%')
            else:
                where_parts.append(f"entry_meta.{_FILTER_FIELD_MAP[key]} = ?")
                params.append(value)
        where_sql = (" AND " + " AND ".join(where_parts)) if where_parts else ""
        sql = (
            "SELECT entries.rowid, distance, entry_meta.path FROM entries "
            "JOIN entry_meta ON entries.rowid = entry_meta.rowid "
            "WHERE embedding MATCH ? AND k = ?" + where_sql + " ORDER BY distance"
        )
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as e:
            print(f"[recall.query] filtered vec search SQL error: {e}", file=stderr)
            return {}
        results: dict[str, float] = {}
        for _rowid, distance, path in rows:
            if _is_inbox_path(path):
                continue
            results[path] = max(0.0, min(1.0, 1.0 - (distance / 2.0)))
        return results
    finally:
        conn.close()


def _vec_search(
    vault: Path,
    query_text: str,
    *,
    k: int,
    deadline: float | None = None,
    mode: str | None = None,
    stderr=sys.stderr,
) -> dict[str, float]:
    """Embed the query + search the vec-index for top-k nearest entries.

    Returns {relative_path_posix: similarity_score}. similarity_score is
    in [0, 1] where 1 = most similar (computed as 1 - cosine_distance).

    Returns {} (silently) under any of:
      - sqlite-vec not installed / Apple system Python missing
        enable_load_extension
      - embedding mode unavailable (no API key, no local model)
      - query embedding raises any other exception
      - deadline elapsed before vec search completes

    All failures degrade gracefully — caller falls back to grep-only.
    """
    # Lazy imports — keep module-level load fast even if deps missing.
    try:
        from embed import EmbeddingUnavailable, embed_text  # type: ignore
        from vec_index import _open_index  # type: ignore
    except ImportError:
        return {}

    if deadline is not None and time.monotonic() >= deadline:
        return {}

    # Try to embed the query. EmbeddingUnavailable is the soft-fail path.
    try:
        embedding = embed_text(query_text, mode=mode)
    except EmbeddingUnavailable as e:
        print(f"[recall.query] embedding unavailable: {e}", file=stderr)
        return {}
    except Exception as e:  # noqa: BLE001 — degraded-graceful catch-all
        print(f"[recall.query] embedding raised {type(e).__name__}: {e}", file=stderr)
        return {}

    if deadline is not None and time.monotonic() >= deadline:
        return {}

    conn = _open_index(vault)
    if conn is None:
        # sqlite-vec unavailable. Caller falls back to grep-only.
        return {}
    try:
        emb_blob = json.dumps(embedding)
        # sqlite-vec MATCH operator: top-k nearest by distance.
        # `vec0` virtual tables expose `distance` (lower = closer).
        try:
            cursor = conn.execute(
                "SELECT entries.rowid, distance "
                "FROM entries "
                "WHERE embedding MATCH ? AND k = ? "
                "ORDER BY distance",
                (emb_blob, k),
            )
            rows = cursor.fetchall()
        except sqlite3.OperationalError as e:
            print(f"[recall.query] vec search SQL error: {e}", file=stderr)
            return {}
        results: dict[str, float] = {}
        for rowid, distance in rows:
            # Look up the path for this rowid.
            meta_cursor = conn.execute(
                "SELECT path FROM entry_meta WHERE rowid = ?", (rowid,)
            )
            meta_row = meta_cursor.fetchone()
            if not meta_row:
                continue
            rel_path = meta_row[0]
            if _is_inbox_path(rel_path):
                continue
            # Convert distance to similarity. sqlite-vec's default for vec0
            # is cosine distance in [0, 2]; similarity = 1 - distance/2
            # clamps to [0, 1]. (For L2 distance the conversion would be
            # different; vec0 with FLOAT[N] uses cosine by default per
            # sqlite-vec docs.)
            sim = max(0.0, min(1.0, 1.0 - (distance / 2.0)))
            results[rel_path] = sim
        return results
    finally:
        conn.close()


def _drift_check_vec_hits(
    vault: Path,
    vec_results: dict[str, float],
    *,
    deadline: float | None = None,
    stderr=sys.stderr,
) -> dict[str, float]:
    """V4 #37 task 5: per-hit drift check + grep-only fallback.

    For each path in `vec_results`, compares the source file's mtime against
    the row's `indexed_at`. Drifted entries are:
      - enqueued for re-embed via `vec_index.enqueue(..., op="upsert")`
      - removed from `vec_results` (so the merge step uses keyword-only
        score for that entry — the file's content is re-read at grep time
        anyway, so the result remains useful)

    A transparency stderr line is emitted iff any drift was detected.

    Budget-aware: if `deadline` elapses mid-check, aborts the remaining
    drift-checks + returns whatever state was reached. Drift-not-checked
    entries stay in `vec_results` with their original vec score (better-
    than-nothing fallback per locked design).

    Graceful-skip: if vec_index can't be imported (sqlite-vec missing /
    install-skipped), the input dict is returned unchanged.

    Per V4 #37 plan #21 task 5.
    """
    if not vec_results:
        return vec_results
    try:
        import vec_index  # type: ignore
    except ImportError:
        return vec_results

    drifted_count = 0
    aborted = False
    fresh_results: dict[str, float] = {}

    for path, sim in vec_results.items():
        if deadline is not None and time.monotonic() >= deadline:
            # Budget exhausted — return what we've computed + carry the rest
            # through unchanged (better-than-nothing per the design call).
            aborted = True
            fresh_results[path] = sim
            continue
        try:
            drifted = vec_index.is_entry_drifted(vault, path)
        except Exception:  # noqa: BLE001 — defensive; never break recall on drift-check failure
            # Treat as not-drifted on exception (drift-check is best-effort).
            fresh_results[path] = sim
            continue
        if drifted:
            drifted_count += 1
            # Enqueue for re-embed via the existing async path. Extract
            # embed-text matching save.py's `{slug} [tags]\n\n{first_para}`
            # format so the re-embed produces a consistent vector shape.
            try:
                src = vault / path
                embed_text = vec_index._extract_embed_text_from_file(src)
                vec_index.enqueue(vault, path, "upsert", text=embed_text)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[recall] drift-detect enqueue failed for {path}: {exc}",
                    file=stderr,
                )
            # Drop from vec_results — current query falls back to grep-only.
        else:
            fresh_results[path] = sim

    if drifted_count > 0:
        notice = (
            f"[recall] {drifted_count} entries flagged for re-embed "
            f"(drift detected); falling back to grep-only for those hits"
        )
        if aborted:
            remaining = len(vec_results) - len(fresh_results) - drifted_count
            notice += f"; drift-check budget-aborted with {remaining} hits unchecked"
        print(notice, file=stderr)

    return fresh_results


def query(
    *,
    vault: Path,
    query_text: str,
    k: int = DEFAULT_K,
    dedup_paths: set[str] | None = None,
    include_inbox: bool = False,
    deadline: float | None = None,
    mode: str | None = None,
    filter_expr: str | None = None,
    stderr=sys.stderr,
) -> list[dict]:
    """Run the 5-step recall engine.

    Steps (per locked design call C2 / recall-loop.md §recall-engine):
      1. Tokenize query (lightweight; doesn't need to be in budget).
      2. Vec search — embed query + sqlite-vec top-k by cosine similarity.
         Returns {} on any failure (graceful — caller falls back).
      3. Grep + frontmatter search — walk vault, count keyword matches
         per entry; filter `status: superseded`; respect `_inbox/` flag.
      4. Merge — union of both result sets; combined score =
         sim × SIM_WEIGHT + keyword × KEYWORD_WEIGHT.
      5. Dedup against `dedup_paths` (typically the always-load set);
         sort by combined score; return top-k.

    Returns a list of dicts:
        [{"path": "<vault-relative-POSIX>", "slug": "<slug>",
          "sim": <float in [0,1]>, "keyword": <int>,
          "combined": <float>}, ...]
    sorted by combined score descending.

    Degraded-graceful: if vec search fails, returns grep-only results
    (still scored via the merge formula with sim=0 for all entries).

    `filter_expr` (V6-11 hybrid recall, agentm-memory-index.md): a
    `tag=security AND project=sherwood`-shaped expression. When present, the
    vec half runs as one SQL query joining `entry_meta`'s `WHERE` with the
    vector `MATCH` (replacing the grep-over-frontmatter pass for the
    filtered case); the grep half (still run as the graceful fallback when
    sqlite-vec is unavailable) applies the same criteria as it walks.
    Raises `FilterError` on a malformed expression — fail loud at parse
    time, before either search runs.
    """
    if dedup_paths is None:
        dedup_paths = set()
    if not query_text or not query_text.strip():
        return []
    query_tokens = _tokenize(query_text)
    criteria = parse_filter(filter_expr)

    # Vec search first — typically dominates the time budget (network /
    # model-load latency on the embed call). Done before grep so we can
    # short-circuit on budget overrun and still return grep results
    # (grep is fast — typical <50ms on <100 entries).
    if criteria:
        vec_results = _vec_search_filtered(
            vault, query_text, criteria, k=max(k * 2, 10),
            deadline=deadline, mode=mode, stderr=stderr,
        )
    else:
        vec_results = _vec_search(
            vault, query_text, k=max(k * 2, 10),
            deadline=deadline, mode=mode, stderr=stderr,
        )

    # V4 #37: per-hit drift check. Each vec result's source file mtime is
    # compared against the row's indexed_at; drifted hits enqueue for
    # re-embed + get dropped from vec_results (the current query falls
    # back to grep-only for those entries — content still searched at
    # query time, just not vec-scored against a stale embedding). Cheap:
    # one os.stat per hit. Budget-aware: aborts remaining drift-checks
    # if deadline elapsed (returns whatever drift-checks completed).
    vec_results = _drift_check_vec_hits(
        vault, vec_results, deadline=deadline, stderr=stderr,
    )

    # BM25 lexical search — independently scored. Fast (<50ms typical). Even
    # if vec consumed most of the budget, we try it — it's bounded by the
    # _iter_entry_paths walk + per-file deadline check, so it naturally
    # terminates if the budget is fully exhausted. Returns {} for no-time-
    # left rather than blocking.
    bm25_results = _bm25_search(
        vault, query_tokens, deadline=deadline, include_inbox=include_inbox,
        filter_criteria=criteria,
    )

    # V6-3 (PLAN-wave-e-v6-index task 5): RRF fusion replaces the old
    # weighted-sum merge (sim × 0.85 + keyword × 0.05), with the MemoryOS
    # 4-level fallback cascade choosing which streams actually feed it.
    if vec_results and bm25_results:
        fused = _rrf_fuse(vec_results, bm25_results)
    elif vec_results:
        fused = _rrf_fuse(vec_results)
    elif bm25_results:
        fused = _rrf_fuse(bm25_results)
    elif criteria:
        # Level 4: neither ranked signal produced anything -- an unranked
        # metadata-filter-only match (the SQLite-tier fallback).
        fused = {p: 0.0 for p in _metadata_filter_only(
            vault, criteria, deadline=deadline, include_inbox=include_inbox,
        )}
    else:
        fused = {}

    # Tencent abstraction-altitude: boost _index/_summary anchor entries so
    # the abstracted layer surfaces first when relevant, without a second
    # query phase.
    for path in list(fused.keys()):
        if Path(path).stem in _ALTITUDE_ANCHOR_SLUGS:
            fused[path] = fused[path] + _ALTITUDE_BOOST

    # V6-12 (task 6, time-weighted retrieval): decay_score multiplies the
    # fused RRF score for every candidate, not just a post-hoc top-k tag —
    # a stale volatile-tier note should rank lower than an equally-relevant
    # fresh one. Read once per candidate (bounded by the fused candidate
    # set BM25/vec already narrowed down to, not the whole corpus) rather
    # than per top-k result, since lifecycle now has to influence *which*
    # entries make the top-k, not just how they're labeled after.
    try:
        import lifecycle  # type: ignore
    except ImportError:
        lifecycle = None  # type: ignore

    all_paths = set(fused.keys())
    merged: list[dict] = []
    for path in all_paths:
        if path in dedup_paths:
            continue
        sim = vec_results.get(path, 0.0)
        keyword = bm25_results.get(path, 0.0)
        slug = Path(path).stem

        lifecycle_tier = None
        decay_score = 1.0
        if lifecycle is not None:
            try:
                content = (vault / path).read_text(encoding="utf-8")
                fm, _ = _parse_frontmatter(content)
            except (OSError, UnicodeDecodeError):
                fm = {}
            lifecycle_tier = lifecycle.lifecycle_tier_for(fm, path)
            decay_score = lifecycle.compute_decay_score(vault, slug, fm, path)

        combined = fused[path] * decay_score

        entry = {
            "path": path,
            "slug": slug,
            "sim": sim,
            "keyword": keyword,
            "combined": combined,
        }
        if lifecycle_tier is not None:
            entry["lifecycle_tier"] = lifecycle_tier
            entry["decay_score"] = decay_score
        merged.append(entry)
    # Sort by combined desc, tiebreak by sim desc then path asc.
    merged.sort(key=lambda r: (-r["combined"], -r["sim"], r["path"]))
    return merged[:k]


def _format_recall_result(result: dict, body: str, fm: dict[str, str]) -> str:
    """Format a single recall result for stdout injection.

    Header includes slug + kind + sim/keyword breakdown so the agent sees
    why this entry was recalled. Body follows verbatim (frontmatter
    stripped by caller).
    """
    kind = fm.get("kind", "unknown")
    tags = fm.get("tags", "")
    header = (
        f"### {result['slug']} (kind: {kind}, "
        f"sim={result['sim']:.2f}, keywords={result['keyword']}"
    )
    if tags and tags not in {"[]", ""}:
        header += f", tags: {tags}"
    if "lifecycle_tier" in result:
        header += f", tier: {result['lifecycle_tier']}"
    header += ")"
    return f"{header}\n\n{body.strip()}"


def prompt_submit(
    *,
    vault: Path | None,
    prompt: str | None,
    budget_ms: int = PROMPT_SUBMIT_BUDGET_MS,
    k: int = DEFAULT_K,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    include_inbox: bool = False,
    mode: str | None = None,
    stdout=sys.stdout,
    stderr=sys.stderr,
) -> int:
    """Inject query-relevant MemoryVault entries on user prompt submit.

    Calls the recall engine for top-K entries relevant to the prompt,
    dedups against the always-load set (already in session context per
    the SessionStart hook), formats matches as markdown + emits on stdout
    for context injection. Stderr gets a transparency line listing what
    got loaded so the operator can see what memory shaped the response.

    Returns exit code:
        0 — always, even on errors (graceful-skip contract).

    Errors are surfaced to stderr but never propagated to exit code. The
    hook contract is "never block the user prompt".
    """
    deadline = time.monotonic() + (budget_ms / 1000.0)

    if vault is None:
        return 0
    if not vault.exists():
        print(
            f"[memory-recall-prompt-submit] vault path not found: {vault} (skipping)",
            file=stderr,
        )
        return 0
    if prompt is None:
        print(
            "[memory-recall-prompt-submit] no prompt on stdin (skipping)",
            file=stderr,
        )
        return 0

    always_load_paths = _collect_always_load_paths(vault)

    # Non-positive budget → deterministic immediate-overrun path (matches
    # session_start's force-overrun branch). Smoke tests rely on this to
    # exercise the degraded-graceful path without depending on machine speed.
    if budget_ms <= 0:
        results: list[dict] = []
    else:
        try:
            results = query(
                vault=vault,
                query_text=prompt,
                k=k,
                dedup_paths=always_load_paths,
                include_inbox=include_inbox,
                deadline=deadline,
                mode=mode,
                stderr=stderr,
            )
        except Exception as e:  # noqa: BLE001 — never block the prompt
            print(
                f"[memory-recall-prompt-submit] recall engine error ({type(e).__name__}: {e}); "
                "skipping injection",
                file=stderr,
            )
            return 0

    # Build formatted blocks from results (reading entry content).
    # Results are already salience-ordered (combined-score desc) from query().
    raw_blocks: list[str] = []
    raw_slugs: list[str] = []
    # Part G (#46): lazy-import heat_policy for on-demand hit recording.
    # Best-effort — missing heat_policy never blocks the recall pipeline.
    try:
        from heat_policy import record_hit as _record_recall_hit  # type: ignore
    except ImportError:
        _record_recall_hit = None
    try:
        from lifecycle import record_recall_access as _record_lifecycle_access  # type: ignore
    except ImportError:
        _record_lifecycle_access = None
    for result in results:
        md_path = vault / result["path"]
        try:
            content = md_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm, body = _parse_frontmatter(content)
        raw_blocks.append(_format_recall_result(result, body, fm))
        raw_slugs.append(result["slug"])
        # Record the on-demand hit for heat tracking (best-effort).
        if _record_recall_hit is not None:
            _record_recall_hit(vault, result["slug"])
        # V6-1: genuine recall access resets the volatile-tier decay clock
        # (best-effort, no-op for decay-exempt entries). This is the ONLY
        # call site — a lint walk, index rebuild, or dreaming pass must
        # never reach this function.
        if _record_lifecycle_access is not None:
            _record_lifecycle_access(vault, result["slug"], fm, result["path"])

    # Apply token budget: results are highest-salience first → truncation
    # drops the least-relevant tail entries, never the top hits.
    blocks, loaded_slugs, token_budget_omitted = _apply_token_budget(
        raw_blocks, raw_slugs, token_budget
    )

    # L1 (ledger ruling 6): one per-recall counter event, query hashed (never
    # raw text) + the slugs actually surfaced after truncation. Best-effort,
    # this function's sole call site — same discipline as the heat/lifecycle
    # recordings above.
    try:
        from recall_counter import record_recall as _record_recall_event  # type: ignore
        _record_recall_event(prompt, loaded_slugs)
    except ImportError:
        pass

    # Output assembly: only print stdout when we have hits or a truncation notice.
    if blocks or token_budget_omitted > 0:
        print(
            "# MemoryVault — recall hits for your prompt",
            file=stdout,
        )
        print("", file=stdout)
        print(
            f"The following entries match your prompt (top {len(blocks)} by "
            f"semantic+keyword merge; deduped against always-load set).",
            file=stdout,
        )
        print("", file=stdout)
        for i, block in enumerate(blocks):
            if i > 0:
                print("\n---\n", file=stdout)
            print(block, file=stdout)
        if token_budget_omitted > 0:
            if blocks:
                print("\n---\n", file=stdout)
            print(
                f"> [!NOTE] recall truncated: {token_budget_omitted} entries omitted "
                f"to stay within token budget "
                f"(budget={token_budget:,} tokens estimated)",
                file=stdout,
            )

    overrun = (budget_ms <= 0) or (time.monotonic() >= deadline)
    slug_list = ", ".join(loaded_slugs) if loaded_slugs else "(none)"
    transparency = (
        f"[memory-recall-prompt-submit] Loaded {len(loaded_slugs)} relevant "
        f"entries: {slug_list}"
    )
    if overrun:
        transparency += (
            f" (WARNING: {budget_ms}ms time budget exceeded; results may be partial)"
        )
    if token_budget_omitted > 0:
        transparency += (
            f" (token budget: {token_budget_omitted} entries omitted; "
            f"budget={token_budget:,})"
        )
    print(transparency, file=stderr)
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="memory-recall",
        description=(
            "MemoryVault recall operations. Subcommands: session-start (load "
            "_always-load/ entries; called by the SessionStart hook). "
            "prompt-submit and query subcommands land in plan #7a part 2 "
            "tasks 2-3."
        ),
    )
    parser.add_argument(
        "--vault-path",
        required=False,
        help="MemoryVault root (overrides MEMORY_VAULT_PATH env var)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    ss = sub.add_parser(
        "session-start",
        help="load _always-load/ entries + emit on stdout for session-boot injection",
    )
    ss.add_argument(
        "--budget-ms",
        type=int,
        default=SESSION_START_BUDGET_MS,
        help=f"time budget in milliseconds (default: {SESSION_START_BUDGET_MS})",
    )
    ss.add_argument(
        "--token-budget",
        type=int,
        default=None,
        help=(
            f"per-recall token budget (default: {DEFAULT_TOKEN_BUDGET}; "
            "0 = unlimited). Also read from RECALL_TOKEN_BUDGET env."
        ),
    )

    ps = sub.add_parser(
        "prompt-submit",
        help=(
            "read UserPromptSubmit JSON from stdin + inject query-relevant "
            "entries on stdout (scaffold ships in task 2; recall engine wires "
            "in task 3)"
        ),
    )
    ps.add_argument(
        "--budget-ms",
        type=int,
        default=PROMPT_SUBMIT_BUDGET_MS,
        help=f"time budget in milliseconds (default: {PROMPT_SUBMIT_BUDGET_MS})",
    )
    ps.add_argument(
        "--token-budget",
        type=int,
        default=None,
        help=(
            f"per-recall token budget (default: {DEFAULT_TOKEN_BUDGET}; "
            "0 = unlimited). Also read from RECALL_TOKEN_BUDGET env."
        ),
    )

    q = sub.add_parser(
        "query",
        help=(
            "run the recall engine against a query string; prints top-K "
            "results as JSON (one record per line) for scripting / "
            "operator-debug. Useful for tuning the rank-merge weights."
        ),
    )
    q.add_argument("query_text", help="the query string (use '-' to read stdin)")
    q.add_argument("-k", type=int, default=DEFAULT_K,
                   help=f"top-K results to return (default: {DEFAULT_K})")
    q.add_argument("--budget-ms", type=int, default=PROMPT_SUBMIT_BUDGET_MS,
                   help=f"time budget in milliseconds (default: {PROMPT_SUBMIT_BUDGET_MS})")
    q.add_argument("--include-inbox", action="store_true",
                   help="include _inbox/ entries in the search (default: excluded)")
    q.add_argument("--mode", choices=["local", "stub"], default=None,
                   help="embedding mode override (default: local; see embed.py for details)")
    q.add_argument("--filter", dest="filter_expr", default=None,
                   help="hybrid filter, e.g. 'tag=security AND project=sherwood' "
                        "(supported keys: tag, kind, project, status, group)")

    hp = sub.add_parser(
        "heat-policy",
        help=(
            "evaluate the heat-based always-load curation policy. "
            "Reports demotion/promotion candidates (dry-run by default). "
            "Pass --apply to move entries. Part G of ROADMAP #46."
        ),
    )
    hp.add_argument(
        "--apply",
        action="store_true",
        help="apply tier changes (move files). Default: dry-run only.",
    )

    hpin = sub.add_parser(
        "heat-pin",
        help=(
            "pin an entry to always-load (heat_pin: true). "
            "Restores the entry to the always-load directory if it was demoted. "
            "Part G of ROADMAP #46."
        ),
    )
    hpin.add_argument("slug", help="slug of the entry to pin (e.g. 'adr-shape')")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    vault = _resolve_vault_path(args.vault_path)
    if args.cmd == "session-start":
        return session_start(
            vault=vault,
            budget_ms=args.budget_ms,
            token_budget=_resolve_token_budget(args.token_budget),
        )
    if args.cmd == "prompt-submit":
        prompt = _read_prompt_from_stdin()
        return prompt_submit(
            vault=vault,
            prompt=prompt,
            budget_ms=args.budget_ms,
            token_budget=_resolve_token_budget(args.token_budget),
        )
    if args.cmd == "query":
        if vault is None:
            print(
                "ERROR: no vault path resolved (set --vault-path or MEMORY_VAULT_PATH)",
                file=sys.stderr,
            )
            return 1
        if not vault.exists():
            print(f"ERROR: vault path does not exist: {vault}", file=sys.stderr)
            return 1
        query_text = sys.stdin.read() if args.query_text == "-" else args.query_text
        deadline = time.monotonic() + (args.budget_ms / 1000.0)
        try:
            results = query(
                vault=vault,
                query_text=query_text,
                k=args.k,
                include_inbox=args.include_inbox,
                deadline=deadline,
                mode=args.mode,
                filter_expr=args.filter_expr,
            )
        except FilterError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        # JSON-Lines output for scriptability.
        for r in results:
            print(json.dumps(r))
        return 0
    if args.cmd == "heat-policy":
        if vault is None:
            print(
                "ERROR: no vault path resolved (set --vault-path or MEMORY_VAULT_PATH)",
                file=sys.stderr,
            )
            return 1
        try:
            from heat_policy import run_policy  # type: ignore
        except ImportError:
            print("ERROR: heat_policy module not found", file=sys.stderr)
            return 1
        result = run_policy(vault, dry_run=not args.apply)
        print(json.dumps(result))
        return 0
    if args.cmd == "heat-pin":
        if vault is None:
            print(
                "ERROR: no vault path resolved (set --vault-path or MEMORY_VAULT_PATH)",
                file=sys.stderr,
            )
            return 1
        try:
            from heat_policy import pin_entry  # type: ignore
        except ImportError:
            print("ERROR: heat_policy module not found", file=sys.stderr)
            return 1
        ok = pin_entry(vault, args.slug)
        return 0 if ok else 1
    return 1  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
