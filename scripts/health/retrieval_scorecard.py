#!/usr/bin/env python3
"""retrieval_scorecard — score a gold set against the daemon, no model in the loop.

The layer this measures, and the one it does not
-----------------------------------------------
`week1_retrieval_experiment.py` and `week3_daemon_retest.py` drive an *agent*
over `memory_search` and score what it concluded. That is the layer users live
in, and it is the layer where the alias backfill was convicted: the aliased
corpus was slightly better at the tool level and 3.85 points worse once an
agent read the results.

This scores the tool alone — query in, ranked list out, hit if any expected
path is in the top k. No driver, no retries, no judgment. That makes it
deterministic, so one run is exact and replicates buy nothing, and it makes it
cheap enough to run on every retrieval change. It is the harness shape AgentKV
used to price hybrid search, which is why it exists here: their numbers and
ours are not comparable until both are measured at the same layer.

Use both. A retrieval-layer win is necessary and not sufficient; we have one
documented case of the two layers disagreeing about the same treatment.

On correct rejection
--------------------
For a negative question the honest answer is "no such memory exists," and this
harness reports how often the tool returned nothing at all. That is a weaker
property than it sounds, and the report says so rather than dressing it up:
FTS5 ANDs its terms, so an empty result means a term was missing from the
index, not that anything judged the corpus unable to answer. There is no score
threshold in this stack today. Rejection is currently an agent-layer behavior,
and a retrieval-layer rejection number here is a floor, not a verdict.

Usage:
    python3 scripts/health/retrieval_scorecard.py --gold-set <path> [--k 5]
        [--json out.json] [--stratum NAME]
"""
from __future__ import annotations

import argparse
import collections
import json
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_MEMORY_SCRIPTS = _HERE.parent.parent / "harness" / "skills" / "memory" / "scripts"
if str(_MEMORY_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_MEMORY_SCRIPTS))
import recall  # noqa: E402  (production query-term extraction)


# The daemon's own words when an arm ran without one of its children. Matched
# rather than inferred: a run that silently lost an arm partway through would
# otherwise be published under the full arm's label, and the number would be
# wrong in the one direction nobody checks — downward, which reads as "the
# model did not help." Three markers: the dense arm falling back to lexical
# (task 2), the cross-encoder pass falling back to unreranked hybrid (task 3),
# and (task 5) `_daemon_search` declining to run a `--via-hook` query at all
# (timeout, missing binary) — a silently-skipped query would otherwise be
# indistinguishable from a real, searched miss, and a `hook e2e` column has to
# know the difference before it is trusted.
DEGRADED_MARKS = ("lexical arm alone", "no reranker was available", "(hook skipped:")


def to_query(question: str) -> str:
    """Reduce a question to the terms the production hook would search for.

    This is not a nicety, it is the difference between measuring the system and
    measuring a pipeline nobody runs. FTS5 ANDs its terms, so handing it a
    fifteen-word question demands all fifteen appear in one note; scored that
    way the whole gold set reads 3.1% and the number is an artifact of the
    harness. `recall.py` is imported rather than reimplemented so the scorecard
    cannot drift from the caller it claims to model — if the extraction changes,
    this moves with it.
    """
    return recall._daemon_query_terms(question)


