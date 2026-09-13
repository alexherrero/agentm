#!/usr/bin/env python3
"""maps_and_root_notes — the vault half of the maps and root notes (agentm-vault plan 07).

The code half ships first: the mocs job paginates a type's page in place and
writes `moc-memory.md` and `moc-root.md`, needs-review links the root map, each
year gets its calendar map, and three gates hold the new shape once this
migration's marker exists. This script changes the vault to match.

  dry run   What goes, where each link to it points next, and a manifest line
            for every deletion: the path, why it goes, where its content went,
            and the vault commit that restores it. The plan is recorded in the
            engine state directory; nothing in the vault changes.
  --apply   The recorded plan, refused unless the vault still matches it and
            the file count is confirmed. The numbered map pages, `Home.md` and
            `Filing.md` go; the stray day note moves into its year with its body
            unchanged; the links to what went are repointed; `index.md` takes
            the reviewed draft that folds `Filing.md`'s table in. All of it
            goes through the revert log and is read back into a journal.
  --finish  After the dreaming pass and needs-review have regenerated the maps:
            every post-condition the gates will enforce, read from the vault.
            The marker is written only when all of them hold.

`--revert RUN_ID` restores every file the run wrote and removes the marker.

A link is repointed only where it may be: in the agent's notes under the memory
root and in `Calendar/`, the maps themselves excepted because the job rewrites
them, and in exactly the `Projects/` notes the operator named. A record under
`_harness/` keeps the names it was written with, `Personal/` is the operator's,
and a link inside code is left alone. A link anywhere else that names what went
is listed, and `--finish` fails on it. A type's page under the page threshold is
held unless the operator gives the word with `--also-delete TYPE`.

  python3 scripts/migrate/maps_and_root_notes.py --index-draft DRAFT [--also-delete TYPE]
  python3 scripts/migrate/maps_and_root_notes.py --apply --plan PLAN --confirm-count N
  python3 scripts/migrate/maps_and_root_notes.py --finish --plan PLAN
  python3 scripts/migrate/maps_and_root_notes.py --revert RUN_ID
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
_SCRIPTS = _REPO / "scripts"
_TOOLKIT = _REPO / "harness" / "skills" / "memory" / "scripts"
for _p in (str(_SCRIPTS), str(_TOOLKIT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import maps_shape as ms  # noqa: E402


def _gate(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


root_notes = _gate("check-root-notes")
calendar_gate = _gate("check-calendar-root")

# The operator's rulings for this migration (2026-09-12). In `Projects/`,
# outside `_harness/`, these links and nothing else, each note holding exactly
# this many; the words on the page are kept.
PROJECTS_LINKS = {
    "Projects/agentm/decisions/memory-os-architecture-scan.md": ("Home", 2),
    "Projects/agentm/decisions/liteparse-doc-ingestion-candidate.md": ("Home", 1),
    "Projects/agentm/decisions/autonomous-write-contract.md": ("Home", 1),
    "Projects/agentm/pattern/corpus-screening-funnel.md": ("Home", 1),
    "Projects/crickets/decisions/agentskills-io-interop.md": ("Home", 1),
    "Projects/index.md": ("Filing", 1),
}
# The stray day note moves into its year with its body unchanged; the template
# beside it stays, because the daily-note wiring is plan 10's.
MOVES = (("Calendar/2026-08-10.md", "Calendar/2026/2026-08-10-diary.md"),)
DEFAULT_FACETS = ("meetings", "correspondence", "docs", "diary")
INDEX = "\0index"
STAGE = "maps-and-root-notes"
_WIKILINK = re.compile(r"\[\[([^\]\n]+?)\]\]")
_SEPARATOR = re.compile(r"\\\||\||#")
_ALIAS = re.compile(r"\\\||\|")
_LISTED = re.compile(r"^- \[\[([^\]|\\]+)", re.M)
_SKIP_DIRS = {".git", ".obsidian", ".trash", "_harness"}


class Refused(Exception):
    """The run cannot do what it was asked, and wrote nothing."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rel(path: Path, vault: Path) -> str:
    return Path(path).relative_to(vault).as_posix()


