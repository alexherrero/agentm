#!/usr/bin/env python3
"""Gate: every vault project carries a `project.yaml` in its one schema (task 176).

A project's root holds five files since the operator's rulings of 2026-09-24,
and `project.yaml` is the one a session reads to tell which project it is in:
the repositories the project owns, the folders its code lives in, and the board
it syncs to. A copy that is missing, does not parse, or names a different
project is worse than none, because a session would trust it — so each of
those is a finding:

  - every live project directory under `projects/` has a `project.yaml`
    (`completed/`, `_`-prefixed and hidden directories are not projects);
  - it parses as YAML, to a mapping;
  - it carries the required keys, each with the right shape: `slug` (equal to
    the directory's name), `title`, `status` (one of the tracker's five),
    `repositories` (a list of `owner/repo`), `code_paths` (a list of
    home-relative paths, `~/…`);
  - the optional keys are well formed: `board` (`owner` and a positive
    `number`) and `sensitivity` (a word); nothing else is allowed, so a typo in
    a key name is caught rather than ignored.

The template the files are seeded from, `standards/templates/project.yaml`, is
checked the same way except for the slug, which it does not have.

It reads the live vault, resolved at runtime, and skips cleanly when none
resolves — CI has no vault. `--self-test` runs it against built-in fixtures
and asserts the findings, so the gate is proven able to fail.

Usage:
  python3 scripts/check-project-yaml.py              # the resolved vault
  python3 scripts/check-project-yaml.py --vault DIR
  python3 scripts/check-project-yaml.py --self-test
Exit: 0 clean (or no vault); 1 on a finding; 2 when PyYAML is missing.
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TOOLKIT = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for _p in (str(_HERE), str(_TOOLKIT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

FILENAME = "project.yaml"
TEMPLATE_REL = ("standards", "templates", FILENAME)
REQUIRED = ("slug", "title", "status", "repositories", "code_paths")
OPTIONAL = ("board", "sensitivity")
STATUSES = ("queued", "active", "parked", "done", "dropped")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_WORD = re.compile(r"^[a-z][a-z0-9-]*$")


def live_projects(vault: Path) -> list:
    space = vault / "projects"
    if not space.is_dir():
        return []
    return sorted(p for p in space.iterdir()
                  if p.is_dir() and not p.name.startswith((".", "_")) and p.name != "completed")


def check_mapping(data, rel: str, *, slug: "str | None") -> list:
    """Findings for one parsed file. `slug` is the directory's name, or None
    for the template, which carries no slug of its own."""
    out = []
    if not isinstance(data, dict):
        return [f"{rel}: does not parse to a mapping"]
    required = REQUIRED if slug is not None else tuple(k for k in REQUIRED if k != "slug")
    for key in required:
        if key not in data:
            out.append(f"{rel}: missing required key `{key}`")
    for key in data:
        if key not in REQUIRED + OPTIONAL:
            out.append(f"{rel}: unknown key `{key}` (allowed: {', '.join(REQUIRED + OPTIONAL)})")
    if slug is not None and "slug" in data and data["slug"] != slug:
        out.append(f"{rel}: slug `{data['slug']}` does not match its directory `{slug}`")
    if "title" in data and not (isinstance(data["title"], str) and data["title"].strip()):
        out.append(f"{rel}: `title` must be a non-empty string")
    if "status" in data and data["status"] not in STATUSES:
        out.append(f"{rel}: status `{data['status']}` is not one of {', '.join(STATUSES)}")
    repos = data.get("repositories")
    if "repositories" in data:
        if not isinstance(repos, list):
            out.append(f"{rel}: `repositories` must be a list (empty when the project has none)")
        else:
            for r in repos:
                if not (isinstance(r, str) and _REPO.match(r)):
                    out.append(f"{rel}: repository `{r}` is not `owner/repo`")
    paths = data.get("code_paths")
    if "code_paths" in data:
        if not isinstance(paths, list):
            out.append(f"{rel}: `code_paths` must be a list (empty when the project has no code)")
        else:
            for p in paths:
                if not (isinstance(p, str) and p.startswith("~/") and ".." not in p):
                    out.append(f"{rel}: code path `{p}` is not home-relative (`~/…`)")
    if "board" in data:
        b = data["board"]
        if not (isinstance(b, dict) and set(b) == {"owner", "number"}
                and isinstance(b.get("owner"), str) and b["owner"]
                and isinstance(b.get("number"), int) and not isinstance(b.get("number"), bool)
                and b["number"] > 0):
            out.append(f"{rel}: `board` must be `owner` and a positive `number`, nothing else")
    if "sensitivity" in data and not (isinstance(data["sensitivity"], str) and _WORD.match(data["sensitivity"])):
        out.append(f"{rel}: `sensitivity` must be one lowercase word")
    return out


def scan(vault: Path, yaml) -> tuple:
    findings, checked = [], 0
    for project in live_projects(vault):
        path = project / FILENAME
        rel = f"projects/{project.name}/{FILENAME}"
        if not path.is_file():
            findings.append(f"{rel}: missing — every project root carries one")
            continue
        checked += 1
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:  # yaml.YAMLError and decoding errors alike
            findings.append(f"{rel}: does not parse ({str(exc).splitlines()[0]})")
            continue
        findings.extend(check_mapping(data, rel, slug=project.name))
    template = vault.joinpath(*TEMPLATE_REL)
    if template.is_file():
        try:
            data = yaml.safe_load(template.read_text(encoding="utf-8"))
            findings.extend(check_mapping(data, "/".join(TEMPLATE_REL), slug=None))
        except Exception as exc:
            findings.append(f"{'/'.join(TEMPLATE_REL)}: does not parse ({str(exc).splitlines()[0]})")
    return findings, checked


def resolve_vault(arg: "str | None") -> "Path | None":
    if arg:
        return Path(arg)
    try:
        import harness_memory as hm  # noqa: E402
        return Path(hm.vault_path())
    except Exception:
        return None


def run_self_test(yaml) -> int:
    good = ("slug: alpha\ntitle: Alpha\nstatus: active\nrepositories:\n  - owner/alpha\n"
            "code_paths:\n  - ~/code/alpha\nboard:\n  owner: owner\n  number: 2\n")
    cases = {
        "alpha": (good, []),
        "beta": ("slug: gamma\ntitle: B\nstatus: active\nrepositories: []\ncode_paths: []\n", ["does not match"]),
        "delta": ("title: D\nstatus: live\nrepositories: owner/d\ncode_paths: [/abs/path]\nboard: {owner: o}\ncolour: red\n",
                  ["missing required key `slug`", "not one of", "must be a list", "unknown key `colour`", "`board` must"]),
        "epsilon": ("slug: epsilon\ntitle: [unclosed\n", ["does not parse"]),
        "zeta": (None, ["missing"]),
    }
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td)
        for slug, (text, _) in cases.items():
            d = vault / "projects" / slug
            d.mkdir(parents=True)
            if text is not None:
                (d / FILENAME).write_text(text, encoding="utf-8")
        (vault / "projects" / "completed" / "old").mkdir(parents=True)
        findings, _ = scan(vault, yaml)
    failed = False
    for slug, (_, wants) in cases.items():
        mine = [f for f in findings if f.startswith(f"projects/{slug}/")]
        if not wants and mine:
            print(f"self-test: {slug} should be clean, got {mine}", file=sys.stderr)
            failed = True
        for w in wants:
            if not any(w in f for f in mine):
                print(f"self-test: {slug} should report {w!r}, got {mine}", file=sys.stderr)
                failed = True
    if any("completed" in f for f in findings):
        print("self-test: completed/ was checked as a project", file=sys.stderr)
        failed = True
    print("check-project-yaml: self-test " + ("FAILED" if failed else "passed"))
    return 1 if failed else 0


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default=None, help="vault root (default: resolved at runtime)")
    ap.add_argument("--self-test", action="store_true", help="prove the gate can fail")
    args = ap.parse_args(argv)
    try:
        import yaml
    except ImportError:
        print("check-project-yaml: PyYAML is not installed — python3 -m pip install -r requirements.txt",
              file=sys.stderr)
        return 2
    if args.self_test:
        return run_self_test(yaml)
    vault = resolve_vault(args.vault)
    if vault is None or not (vault / "projects").is_dir():
        print("check-project-yaml: no vault projects space resolves; nothing to check")
        return 0
    findings, checked = scan(vault, yaml)
    for f in findings:
        print(f"FINDING: {f}")
    print(f"check-project-yaml: {len(findings)} finding(s) across {checked} of "
          f"{len(live_projects(vault))} projects")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
