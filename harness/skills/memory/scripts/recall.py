#!/usr/bin/env python3
# recall.py — MemoryVault read loop.
#
# Provides the recall operations invoked by:
#   - the SessionStart hook (subcommand: session-start; plan #7a part 2 task 1).
#   - the UserPromptSubmit hook (subcommand: prompt-submit; plan #7a part 2
#     task 2 ships the scaffold, task 3 wires the recall engine).
#   - the operator-facing `query` subcommand (manual lexical search; plan
#     #7a part 2 task 3). Future `/memory search` sub-command in the memory
#     skill will be a thin wrapper around this.
#
# Recall engine. This was a hybrid of a vector stream and a lexical one until
# the vector stack was removed (see `wiki/designs/agentm-rescope-week1-
# experiment.md`); what remains is lexical throughout:
#   1. Tokenize query.
#   2. BM25 corpus walk — per-entry score, filter `status: superseded`,
#      exclude `_archive/` and `_inbox/` by default (each independently
#      reopenable: --include-archive / --include-inbox).
#   3. Rank-normalize via RRF, apply altitude boost + lifecycle decay.
#   4. Dedup against caller-provided path set (always-load), return top-K.
#
# All steps degrade gracefully:
#   - Time budget exceeded → the walk is discarded rather than ranked partially.
#   - Vault path unresolvable → exit 0 with no output (never blocks hooks).
#
# Vault resolution chain (matches save.py):
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
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

# Sibling modules live in this same scripts/ dir — sys.path-injected import so
# the recall engine can pull in shared helpers. Lazy imports inside functions so
# a missing optional dep doesn't break module-level load.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Hard time budgets per locked design call (plan #7a part 2):
#   SessionStart: 500ms
#   UserPromptSubmit: 300ms
SESSION_START_BUDGET_MS = 500
PROMPT_SUBMIT_BUDGET_MS = 300

# The standalone `query` CLI subcommand is an explicit, operator-initiated
# search, so it can afford a full corpus walk (~3.1s measured on an M-series
# Mac) where the two interactive hooks above cannot. This budget was sized for
# a cold sentence-transformers load instead, back when the subcommand was the
# one surface that could reach the vector index; the walk it now covers is the
# same order of magnitude, so the number stands on its own terms.
QUERY_CLI_BUDGET_MS = 10_000

# Stream admission (GH #92). A recall stream runs to completion or does not run
# at all; recall never emits a ranking derived from a partially-searched
# corpus. See `query()` for why partial is not merely "degraded" here.
#
# Measured cost that sets these numbers (M-series Mac, 1745-entry vault):
#   full BM25 corpus walk ....................... ~3050ms
# Default top-K per locked design call (plan #7a part 2 recall-loop).
DEFAULT_K = 5

# Default per-recall token budget (≈10% of a 200k Claude context).
# 0 = unlimited. Override via --token-budget CLI arg or RECALL_TOKEN_BUDGET env.
DEFAULT_TOKEN_BUDGET = 20_000

# --- daemon fast path --------------------------------------------------------
# `agentmd` (the Go memory daemon) answers a search off a maintained index in
# ~12ms including process startup, against ~3170ms for this module's own corpus
# walk over the same vault. The interactive hook budgets are 300ms and stay
# there, so on a daemon-backed machine the daemon is the only engine that can
# finish inside one — the in-process engine returned nothing at all on every
# prompt, because a partial corpus walk is discarded rather than ranked (GH #92).
#
# Shelling out is deliberate, not laziness: recall.py is a vendored standalone
# surface, and check-one-way-imports' lc8-bridge rule forbids it from importing
# harness_memory back. A subprocess keeps it dependency-free, and the 12ms
# measurement already includes the spawn.
DAEMON_BIN = "agentmd"

# Most of the 300ms goes to the daemon, and the split is deliberately lopsided.
#
# The fallback's value is almost entirely in the daemon-is-absent case, and that
# case costs nothing to detect: a missing binary raises FileNotFoundError in
# about 2ms, leaving the in-process engine essentially its whole budget. The
# case this number actually governs is daemon-installed-but-slow, and there the
# fallback cannot help — a corpus walk needs ~3170ms on this vault, so whatever
# is left after a daemon timeout is nowhere near enough. Cutting the daemon off
# early to hand 150ms to an engine that needs 3170ms produces exactly what this
# change exists to fix: no results, slower.
#
# 250ms against a measured worst case of 153ms over 18 realistic prompts (p50
# 20ms, p90 138ms). An earlier 150ms setting clipped that tail — one prompt in
# 15 timed out, fell through to the doomed walk, and took 408ms to return
# nothing.
DAEMON_BUDGET_MS = 250

# The daemon ranks the whole vault and knows none of recall's hygiene rules
# (_dream-staging/, _inbox/, _archive/, superseded, memory-root scope), so ask
# for more than k and filter down.
#
# Over-fetching is not free — measured across 15 realistic prompts, p90 went
# 49ms (k=5) → 58ms (k=10) → 66ms (k=15), with worst cases 58ms → 134ms → 218ms.
# 2x is where the curve is still cheap and the yield is worth it: asking for k
# alone left 5 of those 15 prompts with fewer than k admissible hits after
# filtering, and 2x cuts that to 3 while staying inside the daemon's budget.
DAEMON_OVERFETCH = 2

# A prompt is not a query, and handing the daemon the raw one is both slow and
# worse. Measured: "what did we decide about the vault git transport?" takes
# 578ms and ranks a stale audit-findings file first; its extracted terms take
# 138ms and rank the vault-git design first. A 16-word prompt takes 2150ms —
# seven times the entire hook budget — because BM25 scores every document any
# common word appears in, and "what/did/we/about/the" appear in all of them.
#
# So the daemon is asked with content words only, capped. The cap is a guard
# against a pathological prompt rather than the main lever: latency tracks how
# common the terms are, not how many there are.
DAEMON_QUERY_TERM_CAP = 6

# Function words plus the conversational filler this corpus never uses as
# content. Deliberately excludes domain verbs that look like filler but are not
# ("run the gates", "fix the CI gate", "add a test", "update the changelog").
# `_tokenize` below stays stopword-free on purpose: it feeds the in-process BM25,
# whose scoring is tuned around that, and this list is a daemon-query concern.
_DAEMON_STOPWORDS = frozenset("""
a an the and or but if then than that this these those of to in on at by for
with from as is are was were be been being it its am
what which who whom whose when where why how do does did doing done
can could should would will shall may might must
i me my we us our you your he she they them their there here
about into over under again more most some any all no not
just really very please lets let tell show give got make made need want
know think say said now also still even only
last week today yesterday tomorrow
""".split())

