#!/usr/bin/env bash
# probe-round-trip.sh — the pre-registered round-trip bar, made repeatable.
#
# The filing-contract build's standing rule is that only the round-trip probe
# marks anything done: save a fact, come back in a fresh session, ask sideways,
# get it back. That bar was read by hand once and left no artifact, so it could
# not be re-read after a change. This is it as a script.
#
# `probe-`, not `verify-`, and deliberately not in `check-all.sh`: it needs a
# running embedder, and optionally a model call. A gate with those dependencies
# is a gate somebody turns off.
#
# ── The bar, written before the run that reads it ───────────────────────────
#
#   Gating — recall. For every sideways question the target is in the top 3, and
#   for the control question it is not. This is the pre-registered wording:
#   "a fact captured, dreamt over, and recalled from a fresh session."
#
#   Reported, not gating — rank. How many questions put the target first. Ranking
#   is explicitly out of this part's scope ("nothing here is a retrieval bar,
#   because nothing in this part changes ranking"), so a rank-1 miss is recorded
#   rather than failed. Recorded so that a change which degrades ranking is
#   visible even while the gate stays green.
#
# ── What makes it a probe rather than a demonstration ───────────────────────
#
#   1. **The dense arm is required.** `fusion` is lexical only, and asking
#      sideways is precisely the case the dense arm exists for. A run without an
#      embedder exits 2 — cannot run — rather than passing, because a probe that
#      quietly measured half the system would read as a pass for the whole.
#
#   2. **Sideways is checked, not asserted.** Each question must share no content
#      word with the note. A question reusing the note's own words proves the
#      index can find a string, not that a memory can be recalled.
#
#   3. **The corpus has distractors.** Recall against one note is not recall.
#      Twelve unrelated notes sit alongside the target.
#
#   4. **There is a negative control.** A question about something else must not
#      return the target. Without it, a ranker that returned the same note for
#      every query would pass every question above.
#
# Usage:
#   bash scripts/probe-round-trip.sh                       # save and recall
#   bash scripts/probe-round-trip.sh --enrich              # the whole arc; spends
#                                                          # one model call
#   bash scripts/probe-round-trip.sh --embedder-url URL --embed-model NAME
#
# Exit: 0 every bar met · 1 a bar missed · 2 could not run.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
PY="${PYTHON:-python3}"

WITH_ENRICH=0
EMBEDDER_URL="${AGENTM_EMBEDDER_URL:-}"
EMBED_MODEL="${AGENTM_EMBED_MODEL:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --enrich) WITH_ENRICH=1; shift ;;
    --embedder-url) EMBEDDER_URL="${2:-}"; shift 2 ;;
    --embed-model) EMBED_MODEL="${2:-}"; shift 2 ;;
    *) echo "probe-round-trip: unknown argument $1" >&2; exit 2 ;;
  esac
done

command -v go >/dev/null 2>&1 || {
  echo "probe-round-trip: go not found; the daemon is the subject" >&2
  exit 2
}

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "probe-round-trip: building the daemon"
AGENTMD="$WORK/agentmd"
( cd "$REPO/daemon" && go build -o "$AGENTMD" ./cmd/agentmd ) || {
  echo "probe-round-trip: the daemon does not build" >&2
  exit 2
}

# Isolation through a scratch kernel config and state directory, passed on every
# invocation. Deliberately *not* by overriding $HOME: the enrichment half shells
# out to `claude`, which reads its credentials from the real home directory, and
# an isolation that hides those turns the model call into "Not logged in".
export AGENTMD AGENTM_STATE_DIR="$WORK"
export WITH_ENRICH EMBEDDER_URL EMBED_MODEL
mkdir -p "$WORK/vault/standards"

PROBE_CONFIG="$WORK/agentm-config.json"
cat > "$PROBE_CONFIG" <<CONFIG
{
  "plugins.obsidian-vault.vault_path": "$WORK/vault",
  "daemon.index_path": "$WORK/index.db"
}
CONFIG
export PROBE_CONFIG

"$AGENTMD" rules --config "$PROBE_CONFIG" --init \
  "$WORK/vault/standards/storage-rules.md" >/dev/null || exit 2

"$PY" - "$WORK" <<'PROBE'
import json
import os
import re
import subprocess
import sys
import pathlib

work = pathlib.Path(sys.argv[1])
binary = os.environ["AGENTMD"]
with_enrich = os.environ.get("WITH_ENRICH") == "1"
embedder_url = os.environ.get("EMBEDDER_URL", "").strip()
embed_model = os.environ.get("EMBED_MODEL", "").strip()

