# How to drop a card in the inbox

> [!NOTE]
> **Status: implemented** — shipped by agentm-vault plan 16 (`tasks/173-the-inbox`).
> **Goal:** Keep something durable from claude.ai, Claude Code's cloud agent, the Gemini Gem or the Claude app on your phone, without any of them gaining write access to the vault.
> **Prereqs:** The vault folder syncing to Google Drive (see [Back the vault with Google Drive](Back-The-Vault-With-Drive)), and the surface's Drive connector allowed to create a file in it.

A chat surface reads the vault and writes in exactly one place: `agent/inbox/`, a folder in your Drive. A card dropped there syncs down to the machine on the next sync, sits untouched by the hourly sweep, is enriched by the night, and waits for you. **Nothing files an inbox card except you, at the review pass.**

There is no credential on any device and no inbound connection to the machine. The authentication is Google's: a Drive folder accepts writes only from your own signed-in account, which is the whole reason this replaced the email door it retired.

## Steps

### 1. Give the surface its instructions

Paste `templates/inbox-card-prompt.md` into each surface that should be able to write:

| Surface | Where |
|---|---|
| claude.ai | Settings → Custom instructions, or a Project's instructions |
| Claude Code's cloud agent | **no instructions field — see below** |
| Gemini Gem | the Gem's **Instructions** |

This is a second paste, separate from the context payload `/memory payload` prints. That one is about reading the vault and goes to every surface; this one is about writing to one folder and goes only to a surface whose Drive connector can create a file there.

The prompt is gated: `scripts/check-card-prompt.py` fails when the field list it teaches and `card_shape.py` disagree, so the paste cannot quietly go stale while the card changes underneath it.

**The cloud agent has no instructions box.** Claude Code's standing-instruction mechanism is a committed file — `CLAUDE.md` or `AGENTS.md` in the repo — and `~/.claude/CLAUDE.md` does not travel to a cloud session. It *can* reach Drive: connectors added on claude.ai are passed into cloud sessions by the cloud host, and that traffic bypasses the environment's network allowlist. It is still not recommended, because a committed `CLAUDE.md` is read by every session on that repo including local ones, where a card should go through `memory_capture` to the daemon instead. See the plan's `handoff.md` for the conditional wording if you want it anyway.

### 2. Allow the connector to create files

On claude.ai this is the Drive connector's **Create file** permission. Approve it once, for the vault folder. A surface that cannot reach the folder is told to say so and show you the card in the conversation instead — a card you can copy is worth more than a claim that something was saved.

### 3. Say what you want kept

In ordinary words. The surface writes one file per card into `Vault/agent/inbox/` — one card, one file — and tells you what it wrote. It does **not** tell you the card has landed, because Drive sync is not instant and the surface cannot see your disk.

### 4. Read the inbox when you feel like it

```bash
python3 harness/skills/memory/scripts/inbox_review.py
```

or ask for it in a session: **`/memory inbox`**. Every card is listed oldest first, with the frontmatter the night gave it — `summary`, `importance_proposed`, `related`; the `why` is the one you or the surface wrote, and no pass overwrites it — and none of them is filed. Everything a card supplies is quoted behind a `| ` gutter and labelled as data: the filename, every frontmatter key and value, and the body. Only the pass's own headings appear without one. A model on a chat surface wrote all of it and it may be quoting a web page.

### 5. File the ones you want, one at a time

```bash
python3 harness/skills/memory/scripts/inbox_review.py --file drive-as-the-write-path.md \
    --type reference --project agentm --why "it reverses the door's premise"
```

The card goes through the write path a capture already takes, to the class directory the filing contract routes **your** type to — not a default the command picked — and leaves the folder only once the write has landed. A card you say nothing about stays where it is. A card the pass cannot parse is reported by name, left in place, and raised again next time; there is no `rejected/` folder, because that moves your own words somewhere you will not look.

## What the night does to a waiting card

The nightly enrichment reaches `agent/inbox/` and takes the **front** of the backlog, ahead of the class cards and the project records, so the card you read in the morning already carries a summary and a reason. Two things follow from that, and both are deliberate:

- **One budget, with a priority order.** The inbox shares the existing enrichment budget rather than getting its own. The inbox is the one folder whose size you do not control — it is fed from a phone — and a second ceiling would have to be kept in step with the first forever.
- **When a night is short, the rest of the backlog waits.** That is the trade. Watch whether the backlog stops catching up; the morning note's enrichment row is where you would see it.

A card that changed in the last five minutes is left alone for the night: it may still be arriving from Drive, and enriching half a card would write the half back over the whole one. It is offered again tomorrow.

## What it is not

- **It is not the rest of the vault.** `agent/inbox/` is the only place a chat surface writes. Everywhere else it reads and never modifies, including with a tool that could.
- **It is not a queue something drains.** The hourly ingest sweep cannot reach it, and the retired `memory/_inbox/` staging directory it must not be confused with is no longer walked at all — [`check-memory-root-shape`](CI-Gates) names a vault that still holds one rather than letting its cards be swept silently.
- **It is not exempt from recall.** A card in the inbox is *dampened*, not walled: it comes back for a query that matches it, below a filed card of equal match. A card you cannot find until you triage it is a card you triage in order to find it, which would make the inbox a queue rather than a place a thought can rest.

## If something does not arrive

- **Check the sync, not the folder.** DriveFS brings the file down when it is ready; the review pass reads what is on disk and does not wait.
- **A `(conflicted copy)` file** means two writers touched one card. `/memory inbox` names any it finds. Read both and keep the one you meant — resolving a conflict is yours.
- **Nothing at all, ever** — the surface's connector may not have file-creation permission for that folder. Re-check step 2.
- **A card reported as "a symbolic link, not a card"** — nothing that arrives over Drive is a symlink, so something local put it there. It is named and never read, and filing it is refused: the pass will not hand an arbitrary file's contents to the write path.

## Related

- [Find your way around the vault](Find-Your-Way-Around-The-Vault) — where `agent/inbox/` sits among the standard children.
- [The AgentMemory context payload](AgentMemory-Context-Payload) — the *other* paste, the one about reading.
- [CI gates](CI-Gates) — `check-card-prompt` and `check-memory-root-shape`, the two that hold this page's promises.