def _git(vault: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def restore_commit(vault: Path, rel: str) -> str:
    """The vault commit that holds `rel` as it stands on disk, or refuse."""
    if _git(vault, "status", "--porcelain", "--", rel):
        raise Refused(f"{rel} has changes the vault has not committed, so no commit would restore it as it is")
    sha = _git(vault, "log", "-1", "--format=%H", "--", rel)
    if not sha:
        raise Refused(f"{rel} is in no vault commit, so nothing could restore it")
    return sha


# ── what goes, and where a link to it points next ──────────────────────────

class Retired:
    def __init__(self, entries=()):
        self.entries = []
        self._by_key, self._bare = {}, {}
        for e in entries:
            self.add(**e)

    def add(self, key: str, to: str, keep: bool, bare: list):
        entry = {"key": key, "to": to, "keep": keep, "bare": list(bare)}
        self.entries.append(entry)
        self._by_key[key] = entry
        for name in bare:
            self._bare[name.lower()] = entry

    def holds(self, path: Path) -> bool:
        return root_notes._key(path) in self._by_key

    def lookup(self, vault: Path, note: Path, target: str):
        target = target.strip()
        if not target:
            return None
        if "/" not in target:
            stem = target[:-3] if target.lower().endswith(".md") else target
            return self._bare.get(stem.lower())
        path = target.lstrip("/")
        if not path.lower().endswith(".md"):
            path += ".md"
        for base in (vault, note.parent):
            hit = self._by_key.get(root_notes._key(base / path))
            if hit:
                return hit
        return None


def _split(inner: str):
    """A wikilink's inside as `(target, alias, escaped)`; a heading is dropped,
    since the page it pointed into is gone."""
    m = _SEPARATOR.search(inner)
    if not m:
        return inner, None, False
    rest = inner[m.start():]
    alias = _ALIAS.search(rest)
    if alias is None:
        return inner[:m.start()], None, False
    return inner[:m.start()], rest[alias.end():], alias.group(0) == "\\|"


def rewrite(vault: Path, note: Path, text: str, retired: Retired):
    """`(text, changes)`: every visible link to what went, repointed."""
    visible = root_notes.visible(text)
    parts, last, changes = [], 0, []
    for m in _WIKILINK.finditer(visible):
        target, alias, escaped = _split(text[m.start() + 2:m.end() - 2])
        hit = retired.lookup(vault, note, target)
        if hit is None:
            continue
        to = hit["to"]
        if to == INDEX:
            to = Path(os.path.relpath(vault / "index", note.parent)).as_posix()
        line_start = text.rfind("\n", 0, m.start()) + 1
        in_table = escaped or text[line_start:m.start()].lstrip().startswith("|")
        display = alias if alias is not None else (target.strip() if hit["keep"] else None)
        new = f"[[{to}{chr(92) + '|' if in_table else '|'}{display}]]" if display is not None else f"[[{to}]]"
        parts += [text[last:m.start()], new]
        last = m.end()
        changes.append({"line": text.count("\n", 0, m.start()) + 1, "from": m.group(0), "to": new})
    parts.append(text[last:])
    return "".join(parts), changes


# ── the plan ───────────────────────────────────────────────────────────────

def _scope(vault: Path, memory_root: Path):
    """The notes a link may be repointed in, sorted."""
    maps = root_notes._key(memory_root / "memory" / "mocs")
    seen = set()
    for base in (memory_root, vault / "Calendar"):
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames
                                 if d not in _SKIP_DIRS and root_notes._key(Path(dirpath) / d) != maps)
            for name in sorted(filenames):
                if name.endswith(".md"):
                    seen.add(Path(dirpath) / name)
    for rel in PROJECTS_LINKS:
        if (vault / rel).is_file():
            seen.add(vault / rel)
    return sorted(seen)


