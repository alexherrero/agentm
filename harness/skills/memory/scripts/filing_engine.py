#!/usr/bin/env python3
"""filing_engine.py — the filing decision at write time (filing-v2, the write path).

A candidate memory arrives from the Stop-hook reflection pass or an explicit
`memory_capture`, and this module decides — before anything is written — what
it is and what to do with it:

  * **type and class**: the memory type through the contract's deprecations
    map, then the class directory the contract routes that type to; a
    candidate with no type takes the contract's default type at low
    confidence;
  * **destination**: `memory/<class>/<slug>.md`, settled against what is on
    disk (a namesake with a different body takes `<slug>~dup.md`);
  * **the update relationship** to the existing corpus — one of
    `add` (novel), `update` (a structural key match with the same value but a
    different body: filed beside the existing note and flagged, never merged
    in place), `supersede` (a key match with a different value — the existing
    note gains `superseded_by` and flips `lifecycle: superseded`, nothing is
    deleted), or `noop` (an exact body twin: the existing note is reinforced).

Deterministic signals lead. `extract_key` yields a (subject, attribute,
object) triple only where the text carries a shape it can read without
judgment — a tool-invocation stub, an always/never directive, a plain "X is
Y" fact — and the key match is what decides duplicate against contradiction.
Embedding similarity (the daemon's search, injected so tests never need a
daemon) is a secondary signal only: a strong title overlap with a note whose
key did not match flags a probable near-duplicate, which is filed and
flagged, never auto-merged. The AUROC-0.59 finding behind that ordering:
similarity cannot tell "same fact" from "opposite fact", because the
contradiction is usually the smaller edit.

Every filed note carries `filing_confidence` and `source`. Low confidence is
the soft inbox — the note lands at its real destination and the needs-review
MOC surfaces it.

Usage (the hook and the MCP tool call `decide`/`apply`; the CLI is for the
operator and for measurement):
  filing_engine.py decide --vault <memory-root> --title T --body-file F [--type T] [--json]
  filing_engine.py measure --vault <memory-root>      # key-extractor coverage over the classes
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import storage_rules  # noqa: E402
from fingerprint import compute_fingerprint  # noqa: E402

CLASS_DIRS = ("semantic", "procedural", "episodic", "entities", "crystallized", "mocs")
CONFIDENCE = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low", "high": "high", "medium": "medium", "low": "low"}
_DUP_SUFFIX = re.compile(r"~dup\d*$")
_WORD = re.compile(r"[a-z0-9][a-z0-9'-]*")
_STOP = frozenset("a an the and or of to in on for with is are was were be been by at from as it its this that "
                  "these those i you we they he she my your our their me us them do does did not no yes if then "
                  "than so but".split())


@dataclass
class Note:
    """One note the corpus index knows about."""
    rel: str
    title: str
    slug: str
    type: str
    status: str
    lifecycle: str
    fingerprint: str
    key: "tuple | None"


@dataclass
class FilingDecision:
    type: str
    class_dir: str                      # e.g. "memory/semantic"
    dest_rel: str                       # e.g. "memory/semantic/<slug>.md"
    op: str                             # add | update | supersede | noop
    related: "str | None" = None        # the existing note the op refers to
    filing_confidence: str = "medium"
    source: str = "conversation"
    key: "tuple | None" = None
    flags: list = field(default_factory=list)
    reasons: list = field(default_factory=list)

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["key"] = list(self.key) if self.key else None
        return d


# ── the structural key ───────────────────────────────────────────────────────

_TOOL_STUB = re.compile(r"The `([A-Za-z_][\w-]*)` tool was invoked (\d+) times", re.I)
_DIRECTIVE = re.compile(r"\b(always|never|don't|do not|must not|must|should not|should)\b\s+(.{4,80})", re.I)
_FACT = re.compile(r"^(?:the |my |our )?([a-z][\w' -]{2,60}?)\s+(is|are|lives at|sits at|defaults? to|equals?)\s+(.{2,120})$", re.I)
# A fact yields a key only when its object is a *value* — a path, a URL, a
# number, or one identifier — because two different values for one subject
# really do contradict. A prose object ("… is synced by Drive") is a second
# fact about the subject, not a rival value: that is the false-contradiction
# trap, and it co-stores.
_VALUE_LIKE = re.compile(r"^(?:[/~][\w./~-]+|[a-z]+://\S+|\d[\d.,:]*[a-z%]*|[\w.~-]{1,40})[.!]?$", re.I)
_NEGATIVE = frozenset({"never", "don't", "do not", "must not", "should not"})
_CLAUSE_END = re.compile(r"\s+[—–-]\s+|[,;:.!?]|\s+\(|\s+(?:because|since|unless|so that|when|if)\b", re.I)


def _tokens(text: str) -> list:
    return [w for w in _WORD.findall(text.casefold()) if w not in _STOP]


def extract_key(title: str, body: str) -> "tuple | None":
    """(subject, attribute, object) where the text carries a shape this can
    read deterministically; None otherwise. Subjects and attributes are
    normalized so two phrasings of one fact meet; the object is the value the
    contradiction test compares."""
    text = f"{title}\n{body}"
    m = _TOOL_STUB.search(text)
    if m:
        return (f"tool:{m.group(1).casefold()}", "invocations", m.group(2))
    first = next((l.strip() for l in body.splitlines() if l.strip()), "") or title.strip()
    # A slug standing in for a title has no spaces and no clause to read.
    probes = tuple(p for p in ((title.strip() if " " in title.strip() else ""), first) if p)
    for probe in probes:
        m = _DIRECTIVE.search(probe)
        if m:
            polarity = "never" if m.group(1).casefold() in _NEGATIVE else "always"
            # The directive's object ends at the first clause boundary, so a
            # trailing explanation ("— rewrite only your own branches") does
            # not change which rule this is.
            phrase = _CLAUSE_END.split(m.group(2), 1)[0]
            subject = " ".join(_tokens(phrase)[:5])
            if subject:
                return (f"directive:{subject}", "polarity", polarity)
    for probe in probes:
        m = _FACT.match(probe)
        if m:
            subject = " ".join(_tokens(m.group(1))[:5])
            obj_raw = m.group(3).strip().rstrip(".!")
            if subject and _VALUE_LIKE.match(obj_raw):
                return (f"fact:{subject}", "value", obj_raw.casefold())
    return None


# ── the corpus index ─────────────────────────────────────────────────────────

def _frontmatter(text: str) -> "tuple[dict, str]":
    """(fields, body) from a note's text; line-level, never a YAML round-trip."""
    if not text.startswith("---\n"):
        return {}, text
    lines = text.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, text
    fields = {}
    for raw in lines[1:end]:
        if not raw or raw[0] in " \t#-":
            continue
        key, sep, value = raw.partition(":")
        if sep:
            fields.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return fields, "\n".join(lines[end + 1:])