# Which slice of the daemon's index ordinary recall may surface.
#
#   memory-root (default) — only notes under the resolved vault (the agent's
#       own tree, `plugins.obsidian-vault.memory_root`). This is the corpus the
#       Python engine searched, and the one agentm-rescope-topology.md's
#       2026-08-10 `memory_root` amendment settled on after the operator's
#       Church/Home/Tech/Other notes "silently entered the agent's retrieval
#       corpus" and were deliberately taken back out.
#   vault — everything the daemon indexes, personal folders included.
#
# Measured before choosing the default (20 prompts, top-5 each): technical
# queries leak nothing, but generic ones a dev session really does type —
# "what should I work on next", "the plan for this week" — pull Church notes
# into context at 13% of results overall. The daemon's rank penalty down-weights
# the unfiled landfill; it does not separate the operator's life from his work.
DAEMON_SCOPE_ENV = "RECALL_DAEMON_SCOPE"
DAEMON_SCOPE_DEFAULT = "memory-root"

# V6-3 (PLAN-wave-e-v6-index task 5): Reciprocal Rank Fusion replaces the
# weighted-sum merge above as the live ranking formula. RRF combines ranked
# lists without needing their raw scores to be on comparable scales — each
# stream contributes 1/(RRF_K + rank) per entry (rank is 1-indexed), summed
# across streams. k=60 is the literature default (Cormack et al. 2009);
# kept as a named, tunable constant rather than inlined.
RRF_K = 60

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
_ALWAYS_LOAD_REL = Path("memory") / "_always-load"

# Directories excluded from recall walks unconditionally. _dream-staging/
# is always excluded (L1/F4 fix: dream.py already excludes it from its own
# source walk, but recall.py had no matching entry -- a bulk-review batch's
# proposal files, each embedding a full copy of a real note's content,
# were keyword-recall candidates until this line closed the gap). _archive/
# used to live in this same always-excluded set; auto-organization part 1
# task 5 gave it its own toggle (--include-archive, mirroring --include-
# inbox exactly) instead, matching the design's own "an archived memory...
# answers an explicit archive search whenever you ask" contract -- see
# `_INCLUDE_ARCHIVE_DIR_NAME` below. _shelf/ was never in this set and
# needs no change: the shelf is a browse convention, not a search boundary,
# so shelved artifacts stay in everyday search by construction.
# Matched against a single directory NAME, not a relative path, so this holds
# the last segment of the scratch space rather than its full `desk/scratch`
# spelling. The stage-2 migration moved that space one level down and briefly
# put the two-segment path here, which silently matched nothing and let dream
# exhaust back into recall.
_EXCLUDE_DIR_NAMES = {"scratch"}
_INBOX_DIR_NAME = "_inbox"
_INCLUDE_ARCHIVE_DIR_NAME = "_archive"


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


# An entry too big to inject whole is excerpted rather than dropped (see
# `_apply_token_budget`'s second pass). The excerpt may claim at most this share
# of the budget, so one enormous hit cannot swallow the room several oversized
# hits should be splitting — 1/k at the default k, which is the fair share in
# the case that motivated this: every hit oversized, every slot yielding nothing.
_EXCERPT_BUDGET_SHARE = 1.0 / DEFAULT_K

# Below this an excerpt is not worth emitting: the header and the truncation
# marker are themselves ~65 tokens, and an allowance that leaves room for little
# else spends a slot on ceremony instead of content.
_MIN_EXCERPT_TOKENS = 200

# Headroom over `token_budget` for the bounded entry read in `prompt_submit`.
# Covers the frontmatter (which is stripped before the body is measured, so it
# is not part of the budget but is part of the bytes) plus a margin, and
# guarantees the read cap sits strictly above the largest block that could ever
# fit whole — see the read-cap comment in `prompt_submit`.
_ENTRY_READ_SLACK_CHARS = 8192

# Marker that opens an excerpted block's body. Leads the body rather than
# trailing it: the agent has to know a block is partial before reading it, not
# after acting on it.
_EXCERPT_MARKER = "> [!NOTE] Excerpt only"


def _truncate_to_tokens(text: str, allowance_tokens: int) -> str:
    """Cut `text` down to at most `allowance_tokens` estimated tokens.

    Breaks on the last whitespace before the limit so an excerpt does not end
    mid-word, but only when that boundary is actually near the limit — a run of
    text with no whitespace in its last quarter should lose a few characters,
    not a quarter of its allowance. Returns `text` unchanged when it already
    fits, "" for a non-positive allowance.
    """
    if allowance_tokens <= 0:
        return ""
    limit = allowance_tokens * 4  # `_estimate_tokens` is len(chars) // 4
    if len(text) <= limit:
        return text
    cut = text[:limit]
    boundary = max(cut.rfind(" "), cut.rfind("\n"))
    if boundary >= int(limit * 0.75):
        cut = cut[:boundary]
    return cut.rstrip()


def _read_entry_head(path: Path, max_chars: int) -> tuple[str, bool]:
    """Read `path`, stopping after `max_chars` characters.

    Returns `(text, was_clipped)`. A `max_chars` of 0 or less reads the whole
    file, which is what an unlimited token budget asks for.

    Text mode, not bytes: the cut then falls on a character boundary and can
    never split a multi-byte character in half. Reading one character past the
    cap is how the clipped flag is learned without a second syscall.
    """
    if max_chars <= 0:
        return path.read_text(encoding="utf-8"), False
    with path.open("r", encoding="utf-8") as fh:
        text = fh.read(max_chars + 1)
    if len(text) > max_chars:
        return text[:max_chars], True
    return text, False


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


def _resolve_budget_ms(arg_value: int | None, default: int) -> int:
    """Resolve an interactive time budget: --budget-ms → RECALL_BUDGET_MS env → default.

    The two interactive subcommands are driven by shell/pwsh hook wrappers that
    pass no flags, so the environment is the only channel a caller has to widen
    the budget. `query` is excluded deliberately: it is invoked directly and can
    already pass --budget-ms.

    This exists because the budget is a production tuning constant that a test
    driving the hook end-to-end would otherwise race. The setup a recall pays
    before the corpus walk even starts is a FIXED cost, independent of corpus
    size. It was 25-42ms of the 300ms prompt-submit budget on an idle M-series
    Mac when opening the vector index dominated it, and is smaller now that
    nothing loads a native extension. On a loaded CI
    runner it can consume the whole budget, at which point `_bm25_search` finds
    the deadline already elapsed, reports `walked 0 of N`, and `query()`
    discards the stream — so recall emits nothing on a two-entry fixture vault
    for reasons that have nothing to do with the behavior under test.

    A non-positive value stays meaningful: 0 or negative is the forced-overrun
    path, so the check here is `is not None`, never truthiness.
    """
    if arg_value is not None:
        return arg_value
    env_val = os.environ.get("RECALL_BUDGET_MS", "").strip()
    if env_val:
        try:
            return int(env_val)
        except ValueError:
            pass
    return default


