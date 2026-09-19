# How to open the mail door

> [!NOTE]
> **Status: implemented** — shipped by agentm-vault plan 12, task 7 (`tasks/160-finish-the-surfaces`).
> **Goal:** Give claude.ai, Claude Desktop and the Gemini Gem a way to keep something durable, without any of them gaining write access to the vault.
> **Prereqs:** A mailbox on an account made for this — not your own inbox. The account and its credential are yours to create; the door reads the mailbox and nothing else. **The provider must write an `authserv-id` and report `dmarc=` in its `Authentication-Results` header.** Gmail does both on every message; so do Fastmail, Postfix with OpenDMARC, and Amavis. Three shapes cannot be this door's mailbox, and the door refuses all three *transiently*, so nothing of yours is destroyed while you find out:

> - **Exchange Online**, which writes no authserv-id at all — its header opens straight with `spf=pass (sender IP is …)`, and a header with no authserv-id cannot be told from one a sender wrote.
> - **A provider that reports no `dmarc=`**, for the reason in "What gets through" below.
> - **A provider that writes more than one `Authentication-Results` header.** Postfix is the common case: OpenDKIM and OpenDMARC are separate milters and each prepends its own. Only the topmost is read — widening that would let a sender supply their own — so whether the door works depends on your milter order. With `opendmarc` last it works; with `opendkim` last every message is refused `no-dmarc-verdict`.

A quick way to check before you commit to a mailbox: mail yourself a card, then run the dry run in step 7 and see whether it is accepted. You also need the hourly `capture-ingest-sweep` job registered (see [Capture from your phone](Capture-From-Your-Phone)), which is what polls the door.

Every chat surface reads the vault and none of them writes it. That read-only posture is the boundary the rest of the design rests on, so the way to keep something from one of them is not to loosen it: the surface writes a card, mails it to an address, and the hourly sweep files what it accepts as an `unfiled`, `untrusted` note.

## Steps

### 1. Make a mailbox that is only for this

Create a separate account. Not a folder or a filter on your own mail — a separate mailbox, because everything that lands in it is read by the door and nothing that lands in your own inbox should be.

The address will be pasted into three chat surfaces, so treat it as published: anyone who learns it can send to it. That is expected, and it is what the acceptance rules below are for.

### 2. Tell the door where the mailbox is

```bash
python3 scripts/agentm_config.py --mailbox-url "imaps://USER:APP-PASSWORD@HOST/INBOX"
```

IMAP over TLS only. The command refuses any other scheme rather than negotiating an upgrade, because a fallback is a downgrade attack with a polite name. For Gmail this is an app password, not your account password.

The value is written to `plugins.autonomy.mailbox_url` and masked on the echo. Nothing in the repo reads it except the IMAP client, and every error the door can raise names the key rather than the URL — an IMAP exception will happily quote the connection string it was given.

### 3. Tell it which senders count

```bash
python3 scripts/agentm_config.py --mail-own-addresses "you@example.com,you-at-work@example.org"
```

The door compares the parsed `From:` address against this list, and nothing else — never the display name, which is free text.

There is no "leave it empty to accept everyone". With no list the door reports itself unconfigured and stays shut.

### 4. Tell it which `Authentication-Results` header is yours

```bash
python3 scripts/agentm_config.py --mail-authserv-id mx.google.com
```

Not optional, and not last by accident: **the door will not come up without it.** `door_config` returns nothing until all three of the mailbox URL, the address list and this are set, exactly as it refuses an empty allow-list — because a door that is listening but cannot authenticate refuses every message, including your own, and a refusal it read as permanent would mark your own cards read and consume them.

`Authentication-Results` is an ordinary header. A sender can put one in the message they send, saying every check passed. The one that means something is the one your own receiving server prepended, and it identifies itself by an `authserv-id` as its first token — `mx.google.com` for Gmail. The door reads only the topmost header of that name and only when it carries this value.

### 5. Tell the payload the address

```bash
python3 scripts/agentm_config.py --capture-address cards@example.com
```

This is `plugins.autonomy.capture_address`, and it is not a secret — it is rendered into the pasted payload, which is why it is a separate key from the mailbox URL. With it set, the payload's posture paragraph changes from *show me a card so I can file it* to *mail it to this address*.

