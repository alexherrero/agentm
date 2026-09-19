#!/usr/bin/env python3
"""The email door: every chat surface's one write path into the vault.

claude.ai, Claude Desktop and the Gem read the vault and never write it. That
read-only posture is the boundary every other ruling in this design rests on, so
the way to keep something durable from one of them is not to loosen it — it is to
mail a card to an address the vault reads.

A mailbox on an account made for it receives captures. The hourly ingest sweep
polls it, accepts only authenticated mail from the operator's own addresses, and
turns each accepted message into exactly one card: `source: email`, which the
contract's sources table stamps `trust: untrusted`, and `status: unfiled`, which
is what the nightly pass drains. Everything else in the mailbox is dropped and
counted in the morning note.

# This is an inbound path from the internet, and it is written like one

Anyone who learns the address can send to it. Nothing here tries to decide
whether a message is *true* — that is a judgment no screening measurably holds,
and the contract says so outright. What it holds instead is narrower and
checkable:

**Transport is IMAP over TLS, and only that.** `imaplib.IMAP4_SSL` with a
default verifying context. There is no plaintext fallback and no STARTTLS
upgrade path, because a fallback is a downgrade attack with a polite name.

**The sender must be one of the operator's own addresses**, compared on the
parsed address alone — never the display name, which is free text and is the
oldest trick there is.

**Authentication is read from the receiving server's own header, and the pass
must be about the sender the allow-list matched.** Two questions, and the second
is the one that is easy to miss.

`Authentication-Results` is an ordinary header: a sender can put one in the
message they send, saying anything they like. The one that means something is
the one the *final* MTA prepended, which is the topmost of its kind, and it
carries that server's `authserv-id`. So this reads the first such header only
and requires its authserv-id to match the configured one.

Then: the door's allow-list checks the `From:` address, so the only
authentication that answers its question is the one that authenticates `From:`.
That is **DMARC, and only DMARC**. SPF authenticates the envelope sender; DKIM
authenticates the signing domain. Somebody who passes both for their own domain
— trivial, it is their domain — can still put the operator's address in `From:`,
and the server will honestly report `spf=pass dkim=pass` about theirs beside
`dmarc=fail`. So the door reads one verdict: `dmarc=pass` with a `header.from`
aligned to the `From:` domain, from a header that reports each method at most
once.

That shape is the product of three rounds of adversarial review, and the three
rounds are the argument for it. Accepting `dkim=pass`/`spf=pass` with an aligned
property looks equivalent and is not: it gives an attacker two more places to
put a `pass` the door will read, and each round found the next way to put one
there — an unaligned pass, a verdict spliced out of a property value, and a `;`
inside a quoted envelope sender manufacturing a whole section. Every fix was
correct; the class survived each one. Requiring DMARC removes the places instead
of guarding them.

The cost is real and named: a provider that does not report `dmarc=` on
delivered mail cannot be this door's mailbox. Gmail is *assumed* to report it on
every message — the door was written against Gmail, but that assumption was
never measured, and if it is wrong for any message class the door silently
refuses the operator's own mail. Replacing the assumption with a fact is what
the config-time DMARC probe in the close-out is for.

**Nothing in the body becomes an instruction.** `capture()`'s `instructions`
field is the sweep's act-step grammar — a field that can cause something to
happen — and this door never populates it, from the body or from anywhere else.
A mailed card is content. The sweep's own fixed grammar is unchanged and
unreachable from here.

**One card per message, plain text only, with a size cap.** Attachments are not
read, not stored, not decoded. HTML alternatives are skipped in favour of the
plain part; a message with no plain part is dropped.

**A dropped message is counted and never stored.** The count, by reason, goes in
the morning note. Nothing about a rejected message reaches the vault — not its
body, not its subject, not its sender.

**A message is marked read only once its card is written.** The fetch peeks
(`BODY.PEEK[]`); the `\Seen` flag is set afterwards, per message, and only for
one that filed. Fetching with `RFC822` sets the flag implicitly at read time,
which would make every refusal the write path can produce — the daily cap, a
lock timeout, a transient error — the silent, permanent loss of one of the
operator's own cards, since the next poll searches `UNSEEN` and would never see
it again. Nothing is ever deleted: the mailbox is the operator's, and a door
that deleted what it could not file would be destroying the only copy of
something it had just decided not to keep.

**The credential is never read by anything but the config tool, by name.** It is
`plugins.autonomy.mailbox_url` in the engine config, beside the SMTP one, and
this module reads exactly that key and never prints, logs or echoes it. Every
error message here names the key, never the value.

Usage:
  python3 mail_door.py --dry-run          # poll and report, write nothing
  python3 mail_door.py                    # poll, file accepted mail
The ingest sweep calls `poll_mailbox()` directly; the CLI is for a hand check.
"""
from __future__ import annotations

