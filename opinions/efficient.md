---
name: efficient
kind: opinion
question: "as cheap as the job allows, above the good floor?"
serves: [tokens]
implements: crickets/tokens
composes: []
---
Efficient means spending the least that still clears the `good` floor —
never the least, full stop. Base-only today: the measurement half
(`token-audit`'s cost tracking) is live; the routing lever (matching model
+ reasoning effort to the job's tier) is `[PENDING-IMPL]` in the
[model + effort routing](../wiki/designs/agentm-model-effort-routing.md)
design and folds into this composite once that scale ships (`composes:`
stays empty until then — see agentm-opinion-registry.md's own note on the
compound being partly blocked).
