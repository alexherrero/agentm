<!-- mode: index -->
# Host adapters

_One harness, three hosts — the same phases reach Claude Code, Antigravity 2.0, and the Antigravity CLI through host-shaped entrypoints, and where a host can't follow, the gap is named._

The phase workflow is host-agnostic, but each host invokes it differently. An adapter per host maps the six phases onto that host's native surface — slash commands on Claude Code, prompted entrypoints on Antigravity — while the canonical phase specs stay in one place: the crickets **developer-workflows** plugin, since the V5 unbundling ([ADR 0011](0011-v5-unbundling-dev-loop)). Supported hosts are **Claude Code · Antigravity 2.0 · Antigravity CLI**.

## How it works

The installer emits one adapter tree per supported host; the host's own loader reads it:

| Host | Adapter tree | Phase entrypoint |
|---|---|---|
| **Claude Code** | `.claude/` | `/plan` · `/work` · `/review` · `/release` · `/bugfix` |
| **Antigravity 2.0 / CLI** | `.agents/` (+ global `~/.gemini/GEMINI.md` at user scope) | prompted "run the &lt;phase&gt; phase" entrypoints |

Each adapter points back to the canonical spec rather than restating it, so a phase's contract is defined once and honored everywhere. Antigravity reads the same on-host config as Claude Code, so state resolution doesn't change with the host.

## How it fits

- **[Phases](Phases)** — the workflow the adapters expose. Adapters define *how a host invokes* a phase; Phases defines *what the phase does*.
- **[Toolkit interface ↔ crickets](Toolkit-Interface)** — crickets primitives install per host through the same adapter destinations.
- **[Device-Wide Substrate](Device-Wide-Substrate)** — every host reads one on-host config, so state is device-wide regardless of which host runs.

## Host gaps

- **Antigravity authoring gaps.** Scheduling/triggers and multi-agent orchestration have no file-based authoring path on Antigravity, so some automation is Claude-first; the gap is named rather than worked around silently.
- **Vestigial `.gemini/` adapter.** The installer still emits a `.gemini/` tree for the Gemini CLI — a **dropped** host (v2.4.0). It is not a supported surface; it remains pending reconciliation, not active maintenance. (`~/.gemini/GEMINI.md` is unrelated — that's Antigravity 2.0's global rules file, a supported surface.)

## See also

Detail:

- [Compatibility](Compatibility) — which surfaces are supported and what each host can honor.
- [ADR 0005 — Drop Codex support](0005-drop-codex-support) · [ADR 0006 — Split into crickets](0006-crickets-split) — the decisions that shaped the host set (no standalone design doc).

[Architecture](Architecture) · [Reference](Reference) · [Home](Home)
