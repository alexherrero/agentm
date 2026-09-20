#!/usr/bin/env python3
"""payload_render.py — the one renderer behind every copy of the context payload.

`templates/agentmemory-context.md` is the source. Three things are derived from
it and must be byte-equal to what this module produces, or they are drift:

  1. the body of `adapters/antigravity/rules/agentmemory-context.md`
  2. the managed section of `~/.gemini/GEMINI.md`
  3. what `/memory payload` prints for the operator to paste

Having one function produce all three is what makes a hash comparison mean
anything; the alternative — three hand-kept variants — is the state this
replaced, in which the Antigravity rule and the template had drifted into
different documents nobody could tell apart at a glance.

What rendering does:

  - drops the leading operator-only HTML comment (paste instructions, not
    instructions to the agent);
  - normalizes blank-line runs.

There is one body and no alternates. The `payload:mail` / `payload:no-mail`
pair and the `{{CAPTURE_ADDRESS}}` the first substituted retired with the email
door's transport (agentm-vault plan 16): a chat surface writes by dropping a
card into `agent/inbox/` over Drive, which is true on every machine and depends
on no config key, so there is nothing left for the renderer to choose between.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
TEMPLATE_PATH = REPO / "templates" / "agentmemory-context.md"
ANTIGRAVITY_RULE_PATH = REPO / "adapters" / "antigravity" / "rules" / "agentmemory-context.md"
GEMINI_RULES_PATH = Path.home() / ".gemini" / "GEMINI.md"

MARKER = "AGENTMEMORY"  # the managed-section marker in GEMINI.md

ANTIGRAVITY_FRONTMATTER = "---\ntrigger: always_on\n---\n\n"

# The operator-only header, stripped before the body reaches a surface. The
# negative lookahead is kept though the alternates are gone: a `payload:` marker
# is still the one leading HTML comment that must never be eaten as a header, and
# removing the guard would make restoring one a silent corruption rather than a
# visible one.
_LEADING_COMMENT = re.compile(r"\A<!--(?!\s*/?payload:).*?-->\s*", re.DOTALL)


def read_template(path: Optional[Path] = None) -> str:
    return (path or TEMPLATE_PATH).read_text(encoding="utf-8")


def render(template_text: str) -> str:
    """The pasteable body: the operator-only header stripped, nothing else."""
    out = _LEADING_COMMENT.sub("", template_text, count=1)
    out = re.sub(r"\n{3,}", "\n\n", out).strip("\n")
    return out + "\n"


def antigravity_rule(body: str) -> str:
    """The tracked Antigravity rule: always_on frontmatter over the rendered body.

    One body, the same on every surface and in every copy: the write path is a
    folder, not a configured address, so there is nothing machine-local left in
    it to keep out of git.
    """
    return ANTIGRAVITY_FRONTMATTER + body


def strip_frontmatter(text: str) -> str:
    return re.sub(r"\A---\n.*?\n---\n\s*", "", text, count=1, flags=re.DOTALL)


def managed_section(text: str) -> Optional[str]:
    """The body inside GEMINI.md's AGENTMEMORY markers, or None if there is none."""
    m = re.search(
        re.escape(f"<!-- {MARKER}:BEGIN") + r".*?-->\n(.*?)\n?" + re.escape(f"<!-- {MARKER}:END -->"),
        text,
        re.DOTALL,
    )
    if not m:
        return None
    return m.group(1).strip("\n") + "\n"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short(text: str) -> str:
    return digest(text)[:12]


def write_antigravity_rule(path: Optional[Path] = None) -> str:
    """Regenerate the tracked Antigravity rule. Returns 'written' or 'kept'."""
    path = path or ANTIGRAVITY_RULE_PATH
    wanted = antigravity_rule(render(read_template()))
    if path.is_file() and path.read_text(encoding="utf-8") == wanted:
        return "kept"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(wanted, encoding="utf-8")
    return "written"


def write_gemini_section(path: Optional[Path] = None) -> str:
    """Merge the rendered body into GEMINI.md's managed section.

    Delegates the marker-bounded merge to merge-managed-section.py, which is the
    one place that knows how to preserve everything outside the markers.
    Returns the merge helper's own action word, or 'absent' when there is no
    ~/.gemini to write into — we do not create config directories for a tool the
    operator does not run.
    """
    import subprocess
    import tempfile

    path = path or GEMINI_RULES_PATH
    if not path.parent.is_dir():
        return "absent"
    body = render(read_template())
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(body)
        tmp = fh.name
    try:
        proc = subprocess.run(
            [sys.executable, str(HERE / "merge-managed-section.py"), str(path), tmp,
             "--marker", MARKER],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "merge-managed-section.py failed")
        # The helper's line is "    <action> <path> (<marker> ...)" — the first
        # word is the action word (created / appended / replaced / kept).
        first = proc.stdout.strip().split()
        return first[0] if first else "written"
    finally:
        Path(tmp).unlink(missing_ok=True)


def _build_parser():
    import argparse

    p = argparse.ArgumentParser(
        prog="payload_render",
        description="Render the context payload; optionally rewrite the derived copies.",
    )
    p.add_argument("--write", action="store_true",
                   help="rewrite both derived copies instead of printing the body")
    p.add_argument("--gemini-only", action="store_true",
                   help="with --write: rewrite only ~/.gemini/GEMINI.md (the installer's path — "
                        "an install never dirties the repo's tracked rule)")
    return p


def main(argv: Optional[list] = None) -> int:
    args = _build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    if not args.write:
        print(render(read_template()), end="")
        return 0
    if not args.gemini_only:
        print(f"    antigravity rule      {write_antigravity_rule()}")
    print(f"    gemini managed section {write_gemini_section()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