# The fact under test, and the questions. The questions are written the way
# somebody would actually ask months later — by the shape of the problem, not by
# the words of the answer.
FACT = ("The Metal compute buffers page-fault above roughly two thousand "
        "tokens and poison the server, so chunk the input rather than reaching "
        "for a bigger window.")
FACT_TITLE = "Embedder faults on long inputs"

QUESTIONS = [
    "why does the local model die on huge documents",
    "laptop GPU crashes when I feed it a whole file",
    "what breaks when the context gets too big",
]

# A question about something else entirely. If the target comes back for this,
# nothing above means anything.
CONTROL = "how do I rotate the database credentials"

DISTRACTORS = [
    ("Weekly review cadence", "Fridays work better than Mondays for the weekly review because the week is still legible."),
    ("Coffee grind for the Aeropress", "Finer than drip, coarser than espresso; eighteen grams to two hundred grams of water."),
    ("Rotating the database credentials", "The staging database credentials rotate on the first of the month through the secrets manager."),
    ("Standing desk height", "Elbows at ninety degrees, monitor top at eye level, and stand for the first hour after lunch."),
    ("Why the deploy pipeline is slow", "Most of the wall clock is the container build, not the tests, because the base image is rebuilt every run."),
    ("Reading queue for the quarter", "Two books on distributed systems and one on the history of the shipping container."),
    ("Bike maintenance interval", "Chain every three hundred miles, brake pads by feel, and a full service once a year."),
    ("Naming things in the scheduler", "The scheduler calls them jobs, the API calls them tasks, and the docs use both interchangeably."),
    ("Where the spare keys live", "The spare keys are in the drawer under the kettle, not the one by the door."),
    ("Sourdough hydration", "Seventy-five per cent hydration gives an open crumb but a slack dough that is harder to shape."),
    ("Onboarding takes two weeks", "New engineers spend the first week on environment setup, which is longer than the work deserves."),
    ("Timezone handling in reports", "Reports render in the viewer's local timezone, which makes two people disagree about which day a number belongs to."),
]

TOP_N = 3

STOPWORDS = set("""a an and are as at be but by can do does for from get got has have how i
if in into is it its like me my no not of on or so that the their them then there these this
to too up was what when where which who why will with you your did doing dont""".split())


def words(text):
    return {w for w in re.findall(r"[a-z]+", text.lower())
            if w not in STOPWORDS and len(w) > 2}


class CannotRun(SystemExit):
    def __init__(self, why):
        print(f"probe-round-trip: cannot run — {why}", file=sys.stderr)
        super().__init__(2)


def agentmd(*args, allow_fail=False):
    # The config flag belongs to the subcommand, so it follows the name.
    argv = [binary, args[0], "--config", os.environ["PROBE_CONFIG"], *args[1:]]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0 and not allow_fail:
        raise CannotRun(f"agentmd {' '.join(args)} failed ({proc.returncode}): "
                        f"{(proc.stderr or proc.stdout).strip()[:300]}")
    return proc.stdout, proc.returncode


def embedder_args():
    out = []
    if embedder_url:
        out += ["--embedder-url", embedder_url]
    if embed_model:
        out += ["--embed-model", embed_model]
    return out


def capture(title, body, status):
    out, _ = agentmd("capture", "--title", title, "--type", "fix",
                     "--status", status, body)
    return json.loads(out)["path"]


def ask(question):
    """One search, in a process that has never seen the note.

    Refuses a degraded answer rather than scoring it. The daemon says plainly
    when the dense arm contributed nothing — "hybrid was requested but no query
    vector was available; this is the lexical arm alone" — and a probe that read
    past that would be measuring the lexical arm and reporting it as the system.
    """
    out, _ = agentmd("search", "--k", str(TOP_N), "--mode", "hybrid", "--json",
                     "--question", question, *embedder_args(), *question.split())
    try:
        payload = json.loads(out) if out.strip() else {}
    except json.JSONDecodeError:
        raise CannotRun(f"search returned something that is not JSON: {out[:200]!r}")
    said = (payload.get("note") or "").strip()
    if "lexical arm alone" in said or "no query vector" in said:
        raise CannotRun(f"the dense arm contributed nothing to {question!r}. "
                        f"The daemon reported: {said}")
    return [r.get("path", "") for r in (payload.get("results") or [])]


results = []
notes = []


def bar(label, ok, detail=""):
    results.append((label, bool(ok), detail))


def note(text):
    notes.append(text)


