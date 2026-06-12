# Telemetry

How we measure whether the harness is pulling its weight. One script, one canonical log file, deterministic counts.

## Source of truth

`.harness/progress.md` per project. Every phase appends one-line entries on completion; those lines are the telemetry signal. No separate events system, no external service — just `grep` and `awk` over append-only markdown.

Entry formats the telemetry script recognises:

| Source | Pattern |
|---|---|
| `/review` (in-process) | `… /review — task <n>: NO ISSUES FOUND` or `… defect found (…)` / `… failing test written (…)` / `… findings (…)` |
| `/review` (cross-model) | `… /review (cross-model) — task <n>: <outcome>` |
| `/review` (fallback)   | `… /review (cross-model fallback) — task <n>: gemini unavailable` |
| dependabot-fixer       | `dependabot-fixer: <pkg> v<old>→v<new> fixed in <n> iterations` or `… ABORTED — <reason>` (skill lives in `crickets` as of v2.0.0; signals only present if toolkit is installed) |
| compaction (PreCompact)| `## compaction event — <ISO-8601>` (markdown header, emitted by the hook) |

## Signals

1. **`/review` rejection rate** — `NO ISSUES FOUND` / total reviews.
   - Band: **30%–70%**.
   - Below 30% → reviewer may be too paranoid, or deterministic gates are missing things they should catch.
   - Above 70% → reviewer is likely rubber-stamping. Audit the `adversarial-reviewer` framing; consider re-auditing after a model bump.
2. **Cross-model availability** — non-fallback cross-model runs / total cross-model attempts. A high fallback rate (>50%) means Gemini isn't actually running — check CLI auth.
3. **Cross-model contribution** — how often cross-model found issues. Ideally non-zero but not dominant; both reviewers catching different things is the point.
4. **dependabot-fixer success rate** — fixed vs. aborted. Aborts are not failures; they're the honest-abort design doing its job. A 100% fix rate is suspicious (budget too loose? verifying too weakly?).
5. **Compaction frequency** — events per project per week. > ~2/week suggests sessions run too long; prefer smaller phases and more frequent commits.

## Invocation

```bash
# Current project:
.harness/scripts/telemetry.sh

# All default roots (~/Antigravity, ~/Claude, ~/Projects):
.harness/scripts/telemetry.sh --all

# Specific roots:
.harness/scripts/telemetry.sh ~/work ~/side-projects
```

The script is pure bash — grep, awk, BSD/GNU date. No runtime deps beyond the standard toolchain.

## Deliberate non-goals

- **Task cycle time.** Tempting but requires a richer `progress.md` format than the current "one line per phase event" convention. Shipping it would entail format migration across existing projects. Defer until the format is already changing for another reason.
- **Disagreement rate between reviewers.** Would need pairing two `/review` entries per review with confidence about which reviewer produced which. Fragile. Revisit once the two reviewers emit a combined line.
- **External dashboards or storage.** Progress.md is the log. If you need multi-project historical analytics, export the script's output into whatever you already use — don't grow the harness into a telemetry platform.

## How to use it

- **Weekly review:** run `--all` once a week, glance at the Warnings section. If there are none, the harness is behaving.
- **After a model bump:** run before and after. If NIF rate jumps from ~50% to ~80%, the new model is rubber-stamping and the reviewer framing needs tightening (per [principles §6](principles.md) — re-audit on every model bump).
- **On adoption in a new project:** run single-project every few weeks until the numbers stabilise. Early noise is normal.