class CorpusIndex:
    """The class directories' flat notes, read once per process: fingerprints
    for the twin test, keys for the structural test. Lanes (subdirectories)
    and generated indexes are not memories and are skipped."""

    def __init__(self, vault: Path):
        self.vault = Path(vault)
        self.notes: list = []
        self.by_fingerprint: dict = {}
        self.by_key: dict = {}
        self._load()

    def _load(self) -> None:
        mem = self.vault / "memory"
        for cls in CLASS_DIRS:
            d = mem / cls
            if not d.is_dir():
                continue
            for p in sorted(d.glob("*.md")):
                if p.name == "_index.md" or p.name.startswith("Icon"):
                    continue
                try:
                    fm, body = _frontmatter(p.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError):
                    continue
                title = fm.get("title") or fm.get("slug") or p.stem
                note = Note(rel=f"memory/{cls}/{p.name}", title=title, slug=fm.get("slug", p.stem),
                            type=fm.get("type", ""), status=fm.get("status", ""),
                            lifecycle=fm.get("lifecycle", ""), fingerprint=compute_fingerprint(body),
                            key=extract_key(title, body))
                self.notes.append(note)
                self.by_fingerprint.setdefault(note.fingerprint, note)
                if note.key and note.lifecycle != "superseded":
                    self.by_key.setdefault(note.key[:2], []).append(note)


# ── the decision ─────────────────────────────────────────────────────────────

def _resolve_type(rules, type_hint: "str | None", kind_hint: "str | None") -> "tuple[str, list]":
    """The memory type a candidate files under, and the reasons. A retired
    value collapses through the deprecations map; a record kind is refused
    (records are not memories); nothing at all takes the default type."""
    reasons = []
    for raw in (type_hint, kind_hint):
        if not raw:
            continue
        value = rules.resolve_deprecated(raw) or raw
        if value != raw:
            reasons.append(f"{raw} -> {value} (deprecations)")
        if value in rules.memory_types():
            return value, reasons
        if value in rules.record_kinds():
            raise ValueError(f"`{raw}` is a record kind — records are not filed as memories")
        reasons.append(f"`{raw}` is in neither register")
    default = rules.default_type()
    reasons.append(f"no memory type — filed as the contract's default `{default}` at low confidence")
    return default, reasons