def _apply_token_budget(
    blocks: list[str],
    slugs: list,
    token_budget: int,
    *,
    excerpt=None,
) -> tuple[list[str], list, int]:
    """Fit blocks into the token budget (highest-salience/priority first).

    Walks blocks in order (caller must pass them pre-ordered, highest
    priority/salience first). R0.8 / voice#0: an oversized block is SKIPPED,
    not a hard stop — smaller entries later in the order can still fit after
    a large one overflows, rather than the entire tail being dropped at the
    first entry that doesn't fit. Returns (kept_blocks, kept_slugs,
    omitted_count); output order matches the input order (not a repack).

    `excerpt`, when given, gives those skipped blocks a second pass rather than
    leaving them dropped. It is called as `excerpt(index, allowance_tokens)` and
    returns a smaller replacement block for `blocks[index]`, or None when it
    cannot build one — in which case that block stays omitted, exactly as
    before. Passing nothing keeps the skip-only behavior.

    Two passes, not one, and the order matters. Whole blocks are packed first,
    so nothing that fits today starts arriving excerpted tomorrow; only the
    budget those blocks left over is spent on excerpts, and a single hit may
    claim at most `_EXCERPT_BUDGET_SHARE` of the whole budget so several
    oversized hits split the remainder instead of the first one taking it all.
    The problem this solves is a block that can never fit at any budget — a
    1MB append-only log the daemon ranks highly for many queries — spending one
    of k retrieval slots and yielding nothing at all.

    `slugs` is measured only by its length matching `blocks` — each element
    rides along with its block and is never itself inspected — so a caller
    may pack a richer per-block payload in here (recall-trace, Loose Ends
    Release 8: `prompt_submit` packs `(slug, hit_dict)` pairs) rather than
    bare slug strings, with no change to which blocks are kept.

    If token_budget <= 0, returns all blocks unchanged (0 = unlimited).
    """
    if token_budget <= 0 or not blocks:
        return blocks, slugs, 0
    kept: dict[int, str] = {}
    overflowed: list[int] = []
    tokens_used = 0
    for i, block in enumerate(blocks):
        est = _estimate_tokens(block)
        if tokens_used + est > token_budget:
            overflowed.append(i)  # doesn't fit — a smaller later entry still might
            continue
        kept[i] = block
        tokens_used += est

    if excerpt is not None:
        cap = max(1, int(token_budget * _EXCERPT_BUDGET_SHARE))
        for i in overflowed:
            allowance = min(cap, token_budget - tokens_used)
            if allowance < _MIN_EXCERPT_TOKENS:
                # `tokens_used` only grows, so nothing later in the order will
                # clear the floor either.
                break
            piece = excerpt(i, allowance)
            if not piece:
                continue
            est = _estimate_tokens(piece)
            if tokens_used + est > token_budget:
                continue
            kept[i] = piece
            tokens_used += est

    order = sorted(kept)
    kept_blocks = [kept[i] for i in order]
    kept_slugs = [slugs[i] for i in order]
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
    #
    # No `excerpt=` here, deliberately. An always-load entry is a standing
    # convention the agent is meant to follow, and half a rule is worse than a
    # named absence — an excerpt that stops before "except when X" reads as the
    # whole rule. A recall hit is context rather than instruction, so
    # `prompt_submit` does excerpt; this side stays all-or-nothing.
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
    include_archive: bool = False,
) -> list[Path]:
    """Walk vault, yield all *.md entry paths (subject to filtering invariants).

    Excludes:
      - `_dream-staging/` subtrees (always — staging content, never
        surfaced to the agent).
      - `_inbox/` subtrees unless `include_inbox=True`.
      - `_archive/` subtrees unless `include_archive=True` (auto-
        organization part 1 task 5 — an archived memory answers an
        explicit archive search, never ordinary recall).
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
    from storage_device_local import DeviceLocalBackend  # noqa: E402 (lazy)
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
                if name == _INCLUDE_ARCHIVE_DIR_NAME and not include_archive:
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
    include_archive: bool = False,
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
    for md_path in _iter_entry_paths(vault, include_inbox=include_inbox, include_archive=include_archive):
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
    include_archive: bool = False,
    filter_criteria: dict[str, str] | None = None,
    status: dict | None = None,
) -> dict[str, float]:
    """BM25 lexical scoring (V6-3, PLAN-wave-e-v6-index task 5) — replaces
    `_grep_search`'s raw substring-count as the lexical stream RRF fuses.

    Same walk, same exclusions (`status: superseded`, `_archive/`, `--filter`
    criteria) as `_grep_search` — only the scoring changes: proper term-
    frequency saturation (k1) + document-length normalization (b) + inverse
    document frequency, computed over this query's own matched-candidate set
    (a single-walk approximation — a persistent corpus-wide IDF index is a
    separate future build; this keeps the same one-walk cost `_grep_search`
    already had). Query and
    document tokens are both stemmed (`_stem`) before matching.

    Returns {relative_path_posix: bm25_score}, score > 0 only.

    `status`, when passed, is populated with `{"complete": bool, "walked": int,
    "total": int}`. `complete` is False when `deadline` cut the corpus walk
    short, and the caller is expected to DISCARD the results in that case
    rather than treat them as partial (GH #92).

    Why a truncated walk is not usable as "partial results": IDF and `avgdl`
    are both computed over the candidate set this walk actually reached, so
    stopping early does not yield a prefix of the full ranking — it yields a
    differently-scored ranking over an arbitrary, walk-order-determined
    subset. On a 1745-entry vault a 300ms budget reaches roughly the first 70
    candidates, and the top hits it reports are simply whichever entries the
    directory walk happened to visit first.
    """
    if status is not None:
        status.update({"complete": True, "walked": 0, "total": 0})
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
    entry_paths = _iter_entry_paths(
        vault, include_inbox=include_inbox, include_archive=include_archive
    )
    if status is not None:
        status["total"] = len(entry_paths)
    for walked, md_path in enumerate(entry_paths):
        if deadline is not None and time.monotonic() >= deadline:
            # Budget cut the walk short. Record it so the caller can discard
            # this ranking rather than present an arbitrary prefix as results.
            if status is not None:
                status.update({"complete": False, "walked": walked})
            break
        if status is not None:
            status["walked"] = walked + 1
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
    include_archive: bool = False,
) -> list[str]:
    """MemoryOS fallback's last level (V6-3): an unranked `--filter`-only
    match, used only when BM25 produced no candidate for a query that does
    carry filter criteria. No relevance ranking — just
    "these entries match the filter," the same degrade sqlite-only browsing
    would be without any query text at all.
    """
    if not criteria:
        return []
    from storage_device_local import DeviceLocalBackend  # noqa: E402
    backend = DeviceLocalBackend(root=vault)
    out: list[str] = []
    for md_path in _iter_entry_paths(vault, include_inbox=include_inbox, include_archive=include_archive):
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
# V6-11 --filter path (agentm-memory-index.md): a `--filter` expression parses
# to a set of frontmatter criteria the corpus walk applies as it goes. This
# compiled to a SQL WHERE joined with a vector MATCH while the vector index
# existed; the walk was always the fallback for that path and is now the only
# implementation, applying the same criteria.
# -----------------------------------------------------------------------------

class FilterError(ValueError):
    """A `--filter` expression is malformed or names an unknown key."""


# Supported filter keys -> the frontmatter field each one matches. `tag` is
# special-cased (membership in the `tags` list, not an equality match) and so
# is absent here; `parse_filter` allows it explicitly.
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


_PROJECTS_GROUP_PREFIX = "desk/projects/"


def _derive_project(group_value: str) -> str | None:
    """`desk/projects/<slug>/...` -> `<slug>`; None for any other group.

    The slug is taken as the first segment after the projects space rather than
    at a fixed index. The space gained a level at the stage-2 migration, and an
    index that happened to be right for `projects/<slug>` silently returned the
    literal "projects" for `desk/projects/<slug>` — a filter that matches
    nothing rather than one that errors.
    """
    if not group_value or not group_value.startswith(_PROJECTS_GROUP_PREFIX):
        return None
    rest = group_value[len(_PROJECTS_GROUP_PREFIX):]
    slug = rest.split("/", 1)[0]
    return slug or None


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


def query(
    *,
    vault: Path,
    query_text: str,
    k: int = DEFAULT_K,
    dedup_paths: set[str] | None = None,
    include_inbox: bool = False,
    include_archive: bool = False,
    deadline: float | None = None,
    filter_expr: str | None = None,
    status: dict | None = None,
    stderr=sys.stderr,
) -> list[dict]:
    """Run the in-process recall engine.

    Steps (recall-loop.md §recall-engine, minus the vector stream this
    engine fused with until that stack was removed):
      1. Tokenize query (lightweight; doesn't need to be in budget).
      2. BM25 corpus walk — score each entry; filter `status: superseded`;
         respect the `_inbox/` and `_archive/` flags.
      3. Rank-normalize via RRF, then apply the altitude boost and the
         lifecycle decay multiplier.
      4. Dedup against `dedup_paths` (typically the always-load set);
         sort by combined score; return top-k.

    Returns a list of dicts:
        [{"path": "<vault-relative-POSIX>", "slug": "<slug>",
          "sim": 0.0, "keyword": <float>, "combined": <float>}, ...]
    sorted by combined score descending. `sim` is retained at zero for
    result-shape compatibility with `trace` and the recall counter; nothing
    computes a similarity any more.

    `filter_expr` (V6-11 recall, agentm-memory-index.md): a
    `tag=security AND project=sherwood`-shaped expression, applied by the
    corpus walk as it goes. Raises `FilterError` on a malformed expression —
    fail loud at parse time, before the search runs.

    Stream admission (GH #92). The walk runs to completion or does not
    contribute: a corpus walk cut short by the deadline has its results
    DISCARDED rather than returned. The alternative — ranking whatever the
    walk reached — silently returns an arbitrary walk-order-determined
    ranking that is indistinguishable from a real one. `status`, when passed,
    records what it did so the caller can tell the operator "found nothing"
    apart from "could not search".
    """
    if status is not None:
        status.update({"lexical": {}, "searched": False})
    if dedup_paths is None:
        dedup_paths = set()
    if not query_text or not query_text.strip():
        return []
    query_tokens = _tokenize(query_text)
    criteria = parse_filter(filter_expr)

    # BM25 lexical search — independently scored. This is a full corpus walk:
    # it costs roughly 3050ms on a 1745-entry vault, NOT the "<50ms" an
    # earlier version of this comment claimed (that figure was measured on a
    # sub-100-entry vault and never revisited as the corpus grew).
    #
    # It is therefore normal for this half to be cut short by an interactive
    # budget. When that happens its results are DISCARDED, not fused — see
    # `_bm25_search`'s docstring for why a truncated walk is a differently-
    # scored ranking rather than a partial one (GH #92).
    bm25_status: dict = {"complete": True, "walked": 0, "total": 0}
    bm25_results = _bm25_search(
        vault, query_tokens, deadline=deadline, include_inbox=include_inbox,
        include_archive=include_archive, filter_criteria=criteria,
        status=bm25_status,
    )
    if not bm25_status["complete"]:
        print(
            f"[recall.query] lexical search discarded: the corpus walk reached "
            f"{bm25_status['walked']} of {bm25_status['total']} entries before the "
            f"time budget elapsed, and a partial walk produces an arbitrary "
            f"ranking rather than a partial one",
            file=stderr,
        )
        bm25_results = {}
    if status is not None:
        status["lexical"] = dict(bm25_status)
        status["searched"] = bm25_status["complete"]

    # V6-3 (PLAN-wave-e-v6-index task 5): RRF fusion replaces the old
    # weighted-sum merge (sim × 0.85 + keyword × 0.05). The cascade used to
    # choose between a vector stream and this one; since the vector stack was
    # removed there is a single ranked stream, and RRF over one source is just
    # a rank normalization of it — kept rather than inlined so the metadata-
    # filter-only fallback below still sits behind the same shape.
    if bm25_results:
        fused = _rrf_fuse(bm25_results)
    elif criteria:
        # Level 4: neither ranked signal produced anything -- an unranked
        # metadata-filter-only match (the SQLite-tier fallback).
        fused = {p: 0.0 for p in _metadata_filter_only(
            vault, criteria, deadline=deadline, include_inbox=include_inbox,
            include_archive=include_archive,
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
        # No embedding stands behind any hit now that the vector stack is
        # gone, so zero is the true value rather than a failed match — the
        # same reasoning the daemon path already applies to its own hits.
        # The key stays for result-shape compatibility with `trace` and the
        # recall counter; `_format_recall_result` declines to print it.
        sim = 0.0
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


def _format_recall_result(
    result: dict,
    body: str,
    fm: dict[str, str],
    *,
    excerpt_of_tokens: int | None = None,
) -> str:
    """Format a single recall result for stdout injection.

    Header includes slug + kind + the ranking evidence so the agent sees why
    this entry was recalled. Body follows verbatim (frontmatter stripped by
    caller).

    Both engines are lexical, so neither reports a similarity. Quoting one
    would be a small lie that reads as a real signal: no embedding stands
    behind either hit, and a `sim` of 0.00 looks like a semantic match that
    failed rather than one that was never attempted. This held for the daemon
    from the start and now holds for the in-process engine too, since the
    vector stack it used to fuse with was removed.
    """
    kind = fm.get("kind", "unknown")
    tags = fm.get("tags", "")
    if result.get("source") == "daemon":
        score = result.get("score", result.get("combined", 0.0))
        header = f"### {result['slug']} (kind: {kind}, score={score:.2f} daemon-lexical"
    else:
        header = (
            f"### {result['slug']} (kind: {kind}, "
            f"keywords={result['keyword']}"
        )
    if tags and tags not in {"[]", ""}:
        header += f", tags: {tags}"
    if "lifecycle_tier" in result:
        header += f", tier: {result['lifecycle_tier']}"
    if result.get("external"):
        header += ", outside the memory root"
    if excerpt_of_tokens is not None:
        header += f", excerpt of ~{excerpt_of_tokens:,} tokens"
    header += ")"
    return f"{header}\n\n{body.strip()}"


def _build_excerpt_block(
    result: dict,
    fm: dict[str, str],
    body: str,
    *,
    allowance_tokens: int,
    full_tokens: int,
    token_budget: int,
) -> str | None:
    """Build a partial block for a hit that will not fit whole.

    Composition, in the order the agent reads it: the marker, so "this is
    partial" arrives before the content; then the daemon's match snippet when
    there is one, because it is centred on the region that matched and a head
    excerpt of a long append-only log is centred on its oldest lines instead;
    then as much of the entry's opening as the allowance still affords, which
    at least says what the document is.

    Returns None when the allowance cannot cover the marker and header, so the
    caller omits the entry rather than emitting a block that is all frame.
    """
    marker = (
        f"{_EXCERPT_MARKER} — this entry is ~{full_tokens:,} tokens, past this "
        f"recall's {token_budget:,}-token budget. Read "
        f"`{result.get('path', '?')}` in the vault for anything not shown here."
    )
    parts = [marker]
    snippet = (result.get("snippet") or "").strip()
    if snippet:
        parts.append(f"**Matched here:** {snippet}")

    scaffold = _format_recall_result(
        result, "\n\n".join(parts), fm, excerpt_of_tokens=full_tokens
    )
    # Reserve the separator and ellipsis the body append costs, so the finished
    # block lands under the allowance rather than one rounding away from it.
    remaining = allowance_tokens - _estimate_tokens(scaffold) - 4
    if remaining <= 0:
        return None
    head = _truncate_to_tokens(body.strip(), remaining)
    if head:
        parts.append(head + " …")
    return _format_recall_result(
        result, "\n\n".join(parts), fm, excerpt_of_tokens=full_tokens
    )


def trace(
    *,
    slug: str,
    n: int = 3,
    history_path: "Path | None" = None,
    stdout=sys.stdout,
) -> int:
    """Print the N most recent recall events that surfaced `slug`, with the
    evidence recall-trace (Loose Ends Release 8) captures at recall time —
    "why did this memory surface," durable past the session that surfaced it.

    Reads the whole ledger and filters, most-recent-first. Deliberately not
    a reverse-chunked scan: the design's own sizing (1,422 rows / 167KB at
    13 days, ~5x row growth from `hits`) doesn't warrant one yet — re-audit
    at ~50MB or a >1s read, per the design doc's named trigger.

    Degrades honestly, one explicit printed line per case, never silence:
      - no ledger file at all
      - `slug` never appears in any event's `hit_slugs` ("never recalled")
      - `slug` appears in `hit_slugs` but that event predates trace capture
        (no matching `hits` entry) — reported per-event, not folded into
        the "never recalled" case, since it WAS recalled, just before this
        feature existed to record why.

    A single event can legitimately contribute more than one match — two
    entries sharing a slug (duplicate basenames are real in a live vault;
    see recall.py's own `_ALTITUDE_ANCHOR_SLUGS`) can both appear in one
    recall's `hits`. `n` counts rendered matches, not events, so that case
    shows both rather than silently picking one.

    Returns 0 always — a CLI reader, never treated as a hook whose failure
    should block anything.
    """
    import recall_counter  # noqa: E402 — lazy, mirrors this module's other cross-file imports

    path = history_path if history_path is not None else recall_counter.default_history_path()
    if not path.is_file():
        print(
            f"[memory-recall trace] no recall ledger found at {path} — nothing recorded yet",
            file=stdout,
        )
        return 0

    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        print(
            f"[memory-recall trace] ledger at {path} is unreadable ({type(e).__name__}) — nothing to show",
            file=stdout,
        )
        return 0

    matches: list[tuple[str, dict | None]] = []
    ever_recalled = False
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        # A ledger line can be syntactically valid JSON without being the
        # object shape every row is written as (e.g. a bare list or scalar).
        # Skip rather than let `.get` raise -- same degraded-graceful
        # discipline as the JSONDecodeError guard above, just for a shape
        # error instead of a syntax one.
        if not isinstance(row, dict):
            continue
        if slug not in row.get("hit_slugs", []):
            continue
        ever_recalled = True
        ts = row.get("ts", "?")
        matched_hits = [
            h for h in (row.get("hits") or [])
            if isinstance(h, dict) and h.get("slug") == slug
        ]
        if matched_hits:
            matches.extend((ts, h) for h in matched_hits)
        else:
            # In hit_slugs but no `hits` entry — this event predates trace
            # capture (recall_counter.record_recall's `hits` param is
            # optional; older rows simply never had it).
            matches.append((ts, None))

    if not ever_recalled:
        print(
            f"[memory-recall trace] '{slug}' was never recalled (no matching event in {path})",
            file=stdout,
        )
        return 0

    matches.reverse()  # ledger is append-only → reversed read order is most-recent-first
    shown = matches[: max(n, 0)]
    for ts, hit in shown:
        if hit is None:
            print(
                f"### {slug} @ {ts}\n\nrecalled before trace capture landed — no evidence recorded for this event.\n",
                file=stdout,
            )
            continue
        header = f"### {slug} @ {ts} (path: {hit.get('path', '?')}"
        if "sim" in hit:
            header += f", sim={hit['sim']:.2f}"
        if "keyword" in hit:
            header += f", keyword={hit['keyword']:.1f}"
        if "combined" in hit:
            header += f", combined={hit['combined']:.4f}"
        if "rank" in hit:
            header += f", rank={hit['rank']}"
        if "lifecycle_tier" in hit:
            header += f", tier: {hit['lifecycle_tier']}"
        header += ")"
        print(header, file=stdout)
    remaining = len(matches) - len(shown)
    if remaining > 0:
        print(
            f"\n... {remaining} more recall event(s) for '{slug}' not shown (pass -n to see more)",
            file=stdout,
        )
    return 0


def _daemon_query_terms(prompt: str, cap: int = DAEMON_QUERY_TERM_CAP) -> str:
    """Reduce a user prompt to the content words the daemon should search for.

    Lowercase, split on non-alphanumerics, drop tokens under `_MIN_TOKEN_LEN`,
    drop stopwords, drop repeats, keep source order, cap the count. See
    `DAEMON_QUERY_TERM_CAP` for the measurements that make this necessary rather
    than tidy — a raw prompt is both slower than the whole hook budget and worse
    ranked than its own keywords.

    Returns "" when the prompt carries no content words, which the caller treats
    as "do not ask the daemon" rather than "search for nothing".
    """
    terms: list[str] = []
    for token in _TOKEN_PATTERN.findall(prompt.lower()):
        if len(token) < _MIN_TOKEN_LEN or token in _DAEMON_STOPWORDS or token in terms:
            continue
        terms.append(token)
        if len(terms) >= cap:
            break
    return " ".join(terms)


def _daemon_root_for(vault: Path, rel_posix: str) -> Path | None:
    """The directory a daemon-returned path is relative to.

    The daemon indexes from its own configured root, which since the
    git-transport cutover is the Obsidian root — one level above the memory
    root recall is pointed at. So the daemon says `Agent/memory/x.md` where
    recall wants `personal/x.md`.

    Rather than re-reading the kernel config (recall.py resolves its vault from
    --vault-path/env and nothing else, and a second resolver is a second thing
    that can disagree), find the ancestor of `vault` that makes the path a real
    file. `vault` itself is tried first, so an install whose daemon root and
    memory root are the same directory — `memory_root` unset, the install that
    never moved — costs a single stat and gets prefix "" for free.
    """
    for root in (vault, *vault.parents):
        try:
            if (root / rel_posix).is_file():
                return root
        except OSError:
            continue
    return None


def _daemon_admissible(
    rel_posix: str, *, include_inbox: bool, include_archive: bool
) -> bool:
    """Apply `_iter_entry_paths`' directory rules to a daemon-returned path.

    The daemon indexes everything by design; these are recall's rules, not the
    index's, and nothing in the daemon knows them. Deliberately path-only so it
    costs no I/O — the `status: superseded` half of the same policy runs in
    `prompt_submit`, where the frontmatter has already been parsed to format the
    entry anyway.
    """
    for name in PurePosixPath(rel_posix).parts[:-1]:
        if name in _EXCLUDE_DIR_NAMES:
            return False
        if name == _INBOX_DIR_NAME and not include_inbox:
            return False
        if name == _INCLUDE_ARCHIVE_DIR_NAME and not include_archive:
            return False
        if name.startswith("."):
            return False
    return True


def _daemon_search(
    *,
    vault: Path,
    query_text: str,
    k: int = DEFAULT_K,
    dedup_paths: set[str] | None = None,
    include_inbox: bool = False,
    include_archive: bool = False,
    budget_ms: int = DAEMON_BUDGET_MS,
    scope: str | None = None,
    status: dict | None = None,
) -> list[dict] | None:
    """Ask the agentm daemon for the top-k entries relevant to `query_text`.

    Returns results in the same shape `query()` returns — `path` (vault-relative
    POSIX), `slug`, `sim`, `keyword`, `combined` — so every downstream consumer
    works unchanged, plus `score`/`source` recording that this ranking came from
    the daemon's BM25 rather than this module's sim+keyword merge, plus
    `snippet` when the daemon returned one (the match-centred extract
    `_build_excerpt_block` prefers over a head excerpt).

    Returns None when the daemon could not answer, which is the caller's signal
    to fall back to the in-process engine. **None and [] are different answers,
    and `prompt_submit` branches on which one it gets** (GH #92): None is "no
    search happened", [] is "the daemon searched and nothing admissible matched".
    Collapsing them is how a broken recall reports itself as an empty vault.
    """
    dedup_paths = dedup_paths or set()
    scope = (
        scope or os.environ.get(DAEMON_SCOPE_ENV) or DAEMON_SCOPE_DEFAULT
    ).strip().lower()

    def _skip(reason: str) -> None:
        if status is not None:
            status.update({"ran": False, "reason": reason, "engine": "daemon"})
        return None

    if not query_text or not query_text.strip():
        return _skip("empty query")
    search_terms = _daemon_query_terms(query_text)
    if not search_terms:
        return _skip("prompt carried no content words to search for")

    started = time.monotonic()
    try:
        proc = subprocess.run(
            [
                DAEMON_BIN, "search", "-json",
                "-k", str(max(1, k) * DAEMON_OVERFETCH),
                search_terms,
            ],
            capture_output=True,
            text=True,
            timeout=budget_ms / 1000.0,
        )
    except FileNotFoundError:
        return _skip(f"{DAEMON_BIN} not on PATH")
    except subprocess.TimeoutExpired:
        return _skip(f"{DAEMON_BIN} did not answer within {budget_ms}ms")
    except OSError as e:  # noqa: BLE001 — never block the prompt
        return _skip(f"{DAEMON_BIN} could not be run ({type(e).__name__})")
    elapsed_ms = (time.monotonic() - started) * 1000.0

    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return _skip(
            f"{DAEMON_BIN} exited {proc.returncode}"
            + (f": {detail[0][:120]}" if detail else "")
        )
    try:
        payload = json.loads(proc.stdout or "")
    except ValueError:
        return _skip(f"{DAEMON_BIN} returned unparseable JSON")
    raw = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return _skip(f"{DAEMON_BIN} response carried no results array")

    root: Path | None = None
    seen: set[str] = set()
    out: list[dict] = []
    for item in raw:
        if len(out) >= k:
            break
        if not isinstance(item, dict):
            continue
        rel_daemon = item.get("path")
        if not isinstance(rel_daemon, str) or not rel_daemon:
            continue
        if not _daemon_admissible(
            rel_daemon, include_inbox=include_inbox, include_archive=include_archive
        ):
            continue
        if root is None:
            root = _daemon_root_for(vault, rel_daemon)
            if root is None:
                continue
        abs_path = root / rel_daemon
        external = False
        try:
            rel = abs_path.relative_to(vault).as_posix()
        except ValueError:
            # Outside the memory root — one of the operator's own folders.
            if scope != "vault":
                continue
            rel = os.path.relpath(abs_path, vault).replace(os.sep, "/")
            external = True
        if rel in dedup_paths or rel in seen:
            continue
        seen.add(rel)
        try:
            score = float(item.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        entry = {
            "path": rel,
            "slug": PurePosixPath(rel_daemon).stem,
            # The daemon ranks lexically and runs no embedding, so there is no
            # similarity to report. Zero here is the true value, not a
            # placeholder — `_format_recall_result` prints `score` for
            # daemon-sourced hits rather than a sim of 0.00 that would read as
            # a failed semantic match.
            "sim": 0.0,
            "keyword": 0,
            "combined": score,
            "score": score,
            "source": "daemon",
        }
        if external:
            # Not a curated memory entry: readable and rankable, but it gets no
            # heat count and no decay-clock reset (see prompt_submit).
            entry["external"] = True
        snippet = item.get("snippet")
        if isinstance(snippet, str) and snippet.strip():
            # Carried for the excerpt path alone (`_build_excerpt_block`). The
            # daemon computes it with FTS5's `snippet()` around the matched
            # region, which is the one view of an oversized entry that is about
            # the query rather than about the top of the file. Whitespace is
            # collapsed because it arrives folded across the note's line breaks.
            entry["snippet"] = " ".join(snippet.split())
        out.append(entry)

    if status is not None:
        status.update({
            "ran": True,
            "reason": "",
            "engine": "daemon",
            "elapsed_ms": elapsed_ms,
            "scope": scope,
            "terms": search_terms,
            "returned": len(raw),
            "admitted": len(out),
            "matched": payload.get("matched"),
        })
    return out


def prompt_submit(
    *,
    vault: Path | None,
    prompt: str | None,
    budget_ms: int = PROMPT_SUBMIT_BUDGET_MS,
    k: int = DEFAULT_K,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    include_inbox: bool = False,
    include_archive: bool = False,
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
    recall_status: dict = {}
    daemon_status: dict = {}
    results: list[dict] = []
    if budget_ms > 0:
        # Ask the daemon first. On a daemon-backed machine it is the only engine
        # that fits an interactive budget at all — this module's own corpus walk
        # needs ~3170ms against 300ms, and a walk cut short is discarded rather
        # than ranked, so the in-process path returned nothing on every prompt.
        # When the daemon cannot answer, the engine below runs exactly as it did
        # before, with whatever budget the attempt did not spend.
        daemon_results: list[dict] | None = None
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms > 0:
            try:
                daemon_results = _daemon_search(
                    vault=vault,
                    query_text=prompt,
                    k=k,
                    dedup_paths=always_load_paths,
                    include_inbox=include_inbox,
                    include_archive=include_archive,
                    budget_ms=min(DAEMON_BUDGET_MS, remaining_ms),
                    status=daemon_status,
                )
            except Exception as e:  # noqa: BLE001 — never block the prompt
                daemon_status.update(
                    {"ran": False, "reason": f"{type(e).__name__}: {e}", "engine": "daemon"}
                )
                daemon_results = None
        else:
            daemon_status.update(
                {"ran": False, "reason": "budget exhausted before the daemon was asked",
                 "engine": "daemon"}
            )

        if daemon_results is not None:
            results = daemon_results
            recall_status.update({"searched": True, "engine": "daemon"})
        else:
            try:
                results = query(
                    vault=vault,
                    query_text=prompt,
                    k=k,
                    dedup_paths=always_load_paths,
                    include_inbox=include_inbox,
                    include_archive=include_archive,
                    deadline=deadline,
                    status=recall_status,
                    stderr=stderr,
                )
                recall_status.setdefault("engine", "in-process")
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
    raw_hits: list[dict] = []
    raw_sources: list[dict] = []  # excerpt inputs, index-aligned with raw_blocks
    # Reading a whole entry only to learn it cannot be injected is I/O spent
    # reaching a conclusion the file's size already gives away, and the live
    # vault's `_harness/progress.md` is a megabyte the daemon ranks highly for a
    # great many queries. So read a bounded head instead. `token_budget` tokens
    # is the largest block that could ever fit whole, and the slack sits above
    # it — which means any read that gets clipped here produces a block that
    # overflows the first pass of `_apply_token_budget` by construction, and
    # lands in the excerpt pass rather than being mistaken for a whole entry.
    read_cap_chars = (
        0 if token_budget <= 0 else token_budget * 4 + _ENTRY_READ_SLACK_CHARS
    )
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
    # recall-trace (Loose Ends Release 8): rank is this hit's 1-indexed
    # position in the full `results` list -- enumerated over `results`
    # itself, before the loop's own unreadable-file `continue` can skip an
    # entry. Computing rank from a loop-local counter instead would
    # silently renumber every hit after a skip.
    for rank, result in enumerate(results, start=1):
        md_path = vault / result["path"]
        try:
            content, clipped = _read_entry_head(md_path, read_cap_chars)
            # Only the clipped case needs the file's true size, and only to
            # report it. `st_size` is bytes where `_estimate_tokens` counts
            # characters; they agree on ASCII and the figure is printed as an
            # estimate either way.
            full_chars = md_path.stat().st_size if clipped else len(content)
        except (OSError, UnicodeDecodeError):
            continue
        fm, body = _parse_frontmatter(content)
        # `query()` filters superseded entries as it walks; the daemon indexes
        # everything and knows nothing about the field, so the check lives here
        # too. Harmless duplication for the in-process path, and the only such
        # check on the daemon path — a retired entry must not come back just
        # because a faster engine found it.
        if fm.get("status") == "superseded":
            continue
        raw_blocks.append(_format_recall_result(result, body, fm))
        raw_slugs.append(result["slug"])
        # The daemon's snippet rides on `result` for the excerpt path only. The
        # ledger records evidence about a hit, not the hit's own text, so it
        # stays out of what gets written there.
        raw_hits.append(
            {**{k: v for k, v in result.items() if k != "snippet"}, "rank": rank}
        )
        raw_sources.append(
            {
                "result": result,
                "fm": fm,
                "body": body,
                "full_tokens": max(1, full_chars // 4),
            }
        )
        # Record the on-demand hit for heat tracking (best-effort). Skipped for
        # `external` hits — notes outside the memory root are readable context,
        # not curated memory, and heat/decay bookkeeping on them would score a
        # tree the memory engine does not own.
        if _record_recall_hit is not None and not result.get("external"):
            _record_recall_hit(vault, result["slug"])
        # V6-1: genuine recall access resets the volatile-tier decay clock
        # (best-effort, no-op for decay-exempt entries). This is the ONLY
        # call site — a lint walk, index rebuild, or dreaming pass must
        # never reach this function.
        if _record_lifecycle_access is not None and not result.get("external"):
            _record_lifecycle_access(vault, result["slug"], fm, result["path"])

    # Apply token budget: results are highest-salience first → truncation
    # drops the least-relevant tail entries, never the top hits. Each
    # slug's evidence rides through packed as (slug, hit) pairs rather than
    # being recovered afterward by filtering `results` against the kept
    # slug list -- slugs are not unique in a real vault (duplicate
    # basenames, including the altitude-boosted `_index`/`_summary`
    # anchors), so a post-hoc membership filter can attach the wrong
    # entry's evidence to a kept slug. _apply_token_budget only ever
    # measures `blocks`, so packing this payload cannot change which
    # blocks survive (recall-trace, Loose Ends Release 8).
    #
    # A hit that overflows is excerpted rather than dropped (`excerpt=` below).
    # All-or-nothing per entry meant an entry too large to ever fit — the
    # vault's megabyte-scale `progress.md`, which the daemon ranks highly for
    # many queries — spent one of k slots and injected nothing, so a recall
    # could honestly report five hits and hand the agent an empty context.
    kept_pairs_in = list(zip(raw_slugs, raw_hits))

    def _excerpt_for(index: int, allowance_tokens: int) -> str | None:
        source = raw_sources[index]
        block = _build_excerpt_block(
            source["result"],
            source["fm"],
            source["body"],
            allowance_tokens=allowance_tokens,
            full_tokens=source["full_tokens"],
            token_budget=token_budget,
        )
        if block is not None:
            # Marked on the hit itself, so the count below reflects what
            # survived the budget rather than what was offered to it — and so
            # `memory-recall trace` can say a hit surfaced only in part.
            kept_pairs_in[index][1]["excerpt"] = True
        return block

    blocks, kept_pairs, token_budget_omitted = _apply_token_budget(
        raw_blocks, kept_pairs_in, token_budget, excerpt=_excerpt_for
    )
    loaded_slugs = [slug for slug, _hit in kept_pairs]
    kept_hits = [hit for _slug, hit in kept_pairs]
    token_budget_excerpted = sum(1 for _slug, hit in kept_pairs if hit.get("excerpt"))

    # L1 (ledger ruling 6): one per-recall counter event, query hashed (never
    # raw text) + the slugs actually surfaced after truncation. Best-effort,
    # this function's sole call site — same discipline as the heat/lifecycle
    # recordings above. `hits` (Loose Ends Release 8) carries the same
    # evidence alongside, for the `memory-recall trace` reader.
    try:
        from recall_counter import record_recall as _record_recall_event  # type: ignore
        _record_recall_event(prompt, loaded_slugs, hits=kept_hits)
    except ImportError:
        pass

    # Output assembly: only print stdout when we have hits or a truncation notice.
    if blocks or token_budget_omitted > 0:
        print(
            "# MemoryVault — recall hits for your prompt",
            file=stdout,
        )
        print("", file=stdout)
        ranked_by = (
            "daemon lexical rank"
            if recall_status.get("engine") == "daemon"
            else "semantic+keyword merge"
        )
        shown = f"top {len(blocks)} by {ranked_by}"
        if token_budget_excerpted > 0:
            shown += (
                f"; {token_budget_excerpted} too large to inject whole and shown "
                "as excerpts"
            )
        print(
            f"The following entries match your prompt ({shown}; deduped against "
            "always-load set).",
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
    # Name the engine. Which one answered changes both the latency and the
    # ranking, so an operator reading this line should never have to guess —
    # and when the fast path declined, the reason is the first thing worth
    # knowing. Kept out of `blocked` below on purpose: an absent daemon is not
    # "partial coverage" when the in-process engine went on to walk the whole
    # corpus, and overloading that phrase would blunt it where it matters.
    if recall_status.get("engine") == "daemon":
        # The searched terms are quoted because they are not the prompt: the
        # daemon is asked with content words only, and an operator debugging a
        # miss needs to see what was actually looked for.
        transparency += (
            f" (engine: daemon, {daemon_status.get('elapsed_ms', 0.0):.0f}ms, "
            f"scope={daemon_status.get('scope', DAEMON_SCOPE_DEFAULT)}, "
            f"terms: {daemon_status.get('terms', '')!r})"
        )
    elif daemon_status.get("reason"):
        transparency += (
            f" (engine: in-process — daemon declined: {daemon_status['reason']})"
        )
    # A zero-result recall has two very different causes, and reporting them
    # identically is how GH #92 survived under green CI for months: the hook
    # fired, exited 0, and honestly announced "Loaded 0" whether the vault
    # genuinely held nothing relevant or no search had actually run. Name the
    # blocked streams whenever nothing was loaded and something was blocked.
    blocked: list[str] = []
    lexical_status = recall_status.get("lexical") or {}
    if lexical_status.get("complete") is False:
        blocked.append(
            f"lexical: discarded, walked {lexical_status.get('walked', 0)} of "
            f"{lexical_status.get('total', 0)} entries before the budget elapsed"
        )
    # "Nothing was searched" is reserved for the case where no stream completed.
    # With the vector stack gone the in-process engine has exactly one stream,
    # so a blocked stream and an unsearched vault now coincide — the branch is
    # kept because the daemon path can still report a search this one did not.
    searched = recall_status.get("searched", True)
    if blocked and not searched:
        transparency += (
            " (NOTHING WAS SEARCHED — this is not an empty vault: "
            + "; ".join(blocked)
            + ")"
        )
    elif blocked:
        transparency += " (partial coverage — " + "; ".join(blocked) + ")"
    if overrun:
        transparency += (
            f" (WARNING: {budget_ms}ms time budget exceeded)"
        )
    # Excerpted and omitted are named separately, never summed. An excerpted
    # entry contributed content the agent can act on; an omitted one
    # contributed nothing. One count covering both would restate the problem
    # this change exists to fix as though it were still the answer.
    budget_notes = []
    if token_budget_excerpted > 0:
        budget_notes.append(f"{token_budget_excerpted} entries excerpted to fit")
    if token_budget_omitted > 0:
        budget_notes.append(f"{token_budget_omitted} entries omitted")
    if budget_notes:
        transparency += (
            f" (token budget: {', '.join(budget_notes)}; budget={token_budget:,})"
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
        default=None,
        help=(
            f"time budget in milliseconds (default: {SESSION_START_BUDGET_MS}). "
            "Also read from RECALL_BUDGET_MS env."
        ),
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
        default=None,
        help=(
            f"time budget in milliseconds (default: {PROMPT_SUBMIT_BUDGET_MS}). "
            "Also read from RECALL_BUDGET_MS env."
        ),
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
    q.add_argument("--budget-ms", type=int, default=QUERY_CLI_BUDGET_MS,
                   help=f"time budget in milliseconds (default: {QUERY_CLI_BUDGET_MS})")
    q.add_argument("--include-inbox", action="store_true",
                   help="include _inbox/ entries in the search (default: excluded)")
    q.add_argument("--include-archive", action="store_true",
                   help="include _archive/ entries in the search (default: excluded)")
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

    tr = sub.add_parser(
        "trace",
        help=(
            "explain why a memory surfaced -- print the N most recent recall "
            "events that surfaced <slug>, with their score evidence. "
            "Loose Ends Release 8 (recall-trace)."
        ),
    )
    tr.add_argument("slug", help="slug of the entry to trace (e.g. 'adr-shape')")
    tr.add_argument(
        "-n", type=int, default=3,
        help="number of most-recent recall events to show (default: 3)",
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    vault = _resolve_vault_path(args.vault_path)
    if args.cmd == "session-start":
        return session_start(
            vault=vault,
            budget_ms=_resolve_budget_ms(args.budget_ms, SESSION_START_BUDGET_MS),
            token_budget=_resolve_token_budget(args.token_budget),
        )
    if args.cmd == "prompt-submit":
        prompt = _read_prompt_from_stdin()
        return prompt_submit(
            vault=vault,
            prompt=prompt,
            budget_ms=_resolve_budget_ms(args.budget_ms, PROMPT_SUBMIT_BUDGET_MS),
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
                include_archive=args.include_archive,
                deadline=deadline,
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
        if args.apply:
            # Heat curation promotes and demotes entries across the whole
            # corpus, editing frontmatter in place. That is a corpus-wide write
            # and it asks the gate first.
            from corpus_gate import require_corpus_write_gate  # type: ignore

            require_corpus_write_gate(vault=str(vault))
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
    if args.cmd == "trace":
        # No vault gate — the ledger `trace` reads lives at a fixed
        # device-local path (recall_counter.default_history_path()), not
        # inside the vault.
        return trace(slug=args.slug, n=args.n)
    return 1  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