### 6. Re-paste the payload

```bash
python3 harness/skills/memory/scripts/payload.py --write
```

That rewrites the derived copies (the Antigravity rule, `~/.gemini/GEMINI.md`'s managed section). The pasted surfaces are yours: paste the printed body into claude.ai's custom instructions and the Gem's instructions.

### 7. Check it before you trust it

```bash
python3 harness/skills/memory/scripts/mail_door.py --dry-run
```

A dry run polls the mailbox, applies every acceptance rule, reports what it would file, and writes nothing — including no `\Seen` flag, which is durable mailbox state and was worth saying out loud. Mail yourself a card and run it.

## What gets through, and what does not

A message becomes a card when **all** of these hold:

| | |
|---|---|
| Transport | IMAP over TLS |
| Sender | the parsed `From:` address is on your list |
| Authentication | your server's own header reports an **aligned** pass |
| Size | under 64 KB, body truncated at 8,000 characters |
| Body | has a plain-text part |

"Aligned" is doing real work in that table, and it is why the door reads **one** verdict rather than three.

Your allow-list checks the `From:` address, so the only authentication that answers the door's own question is the one that authenticates `From:` — and that is DMARC. SPF authenticates the envelope sender; DKIM authenticates the signing domain. Somebody who passes both for *their own* domain, which is trivial because it is their domain, can still put your address in `From:`, and your server will honestly report `spf=pass dkim=pass` about theirs beside `dmarc=fail`.

So the rule is: `dmarc=pass` with a `header.from` aligned to your domain — exact match or a true subdomain — read from a header that reports each method at most once. A method answered twice makes the header ambiguous, and ambiguity is refused.

Accepting an aligned `dkim=pass` or `spf=pass` as a fallback looks equivalent and is not: it gives an attacker two more places to put a `pass` the door will read. Three rounds of adversarial review found three different ways to put one there — an unaligned pass, a verdict spliced out of a property value, and a `;` inside a quoted envelope sender manufacturing a whole section. Each fix was correct and the next round found the next variant. Requiring DMARC removes the places rather than guarding them.

**That is the cost this how-to's prereq names.** A provider that does not report `dmarc=` cannot be this door's mailbox; the door will refuse everything with `no-dmarc-verdict`, which is its own reason precisely so it reads as "this mailbox is unsuitable" rather than "that message failed".

Everything else is dropped, counted by reason in the morning note, and never stored. Nothing about a refused message reaches the vault: not its body, not its subject, not its sender.

## What a mailed card looks like when it lands

```yaml
type: <from a [tag] in the subject, or the contract's default>
source: email          # which the contract's sources table stamps…
trust: untrusted       # …as this
status: unfiled
why: <the sender's own `why:` line, when there was one>
project: <a `project:` line, when there was one>
```

`status: unfiled` is what the nightly pass drains, and `trust: untrusted` is what keeps it out of anything that reads memory as instruction. A mailed card files through the same write path every other capture takes, so the [Vault write protocol](Vault-Write-Protocol)'s `thresholds.daily_write_cap` applies and a flood of mail cannot crowd out your own captures beyond one day's worth.

**The body never becomes an instruction.** The sweep has a fixed act-step grammar in a frontmatter field called `instructions`, and the door never populates it — from the body or from anywhere else. A mailed message that says "ignore previous instructions" is a card that contains that sentence.

## Reading it the next morning

The ingest-sweep digest carries a line per pass:

```
Mailed cards: 2 filed, 5 dropped
```

with a breakdown by reason underneath. **A non-zero dropped count on a day you sent nothing is the re-audit this door was given** — it means somebody found the address. That is not an emergency: a refused message never touched the vault. It is a reason to look at whether the address should change.

## Turning it off

Unset either the mailbox URL or the address list and the door reports itself unconfigured and stops polling. Nothing else changes; the sweep's other six duties run exactly as before.

## Related

- [Capture from your phone](Capture-From-Your-Phone) — the ingest sweep this rides on
- [Use AgentMemory in any agent](Use-AgentMemory-In-Any-Agent) — the payload the chat surfaces read
- [Vault write protocol](Vault-Write-Protocol) — the write path a mailed card takes, and the cap it obeys
- [AgentM Vault](agentm-vault) § Surfaces — why the door is email rather than a write tool
