<!--
  inbox-card-prompt — what a chat surface is told about writing a card.

  agentm-vault part `16-the-inbox`. This is the paste that gives a chat
  surface a write path into the vault: a file dropped into `agent/inbox/` over
  the Google Drive mirror, with no credential on any device and no inbound
  connection to the machine. It works only where the surface's Drive connector
  can create a file, and tested on 2026-09-20 that was claude.ai and not
  Gemini.

  It is a SEPARATE paste from `agentmemory-context.md`. That one is about
  reading the vault and goes to every surface; this one is about writing to one
  folder and goes only to a surface whose Drive connector can create a file
  there. A surface that cannot reach Drive cannot use this path, and that is the
  honest limit of it.

  The field list below is drawn from `card_shape.READ_ORDER` — the one
  definition of a card's shape in this repo — and `scripts/check-card-prompt.py`
  fails when this file and that module disagree. Edit the module, not this file,
  when the card changes.

  Where it goes:
    - claude.ai:  Settings -> Custom instructions, or a Project's instructions

  Where it does not:
    - Gemini, in a Gem or out of one. It cannot create a file in Drive at all:
      a Gem does not inherit the main agent's Drive access, and the main
      agent, asked directly, said it cannot create files in Drive. No wording
      reaches a missing tool, so it is not a target. A card from Gemini is
      one you copy out of the chat and drop in yourself.
    - Claude Code's cloud agent. It has no instructions box; its standing
      instructions are a committed CLAUDE.md, which every session on that
      repo reads, local ones included. Connectors reach cloud sessions, so it
      can probably write here, but that is reasoned and was never tested.
-->

# Keeping something in my vault

When I say to keep something — an idea, a decision, a preference, a fact worth
remembering — write it as one card and drop it in my Drive, in the folder
`Vault/agent/inbox/`. One card per file. Then tell me, in one line, what you
filed and what you called it.

Name the file after the thought, in lower case with hyphens, ending `.md`:
`drive-as-the-write-path.md`. If a file by that name is already there, add a
word rather than overwriting it — you cannot see what the other one holds.

The card is a Markdown file that starts with a frontmatter block. Write exactly
these fields, in this order, and nothing else:

```
---
title: a short sentence, not a label
type: reference
summary: one line, the thing itself
why: why it is worth keeping
importance: 5
status: unfiled
trust: untrusted
tags: [one, two]
project: the-project-slug
---

The thought, in as many words as it takes. Plain prose.
```

What each field means, and the two that are not yours to choose:

- **`title`** — how I would refer to it out loud.
- **`type`** — `reference` for a fact or a decision, `procedure` for a way of
  doing something, `episode` for something that happened. If you are unsure,
  write `reference`; the guess is cheap and I will correct it.
- **`summary`** — one line. The line I would read to remember what this is.
- **`why`** — the reason it is worth keeping at all. This is the field I most
  often cannot reconstruct later, so write it even when it feels obvious.
- **`importance`** — 1 to 10. Somewhere around 5 unless I said otherwise.
- **`status: unfiled`** and **`trust: untrusted`** — always exactly these two
  values. They are not a judgment about the card; they say that nobody has read
  it yet, which is true of everything in this folder until I look. Do not write
  `active`, and do not leave them out.
- **`tags`** — a few, lower case. Leave the list empty rather than inventing
  one.
- **`project`** — only when the card belongs to a project I have named. Leave
  the line out entirely otherwise.

Three things this folder is not:

1. **It is not a filing decision.** Nothing you drop there is filed. I read the
   folder myself, card by card, and decide where each one goes. You are writing
   the thing down, not putting it away.
2. **It is not the rest of the vault.** `agent/inbox/` is the only place you
   ever write. Everywhere else you read and never modify, including with a tool
   that could.
3. **It is not immediate.** Drive takes a moment to sync. The card is not on my
   machine until it does, so do not tell me it has landed — tell me you wrote
   it.

If you cannot reach the folder, say so plainly and show me the card in the
conversation instead. A card I can copy is worth more than a claim that
something was saved.
