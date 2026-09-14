#!/usr/bin/env python3
"""root_casing — the vault half of the root casing (agentm-vault plan 08).

The code half ships first: every literal that named a root space by its Title
Case spelling is lowercase, and `check-no-title-case-roots` keeps it so. This
script renames the four roots on disk to match — `Agent`, `Calendar`, `Personal`
and `Projects` become `agent`, `calendar`, `personal` and `projects` beside
`standards` — and proves it, on a case-insensitive disk under Drive where a
case-only rename needs a temporary name and git needs to be told.

  dry run   Reads the vault root as it is listed (exact names, never opens):
            which roots still carry the old name, which are done, and the git
            index count under each. Prints the manifest — one line per rename
            with its two-step and the reverse two-step — and records the plan
            under the engine state directory. Nothing changes.
  --apply   The recorded plan, refused unless the root listing still matches
            it, the count is confirmed, nothing is staged in the vault's index
            (the hand commit that follows is restricted to the renames) and
            no writer is live: the daemon and runner jobs booted out, Obsidian
            quit, no reflect hook running. Each root then moves in one command,
            `git mv <Root> .<root>-tmp && git mv .<root>-tmp <root>`, so the
            index moves with the tree (a plain `mv` leaves git believing the
            old path exists), with a pause between roots so Drive sees two
            renames rather than a delete and a create. The Obsidian settings
            that name a root and the vault's `.gitignore` are rewritten last,
            their pre-images journaled. Then the counts are read back: 0 under
            every old name, the old count under every new one.
  --finish  After the config keys, the exports, the rebuild and the daemon's
            return: every post-condition read from the vault and the config —
            five lowercase spaces and no Title Case or temporary directory,
            the index counts, the Obsidian settings, `memory_root`, the two
            daemon spaces, the exports, and the resolvers answering the new
            names with no override. The marker is written only when all hold.

`--revert RUN_ID` is the two-step in reverse for every root the run renamed,
newest first, refused when the root listing no longer matches what the run
left (a returned Title Case directory, a temporary name, a root gone) or when
a rewritten settings file no longer holds what the run wrote. It removes the
marker. The config keys and the exports are put back by hand, as they were set.

  python3 scripts/migrate/root_casing.py [--vault VAULT]
  python3 scripts/migrate/root_casing.py --apply --plan PLAN --confirm-count N [--pause SECONDS]
  python3 scripts/migrate/root_casing.py --finish --plan PLAN
  python3 scripts/migrate/root_casing.py --revert RUN_ID
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
_SCRIPTS = _REPO / "scripts"
_TOOLKIT = _REPO / "harness" / "skills" / "memory" / "scripts"
for _p in (str(_SCRIPTS), str(_TOOLKIT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

STAGE = "root-casing"
MARKER_REL = "agent/memory/.root-casing-complete"
# The names this migration retires, and what each becomes.
RENAMES = (("Agent", "agent"), ("Calendar", "calendar"), ("Personal", "personal"), ("Projects", "projects"))  # root-casing: the names this migration retires
LOWER = tuple(new for _old, new in RENAMES)
STANDARDS = "standards"
# The settings that name a root, and the one ignore file, rewritten after the
# renames; `workspace.json` is Obsidian's own and is left alone.
SETTINGS_REL = (".obsidian/daily-notes.json", ".obsidian/app.json", ".obsidian/bookmarks.json", ".gitignore")
_ROOT_FORM = re.compile(r"(?<![A-Za-z0-9_-])(%s)/" % "|".join(old for old, _new in RENAMES))
_ROOT_FORM_BARE = re.compile(r'"(%s)"' % "|".join(old for old, _new in RENAMES))
_CASING = dict(RENAMES)
# The config keys and the exports the finish checks.
EXPECTED_KEYS = {"plugins.obsidian-vault.memory_root": "agent",
                 "daemon.spaces.memory": "agent/memory",
                 "daemon.spaces.projects": "projects"}


class Refused(Exception):
    """The run cannot do what it was asked, and wrote nothing."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(vault: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True)


