#!/usr/bin/env python3
"""lesson_compile.py — the night's lesson compile, one per project per day.

Mining used to happen at a session's first Stop, over the operator's typed
words only, once per session. Every later turn of a long session went unread,
an agent's own conclusions had no route in, Claude Code's auto-memory never
reached the vault, and Antigravity sessions were never read at all. This module
moves the work to the night (task 199):

  * **Bookmarks.** Each transcript keeps a high-water mark — how many of its
    lines have been read — in the engine's state directory. Every transcript
    folder is scanned, worktree and chip sessions included, and only the lines
    past the mark are read. A file that shrank was rewritten and is read again
    from the top.
  * **Slices.** What is new is condensed to prose: the operator's turns, the
    agent's, and relayed turns labelled as relayed; tool calls by name and a
    short head; a failed tool call by the first lines of its error; never tool
    output. The regex miner's candidates over the slice ride along as hints. A
    slice waits in a per-project pending queue until its project compiles, so a
    failed night loses nothing.
  * **The compile.** Per project, the slices, the day's commits and merged
    PRs, the session traces and the auto-memory notes saved since the last run
    go to one Sonnet call that drafts candidates. Each candidate is searched
    across the whole vault with the daemon's search, and a second call decides,
    for at most five, whether it is new, augments an existing lesson, or adds
    nothing. A new lesson files through `filing_engine` (`source:
    conversation`, `status: unfiled`, so enrichment judges it tonight). An
    augment is prose under the card's `## Added by dreaming` section, below
    everything already there; the text above it and the frontmatter are never
    touched. Lessons written earlier in the night are shown to each later
    project, so two projects do not file the same lesson twice.
  * **Antigravity.** agy transcripts are read with their own bookmarks and
    pre-digested by the newest Flash through `agy -p`. Until
    `lesson_compile.agy_enabled` is true the digests go to a dry report and
    never reach the compile.
  * **Auto-memory.** A PostToolUse hook records each note written under
    `~/.claude/projects/*/memory/` (`note-automemory`); the night reads it as
    an input and files nothing from it directly. `backfill-automemory` folds the
    notes that existed before the hook, once, outside the daily cap.

Costs are bounded twice: a per-project daily cap and a night cap, both in
dollars, each model call given what is left as `--max-budget-usd`. The last
line printed is a JSON object carrying `total_cost_usd`, which the runner reads.

    lesson_compile.py night [--dry-run] [--project P]
    lesson_compile.py collect [--transcript PATH] [--dry-run]
    lesson_compile.py compile [--dry-run] [--project P]
    lesson_compile.py note-automemory            # PostToolUse payload on stdin
    lesson_compile.py backfill-automemory [--dry-run] [--project P]

Every external call — `claude`, `agy`, `agentmd`, `git`, `gh` — goes through one
injectable runner, so tests never reach a model or the network.
"""
from __future__ import annotations

import argparse
import difflib
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import engine_state  # noqa: E402

STATE_SUBDIR = "lesson-compile"
GENERAL = "general"
CLAUDE = "claude-code"
AGY = "antigravity"

# At most this many writes — new lessons and augments together — per project
# per day. The operator's line is three to five; five is the ceiling, and a
# quiet day writes none.
MAX_LESSONS = 5
# What the drafting call may propose before the dedupe narrows it.
MAX_CANDIDATES = 8
PROJECT_CAP_USD = 0.60
NIGHT_CAP_USD = 4.00
# A call is not started with less than this left: it would stop half-way and
# the money spent on it would buy nothing.
MIN_CALL_USD = 0.05

# The condensing budget. A slice is prose, not a transcript: an agent turn is
# cut to its head, a tool call to its name and first characters, an error to
# its first lines, and a session's whole slice to a fixed size, head and tail.
OPERATOR_CHARS = 4000
AGENT_CHARS = 1500
TOOL_HEAD = 120
ERROR_HEAD = 240
SLICE_CHARS = 24000
PROJECT_INPUT_CHARS = 90000
AUTOMEMORY_NOTE_CHARS = 3000
HINTS_PER_SLICE = 10

# On the first run every transcript untouched this long is marked as read: the
# compile starts from today, not from the history.
SEED_QUIET_HOURS = 24
# A slice that has waited this long in the pending queue is dropped.
PENDING_MAX_DAYS = 3
# At most this many agy digests a night, dry or not; the rest wait in the
# pending queue for the next night. Many agy conversations are another agent's
# `agy -p` calls, and a night should not spend its quota on a backlog.
AGY_NIGHT_LIMIT = 30
# An agy slice shorter than this is a probe ("reply with the single word ok"),
# not a session: it is dropped without a digest.
AGY_MIN_SLICE_CHARS = 600

DEFAULT_MODEL = "sonnet"
FLASH_FALLBACK = "Gemini 3.8 Flash (Medium)"
CLAUDE_TIMEOUT_SEC = 600
AGY_TIMEOUT_SEC = 180

# Within the night's own candidates, two bodies this alike are one lesson —
# the same bar `dream.py`'s dedup proposals use.
SAME_LESSON_RATIO = 0.92

LESSON_TYPES = ("preference", "convention", "reference", "workflow", "fix")
AUGMENT_CLASSES = ("memory/semantic/", "memory/procedural/")
DREAMING_HEADING = "## Added by dreaming"


# ── the runner ────────────────────────────────────────────────────────────────

def subprocess_runner(argv, input_text=None, cwd=None, timeout=60, env=None):
    """(returncode, stdout, stderr). The one door to every external program."""
    proc = subprocess.run(argv, input=input_text, capture_output=True, text=True,
                          cwd=cwd, timeout=timeout, env=env)
    return proc.returncode, proc.stdout, proc.stderr


# ── state and config ─────────────────────────────────────────────────────────