import argparse
import email
import email.policy
import imaplib
import json
import os
import re
import ssl
import sys
from dataclasses import dataclass, field
from email.utils import getaddresses, parseaddr
from pathlib import Path
from urllib.parse import unquote, urlparse

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# The engine-config keys this door reads. Named as constants so the one that
# holds a secret is greppable and its value never is.
MAILBOX_URL_KEY = "plugins.autonomy.mailbox_url"
CAPTURE_ADDRESS_KEY = "plugins.autonomy.capture_address"
OWN_ADDRESSES_KEY = "plugins.autonomy.mail_own_addresses"
AUTHSERV_ID_KEY = "plugins.autonomy.mail_authserv_id"

# A mailed card is a card, not an article. Past this the message is something
# else — a forward, a newsletter, a reply chain — and the door is not the place
# to decide what to do with it.
MAX_MESSAGE_BYTES = 64 * 1024
MAX_BODY_CHARS = 8_000

# A subject may tag the card's type: `[fix] the thing that broke`. The tag is
# matched against the contract's own types by the caller; an unrecognised tag is
# dropped from the subject and the contract's default type stands, rather than
# inventing a type from a stranger's subject line.
_SUBJECT_TAG = re.compile(r"^\s*\[([a-z][a-z-]{0,30})\]\s*(.*)$", re.I)

# `project: <slug>` on its own line, anywhere in the body. Kebab only: this ends
# up as a frontmatter value and as a directory name in some readers.
_PROJECT_LINE = re.compile(r"^\s*project\s*:\s*([a-z0-9][a-z0-9-]{0,63})\s*$", re.I | re.M)
# `why: <one line>` — the sender's reason for keeping it, which the card keeps.
_WHY_LINE = re.compile(r"^\s*why\s*:\s*(\S.*)$", re.I | re.M)

# Every reason a message can be refused. Named rather than free-text so the
# morning note's count is groupable and a new reason cannot arrive unnoticed.
DROP_REASONS = (
    "door-not-configured",
    "not-from-an-own-address",
    "no-authentication-results-from-our-server",
    "authserv-id-mismatch",
    "no-dmarc-verdict",
    "authentication-header-is-ambiguous",
    "authentication-did-not-pass",
    "too-large",
    "no-plain-text-part",
    "empty-body",
    "unparseable",
    "write-refused",
)

# The one reason that can come out differently tomorrow. Everything else above
# is a property of the message and will still be true on the hundredth poll.
#
# The distinction decides whether a message is marked read. A refusal the vault
# might not repeat — the daily cap, a lock timeout, a transient error — has to
# stay UNSEEN so the next poll offers it again; that is the whole reason the
# fetch peeks. A permanent refusal marked UNSEEN is re-downloaded every hour
# forever, which is an unbounded workload any sender can plant by mailing junk
# once, and it pins the morning note's dropped count non-zero for good — which
# would destroy the one re-audit this door was given.
TRANSIENT_DROP_REASONS = frozenset({
    "write-refused",
    # Four reasons that are not about the message.
    #
    # `door-not-configured` is the door's own state. The other two are the
    # provider's: whether it reports a DMARC verdict at all, and whether the
    # header it writes is readable. A message refused for one of those is a
    # message this door cannot currently judge — and marking it read would
    # delete one of the operator's own cards over a configuration they can fix,
    # with nothing left to say what was lost, because a drop stores nothing by
    # design.
    #
    # This is round 3's lesson applied to the reasons round 4 introduced. The
    # rule it comes from: a refusal about the door or the provider is never
    # permanent; only a refusal about the message is.
    "door-not-configured",
    "authserv-id-mismatch",
    "no-dmarc-verdict",
    "authentication-header-is-ambiguous",
})

# `not-from-an-own-address` stays permanent, and the asymmetry is deliberate.
# A typo in `mail_own_addresses` destroys the operator's own mail the same way a
# typo'd authserv-id did — but making it transient re-creates the unbounded
# re-fetch this door already had once, where junk from a stranger is downloaded
# every hour forever. The difference that settles it: the allow-list holds
# values the operator authored and can read back, where the authserv-id is a
# value the provider chose and they can only learn by inspecting a header. The
# how-to's `--dry-run` step, which marks nothing, is where a typo is meant to
# be caught.


@dataclass
class MailResult:
    """What one poll did. Counts only, for anything refused."""

    accepted: list = field(default_factory=list)   # [(slug, subject)]
    dropped: dict = field(default_factory=dict)    # reason -> count
    polled: int = 0
    error: "str | None" = None
    # Addresses that failed the allow-list, filled on a dry run only. Empty on
    # every production poll: a refused message stores nothing, and this exists
    # so the operator's own hand-check can tell them which address arrived.
    unmatched_senders: list = field(default_factory=list)

    def drop(self, reason: str) -> None:
        self.dropped[reason] = self.dropped.get(reason, 0) + 1

    @property
    def dropped_total(self) -> int:
        return sum(self.dropped.values())

    def summary(self) -> str:
        if self.error:
            return f"mail door: {self.error}"
        if not self.polled:
            return "mail door: nothing new"
        parts = [f"{len(self.accepted)} card(s) from {self.polled} message(s)"]
        if self.dropped:
            parts.append(", ".join(f"{n} {r}" for r, n in sorted(self.dropped.items())))
        return "mail door: " + "; ".join(parts)


