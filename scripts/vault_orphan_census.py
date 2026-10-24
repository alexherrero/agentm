#!/usr/bin/env python3
"""vault_orphan_census — a read-only link census of the vault, counted the way Obsidian counts.

    python3 scripts/vault_orphan_census.py                 # summary tables to stdout
    python3 scripts/vault_orphan_census.py --out DIR       # census.jsonl + census-summary.md in DIR
    python3 scripts/vault_orphan_census.py --vault ROOT    # a vault other than the resolved one

A report tool, not a gate (task 193, LD-8). The operator's own notes and other
repositories' records are orphans by choice, so a threshold would only learn to
tolerate them. Ported from the census of 2026-10-22 (`census-instrument.py`, kept
beside task 193's plan) with the same counting rules. It never writes the vault:
`--out` must name a directory outside it, and without `--out` nothing is written.

What counts as a link. Rules 3 and 6 match the vault's graph settings
(`hideUnresolved: false`, `showAttachments: false` in `.obsidian/graph.json`),
which are fixed here rather than read:

  1. Every `.md` file with no path component starting with `.` is a note.
  2. Body wikilinks, embeds and Markdown links to local files are links. Fenced
     code, inline code spans and `%%comments%%` are skipped.
  3. A link to a note that doesn't exist is a ghost node, so it is a graph edge.
  4. A frontmatter value counts only when it is a string, or a flat list of
     strings, containing `[[…]]`. An unquoted `related: [[x]]` is a nested list
     to YAML and draws nothing.
  5. Frontmatter strings with no `[[` that would resolve to a note are reported
     as `invisible_fm`, and never counted as edges.
  6. A link that is a vault-relative path names that note. Otherwise a bare name
     resolves by file name across the vault and a path by its suffix,
     case-insensitively. Among several notes of one name the note in the linking
     note's own folder wins, then the shortest path. A link to an attachment
     resolves but is not an edge.
  7. `userIgnoreFilters` in `.obsidian/app.json` drop a note from the graph, and
     drop every edge to or from it.

A graph orphan is a note the filters keep with no edge left in or out. Python 3.9
compatible (Apple python). Needs PyYAML.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import posixpath
import re
import subprocess
import sys
import time
import unicodedata
import urllib.parse

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def nfc(s):
    return unicodedata.normalize("NFC", s)


# ---------------------------------------------------------------- 1. universe
def walk_universe(root):
    """Every file whose path has no component starting with '.'; vault-relative, '/'-separated."""
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if not d.startswith("."))
        for fn in sorted(fns):
            if fn.startswith("."):
                continue
            rel = os.path.relpath(os.path.join(dp, fn), root).replace(os.sep, "/")
            out.append(rel)
    return out


def load_ignore_filters(root):
    """userIgnoreFilters: '/re/' -> JS regex tested on the path; anything else -> path prefix.

    A vault with no `.obsidian/app.json`, or none that parses, has no filters."""
    try:
        with open(os.path.join(root, ".obsidian", "app.json"), encoding="utf-8") as fh:
            raw = json.load(fh).get("userIgnoreFilters", [])
    except (OSError, ValueError):
        return []
    filters = []
    for entry in raw or []:
        m = re.match(r"^/(.+)/([a-z]*)$", entry)
        if m:
            flags = re.I if "i" in m.group(2) else 0
            filters.append(("regex", entry, re.compile(m.group(1), flags)))
        else:
            filters.append(("prefix", entry, entry))
    return filters


def is_ignored(path, filters):
    return matching_filter(path, filters) is not None


def matching_filter(path, filters):
    for kind, raw, pat in filters:
        if (kind == "regex" and pat.search(path)) or (kind == "prefix" and path.startswith(pat)):
            return raw
    return None


class VaultIndex:
    """The vault's files, its notes, its ignore filters and the lookups resolution uses."""

    def __init__(self, root):
        self.root = str(root)
        self.files = walk_universe(self.root)
        self.notes = [f for f in self.files if f.endswith(".md")]
        self.filters = load_ignore_filters(self.root)
        self.ignored = {f: is_ignored(f, self.filters) for f in self.notes}
        # lookup indexes, all keyed on NFC + lowercase (Obsidian matches case-insensitively)
        self.lower_map = {}                                   # lower path -> real path
        self.by_base = collections.defaultdict(list)          # lower basename (with extension) -> [real paths]
        for f in self.files:
            self.lower_map.setdefault(nfc(f).lower(), f)
            self.by_base[nfc(posixpath.basename(f)).lower()].append(f)


