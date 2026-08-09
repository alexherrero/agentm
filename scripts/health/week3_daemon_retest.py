#!/usr/bin/env python3
"""week3_daemon_retest.py — the week-1 gold set, run against the Go daemon.

Standing-scorecard run #2. Same 60 questions, same 6-call budget, same driver
protocol and the same integrity checks as the 2026-08-06 and 2026-08-07 runs.
Two things change, and only two:

**The surface.** Week 1 measured a Python FTS5 layer built for the experiment.
This measures `agentmd`'s `memory_search` over HTTP — the tool a real session
calls, with the daemon's own ranking, its own penalty constants, and its own
tool description. Nothing here re-implements search; a search this runner
cannot reach through the daemon is a search the operator cannot reach either.

**The variable.** One daemon per corpus copy, the two copies differing by
exactly the alias backfill and nothing else. AL carries the 1,930 backfilled
`aliases` lines; NO has them reverted from the write journal.

Everything else is deliberately unchanged, because a comparison against week 1
is only readable if the protocol is. Scoring is `eval_v6_retrieval.score_at_k`,
aggregation and the miss taxonomy are imported from
`week1_retrieval_experiment` rather than rewritten.

    python3 scripts/health/week3_daemon_retest.py \
        --gold-set scripts/health/fixtures/week1-gold/gold-set.json \
        --daemon-url http://127.0.0.1:51468/mcp --label al-opus-r1 \
        --model opus --out scripts/health/results/week3-retest/al-opus-r1.json

What this refuses to take on trust, unchanged from week 1
---------------------------------------------------------
**The tool-call ceiling.** Enforced in the shim, where calls land, and
cross-checked against the count of `tool_use` blocks in the driver transcript.
A ceiling only ever checked by the thing enforcing it is not checked.

**Context isolation.** The operator's `UserPromptSubmit` hook injects recalled
vault entries into every prompt and would hand the driver its answers before it
searched. `--settings '{"disableAllHooks":true}'` is the one suppression that
does not also break OAuth; every run is parsed for hook events regardless, and
any hook that fires fails the run.

**The tool denylist is not what validity rests on.** `--disallowedTools` does
not cover Claude Code's deferred surface — a driver can reach `Monitor` through
`ToolSearch` and grep the corpus directly. `permissions.deny` closes that, and
the transcript audit catches whatever the denylist misses, because it checks
what was actually called.

**Which corpus answered.** The daemon is asked, over `/status`, what vault it
is serving and how many documents it indexed, and a run whose daemon does not
match the copy this scorecard claims is refused rather than mislabelled.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
for _p in (_HERE, _REPO / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import week1_retrieval_experiment as w1  # noqa: E402
from eval_v6_retrieval import score_at_k  # noqa: E402

SCORE_K = w1.SCORE_K
SEARCH_TOOL = "mcp__agentm__memory_search"
_ARM_TOOLS = {SEARCH_TOOL}
_HARNESS_TOOLS = w1._HARNESS_TOOLS
DEFAULT_CALL_BUDGET = 6


def build_system_prompt(call_budget):
    """Week 1's prompt, with the daemon's tool in place of the experiment's.

    Deliberately not an improved prompt. The daemon's own tool description
    already carries the how-to-search guidance — that description is part of
    the surface under test — and rewriting the driver's instructions at the
    same time as the corpus changes would confound the one variable this run
    exists to isolate.
    """
    return f"""You are answering questions about a personal knowledge vault of Markdown notes. You cannot read the notes. You can only search.

Your tools:
- `memory_search` — search the vault. Read its description for how it ranks and what it expects.

You may make at most {call_budget} tool calls for this question. The limit is enforced — call {call_budget + 1} will be refused, so spend the budget deliberately. If the first search disappoints, reformulate and search again: try the vocabulary the note itself would likely use rather than the vocabulary of the question. Searching fewer times than the budget allows is fine when you already have the answer.

When you are done, end your reply with a single line in exactly this form:

