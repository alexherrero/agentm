---
name: private
kind: opinion
question: "safe to commit / share?"
serves: [development-lifecycle]
implements: crickets/privacy
composes: []
---
Private means it clears the deterministic leak floor before it ships
anywhere — no email, personal path, API key, or phone number in a diff,
commit, or push. This is `privacy`'s own scan; the opinion just makes the
standard askable by name from anywhere else in the system.
