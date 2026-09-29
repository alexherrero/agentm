#!/usr/bin/env python3
"""plan_c_manifests — the one-time cleanups of the AgentKV rulings' Plan C
(task 178), planned as manifests the dreaming binary makes through its journal.

Each pass reads the live vault, decides every rewrite, and writes a manifest:
one act per note, naming the note, the sha256 of the bytes it read and the
bytes to write in their place. Nothing here writes a note. The manifest is
checked and made by `agentmdream apply -manifest FILE [-apply]`, which journals
each act like the night's own and refuses the whole manifest when any of its
notes changed since the plan. Every supersede takes the contract's one shape
for the relation — `lifecycle: superseded` and `superseded_by` naming the
survivor, `status` untouched — the shape the copies job writes.

    traces   a session with two or more active traces keeps one: the trace the
             writer would find (titled from a request over a fallback title,
             then the older day, then the name) takes the others' links and
             candidates, and the newest closing recap; the others are
             superseded by it.

    chunks   an article is one note: an active `<doc>-chunk-N` note whose
             `<doc>.md` is active beside it is superseded by the document, and
             so is an older full-length note carrying the document's
             `source_url` (an earlier ingest of the same page). The facts
             drawn from the article are short and stay.

    python3 scripts/migrate/plan_c_manifests.py {traces,chunks} [--memory-root DIR] [--out FILE]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
_SKILL = _REPO / "harness" / "skills" / "memory" / "scripts"
if str(_SKILL) not in sys.path:
    sys.path.insert(0, str(_SKILL))

import card_shape  # noqa: E402
import episodic_trace as et  # noqa: E402


def _text(raw: bytes) -> str:
    """A note's text with its line endings as the writers make them. A file a
    Windows editor saved carries CRLF, and every frontmatter reader here reads
    `---\n`; the hash in the manifest is still of the bytes as they are."""
    return raw.decode("utf-8").replace("\r\n", "\n")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def supersede(text: str, survivor: str) -> str:
    """The note with `lifecycle: superseded` and `superseded_by: <survivor>`,
    in the card's order, its body and every other field untouched."""
    parsed = card_shape.split_note(text)
    if parsed is None:
        raise ValueError("no frontmatter block")
    entries, rest = parsed
    entries = card_shape.set_value(entries, "lifecycle", "superseded")
    entries = card_shape.set_value(entries, "superseded_by", survivor)
    return card_shape.reorder(card_shape.join_note(entries, rest))


def lifecycle_of(text: str) -> str:
    parsed = card_shape.split_note(text)
    value = card_shape.scalar(card_shape.raw_value(parsed[0], "lifecycle")) if parsed else None
    return (value or "active").strip()


def act(rel: str, before: bytes, after: str, summary: str, *, frm: str = "", to: str = "") -> dict:
    out = {"rel": rel, "before_sha256": sha(before), "after": after, "summary": summary}
    if frm or to:
        out["from"], out["to"] = frm, to
    return out


# ── traces ────────────────────────────────────────────────────────────────────

def plan_traces(memory_root: Path) -> dict:
    """The manifest folding every session's extra traces into its first."""
    folder = memory_root / et.EPISODIC_DIR
    groups = defaultdict(list)
    for p in sorted(folder.glob("*.md")):
        raw = p.read_bytes()
        fm, _ = et._split_trace(_text(raw))
        sid = et._fm_value(fm, "session")
        if not sid:
            continue
        if (et._fm_value(fm, "lifecycle") or "").lower() == "superseded" or et._fm_value(fm, "superseded_by"):
            continue
        titled = et._fm_value(fm, "title") != et.fallback_title(sid)
        groups[sid].append(((not titled, et._fm_value(fm, "created") or "", p.name), p, raw))
    acts, sessions = [], []
    for sid, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        members.sort(key=lambda m: m[0])
        _, survivor, s_raw = members[0]
        s_rel = survivor.relative_to(memory_root).as_posix()
        merged = _text(s_raw)
        newest = survivor.stat().st_mtime
        others = sorted(members[1:], key=lambda m: m[1].stat().st_mtime)
        for _, other, o_raw in others:
            mtime = other.stat().st_mtime
            merged = et.merge_trace_text(merged, _text(o_raw), sid, newest_outcome=mtime >= newest)
            newest = max(newest, mtime)
        acts.append(act(s_rel, s_raw, merged,
                        f"the session's one trace: {len(others)} other trace(s) of {sid} folded in"))
        folded = []
        for _, other, o_raw in others:
            o_rel = other.relative_to(memory_root).as_posix()
            text = _text(o_raw)
            acts.append(act(o_rel, o_raw, supersede(text, s_rel),
                            f"a second trace of session {sid}, folded into {s_rel}",
                            frm=lifecycle_of(text), to="superseded"))
            folded.append(o_rel)
        sessions.append({"session": sid, "survivor": s_rel, "folded": folded})
    return {"job": "manifest-plan-c-traces",
            "reason": "one trace per session (agentm-vault § Capture, amended 2026-09-28; task 178 step 3)",
            "sessions": sessions, "acts": acts}