ANSWER: path/to/note.md, path/to/other-note.md

List the note paths you believe answer the question, best first, at most 5. Use the paths exactly as the search results gave them. If you do not believe any note in the vault answers this question, end with exactly:

ANSWER: no answer found

Answering "no answer found" when nothing fits is correct and expected for some questions. A confident wrong path is worse than admitting the vault does not cover it."""


def daemon_status(daemon_url, timeout=15.0):
    """Ask the daemon what it is serving. Never inferred from the arguments."""
    status_url = daemon_url.rsplit("/mcp", 1)[0] + "/status"
    with urllib.request.urlopen(status_url, timeout=timeout) as resp:
        status = json.loads(resp.read().decode("utf-8"))
    # The version lives on the MCP handshake rather than /status, and a scorecard
    # that cannot say which build answered it is a scorecard that cannot be
    # re-run against the same one.
    req = urllib.request.Request(
        daemon_url,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {}}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        info = json.loads(resp.read().decode("utf-8"))
    status["version"] = ((info.get("result") or {}).get("serverInfo") or {}).get("version")
    return status


def run_driver(question, daemon_url, call_budget, *, model, timeout, config_dir,
               call_log_path, verbose=False):
    """One question through `claude -p`, talking to the daemon through the shim."""
    mcp_config = {
        "mcpServers": {
            "agentm": {
                "command": sys.executable,
                "args": [str(_HERE / "week3_daemon_shim.py")],
                "env": {
                    "WEEK3_DAEMON_URL": daemon_url,
                    "WEEK3_CALL_LOG": str(call_log_path),
                    "WEEK3_CALL_BUDGET": str(call_budget),
                },
            }
        }
    }
    cmd = [
        "claude", "-p", question["question"],
        "--model", model,
        "--system-prompt", build_system_prompt(call_budget),
        "--mcp-config", json.dumps(mcp_config),
        "--strict-mcp-config",
        "--allowedTools", SEARCH_TOOL,
        "--settings", json.dumps({
            "disableAllHooks": True,
            "permissions": {"deny": w1._DENIED_TOOLS},
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
                "unexpected_tools": [], "refused_tool_attempts": [], "cost_usd": 0.0,
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
        print(f"[week3] driver error on {question['id']}: {err}", file=sys.stderr)
    # An escape is a *successful* call to something outside the permitted set.
    # A call that came back an error reached nothing — the common case being the
    # model inventing a plausible tool name — so the error state is what
    # separates a contaminated run from a typo.
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


def read_call_log(path):
    """The shim's record of this question, split into what it served and what it refused."""
    served, refused = [], []
    if not os.path.exists(path):
        return served, refused
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            (served if rec.get("served") else refused).append(rec)
    return served, refused


