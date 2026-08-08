#!/usr/bin/env python3
"""week1_retrieval_experiment.py — runs one arm of the week-1 retrieval experiment.

Implements `wiki/designs/agentm-rescope-week1-experiment.md`. Takes a gold set
and an arm, drives an agent over the vault with that arm's tools, and scores
what it answered.

    python3 scripts/health/week1_retrieval_experiment.py \
        --gold-set scripts/health/fixtures/week1-gold/gold-set.json --arm A

Both arms are the same driver — `claude -p`, at most six tool calls per
question, answering with the note paths it believes are correct or with "no
answer found". Only the toolset differs:

    Arm A   search_lexical                  FTS5 + BM25 over every .md file
    Arm B   search_lexical + search_vector   the same, plus brute-force cosine

The agent's answer is the ranking that gets scored. Not the tool output — the
question is whether an agent that iterates *arrives at* the right note, and a
tool result it read and correctly discarded is not a hit.

Scoring is `eval_v6_retrieval.score_at_k`, the same P@5/R@5 arithmetic the V6
harness uses, imported rather than re-implemented. That module's 22-query set is
deliberately not reused: those queries all target the harness's own design
documents, which is the system's memory about itself and not a sample of
anyone's recall needs.

Three things this runner refuses to take on trust
-------------------------------------------------
**The tool-call ceiling.** Enforced in `week1_search_daemon.py`, where calls
land. The runner additionally counts `tool_use` blocks in the driver transcript
and fails loud if the two counts disagree — a ceiling that is only ever checked
by the thing enforcing it is not checked.

**Context isolation.** The operator's `UserPromptSubmit` hook injects recalled
vault entries into every prompt. Left alone it would hand the driver the answer
before it searched, and the experiment would report near-perfect recall for both
arms. Suppressing it is `--settings '{"disableAllHooks":true}'`, which was
arrived at by measurement — `{"hooks":{}}` and per-event empty arrays both leave
the hooks running, while `CLAUDE_CONFIG_DIR` and `--bare` stop them but break
OAuth. Since a wrong choice here fails silently and inflates both arms, every
run is parsed for hook events regardless, and any hook that fires fails the run.

**The warm embedding model.** The daemon loads it once for the whole run and
probes it; see that module's docstring for why this is the one thing the
experiment cannot afford to get wrong.

Gold-set schema — a JSON list, or `{"entries": [...]}`, of:

    {"id": str, "question": str, "expected_note_paths": [str],
     "stratum": str, "source": "transcript" | "cold" | "authored"}

Paths are vault-relative POSIX. An empty `expected_note_paths` is the negative
stratum: the correct answer is that no note answers the question.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
for _p in (_HERE, _REPO / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import week1_corpus as corpus_mod  # noqa: E402
import week1_search_daemon as daemon_mod  # noqa: E402
from eval_v6_retrieval import score_at_k  # noqa: E402

VALID_SOURCES = {"transcript", "cold", "authored"}
SCORE_K = 5

# Tools the driver is permitted to touch. `ToolSearch` is Claude Code's own
# deferred-tool loader: MCP tools arrive deferred, so the agent must load the
# search tool's schema before it can call it. It is plumbing, not a search, and
# does not count against the budget.
_HARNESS_TOOLS = {"ToolSearch"}
_ARM_TOOLS = {"mcp__week1__search_lexical", "mcp__week1__search_vector"}

# Denied outright. `--disallowedTools` alone is NOT sufficient: it does not
# cover the deferred surface, and an agent told to try can reach `Monitor`
# through `ToolSearch` and run `grep -rl <vault>` against the corpus — verified
# by doing exactly that during this build. That single hole would have made Arm
# A measure grep instead of FTS5, which is the specific failure the MCP design
# was chosen to prevent. `permissions.deny` in `--settings` does close it.
#
# This list is prevention and will drift as Claude Code adds tools, so it is not
# what the experiment's validity rests on — `_UNEXPECTED_TOOL_USE` below audits
# the transcript for any tool outside the permitted set and fails the run. A
# denylist can be incomplete; the audit cannot be, because it checks what was
# actually called.
_DENIED_TOOLS = [
    "Bash", "Read", "Grep", "Glob", "Edit", "Write", "NotebookEdit",
    "Task", "Agent", "Workflow", "Skill", "Artifact", "SendUserFile",
    "Monitor", "TaskCreate", "TaskGet", "TaskList", "TaskOutput", "TaskStop",
    "TaskUpdate", "ScheduleWakeup", "CronCreate", "CronList", "CronDelete",
    "RemoteTrigger", "SendMessage", "PushNotification", "DesignSync",
    "EnterWorktree", "ExitWorktree", "EnterPlanMode", "ExitPlanMode",
    "WebSearch", "WebFetch", "ListMcpResourcesTool", "ReadMcpResourceTool",
    "ReadMcpResourceDirTool", "ReportFindings",
]
_ANSWER_RE = re.compile(r"^\s*ANSWER:\s*(.*)$", re.IGNORECASE | re.MULTILINE)
_NO_ANSWER = {"no answer found", "none", "no answer", "n/a", "-"}


# ---------------------------------------------------------------------------
# Vault + gold set
# ---------------------------------------------------------------------------

def resolve_vault(arg_vault_path=None):
    """Resolve the vault root. Never a literal — `harness_memory.vault_path()` reads
    `plugins.obsidian-vault.vault_path` from the on-host kernel config, and
    `$MEMORY_VAULT_PATH` overrides per-invocation. See AGENTS.md's vault-path
    convention for why a cached absolute path goes wrong silently.
    """
    if arg_vault_path:
        p = Path(arg_vault_path).expanduser()
    else:
        import harness_memory  # noqa: E402
        p = harness_memory.vault_path()
    if p is None or not Path(p).is_dir():
        raise SystemExit(
            "[week1] no reachable vault. Set plugins.obsidian-vault.vault_path via "
            "`agentm_config --vault-path`, export $MEMORY_VAULT_PATH, or pass "
            "--vault-path."
        )
    return Path(p)


def load_gold_set(path):
    """Load and validate a gold set. Raises SystemExit with every problem at once."""
    path = Path(path)
    if not path.exists():
        raise SystemExit(f"[week1] gold set not found: {path}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    entries = doc.get("entries") if isinstance(doc, dict) else doc
    if not isinstance(entries, list) or not entries:
        raise SystemExit(f"[week1] gold set has no entries: {path}")

    problems, seen = [], set()
    for i, e in enumerate(entries):
        where = f"entry {i} (id={e.get('id', '?')!r})"
        for field in ("id", "question", "stratum"):
            if not isinstance(e.get(field), str) or not e[field].strip():
                problems.append(f"{where}: missing or empty {field!r}")
        if e.get("id") in seen:
            problems.append(f"{where}: duplicate id")
        seen.add(e.get("id"))
        paths = e.get("expected_note_paths")
        if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
            problems.append(f"{where}: expected_note_paths must be a list of strings")
        src = e.get("source")
        if src not in VALID_SOURCES:
            problems.append(f"{where}: source {src!r} not in {sorted(VALID_SOURCES)}")
    if problems:
        raise SystemExit("[week1] gold set is invalid:\n  " + "\n  ".join(problems))
    return entries


def check_expected_paths_exist(entries, vault):
    """Return expected paths that are not in the vault.

    The same fail-loud `eval_v6_retrieval` learned to do: a gold set pointing at
    moved or deleted notes reports every affected question as a clean retrieval
    miss, with nothing to distinguish "search failed" from "the ground truth is
    gone". Negative-stratum entries have no expected paths and are exempt.
    """
    missing = set()
    for e in entries:
        for p in e["expected_note_paths"]:
            if not (vault / p).exists():
                missing.add(p)
    return sorted(missing)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def build_system_prompt(arm, call_budget):
    tools = ["`search_lexical` — keyword search. The note must contain words you search for."]
    if arm == "B":
        tools.append(
            "`search_vector` — semantic search. Finds notes about your idea even when "
            "they share no words with it. Phrase these as a full sentence."
        )
    tool_lines = "\n".join(f"- {t}" for t in tools)
    return f"""You are answering questions about a personal knowledge vault of Markdown notes. You cannot read the notes. You can only search.

