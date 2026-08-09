#!/usr/bin/env python3
"""Gate: every vault note's frontmatter parses as YAML.

Nothing in the stack parsed frontmatter strictly before this gate. The two vault
linters (`frontmatter_validator.py`, `vault_lint.py`) are stdlib-only by design —
they split `key: value` on the first colon and never parse, so a syntax error is
invisible to them: they read the value wrong and report nothing. Both also carry
an `_EXCLUDE_DIRS` frozenset containing `_harness`, which is where the design and
handoff memos live — the notes most likely to break, because their `status:` and
`inputs:` fields carry long prose. The Go daemon has no YAML dependency at all;
`splitFrontmatter` is a regex split. Nine notes carried unparseable frontmatter
for up to two months under fully green CI, and surfaced only when
`alias_backfill.py` refused to write them.

This gate is the strict parse. It walks every `.md` under the resolved vault —
deliberately **without** `_EXCLUDE_DIRS`, since that is where the defects live —
and reports three classes:

  parse-error      `yaml.safe_load` raises. The usual cause is an unquoted
                   scalar containing ": ", which YAML reads as a nested mapping
                   and rejects with "mapping values are not allowed here".

  not-a-mapping    The block parses, but to a list, a bare scalar, or nothing.
                   Every consumer expects a mapping.

  truncated-value  An unquoted scalar contains " #", so YAML ends the value at
                   the comment and drops the rest. No error is raised. The note
                   just loses text — one note lost 108 of 509 characters.

A `#` that opens a conventional comment (followed by a space, or ending the
line) is not reported: `area: agentm/storage   # the seam owns this area` is a
deliberate comment and legal YAML. A `#` followed by a non-space character is
not a comment — in this corpus it is an issue reference like `#13` sitting
inside a value that YAML then truncates. Every truncation is confirmed against
the parser before it is reported: the scanner predicts the prefix YAML would
keep, and reports only when `yaml.safe_load` returned exactly that prefix. When
the parser disagrees, the scanner declines to guess and stays quiet.

Known gaps, all in the quiet direction. A truncating `# ` reads as a deliberate
comment and is not reported. A plain scalar continued on the following line is
skipped, because the parsed value cannot match the predicted prefix. Truncation
scanning covers top-level keys and their block-list items, not deeper nesting —
the corpus keeps frontmatter flat. Parse errors are caught at any depth, since
the parse is over the whole block.

The vault path is resolved at runtime via `harness_memory.vault_path()` —
`$MEMORY_VAULT_PATH`, then `plugins.obsidian-vault.vault_path` from the kernel
config. Never a literal (see AGENTS.md § Vault-path convention).

Usage:
  python3 scripts/check-vault-frontmatter.py            # scan the resolved vault
  python3 scripts/check-vault-frontmatter.py --vault DIR
  python3 scripts/check-vault-frontmatter.py --self-test # scan built fixtures

Exit:
  0  clean, or no vault to scan (graceful skip)
  1  violations found
  2  setup error (PyYAML missing, --vault not a directory, self-test failed)
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


# ── frontmatter extraction ────────────────────────────────────────────────────

# A block only counts as frontmatter when the file opens with the fence. Matches
# alias_backfill.py's FRONTMATTER_RE, plus a closing fence at end-of-file.
_FRONTMATTER_RE = re.compile(r"\A---[ \t\r]*\n(.*?)\n---[ \t\r]*(?:\n|\Z)", re.S)

# A top-level `key: value` line — no indentation, no leading comment marker.
_TOP_KEY_RE = re.compile(r"^(?P<key>[^\s#][^:]*):(?P<rest>[ \t].*|)$")

# A `#` opening a comment: at the start of the value, or preceded by whitespace.
_HASH_RE = re.compile(r"(?:^|(?<=\s))#")

# Values YAML resolves to None. A `key: null` line must not read as "eaten".
_NULL_SPELLINGS = frozenset({"null", "Null", "NULL", "~"})

# Value text that is not a single-line plain scalar: quoted, block, flow,
# anchored, or tagged. Truncation cannot apply, so the scanner skips it.
_NON_PLAIN_PREFIXES = ('"', "'", "|", ">", "[", "{", "&", "*", "!")

# Directories pruned from the walk. Dot-directories only — `_harness`, `_inbox`,
# `_archive` and the rest of the underscore namespace are scanned on purpose.
_SKIP_DIR_PREFIXES = (".",)

# Offset from a 0-based frontmatter-block index to a 1-based file line: one for
# the opening `---`, one for the 1-based count.
_FILE_LINE = 2


class Finding:
    """One defect in one note."""

    def __init__(self, rel: str, code: str, detail: str, line: int | None = None):
        self.rel = rel
        self.code = code
        self.detail = detail
        self.line = line

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Finding({self.rel!r}, {self.code!r}, line={self.line!r})"

    def render(self) -> str:
        where = f"{self.rel}:{self.line}" if self.line else self.rel
        return f"  {where} [{self.code}]  {self.detail}"


# ── the three checks ──────────────────────────────────────────────────────────

def _predict_kept(value: str) -> tuple[str, int] | None:
    """Return (prefix YAML keeps, index of the `#`) for a truncating value.

    None when the value has no `#`, or when its `#` opens a conventional comment
    (a space follows it, or it ends the line).
    """
    for match in _HASH_RE.finditer(value):
        after = value[match.end():]
        if after == "" or after[0].isspace():
            return None  # conventional comment — deliberate, not a defect
        return value[:match.start()].rstrip(), match.start()
    return None


def _scan_scalar(value: str, parsed: object) -> tuple[str, int] | None:
    """Confirm a truncation against the parser. Returns (kept, lost) or None."""
    if value.startswith(_NON_PLAIN_PREFIXES):
        return None
    prediction = _predict_kept(value)
    if prediction is None:
        return None
    kept, _ = prediction
    if kept == "":
        # The whole value was eaten. YAML leaves None behind.
        if parsed is None and value not in _NULL_SPELLINGS:
            return "", len(value)
        return None
    if isinstance(parsed, str) and parsed == kept:
        return kept, len(value) - len(kept)
    return None  # the parser disagrees with the prediction — do not guess


def _block_list_items(lines: list[str], key_index: int) -> list[tuple[int, str]] | None:
    """Raw `  - item` lines under a top-level key, as (0-based lineno, text).

    None when the shape is anything other than a flat block list — a nested
    mapping, a continuation line, a blank line. The caller then skips the key.
    """
    items: list[tuple[int, str]] = []
    for i in range(key_index + 1, len(lines)):
        line = lines[i]
        if not line.strip():
            return None
        if len(line) - len(line.lstrip()) == 0:
            break  # next top-level key
        stripped = line.lstrip()
        if not stripped.startswith("- "):
            return None
        items.append((i, stripped[2:].rstrip()))
    return items


def scan_block(rel: str, block: str, doc: dict) -> list[Finding]:
    """Report every confirmed truncation in one parsed frontmatter block.

    Line numbers are reported against the file, not the block, so `file:line`
    lands on the offending line in an editor. The block starts on file line 2,
    below the opening fence, so a 0-based block index is offset by `_FILE_LINE`.
    """
    findings: list[Finding] = []
    lines = block.splitlines()

    for index, line in enumerate(lines):
        match = _TOP_KEY_RE.match(line)
        if not match:
            continue
        key = match.group("key").strip()
        if key not in doc:
            continue  # a key the parser did not see — do not guess at it
        parsed = doc[key]
        value = match.group("rest").strip()

        if value:
            hit = _scan_scalar(value, parsed)
            if hit is not None:
                kept, lost = hit
                findings.append(Finding(
                    rel, "truncated-value",
                    f"`{key}:` loses {lost} of {len(value)} characters at an "
                    f"unquoted `#` — YAML kept {kept!r}. Quote the value.",
                    line=index + _FILE_LINE,
                ))
            continue

        # No inline value: a block list may follow.
        if not isinstance(parsed, list) or not parsed:
            continue
        items = _block_list_items(lines, index)
        if items is None or len(items) != len(parsed):
            continue
        for (item_index, raw_item), parsed_item in zip(items, parsed):
            hit = _scan_scalar(raw_item, parsed_item)
            if hit is None:
                continue
            kept, lost = hit
            findings.append(Finding(
                rel, "truncated-value",
                f"`{key}:` item loses {lost} of {len(raw_item)} characters at "
                f"an unquoted `#` — YAML kept {kept!r}. Quote the item.",
                line=item_index + _FILE_LINE,
            ))
    return findings


def check_note(path: Path, rel: str, yaml_mod) -> list[Finding]:
    """Every finding in one note. Empty when the note is clean or unfenced."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [Finding(rel, "unreadable", str(exc))]

    match = _FRONTMATTER_RE.match(text)
    if not match:
        return []  # no frontmatter is not a defect

    block = match.group(1)
    try:
        doc = yaml_mod.safe_load(block)
    except Exception as exc:  # noqa: BLE001 - any yaml.YAMLError subclass
        detail = " ".join(str(exc).split())
        return [Finding(rel, "parse-error", detail[:300])]

    if not isinstance(doc, dict):
        kind = type(doc).__name__
        return [Finding(
            rel, "not-a-mapping",
            f"frontmatter parses to {kind}, not a mapping",
        )]

    return scan_block(rel, block, doc)