def search(query: str, k: int, mode: str = "and", target: tuple = (),
           question: str | None = None,
           lex3: bool = False) -> tuple[list[str], float, str, float | None, int | None]:
    """Top-k paths for a query, the wall time in milliseconds, the daemon's
    note, and — for `-mode rerank` only — the cross-encoder's own wall time
    and pair count for this one query.

    The arm is selected by the daemon's own `-mode` flag rather than simulated
    here. An arm the scorecard implements itself is an arm nobody can ship: the
    2-term fusion numbers in `results/goldv2/NOTES.md` were produced by a
    throwaway driver, so "does the daemon do what the experiment did" had no
    answer until the mode existed in the daemon and this script stopped
    reimplementing it.

    `target` carries --vault/--index through to the daemon. Without it every arm
    scores whatever vault the kernel config resolves, which is the live one and
    drifts between runs; the ladder's columns are only comparable because each is
    pointed at the same frozen snapshot. The index has to be named too — it does
    not derive from the vault path, so a vault override alone would write the
    snapshot's index over the operator's live one.

    `question`, when given, is passed as the daemon's own `-question` flag
    (task 3.5) — the dense arm embeds it instead of `query`, and `query` keeps
    driving the lexical arms exactly as it always has. Passed straight through
    rather than reimplemented for the same reason `mode` is: the daemon owns
    the mechanism, this script only asks for it.

    `lex3`, when true, is passed as the daemon's own `-lex3` flag (task 4) —
    `fusion`/`hybrid`'s lexical arm widens from 2-term to 2- and 3-term subsets.
    Same reasoning as `question`: the daemon owns the widening, this script
    only asks for it.
    """
    started = time.monotonic()
    argv = ["agentmd", "search", "-json", "-mode", mode, "-k", str(k), *target]
    if question:
        argv += ["-question", question]
    if lex3:
        argv.append("-lex3")
    argv.append(query)
    proc = subprocess.run(argv, capture_output=True, text=True)
    elapsed = (time.monotonic() - started) * 1000.0
    out = proc.stdout
    brace = out.find("{")
    if brace < 0:
        return [], elapsed, "", None, None
    try:
        doc = json.loads(out[brace:])
    except json.JSONDecodeError:
        return [], elapsed, "", None, None
    return ([r.get("path", "") for r in (doc.get("results") or [])],
            elapsed, doc.get("note") or "",
            doc.get("rerank_ms"), doc.get("rerank_pairs"))


def search_via_hook(question_text: str, k: int, vault: str, index: str,
                     budget_ms: int | None = None,
                     ) -> tuple[list[str], float, str, float | None, int | None]:
    """Top-k paths for one gold question, scored through recall.py's own
    `_daemon_search` — the hook's real code path — instead of shelling to
    `agentmd search` directly.

    `search()` above proves the daemon can answer a query; this proves
    recall.py's own terms-extraction (`_daemon_query_terms`), its `-mode
    hybrid -question` wiring (task 5's cutover), and its 250ms subprocess
    budget (`DAEMON_BUDGET_MS`) produce the same answer the installed hook
    would — which is what task 5's rule means by "scored through the hook
    rather than the CLI." `question_text` is the gold question's raw text,
    unreduced: `_daemon_search` derives its own lexical terms internally,
    exactly as the real hook does, so handing it anything already reduced
    would double-apply the reduction to the wrong argument.

    `vault`/`index` are the frozen corpus's own root and index file, required
    (never the live vault — see main()'s enforcement). They reach the daemon
    as `_daemon_search`'s `daemon_vault`/`daemon_index` escape hatch, and
    `vault` doubles as `_daemon_search`'s own relativization root: passing the
    corpus ROOT rather than its `Agent/` subdirectory is deliberate — it is
    what makes `_daemon_search`'s returned paths come back
    `Agent/...`-prefixed, comparable to `expected_note_paths` with no second
    transform (see `_daemon_root_for`'s own root-or-parents search: `vault`
    resolves on the first try instead of stripping a level).

    Timing wraps the whole `_daemon_search` call, which is dominated by its
    own internal `subprocess.run` — the same cost the real hook pays, run from
    an already-warm interpreter rather than one recall.py's own shell wrapper
    would have to start fresh each time. See the task's close-out for the
    separate true end-to-end sampling that timing gap is why this script does
    not claim to answer alone.

    `budget_ms`, when given, overrides `_daemon_search`'s own default
    (`DAEMON_BUDGET_MS`, 250ms — the real hook's own subprocess timeout).
    Task 5's rule is two independent clauses on purpose ("both halves
    matter"): correctness (do the right strata come back) and latency (is it
    fast). A pathological query's cost belongs to the second clause, not the
    first — a fusion candidate pool cheap at k=10 but expensive at hybrid's
    internal k=50 (rrfDepth, search.go) for a term combination common enough
    in-corpus can cost seconds, confirmed directly against `-mode fusion -k
    50` alone, no dense arm involved. The production hook is unaffected
    either way (its own 250ms budget already bounds and gracefully falls
    back), but a *strata* column scored at that same 250ms would silently
    misreport a slow-but-correct answer as a miss, conflating "hybrid did not
    find it" with "hybrid took too long to ask." The correctness pass this
    parameter serves is deliberately generous so a genuine miss and a timeout
    are never the same bucket; the latency clause is measured separately,
    under the real unmodified budget, against the installed hook.
    """
    status: dict = {}
    started = time.monotonic()
    kwargs = {} if budget_ms is None else {"budget_ms": budget_ms}
    hits = recall._daemon_search(
        vault=Path(vault), query_text=question_text, k=k,
        daemon_vault=vault, daemon_index=index, status=status, **kwargs,
    )
    elapsed = (time.monotonic() - started) * 1000.0
    if hits is None:
        note = f"(hook skipped: {status.get('reason', '?')})"
        return [], elapsed, note, None, None
    return ([h["path"] for h in hits], elapsed, status.get("note", ""), None, None)


