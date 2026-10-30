#!/usr/bin/env python3
"""vault_note_check.py — check one vault note the moment it is saved.

Memory cards reach the vault through the memory server, which checks their type
and writes their header. Plans, design parts, trackers and reports don't: a
session writes them straight into the vault, and until this check nothing read
them before the next full gate battery. In the week to 2026-10-29 that let
through six design parts whose `title:` held an unquoted colon, two trackers at
`status: open`, ten notes at types nobody had registered and a report at
`kind: check`, each fixed by hand later by a session that hadn't written it.

The vault's `.harness/verify.sh` runs this with the saved file's path, through
the machine-wide `verify-dispatch` PostToolUse hook. It asks the three vault
gates' own questions of that one note, and holds no rule of its own:

  the header parses       `check-vault-frontmatter.py`'s `check_note`
  the type is registered  `kind_registry.note_kind` and `classify`, the audit
                          `check-vocabulary-membership.py` runs
  a tracker holds         `check-tracker-schema.py`'s `note_findings`

Each check runs only where its gate's own walk would reach the note
(`in_scan`, `in_corpus`, `in_projects_space`), so a note a gate skips is
skipped here by the same rule. A retired type is not a failure, as it is not in
the gate: the migration already maps it.

What this file adds is the message. A session reads it right after its save, so
it names the line and the fix: the YAML error and the line it stops at, the
nearest registered types, the allowed statuses.

Silence is the default. A clean note, a file outside the vault, a file that is
not Markdown, and a machine with no vault all exit 0 and print nothing. On a
finding it exits 2 with the message on stderr, which Claude Code hands back to
the session that saved the note. It never edits the note: a PostToolUse hook
can't undo a write, so the session that wrote it fixes it.

The vault root comes from `harness_memory.vault_path()` at run time.

Usage:
  python3 scripts/vault_note_check.py --file PATH [--vault DIR]
  python3 scripts/vault_note_check.py --install [--vault DIR]   # the installers' call
Exit: 0 clean or out of scope; 2 on a finding; 1 if the check itself breaks.
"""
from __future__ import annotations

