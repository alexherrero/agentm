#!/usr/bin/env python3
"""alias_pilot.py — targeted, gold-blind alias proposals, propose→confirm.

`wiki/designs/agentm-rejection-and-vocabulary.md` §4. This is not a second
implementation of the alias mechanism: it imports `alias_backfill.py`'s
generation, cleaning, and write primitives directly (`call_model`,
`clean_aliases`, `insert_aliases`, `verify_written`, the corpus-write gate)
rather than re-deriving them, because those primitives are already gold-blind
and already tested. What this module adds is everything the design requires
beyond the backfill:

- **Scope.** `alias_backfill.py run` covers the whole corpus by default. This
  pilot is bounded to a fixed, ≤300-note scope described entirely by
  structural patterns (`_index.md`, `external/`, `PLAN.archive.*.md`) — see
  `in_pilot_scope()` below and the plan's task-1 rationale. Never bulk.
- **Project context.** Each note's prompt carries a short excerpt of its own
  project's `_index.md` (if one exists and the note is not itself that
  index) — `project_context()`. This is derived entirely from the note's own
  path; it is never gold-set-shaped.
- **Propose→confirm.** `propose` only ever writes a journal; it never touches
  the vault. `apply` is a separate, explicit step, gated by
  `agentmd gate corpus-write`, that takes a *reviewed* propose journal and
  writes `aliases:` frontmatter — usable against a frozen-corpus copy (for
  scoring) or the live vault (once the scoring rule has held), without
  re-calling the model either time. `revert` / `reapply` delegate to
  `alias_backfill.py`'s own implementations, unchanged, since the journal
  schema `apply` writes is identical to the one those already round-trip.

**Gold-blindness (design §1's boundary, inherited in full by §4).** Nothing
in this module imports, opens, or otherwise reads `gold-set-v2.json`, any
`scripts/health/` module, or anything under an `alias-oracle` path. The
prompt this module builds is a pure function of a note's own frontmatter,
body, and (optionally) its project's own `_index.md` excerpt — never a gold
question, never anything shaped by one. `test_alias_pilot.py`'s
`GoldBlindnessTests` makes this a mechanical property of the code rather than
a claim about it: it instruments every file open during a full propose run
and fails if any path or any prompt this module builds ever contains a gold
question's literal text or a gold/oracle-shaped path.

**The explicit null hypothesis.** `alias_backfill.py` is the reverted 2026-08
bulk mechanism this pilot must demonstrate it is not a re-run of: same
generation core, but targeted instead of bulk, and gated behind an explicit
apply step instead of writing on generation.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import alias_backfill as ab  # noqa: E402

# ---------------------------------------------------------------------------
# scope — the design's three named categories, applied as structural patterns
# ---------------------------------------------------------------------------


def in_pilot_scope(rel: str) -> bool:
    """True iff `rel` (vault-memory-root-relative) falls in the pilot's fixed
    scope: a project `_index.md`, anything under `external/`, or a
    `PLAN.archive.*.md` — the harness's own name for a closed-out decision
    record (see `CLAUDE.md` § Development flow conventions).

    Purely structural — a filename/path test, never a lookup against any
    note-specific list. See the plan's task-1 rationale for why this is fixed
    before, not tuned against, the oracle's eight targets.
    """
    name = Path(rel).name
    if name == "_index.md":
        return True
    if rel.startswith("external/"):
        return True
    if name.startswith("PLAN.archive.") and name.endswith(".md"):
        return True
    return False


def select_scope(vault: str, rows: list[dict], limit: int) -> list[ab.Candidate]:
    """Eligible candidates inside the fixed scope, capped at `limit`.

    Eligibility reuses `alias_backfill.survey_corpus`'s own rule unmodified
    (unpenalized class, no existing `aliases:`, frontmatter already present —
    this pilot never opts into creating frontmatter from nothing, the more
    aggressive case `alias_backfill` also treats as opt-in) so the pilot
    cannot diverge from the backfill's own eligibility bar by accident.
    """
    census = ab.survey_corpus(vault, rows, subdir=None, create_frontmatter=False)
    scoped = [c for c in census.eligible if in_pilot_scope(c.path)]
    scoped.sort(key=lambda c: c.path)
    return scoped[:limit]


# ---------------------------------------------------------------------------
# project context — derived from the note's own path, nothing else
# ---------------------------------------------------------------------------


def project_context(vault: str, rel: str, max_chars: int = 500) -> str:
    """A short excerpt of the note's own project index, if any.

    Skips a note that is itself the index it would otherwise cite. The
    candidate index path is derived from `rel`'s own directory structure —
    `desk/projects/<name>/_index.md` or `external/<name>/_index.md` — never
    from any external list.
    """
    parts = rel.split("/")
    candidates: list[str] = []
    if parts[:2] == ["desk", "projects"] and len(parts) > 2:
        candidates.append("/".join(parts[:3]) + "/_index.md")
    if parts[:1] == ["external"] and len(parts) > 1:
        candidates.append("/".join(parts[:2]) + "/_index.md")
    for cand in candidates:
        if cand == rel:
            continue
        p = Path(vault, cand)
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="replace").strip()
            return text[:max_chars]
    return ""


# ---------------------------------------------------------------------------
# generation — reuses alias_backfill's task rules, call_model, clean_aliases
# ---------------------------------------------------------------------------

CONTEXT_ADDENDUM = """\
Some notes below include a PROJECT CONTEXT excerpt: the opening of that \
note's own project index, included only so you know what larger effort the \
note belongs to. Do not write aliases for the context — it is background \
for the one note you are aliasing, not a note of its own.
"""

# alias-pilot-structural, task 1. alias-pilot's own content-only prompt
# converted 0 of 8 oracle targets; a lexical-rank diagnostic showed the
# generated aliases never became lexically competitive because the gold
# questions ask in a structural/meta register ("give me a LIST of my
# PENDING ideas") the content-only prompt never targeted. This addendum is
# pure category language — roles and template placeholders, never a gold
# question's own words or anything shaped by one; the gold-blindness
# boundary is unchanged and GoldBlindnessTests covers this variant too.
STRUCTURAL_ADDENDUM = """\
In addition to what the note is ABOUT, also consider what KIND of thing it \
is — the role it plays for someone looking for it later, not just its \
subject. Common roles: a LIST or INDEX of items; a SUMMARY or TLDR of a \
larger body of work; a DECISION or PLAN record (what was decided, and \
whether it shipped); a STATUS or CAPABILITY snapshot (does X still do Y, is \
Z supported); an AUDIT or REVIEW of something.