def score(entries: list[dict], k: int, mode: str = "and", target: tuple = (),
          pass_question: bool = False, lex3: bool = False,
          via_hook: bool = False, vault: str | None = None, index: str | None = None,
          via_hook_budget_ms: int | None = None) -> dict:
    rows = []
    for e in entries:
        expected = e.get("expected_note_paths") or []
        # Folder-level accept (task 3, goldv3 pp09): any returned path under
        # one of these prefixes counts as a hit alongside the exact paths.
        # v2 entries never carry this key, so `prefixes` is always `[]` for
        # them and every line below collapses to its old behavior exactly.
        prefixes = e.get("expected_note_prefixes") or []
        is_negative = not expected and not prefixes
        query = to_query(e["question"])
        # The gold question passed through verbatim, not to_query's reduction
        # of it — task 3.5's whole point is that the dense arm sees the
        # natural question, not the AND-terms built for FTS5.
        question = e["question"] if pass_question else None
        if via_hook:
            # search_via_hook derives its own terms from the raw question
            # internally (recall._daemon_search calling _daemon_query_terms),
            # exactly as the installed hook does — `query` here is computed
            # only so this row still records what that reduction produced,
            # for the console render's miss report and for parity with every
            # other mode's row shape.
            ranked, ms, note, rerank_ms, rerank_pairs = search_via_hook(
                e["question"], k, vault, index, budget_ms=via_hook_budget_ms)
        elif query:
            ranked, ms, note, rerank_ms, rerank_pairs = search(
                query, k, mode, target, question, lex3)
        else:
            ranked, ms, note, rerank_ms, rerank_pairs = [], 0.0, "", None, None
        exact_hits = [p for p in expected if p in ranked]
        prefix_hits = [p for p in ranked if p not in exact_hits
                       and any(p.startswith(pref) for pref in prefixes)]
        hits = exact_hits + prefix_hits
        first = min((ranked.index(p) + 1 for p in hits), default=None)
        rows.append({
            "id": e["id"], "stratum": e["stratum"], "question": e["question"],
            "query": query,
            "question_passed": bool(question),
            "lex3": lex3,
            "is_negative": is_negative,
            # goldv3: a negative annotated `layer: gate-only` measures nothing
            # at this layer by design — rejection moved to the deliberate-path
            # gate. Reported in its own block (see render()), not folded into
            # the ordinary "rejection (floor)" line. Never true for a v2 entry
            # (no `layer` key), so v2's rejection reporting is untouched.
            "gate_only": is_negative and e.get("layer") == "gate-only",
            # goldv3 Group A (dt01/ep10/ep12): the hook arm's own recall path
            # excludes the subtree every expected note lives in by policy, not
            # by a retrieval defect — excluded from *that arm's* denominator
            # only. A bare --mode run or --question without --via-hook scores
            # these exactly as it always has; only true when via_hook is set,
            # so a non-hook run is never affected.
            "hook_excluded": via_hook and e.get("hook_reachable") is False,
            # A negative is "correct" when the tool returned nothing at all.
            # See the module docstring for why that is a floor, not a verdict.
            "correct_rejection": (not ranked) if is_negative else None,
            "hit": bool(hits) if not is_negative else None,
            "first_hit_rank": first,
            "returned": len(ranked),
            "expected": expected,
            "expected_prefixes": prefixes,
            "top": ranked[:k],
            "ms": round(ms, 1),
            "rerank_ms": rerank_ms,
            "rerank_pairs": rerank_pairs,
            "alias_overlap": e.get("alias_overlap"),
            # The daemon says so when it served a mode it could not fully honor.
            # Carried per row because a run that degraded halfway is the case
            # this is here to catch, and an aggregate would hide which half.
            "degraded": any(mark in note for mark in DEGRADED_MARKS),
        })
    return {"rows": rows}


