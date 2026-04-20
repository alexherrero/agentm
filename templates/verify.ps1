# verify.ps1 — per-project verification hook (PowerShell twin of verify.sh).
# Called by the Claude Code PostToolUse hook after every Write|Edit with the
# path of the file that was just written or edited as $args[0].
#
# Customize the switch statement below to match your project's typecheck/lint.
# Leave commands commented until you know you want them — a noisy hook is
# worse than no hook.
#
# RULES:
# - This runs on EVERY Write/Edit. Keep it FAST (<2s total). Full-suite tests
#   belong in /review or CI, not here.
# - Prefer single-file operations (lint one file, not the whole project).
# - Exit 0 on success (silent), non-zero on failure (shown as system message).
# - Stdout/stderr are shown to the user, so keep output minimal on success.

$ErrorActionPreference = 'Continue'

$file = if ($args.Count -ge 1) { $args[0] } else { '' }
if (-not $file) { exit 0 }
if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { exit 0 }

$ext = [System.IO.Path]::GetExtension($file).ToLowerInvariant()

switch ($ext) {
    # Uncomment the clause(s) that match your project's toolchain.
    # A `default` no-op is required so the switch parses even when every
    # real clause is commented out.

    # TypeScript — typecheck single file
    # { $_ -in '.ts', '.tsx' } {
    #     npx tsc --noEmit $file
    #     if ($LASTEXITCODE -ne 0) { exit 1 }
    # }

    # JavaScript — lint single file
    # { $_ -in '.js', '.jsx', '.mjs', '.cjs' } {
    #     npx eslint --no-error-on-unmatched-pattern $file
    #     if ($LASTEXITCODE -ne 0) { exit 1 }
    # }

    # Python — lint with ruff (fast) or flake8
    # '.py' {
    #     ruff check $file
    #     if ($LASTEXITCODE -ne 0) { exit 1 }
    # }

    # Go — vet the containing package
    # '.go' {
    #     go vet "./$(Split-Path -Parent $file)/..."
    #     if ($LASTEXITCODE -ne 0) { exit 1 }
    # }

    # Rust — check (fast; no codegen)
    # '.rs' {
    #     cargo check --quiet
    #     if ($LASTEXITCODE -ne 0) { exit 1 }
    # }

    default { }
}

exit 0
