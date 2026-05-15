# Designs

Design docs authored via [`agent-toolkit`'s `/design` skill](https://github.com/alexherrero/agent-toolkit/blob/main/wiki/how-to/Use-The-Design-Skill.md). Published-visibility designs land here as the canonical "Why we built X" entry point for anyone trying to understand what was built and why.

Confidential-visibility designs live at `.harness/designs/` (gitignored, machine-local; not committed to a public repo).

The design Status lifecycle is `draft → review → final → launched`. Status transitions are skill-driven (via `/design author`) and harness-driven (the harness `/release` flow transitions `final → launched` when the design's last queued part's `PLAN.md` completes — see [`harness/phases/05-release.md` §1b](https://github.com/alexherrero/agentic-harness/blob/main/harness/phases/05-release.md)).

When designs land in `launched` Status, the harness `/release` flow updates `wiki/Home.md` + `_Sidebar.md` to surface them as discoverable entry points.

If `agent-toolkit` isn't installed alongside this harness install, this directory stays empty — design-doc authoring requires the toolkit's `/design` skill.
