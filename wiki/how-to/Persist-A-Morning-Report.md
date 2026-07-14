# How to persist a morning report to disk

> [!NOTE]
> **Status: implemented** — shipped by `PLAN-observability-residue-trio.md#task-2` (agentm `_harness/`).
> **Goal:** Write an overnight run's morning report to a file on disk instead of only printing it to stdout, so it can be re-derived or archived after the terminal session that produced it is gone.
> **Prereqs:** A populated observability rollup (`aggregator.default_db_path()` or a `--db-path` you pass explicitly) with at least one event for the plan you're reporting on. See [CI gates](CI-Gates) § The nightly health tier for the sibling `scripts/health/` CLI family this script belongs to.

`scripts/health/morning_report.py` renders why an overnight run ended — plan finished, gates green, an escalation parked, or the window ran out — with the plan's spend attached (`wiki/designs/agentm-autonomy.md` § When the window runs out). The `--out` flag adds a second, on-disk copy of the same markdown without changing stdout behavior.

## Steps

1. Run `morning_report.py` with `--plan`, `--ending-cause`, and `--db-path` as before, adding `--out <path>` to also write the rendered markdown to `<path>`: `scripts/health/morning_report.py:110` declares the flag (`--out`, default `None`, "also write the rendered report to this path (stdout output is unchanged either way)").
2. Stdout is unchanged whether or not `--out` is passed: `morning_report.py:127-128` renders the page once (`page = render_morning_report(data)`) and always prints it (`print(page, end="")`) before the `--out` branch runs.
3. When `--out` is given, `morning_report.py:129-132` builds `Path(args.out)`, creates any missing parent directories (`out_path.parent.mkdir(parents=True, exist_ok=True)`), then writes the same `page` string to it (`out_path.write_text(page, encoding="utf-8")`) — so a nested path like `reports/morning.md` works even if `reports/` doesn't exist yet.
4. The trailing `{"total_cost_usd": 0.0}` JSON line still prints to stdout after, unaffected by `--out` (`morning_report.py:133`).
5. Read back the persisted file to confirm it matches what was printed — the file content and the stdout content are byte-identical copies of the same `page` string.

## Verify

- `test_main_with_out_writes_the_same_content_stdout_gets` (`scripts/health/test_morning_report.py:165-179`) passes `--out <tmp>/reports/morning.md` — a nested, not-yet-existing directory, proving the `mkdir(parents=True)` behavior — and asserts the written file's content equals `render_morning_report(...)`'s output and that the same content also appears in stdout.
- `test_main_without_out_writes_no_file_and_stdout_is_unchanged` (`scripts/health/test_morning_report.py:154-163`) asserts that omitting `--out` produces zero `.md` files in the tmp dir and that stdout equals the exact pre-existing byte-identical format (`expected + '{"total_cost_usd": 0.0}\n'`).

## See also

- [Health scorecard](Health-Scorecard) — the sibling `scripts/health/` surface, explains where the scorecard lives and how it's produced.
- [CI gates](CI-Gates) § The nightly health tier — the reference page for the tier this script's data comes from.
- `wiki/designs/agentm-autonomy.md` § When the window runs out — the design line this flag closes out ("The morning report names why the run ended... with the spend attached").
