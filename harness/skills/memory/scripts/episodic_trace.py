#!/usr/bin/env python3
"""episodic_trace.py — the agent's memory of its own sessions.

Filing v2 remainders, task 7. One note per session under `memory/episodic/`,
`kind: session-trace`: the notes the session captured and recalled, as
wikilinks, under a one-line title taken from the session's first request —
built from the transcript and the recall history with no model call, capped,
and written at session end by the reflection hook. The calendar's day index
links the day's traces by `day:`; the dreaming binary's promotion job reads
their links as its recurrence signal. A session that touched nothing leaves
no trace: "what happened" without "to what" is a diary entry, and the diary
is the calendar's.

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
TITLE_CHARS = 120
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
    project: str = ""

    @property
    def rel(self) -> str:
        return trace_rel(self.when, self.session_id, self.title)

    @property
    def slug(self) -> str:
        return Path(self.rel).stem

    def render(self) -> str:
        entities = sorted({_stem(t) for t in self.touched})
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
        if entities:
            lines.append("entities: [" + ", ".join(_yaml_scalar(e) for e in entities) + "]")
        lines += ["---", "", self.title.strip(), ""]
        if self.captured:
            lines += ["## Captured", ""] + [f"- [[{_link(t)}]]" for t in self.captured] + [""]
        if self.recalled:
            lines += ["## Recalled", ""] + [f"- [[{_link(t)}]]" for t in self.recalled] + [""]
        return "\n".join(lines)


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
    for m in messages:
        if m.get("type") != "user":
            continue
        for b in _blocks(m):
            if b.get("type") == "text":
                line = next((l.strip() for l in (b.get("text") or "").splitlines() if l.strip()), "")
                if line and not line.startswith("<") and "tool_result" not in line:
                    return line[:TITLE_CHARS]
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


def from_transcript(transcript_path: Path, *, session_id: str, when: date = None,
                    history_path: Path = None, project: str = "") -> Trace:
    import reflect  # the sidecar's own transcript reader, so both read one shape

    messages = reflect.load_messages(Path(transcript_path))
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
    title = _first_request(messages) or f"session {session_id[:8]}"
    touched = (captured + recalled)[:MAX_TOUCHED]
    return Trace(when=when, session_id=session_id, title=title, touched=touched,
                 captured=[t for t in captured if t in touched], recalled=[t for t in recalled if t in touched],
                 project=project)


def write_trace(vault_path, trace: Trace) -> str | None:
    """Write the trace under the memory root; None when the session touched
    nothing. Never overwrites: a second write of the same session in the same
    day is the same file, rewritten whole."""
    if not trace.touched:
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
    ap.add_argument("--vault-path", default=None, help="the memory root (default: $MEMORY_VAULT_PATH)")
    ap.add_argument("--day", default=None, help="YYYY-MM-DD (default: the transcript's first message)")
    ap.add_argument("--history", default=None, help="recall history JSONL (default: ~/.cache/agentm/telemetry/recall-history.jsonl)")
    ap.add_argument("--project", default="")
    a = ap.parse_args(argv)
    vault = a.vault_path or os.environ.get("MEMORY_VAULT_PATH", "").strip()
    if not vault:
        print("episodic_trace: no memory root (--vault-path or MEMORY_VAULT_PATH)", file=sys.stderr)
        return 0
    try:
        trace = from_transcript(Path(a.transcript), session_id=a.session,
                                when=date.fromisoformat(a.day) if a.day else None,
                                history_path=Path(a.history) if a.history else None, project=a.project)
        rel = write_trace(vault, trace)
    except Exception as e:  # a hook never blocks session end on a trace
        print(f"episodic_trace: skipped ({e})", file=sys.stderr)
        return 0
    print(rel or "nothing touched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
