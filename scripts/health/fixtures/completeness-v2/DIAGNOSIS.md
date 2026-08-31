# Completeness v2 — the bar held, and what the number does and does not mean

**Verdict: held, provisionally — operator ratification of the grades is pending.**
Spearman rho 0.8031 against a bar of 0.50. Zero of 200,000 permutations reached it,
so p < 5e-6 against 0.05. Separation exactly 1/5 = 0.2000 against 0.20, met by
equality — the fractions are exact (complete median 1, major median 4/5), and the
file records that a strict > would have failed this run. v1, for contrast, read
rho 0.1065 on the broken splitter.

**What changed between v1 and v2 is the splitter, and the sample shows it.** The
structured `**Key**: value` lines the old `MinClaimWords = 4` floor discarded are
claims now. They are why `minor` exists in this grade set at all: fifteen notes
whose prose survives but whose metadata values dropped, scoring a median 1/2 —
exactly the population v1's scorer waved through at 1.0.

**The grades are an ensemble, not a hand pass, and that is recorded honestly.**
The operator redirected task 3 on 2026-08-30: three arms graded independently —
Fable (locked to disk first), an adversarial Claude sub-agent, and Gemini via
agy. 30 of 32 unanimous; kappa 1.000 between the Claude arms (the correlated-
family caveat made visible), 0.885 cross-family. The two disputes (pairs 22 and
27) are flagged for the operator, and the verdict survives every possible ruling
on them: separation stays at 0.2000 or improves to 0.2250. Ratification can
change individual grades; it cannot change the verdict.

**One finding limits what the score may claim.** Coverage is claims-weighted, so
it measures the volume of loss, never its severity. The draw's one major — a
note whose invocation count 185 was rewritten as 33 — scores 4/5, because four
of five claims survive. The minors score lower. Complete-versus-lost separates
cleanly and carries the correlation; the score cannot rank a corrupted number
above three dropped metadata lines. It is a loss detector. It is not a severity
ranking, and the scorecard should never present it as one.

**Controls.** The gutted-note check was re-run live on two v2 notes, both
directions: faithful rewrites scored 0.8333 and 1.0 against the 0.8 floor,
first-claim-only stand-ins scored 0.3333 against the 0.5 ceiling, spread 0.0
everywhere. The draw carries one internal duplicate (rows 11/17, the journal
holding two writes of one note); statistics run with and without it agree to
the third decimal, recorded because v1 was withdrawn over exactly this class
of double-count.

**What closes this.** Operator review of `GRADES.md` (vault, desk/scratch/
completeness-v2/). On ratification: part 6 task 1 closes, the archived plan
stops disagreeing with main, and the filing-contract design flips to launched.
