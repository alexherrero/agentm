<!-- mode: how-to -->
# How to use AgentMemory in any agent

> [!NOTE]
> **Goal:** make a chat surface read your vault before it answers, so it already knows your conventions and where your projects stand.
> **Prereqs:** the vault synced to Google Drive, signed into the account that owns it; an agentm checkout; access to each surface's own settings, which the agent cannot log into for you.

## Print the payload

```bash
python3 harness/skills/memory/scripts/payload.py
```

That prints the body, then a list of which surfaces need a paste. `--body-only` gives the bytes alone, for a clipboard.

The text is layout-free on purpose: it names `index.md`, `standards/` and `moc-projects.md`, and no folder that a migration moves. That is why you paste it once and not again after every landing.

| Surface | How it reads the vault | What you do |
|---|---|---|
| Claude Code | the local path, through the session hooks | nothing |
| Antigravity | the installed rule, and `~/.gemini/GEMINI.md` | nothing — `install.sh` writes both |
| claude.ai | the Google Drive connector | paste |
| Claude Desktop | the Drive connector, until the daemon is registered there | paste |
| Gemini | the same Drive folder, in a Gem | paste |

## Paste it into claude.ai

1. Settings → Connectors → enable **Google Drive** and finish the OAuth. That grants search over your whole Drive; the payload is what scopes it to the vault.
2. Paste the body into Settings → Custom instructions, or into a Project's instructions.

For steadier recall, make a Claude Project, put the payload in its instructions, and add the `standards/` files to the Project's knowledge. Search at query time depends on Claude choosing to search; knowledge files do not.

## Paste it into the Gem

Create a Gem, paste the body into its Instructions, and add `index.md` and the `standards/` files as knowledge. Gemini reaches the same Drive folder.

## Claude Desktop

Today it is claude.ai in another window: the Drive connector plus the same paste. The design registers the daemon there as a local server — the same two tools, the same walls, the clock written — and that lands with the surfaces plan.

## Antigravity needs no paste

`install.sh` merges the rendered payload into `~/.gemini/GEMINI.md` as a managed section, so every workspace picks up the vault with nothing installed per project. Your own content in that file is preserved. The tracked rule at `adapters/antigravity/rules/agentmemory-context.md` is generated from the same render.

Unlike the chat surfaces, Antigravity may write to the vault through the capture tool.

## Check that it took

Run these in a fresh chat, with no priming.

1. *What's our commit-message convention?* — the answer comes from the vault and cites the note's path.
2. *Where does agentm stand?* — the answer comes from `moc-projects.md` or a tracker's State. This one waits on the projects migration; until then the honest answer is the charter.
3. *Where does a new capture go?* — the answer names a memory class and `status: unfiled`, and never `_inbox/`, `_always-load/`, `_index.md` or `_harness/`.
4. From any session: a Drive title search for `storage-rules.md`, `voice-kernel.md` and `index.md` returns one file each, and `/doctor` reports the Gemini managed section equal to the template.

Record the result where you keep the project's follow-ups.

**If a check fails.** A generic answer, or "I can't see the vault", usually means one of three things: you are signed into the wrong Google account; more than one copy of the vault is in the Drive being searched, so the connector finds a stale twin first; or a local copy has drifted. Run `/memory payload --check` for the third — it names the copy and `--write` repairs it.

## Keeping it current

A re-paste is owed only when the posture, the card guide or the surface list changes. Nothing else earns one. When you do change the template, run:

```bash
python3 harness/skills/memory/scripts/payload.py --write
```

and paste the new body on the two chat surfaces the same day.

## Related

- [AgentMemory context payload](AgentMemory-Context-Payload) — what each part of the text says, and how the copies stay equal.
- [Memory daemon (agentmd)](Memory-Daemon) — the search surface local agents use.