def render(result: dict, k: int) -> str:
    rows = result["rows"]
    by = collections.defaultdict(list)
    for r in rows:
        by[r["stratum"]].append(r)

    mode = result.get("mode", "and")
    # The header has to stop claiming "no model in the loop" once there is one.
    # A hybrid column filed under a lexical banner is the kind of mislabel that
    # survives into a design doc and gets cited as a lexical result.
    if mode == "rerank":
        channel = (f"hybrid + cross-encoder rerank + floor, "
                   f"embedder={result.get('embed_model') or '?'}, "
                   f"reranker={result.get('rerank_model') or '?'}")
        caveat = "two local models, no network, no LLM call; retrieval is still deterministic"
    elif mode == "hybrid":
        channel = f"hybrid (FTS5 + dense vectors, RRF), embedder={result.get('embed_model') or '?'}"
        caveat = "the dense arm is a local embedding model; retrieval is still deterministic"
    else:
        channel = f"lexical (FTS5)"
        caveat = "no model in the loop"
    # Task 5's rule is "scored through the hook rather than the CLI" — a
    # `hook e2e` column that read identically to an ordinary --mode hybrid run
    # would hide the one thing that column exists to prove: that recall.py's
    # own terms-extraction, mode/question wiring, and subprocess budget
    # produced these numbers, not a bare CLI invocation.
    if result.get("via_hook"):
        channel += ", via the installed hook's own code path (recall._daemon_search)"
    # The header must say when the dense arm embedded the natural question
    # instead of the reduced terms (task 3.5) — otherwise a +question column
    # reads as an ordinary hybrid run, which is exactly the kind of mislabel
    # the mode-vs-caveat check above already exists to prevent one layer up.
    if result.get("question_passed"):
        channel += ", dense arm embeds the natural question"
    # Same reasoning as the question_passed note above: a +lex3 column filed
    # without saying so reads as an ordinary fusion/hybrid run, and the whole
    # point of the flag is that it is never on by default.
    if result.get("lex3"):
        channel += ", lexical arm widened to 2- and 3-term subsets"
    out = [f"retrieval scorecard — {channel}, mode={mode}, k={k}, {caveat}",
           "queries reduced by recall._daemon_query_terms, as the prompt-submit hook does " +
           ("(lexical arms) / passed through verbatim to the dense arm (task 3.5)"
            if result.get("question_passed") else ""),
           ""]
    out.append(f"{'stratum':<20}{'n':>4}{'hit@k':>9}{'rate':>9}")
    out.append("-" * 42)
    scored = [r for r in rows if not r["is_negative"] and not r["hook_excluded"]]
    for stratum in sorted(by):
        rs = by[stratum]
        pos = [r for r in rs if not r["is_negative"] and not r["hook_excluded"]]
        if pos:
            h = sum(1 for r in pos if r["hit"])
            out.append(f"{stratum:<20}{len(pos):>4}{h:>9}{h/len(pos):>9.1%}")
        else:
            neg_rows = [r for r in rs if r["is_negative"]]
            # goldv3: a stratum whose rows are entirely gate-only negatives is
            # reported in its own block below instead of inline here — see
            # "gate-only" below. A v2 negative never carries `gate_only`, so
            # this is always False there and the old inline row is unchanged.
            if neg_rows and all(r["gate_only"] for r in neg_rows):
                continue
            c = sum(1 for r in rs if r["correct_rejection"])
            out.append(f"{stratum:<20}{len(rs):>4}{c:>9}{c/len(rs):>9.1%}  (returned nothing)")
    out.append("-" * 42)
    hits = sum(1 for r in scored if r["hit"])
    out.append(f"{'OVERALL R@' + str(k):<20}{len(scored):>4}{hits:>9}{hits/len(scored):>9.1%}")

    # goldv3 Group A: reported separately from R@k above, never folded into
    # a generic miss — see score()'s `hook_excluded` for why.
    hook_excl = [r for r in rows if r["hook_excluded"]]
    if hook_excl:
        h = sum(1 for r in hook_excl if r["hit"])
        out.append(f"{'hook-excluded (policy)':<20}{len(hook_excl):>4}{h:>9}{h/len(hook_excl):>9.1%}"
                    "  (expected notes live in a hook-excluded subtree)")

    negs = [r for r in rows if r["is_negative"] and not r["gate_only"]]
    if negs:
        c = sum(1 for r in negs if r["correct_rejection"])
        out.append(f"{'rejection (floor)':<20}{len(negs):>4}{c:>9}{c/len(negs):>9.1%}")

    # goldv3: negatives annotated `layer: gate-only` measure nothing at this
    # layer by design (rejection moved to the deliberate-path gate) — reported
    # here, never counted inside R@k or the ordinary rejection-floor line
    # above. Empty for a v2 gold set (no entry carries `layer`).
    gate_only = [r for r in rows if r["gate_only"]]
    if gate_only:
        c = sum(1 for r in gate_only if r["correct_rejection"])
        out.append(f"{'gate-only (not scored here)':<20}{len(gate_only):>4}{c:>9}{c/len(gate_only):>9.1%}"
                    "  (rejection moved to the deliberate-path gate)")

    lat = sorted(r["ms"] for r in rows)
    out += ["", f"latency  p50 {lat[len(lat)//2]:.1f}ms   p90 "
                f"{lat[int(len(lat)*0.9)]:.1f}ms   max {lat[-1]:.1f}ms  (includes CLI startup)"]

    # CE ms/pair: task 5's 300ms feasibility question is priced per pair, not
    # per query, since a query's pair count varies with how many fused
    # candidates had more than one chunk. Each row contributes its own
    # ms-per-pair, so the p50/p90 here is a distribution over queries of "how
    # long did this query's cross-encoder pass take, divided by how many
    # pairs it scored" — not a single aggregate hiding per-query variance.
    ce = sorted(r["rerank_ms"] / r["rerank_pairs"] for r in rows
                if r.get("rerank_ms") is not None and r.get("rerank_pairs"))
    if ce:
        pairs = [r["rerank_pairs"] for r in rows if r.get("rerank_pairs")]
        out += ["", f"CE ms/pair  p50 {ce[len(ce)//2]:.2f}   p90 {ce[int(len(ce)*0.9)]:.2f}"
                    f"   (n={len(ce)} queries, {sum(pairs)/len(pairs):.1f} pairs/query mean)"]

    overlap = [r for r in rows if r.get("alias_overlap") is not None]
    if overlap:
        out.append("")
        for flag, label in ((True, "alias-overlapping"), (False, "alias-independent")):
            grp = [r for r in overlap if r["alias_overlap"] is flag and not r["is_negative"]]
            if grp:
                h = sum(1 for r in grp if r["hit"])
                out.append(f"  research-corpus, {label:<18} {h}/{len(grp)}  {h/len(grp):.1%}")
        out.append("  (split deliberately: the session that wrote those notes' aliases also")
        out.append("   wrote these questions, so the overlapping half grades its own author)")

    misses = [r for r in scored if not r["hit"]]
    if misses:
        out += ["", f"misses ({len(misses)}):"]
        for r in misses:
            out.append(f"  [{r['id']}] {r['question'][:66]}")
            out.append(f"        query  {r['query']}")
            # A prefix-only entry (goldv3 pp09-shaped) has no exact path to
            # show; fall back to the prefix itself, marked as such.
            wanted = r["expected"][0] if r["expected"] else \
                (r["expected_prefixes"][0] + "**" if r["expected_prefixes"] else "?")
            out.append(f"        wanted {wanted.split('/')[-1][:56]}")
            got = r["top"][0].split("/")[-1][:56] if r["top"] else "(nothing returned)"
            out.append(f"        got    {got}")
    return "\n".join(out)


