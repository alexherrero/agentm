# How to use AgentMemory in Claude.ai

> [!NOTE]
> **Status:** pending
> **Plan:** PLAN.md (V4 #22) task 2 — Claude.ai (Google Drive connector) setup how-to + dogfood.
> **Goal:** Enable the Claude.ai Google Drive connector, pin the AgentMemory vault folder, and paste the canonical payload so a fresh Claude.ai chat reads your vault natively (read-only). It answers from `_always-load/` without you priming it.
> **Prereqs:** A Claude.ai account with the Google Drive connector available, the AgentMemory vault synced to Google Drive, and the canonical payload (`templates/agentmemory-context.md`, V4 #22 task 1).

> _Setup steps are drafted; pending the operator-assisted connector dogfood (PLAN.md task 2)._

For the cross-surface overview see [Use AgentMemory in any agent surface](Use-AgentMemory-In-Any-Agent); for what's in the payload you paste see [AgentMemory context payload](AgentMemory-Context-Payload).

## Steps

1. **Enable the Google Drive connector.** In Claude.ai, open **Settings → Connectors** and enable the **Google Drive** connector. Complete the Google OAuth flow so Claude can read your Drive.

2. **Pin the AgentMemory folder.** In the connector's settings, select/pin the top-level **`AgentMemory/`** folder so Claude scopes reads to the vault. Confirm the whole tree (`personal-private/`, `projects/`, `_idea-incubator/`, `_inbox/`, `_meta/`) is in scope, not just the root.

3. **Paste the canonical payload.** Open `templates/agentmemory-context.md` and copy the body from `# Using my Agent Memory` (line 19) to the end — *skip the leading HTML comment*, which is operator-only instructions. Paste it into **Settings → Custom Instructions**, or into a dedicated **Claude Project's** instructions if you want a vault-aware Project. Optionally, attach the most-relevant entries (e.g. an `_always-load/` rule) as Project files for faster recall.

4. **Dogfood.** Open a **fresh** chat (or one in the vault-aware Project) and ask *"what's our commit-message convention?"* — with no priming. It should answer from `personal-private/_always-load/` (the no-`Co-Authored-By` rule) by reading the vault, not from general knowledge. If it can't see the folder, re-check the connector scope in step 2.

## Related

- [Use AgentMemory in any agent surface](Use-AgentMemory-In-Any-Agent) — the umbrella recipe.
- [AgentMemory context payload](AgentMemory-Context-Payload) — what's in the payload you paste.
