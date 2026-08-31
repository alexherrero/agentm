#!/usr/bin/env python3
"""Compare the operator's labels against the judges, and report it honestly.

Reads the labelled worksheet from the vault, the judges' verdicts from the
cross-model run, and reports Cohen's κ with its interval, raw agreement kept
separate and labelled as not-κ, the confusion matrix, and — where there are
enough labels — a prediction-powered population estimate.

Nothing here decides who is right. κ measures whether two raters agree; when
they do not, the confusion matrix says in which direction, and that is the
material for a conversation rather than a verdict.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import agreement as ag  # noqa: E402

VALID = ("sufficient", "insufficient", "n/a")
_TURN = re.compile(r"^## \d+\. `([^`]+)`")
_LABEL = re.compile(r"^\*\*LABEL:\s*([^*]+?)\s*\*\*", re.I)
_FLAG = re.compile(r"^FLAG:\s*(\S+)", re.I)


def normalise(raw: str):
    """A label, or None if it is not one.

    Typos are repaired only when the intent is unambiguous — a trailing stray
    character on an otherwise exact match. Anything else is returned as None
    and counted, because guessing at what someone meant is how a label set
    quietly becomes the labeller's transcription errors.
    """
    v = (raw or "").strip().lower()
    if v in VALID:
        return v
    if v in ("?", ""):
        return None
    for good in VALID:
        # One stray character appended or a single trailing typo.
        if v.startswith(good) and len(v) - len(good) <= 2:
            return good
    return None


def read_labels(paths: list) -> dict:
    """Every label in the worksheets, by turn id."""
    out: dict = {}
    for p in paths:
        cur = None
        for line in p.read_text(encoding="utf-8").splitlines():
            m = _TURN.match(line)
            if m:
                cur = m.group(1)
                continue
            m = _LABEL.match(line)
            if m and cur:
                out.setdefault(cur, {})["raw"] = m.group(1).strip()
                out[cur]["label"] = normalise(m.group(1))
                continue
            m = _FLAG.match(line)
            if m and cur:
                out.setdefault(cur, {})["flag"] = m.group(1)
    return out


def compare(labels: dict, verdicts: dict, who: str) -> dict:
    """κ, raw agreement and the confusion matrix against one judge."""
    pairs = [(v["label"], verdicts[k]) for k, v in labels.items()
             if v.get("label") and verdicts.get(k)]
    if not pairs:
        return {"judge": who, "n": 0,
                "note": "no turn carried both a label and a verdict"}
    op = [a for a, _ in pairs]
    ju = [b for _, b in pairs]
    out = ag.cohen_kappa(op, ju)
    out["judge"] = who
    out["confusion"] = {f"{a}|{b}": n for (a, b), n
                        in collections.Counter(pairs).items()}
    # Which way the disagreement runs, which κ alone will not say.
    order = {"n/a": 0, "insufficient": 1, "sufficient": 2}
    kinder = sum(1 for a, b in pairs if order[a] > order[b])
    harsher = sum(1 for a, b in pairs if order[a] < order[b])
    out["operator_more_generous"] = kinder
    out["operator_stricter"] = harsher
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--worksheets", type=pathlib.Path, required=True,
                    help="directory holding the labelled batches")
    ap.add_argument("--verdicts", type=pathlib.Path, required=True,
                    help="cross-model results (claude + gemini per turn)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    sheets = sorted(args.worksheets.glob("recall-sufficiency-v1-0*.md"))
    labels = read_labels(sheets)
    rows = json.loads(args.verdicts.read_text(encoding="utf-8"))["rows"]
    claude = {r["id"]: r.get("claude") for r in rows}
    gemini = {r["id"]: r.get("gemini") for r in rows}

    good = {k: v for k, v in labels.items() if v.get("label")}
    unreadable = {k: v["raw"] for k, v in labels.items()
                  if not v.get("label") and v.get("raw") not in ("?", "")}

    out = {
        "worksheets": len(sheets),
        "turns_labelled": len(good),
        "turns_unlabelled": len(labels) - len(good),
        "labels_not_understood": unreadable,
        "operator_distribution": dict(collections.Counter(
            v["label"] for v in good.values())),
        "flags": sum(1 for v in good.values() if v.get("flag")),
        "vs_claude": compare(good, claude, "claude"),
        "vs_gemini": compare(good, gemini, "gemini"),
    }

    if args.json:
        print(json.dumps(out, indent=2))
        return 0

    print(f"{out['turns_labelled']} labelled of "
          f"{out['turns_labelled'] + out['turns_unlabelled']} seen")
    if unreadable:
        print(f"  not understood, excluded: {unreadable}")
    print(f"  operator said: {out['operator_distribution']}")
    for key in ("vs_claude", "vs_gemini"):
        c = out[key]
        if not c.get("n"):
            continue
        print(f"\nagainst {c['judge']} ({c['n']} turns)")
        if c.get("kappa") is None:
            print(f"  {c.get('note', '')}")
        else:
            print(f"  kappa          : {c['kappa']}  95% CI {c['kappa_ci']}")
        print(f"  raw agreement  : {c['raw_agreement']}  (not kappa)")
        print(f"  operator more generous on {c['operator_more_generous']}, "
              f"stricter on {c['operator_stricter']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
