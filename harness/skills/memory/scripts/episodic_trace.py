#!/usr/bin/env python3
"""episodic_trace.py — the agent's memory of its own sessions.

One note per session under `memory/episodic/`, `kind: session-trace`: the
session's handoff record, built from the transcript and the recall history
with no model call, capped, and written at session end by the reflection hook.

Five sections, all of them the session's own words: `## Asked` (the first
request), `## Outcome` (the closing recap the session already wrote — free,
and better than anything a cold model could reconstruct afterwards), `##
Captured` and `## Recalled` as wikilinks, and `## Candidates` — every line the
miner once filed as a note, kept as a line with the rule that fired, the
excerpt, and the count. The calendar's day index links the day's traces by
`day:`; the dreaming binary's promotion job reads their links as its
recurrence signal. A session that left nothing to record — nothing touched
and nothing said in passing — leaves no trace: "what happened" without "to
what" is a diary entry, and the diary is the calendar's.

    python3 episodic_trace.py <transcript.jsonl> --session <id> [--vault-path <memory root>] [--day YYYY-MM-DD]

Prints the vault-relative path it wrote, or `nothing touched`. Exit 0 either
way; a hook must never block session end on a trace.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

EPISODIC_DIR = "memory/episodic"
KIND = "session-trace"
# A trace links what the session touched, not everything it saw. Past this
# many links a trace is a search result, not a memory.
MAX_TOUCHED = 40
# The candidates a session said in passing. The miner files a note for none of
# them below HIGH, so this list is where the material lives — capped for the
# same reason the links are: past this it is a transcript, not a record.
MAX_CANDIDATES = 40
TITLE_CHARS = 120
# What was asked, at more than title length. The title is the handle; `##
# Asked` is the request, and cutting it to a filename's worth would throw away
# the half that makes the trace readable a month later.
ASKED_CHARS = 600
# The closing recap. It is the best summary the session produced and it costs
# nothing — it was already written — but a long final answer is an essay, and
# a handoff record wants its head.
OUTCOME_CHARS = 1200
_TOOL_SUFFIXES = ("memory_capture", "memory_search")
_KEBAB = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, fallback: str = "session") -> str:
    s = _KEBAB.sub("-", (text or "").lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)[:48].strip("-")
    return s or fallback


def trace_rel(when: date, session_id: str, title: str) -> str:
    """`memory/episodic/YYYY-MM-DD-<slug>-<session8>.md` — the session id's
    head keeps two sessions on one day from writing the same file."""
    head = re.sub(r"[^a-z0-9]", "", (session_id or "").lower())[:8] or "session"
    return f"{EPISODIC_DIR}/{when:%Y-%m-%d}-{slugify(title)}-{head}.md"


def _yaml_scalar(value: str) -> str:
    value = (value or "").replace("\n", " ").strip()
    return json.dumps(value, ensure_ascii=False)


@dataclass
class Trace:
    when: date
    session_id: str
    title: str
    touched: list = field(default_factory=list)  # vault-relative paths or slugs, in first-touch order
    captured: list = field(default_factory=list)
    recalled: list = field(default_factory=list)
    # What the session asked for and what it concluded. Both are the session's
    # own words, lifted from the transcript with no model call: the first
    # request, and the closing recap every session already ends with.
    asked: str = ""
    outcome: str = ""
    # The lines the miner used to file as notes. Each is `{"rule", "excerpt",
    # "count"}`: which rule fired, what it fired on, how often. MEDIUM and LOW
    # live here and nowhere else — a fragment does not become a memory by
    # being rewritten, but the record of what was said in passing is free, and
    # the next session's model can promote from it with context a nightly pass
    # over a lone note never has.
    candidates: list = field(default_factory=list)
    project: str = ""
    surface: str = ""

    @property
    def rel(self) -> str:
        return trace_rel(self.when, self.session_id, self.title)

    @property
    def slug(self) -> str:
        return Path(self.rel).stem

    def render(self) -> str:
        # `touched:`, not `entities:`. The old name promised named things and
        # held note basenames, which is what the field always was.
        touched = sorted({_stem(t) for t in self.touched})
        lines = [
            "---",
            f"title: {_yaml_scalar(self.title)}",
            f"kind: {KIND}",
            "status: active",
            "lifecycle: active",
            f"slug: {self.slug}",
            f"day: {self.when:%Y-%m-%d}",
            f"created: {self.when:%Y-%m-%d}",
            f"session: {_yaml_scalar(self.session_id)}",
            "source: conversation",
        ]
        if self.project:
            lines.append(f"project: {_yaml_scalar(self.project)}")
        if self.surface:
            lines.append(f"surface: {_yaml_scalar(self.surface)}")
        if touched:
            lines.append("touched: [" + ", ".join(_yaml_scalar(t) for t in touched) + "]")
        lines += ["---", ""]
        # The five sections of a handoff record: what was asked, what came of
        # it, what was written, what was read, and what was said in passing.
        # A section with nothing in it is omitted — a trace is a record, not a
        # form to be filled in.
        asked = (self.asked or self.title).strip()
        if asked:
            lines += ["## Asked", "", asked, ""]
        if self.outcome.strip():
            lines += ["## Outcome", "", self.outcome.strip(), ""]
        if self.captured:
            lines += ["## Captured", ""] + [f"- [[{_link(t)}]]" for t in self.captured] + [""]
        if self.recalled:
            lines += ["## Recalled", ""] + [f"- [[{_link(t)}]]" for t in self.recalled] + [""]
        if self.candidates:
            lines += ["## Candidates", ""] + [_candidate_line(c) for c in self.candidates] + [""]
        return "\n".join(lines)


# An excerpt is one line of someone's prose dropped into a bullet. Flattened
# and capped so it cannot break the list, and left otherwise exactly as it was
# said — the point of keeping it is that it is what was said.
EXCERPT_CHARS = 240


def _candidate_line(candidate) -> str:
    """One `## Candidates` bullet: the rule that fired, how often, and what it
    fired on. Tolerant of a dict or of anything carrying the three
    attributes, so the miner can hand over its own candidate objects."""
    def read(name, default=""):
        if isinstance(candidate, dict):
            return candidate.get(name, default)
        return getattr(candidate, name, default)

    rule = str(read("rule") or "unnamed rule").strip()
    excerpt = " ".join(str(read("excerpt") or "").split())[:EXCERPT_CHARS]
    try:
        count = int(read("count", 1) or 1)
    except (TypeError, ValueError):
        count = 1
    line = f"- {rule}"
    if count > 1:
        line += f" (×{count})"
    if excerpt:
        line += f" — “{excerpt}”"
    return line


def _stem(ref: str) -> str:
    return Path(ref).stem if "/" in ref or ref.endswith(".md") else ref


def _link(ref: str) -> str:
    ref = ref.strip()
    return ref[:-3] if ref.endswith(".md") else ref


# ── reading a session ─────────────────────────────────────────────────────────

def _blocks(msg: dict) -> list:
    content = (msg.get("message") or {}).get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [c for c in (content or []) if isinstance(c, dict)]


def _result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


def _paths_from_result(tool: str, text: str) -> list:
    """The vault-relative paths a memory tool's result names."""
    text = (text or "").strip()
    start = text.find("{")
    if start < 0:
        return []
    try:
        data = json.loads(text[start:])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    if tool.endswith("memory_capture"):
        p = data.get("path")
        return [p] if isinstance(p, str) and p.endswith(".md") else []
    out = []
    for r in data.get("results") or []:
        p = r.get("path") if isinstance(r, dict) else None
        if isinstance(p, str) and p.endswith(".md"):
            out.append(p)
    return out


