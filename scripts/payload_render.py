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
  - resolves the two alternates marked `payload:mail` / `payload:no-mail`,
    keeping the mail sentence only when a capture address is configured, and
    substituting it for `{{CAPTURE_ADDRESS}}`;
  - normalizes blank-line runs so either branch leaves the same shape.

The address lives at `plugins.autonomy.capture_address` in the engine config,
beside the SMTP target the autonomy plugin already keeps there. It is unset
until the mail door lands, and an unset address means the payload does not tell
a chat surface to mail a mailbox that does not exist.
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

CAPTURE_ADDRESS_KEY = "plugins.autonomy.capture_address"
PLACEHOLDER = "{{CAPTURE_ADDRESS}}"
MARKER = "AGENTMEMORY"  # the managed-section marker in GEMINI.md

ANTIGRAVITY_FRONTMATTER = "---\ntrigger: always_on\n---\n\n"

# The operator-only header, stripped before the body reaches a surface. The
# negative lookahead matters: the alternates below are also leading HTML
# comments, and a template that ever loses its header must not have its first
# `payload:` marker eaten as though it were one.
_LEADING_COMMENT = re.compile(r"\A<!--(?!\s*/?payload:).*?-->\s*", re.DOTALL)
_BLOCK_OPEN = re.compile(r"\A\s*<!--\s*payload:(mail|no-mail)\s*-->\s*\Z")
_BLOCK_CLOSE = re.compile(r"\A\s*<!--\s*/payload:(mail|no-mail)\s*-->\s*\Z")


def read_template(path: Optional[Path] = None) -> str:
    return (path or TEMPLATE_PATH).read_text(encoding="utf-8")


def capture_address(install_prefix: Optional[Path] = None) -> Optional[str]:
    """The configured capture mailbox, or None. Never raises on a missing config."""
    try:
        sys.path.insert(0, str(HERE))
        import agentm_config  # noqa: PLC0415 — local import keeps this module import-light

        prefix = agentm_config._resolve_install_prefix(
            str(install_prefix) if install_prefix else None
        )
        config = agentm_config._read_config(prefix) or {}
    except Exception:  # a missing / unreadable config is "no address", not a crash
        return None
    value = config.get(CAPTURE_ADDRESS_KEY)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def render(template_text: str, address: Optional[str] = None) -> str:
    """The pasteable body: header stripped, one alternate kept, address filled in."""
    body = _LEADING_COMMENT.sub("", template_text, count=1)
    wanted = "mail" if address else "no-mail"

    kept: list = []
    skipping_until: Optional[str] = None
    for line in body.splitlines():
        close = _BLOCK_CLOSE.match(line)
        if close:
            if skipping_until == close.group(1):
                skipping_until = None
            continue
        if skipping_until is not None:
            continue
        opened = _BLOCK_OPEN.match(line)
        if opened:
            if opened.group(1) != wanted:
                skipping_until = opened.group(1)
            continue
        kept.append(line)

    out = "\n".join(kept)
    if address:
        out = out.replace(PLACEHOLDER, address)
    if PLACEHOLDER in out:
        raise ValueError(
            f"{PLACEHOLDER} survived rendering — the mail alternate is active with no address"
        )
    out = re.sub(r"\n{3,}", "\n\n", out).strip("\n")
    return out + "\n"


def antigravity_rule(body: str) -> str:
    """The tracked Antigravity rule: always_on frontmatter over the rendered body.

    Rendered with NO address, deliberately: this file is tracked in git, and the
    operator's capture mailbox is not something to commit. The machine-local
    Gemini copy is the one that carries the address.
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
    wanted = antigravity_rule(render(read_template(), None))
    if path.is_file() and path.read_text(encoding="utf-8") == wanted:
        return "kept"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(wanted, encoding="utf-8")
    return "written"


def write_gemini_section(
    path: Optional[Path] = None, *, address: Optional[str] = None
) -> str:
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
    body = render(read_template(), address if address is not None else capture_address())
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
        print(render(read_template(), capture_address()), end="")
        return 0
    if not args.gemini_only:
        print(f"    antigravity rule      {write_antigravity_rule()}")
    print(f"    gemini managed section {write_gemini_section()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