def build_plan(vault, memory_root, rules=None, index_draft=None, also_delete=()):
    """`(plan, link contents, draft bytes)`. Reads only."""
    vault, memory_root = Path(vault), Path(memory_root)
    also_delete = set(also_delete)
    maps = memory_root / "memory" / "mocs"
    counts = ms.live_type_counts(memory_root, rules)
    floor = ms.min_members(rules)
    types = (set(rules.memory_types()) if rules is not None else set()) | set(counts)
    retired = Retired()
    deletions, held, moves = [], [], []

    def goes(path: Path, why: str, went: str, to: str, keep: bool, bare: tuple):
        rel = _rel(path, vault)
        deletions.append({"rel": rel, "why": why, "went": went, "restore": restore_commit(vault, rel),
                          "sha256": _sha(path.read_bytes())})
        retired.add(root_notes._key(path), to, keep, list(bare))

    if maps.is_dir():
        for p in sorted(maps.glob("*.md")):
            numbered = ms._NUMBERED.match(p.stem)
            if numbered and numbered.group("base") in types:
                base = numbered.group("base")
                has_page = counts.get(base, 0) >= floor
                to = base if has_page or base not in also_delete else ms.MAP_NAMES[1]
                went = (f"its members are on `{base}.md`, which the mocs job paginates in place" if has_page
                        else f"its members are listed on `{ms.MAP_NAMES[1]}.md`")
                goes(p, f"a numbered map page; `{base}` has one page now", went, to, False, (p.stem,))
            elif p.stem in types and counts.get(p.stem, 0) < floor:
                why = f"`{p.stem}` has {counts.get(p.stem, 0)} live note(s), under the page threshold of {floor}"
                if p.stem in also_delete:
                    goes(p, why, f"its notes are listed in full on `{ms.MAP_NAMES[1]}.md`", ms.MAP_NAMES[1], False, (p.stem,))
                else:
                    held.append({"rel": _rel(p, vault), "why": f"{why}; held for the operator's word (--also-delete {p.stem})"})

    home = memory_root / "Home.md"
    if home.exists():
        goes(home, "the hand-kept entry point retires for the generated root map",
             f"`{ms.MAP_NAMES[0]}.md` is the agent's entry point and `index.md` links it; the hand lists had gone "
             "stale and are not carried", ms.MAP_NAMES[0], True, ("Home",))
    filing = vault / "Filing.md"
    if filing.exists():
        goes(filing, "it folds into `index.md`, which carries the write-authority table once",
             "its write-authority table is in `index.md`", INDEX, True, ("Filing",))

    for src_rel, dst_rel in MOVES:
        src, dst = vault / src_rel, vault / dst_rel
        if not src.exists():
            continue
        if dst.exists():
            raise Refused(f"{dst_rel} already exists, and the move would overwrite it")
        moves.append({"from": src_rel, "to": dst_rel, "sha256": _sha(src.read_bytes()),
                      "restore": restore_commit(vault, src_rel)})
        retired.add(root_notes._key(src), Path(dst_rel).stem, False, [Path(src_rel).stem])

    links, contents, in_scope = [], {}, set()
    for note in _scope(vault, memory_root):
        if retired.holds(note):
            continue
        in_scope.add(root_notes._key(note))
        raw = note.read_bytes()
        text, changes = rewrite(vault, note, raw.decode("utf-8"), retired)
        rel = _rel(note, vault)
        if rel in PROJECTS_LINKS:
            name, expected = PROJECTS_LINKS[rel]
            named = [c for c in changes if re.split(r"\\?\||#", c["from"][2:-2], maxsplit=1)[0].strip() == name]
            if len(named) != expected or len(changes) != expected:
                raise Refused(f"{rel}: the ruling names {expected} link(s) to {name}, and the note now holds "
                              f"{len(named)} of them among {len(changes)} to retired notes")
        if changes:
            links.append({"rel": rel, "before_sha": _sha(raw), "after_sha": _sha(text.encode("utf-8")),
                          "changes": changes})
            contents[rel] = text

    index_path = vault / "index.md"
    not_written = []
    for note in root_notes._notes(vault):
        key = root_notes._key(note)
        if key in in_scope or retired.holds(note) or root_notes._key(note.parent) == root_notes._key(maps) \
                or key == root_notes._key(index_path):
            continue
        _text, changes = rewrite(vault, note, note.read_text(encoding="utf-8", errors="replace"), retired)
        not_written += [{"rel": _rel(note, vault), "line": c["line"], "link": c["from"]} for c in changes]

    index, draft = None, None
    if index_draft is not None:
        draft = Path(index_draft).read_bytes()
        draft_text = draft.decode("utf-8")
        if root_notes.authority_tables(draft_text) != 1:
            raise Refused("the index draft must carry the write-authority table exactly once")
        _text, named = rewrite(vault, index_path, draft_text, retired)
        if named:
            raise Refused(f"the index draft still links a retired note: {named[0]['from']}")
        index = {"rel": "index.md", "before_sha": _sha(index_path.read_bytes()), "after_sha": _sha(draft),
                 "restore": restore_commit(vault, "index.md")}

    plan = {"deletions": deletions, "held": held, "moves": moves, "links": links, "index": index,
            "not_written": not_written, "retired": retired.entries,
            "index_draft": str(index_draft) if index_draft is not None else None,
            "also_delete": sorted(also_delete)}
    plan["counts"] = {"deletions": len(deletions), "moves": len(moves), "links": len(links),
                      "index": 1 if index else 0}
    plan["counts"]["files"] = sum(plan["counts"].values())
    return plan, contents, draft