# ── configuration ────────────────────────────────────────────────────────────

def _install_prefix() -> Path:
    raw = os.environ.get("AGENTM_INSTALL_PREFIX", "").strip()
    return Path(os.path.expanduser(raw)) if raw else Path.home() / ".claude"


def door_config(install_prefix: "Path | None" = None) -> "dict | None":
    """The door's configuration, or None when it is not set up.

    Returns the mailbox URL under a key the caller must not log. Everything
    that reports on this door reports the *other* keys; this one is passed
    straight to the IMAP client and nowhere else.
    """
    prefix = install_prefix or _install_prefix()
    path = prefix / ".agentm-config.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    url = data.get(MAILBOX_URL_KEY)
    if not isinstance(url, str) or not url.strip():
        return None
    own = data.get(OWN_ADDRESSES_KEY)
    if isinstance(own, str):
        own = [a.strip() for a in own.split(",")]
    if not isinstance(own, list):
        own = []
    own = [a.strip().lower() for a in own if isinstance(a, str) and a.strip()]
    if not own:
        # No allow-list is not "accept everything" — it is "not configured".
        # A door with no allow-list would accept mail from anyone who learned
        # the address, which is the one thing this door must never do.
        return None
    authserv = (data.get(AUTHSERV_ID_KEY) or "").strip().lower()
    if not authserv:
        # Shut, exactly as an empty allow-list shuts it, and for a sharper
        # reason. Without an authserv-id nothing can tell our server's
        # `Authentication-Results` from one a sender wrote, so every message is
        # refused — and a refusal for a reason that is about *the door* rather
        # than about the message is one `_mark_seen` would read as permanent and
        # consume the operator's own mail over. Four independent setters means
        # the half-configured window is an ordinary sequence, not a mistake.
        return None
    return {
        "url": url.strip(),
        "own_addresses": own,
        "authserv_id": authserv,
    }


# ── acceptance ───────────────────────────────────────────────────────────────

def sender_is_own(msg, own_addresses) -> bool:
    """The parsed `From:` address is one of the operator's.

    The address only. A display name is free text — `From: alex
    <attacker@example.com>` is a real message anybody can send — and comparing
    it would be comparing the part the sender chose.
    """
    _name, addr = parseaddr(msg.get("From", ""))
    return addr.strip().lower() in set(own_addresses)


def _domain_of(address: str) -> str:
    """The domain half of an address, lowercased. Empty when there is not one."""
    _name, addr = parseaddr(address or "")
    _, _, domain = addr.partition("@")
    return domain.strip().strip(">").lower()


def _aligned(candidate: str, from_domain: str) -> bool:
    """Whether an authenticated identifier belongs to the `From:` domain.

    Exact match, or a subdomain of it — DMARC's relaxed alignment. `candidate`
    may arrive as a bare domain, as an address, or quoted. The subdomain test is
    on the label boundary, so `notexample.com` is not `example.com`'s.
    """
    cand = candidate.strip().strip('"<>').lower()
    if "@" in cand:
        cand = cand.rsplit("@", 1)[1]
    cand = cand.rstrip(".")
    if not cand or not from_domain:
        return False
    return cand == from_domain or cand.endswith("." + from_domain)


