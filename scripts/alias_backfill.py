#!/usr/bin/env python3
"""alias_backfill.py — write `aliases` frontmatter across the existing corpus.

This is dreaming's first real job, run by hand before dreaming exists to run it
on a schedule.

Week 1 measured paraphrase recall at R@5 0.472 and found the cause on the write
side rather than the read side: six questions missed under every ranking
configuration because the words the operator asked with were never in the note.
The daemon already indexes an `aliases`+`tags` column weighted above body, and it
already measures as a no-op — 56 notes out of ~8,700 carry an alias. This fills
that column.

What it does, and nothing else: adds one `aliases:` line to a note's existing
frontmatter. It never edits a body, never moves a file, never creates
frontmatter where there is none, and never touches a note that already has
aliases. Recovering the destroyed signal in truncated miner fragments is a real
job and a different one.

Eligibility comes from `agentmd classify --json` — the daemon's own classifier,
not a second implementation of it. A note is eligible when it carries no
penalized class (`fragment` / `status` / `staging`); `fragment-promoted` is
eligible because the status gate already spares it, but only where the model can
still tell what the note is about. Where it cannot, the note is skipped and
counted, because a confident alias invented from a garbled fragment poisons
retrieval more quietly than no alias at all.

    python3 scripts/alias_backfill.py survey
    python3 scripts/alias_backfill.py run --limit 50 --dry-run
    python3 scripts/alias_backfill.py run
    python3 scripts/alias_backfill.py revert --journal <path>
    python3 scripts/alias_backfill.py reapply --journal <path>

Resumable by construction: a note with a non-empty `aliases` list is done, so a
re-run picks up exactly what is left and cannot duplicate or overwrite.

`run` and `reapply` are corpus-wide writes and ask `agentmd gate corpus-write`
first; a refusal stops them, and so does a gate that could not answer. `revert`
does not ask, because gating the undo on there being an undo is the one
arrangement that could strand the corpus.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpus_gate import require_corpus_write_gate  # noqa: E402

# The classifier's penalized classes. `fragment-promoted` is deliberately absent:
# it is classified but unpenalized, which is the status gate, and those notes are
# in scope.
PENALIZED = {"fragment", "status", "staging"}

FRONTMATTER_RE = re.compile(r"\A---[ \t\r]*\n(.*?)\n---[ \t\r]*\n", re.S)
ALIASES_KEY_RE = re.compile(r"(?m)^aliases:[ \t]*(.*)$")
TITLE_RE = re.compile(r"(?m)^title:[ \t]*(.+?)[ \t\r]*$")

# An alias goes into an inline YAML flow list. Rather than guess which characters
# survive that, the renderer quotes when it has to and every write is parsed back
# before it is trusted — so the only characters banned outright are the ones with
# no readable representation at all.
#
# The first cut of this banned apostrophes, which silently threw away
# "don't rewrite whole files" — a phrasing that parses fine and is exactly the
# kind of alias the job exists to write. Guessing at YAML is how that happened.
FORBIDDEN_IN_ALIAS = set("\n\r\t\x00")

# Plain (unquoted) flow scalars stay readable in Obsidian's property editor, so
# they are preferred where they are unambiguous. `?` and `#` are flow indicators,
# `: ` opens a mapping, and `[]{},` are structure.
PLAIN_SAFE_RE = re.compile(r"\A[A-Za-z0-9][^\[\]{},?#\n\r\t]*\Z")

# Notes with no frontmatter at all are eligible by the classifier and cannot be
# edited in place, because there is no block to add a key to. Giving them one is a
# structural change rather than a field, so it is opt-in (`--create-frontmatter`)
# and it is not applied to everything — a third of that population is machine
# exhaust that would rank against real memories for nothing.
#
# The rule is a path allowlist rather than a heuristic on purpose: what counts as
# a real document here is a judgement, and a judgement should be readable and
# arguable rather than inferred from a word count.
ARTIFACT_PREFIXES = {
    # Scraped upstream READMEs, kept as a diff cache. External text, not memory.
    "_meta/skill-discovery-cache/": "raw upstream README diff",
    # Regenerated wikilink indexes — a title and a bare list of links, no prose,
    # and rewritten wholesale by auto-organization whenever it runs.
    "_moc/": "regenerated link index",
    # Staging exhaust for proposals that are themselves a penalized class.
    "desk/scratch/": "dream-staging digest",
    # Machine lint and link-suggestion reports, dated and superseded weekly.
    "_meta/archive/": "machine lint report",
    "_meta/vault-lint-": "machine lint report",
}


def frontmatter_is_worth_creating(rel: str) -> str | None:
    """Return None if this note should get a frontmatter block, else the reason not to."""
    for prefix, reason in ARTIFACT_PREFIXES.items():
        if rel.startswith(prefix):
            return reason
    return None


MIN_ALIASES = 3
MAX_ALIASES = 6
MIN_ALIAS_WORDS = 2
MAX_ALIAS_CHARS = 70


# ---------------------------------------------------------------------------
# the corpus
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    """One note the backfill might write to."""

    path: str
    flags: list[str]
    status: str
    raw: str = ""
    head: str = ""
    body: str = ""
    promoted_fragment: bool = False
    # True when the note has no frontmatter and one has to be created for the
    # alias to have anywhere to live.
    needs_frontmatter: bool = False

    @property
    def title(self) -> str:
        m = TITLE_RE.search(self.head)
        if m:
            return m.group(1).strip().strip("\"'")
        return Path(self.path).stem.replace("-", " ").replace("_", " ")


def resolve_vault(explicit: str | None) -> str:
    """Resolve the vault the same way everything else in this repo does.

    Never a literal. `$MEMORY_VAULT_PATH` is the per-invocation override, then the
    kernel config's `plugins.obsidian-vault.vault_path`.
    """
    if explicit:
        return explicit
    env = os.environ.get("MEMORY_VAULT_PATH")
    if env:
        return env
    cfg_path = Path(os.environ.get("AGENTM_CONFIG") or "")
    if not cfg_path.is_file():
        cfg_path = Path.home() / ".claude" / ".agentm-config.json"
    cfg = json.loads(cfg_path.read_text())
    # The kernel stores the plugin key flat-with-dots (V5-7); the nested form is
    # read too so this does not become the one place that disagrees.
    path = (
        cfg.get("plugins.obsidian-vault.vault_path")
        or cfg.get("plugins", {}).get("obsidian-vault", {}).get("vault_path")
        or cfg.get("vault_path")
    )
    if not path:
        raise SystemExit(
            f"no vault path in {cfg_path} — set plugins.obsidian-vault.vault_path "
            f"or pass --vault"
        )
    return path


def resolve_memory_root(explicit: str | None) -> str:
    """Resolve the agent's own tree — what a corpus-wide job reads and rewrites.

    `resolve_vault()` above returns the *repository* root, which is what the
    corpus-write gate has to see: it decides whether an undo exists, and that
    question is answered by git at the repository root. Since the 2026-08-10
    git-transport cutover those are not the same directory — the repo root is
    the whole Obsidian vault so wikilinks resolve across the operator's folders,
    while the agent's tree is one level down. This module needs both, and
    handing the gate a memory root makes it refuse with `git-degraded`.

    An explicit `--vault` or `$MEMORY_VAULT_PATH` already names a memory tree by
    the convention every other consumer follows, so the prefix is not re-applied
    to either.
    """
    if explicit:
        return explicit
    env = os.environ.get("MEMORY_VAULT_PATH")
    if env:
        return env
    root = resolve_vault(None)
    cfg_path = Path(os.environ.get("AGENTM_CONFIG") or "")
    if not cfg_path.is_file():
        cfg_path = Path.home() / ".claude" / ".agentm-config.json"
    try:
        cfg = json.loads(cfg_path.read_text())
    except (OSError, json.JSONDecodeError):
        return root
    rel = cfg.get("plugins.obsidian-vault.memory_root")
    if not isinstance(rel, str):
        return root
    candidate = rel.strip().replace("\\", "/")
    # Absoluteness before stripping — "/etc" must not strip to a usable "etc".
    if not candidate or candidate.startswith("/") or ":" in candidate:
        return root
    parts = candidate.strip("/").split("/")
    if not parts or "" in parts or ".." in parts:
        return root
    return str(Path(root).joinpath(*parts))


def classify(agentmd: str, vault: str) -> list[dict]:
    """Ask the daemon's classifier what every note in the vault is.

    Shelling out rather than reimplementing is the point: the classifier's
    behaviour is pinned by the daemon's own tests and drift check, and a second
    copy of it here would be a second thing to keep true.
    """
    proc = subprocess.run(
        [agentmd, "classify", "--json", "--vault", vault],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"agentmd classify failed:\n{proc.stderr.strip()}")
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def existing_aliases(head: str) -> str | None:
    """Return the note's current aliases as text, or None if it has no such key.

    Both YAML forms count. An empty inline list (`aliases: []`) reads as absent —
    it is a placeholder, not an answer — while a block list under the key counts
    as present.
    """
    m = ALIASES_KEY_RE.search(head)
    if not m:
        return None
    inline = m.group(1).strip()
    if inline and inline not in ("[]", "[ ]", "~", "null"):
        return inline
    rest = head[m.end():]
    items = []
    for line in rest.split("\n")[1:] if rest.startswith("\n") else rest.split("\n"):
        if not line[:1] in (" ", "\t"):
            break
        stripped = line.strip()
        if not stripped.startswith("-"):
            break
        items.append(stripped.lstrip("-").strip())
    if any(items):
        return ", ".join(items)
    return None


@dataclass
class Census:
    eligible: list[Candidate] = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)
    skipped_dirs: Counter = field(default_factory=Counter)


def survey_corpus(
    vault: str,
    rows: list[dict],
    subdir: str | None = None,
    create_frontmatter: bool = False,
) -> Census:
    """Split the corpus into what this job writes to and what it leaves alone."""
    census = Census()
    for row in rows:
        rel = row["path"]
        if subdir and not rel.startswith(subdir):
            continue
        flags = set(row["flags"])
        census.counts["total"] += 1
        if flags & PENALIZED:
            census.counts["skip:penalized-class"] += 1
            for f in sorted(flags & PENALIZED):
                census.counts[f"  penalized:{f}"] += 1
            continue
        raw = Path(vault, rel).read_text(encoding="utf-8", errors="replace")
        m = FRONTMATTER_RE.match(raw)
        promoted = "fragment-promoted" in flags
        if not m:
            reason = frontmatter_is_worth_creating(rel)
            if not create_frontmatter:
                census.counts["skip:no-frontmatter"] += 1
                census.skipped_dirs[
                    rel.rsplit("/", 1)[0] if "/" in rel else "(root)"
                ] += 1
                continue
            if reason:
                census.counts[f"skip:machine-artifact ({reason})"] += 1
                continue
            census.counts["eligible:needs-frontmatter"] += 1
            census.eligible.append(
                Candidate(
                    path=rel,
                    flags=sorted(flags),
                    status=row.get("status", ""),
                    raw=raw,
                    head="",
                    body=raw,
                    promoted_fragment=promoted,
                    needs_frontmatter=True,
                )
            )
            continue
        head = m.group(1)
        if existing_aliases(head) is not None:
            census.counts["skip:already-aliased"] += 1
            continue
        census.counts[
            "eligible:fragment-promoted" if promoted else "eligible:unpenalized"
        ] += 1
        census.eligible.append(
            Candidate(
                path=rel,
                flags=sorted(flags),
                status=row.get("status", ""),
                raw=raw,
                head=head,
                body=raw[m.end():],
                promoted_fragment=promoted,
            )
        )
    return census


# ---------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You write retrieval aliases for notes in a personal memory vault. You reply with \
JSON and nothing else — no preamble, no code fence, no commentary."""

