# Designs

Design docs authored via [`crickets`'s `/design` skill](https://github.com/alexherrero/crickets/blob/main/wiki/how-to/Use-The-Design-Skill.md). Published-visibility designs land here as the canonical "Why we built X" entry point for anyone trying to understand what was built and why.

Confidential-visibility designs live at `.harness/designs/` (gitignored, machine-local; not committed to a public repo).

The design Status lifecycle is `draft → review → final → launched`. Status transitions are skill-driven (via `/design author`) and release-driven (the `/release` flow transitions `final → launched` when the design's last queued part's `PLAN.md` completes).

When designs land in `launched` Status, the `/release` flow updates `wiki/Home.md` + `_Sidebar.md` to surface them as discoverable entry points.

If `crickets` isn't installed alongside this harness install, this directory stays empty — design-doc authoring requires the toolkit's `/design` skill.
