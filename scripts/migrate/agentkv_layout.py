#!/usr/bin/env python3
"""agentkv_layout — the vault moves of the AgentKV layout convergence (task 176).

The operator's rulings of 2026-09-24 move three sets of files, each in its own
batch, each after the readers of the old path have learned the new one and
shipped:

  root-files  Every file at a project root outside the five the root is locked
              to (`project.yaml`, `charter.md`, `blueprint.md`, `tracker.md`,
              `moc-<slug>.md`). Living documents go to `docs/`; the config and
              source lists machinery reads go to `desk/`.
  resources   agentm's reference cards, `projects/agentm/research/<topic>/
              reference/`, to `resources/topics/<topic>/`, dropping the
              `reference/` level; and the watchlist, `projects/agentm/_watchlist/`,
              to `resources/watchlist/`.
  systems     The six homelab notes in `agent/memory/semantic/` to
              `systems/homelab/` — the overview to `system.md`, the rest to
              `components/`.

Each batch runs the same way:

  dry run   Plans the moves and prints them, one line per move, and records the
            plan beside the engine state. Never writes the vault.
  --apply   The recorded plan, refused unless the listing still matches it, the
            count is confirmed, the vault's git tree is clean and no writer is
            live. Each file moves by `git mv`; every link that named a moved
            file by its path — a wikilink by full or partial path, or a markdown
            link, relative or from the vault root — is pointed at the new path
            with its words kept; the heat and lifecycle sidecars are re-keyed to
            the new paths with fresh fingerprints; and the whole batch is one
            vault commit.
  --audit   A path-aware census of every unresolved link in the vault: a
            wikilink by basename resolves when any file carries the name, one by
            path only when that path exists. Written as JSON so two runs can be
            compared as sets of (source, target), never as totals.
  --revert  `git revert` of the batch's commit, and the sidecar keys put back.

A basename link needs no rewrite — no file is renamed, only moved, and Obsidian
resolves `[[followups]]` wherever the file sits. What a move breaks is a link
that spelled the path, which the basename-only link check cannot see; the audit
here resolves paths for that reason.

The walled area, `recall_exempt_areas`, is never opened: its notes are neither
read for links nor rewritten, and a link into it resolves by existence alone.

  python3 scripts/migrate/agentkv_layout.py root-files
  python3 scripts/migrate/agentkv_layout.py root-files --apply --plan PLAN --confirm-count N
  python3 scripts/migrate/agentkv_layout.py --audit --out FILE
  python3 scripts/migrate/agentkv_layout.py --revert RUN_ID
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote, unquote

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
_TOOLKIT = _REPO / "harness" / "skills" / "memory" / "scripts"
for _p in (str(_REPO / "scripts"), str(_TOOLKIT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

STAGE = "agentkv-layout"
PROJECTS = "projects"
SKIP_DIRS = {".git", ".obsidian", ".trash", "node_modules", "__pycache__"}

# The five files a project root is locked to (the operator's ruling, 2026-09-24).
ROOT_FILES = ("project.yaml", "charter.md", "blueprint.md", "tracker.md")
# What machinery reads, rather than what a person opens: these go to `desk/`.
# The two home files are the home-tools engine's config and its regression
# guard; the engine finds them by path, and `desk/` is where its reader looks.
DESK_NAMES = frozenset({
    "trusted-sources.md", "forward-learning-sources.json", "skill-discovery-sources.md",
    "auto-orchestration-config.md", "home_config.py", "test_model.py",
})
DOCS_SUFFIXES = (".md",)

REFERENCE_PROJECT = "agentm"
HOMELAB_NOTES = ("home-server", "homelab-domain", "nas-unraid", "nas-backup",
                 "docker-inventory", "network-topology")
# The overview: `homelab-domain` is the homelab's anchor note, and becomes
# `system.md`; `home-server` is a short redirect to the NAS and keeps its own
# name among the components, where the overview links to it.
HOMELAB_OVERVIEW = "homelab-domain"


class Refused(Exception):
    """The run cannot go ahead, and says why."""


# ── the vault ──────────────────────────────────────────────────────────────

def _vault_default() -> Path:
    import harness_memory as hm  # noqa: E402
    return Path(hm.vault_path())


def _state_dir() -> Path:
    import engine_state  # noqa: E402
    return Path(engine_state.engine_state_dir())


def _git(vault: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True, check=check)


def _walled(vault: Path) -> list:
    try:
        import storage_rules  # noqa: E402
        return [a.strip("/") for a in storage_rules.rules().recall_exempt_areas()]
    except Exception:
        import storage_rules  # noqa: E402
        return list(storage_rules._FALLBACK_RECALL_EXEMPT_AREAS)


def _is_walled(rel: str, walled: list) -> bool:
    low = rel.lower()
    return any(low == a.lower() or low.startswith(a.lower() + "/") for a in walled)


def vault_files(vault: Path) -> list:
    """Every file in the vault, vault-relative, POSIX, sorted; dot and tool
    directories skipped."""
    out = []
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in filenames:
            if name.startswith("."):
                continue
            out.append(Path(dirpath, name).relative_to(vault).as_posix())
    return sorted(out)


# ── the plans ──────────────────────────────────────────────────────────────

def live_projects(vault: Path) -> list:
    space = vault / PROJECTS
    if not space.is_dir():
        return []
    return sorted(p.name for p in space.iterdir()
                  if p.is_dir() and not p.name.startswith((".", "_")) and p.name != "completed")


def plan_root_files(vault: Path) -> list:
    moves = []
    for slug in live_projects(vault):
        root = vault / PROJECTS / slug
        allowed = set(ROOT_FILES) | {f"moc-{slug}.md"}
        for f in sorted(root.iterdir()):
            if not f.is_file() or f.name.startswith(".") or f.name in allowed or f.name == "Icon\r":
                continue
            if f.name in DESK_NAMES:
                folder = "desk"
            elif f.suffix in DOCS_SUFFIXES:
                folder = "docs"
            else:
                raise Refused(f"{PROJECTS}/{slug}/{f.name}: no destination rule for this file; "
                              "name one before the move runs")
            moves.append({"src": f"{PROJECTS}/{slug}/{f.name}",
                          "dst": f"{PROJECTS}/{slug}/{folder}/{f.name}"})
    return moves


def plan_resources(vault: Path) -> list:
    moves = []
    research = vault / PROJECTS / REFERENCE_PROJECT / "research"
    if research.is_dir():
        for topic in sorted(p for p in research.iterdir() if p.is_dir()):
            ref = topic / "reference"
            if not ref.is_dir():
                continue
            for f in sorted(ref.rglob("*")):
                if f.is_file() and not f.name.startswith("."):
                    tail = f.relative_to(ref).as_posix()
                    moves.append({"src": f.relative_to(vault).as_posix(),
                                  "dst": f"resources/topics/{topic.name}/{tail}"})
    watch = vault / PROJECTS / REFERENCE_PROJECT / "_watchlist"
    if watch.is_dir():
        for f in sorted(watch.rglob("*")):
            if f.is_file() and not f.name.startswith("."):
                moves.append({"src": f.relative_to(vault).as_posix(),
                              "dst": f"resources/watchlist/{f.relative_to(watch).as_posix()}"})
    return moves


def plan_systems(vault: Path) -> list:
    import harness_memory as hm  # noqa: E402
    try:
        memory_prefix = Path(hm.memory_root()).relative_to(vault).as_posix()
    except Exception:
        memory_prefix = "agent"
    semantic = f"{memory_prefix}/memory/semantic"
    moves = []
    for stem in HOMELAB_NOTES:
        src = f"{semantic}/{stem}.md"
        if not (vault / src).is_file():
            continue
        dst = "systems/homelab/system.md" if stem == HOMELAB_OVERVIEW else f"systems/homelab/components/{stem}.md"
        moves.append({"src": src, "dst": dst})
    return moves


PLANNERS = {"root-files": plan_root_files, "resources": plan_resources, "systems": plan_systems}


def build_plan(vault: Path, batch: str) -> dict:
    moves = PLANNERS[batch](vault)
    for m in moves:
        if (vault / m["dst"]).exists():
            raise Refused(f"{m['dst']} already exists; a move never overwrites")
    dsts = [m["dst"] for m in moves]
    if len(set(dsts)) != len(dsts):
        raise Refused("two files would land on one path")
    return {"stage": STAGE, "batch": batch, "vault": str(vault), "moves": moves,
            "signature": _signature(moves)}


def _signature(moves: list) -> str:
    return hashlib.sha256(json.dumps(moves, sort_keys=True).encode()).hexdigest()[:16]


# ── links ──────────────────────────────────────────────────────────────────

_WIKI = re.compile(r"(!?)\[\[([^\]\|#\n]+)(#[^\]\|\n]*)?(\|[^\]\n]*)?\]\]")
_MDLINK = re.compile(r"(!?\[[^\]\n]*\]\()(<[^>\n]+>|[^)\s]+)(\s+\"[^\"\n]*\")?\)")
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def _noext(rel: str) -> str:
    return rel[:-3] if rel.endswith(".md") else rel


def _path_match(target: str, moved_old: dict) -> Optional[str]:
    """The old path a path-shaped wikilink target names, if any: a full path
    or a trailing part of one (Obsidian resolves `[[agentm/followups]]` as any
    file whose path ends that way), with or without the `.md`."""
    t = target.strip().strip("/").lower()
    if "/" not in t:
        return None
    for key in (t, _noext(t)):
        if key in moved_old:
            return moved_old[key]
    for old_key, old in moved_old.items():
        if old_key.endswith("/" + t) or old_key.endswith("/" + _noext(t)):
            return old
    return None


def rewrite_text(text: str, src_old: str, src_new: str, moves: dict, exists,
                 renamed: Optional[dict] = None) -> tuple:
    """`text` with every path link into a moved file pointed at the new path.

    `moves` maps old vault-relative path to new. `renamed` maps a lowercased
    old stem to its old path for a move that changed the file's name and whose
    old name no other file carries: a basename link to it follows the rename
    as a path link, its words kept. `src_old`/`src_new` are this
    note's own paths before and after the run (the same when it did not move),
    so a relative markdown link inside a moved note is recomputed from where
    the note now sits. `exists(rel)` answers for the vault after the run.
    Returns the new text and the number of links changed."""
    moved_old = {}
    for old in moves:
        moved_old[old] = old
        moved_old[_noext(old)] = old
    # The disk is case-insensitive and so is Obsidian's resolution on it, so a
    # link spelled `ROADMAP.md` names `roadmap.md` as surely as the exact case.
    folded = {k.lower(): v for k, v in moved_old.items()}
    count = 0

    def wiki(m):
        nonlocal count
        bang, target, anchor, alias = m.group(1), m.group(2), m.group(3) or "", m.group(4)
        old = _path_match(target, folded)
        if old is None and renamed and "/" not in target:
            old = renamed.get(_noext(target.strip()).lower())
            if old is not None:
                count += 1
                return f"{bang}[[{_noext(moves[old])}{anchor}{alias if alias else '|' + target.strip()}]]"
        if old is None:
            return m.group(0)
        new = moves[old]
        keep_ext = target.strip().endswith(".md") or not old.endswith(".md")
        shown = new if keep_ext else _noext(new)
        count += 1
        return f"{bang}[[{shown}{anchor}{alias if alias else '|' + target.strip()}]]"

    def md(m):
        nonlocal count
        head, raw, title = m.group(1), m.group(2), m.group(3) or ""
        bare = raw[1:-1] if raw.startswith("<") else raw
        if _SCHEME.match(bare) or bare.startswith("#"):
            return m.group(0)
        path, _, frag = bare.partition("#")
        dec = unquote(path)
        if not dec:
            return m.group(0)
        if dec.startswith("/"):
            resolved = os.path.normpath(dec.lstrip("/"))
        else:
            resolved = os.path.normpath(os.path.join(os.path.dirname(src_old), dec))
        resolved = resolved.replace(os.sep, "/")
        old = folded.get(resolved.lower())
        target_new = moves[old] if old is not None else None
        if target_new is None:
            if src_old == src_new or dec.startswith("/"):
                return m.group(0)
            # This note moved and the link is relative to where it was: keep
            # it pointing at the same file from the note's new folder.
            if not exists(resolved):
                return m.group(0)
            target_new = resolved
        if dec.startswith("/"):
            new_path = "/" + target_new
        else:
            new_path = os.path.relpath(target_new, os.path.dirname(src_new) or ".").replace(os.sep, "/")
        encoded = quote(new_path, safe="/.-_~")
        if raw.startswith("<"):
            out = "<" + new_path + (("#" + frag) if frag else "") + ">"
        else:
            out = (encoded if "%" in path else new_path.replace(" ", "%20")) + (("#" + frag) if frag else "")
        if out == raw:
            return m.group(0)
        count += 1
        return f"{head}{out}{title})"

    text = _WIKI.sub(wiki, text)
    text = _MDLINK.sub(md, text)
    return text, count


# ── the audit ──────────────────────────────────────────────────────────────

def audit(vault: Path) -> list:
    """Every unresolved link as a sorted list of [source, target] pairs."""
    walled = _walled(vault)
    files = vault_files(vault)
    present = set(files)
    names = {}
    for rel in files:
        base = rel.rsplit("/", 1)[-1].lower()
        names.setdefault(base, True)
        if base.endswith(".md"):
            names.setdefault(base[:-3], True)
    suffixes = set()
    for rel in files:
        low = rel.lower()
        parts = low.split("/")
        for i in range(1, len(parts)):
            s = "/".join(parts[i:])
            suffixes.add(s)
            if s.endswith(".md"):
                suffixes.add(s[:-3])
        suffixes.add(low)
        if low.endswith(".md"):
            suffixes.add(low[:-3])
    broken = set()
    for rel in files:
        if not rel.endswith(".md") or _is_walled(rel, walled):
            continue
        try:
            text = (vault / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _WIKI.finditer(text):
            t = m.group(2).strip()
            if not t:
                continue
            key = t.lower().strip("/")
            if "/" in key:
                ok = key in suffixes
            else:
                ok = key in names
            if not ok:
                broken.add((rel, t))
        for m in _MDLINK.finditer(text):
            raw = m.group(2)
            bare = raw[1:-1] if raw.startswith("<") else raw
            if _SCHEME.match(bare) or bare.startswith("#"):
                continue
            dec = unquote(bare.partition("#")[0])
            if not dec:
                continue
            if dec.startswith("/"):
                r = os.path.normpath(dec.lstrip("/"))
            else:
                r = os.path.normpath(os.path.join(os.path.dirname(rel), dec))
            r = r.replace(os.sep, "/")
            if r not in present and not (vault / r).exists():
                broken.add((rel, bare))
    return sorted([list(p) for p in broken])


# ── the sidecars ───────────────────────────────────────────────────────────

SIDECARS = (".heat.json", ".lifecycle.json")


def rekey_sidecars(state_dir: Path, vault: Path, moves: dict, *, dry: bool = False) -> dict:
    """Move each sidecar entry from a moved note's old key to its new one, with
    the note's fingerprint recomputed where the entry carries one. Returns the
    count re-keyed per sidecar."""
    import lifecycle  # noqa: E402
    counts = {}
    for name in SIDECARS:
        path = state_dir / name
        if not path.is_file():
            counts[name] = 0
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("entries", {})
        n = 0
        for old, new in moves.items():
            if old in entries and new not in entries:
                entry = entries.pop(old)
                if "fingerprint" in entry:
                    fp = lifecycle.fingerprint_of(vault / new)
                    if fp:
                        entry["fingerprint"] = fp
                entries[new] = entry
                n += 1
        counts[name] = n
        if not dry and n:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(tmp, path)
    return counts


# ── apply / revert ─────────────────────────────────────────────────────────

def live_writers() -> list:
    """Who could write into the vault while its files are in flight."""
    out = []
    uid = os.getuid() if hasattr(os, "getuid") else None
    for job in ("com.agentm.daemon", "com.agentm.runner"):
        if uid is not None and subprocess.run(["launchctl", "print", f"gui/{uid}/{job}"],
                                              capture_output=True).returncode == 0:
            out.append(f"{job} is loaded in launchd; boot it out first")
    if subprocess.run(["pgrep", "-x", "Obsidian"], capture_output=True).returncode == 0:
        out.append("Obsidian is running, and it rewrites links on a rename; quit it first")
    return out


# What a sync client or a desktop leaves in a folder is not content: a folder
# holding only these is empty for the prune below.
_LITTER = {".DS_Store", "Icon\r", "desktop.ini"}


def prune_emptied(vault: Path, sources: list) -> list:
    """Remove each folder a move emptied, deepest first, up to (never
    including) its project or space root: `research/<topic>/reference/` and
    `_watchlist/<source>/` are left with nothing once their files move, and
    git does not carry an empty folder away. Only a folder holding nothing but
    sync litter goes; one that still holds anything stays. Returns the pruned
    folders, vault-relative."""
    import shutil
    stop = {vault.resolve()}
    for top in ("projects", "agent", "resources", "systems"):
        stop.add((vault / top).resolve())
    for proj in (vault / "projects").glob("*"):
        stop.add(proj.resolve())
    candidates = set()
    for src in sources:
        d = (vault / src).parent
        while d.resolve() not in stop and vault.resolve() in d.resolve().parents:
            candidates.add(d)
            d = d.parent
    pruned = []
    for d in sorted(candidates, key=lambda p: len(p.parts), reverse=True):
        if not d.is_dir():
            continue
        entries = list(d.iterdir())
        if all(e.is_file() and e.name in _LITTER for e in entries):
            shutil.rmtree(d)
            pruned.append(d.relative_to(vault).as_posix())
    return sorted(pruned)


def apply(vault: Path, recorded: dict, confirm_count: int, state_dir: Path, *,
          check_writers=live_writers, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    fresh = build_plan(vault, recorded["batch"])
    if fresh["signature"] != recorded["signature"]:
        raise Refused("the vault no longer matches the recorded plan; run the dry run again")
    moves = recorded["moves"]
    if confirm_count != len(moves):
        raise Refused(f"--confirm-count {confirm_count} does not match the plan's {len(moves)} moves")
    if not moves:
        raise Refused("the plan moves nothing")
    writers = check_writers()
    if writers:
        raise Refused("; ".join(writers))
    if _git(vault, "status", "--porcelain").stdout.strip():
        raise Refused("the vault's git tree is not clean; let it commit first")
    mapping = {m["src"]: m["dst"] for m in moves}
    walled = _walled(vault)
    for src, dst in mapping.items():
        (vault / dst).parent.mkdir(parents=True, exist_ok=True)
        _git(vault, "mv", src, dst)
    pruned = prune_emptied(vault, list(mapping))
    inverse = {v: k for k, v in mapping.items()}
    after = set(vault_files(vault))
    # A move that renamed a note leaves its old name to no file; a basename
    # link to that name follows the rename, but only when the old name is
    # unique — a name another file still carries resolves to that file.
    stems = {}
    for rel in after:
        stems.setdefault(Path(rel).stem.lower(), []).append(rel)
    renamed = {}
    for src, dst in mapping.items():
        old, new = Path(src).stem.lower(), Path(dst).stem.lower()
        if src.endswith(".md") and old != new and old not in stems:
            renamed[old] = None if old in renamed else src
    renamed = {k: v for k, v in renamed.items() if v is not None}
    rewritten = []
    links = 0
    for rel in sorted(after):
        if not rel.endswith(".md") or _is_walled(rel, walled):
            continue
        p = vault / rel
        text = p.read_text(encoding="utf-8", errors="surrogateescape")
        new, n = rewrite_text(text, inverse.get(rel, rel), rel, mapping,
                              lambda r: r in after or (vault / r).exists(), renamed)
        if n:
            p.write_text(new, encoding="utf-8", errors="surrogateescape")
            rewritten.append({"path": rel, "links": n})
            links += n
    counts = rekey_sidecars(state_dir, vault, mapping)
    _git(vault, "add", "-A")
    msg = f"vault: agentkv layout — {recorded['batch']} ({len(moves)} moved, {links} links rewritten)"
    _git(vault, "commit", "-q", "-m", msg)
    sha = _git(vault, "rev-parse", "HEAD").stdout.strip()
    run_id = now.strftime("%Y%m%dT%H%M%SZ") + "-" + recorded["batch"]
    record = {"run_id": run_id, "batch": recorded["batch"], "commit": sha, "moves": moves,
              "rewritten": rewritten, "links": links, "sidecars": counts, "pruned": pruned}
    out = state_dir / STAGE / f"run-{run_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    record["record"] = str(out)
    return record


def revert(vault: Path, run_id: str, state_dir: Path, *, check_writers=live_writers) -> str:
    rec_path = state_dir / STAGE / f"run-{run_id}.json"
    if not rec_path.is_file():
        raise Refused(f"no run {run_id} under {rec_path.parent}")
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    writers = check_writers()
    if writers:
        raise Refused("; ".join(writers))
    if _git(vault, "status", "--porcelain").stdout.strip():
        raise Refused("the vault's git tree is not clean")
    r = _git(vault, "revert", "--no-edit", rec["commit"], check=False)
    if r.returncode != 0:
        _git(vault, "revert", "--abort", check=False)
        raise Refused(f"git revert of {rec['commit']} did not apply cleanly: {r.stderr.strip()}")
    back = {m["dst"]: m["src"] for m in rec["moves"]}
    rekey_sidecars(state_dir, vault, back)
    return _git(vault, "rev-parse", "HEAD").stdout.strip()


# ── the command ────────────────────────────────────────────────────────────

def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("batch", nargs="?", choices=sorted(PLANNERS))
    ap.add_argument("--vault", help="vault root (default: the configured one)")
    ap.add_argument("--state-dir", help="engine state directory (default: the configured one)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--plan", help="the plan file the dry run recorded")
    ap.add_argument("--confirm-count", type=int)
    ap.add_argument("--audit", action="store_true", help="write the unresolved-link census")
    ap.add_argument("--out", help="where --audit writes its JSON")
    ap.add_argument("--revert", metavar="RUN_ID")
    args = ap.parse_args(argv)
    vault = Path(args.vault) if args.vault else _vault_default()
    state_dir = Path(args.state_dir) if args.state_dir else _state_dir()
    try:
        if args.audit:
            broken = audit(vault)
            text = json.dumps(broken, indent=1) + "\n"
            if args.out:
                Path(args.out).write_text(text, encoding="utf-8")
            print(f"{STAGE}: {len(broken)} unresolved links" + (f" -> {args.out}" if args.out else ""))
            return 0
        if args.revert:
            print(f"{STAGE}: reverted, vault now at {revert(vault, args.revert, state_dir)}")
            return 0
        if not args.batch:
            ap.error("name a batch, or pass --audit / --revert")
        if args.apply:
            if not args.plan or args.confirm_count is None:
                ap.error("--apply needs --plan and --confirm-count")
            recorded = json.loads(Path(args.plan).read_text(encoding="utf-8"))
            if recorded.get("batch") != args.batch:
                raise Refused(f"the plan is for {recorded.get('batch')!r}, not {args.batch!r}")
            rec = apply(vault, recorded, args.confirm_count, state_dir)
            print(f"{STAGE}: {len(rec['moves'])} moved, {rec['links']} links rewritten in "
                  f"{len(rec['rewritten'])} notes, {len(rec['pruned'])} emptied folders removed, "
                  f"sidecars {rec['sidecars']}, commit {rec['commit'][:10]}")
            print(f"  record: {rec['record']}")
            print(f"  revert: python3 scripts/migrate/agentkv_layout.py --revert {rec['run_id']}")
            return 0
        plan = build_plan(vault, args.batch)
        for m in plan["moves"]:
            print(f"  {m['src']}  ->  {m['dst']}")
        out = state_dir / STAGE / f"plan-{args.batch}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        print(f"{STAGE}: {len(plan['moves'])} moves planned for {args.batch} -> {out}")
        return 0
    except Refused as e:
        print(f"{STAGE}: refused — {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
