# How to use AgentMemory in Claude.ai

> [!NOTE]
> **Status:** implemented
> **Plan:** PLAN.md (V4 #22) task 2 — Claude.ai (Google Drive connector) setup + dogfood (validated 2026-05-30: a fresh chat searched Drive, read `_always-load/commit-no-coauthor-trailer.md`, and answered correctly without priming).
> **Goal:** Connect the Claude.ai Google Drive connector and paste the canonical payload so a fresh Claude.ai chat reads your vault natively (read-only) — it searches Drive and answers from `_always-load/` without you priming it.
> **Prereqs:** A Claude.ai account with the Google Drive connector available, the AgentMemory vault synced to Google Drive (signed in with the account that owns it), and the canonical payload (`templates/agentmemory-context.md`, V4 #22 task 1).

For the cross-surface overview see [Use AgentMemory in any agent surface](Use-AgentMemory-In-Any-Agent); for what's in the payload you paste see [AgentMemory context payload](AgentMemory-Context-Payload).

## Steps

1. **Enable the Google Drive connector.** In Claude.ai, open **Settings → Connectors** and enable the **Google Drive** connector. Complete the Google OAuth flow so Claude can read your Drive.

2. **There's no folder to pin — the connector grants Drive *search* access.** The Google Drive connector gives Claude search access to your whole Drive; there is **no** setting to pin or scope it to a folder (an earlier draft of this page wrongly said there was). Scoping to the vault comes from the payload in step 3 — it tells Claude the vault lives at `AgentMemory/` on your Drive, and Claude searches there at query time. Just confirm you connected the Google account that **owns the synced vault**.

3. **Paste the canonical payload.** Open `templates/agentmemory-context.md` and copy the body from `# Using my Agent Memory` (line 19) to the end — *skip the leading HTML comment*, which is operator-only instructions. Paste it into **Settings → Custom Instructions** (applies to every chat) or into a dedicated **Claude Project's** instructions.

4. **Dogfood.** Open a **fresh** chat and ask *"what's our commit-message convention?"* — with no priming. It should **search Google Drive** (you'll see a search step / a cited file), read `personal-private/_always-load/commit-no-coauthor-trailer.md`, and answer *"no `Co-Authored-By` trailer"* — not from general knowledge. If it answers generically without searching, confirm the connector is connected to the vault-owning account, then use the more-reliable Project approach below.

## More reliable recall (optional)

Search-at-query-time depends on Claude *deciding* to search Drive. To ground recall so it always has the context, create a **Claude Project**, put the payload in the Project's instructions, and add the key vault files — especially the `personal-private/_always-load/` entries — to the Project's knowledge. A Project chat then reads those directly instead of relying on a search.

## Related

- [Use AgentMemory in any agent surface](Use-AgentMemory-In-Any-Agent) — the umbrella recipe.
- [AgentMemory context payload](AgentMemory-Context-Payload) — what's in the payload you paste.
