# Detection rules reference

The 9 built-in rules the detection engine scans an unconfigured repo against. Each rule is a deterministic, side-effect-free check that, when matched, attaches a *rationale* to a skill or hook in the proposed config. Rules do not gate which skills/hooks are present — the proposed config is default-all-enabled; rules only surface why each is relevant to this repo. Run via `python3 scripts/detect_project.py <cwd> --format json|text`.

## ⚡ Quick Reference

| Question | Answer |
|---|---|
| What runs the rules? | [`scripts/detect_project.py`](https://github.com/alexherrero/agentm/blob/main/scripts/detect_project.py) — the `RULES` registry + `detect()`. |
| How do I see the proposed config? | `python3 scripts/detect_project.py <cwd> --format text` |
| What's the machine-readable form? | `python3 scripts/detect_project.py <cwd> --format json` |
| What happens in a harness repo? | `R-harness` match → `bypass` verdict (skip detection; offer legacy `.harness/` migration if present). |
| Do rules gate which skills/hooks are present? | No. The proposal is default-all-enabled; rules only overlay a per-target rationale (DC-7). |
| Related pages | [Configure a new project](Configure-A-New-Project), [Project config](Project-Config), [Auto-detect + auto-configure](Auto-Detect-Configure) |

## The 9 rules

Evaluated in registry order. `R-harness` runs first so a bypass short-circuits before the rest. Each rule is a side-effect-free `Path -> Optional[RuleMatch]`; a match overlays its rationale + `rule_id` onto the named targets in the already-default-enabled proposal.

| Rule | Detects | Attaches rationale to | Notes |
|---|---|---|---|
| `R-harness` | `harness/` dir **and** `scripts/harness_memory.py` present | — (sets `bypass` verdict) | This IS the harness source repo → detection is skipped. JSON reports `legacy_harness_present: true` when a `.harness/` state dir is also present, to offer migration. |
| `R-wiki` | `wiki/` dir present | `diataxis-author` skill | Diátaxis-shaped docs to maintain. |
| `R-changelog` | `CHANGELOG.md` **and** a language manifest (`package.json` / `pyproject.toml` / `Cargo.toml` / `go.mod`) | `ship-release` skill | Both signals required, not either-or. |
| `R-dependabot` | `.github/dependabot.yml` or `.github/dependabot.yaml` | `dependabot-fixer` skill | Helps fix breakage on update PRs. |
| `R-pii` | any `.env*` file (`.env`, `.env.*`) | `pii-scrubber` skill | Filename signal only — no content scanning. `.envrc` (direnv) is a known false positive the operator declines at approval; the decline is recorded in `operator_overrides`. |
| `R-pkg-scripts` | `package.json` with a non-empty `scripts` key, or a `Makefile`, or a `justfile` | `kill-switch` + `steer` hooks | Long-running tasks → interrupt + redirect. |
| `R-vault-content` | `_index.md` file, or `decisions/` dir **and** `conventions.md` | `memory` skill + the 4 memory hooks (`memory-recall-session-start`, `memory-recall-prompt-submit`, `memory-reflect-idle`, `memory-reflect-stop`) | Looks operator-personal → memory captures context. |
| `R-design` | `wiki/explanation/designs/` or `docs/design/` dir | `design` skill | Manages the design-then-implement pipeline. |
| `R-non-coding` | — (always returns `None`) | — | Stub in v1 per DC-6; the type taxonomy (build/vacation/research from `_index.md` frontmatter) is deferred to V5. |

## Related

- [Configure a new project](Configure-A-New-Project) — the operator recipe that consumes these rules.
- [Project config](Project-Config) — where matched rationale is persisted (the `project.json` enablement block).
- [Auto-detect + auto-configure](Auto-Detect-Configure) — why detection proposes rather than gates.
