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
    project: str = ""
    task: str = ""
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
    """The ledger `recall_counter` writes, override included, so a test that
    redirects the ledger reads back the one it wrote."""
    import recall_counter  # same skill dir
    return recall_counter.default_history_path()


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
    # A reply to the agent is never filed; the trace is where it is logged.
    out.extend(mined.get("dropped_replies") or [])
    return out


def from_transcript(transcript_path: Path, *, session_id: str, when: date = None,
                    history_path: Path = None, project: str = "", task: str = "", surface: str = "",
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
    title = asked[:TITLE_CHARS] if asked else fallback_title(session_id)
    touched = (captured + recalled)[:MAX_TOUCHED]
    return Trace(when=when, session_id=session_id, title=title, touched=touched,
                 captured=[t for t in captured if t in touched], recalled=[t for t in recalled if t in touched],
                 asked=asked[:ASKED_CHARS], outcome=_closing_recap(messages),
                 candidates=list(candidates or [])[:MAX_CANDIDATES],
                 project=project, task=task, surface=surface)


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

_SECTIONS = ("Asked", "Outcome", "Captured", "Recalled", "Candidates")
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

    Candidates count as something to record. The miner files no note below
    HIGH any more, so a session whose only durable output was things said in
    passing has that material here or nowhere."""
    if not trace.touched and not trace.candidates:
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
                                project=project, task=task, surface=a.surface)
        rel = write_trace(vault, trace)
    except Exception as e:  # a hook never blocks session end on a trace
        print(f"episodic_trace: skipped ({e})", file=sys.stderr)
        return 0
    print(rel or "nothing touched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