def _signature(plan: dict) -> str:
    return json.dumps({k: plan[k] for k in ("deletions", "moves", "links", "index")}, sort_keys=True)


def manifest(plan: dict, vault: Path) -> list:
    """The progress file's manifest, one line per file that goes or moves."""
    out = []
    for d in plan["deletions"]:
        out.append(f"- `{d['rel']}` goes: {d['why']}. Where its content went: {d['went']}. "
                   f"Restore: `git -C \"{vault}\" checkout {d['restore'][:12]} -- \"{d['rel']}\"`.")
    for m in plan["moves"]:
        out.append(f"- `{m['from']}` moves to `{m['to']}` with its body unchanged. "
                   f"Restore: `git -C \"{vault}\" checkout {m['restore'][:12]} -- \"{m['from']}\"`.")
    if plan["index"]:
        out.append(f"- `index.md` takes the reviewed draft folding `Filing.md`'s table in. "
                   f"Restore: `git -C \"{vault}\" checkout {plan['index']['restore'][:12]} -- \"index.md\"`.")
    return out


def print_summary(plan: dict, vault: Path, out=sys.stdout) -> None:
    c = plan["counts"]
    print(f"  files: {c['files']} — {c['deletions']} deletion(s), {c['moves']} move(s), "
          f"{c['links']} note(s) with links repointed, index.md {'from the draft' if c['index'] else 'not given'}", file=out)
    for h in plan["held"]:
        print(f"  held: {h['rel']} — {h['why']}", file=out)
    for link in plan["links"]:
        for ch in link["changes"]:
            print(f"  link: {link['rel']}:{ch['line']}  {ch['from']} -> {ch['to']}", file=out)
    for nw in plan["not_written"]:
        print(f"  not written: {nw['rel']}:{nw['line']}  {nw['link']} (outside the link scope; --finish fails on it)", file=out)
    if not plan["index"]:
        print("  index.md: no draft given; --finish needs index.md to carry the table once and name no retired note",
              file=out)
    print("  manifest:", file=out)
    for line in manifest(plan, vault):
        print(f"    {line}", file=out)


# ── apply, finish, revert ──────────────────────────────────────────────────

def apply(vault, memory_root, recorded, confirm_count, out_dir, rules=None, log_root=None, lock_root=None):
    import revert_log  # noqa: E402
    vault, memory_root, out_dir = Path(vault), Path(memory_root), Path(out_dir)
    plan, contents, draft = build_plan(vault, memory_root, rules, recorded.get("index_draft"),
                                       recorded.get("also_delete", ()))
    if _signature(plan) != _signature(recorded):
        raise Refused("the vault is not what the dry run read; run the dry run again. Nothing written.")
    if confirm_count != plan["counts"]["files"]:
        raise Refused(f"the plan writes {plan['counts']['files']} files; you confirmed {confirm_count}. Nothing written.")
    run_id = recorded["run_id"]
    log = revert_log.RevertLog(vault, log_root=log_root, lock_root=lock_root)
    entries = {}
    if plan["deletions"]:
        entries["deletions"] = log.record_and_apply(run_id, f"{STAGE}-deletions",
                                                    [(vault / d["rel"], None) for d in plan["deletions"]])
    if plan["moves"]:
        muts = []
        for m in plan["moves"]:
            muts += [(vault / m["to"], (vault / m["from"]).read_bytes()), (vault / m["from"], None)]
        entries["moves"] = log.record_and_apply(run_id, f"{STAGE}-moves", muts)
    if contents:
        entries["links"] = log.record_and_apply(run_id, f"{STAGE}-links",
                                                [(vault / rel, text) for rel, text in sorted(contents.items())])
    if plan["index"]:
        entries["index"] = log.record_and_apply(run_id, f"{STAGE}-index", [(vault / "index.md", draft)])

    landed = {
        "deletions": all(not (vault / d["rel"]).exists() for d in plan["deletions"]),
        "moves": all((vault / m["to"]).is_file() and _sha((vault / m["to"]).read_bytes()) == m["sha256"]
                     and not (vault / m["from"]).exists() for m in plan["moves"]),
        "links": all(_sha((vault / link["rel"]).read_bytes()) == link["after_sha"] for link in plan["links"]),
        "index": plan["index"] is None or _sha((vault / "index.md").read_bytes()) == plan["index"]["after_sha"],
    }
    journal = {"run_id": run_id, "applied_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "revert_log_entries": entries, "counts": plan["counts"], "landed": landed,
               "matches_dry_run": plan["counts"] == recorded["counts"] and all(landed.values())}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"journal-{run_id}.json").write_text(json.dumps(journal, indent=2) + "\n", encoding="utf-8")
    return journal