# ── the corpus ──────────────────────────────────────────────────────────────
# Status by mode, so that whichever run this is, the target competes on equal
# terms at the moment the questions are asked.
#
# Enriching: the distractors are filed and the target is not. Enrichment's
# eligibility gate takes `status: unfiled`, so this puts the single call on the
# note under test — otherwise it goes to whichever note the queue offers first
# and "dreamt over" is about a different note than the one being recalled. By
# the time the questions run, the target has been filed too.
#
# Not enriching: everything stays unfiled. The alternative — distractors filed,
# target not — leaves the target alone carrying the unfiled ranking penalty, and
# a question it then misses says nothing about recall.
distractor_status = "active" if with_enrich else "unfiled"
for title, body in DISTRACTORS:
    capture(title, body, distractor_status)
target = capture(FACT_TITLE, FACT, "unfiled")
agentmd("reindex")

# ── the dense arm is a precondition, not an option ──────────────────────────
out, rc = agentmd("embed", *embedder_args(), allow_fail=True)
# The count, not the word. "embedded 0 notes" contains "embedded", and an
# unreachable server produces exactly that line with a zero in it — which is how
# this check passed while measuring nothing.
embedded = 0
for line in out.splitlines():
    m = re.search(r"embedded (\d+) notes?", line)
    if m:
        embedded = int(m.group(1))
        note(line.strip())
        break
if rc != 0 or embedded < len(DISTRACTORS) + 1:
    raise CannotRun(
        f"the corpus did not embed ({embedded} of {len(DISTRACTORS) + 1} notes). "
        "`fusion` is lexical only and asking sideways is exactly what the dense "
        "arm is for, so a lexical-only run would measure half the system and "
        "report it as the whole. Start an embedder and pass --embedder-url / "
        "--embed-model. Reported: "
        + (out.strip().splitlines()[-1] if out.strip() else "no output"))

# ── sideways is a property of the questions, so it is checked ───────────────
note_words = words(FACT + " " + FACT_TITLE)
shared = {q: sorted(words(q) & note_words) for q in QUESTIONS}
overlapping = {q: s for q, s in shared.items() if s}
bar("every question is sideways — shares no content word with the note",
    not overlapping,
    "; ".join(f"{q!r} shares {s}" for q, s in overlapping.items()))

# ── dreamt over ─────────────────────────────────────────────────────────────
if with_enrich:
    rep, rc = agentmd("enrich", "--max-calls", "1", "--yes", "--json",
                      allow_fail=True)
    parsed = {}
    try:
        parsed = json.loads(rep)
    except json.JSONDecodeError:
        pass
    wrote = parsed.get("enriched") or parsed.get("written") or 0
    failed = parsed.get("failed") or 0
    considered = parsed.get("considered") or 0
    bar("dreamt over — one enrichment call rewrote the note under test",
        wrote >= 1 and failed == 0,
        f"exit {rc}, considered {considered}, enriched {wrote}, failed {failed}: "
        + "; ".join(parsed.get("errors") or [])[:300])
    agentmd("reindex")
    agentmd("embed", *embedder_args())
else:
    note("enrichment not run — pass --enrich to spend one model call and read "
         "the whole arc; this run reads the save-and-recall half")

# ── ask sideways ────────────────────────────────────────────────────────────
ranks = {}
for q in QUESTIONS:
    paths = ask(q)
    ranks[q] = paths.index(target) + 1 if target in paths else 0
    bar(f"recalled in the top {TOP_N}: {q!r}", ranks[q] != 0,
        f"returned {paths or '(nothing)'}")

hits = sum(1 for r in ranks.values() if r == 1)
shown = ", ".join(str(r) if r else "absent" for r in ranks.values())
note(f"rank 1 on {hits} of {len(QUESTIONS)} questions (ranks: {shown}) — "
     "reported, not gated: ranking is out of this part's scope")

# ── the negative control ────────────────────────────────────────────────────
control_paths = ask(CONTROL)
bar("the control question does not return the target",
    target not in control_paths,
    f"the target came back for {CONTROL!r}, so nothing above is a recall result")

print()
for text in notes:
    if text:
        print(f"  note: {text}")
print()
for label, ok, detail in results:
    print(("  ok   " if ok else "  FAIL ") + label +
          (f"\n         {detail}" if detail and not ok else ""))
sys.exit(0 if all(ok for _, ok, _ in results) else 1)
PROBE
rc=$?

echo
case "$rc" in
  0) if [ "$WITH_ENRICH" -eq 1 ]; then
       echo "probe-round-trip: PASS — saved, dreamt over, recalled sideways"
     else
       echo "probe-round-trip: PASS — saved and recalled sideways (--enrich for the whole arc)"
     fi ;;
  2) echo "probe-round-trip: COULD NOT RUN" ;;
  *) echo "probe-round-trip: FAIL" ;;
esac
exit "$rc"
