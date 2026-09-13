#!/usr/bin/env python3
"""The card backfill (agentm-vault plan 06): what the dry run counts, what the
applied run writes and journals, what it re-records for the night, and what it
must never write."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
for _p in (str(_REPO / "scripts"), str(_REPO / "scripts" / "migrate"),
           str(_REPO / "harness" / "skills" / "memory" / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import card_backfill as cb  # noqa: E402
import card_shape as cs  # noqa: E402

PV = "enrich/1+prompt/5d3a4cca1b02"
RULES = "3c57fd89087c1a25"
CONTRACT = cb.Contract(
    {"operator-direct": "trusted", "conversation": "trusted", "external-fetch": "untrusted", "email": "untrusted"},
    0.65, "active")
S = "Agent/memory/semantic"

FIXTURE = {
    # A card from before the write path: retired fields, an empty tag list, a
    # captured date, `active` at `low`, no source, and an H1 for a title.
    f"{S}/old-card.md": """---
type: preference
status: active
altitude: artifact
captured: '2026-06-27'
tags: []
group: memory
slug: old-card
always_load: false
mining_confidence: LOW
mining_rationale: "follow-up marker"
mining_occurrences: 1
filing_confidence: low
---

# An old card

Body text.
""",
    # An enriched card: scored above the floor with no tier, a trust that does
    # not match its transport, and the render's order.
    f"{S}/scored-card.md": f"""---
title: A scored card
type: reference
status: active
confidence: 0.82
tags: [a]
summary: "Already summarized."
updated: "2026-09-12"
enriched_by: {PV}
rules_hash: {RULES}
enriched_at: "2026-09-12T09:27:39Z"
source: external-fetch
source_url: https://example.com/x
lifecycle: active
created: 2026-08-12
trust: trusted
---

Body.
""",
    # A counter slug with a title, refused by the night and linked from around the vault.
    f"{S}/never-judged-by-a-model-1.md": f"""---
title: Staleness computed from input hashes
type: idea
status: unfiled
confidence: 0.40
filing_confidence: low
source: conversation
trust: trusted
created: 2026-08-01
updated: 2026-08-02
slug: never-judged-by-a-model-1
derived_from: [personal/_inbox/never-judged-by-a-model-1.md]
enriched_by: {PV}
enriched_at: "2026-09-12T09:00:00Z"
rules_hash: {RULES}
---

...never judged by a model.
""",
    # A `captured` the ingest sweep moved to a later write: it post-dates the
    # file's first commit, so the commit dates the card.
    f"{S}/clobbered.md": f"""---
title: A clobbered capture
type: reference
status: unfiled
lifecycle: active
filing_confidence: low
source: conversation
trust: trusted
updated: 2026-09-12
enriched_by: {PV}
enriched_at: "2026-09-12T09:31:16Z"
rules_hash: {RULES}
captured: 2026-09-12T20:22:27+00:00
---

A body.
""",
    f"{S}/twin.md": """---
title: Twin
type: reference
status: active
lifecycle: active
filing_confidence: high
source: conversation
trust: trusted
created: 2026-08-03
updated: 2026-08-03
slug: twin
---

A twin.

With a second paragraph, so no summary derives.
""",
    # A title-less namesake: its counter comes off, the name is taken, and it
    # grows from its own text.
    f"{S}/twin~dup.md": """---
type: reference
status: unfiled
lifecycle: active
filing_confidence: low
source: conversation
trust: trusted
created: 2026-08-03
updated: 2026-08-03
---

...twin note written again later today.
""",
    f"{S}/agentm-self-probe.md": """---
title: "AgentM self-probe"
type: reference
status: active
lifecycle: active
altitude: artifact
filing_confidence: high
created: 2026-09-12T05:41:38Z
updated: 2026-09-12T05:41:38Z
slug: agentm-self-probe
probe: self-probe
---

synthetic
""",
    f"{S}/in-shape.md": """---
title: In shape
type: convention
status: active
lifecycle: active
filing_confidence: high
source: conversation
trust: trusted
created: 2026-08-01
updated: 2026-08-02
tags: [x]
slug: in-shape
---

Body.

More body, so no summary derives.
""",
    "Agent/memory/episodic/2026-09-05-a-session-65016765.md": """---
title: A session
kind: session-trace
status: active
lifecycle: active
slug: 2026-09-05-a-session-65016765
day: 2026-09-05
created: 2026-09-05
session: abc
source: conversation
entities: [never-judged-by-a-model-1, twin]
---

## Asked
""",
    "Agent/memory/procedural/_index.md": """---