# ── walk ──────────────────────────────────────────────────────────────────────

def iter_notes(root: Path):
    """Every `.md` under root, dot-directories pruned, sorted for determinism."""
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(
            d for d in dirs if not d.startswith(_SKIP_DIR_PREFIXES)
        )
        for name in sorted(files):
            if name.endswith(".md"):
                yield Path(dirpath) / name


def scan_vault(root: Path, yaml_mod) -> tuple[list[Finding], int]:
    """Scan every note under root. Returns (findings, notes scanned)."""
    findings: list[Finding] = []
    scanned = 0
    for path in iter_notes(root):
        scanned += 1
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:  # pragma: no cover - path always under root
            rel = str(path)
        findings.extend(check_note(path, rel, yaml_mod))
    return findings, scanned


# ── output ────────────────────────────────────────────────────────────────────

def _write(stream, text: str) -> None:
    """Print without letting a console encoding turn a finding into a crash."""
    try:
        print(text, file=stream)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "ascii"
        print(text.encode(encoding, "replace").decode(encoding), file=stream)


def report(findings: list[Finding], scanned: int, root: Path) -> int:
    if not findings:
        _write(sys.stdout, f"check-vault-frontmatter: {scanned} notes clean ({root})")
        return 0
    _write(sys.stderr, (
        f"check-vault-frontmatter: {len(findings)} violation(s) across "
        f"{scanned} notes in {root}"
    ))
    _write(sys.stderr, (
        "  Frontmatter must parse as a YAML mapping. Quote any value holding "
        "a colon-space or a `#`."
    ))
    for finding in sorted(findings, key=lambda f: (f.rel, f.line or 0)):
        _write(sys.stderr, finding.render())
    return 1


