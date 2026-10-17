#!/usr/bin/env python3
"""episodic_trace.py — the agent's memory of its own sessions.

One note per session under `memory/episodic/`, `kind: session-trace`: the
session's handoff record, built from the transcript and the recall history
with no model call, capped, and written at session end by the reflection hook.

Six sections, all of them the session's own words: `## Asked` (the first
request), `## Outcome` (the closing recap the session already wrote — free,
and better than anything a cold model could reconstruct afterwards), `##
Captured` and `## Recalled` as wikilinks, `## Candidates` — every line the
miner once filed as a note, kept as a line with the rule that fired, the
excerpt, and the count — and `## Ideas`, each idea-shaped remark word for word
with the conversation around it and a pointer back, for the night's idea judge
(task 192). The calendar's day index links the day's traces by
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
# The idea remarks a session made, for the night's judge (task 192). Each
# carries up to about 1,200 characters of conversation, so the cap is lower.
MAX_IDEAS = 20
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


def _head(session_id: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (session_id or "").lower())[:8] or "session"


def fallback_title(session_id: str) -> str:
    """The title of a session whose first request could not be read."""
    return f"session {(session_id or '')[:8]}"


def trace_rel(when: date, session_id: str, title: str) -> str:
    """`memory/episodic/YYYY-MM-DD-<slug>-<session8>.md` — the session id's
    head keeps two sessions on one day from writing the same file. A title that
    already ends in the head (the fallback, `session <id8>`) does not repeat it."""
    head = _head(session_id)
    slug = slugify(title)
    if slug != head and slug.endswith("-" + head):
        slug = slug[: -len(head) - 1]
    return f"{EPISODIC_DIR}/{when:%Y-%m-%d}-{slug}-{head}.md"


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
    # The idea remarks, for the night's judge (task 192): each a dict with the
    # remark as it was said, the rule that fired, the conversation around it,
    # and the pointer back (`transcript`, `message`). `first_request` marks a
    # remark the session's first request carried. Nothing is filed at session
    # end; the judge reads these from the vault, never from the transcript.
    ideas: list = field(default_factory=list)
    project: str = ""
    task: str = ""
    surface: str = ""
    # Whether the operator took a turn (`has_operator_turn`). A session nobody
    # was at writes no trace, whatever it touched.
    attended: bool = True

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
        # A record keeps the card's order for the fields it shares with a card
        # and adds its own after the read block (agentm-vault § The card): the
        # shared read fields, then the trace's own, then the machine block.
        lines = [
            "---",
            f"title: {_yaml_scalar(self.title)}",
            f"kind: {KIND}",
            "status: active",
            "lifecycle: active",
            "source: conversation",
            f"created: {self.when:%Y-%m-%d}",
        ]
        if self.project:
            lines.append(f"project: {_yaml_scalar(self.project)}")
        if self.task:
            lines.append(f"task: {_yaml_scalar(self.task)}")
        lines += [
            f"day: {self.when:%Y-%m-%d}",
            f"session: {_yaml_scalar(self.session_id)}",
        ]
        if touched:
            lines.append("touched: [" + ", ".join(_yaml_scalar(t) for t in touched) + "]")
        lines.append(f"slug: {self.slug}")
        if self.surface:
            lines.append(f"surface: {_yaml_scalar(self.surface)}")
        lines += ["---", ""]
        # The six sections of a handoff record: what was asked, what came of
        # it, what was written, what was read, what was said in passing, and
        # the idea remarks the night will judge.
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
        if self.ideas:
            lines += ["## Ideas", ""] + render_ideas(self.ideas) + [""]
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


# ── ## Ideas ─────────────────────────────────────────────────────────────────
#
# One JSON object per remark, inside a fenced block. Fenced, because the context
# is somebody's conversation: an issue number or a link in it is a sample, not a
# mention, and the link and entity extractors skip fenced text. One object a
# line, because a line that opens with `{` can never close the fence, whatever
# the words inside it hold. The dreaming binary's idea judge reads the same
# shape (`daemon/internal/ideajudge`).
IDEAS_FENCE = "```jsonl"
_IDEA_KEYS = ("remark", "rule", "first_request", "transcript", "message", "context", "filed_key")


def idea_entry(candidate, transcript: str = "") -> dict:
    """A miner's idea candidate as the trace keeps it."""
    def read(name, default=""):
        if isinstance(candidate, dict):
            return candidate.get(name, default)
        return getattr(candidate, name, default)

    remark = " ".join(str(read("remark") or read("title") or "").split())
    # The key the retired session-end lane would have recorded in the filed
    # ledger for this remark (`reflect.filed_key`): a remark whose card the
    # night or the operator removed is dropped by the judge without a call.
    filed_key = str(read("filed_key") or "")
    if not filed_key and read("slug"):
        import reflect
        filed_key = reflect.filed_key(str(read("slug")), str(read("body") or read("title") or ""))
    return {"remark": remark, "rule": str(read("rationale") or read("rule") or ""),
            "first_request": bool(read("from_first_request", False) or read("first_request", False)),
            "transcript": transcript, "message": str(read("message_id") or read("message") or ""),
            "context": str(read("context") or ""), "filed_key": filed_key}


