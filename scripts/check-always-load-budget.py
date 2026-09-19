#!/usr/bin/env python3
"""Gate: the always-load tier stays under 40,000 tokens (plan 12 task 2).

Every session starts by reading this tier whole. That is the one cost the
operator pays on every prompt of every session without being asked, so it gets
a ceiling — 40,000 tokens, the operator's number.

**The gate is against creep, not against the operator.** `user-preferences.md`
is a file the operator writes, and a budget that makes them count words there
is the wrong pressure. So the ceiling sits high, the gate reads the *packaged*
set — what this repo ships and what an install puts on a fresh machine — and
the live tier is reported beside it rather than enforced. A tier that grows
because the operator wrote more is theirs; a tier that grows because the build
added a file is this gate's business.

**Measured as injected, not as stored.** The tier's largest file is the filing
contract, and three fifths of it is one fenced `storage-rules` block that the
Go daemon parses out of the file at runtime and that no session ever reads.
`recall.py` strips it from the injection, so counting it here would gate on
bytes nobody is charged for — and would hide real creep behind a number that
was already mostly machine block. The same stripper the loader uses is the one
this gate measures through, so the two can never disagree.

Usage:
  python3 scripts/check-always-load-budget.py            # packaged, + live where it resolves
  python3 scripts/check-always-load-budget.py --memory-root DIR
  python3 scripts/check-always-load-budget.py --self-test
Exit: 0 under the ceiling; 1 over it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_TOOLKIT = _REPO / "harness" / "skills" / "memory" / "scripts"
for _p in (str(_HERE), str(_TOOLKIT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import recall  # noqa: E402 — the loader's own stripper and token estimate
import vault_layout  # noqa: E402

# The operator's number (agentm-vault § the always-load tier), in tokens.
CEILING_TOKENS = 40_000

# What an install puts in `standards/`: the templates this repo ships plus the
# packaged filing contract, which installs as `standards/storage-rules.md`.
PACKAGED = (
    _REPO / "daemon" / "internal" / "rules" / "storage-rules.default.md",
    *sorted((_REPO / "templates" / "standards").glob("*.md")),
)


def tier_tokens(files) -> tuple:
    """`(total, [(name, tokens), …])` as the loader would inject them."""
    rows = []
    for f in files:
        f = Path(f)
        if not f.is_file():
            continue
        _fm, body = recall._parse_frontmatter(f.read_text(encoding="utf-8"))
        rows.append((f.name, recall._estimate_tokens(recall._strip_machine_blocks(body))))
    return sum(t for _n, t in rows), rows


def live_files(memory_root: Path) -> list:
    out = []
    for d in vault_layout.always_load_dirs(memory_root):
        out.extend(sorted(p for p in Path(d).glob("*.md") if p.is_file()))
    return out


def report(memory_root: Path | None, out=sys.stdout) -> int:
    total, rows = tier_tokens(PACKAGED)
    print(f"check-always-load-budget: packaged tier {total:,} tokens "
          f"of {CEILING_TOKENS:,} ({total / CEILING_TOKENS:.0%})", file=out)
    for name, tokens in sorted(rows, key=lambda r: -r[1]):
        print(f"    {tokens:>7,}  {name}", file=out)

    # The live tier is reported, never enforced: it holds the operator's own
    # files, and this gate is not a word limit on those.
    if memory_root is not None:
        files = live_files(memory_root)
        if files:
            live_total, _live_rows = tier_tokens(files)
            over = " — OVER THE CEILING" if live_total > CEILING_TOKENS else ""
            print(f"  live tier: {live_total:,} tokens across {len(files)} file(s)"
                  f"{over} (reported, not enforced — these are yours)", file=out)

    if total > CEILING_TOKENS:
        print(f"  FAIL: the packaged set is {total - CEILING_TOKENS:,} tokens over "
              "the ceiling. Every session pays this before it reads a word of "
              "the operator's own files.", file=out)
        return 1
    return 0


def self_test(out=sys.stdout) -> int:
    """A ceiling nobody has seen refuse anything is a number in a comment."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        big = Path(d) / "enormous.md"
        big.write_text("x " * (CEILING_TOKENS * 3), encoding="utf-8")
        total, _rows = tier_tokens([big])
        if total <= CEILING_TOKENS:
            print("check-always-load-budget: SELF-TEST FAILED — a file three "
                  f"times the ceiling measured {total:,} tokens", file=out)
            return 1
        # And the strip is load-bearing in the measurement, not decoration.
        blocked = Path(d) / "contract.md"
        filler = "machine line\n" * 5_000
        blocked.write_text(f"prose\n\n```storage-rules\n{filler}```\n", encoding="utf-8")
        stripped, _ = tier_tokens([blocked])
        raw = recall._estimate_tokens(blocked.read_text(encoding="utf-8"))
        if stripped >= raw:
            print("check-always-load-budget: SELF-TEST FAILED — the fenced "
                  "block was counted; the gate is measuring stored bytes, not "
                  "injected ones", file=out)
            return 1
    print("check-always-load-budget: self-test OK — an oversized set fails and "
          "a machine block is not counted", file=out)
    return 0


def _resolve(arg: str | None) -> Path | None:
    if arg:
        return Path(arg)
    root = vault_layout.env_memory_root()
    if root is None:
        try:
            import harness_memory as hm  # noqa: E402
            root = hm.memory_root()
        except ImportError:
            root = None
    return Path(root) if root else None


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--memory-root", default=None)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    rc = self_test()
    if args.self_test or rc:
        return rc
    root = _resolve(args.memory_root)
    if root is not None and not root.is_dir():
        root = None
    return report(root)


if __name__ == "__main__":
    raise SystemExit(main())