kind: dir-index
status: active
group: memory
slug: _index
---

Index.
""",
    "Calendar/2026/2026-09-05.md": "---\nkind: day\n---\n\n- [[never-judged-by-a-model-1|a note]]\n",
    "Projects/agentm/_harness/log.md": "Moved Agent/memory/semantic/never-judged-by-a-model-1.md today.\n",
    "Personal/notes/mine.md": "My link: [[never-judged-by-a-model-1]]\n",
}


class _Vault(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.vault, self.state, self.logs = base / "Vault", base / "state", base / "logs"
        self.root = self.vault / "Agent"
        for rel, text in FIXTURE.items():
            p = self.vault / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        self.state.mkdir()
        # Two lane directories the purge emptied: one bare, one kept alive by
        # sync litter (Drive's icon file; `.DS_Store` on Windows, which refuses a
        # carriage return in a file name). A class directory is flat, so both go.
        (self.root / "memory" / "crystallized" / "good").mkdir(parents=True)
        (self.root / "memory" / "crystallized" / "private").mkdir()
        litter = "Icon\r" if os.name != "nt" else ".DS_Store"
        (self.root / "memory" / "crystallized" / "private" / litter).write_text("", encoding="utf-8")
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "fixture")
        refused = {"rel": f"{S}/never-judged-by-a-model-1.md", "gate": "grounding",
                   "key": cb.fingerprint_key(PV, RULES, FIXTURE[f"{S}/never-judged-by-a-model-1.md"]),
                   "version": PV, "rules_hash": RULES, "gates": "gates/1+faith/x", "reason": "r",
                   "at": "2026-09-12T02:33:00Z"}
        stale = dict(refused, rel=f"{S}/scored-card.md", key="0" * 64)
        (self.state / "enrich-refusals.jsonl").write_text(
            json.dumps(refused) + "\n" + json.dumps(stale) + "\n", encoding="utf-8")
        (self.state / ".heat.json").write_text(json.dumps(
            {"entries": {"never-judged-by-a-model-1": {"hits": 3}}, "version": 1}), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def git(self, *args):
        env = dict(os.environ, GIT_AUTHOR_DATE="2026-07-01T10:00:00+00:00",
                   GIT_COMMITTER_DATE="2026-07-01T10:00:00+00:00")
        subprocess.run(["git", "-C", str(self.vault), "-c", "user.email=t@t", "-c", "user.name=t",
                        "-c", "commit.gpgsign=false", *args], check=True, capture_output=True, env=env)

    def plan(self):
        return cb.build_plan(self.vault, self.root, CONTRACT, cb.Git(self.vault),
                             self.state / "enrich-refusals.jsonl", self.state)

    def apply(self, plan, count=None):
        return cb.apply(self.vault, self.root, CONTRACT, cb.Git(self.vault), plan,
                        plan["counts"]["files"] if count is None else count,
                        self.state / "card-backfill", plan["run_id"],
                        self.state / "enrich-refusals.jsonl", self.state, log_root=self.logs)

    def read(self, rel):
        return (self.vault / rel).read_text(encoding="utf-8")


class TheDryRun(_Vault):
    def test_it_counts_each_stamp_retirement_and_rename(self):
        plan, _contents, _links = self.plan()
        c = plan["counts"]
        self.assertEqual((c["cards"], c["records"]), (8, 2))
        # lifecycle: the old card and the counter card lack one; summary: the
        # clobbered card's body is one sentence.
        self.assertEqual(c["stamp"], {"created": 2, "filing_confidence": 1, "lifecycle": 2, "source": 1,
                                      "summary": 1, "title": 1, "trust": 1, "updated": 1})
        self.assertEqual(c["correct"], {"status": 1, "trust": 1})
        self.assertEqual(c["basis"], {"created:captured": 1, "created:first-commit": 1,
                                      "filing_confidence:confidence": 1, "source:default": 1})
        self.assertEqual(c["retire"], {"altitude": 2, "always_load": 1, "captured": 2, "group": 2,
                                       "mining_confidence": 1, "mining_occurrences": 1, "mining_rationale": 1})
        self.assertEqual((c["empty_tags"], c["entities_to_touched"], c["renames"]), (1, 1, 2))
        self.assertEqual(plan["renames"], {"never-judged-by-a-model-1": "staleness-computed-from-input-hashes",
                                           "twin~dup": "twin-note"})
        # The calendar's link is rewritten; the project space's record and a
        # personal note keep the names they were written with.
        self.assertEqual(c["links"]["files"], 1)
        self.assertEqual([i["rel"] for i in c["links"]["not_written"]],
                         ["Personal/notes/mine.md", "Projects/agentm/_harness/log.md"])
        self.assertEqual(c["refusals"], {"stored": 2, "standing": 1, "reproduced": 1, "rerecord": 1})
        self.assertEqual((c["sidecars"], len(c["probes"]), c["titleless_judged"], c["titleless_unjudged"]),
                         (1, 1, [], 1))
        self.assertEqual(sorted(plan["empty_dirs"]),
                         ["Agent/memory/crystallized/good", "Agent/memory/crystallized/private"])
        self.assertEqual(c["files"], len(plan["notes"]) + 1)
        self.assertNotIn(f"{S}/in-shape.md", [n["rel"] for n in plan["notes"]])

    def test_it_never_writes_an_enrichment_stamp_and_a_record_never_gains_card_fields(self):
        _plan, contents, _links = self.plan()
        for rel, text in contents.items():
            before = cs.split_note(FIXTURE[rel])[0]
            after = cs.split_note(text)[0]
            for key in ("enriched_by", "enriched_at", "rules_hash"):
                self.assertEqual(cs.raw_value(after, key), cs.raw_value(before, key), f"{rel} {key}")
            if cs.raw_value(after, "kind") is not None:
                for key in cs.RECORD_NEVER + ("backfilled",):
                    self.assertIsNone(cs.raw_value(after, key), f"{rel} gained {key}")

    def test_every_note_leaves_in_the_cards_order_and_names_what_was_stamped(self):
        _plan, contents, _links = self.plan()
        for rel, text in contents.items():
            self.assertEqual(cs.reorder(text), text, rel)
            self.assertEqual(cs.order_findings(cs.keys(text)), [], rel)
        old = cs.split_note(contents[f"{S}/old-card.md"])[0]
        self.assertEqual(cs.flow_list(cs.raw_value(old, "backfilled")),
                         ["title", "status", "lifecycle", "source", "trust", "created", "updated"])
        self.assertEqual(cs.raw_value(old, "created"), "2026-06-27")
        self.assertEqual(cs.scalar(cs.raw_value(old, "title")), "An old card")
        self.assertEqual(cs.raw_value(old, "status"), "unfiled")
        for gone in cs.RETIRED_FIELDS + ("tags",):
            self.assertIsNone(cs.raw_value(old, gone), gone)
        clobbered = cs.split_note(contents[f"{S}/clobbered.md"])[0]
        self.assertEqual(cs.raw_value(clobbered, "created"), "2026-07-01")
        scored = cs.split_note(contents[f"{S}/scored-card.md"])[0]
        self.assertEqual((cs.raw_value(scored, "filing_confidence"), cs.raw_value(scored, "trust")),
                         ("high", "untrusted"))
        probe = cs.split_note(contents[f"{S}/agentm-self-probe.md"])[0]
        self.assertIsNone(cs.raw_value(probe, "source"))
        trace = cs.split_note(contents["Agent/memory/episodic/2026-09-05-a-session-65016765.md"])[0]
        self.assertEqual(cs.flow_list(cs.raw_value(trace, "touched")),
                         ["staleness-computed-from-input-hashes", "twin"])

    def test_a_directory_holding_notes_is_reported_and_never_removed(self):
        lane = self.root / "memory" / "semantic" / "lane"
        lane.mkdir()
        (lane / "kept.md").write_text("---\ntype: idea\n---\nkept\n", encoding="utf-8")
        plan, _c, _l = self.plan()
        self.assertNotIn("Agent/memory/semantic/lane", plan["empty_dirs"])
        self.assertIn("Agent/memory/semantic/lane/: a directory holding notes inside a class directory",
                      plan["findings"])

    def test_links_are_rewritten_in_every_form_a_note_is_named(self):
        text = ("---\nrelated: [\"[[old-1]]\"]\ntouched: [old-1, other]\n"
                "derived_from: [memory/semantic/old-1.md, personal/_inbox/old-1.md]\n---\n"
                "See [[old-1]], [[old-1|this]], [[old-1#Part]] and Agent/memory/semantic/old-1.md; "
                "old-1 in prose stays, and so does memory/idea/old-1.md.\n")
        out, n = cb.rewrite_links(text, {"old-1": "new-name"}, {"old-1": "semantic"})
        self.assertEqual(n, 7)  # three in the frontmatter lists, three wikilinks, one body path
        self.assertNotIn("[[old-1", out)
        self.assertIn("touched: [new-name, other]", out)
        self.assertIn("Agent/memory/semantic/new-name.md", out)
        # A path in another directory names a different file: the inbox capture a
        # card was mined from, or a class directory from before the migration.
        self.assertIn("derived_from: [memory/semantic/new-name.md, personal/_inbox/old-1.md]", out)
        self.assertIn("so does memory/idea/old-1.md", out)
        self.assertIn("old-1 in prose stays", out)


class TheAppliedRun(_Vault):
    def test_it_matches_the_dry_run_rerecords_the_refusal_and_revert_restores_every_byte(self):
        plan, contents, _links = self.plan()
        plan["run_id"] = "card-backfill-test"
        journal = self.apply(plan)
        self.assertTrue(journal["matches_dry_run"], journal)
        self.assertTrue((self.root / "memory" / cs.MARKER_NAME).exists())
        new_rel = f"{S}/staleness-computed-from-input-hashes.md"
        self.assertFalse((self.vault / S / "never-judged-by-a-model-1.md").exists())
        self.assertIn("slug: staleness-computed-from-input-hashes", self.read(new_rel))
        self.assertTrue((self.vault / S / "twin-note.md").exists())
        self.assertIn("[[staleness-computed-from-input-hashes|a note]]", self.read("Calendar/2026/2026-09-05.md"))
        # The project space's record keeps the name it was written with, and the
        # renamed card's provenance still names the inbox file it came from.
        self.assertEqual(self.read("Projects/agentm/_harness/log.md"), FIXTURE["Projects/agentm/_harness/log.md"])
        self.assertIn("derived_from: [personal/_inbox/never-judged-by-a-model-1.md]", self.read(new_rel))
        self.assertEqual(self.read("Personal/notes/mine.md"), FIXTURE["Personal/notes/mine.md"])

        rows = cb._standing_refusals(self.state / "enrich-refusals.jsonl")
        self.assertEqual(rows[new_rel]["key"], cb.fingerprint_key(PV, RULES, self.read(new_rel)))
        self.assertEqual(rows[f"{S}/scored-card.md"]["key"], "0" * 64)  # a stale row is not re-recorded
        heat = json.loads((self.state / ".heat.json").read_text(encoding="utf-8"))["entries"]
        self.assertIn("staleness-computed-from-input-hashes", heat)

        self.assertFalse((self.root / "memory" / "crystallized" / "good").exists())
        self.assertFalse((self.root / "memory" / "crystallized" / "private").exists())
        self.assertEqual(sorted(journal["empty_dirs_removed"]),
                         ["Agent/memory/crystallized/good", "Agent/memory/crystallized/private"])

        again, _c, _l = self.plan()
        self.assertEqual(again["counts"]["files"], 0, again["notes"])
        self.assertEqual(again["empty_dirs"], [])

        cb.revert(self.vault, self.root, "card-backfill-test", log_root=self.logs)
        for rel, text in FIXTURE.items():
            self.assertEqual(self.read(rel), text, rel)
        self.assertFalse((self.vault / new_rel).exists())
        self.assertFalse((self.root / "memory" / cs.MARKER_NAME).exists())

    def test_it_refuses_a_vault_that_moved_or_a_count_that_does_not_match(self):
        plan, _c, _l = self.plan()
        plan["run_id"] = "card-backfill-test"
        with self.assertRaises(cb.Refused):
            self.apply(plan, count=plan["counts"]["files"] + 1)
        (self.vault / S / "in-shape.md").write_text(FIXTURE[f"{S}/in-shape.md"].replace("tags: [x]", "tags: []"),
                                                  encoding="utf-8")
        with self.assertRaises(cb.Refused):
            self.apply(plan)
        self.assertEqual(self.read(f"{S}/old-card.md"), FIXTURE[f"{S}/old-card.md"])
        self.assertFalse((self.root / "memory" / cs.MARKER_NAME).exists())


class TheFingerprintPort(unittest.TestCase):
    def test_it_reproduces_the_key_the_daemon_computes(self):
        # daemon/internal/enrich/fingerprint_port_test.go pins the same literal
        # for the Go implementation; when either side changes, one test fails.
        text = "---\r\ntitle: Ünïcode Title\r\n\ttags: [a,  b]\n---\n\n  Body  WITH\ttabs  \n\n\nEnd.\n"
        self.assertEqual(cb.fingerprint_key(PV, RULES, text),
                         "7b214f29925d6dae9e94fb60eecde051619494d8c84b9d9ae1f5d672b8240dc9")


if __name__ == "__main__":
    unittest.main()
