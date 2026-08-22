#!/usr/bin/env bash
# verify-declare-a-type.sh — the declare-a-type worked path, across the seam.
#
# The design's own worked example: an operator edits `standards/storage-rules.md`
# and the next cycle notices. The mechanism has a Go end-to-end of its own
# (daemon/cmd/agentmd/declare_a_type_test.go); this script proves the part the Go
# test cannot, which is that the *seam* holds — the built binary, the shipped
# Python, and a vault on disk, with nothing stubbed between them.
#
# That distinction is the whole reason this file exists. The daemon owns the
# ledger and the queues; the harness scripts reach them by shelling out to
# `agentmd`. A change to a flag name, a JSON field, or an exit code breaks the
# harness without breaking a single Go test, and the unit tests on the Python
# side run against a FakeLedger that would never notice.
#
# Checks:
#   A. a corpus enriched under one contract reads as fully covered
#   B. declaring a type changes the contract hash
#   C. coverage falls to zero, and every item's reason is `stale` — the notes did
#      not change, so anything else is the report blaming the corpus
#   D. the reason text names the filing contract, which is what tells a person
#      reading the digest that one edit did this rather than forty
#   E. the discovery stage enqueues that work through the real seam, and the
#      queue the daemon reports holds it
#   F. re-enriching under the new contract brings coverage back
#   G. the rollup stage enqueues its own owner's work off the same corpus, so
#      the cycle's queue holds both kinds the criterion names
#
# Usage:   bash scripts/verify-declare-a-type.sh
# Exit:    0 iff every check passes.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
PY="${PYTHON:-python3}"