def arm_cells(result: dict, k: int) -> dict:
    """The cells this run contributes to the arm table, keyed by row label."""
    rows = result["rows"]
    by = collections.defaultdict(list)
    for r in rows:
        by[r["stratum"]].append(r)

    cells = {}
    for stratum, rs in by.items():
        pos = [r for r in rs if not r["is_negative"] and not r["hook_excluded"]]
        if pos:
            cells[stratum] = f"{sum(1 for r in pos if r['hit'])}/{len(pos)}"
    scored = [r for r in rows if not r["is_negative"] and not r["hook_excluded"]]
    if scored:
        hits = sum(1 for r in scored if r["hit"])
        cells[f"R@{k}"] = f"**{hits / len(scored):.1%}**"
    # goldv3: gate-only negatives measure nothing at this layer — excluded
    # from the arm table's rejection cell the same way render() excludes them
    # from its own "rejection (floor)" line. Always False for v2 (no `layer`).
    negs = [r for r in rows if r["is_negative"] and not r["gate_only"]]
    if negs:
        c = sum(1 for r in negs if r["correct_rejection"])
        cells["negative rejection"] = f"**{c / len(negs):.0%}**"
    return cells


def _row_label(line: str) -> str:
    return line.split("|")[1].strip().strip("*") if line.count("|") >= 2 else ""


