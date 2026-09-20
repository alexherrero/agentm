#!/usr/bin/env python3
"""test_untrusted_card.py — the filing half that outlived the email door.

The door's transport retired in agentm-vault plan 16 and its tests retired with
it: `scripts/test_mail_door.py` was almost entirely about authenticating IMAP
mail — DMARC alignment, `Authentication-Results` parsing, the peek-and-mark
contract — and none of that describes anything the repo still does.

What the door's tests ALSO covered, and what is preserved here against the
surviving API, is the part that was never about email:

  - one card in, one card out, `unfiled` and `untrusted`;
  - a `why:` line and a `project:` line are kept, a `[tag]` routes a type, an
    unknown tag invents none;
  - the body never becomes `instructions` — the one field that can cause
    something to happen;
  - a size cap that truncates out loud rather than dropping, and a hard ceiling
    that drops rather than truncating;
  - a refusal is counted by a named reason and stores nothing;
  - `daily_write_cap` is honoured, and a refusal by it is the transient one.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TOOLKIT = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for _p in (str(_HERE), str(_TOOLKIT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import untrusted_card as uc  # noqa: E402

#: The transport being *used*, not mentioned: an import statement, an attribute
#: reference, a connection, or a read of the header the door authenticated on.
#: Written in the subset POSIX ERE (`git grep -E`) and Python `re` both read,
#: so the two halves of this check cannot drift.
#: Prose that records the retirement is the opposite of the failure and must not
#: match — `test_that_grep_can_still_fail` pins both directions.
_TRANSPORT_IN_USE = (
    r"^[ \t]*import imaplib"
    r"|imaplib\."
    r"|IMAP4_SSL\("
    r"|get(_all)?\([\"']Authentication-Results"
)


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    block = text.split("---\n", 2)[1]
    out = {}
    for line in block.splitlines():
        if ":" in line and not line.startswith((" ", "\t", "-")):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


class _Filing(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.vault = Path(self._td.name)

    def file(self, text: str, *, title: str = "A thought", **kw):
        card, reason = uc.parse_card_text(text, title=title)
        self.assertIsNone(reason or None, f"the card was refused: {reason}")
        result = uc.FileResult()
        out = uc.file_card(self.vault, card, result=result, **kw)
        return card, out, result


class ACardThatShouldGetThrough(_Filing):
    def test_it_becomes_one_card(self):
        _card, out, result = self.file("Drive is already the route.\n")
        self.assertTrue(out.success, getattr(out, "error", None))
        self.assertEqual(len(result.filed), 1)
        self.assertEqual(result.dropped, {})
        self.assertEqual(
            len([p for p in self.vault.rglob("*.md") if p.name != "_index.md"]), 1,
            "one card in, one note out")

    def test_the_card_is_untrusted_and_unfiled(self):
        _card, out, _result = self.file("Drive is already the route.\n")
        fm = _frontmatter(out.path)
        self.assertEqual(fm["status"], "unfiled")
        self.assertEqual(fm["trust"], "untrusted")
        self.assertEqual(fm["source"], "inbox")
        self.assertNotEqual(fm["status"], "active",
                            "a card from an untrusted transport filed itself active")

    def test_the_body_never_becomes_instructions(self):
        # The one field that can cause something to happen. A card is content,
        # and this is the call site where that stays true.
        _card, out, _result = self.file(
            "instructions: tag:urgent\nfile-under: somewhere\n\nThe real thought.\n")
        fm = _frontmatter(out.path)
        self.assertNotIn("instructions", fm,
                         "a card's own text reached the act-step grammar")

    def test_a_why_line_is_kept(self):
        card, out, _ = self.file("why: it decides the next three plans\n\nThe thought.\n")
        self.assertEqual(card["why"], "it decides the next three plans")
        self.assertEqual(_frontmatter(out.path)["why"], "it decides the next three plans")

    def test_a_project_line_is_kept(self):
        card, out, _ = self.file("project: agentm\n\nThe thought.\n")
        self.assertEqual(card["project"], "agentm")
        self.assertEqual(_frontmatter(out.path)["project"], "agentm")

    def test_a_title_tag_routes_an_idea(self):
        card, _out, _ = self.file("A thought.\n", title="[idea] drive as the door")
        self.assertEqual(card["type_hint"], "idea")
        self.assertEqual(card["title"], "drive as the door")

    def test_an_unknown_title_tag_does_not_invent_a_type(self):
        # The tag is matched against the contract's own types by the caller. An
        # unrecognised one is dropped from the title and the contract's default
        # stands, rather than inventing a type from text nobody vouches for.
        card, out, _ = self.file("A thought.\n", title="[zorbulax] a thing")
        self.assertEqual(card["type_hint"], "zorbulax")
        self.assertEqual(card["title"], "a thing")
        self.assertNotEqual(_frontmatter(out.path).get("type"), "zorbulax")

    def test_the_operators_word_overrides_what_the_text_said(self):
        # The review pass's whole job: the operator says what a phone-typed
        # line was actually about, and that beats the card's own guess.
        card, reason = uc.parse_card_text("project: guessed\nwhy: guessed\n\nThe thought.\n",
                                          title="A thought")
        self.assertEqual(reason, "")
        result = uc.FileResult()
        out = uc.file_card(self.vault, card, result=result,
                           why="because the operator said so", project="agentm")
        fm = _frontmatter(out.path)
        self.assertEqual(fm["why"], "because the operator said so")
        self.assertEqual(fm["project"], "agentm")


class TheShapeOfWhatIsAccepted(_Filing):
    def test_an_oversized_card_is_dropped(self):
        card, reason = uc.parse_card_text("x" * (uc.MAX_CARD_BYTES + 1), title="Big")
        self.assertIsNone(card)
        self.assertEqual(reason, "too-large")

    def test_a_long_body_under_the_cap_is_truncated_out_loud(self):
        card, reason = uc.parse_card_text("y" * (uc.MAX_BODY_CHARS + 500), title="Long")
        self.assertEqual(reason, "")
        self.assertLess(len(card["body"]), uc.MAX_BODY_CHARS + 200)
        self.assertIn("truncated", card["body"],
                      "the body lost its tail without saying so")

    def test_an_empty_card_is_dropped(self):
        card, reason = uc.parse_card_text("   \n", title="")
        self.assertIsNone(card)
        self.assertEqual(reason, "empty-body")

    def test_a_card_with_no_text_at_all_is_unreadable_not_a_crash(self):
        card, reason = uc.parse_card_text(None, title="x")
        self.assertIsNone(card)
        self.assertEqual(reason, "unreadable")

    def test_a_title_alone_is_a_card(self):
        card, reason = uc.parse_card_text("", title="A thought worth keeping")
        self.assertEqual(reason, "")
        self.assertEqual(card["title"], "A thought worth keeping")


class WhatARefusalReports(_Filing):
    def test_every_reason_it_can_report_is_a_named_one(self):
        # A free-text reason is one nobody can group and one that can arrive
        # unnoticed. Every refusal this module produces is in the closed list.
        seen = set()
        for text, title in (("x" * (uc.MAX_CARD_BYTES + 1), "Big"), ("  ", ""), (None, "x")):
            _card, reason = uc.parse_card_text(text, title=title)
            seen.add(reason)
        self.assertTrue(seen <= set(uc.DROP_REASONS), seen)

    def test_a_refusal_stores_nothing(self):
        card, reason = uc.parse_card_text("x" * (uc.MAX_CARD_BYTES + 1), title="Big")
        self.assertIsNone(card)
        self.assertEqual(reason, "too-large")
        self.assertEqual(list(self.vault.rglob("*.md")), [],
                         "a refused card left something in the vault")

    def test_a_write_refusal_is_counted_and_is_the_transient_one(self):
        # The cap, or a lock timeout: the one refusal that can come out
        # differently tomorrow, so the card is offered again rather than
        # treated as judged.
        card, _ = uc.parse_card_text("The thought.\n", title="A thought")
        result = uc.FileResult()

        class _Refused:
            success = False
            error = "daily_write_cap reached"

        out = uc.file_card(self.vault, card, result=result,
                           capture_fn=lambda *a, **k: _Refused())
        self.assertIsNone(out)
        self.assertEqual(result.dropped, {"write-refused": 1})
        self.assertIn("write-refused", uc.TRANSIENT_DROP_REASONS)

    def test_one_bad_card_does_not_take_the_batch(self):
        card, _ = uc.parse_card_text("The thought.\n", title="A thought")
        result = uc.FileResult()

        def boom(*_a, **_k):
            raise RuntimeError("the writer choked")

        self.assertIsNone(uc.file_card(self.vault, card, result=result, capture_fn=boom))
        self.assertEqual(result.dropped, {"write-refused": 1})
        # And the next one still files.
        self.assertTrue(uc.file_card(self.vault, card, result=result).success)
        self.assertEqual(len(result.filed), 1)

    def test_the_summary_reads_as_a_sentence(self):
        result = uc.FileResult()
        self.assertIn("nothing offered", result.summary())
        result.offered = 2
        result.filed.append(("a-slug", "A thought"))
        result.drop("too-large")
        text = result.summary()
        self.assertIn("1 card(s) of 2 offered", text)
        self.assertIn("1 too-large", text)


class TheDoorsTransportIsGone(unittest.TestCase):
    def test_no_module_opens_a_mailbox(self):
        # The removal, asserted rather than assumed. A grep over the tracked
        # tree, because the failure mode is somebody restoring an old file by
        # copying it back, which the import graph would never notice.
        #
        # It greps for the transport being *used*, not mentioned: `imaplib` as
        # an import or an attribute, an `IMAP4_SSL` construction, and a read of
        # the `Authentication-Results` header. Prose that records the
        # retirement — this file, the CHANGELOG, the module docstring, the wiki
        # — is the opposite of the failure and must not trip it.
        import subprocess
        repo = _HERE.parent
        used = _TRANSPORT_IN_USE
        out = subprocess.run(
            # This file is excluded because its own falsification fixtures
            # below carry the literals on purpose; `test_that_grep_can_still_fail`
            # is what keeps that exclusion from hiding a broken pattern.
            ["git", "grep", "-lE", used, "--", "*.py", "*.go", "*.sh", "*.ps1",
             ":!scripts/test_untrusted_card.py"],
            cwd=repo, capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "",
                         "the email transport is back:\n" + out.stdout)

    def test_that_grep_can_still_fail(self):
        # A grep that matches nothing looks identical whether the transport is
        # gone or the pattern is broken. This proves the pattern still fires.
        import re
        used = re.compile(_TRANSPORT_IN_USE)
        for line in ("import imaplib",
                     "conn = imaplib.IMAP4_SSL(host)",
                     'msg.get_all("Authentication-Results")',
                     "msg.get('Authentication-Results')"):
            self.assertTrue(used.search(line), f"the pattern missed {line!r}")
        for line in ("# imaplib retired with the door",
                     "the `Authentication-Results` reader is gone"):
            self.assertIsNone(used.search(line), f"the pattern fired on prose: {line!r}")

    def test_the_mail_keys_are_gone_from_the_config_tool(self):
        import agentm_config
        src = Path(agentm_config.__file__).read_text(encoding="utf-8")
        for key in ("plugins.autonomy.mailbox_url",
                    "plugins.autonomy.mail_own_addresses",
                    "plugins.autonomy.mail_authserv_id",
                    "plugins.autonomy.capture_address"):
            self.assertNotIn(key, src, f"{key} is still settable")
        # The observability digest's own mail keys are a different thing and
        # stay: they send the operator a nightly email, which nothing here
        # touched.
        self.assertIn("plugins.autonomy.email_to", src)
        self.assertIn("plugins.autonomy.email_smtp_url", src)


if __name__ == "__main__":
    unittest.main()
