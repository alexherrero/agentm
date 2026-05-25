# Contributing

The harness is self-tested on every push to `main` and every PR by three per-OS workflows running in parallel:

| Workflow | Runs on | Jobs |
|---|---|---|
| [`[T] Linux Tests`](.github/workflows/tests-linux.yml) | `ubuntu-latest` | install-smoke + adapter-parity + validate + syntax + check-wiki |
| [`[T] Mac Tests`](.github/workflows/tests-mac.yml) | `macos-latest` | install-smoke + validate + syntax |
| [`[T] Windows Tests`](.github/workflows/tests-windows.yml) | `windows-latest` | install-smoke (via `install.ps1`) + validate + pwsh syntax |

Adapter-parity is repo-invariant (runs once on Linux). Validate + syntax + cross-reference + integrity checks run on every OS, so any shell-assumption regression surfaces on the platform it broke.

## What CI verifies without running an agent or needing an API key

- **install-smoke** — fresh install, idempotent re-run, `--update` refreshes managed files but preserves user edits to `wiki/` and `AGENTS.md`, installer-boundary invariant (test infra never propagates to scratch), `settings.json` hook arrays have the correct shape.
- **post-install integrity** — hook-command paths in `settings.json` resolve to files that exist; every installed `.sh`/`.ps1` parses cleanly; the bash installer produces bash-shell hook commands, the pwsh installer produces pwsh-shell commands (catches fragment-picker regressions).
- **adapter-parity** — each adapter ships the canonical set of phase-commands, sub-agents, and skills with documented divergences.
- **validate** — every TOML, YAML frontmatter, and JSON across `adapters/` and `templates/` parses and has required keys.
- **check-references** — every `harness/<phases|agents|skills|pipelines>/*.md` path mentioned in an adapter file actually exists; every phase spec's "dispatch the `<name>` sub-agent / invoke the `<name>` skill" line points at a canonical spec; `settings-fragment-bash.json` and `-pwsh.json` have matching top-level event/matcher schemas.
- **check-wiki** — Diátaxis structural lint on `wiki/` (mode purity, ADR append-only, orphan-link check, globally-unique filenames). Blocks on `--strict` per [ADR 0004](wiki/explanation/decisions/0004-diataxis-documentation-spec.md).
- **syntax** — `bash -n` on every `.sh`, PowerShell AST parse on every `.ps1`, across repo root + `scripts/` + `templates/` + `adapters/`.

## Installer-boundary invariant

The workflow files at `.github/workflows/tests-*.yml` and the helper scripts under `scripts/` live at the harness repo root, never under `templates/` or `adapters/`, so the installer never propagates them to target projects. The smoke tests assert this explicitly — if you add a test workflow or script, verify it does not appear in the scratch-install tree.

## Run the same gates locally

```bash
bash scripts/smoke-install-bash.sh      # fresh install + idempotence + --update + integrity
bash scripts/check-parity.sh            # adapter name-set invariants
bash scripts/check-syntax.sh            # bash -n on every .sh
python3 scripts/validate-adapters.py    # TOML/YAML/JSON + canonical-spec backing
python3 scripts/check-references.py     # cross-reference integrity
python3 scripts/check-wiki.py --strict  # Diátaxis structural lint
```

On Windows:

```pwsh
pwsh -NoProfile -File scripts/smoke-install-pwsh.ps1   # fresh install + integrity
pwsh -NoProfile -File scripts/check-syntax.ps1          # AST-parse every .ps1
```

## Regenerating the brand banner

The Agent M brand banner (`assets/agent-m/banner-1600.png` + `banner-3200.png`) ships in the README hero + the wiki Home page. The banner is rendered from `assets/banner.html` via headless Chrome.

**Run whenever you change `assets/banner.html`:**

```bash
bash scripts/regenerate-banner.sh
```

The script renders both PNG sizes (1600×430 + 3200×860 retina) and writes them to `assets/agent-m/`. **Commit the regenerated PNGs alongside the `banner.html` change.**

Requirements: a Google Chrome install (macOS auto-detected; Linux `google-chrome`/`chromium` on `PATH`; Windows Chrome in default Program Files). If Chrome isn't found the script prints the install paths it checked.

The banner is a **static brand asset** — it does not carry release-version data (live version + CI status live in shields.io badges in the README), so regeneration is NOT tied to releases. Crickets has its own designer-rendered banner setup (`crickets/assets/crickets/banner-*.png`) which is not currently script-driven.