def _first_request(messages: list) -> str:
    """The session's opening ask, uncut. The title takes its head; `## Asked`
    takes more of it."""
    for m in messages:
        if m.get("type") != "user":
            continue
        for b in _blocks(m):
            if b.get("type") == "text":
                line = next((l.strip() for l in (b.get("text") or "").splitlines() if l.strip()), "")
                if line and not line.startswith("<") and "tool_result" not in line:
                    return line
    return ""


def _closing_recap(messages: list) -> str:
    """The last thing the assistant said, which is the session's own summary
    of itself. Free — it was already written — and better than anything a
    cold model could reconstruct from the transcript afterwards.

    Prose only: a final turn that is nothing but tool calls has no recap, and
    a manufactured one would be worse than none."""
    for m in reversed(messages):
        if m.get("type") != "assistant":
            continue
        text = "\n".join(
            (b.get("text") or "") for b in _blocks(m) if b.get("type") == "text"
        ).strip()
        if text:
            return text[:OUTCOME_CHARS]
    return ""


def _timestamps(messages: list) -> tuple:
    stamps = []
    for m in messages:
        ts = m.get("timestamp")
        if isinstance(ts, str):
            try:
                stamps.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
            except ValueError:
                pass
    if not stamps:
        return None, None
    return min(stamps), max(stamps)