Your tools:
{tool_lines}

You may make at most {call_budget} tool calls for this question. The limit is enforced — call {call_budget + 1} will be refused, so spend the budget deliberately. If the first search disappoints, reformulate and search again: try the vocabulary the note itself would likely use rather than the vocabulary of the question. Searching fewer times than the budget allows is fine when you already have the answer.

When you are done, end your reply with a single line in exactly this form:

ANSWER: path/to/note.md, path/to/other-note.md

List the note paths you believe answer the question, best first, at most 5. Use the paths exactly as the search results gave them. If you do not believe any note in the vault answers this question, end with exactly:

ANSWER: no answer found

Answering "no answer found" when nothing fits is correct and expected for some questions. A confident wrong path is worse than admitting the vault does not cover it."""


def parse_answer(text):
    """Pull note paths out of the driver's final message.

    Reads the last `ANSWER:` line. Strips backticks, quotes, list bullets, and a
    leading slash, so a model that dresses up its paths still scores.
    """
    matches = _ANSWER_RE.findall(text or "")
    if not matches:
        return None, []
    raw = matches[-1].strip()
    if raw.strip().lower().strip(".") in _NO_ANSWER:
        return "no_answer", []
    paths = []
    for part in re.split(r"[,\n]", raw):
        p = part.strip().strip("`'\"").strip().lstrip("-*• ").strip()
        if p.startswith("/"):
            p = p[1:]
        if p and p.lower() not in _NO_ANSWER:
            paths.append(p)
    return ("answer" if paths else "no_answer"), paths[:SCORE_K]


def run_driver_claude(question, arm, socket_path, call_budget, *, model, timeout,
                      config_dir, verbose=False):
    """Run one question through `claude -p`. Returns a dict of what happened."""
    allowed = ["mcp__week1__search_lexical"]
    if arm == "B":
        allowed.append("mcp__week1__search_vector")
    mcp_config = {
        "mcpServers": {
            "week1": {
                "command": sys.executable,
                "args": [str(_HERE / "week1_search_shim.py")],
                "env": {"WEEK1_SOCKET": str(socket_path)},
            }
        }
    }
    cmd = [
        "claude", "-p", question["question"],
        "--model", model,
        "--system-prompt", build_system_prompt(arm, call_budget),
        "--mcp-config", json.dumps(mcp_config),
        "--strict-mcp-config",
        "--allowedTools", *allowed,
        # The operator's real ~/.claude carries a UserPromptSubmit hook that
        # injects recalled vault entries into every prompt — it would hand the
        # driver its answers before it searched. Suppressing it took measuring
        # rather than guessing, because three plausible approaches fail, two of
        # them silently:
        #   --settings '{"hooks":{}}'          hooks still fire (settings merge)
        #   --settings '{"hooks":{"X":[]}}'    hooks still fire (arrays concat)
        #   CLAUDE_CONFIG_DIR=<scratch>        hooks off, but OAuth breaks
        #   --bare / CLAUDE_CODE_SIMPLE=1      hooks off, but OAuth breaks
        # Only this one gives both. The two properties are otherwise coupled:
        # every mode that drops the hook config also refuses keychain OAuth.
        "--settings", json.dumps({
            "disableAllHooks": True,
            "permissions": {"deny": _DENIED_TOOLS},
        }),
        "--output-format", "stream-json", "--verbose", "--include-hook-events",
        "--no-session-persistence", "--disable-slash-commands",
    ]
    env = dict(os.environ)
    env.pop("MEMORY_VAULT_PATH", None)

    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, env=env,
            stdin=subprocess.DEVNULL, cwd=str(config_dir),
        )
    except subprocess.TimeoutExpired:
        return {"text": "", "hooks_fired": [], "transcript_tool_calls": 0,
                "unexpected_tools": [], "refused_tool_attempts": [],
                "error": f"driver timed out after {timeout}s",
                "wall_s": round(time.monotonic() - started, 1)}

    text, hooks, err = "", [], None
    tools_used, cost_usd = [], 0.0
    tool_names_by_id, failed_tool_ids = {}, set()
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("subtype") == "hook_started":
            hooks.append(ev.get("hook_name"))
        elif ev.get("type") == "assistant":
            for block in (ev.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use":
                    tools_used.append(block.get("name"))
                    tool_names_by_id[block.get("id")] = block.get("name")
        elif ev.get("type") == "user":
            # Whether each call actually returned anything. A tool the model
            # invented does not exist to be called, so it comes back an error
            # and reaches nothing.
            for block in (ev.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_result" and block.get("is_error"):
                    failed_tool_ids.add(block.get("tool_use_id"))
        elif ev.get("type") == "result":
            if ev.get("is_error") or ev.get("subtype") != "success":
                err = str(ev.get("result") or ev.get("subtype") or "driver error")
            text = ev.get("result") or text
            cost_usd = float(ev.get("total_cost_usd") or 0.0)
    if not text and proc.returncode != 0:
        err = err or (proc.stderr.strip().splitlines() or ["driver failed"])[-1]
    if verbose and err:
        print(f"[week1] driver error on {question['id']}: {err}", file=sys.stderr)
    # An escape is a *successful* call to something outside the permitted set —
    # a way the driver actually reached the vault other than its arm's tool.
    # A call that came back an error reached nothing, and the common case is the
    # model inventing a plausible tool name (`mcp__week1__search_vault` turned up
    # once in a 60-question run). Flagging those would fail a sound run on a
    # typo; ignoring a *successful* one would pass a contaminated run, so the
    # error state is what separates them. Hallucinated names are still recorded,
    # just not as escapes.
    outside = [(tid, name) for tid, name in tool_names_by_id.items()
               if name not in _ARM_TOOLS and name not in _HARNESS_TOOLS]
    return {
        "text": text, "hooks_fired": hooks, "error": err,
        "transcript_tool_calls": sum(1 for t in tools_used if t in _ARM_TOOLS),
        "cost_usd": round(cost_usd, 4),
        "unexpected_tools": sorted(
            {name for tid, name in outside if tid not in failed_tool_ids}),
        "refused_tool_attempts": sorted(
            {name for tid, name in outside if tid in failed_tool_ids}),
        "wall_s": round(time.monotonic() - started, 1),
    }


def run_driver_mock(question, arm, socket_path, call_budget, *, mock_calls, **_):
    """A deterministic stand-in for `claude -p`, for smoke-testing without an API key.

    Exercises the real path everything below the model depends on: it calls the
    real tools through the real daemon, overruns the budget on purpose so the
    ceiling is proven on every question, and answers with the paths the searches
    returned. It does not iterate or reformulate — that is the model's job, and
    mocking it would be mocking the thing the experiment exists to measure. What
    this proves is the harness, not the result.
    """
    tools = [daemon_mod.TOOL_LEXICAL] + ([daemon_mod.TOOL_VECTOR] if arm == "B" else [])
    seen, refusals, calls = [], 0, 0
    for i in range(mock_calls):
        tool = tools[i % len(tools)]
        # Vary the query per round the way a reformulating agent would, so the
        # daemon sees distinct queries rather than one repeated string.
        words = question["question"].split()
        q = question["question"] if i == 0 else " ".join(words[i % max(len(words), 1):]) or words[0]
        resp = daemon_mod.request(socket_path, {"op": "search", "tool": tool, "query": q, "k": 5})
        calls += 1
        if not resp.get("ok"):
            if resp.get("error") == "budget_exhausted":
                refusals += 1
                continue
            return {"text": "", "hooks_fired": [], "transcript_tool_calls": calls,
                    "unexpected_tools": [], "refused_tool_attempts": [],
                    "error": f"mock search failed: {resp.get('error')}", "wall_s": 0.0}
        for r in resp.get("results") or []:
            if r["path"] not in seen:
                seen.append(r["path"])
    answer = ", ".join(seen[:SCORE_K]) if seen else "no answer found"
    return {"text": f"ANSWER: {answer}", "hooks_fired": [], "unexpected_tools": [], "refused_tool_attempts": [],
            "transcript_tool_calls": calls, "error": None, "wall_s": 0.0,
            "mock_budget_refusals": refusals}


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _blank_bucket():
    return {"n": 0, "p_at_5_sum": 0.0, "r_at_5_sum": 0.0, "correct": 0,
            "tool_calls_sum": 0, "n_negative": 0, "correct_rejections": 0}


def _accumulate(bucket, row):
    bucket["n"] += 1
    bucket["p_at_5_sum"] += row["p_at_5"]
    bucket["r_at_5_sum"] += row["r_at_5"]
    bucket["correct"] += 1 if row["correct"] else 0
    bucket["tool_calls_sum"] += row["tool_calls"]
    if row["is_negative"]:
        bucket["n_negative"] += 1
        bucket["correct_rejections"] += 1 if row["correct"] else 0


def _finalize(bucket):
    n = bucket["n"] or 1
    out = {
        "n": bucket["n"],
        "p_at_5": round(bucket["p_at_5_sum"] / n, 4),
        "r_at_5": round(bucket["r_at_5_sum"] / n, 4),
        "accuracy": round(bucket["correct"] / n, 4),
        "mean_tool_calls": round(bucket["tool_calls_sum"] / n, 2),
    }
    if bucket["n_negative"]:
        out["correct_rejection_rate"] = round(
            bucket["correct_rejections"] / bucket["n_negative"], 4)
    return out


def _surface_stats(rows):
    """How much of what the agent actually read was flagged junk.

    The scorecard's P@5/R@5 score the agent's *answer*, which is the right thing
    to decide the design on and the wrong thing to diagnose a ranking change
    with — an agent can read five fragments, discard all five, and still answer
    correctly on the sixth call. This counts the results the tools returned, so
    "the penalty cleared the reading surface" and "the penalty changed the
    answer" are two separate claims with two separate numbers behind them.
    """
    served = flagged = 0
    by_class = {}
    for r in rows:
        for call in r.get("tool_call_log") or []:
            for pen in call.get("result_penalties") or []:
                served += 1
                if pen:
                    flagged += 1
                    for c in pen.split(","):
                        by_class[c] = by_class.get(c, 0) + 1
    return {
        "results_served": served,
        "flagged_results_served": flagged,
        "flagged_share": round(flagged / served, 4) if served else None,
        "flagged_by_class": dict(sorted(by_class.items())),
    }


def render_table(report):
    """A readable scorecard: overall, per stratum, then every question that missed."""
    lines = []
    o = report["overall"]
    lines.append(
        f"Arm {report['arm']}  ·  {report['n_questions']} questions  ·  "
        f"{report['corpus']['n_docs']} notes indexed  ·  driver={report['driver']}"
        + (f"/{report['model']}" if report.get("model") else "")
        + (f"  ·  ${report['cost_usd_total']:.2f}  ·  {report['wall_s_total'] / 60:.1f} min"
           if report.get("cost_usd_total") else "")
    )
    lines.append(
        f"lexical={report.get('lexical_variant', 'baseline')}"
        f"  ·  query-mode={report.get('query_mode', 'as-is')}  ·  penalty="
        + (json.dumps(report["penalty"], sort_keys=True) if report.get("penalty") else "off")
    )
    surface = report.get("surface") or {}
    if surface.get("results_served"):
        lines.append(
            f"reading surface: {surface['flagged_results_served']}/"
            f"{surface['results_served']} results served were flagged "
            f"({surface['flagged_share']:.1%})"
            + (f"  {surface['flagged_by_class']}" if surface.get("flagged_by_class") else "")
        )
    lines.append("")
    header = f"{'stratum':<22} {'n':>4} {'P@5':>7} {'R@5':>7} {'acc':>7} {'calls':>7}"
    lines.append(header)
    lines.append("-" * len(header))
    for stratum in sorted(report["per_stratum"]):
        s = report["per_stratum"][stratum]
        lines.append(
            f"{stratum:<22} {s['n']:>4} {s['p_at_5']:>7.3f} {s['r_at_5']:>7.3f} "
            f"{s['accuracy']:>7.3f} {s['mean_tool_calls']:>7.2f}"
        )
    lines.append("-" * len(header))
    lines.append(
        f"{'OVERALL':<22} {o['n']:>4} {o['p_at_5']:>7.3f} {o['r_at_5']:>7.3f} "
        f"{o['accuracy']:>7.3f} {o['mean_tool_calls']:>7.2f}"
    )

    misses = report["misses"]
    lines.append("")
    lines.append(f"MISSES ({len(misses)} of {report['n_questions']})")
    if not misses:
        lines.append("  none")
    for m in misses:
        lines.append(f"  [{m['stratum']}] {m['id']}  ({m['tool_calls']} calls)")
        lines.append(f"      Q: {m['question']}")
        lines.append(f"      expected: {', '.join(m['expected']) or '(no note — negative)'}")
        lines.append(f"      answered: {', '.join(m['answered']) or '(no answer found)'}")
        lines.append(f"      why: {m['reason']}")
    return "\n".join(lines)


def judge_correct(score, answer_kind, driver_error):
    """Did this question come out right? Returns `(correct, p_at_5, r_at_5)`.

    For an ordinary question, right means at least one expected note made the
    answer.

    For a negative question — where the gold set asserts no note answers it —
    right means the driver *concluded* that nothing fits. Silence is not a
    correct rejection. A timeout, a crash, and a reply that trailed off without
    an ANSWER line all produce an empty path list, identical in shape to a
    deliberate "no answer found". Crediting those would let driver failures
    inflate the one stratum whose right answer is nothing, so an arm would score
    best on it exactly when it was working least.
    """
    if not score["is_negative"]:
        return bool(score["hits"]), score["p_at_k"], score["r_at_k"]
    correct = bool(score["correct_rejection"]) and answer_kind == "no_answer" \
        and not driver_error
    return correct, (1.0 if correct else 0.0), (1.0 if correct else 0.0)


def _miss_reason(row):
    if row["driver_error"]:
        return f"driver error — {row['driver_error']}"
    if row.get("never_concluded"):
        return "never reached an ANSWER line — no conclusion to score"
    if row["is_negative"]:
        return "should have found nothing, but named a note"
    if not row["answered"]:
        return "gave up — said no answer found, but a note does answer it"
    return "answered with the wrong note(s)"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _make_run_dir(arm):
    """A scratch dir short enough to hold a Unix socket path.

    `sockaddr_un.sun_path` is 104 bytes on macOS, and it is the *absolute* path
    that has to fit. `--work-dir` is chosen for where indexes should live and can
    easily be longer than that on its own, so the socket does not live there.
    Tries the platform temp dir, then `/tmp`, and says which limit it hit rather
    than failing later inside `bind()` with a bare errno.
    """
    for base in (tempfile.gettempdir(), "/tmp"):
        d = Path(tempfile.mkdtemp(prefix=f"w1{arm}-", dir=base))
        if len(str(d / "search.sock").encode()) <= 100:
            return d
        shutil.rmtree(d, ignore_errors=True)
    raise SystemExit(
        "[week1] cannot place a Unix socket: every candidate temp directory "
        "produces a path over the 104-byte AF_UNIX limit. Set $TMPDIR to "
        "something short."
    )


def run_arm(entries, vault, arm, *, work_dir, call_budget, driver, model, timeout,
            exclude_dirs, embed_mode, mock_calls, verbose=True,
            lexical_variant=corpus_mod.DEFAULT_VARIANT, penalty=None,
            query_mode="as-is"):
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    run_dir = _make_run_dir(arm)
    socket_path = run_dir / "search.sock"
    ready_path = run_dir / "ready"
    config_dir = run_dir / "claude-config"
    config_dir.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(
        [sys.executable, str(_HERE / "week1_search_daemon.py"),
         "--vault-path", str(vault), "--work-dir", str(work_dir),
         "--socket", str(socket_path), "--ready-file", str(ready_path),
         "--arm", arm, "--call-budget", str(call_budget),
         "--embed-mode", embed_mode, "--variant", lexical_variant,
         "--query-mode", query_mode]
        + (["--penalty", json.dumps(penalty)] if penalty else [])
        + [a for d in exclude_dirs for a in ("--exclude-dir", d)],
        stdout=None, stderr=None,
    )
    try:
        # Index build + a cold embedding load is minutes on a first run, so this
        # waits generously and fails only when the daemon has actually died.
        deadline = time.monotonic() + 3600
        while not ready_path.exists():
            if proc.poll() is not None:
                raise SystemExit(f"[week1] daemon exited early (code {proc.returncode})")
            if time.monotonic() > deadline:
                raise SystemExit("[week1] daemon did not become ready within an hour")
            time.sleep(0.25)

        info = daemon_mod.request(socket_path, {"op": "ping"})
        if not info.get("ok"):
            raise SystemExit(f"[week1] daemon ping failed: {info}")
        # The daemon is the authority on what it actually built. Trusting the
        # flag we passed would let a scorecard claim a variant it never ran —
        # the same class of error as a mirror test, and the reason every
        # integrity check here reads back rather than assumes.
        if (info.get("variant") != lexical_variant
                or (info.get("penalty") or None) != (penalty or None)
                or info.get("query_mode") != query_mode):
            raise SystemExit(
                f"[week1] the daemon is serving variant={info.get('variant')!r} "
                f"query_mode={info.get('query_mode')!r} penalty={info.get('penalty')!r}, "
                f"but this run was asked for variant={lexical_variant!r} "
                f"query_mode={query_mode!r} penalty={penalty!r}. Refusing to label a "
                f"scorecard with a configuration it did not run under.")
        if verbose:
            print(f"[week1] arm {arm} ready — tools {info['tools']}, "
                  f"{info['n_docs']} notes, budget {info['call_budget']}, "
                  f"variant {info.get('variant')}, "
                  f"query-mode {info.get('query_mode')}, "
                  f"{info.get('n_flagged', 0)} flagged notes, penalty "
                  f"{info.get('penalty') or 'off'}", file=sys.stderr)

        rows, hook_violations, count_mismatches, tool_escapes = [], [], [], []
        refused_attempts = []
        for i, e in enumerate(entries, 1):
            daemon_mod.request(socket_path, {"op": "begin", "question_id": e["id"], "arm": arm})
            if driver == "mock":
                res = run_driver_mock(e, arm, socket_path, call_budget, mock_calls=mock_calls)
            else:
                res = run_driver_claude(e, arm, socket_path, call_budget, model=model,
                                        timeout=timeout, config_dir=config_dir,
                                        verbose=verbose)
            stats = daemon_mod.request(socket_path, {"op": "stats"})
            tool_calls = stats.get("calls_used", 0) if stats.get("ok") else 0

            if res["hooks_fired"]:
                hook_violations.append((e["id"], sorted(set(res["hooks_fired"]))))
            if res.get("unexpected_tools"):
                tool_escapes.append((e["id"], res["unexpected_tools"]))
            if res.get("refused_tool_attempts"):
                refused_attempts.append((e["id"], res["refused_tool_attempts"]))
            # The daemon counts calls it served; the transcript counts calls the
            # model made. They agree unless something is calling the tools that
            # is not the driver, or the driver is calling tools we did not give it.
            if driver != "mock" and res["transcript_tool_calls"] != tool_calls:
                count_mismatches.append(
                    (e["id"], res["transcript_tool_calls"], tool_calls))

            kind, answered = parse_answer(res["text"])
            score = score_at_k(e["expected_note_paths"], answered, k=SCORE_K)
            correct, p_at_5, r_at_5 = judge_correct(score, kind, res["error"])
            row = {
                "id": e["id"], "stratum": e["stratum"], "source": e["source"],
                "question": e["question"], "expected": e["expected_note_paths"],
                "answered": answered, "said_no_answer": kind == "no_answer",
                "never_concluded": kind is None,
                # Rounded like the aggregates. Full precision buys nothing on a
                # ratio of small integers, and 1/3 written out in full contains
                # a ten-digit run that the PII scanner reads as a phone number.
                "hits": score["hits"],
                "p_at_5": round(p_at_5, 4), "r_at_5": round(r_at_5, 4),
                "first_hit_rank": score["first_hit_rank"], "is_negative": score["is_negative"],
                "correct": bool(correct), "tool_calls": tool_calls,
                "tool_call_log": stats.get("call_log", []) if stats.get("ok") else [],
                # Attempts past the ceiling are not a failure — they are the
                # gate doing its job, and worth recording because "how often did
                # the agent want a seventh call" is a real finding about whether
                # six is the right number.
                "refused_over_budget": max(0, res["transcript_tool_calls"] - call_budget),
                "driver_error": res["error"], "wall_s": res["wall_s"],
                "cost_usd": res.get("cost_usd", 0.0),
            }
            rows.append(row)
            if verbose:
                mark = "OK  " if correct else "MISS"
                print(f"[week1] {i}/{len(entries)} {mark} {e['id']:<16} "
                      f"{tool_calls} calls, {res['wall_s']}s", file=sys.stderr)
    finally:
        try:
            daemon_mod.request(socket_path, {"op": "shutdown"}, timeout=10)
        except Exception:
            proc.kill()
        proc.wait(timeout=30)
        shutil.rmtree(run_dir, ignore_errors=True)

    overall, per_stratum = _blank_bucket(), {}
    for row in rows:
        _accumulate(overall, row)
        _accumulate(per_stratum.setdefault(row["stratum"], _blank_bucket()), row)

    return {
        "arm": arm, "driver": driver,
        "model": model if driver == "claude" else None,
        # The vault's name, never its absolute path. Reports are committed as
        # the experiment's durable scorecard, and an absolute path here encodes
        # a username and a Google-Drive mount id — the exact literal AGENTS.md's
        # vault-path convention forbids, and which `check-no-hardcoded-vault-path`
        # fails the build over. The full path still goes to the daemon's stderr
        # at startup, where it helps and is not committed.
        "vault_name": vault.name,
        "corpus": {"n_docs": info["n_docs"], "excluded_dirs": exclude_dirs,
                   "embed_mode": embed_mode if arm == "B" else None},
        # Read back off the daemon, not echoed from the arguments — see the
        # ready-check above for why.
        "lexical_variant": info.get("variant"),
        "query_mode": info.get("query_mode"),
        "penalty": info.get("penalty"),
        "n_flagged_notes": info.get("n_flagged"),
        "surface": _surface_stats(rows),
        "call_budget": call_budget,
        "n_questions": len(rows),
        "cost_usd_total": round(sum(r.get("cost_usd", 0.0) for r in rows), 4),
        "wall_s_total": round(sum(r["wall_s"] for r in rows), 1),
        "overall": _finalize(overall),
        "per_stratum": {k: _finalize(v) for k, v in per_stratum.items()},
        "per_question": rows,
        "misses": [
            {"id": r["id"], "stratum": r["stratum"], "question": r["question"],
             "expected": r["expected"], "answered": r["answered"],
             "tool_calls": r["tool_calls"], "reason": _miss_reason(r)}
            for r in rows if not r["correct"]
        ],
        "integrity": {
            "hook_violations": [{"id": i, "hooks": h} for i, h in hook_violations],
            "tool_escapes": [{"id": i, "tools": t} for i, t in tool_escapes],
            # Recorded, not fatal: calls to tools that do not exist or were
            # denied. They reached nothing.
            "refused_tool_attempts": [
                {"id": i, "tools": t} for i, t in refused_attempts],
            "tool_call_count_mismatches": [
                {"id": i, "transcript": t, "daemon": d} for i, t, d in count_mismatches],
            # A genuine failure: the daemon SERVED more calls than the ceiling
            # allows, meaning the gate leaked. Distinct from an agent asking for
            # a seventh call and being refused, which is the gate working.
            "budget_leaks": [r["id"] for r in rows if r["tool_calls"] > call_budget],
            "questions_that_wanted_more_calls": sum(
                1 for r in rows if r["refused_over_budget"] > 0),
            "driver_errors": [r["id"] for r in rows if r["driver_error"]],
        },
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Run one arm of the week-1 retrieval experiment.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--gold-set", required=True, help="path to the gold-set JSON")
    ap.add_argument("--arm", required=True, choices=("A", "B"))
    ap.add_argument("--vault-path", default=None,
                    help="override; normally resolved from the kernel config")
    ap.add_argument("--work-dir", default=str(Path.home() / ".agentm" / "week1-experiment"),
                    help="where indexes and embedding caches live between runs")
    ap.add_argument("--out", default=None, help="write the JSON report here")
    ap.add_argument("--driver", default="claude", choices=("claude", "mock"),
                    help="mock exercises the harness end-to-end without an API key")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--call-budget", type=int, default=daemon_mod.DEFAULT_CALL_BUDGET)
    ap.add_argument("--timeout", type=float, default=300.0, help="per-question, seconds")
    ap.add_argument("--exclude-dir", action="append", default=[],
                    help="skip a directory name anywhere in the vault "
                         "(e.g. --exclude-dir _dream-staging)")
    ap.add_argument("--embed-mode", default="local", choices=("local", "stub"),
                    help="stub is a hash-based fake for tests; never a real result")
    ap.add_argument("--mock-calls", type=int, default=daemon_mod.DEFAULT_CALL_BUDGET + 1,
                    help="mock driver only; defaults to one past the budget so the "
                         "ceiling is exercised on every question")
    ap.add_argument("--limit", type=int, default=None, help="first N questions only")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument(
        "--lexical-variant", default=corpus_mod.DEFAULT_VARIANT,
        choices=sorted(corpus_mod.LEXICAL_VARIANTS),
        help="which FTS5 schema the lexical tool queries (default: baseline, "
             "the schema the 2026-08-06 run used)")
    ap.add_argument(
        "--query-mode", default="as-is", choices=corpus_mod.QUERY_MODES,
        help="'as-is' hands the agent's query straight to FTS5, which ANDs its "
             "terms — the 2026-08-06 behaviour; 'or' OR-joins them, phrases intact")
    ap.add_argument(
        "--penalty", default=None,
        help='rank-penalty weights as JSON, or a preset name. "default" is the '
             'recommended shape, which spares notes filing already promoted; '
             '"as-measured" is what the 2026-08-07 replicates ran, which does '
             'not. Omit for no penalty.')
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    if args.penalty == "default":
        penalty = dict(corpus_mod.DEFAULT_PENALTY_WEIGHTS)
    elif args.penalty == "as-measured":
        penalty = dict(corpus_mod.AS_MEASURED_PENALTY_WEIGHTS)
    elif args.penalty:
        penalty = json.loads(args.penalty)
        unknown = set(penalty) - corpus_mod.PENALTY_CLASSES
        if unknown:
            raise SystemExit(
                f"[week1] unknown penalty class(es) {sorted(unknown)}; known classes "
                f"are {sorted(corpus_mod.PENALTY_CLASSES)}. A typo here would "
                f"silently run an unpenalized arm and report it as penalized.")
        if any(not (0 < float(v) <= 1) for v in penalty.values()):
            raise SystemExit(
                "[week1] penalty weights must be in (0, 1]. A weight of 0 is "
                "exclusion wearing a demotion's clothes, and this experiment "
                "exists partly because exclusion cost four months of dead recall.")
    else:
        penalty = None

    vault = resolve_vault(args.vault_path)
    entries = load_gold_set(args.gold_set)
    if args.limit:
        entries = entries[:args.limit]

    missing = check_expected_paths_exist(entries, vault)
    if missing:
        print(
            f"[week1] ERROR: {len(missing)} expected note path(s) in "
            f"{Path(args.gold_set).name} do not exist in the vault. Every question "
            f"pointing at one would score as a retrieval miss that is really a "
            f"labeling error, and the arms could not be compared honestly. "
            f"Missing:\n  " + "\n  ".join(missing),
            file=sys.stderr)
        return 2

    if args.arm == "B" and args.embed_mode == "stub":
        print("[week1] NOTE: --embed-mode stub — the vector arm is hash noise, not "
              "semantics. Smoke-test only; never a reportable result.", file=sys.stderr)

    report = run_arm(
        entries, vault, args.arm, work_dir=args.work_dir, call_budget=args.call_budget,
        driver=args.driver, model=args.model, timeout=args.timeout,
        exclude_dirs=args.exclude_dir, embed_mode=args.embed_mode,
        mock_calls=args.mock_calls, verbose=not args.quiet,
        lexical_variant=args.lexical_variant, penalty=penalty,
        query_mode=args.query_mode,
    )
    # Repo-relative when it resolves inside the repo, else just the filename —
    # committed reports must not carry `/Users/<name>/...` (the PII gate blocks
    # the push; it did, on the first Fable scorecards).
    _gs = Path(args.gold_set).resolve()
    try:
        report["gold_set"] = str(_gs.relative_to(_REPO))
    except ValueError:
        report["gold_set"] = _gs.name

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"[week1] report -> {args.out}", file=sys.stderr)
    else:
        print(json.dumps(report, indent=2))

    print("\n" + render_table(report), file=sys.stderr)

    integrity = report["integrity"]
    if integrity["hook_violations"]:
        print(
            f"\n[week1] ERROR: hooks fired during {len(integrity['hook_violations'])} "
            f"question(s). The memory-recall hook injects vault content into the "
            f"prompt, so the driver may have been handed answers it never searched "
            f"for. These scores do not mean what they appear to mean. "
            f"Hooks: {integrity['hook_violations'][0]['hooks']}", file=sys.stderr)
        return 3
    if integrity["tool_escapes"]:
        print(
            f"\n[week1] ERROR: the driver used {len(integrity['tool_escapes'])} "
            f"tool(s) outside this arm's permitted set — it had a way to reach the "
            f"vault that is not the arm's search tool, so these scores do not "
            f"measure the arm. Escapes: {integrity['tool_escapes'][:3]}",
            file=sys.stderr)
        return 6
    if integrity["tool_call_count_mismatches"]:
        print(f"\n[week1] ERROR: the daemon and the driver transcript disagree on how "
              f"many tool calls happened for "
              f"{len(integrity['tool_call_count_mismatches'])} question(s): "
              f"{integrity['tool_call_count_mismatches'][:3]}", file=sys.stderr)
        return 4
    if integrity["budget_leaks"]:
        print(f"\n[week1] ERROR: the daemon served more than {report['call_budget']} "
              f"calls on {len(integrity['budget_leaks'])} question(s) — the ceiling "
              f"leaked, so those questions were not run under the same constraint as "
              f"the rest: {integrity['budget_leaks'][:5]}", file=sys.stderr)
        return 5
    if integrity["driver_errors"]:
        print(f"\n[week1] WARNING: the driver errored on "
              f"{len(integrity['driver_errors'])} question(s): "
              f"{integrity['driver_errors'][:5]} — scored as misses.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