def _settle_dest(vault: Path, class_dir: str, slug: str, fingerprint: str) -> "tuple[str, list]":
    """`<class>/<slug>.md`, or the next free `~dup` name when a different note
    already owns the basename. Returns (dest_rel, flags)."""
    dest = f"{class_dir}/{slug}.md"
    p = vault / dest
    if not p.exists():
        return dest, []
    try:
        _fm, body = _frontmatter(p.read_text(encoding="utf-8"))
        if compute_fingerprint(body) == fingerprint:
            return dest, []  # the twin test upstream already decided noop
    except (OSError, UnicodeDecodeError):
        pass
    stem = _DUP_SUFFIX.sub("", slug)
    n = 1
    while True:
        cand = f"{class_dir}/{stem}~dup{'' if n == 1 else n}.md"
        if not (vault / cand).exists():
            return cand, ["basename-clash"]
        n += 1


def _title_overlap(a: str, b: str) -> float:
    ta, tb = set(_tokens(a)), set(_tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def daemon_search(vault: Path, query: str, *, k: int = 5) -> list:
    """`agentmd search -json` for the similarity shortlist: vault-relative
    paths of the top hits, memory-root-relative where they fall under it.
    Empty on any failure — the shortlist is a secondary signal and its
    absence only means fewer flags."""
    binary = os.environ.get("AGENTMD", "").strip() or "agentmd"
    try:
        import recall  # noqa: E402  (the hook's own term extraction, so the engine asks the same question)
        terms = recall._daemon_query_terms(query)
    except Exception:
        terms = query
    if not terms:
        return []
    try:
        proc = subprocess.run([binary, "search", "-json", "-k", str(k), "-mode", "and", terms],
                              capture_output=True, text=True, timeout=20)
        rows = json.loads(proc.stdout) if proc.returncode == 0 and proc.stdout.strip() else []
    except Exception:
        return []
    out = []
    root_name = Path(vault).name + "/"
    for row in rows if isinstance(rows, list) else rows.get("results", []):
        path = row.get("path") if isinstance(row, dict) else None
        if not path:
            continue
        out.append(path[len(root_name):] if path.startswith(root_name) else path)
    return out


def decide(vault: "Path | str", *, title: str, body: str, slug: str, type_hint: "str | None" = None,
           kind_hint: "str | None" = None, confidence: "str | None" = None, source: "str | None" = None,
           rules=None, corpus: "CorpusIndex | None" = None, search=None, near_threshold: float = 0.6) -> FilingDecision:
    vault = Path(vault)
    rules = rules or storage_rules.rules()
    corpus = corpus or CorpusIndex(vault)
    mtype, reasons = _resolve_type(rules, type_hint, kind_hint)
    class_dir = rules.routing().get(mtype)
    if not class_dir:
        raise ValueError(f"the contract routes no class for type `{mtype}`")
    conf = CONFIDENCE.get((confidence or "medium"), "medium")
    flags: list = []
    if any("default" in r for r in reasons):
        conf = "low"
        flags.append("no-type")
    fp = compute_fingerprint(body)
    key = extract_key(title, body)
    decision = FilingDecision(type=mtype, class_dir=class_dir, dest_rel="", op="add",
                              filing_confidence=conf, source=source or "conversation", key=key,
                              flags=flags, reasons=reasons)

    twin = corpus.by_fingerprint.get(fp)
    if twin is not None:
        decision.op, decision.related = "noop", twin.rel
        decision.dest_rel = twin.rel
        decision.flags.append("exact-twin")
        decision.reasons.append(f"body is an exact twin of {twin.rel}")
        return decision

    if key is not None:
        for existing in corpus.by_key.get(key[:2], []):
            if existing.key[2] != key[2]:
                decision.op, decision.related = "supersede", existing.rel
                decision.flags.append("contradiction")
                decision.reasons.append(f"same key {key[:2]} as {existing.rel}, value {existing.key[2]!r} -> {key[2]!r}")
                break
            decision.op, decision.related = "update", existing.rel
            decision.flags.append("update-candidate")
            decision.reasons.append(f"same key and value as {existing.rel}; different body — filed beside it, flagged")
            break

    if decision.op == "add" and search is not None:
        try:
            hits = search(title) or []
        except Exception:
            hits = []
        for rel in hits:
            match = next((n for n in corpus.notes if n.rel == rel), None)
            if match is None or match.lifecycle == "superseded":
                continue
            if _title_overlap(title, match.title) >= near_threshold:
                decision.related = match.rel
                decision.flags.append("near-duplicate")
                decision.filing_confidence = "low"
                decision.reasons.append(f"title overlaps {match.rel} — probable duplicate, filed flagged (never merged)")
                break

    if decision.op == "supersede" and decision.related and not slug:
        slug = Path(decision.related).stem
    dest, dflags = _settle_dest(vault, class_dir, slug, fp)
    decision.dest_rel = dest
    decision.flags.extend(dflags)
    return decision


# ── applying ─────────────────────────────────────────────────────────────────

def _stamp_superseded(vault: Path, rel: str, by_rel: str) -> None:
    """The existing note gains `superseded_by` and flips `lifecycle` —
    line-surgical, and nothing is deleted."""
    p = vault / rel
    text = p.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return
    lines = text.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return
    seen = {}
    for i in range(1, end):
        k, sep, _ = lines[i].partition(":")
        if sep and lines[i][:1] not in " \t#-":
            seen.setdefault(k.strip(), i)
    for k, v in (("lifecycle", "superseded"), ("superseded_by", by_rel)):
        if k in seen:
            lines[seen[k]] = f"{k}: {v}"
        else:
            lines.insert(end, f"{k}: {v}")
            end += 1
    p.write_text("\n".join(lines), encoding="utf-8")


def apply(vault: "Path | str", decision: FilingDecision, *, body: str, tags: "list | None" = None,
          title: "str | None" = None) -> "Path | None":
    """Perform the decision. `add`/`update`/`supersede` write the note at its
    destination through `save.save_entry` with the write-time stamps;
    `supersede` then marks the existing note; `noop` reinforces the twin and
    writes nothing. Returns the path written (or reinforced)."""
    vault = Path(vault)
    import save  # noqa: E402  (same skill dir)
    if decision.op == "noop":
        import dedup_guard  # noqa: E402
        twin = vault / decision.dest_rel
        try:
            dedup_guard.reinforce(twin)
        except Exception:
            pass
        return twin
    slug = Path(decision.dest_rel).stem
    written = save.save_entry(vault, decision.type, slug, body, group="memory", tags=tags or [],
                              lifecycle="active", source=decision.source,
                              filing_confidence=decision.filing_confidence,
                              supersedes=decision.related if decision.op == "supersede" else None)
    if decision.op == "supersede" and decision.related:
        _stamp_superseded(vault, decision.related, decision.dest_rel)
    return written


# ── measurement + CLI ────────────────────────────────────────────────────────

def measure(vault: "Path | str") -> dict:
    """What share of the filed corpus yields a structural key — the number the
    plan asked for before leaning on the extractor."""
    corpus = CorpusIndex(Path(vault))
    total = len(corpus.notes)
    keyed = sum(1 for n in corpus.notes if n.key)
    kinds = {}
    for n in corpus.notes:
        if n.key:
            kinds[n.key[0].split(":", 1)[0]] = kinds.get(n.key[0].split(":", 1)[0], 0) + 1
    return {"notes": total, "keyed": keyed, "share": round(keyed / total, 3) if total else 0.0, "by_shape": kinds}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="filing_engine.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decide")
    d.add_argument("--vault", required=True)
    d.add_argument("--title", required=True)
    d.add_argument("--body-file", required=True)
    d.add_argument("--slug", default="")
    d.add_argument("--type", default=None)
    d.add_argument("--confidence", default=None)
    d.add_argument("--source", default=None)
    d.add_argument("--no-search", action="store_true")
    m = sub.add_parser("measure")
    m.add_argument("--vault", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "measure":
        print(json.dumps(measure(args.vault), indent=2))
        return 0
    body = Path(args.body_file).read_text(encoding="utf-8")
    slug = args.slug or re.sub(r"[^a-z0-9]+", "-", args.title.casefold()).strip("-")[:60]
    search = None if args.no_search else (lambda q: daemon_search(Path(args.vault), q))
    dec = decide(args.vault, title=args.title, body=body, slug=slug, type_hint=args.type,
                 confidence=args.confidence, source=args.source, search=search)
    print(json.dumps(dec.as_dict(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
