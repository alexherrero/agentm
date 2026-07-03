<!-- mode: index -->
# Toolkit interface ↔ crickets

_The seam with the sibling crickets toolkit — agentm owns the durable state substrate and memory engine the phases run on; crickets' developer-workflows plugin owns the phase loop itself, plus the skills, agents, and hooks that ride on it, and neither requires the other._

`agentm` is the memory-engine substrate; **crickets** is the toolkit that ships the phase-gated workflow (`/plan` · `/work` · `/review` · `/release` · `/bugfix`) and the customizations that run inside it. The two are **siblings, not layers** — each ships and versions independently, and neither requires the other to be installed. Since the V5 unbundling, crickets' developer-workflows plugin owns the phases and their canonical specs; agentm owns the on-disk state layout and memory engine those phases read and write.

## How it works

The seam is graceful-skip in both directions, so each side works alone:

| Direction | Mechanism |
|---|---|
| **developer-workflows → other crickets plugins** | a phase spec *suggests* a sibling crickets primitive (e.g. `/release` suggests `ship-release`) and graceful-skips when it's absent. |
| **crickets → agentm** | developer-workflows' phases read and write agentm's on-disk state layout (`.harness/PLAN.md`, `progress.md`) and call into the memory engine; both graceful-skip when agentm isn't installed. |

Because the dependency is soft in every direction, a bare agentm memory engine runs with no phase loop installed, and the phase loop goes quiet on a project with no memory-engine state to read. Neither hard-wires to the other.

## How it fits

- **[Phases](Phases)** — the surface crickets enhances. The harness defines the phase contract; crickets primitives compose onto it through the soft seam.
- **[Host adapters](Host-Adapters)** — crickets primitives install per host through the same adapter destinations the harness uses.

## See also

Detail:

- [Product intent](Product-Intent) · [How the pieces fit](How-The-Pieces-Fit) — what the harness is, and where the toolkit sits beside it.
- [Foundations HLD — crickets split](agentm-foundations-hld) — the decision that drew this seam.
- [crickets wiki ↗](https://github.com/alexherrero/crickets/wiki) — the toolkit's own documentation, from the other side of the seam.

[Architecture](Architecture) · [Explanation](Explanation) · [Home](Home)