# ── vault resolution ──────────────────────────────────────────────────────────

def resolve_vault(explicit: str | None) -> Path | None:
    """The vault to scan, or None to skip. Never a literal path."""
    if explicit:
        return Path(os.path.expanduser(explicit))
    try:
        import harness_memory
        return harness_memory.vault_path()
    except Exception as exc:  # noqa: BLE001 - import or backend-guard failure
        print(
            f"check-vault-frontmatter: no vault resolved ({exc}) — skipping",
            file=sys.stderr,
        )
        return None


# ── self-test ─────────────────────────────────────────────────────────────────

# Fixtures, and the (code, file line) each one must produce. The expected side
# is hand-written — counted off the fixture body by hand, never recomputed from
# the scanner's own offsets, which is the only way it can catch an off-by-one in
# them. `None` marks a whole-block finding, which carries no line. Paths put the
# two hardest cases under `_harness/`, the directory both existing linters
# exclude — a scanner that inherited `_EXCLUDE_DIRS` fails here.
_FIXTURES: list[tuple[str, str, list[tuple[str, int | None]]]] = [
    (
        "personal/clean.md",
        "---\nkind: reference\ntitle: A clean note\ntags: [one, two]\n---\n\nBody.\n",
        [],
    ),
    (
        "projects/agentm/_harness/designs/parse-error.md",
        "---\nstatus: rendered 2026-07-06: the judgment layer for the re-audit\n"
        "kind: design\n---\n\nAn unquoted scalar holding a colon-space.\n",
        [("parse-error", None)],
    ),
    (
        "projects/agentm/_harness/designs/truncated.md",
        # `prd:` is file line 3 — fence, kind, prd.
        "---\nkind: design\nprd: <none, codified from ROADMAP item #13 plus the "
        "predecessor>\n---\n\nThe value ends at the issue reference.\n",
        [("truncated-value", 3)],
    ),
    (
        "_inbox/not-a-mapping.md",
        "---\n- just\n- a list\n---\n\nParses, but not to a mapping.\n",
        [("not-a-mapping", None)],
    ),
    (
        "_archive/comment-is-legal.md",
        "---\nkind: design\narea: agentm/storage          # the seam owns this "
        "area\ngoverns: []  # stamped at lift\n---\n\nDeliberate comments, not "
        "defects.\n",
        [],
    ),
    (
        "personal/quoted-hash.md",
        '---\nkind: reference\ntitle: "Writer #2 plus source resolution"\n'
        "---\n\nQuoted, so the hash is literal.\n",
        [],
    ),
    (
        "personal/list-truncated.md",
        # The bad item is file line 5 — fence, kind, aliases, clean item, bad item.
        "---\nkind: reference\naliases:\n  - a clean alias\n  - an alias #7 with "
        "more text\n---\n\nOne list item truncates.\n",
        [("truncated-value", 5)],
    ),
    (
        "personal/value-eaten.md",
        # `title:` is file line 3 — fence, kind, title.
        "---\nkind: reference\ntitle: #13\n---\n\nThe whole value is eaten.\n",
        [("truncated-value", 3)],
    ),
    (
        "personal/explicit-null.md",
        "---\nkind: reference\ntitle: null\n---\n\nA real null is not a defect.\n",
        [],
    ),
    (
        "personal/no-frontmatter.md",
        "# Just a heading\n\nNo fence at all.\n",
        [],
    ),
]


