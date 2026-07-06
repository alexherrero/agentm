---
name: recoverable
kind: opinion
question: "able to be undone?"
serves: [development-lifecycle]
implements: crickets/developer-safety
composes: []
---
Recoverable means the stop-gate is reversibility, not destructiveness or
blast-radius: a recoverable action proceeds, announced; a genuinely
unrecoverable one (force-push rewriting published shared history, a
sole-ref delete of unmerged work, an immutable deploy) stops for
confirmation. When uncertain, treat it as unrecoverable — the doctrine
this opinion serves by name lives in `developer-safety`.