def append_arm_column(notes: Path, label: str, cells: dict) -> None:
    """Add one column to the arm table in NOTES.md, in place.

    The table is located by its `R@k` row rather than by line number, so an edit
    anywhere above it cannot silently write the column into a different table —
    this file has several, and the ladder appends to exactly one of them.

    A stratum the arm table names but this run did not score gets an em dash. A
    blank would read as a zero, and a zero is a result.
    """
    lines = notes.read_text(encoding="utf-8").splitlines()

    blocks, start = [], None
    for i, line in enumerate(lines + [""]):
        if line.startswith("|"):
            if start is None:
                start = i
        elif start is not None:
            blocks.append((start, i))
            start = None

    target = None
    for lo, hi in blocks:
        if any(_row_label(lines[i]).startswith("R@") for i in range(lo, hi)):
            target = (lo, hi)
            break
    if target is None:
        raise SystemExit(f"no arm table (a table carrying an R@k row) found in {notes}")

    lo, hi = target
    for i in range(lo, hi):
        line = lines[i].rstrip()
        if not line.endswith("|"):
            continue
        bare = line.replace("|", "").replace(" ", "")
        if bare and set(bare) <= set("-:"):
            lines[i] = line + "---:|"
        elif i == lo:
            lines[i] = line + f" {label} |"
        else:
            lines[i] = line + f" {cells.get(_row_label(line), '—')} |"
    notes.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gold-set", required=True)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--stratum", default=None, help="score only this stratum")
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--mode", default="and", choices=["and", "fusion", "hybrid", "rerank"],
                    help="which daemon search mode to score — the arm")
    ap.add_argument("--arm-label", default=None,
                    help="column header used by --append-notes (defaults to --mode)")
    ap.add_argument("--append-notes", default=None,
                    help="append this run as a column to the arm table in this NOTES.md")
    ap.add_argument("--vault", default=None,
                    help="vault to score — pass the restored corpus snapshot, never the live vault")
    ap.add_argument("--index", default=None,
                    help="index file for that vault; required with --vault so the "
                         "live index is not overwritten")
    ap.add_argument("--embedder-url", default=None,
                    help="embedding server to attach to for --mode hybrid. Without it "
                         "each of the 84 queries spawns and loads its own model, which "
                         "prices a run in model loads rather than in searches")
    ap.add_argument("--embed-model", default=None,
                    help="which model's vectors to score against; must be the model the "
                         "index was embedded with")
    ap.add_argument("--reranker-url", default=None,
                    help="reranking server to attach to for --mode rerank. Without it "
                         "each of the 84 queries spawns and loads its own model")
    ap.add_argument("--rerank-model", default=None,
                    help="which reranker model to score with; carries its own floor "
                         "(daemon/internal/rerank/model.go) — never a runtime knob")
    ap.add_argument("--question", action="store_true",
                    help="pass the gold question through as the daemon's -question flag "
                         "(task 3.5); the dense arm embeds it instead of the reduced "
                         "terms, which keep driving the lexical arms unchanged")
    ap.add_argument("--lex3", action="store_true",
                    help="pass the daemon's own -lex3 flag (task 4): widen "
                         "fusion/hybrid's lexical arm from 2-term to 2- and 3-term "
                         "subsets; false (the default) reproduces lexical-fusion/"
                         "+question exactly")
    ap.add_argument("--via-hook", action="store_true",
                    help="score through recall.py's own _daemon_search (task 5's "
                         "'scored through the hook rather than the CLI') instead of "
                         "shelling to agentmd search directly; requires --mode hybrid "
                         "--question --vault --index, and ignores --embedder-url/"
                         "--reranker-url/--lex3, none of which recall.py's own call "
                         "ever forwards")
    ap.add_argument("--via-hook-budget-ms", type=int, default=None,
                    help="override _daemon_search's own subprocess timeout "
                         "(default: DAEMON_BUDGET_MS, 250ms, the real hook's own "
                         "budget) for the correctness/strata pass, so a query slow "
                         "for reasons unrelated to whether hybrid found the right "
                         "answer is not miscounted as a miss. The latency clause "
                         "must still be measured separately, under the real budget, "
                         "against the installed hook — this flag only affects "
                         "--via-hook")
    args = ap.parse_args(argv)

    if bool(args.vault) != bool(args.index):
        ap.error("--vault and --index go together: a vault override without its own "
                 "index writes the snapshot's index over the live one")
    if args.question and args.mode not in ("hybrid", "rerank"):
        # Not a daemon error — and-mode ignores an unrecognized dense-arm
        # concern harmlessly, at the daemon level — but a scorecard run that
        # asked for question-passthrough and silently scored a mode with no
        # dense arm to pass it to would publish a column mislabeled by intent.
        ap.error("--question only affects a dense arm; pass --mode hybrid or --mode rerank")
    if args.lex3 and args.mode not in ("fusion", "hybrid", "rerank"):
        # Same reasoning as --question above: and-mode has no lexical-fusion
        # arm to widen, so --lex3 there would silently do nothing rather than
        # what the flag's name promises.
        ap.error("--lex3 only affects a lexical-fusion arm; pass --mode fusion, "
                 "--mode hybrid or --mode rerank")
    if args.via_hook:
        # recall._daemon_search hardcodes -mode hybrid -question internally
        # (the hook cutover) — a --via-hook run under any other --mode/
        # --question combination would silently score hybrid+question anyway
        # while its own metadata claimed something else.
        if args.mode != "hybrid" or not args.question:
            ap.error("--via-hook always scores -mode hybrid -question (that is "
                     "the hook's own behavior); pass --mode hybrid --question")
        if args.lex3:
            ap.error("--via-hook has no -lex3 wiring — recall._daemon_search never "
                     "sends it, so --lex3 here would silently do nothing")
        if not (args.vault and args.index):
            ap.error("--via-hook needs --vault and --index — recall._daemon_search "
                     "must be pointed at the frozen corpus explicitly, the same "
                     "reason --mode hybrid needs --vault/--index for every other run")
        if args.embedder_url or args.reranker_url:
            ap.error("--via-hook ignores --embedder-url/--reranker-url — "
                     "recall._daemon_search never forwards either; the daemon "
                     "resolves its own embedder (see embedderAttachDefault, "
                     "cmd/agentmd) the same way a real hook invocation would")
    elif args.via_hook_budget_ms is not None:
        ap.error("--via-hook-budget-ms only affects --via-hook")
    if args.mode in ("hybrid", "rerank") and not args.embedder_url and not args.via_hook:
        # Refused rather than allowed-but-slow. A hybrid (or rerank, which
        # runs hybrid underneath) run without a warm server still produces
        # numbers, and they would be a model load per query on a machine
        # already busy — the kind of run that gets abandoned halfway and
        # half-reported.
        ap.error(f"--mode {args.mode} needs --embedder-url; start one with "
                 "`llama-server -m <gguf> --embeddings --pooling mean -np 1 -c <ctx> -ub <ctx>`")
    if args.mode == "rerank" and not args.reranker_url:
        ap.error("--mode rerank needs --reranker-url; start one with "
                 "`llama-server -m <gguf> --rerank -np 1 -c 2048 -b 2048 -ub 2048`")

    doc = json.loads(Path(args.gold_set).read_text(encoding="utf-8"))
    entries = doc["entries"] if isinstance(doc, dict) else doc
    if args.stratum:
        entries = [e for e in entries if e["stratum"] == args.stratum]
    if not entries:
        print("no entries to score", file=sys.stderr)
        return 2

    target = ("--vault", args.vault, "--index", args.index) if args.vault else ()
    if args.embedder_url:
        target += ("-embedder-url", args.embedder_url)
    if args.embed_model:
        target += ("-embed-model", args.embed_model)
    if args.reranker_url:
        target += ("-reranker-url", args.reranker_url)
    if args.rerank_model:
        target += ("-rerank-model", args.rerank_model)
    result = score(entries, args.k, args.mode, target, pass_question=args.question,
                   lex3=args.lex3, via_hook=args.via_hook, vault=args.vault, index=args.index,
                   via_hook_budget_ms=args.via_hook_budget_ms)
    result["vault"] = args.vault
    result["gold_set"] = Path(args.gold_set).name
    result["corpus"] = (doc.get("$corpus") or {}).get("name") if isinstance(doc, dict) else None
    result["k"] = args.k
    result["mode"] = args.mode
    result["embed_model"] = args.embed_model
    result["rerank_model"] = args.rerank_model
    result["question_passed"] = args.question
    result["lex3"] = args.lex3
    result["via_hook"] = args.via_hook
    # An arm that lost a child partway through is a weaker arm wearing a
    # stronger label — hybrid that silently fell back to lexical, or rerank
    # that silently fell back to unreranked hybrid. Refuse to publish it
    # rather than writing a column whose number is real and whose name is
    # wrong — the ladder's whole value is that each column means what its
    # header says.
    degraded = [r["id"] for r in result["rows"] if r.get("degraded")]
    if degraded:
        reason = ("a query was silently skipped instead of searched (see each "
                  "row's own note — a daemon timeout or missing binary)"
                  if args.via_hook else
                  "a child (embedder or reranker) was not serving")
        print(f"\nREFUSING TO REPORT: {len(degraded)} of {len(result['rows'])} queries "
              f"fell back to a weaker arm than -mode {args.mode} claims — {reason}.",
              file=sys.stderr)
        print(f"  first affected: {', '.join(degraded[:5])}", file=sys.stderr)
        print("  a llama-server can answer /health with 200 while failing every "
              "real request; check the server log, restart it, and re-run.", file=sys.stderr)
        return 3

    print(render(result, args.k))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {args.json_out}")
    if args.append_notes:
        label = args.arm_label or args.mode
        append_arm_column(Path(args.append_notes), label, arm_cells(result, args.k))
        print(f"\nappended column {label!r} to {args.append_notes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