pass=0
fail=0
note() { printf '  %s\n' "$1"; }
ok()   { pass=$((pass + 1)); printf '  ok   %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf '  FAIL %s\n' "$1"; }

command -v go >/dev/null 2>&1 || {
  echo "verify-declare-a-type: go not found; skipping (the daemon is the subject)"
  exit 0
}

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "verify-declare-a-type: building the daemon"
AGENTMD="$WORK/agentmd"
( cd "$REPO/daemon" && go build -o "$AGENTMD" ./cmd/agentmd ) || {
  echo "verify-declare-a-type: the daemon does not build" >&2
  exit 2
}
export AGENTMD
export REPO="$REPO"

# Isolation, through the only channels the harness's own calls go through.
# $HOME last: the kernel config is resolved from it, and left alone this run
# would read the operator's real vault path out of it.
export MEMORY_VAULT_PATH="$WORK/vault"
export AGENTM_STATE_DIR="$WORK"
export HOME="$WORK"

# The whole arc is one Python program rather than a series of shell calls,
# because the point is the seam the harness actually uses — work_ledger's
# subprocess calls into the binary — and re-implementing those in bash would
# test a path nothing ships.
"$PY" - "$WORK" <<'ARC'
import json
import os
import subprocess
import sys
import pathlib

work = pathlib.Path(sys.argv[1])
repo = pathlib.Path(os.environ["REPO"])

vault = work / "vault"
(vault / "standards").mkdir(parents=True, exist_ok=True)
(vault / "memory").mkdir(parents=True, exist_ok=True)
binary = os.environ["AGENTMD"]

results = []


def check(label, condition, detail=""):
    results.append((label, bool(condition), detail))


def rules_file(*types):
    routing = "".join(f"  {t}: memory/semantic\n" for t in types)
    return (
        "# Storage rules\n\n```storage-rules\n"
        "classes:\n  semantic: Facts.\n  procedural: How.\n  episodic: Traces.\n"
        "  entities: Referents.\n  crystallized: Lessons.\n  mocs: Maps.\n"
        f"memory_types: [{', '.join(types)}]\n"
        f"default_type: {types[0]}\n"
        f"routing:\n{routing}"
        "record_kinds: [brief]\ndeprecations: {}\nwarrants: {}\n"
        "thresholds: {low_confidence: 0.65}\n```\n"
    )


def write_rules(*types):
    (vault / "standards" / "storage-rules.md").write_text(rules_file(*types))


def agentmd(*args):
    proc = subprocess.run(
        [binary, *args],
        capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise SystemExit(
            f"agentmd {' '.join(args)} failed ({proc.returncode}):\n"
            f"{proc.stderr or proc.stdout}")
    return proc.stdout


def contract_hash():
    return json.loads(agentmd("rules", "--json"))["hash"]


def write_note(rel, rules_hash, marker):
    body = (
        "---\n"
        "title: a note\n"
        "type: preference\n"
        "status: unfiled\n"
        f"enriched_by: {version}\n"
        f"rules_hash: {rules_hash}\n"
        "enriched_at: 2026-08-20T09:00:00Z\n"
        "---\n\n"
        f"The body, {marker}. It refers to alexherrero/agentm, which is how an\n"
        "entity comes to be mentioned in enough notes to be worth a file.\n"
    )
    (vault / rel).write_text(body)


# ── A. a corpus enriched under one contract reads as covered ────────────────
write_rules("preference", "convention")
# The pass version comes from the binary rather than from a constant here, so a
# version bump does not quietly turn this into a test of nothing.
version = json.loads(agentmd("ledger", "--pending", "--json"))["version"]
first = contract_hash()
# Six, not three: the rollup floor is five mentions, and a corpus below it would
# leave check G passing because the stage had nothing to find.
CORPUS = ("a", "b", "c", "d", "e", "f")
for name in CORPUS:
    write_note(f"memory/{name}.md", first, "written under the first contract")

agentmd("reindex")
agentmd("ledger", "--rebuild")

report = json.loads(agentmd("ledger", "--pending", "--json", "--limit", "0"))
check("A. a corpus enriched under its contract reads as covered",
      report["eligible"] == len(CORPUS) and report["current"] == len(CORPUS),
      f'coverage {report["current"]}/{report["eligible"]}')

# The population is the control for everything below: over an empty corpus,
# "fully covered" and "nothing covered" are the same three zeroes.
if report["eligible"] != len(CORPUS):
    print(f"  the eligible population is {report['eligible']}, not {len(CORPUS)} — "
          f"every check below would be vacuous", file=sys.stderr)

# ── B. declaring a type changes the hash ────────────────────────────────────
write_rules("preference", "convention", "recipe")
second = contract_hash()
check("B. declaring a type changes the contract hash", second != first,
      f"{first} -> {second}")

# ── C/D. coverage falls, and the report says why ────────────────────────────
after = json.loads(agentmd("ledger", "--pending", "--json", "--limit", "0"))
check("C. coverage falls to zero over the same population",
      after["current"] == 0 and after["eligible"] == len(CORPUS)
      and len(after.get("pending") or []) == len(CORPUS),
      f'coverage {after["current"]}/{after["eligible"]}, '
      f'{len(after.get("pending") or [])} pending')

reasons = {item.get("reason") for item in (after.get("pending") or [])}
check("C. every item reads as stale rather than changed", reasons == {"stale"},
      f"reasons: {sorted(reasons)}")

details = [item.get("detail", "") for item in (after.get("pending") or [])]
check("D. the reason names the filing contract",
      all("filing contract" in d for d in details),
      f"details: {details[:1]}")

check("D. the report carries the contract it was taken against",
      after.get("rules_hash") == second,
      f'rules_hash: {after.get("rules_hash")}')

# ── E. the discovery stage enqueues it, through the real seam ───────────────
sys.path.insert(0, str(repo / "harness/skills/memory/scripts"))
import dream_stages  # noqa: E402

res = dream_stages.stage_unfiled_drain(enabled=True, budget=2)
check("E. the discovery stage enqueues under its budget",
      not res.unavailable and res.enqueued == 2,
      f"enqueued {res.enqueued}, unavailable={res.unavailable}")

queues = json.loads(agentmd("queue", "--owner", "enrich", "--json"))
depth = queues[0]["depth"] if queues else 0
check("E. the daemon's own queue holds that work", depth == 2,
      f"depth {depth}")

# ── F. re-enrichment brings coverage back ───────────────────────────────────
for name in CORPUS:
    write_note(f"memory/{name}.md", second, "re-enriched under the second contract")
agentmd("reindex")
agentmd("ledger", "--rebuild")

back = json.loads(agentmd("ledger", "--pending", "--json", "--limit", "0"))
check("F. coverage climbs back over the same population",
      back["current"] == len(CORPUS) and back["eligible"] == len(CORPUS),
      f'coverage {back["current"]}/{back["eligible"]}')

# ── G. and the rollup half of the same cycle ────────────────────────────────
roll = dream_stages.stage_entity_rollups()
check("G. the rollup stage enqueues work off the same corpus",
      not roll.unavailable and roll.enqueued >= 1,
      f"considered {roll.considered}, enqueued {roll.enqueued}, "
      f"unavailable={roll.unavailable}")

owners = {q["owner"]: q["depth"]
          for q in json.loads(agentmd("queue", "--json")) or []}
check("G. the cycle's queue holds both kinds of work the criterion names",
      owners.get("enrich", 0) >= 1 and owners.get("entity-rollup", 0) >= 1,
      f"owners: {owners}")

for label, good, detail in results:
    print(("  ok   " if good else "  FAIL ") + label +
          (f"  [{detail}]" if detail and not good else ""))
sys.exit(0 if all(g for _, g, _ in results) else 1)
ARC
rc=$?

if [ "$rc" -eq 0 ]; then
  echo "verify-declare-a-type: PASS"
else
  echo "verify-declare-a-type: FAIL"
fi
exit "$rc"