def recalled_between(history_path: Path, start, end) -> list:
    """Slugs the recall hook injected between two instants, from the recall
    history the daemon's gate already reads. Empty when either bound is
    unknown or the history is missing."""
    if start is None or end is None or not Path(history_path).is_file():
        return []
    out: list = []
    try:
        with open(history_path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = row.get("ts")
                if not isinstance(ts, str):
                    continue
                try:
                    at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if start <= at <= end:
                    for s in row.get("hit_slugs") or []:
                        if isinstance(s, str) and s and s not in out:
                            out.append(s)
    except OSError:
        return []
    return out


def default_history_path() -> Path:
    return Path.home() / ".cache" / "agentm" / "telemetry" / "recall-history.jsonl"


def mined_candidates(messages: list) -> list:
    """The lines the miner will not file: every candidate below HIGH, as
    `{"rule", "excerpt", "count"}`.

    Mined here rather than handed over from the routing pass, because the two
    run as separate processes from the Stop hook and `mine_transcript` is
    deterministic over the same transcript — reading it twice is cheaper than
    passing a candidate list between them, and it keeps the trace buildable
    from the transcript alone. A HIGH candidate is not listed: it becomes a
    card, and a record that says both would double-count it.
    """
    try:
        import reflect
        mined = reflect.mine_transcript_messages(messages)
    except Exception:
        # A miner failure must never cost the trace: the sections above it are
        # the handoff, and this one is the appendix.
        return []
    out = []
    for c in mined.get("memory_candidates") or []:
        if getattr(c, "confidence", "") == "HIGH":
            continue
        out.append({
            "rule": getattr(c, "rationale", "") or "",
            "excerpt": (getattr(c, "excerpts", None) or [getattr(c, "title", "")])[0],
            "count": getattr(c, "occurrences", 1),
        })
    return out


def from_transcript(transcript_path: Path, *, session_id: str, when: date = None,
                    history_path: Path = None, project: str = "", surface: str = "",
                    candidates: list = None) -> Trace:
    import reflect  # the sidecar's own transcript reader, so both read one shape

    messages = reflect.load_messages(Path(transcript_path))
    if candidates is None:
        candidates = mined_candidates(messages)
    pending: dict = {}
    captured: list = []
    recalled: list = []
    for m in messages:
        for b in _blocks(m):
            if b.get("type") == "tool_use" and any(str(b.get("name", "")).endswith(s) for s in _TOOL_SUFFIXES):
                pending[b.get("id")] = str(b.get("name", ""))
            elif b.get("type") == "tool_result" and b.get("tool_use_id") in pending:
                tool = pending.pop(b["tool_use_id"])
                for p in _paths_from_result(tool, _result_text(b)):
                    target = captured if tool.endswith("memory_capture") else recalled
                    if p not in captured and p not in recalled:
                        target.append(p)
    start, end = _timestamps(messages)
    for slug in recalled_between(history_path or default_history_path(), start, end):
        if slug not in recalled and slug not in captured:
            recalled.append(slug)
    if when is None:
        when = (start or datetime.now(timezone.utc)).date()
    asked = _first_request(messages)
    title = asked[:TITLE_CHARS] if asked else f"session {session_id[:8]}"
    touched = (captured + recalled)[:MAX_TOUCHED]
    return Trace(when=when, session_id=session_id, title=title, touched=touched,
                 captured=[t for t in captured if t in touched], recalled=[t for t in recalled if t in touched],
                 asked=asked[:ASKED_CHARS], outcome=_closing_recap(messages),
                 candidates=list(candidates or [])[:MAX_CANDIDATES],
                 project=project, surface=surface)


def write_trace(vault_path, trace: Trace) -> str | None:
    """Write the trace under the memory root; None when the session left
    nothing to record. Never overwrites: a second write of the same session in
    the same day is the same file, rewritten whole.

    Candidates count as something to record. The miner files no note below
    HIGH any more, so a session whose only durable output was things said in
    passing has that material here or nowhere."""
    if not trace.touched and not trace.candidates:
        return None
    root = Path(vault_path)
    path = root / trace.rel
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(trace.render(), encoding="utf-8")
    os.replace(tmp, path)
    return trace.rel


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="write the session's episodic trace")
    ap.add_argument("transcript")
    ap.add_argument("--session", required=True)
    ap.add_argument("--vault-path", default=None, help="the memory root (default: $MEMORY_ROOT)")
    ap.add_argument("--day", default=None, help="YYYY-MM-DD (default: the transcript's first message)")
    ap.add_argument("--history", default=None, help="recall history JSONL (default: ~/.cache/agentm/telemetry/recall-history.jsonl)")
    ap.add_argument("--project", default="")
    ap.add_argument("--surface", default=os.environ.get("AGENTM_SURFACE", "").strip(),
                    help="where the session ran, e.g. claude-code (default: $AGENTM_SURFACE)")
    a = ap.parse_args(argv)
    vault = a.vault_path or (os.environ.get("MEMORY_ROOT") or os.environ.get("MEMORY_VAULT_PATH", "")).strip()
    if not vault:
        print("episodic_trace: no memory root (--vault-path or MEMORY_ROOT)", file=sys.stderr)
        return 0
    try:
        trace = from_transcript(Path(a.transcript), session_id=a.session,
                                when=date.fromisoformat(a.day) if a.day else None,
                                history_path=Path(a.history) if a.history else None,
                                project=a.project, surface=a.surface)
        rel = write_trace(vault, trace)
    except Exception as e:  # a hook never blocks session end on a trace
        print(f"episodic_trace: skipped ({e})", file=sys.stderr)
        return 0
    print(rel or "nothing touched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