# ---------------------------------------------------------------- 2. resolution (rule 6)
def clean_linkpath(L):
    """Strip a trailing |alias, #anchor and .md, case kept (lowercased later)."""
    L = nfc(L).strip()
    L = L.split("|", 1)[0]
    L = L.split("#", 1)[0]
    L = L.strip().rstrip("\\").strip()          # a table-escaped pipe leaves a trailing backslash
    if L.lower().endswith(".md"):
        L = L[:-3]
    return L


def resolve(ix, L, src, md_link=False):
    """Return (target_path or None, ambiguous). `src` is the vault-relative source note."""
    L = clean_linkpath(L)
    if not L:
        return None, False
    key = L.lower()
    srcdir = posixpath.dirname(src)

    # a. explicit relative path
    if key.startswith("./") or key.startswith("../"):
        p = posixpath.normpath(posixpath.join(srcdir, key))
        if p.startswith(".."):
            return None, False
        for c in (p, p + ".md"):
            if c in ix.lower_map:
                return ix.lower_map[c], False
        return None, False

    # b. exact vault-relative path, then + .md
    for c in (key, key + ".md"):
        if c in ix.lower_map:
            return ix.lower_map[c], False

    # c. markdown links only: relative to the source's folder
    if md_link:
        p = posixpath.normpath(posixpath.join(srcdir, key))
        for c in (p, p + ".md"):
            if c in ix.lower_map:
                return ix.lower_map[c], False

    # d / e. path-suffix match (contains '/'), else basename match
    cands = []
    if "/" in key:
        last = key.rsplit("/", 1)[1]
        for path in ix.by_base.get(last, []) + ix.by_base.get(last + ".md", []):
            lp = nfc(path).lower()
            if lp.endswith("/" + key) or lp.endswith("/" + key + ".md"):
                cands.append(path)
    else:
        cands = ix.by_base.get(key, []) + ix.by_base.get(key + ".md", [])
    cands = sorted(set(cands))
    if not cands:
        return None, False
    cands.sort(key=lambda p: (posixpath.dirname(p) != srcdir, len(p), p))
    return cands[0], len(cands) > 1


# ---------------------------------------------------------------- 3. parsing
WIKI_RE = re.compile(r"\[\[([^\[\]\r\n]+)\]\]")
MD_LINK_RE = re.compile(
    r"!?\[[^\]\n]*\]\(\s*"
    r"(<[^>\n]*>|[^()\s]*(?:\([^()\s]*\)[^()\s]*)*)"
    r"(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)"
)
SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
# inline code span (backtick run closed by a run of the same length; not across a blank line)
# or a %%comment%% -- one alternation so whichever starts first wins
INLINE_RE = re.compile(
    r"(?<!`)(`+)(?!`)((?:(?!\n[ \t]*\n).)+?)(?<!`)\1(?!`)|%%.*?%%", re.S
)


def split_frontmatter(text):
    """-> (has_frontmatter, fm_text, body). Frontmatter must open on line 1 with '---'."""
    lines = text.lstrip("﻿").split("\n")
    if lines and lines[0].rstrip() == "---":
        for i in range(1, len(lines)):
            if lines[i].rstrip() in ("---", "..."):
                return True, "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    return False, "", text


def strip_code_and_comments(body):
    kept, fence = [], None
    for line in body.split("\n"):
        m = FENCE_RE.match(line)
        if fence is None:
            if m:
                fence = (m.group(1)[0], len(m.group(1)))
                continue
            kept.append(line)
        else:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] \
                    and line.strip() == m.group(1):
                fence = None
    return INLINE_RE.sub(" ", "\n".join(kept))


def body_links(body):
    """-> list of (raw linkpath, is_markdown_link) from wikilinks, embeds, markdown links."""
    text = strip_code_and_comments(body)
    out = []
    for m in WIKI_RE.finditer(text):
        target = re.split(r"(?<!\\)\|", m.group(1), 1)[0]
        if clean_linkpath(target):          # '[[#heading]]' cleans to '' = self-link, skipped
            out.append((target, False))
    for m in MD_LINK_RE.finditer(text):
        dest = m.group(1).strip()
        if dest.startswith("<") and dest.endswith(">"):
            dest = dest[1:-1].strip()
        if not dest or SCHEME_RE.match(dest):
            continue
        dest = urllib.parse.unquote(dest).split("#", 1)[0]
        if dest.strip():
            out.append((dest, True))
    return out


