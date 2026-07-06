---
name: simple
kind: opinion
question: "the simplest thing that works?"
serves: [code-review]
implements: crickets/code-review
composes: []
---
Simple means Chesterton's Fence (understand why code exists before removing
it) and the Rule of 500 (a long function or file is a signal to investigate,
not a hard limit) — cleanup that reduces accidental complexity without
touching load-bearing structure. Simplicity is earned by understanding,
not by deleting the first thing that looks unnecessary.