def _strip_comments(text: str) -> str:
    """RFC 5322 comments removed, each replaced by a space — **quote-aware**.

    A server copies attacker-influenced text into a comment verbatim, so
    nothing read out of a comment is the server's word.
    `dkim=fail (test mode: dkim=pass not asserted)` is the shape of the hazard —
    an illustration written to make it concrete, not a captured example.

    The quote-awareness is not a refinement; it is the whole correctness of the
    thing, and getting it wrong composed two parsers that disagreed about the
    same bytes. RFC 5321's `qtextSMTP` admits `(`, `)`, `;` and space, so a
    quoted local part is a place an attacker can write parentheses. A
    comment stripper blind to quotes honours them: a `)` in a quoted pvalue
    closes the server's own comment early and hoists the attacker's text to
    depth zero, and a `(` re-opens one and swallows the genuine sections that
    follow. With the envelope sender `") ; dmarc=pass header.from=<theirs> ("@…`
    the server's real `dmarc=fail (p=REJECT)` was *deleted* and the attacker's
    verdict left in its place — which the duplicate-method rule cannot catch,
    because nothing was duplicated.

    So one pass, three states, and the rule that inside one the other's
    delimiters are ordinary characters: a `(` inside a quoted string is a
    parenthesis, and a `"` inside a comment is `ctext`, not a quote.

    Returns `(stripped, readable)`. `readable` is false in two cases, and both
    are about what the *server* wrote rather than about what any verdict says.

    **The header ends inside a comment or a quoted string.** It did not close
    what it opened, and an attacker's unclosed `(` is how the server's own
    verdict gets swallowed rather than contradicted.

    **A comment contained a `"`.** That is the one character that lets
    comment-stripping launder text from inside a comment back out to the
    structural level, and the argument is structural rather than empirical. To
    manufacture a section the attacker needs, after their early comment close,
    a `;` *and* whitespace-separated tokens *and* an `=`. Neither space nor `;`
    is `atext`, so the echoed pvalue has to be a quoted string — and its opening
    `"` lands inside the comment. A `;` inside a comment with no `"` anywhere
    means no quoting, so no spaces, so no second token, so no `header.from`, so
    nothing to accept.

    `;` was in this set for one round and came out again, which is worth
    recording because the cost was real and invisible: OpenDKIM's canonical
    comment is `(1024-bit key; unprotected)` and Amavis writes
    `(2048-bit key; secure)`, so tainting on `;` refused every message from a
    Postfix or Amavis mailbox — the operator's own aligned, DMARC-passing cards
    included. Nothing was destroyed, because the reason is transient; the door
    simply never worked, and said so with a name pointing at duplicate DMARC
    verdicts rather than at a semicolon in a key-size comment. Measured over
    3,570 attack headers, `"`-only refuses exactly as many as `"`-or-`;` did:
    all of them.

    That second rule is what makes the guard hold on a provider that reports no
    DMARC. The first rule alone only bites when there is a genuine verdict to
    delete; with nothing to delete, a forged section needs no unclosed comment,
    the header balances, and the door would accept the attacker while refusing
    the operator — shut to them and open to the internet.
    """
    out, depth, in_quote, escaped, tainted = [], 0, False, False, False
    for ch in text:
        if escaped:
            if depth == 0:
                out.append(ch)
            escaped = False
            continue
        if ch == "\\" and (in_quote or depth):
            if depth == 0:
                out.append(ch)
            escaped = True
            continue
        if in_quote:
            out.append(ch)
            if ch == '"':
                in_quote = False
            continue
        if depth:
            if ch == '"':
                # The one character that lets comment-stripping launder text
                # from inside a comment out to the structural level. See the
                # docstring for why `;` is not the second one.
                tainted = True
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    out.append(" ")
            continue
        if ch == '"':
            in_quote = True
            out.append(ch)
            continue
        if ch == "(":
            depth += 1
            continue
        out.append(ch)
    # Readable, or not. Two conditions, both about what the *server* wrote
    # rather than about what any verdict says — see the docstring.
    return "".join(out), depth == 0 and not in_quote and not tainted


def _quoted_split(text: str) -> list:
    """Whitespace-split, outside quotes. A quoted pvalue may hold a space
    (RFC 5321 `qtextSMTP` admits 32), and splitting inside one turns the tail of
    an attacker's address into a token of its own."""
    out, buf, in_quotes, escaped = [], [], False, False
    for ch in text:
        if escaped:
            buf.append(ch)
            escaped = False
            continue
        if ch == "\\" and in_quotes:
            buf.append(ch)
            escaped = True
            continue
        if ch == '"':
            in_quotes = not in_quotes
            buf.append(ch)
            continue
        if ch.isspace() and not in_quotes:
            if buf:
                out.append("".join(buf))
                buf = []
            continue
        buf.append(ch)
    if buf:
        out.append("".join(buf))
    return out


def _split_sections(header: str) -> list:
    """`Authentication-Results` split into its resinfo sections.

    On `;` at quote depth zero only. RFC 8601's `pvalue` may be a
    `quoted-string`, and RFC 5321's `qtextSMTP` admits `;`, `=` and space — so
    `MAIL FROM:<"a;dkim=pass header.d=yours "@theirs>` is a deliverable envelope
    sender. That much is the grammar and is checkable. That a server copies such
    an address into its own header *unescaped* is the other half, and it is an
    assumption — plausible, never observed here, and the reason these guards are
    written to cost an honest header nothing is so that being wrong about it is
    free. Given it, a `str.split(";")` cuts the attacker's address in half and
    hands back a section whose first token is a methodspec they wrote.

    That was a live bypass: the server said `dkim=fail`, `dmarc=fail (p=REJECT)`
    and `spf=pass` for the sender's own domain, and the door accepted the
    message on a `dkim=pass header.d=<operator domain>` that existed only inside
    the envelope sender. Depth-tracking here is the same shape `_strip_comments`
    uses for parens, and for the same reason: the delimiter only delimits
    outside the thing that quotes it.
    """
    out, buf, in_quotes, escaped = [], [], False, False
    for ch in header:
        if escaped:
            buf.append(ch)
            escaped = False
            continue
        if ch == "\\" and in_quotes:
            buf.append(ch)
            escaped = True
            continue
        if ch == '"':
            in_quotes = not in_quotes
            buf.append(ch)
            continue
        if ch == ";" and not in_quotes:
            out.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    out.append("".join(buf))
    return out