def norm_val(v):
    if v is None:
        return None
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return ",".join(str(norm_val(x)) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, default=str)[:80]
    return str(v)


def fm_wikilinks(props):
    """Rule 4: only top-level values; a string, or strings of a flat list, containing [[..]]."""
    out = []
    for _k, v in props.items():
        items = v if isinstance(v, list) else [v]
        for it in items:               # a nested list element is itself a list -> not a str -> skipped
            if isinstance(it, str) and "[[" in it:
                for m in WIKI_RE.finditer(it):
                    target = re.split(r"(?<!\\)\|", m.group(1), 1)[0]
                    if clean_linkpath(target):
                        out.append((target, False))
    return out


def invisible_fm(ix, props, src):
    """Rule 5: frontmatter strings with no [[ that still resolve to another note, any depth;
    plus every string inside a nested list (the unquoted `related: [[x]]` case)."""
    out = []

    def pathlike(s):    # looks like a path/filename rather than a bare word like `report`
        return "/" in s or s.lower().endswith(".md")

    def walk(node, topkey, at, nested):
        if isinstance(node, str):
            s = node.strip()
            if not s:
                return
            target, _amb = resolve(ix, s, src)
            if nested:
                out.append({"key": topkey, "at": at, "value": s[:120], "target": target,
                            "why": "nested-list", "pathlike": pathlike(s)})
            elif "[[" not in s and target and target.endswith(".md") and target != src:
                out.append({"key": topkey, "at": at, "value": s[:120], "target": target,
                            "pathlike": pathlike(s)})
        elif isinstance(node, list):
            for el in node:
                walk(el, topkey, at, True if isinstance(el, list) else nested)
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, topkey, at + "." + str(k), False)

    for k, v in props.items():
        walk(v, str(k), str(k), False)
    return out


def related_info(props, fm_text, yaml_error):
    raw_lines, grab = [], False
    for line in fm_text.split("\n"):
        if re.match(r"^related\s*:", line):
            raw_lines, grab = [line], True
        elif grab and (line.startswith((" ", "\t", "- ")) or not line.strip()):
            if line.strip():
                raw_lines.append(line)
        elif grab:
            grab = False
    raw = " ⏎ ".join(l.strip() for l in raw_lines)[:200]
    present = bool(raw_lines) if yaml_error else ("related" in props)
    if not present:
        return False, "missing", ""
    if yaml_error:
        return True, "other", raw
    v = props["related"]
    if v is None or v == "" or v == []:
        shape = "empty"
    elif isinstance(v, str):
        shape = "single-string"
    elif isinstance(v, list):
        if any(isinstance(x, list) for x in v):
            shape = "nested-list"
        elif v and all(isinstance(x, str) for x in v):
            shape = "wikilink-strings" if all("[[" in x for x in v) else "bare-strings"
        else:
            shape = "other"
    else:
        shape = "other"
    return True, shape, raw


DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def parse_date(v):
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    if isinstance(v, str):
        m = DATE_RE.search(v)
        if m:
            try:
                return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                return None
    return None


def iso_week(d):
    y, w, _ = d.isocalendar()
    return "%d-W%02d" % (y, w)


def git_first_added(root):
    """One pass: earliest commit date each path appears as Added (--no-renames: a rename is an add).

    A vault that isn't a git repository, or has no commits, gives no dates."""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    try:
        res = subprocess.run(
            ["git", "-C", root, "-c", "core.quotepath=false", "log", "--no-renames",
             "--diff-filter=A", "--format=%x01%cs", "--name-only"],
            capture_output=True, text=True, env=env, check=True)
    except (OSError, subprocess.CalledProcessError):
        return {}
    first, date = {}, None
    for line in res.stdout.split("\n"):
        if line.startswith("\x01"):
            date = line[1:].strip()
        elif line.strip() and date:
            p = nfc(line)
            if p not in first or date < first[p]:
                first[p] = date
    return first


