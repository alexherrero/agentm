<!-- mode: reference -->
# PII Guardrail

AgentM is a public repo, so personal information has to stay out of it — real email addresses, personal file paths, API keys, phone numbers, and private names. The guardrail keeps them out in layers: safe stand-ins as you write, an on-demand scan before you commit, a hard block before every push, and a final scan in CI. This page is what to avoid and how to check.

## What not to commit

| Don't commit | Use instead |
|---|---|
| Email addresses | the GitHub handle `alexherrero`, or `@example.com` / `@example.org` (RFC 2606) for fakes in docs |
| Personal paths — `/Users/<name>/`, `C:\Users\<name>\`, `/home/<name>/` | a `<your-user>/` placeholder, or `$HOME` |
| API keys / tokens — `sk-…`, `gho_…`, `ghp_…`, `glpat-…`, `AKIA…` | `<API_KEY>` |
| Phone numbers | `555-0100`–`555-0199` (reserved for fiction) |
| Private project names, internal hostnames, IP addresses | anything already public |

## The layers

Three layers keep personal information out, from the interactive to the absolute:

| Layer | What it is | When it fires |
|---|---|---|
| **`pii-scrubber` skill** | the agent-facing interactive layer, from the sibling [crickets](https://github.com/alexherrero/crickets) install | An agent runs it before a commit or push: it shows findings by `file:line`, offers redactions, and loops until clean. |
| **Pre-push git hook** | `templates/hooks/pre-push` — copy it in once: `cp templates/hooks/pre-push .git/hooks/ && chmod +x .git/hooks/pre-push` | On every `git push`. Scans the push range (or the whole tree for a new branch) and blocks the push before it leaves your machine. |
| **CI gate** (`pii-guardrails`) | `check-no-pii.sh --all` + `gitleaks`, in the Linux CI job | Every PR (and manual `workflow_dispatch`/full-battery runs), server-side — a plain push to `main` runs only the lightweight `syntax` job under the worktree-native flow (ratified 2026-07-06). A hit on a PR blocks the merge — the backstop you can't skip there. |

## How to test locally

```bash
bash scripts/check-no-pii.sh --all                    # the whole working tree (what CI runs)
bash scripts/check-no-pii.sh --staged                 # only staged changes
bash scripts/check-no-pii.sh --diff origin/main..HEAD # a git range (what the pre-push hook runs)
gitleaks detect --source . --config .gitleaks.toml    # the secret scan
```

## False positives

If the scanner flags a genuine false positive — a doc example that *looks* like PII but isn't:

1. **Rephrase to dodge the match** — `alice@example.com`, `555-0100`, `<API_KEY>`. This is almost always the right fix.
2. **Allowlist** if the value legitimately must look real (for example, when testing the scanner itself): add it to `ALLOWLIST_PATTERNS=` in `scripts/check-no-pii.sh`, or to `[allowlist].regexes` / `.paths` in `.gitleaks.toml`, with a comment saying why — and note the reason in the commit message. There is no silent suppression.

## See also

- [CI gates](CI-Gates) — the `pii-guardrails` gate that backstops the scan.
- [Repo layout](Repo-Layout) — where the guardrail scripts and hooks live.

[Reference](Reference) · [Architecture](Architecture) · [Home](Home)