def _resinfo(section: str) -> "tuple[str, str, dict]":
    """One `;`-delimited section of `Authentication-Results`, parsed.

    RFC 8601's grammar is `resinfo = ";" [CFWS] methodspec [1*SP propspec]`, so
    the verdict is the section's *first* token and the properties that qualify
    it are the tokens after it, in that same section. Returning
    `(method, result, properties)` is what lets the caller keep them together —
    and keeping them together is the whole point.

    Tokenized on whitespace, with each token split at its *first* `=`. That is
    what makes a value containing `=` inert: `smtp.mailfrom=dkim=pass@…` is one
    token whose name is `smtp.mailfrom` and whose value is `dkim=pass@…`, and
    the value is never scanned for anything.

    First occurrence of a property name wins, not the last. A section that names
    `header.d` twice is malformed, and taking the later one would let an
    attacker append a correction to a server's own words.
    """
    tokens = _quoted_split(section)
    if not tokens:
        return "", "", {}
    head = tokens[0]
    # `methodspec = [version "/"] method "=" result`.
    method, _, result = head.partition("=")
    method = method.split("/", 1)[0].strip().lower()
    props: dict = {}
    for tok in tokens[1:]:
        name, sep, value = tok.partition("=")
        if not sep:
            continue
        name = name.strip().lower()
        if name and name not in props:
            props[name] = value
    return method, result.strip().lower(), props


def authentication_passed(msg, authserv_id) -> "tuple[bool, str]":
    """`(passed, reason)` from the receiving server's own `Authentication-Results`.

    Three questions, and each of the last two was got wrong once before the
    security review this task required.

    **Whose header is this?** The header is ordinary: a sender may include one
    asserting anything. What makes one trustworthy is that our own final MTA
    prepended it, and a prepended header is the *first* of its name. So only
    `headers[0]` is read, and it must carry the configured `authserv-id`.
    Without one configured there is nothing to tell our server's header from a
    forged one, so the door refuses rather than trusting the first it sees.

    **What did it authenticate, and about whom?** SPF authenticates the envelope
    `MAIL FROM`; DKIM authenticates the signing domain in `header.d`. *Neither is
    the `From:` header.* An attacker who passes both for their own domain —
    trivial, it is their domain — can put the operator's address in `From:`, and
    our server will honestly report `spf=pass dkim=pass` about theirs beside
    `dmarc=fail`. So a pass must be **aligned**: the identifier that
    authenticated has to belong to the domain of the `From:` address the
    allow-list matched.

    **Which property belongs to which verdict?** A header has several `;`
    sections and a domain can be signed twice. Flattening the header into
    `{method: result}` and `{property: value}` lets a `pass` from one section be
    paired with an aligned `header.d` from another — `dkim=fail header.d=<yours>;
    dkim=pass header.i=@<theirs>` is two ordinary signatures and needs no
    forgery at all. So each section is evaluated whole, as `_resinfo` parses it,
    and a verdict is only ever read beside the properties in its own section.

    A `dmarc=pass` with no `header.from` is refused rather than accepted: the
    absent property is the entire claim, and treating absence as alignment was
    the fail-open in the second version of this function.
    """
    if not authserv_id:
        # A property of the door, not of the message. `door_config` refuses to
        # build a config without one, so this is only reachable through a
        # hand-built config — and it must still be transient, or the door marks
        # the operator's own genuine mail read and destroys it.
        return False, "door-not-configured"
    headers = msg.get_all("Authentication-Results") or []
    if not headers:
        return False, "no-authentication-results-from-our-server"
    first = str(headers[0])
    clean, well_formed = _strip_comments(first)
    if not well_formed:
        # The header ends inside a comment or a quoted string, and that is the
        # tell for the sharpest attack on this function.
        #
        # Being RFC-correct about comments is not the same as being safe about
        # them. An unescaped `)` really does close a comment, and a server
        # echoes the envelope sender into its own comments without escaping —
        # so the sender chooses where the server's comments begin and end. With
        # `") ; dmarc=pass header.from=<theirs> ((("@…` the first echo's `)`
        # closes the server's comment early and hoists a forged verdict to
        # depth zero, and the three `(` hold depth above zero through the
        # server's *genuine* `dmarc=fail (p=REJECT)` and off the end of the
        # header — so the real verdict is not contradicted, it is deleted. The
        # duplicate-DMARC rule cannot see that: there is nothing left to
        # duplicate.
        #
        # What that attack cannot do is balance itself: the server writes its
        # sender echo before its DMARC section, so swallowing that section
        # means opening a comment nothing closes.
        #
        # But balance is only half the guard, and on its own it is the half
        # that holds against the wrong provider. Where the server reports no
        # DMARC at all there is nothing to delete, so a forged section needs no
        # unclosed comment and the header balances — the door would accept the
        # attacker while refusing the operator's own mail. The second rule, no
        # `"` or `;` inside a comment, is what closes that: it refuses the
        # laundering primitive rather than one of its outcomes.
        #
        # **Not** a grammar problem, which is worth saying because it is the
        # natural thing to reach for. A full RFC 8601 tokenizer would not have
        # caught either attack: the forged section is grammatically perfect —
        # `;`, methodspec, propspec, all well formed — and a perfect parser run
        # after comment-stripping sees a perfect section. The defect is that
        # stripping promotes what the server put inside a comment to the
        # structural level, and the fix has to be there.
        #
        # The follow-up this leaves, and it is not the tokenizer: **pin the
        # provider's DMARC behaviour at configuration time.** One poll at setup,
        # asserting the header from the configured authserv-id carries a
        # `dmarc=` section, and refusing to complete setup otherwise. Today it
        # is belt-and-braces. It becomes load-bearing the moment the argument
        # above is wrong about any provider — that argument rests on "an echoed
        # pvalue carrying a space must have been quoted", and a server that ever
        # echoes an unquoted value with spaces in it (a malformed HELO, a
        # free-text `reason=`) would slip the `"` rule. Pinning removes the
        # branch instead of reasoning about it: it turns "does this provider
        # report DMARC?" from a per-message inference an attacker can supply the
        # answer to into a setup-time fact.
        return False, "authentication-header-is-ambiguous"
    parts = _split_sections(clean)
    # `authserv-id` is the first token of the header, before any resinfo.
    served_by = parts[0].strip().split()[0].strip().lower() if parts[0].strip() else ""
    if served_by != authserv_id:
        # Its own reason, and transient, because this compares the header
        # against a value the operator typed. They can only learn the right one
        # by reading a header they received, the setter validates nothing, and
        # `google.com` for `mx.google.com` is exactly the slip somebody makes —
        # at which point every card they mail is refused. Permanent, that
        # marked each one read and destroyed it, which is the failure three
        # other reasons were already moved to transient to prevent.
        return False, "authserv-id-mismatch"

    from_domain = _domain_of(msg.get("From", ""))
    if not from_domain:
        return False, "authentication-did-not-pass"

    # One verdict is read, and it is DMARC's.
    #
    # This door's allow-list checks the `From:` address, so the only
    # authentication that answers the door's own question is the one that
    # authenticates `From:` — and that is DMARC alone. SPF authenticates the
    # envelope sender and DKIM authenticates the signing domain; accepting
    # either as a fallback means accepting a verdict about a different identity
    # than the one that was checked, and closing that gap by requiring
    # *alignment* is a second mechanism to get right on top of the first.
    #
    # Three separate bypasses lived in those two fallback paths across three
    # rounds of review: an unaligned pass, a verdict spliced out of a property
    # value, and a `;` inside a pvalue manufacturing a whole section. Each fix
    # was correct and the next round found the next variant, because the fallbacks
    # gave an attacker two more places to put a `pass` that the door would read.
    # Requiring DMARC removes the places rather than guarding them.
    #
    # The cost is named rather than hidden: a mail provider that does not report
    # `dmarc=` on delivered mail cannot be used as this door's mailbox. Gmail
    # is assumed to report it on every message; the door was written against it,
    # and that assumption is what the config-time probe replaces.
    #
    # Two DMARC verdicts make the header ambiguous: a server computes one, so a
    # second is either a forged section or a header nobody can read, and nothing
    # in the bytes tells them apart. Refused either way.
    #
    # `dmarc` alone, and not every method. A message signed twice is ordinary —
    # RFC 8463 recommends exactly that for an RSA-to-Ed25519 key rollover, and
    # any list or gateway that re-signs adds one — so a rule against duplicates
    # in general refuses the operator's own perfectly aligned card. Since the
    # only verdict read here is DMARC's, a second `dkim=` section is not
    # ambiguity, it is a second signature, and it changes nothing.
    dmarc: "tuple[str, dict] | None" = None
    for section in parts[1:]:
        method, result, props = _resinfo(section)
        if method != "dmarc":
            continue
        if dmarc is not None:
            return False, "authentication-header-is-ambiguous"
        dmarc = (result, props)

    if dmarc is None:
        return False, "no-dmarc-verdict"
    result, props = dmarc
    if result != "pass":
        return False, "authentication-did-not-pass"
    stated = props.get("header.from")
    if stated and _aligned(stated, from_domain):
        return True, ""
    return False, "authentication-did-not-pass"


