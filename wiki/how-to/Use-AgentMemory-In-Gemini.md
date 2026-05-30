# How to use AgentMemory in Gemini

> [!NOTE]
> **Status:** pending
> **Plan:** PLAN.md (V4 #22) task 3 — Gemini (native Workspace/Drive) setup how-to + dogfood.
> **Goal:** Create a custom Gem whose system instructions carry the canonical payload, pointing at the AgentMemory vault via Gemini's native Drive access, so a fresh Gem conversation reads your vault natively (read-only) and recalls your context without priming.
> **Prereqs:** A Gemini account with custom Gem creation + native Drive access, the AgentMemory vault synced to Google Drive, and the canonical payload (`templates/agentmemory-context.md`, V4 #22 task 1).

> _Setup steps are drafted; pending the operator-assisted connector dogfood (PLAN.md task 3)._

For the cross-surface overview see [Use AgentMemory in any agent surface](Use-AgentMemory-In-Any-Agent); for what's in the payload the Gem carries see [AgentMemory context payload](AgentMemory-Context-Payload).

## Steps

1. **Create a custom Gem.** In Gemini, open the **Gem manager** and create a **New Gem**. Give it a name (e.g. *AgentMemory*) so you can pick it for any conversation.

2. **Set the Gem's instructions to the canonical payload.** Open `templates/agentmemory-context.md` and copy the body from `# Using my Agent Memory` (line 19) to the end — *skip the leading HTML comment*, which is operator-only. Paste it into the Gem's **Instructions** field.

3. **Point the Gem at the vault via Drive.** Ensure the Gem has Google **Workspace/Drive** access to the synced **`AgentMemory/`** folder (Gemini reads Drive natively for the signed-in account — confirm the account that owns the vault is the one in use). The payload tells the Gem to resolve the vault at `AgentMemory/` in Drive.

4. **Dogfood.** Start a **fresh** conversation **with the Gem selected** and ask *"what's our commit-message convention?"* — no priming. It should answer from `personal-private/_always-load/` (the no-`Co-Authored-By` rule) by reading the vault, not from general knowledge. If recall fails, confirm the Gem is selected and the right Google account has Drive access.

## Related

- [Use AgentMemory in any agent surface](Use-AgentMemory-In-Any-Agent) — the umbrella recipe.
- [AgentMemory context payload](AgentMemory-Context-Payload) — what's in the payload the Gem carries.
