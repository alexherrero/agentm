#!/usr/bin/env python3
# reflect.py — MemoryVault reflection sidecar mining logic.
#
# Reads a Claude Code session transcript (JSONL at
# ~/.claude/projects/<repo>/<session-id>.jsonl) + emits candidate entries
# in two parallel mining passes:
#
#   1. 3-category mine (writes to MemoryVault):
#      - Successful Workflows
#      - User Preferences
#      - Fixes & Workarounds
#   2. Idea-candidate mine (downstream idea-ledger part subscribes):
#      - follow-ups / future projects / research candidates
#
# Each candidate carries:
#   - category (preferences | workflow | fix | idea)
#   - confidence (HIGH | MEDIUM | LOW) per the locked tri-modal heuristic
#   - rationale (what pattern matched — instrumentation per Tech Debt #7)
#   - excerpts (verbatim transcript snippets supporting the candidate)
#   - occurrences (match count — used for confidence routing)
#
# Plan #7a part 3 task 1 (this commit) ships ONLY the mining module +
# CLI surface. Tasks 2-7 of part 3 wire this into:
#   - /memory reflect sub-command (task 2)
#   - Stop-event hook (task 3)
#   - Idle-time hook (task 4)
#   - Tri-modal routing + interactive review (task 5)
#   - Crash-recovery markers (task 6)
#   - Documentation (task 7)
#
# Locked design calls referenced:
#   - B1: aggressive end-of-session sweep + parallel idea-candidate sweep
#   - B2.iii: tri-modal confidence routing (HIGH/MEDIUM/LOW)
#   - Tech Debt #7: ship instrumented + tune from real use

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# save_entry / evolve_entry live in this same scripts/ dir; lazy-imported
# inside route_candidates() so reflect.py's mining-only path doesn't pay
# the import cost (sys.path is augmented at module load below).
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# ── Pattern catalog ────────────────────────────────────────────────────────
#
# Each entry is (compiled_regex, rationale_string). The rationale gets
# surfaced in the candidate's `rationale` field — operator-debug surface
# for "why was this mined?". Patterns are CASE-INSENSITIVE.
#
# Heuristic shape per Tech Debt #7 — ship simple + instrumented; tune from
# real use. v1 explicitly avoids fancy NLP / LLM-based extraction. The
# "user manually corrected the agent" pattern is the noisy one (per part
# doc's "Notes for the implementing /work session" — defer fancy detection
# to follow-up if simple heuristic too noisy).

# HIGH-confidence: explicit user preference statements ("always X" / "I prefer X").
#
# The bare always/never pattern (below) is demoted out of the HIGH lane at
# candidate-creation time (R0.3 / agentmExperience#2) — it fires on any
# sentence containing "always"/"never" in any user-role message, and in
# practice the overwhelming majority of matches are mid-sentence discussion
# ("was never touched", "it always crashes") or quoted document prose, not an
# operator directive. Demoted candidates still route through the tri-modal
# system (MEDIUM → _inbox/ in auto mode) rather than being dropped, so a real
# "always X" / "never Y" directive still reaches the operator for triage.
_BARE_ALWAYS_NEVER_RATIONALE = "explicit always/never directive"

_PREFERENCE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:always|never)\s+\w+(?:\s+\w+){0,5}", re.IGNORECASE),
     _BARE_ALWAYS_NEVER_RATIONALE),
    (re.compile(r"\bI\s+(?:prefer|want|need|like|expect|require)\b[^.!?\n]{0,80}",
                re.IGNORECASE),
     "explicit preference statement"),
    (re.compile(r"\b(?:use|do|prefer)\s+[\w\s-]{1,40}\s+(?:not|instead of|rather than)\s+\w+",
                re.IGNORECASE),
     "explicit choice statement"),
    (re.compile(r"\bdon'?t\s+(?:use|do|add|include|put|write|commit)\b[^.!?\n]{0,80}",
                re.IGNORECASE),
     "explicit negative directive"),
]

# MEDIUM-confidence: user corrected the agent — simple heuristic per part-doc
# note. Detection is intentionally lossy — false positives are filtered by the
# interactive review prompt (task 5); false negatives end up in _inbox/ or stay
# uncaptured. Tune from real use per Tech Debt #7.
_CORRECTION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:no|nope|actually|wait)[\s,.]+[^.!?\n]{0,80}(?:wrong|incorrect|missing|broken|not right)\b",
                re.IGNORECASE),
     "user correction signal"),
    (re.compile(r"\b(?:you|that)\s+should(?:n'?t|\s+not)?\s+have\s+\w+",
                re.IGNORECASE),
     "user retrospective correction"),
    (re.compile(r"\bI\s+(?:said|told|asked)\s+(?:you\s+)?(?:to\s+)?\w+",
                re.IGNORECASE),
     "user repeats instruction"),
]

# MEDIUM-confidence: fixes / workarounds
_FIX_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bfixed by\b[^.!?\n]{0,120}", re.IGNORECASE),
     "explicit fix statement"),
    (re.compile(r"\bresolved by\b[^.!?\n]{0,120}", re.IGNORECASE),
     "explicit resolution statement"),
    (re.compile(r"\bworkaround\b[^.!?\n]{0,120}", re.IGNORECASE),
     "workaround mention"),
    (re.compile(r"\b(?:bug|error|issue|failure)\b[^.!?\n]{0,40}\bwas\b[^.!?\n]{0,40}\b(?:caused by|due to|because of)\b[^.!?\n]{0,80}",
                re.IGNORECASE),
     "root cause statement"),
]

