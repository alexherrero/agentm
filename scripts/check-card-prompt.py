#!/usr/bin/env python3
"""Gate: the inbox card prompt teaches the shape the card actually has
(agentm-vault plan 16).

`templates/inbox-card-prompt.md` is pasted into a chat surface whose Drive
connector can create a file — claude.ai, tested on 2026-09-20; Gemini cannot
and is not a target — and from then on it is the only thing that surface knows
about a card. Nobody re-reads it. So the one failure that matters is the
silent one: the card's shape moves in `card_shape.py` and the paste keeps
teaching the old field for months, because a paste has no CI.

What this checks, and why each one:

  - **Every field the prompt teaches is a real card field.** A prompt naming a
    field no reader reads produces cards carrying dead frontmatter, and the
    review pass shows it to the operator as though it meant something.
  - **No retired field is taught.** `card_shape.RETIRED_FIELDS` is the design's
    own list of what a card stopped carrying. Teaching one is worse than
    teaching nothing: it looks current.
  - **The worked example parses, and carries what the prose promises.** The
    example is what a model actually imitates. If the prose says `why` and the
    example omits it, the cards will omit it.
  - **The two fixed values are exactly `unfiled` and `untrusted`.** That pair is
    the whole trust posture of the folder: a card sits unread until a person
    reads it. A prompt that taught `status: active` would let a chat surface
    file into the vault by writing a word, which is the thing part 16 exists to
    prevent.
  - **`agent/inbox/` is the only folder the prompt names as a write target.** A
    second one would be a second write path nobody decided on.

Usage:
  python3 scripts/check-card-prompt.py [<prompt path>]
  python3 scripts/check-card-prompt.py --self-test   # prove it can fail
Exit: 0 clean; 1 on a finding.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TOOLKIT = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for _p in (str(_HERE), str(_TOOLKIT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import card_shape  # noqa: E402

DEFAULT_PROMPT = _HERE.parent / "templates" / "inbox-card-prompt.md"

#: The fields the prompt must teach. Fewer than a class card carries, on
#: purpose: a phone-typed thought has no `lifecycle`, no `filing_confidence`
#: and no `created` it could honestly supply, and the night fills what it can.
MUST_TEACH = ("title", "type", "summary", "why", "importance", "status",
              "trust", "tags")

#: The two the surface does not get to choose, and their only allowed values.
FIXED = {"status": "unfiled", "trust": "untrusted"}

#: The one folder a chat surface may write to.
WRITE_TARGET = "agent/inbox/"

_FENCE = re.compile(r"```\n(---\n.*?\n---\n)", re.S)
_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):", re.M)


def example_block(text: str) -> "str | None":
    m = _FENCE.search(text)
    return m.group(1) if m else None


def findings(text: str) -> list:
    out = []
    block = example_block(text)
    if block is None:
        return ["the prompt carries no fenced frontmatter example — the example "
                "is the thing a model imitates, so its absence is the finding"]
    taught = _KEY.findall(block)

    known = set(card_shape.READ_ORDER) | set(card_shape.MACHINE_ORDER)
    for key in taught:
        if key in card_shape.RETIRED_FIELDS:
            out.append(f"the example teaches `{key}`, which the design retired "
                       f"(card_shape.RETIRED_FIELDS)")
        elif key not in known:
            out.append(f"the example teaches `{key}`, which is not a card field "
                       f"(card_shape.READ_ORDER / MACHINE_ORDER)")

    for key in MUST_TEACH:
        if key not in taught:
            out.append(f"the example omits `{key}` — the prose promises it and "
                       f"the cards will not carry it")
        # The prose spells a field either bare (`` `why` ``) or with the value
        # it fixes (`` `status: unfiled` ``), so match the key inside backticks
        # up to a word boundary rather than an exact token.
        if not re.search(rf"`{re.escape(key)}(?![A-Za-z0-9_])", text):
            out.append(f"the prose never explains `{key}`")

    order = {k: i for i, k in enumerate(card_shape.READ_ORDER)}
    placed = [k for k in taught if k in order]
    if placed != sorted(placed, key=lambda k: order[k]):
        out.append("the example's fields are not in the card's own order "
                   "(card_shape.READ_ORDER), so every card it produces needs "
                   "reordering before it reads like the rest of the vault")

    for key, value in FIXED.items():
        m = re.search(rf"^{key}:\s*(\S+)\s*$", block, re.M)
        if m is None:
            continue  # the omission is already reported above
        if m.group(1) != value:
            out.append(f"the example writes `{key}: {m.group(1)}` where the "
                       f"folder's posture is `{key}: {value}` — a chat surface "
                       f"must not be able to file by writing a word")

    if WRITE_TARGET not in text:
        out.append(f"the prompt never names `{WRITE_TARGET}` as the folder to "
                   f"write to")
    for stray in ("agent/memory/", "memory/semantic/", "personal/", "standards/"):
        if stray in text:
            out.append(f"the prompt names `{stray}` as somewhere to write — "
                       f"`{WRITE_TARGET}` is the only write target")
    return out


def check(path: Path, out=sys.stdout) -> int:
    if not path.is_file():
        print(f"check-card-prompt: FAIL — no prompt at {path}", file=out)
        return 1
    found = findings(path.read_text(encoding="utf-8"))
    if not found:
        print("check-card-prompt: clean — the paste teaches the card's own "
              "shape", file=out)
        return 0
    print(f"check-card-prompt: {len(found)} finding(s) in {path.name}", file=out)
    for f in found:
        print(f"  {f}", file=out)
    return 1


def self_test(out=sys.stdout) -> int:
    """A gate nobody has seen fail is a gate nobody knows works."""
    real = DEFAULT_PROMPT.read_text(encoding="utf-8") if DEFAULT_PROMPT.is_file() else ""
    if not real:
        print("check-card-prompt: SELF-TEST SKIPPED — no shipped prompt", file=out)
        return 0
    if findings(real):
        print("check-card-prompt: SELF-TEST FAILED — the shipped prompt reports "
              "findings", file=out)
        return 1
    cases = (
        (real.replace("status: unfiled", "status: active"),
         "a prompt teaching status: active"),
        (real.replace("why: why it is worth keeping\n", ""),
         "a prompt that dropped `why` from the example"),
        (real.replace("importance: 5", "altitude: artifact"),
         "a prompt teaching a retired field"),
        (real.replace("`Vault/agent/inbox/`", "`Vault/agent/memory/semantic/`"),
         "a prompt pointing at a class directory"),
    )
    for text, what in cases:
        if not findings(text):
            print(f"check-card-prompt: SELF-TEST FAILED — {what} passed", file=out)
            return 1
    print("check-card-prompt: self-test OK — the shipped prompt passes and four "
          "drifted ones each fail", file=out)
    return 0


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", nargs="?", default=None)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    rc = self_test()
    if args.self_test or rc:
        return rc
    return check(Path(args.path) if args.path else DEFAULT_PROMPT)


if __name__ == "__main__":
    raise SystemExit(main())