def run_copy(entries, *, daemon_url, label, copy_name, call_budget, model, timeout,
             out_path, verbose=True):
    status = daemon_status(daemon_url)
    vault_served = status.get("vault", "")
    n_docs = (status.get("index") or {}).get("documents")
    if Path(vault_served).name != copy_name:
        raise SystemExit(
            f"[week3] the daemon on {daemon_url} is serving {Path(vault_served).name!r}, "
            f"but this run is labelled {copy_name!r}. Refusing to write a scorecard "
            f"under a corpus it did not run against.")

    run_dir = Path(tempfile.mkdtemp(prefix=f"w3-{label}-"))
    config_dir = run_dir / "claude-config"
    config_dir.mkdir(parents=True, exist_ok=True)
    if verbose:
        print(f"[week3] {label}: daemon {daemon_url} · corpus {copy_name} · "
              f"{n_docs} documents · budget {call_budget}", file=sys.stderr)

    rows, hook_violations, count_mismatches, tool_escapes = [], [], [], []
    refused_attempts = []
    for i, e in enumerate(entries, 1):
        # One log file per question is what makes the budget per-question: the
        # shim counts the lines in whatever file it is pointed at.
        call_log_path = run_dir / f"calls-{e['id']}.jsonl"
        res = run_driver(e, daemon_url, call_budget, model=model, timeout=timeout,
                         config_dir=config_dir, call_log_path=call_log_path,
                         verbose=verbose)
        served, refused = read_call_log(call_log_path)
        tool_calls = len(served)
        # A budget refusal is still a `memory_search` the transcript records, so
        # it belongs on the shim's side of the count check. A wrong-tool refusal
        # is a different tool name and is caught by the escape audit instead.
        over_budget = [r for r in refused if r.get("refused") == "budget_exhausted"]

        if res["hooks_fired"]:
            hook_violations.append((e["id"], sorted(set(res["hooks_fired"]))))
        if res.get("unexpected_tools"):
            tool_escapes.append((e["id"], res["unexpected_tools"]))
        if res.get("refused_tool_attempts"):
            refused_attempts.append((e["id"], res["refused_tool_attempts"]))
        # The shim counts calls it served; the transcript counts calls the model
        # made. They agree unless something other than the driver is calling the
        # tool, or the driver reached a tool we did not give it.
        if res["transcript_tool_calls"] != tool_calls + len(over_budget):
            count_mismatches.append(
                (e["id"], res["transcript_tool_calls"], tool_calls + len(over_budget)))

        kind, answered = w1.parse_answer(res["text"])
        score = score_at_k(e["expected_note_paths"], answered, k=SCORE_K)
        correct, p_at_5, r_at_5 = w1.judge_correct(score, kind, res["error"])
        rows.append({
            "id": e["id"], "stratum": e["stratum"], "source": e["source"],
            "question": e["question"], "expected": e["expected_note_paths"],
            "answered": answered, "said_no_answer": kind == "no_answer",
            "never_concluded": kind is None,
            "hits": score["hits"],
            "p_at_5": round(p_at_5, 4), "r_at_5": round(r_at_5, 4),
            "first_hit_rank": score["first_hit_rank"], "is_negative": score["is_negative"],
            "correct": bool(correct), "tool_calls": tool_calls,
            "tool_call_log": served,
            "refused_over_budget": len(over_budget),
            "refused_other": [r for r in refused
                              if r.get("refused") != "budget_exhausted"],
            "daemon_ms_total": round(sum(c.get("ms", 0.0) for c in served), 1),
            "driver_error": res["error"], "wall_s": res["wall_s"],
            "cost_usd": res.get("cost_usd", 0.0),
        })
        if verbose:
            mark = "OK  " if rows[-1]["correct"] else "MISS"
            print(f"[week3] {label} {i}/{len(entries)} {mark} {e['id']:<16} "
                  f"{tool_calls} calls, {res['wall_s']}s", file=sys.stderr)

    overall, per_stratum = w1._blank_bucket(), {}
    for row in rows:
        w1._accumulate(overall, row)
        w1._accumulate(per_stratum.setdefault(row["stratum"], w1._blank_bucket()), row)

    served_calls = [c for r in rows for c in r["tool_call_log"]]
    report = {
        "run_label": label,
        "arm": "daemon",
        "copy": copy_name,
        "driver": "claude",
        "model": model,
        # The corpus copy's directory name, never its absolute path — a committed
        # scorecard carrying an absolute path encodes a username and a mount id,
        # which is the literal AGENTS.md's vault-path convention forbids.
        "vault_name": Path(vault_served).name,
        "corpus": {"n_docs": n_docs, "excluded_dirs": [], "embed_mode": None},
        "daemon": {
            "version": status.get("version"),
            "vault_source": status.get("vault_source"),
            "index_documents": n_docs,
            # The daemon's own census of what it demotes, read back from the
            # running process rather than restated from its source.
            "penalized_census": (status.get("index") or {}).get("penalized"),
            "unfiled": (status.get("index") or {}).get("unfiled"),
        },
        # The daemon has none of the week-1 corpus layer's switches: one FTS5
        # schema, ANDed terms, and a penalty that is a compiled-in constant with
        # no configuration surface — which is what the 2026-08-07 sweep
        # recommended. Stated so the shared renderer does not print week-1
        # defaults over a run that never had them.
        "lexical_variant": "daemon-fts5",
        "query_mode": "daemon (AND)",
        "penalty": "daemon constant",
        "surface": w1._surface_stats(rows),
        "call_budget": call_budget,
        "n_questions": len(rows),
        "cost_usd_total": round(sum(r.get("cost_usd", 0.0) for r in rows), 4),
        "wall_s_total": round(sum(r["wall_s"] for r in rows), 1),
        "daemon_latency": {
            "n_calls": len(served_calls),
            "mean_ms": round(sum(c.get("ms", 0.0) for c in served_calls)
                             / max(len(served_calls), 1), 2),
            "max_ms": round(max((c.get("ms", 0.0) for c in served_calls), default=0.0), 1),
        },
        "overall": w1._finalize(overall),
        "per_stratum": {k: w1._finalize(v) for k, v in per_stratum.items()},
        "per_question": rows,
        "misses": [
            {"id": r["id"], "stratum": r["stratum"], "question": r["question"],
             "expected": r["expected"], "answered": r["answered"],
             "tool_calls": r["tool_calls"], "reason": w1._miss_reason(r)}
            for r in rows if not r["correct"]
        ],
        "integrity": {
            "hook_violations": [{"id": i, "hooks": h} for i, h in hook_violations],
            "tool_escapes": [{"id": i, "tools": t} for i, t in tool_escapes],
            "refused_tool_attempts": [{"id": i, "tools": t} for i, t in refused_attempts],
            "tool_call_count_mismatches": [
                {"id": i, "transcript": t, "daemon": d} for i, t, d in count_mismatches],
            "budget_leaks": [r["id"] for r in rows if r["tool_calls"] > call_budget],
            "questions_that_wanted_more_calls": sum(
                1 for r in rows if r["refused_over_budget"] > 0),
            "driver_errors": [r["id"] for r in rows if r["driver_error"]],
        },
    }
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Run the week-1 gold set against a running agentmd daemon.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--gold-set", required=True)
    ap.add_argument("--daemon-url", required=True,
                    help="the daemon's MCP endpoint, e.g. http://127.0.0.1:51468/mcp")
    ap.add_argument("--copy-name", required=True,
                    help="the corpus copy's directory name; checked against /status")
    ap.add_argument("--label", required=True, help="run label, e.g. al-opus-r1")
    ap.add_argument("--model", default="opus")
    ap.add_argument("--call-budget", type=int, default=DEFAULT_CALL_BUDGET)
    ap.add_argument("--timeout", type=float, default=300.0, help="per-question, seconds")
    ap.add_argument("--limit", type=int, default=None, help="first N questions only")
    ap.add_argument("--out", default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    entries = w1.load_gold_set(args.gold_set)
    if args.limit:
        entries = entries[:args.limit]

    report = run_copy(
        entries, daemon_url=args.daemon_url, label=args.label,
        copy_name=args.copy_name, call_budget=args.call_budget, model=args.model,
        timeout=args.timeout, out_path=args.out, verbose=not args.quiet,
    )
    print(w1.render_table(report))
    integrity = report["integrity"]
    hard = (integrity["hook_violations"] or integrity["tool_escapes"]
            or integrity["tool_call_count_mismatches"] or integrity["budget_leaks"])
    print()
    print("INTEGRITY: " + ("FAIL" if hard else "clean")
          + f"  hooks={len(integrity['hook_violations'])}"
          + f" escapes={len(integrity['tool_escapes'])}"
          + f" count-mismatches={len(integrity['tool_call_count_mismatches'])}"
          + f" budget-leaks={len(integrity['budget_leaks'])}"
          + f" driver-errors={len(integrity['driver_errors'])}")
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