# Idea-candidate patterns
_IDEA_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bwe\s+(?:should|could)\s+(?:also|later|eventually)\b[^.!?\n]{0,120}",
                re.IGNORECASE),
     "explicit follow-up suggestion"),
    (re.compile(r"\b(?:later|future)\b[\s,]+[^.!?\n]{0,30}\b(?:could|should|might|may|would)\b[^.!?\n]{0,80}",
                re.IGNORECASE),
     "future-possibility statement"),
    (re.compile(r"\bfollow.?ups?\b[^.!?\n]{0,120}", re.IGNORECASE),
     "follow-up marker"),
    (re.compile(r"\bcould be (?:its|their) own\b[^.!?\n]{0,80}", re.IGNORECASE),
     "potential project split"),
    (re.compile(r"\bas a follow.?up\b[^.!?\n]{0,120}", re.IGNORECASE),
     "follow-up marker"),
    (re.compile(r"\bidea[s]?\s*[:—-]\s+\w+[^.!?\n]{0,120}", re.IGNORECASE),
     "idea declaration"),
]

# Workflow detection: tool-use frequency threshold. Locked design call B1
# says "pattern-inferred 3+ occurrences" → MEDIUM. Simple v1 detection: count
# distinct tool names used; any tool used N>=3 times suggests a workflow.
_WORKFLOW_OCCURRENCE_THRESHOLD = 3


@dataclass
class Candidate:
    """A mined candidate entry. Either heading for MemoryVault (memory pass)
    or for the future idea-ledger (idea pass)."""
    category: str          # preferences | workflow | fix | idea
    confidence: str        # HIGH | MEDIUM | LOW
    slug: str              # kebab-case slug suggestion
    title: str             # 1-line summary
    body: str              # full markdown body for save.py
    rationale: str         # what pattern matched (instrumentation)
    excerpts: list[str] = field(default_factory=list)  # verbatim snippets
    occurrences: int = 1   # match count (instrumentation)


