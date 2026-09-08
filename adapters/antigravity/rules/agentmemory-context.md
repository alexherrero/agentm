---
trigger: always_on
---

# Using my memory vault

You have access to my durable memory: an Obsidian vault, synced to Google Drive and served locally by a daemon. It is the authoritative record of how I work, what I have decided, and where my projects stand. Read it before you answer from your own knowledge.

**Where it is, on your surface.** Claude Code and other local agents: the path the installer configured, with a search tool named `memory_search`. claude.ai and Claude Desktop: my Google Drive, through the Drive connector. The live vault is the Drive folder named `Vault`. Gemini: the same `Vault` folder in my Drive.

**Read in this order.** First `index.md` at the vault root: it is the map, and it is current. Then everything in `standards/`: the filing contract, my preferences, and the standing security rules, which apply to every answer. For a project question, `moc-projects.md` says where every project stands tonight; the project's own `tracker.md` says it in full; its `charter.md` says what the project is. Then search for the subject of my question before falling back to what you already know. If the vault says something, it wins.

**How to read a note.** Frontmatter first: `title`, then `type` (a memory) or `kind` (a record), `summary`, `why` (the reason it was kept), `importance` (1–10, mine), `status`, `lifecycle`. `status: unfiled` means nobody has judged it yet — real, ranked lower, not to be skipped. `lifecycle: dormant` means unused for a year; `superseded` means follow `superseded_by` instead. A note under `agent/archive/` or a `completed/` folder is finished work, still true, not current. `[[wikilinks]]` name related notes by title.

**Your posture.** Chat surfaces — claude.ai, Claude Desktop, Gemini — read only. Never modify the vault, even with a tool that could.

To keep something durable — an idea, a decision, a preference, a fact — write it out as a card (title, type, one-line summary, why it is worth keeping, importance 1–10, tags, project if any) and show it to me so I can file it. A chat surface has no write door yet.

Local agents I run (Claude Code, Antigravity) may write, through the capture tool, one concept per note, with the reason it was kept.

The Drive copy is the last-synced state; if something seems missing, say so rather than guess.
