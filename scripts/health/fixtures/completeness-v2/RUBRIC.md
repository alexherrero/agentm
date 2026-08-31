# Completeness rubric v2 — v1's, verbatim

The rubric is `../completeness-v1/RUBRIC.md`, unchanged. It was frozen 2026-08-27
before either sample existed, and the v1 diagnosis exonerated it — the judge
"answered correctly about the claims it received"; the splitter was at fault and
was fixed in #497. Revising an instrument after seeing which notes it failed on
is how it gets fitted to its own test set, so nothing here was rewritten.

One resolution is recorded rather than added: rule 4 excludes YAML frontmatter
from grading, but the mining-metadata block in the note *body* appears in the
claims list and is what the v1 operator grading marked notes down for losing. A
metadata claim counts as preserved iff its value survives in any wording, and
metadata-only loss grades `minor`.