# ---------------------------------------------------------------- 5. writer labels
# (substring in lowercased `source`, label) -- first match wins; refined after surveying real values
# Survey (agent/ + projects/, 2026-10-22): `source:` only ever holds conversation / external-fetch /
# operator-direct / inbox -- no hook/miner/enrich/crystallize/dream/digest values existed then, so the
# first group is future-proofing and `conversation` is the only live in-session signal.
SOURCE_WRITERS = [
    ("stop-hook", "stop-hook-miner"), ("stop_hook", "stop-hook-miner"), ("miner", "stop-hook-miner"),
    ("crystalliz", "crystallize"), ("dream", "mocs-dreaming"), ("digest", "digests-diagnostics"),
    ("enrich", "enricher"),
    ("external-fetch", "external-fetch"), ("inbox", "capture-inbox"),
    ("in-session", "in-session-capture"), ("conversation", "in-session-capture"),
]


def source_writer(src):
    s = (src or "").lower()
    for needle, label in SOURCE_WRITERS:
        if needle in s:
            return label
    return None


def writer_label(path, kind, typ, source):
    parts = path.split("/")
    base = parts[-1].lower()
    kt = {(kind or "").lower(), (typ or "").lower()}
    sw = source_writer(source)
    if parts[0] == "personal":
        return (sw, "personal+source-names-agent-writer") if sw else ("operator-personal", "personal/**")
    if kt & {"dir-index"}:      # _index.md pages are generated from standards/storage-rules.md
        return "generated-index", "kind|type=dir-index"
    if path.startswith("agent/memory/episodic/") or kt & {"trace", "session-trace"}:
        return "session-trace", "agent/memory/episodic/** or kind|type=trace"
    if path.startswith("agent/memory/entities/"):
        return "entity-builder", "agent/memory/entities/**"
    if path.startswith("agent/memory/mocs/") or base.startswith("moc-") or base == "needs-review.md":
        return "mocs-dreaming", "mocs/** | moc-*.md | needs-review.md"
    if path.startswith("agent/memory/crystallized/"):
        return "crystallize", "agent/memory/crystallized/**"
    if path.startswith("agent/diagnostics/"):
        return "digests-diagnostics", "agent/diagnostics/**"
    if path.startswith("agent/inbox/"):
        return "capture-inbox", "agent/inbox/**"
    if path.startswith(("agent/memory/semantic/", "agent/memory/procedural/")):
        if sw:
            return sw, "semantic|procedural + source signal"
        return "memory-card", "agent/memory/semantic|procedural/** (no source signal)"
    if path.startswith("agent/archive/"):
        return "archive", "agent/archive/**"
    if len(parts) > 3 and parts[0] == "projects" and (
            parts[2] == "tasks" or (parts[2] == "completed" and parts[3] == "tasks")):
        return "task-files", "projects/*/(completed/)tasks/**"
    if kt & {"handoff-artifact", "session-handoff", "handoff-index", "session-brief"}:
        return "handoff", "refinement: kind|type=handoff-*"      # written by the tokens:handoff skill
    if any(k.startswith("idea-incubator") for k in kt) or "idea" in kt:
        return "idea-incubator", "refinement: kind|type=idea|idea-incubator-*"
    for top, label in (("calendar", "calendar"), ("standards", "standards"),
                       ("systems", "systems"), ("resources", "resources")):
        if parts[0] == top:
            return label, top + "/**"
    folder2 = "/".join(parts[:-1][:2]) or "(root)"
    return "other:" + folder2, "fallthrough"