TASK_RULES = """\
For each note below, write the phrasings someone would plausibly type when \
looking for that note months later, having forgotten how it was written.

An alias earns its place by adding VOCABULARY the note does not already contain. \
Reordering the title's words adds nothing — a search index stems and sorts them \
away. Write the other words for the same idea: the synonym, the symptom, the \
consequence, the question form, the jargon the note avoids, the plain phrase the \
note states in jargon.

Worked example. A note titled "Use Edit not Write for existing files" whose body \
explains that output tokens are billed ~5x input.
  BAD  (word order only, adds no vocabulary):
       "use edit rather than write", "Edit over Write for existing files"
  GOOD (different ways in):
       "don't rewrite whole files", "token cost of full-file writes",
       "why patch instead of replacing a file", "cheaper way to change a file"

Rules:
- 3 to 6 aliases per note. Fewer honest ones beat padding.
- Each is 2 to 10 words, lowercase unless a proper noun demands otherwise.
- No commas, colons, brackets, question marks or hashes. Apostrophes, hyphens and
  slashes are fine — "don't rewrite whole files" is a good alias.
- Do not repeat the title or the note's filename back with the words shuffled.
- Do not invent facts the note does not contain. An alias is a way of ASKING for \
this note, not a claim about the world.
- Stay concrete. "important information" and "useful note" match everything and \
therefore find nothing.

SKIPPING. Some notes are truncated mid-sentence — a fragment clipped out of a \
transcript, often opening or closing with an ellipsis. Alias one only when you can \
still tell what it is ABOUT from what survived. If the surviving text is too \
garbled, or is a bare placeholder with no subject of its own (for instance a \
mining stub that only records how many times a tool was invoked), return \
"skip" with a one-clause reason instead of guessing. A confident alias invented \
from a garbled fragment is worse than no alias, because nothing downstream will \
ever tell you it is wrong.

Reply with a JSON array, one object per note, in the same order, each shaped:
  {"id": <the note's id>, "aliases": ["...", "..."]}
or, when you cannot tell what the note is about:
  {"id": <the note's id>, "skip": "<short reason>"}
"""