At least one or two of the aliases you write should target that role \
directly — the phrase someone uses when they remember the SHAPE of what \
they are looking for before they remember its subject: "my list of \
<category>", "the summary of <project>", "where we decided <topic>", "does \
<thing> still support <capability>". Do not invent a role the note does not \
actually have — an ordinary note is not secretly a list just because this \
option exists; use a role phrasing only when the note genuinely reads as \
one of these shapes.
"""


def build_pilot_prompt(batch: list[tuple[ab.Candidate, str]], body_chars: int,
                        variant: str = "content") -> str:
    parts = [ab.TASK_RULES]
    if variant == "structural":
        parts.append("\n" + STRUCTURAL_ADDENDUM)
    parts.append("\n" + CONTEXT_ADDENDUM)
    parts.append("\nNOTES\n")
    for i, (c, context) in enumerate(batch):
        head = c.head.strip()
        if len(head) > 600:
            head = head[:600] + " …"
        body = c.body.strip()
        if len(body) > body_chars:
            body = body[:body_chars] + " …"
        section = [
            f"\n--- note id={i}\n"
            f"path: {c.path}\n"
            f"frontmatter:\n{head}\n"
            f"body:\n{body}\n"
        ]
        if context:
            section.append(f"project context (background only):\n{context}\n")
        parts.append("".join(section))
    parts.append("\nJSON array now, one object per note id 0..%d." % (len(batch) - 1))
    return "".join(parts)


def _propose_batch(batch: list[tuple[ab.Candidate, str]], args: argparse.Namespace) -> list[dict]:
    """Generate for one batch and return per-note proposal records.

    Never writes to any file — that is `apply`'s job, on a separate,
    explicit invocation.
    """
    prompt = build_pilot_prompt(batch, args.body_chars, variant=args.variant)
    notes = [c for c, _ctx in batch]
    last_err = ""
    answers: dict[int, dict] = {}
    for attempt in range(args.retries + 1):
        try:
            answers = ab.parse_response(ab.call_model(prompt, args.model, args.timeout), notes)
            if answers:
                break
        except Exception as exc:  # noqa: BLE001 - the reason is reported, not swallowed
            last_err = f"{type(exc).__name__}: {exc}"
            time.sleep(2 * (attempt + 1))
    records = []
    for i, note in enumerate(notes):
        item = answers.get(i)
        if item is None:
            records.append({"path": note.path, "outcome": "error", "reason": last_err or "no answer"})
            continue
        if item.get("skip"):
            records.append({
                "path": note.path, "outcome": "skip-indeterminable",
                "reason": str(item["skip"])[:200],
            })
            continue
        kept, rejected = ab.clean_aliases(item.get("aliases"), note)
        if len(kept) < ab.MIN_ALIASES:
            records.append({
                "path": note.path, "outcome": "skip-too-few",
                "reason": f"kept {len(kept)} of {len(item.get('aliases') or [])}",
                "rejected": rejected,
            })
            continue
        records.append({
            "path": note.path, "outcome": "aliased", "op": "insert",
            "aliases": kept, "rejected": rejected,
        })
    return records


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_propose(args: argparse.Namespace) -> int:
    """Generate proposals for the fixed scope. Writes only a journal."""
    vault = ab.resolve_memory_root(args.vault)
    rows = ab.classify(args.agentmd, vault)
    scope = select_scope(vault, rows, args.limit)
    print(f"vault: {vault}")
    print(f"scope: {len(scope)} notes (cap {args.limit})")
    print(f"variant: {args.variant}")
    if not scope:
        return 0

    batch_inputs = [(c, project_context(vault, c.path)) for c in scope]
    batches = [batch_inputs[i:i + args.batch] for i in range(0, len(batch_inputs), args.batch)]

    journal = Path(args.journal)
    journal.parent.mkdir(parents=True, exist_ok=True)
    outcomes: Counter = Counter()

    with journal.open("w", encoding="utf-8") as jf:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            for chunk_start in range(0, len(batches), args.jobs):
                chunk = batches[chunk_start:chunk_start + args.jobs]
                for records in pool.map(lambda b: _propose_batch(b, args), chunk):
                    for rec in records:
                        outcomes[rec["outcome"]] += 1
                        rec["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                        jf.write(json.dumps(rec) + "\n")
                jf.flush()

    print("\noutcomes:")
    for k, v in outcomes.most_common():
        print(f"{v:7d}  {k}")
    print(f"\njournal: {journal}")
    print("nothing was written to the vault — review, then run `apply`.")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    """Apply a reviewed propose journal's `aliased` records to a target vault.

    Never re-calls the model — the aliases applied are exactly the ones the
    propose journal recorded, whether the target is a frozen-corpus copy
    (scoring) or the live vault (once the scoring rule has held).

    The corpus-write gate (`agentmd gate corpus-write`) answers "does an undo
    exist" by checking that the vault is a git repository — true of the live
    vault, false of a bare-extracted frozen-corpus scratch copy by
    construction (it is a tarball, not a checkout). The design's own gate
    applies to *live* writes (§4: "Live alias writes are propose→confirm
    behind the corpus-write gate"); scoring against a frozen-corpus copy is
    a measurement arm, verified instead by the manifest diff and the
    row-for-row baseline reproduction the plan's task 3 already requires.
    `--allow-ungated` is that explicit, named opt-out — it exists to write a
    scratch measurement copy, and must never be passed for a live vault path.
    """
    vault = ab.resolve_memory_root(args.vault)
    if not args.dry_run and not args.allow_ungated:
        ab.require_corpus_write_gate(args.agentmd, ab.resolve_vault(args.vault))

    out_journal = Path(args.out_journal)
    out_journal.parent.mkdir(parents=True, exist_ok=True)
    counts: Counter = Counter()

    with out_journal.open("w", encoding="utf-8") as jf:
        for line in Path(args.journal).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("outcome") != "aliased":
                continue
            abs_path = Path(vault, rec["path"])
            if not abs_path.is_file():
                counts["missing"] += 1
                continue
            before = abs_path.read_text(encoding="utf-8")
            m = ab.FRONTMATTER_RE.match(before)
            if not m:
                counts["no-frontmatter"] += 1
                continue
            if ab.existing_aliases(m.group(1)) is not None:
                counts["already-aliased"] += 1
                continue
            try:
                after = ab.insert_aliases(before, rec["aliases"])
                ab.verify_written(after, rec["aliases"])
            except Exception as exc:  # noqa: BLE001
                counts["error"] += 1
                jf.write(json.dumps({
                    "path": rec["path"], "outcome": "error",
                    "reason": f"write refused: {exc}",
                }) + "\n")
                continue
            out_rec = {
                "path": rec["path"], "outcome": "aliased", "op": "insert",
                "aliases": rec["aliases"],
                "sha_before": ab.sha(before), "sha_after": ab.sha(after),
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            if not args.dry_run:
                abs_path.write_text(after, encoding="utf-8")
                counts["applied"] += 1
            else:
                counts["would-apply"] += 1
            jf.write(json.dumps(out_rec) + "\n")

    for k, v in counts.most_common():
        print(f"{v:7d}  {k}")
    print(f"\njournal: {out_journal}")
    if args.dry_run:
        print("dry run — nothing was written")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--vault", help="override the resolved vault path")
    ap.add_argument("--agentmd", default="agentmd", help="path to the agentmd binary")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("propose", help="generate proposals for the fixed scope (journal only)")
    p.add_argument("--limit", type=int, default=300)
    p.add_argument("--batch", type=int, default=15, help="notes per model call")
    p.add_argument("--jobs", type=int, default=4, help="concurrent model calls")
    p.add_argument("--model", default="sonnet")
    p.add_argument("--timeout", type=int, default=300)
    p.add_argument("--retries", type=int, default=2)
    p.add_argument("--body-chars", type=int, default=1100)
    p.add_argument(
        "--variant", choices=["content", "structural"], default="content",
        help=("content: alias-pilot's original prompt, unchanged (default). "
              "structural: also asks for role/meta phrasings (list, index, "
              "summary, decision record, status snapshot) alongside content."),
    )
    p.add_argument("--journal", required=True, help="where proposals are written")
    p.set_defaults(func=cmd_propose)

    a = sub.add_parser("apply", help="apply a reviewed propose journal to a target vault")
    a.add_argument("--journal", required=True, help="a propose journal to apply")
    a.add_argument("--out-journal", required=True, help="apply-time journal, for revert/reapply")
    a.add_argument("--dry-run", action="store_true")
    a.add_argument(
        "--allow-ungated", action="store_true",
        help=("skip the corpus-write gate — for a frozen-corpus SCRATCH COPY only "
              "(it is not a git repo, so the gate cannot answer). Never pass this "
              "for the live vault."),
    )
    a.set_defaults(func=cmd_apply)

    r = sub.add_parser("revert", help="undo an apply, from its out-journal")
    r.add_argument("--journal", required=True)
    r.add_argument("--dry-run", action="store_true")
    r.set_defaults(func=ab.cmd_revert)

    ra = sub.add_parser("reapply", help="put back exactly what revert removed")
    ra.add_argument("--journal", required=True)
    ra.add_argument("--dry-run", action="store_true")
    ra.set_defaults(func=ab.cmd_reapply)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