def plain_text_body(msg) -> "str | None":
    """The message's plain-text part, or None.

    Attachments are never read: `get_body` is asked for `plain` only, and a
    part with a filename is not a body. An HTML-only message is dropped rather
    than stripped — converting markup to text here would be parsing a
    stranger's document with a parser nobody has audited for it.
    """
    try:
        part = msg.get_body(preferencelist=("plain",))
    except Exception:  # noqa: BLE001 — a malformed message is a drop, not a crash
        return None
    if part is None:
        return None
    try:
        text = part.get_content()
    except Exception:  # noqa: BLE001
        return None
    return text if isinstance(text, str) else None


def parse_card(msg) -> "tuple[dict | None, str]":
    """`(card, "")` or `(None, reason)` for one accepted message.

    A card is: a title from the subject, a type from its `[tag]` when it has
    one, the body as content, a `why:` line if the sender wrote one, and a
    `project:` line if they named one. Nothing else in the message is read.
    """
    subject = str(msg.get("Subject", "")).strip()
    type_hint = None
    m = _SUBJECT_TAG.match(subject)
    if m:
        type_hint, subject = m.group(1).lower(), m.group(2).strip()
    body = plain_text_body(msg)
    if body is None:
        return None, "no-plain-text-part"
    body = body.strip()
    if not body and not subject:
        return None, "empty-body"
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS].rstrip() + "\n\n[truncated at the door's size cap]"
    why = None
    mw = _WHY_LINE.search(body)
    if mw:
        why = mw.group(1).strip()[:300]
    project = None
    mp = _PROJECT_LINE.search(body)
    if mp:
        project = mp.group(1).lower()
    title = subject or body.splitlines()[0][:120]
    return {
        "title": title,
        "type_hint": type_hint,
        "body": body,
        "why": why,
        "project": project,
    }, ""


