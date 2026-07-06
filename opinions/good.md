---
name: good
kind: opinion
question: "does it survive a hostile read?"
serves: [code-review, design]
implements: crickets/code-review
composes: []
---
Good means it survives an adversarial pass primed to assume bugs exist, not
a friendly skim. The standard is a failing test, a specific file:line
defect, or an explicit "no issues found" after genuinely looking — prose
critique without one of those three is not a review.