def build_batch_prompt(batch: list[Candidate], body_chars: int) -> str:
    parts = [TASK_RULES, "\nNOTES\n"]
    for i, c in enumerate(batch):
        head = c.head.strip()
        if len(head) > 600:
            head = head[:600] + " …"
        body = c.body.strip()
        if len(body) > body_chars:
            body = body[:body_chars] + " …"
        parts.append(
            f"\n--- note id={i}\n"
            f"path: {c.path}\n"
            f"frontmatter:\n{head}\n"
            f"body:\n{body}\n"
        )
    parts.append("\nJSON array now, one object per note id 0..%d." % (len(batch) - 1))
    return "".join(parts)


JSON_ARRAY_RE = re.compile(r"\[.*\]", re.S)


def call_model(prompt: str, model: str, timeout: int) -> str:
    """One `claude -p` call.

    `--settings '{"disableAllHooks":true}'` is load-bearing: without it this
    project's own recall hooks fire on every invocation, which is both slow and a
    way for the vault to end up in the prompt that is writing to the vault.
    """
    cmd = [
        "claude",
        "-p",
        prompt,
        "--model",
        model,
        "--settings",
        '{"disableAllHooks":true}',
        "--system-prompt",
        SYSTEM_PROMPT,
        "--disallowed-tools",
        "Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch,Task,ToolSearch",
        # Not 1. Tool use is already blocked by --disallowed-tools, and a batch
        # where the honest answer is "skip all twelve" reliably needs a second
        # turn — capping at one turned 132 of those into hard errors rather than
        # into skips.
        "--max-turns",
        "4",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        # A non-zero exit with an empty stderr is common enough here — usage
        # limits come back that way — that reporting only stderr produced 132
        # journal lines saying "failed: " and nothing else.
        detail = proc.stderr.strip() or proc.stdout.strip() or "(no output)"
        raise RuntimeError(f"claude -p exit {proc.returncode}: {detail[:400]}")
    return proc.stdout.strip()


def parse_response(text: str, batch: list[Candidate]) -> dict[int, dict]:
    m = JSON_ARRAY_RE.search(text)
    if not m:
        raise RuntimeError(f"no JSON array in response: {text[:300]!r}")
    payload = json.loads(m.group(0))
    out: dict[int, dict] = {}
    for item in payload:
        if not isinstance(item, dict) or "id" not in item:
            continue
        try:
            idx = int(item["id"])
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(batch):
            out[idx] = item
    return out


def clean_aliases(raw_list, note: Candidate) -> tuple[list[str], list[str]]:
    """Filter a model's aliases down to the ones that are safe and worth writing.

    Returns (kept, rejected-with-reason). Rejection is per-alias rather than
    per-note: one bad phrasing should not cost a note its other four.
    """
    kept: list[str] = []
    rejected: list[str] = []
    title_words = set(re.findall(r"[a-z0-9]+", note.title.lower()))
    stem_words = set(re.findall(r"[a-z0-9]+", Path(note.path).stem.lower()))
    seen = set()
    for item in raw_list or []:
        if not isinstance(item, str):
            rejected.append(f"not-a-string:{item!r}")
            continue
        alias = " ".join(item.split()).strip().strip(".")
        # A leading YAML indicator has to go regardless of quoting, and it is never
        # load-bearing in a phrase someone would type.
        alias = alias.lstrip("-?:,[]{}#&*!|>'\"%@` ")
        low = alias.lower()
        if not alias:
            continue
        if set(alias) & FORBIDDEN_IN_ALIAS:
            rejected.append(f"unprintable:{alias!r}")
            continue
        if len(alias) > MAX_ALIAS_CHARS:
            rejected.append(f"too-long:{alias}")
            continue
        words = re.findall(r"[a-z0-9]+", low)
        if len(words) < MIN_ALIAS_WORDS:
            rejected.append(f"too-short:{alias}")
            continue
        if low in seen:
            rejected.append(f"duplicate:{alias}")
            continue
        # A permutation of the title or the filename contributes no vocabulary the
        # index does not already have from those two fields. This is the mechanical
        # half of "differ in vocabulary, not word order"; the prompt is the other.
        if words and set(words) <= title_words:
            rejected.append(f"title-permutation:{alias}")
            continue
        if words and set(words) <= stem_words:
            rejected.append(f"slug-permutation:{alias}")
            continue
        seen.add(low)
        kept.append(alias)
        if len(kept) >= MAX_ALIASES:
            break
    return kept, rejected


# ---------------------------------------------------------------------------
# the write
# ---------------------------------------------------------------------------


def _plain_safe(alias: str) -> bool:
    return bool(PLAIN_SAFE_RE.match(alias)) and ": " not in alias and " #" not in alias


def render_line(aliases: list[str]) -> str:
    """Render the aliases as one inline YAML flow list.

    Plain where every value is unambiguous, double-quoted otherwise. All-or-
    nothing rather than per-value so the line reads consistently, and so `revert`
    can reproduce it from the journal without re-deciding anything.
    """
    if all(_plain_safe(a) for a in aliases):
        return "aliases: [" + ", ".join(aliases) + "]"
    quoted = [a.replace("\\", "\\\\").replace('"', '\\"') for a in aliases]
    return 'aliases: ["' + '", "'.join(quoted) + '"]'


def insert_aliases(raw: str, aliases: list[str]) -> str:
    """Add one `aliases:` line as the last key of the existing frontmatter.

    Appending rather than inserting at a chosen position is deliberate: a
    top-level key is valid last no matter what shape the block above it has, and
    the alternative needs to understand block lists, nested maps, and comments to
    avoid landing inside one of them.
    """
    m = FRONTMATTER_RE.match(raw)
    if not m:
        raise ValueError("no frontmatter")
    head = m.group(1)
    if ALIASES_KEY_RE.search(head):
        raise ValueError("already has an aliases key")
    # No normalizing of what is already there — not even trailing blank lines.
    # Anything this rewrites beyond the one added line is a byte `revert` can no
    # longer restore, and a backfill that cannot be undone is not a backfill.
    new_head = head + "\n" + render_line(aliases)
    return raw[: m.start(1)] + new_head + raw[m.end(1):]


def create_frontmatter_block(raw: str, aliases: list[str]) -> str:
    """Give a note with no frontmatter one, containing the aliases and nothing else.

    Deliberately not `type` or `status`. Those are filing decisions, this job is
    not the filing pass, and a guessed `status` would change the note's rank
    penalty — writing `unfiled` here would demote several hundred real documents
    to make a frontmatter block look complete.
    """
    if FRONTMATTER_RE.match(raw):
        raise ValueError("already has frontmatter")
    return "---\n" + render_line(aliases) + "\n---\n\n" + raw


def created_prefix(aliases: list[str]) -> str:
    """The exact bytes `create_frontmatter_block` prepends, so revert can remove them."""
    return "---\n" + render_line(aliases) + "\n---\n\n"


def verify_written(text: str, aliases: list[str]) -> None:
    """Parse the result before trusting it.

    A frontmatter block this job corrupts is a note the daemon stops reading
    correctly, so the YAML is parsed back and the values compared to what was
    asked for. yaml is optional only so the script still runs without it.
    """
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError("frontmatter disappeared")
    try:
        import yaml
    except ImportError:  # pragma: no cover - environment without pyyaml
        return
    doc = yaml.safe_load(m.group(1))
    if not isinstance(doc, dict):
        raise ValueError(f"frontmatter no longer a mapping: {type(doc).__name__}")
    got = doc.get("aliases")
    if got != aliases:
        raise ValueError(f"aliases round-tripped wrong: {got!r} != {aliases!r}")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_survey(args: argparse.Namespace) -> int:
    vault = resolve_memory_root(args.vault)
    census = survey_corpus(
        vault, classify(args.agentmd, vault), args.subdir, args.create_frontmatter
    )
    print(f"vault: {vault}")
    for key, val in sorted(census.counts.items()):
        print(f"{val:7d}  {key}")
    if census.skipped_dirs:
        print("\nno-frontmatter notes by directory (top 15):")
        for d, n in census.skipped_dirs.most_common(15):
            print(f"{n:7d}  {d}")
    return 0


def _work_batch(batch: list[Candidate], args: argparse.Namespace) -> list[dict]:
    """Generate for one batch and return per-note outcome records."""
    prompt = build_batch_prompt(batch, args.body_chars)
    last_err = ""
    answers: dict[int, dict] = {}
    for attempt in range(args.retries + 1):
        try:
            answers = parse_response(call_model(prompt, args.model, args.timeout), batch)
            if answers:
                break
        except Exception as exc:  # noqa: BLE001 - the reason is reported, not swallowed
            last_err = f"{type(exc).__name__}: {exc}"
            time.sleep(2 * (attempt + 1))
    records = []
    for i, note in enumerate(batch):
        item = answers.get(i)
        if item is None:
            records.append(
                {"path": note.path, "outcome": "error", "reason": last_err or "no answer"}
            )
            continue
        if item.get("skip"):
            records.append(
                {
                    "path": note.path,
                    "outcome": "skip-indeterminable",
                    "reason": str(item["skip"])[:200],
                    "promoted_fragment": note.promoted_fragment,
                }
            )
            continue
        kept, rejected = clean_aliases(item.get("aliases"), note)
        if len(kept) < MIN_ALIASES:
            records.append(
                {
                    "path": note.path,
                    "outcome": "skip-too-few",
                    "reason": f"kept {len(kept)} of {len(item.get('aliases') or [])}",
                    "rejected": rejected,
                    "promoted_fragment": note.promoted_fragment,
                }
            )
            continue
        records.append(
            {
                "path": note.path,
                "outcome": "aliased",
                "op": "create" if note.needs_frontmatter else "insert",
                "aliases": kept,
                "rejected": rejected,
                "promoted_fragment": note.promoted_fragment,
            }
        )
    return records


def cmd_run(args: argparse.Namespace) -> int:
    vault = resolve_memory_root(args.vault)
    if not args.dry_run:
        # The gate is asked about the REPOSITORY, not the memory tree — it
        # answers "does an undo exist", and git answers that at the repo root.
        require_corpus_write_gate(args.agentmd, resolve_vault(args.vault))
    census = survey_corpus(
        vault, classify(args.agentmd, vault), args.subdir, args.create_frontmatter
    )
    todo = census.eligible
    if args.only_promoted:
        todo = [c for c in todo if c.promoted_fragment]
    if args.only_unpenalized:
        todo = [c for c in todo if not c.promoted_fragment]
    if args.only_new_frontmatter:
        todo = [c for c in todo if c.needs_frontmatter]
    todo.sort(key=lambda c: c.path)
    if args.sample:
        # An evenly-strided slice rather than the first N. The first fifty notes in
        # path order all come from two directories, which would make a hand-check of
        # them a check of two directories.
        step = max(len(todo) // args.sample, 1)
        todo = todo[::step][: args.sample]
    if args.limit:
        todo = todo[args.offset: args.offset + args.limit]
    elif args.offset:
        todo = todo[args.offset:]

    print(f"vault:    {vault}")
    print(f"eligible: {len(census.eligible)}  selected: {len(todo)}")
    if not todo:
        return 0

    journal = Path(args.journal)
    journal.parent.mkdir(parents=True, exist_ok=True)

    batches = [
        todo[i: i + args.batch] for i in range(0, len(todo), args.batch)
    ]
    outcomes: Counter = Counter()
    applied = 0
    started = time.time()

    with journal.open("a", encoding="utf-8") as jf:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            for chunk_start in range(0, len(batches), args.jobs):
                chunk = batches[chunk_start: chunk_start + args.jobs]
                for records in pool.map(lambda b: _work_batch(b, args), chunk):
                    for rec in records:
                        outcomes[rec["outcome"]] += 1
                        if rec["outcome"] != "aliased":
                            rec["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                            jf.write(json.dumps(rec) + "\n")
                            continue
                        abs_path = Path(vault, rec["path"])
                        before = abs_path.read_text(encoding="utf-8")
                        try:
                            if rec["op"] == "create":
                                after = create_frontmatter_block(before, rec["aliases"])
                            else:
                                after = insert_aliases(before, rec["aliases"])
                            verify_written(after, rec["aliases"])
                        except Exception as exc:  # noqa: BLE001
                            outcomes["aliased"] -= 1
                            outcomes["error"] += 1
                            jf.write(
                                json.dumps(
                                    {
                                        "path": rec["path"],
                                        "outcome": "error",
                                        "reason": f"write refused: {exc}",
                                    }
                                )
                                + "\n"
                            )
                            continue
                        rec["sha_before"] = sha(before)
                        rec["sha_after"] = sha(after)
                        rec["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                        if not args.dry_run:
                            abs_path.write_text(after, encoding="utf-8")
                            applied += 1
                        jf.write(json.dumps(rec) + "\n")
                    jf.flush()
                done = min((chunk_start + args.jobs) * args.batch, len(todo))
                rate = done / max(time.time() - started, 1e-9)
                print(
                    f"  {done}/{len(todo)}  applied={applied}  "
                    f"{dict(outcomes)}  {rate:.1f} notes/s",
                    flush=True,
                )
                if args.pause:
                    time.sleep(args.pause)

    print("\noutcomes:")
    for k, v in outcomes.most_common():
        print(f"{v:7d}  {k}")
    print(f"\njournal: {journal}")
    if args.dry_run:
        print("dry run — nothing was written")
    return 0


def cmd_revert(args: argparse.Namespace) -> int:
    """Undo exactly what this job wrote, and refuse anything it did not.

    Reversal is only safe while the file is still byte-identical to what was
    written, so a note touched since is reported and left alone rather than
    stamped over.
    """
    vault = resolve_memory_root(args.vault)
    counts: Counter = Counter()
    for line in Path(args.journal).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("outcome") != "aliased" or "sha_after" not in rec:
            continue
        abs_path = Path(vault, rec["path"])
        if not abs_path.exists():
            counts["missing"] += 1
            continue
        current = abs_path.read_text(encoding="utf-8")
        if sha(current) != rec["sha_after"]:
            counts["changed-since — left alone"] += 1
            continue
        if rec.get("op") == "create":
            prefix = created_prefix(rec["aliases"])
            restored = current[len(prefix):] if current.startswith(prefix) else current
        else:
            restored = current.replace("\n" + render_line(rec["aliases"]), "", 1)
        if sha(restored) != rec["sha_before"]:
            counts["would-not-restore-cleanly — left alone"] += 1
            continue
        if not args.dry_run:
            abs_path.write_text(restored, encoding="utf-8")
        counts["reverted"] += 1
    for k, v in counts.most_common():
        print(f"{v:7d}  {k}")
    if args.dry_run:
        print("dry run — nothing was written")
    return 0


def cmd_reapply(args: argparse.Namespace) -> int:
    """Put back exactly what `revert` removed, from the same journal.

    `revert` was written first and used alone for a year of nothing, because the
    only reason to undo a backfill was a mistake. Measuring one changed that: the
    week-3 retest reverted 1,930 aliases on evidence, and evidence can move
    again. Without this, restoring them meant re-running the generator, which
    calls a model and produces *different* aliases — a re-run is a new treatment,
    not an undo, and a corpus you cannot return to a known state is a corpus you
    cannot measure against twice.

    Symmetric with `revert` in the one way that matters: it writes only to a file
    that is still byte-identical to what the revert left behind, and reports
    anything else rather than stamping over it.
    """
    vault = resolve_memory_root(args.vault)
    # Gated like `run`, and for the same reason: reapply is a corpus-wide write.
    # `revert` deliberately is not — gating the undo on there being an undo is
    # the one arrangement that could strand the corpus.
    if not args.dry_run:
        # The gate is asked about the REPOSITORY, not the memory tree — it
        # answers "does an undo exist", and git answers that at the repo root.
        require_corpus_write_gate(args.agentmd, resolve_vault(args.vault))
    counts: Counter = Counter()
    for line in Path(args.journal).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("outcome") != "aliased" or "sha_after" not in rec:
            continue
        abs_path = Path(vault, rec["path"])
        if not abs_path.exists():
            counts["missing"] += 1
            continue
        current = abs_path.read_text(encoding="utf-8")
        if sha(current) == rec["sha_after"]:
            counts["already-aliased — left alone"] += 1
            continue
        if sha(current) != rec["sha_before"]:
            counts["changed-since — left alone"] += 1
            continue
        if rec.get("op") == "create":
            restored = created_prefix(rec["aliases"]) + current
        else:
            restored = insert_aliases(current, rec["aliases"])
        if sha(restored) != rec["sha_after"]:
            counts["would-not-restore-cleanly — left alone"] += 1
            continue
        if not args.dry_run:
            abs_path.write_text(restored, encoding="utf-8")
        counts["reapplied"] += 1
    for k, v in counts.most_common():
        print(f"{v:7d}  {k}")
    if args.dry_run:
        print("dry run — nothing was written")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--vault", help="override the resolved vault path")
    ap.add_argument("--agentmd", default="agentmd", help="path to the agentmd binary")
    sub = ap.add_subparsers(dest="cmd", required=True)

    frontmatter_help = (
        "also cover notes with no frontmatter at all, by giving them a block "
        "holding only the aliases. Machine artifacts stay out — see ARTIFACT_PREFIXES."
    )

    s = sub.add_parser("survey", help="census of what is eligible and what is not")
    s.add_argument("--subdir", help="restrict to one vault subdirectory")
    s.add_argument("--create-frontmatter", action="store_true", help=frontmatter_help)
    s.set_defaults(func=cmd_survey)

    r = sub.add_parser("run", help="generate and write aliases")
    r.add_argument("--subdir", help="restrict to one vault subdirectory")
    r.add_argument("--create-frontmatter", action="store_true", help=frontmatter_help)
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--offset", type=int, default=0)
    r.add_argument(
        "--sample", type=int, default=0,
        help="take an evenly-strided slice across the whole eligible set",
    )
    r.add_argument("--batch", type=int, default=15, help="notes per model call")
    r.add_argument("--jobs", type=int, default=4, help="concurrent model calls")
    r.add_argument("--model", default="sonnet")
    r.add_argument("--timeout", type=int, default=300)
    r.add_argument("--retries", type=int, default=2)
    r.add_argument("--body-chars", type=int, default=1100)
    r.add_argument("--pause", type=float, default=0.0, help="seconds between chunks")
    r.add_argument("--only-promoted", action="store_true")
    r.add_argument("--only-unpenalized", action="store_true")
    r.add_argument(
        "--only-new-frontmatter", action="store_true",
        help="only the notes a frontmatter block is being created for",
    )
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--journal", required=True, help="append-only record of every write")
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("revert", help="undo writes recorded in a journal")
    v.add_argument("--journal", required=True)
    v.add_argument("--dry-run", action="store_true")
    v.set_defaults(func=cmd_revert)

    ra = sub.add_parser(
        "reapply", help="put back exactly what revert removed, from the same journal")
    ra.add_argument("--journal", required=True)
    ra.add_argument("--dry-run", action="store_true")
    ra.set_defaults(func=cmd_reapply)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