# ── the poll ─────────────────────────────────────────────────────────────────

def _connect(url: str):
    """An IMAP-over-TLS connection. No plaintext, no STARTTLS upgrade.

    A downgrade path is the thing an attacker asks for, so there is not one: a
    URL that does not name `imaps` is refused here rather than honoured.
    """
    parsed = urlparse(url)
    if parsed.scheme != "imaps":
        raise ValueError(
            f"{MAILBOX_URL_KEY} must be an imaps:// URL — this door does not "
            "speak plaintext IMAP and does not upgrade")
    if not parsed.hostname:
        raise ValueError(f"{MAILBOX_URL_KEY} names no host")
    context = ssl.create_default_context()
    conn = imaplib.IMAP4_SSL(parsed.hostname, parsed.port or 993, ssl_context=context)
    user = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    if not user or not password:
        raise ValueError(f"{MAILBOX_URL_KEY} carries no credentials")
    conn.login(user, password)
    return conn, (parsed.path.lstrip("/") or "INBOX")


def poll_mailbox(vault_path, *, config=None, dry_run: bool = False,
                 client=None, capture_fn=None) -> MailResult:
    """Poll the door once and file what it accepts.

    `client` and `capture_fn` are the seams the fixture mailbox uses. `client`
    is an iterable of `(uid, raw_bytes)` — the uid is what a real mailbox needs
    to mark one message read and leave another alone — and a bare iterable of
    bytes is accepted too, for a caller that does not care. A real poll runs the
    same acceptance path over a real connection, so the tests exercise the rules
    rather than a re-implementation of them.
    """
    result = MailResult()
    cfg = config if config is not None else door_config()
    if cfg is None:
        result.error = (
            f"not configured — set {MAILBOX_URL_KEY} and {OWN_ADDRESSES_KEY} "
            "with `agentm_config`; the door stays shut until both are set")
        return result

    own = cfg["own_addresses"]
    authserv = cfg.get("authserv_id")
    conn = None
    # Every uid this pass is finished with: filed, or refused for a reason that
    # will not change. The transient refusals are the ones deliberately left
    # out, so the next poll sees them again.
    done_uids: list = []

    if client is None:
        try:
            conn, folder = _connect(cfg["url"])
        except Exception as exc:  # noqa: BLE001 — never raise into the sweep
            # The class, not the message: an IMAP error can quote the URL it
            # was given, and that URL carries the password.
            result.error = f"could not open the mailbox ({type(exc).__name__})"
            return result
        try:
            messages = list(_fetch_unseen(conn, folder))
        except Exception as exc:  # noqa: BLE001
            result.error = f"could not read the mailbox ({type(exc).__name__})"
            _logout(conn)
            return result
    else:
        messages = [m if isinstance(m, tuple) else (None, m) for m in client]

    if capture_fn is None:
        import capture as capture_mod  # same skill dir
        capture_fn = capture_mod.capture

    def refuse(reason: str, uid) -> None:
        """Count a refusal, and decide whether the message is finished with."""
        result.drop(reason)
        if uid is not None and reason not in TRANSIENT_DROP_REASONS:
            done_uids.append(uid)

    try:
        for uid, raw in messages:
            result.polled += 1
            if len(raw) > MAX_MESSAGE_BYTES:
                refuse("too-large", uid)
                continue
            try:
                msg = email.message_from_bytes(raw, policy=email.policy.default)
            except Exception:  # noqa: BLE001
                refuse("unparseable", uid)
                continue
            if not sender_is_own(msg, own):
                refuse("not-from-an-own-address", uid)
                if dry_run:
                    # The one place a refused message's sender is reported, and
                    # only here: operator-initiated, writing nothing, marking
                    # nothing.
                    #
                    # The production path counts and stores nothing, which is
                    # invariant 5 and stays. But that makes the count unusable
                    # for the thing it is supposed to help with: `1
                    # not-from-an-own-address` does not say *which* address, and
                    # the cases that actually bite are not typos — a client
                    # sending as a plus-address, an alias nobody remembered, a
                    # list rewriting `From:`. The operator authored the list
                    # correctly and it still does not match what arrives, and a
                    # hand-check that cannot say what arrived cannot resolve it.
                    _name, addr = parseaddr(msg.get("From", ""))
                    result.unmatched_senders.append(addr or "(unparseable)")
                continue
            ok, why_not = authentication_passed(msg, authserv)
            if not ok:
                refuse(why_not, uid)
                continue
            card, why_not = parse_card(msg)
            if card is None:
                refuse(why_not, uid)
                continue
            if dry_run:
                result.accepted.append(("(dry-run)", card["title"]))
                continue

            content = card["title"] + "\n\n" + card["body"]
            # `instructions` is deliberately absent, and its absence is the
            # security property: it is the sweep's act-step grammar, and a
            # mailed body must never reach a field that can cause something to
            # happen. `transport="email"` is what makes the contract stamp
            # `trust: untrusted` — the door does not decide its own trust level.
            #
            # Wrapped per message so one card the writer chokes on cannot take
            # the batch with it: an exception `capture()` does not catch would
            # otherwise unwind this loop into the sweep's catch-all, and every
            # message behind it would be lost with a class name for an
            # explanation.
            try:
                out = capture_fn(
                    vault_path, content,
                    kind="idea" if card["type_hint"] == "idea" else "capture",
                    source="email-door",
                    surface="email",
                    transport="email",
                    why=card["why"],
                    project=card["project"],
                )
            except Exception:  # noqa: BLE001 — one bad card, not a lost batch
                refuse("write-refused", uid)
                continue
            if getattr(out, "success", False):
                result.accepted.append((getattr(out, "slug", "?"), card["title"]))
                if uid is not None:
                    done_uids.append(uid)
            else:
                # The cap, or a write failure. The one refusal that stays
                # UNREAD, so tomorrow's poll sees it again — which is only true
                # because the fetch peeked and `_mark_seen` is the only thing
                # that sets the flag. Fetching with `RFC822` sets it at read
                # time, and then every refusal the write path can produce is the
                # silent, permanent loss of one of the operator's own cards.
                refuse("write-refused", uid)
    finally:
        if conn is not None:
            # Not under `--dry-run`. Every refusal is counted before the
            # dry-run check — deliberately, so a dry run reports what a real one
            # would refuse — which means `done_uids` fills on a dry run too, and
            # marking from it would make a command documented as "write nothing"
            # consume the mailbox.
            if not dry_run:
                _mark_seen(conn, done_uids)
            _logout(conn)
    return result