def _year_map_problems(memory_root: Path, rules=None) -> list:
    cal = calendar_gate.calendar_root(memory_root)
    if cal is None:
        return []
    facets = set((rules.facets() if rules is not None else ()) or DEFAULT_FACETS)
    out = []
    for year in sorted(p for p in cal.iterdir() if p.is_dir() and re.fullmatch(r"\d{4}", p.name)):
        want = set()
        for p in year.glob("*.md"):
            m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})-(.+)", p.stem)
            if m and m.group(1).startswith(year.name + "-") and m.group(2) in facets:
                want.add(p.stem)
        if not want:
            continue
        page = cal / f"moc-calendar-{year.name}.md"
        if not page.is_file():
            out.append(f"Calendar/{page.name}: missing; run the dreaming pass")
            continue
        got = set(_LISTED.findall(page.read_text(encoding="utf-8")))
        if got != want:
            out.append(f"Calendar/{page.name}: lists {sorted(got)}, and {year.name}/ holds {sorted(want)}")
    return out


def _root_map_problems(memory_root: Path) -> list:
    page = memory_root / "memory" / "mocs" / f"{ms.MAP_NAMES[0]}.md"
    if not page.is_file():
        return []
    listed = set(_LISTED.findall(page.read_text(encoding="utf-8")))
    vault = root_notes.vault_root(memory_root)
    want = {name for name in ms.MAP_NAMES[1:] if (memory_root / "memory" / "mocs" / f"{name}.md").is_file()}
    for d in (memory_root / "diagnostics", vault / "standards", vault / "Projects"):
        if d.is_dir():
            want |= {p.stem for p in d.glob("moc-*.md")}
    cal = calendar_gate.calendar_root(memory_root)
    if cal is not None:
        want |= {p.stem for p in cal.glob("moc-calendar-*.md")}
    missing = sorted(want - listed)
    return [f"memory/mocs/{page.name}: does not list {', '.join(missing)}"] if missing else []


