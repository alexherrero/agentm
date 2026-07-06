---
name: ready
kind: opinion
question: "ready to ship to real users?"
serves: [development-lifecycle]
implements: crickets/development-lifecycle
composes: []
---
Ready means the pre-launch floor is cleared: observability wired, a
tested rollback path, a feature-flag off-switch confirmed, a staged
rollout plan written. Not "the code works" — "the operator can turn it
off in production without a redeploy if it doesn't."
