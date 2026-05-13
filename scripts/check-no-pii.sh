#!/usr/bin/env bash
# check-no-pii.sh — scan for personal information that should not ship to a public repo.
#
# Modes:
#   --all              scan the entire working tree (default)
#   --staged           scan only files staged for commit
#   --diff <range>     scan only files changed in a git range (e.g. origin/main..HEAD)
#   --help, -h         print this help and exit
#
# Exit:
#   0  clean
#   1  findings (file:line:kind:match printed to stderr)
#   2  argument error
#
# Patterns caught: emails, personal paths (mac/linux/windows), API key shapes
# (OpenAI, GitHub, GitLab, AWS), US phone numbers. See ALLOWLIST_PATTERNS below
# for known-safe substrings (the public handle, RFC 2606 reserved domains, etc.).
#
# See CONTRIBUTING.md § PII guardrails for the full pattern list and override
# protocol.

set -uo pipefail

# ── help ──────────────────────────────────────────────────────────────────
print_help() {
    cat <<'EOF'
check-no-pii.sh — scan for personal information that should not ship to a public repo.

Modes:
  --all              scan the entire working tree (default)
  --staged           scan only files staged for commit
  --diff <range>     scan only files changed in a git range (e.g. origin/main..HEAD)
  --help, -h         print this help and exit

Exit:
  0  clean
  1  findings (file:line:kind:match printed to stderr)
  2  argument error

Patterns caught: emails, personal paths, API key shapes, US phone numbers.
See CONTRIBUTING.md § PII guardrails for the override protocol.
EOF
}

# ── argument parsing ──────────────────────────────────────────────────────
MODE="all"
DIFF_RANGE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --all) MODE="all"; shift ;;
        --staged) MODE="staged"; shift ;;
        --diff)
            MODE="diff"
            DIFF_RANGE="${2:-}"
            shift 2 2>/dev/null || shift
            ;;
        --help|-h) print_help; exit 0 ;;
        *) echo "check-no-pii: unknown argument: $1" >&2; print_help >&2; exit 2 ;;
    esac
done

if [[ "$MODE" == "diff" && -z "$DIFF_RANGE" ]]; then
    echo "check-no-pii: --diff requires a range argument (e.g. origin/main..HEAD)" >&2
    exit 2
fi

# ── patterns ──────────────────────────────────────────────────────────────
# Format: KIND|REGEX (KIND is for output)
PATTERNS=(
    'email|[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    'personal-path-mac|/Users/[a-zA-Z][a-zA-Z0-9_-]+/'
    'personal-path-linux|/home/[a-zA-Z][a-zA-Z0-9_-]+/'
    'personal-path-windows|C:\\\\Users\\\\[a-zA-Z][a-zA-Z0-9_-]+'
    'openai-key|sk-[a-zA-Z0-9_-]{20,}'
    'github-token|gh[psuro]_[a-zA-Z0-9_-]{20,}'
    'gitlab-token|glpat-[a-zA-Z0-9_-]{20,}'
    'aws-access-key|AKIA[A-Z0-9]{16}'
    'phone-us|(\+?1[ -.]?)?\(?[2-9][0-9]{2}\)?[ -.]?[0-9]{3}[ -.]?[0-9]{4}'
)

# Substrings that are known-safe in this repo. If a match contains any of
# these patterns, the finding is suppressed.
ALLOWLIST_PATTERNS=(
    'alexherrero'                       # public GitHub handle
    '@example\.(com|org|net)'           # RFC 2606 reserved domains
    '555-01[0-9]{2}'                    # NANP reserved phone-number prefix
    'sk-abc123def456ghi789jkl'          # documentation example
    'AKIA[A-Z0-9]{0,15}EXAMPLE'         # documentation example
)

# ── file collection ───────────────────────────────────────────────────────
get_files() {
    case "$MODE" in
        all) git ls-files 2>/dev/null ;;
        staged) git diff --cached --name-only --diff-filter=ACMR 2>/dev/null ;;
        diff) git diff --name-only --diff-filter=ACMR "$DIFF_RANGE" 2>/dev/null ;;
    esac
}

# ── self-skip ─────────────────────────────────────────────────────────────
SELF_SKIP_PATHS=(
    'scripts/check-no-pii.sh'
    '.gitleaks.toml'
    'skills/pii-scrubber/SKILL.md'
    'templates/hooks/pre-push'
    'CONTRIBUTING.md'
)

is_self_skip() {
    local file="$1"
    for skip in "${SELF_SKIP_PATHS[@]}"; do
        [[ "$file" == "$skip" ]] && return 0
    done
    return 1
}

# ── allowlist check ───────────────────────────────────────────────────────
is_allowed() {
    local match="$1"
    for allow in "${ALLOWLIST_PATTERNS[@]}"; do
        if echo "$match" | grep -qE "$allow"; then
            return 0
        fi
    done
    return 1
}

# ── scan ──────────────────────────────────────────────────────────────────
findings=0

while IFS= read -r file; do
    [[ -z "$file" ]] && continue
    [[ ! -f "$file" ]] && continue
    is_self_skip "$file" && continue
    # Skip binary files (heuristic: grep -I succeeds on text)
    if ! grep -Iq "" "$file" 2>/dev/null; then
        continue
    fi

    for entry in "${PATTERNS[@]}"; do
        kind="${entry%%|*}"
        regex="${entry#*|}"
        # -n line numbers, -E extended regex, -o only match
        while IFS=: read -r lineno match; do
            [[ -z "$lineno" ]] && continue
            is_allowed "$match" && continue
            echo "$file:$lineno: $kind match: $match" >&2
            findings=$((findings + 1))
        done < <(grep -nEo "$regex" "$file" 2>/dev/null || true)
    done
done < <(get_files)

if [[ $findings -gt 0 ]]; then
    echo "" >&2
    echo "check-no-pii: $findings finding(s) in $MODE mode" >&2
    echo "  See CONTRIBUTING.md § PII guardrails for the override protocol." >&2
    exit 1
fi

echo "check-no-pii: clean ($MODE mode)"
exit 0