def finish(vault, memory_root, recorded, out_dir, rules=None) -> list:
    """The post-conditions that still fail; the marker is written when none do."""
    vault, memory_root, out_dir = Path(vault), Path(memory_root), Path(out_dir)
    journal_path = out_dir / f"journal-{recorded['run_id']}.json"
    if not journal_path.is_file():
        raise Refused(f"no journal for {recorded['run_id']}; apply the plan first")
    if not json.loads(journal_path.read_text(encoding="utf-8")).get("matches_dry_run"):
        raise Refused(f"the applied run did not match its dry run; read {journal_path}")
    problems = [f"memory/mocs/{name}.md: missing; run the dreaming pass and needs-review first"
                for name in ms.MAP_NAMES if not (memory_root / "memory" / "mocs" / f"{name}.md").is_file()]
    problems += ms.mocs_findings(memory_root, rules)
    problems += root_notes.findings(memory_root)
    problems += calendar_gate.findings(memory_root)[0]
    problems += _year_map_problems(memory_root, rules)
    problems += _root_map_problems(memory_root)
    retired = Retired(recorded["retired"])
    maps = root_notes._key(memory_root / "memory" / "mocs")
    for note in root_notes._notes(vault):
        if root_notes._key(note.parent) == maps:
            continue
        _text, changes = rewrite(vault, note, note.read_text(encoding="utf-8", errors="replace"), retired)
        problems += [f"{_rel(note, vault)}:{c['line']}: {c['from']} still names what went" for c in changes]
    if not problems:
        marker = ms.marker_path(memory_root)
        marker.write_text(f"run {recorded['run_id']}\nfinished "
                          f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n", encoding="utf-8")
    return sorted(set(problems))


def revert(vault, memory_root, run_id: str, log_root=None, lock_root=None) -> None:
    import revert_log  # noqa: E402
    revert_log.RevertLog(Path(vault), log_root=log_root, lock_root=lock_root).revert(run_id)
    marker = ms.marker_path(memory_root)
    if marker.exists():
        marker.unlink()


# ── command line ───────────────────────────────────────────────────────────

def _defaults():
    import engine_state  # noqa: E402
    import harness_memory as hm  # noqa: E402
    return Path(hm.vault_path()), Path(hm.memory_root()), Path(engine_state.engine_state_dir())


def _load_rules():
    try:
        import storage_rules  # noqa: E402
        return storage_rules.load()
    except Exception:
        return None


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", help="vault root (default: the configured one)")
    ap.add_argument("--memory-root", help="memory root (default: the configured one)")
    ap.add_argument("--state-dir", help="engine state directory (default: the configured one)")
    ap.add_argument("--index-draft", help="the reviewed index.md that folds Filing.md's table in")
    ap.add_argument("--also-delete", action="append", default=[], metavar="TYPE",
                    help="delete TYPE's page though it is under the page threshold (the operator's word)")
    ap.add_argument("--apply", action="store_true", help="apply a recorded plan")
    ap.add_argument("--finish", action="store_true", help="check the post-conditions and write the marker")
    ap.add_argument("--plan", help="the plan file the dry run recorded")
    ap.add_argument("--confirm-count", type=int, help="the plan's file count, typed by the operator")
    ap.add_argument("--revert", metavar="RUN_ID", help="restore every file a run wrote")
    args = ap.parse_args(argv)

    vault = memory_root = state_dir = None
    if not (args.vault and args.memory_root and args.state_dir):
        vault, memory_root, state_dir = _defaults()
    vault = Path(args.vault) if args.vault else vault
    memory_root = Path(args.memory_root) if args.memory_root else memory_root
    state_dir = Path(args.state_dir) if args.state_dir else state_dir
    out_dir = state_dir / STAGE

    if args.revert:
        revert(vault, memory_root, args.revert)
        print(f"maps and root notes: reverted {args.revert}")
        return 0
    rules = _load_rules()
    try:
        if args.apply or args.finish:
            if not args.plan:
                print("maps and root notes: --apply and --finish need --plan", file=sys.stderr)
                return 2
            recorded = json.loads(Path(args.plan).read_text(encoding="utf-8"))
            if args.apply:
                if args.confirm_count is None:
                    print("maps and root notes: --apply needs --confirm-count", file=sys.stderr)
                    return 2
                journal = apply(vault, memory_root, recorded, args.confirm_count, out_dir, rules)
                print(f"maps and root notes: applied {recorded['run_id']}; landed {journal['landed']}; "
                      f"journal matches the dry run: {'yes' if journal['matches_dry_run'] else 'NO'}")
                print(f"next: the dreaming pass and needs-review, then --finish --plan {args.plan}")
                return 0 if journal["matches_dry_run"] else 1
            problems = finish(vault, memory_root, recorded, out_dir, rules)
            if problems:
                print(f"maps and root notes: {len(problems)} post-condition(s) fail; no marker written", file=sys.stderr)
                for p in problems:
                    print(f"  {p}", file=sys.stderr)
                return 1
            marker = ms.marker_path(memory_root)
            print(f"maps and root notes: every post-condition holds; wrote {marker}")
            print(f"it is a new dot-named file, which the daemon does not commit: git -C \"{vault}\" add "
                  f"\"{_rel(marker, vault)}\" && git -C \"{vault}\" commit -m \"maps and root notes: the data run's marker\"")
            return 0
        run_id = "maps-and-root-notes-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        plan, _contents, _draft = build_plan(vault, memory_root, rules, args.index_draft, args.also_delete)
    except Refused as exc:
        print(f"maps and root notes: refused — {exc}", file=sys.stderr)
        return 1
    plan["run_id"] = run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    plan_path = out_dir / f"plan-{run_id}.json"
    plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    print(f"maps and root notes — dry run {run_id}")
    print_summary(plan, vault)
    print(f"plan: {plan_path}")
    print(f"apply: python3 scripts/migrate/maps_and_root_notes.py --apply --plan {plan_path} "
          f"--confirm-count {plan['counts']['files']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
