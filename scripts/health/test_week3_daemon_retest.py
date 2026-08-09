#!/usr/bin/env python3
"""Tests for the week-3 daemon retest harness.

Expected values are written by hand from what the behaviour should be, never
computed with the implementation's own formula — a check that derives its
expectation the way the code does proves only that the code agrees with itself.
The permutation p-values below are enumerated on paper in each test's comment.

The shim tests run the real shim as a subprocess against a stand-in daemon that
speaks the same JSON-RPC, because the three properties worth pinning are all
process-level: that the call ceiling holds across more than one shim instance,
that `memory_capture` never reaches a driver, and that the call log records what
was served. A mock of the shim would prove none of them.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
for _p in (_HERE, _REPO / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import week3_analyze as wa  # noqa: E402
import week3_daemon_retest as w3  # noqa: E402

SHIM = _HERE / "week3_daemon_shim.py"


# ---------------------------------------------------------------------------
# A stand-in daemon
# ---------------------------------------------------------------------------

class _FakeDaemon(BaseHTTPRequestHandler):
    """Answers /status and /mcp the way agentmd does, with fixed results."""

    vault_name = "week3-AL"
    calls_seen = []

    def log_message(self, *_args):  # keep the test output clean
        pass

    def _send(self, obj, code=200):
        blob = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def do_GET(self):
        if self.path == "/status":
            self._send({
                "vault": f"/tmp/{self.vault_name}",
                "vault_source": "flag",
                "index": {"documents": 8993, "penalized": {"fragment": 5277},
                          "unfiled": 4820},
            })
            return
        self._send({"error": "not found"}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        method = body.get("method")
        if method == "initialize":
            self._send({"jsonrpc": "2.0", "id": body.get("id"), "result": {
                "protocolVersion": "2025-06-18",
                "serverInfo": {"name": "agentm", "version": "0.1.0-test"},
                "instructions": "Two tools.",
            }})
            return
        if method == "tools/list":
            self._send({"jsonrpc": "2.0", "id": body.get("id"), "result": {
                "tools": [{"name": "memory_search", "description": "search"},
                          {"name": "memory_capture", "description": "write"}],
            }})
            return
        if method == "tools/call":
            params = body.get("params") or {}
            self.__class__.calls_seen.append(params)
            payload = {"results": [{"path": "a/one.md", "penalty": "fragment"},
                                   {"path": "b/two.md"}],
                       "matched": 7}
            self._send({"jsonrpc": "2.0", "id": body.get("id"), "result": {
                "content": [{"type": "text", "text": json.dumps(payload)}],
                "structuredContent": payload, "isError": False,
            }})
            return
        self._send({"jsonrpc": "2.0", "id": body.get("id"),
                    "error": {"code": -32601, "message": "unknown"}})


class _DaemonFixture:
    def __enter__(self):
        _FakeDaemon.calls_seen = []
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeDaemon)
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        return self

    def __exit__(self, *_exc):
        self.srv.shutdown()
        self.srv.server_close()


def run_shim(url, log_path, requests, budget=6):
    """Feed the real shim a list of JSON-RPC requests; return its replies."""
    proc = subprocess.run(
        [sys.executable, str(SHIM)],
        input="\n".join(json.dumps(r) for r in requests) + "\n",
        capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin", "WEEK3_DAEMON_URL": f"{url}/mcp",
             "WEEK3_CALL_LOG": str(log_path), "WEEK3_CALL_BUDGET": str(budget)},
    )
    out = []
    for line in proc.stdout.splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out, proc


def _search(i, query="q"):
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call",
            "params": {"name": "memory_search", "arguments": {"query": query, "k": 5}}}


# ---------------------------------------------------------------------------
# The shim
# ---------------------------------------------------------------------------

class ShimTests(unittest.TestCase):
    def test_capture_is_not_offered_to_the_driver(self):
        """The corpus under measurement is frozen; a write tool has no business
        in front of a driver whose job is to search it."""
        with _DaemonFixture() as d, tempfile.TemporaryDirectory() as tmp:
            replies, _ = run_shim(d.url, Path(tmp) / "calls.jsonl",
                                  [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}])
        self.assertEqual([t["name"] for t in replies[0]["result"]["tools"]],
                         ["memory_search"])

    def test_capture_call_is_refused_even_if_asked_for(self):
        with _DaemonFixture() as d, tempfile.TemporaryDirectory() as tmp:
            replies, _ = run_shim(d.url, Path(tmp) / "calls.jsonl", [
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": "memory_capture", "arguments": {"text": "x"}}}])
        self.assertTrue(replies[0]["result"]["isError"])
        self.assertEqual(_FakeDaemon.calls_seen, [],
                         "a refused capture must never reach the daemon")

    def test_budget_serves_six_and_refuses_the_seventh(self):
        with _DaemonFixture() as d, tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "calls.jsonl"
            replies, _ = run_shim(d.url, log, [_search(i) for i in range(1, 8)])
        served = [r for r in replies if not r["result"].get("isError")]
        refused = [r for r in replies if r["result"].get("isError")]
        self.assertEqual(len(served), 6)
        self.assertEqual(len(refused), 1)
        self.assertIn("budget", refused[0]["result"]["content"][0]["text"])
        self.assertEqual(len(_FakeDaemon.calls_seen), 6,
                         "the refused call must not reach the daemon")

    def test_the_budget_is_shared_across_shim_processes(self):
        """Claude Code may start more than one server process for a question.
        A per-process counter would hand the driver twice the budget, and the
        run would report a ceiling it never enforced."""
        with _DaemonFixture() as d, tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "calls.jsonl"
            run_shim(d.url, log, [_search(i) for i in range(1, 5)])   # 4 served
            replies, _ = run_shim(d.url, log, [_search(i) for i in range(5, 9)])
        served = [r for r in replies if not r["result"].get("isError")]
        self.assertEqual(len(served), 2, "only two of the six may remain")
        self.assertEqual(len(_FakeDaemon.calls_seen), 6)

    def test_call_log_records_what_the_daemon_served(self):
        with _DaemonFixture() as d, tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "calls.jsonl"
            run_shim(d.url, log, [_search(1, "penalty shape")])
            served, refused = w3.read_call_log(log)
        self.assertEqual(len(served), 1)
        self.assertEqual(refused, [])
        rec = served[0]
        self.assertEqual(rec["query"], "penalty shape")
        self.assertEqual(rec["n_results"], 2)
        self.assertEqual(rec["result_paths"], ["a/one.md", "b/two.md"])
        # The daemon's own verdict, including the empty string for an
        # unpenalized row — the surface stats count rows, not truthy values.
        self.assertEqual(rec["result_penalties"], ["fragment", ""])
        self.assertGreaterEqual(rec["ms"], 0.0)

    def test_read_call_log_splits_served_from_refused(self):
        with _DaemonFixture() as d, tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "calls.jsonl"
            run_shim(d.url, log, [_search(i) for i in range(1, 8)])
            served, refused = w3.read_call_log(log)
        self.assertEqual(len(served), 6)
        self.assertEqual([r["refused"] for r in refused], ["budget_exhausted"])


# ---------------------------------------------------------------------------
# The corpus guard
# ---------------------------------------------------------------------------

class CorpusGuardTests(unittest.TestCase):
    def test_status_reports_the_vault_and_the_version(self):
        with _DaemonFixture() as d:
            status = w3.daemon_status(f"{d.url}/mcp")
        self.assertTrue(status["vault"].endswith("week3-AL"))
        self.assertEqual(status["version"], "0.1.0-test")
        self.assertEqual(status["index"]["documents"], 8993)

    def test_a_run_labelled_for_the_wrong_copy_is_refused(self):
        """Mislabelling which corpus answered is the one error that would make
        every number in the scorecard read as valid while measuring the other
        arm."""
        with _DaemonFixture() as d:
            with self.assertRaises(SystemExit) as ctx:
                w3.run_copy([], daemon_url=f"{d.url}/mcp", label="x",
                            copy_name="week3-NO", call_budget=6, model="opus",
                            timeout=5, out_path=None, verbose=False)
        self.assertIn("week3-AL", str(ctx.exception))
        self.assertIn("week3-NO", str(ctx.exception))


# ---------------------------------------------------------------------------
# The statistics
# ---------------------------------------------------------------------------

class PermutationTests(unittest.TestCase):
    def test_three_against_three_fully_separated(self):
        """a=[1,1,1] b=[0,0,0]. C(6,3) = 20 rearrangements. Only the observed
        split and its mirror reach |mean diff| = 1, so p = 2/20 = 0.1."""
        delta, p = wa.exact_permutation_p([1.0, 1.0, 1.0], [0.0, 0.0, 0.0])
        self.assertAlmostEqual(delta, 1.0)
        self.assertAlmostEqual(p, 0.1)

    def test_two_against_two_fully_separated(self):
        """a=[2,2] b=[0,0]. C(4,2) = 6 splits: one at +2, one at -2, four at 0.
        p = 2/6 = 0.3333."""
        delta, p = wa.exact_permutation_p([2.0, 2.0], [0.0, 0.0])
        self.assertAlmostEqual(delta, 2.0)
        self.assertAlmostEqual(p, 0.3333)

    def test_identical_arms_are_never_significant(self):
        delta, p = wa.exact_permutation_p([0.5] * 6, [0.5] * 6)
        self.assertAlmostEqual(delta, 0.0)
        self.assertAlmostEqual(p, 1.0)

    def test_six_against_six_enumerates_924_rearrangements(self):
        """The count the rank-penalty result quoted. One arm strictly above the
        other on every run, so only the observed split and its mirror are as
        extreme: p = 2/924 = 0.0022."""
        delta, p = wa.exact_permutation_p([0.7] * 6, [0.6] * 6)
        self.assertAlmostEqual(delta, 0.1)
        self.assertAlmostEqual(p, 0.0022)

    def test_direction_is_al_minus_no(self):
        """A negative delta must read as "the aliases lost", not be absorbed
        into a magnitude — a null or negative result is a real result here."""
        delta, _ = wa.exact_permutation_p([0.4, 0.4], [0.6, 0.6])
        self.assertAlmostEqual(delta, -0.2)

    def test_an_empty_arm_returns_no_verdict(self):
        self.assertEqual(wa.exact_permutation_p([], [1.0]), (None, None))


class PairedTests(unittest.TestCase):
    def test_three_pairs_all_favouring_al(self):
        """diffs [1,1,1]. 2^3 = 8 sign assignments; only all-plus and all-minus
        reach |mean| = 1, so p = 2/8 = 0.25."""
        delta, p = wa.exact_paired_p([(1.0, 0.0)] * 3)
        self.assertAlmostEqual(delta, 1.0)
        self.assertAlmostEqual(p, 0.25)

    def test_six_pairs_all_favouring_al(self):
        """2^6 = 64 assignments, two of them as extreme: p = 2/64 = 0.03125,
        which rounds to 0.0312."""
        delta, p = wa.exact_paired_p([(0.7, 0.6)] * 6)
        self.assertAlmostEqual(delta, 0.1)
        self.assertAlmostEqual(p, 0.0312)

    def test_pairs_that_cancel_are_never_significant(self):
        delta, p = wa.exact_paired_p([(1.0, 0.0), (0.0, 1.0)])
        self.assertAlmostEqual(delta, 0.0)
        self.assertAlmostEqual(p, 1.0)

    def test_rounds_are_matched_by_label_not_by_order(self):
        """Pairing on list position would silently pair round 1 against round 4
        the moment one run is re-run or arrives out of order."""
        al = [{"run_label": "al-opus-r2", "v": 0.7}, {"run_label": "al-opus-r1", "v": 0.5}]
        no = [{"run_label": "no-opus-r1", "v": 0.4}, {"run_label": "no-opus-r2", "v": 0.6}]
        self.assertEqual(wa.paired_values(al, no, lambda r: r["v"]),
                         [(0.5, 0.4), (0.7, 0.6)])

    def test_a_round_missing_one_side_is_dropped(self):
        al = [{"run_label": "al-opus-r1", "v": 0.5}, {"run_label": "al-opus-r2", "v": 0.7}]
        no = [{"run_label": "no-opus-r1", "v": 0.4}]
        self.assertEqual(wa.paired_values(al, no, lambda r: r["v"]), [(0.5, 0.4)])


class ArmStatsTests(unittest.TestCase):
    def test_stats_over_a_hand_written_arm(self):
        s = wa.arm_stats([0.60, 0.62, 0.64])
        self.assertEqual(s["n"], 3)
        self.assertAlmostEqual(s["mean"], 0.62)
        self.assertAlmostEqual(s["min"], 0.60)
        self.assertAlmostEqual(s["max"], 0.64)
        self.assertAlmostEqual(s["sd"], 0.02)

    def test_a_single_run_reports_no_spread(self):
        """One run has no spread to report, and inventing one would be the
        exact overclaim the replicate discipline exists to prevent."""
        self.assertIsNone(wa.arm_stats([0.5])["sd"])


class SystemPromptTests(unittest.TestCase):
    def test_the_budget_appears_and_the_answer_contract_is_intact(self):
        prompt = w3.build_system_prompt(6)
        self.assertIn("at most 6 tool calls", prompt)
        self.assertIn("call 7 will be refused", prompt)
        self.assertIn("ANSWER: no answer found", prompt)
        self.assertIn("memory_search", prompt)

    def test_answer_parsing_is_week_ones(self):
        """The scoring path is imported, not reimplemented — pinned here so a
        divergence from week 1 shows up as a test failure rather than as a
        score that cannot be compared."""
        self.assertEqual(w3.w1.parse_answer("ANSWER: a/b.md, c/d.md"),
                         ("answer", ["a/b.md", "c/d.md"]))
        self.assertEqual(w3.w1.parse_answer("ANSWER: no answer found"),
                         ("no_answer", []))


if __name__ == "__main__":
    unittest.main()