def run_self_test(yaml_mod) -> int:
    """Build a scratch vault of known-bad notes and assert the exact findings."""
    with tempfile.TemporaryDirectory(prefix="check-vault-frontmatter-") as tmp:
        root = Path(tmp)
        for rel, body, _ in _FIXTURES:
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")

        findings, scanned = scan_vault(root, yaml_mod)

        expected = sorted(
            (rel, code, line)
            for rel, _, hits in _FIXTURES
            for code, line in hits
        )
        actual = sorted((f.rel, f.code, f.line) for f in findings)

        failures: list[str] = []
        if scanned != len(_FIXTURES):
            failures.append(
                f"scanned {scanned} notes, expected {len(_FIXTURES)}"
            )
        for rel, code, line in [t for t in expected if t not in actual]:
            failures.append(f"missed   {code:15} {rel}:{line}")
        for rel, code, line in [t for t in actual if t not in expected]:
            failures.append(f"spurious {code:15} {rel}:{line}")

        if failures:
            _write(sys.stderr, "check-vault-frontmatter --self-test: FAILED")
            for line in failures:
                _write(sys.stderr, f"  {line}")
            for finding in findings:
                _write(sys.stderr, f"  reported: {finding.render().strip()}")
            return 2

        clean = sum(1 for _, _, hits in _FIXTURES if not hits)
        _write(sys.stdout, (
            f"check-vault-frontmatter --self-test: PASS — {len(expected)} "
            f"violation(s) detected at the expected file:line across "
            f"{scanned} fixture notes, {clean} of them clean."
        ))
        return 0


# ── main ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument(
        "--vault", default=None,
        help="Vault root to scan (default: resolved at runtime)",
    )
    parser.add_argument(
        "--self-test", action="store_true",
        help="Scan built-in fixtures instead of a vault, and assert the findings",
    )
    args = parser.parse_args(argv)

    try:
        import yaml
    except ImportError:
        print(
            "check-vault-frontmatter: PyYAML is not installed — this gate cannot "
            "run without it. Install it: python3 -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 2

    if args.self_test:
        return run_self_test(yaml)

    root = resolve_vault(args.vault)
    if root is None:
        print("check-vault-frontmatter: no vault configured — skipping")
        return 0
    if not root.is_dir():
        stream = sys.stderr if args.vault else sys.stdout
        message = f"check-vault-frontmatter: not a directory: {root}"
        if args.vault:
            print(message, file=stream)
            return 2
        print(f"{message} — skipping", file=stream)
        return 0

    findings, scanned = scan_vault(root, yaml)
    return report(findings, scanned, root)


if __name__ == "__main__":
    sys.exit(main())
