# How to use AgentMemory in any agent surface

> [!NOTE]
> **Status:** pending
> **Plan:** PLAN.md (V4 #22) tasks 1 + 5 — canonical context payload + release umbrella.
> **Goal:** Configure any agent surface you use (Claude.ai chat, Gemini, Antigravity) to **read** the GDrive-synced AgentMemory vault natively, so a fresh conversation already knows your dev-flow conventions, in-flight projects, and locked decisions — without you re-explaining anything. Read-only (v1): agents read + query; they suggest entries for you to paste into Obsidian by hand.
> **Prereqs:** agentm with the canonical payload shipped (V4 #22), the AgentMemory vault synced to Google Drive, and operator access to each surface's connector / Gem / rule settings (the agent can't log into your accounts — connector setup is an operator action).

> _Setup steps are drafted; pending the operator-assisted connector dogfood (PLAN.md tasks 2/3)._

This is the umbrella recipe. Each surface reads from **one canonical payload** (`templates/agentmemory-context.md`) — paste it into each surface so they share the same path resolution, folder map, read priority, conventions, and the read-only boundary. The per-surface pages below are thin wrappers over that payload; for the payload's sections see [AgentMemory context payload](AgentMemory-Context-Payload).

## Steps

1. **Read the canonical payload.** Open `templates/agentmemory-context.md`. The pasteable body starts at `# Using my Agent Memory` (line 19); everything above it is operator-only instructions. This is what every surface gets — copy it once, reuse it everywhere.

2. **Configure Claude.ai.** Enable the Google Drive connector, pin the `AgentMemory/` folder, and paste the payload into Custom Instructions (or a Claude Project). Full steps + dogfood: [Use AgentMemory in Claude.ai](Use-AgentMemory-In-Claude-Ai).

3. **Configure Gemini.** Create a custom Gem, paste the payload into its Instructions, and confirm it has Drive access to `AgentMemory/`. Full steps + dogfood: [Use AgentMemory in Gemini](Use-AgentMemory-In-Gemini).

4. **Configure Antigravity.** **TODO** — deferred until the rule's repo home (agentm vs crickets) is resolved (DC-7, a later /work task). Once the rule lands, the installer wires the `agentmemory-context` rule automatically and a dedicated page is added here.

5. **Dogfood each surface.** In a fresh conversation on each configured surface — no priming — ask *"what's our commit-message convention?"*. Each should answer from `personal-private/_always-load/` (the no-`Co-Authored-By` rule) by reading the vault. If a surface answers from general knowledge instead, re-check its connector/Gem scope on the per-surface page.

## Related

- [Use AgentMemory in Claude.ai](Use-AgentMemory-In-Claude-Ai) — Google Drive connector setup + dogfood (task 2).
- [Use AgentMemory in Gemini](Use-AgentMemory-In-Gemini) — custom Gem setup + dogfood (task 3).
- [AgentMemory context payload](AgentMemory-Context-Payload) — the canonical payload's sections (path resolution, folder map, read priority, conventions, read-only boundary).
- **TODO (Antigravity):** an Antigravity setup page is deferred until the rule's repo home (agentm vs crickets) is resolved at /work (DC-7, task 4). Add the page + link once the rule lands.
