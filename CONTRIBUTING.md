# Contributing

Contributions are welcome. Fork the repo, work on a branch, and open a pull request — here's what makes it land smoothly:

- **Get your bearings.** The [Reference](https://github.com/alexherrero/agentm/wiki/Reference) covers the flags, schemas, and repo layout.
- **Test locally before you push.** Run `bash scripts/check-all.sh` — the details are in [How to test locally](#how-to-test-locally) below.
- **Keep secrets and personal data out.** This is a public repo, so a PII scan runs in CI and blocks anything it finds. See [CI gates](https://github.com/alexherrero/agentm/wiki/CI-Gates) for what the scan covers and how to run it yourself.
- **What CI does with your PR.** Every push runs three per-OS workflows (Linux, macOS, Windows) in parallel; all of them need to be green before a PR can merge.
- **Review turnaround.** Expect a first review within about a week.

## How to test locally

One command runs the full deterministic battery — the unit tests plus every gate — and prints a pass/fail table:

```bash
bash scripts/check-all.sh
```

Run it before every commit. It leaves out two slower checks that CI also runs — a fresh-install smoke test and a secret scan — so run those directly when you need them:

```bash
bash scripts/smoke-install-bash.sh                   # a fresh install, start to finish
gitleaks detect --source . --config .gitleaks.toml   # the secret scan
```

On Windows:

```pwsh
pwsh -NoProfile -File scripts/smoke-install-pwsh.ps1
pwsh -NoProfile -File scripts/check-syntax.ps1
```

Every gate — what it proves and the script behind it — is documented on the [CI gates](https://github.com/alexherrero/agentm/wiki/CI-Gates) page.
