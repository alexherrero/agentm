<!-- mode: reference -->
# PII Guardrail

AgentM is a public repo. You must keep personal information out of it. This includes real email addresses, personal file paths, API keys, phone numbers, and private names. A layered guardrail protects your code. You use safe stand-ins while you write. You run an on-demand scan before you commit. A hard block stops you before every push. A final scan runs in CI. This page lists what to avoid and shows you how to check.

## What not to commit

| Don't commit | Use instead |
|---|---|
| Email addresses | the GitHub handle `alexherrero`, or `@example.com` / `@example.org` (RFC 2606) for fakes in docs |
| Personal paths — `/Users/<name>/`, `C:\Users\<name>\`, `/home/<name>/` | a `<your-user>/` placeholder, or `$HOME` |
| API keys / tokens — `sk-…`, `gho_…`, `ghp_…`, `glpat-…`, `AKIA…` | `<API_KEY>` |
| Phone numbers | `555-0100`–`555-0199` (reserved for fiction) |
| Private project names, internal hostnames, IP addresses | anything already public |

## The layers

Three layers keep personal information out. They range from interactive checks to absolute blocks.

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

The scanner may flag a false positive. This happens when a doc example *looks* like PII but is not.

1. **Rephrase to dodge the match.** Use `alice@example.com`, `555-0100`, or `<API_KEY>`. This is the right fix.
2. **Allowlist** the value if it must look real. You might need this when you test the scanner itself. Add the value to `ALLOWLIST_PATTERNS=` in `scripts/check-no-pii.sh`. You can also add it to `[allowlist].regexes` or `.paths` in `.gitleaks.toml`. Add a comment explaining your reason. Note the reason in your commit message. The scanner has no silent suppression.

## See also

- [CI gates](CI-Gates) — The `pii-guardrails` gate backstops the scan.
- [Repo layout](Repo-Layout) — The guardrail scripts and hooks live here.

[Reference](Reference) · [Architecture](Architecture) · [Home](Home)