def _idea_key(entry: dict) -> tuple:
    return (entry.get("message") or "", " ".join(str(entry.get("remark") or "").split()))


def render_ideas(ideas: list) -> list:
    out = [IDEAS_FENCE]
    for e in ideas:
        out.append(json.dumps({k: e.get(k, "") for k in _IDEA_KEYS}, ensure_ascii=False))
    return out + ["```"]


def parse_ideas(section: str) -> list:
    """The entries of a `## Ideas` section; a line that is not one is skipped."""
    out = []
    for line in (section or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and str(entry.get("remark") or "").strip():
            out.append(entry)
    return out


def _transcript_pointer(path) -> str:
    """The transcript, home-relative when it is under the home directory."""
    p = Path(path).expanduser()
    try:
        return "~/" + p.resolve().relative_to(Path.home().resolve()).as_posix()
    except (ValueError, OSError):
        return p.as_posix()


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


# Who was at a session is reflect's reading (task 192 moved it there, so the
# miner and the trace writer share one): `reflect.is_operator_turn` and
# `reflect.has_operator_turn`, the host's bracketed lines in `reflect.HOST_LINE`.

def _is_operator_turn(msg: dict) -> bool:
    import reflect  # the sidecar's own reading of who said what
    return reflect.is_operator_turn(msg)


def has_operator_turn(messages: list) -> bool:
    """Whether anyone was at the session. One with nobody at it writes no
    trace; the job's own run records say what it did."""
    import reflect
    return reflect.has_operator_turn(messages)


def _first_request(messages: list) -> str:
    """The session's opening ask, uncut. The title takes its head; `## Asked`
    takes more of it. A line the host wrote is skipped."""
    import reflect
    for m in messages:
        if m.get("type") != "user":
            continue
        for b in _blocks(m):
            if b.get("type") == "text":
                line = next((l.strip() for l in (b.get("text") or "").splitlines()
                             if l.strip() and not reflect.HOST_LINE.match(l.strip())), "")
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
    """The ledger `recall_counter` writes, override included, so a test that
    redirects the ledger reads back the one it wrote."""
    import recall_counter  # same skill dir
    return recall_counter.default_history_path()


def _mine(messages: list, standing: "dict | None" = None) -> dict:
    """The miner's reading of a session, or an empty one when the miner fails:
    a miner failure must never cost the trace — the sections above its lists
    are the handoff, and the lists are the appendix."""
    try:
        import reflect
        return reflect.mine_transcript_messages(messages, standing=standing)
    except Exception:
        return {}


def mined_ideas(messages: list, transcript="", *, standing: "dict | None" = None, mined: "dict | None" = None) -> list:
    """The session's idea remarks as the trace keeps them (`idea_entry`)."""
    mined = _mine(messages, standing) if mined is None else mined
    pointer = _transcript_pointer(transcript) if transcript else ""
    return [idea_entry(c, pointer) for c in mined.get("idea_candidates") or []]


def mined_candidates(messages: list, *, standing: "dict | None" = None, mined: "dict | None" = None) -> list:
    """The lines the miner will not file: every candidate below HIGH, as
    `{"rule", "excerpt", "count"}`, and what it dropped and why.

    Mined here rather than handed over from the routing pass, because the two
    run as separate processes from the Stop hook and `mine_transcript` is
    deterministic over the same transcript — reading it twice is cheaper than
    passing a candidate list between them, and it keeps the trace buildable
    from the transcript alone. A HIGH candidate is not listed: it becomes a
    card, and a record that says both would double-count it. `standing` is
    `reflect.load_standing`'s sources, the same the routing pass reads.
    """
    mined = _mine(messages, standing) if mined is None else mined
    out = []
    for c in mined.get("memory_candidates") or []:
        if getattr(c, "confidence", "") == "HIGH":
            continue
        out.append({
            "rule": getattr(c, "rationale", "") or "",
            "excerpt": (getattr(c, "excerpts", None) or [getattr(c, "title", "")])[0],
            "count": getattr(c, "occurrences", 1),
        })
    # A reply to the agent is never filed; the trace is where it is logged.
    out.extend(mined.get("dropped_replies") or [])
    # Nor is a question, or a quote of what the session already carried.
    out.extend(mined.get("dropped") or [])
    return out


def note_stems(memory_root) -> "set[str]":
    """Every note's file name, without `.md` and in lower case, across the vault
    the memory root sits in: the vault is the nearest folder at or above it that
    holds `.obsidian` or `.git`, else the memory root itself. Hidden folders are
    not read."""
    start = Path(memory_root)
    vault = start
    for cand in (start, *list(start.parents)[:2]):
        if (cand / ".obsidian").is_dir() or (cand / ".git").exists():
            vault = cand
            break
    stems: set = set()
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.endswith(".md"):
                stems.add(name[:-3].lower())
    return stems


def from_transcript(transcript_path: Path, *, session_id: str, when: date = None,
                    history_path: Path = None, project: str = "", task: str = "", surface: str = "",
                    candidates: list = None, known: "set | None" = None, memory_root=None,
                    ideas: list = None) -> Trace:
    """The session's trace. `known` is the vault's note names (`note_stems`):
    when given, a recall-history slug that names no note is not linked. The
    history is the machine's, shared by every surface, and holds names that
    are no note — a self-probe's note retired the same minute, a test's
    fixture — and a trace that linked them linked nowhere (task 186).
    `memory_root`, when given, lets the miner read the always-load tier as
    what the session already carried (task 192)."""
    import reflect  # the sidecar's own transcript reader, so both read one shape

    messages = reflect.load_messages(Path(transcript_path))
    if candidates is None or ideas is None:
        standing = reflect.load_standing(messages, memory_root) if memory_root else None
        mined = _mine(messages, standing)
        if candidates is None:
            candidates = mined_candidates(messages, mined=mined)
        if ideas is None:
            ideas = mined_ideas(messages, transcript_path, mined=mined)
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
        if known is not None and slug.lower() not in known:
            continue
        if slug not in recalled and slug not in captured:
            recalled.append(slug)
    if when is None:
        when = (start or datetime.now(timezone.utc)).date()
    asked = _first_request(messages)
    title = asked[:TITLE_CHARS] if asked else fallback_title(session_id)
    touched = (captured + recalled)[:MAX_TOUCHED]
    return Trace(when=when, session_id=session_id, title=title, touched=touched,
                 captured=[t for t in captured if t in touched], recalled=[t for t in recalled if t in touched],
                 asked=asked[:ASKED_CHARS], outcome=_closing_recap(messages),
                 candidates=list(candidates or [])[:MAX_CANDIDATES],
                 ideas=list(ideas or [])[:MAX_IDEAS],
                 project=project, task=task, surface=surface,
                 attended=has_operator_turn(messages))


# ── one trace per session ────────────────────────────────────────────────────
#
# A session keeps one trace (agentm-vault § Capture, amended 2026-09-28). Its
# path used to be a pure function of the day, the first request and the id, so
# a session resumed after a compaction — whose transcript now opens on a
# summary, or on a bare "yes" — wrote a second file under the same id: eleven
# sessions had two by 2026-09-24. The trace is found by its `session:` id
# first, and a later write merges into it: the first request and the file name
# stay, what was captured, recalled and said in passing accumulates, and the
# newest closing recap is the Outcome.

_SECTIONS = ("Asked", "Outcome", "Captured", "Recalled", "Candidates", "Ideas")
_LIST_SECTIONS = ("Captured", "Recalled", "Candidates")


def _split_trace(text: str) -> "tuple[list, dict]":
    """A trace's frontmatter lines, and its sections by heading, in order."""
    fm_lines: list = []
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end >= 0:
            fm_lines = text[4:end].split("\n")
            body = text[end + 5:]
    sections: dict = {}
    current = None
    for line in body.split("\n"):
        if line.startswith("## "):
            current = line[3:].strip()
            sections.setdefault(current, [])
        elif current is not None:
            sections[current].append(line)
    return fm_lines, {k: "\n".join(v).strip("\n") for k, v in sections.items()}


def _fm_value(fm_lines: list, key: str) -> "str | None":
    for line in fm_lines:
        if line.startswith(key + ":"):
            raw = line[len(key) + 1:].strip()
            try:
                value = json.loads(raw)
                return value if isinstance(value, str) else raw
            except (json.JSONDecodeError, ValueError):
                return raw.strip("'\"")
    return None


def _fm_list(fm_lines: list, key: str) -> list:
    raw = next((line[len(key) + 1:].strip() for line in fm_lines if line.startswith(key + ":")), "")
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


def _set_fm(fm_lines: list, key: str, rendered: "str | None", after: str) -> list:
    """Set `key` to its rendered line, keeping its place; a new key goes after
    `after`, and None removes the key."""
    out = [line for line in fm_lines if not line.startswith(key + ":")]
    if rendered is None:
        return out
    for i, line in enumerate(fm_lines):
        if line.startswith(key + ":"):
            return fm_lines[:i] + [rendered] + fm_lines[i + 1:]
    for i, line in enumerate(out):
        if line.startswith(after + ":"):
            return out[:i + 1] + [rendered] + out[i + 1:]
    return out + [rendered]


def _link_target(line: str) -> str:
    m = re.match(r"^- \[\[([^\]|]+)", line.strip())
    return Path(m.group(1)).name if m else line.strip()


def _union_lines(base: str, incoming: str, key) -> str:
    lines = [ln for ln in base.split("\n") if ln.strip()]
    seen = {key(ln) for ln in lines}
    for ln in incoming.split("\n"):
        if ln.strip() and key(ln) not in seen:
            lines.append(ln)
            seen.add(key(ln))
    return "\n".join(lines)


def merge_trace_text(base: str, incoming: str, session_id: str, *, newest_outcome: bool = True) -> str:
    """One session's trace, from the file it already has (`base`) and a later
    write of it (`incoming`). The base keeps its name, its `created` day and its
    first request; a base written before any request could be read takes the
    incoming title and request. `touched` and the three lists take the union,
    in first-seen order. The Outcome is the incoming one when it has one: the
    newest closing recap is where the session stands. A fold that merges an
    older file into a newer one passes `newest_outcome=False` to keep the
    base's."""
    b_fm, b_sec = _split_trace(base)
    i_fm, i_sec = _split_trace(incoming)
    fm = list(b_fm)
    fallback = _fm_value(b_fm, "title") == fallback_title(session_id)
    if fallback and _fm_value(i_fm, "title") not in (None, fallback_title(session_id)):
        fm = _set_fm(fm, "title", next(line for line in i_fm if line.startswith("title:")), "title")
        b_sec["Asked"] = i_sec.get("Asked", b_sec.get("Asked", ""))
    for key, after in (("project", "created"), ("task", "project"), ("surface", "slug")):
        line = next((ln for ln in i_fm if ln.startswith(key + ":")), None)
        if line is not None:
            fm = _set_fm(fm, key, line, after)
    touched = _fm_list(b_fm, "touched")
    touched += [t for t in _fm_list(i_fm, "touched") if t not in touched]
    if touched:
        fm = _set_fm(fm, "touched", "touched: [" + ", ".join(_yaml_scalar(t) for t in sorted(set(touched))) + "]",
                     "session")
    sections = dict(b_sec)
    if i_sec.get("Outcome", "").strip() and (newest_outcome or not b_sec.get("Outcome", "").strip()):
        sections["Outcome"] = i_sec["Outcome"]
    for name in _LIST_SECTIONS:
        key = _link_target if name != "Candidates" else (lambda ln: ln.strip())
        merged = _union_lines(b_sec.get(name, ""), i_sec.get(name, ""), key)
        if merged:
            sections[name] = merged
    # An idea remark is one entry whatever its words hold, so the union is by
    # entry (its message and its words), never by line.
    ideas = parse_ideas(b_sec.get("Ideas", ""))
    seen = {_idea_key(e) for e in ideas}
    for e in parse_ideas(i_sec.get("Ideas", "")):
        if _idea_key(e) not in seen:
            ideas.append(e)
            seen.add(_idea_key(e))
    if ideas:
        sections["Ideas"] = "\n".join(render_ideas(ideas[:MAX_IDEAS]))
    out = ["---", *fm, "---", ""]
    for name in list(_SECTIONS) + [k for k in sections if k not in _SECTIONS]:
        text = (sections.get(name) or "").strip("\n")
        if text.strip():
            out += [f"## {name}", "", text, ""]
    return "\n".join(out)


def find_session_trace(vault_path, session_id: str) -> "Path | None":
    """The trace this session already has, by its `session:` id. When a past
    write left two, the one titled from a request wins over a fallback title,
    then the older day, then the name. A superseded trace is never found."""
    folder = Path(vault_path) / EPISODIC_DIR
    if not session_id or not folder.is_dir():
        return None
    found = []
    for p in folder.glob("*.md"):
        try:
            with p.open(encoding="utf-8") as fh:
                head = fh.read(16384)
        except (OSError, UnicodeDecodeError):
            continue
        fm_lines, _ = _split_trace(head if "\n---\n" in head[4:] else head + "\n---\n")
        if _fm_value(fm_lines, "session") != session_id:
            continue
        if (_fm_value(fm_lines, "lifecycle") or "").lower() == "superseded" or _fm_value(fm_lines, "superseded_by"):
            continue
        titled = _fm_value(fm_lines, "title") != fallback_title(session_id)
        found.append((not titled, _fm_value(fm_lines, "created") or "", p.name, p))
    return sorted(found)[0][3] if found else None


def write_trace(vault_path, trace: Trace) -> str | None:
    """Write the trace under the memory root; None when the session left
    nothing to record. A session that already has a trace is written into it,
    whatever its title says now; only a session with none gets a new name.

    Candidates and ideas count as something to record. The miner files no note
    below HIGH and no idea at all, so a session whose only durable output was
    things said in passing has that material here or nowhere.

    A session with no operator turn writes nothing (the operator's ruling of
    2026-10-07): a scheduled poll every three hours wrote nine of the twenty
    cards in the 2026-10-07 write-quality audit, each one a trace of nobody."""
    if not trace.attended:
        return None
    if not trace.touched and not trace.candidates and not trace.ideas:
        return None
    root = Path(vault_path)
    existing = find_session_trace(root, trace.session_id)
    if existing is not None:
        rel = existing.relative_to(root).as_posix()
        incoming = trace.render().replace(f"slug: {trace.slug}\n", f"slug: {existing.stem}\n", 1)
        text = merge_trace_text(existing.read_text(encoding="utf-8"), incoming, trace.session_id)
    else:
        rel = trace.rel
        text = trace.render()
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return rel


def _stamps_for(transcript: Path, project: str, task: str, memory_root=None) -> tuple:
    """The trace's `project` and `task`: a flag when one is given, else the binding
    of the directory the session ran in (agentm-vault plan 09). A task comes from
    the binding only beside the binding's own project."""
    import session_binding  # same skill dir
    binding = session_binding.for_transcript(transcript, memory_root=memory_root)
    if project:
        default_task = binding.task if project == binding.project else None
    else:
        project, default_task = binding.project or "", binding.task
    return project, task or default_task or ""


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="write the session's episodic trace")
    ap.add_argument("transcript")
    ap.add_argument("--session", required=True)
    ap.add_argument("--vault-path", default=None, help="the memory root (default: $MEMORY_ROOT)")
    ap.add_argument("--day", default=None, help="YYYY-MM-DD (default: the transcript's first message)")
    ap.add_argument("--history", default=None, help="recall history JSONL (default: $AGENTM_RECALL_HISTORY, else ~/.cache/agentm/telemetry/recall-history.jsonl)")
    ap.add_argument("--project", default="", help="the vault project (default: the session's binding)")
    ap.add_argument("--task", default="", help="the task (default: the session's binding)")
    ap.add_argument("--surface", default=os.environ.get("AGENTM_SURFACE", "").strip(),
                    help="where the session ran, e.g. claude-code (default: $AGENTM_SURFACE)")
    a = ap.parse_args(argv)
    vault = a.vault_path or (os.environ.get("MEMORY_ROOT") or os.environ.get("MEMORY_VAULT_PATH", "")).strip()
    if not vault:
        print("episodic_trace: no memory root (--vault-path or MEMORY_ROOT)", file=sys.stderr)
        return 0
    project, task = _stamps_for(Path(a.transcript), a.project, a.task, memory_root=vault)
    try:
        trace = from_transcript(Path(a.transcript), session_id=a.session,
                                when=date.fromisoformat(a.day) if a.day else None,
                                history_path=Path(a.history) if a.history else None,
                                project=project, task=task, surface=a.surface,
                                known=note_stems(vault), memory_root=vault)
        rel = write_trace(vault, trace)
    except Exception as e:  # a hook never blocks session end on a trace
        print(f"episodic_trace: skipped ({e})", file=sys.stderr)
        return 0
    print(rel or ("nothing touched" if trace.attended else "no operator turn"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