# ---------------------------------------------------------------- main census
def census(ix, yaml_mod, git_dates=None):
    """-> {path: record} for every note in `ix`, with the graph arithmetic applied."""
    if git_dates is None:
        git_dates = git_first_added(ix.root)
    recs, out_md = {}, {}
    for path in ix.notes:
        full = os.path.join(ix.root, path)
        with open(full, encoding="utf-8", errors="replace") as fh:
            text = fh.read().replace("\r\n", "\n")
        has_fm, fm_text, body = split_frontmatter(text)
        props, yaml_error = {}, False
        if has_fm:
            try:
                loaded = yaml_mod.safe_load(fm_text)
                props = loaded if isinstance(loaded, dict) else {}
            except Exception:  # noqa: BLE001 - any yaml.YAMLError subclass
                yaml_error = True
        links = body_links(body) + fm_wikilinks(props)
        inv = invisible_fm(ix, props, path)

        md_t, unres, attach, amb = set(), set(), set(), set()
        for raw, is_md in links:
            tgt, ambiguous = resolve(ix, raw, path, md_link=is_md)
            if tgt is None:
                unres.add(clean_linkpath(raw).lower())
                continue
            if ambiguous:
                amb.add(tgt)
            if tgt.endswith(".md"):
                if tgt != path:
                    md_t.add(tgt)
            else:
                attach.add(tgt)

        rel_present, rel_shape, rel_raw = related_info(props, fm_text, yaml_error)
        parts = path.split("/")
        folder = parts[:-1]
        mtime = datetime.date.fromtimestamp(os.stat(full).st_mtime).isoformat()
        gdate = git_dates.get(nfc(path))
        cdate = parse_date(props.get("created"))
        if cdate:
            week, wsrc = iso_week(cdate), "created"
        elif gdate:
            week, wsrc = iso_week(parse_date(gdate)), "git"
        else:
            week, wsrc = iso_week(parse_date(mtime)), "mtime"
        kind, typ, source = norm_val(props.get("kind")), norm_val(props.get("type")), norm_val(props.get("source"))
        writer, wrule = writer_label(path, kind, typ, source)
        recs[path] = {
            "path": path,
            "space": parts[0] if len(parts) > 1 else "(root)",
            "folder2": "/".join(folder[:2]) or "(root)",
            "folder3": "/".join(folder[:3]) or "(root)",
            "kind": kind, "type": typ, "status": norm_val(props.get("status")),
            "lifecycle": norm_val(props.get("lifecycle")), "source": source,
            "project": norm_val(props.get("project")), "task": norm_val(props.get("task")),
            "created": norm_val(props.get("created")),
            "enriched_by": "enriched_by" in props, "enriched_at": "enriched_at" in props,
            "related_present": rel_present, "related_shape": rel_shape, "related_raw": rel_raw,
            "has_frontmatter": has_fm, "yaml_error": yaml_error,
            "fm_keys": sorted(str(k) for k in props), "word_count": len(body.split()),
            "git_first_added": gdate, "mtime": mtime,
            "created_week": week, "created_week_src": wsrc,
            "writer": writer, "writer_rule": wrule,
            "out_md": sorted(md_t), "out_unresolved": len(unres),
            "out_unresolved_examples": sorted(unres)[:5], "out_attach": len(attach),
            "ambiguous": bool(amb), "out_ambiguous": len(amb),
            "is_ignored": ix.ignored[path], "invisible_fm": inv,
        }
        out_md[path] = md_t

    # ---- 4. graph arithmetic: invert out-links, then apply the ignore filters
    in_md = collections.defaultdict(set)
    for src, tgts in out_md.items():
        for t in tgts:
            in_md[t].add(src)
    for path, r in recs.items():
        outs, ins = set(r["out_md"]), in_md.get(path, set())
        outs_ign = {t for t in outs if ix.ignored[t]}
        ins_ign = {s for s in ins if ix.ignored[s]}
        full = not outs and r["out_unresolved"] == 0 and not ins
        graph = (not r["is_ignored"] and not (outs - outs_ign)
                 and r["out_unresolved"] == 0 and not (ins - ins_ign))
        r.update({
            "out_md_ignored": sorted(outs_ign), "in_md": sorted(ins), "in_md_ignored": sorted(ins_ign),
            "full_orphan": full, "graph_orphan": graph,
            "hidden_by_ignore": graph and not full,
            "invisible_only": graph and any((e["target"] or "").endswith(".md") for e in r["invisible_fm"]),
            "ghost_only": (not r["is_ignored"] and r["out_unresolved"] > 0
                           and not (outs - outs_ign) and not (ins - ins_ign)),
        })
    return recs


# ---------------------------------------------------------------- 6. summary tables
def pct(a, b):
    return "%.1f" % (100.0 * a / b) if b else "0.0"


def grouped(recs, keyfn, top=None, only_with_orphans=True):
    """-> rows [(group, orphans, notes, pct)] sorted by orphans desc."""
    tot, orph = collections.Counter(), collections.Counter()
    for r in recs:
        k = keyfn(r)
        tot[k] += 1
        orph[k] += r["graph_orphan"]
    rows = [(k, orph[k], tot[k], pct(orph[k], tot[k])) for k in tot if orph[k] or not only_with_orphans]
    rows.sort(key=lambda x: (-x[1], str(x[0])))
    return rows[:top] if top else rows