# ── chunks ────────────────────────────────────────────────────────────────────

_CHUNK = re.compile(r"^(?P<doc>.+)-chunk-\d+$")
_CLASSES = ("semantic", "procedural")
#: A note this long carrying the document's page address is an earlier ingest
#: of the same article, not one of the facts drawn from it.
_EARLIER_INGEST_WORDS = 1000


def _fields(text: str) -> dict:
    parsed = card_shape.split_note(text)
    if parsed is None:
        return {}
    entries, rest = parsed
    out = {k: (card_shape.scalar(card_shape.raw_value(entries, k)) or "") for k, _ in entries if k}
    out["_body"] = rest.split("\n", 1)[1] if "\n" in rest else ""
    return out


def _active(f: dict) -> bool:
    return (f.get("lifecycle", "").lower() not in ("superseded", "archived") and not f.get("superseded_by")
            and f.get("status", "").lower() != "superseded")


def plan_chunks(memory_root: Path) -> dict:
    """The manifest folding every article's chunk notes into its document: an
    active `<doc>-chunk-N` note whose `<doc>.md` is an active note beside it is
    superseded by the document, and so is an older full-length note carrying
    the document's `source_url` — an earlier ingest of the same page. The facts
    drawn from the article are short and stay."""
    acts, families = [], []
    for cls in _CLASSES:
        folder = memory_root / "memory" / cls
        notes = {p: _text(p.read_bytes()) for p in sorted(folder.glob("*.md"))}
        fields = {p: _fields(t) for p, t in notes.items()}
        docs: dict = {}
        for p in notes:
            m = _CHUNK.match(p.stem)
            doc = folder / f"{m.group('doc')}.md" if m else None
            if doc is not None and doc in notes and _active(fields[doc]) and _active(fields[p]):
                docs.setdefault(doc, []).append(p)
        for doc, chunks in sorted(docs.items()):
            d = fields[doc]
            doc_rel = doc.relative_to(memory_root).as_posix()
            earlier = [p for p, f in fields.items()
                       if p != doc and p not in chunks and _active(f) and f.get("source_url")
                       and f.get("source_url") == d.get("source_url")
                       and len(f["_body"].split()) >= _EARLIER_INGEST_WORDS
                       and f.get("created", "")[:10] < d.get("created", "")[:10]]
            folded = []
            for p in sorted(chunks, key=lambda q: int(q.stem.rsplit("-", 1)[1])) + sorted(earlier):
                rel = p.relative_to(memory_root).as_posix()
                why = ("a chunk note of " if p in chunks else "an earlier ingest of the page ") + doc_rel
                acts.append(act(rel, p.read_bytes(), supersede(notes[p], doc_rel), why,
                                frm=lifecycle_of(notes[p]), to="superseded"))
                folded.append(rel)
            families.append({"document": doc_rel, "folded": folded})
    return {"job": "manifest-plan-c-chunks",
            "reason": "one article is one note (agentm-vault § Capture, amended 2026-09-28; task 178 step 5)",
            "families": families, "acts": acts}


# ── the command ───────────────────────────────────────────────────────────────

PASSES = {"traces": plan_traces, "chunks": plan_chunks}


def _memory_root(arg: "str | None") -> Path:
    if arg:
        return Path(arg)
    import harness_memory  # noqa: E402 — resolved at run time, never cached
    root = harness_memory.memory_root()
    if root is None:
        raise SystemExit("plan_c_manifests: no memory root resolves; pass --memory-root")
    return Path(root)


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description="plan a Plan C one-time cleanup as a manifest")
    ap.add_argument("pass_name", choices=sorted(PASSES), metavar="pass", help=" | ".join(sorted(PASSES)))
    ap.add_argument("--memory-root", help="the memory root (default: the configured one)")
    ap.add_argument("--out", help="the manifest file (default: <memory root>/diagnostics/migrations/"
                                  "plan-c/<today>-<pass>.json)")
    a = ap.parse_args(argv)
    root = _memory_root(a.memory_root)
    manifest = PASSES[a.pass_name](root)
    out = Path(a.out) if a.out else (root / "diagnostics" / "migrations" / "plan-c"
                                     / f"{date.today():%Y-%m-%d}-{a.pass_name}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{manifest['job']}: {len(manifest['acts'])} act(s) -> {out}")
    for s in manifest.get("sessions", []):
        print(f"  {s['session']}: keep {s['survivor']}; fold {', '.join(s['folded'])}")
    for f in manifest.get("families", []):
        print(f"  {f['document']}: fold {len(f['folded'])} note(s)")
    print(f"check it: agentmdream apply -manifest {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
