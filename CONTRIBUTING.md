# Contributing

The harness is self-tested on every push to `main` and every PR by three per-OS workflows running in parallel — all deterministic, none needs an agent or an API key:

| Workflow | Runs on | Jobs |
|---|---|---|
| [`[T] Linux Tests`](.github/workflows/tests-linux.yml) | `ubuntu-latest` | install-smoke · adapter-parity · validate (frontmatter/TOML/JSON · check-references · check-wiki · unit tests · verify-v4 · verify-phases · verify-memory-roundtrip) · syntax (bash + pwsh) · lib-parity · pii-guardrails (check-no-pii + gitleaks) · dogfood-workflows |
| [`[T] Mac Tests`](.github/workflows/tests-mac.yml) | `macos-latest` | install-smoke · validate · check-references · syntax (bash + pwsh) · lib-parity · check-no-pii · unit tests · verify-v4 · verify-phases · verify-memory-roundtrip |
| [`[T] Windows Tests`](.github/workflows/tests-windows.yml) | `windows-latest` | install-smoke (pwsh) · installer-boundary · validate · check-references · pwsh syntax · lib-parity · check-no-pii · unit tests |

Adapter-parity, check-wiki, gitleaks, and dogfood-workflows are repo-invariant — they run once on Linux. The cross-platform gates (validate, check-references, syntax, lib-parity, check-no-pii, unit tests, and the bash-native `verify-*` integration checks) run on each OS that supports them, so any shell- or path-assumption regression surfaces on the platform it broke.

**Authoritative gate reference.** Every gate — the invariant it proves plus the script behind it — is documented in [wiki/reference/CI-Gates.md](wiki/reference/CI-Gates.md). That page is the single source of truth; this file is the contributor quick-start.

## Run the same gates locally

One command runs the full deterministic battery — **16 gates**: unit tests + every `check-*` gate + the three `verify-*` integration checks. It prints a PASS/FAIL table and exits non-zero on any failure:

```bash
bash scripts/check-all.sh
```

Run it before every commit. `check-all.sh` is the maintained source of truth for the local battery — add a `gate "<name>" <command>` line as the project grows.

It deliberately omits the heavier `smoke-install` + `gitleaks` (slow / external tooling) that CI also runs on every push. Run those directly when you need them:

```bash
bash scripts/smoke-install-bash.sh                   # fresh install + idempotence + --update + integrity
gitleaks detect --source . --config .gitleaks.toml   # secret scan (CI's pii-guardrails second layer)
```

On Windows:

```pwsh
pwsh -NoProfile -File scripts/smoke-install-pwsh.ps1   # fresh install + integrity
pwsh -NoProfile -File scripts/check-syntax.ps1          # AST-parse every .ps1
```

## PII guardrails

**agentm is a public repo.** Three layers keep personal information out of it:

| Layer | What | When it fires |
|---|---|---|
| **CI gate** (`pii-guardrails`) | `scripts/check-no-pii.sh --all` + `gitleaks` (`.gitleaks.toml`) — the Linux job in [`tests-linux.yml`](.github/workflows/tests-linux.yml) | Every push + every PR, server-side. Non-zero blocks the merge. The non-skippable backstop. |
| **Pre-push git hook** | `templates/hooks/pre-push` — copy it in once: `cp templates/hooks/pre-push .git/hooks/ && chmod +x .git/hooks/pre-push` | On every `git push`. Scans the push range (`check-no-pii.sh --diff`), or the whole tree for a new branch; non-zero blocks the push before it leaves your machine. |
| **`pii-scrubber` skill** | Agent-facing interactive layer, from the sibling [`crickets`](https://github.com/alexherrero/crickets) install | Invoked by an agent before commit / push. Presents findings, offers redactions, loops until clean. |

### What NOT to commit

- **Email addresses** — use the GitHub handle `alexherrero` as an identifier; use `@example.com` / `@example.org` (RFC 2606 reserved) for fake addresses in docs.
- **Personal paths** — `/Users/<name>/`, `C:\Users\<name>\`, `/home/<name>/`. Use a `<your-user>/` placeholder or `$HOME`.
- **API keys / tokens** — anything matching common shapes (`sk-…`, `gho_…`, `ghp_…`, `glpat-…`, `AKIA…`). Use `<API_KEY>` if an example needs one.
- **Phone numbers** — use `555-0100`–`555-0199` (NANP reserved for fiction).
- **Private project names, internal hostnames, IP addresses** — anything not already public.

### Test locally

```bash
bash scripts/check-no-pii.sh --all                    # entire working tree (what CI runs)
bash scripts/check-no-pii.sh --staged                 # only staged changes
bash scripts/check-no-pii.sh --diff origin/main..HEAD # a git range (what the pre-push hook runs)
gitleaks detect --source . --config .gitleaks.toml    # secret scan
```

### False positives — the override protocol

If the scanner flags a genuine false positive (a doc example that *looks* like PII but isn't):

1. **Rephrase to dodge the match** — `alice@example.com`, `555-0100`, `<API_KEY>`. This is almost always the right fix.
2. **Allowlist** if the value legitimately must look real (e.g. testing the scanner itself): add it to `ALLOWLIST_PATTERNS=` in `scripts/check-no-pii.sh`, or to `[allowlist].regexes` / `.paths` in `.gitleaks.toml`, with a comment explaining why — and note the reason in the commit message. There is no silent suppression.

## Installer-boundary invariant

The workflow files at `.github/workflows/tests-*.yml` and the helper scripts under `scripts/` live at the harness repo root, never under `templates/` or `adapters/`, so the installer never propagates them to target projects. The smoke tests assert this explicitly — if you add a test workflow or script, verify it does not appear in the scratch-install tree.

(The deliberate exception is `templates/.github/workflows/*.yml` — workflows agentm *does* ship into target projects. Those are kept byte-identical to their active twins at `.github/workflows/` by the `dogfood-workflows` CI job and its local mirror `check-workflow-parity`.)

## Regenerating the brand banner

The Agent M brand banner (`assets/agent-m/banner-1600.png` + `banner-3200.png`) ships in the README hero + the wiki Home page. The banner is rendered from `assets/banner.html` via headless Chrome.

**Run whenever you change `assets/banner.html`:**

```bash
bash scripts/regenerate-banner.sh
```

The script renders both PNG sizes (1600×430 + 3200×860 retina) and writes them to `assets/agent-m/`. **Commit the regenerated PNGs alongside the `banner.html` change.**

Requirements: a Google Chrome install (macOS auto-detected; Linux `google-chrome`/`chromium` on `PATH`; Windows Chrome in default Program Files). If Chrome isn't found the script prints the install paths it checked.

The banner is a **static brand asset** — it does not carry release-version data (live version + CI status live in shields.io badges in the README), so regeneration is NOT tied to releases. Crickets has its own designer-rendered banner setup (`crickets/assets/crickets/banner-*.png`) which is not currently script-driven.