def md_table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(c) for c in row) + " |")
    return out


def totals(recs):
    """The T1 rows as (metric, notes) pairs; the figures a re-run is compared on."""
    R = list(recs.values())
    G = [r for r in R if r["graph_orphan"]]
    return [
        ("notes", len(R)),
        ("ignored notes (match a filter)", sum(r["is_ignored"] for r in R)),
        ("full_orphan (filters OFF; no out, no ghost, no in)", sum(r["full_orphan"] for r in R)),
        ("graph_orphan (Obsidian graph: ghosts = edges, filters ON)", len(G)),
        ("hidden_by_ignore (graph_orphan and not full_orphan)", sum(r["hidden_by_ignore"] for r in R)),
        ("invisible_only (graph_orphan with an invisible_fm that targets a note)", sum(r["invisible_only"] for r in R)),
        ("  of which a path-like value (contains '/' or ends .md), i.e. not a bare word like `report`",
         sum(r["invisible_only"] and any(e["pathlike"] and (e["target"] or "").endswith(".md")
                                         for e in r["invisible_fm"]) for r in G)),
        ("notes whose only graph edges are dangling ghost links", sum(r["ghost_only"] for r in R)),
        ("notes with any invisible_fm entry", sum(bool(r["invisible_fm"]) for r in R)),
        ("notes with yaml_error", sum(r["yaml_error"] for r in R)),
        ("notes with an ambiguous link", sum(r["ambiguous"] for r in R)),
    ]