def _logout(conn) -> None:
    """Close a connection without letting its failure become the result."""
    try:
        conn.logout()
    except Exception:  # noqa: BLE001
        pass


def _fetch_unseen(conn, folder: str):
    """`(uid, raw)` for every unseen message in `folder`, without marking any.

    `BODY.PEEK[]`, not `RFC822`. The difference is the whole retry contract:
    `RFC822` sets `\\Seen` implicitly as it reads, so a message the vault then
    refused — the daily cap, a lock timeout, a transient error — would never be
    offered again, because the next poll searches `UNSEEN`. Peeking leaves the
    flag to `_mark_seen`, which sets it per message and only for one that filed.

    Read-only on the mailbox otherwise. Nothing is ever deleted: the mailbox is
    the operator's, and a door that deleted what it could not file would be
    destroying the only copy of something it had just decided not to keep.
    """
    conn.select(folder)
    typ, data = conn.uid("SEARCH", None, "UNSEEN")
    if typ != "OK":
        return
    for uid in (data[0] or b"").split():
        typ, payload = conn.uid("FETCH", uid, "(BODY.PEEK[])")
        if typ != "OK" or not payload or not payload[0]:
            continue
        raw = payload[0][1]
        if isinstance(raw, (bytes, bytearray)):
            yield uid, bytes(raw)


def _mark_seen(conn, uids) -> None:
    """Mark exactly the messages this pass is finished with.

    Filed, or refused for a reason that will still be true tomorrow. The one
    reason held back is `write-refused`, which the vault might not repeat.

    Best-effort and last: a mailbox that refuses the flag leaves a message to be
    offered again, which files a duplicate card — recoverable, and the right
    direction to fail when the alternative is losing one.
    """
    for uid in uids or ():
        try:
            conn.uid("STORE", uid, "+FLAGS", "(\\Seen)")
        except Exception:  # noqa: BLE001
            pass


def _resolve_vault(cli_arg: "str | None") -> "Path | None":
    """The memory root, from the flag or the environment.

    Not through `harness_memory`: a skill script importing it is a back-edge
    the one-way-imports gate refuses, and the sweep — this module's only
    production caller — passes the vault in anyway. This resolver exists for
    the hand-run CLI alone, and mirrors `ingest_sweep._resolve_vault`.
    """
    if cli_arg:
        p = Path(cli_arg)
        return p if p.is_dir() else None
    env = (os.environ.get("MEMORY_ROOT") or os.environ.get("MEMORY_VAULT_PATH", "")).strip()
    if env:
        p = Path(env).expanduser()
        return p if p.is_dir() else None
    return None


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    vault = _resolve_vault(args.vault)
    if vault is None:
        print("mail-door: no vault resolves — pass --vault or set $MEMORY_ROOT",
              file=sys.stderr)
        return 2
    result = poll_mailbox(vault, dry_run=args.dry_run)
    print(result.summary())
    for slug, subject in result.accepted:
        print(f"  filed {slug}: {subject[:70]}")
    for addr in result.unmatched_senders:
        print(f"  not on the allow-list: {addr}")
    return 1 if result.error else 0


if __name__ == "__main__":
    raise SystemExit(main())