def listing(vault: Path) -> list[str]:
    """The vault root's entries as the disk lists them — exact names."""
    return sorted(os.listdir(vault))


def tracked_count(vault: Path, name: str) -> int:
    """How many index entries sit under `name/`, spelled exactly so — the
    whole listing filtered here rather than a pathspec, which a repository
    with `core.ignorecase` could match either way."""
    r = _git(vault, "ls-files", "-z")
    if r.returncode != 0:
        return 0
    prefix = name + "/"
    return sum(1 for p in r.stdout.split("\0") if p.startswith(prefix))


def temp_name(new: str) -> str:
    return f".{new.lower()}-tmp"


def two_step(vault: Path, old: str, new: str) -> str:
    """The one command that moves a root and its index entries through a
    temporary name, on a disk that would otherwise see no change."""
    v = str(vault)
    return (f'git -C "{v}" mv {old} {temp_name(new)} && git -C "{v}" mv {temp_name(new)} {new}')


# ── the plan ───────────────────────────────────────────────────────────────

def build_plan(vault) -> dict:
    """What each root's name is now, and what moves. Reads only."""
    vault = Path(vault)
    if not vault.is_dir():
        raise Refused(f"{vault} is not a directory")
    if _git(vault, "rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
        raise Refused(f"{vault} is not a git work tree; the rename must move the index with the tree")
    names = listing(vault)
    renames, problems = [], []
    for old, new in RENAMES:
        has_old, has_new, has_tmp = old in names, new in names, temp_name(new) in names
        if has_tmp:
            problems.append(f"{temp_name(new)}/ exists: a two-step did not finish; move it to {new}/ by hand first")
        if has_old and has_new:
            problems.append(f"both {old}/ and {new}/ exist: a Title Case directory came back beside the lowercase one")
        state = "pending" if has_old and not has_new else ("done" if has_new else "absent")
        count = tracked_count(vault, old if state == "pending" else new) if state != "absent" else 0
        renames.append({"old": old, "new": new, "state": state, "tracked": count})
    if problems:
        raise Refused("; ".join(problems))
    settings = {}
    for rel in SETTINGS_REL:
        p = vault / rel
        if p.is_file():
            text = p.read_text(encoding="utf-8")
            new_text = rewrite_settings(rel, text)
            settings[rel] = {"changes": new_text != text, "before_sha": _sha(text.encode("utf-8"))}
    pending = [r for r in renames if r["state"] == "pending"]
    return {"vault": str(vault), "listing": names, "renames": renames, "settings": settings,
            "counts": {"renames": len(pending), "tracked": sum(r["tracked"] for r in pending)}}


def rewrite_settings(rel: str, text: str) -> str:
    """The settings file with every root it names spelled the new way. A path
    inside a space keeps its own casing (`personal/Home/...`)."""
    if rel == ".gitignore":
        return _ROOT_FORM.sub(lambda m: _CASING[m.group(1)] + "/", text)
    out = _ROOT_FORM.sub(lambda m: _CASING[m.group(1)] + "/", text)
    return _ROOT_FORM_BARE.sub(lambda m: '"' + _CASING[m.group(1)] + '"', out)


def _signature(plan: dict) -> str:
    return json.dumps({"listing": plan["listing"], "renames": plan["renames"]}, sort_keys=True)


def manifest(plan: dict) -> list[str]:
    """The progress file's manifest: one line per rename, its command and its reverse."""
    vault = Path(plan["vault"])
    out = []
    for r in plan["renames"]:
        if r["state"] != "pending":
            continue
        out.append(f"- `{r['old']}/` becomes `{r['new']}/` ({r['tracked']:,} tracked files): "
                   f"`{two_step(vault, r['old'], r['new'])}`. "
                   f"Reverse: `{two_step(vault, r['new'], r['old'])}`.")
    for rel, s in plan["settings"].items():
        if s["changes"]:
            out.append(f"- `{rel}` is rewritten with the new names; its pre-image is journaled. "
                       f"Restore: `--revert`, or `git -C \"{vault}\" checkout HEAD -- \"{rel}\"`.")
    return out


def print_summary(plan: dict, out=sys.stdout) -> None:
    for r in plan["renames"]:
        if r["state"] == "pending":
            print(f"  rename: {r['old']}/ -> {r['new']}/  ({r['tracked']:,} tracked files)", file=out)
        elif r["state"] == "done":
            print(f"  done:   {r['new']}/ already carries the new name ({r['tracked']:,} tracked files)", file=out)
        else:
            print(f"  absent: neither {r['old']}/ nor {r['new']}/ exists", file=out)
    for rel, s in plan["settings"].items():
        print(f"  settings: {rel} {'names a root and is rewritten' if s['changes'] else 'already reads the new names'}",
              file=out)
    c = plan["counts"]
    print(f"  renames: {c['renames']} ({c['tracked']:,} tracked files move)", file=out)
    print("  manifest:", file=out)
    for line in manifest(plan):
        print(f"    {line}", file=out)


# ── the writers that must be quiet ─────────────────────────────────────────

def live_writers() -> list[str]:
    """Who could write into a root while it sits at its temporary name."""
    out = []
    uid = os.getuid() if hasattr(os, "getuid") else None
    for job in ("com.agentm.daemon", "com.agentm.runner"):
        if uid is not None and subprocess.run(["launchctl", "print", f"gui/{uid}/{job}"],
                                              capture_output=True).returncode == 0:
            out.append(f"{job} is loaded in launchd; boot it out first")
    if subprocess.run(["pgrep", "-x", "Obsidian"], capture_output=True).returncode == 0:
        out.append("Obsidian is running, and it rewrites links on a rename; quit it first")
    if subprocess.run(["pgrep", "-f", "memory-reflect"], capture_output=True).returncode == 0:
        out.append("a memory-reflect hook is running; wait for it to finish")
    return out


# ── apply, finish, revert ──────────────────────────────────────────────────

def apply(vault, recorded: dict, confirm_count: int, out_dir, *, pause: float = 5.0,
          writers=live_writers, sleep=time.sleep, run=None) -> dict:
    vault, out_dir = Path(vault), Path(out_dir)
    plan = build_plan(vault)
    if _signature(plan) != _signature(recorded):
        raise Refused("the vault root is not what the dry run read; run the dry run again. Nothing written.")
    if confirm_count != plan["counts"]["renames"]:
        raise Refused(f"the plan renames {plan['counts']['renames']} root(s); you confirmed {confirm_count}. "
                      "Nothing written.")
    staged = _git(vault, "diff", "--cached", "--name-only").stdout.strip()
    if staged:
        raise Refused("the vault's index holds staged changes, and the hand commit after the renames "
                      f"must hold the renames alone: {staged.splitlines()[0]} … Nothing written.")
    busy = writers()
    if busy:
        raise Refused("a writer is live: " + "; ".join(busy) + ". Nothing written.")
    run = run or (lambda cmd: subprocess.run(["sh", "-c", cmd], capture_output=True, text=True))
    run_id = recorded["run_id"]
    journal = {"run_id": run_id, "applied_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "vault": str(vault), "renames": [], "settings": {}, "landed": True}
    pending = [r for r in plan["renames"] if r["state"] == "pending"]
    for i, r in enumerate(pending):
        if i:
            sleep(pause)
        quiet = [w for w in writers() if "memory-reflect" in w]
        if quiet:
            journal["landed"] = False
            journal["renames"].append({**r, "skipped": quiet[0]})
            break
        before = r["tracked"]
        result = run(two_step(vault, r["old"], r["new"]))
        names = listing(vault)
        landed = (result.returncode == 0 and r["new"] in names and r["old"] not in names
                  and temp_name(r["new"]) not in names)
        after = tracked_count(vault, r["new"])
        entry = {"old": r["old"], "new": r["new"], "tracked_before": before, "tracked_after": after,
                 "old_name_tracked_after": tracked_count(vault, r["old"]), "landed": landed and after == before}
        if not landed:
            entry["stderr"] = (result.stderr or result.stdout).strip()[:400]
        journal["renames"].append(entry)
        if not entry["landed"]:
            journal["landed"] = False
            break
    if journal["landed"]:
        for rel, s in plan["settings"].items():
            if not s["changes"]:
                continue
            p = vault / rel
            text = p.read_text(encoding="utf-8")
            if _sha(text.encode("utf-8")) != s["before_sha"]:
                journal["landed"] = False
                journal["settings"][rel] = {"landed": False, "why": "changed since the dry run"}
                continue
            new_text = rewrite_settings(rel, text)
            p.write_text(new_text, encoding="utf-8")
            journal["settings"][rel] = {"landed": True, "before": text, "after_sha": _sha(new_text.encode("utf-8"))}
    journal["listing_after"] = listing(vault)
    journal["returned"] = [old for old, _new in RENAMES if old in journal["listing_after"]
                           and _CASING[old] in journal["listing_after"]]
    if journal["returned"]:
        journal["landed"] = False
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"journal-{run_id}.json").write_text(json.dumps(journal, indent=2) + "\n", encoding="utf-8")
    return journal


def _default_config_reader():
    def read(key: str):
        r = subprocess.run([sys.executable, str(_SCRIPTS / "agentm_config.py"), "--get", key],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return None
        value = r.stdout.strip()
        try:
            return json.loads(value)
        except ValueError:
            return value
    return read


def _config_values(read) -> dict:
    """The three keys, from a reader that returns a flat key's value (a nested
    one as its parsed object)."""
    out = {}
    out["plugins.obsidian-vault.memory_root"] = read("plugins.obsidian-vault.memory_root")
    spaces = read("daemon.spaces")
    if isinstance(spaces, str):
        try:
            spaces = json.loads(spaces)
        except ValueError:
            spaces = {}
    spaces = spaces if isinstance(spaces, dict) else {}
    out["daemon.spaces.memory"] = spaces.get("memory")
    out["daemon.spaces.projects"] = spaces.get("projects")
    return out


def _default_resolver(vault: Path):
    """`(vault_path, memory_root)` as the Python stack resolves them with no
    override in the environment — what every hook sees."""
    env = {k: v for k, v in os.environ.items() if k not in ("MEMORY_ROOT", "MEMORY_VAULT_PATH")}
    code = ("import harness_memory as hm; print(hm.vault_path() or ''); print(hm.memory_root() or '')")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=str(_SCRIPTS))
    lines = (r.stdout or "").splitlines() + ["", ""]
    return lines[0].strip(), lines[1].strip()


def finish(vault, recorded: dict, out_dir, *, config_reader=None, exports=None, resolver=None) -> list[str]:
    """The post-conditions that still fail; the marker is written when none do."""
    vault, out_dir = Path(vault), Path(out_dir)
    journal_path = out_dir / f"journal-{recorded['run_id']}.json"
    if not journal_path.is_file():
        raise Refused(f"no journal for {recorded['run_id']}; apply the plan first")
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    if not journal.get("landed"):
        raise Refused(f"the applied run did not land as planned; read {journal_path}")
    problems = []
    names = listing(vault)
    for old, new in RENAMES:
        if old in names:
            problems.append(f"{old}/ exists at the vault root: the Title Case name is back, or never moved")
        if temp_name(new) in names:
            problems.append(f"{temp_name(new)}/ exists: a two-step did not finish")
        if new not in names:
            problems.append(f"{new}/ is missing from the vault root")
        elif tracked_count(vault, old) and not tracked_count(vault, new):
            problems.append(f"the index still lists files under {old}/ and none under {new}/")
    if STANDARDS not in names:
        problems.append(f"{STANDARDS}/ is missing from the vault root")
    for r in journal.get("renames", []):
        if r.get("landed") and tracked_count(vault, r["new"]) < r["tracked_before"]:
            problems.append(f"{r['new']}/ tracks {tracked_count(vault, r['new'])} files, fewer than the "
                            f"{r['tracked_before']} that moved")
    for rel in SETTINGS_REL:
        p = vault / rel
        if p.is_file():
            text = p.read_text(encoding="utf-8")
            if rewrite_settings(rel, text) != text:
                problems.append(f"{rel} still names a root the old way")
    values = _config_values(config_reader or _default_config_reader())
    for key, want in EXPECTED_KEYS.items():
        if values.get(key) != want:
            problems.append(f"{key} reads {values.get(key)!r}; the root casing needs {want!r}")
    for label, path in (exports or default_exports(vault)).items():
        if path is None or not Path(path).is_file():
            continue
        try:
            env = json.loads(Path(path).read_text(encoding="utf-8")).get("env") or {}
        except (ValueError, OSError):
            env = {}
        value = str(env.get("MEMORY_VAULT_PATH") or env.get("MEMORY_ROOT") or "")
        if value and not value.rstrip("/").endswith("/" + LOWER[0]):
            problems.append(f"{label} exports MEMORY_VAULT_PATH={value}; it must end in /{LOWER[0]}")
    if resolver is not False:
        resolved_vault, resolved_root = (resolver or _default_resolver)(vault)
        if Path(resolved_vault or "/") != vault or Path(resolved_root or "/") != vault / LOWER[0]:
            problems.append(f"with no override, the Python stack resolves vault={resolved_vault!r} "
                            f"memory root={resolved_root!r}; expected {vault} and {vault / LOWER[0]}")
    if not problems:
        marker = vault / MARKER_REL
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"run {recorded['run_id']}\nfinished "
                          f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n", encoding="utf-8")
    return sorted(set(problems))


def default_exports(vault: Path) -> dict:
    return {"agentm .harness/project.json": _REPO / ".harness" / "project.json",
            "the vault's projects/agentm/_harness/project.json": vault / LOWER[3] / "agentm" / "_harness" / "project.json"}


def revert(vault, run_id: str, out_dir, *, run=None, sleep=time.sleep, pause: float = 5.0) -> None:
    vault, out_dir = Path(vault), Path(out_dir)
    journal_path = out_dir / f"journal-{run_id}.json"
    if not journal_path.is_file():
        raise Refused(f"no journal for {run_id}")
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    names = listing(vault)
    problems = []
    done = [r for r in journal.get("renames", []) if r.get("landed")]
    for r in done:
        if r["new"] not in names:
            problems.append(f"{r['new']}/ is gone; nothing to move back")
        if r["old"] in names:
            problems.append(f"{r['old']}/ exists beside {r['new']}/; the run's state is not what is on disk")
        if temp_name(r["new"]) in names or temp_name(r["old"]) in names:
            problems.append(f"a temporary name for {r['new']}/ exists; finish or clear it by hand")
    for rel, s in journal.get("settings", {}).items():
        p = vault / rel
        if s.get("landed") and p.is_file() and _sha(p.read_bytes()) != s["after_sha"]:
            problems.append(f"{rel} no longer holds what the run wrote; restoring it would lose a later write")
    if problems:
        raise Refused("; ".join(problems) + ". Nothing restored.")
    run = run or (lambda cmd: subprocess.run(["sh", "-c", cmd], capture_output=True, text=True))
    for i, r in enumerate(reversed(done)):
        if i:
            sleep(pause)
        result = run(two_step(vault, r["new"], r["old"]))
        names = listing(vault)
        if result.returncode != 0 or r["old"] not in names or r["new"] in names:
            raise Refused(f"moving {r['new']}/ back to {r['old']}/ failed: {(result.stderr or result.stdout).strip()[:300]}")
    for rel, s in journal.get("settings", {}).items():
        if s.get("landed"):
            (vault / rel).write_text(s["before"], encoding="utf-8")
    marker = vault / MARKER_REL
    if marker.exists():
        marker.unlink()


# ── command line ───────────────────────────────────────────────────────────

def _defaults():
    import engine_state  # noqa: E402
    import harness_memory as hm  # noqa: E402
    return Path(hm.vault_path()), Path(engine_state.engine_state_dir())


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", help="vault root (default: the configured one)")
    ap.add_argument("--state-dir", help="engine state directory (default: the configured one)")
    ap.add_argument("--apply", action="store_true", help="apply a recorded plan")
    ap.add_argument("--finish", action="store_true", help="check the post-conditions and write the marker")
    ap.add_argument("--plan", help="the plan file the dry run recorded")
    ap.add_argument("--confirm-count", type=int, help="the plan's rename count, typed by the operator")
    ap.add_argument("--pause", type=float, default=5.0, help="seconds between roots, for Drive (default 5)")
    ap.add_argument("--revert", metavar="RUN_ID", help="move every root a run renamed back")
    args = ap.parse_args(argv)

    vault = state_dir = None
    if not (args.vault and args.state_dir):
        vault, state_dir = _defaults()
    vault = Path(args.vault) if args.vault else vault
    state_dir = Path(args.state_dir) if args.state_dir else state_dir
    out_dir = state_dir / STAGE

    try:
        if args.revert:
            revert(vault, args.revert, out_dir, pause=args.pause)
            print(f"root casing: reverted {args.revert}; put the config keys and the exports back by hand")
            return 0
        if args.apply or args.finish:
            if not args.plan:
                print("root casing: --apply and --finish need --plan", file=sys.stderr)
                return 2
            recorded = json.loads(Path(args.plan).read_text(encoding="utf-8"))
            if args.apply:
                if args.confirm_count is None:
                    print("root casing: --apply needs --confirm-count", file=sys.stderr)
                    return 2
                journal = apply(vault, recorded, args.confirm_count, out_dir, pause=args.pause)
                for r in journal["renames"]:
                    print(f"  {r['old']}/ -> {r['new']}/: tracked {r.get('tracked_before')} before, "
                          f"{r.get('tracked_after')} after, {r.get('old_name_tracked_after')} left under the old name; "
                          f"{'landed' if r.get('landed') else 'DID NOT LAND: ' + str(r.get('stderr') or r.get('skipped'))}")
                for rel, s in journal["settings"].items():
                    print(f"  {rel}: {'rewritten' if s.get('landed') else 'NOT rewritten: ' + str(s.get('why'))}")
                print(f"  vault root now: {' '.join(journal['listing_after'])}")
                if journal["returned"]:
                    print(f"  a Title Case directory came back: {journal['returned']}")
                print(f"root casing: applied {recorded['run_id']}; journal matches the plan: "
                      f"{'yes' if journal['landed'] else 'NO'}")
                print(f'next: git -C "{vault}" commit -m "root casing: the four roots renamed" (the renames are '
                      f"staged; the settings are not), then the config keys and the exports, the rebuild, the "
                      f"daemon, and --finish --plan {args.plan}")
                return 0 if journal["landed"] else 1
            problems = finish(vault, recorded, out_dir)
            if problems:
                print(f"root casing: {len(problems)} post-condition(s) fail; no marker written", file=sys.stderr)
                for p in problems:
                    print(f"  {p}", file=sys.stderr)
                return 1
            marker = vault / MARKER_REL
            print(f"root casing: every post-condition holds; wrote {marker}")
            print(f"it is a new dot-named file, which the daemon does not commit: git -C \"{vault}\" add "
                  f"\"{MARKER_REL}\" && git -C \"{vault}\" commit -m \"root casing: the data run's marker\"")
            return 0
        run_id = "root-casing-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        plan = build_plan(vault)
    except Refused as exc:
        print(f"root casing: refused — {exc}", file=sys.stderr)
        return 1
    plan["run_id"] = run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    plan_path = out_dir / f"plan-{run_id}.json"
    plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    print(f"root casing — dry run {run_id} over {vault}")
    print_summary(plan)
    print(f"plan: {plan_path}")
    print(f"apply: python3 scripts/migrate/root_casing.py --vault \"{vault}\" --apply --plan {plan_path} "
          f"--confirm-count {plan['counts']['renames']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