def summarize(ix, recs, today, elapsed):
    R = [recs[p] for p in sorted(recs)]
    G = [r for r in R if r["graph_orphan"]]
    L = []
    add = L.append

    add("# Vault link census summary")
    add("")
    add("Vault `%s`; %d files, %d notes; ignore filters: %s. Generated %s by `vault_orphan_census.py`; "
        "full per-note data in `census.jsonl`." % (
            ix.root, len(ix.files), len(R), ", ".join("`%s`" % f[1] for f in ix.filters) or "none", today))
    add("")
    add("## T1 totals")
    L += md_table(["metric", "notes"], totals(recs))
    add("")

    add("## T2 graph_orphan by space, then folder3 (top 45)")
    L += md_table(["space", "orphans", "notes in space", "%"], grouped(R, lambda r: r["space"]))
    add("")
    L += md_table(["folder3", "orphans", "notes in folder", "%"], grouped(R, lambda r: r["folder3"], 45))
    add("")

    def field_rows(field):
        miss = [r for r in R if not r[field]]
        rows = grouped([r for r in R if r[field]], lambda r: r[field], 30)
        mo = sum(r["graph_orphan"] for r in miss)
        return rows, ("(missing)", mo, len(miss), pct(mo, len(miss)))

    add("## T3/T4 graph_orphan by kind, type, status, lifecycle (top 30 values with >=1 orphan each; missing separate)")
    allrows = []
    for f in ("kind", "type", "status", "lifecycle"):
        rows, miss = field_rows(f)
        allrows += [("%s = %s" % (f, v), o, n, pc) for v, o, n, pc in rows + [miss]]
    L += md_table(["field = value", "orphans", "notes", "%"], allrows)
    add("")

    add("## T5 graph_orphan by writer")
    L += md_table(["writer", "orphans", "notes", "%"], grouped(R, lambda r: r["writer"]))
    zero = [(w, n) for w, o, n, _p in grouped(R, lambda r: r["writer"], only_with_orphans=False) if not o]
    add("")
    add("Writers with 0 orphans (notes): " + ", ".join("%s (%d)" % z for z in zero))
    add("")

    cur = iso_week(today)
    add("## T6 graph_orphan by created_week (2026-W30 .. current %s, one earlier bucket)" % cur)

    def bucket(r):
        w = r["created_week"]
        return "earlier" if w < "2026-W30" else ("later" if w > cur else w)

    srcs_all, srcs_orph = collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter)
    for r in R:
        srcs_all[bucket(r)][r["created_week_src"]] += 1
        if r["graph_orphan"]:
            srcs_orph[bucket(r)][r["created_week_src"]] += 1

    def dom(c):
        if not c:
            return "-"
        k, v = c.most_common(1)[0]
        return "%s %d%%" % (k, round(100.0 * v / sum(c.values())))

    wk = grouped(R, bucket, only_with_orphans=False)
    wk.sort(key=lambda x: ("0" if x[0] == "earlier" else "2" if x[0] == "later" else "1") + x[0])
    L += md_table(["week", "orphans", "notes created that week", "dominant date src (all notes)",
                   "dominant date src (orphans)"],
                  [(w, o, n, dom(srcs_all[w]), dom(srcs_orph[w])) for w, o, n, _p in wk])
    add("")

    add("## T7 related_shape")
    shapes = ["missing", "empty", "wikilink-strings", "nested-list", "bare-strings", "single-string", "other"]
    ca, cg = collections.Counter(r["related_shape"] for r in R), collections.Counter(r["related_shape"] for r in G)
    L += md_table(["related_shape", "all notes", "graph orphans", "% of its notes that are orphans"],
                  [(s_, ca[s_], cg[s_], pct(cg[s_], ca[s_])) for s_ in shapes])
    add("")

    add("## T8 invisible_fm by key (top 15 by notes affected)")
    by_key = collections.defaultdict(list)
    for r in R:
        for e in r["invisible_fm"]:
            by_key[e["key"]].append((r, e))
    rows = []
    for k, items in by_key.items():
        notes = {r["path"] for r, _e in items}
        orph = {r["path"] for r, _e in items if r["graph_orphan"]}
        nested = sum(1 for _r, e in items if e.get("why") == "nested-list")
        plike = sum(1 for _r, e in items if e["pathlike"])
        seen, ex = set(), []
        for r, e in sorted(items, key=lambda x: (not x[1]["pathlike"], x[0]["path"])):   # path-like first
            if len(ex) < 3 and r["path"] not in seen:
                seen.add(r["path"])
                ex.append("`%s`: %s=%s → %s" % (r["path"], e["at"], e["value"][:40].replace("|", "/"), e["target"]))
        rows.append((k, len(notes), len(orph), len(items), plike, nested, "<br>".join(ex)))
    rows.sort(key=lambda x: (-x[1], x[0]))
    L += md_table(["key", "notes", "graph orphans", "entries", "path-like", "nested-list", "3 examples (path-like first)"],
                  rows[:15])
    add("")

    add("## T9 graph orphans: hygiene")
    L += md_table(["condition", "orphans", "% of orphans"], [
        (c, n, pct(n, len(G))) for c, n in [
            ("has_frontmatter = false", sum(not r["has_frontmatter"] for r in G)),
            ("yaml_error = true", sum(r["yaml_error"] for r in G)),
            ("word_count < 50", sum(r["word_count"] < 50 for r in G)),
            ("missing kind AND type", sum(not r["kind"] and not r["type"] for r in G)),
        ]])
    add("")

    add("## T10 hidden_by_ignore: ignored folders their links point into (top 10)")
    hid = [r for r in R if r["hidden_by_ignore"]]
    f2 = lambda p: "/".join(p.split("/")[:-1][:2]) or "(root)"  # noqa: E731
    n_notes, via_out, via_in = collections.Counter(), collections.Counter(), collections.Counter()
    for r in hid:
        outs = {f2(t) for t in r["out_md_ignored"]}
        ins = {f2(t) for t in r["in_md_ignored"]}
        for f in outs | ins:
            n_notes[f] += 1
        for f in outs:
            via_out[f] += 1
        for f in ins:
            via_in[f] += 1
    flt = {f: matching_filter(f + "/x.md", ix.filters) for f in n_notes}
    if hid:
        L += md_table(["ignored folder (folder2)", "hidden notes", "via its out-links", "via its in-links", "matching filter"],
                      [(f, n, via_out[f], via_in[f], "`%s`" % flt[f]) for f, n in n_notes.most_common(10)])
        add("")
        add("(%d hidden notes in all; a note counts once per folder.)" % len(hid))
    else:
        nign = sum(1 for r in R if not r["is_ignored"] and (r["out_md_ignored"] or r["in_md_ignored"]))
        add("None: 0 hidden notes. %d non-ignored notes have a link to/from an ignored note, but every one "
            "also has a non-ignored edge, so none is orphaned by the filters." % nign)
    add("")

    add("## T11 spot-check (verify by hand)")
    picked, seen_w = [], set()
    for w, _o, _n, _p in grouped(R, lambda r: r["writer"]):
        cand = [r for r in G if r["writer"] == w and not r["invisible_fm"]]
        cand = cand or [r for r in G if r["writer"] == w]
        if cand and w not in seen_w and len(picked) < 4:
            seen_w.add(w)
            picked.append(("orphan", cand[len(cand) // 2]))
    nonorph = [r for r in R if not r["graph_orphan"] and not r["is_ignored"]]
    ghost = [r for r in nonorph if r["ghost_only"]]
    both = [r for r in nonorph if r["out_md"] and r["in_md"] and r["invisible_fm"]] or \
           [r for r in nonorph if r["out_md"] and r["in_md"]]
    for tag, pool in (("non-orphan (ghost-only)", ghost), ("non-orphan (out+in)", both)):
        if pool:
            picked.append((tag, pool[len(pool) // 2]))
    for tag, r in picked:
        inv = ["%s=%s→%s" % (e["key"], e["value"][:30], e["target"]) for e in r["invisible_fm"][:3]]
        add("- **%s** `%s` (writer `%s`)" % (tag, r["path"], r["writer"]))
        add("  - out_md(%d) %s | unresolved %d %s | attach %d | in_md(%d, %d ignored) %s | invisible_fm %s" % (
            len(r["out_md"]), [x for x in r["out_md"][:5]] or "none", r["out_unresolved"],
            r["out_unresolved_examples"][:5], r["out_attach"], len(r["in_md"]), len(r["in_md_ignored"]),
            [x for x in r["in_md"][:5]] or "none", inv or "none"))
    add("")

    add("## Writer rules (first match wins; count in brackets)")
    rc = collections.Counter((r["writer"] if not r["writer"].startswith("other:") else "other:<folder2>",
                              r["writer_rule"]) for r in R)
    add("; ".join("`%s` <- %s [%d]" % (w, rule, n) for (w, rule), n in sorted(rc.items(), key=lambda x: (-x[1], x[0]))))
    add("")
    add("Runtime %.1fs." % elapsed)
    return L


def jsonl_lines(recs):
    return [json.dumps(recs[path], ensure_ascii=False, sort_keys=True, default=str) for path in sorted(recs)]


# ---------------------------------------------------------------- CLI
def _inside(child, parent):
    child, parent = os.path.realpath(child), os.path.realpath(parent)
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


def resolve_vault(explicit):
    """The vault root: `--vault` when given, else `harness_memory.vault_path()`."""
    if explicit:
        return os.path.abspath(explicit)
    import harness_memory  # noqa: PLC0415 - only the default path needs the resolver
    root = harness_memory.vault_path()
    return str(root) if root else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--vault", default=None, help="vault root to count (default: resolved at runtime)")
    ap.add_argument("--out", default=None,
                    help="directory outside the vault for census.jsonl and census-summary.md "
                         "(default: print the summary to stdout and write nothing)")
    args = ap.parse_args(argv)

    t0 = time.time()
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        print("vault_orphan_census: PyYAML is not installed. Install it: "
              "python3 -m pip install -r requirements.txt", file=sys.stderr)
        return 2
    root = resolve_vault(args.vault)
    if not root or not os.path.isdir(root):
        print("vault_orphan_census: no vault found%s" % (" at %s" % root if root else ""), file=sys.stderr)
        return 2
    if args.out and _inside(args.out, root):
        print("vault_orphan_census: --out %s is inside the vault; the census never writes the vault"
              % args.out, file=sys.stderr)
        return 2

    ix = VaultIndex(root)
    recs = census(ix, yaml)
    summary = summarize(ix, recs, datetime.date.today(), time.time() - t0)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "census.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(jsonl_lines(recs)) + "\n")
        with open(os.path.join(args.out, "census-summary.md"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(summary) + "\n")
        orphans = sum(r["graph_orphan"] for r in recs.values())
        print("vault_orphan_census: %d notes, %d graph orphans; wrote census.jsonl and census-summary.md to %s"
              % (len(recs), orphans, args.out))
    else:
        print("\n".join(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
