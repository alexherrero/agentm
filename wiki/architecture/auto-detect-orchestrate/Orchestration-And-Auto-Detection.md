<!-- mode: index -->
# Orchestration and Auto-Detection

_Zero-config by construction — the harness reads a project's shape to wire itself, then surfaces memory at session start and phase boundaries without being asked._

Two things happen automatically so you never hand-configure them: the harness **detects** a project's shape (language, layout, CI) to wire the right phase tooling, and it **orchestrates** memory around your work — a session-start briefing and idle-time chains that push what's relevant, plus phase-boundary recall/save that pulls and persists context. Both are tuned to inform, never to nag.

## How it works

Detection and orchestration are separate surfaces over the same vault:

| Surface | Trigger | Does |
|---|---|---|
| **Auto-detect** | install / register | reads project shape → writes `.harness/project.json` config |
| **Push (orchestration)** | session start · idle | briefing + idle chains surface over-threshold signals |
| **Pull (auto-context)** | phase boundary | recalls relevant memory in, saves phase state out |

Every emission is gated — thresholds, cooldowns, and per-emission toggles in `auto-orchestration-config.md` — so a signal fires only when it crosses a bar *and* its cooldown allows. Nothing emits on every session by default; the config is yours to tune, and a re-seed never clobbers your edits.

## How it fits

- **[AgentMemory](AgentMemory)** — the store the push/pull surfaces read and write. Orchestration is the *when*; AgentMemory is the *what*.
- **[Phases](Phases)** — auto-context injects at phase boundaries, so each phase opens with the memory it needs and closes by persisting what it learned.
- **[Device-Wide Substrate](Device-Wide-Substrate)** — detection writes the on-host/project config that the substrate resolves state against.

## See also

Detail:

- [Auto-detect + auto-configure](Auto-Detect-Configure) · [Auto-orchestration](Auto-Orchestration) — what each surface does and why it never nags.
- [Detection rules](Detection-Rules) · [Auto-orchestration config](Auto-Orchestration-Config) — the rule table and every config key.
- [Configure a new project](Configure-A-New-Project) · [Tune auto-orchestration](Tune-Auto-Orchestration) — the recipes.
- [ADR 0007 — Auto-context into harness phases](agentm-memory-system) — the decision behind the pull surface (no standalone design doc).

[Architecture](Architecture) · [Explanation](Explanation) · [Home](Home)
