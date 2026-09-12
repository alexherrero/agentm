<!-- mode: reference -->
# AgentMemory context payload reference

The context payload is the brief you paste into a chat surface so it reads your vault before it answers from its own knowledge. It has one source in this repo, `templates/agentmemory-context.md`, and everything else is derived from that file.

The payload is **layout-free**. It names no folder that a migration moves. It names three things that do not move — `index.md`, `standards/` and `moc-projects.md` — and lets the map explain itself. A surface that goes looking for a folder that no longer exists finds nothing, says nothing, and answers from its own knowledge instead, which is the failure this text exists to prevent.

## ⚡ Quick reference

| Question | Answer |
|---|---|
| Where is the source? | [`templates/agentmemory-context.md`](https://github.com/alexherrero/agentm/blob/main/templates/agentmemory-context.md). |
| What do I paste? | Whatever `/memory payload` prints. The leading HTML comment in the file is yours, not the surface's, and the renderer strips it. |
| Which surfaces need a paste? | claude.ai and the Gem. Antigravity's copies are derived; Claude Code needs nothing. |
| Where do the copies come from? | One renderer, [`scripts/payload_render.py`](https://github.com/alexherrero/agentm/blob/main/scripts/payload_render.py), feeds all three outputs. |
| How do I know a copy is current? | [`check-payload-parity`](CI-Gates) in the battery, and the doctor's `payload-copy` rows. |
| When do I paste again? | Only when the posture, the card guide or the surface list changes. A layout change never triggers one. |

## What the payload says

Five parts, in order.

| Part | Covers |
|---|---|
| Where it is, on your surface | Local agents read the configured path through `memory_search`; claude.ai, Claude Desktop and Gemini read the Drive folder named `Vault`. |
| Read in this order | `index.md` first — it is the map and it is current. Then everything in `standards/`. For a project question, `moc-projects.md`, then that project's `tracker.md` and `charter.md`. Then search, before falling back on general knowledge. If the vault says something, it wins. |
| How to read a note | The frontmatter field order, and what `status: unfiled`, `lifecycle: dormant`, `superseded`, `completed/` and `agent/archive/` each mean. |
| Your posture | Chat surfaces read; they never write. Local agents you run may write through the capture tool. |
| The sync caveat | Drive shows the last-synced state. Say so rather than guess. |

## The copies, and what keeps them honest

| Copy | Rendered | Kept current by |
|---|---|---|
| `adapters/antigravity/rules/agentmemory-context.md` | without a capture address — the repo copy never carries your mailbox | `/memory payload --write`, and `check-payload-parity` in the battery |
| the `AGENTMEMORY` section of `~/.gemini/GEMINI.md` | with the address when one is configured | `install.sh` on every run, and `/memory payload --write` |
| what `/memory payload` prints | with the address when one is configured | it is the render itself |

Everything outside the `AGENTMEMORY` markers in `GEMINI.md` is preserved, so your own global rules survive a rewrite.

All three were kept by hand until 2026-09-07, and all three had drifted: the rule and the template had become different documents, and Gemini's copy was still teaching a July folder map. Never edit a copy. Edit the template and run `/memory payload --write`.

## The write path

Chat surfaces read only. The design gives them one write path — an email door: you write the card, mail it to a capture address, and the hourly sweep files it as an untrusted, unfiled note the nightly pass judges like any other candidate.

The door is not built yet; it lands with the surfaces plan. Until then the payload's posture asks a surface to show you the card so you can file it, and it says so rather than pointing you at a mailbox that does not exist. The address lives at `plugins.autonomy.capture_address` in the engine config, and the renderer switches the sentence when it is set.

## The four checks that prove a paste took

1. In a fresh chat on each surface, with no priming: *what's our commit-message convention?* It passes when the answer comes from the vault and cites the note's path.
2. *Where does agentm stand?* It passes when the answer comes from `moc-projects.md` or a tracker's State. This one can only pass after the projects migration writes the trackers.
3. *Where does a new capture go?* It passes when the answer names a memory class and `status: unfiled`, and never `_inbox/`, `_always-load/`, `_index.md` or `_harness/`.
4. From any session: a Drive title search for `storage-rules.md`, `user-preferences.md` and `index.md` returns exactly one file each, and the doctor reports the Gemini managed section equal to the template. (Before the memory-root trims folded it in, this checked `voice-kernel.md`; a migrated vault holds the voice content inside `user-preferences.md` instead.)

Check 4 has two halves. The Drive half passes once only one copy of the vault is in the Drive the connector searches. The doctor half is `machinery_doctor.py`'s `payload-copy: gemini managed section` row.

## Related

- [Use AgentMemory in any agent](Use-AgentMemory-In-Any-Agent) — the setup recipe, surface by surface.
- [CI gates](CI-Gates) — `check-payload-layout-free` and `check-payload-parity`.
- [Memory daemon (agentmd)](Memory-Daemon) — the surface local agents search through.