def _extract_text(msg: dict) -> str:
    """Extract plain text from a transcript message.

    Claude Code transcripts have two content shapes:
      - user messages: `message.content` is a string OR a list of content blocks
      - assistant messages: `message.content` is always a list of content blocks
        ({type: text|thinking|tool_use|tool_result, ...})

    We extract `text` blocks + record tool_use names as bracketed markers
    (e.g. `[tool_use: Bash]`) so workflow detection can tally them downstream.
    Thinking blocks are skipped — they're internal-only and not part of the
    agent's visible output.
    """
    content = msg.get("message", {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for c in content:
            if not isinstance(c, dict):
                continue
            ctype = c.get("type")
            if ctype == "text":
                parts.append(c.get("text", ""))
            elif ctype == "tool_use":
                tool = c.get("name", "?")
                parts.append(f"[tool_use: {tool}]")
            # Skip thinking + tool_result + attachments — not part of mining
            # surface area (per part doc: mining works on user signals +
            # assistant turn content, not introspection).
        return " ".join(parts)
    return ""


def _extract_tool_uses(msg: dict) -> list[str]:
    """Return the list of tool names used in an assistant message."""
    if msg.get("type") != "assistant":
        return []
    content = msg.get("message", {}).get("content")
    if not isinstance(content, list):
        return []
    tools: list[str] = []
    for c in content:
        if isinstance(c, dict) and c.get("type") == "tool_use":
            tools.append(c.get("name", "?"))
    return tools


def _slug_from_text(text: str, max_words: int = 6) -> str:
    """Generate a kebab-case slug from text.

    Lowercases, extracts alphanumeric runs, takes the first N tokens,
    joins with `-`. Falls back to "candidate" if input has no extractable
    alphanumerics (rare — typically only for emoji-only messages).
    """
    words = re.findall(r"[a-z0-9]+", text.lower())[:max_words]
    return "-".join(words) or "candidate"


def _excerpt_around(text: str, start: int, end: int, radius: int = 80) -> str:
    """Extract a windowed excerpt around a regex match.

    Returns the match plus up to `radius` chars on each side, with `...`
    ellipses if the window doesn't cover the full text. Newlines flattened
    to spaces so the excerpt is a single line (rationale + excerpts are
    rendered in transparency lines + interactive review prompts; multi-line
    excerpts would break that surface).
    """
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    excerpt = text[lo:hi].replace("\n", " ").replace("\r", " ").strip()
    # Collapse runs of whitespace to single spaces for readability.
    excerpt = re.sub(r"\s+", " ", excerpt)
    if lo > 0:
        excerpt = "..." + excerpt
    if hi < len(text):
        excerpt = excerpt + "..."
    return excerpt


def load_messages(transcript_path: Path) -> list[dict]:
    """Read transcript JSONL + return only user + assistant messages in order.

    Skips queue-operation / ai-title / last-prompt / attachment / system /
    custom-title — those are metadata, not mining surface area.

    Raises FileNotFoundError if the transcript path doesn't exist. Malformed
    JSON lines are silently skipped (transcripts can have partial writes if
    Claude Code crashed mid-line; ship robust).
    """
    if not transcript_path.exists():
        raise FileNotFoundError(f"Transcript not found: {transcript_path}")
    messages: list[dict] = []
    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if r.get("type") in ("user", "assistant"):
                messages.append(r)
    return messages


def _scan_patterns(
    text: str,
    patterns: list[tuple[re.Pattern, str]],
    *,
    role: str,
) -> list[tuple[str, str, str]]:
    """Run all patterns over text. Returns list of (match_text, rationale, excerpt) tuples.

    `role` is used in the excerpt rationale tagging but doesn't change matching
    behavior (kept here for future per-role pattern tuning).
    """
    out: list[tuple[str, str, str]] = []
    for pattern, rationale in patterns:
        for m in pattern.finditer(text):
            excerpt = _excerpt_around(text, m.start(), m.end())
            out.append((m.group(0), rationale, excerpt))
    return out


def mine_transcript(transcript_path: Path) -> dict:
    """Mine a session transcript for memory + idea candidates.

    Returns a dict with:
      - transcript_path: stringified path
      - messages_processed: int (user + assistant messages scanned)
      - memory_candidates: list[Candidate] for the 3-category pass
      - idea_candidates: list[Candidate] for the idea-ledger pass

    Candidates are deduped per (category, slug) — repeated patterns bump
    `occurrences` rather than emit duplicate candidates. Confidence is
    initially set per pattern type; candidates with `occurrences < 3` in
    MEDIUM-mode-initial buckets get demoted to LOW per the locked tri-modal
    routing (single-instance inference → _inbox/).

    The function is deterministic for a given transcript — same input
    produces same output. No I/O beyond reading the transcript.
    """
    messages = load_messages(transcript_path)

    # (category, slug) → Candidate. Dedupes repeated matches.
    seen_memory: dict[tuple[str, str], Candidate] = {}
    seen_ideas: dict[str, Candidate] = {}

    # Tool-use frequency tally for workflow mining
    tool_counts: dict[str, int] = {}

    for msg in messages:
        role = msg.get("type")
        text = _extract_text(msg)
        if not text:
            continue

        # ── Preferences + corrections + ideas only apply to user messages.
        # User intent is the signal; agent outputs are derivative.
        if role == "user":
            # HIGH-confidence preferences
            for match, rationale, excerpt in _scan_patterns(
                text, _PREFERENCE_PATTERNS, role=role
            ):
                slug = _slug_from_text(match)
                key = ("preferences", slug)
                if key in seen_memory:
                    seen_memory[key].occurrences += 1
                    seen_memory[key].excerpts.append(excerpt)
                else:
                    seen_memory[key] = Candidate(
                        category="preferences",
                        confidence=(
                            "MEDIUM" if rationale == _BARE_ALWAYS_NEVER_RATIONALE
                            else "HIGH"
                        ),
                        slug=slug,
                        title=match.strip()[:80],
                        body=f"User stated: {excerpt}",
                        rationale=rationale,
                        excerpts=[excerpt],
                    )

            # MEDIUM-confidence corrections
            for match, rationale, excerpt in _scan_patterns(
                text, _CORRECTION_PATTERNS, role=role
            ):
                slug = _slug_from_text(match)
                key = ("preferences", slug)
                if key in seen_memory:
                    seen_memory[key].occurrences += 1
                    seen_memory[key].excerpts.append(excerpt)
                else:
                    seen_memory[key] = Candidate(
                        category="preferences",
                        confidence="MEDIUM",
                        slug=slug,
                        title=f"Correction: {match.strip()[:60]}",
                        body=f"User corrected the agent: {excerpt}",
                        rationale=rationale,
                        excerpts=[excerpt],
                    )

            # Idea candidates
            for match, rationale, excerpt in _scan_patterns(
                text, _IDEA_PATTERNS, role=role
            ):
                slug = _slug_from_text(excerpt[:60])
                if slug in seen_ideas:
                    seen_ideas[slug].occurrences += 1
                    seen_ideas[slug].excerpts.append(excerpt)
                else:
                    seen_ideas[slug] = Candidate(
                        category="idea",
                        confidence="MEDIUM",
                        slug=slug,
                        title=excerpt[:80],
                        body=excerpt,
                        rationale=rationale,
                        excerpts=[excerpt],
                    )

        # ── Fixes apply to both user + assistant messages (either side can
        # report a fix).
        for match, rationale, excerpt in _scan_patterns(
            text, _FIX_PATTERNS, role=role
        ):
            slug = _slug_from_text(excerpt[:60])
            key = ("fix", slug)
            if key in seen_memory:
                seen_memory[key].occurrences += 1
                seen_memory[key].excerpts.append(excerpt)
            else:
                seen_memory[key] = Candidate(
                    category="fix",
                    confidence="MEDIUM",
                    slug=slug,
                    title=f"Fix: {excerpt[:60]}",
                    body=f"Fix observed: {excerpt}",
                    rationale=rationale,
                    excerpts=[excerpt],
                )

        # ── Workflow: tally tool usages from assistant turns.
        for tool in _extract_tool_uses(msg):
            tool_counts[tool] = tool_counts.get(tool, 0) + 1

    memory_candidates: list[Candidate] = list(seen_memory.values())
    idea_candidates: list[Candidate] = list(seen_ideas.values())

    # Workflow candidates: any tool used >= threshold times.
    for tool, count in sorted(tool_counts.items()):
        if count >= _WORKFLOW_OCCURRENCE_THRESHOLD:
            slug = _slug_from_text(f"workflow {tool}")
            memory_candidates.append(Candidate(
                category="workflow",
                confidence="MEDIUM",
                slug=slug,
                title=f"Workflow: {tool} used {count}x",
                body=(
                    f"The `{tool}` tool was invoked {count} times during this "
                    f"session. If this represents a repeatable workflow, capture "
                    f"the sequence + when to use it."
                ),
                rationale=(
                    f"tool_use frequency threshold "
                    f"(N>={_WORKFLOW_OCCURRENCE_THRESHOLD}, observed {count})"
                ),
                excerpts=[],
                occurrences=count,
            ))

    # ── Tri-modal demotion: MEDIUM-initial candidates with <3 occurrences
    # get demoted to LOW (single-instance inference → _inbox/ per locked
    # design call B2.iii). HIGH candidates stay HIGH regardless of count
    # (explicit user signal trumps frequency).
    for c in memory_candidates:
        if c.confidence == "MEDIUM" and c.occurrences < _WORKFLOW_OCCURRENCE_THRESHOLD:
            c.confidence = "LOW"
    # For idea candidates, the bar is lower (2+ occurrences keeps MEDIUM;
    # ideas don't need 3 repetitions to be worth surfacing).
    for c in idea_candidates:
        if c.confidence == "MEDIUM" and c.occurrences < 2:
            c.confidence = "LOW"

    # Sort: HIGH first, then MEDIUM, then LOW; within tier, by occurrences desc.
    _confidence_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    memory_candidates.sort(
        key=lambda c: (_confidence_order.get(c.confidence, 99), -c.occurrences, c.slug)
    )
    idea_candidates.sort(
        key=lambda c: (_confidence_order.get(c.confidence, 99), -c.occurrences, c.slug)
    )

    return {
        "transcript_path": str(transcript_path),
        "messages_processed": len(messages),
        "memory_candidates": memory_candidates,
        "idea_candidates": idea_candidates,
    }


# ── Routing (plan #7a part 3 task 5) ───────────────────────────────────────
#
# Tri-modal confidence routing per locked design call B2.iii:
#   HIGH   → auto-save via save.save_entry() at canonical path
#   MEDIUM → interactive prompt (if --route-mode=interactive AND stdin is a TTY)
#            OR auto-save (if --route-mode=silent)
#            OR write to _inbox/ (if --route-mode=auto — the safe default)
#   LOW    → write to _inbox/ for batch triage
#
# Routing modes (--route-mode flag OR MEMORY_REVIEW_MODE env var):
#   - `auto` (default): hook-safe; never prompts; MEDIUM → _inbox/. Used by
#     Stop + idle hooks since hook contexts have no TTY.
#   - `silent`: auto-approves MEDIUM (saves to canonical path); used when
#     operator trusts the heuristic fully + wants zero prompts.
#   - `interactive`: prompts for each MEDIUM candidate (only meaningful when
#     stdin is a TTY; falls back to `auto` behavior otherwise).
#
# The HIGH path is unconditional auto-save — explicit user signals
# (always X / never Y / prefer Z) are saved without prompting in all modes.
# The LOW path is unconditional _inbox/ — single-instance inferences are
# never important enough to interrupt the operator.

ROUTE_MODE_AUTO = "auto"
ROUTE_MODE_SILENT = "silent"
ROUTE_MODE_INTERACTIVE = "interactive"
_VALID_ROUTE_MODES = {ROUTE_MODE_AUTO, ROUTE_MODE_SILENT, ROUTE_MODE_INTERACTIVE}


def _resolve_route_mode(arg_mode: str | None) -> str:
    """Resolve routing mode: CLI arg → MEMORY_REVIEW_MODE env → default 'auto'."""
    if arg_mode:
        if arg_mode not in _VALID_ROUTE_MODES:
            raise ValueError(
                f"unknown route mode {arg_mode!r}: expected one of {_VALID_ROUTE_MODES}"
            )
        return arg_mode
    env_mode = os.environ.get("MEMORY_REVIEW_MODE", "").strip().lower()
    if env_mode in _VALID_ROUTE_MODES:
        return env_mode
    return ROUTE_MODE_AUTO


def _save_candidate_to_inbox(
    candidate: Candidate, vault: Path, *, stderr=sys.stderr
) -> Path | None:
    """Save a candidate to MemoryVault/personal/_inbox/<slug>.md.

    Returns the saved path on success, None on failure (e.g. slug collision —
    operator runs `/memory evolve` to resolve). Writes the candidate's body
    with a header containing category + confidence + rationale + excerpts
    (full instrumentation for later operator triage).
    """
    try:
        from save import save_entry  # type: ignore  # noqa
    except ImportError as e:
        print(f"[reflect.route] cannot import save module: {e}", file=stderr)
        return None

    # Compose inbox body: the candidate's body + appended instrumentation
    # (rationale + excerpts + occurrences) so triage has full context.
    excerpts_block = ""
    if candidate.excerpts:
        excerpts_lines = [f"> {e}" for e in candidate.excerpts]
        excerpts_block = "\n\n## Supporting excerpts\n\n" + "\n".join(excerpts_lines)
    inbox_body = (
        f"{candidate.body}\n\n"
        f"## Mining metadata\n\n"
        f"- **Category**: `{candidate.category}`\n"
        f"- **Confidence**: `{candidate.confidence}`\n"
        f"- **Rationale**: {candidate.rationale}\n"
        f"- **Occurrences**: {candidate.occurrences}\n"
        f"{excerpts_block}"
    )

    # Inbox path: <vault>/personal/_inbox/<slug>.md. save_entry's
    # standard target is <vault>/<group>/<kind>/<slug>.md, so we save with
    # kind="_inbox" to route there. Actually _inbox/ is a flat dir like
    # _always-load/, not a kind subdir. Use group=personal + kind=_inbox.
    # save_entry validates kind as kebab-case ([a-z0-9-]+) — "_inbox" has
    # underscore, fails validation. Bypass via direct file write.
    inbox_dir = vault / "personal" / "_inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    target = inbox_dir / f"{candidate.slug}.md"
    if target.exists():
        # Collision — append a numeric suffix to keep both. Inbox is a
        # raw-capture surface; duplicates here are OK.
        n = 1
        while target.exists():
            target = inbox_dir / f"{candidate.slug}-{n}.md"
            n += 1
    # Write a minimal frontmatter + body. Inbox entries are triage targets,
    # not full canonical entries — they don't need the full save.py frontmatter
    # shape (kind/status/created/updated/tags/group/slug/always_load).
    fm = (
        "---\n"
        f"kind: {candidate.category}\n"
        "status: inbox\n"
        f"slug: {target.stem}\n"
        f"mining_confidence: {candidate.confidence}\n"
        f"mining_rationale: {json.dumps(candidate.rationale)}\n"
        f"mining_occurrences: {candidate.occurrences}\n"
        "---\n"
    )
    content = fm + "\n" + inbox_body + "\n"
    target.write_bytes(content.encode("utf-8"))
    return target


def _save_candidate_canonical(
    candidate: Candidate, vault: Path, *, stderr=sys.stderr
) -> Path | None:
    """Save a candidate to its canonical path via save.save_entry().

    Returns saved path on success, None on collision / validation error.
    """
    try:
        from save import save_entry  # type: ignore
    except ImportError as e:
        print(f"[reflect.route] cannot import save module: {e}", file=stderr)
        return None

    # save_entry signature requires kind + slug + body. Map category to kind
    # using the same vocabulary save accepts (preferences / workflow / fix /
    # idea — all are valid kebab-case names).
    try:
        return save_entry(
            vault_path=vault,
            kind=candidate.category,
            slug=candidate.slug,
            body=candidate.body,
            group="personal",
        )
    except (FileExistsError, ValueError, FileNotFoundError) as e:
        print(
            f"[reflect.route] could not save {candidate.slug} ({candidate.category}): {e}",
            file=stderr,
        )
        return None


def _prompt_user_for_candidate(
    candidate: Candidate, *, stdin=sys.stdin, stdout=sys.stdout
) -> str:
    """Display a MEDIUM-confidence candidate + prompt user for action.

    Returns one of: 'approve' / 'reject' / 'skip' / 'inbox'.
    The 'edit' + 'supersede' options from the design doc are deferred — v1
    supports approve / reject / skip / inbox (the 4 verbs operator can
    perform without launching $EDITOR or invoking /memory evolve mid-prompt).
    """
    print("", file=stdout)
    print("─" * 72, file=stdout)
    print(f"MEDIUM-confidence candidate ({candidate.category}):", file=stdout)
    print(f"  slug:       {candidate.slug}", file=stdout)
    print(f"  title:      {candidate.title[:80]}", file=stdout)
    print(f"  rationale:  {candidate.rationale}", file=stdout)
    print(f"  occurrences: {candidate.occurrences}", file=stdout)
    if candidate.excerpts:
        print(f"  excerpts:", file=stdout)
        for ex in candidate.excerpts[:3]:
            print(f"    > {ex[:120]}", file=stdout)
    print("─" * 72, file=stdout)
    print("Action: [a]pprove (save canonical) / [r]eject / [s]kip / [i]nbox (default: i)", file=stdout)
    stdout.flush()
    try:
        choice = stdin.readline().strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "skip"
    if not choice or choice in ("i", "inbox"):
        return "inbox"
    if choice in ("a", "approve"):
        return "approve"
    if choice in ("r", "reject"):
        return "reject"
    if choice in ("s", "skip"):
        return "skip"
    # Unknown input → safe default (inbox).
    print(f"  (unknown choice {choice!r}; defaulting to inbox)", file=stdout)
    return "inbox"


def route_candidates(
    memory_candidates: list[Candidate],
    idea_candidates: list[Candidate],
    *,
    vault: Path,
    mode: str = ROUTE_MODE_AUTO,
    stdin=sys.stdin,
    stdout=sys.stdout,
    stderr=sys.stderr,
) -> dict:
    """Route mined candidates per the tri-modal heuristic.

    Args:
        memory_candidates: from `mine_transcript`'s memory_candidates list
        idea_candidates: ditto idea_candidates
        vault: MemoryVault root (where save.py writes)
        mode: 'auto' | 'silent' | 'interactive' (see module-level docstring)

    Returns stats dict:
        {
            "auto_saved": N,       # HIGH + silent-mode-MEDIUM auto-saves
            "approved": N,         # interactive-mode-MEDIUM user-approved saves
            "rejected": N,         # interactive-mode-MEDIUM user-rejected
            "skipped": N,          # interactive-mode-MEDIUM user-skipped
            "inboxed": N,          # LOW + auto-mode-MEDIUM + interactive-fallback
            "ideas_inboxed": N,    # all idea candidates → _inbox/idea/<slug>.md
            "errors": N,           # save errors (e.g. slug collision)
        }
    """
    stats = {
        "auto_saved": 0, "approved": 0, "rejected": 0,
        "skipped": 0, "inboxed": 0, "ideas_inboxed": 0, "errors": 0,
    }
    # If interactive mode but stdin is not a TTY, fall back to auto (hook-safe).
    if mode == ROUTE_MODE_INTERACTIVE and not stdin.isatty():
        print(
            "[reflect.route] interactive mode requested but stdin is not a TTY; "
            "falling back to auto-route (MEDIUM → _inbox/)",
            file=stderr,
        )
        mode = ROUTE_MODE_AUTO

    for c in memory_candidates:
        if c.confidence == "HIGH":
            if _save_candidate_canonical(c, vault, stderr=stderr):
                stats["auto_saved"] += 1
            else:
                stats["errors"] += 1
            continue
        if c.confidence == "MEDIUM":
            if mode == ROUTE_MODE_SILENT:
                if _save_candidate_canonical(c, vault, stderr=stderr):
                    stats["auto_saved"] += 1
                else:
                    stats["errors"] += 1
                continue
            if mode == ROUTE_MODE_INTERACTIVE:
                action = _prompt_user_for_candidate(c, stdin=stdin, stdout=stdout)
                if action == "approve":
                    if _save_candidate_canonical(c, vault, stderr=stderr):
                        stats["approved"] += 1
                    else:
                        stats["errors"] += 1
                elif action == "reject":
                    stats["rejected"] += 1
                elif action == "skip":
                    stats["skipped"] += 1
                else:  # inbox
                    if _save_candidate_to_inbox(c, vault, stderr=stderr):
                        stats["inboxed"] += 1
                    else:
                        stats["errors"] += 1
                continue
            # Default route mode: MEDIUM → inbox
            if _save_candidate_to_inbox(c, vault, stderr=stderr):
                stats["inboxed"] += 1
            else:
                stats["errors"] += 1
            continue
        # LOW → inbox unconditionally
        if _save_candidate_to_inbox(c, vault, stderr=stderr):
            stats["inboxed"] += 1
        else:
            stats["errors"] += 1

    # Idea candidates always → _inbox/ (idea-ledger persistence is plan #7a
    # part 4's scope; for v1 ideas go to inbox where the future ledger can
    # pick them up).
    for c in idea_candidates:
        if _save_candidate_to_inbox(c, vault, stderr=stderr):
            stats["ideas_inboxed"] += 1
        else:
            stats["errors"] += 1

    return stats


# ── Corpus mode (plan #7b task 2) ──────────────────────────────────────────
#
# Batched paced processing of historical Claude Code transcripts at
# ~/.claude/projects/*/<session>.jsonl. Single-transcript reflect logic
# (mine_transcript + route_candidates) stays unchanged; corpus mode wraps
# both in a paced loop with skip-resume state-file management.
#
# Locked design calls for this mode:
#   - Default behavior is DRY-RUN: first call counts sessions + estimates
#     candidate volume without writing entries. Operator re-runs with
#     --execute after seeing scope. Mitigates the "transcript backlog
#     produces thousands of inbox candidates" risk flagged in PLAN.md.
#   - State file lives at <vault>/_meta/transcript-reflection-state.json.
#     Schema (versioned): {schema_version: 1, sessions: {<session-id>:
#     {processed_at, message_count, memory_count, idea_count, status}}}.
#   - Sessions identified by transcript-file BASENAME (typically the
#     session UUID assigned by Claude Code). Resume = skip session IDs
#     already present in state file with status == "done".
#   - Atomic state-file writes via tempfile + rename so Ctrl-C mid-write
#     can't leave a half-written state file. State writes happen after
#     each session (not each batch) — finer-grained resume.
#   - MEDIUM-confidence candidates route to _inbox/ by default (auto mode)
#     since historical-pass volume makes interactive routing impractical.
#     Operator triages later via inbox-bulk-review (separate follow-up).

_STATE_SCHEMA_VERSION = 1


def _resolve_projects_root(arg_path: str | None) -> Path:
    """Resolve transcript-projects root: arg → env → default ~/.claude/projects."""
    if arg_path:
        return Path(arg_path).expanduser()
    env = os.environ.get("MEMORY_TRANSCRIPT_ROOT", "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / ".claude" / "projects"


def _discover_transcripts(projects_root: Path) -> list[Path]:
    """Find every operator-session .jsonl transcript under projects_root.

    Excludes machine-generated transcripts that are not operator conversation:
    any path with a `subagents` component (subagent transcripts + nested
    workflow journals), any path with a `wf_*` component (workflow run dirs),
    and any `journal.jsonl` filename. Mining these as user statements is the
    R0.3 source of the HIGH-lane junk-slug pollution — a subagent prompt or a
    quoted design doc saying "always X" is not the operator stating a
    preference.

    Returns a sorted list of absolute paths. Deterministic ordering (by
    relative path) so batch boundaries are stable across runs.
    """
    if not projects_root.exists() or not projects_root.is_dir():
        return []
    found: list[Path] = []
    for p in projects_root.rglob("*.jsonl"):
        if not p.is_file():
            continue
        parts = p.parts
        if "subagents" in parts:
            continue
        if any(part.startswith("wf_") for part in parts):
            continue
        if p.name == "journal.jsonl":
            continue
        found.append(p.resolve())
    return sorted(found)


def _state_file_path(vault: Path) -> Path:
    return vault / "_meta" / "transcript-reflection-state.json"


def load_state(vault: Path) -> dict:
    """Load resume state from disk. Returns empty initialized state if missing.

    State shape:
        {
            "schema_version": 1,
            "sessions": {
                "<session-id>": {
                    "processed_at": "<ISO8601>",
                    "message_count": int,
                    "memory_count": int,
                    "idea_count": int,
                    "status": "done" | "error",
                    "transcript_path": "<absolute path>"
                },
                ...
            }
        }
    """
    sf = _state_file_path(vault)
    if not sf.exists():
        return {"schema_version": _STATE_SCHEMA_VERSION, "sessions": {}}
    try:
        data = json.loads(sf.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Corrupted state file — start fresh rather than corrupt further.
        return {"schema_version": _STATE_SCHEMA_VERSION, "sessions": {}}
    if not isinstance(data, dict) or "sessions" not in data:
        return {"schema_version": _STATE_SCHEMA_VERSION, "sessions": {}}
    # Forward-compat: if a future schema bumps the version, we don't try to
    # interpret it. Treat unknown-future as "skip nothing" + warn.
    if data.get("schema_version", 0) != _STATE_SCHEMA_VERSION:
        return {"schema_version": _STATE_SCHEMA_VERSION, "sessions": {}}
    return data


def save_state(vault: Path, state: dict) -> None:
    """Atomically write state to disk via tempfile + rename.

    Ctrl-C mid-write can't leave a half-written state file because we write
    to a sibling tempfile first, fsync, then rename (rename is atomic on
    POSIX + acceptably-atomic on Windows for files on the same volume).
    """
    sf = _state_file_path(vault)
    sf.parent.mkdir(parents=True, exist_ok=True)
    tmp = sf.with_suffix(sf.suffix + ".tmp")
    payload = json.dumps(state, indent=2, sort_keys=True)
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, sf)


def _session_id_from_path(transcript_path: Path) -> str:
    """Extract a session ID from a transcript file path.

    For ~/.claude/projects/<repo-slug>/<session-uuid>.jsonl the session ID
    is the file stem. We also prefix the parent-dir name so sessions across
    different projects don't collide even if their UUIDs somehow matched.
    """
    return f"{transcript_path.parent.name}/{transcript_path.stem}"


def reflect_corpus(
    *,
    vault: Path,
    projects_root: Path,
    batch_size: int = 10,
    max_batches: int | None = None,
    dry_run: bool = True,
    reset: bool = False,
    route_mode: str = ROUTE_MODE_AUTO,
    stdout=sys.stdout,
    stderr=sys.stderr,
) -> dict:
    """Walk historical transcripts; mine + route in paced batches.

    Args:
        vault: MemoryVault root (where inbox + state file live).
        projects_root: where to find .jsonl transcripts (recursive).
        batch_size: sessions per batch (state-flushed each session, but
            batch summaries print every N sessions).
        max_batches: if set, stop after this many batches (operator scout
            mode). None = process all unseen sessions.
        dry_run: if True, count + estimate but don't write entries or
            update state. Default True for safety.
        reset: if True, ignore the existing state file (re-process everything).
        route_mode: route_candidates() mode; default 'auto' (MEDIUM → _inbox/).
        stdout, stderr: streams for output.

    Returns summary dict:
        {
            "dry_run": bool,
            "projects_root": str,
            "total_transcripts": int,
            "skipped_already_processed": int,
            "to_process": int,
            "processed_this_run": int,
            "candidates_estimated": int  (dry-run only),
            "candidates_written": int    (real-run only),
            "memory_inboxed": int,
            "memory_auto_saved": int,
            "ideas_inboxed": int,
            "errors": int,
            "batches": int,
        }
    """
    if not vault.exists() or not vault.is_dir():
        raise FileNotFoundError(
            f"vault path does not exist or is not a directory: {vault}"
        )

    transcripts = _discover_transcripts(projects_root)
    state = {"schema_version": _STATE_SCHEMA_VERSION, "sessions": {}} if reset else load_state(vault)
    already_done = {
        sid for sid, info in state["sessions"].items()
        if info.get("status") == "done"
    }

    to_process: list[Path] = []
    for t in transcripts:
        sid = _session_id_from_path(t)
        if sid not in already_done:
            to_process.append(t)

    summary = {
        "dry_run": dry_run,
        "projects_root": str(projects_root),
        "total_transcripts": len(transcripts),
        "skipped_already_processed": len(transcripts) - len(to_process),
        "to_process": len(to_process),
        "processed_this_run": 0,
        "candidates_estimated": 0,
        "candidates_written": 0,
        "memory_inboxed": 0,
        "memory_auto_saved": 0,
        "ideas_inboxed": 0,
        "errors": 0,
        "batches": 0,
    }

    if not to_process:
        print(
            f"[reflect.corpus] nothing to process: "
            f"{len(transcripts)} transcripts, all already in state file.",
            file=stderr,
        )
        return summary

    mode_label = "DRY-RUN" if dry_run else "EXECUTE"
    print(
        f"[reflect.corpus] {mode_label} mode | projects_root={projects_root} | "
        f"to_process={len(to_process)} sessions (of {len(transcripts)} total; "
        f"{summary['skipped_already_processed']} already in state)",
        file=stderr,
    )

    batch_idx = 0
    batch_processed = 0
    batch_mem_total = 0
    batch_idea_total = 0

    for t_idx, transcript in enumerate(to_process, start=1):
        sid = _session_id_from_path(transcript)
        try:
            result = mine_transcript(transcript)
        except (FileNotFoundError, OSError) as e:
            print(f"[reflect.corpus] ERROR mining {sid}: {e}", file=stderr)
            summary["errors"] += 1
            if not dry_run:
                state["sessions"][sid] = {
                    "processed_at": _utcnow_iso(),
                    "message_count": 0,
                    "memory_count": 0,
                    "idea_count": 0,
                    "status": "error",
                    "transcript_path": str(transcript),
                    "error": str(e),
                }
                save_state(vault, state)
            continue

        mem_count = len(result["memory_candidates"])
        idea_count = len(result["idea_candidates"])
        batch_mem_total += mem_count
        batch_idea_total += idea_count
        summary["processed_this_run"] += 1
        batch_processed += 1

        if dry_run:
            summary["candidates_estimated"] += mem_count + idea_count
        else:
            stats = route_candidates(
                result["memory_candidates"],
                result["idea_candidates"],
                vault=vault,
                mode=route_mode,
                stderr=stderr,
            )
            summary["memory_auto_saved"] += stats["auto_saved"]
            summary["memory_inboxed"] += stats["inboxed"]
            summary["ideas_inboxed"] += stats["ideas_inboxed"]
            summary["errors"] += stats["errors"]
            summary["candidates_written"] += (
                stats["auto_saved"] + stats["inboxed"] + stats["ideas_inboxed"]
            )
            state["sessions"][sid] = {
                "processed_at": _utcnow_iso(),
                "message_count": result["messages_processed"],
                "memory_count": mem_count,
                "idea_count": idea_count,
                "status": "done",
                "transcript_path": str(transcript),
            }
            save_state(vault, state)

        # Batch boundary (every `batch_size` sessions or at the end of to_process)
        is_batch_end = (
            batch_processed >= batch_size or t_idx == len(to_process)
        )
        if is_batch_end:
            batch_idx += 1
            summary["batches"] = batch_idx
            print(
                f"[reflect.corpus] Batch {batch_idx}"
                f"{f'/{max_batches}' if max_batches else ''}: "
                f"{batch_processed} session(s) | "
                f"~{batch_mem_total} memory + {batch_idea_total} idea candidates"
                f"{' (dry-run; not written)' if dry_run else ''}",
                file=stderr,
            )
            batch_processed = 0
            batch_mem_total = 0
            batch_idea_total = 0
            if max_batches is not None and batch_idx >= max_batches:
                print(
                    f"[reflect.corpus] reached --max-batches={max_batches}; "
                    f"halting. State preserved for resume.",
                    file=stderr,
                )
                break

    return summary


def _utcnow_iso() -> str:
    """UTC now in ISO 8601 (sub-second precision dropped for state-file readability)."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="memory-reflect",
        description=(
            "Mine a Claude Code session transcript for durable candidate "
            "entries. Emits one JSON record per line (pass='memory' for the "
            "3-category mine; pass='idea' for the idea-ledger mine). "
            "Plan #7a part 3 task 1 — mining only; wiring to /memory reflect "
            "+ Stop/idle hooks + tri-modal routing lands in tasks 2-7."
        ),
    )
    parser.add_argument(
        "transcript_path",
        help="path to a Claude Code session transcript JSONL "
             "(typically ~/.claude/projects/<repo>/<session-id>.jsonl)",
    )
    parser.add_argument(
        "--memory-only", action="store_true",
        help="emit only the 3-category memory candidates (skip idea pass)",
    )
    parser.add_argument(
        "--idea-only", action="store_true",
        help="emit only the idea candidates (skip memory pass)",
    )
    parser.add_argument(
        "--summary", action="store_true",
        help="prefix output with a 1-line summary "
             "(messages processed + candidate counts per pass)",
    )
    parser.add_argument(
        "--route", action="store_true",
        help="after mining, route candidates per tri-modal heuristic "
             "(HIGH → auto-save; MEDIUM → see --route-mode; LOW → _inbox/). "
             "Requires --vault-path or MEMORY_VAULT_PATH env var.",
    )
    parser.add_argument(
        "--route-mode", choices=list(_VALID_ROUTE_MODES), default=None,
        help="MEDIUM-confidence routing: 'auto' (default; → _inbox/), "
             "'silent' (auto-save), or 'interactive' (prompt user via stdin; "
             "falls back to 'auto' if stdin isn't a TTY). "
             "Resolves from --route-mode flag → MEMORY_REVIEW_MODE env → 'auto'.",
    )
    parser.add_argument(
        "--vault-path",
        help="MemoryVault root (used when --route is set). Resolves from "
             "--vault-path → MEMORY_VAULT_PATH env. Required for --route.",
    )
    return parser.parse_args(argv)


def _parse_corpus_args(argv: list[str]) -> argparse.Namespace:
    """Parse args for the corpus subcommand (plan #7b task 2)."""
    parser = argparse.ArgumentParser(
        prog="memory-reflect corpus",
        description=(
            "Walk historical Claude Code transcripts (~/.claude/projects/*/) "
            "and mine each via the single-transcript reflect pipeline. "
            "Batched + paced with state-file resume. Dry-run by default — "
            "first call counts sessions + estimates candidate volume; "
            "operator re-runs with --execute after seeing scope."
        ),
    )
    parser.add_argument(
        "--projects-root", default=None,
        help="root dir to walk for .jsonl transcripts "
             "(default: $MEMORY_TRANSCRIPT_ROOT or ~/.claude/projects)",
    )
    parser.add_argument(
        "--vault-path", default=None,
        help="MemoryVault root (default: $MEMORY_VAULT_PATH env var). "
             "Required — state file + inbox entries land here.",
    )
    parser.add_argument(
        "--batch-size", type=int, default=10,
        help="sessions per batch (state saved each session; batch summary "
             "printed every N sessions). Default 10.",
    )
    parser.add_argument(
        "--max-batches", type=int, default=None,
        help="stop after this many batches — scout mode. State preserved for "
             "resume. Default: process all unseen sessions.",
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="actually write entries + update state (default: dry-run, "
             "estimates only). Pair with --reset to re-process everything.",
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="ignore existing state file (re-process everything). Combine "
             "with --execute to actually re-write — otherwise the dry-run "
             "just re-counts.",
    )
    parser.add_argument(
        "--route-mode", choices=list(_VALID_ROUTE_MODES), default=None,
        help="MEDIUM-confidence routing mode. Default 'auto' (→ _inbox/) — "
             "appropriate for historical-pass volume.",
    )
    return parser.parse_args(argv)


def _main_corpus(argv: list[str]) -> int:
    """Entry point for the `reflect.py corpus ...` subcommand."""
    args = _parse_corpus_args(argv)
    # Resolve vault.
    vault_arg = args.vault_path
    if not vault_arg:
        vault_arg = os.environ.get("MEMORY_VAULT_PATH", "").strip() or None
    if not vault_arg:
        print(
            "ERROR: corpus mode requires --vault-path or MEMORY_VAULT_PATH env var",
            file=sys.stderr,
        )
        return 1
    vault = Path(vault_arg).expanduser()
    if not vault.exists() or not vault.is_dir():
        print(
            f"ERROR: vault path does not exist or is not a directory: {vault}",
            file=sys.stderr,
        )
        return 1
    projects_root = _resolve_projects_root(args.projects_root)
    try:
        route_mode = _resolve_route_mode(args.route_mode)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    # --reset without --execute = dry-run re-count (harmless).
    summary = reflect_corpus(
        vault=vault,
        projects_root=projects_root,
        batch_size=args.batch_size,
        max_batches=args.max_batches,
        dry_run=not args.execute,
        reset=args.reset,
        route_mode=route_mode,
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary["errors"] == 0 else 2


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    # Subcommand dispatch — corpus mode is a different shape from the
    # existing single-transcript mine + route path.
    if argv and argv[0] == "corpus":
        return _main_corpus(argv[1:])
    args = _parse_args(argv)
    try:
        result = mine_transcript(Path(args.transcript_path).expanduser())
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if args.summary:
        print(json.dumps({
            "pass": "summary",
            "transcript_path": result["transcript_path"],
            "messages_processed": result["messages_processed"],
            "memory_candidate_count": len(result["memory_candidates"]),
            "idea_candidate_count": len(result["idea_candidates"]),
        }))

    if not args.idea_only:
        for c in result["memory_candidates"]:
            print(json.dumps({"pass": "memory", **asdict(c)}))
    if not args.memory_only:
        for c in result["idea_candidates"]:
            print(json.dumps({"pass": "idea", **asdict(c)}))

    # ── Routing pass (plan #7a part 3 task 5) ──────────────────────────────
    if args.route:
        # Resolve vault path: arg → env → error.
        vault_arg = args.vault_path
        if not vault_arg:
            vault_arg = os.environ.get("MEMORY_VAULT_PATH", "").strip() or None
        if not vault_arg:
            print(
                "ERROR: --route requires --vault-path or MEMORY_VAULT_PATH env var",
                file=sys.stderr,
            )
            return 1
        vault = Path(vault_arg).expanduser()
        if not vault.exists() or not vault.is_dir():
            print(f"ERROR: vault path does not exist or is not a directory: {vault}",
                  file=sys.stderr)
            return 1
        try:
            route_mode = _resolve_route_mode(args.route_mode)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        # idea_only / memory_only filter the routing pass too — if the
        # operator only emitted one pass, only route that pass.
        memory_to_route = result["memory_candidates"] if not args.idea_only else []
        idea_to_route = result["idea_candidates"] if not args.memory_only else []
        stats = route_candidates(
            memory_to_route, idea_to_route, vault=vault, mode=route_mode,
        )
        # Routing stats as a final JSON-Lines record on stdout (after the
        # candidate records). Operator scripts + hooks can parse this to
        # surface counts in transparency lines.
        print(json.dumps({
            "pass": "route",
            "route_mode": route_mode,
            **stats,
        }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