import argparse
import difflib
import importlib.util
import re
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
for _p in (str(_HERE), str(_HERE.parent / "harness" / "skills" / "memory" / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import kind_registry  # noqa: E402
import storage_rules  # noqa: E402
import tracker as tk  # noqa: E402


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


frontmatter_gate = _load("check_vault_frontmatter", "check-vault-frontmatter.py")
tracker_gate = _load("check_tracker_schema", "check-tracker-schema.py")

# The projects space under the vault root, the one `check-tracker-schema`
# resolves first.
PROJECTS = "projects"

# How many registered types a message offers for an unregistered one.
_NEAREST = 3

_QUOTE_HINT = ("A value holding `: ` or ` #`, or starting with a character YAML "
               "reserves, must be quoted: `title: \"Part 2: the renderer\"`.")
_STATUS_HINT = ("`tracker.py transition` can't move a status it doesn't know, so "
                "fix this line by hand.")


class Problem:
    """One thing wrong with the saved note, with the file line it sits on."""

    def __init__(self, text: str, line: Optional[int] = None):
        self.text = text
        self.line = line

    def render(self) -> str:
        return f"  line {self.line}: {self.text}" if self.line else f"  {self.text}"


def resolve_vault(explicit: Optional[str]) -> Optional[Path]:
    """The vault root, or None when this machine has none."""
    if explicit:
        return Path(explicit).expanduser()
    try:
        import harness_memory
        return harness_memory.vault_path()
    except Exception:  # noqa: BLE001 - no vault is a silent pass, never a block
        return None


def vault_rel(path: Path, vault: Path) -> Optional[str]:
    """The note's POSIX path from the vault root, or None when it sits outside."""
    try:
        return path.resolve().relative_to(vault.resolve()).as_posix()
    except (ValueError, OSError):
        return None


def _key_line(text: str, key: str) -> Optional[int]:
    """The file line of a top-level frontmatter key, or None."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    pattern = re.compile(rf"^{re.escape(key)}[ \t]*:")
    for index, line in enumerate(lines[1:], start=2):
        if line.strip() == "---":
            return None
        if pattern.match(line):
            return index
    return None


def _parse_error(text: str, yaml_mod) -> tuple:
    """`(file line, YAML's own words)` for a header that won't parse; either is
    None when the parser doesn't say."""
    match = frontmatter_gate._FRONTMATTER_RE.match(text)
    if not match:
        return None, None
    try:
        yaml_mod.safe_load(match.group(1))
    except Exception as exc:  # noqa: BLE001 - any yaml.YAMLError subclass
        mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
        line = mark.line + frontmatter_gate._FILE_LINE if mark is not None else None
        return line, getattr(exc, "problem", None)
    return None, None


def header_problems(path: Path, rel: str, text: str, yaml_mod) -> list:
    """`check-vault-frontmatter`'s findings for the note, as messages."""
    out = []
    for finding in frontmatter_gate.check_note(path, rel, yaml_mod):
        if finding.code == "parse-error":
            line, said = _parse_error(text, yaml_mod)
            quoted = ""
            if line:
                quoted = f" at `{text.splitlines()[line - 1].strip()}`"
            out.append(Problem(
                f"the header doesn't parse{quoted}; YAML says: {said or finding.detail}. "
                f"{_QUOTE_HINT}", line))
        else:
            out.append(Problem(f"{finding.detail} [{finding.code}]", finding.line))
    return out


def nearest_types(value: str, known) -> list:
    return difflib.get_close_matches(value, sorted(known), n=_NEAREST, cutoff=0.0)


def type_problems(rel: str, text: str) -> list:
    """The vocabulary audit's verdict on the note's `type:` or `kind:`."""
    try:
        if not kind_registry.in_corpus(rel):
            return []
    except storage_rules.StorageRulesError:
        return []
    raw = kind_registry.note_kind(text)
    if raw is None:
        return []
    try:
        known = storage_rules.known_values()
        bucket = kind_registry.classify(raw, known)
    except storage_rules.StorageRulesError:
        return []  # no daemon, no contract: the other two checks still run
    if bucket is None or bucket == "retired":
        return []
    field = "type" if _key_line(text, "type") else "kind"
    line = _key_line(text, field)
    if raw == "<both type and kind>":
        return [Problem("the header carries both `type:` and `kind:`; a note carries one", line)]
    nearest = ", ".join(nearest_types(raw, known))
    where = "`standards/storage-rules.md`"
    if bucket == "malformed":
        return [Problem(f"`{field}: {raw}` isn't kebab-case, so no register can hold it. "
                        f"Nearest registered: {nearest}. The registers are in {where}.", line)]
    return [Problem(f"`{field}: {raw}` isn't a registered type. Nearest registered: "
                    f"{nearest}. Use one of them, or register the value in {where}.", line)]


def tracker_problems(path: Path, rel: str, text: str) -> list:
    """`check-tracker-schema`'s findings, for a note in a project's directory."""
    parts = tuple(rel.split("/"))
    if len(parts) < 3 or parts[0] != PROJECTS:
        return []
    slug, inner = parts[1], parts[2:]
    if not tracker_gate.in_projects_space(slug, inner):
        return []
    out = []
    for finding in tracker_gate.note_findings(path, slug, inner) or []:
        key = re.match(r"`(\w+)", finding)
        line = _key_line(text, key.group(1)) if key else None
        if finding.startswith("`status:") and "is not one of" in finding:
            finding = f"{finding}. {_STATUS_HINT}"
        out.append(Problem(finding, line))
    return out


def check_file(path: Path, vault: Path, yaml_mod=None) -> list:
    """Every problem in the saved note at `path`; empty when it is clean or
    out of scope."""
    if path.suffix != ".md" or not path.is_file():
        return []
    rel = vault_rel(path, vault)
    if rel is None:
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    problems = []
    if yaml_mod is not None and frontmatter_gate.in_scan(rel, frontmatter_gate._walled_areas()):
        problems += header_problems(path, rel, text, yaml_mod)
    problems += type_problems(rel, text)
    problems += tracker_problems(path, rel, text)
    return sorted(problems, key=lambda p: p.line or 0)


def render(rel: str, problems: list) -> str:
    head = (f"vault note check: {rel} was saved with "
            f"{len(problems)} problem{'s' if len(problems) != 1 else ''}. "
            f"The save stands; fix the note now.")
    return "\n".join([head] + [p.render() for p in problems])


# ── the nightly sweep ─────────────────────────────────────────────────────────

def sweep(vault: Path, yaml_mod=None) -> list:
    """Every note in the vault the three gates would fail tonight, as
    `{"rel", "project", "finding"}` sorted by path. The morning note runs this:
    scripts, the daemon and other hosts' sessions write notes no hook sees,
    and this is where they are named. It runs each gate's whole-vault walk,
    so its findings are the gates' own. `project` is the note's project under
    `projects/`, else the space it sits in."""
    found = []
    if yaml_mod is not None:
        findings, _ = frontmatter_gate.scan_vault(vault, yaml_mod)
        found += [(f.rel, f"{f.code}: {f.detail}") for f in findings]
    audit = kind_registry.audit(vault, vault_root=vault)
    for bucket in ("unrecognized", "malformed"):
        found += [(rel, f"`{raw}` isn't a registered type") for rel, raw in audit[bucket]]
    projects = vault / PROJECTS
    if projects.is_dir():
        _, findings = tracker_gate.projects_findings(projects)
        for line in findings:
            rel, _, finding = line.partition(": ")
            found.append((f"{PROJECTS}/{rel}", finding))
    out = []
    for rel, finding in sorted(found):
        parts = rel.split("/")
        project = parts[1] if parts[0] == PROJECTS and len(parts) > 2 else parts[0]
        out.append({"rel": rel, "project": project, "finding": finding})
    return out


# ── install ───────────────────────────────────────────────────────────────────

# The templates the vault's `.harness/` gets, by the name each is installed as.
# Both twins go in on every host: the vault syncs between machines, and
# verify-dispatch.ps1 runs `verify.ps1` in preference to `verify.sh`.
VERIFY_TEMPLATES = (("verify.sh", "vault-verify.sh"), ("verify.ps1", "vault-verify.ps1"))
# A verify script whose head carries this is AgentM's to rewrite.
MARKER = "vault-verify (AgentM)"


def install_verify(vault: Path, templates: Path) -> list:
    """Put the verify scripts in `<vault>/.harness/`, and say what happened,
    one line per script. A script that is there without the marker is the
    operator's own and is left alone."""
    harness = vault / ".harness"
    harness.mkdir(exist_ok=True)
    lines = []
    for name, template in VERIFY_TEMPLATES:
        body = (templates / template).read_text(encoding="utf-8")
        dest = harness / name
        if dest.exists():
            current = dest.read_text(encoding="utf-8", errors="replace")
            if MARKER not in "\n".join(current.splitlines()[:3]):
                lines.append(f"WARN: {dest} is not AgentM's; left as it is, "
                             f"so a saved note is not checked on this host")
                continue
            if current == body:
                lines.append(f"{dest}: current")
                continue
        dest.write_text(body, encoding="utf-8")
        if name.endswith(".sh"):
            dest.chmod(0o755)
        lines.append(f"{dest}: installed")
    return lines


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--file", help="the note that was just saved")
    action.add_argument("--install", action="store_true",
                        help="install the vault's verify scripts (install.sh, install.ps1)")
    parser.add_argument("--vault", default=None, help="the vault root (default: resolved)")
    args = parser.parse_args(argv)

    vault = resolve_vault(args.vault)
    if args.install:
        if vault is None or not vault.is_dir():
            print("no vault resolves; the save-time check is not installed")
            return 0
        for line in install_verify(vault, _HERE.parent / "templates"):
            print(line)
        return 0
    if vault is None or not vault.is_dir():
        return 0
    try:
        import yaml
    except ImportError:
        yaml = None  # the header check needs PyYAML; the other two don't
    path = Path(args.file).expanduser()
    problems = check_file(path, vault, yaml)
    if not problems:
        return 0
    print(render(vault_rel(path, vault), problems), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