def state_dir() -> Path:
    return engine_state.engine_state_dir() / STATE_SUBDIR


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _append_jsonl(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")


def _read_jsonl(path: Path) -> list:
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError:
        pass
    return out


def load_config() -> dict:
    """The `lesson_compile.*` keys of the kernel config, as a flat dict."""
    prefix = Path(os.environ.get("AGENTM_INSTALL_PREFIX") or Path.home() / ".claude")
    data = _read_json(prefix / ".agentm-config.json", {})
    if not isinstance(data, dict):
        return {}
    return {k.split(".", 1)[1]: v for k, v in data.items()
            if isinstance(k, str) and k.startswith("lesson_compile.")}


@dataclass
class Settings:
    model: str = DEFAULT_MODEL
    flash_model: str = ""
    max_lessons: int = MAX_LESSONS
    project_cap_usd: float = PROJECT_CAP_USD
    night_cap_usd: float = NIGHT_CAP_USD
    agy_enabled: bool = False

    @classmethod
    def from_config(cls, cfg: dict) -> "Settings":
        s = cls()
        if isinstance(cfg.get("model"), str) and cfg["model"].strip():
            s.model = cfg["model"].strip()
        if isinstance(cfg.get("flash_model"), str):
            s.flash_model = cfg["flash_model"].strip()
        for key, cast in (("max_lessons", int), ("project_cap_usd", float), ("night_cap_usd", float)):
            try:
                if cfg.get(key) is not None:
                    setattr(s, key, cast(cfg[key]))
            except (TypeError, ValueError):
                pass
        s.max_lessons = max(0, min(s.max_lessons, MAX_LESSONS))
        s.agy_enabled = cfg.get("agy_enabled") in (True, "true", "1", 1)
        return s


# ── sources and bookmarks ────────────────────────────────────────────────────

@dataclass
class Source:
    host: str
    key: str          # `host:session`, the bookmark's key
    session: str
    path: Path


def claude_projects_root() -> Path:
    return Path(os.environ.get("AGENTM_CLAUDE_PROJECTS") or Path.home() / ".claude" / "projects")


def agy_roots() -> list:
    raw = os.environ.get("AGENTM_AGY_ROOTS")
    if raw is not None:
        return [Path(p) for p in raw.split(os.pathsep) if p]
    gemini = Path.home() / ".gemini"
    return [gemini / "antigravity-cli", gemini / "antigravity", gemini / "antigravity-ide"]


def discover(claude_root: Path, agy: "list | None" = None) -> list:
    """Every session transcript there is: Claude Code's top-level session files
    in every project folder — worktree and chip sessions file there too — and
    agy's `transcript_full.jsonl`, one per conversation. A sub-agent's file and
    the daemon's own neutral-folder calls are not sessions."""
    out = []
    if claude_root.is_dir():
        for p in sorted(claude_root.glob("*/*.jsonl")):
            if p.is_file() and "agentm-neutral-cwd" not in p.parent.name:
                out.append(Source(CLAUDE, f"{CLAUDE}:{p.stem}", p.stem, p))
    for root in agy if agy is not None else agy_roots():
        for p in sorted(Path(root).glob("brain/*/.system_generated/logs/transcript_full.jsonl")):
            sid = p.parents[2].name
            out.append(Source(AGY, f"{AGY}:{sid}", sid, p))
    return out


def bookmarks_path() -> Path:
    return state_dir() / "bookmarks.json"


def load_bookmarks() -> "dict | None":
    data = _read_json(bookmarks_path(), None)
    return data if isinstance(data, dict) else None


def _count_lines(path: Path) -> "tuple[int, int]":
    """(complete lines, bytes through the last complete line)."""
    lines = size = 0
    with open(path, "rb") as f:
        for raw in f:
            if not raw.endswith(b"\n"):
                break
            lines += 1
            size += len(raw)
    return lines, size


def seed_bookmarks(sources: list, *, now: datetime) -> dict:
    """The first run's marks: a transcript untouched for a day is read to its
    end; one touched since starts from the top, so today's sessions are not
    lost to the switch-over."""
    marks = {}
    cutoff = now.timestamp() - SEED_QUIET_HOURS * 3600
    for s in sources:
        try:
            quiet = s.path.stat().st_mtime < cutoff
            lines, size = _count_lines(s.path) if quiet else (0, 0)
        except OSError:
            continue
        marks[s.key] = {"lines": lines, "size": size, "host": s.host, "path": str(s.path)}
    return marks


def read_new(source: Source, mark: "dict | None") -> "tuple[list, dict]":
    """The rows past the mark, parsed, and the mark after them. Only complete
    lines are read: a line still being written waits for the next pass. A file
    smaller than the mark was rewritten and is read from the top."""
    start = int((mark or {}).get("lines") or 0)
    seen = int((mark or {}).get("size") or 0)
    try:
        size_now = source.path.stat().st_size
    except OSError:
        return [], dict(mark or {})
    if size_now < seen:
        start, seen = 0, 0
    rows, lines, consumed = [], 0, 0
    with open(source.path, "rb") as f:
        for raw in f:
            if not raw.endswith(b"\n"):
                break
            lines += 1
            consumed += len(raw)
            if lines <= start:
                continue
            try:
                rec = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if isinstance(rec, dict):
                rows.append(rec)
    new_mark = {"lines": lines, "size": consumed, "host": source.host, "path": str(source.path)}
    return rows, new_mark


# ── condensing a slice ───────────────────────────────────────────────────────

def _cut(text: str, limit: int) -> str:
    text = " ".join((text or "").split()) if limit <= ERROR_HEAD else (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _tool_head(name: str, args) -> str:
    args = args if isinstance(args, dict) else {}
    for key in ("command", "CommandLine", "file_path", "AbsolutePath", "Path", "path", "pattern", "Query",
                "url", "query", "description", "prompt"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return f"  > {name}: {_cut(val, TOOL_HEAD)}"
    return f"  > {name}"


def _result_text(block: dict) -> str:
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(str(x.get("text") or "") for x in c if isinstance(x, dict))
    return ""


def _fit(text: str, limit: int = SLICE_CHARS) -> str:
    """Head and tail of an over-long slice: the opening says what the session
    was for, the end says how it came out."""
    if len(text) <= limit:
        return text
    head = limit // 3
    tail = limit - head
    return f"{text[:head]}\n[… {len(text) - limit} characters left out …]\n{text[-tail:]}"


def condense_claude(rows: list) -> "tuple[str, list]":
    """A Claude Code slice as labelled prose, and the user/assistant messages
    the regex miner reads. Labels: OPERATOR for what the operator typed,
    RELAYED for a turn the host says arrived from another session, a script
    or a handoff, AGENT for the agent's own prose. Injected context, sidechains
    and compaction summaries are left out; tool output is left out except the
    head of an error."""
    import reflect
    lines: list = []
    messages: list = []
    for r in rows:
        kind = r.get("type")
        if kind not in ("user", "assistant") or r.get("isSidechain"):
            continue
        messages.append(r)
        content = (r.get("message") or {}).get("content")
        if kind == "user":
            if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result"
                                                 for b in content):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"):
                        lines.append(f"  ! error: {_cut(_result_text(b), ERROR_HEAD)}")
                continue
            if r.get("isCompactSummary") or r.get("isMeta"):
                continue
            if reflect.is_operator_turn(r):
                lines.append(f"OPERATOR: {_cut(reflect._operator_text(r), OPERATOR_CHARS)}")
                continue
            raw = reflect._strip_envelopes(reflect._extract_text(r)).strip()
            if raw and (reflect._relayed(r) or reflect._HANDOFF_MARKER_KEY in raw
                        or (isinstance(r.get("origin"), dict) and r["origin"].get("kind") not in (None, "human"))):
                lines.append(f"RELAYED (not the operator; counts only if the agent checked or acted on it): "
                             f"{_cut(raw, OPERATOR_CHARS)}")
            continue
        prose = reflect._prose(r)
        if prose:
            lines.append(f"AGENT: {_cut(prose, AGENT_CHARS)}")
        for b in content if isinstance(content, list) else []:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                lines.append(_tool_head(str(b.get("name") or "?"), b.get("input")))
    return _fit("\n".join(lines)), messages


def scripted_exchange_ok(text: str) -> bool:
    """Whether a slice is a session at all. One with no operator turn and no
    tool call is a scripted exchange — a headless prompt in, an answer out, a
    translation job's batch — with nothing tried and nobody there."""
    return any(l.startswith(("OPERATOR:", "  > ")) for l in text.splitlines())


_AGY_REQUEST = re.compile(r"</?USER_REQUEST>")
_AGY_EXIT = re.compile(r"exited with code (\d+)")


def condense_agy(rows: list, *, relayed: bool) -> str:
    """An agy slice as labelled prose. agy cannot say who typed a request: a
    conversation run from another agent's scratch folder is labelled RELAYED,
    any other REQUESTER, and either way a claim in it counts only if the
    session checked or acted on it."""
    who = ("RELAYED (another agent's request; counts only if checked or acted on)" if relayed
           else "REQUESTER (the operator or a script)")
    lines = []
    for r in rows:
        src, kind = r.get("source"), r.get("type")
        content = r.get("content") if isinstance(r.get("content"), str) else ""
        if kind == "USER_INPUT":
            lines.append(f"{who}: {_cut(_AGY_REQUEST.sub('', content), OPERATOR_CHARS)}")
        elif src == "MODEL" and kind == "PLANNER_RESPONSE":
            if content.strip():
                lines.append(f"AGENT: {_cut(content, AGENT_CHARS)}")
            for call in r.get("tool_calls") or []:
                if isinstance(call, dict):
                    lines.append(_tool_head(str(call.get("name") or "?"), call.get("args")))
        elif src == "MODEL" and kind == "GENERIC":
            m = _AGY_EXIT.search(content)
            if (m and m.group(1) != "0") or r.get("error"):
                lines.append(f"  ! error: {_cut(str(r.get('error') or content), ERROR_HEAD)}")
        elif kind == "ERROR_MESSAGE":
            lines.append(f"  ! error: {_cut(content or str(r.get('error') or ''), ERROR_HEAD)}")
    return _fit("\n".join(lines))


def own_agy_digest(rows: list) -> bool:
    """Whether an agy conversation is one of the night's own digest calls. agy
    keeps a transcript of every `agy -p`, so each digest leaves a conversation
    that the next night would otherwise read as new work and digest again."""
    first = AGY_DIGEST_PROMPT.splitlines()[0]
    for r in rows:
        if r.get("type") == "USER_INPUT":
            content = r.get("content") if isinstance(r.get("content"), str) else ""
            return _AGY_REQUEST.sub("", content).lstrip().startswith(first)
    return False


_SCRATCH = re.compile(r"^/(?:private/)?tmp/claude-[^/]+/([^/]+)/")
_AGY_PATH_ARGS = ("Cwd", "Path", "AbsolutePath", "TargetFile", "File", "DirectoryPath")


def agy_paths(rows: list) -> list:
    """Every absolute path an agy conversation's tool calls name. agy records no
    working folder: an older build put it in `run_command`'s `Cwd`, a newer one
    only in the file paths its tools read and edit."""
    out = []
    for r in rows:
        for call in r.get("tool_calls") or []:
            args = call.get("args") if isinstance(call, dict) else None
            for key in _AGY_PATH_ARGS if isinstance(args, dict) else ():
                val = args.get(key)
                if isinstance(val, str) and (val.startswith("/") or Path(val).is_absolute()):
                    out.append(val)
    return out


def agy_cwd(rows: list) -> "str | None":
    """The folder the conversation worked in: its first `Cwd`, else the folder
    of its first file path."""
    for path in agy_paths(rows):
        return path if Path(path).suffix == "" else str(Path(path).parent)
    return None


def project_for_agy(rows: list, vault_root) -> "tuple[str | None, bool]":
    """(project, relayed). The project most of the conversation's paths sit
    under. A path in a Claude session's scratch folder means that session
    started the conversation: it counts for the project the folder is named
    for, and the requests are relayed."""
    import session_binding
    votes: dict = {}
    relayed = False
    seen: dict = {}
    for path in agy_paths(rows):
        m = _SCRATCH.match(path)
        if m:
            relayed = True
            key = ("folder", m.group(1))
        else:
            key = ("dir", path if Path(path).suffix == "" else str(Path(path).parent))
        if key not in seen:
            seen[key] = (session_binding.project_for_transcript_folder(key[1], vault_root) if key[0] == "folder"
                         else session_binding.project_for_directory(key[1], vault_root))
        if seen[key]:
            votes[seen[key]] = votes.get(seen[key], 0) + 1
    if not votes:
        return None, relayed
    return max(sorted(votes), key=lambda k: votes[k]), relayed


def miner_hints(messages: list) -> list:
    """The regex miner's memory candidates over the slice — one input among
    several, never a filing (task 199 call 7)."""
    try:
        import reflect
        mined = reflect.mine_transcript_messages(messages)
    except Exception:
        return []
    out = []
    for c in mined.get("memory_candidates", [])[:HINTS_PER_SLICE]:
        out.append(f"{c.category} ({c.confidence}): {_cut(c.body or c.title, 300)}")
    return out


# ── collect ──────────────────────────────────────────────────────────────────

def pending_dir() -> Path:
    return state_dir() / "pending"


def _bucket(project: "str | None") -> str:
    return project or GENERAL


def collect(*, memory_root: "Path | None", claude_root: "Path | None" = None, agy: "list | None" = None,
            only: "Path | None" = None, dry_run: bool = False, now: "datetime | None" = None) -> dict:
    """Read every transcript past its mark, stage the condensed slices, and
    move the marks. `only` stages one transcript (the `/work` dispatch)."""
    import session_binding
    now = now or datetime.now(timezone.utc)
    vault_root = session_binding._vault_root(memory_root) if memory_root else None
    sources = discover(claude_root or claude_projects_root(), agy)
    if only is not None:
        target = Path(only).resolve()
        sources = [s for s in sources if s.path.resolve() == target] or [
            Source(CLAUDE, f"{CLAUDE}:{target.stem}", target.stem, target)]
    marks = load_bookmarks()
    seeded = False
    if marks is None:
        marks = seed_bookmarks(sources if only is None else [], now=now)
        seeded = True
    report = {"sessions": 0, "staged": 0, "seeded": seeded, "by_project": {}}
    for s in sources:
        try:
            rows, new_mark = read_new(s, marks.get(s.key))
        except OSError:
            continue
        if not rows:
            marks[s.key] = new_mark
            continue
        report["sessions"] += 1
        if s.host == CLAUDE:
            text, messages = condense_claude(rows)
            if not scripted_exchange_ok(text):
                marks[s.key] = new_mark
                continue
            hints = miner_hints(messages)
            project = session_binding.for_transcript(s.path, memory_root=memory_root).project
            cwd = session_binding.transcript_cwd(s.path)
            relayed = False
        else:
            if own_agy_digest(rows):
                marks[s.key] = new_mark
                continue
            cwd = agy_cwd(rows)
            project, relayed = project_for_agy(rows, vault_root)
            text, hints = condense_agy(rows, relayed=relayed), []
        marks[s.key] = new_mark
        if not text.strip():
            continue
        bucket = _bucket(project)
        rec = {"host": s.host, "session": s.session, "project": bucket, "cwd": cwd or "",
               "collected": now.isoformat(timespec="seconds"), "text": text, "hints": hints,
               "relayed": relayed}
        report["staged"] += 1
        report["by_project"][bucket] = report["by_project"].get(bucket, 0) + 1
        if not dry_run:
            _append_jsonl(pending_dir() / f"{bucket}.jsonl", rec)
    if not dry_run:
        _write_json(bookmarks_path(), marks)
    return report


# ── the day's other inputs ───────────────────────────────────────────────────

def automemory_queue() -> Path:
    return state_dir() / "automemory.jsonl"


_AUTOMEMORY = re.compile(r"/\.claude/projects/([^/]+)/memory/[^/]+\.md$")


def note_automemory(payload: dict, *, now: "datetime | None" = None) -> bool:
    """The PostToolUse hook's entry: a write under an auto-memory folder is
    recorded for tonight's compile. Nothing is filed. The index file is not a
    note."""
    tool_input = payload.get("tool_input") if isinstance(payload, dict) else None
    path = (tool_input or {}).get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(path, str):
        return False
    norm = path.replace("\\", "/")
    m = _AUTOMEMORY.search(norm)
    if not m or norm.endswith("/MEMORY.md"):
        return False
    now = now or datetime.now(timezone.utc)
    _append_jsonl(automemory_queue(), {"path": path, "folder": m.group(1),
                                       "ts": now.isoformat(timespec="seconds"),
                                       "session": str(payload.get("session_id") or "")})
    return True


def _project_paths(vault_root: Path, project: str) -> list:
    import session_binding
    for y in sorted(Path(vault_root).joinpath("projects").glob("*/project.yaml")):
        slug, paths = session_binding._project_yaml(y)
        if (slug or y.parent.name) == project:
            return [p for p in paths if isinstance(p, str)]
    return []


_GH_REMOTE = re.compile(r"github\.com[:/]+([^/\s]+/[^/\s]+?)(?:\.git)?/?$")


def commits_and_prs(vault_root, project: str, since: datetime, runner) -> list:
    """The project's commits and merged PRs since the last compile, one line
    each. Best-effort: a repo that is gone or a `gh` that fails adds nothing."""
    out: list = []
    seen_repos = set()
    for raw in _project_paths(vault_root, project) if vault_root else []:
        repo = str(Path(raw).expanduser())
        if repo in seen_repos or not Path(repo).is_dir():
            continue
        seen_repos.add(repo)
        try:
            code, log, _ = runner(["git", "-C", repo, "log", "--all", "--no-merges",
                                   f"--since={since.isoformat()}", "--format=%h %ad %s",
                                   "--date=short", "-n", "40"], timeout=30)
            if code == 0:
                out += [f"commit {l}" for l in log.splitlines() if l.strip()]
            code, url, _ = runner(["git", "-C", repo, "remote", "get-url", "origin"], timeout=15)
            m = _GH_REMOTE.search(url.strip()) if code == 0 else None
            if m:
                code, js, _ = runner(["gh", "pr", "list", "-R", m.group(1), "--state", "merged",
                                      "--search", f"merged:>={since.date().isoformat()}",
                                      "--json", "number,title", "--limit", "20"], timeout=30)
                for pr in json.loads(js) if code == 0 and js.strip() else []:
                    out.append(f"merged PR {m.group(1)}#{pr.get('number')} {pr.get('title')}")
        except Exception:
            continue
    return out[:60]


_FM_LINE = re.compile(r"^(\w+):\s*(.*)$")


def _frontmatter(text: str) -> "tuple[dict, str]":
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    fm = {}
    for line in text[4:end].splitlines():
        m = _FM_LINE.match(line)
        if m:
            fm[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    nl = text.find("\n", end + 4)
    return fm, text[nl + 1:] if nl >= 0 else ""


def _section(body: str, heading: str, limit: int) -> str:
    m = re.search(rf"^## {re.escape(heading)}\s*$", body, re.M)
    if not m:
        return ""
    rest = body[m.end():]
    nxt = re.search(r"^## ", rest, re.M)
    return _cut((rest[: nxt.start()] if nxt else rest).strip(), limit)


def traces_for(memory_root: Path, project: str, days: set, limit: int = 15) -> list:
    """The day's session traces for the project — what each session asked and
    how it came out."""
    out = []
    episodic = Path(memory_root) / "memory" / "episodic"
    if not episodic.is_dir():
        return out
    for p in sorted(episodic.rglob("*.md")):
        try:
            with open(p, encoding="utf-8") as f:
                head = f.read(4096)
        except OSError:
            continue
        fm, _ = _frontmatter(head)
        if fm.get("day", "")[:10] not in days:
            continue
        if (fm.get("project") or GENERAL) != project:
            continue
        try:
            _, body = _frontmatter(p.read_text(encoding="utf-8"))
        except OSError:
            continue
        out.append(f"- {fm.get('title') or p.stem}: asked {_section(body, 'Asked', 300)!r}; "
                   f"outcome {_section(body, 'Outcome', 600)!r}")
        if len(out) >= limit:
            break
    return out


# ── the model calls ──────────────────────────────────────────────────────────

@dataclass
class CallResult:
    text: str = ""
    cost_usd: float = 0.0
    error: str = ""


def claude_argv(model: str, budget: float) -> list:
    """The shape `field_brief.py` uses, with no tools at all: the compile
    reads what it is given and answers in JSON."""
    settings = {"disableAllHooks": True}
    return ["claude", "-p", "--model", model, "--no-session-persistence", "--tools=",
            "--settings", json.dumps(settings), "--strict-mcp-config", "--disable-slash-commands",
            "--output-format", "json", "--max-budget-usd", f"{budget:.2f}"]


def run_claude(prompt: str, *, model: str, budget: float, runner) -> CallResult:
    """One `claude -p` from a scratch directory, so no CLAUDE.md or memory index
    is loaded and no session transcript is left behind to be read tomorrow."""
    cwd = tempfile.mkdtemp(prefix="lesson-compile-")
    try:
        code, out, err = runner(claude_argv(model, budget), input_text=prompt, cwd=cwd,
                                timeout=CLAUDE_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        return CallResult(error=f"claude timed out after {CLAUDE_TIMEOUT_SEC} s")
    except OSError as e:
        return CallResult(error=f"claude could not be started ({type(e).__name__})")
    finally:
        shutil.rmtree(cwd, ignore_errors=True)
    try:
        res = json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
    except ValueError:
        res = {}
    cost = float(res.get("total_cost_usd") or 0.0) if isinstance(res, dict) else 0.0
    if not isinstance(res, dict) or res.get("is_error") or "result" not in res:
        tail = (err or "").strip()[-300:] or (str(res.get("result")) if isinstance(res, dict) else "")
        return CallResult(cost_usd=cost, error=tail or f"claude exited {code} with no result")
    return CallResult(text=str(res.get("result") or ""), cost_usd=cost)


_FLASH = re.compile(r"^\S+\t(Gemini (\d+(?:\.\d+)*) Flash \(Medium\))\s*$")


def newest_flash(runner) -> str:
    """The newest Flash `agy models` serves, at medium effort; the fallback
    when the list cannot be read."""
    try:
        code, out, _ = runner(["agy", "models"], timeout=60)
    except Exception:
        return FLASH_FALLBACK
    best, best_v = FLASH_FALLBACK, ()
    for line in out.splitlines() if code == 0 else []:
        m = _FLASH.match(line.strip())
        if m:
            v = tuple(int(x) for x in m.group(2).split("."))
            if v > best_v:
                best, best_v = m.group(1), v
    return best


AGY_DIGEST_PROMPT = """You are condensing one Antigravity session for a nightly memory compile.
Write at most 1,500 characters of plain prose: what the session set out to do; what was tried; what
failed and why; what fixed it; any conclusion the agent verified; any preference or rule the requester
stated. A claim in a RELAYED or REQUESTER line counts only if the agent's later turns show it checked or
acted on it, so say which claims were checked. If nothing durable happened, answer exactly: NOTHING DURABLE

=== SESSION ===
"""


def digest_agy(text: str, *, model: str, runner) -> CallResult:
    env = dict(os.environ, AGENTM_AGY_RECALL="off")
    try:
        code, out, err = runner(["agy", "-p", AGY_DIGEST_PROMPT + text, "--model", model,
                                 "--print-timeout", f"{AGY_TIMEOUT_SEC}s"],
                                timeout=AGY_TIMEOUT_SEC + 30, env=env)
    except Exception as e:
        return CallResult(error=f"agy failed ({type(e).__name__})")
    if code != 0 or not out.strip():
        return CallResult(error=(err or "").strip()[-300:] or f"agy exited {code}")
    return CallResult(text=out.strip())


_ESCAPE = re.compile(r'\\(["\\/bfnrtu]?)')


def _repair_escapes(raw: str) -> str:
    """Every backslash that does not start a valid JSON escape, doubled. A
    valid escape is consumed whole, so `\\\\` stays a pair."""
    return _ESCAPE.sub(lambda m: m.group(0) if m.group(1) else "\\\\", raw)


def parse_json_reply(text: str) -> "dict | None":
    """The JSON object in a model's reply, fenced or bare; None when there is
    none. A reply about shell code tends to carry what strict JSON refuses —
    a raw newline inside a string, a `\\$` or `\\(` escape — so control
    characters are allowed and an invalid escape is read as a literal
    backslash before the reply is given up on."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    raw = m.group(0)
    for candidate in (raw, _repair_escapes(raw)):
        try:
            data = json.loads(candidate, strict=False)
        except ValueError:
            continue
        return data if isinstance(data, dict) else None
    return None


def _keep_unparsed(project: str, stage: str, text: str) -> None:
    """A reply that would not parse, kept for the operator to read: it costs
    money, and an empty result is otherwise indistinguishable from a quiet
    day."""
    try:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = state_dir() / "unparsed" / f"{stamp}-{project}-{stage}.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text or "", encoding="utf-8")
    except OSError:
        pass


DRAFT_PROMPT = """You are AgentM's nightly lesson compiler for the project `{project}`.
Below is everything new from its sessions since the last compile: condensed session slices, the regex
miner's hints, commits and merged PRs, session traces, and notes the agent saved to its own memory.

Draft the durable lessons a future session would be glad to recall, whether it works on this project or
another: a fix and its cause, a tool or system behaviour learned the hard way, a fact about how this
project builds, runs or ships, a workflow that worked, a convention decided, a preference the operator
stated. Not a status report, a task log, a plan, or anything only true today.

Notes the agent saved to its own memory live in one host's private folder and have never reached the
shared vault. Their durable content is lesson material like any other: fold related notes into one lesson.

Rules:
- At most {max_candidates} candidates, and usually far fewer. A quiet day yields none: return an empty list.
- Lines marked RELAYED or REQUESTER came from another session or a script. A claim there counts only if
  a later AGENT line or a tool call shows this session checked it or acted on it.
- OPERATOR lines are the operator's own words; a preference or rule stated there is a lesson.
- Never include secrets, credentials, personal contact details or private data.
- `type` is one of: preference, convention, reference, workflow, fix.
- `lesson` is two to five plain sentences that stand alone. `evidence` lists the sessions, commits or PRs
  it rests on, briefly. `query` is a short search phrase that would find an existing note saying the same.

Answer with JSON only:
{{"candidates": [{{"title": "...", "type": "...", "lesson": "...", "evidence": ["..."], "query": "..."}}]}}

=== INPUTS ===
{inputs}
"""

DECIDE_PROMPT = """You are deciding what AgentM's nightly lesson compile writes for project `{project}`.
Each candidate below comes with the existing notes the vault's search found for it (N…), and the
lessons already written tonight (T…). Never duplicate a lesson.

For each candidate choose one action:
- "augment": an existing note marked AUGMENTABLE, or a T note, already states this lesson or a close one.
  Add what is new: a sharper statement, new evidence or a new case, or a correction if the note is wrong.
  `kind` is one of sharpen, evidence, correct. `added` is at most 600 characters of plain prose.
- "drop": the vault already holds it (in any note, augmentable or not) and there is nothing to add, or it
  is not a durable lesson after all, or it repeats another candidate you already kept.
- "new": nothing in the vault says it. Give `title`, `type` and `body` (the lesson, plain sentences).

Merge candidates that say the same thing into one action. At most {max_lessons} actions may be new or
augment; drop the rest. Fewer is better than churn.

Answer with JSON only:
{{"actions": [{{"candidate": 1, "action": "new|augment|drop", "target": "N2|T1", "kind": "...",
"title": "...", "type": "...", "body": "...", "evidence": ["..."], "added": "...", "why": "..."}}]}}

=== CANDIDATES ===
{candidates}

=== WRITTEN TONIGHT ===
{tonight}
"""


# ── neighbours ───────────────────────────────────────────────────────────────

@dataclass
class Neighbour:
    rel: str            # memory-root-relative where it falls under it, else vault-relative
    title: str
    summary: str
    augmentable: bool


def search_neighbours(query: str, *, vault_root: Path, memory_root: Path, runner, question: str = "",
                      k: int = 6) -> list:
    """The daemon's own search over the whole vault, in the hybrid mode recall
    uses: the query's terms on the lexical arm and the lesson itself on the
    dense one (`-surface measure`: nobody reads these hits, so none counts as a
    recall). Session traces are records of a day, not lessons, and are left
    out."""
    binary = os.environ.get("AGENTMD", "").strip() or "agentmd"
    try:
        import recall
        terms = recall._daemon_query_terms(query) or query
    except Exception:
        terms = query
    if not terms.strip():
        return []
    # A wide pool, because a project's own lists and plans crowd a lesson card
    # out of the top few: a card that already holds the lesson ranked 20th
    # behind six of its project's files in the first live run.
    argv = [binary, "search", "-json", "-surface", "measure", "-mode", "hybrid", "-k", str(max(k * 4, 24))]
    if question.strip():
        argv += ["-question", question.strip()[:1000]]
    try:
        code, out, _ = runner(argv + [terms], timeout=30)
        data = json.loads(out) if code == 0 and out.strip() else {}
    except Exception:
        return []
    rows = data.get("results", []) if isinstance(data, dict) else data if isinstance(data, list) else []
    # The daemon answers vault-relative paths; a card is written by its path
    # under the memory root, which is the vault root itself on a flat layout.
    try:
        inner = Path(memory_root).resolve().relative_to(Path(vault_root).resolve()).as_posix()
    except ValueError:
        inner = None
    prefix = "" if inner in (None, ".") else inner + "/"
    out_rows = []
    for row in rows:
        path = row.get("path") if isinstance(row, dict) else None
        if not isinstance(path, str):
            continue
        under = inner is not None and path.startswith(prefix)
        rel = path[len(prefix):] if under else path
        if rel.startswith("memory/episodic/"):
            continue
        augmentable = (under and rel.startswith(AUGMENT_CLASSES) and bool(row.get("type"))
                       and row.get("lifecycle") not in ("superseded", "archived"))
        out_rows.append(Neighbour(rel, str(row.get("title") or Path(path).stem),
                                  _cut(str(row.get("summary") or row.get("snippet") or ""), 400),
                                  augmentable))
    # Lesson cards first, in rank order: they are what a candidate may
    # augment. The best read-only matches fill what is left, so the decision
    # still sees a design or a list that already says the same thing.
    cards = [n for n in out_rows if n.augmentable][: max(1, k - 2)]
    others = [n for n in out_rows if not n.augmentable][: k - len(cards)]
    return cards + others


def neighbours_for(c: dict, night, k: int = 6) -> list:
    """A candidate's neighbours, searched twice: by the query the model wrote
    for it and by its own title. The query alone missed cards that held the
    same lesson under the same title (the first unattended night refiled three
    of them), so the two result lists are interleaved, each one's best first,
    and cut to the usual pool with lesson cards first."""
    question = f"{c.get('title') or ''}. {c.get('lesson') or ''}"
    runs = []
    for terms in dict.fromkeys(t for t in (str(c.get("query") or ""), str(c.get("title") or "")) if t.strip()):
        runs.append(search_neighbours(terms, vault_root=night.vault_root, memory_root=night.memory_root,
                                      runner=night.runner, question=question, k=k))
    merged, seen = [], set()
    for row in (n for tier in itertools.zip_longest(*runs) for n in tier if n is not None):
        if row.rel not in seen:
            seen.add(row.rel)
            merged.append(row)
    cards = [n for n in merged if n.augmentable][: max(1, k - 2)]
    return cards + [n for n in merged if not n.augmentable][: k - len(cards)]


# ── writing ──────────────────────────────────────────────────────────────────

def with_addition(current: str, added: str, *, day: str, kind: str) -> "str | None":
    """The card's text with `added` at the end of its `## Added by dreaming`
    section, or in a new section at the end. Nothing already there moves or
    changes. None when the card already holds the words."""
    added = added.strip()
    if not added or " ".join(added.split()) in " ".join(current.split()):
        return None
    entry = f"**{day} · lesson compile · {kind}**\n\n{added}\n"
    lines = current.split("\n")
    at = next((i for i, l in enumerate(lines) if l.startswith(DREAMING_HEADING)), None)
    if at is None:
        return current.rstrip("\n") + "\n\n" + DREAMING_HEADING + "\n\n" + entry
    end = next((i for i in range(at + 1, len(lines)) if lines[i].startswith(("## ", "# "))), len(lines))
    head = "\n".join(lines[:end]).rstrip("\n") + "\n\n" + entry
    tail = "\n".join(lines[end:])
    return head + ("\n" + tail if tail else "")


@dataclass
class Written:
    rel: str
    title: str
    text: str
    action: str


def _lesson_body(body: str, evidence: list) -> str:
    out = body.strip() + "\n"
    ev = [e for e in (evidence or []) if isinstance(e, str) and e.strip()]
    if ev:
        out += "\n## Evidence\n\n" + "\n".join(f"- {_cut(e, 300)}" for e in ev[:8]) + "\n"
    return out


def file_new(memory_root: Path, *, project: str, title: str, mtype: str, body: str, evidence: list,
             day: str) -> "Path | None":
    import card_shape
    import filing_engine
    import session_binding
    mtype = mtype if mtype in LESSON_TYPES else "reference"
    text = _lesson_body(body, evidence)
    slug = card_shape.kebab(title)[:60].strip("-") or "lesson"
    binding = session_binding.Binding(None if project == GENERAL else project, None)
    extra = {"captured": day, **session_binding.stamps_for_type(binding, mtype)}
    decision = filing_engine.decide(memory_root, title=title, body=text, slug=slug, type_hint=mtype,
                                    confidence="medium", source="conversation")
    if decision.op == "noop":
        filing_engine.apply(memory_root, decision, body=text)
        return None
    return filing_engine.apply(memory_root, decision, body=text, title=title,
                               tags=["lesson-compile"], extra=extra)


def augment(memory_root: Path, rel: str, *, added: str, kind: str, day: str) -> bool:
    p = Path(memory_root) / rel
    if not p.is_file():
        return False
    current = p.read_text(encoding="utf-8")
    new = with_addition(current, added, day=day, kind=kind if kind in ("sharpen", "evidence", "correct")
                        else "evidence")
    if new is None:
        return False
    p.write_text(new, encoding="utf-8")
    return True


def _same(a: str, b: str) -> bool:
    return difflib.SequenceMatcher(None, " ".join(a.lower().split()), " ".join(b.lower().split())).ratio() \
        >= SAME_LESSON_RATIO


# ── the compile ──────────────────────────────────────────────────────────────

@dataclass
class Night:
    settings: Settings
    runner: object
    memory_root: Path
    vault_root: Path
    day: str
    dry_run: bool = False
    spent: float = 0.0
    written: list = field(default_factory=list)
    report: dict = field(default_factory=dict)

    def left(self, project_spent: float) -> float:
        return max(0.0, min(self.settings.project_cap_usd - project_spent,
                            self.settings.night_cap_usd - self.spent))


def spend_path() -> Path:
    return state_dir() / "spend.json"


def _inputs_text(slices: list, automemory: list, history: list, traces: list) -> str:
    parts = []
    for s in slices:
        head = f"--- session {s['session'][:8]} ({s['host']}{', ' + s['cwd'] if s.get('cwd') else ''})"
        body = s.get("digest") or s.get("text") or ""
        hints = "\n".join(f"  hint: {h}" for h in s.get("hints") or [])
        parts.append(f"{head}\n{body}" + (f"\n{hints}" if hints else ""))
    if automemory:
        parts.append("--- notes the agent saved to its own memory\n" + "\n\n".join(automemory))
    if history:
        parts.append("--- commits and merged PRs\n" + "\n".join(history))
    if traces:
        parts.append("--- session traces\n" + "\n".join(traces))
    return _fit("\n\n".join(parts), PROJECT_INPUT_CHARS)


def _render_candidates(cands: list, neighbours: list) -> str:
    out = []
    for i, (c, ns) in enumerate(zip(cands, neighbours), 1):
        out.append(f"[{i}] {c.get('title')} ({c.get('type')})\n{c.get('lesson')}\n"
                   f"evidence: {'; '.join(map(str, c.get('evidence') or []))}")
        for j, n in enumerate(ns, 1):
            flag = "AUGMENTABLE" if n.augmentable else "read-only"
            out.append(f"   N{i}.{j} [{flag}] {n.rel} — {n.title}: {n.summary}")
    return "\n".join(out)


def compile_project(night: Night, project: str, *, slices: list, automemory: list, history: list,
                    traces: list, project_spent: float = 0.0) -> dict:
    """One project's compile: draft, search, decide, write. Returns its report."""
    rep = {"project": project, "slices": len(slices), "candidates": 0, "new": [], "augmented": [],
           "dropped": 0, "cost_usd": 0.0, "error": "", "actions": []}
    if night.left(project_spent) < MIN_CALL_USD:
        rep["error"] = "cost cap reached"
        return rep
    inputs = _inputs_text(slices, automemory, history, traces)
    draft = run_claude(DRAFT_PROMPT.format(project=project, max_candidates=MAX_CANDIDATES, inputs=inputs),
                       model=night.settings.model, budget=night.left(project_spent), runner=night.runner)
    night.spent += draft.cost_usd
    project_spent += draft.cost_usd
    rep["cost_usd"] += draft.cost_usd
    if draft.error:
        rep["error"] = f"draft: {draft.error}"
        return rep
    drafted = parse_json_reply(draft.text)
    if drafted is None:
        _keep_unparsed(project, "draft", draft.text)
        rep["error"] = "draft: the reply held no JSON"
        return rep
    cands = [c for c in drafted.get("candidates") or [] if isinstance(c, dict)
             and str(c.get("lesson") or "").strip()][:MAX_CANDIDATES]
    # The night's own duplicates, before any search: one lesson said twice is one candidate.
    unique: list = []
    for c in cands:
        if not any(_same(str(c["lesson"]), str(u["lesson"])) for u in unique):
            unique.append(c)
    cands = unique
    rep["candidates"] = len(cands)
    if not cands:
        return rep
    neighbours = [neighbours_for(c, night) for c in cands]
    rep["searched"] = [{"title": _cut(str(c.get("title") or ""), 120), "query": _cut(str(c.get("query") or ""), 200),
                        "neighbours": [n.rel for n in ns]} for c, ns in zip(cands, neighbours)]
    if night.left(project_spent) < MIN_CALL_USD:
        rep["error"] = "cost cap reached before the decision"
        return rep
    tonight = "\n".join(f"T{i} {w.rel} — {w.title}: {_cut(w.text, 300)}"
                        for i, w in enumerate(night.written, 1)) or "(none)"
    decide = run_claude(DECIDE_PROMPT.format(project=project, max_lessons=night.settings.max_lessons,
                                             candidates=_render_candidates(cands, neighbours), tonight=tonight),
                        model=night.settings.model, budget=night.left(project_spent), runner=night.runner)
    night.spent += decide.cost_usd
    rep["cost_usd"] += decide.cost_usd
    if decide.error:
        rep["error"] = f"decide: {decide.error}"
        return rep
    decided = parse_json_reply(decide.text)
    if decided is None:
        _keep_unparsed(project, "decide", decide.text)
        rep["error"] = "decide: the reply held no JSON"
        return rep
    writes = 0
    for act in decided.get("actions") or []:
        if not isinstance(act, dict):
            continue
        kind = act.get("action")
        rep["actions"].append({"action": str(kind), "title": _cut(str(act.get("title") or ""), 120),
                               "target": str(act.get("target") or ""), "why": _cut(str(act.get("why") or ""), 300)})
        if kind not in ("new", "augment") or writes >= night.settings.max_lessons:
            rep["dropped"] += 1
            continue
        if kind == "augment":
            target = _resolve_target(str(act.get("target") or ""), act.get("candidate"), neighbours, night.written)
            if target is None:
                rep["dropped"] += 1
                continue
            added = str(act.get("added") or "").strip()
            if night.dry_run or augment(night.memory_root, target, added=added, kind=str(act.get("kind") or ""),
                                        day=night.day):
                rep["augmented"].append(target)
                writes += 1
            continue
        title = _cut(str(act.get("title") or ""), 120)
        body = str(act.get("body") or "").strip()
        if not title or not body:
            rep["dropped"] += 1
            continue
        if any(_same(body, w.text) for w in night.written):
            rep["dropped"] += 1
            continue
        if night.dry_run:
            rep["new"].append(title)
            night.written.append(Written("(dry run)", title, body, "new"))
            writes += 1
            continue
        try:
            path = file_new(night.memory_root, project=project, title=title, mtype=str(act.get("type") or ""),
                            body=body, evidence=act.get("evidence") or [], day=night.day)
        except Exception as e:  # the volume gate, a contract that will not parse
            rep["error"] = f"filing: {e}"
            break
        if path is None:
            rep["dropped"] += 1
            continue
        rel = Path(path).resolve().relative_to(Path(night.memory_root).resolve()).as_posix()
        rep["new"].append(rel)
        night.written.append(Written(rel, title, body, "new"))
        writes += 1
    return rep


def _resolve_target(ref: str, cand, neighbours: list, written: list) -> "str | None":
    """An action's target, only if it may be written: an augmentable
    neighbour, or a lesson written tonight."""
    m = re.fullmatch(r"T(\d+)", ref.strip())
    if m:
        i = int(m.group(1)) - 1
        return written[i].rel if 0 <= i < len(written) and written[i].rel != "(dry run)" else None
    m = re.fullmatch(r"N(\d+)\.(\d+)", ref.strip()) or re.fullmatch(r"N(\d+)", ref.strip())
    if not m:
        return None
    if m.lastindex == 2:
        ci, ni = int(m.group(1)) - 1, int(m.group(2)) - 1
    else:
        try:
            ci = int(cand) - 1
        except (TypeError, ValueError):
            return None
        ni = int(m.group(1)) - 1
    if not (0 <= ci < len(neighbours)) or not (0 <= ni < len(neighbours[ci])):
        return None
    n = neighbours[ci][ni]
    return n.rel if n.augmentable else None


def _automemory_by_project(entries: list, vault_root) -> dict:
    import session_binding
    by: dict = {}
    for e in entries:
        project = session_binding.project_for_transcript_folder(str(e.get("folder") or ""), vault_root)
        by.setdefault(_bucket(project), []).append(e)
    return by


def _note_text(path: str) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""
    return f"({Path(path).name})\n" + _cut(text, AUTOMEMORY_NOTE_CHARS)


def compile_night(*, memory_root: Path, runner=subprocess_runner, settings: "Settings | None" = None,
                  only_project: "str | None" = None, dry_run: bool = False,
                  now: "datetime | None" = None) -> dict:
    """Compile every project with something new. A project whose compile fails
    keeps its pending slices for the next night."""
    import session_binding
    now = now or datetime.now(timezone.utc)
    settings = settings or Settings.from_config(load_config())
    vault_root = session_binding._vault_root(memory_root) or Path(memory_root)
    day = now.astimezone().date().isoformat()
    night = Night(settings, runner, Path(memory_root), Path(vault_root), day, dry_run)
    spend = _read_json(spend_path(), {})
    spend = spend if isinstance(spend, dict) and spend.get("day") == day else {"day": day, "projects": {}}
    night.spent = sum(float(v) for v in spend["projects"].values())
    last = _read_json(state_dir() / "last-run.json", {})
    try:
        since = datetime.fromisoformat(last["at"])
    except (KeyError, TypeError, ValueError):
        since = now - timedelta(days=1)
    since = max(since, now - timedelta(days=3))
    days = {(since + timedelta(days=i)).astimezone().date().isoformat()
            for i in range((now - since).days + 2)}
    pending = {p.stem: _read_jsonl(p) for p in sorted(pending_dir().glob("*.jsonl"))}
    queue = _read_jsonl(automemory_queue())
    auto_by = _automemory_by_project(queue, vault_root)
    projects = sorted(set(pending) | set(auto_by))
    if only_project:
        projects = [p for p in projects if p == only_project]
    flash = ""
    agy_dry: list = []
    agy_digests = 0
    reports = []
    done_projects = set()
    leftover: dict = {}
    for project in projects:
        fresh = [s for s in pending.get(project, [])
                 if _age_days(s.get("collected"), now) <= PENDING_MAX_DAYS]
        slices = []
        for s in fresh:
            if s.get("host") != AGY:
                slices.append(s)
                continue
            if len(s.get("text") or "") < AGY_MIN_SLICE_CHARS:
                continue
            if agy_digests >= AGY_NIGHT_LIMIT:
                leftover.setdefault(project, []).append(s)
                continue
            agy_digests += 1
            flash = flash or settings.flash_model or newest_flash(runner)
            d = digest_agy(s.get("text") or "", model=flash, runner=runner)
            if d.error:
                # A digest that failed (agy's print timeout, a quota refusal) is
                # not a verdict on the session: the slice waits for the next
                # night, until PENDING_MAX_DAYS drops it.
                agy_dry.append({"session": s["session"], "project": project, "model": flash,
                                "digest": d.text, "error": d.error, "retry": True})
                leftover.setdefault(project, []).append(s)
                continue
            if d.text.strip() == "NOTHING DURABLE":
                agy_dry.append({"session": s["session"], "project": project, "model": flash,
                                "digest": d.text})
                continue
            if settings.agy_enabled:
                slices.append(dict(s, digest=d.text))
            else:
                agy_dry.append({"session": s["session"], "project": project, "model": flash,
                                "digest": d.text, "slice": s.get("text", "")[:4000]})
        automemory = [t for t in (_note_text(e["path"]) for e in auto_by.get(project, [])) if t]
        if not slices and not automemory:
            done_projects.add(project)
            continue
        history = commits_and_prs(vault_root, project, since, runner) if project != GENERAL else []
        traces = traces_for(memory_root, project, days)
        rep = compile_project(night, project, slices=slices, automemory=automemory, history=history,
                              traces=traces, project_spent=float(spend["projects"].get(project, 0.0)))
        spend["projects"][project] = round(float(spend["projects"].get(project, 0.0)) + rep["cost_usd"], 4)
        reports.append(rep)
        if not rep["error"] or rep["error"].startswith("filing"):
            done_projects.add(project)
    if not dry_run:
        for project in done_projects:
            _write_lines(pending_dir() / f"{project}.jsonl", leftover.get(project, []))
        keep = [e for proj, es in auto_by.items() if proj not in done_projects for e in es]
        _write_lines(automemory_queue(), keep)
        _write_json(spend_path(), spend)
        _write_json(state_dir() / "last-run.json", {"at": now.isoformat(timespec="seconds")})
        _append_jsonl(state_dir() / "runs.jsonl", {"at": now.isoformat(timespec="seconds"),
                                                   "projects": reports, "spent": round(night.spent, 4)})
    if agy_dry:
        out = state_dir() / "agy-dry" / f"{day}.jsonl"
        for rec in agy_dry:
            _append_jsonl(out, rec)
    return {"pass": "compile", "day": day, "dry_run": dry_run, "projects": reports,
            "agy_digests": len(agy_dry), "agy_enabled": settings.agy_enabled,
            "total_cost_usd": round(sum(r["cost_usd"] for r in reports), 4)}


def _write_lines(path: Path, recs: list) -> None:
    if not recs:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in recs), encoding="utf-8")


def _age_days(iso, now: datetime) -> float:
    try:
        then = datetime.fromisoformat(str(iso))
    except ValueError:
        return 0.0
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (now - then).total_seconds() / 86400


# ── the one-time auto-memory backfill ────────────────────────────────────────

def backfill_automemory(*, memory_root: Path, runner=subprocess_runner, claude_root: "Path | None" = None,
                        settings: "Settings | None" = None, only_project: "str | None" = None,
                        dry_run: bool = False, now: "datetime | None" = None) -> dict:
    """Fold the auto-memory notes that predate the hook into a short set per
    project, deduped against the vault like any night's lessons. Outside the
    daily cap: it has its own, the night cap, per run. A marker records each
    project folded, so a run the cap stops is finished by the next one and a
    folded project is never folded twice."""
    import session_binding
    now = now or datetime.now(timezone.utc)
    marker = state_dir() / "automemory-backfill.json"
    state = _read_json(marker, {})
    done = dict(state.get("done") or {}) if isinstance(state, dict) else {}
    settings = settings or Settings.from_config(load_config())
    vault_root = session_binding._vault_root(memory_root) or Path(memory_root)
    root = claude_root or claude_projects_root()
    by: dict = {}
    for p in sorted(root.glob("*/memory/*.md")):
        if p.name == "MEMORY.md":
            continue
        project = session_binding.project_for_transcript_folder(p.parent.parent.name, vault_root)
        by.setdefault(_bucket(project), []).append(str(p))
    if only_project:
        by = {k: v for k, v in by.items() if k == only_project}
    by = {k: v for k, v in by.items() if k not in done}
    if not by and not dry_run:
        return {"pass": "backfill", "skipped": "already run", "total_cost_usd": 0.0}
    night = Night(Settings(settings.model, settings.flash_model, settings.max_lessons,
                           settings.night_cap_usd, settings.night_cap_usd, False),
                  runner, Path(memory_root), Path(vault_root), now.astimezone().date().isoformat(), dry_run)
    reports = []
    for project, paths in sorted(by.items()):
        notes = [t for t in (_note_text(p) for p in paths) if t]
        rep = compile_project(night, project, slices=[], automemory=notes, history=[], traces=[])
        rep["notes"] = len(notes)
        reports.append(rep)
        if not rep["error"] or rep["error"].startswith("filing"):
            done[project] = now.isoformat(timespec="seconds")
    if not dry_run:
        runs = state.get("runs") if isinstance(state, dict) and isinstance(state.get("runs"), list) else []
        _write_json(marker, {"done": done,
                             "runs": runs + [{"at": now.isoformat(timespec="seconds"), "projects": reports}]})
    return {"pass": "backfill", "dry_run": dry_run, "projects": reports,
            "total_cost_usd": round(sum(r["cost_usd"] for r in reports), 4)}


# ── CLI ──────────────────────────────────────────────────────────────────────

def _memory_root(arg: "str | None") -> "Path | None":
    raw = arg or os.environ.get("MEMORY_ROOT") or os.environ.get("MEMORY_VAULT_PATH", "")
    return Path(raw).expanduser() if raw else None


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=("night", "collect", "compile", "note-automemory",
                                        "backfill-automemory"))
    ap.add_argument("--vault-path", help="memory root (else $MEMORY_ROOT)")
    ap.add_argument("--transcript", help="collect: stage this one transcript now")
    ap.add_argument("--project", help="compile only this project")
    ap.add_argument("--dry-run", action="store_true", help="read and decide, write nothing")
    args = ap.parse_args(argv)

    if args.command == "note-automemory":
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except ValueError:
            return 0
        try:
            note_automemory(payload)
        except OSError:
            pass
        return 0

    memory_root = _memory_root(args.vault_path)
    if memory_root is None or not memory_root.is_dir():
        print("[lesson-compile] no vault resolved: pass --vault-path or set MEMORY_ROOT", file=sys.stderr)
        print(json.dumps({"error": "no vault", "total_cost_usd": 0.0}))
        return 1
    out: dict = {}
    if args.command in ("collect", "night"):
        out["collect"] = collect(memory_root=memory_root, dry_run=args.dry_run,
                                 only=Path(args.transcript) if args.transcript else None)
    if args.command in ("compile", "night"):
        out.update(compile_night(memory_root=memory_root, only_project=args.project, dry_run=args.dry_run))
    if args.command == "backfill-automemory":
        out.update(backfill_automemory(memory_root=memory_root, only_project=args.project,
                                       dry_run=args.dry_run))
    out.setdefault("total_cost_usd", 0.0)
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
