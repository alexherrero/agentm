<!-- mode: index -->
# Toolkit interface ↔ crickets

_The seam with the sibling crickets toolkit — agentm owns the phases and their state; crickets owns the skills, agents, and hooks that ride on them, and neither requires the other._

`agentm` is the phase-gated workflow harness; **crickets** is the toolkit of customizations that run inside it. The two are **siblings, not layers** — each ships and versions independently, and neither requires the other to be installed. agentm owns the phases (`/plan` · `/work` · `/review` · `/release` · `/bugfix`) and their canonical specs; crickets owns the skills, commands, agents, and hooks that enhance them.

## How it works

The seam is graceful-skip in both directions, so each side works alone:

| Direction | Mechanism |
|---|---|
| **harness → toolkit** | a phase spec *suggests* a crickets primitive (e.g. `/release` suggests `ship-release`) and graceful-skips when it's absent. |
| **toolkit → harness** | a crickets plugin declares it *enhances* a phase and probes for it at run time, staying inert when the phase isn't installed. |

Because the dependency is soft in both directions, a bare harness runs with no toolkit installed, and a crickets primitive goes quiet on a project that has no harness. Neither hard-wires to the other.

## How it fits

- **[Phases](Phases)** — the surface crickets enhances. The harness defines the phase contract; crickets primitives compose onto it through the soft seam.
- **[Host adapters](Host-Adapters)** — crickets primitives install per host through the same adapter destinations the harness uses.

## See also

Detail:

- [Product intent](Product-Intent) · [How the pieces fit](How-The-Pieces-Fit) — what the harness is, and where the toolkit sits beside it.
- [Foundations HLD — crickets split](agentm-foundations-hld) — the decision that drew this seam.
- [crickets wiki ↗](https://github.com/alexherrero/crickets/wiki) — the toolkit's own documentation, from the other side of the seam.

[Architecture](Architecture) · [Explanation](Explanation) · [Home](Home)
