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
    'personal-path-windows|C:\\{1,2}Users\\{1,2}[a-zA-Z][a-zA-Z0-9_-]+'
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

# Line-level allowlist: applied to the WHOLE LINE a match sits on, for cases
# where the surrounding context proves the match harmless. Kept separate from
# ALLOWLIST_PATTERNS (match-level) so broad context patterns can't mask a real
# finding that merely shares a line with allowlisted text. Adopted from
# crickets' canonical copy (CONS-1 — crickets is the source of truth for this
# gate's detection logic).
LINE_ALLOWLIST_PATTERNS=(
    'uses: [A-Za-z0-9_./-]+@[0-9a-f]{40}'  # SHA-pinned GitHub Actions (public refs; digit runs inside a SHA can mimic a phone number)
    # Machine-stamped vault paths whose timestamp digits mimic a US phone
    # number. The week-1 retrieval scorecards record the paths a search
    # returned, verbatim, so every timestamped note name in this vault lands in
    # committed JSON. Each pattern below names one real path shape rather than
    # allowlisting date-like digit runs generally — a broad rule here would mask
    # a genuine phone number that happened to sit in a path.
    '_dream(-staging)?/(inbox-|insights/)?[0-9]{8}-[0-9]{6}-[0-9a-f]{8}'  # staging dirnames + distilled insights, YYYYMMDD-HHMMSS-hash
    'personal/_inbox/[0-9]{8}-[0-9]{4}-'                                 # timestamped note filenames, YYYYMMDD-HHMM-slug
    # Go module pseudo-versions. A dependency pinned to an untagged commit is
    # written v0.0.0-<YYYYMMDDHHMMSS>-<12 hex>, and the 14-digit timestamp's
    # leading run mimics a US phone number — so most of daemon/go.sum
    # false-positives. The pattern names the full pseudo-version shape (semver
    # prefix, 14-digit stamp, 12-char commit hash) rather than adding go.sum to
    # SELF_SKIP_PATHS, which would stop scanning a lockfile entirely — and a
    # lockfile is exactly where a credential in a vanity module URL would hide.
    # Residual risk, stated plainly because this is a line-level rule: a real
    # phone number sharing a line with a pseudo-version is masked. That needs a
    # module path containing one, which no dependency here has.
    'v[0-9]+\.[0-9]+\.[0-9]+-([0-9A-Za-z.-]+\.)?[0-9]{14}-[0-9a-f]{12}'
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
    'lib/install/.checksums.txt'   # generated SHA256 file; hex substrings false-positive on phone-us regex
    'scripts/health/fixtures/v6-eval/edge-fixture-v0.json'   # per-entry source_sha256_12 hex hashes; same false-positive class
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

is_line_allowed() {
    local line="$1"
    for allow in "${LINE_ALLOWLIST_PATTERNS[@]}"; do
        if echo "$line" | grep -qE "$allow"; then
            return 0
        fi
    done
    return 1
}

# ── combined regex (one-time build) ──────────────────────────────────────
# One grep per file instead of one grep per (file, pattern) pair — the old
# per-pattern loop spawned a binary-check + 9 pattern greps per file, which
# is cheap on Linux/Mac fork() but an order of magnitude more expensive per
# spawn under Windows Git-Bash/MSYS2 (measured: this script alone was 226s
# on Windows vs 24s on Mac for an identical commit on crickets' copy — CI
# wall-time diet investigation, 2026-07-05, PLAN-ci-walltime-diet task 1).
# `-I` folds the old separate binary-file probe into this same single grep.
# Classification (which KIND matched) still runs per-match, not per-file,
# via classify_kind below — matches are rare, so that subprocess cost stays
# negligible.
COMBINED_REGEX=""
for entry in "${PATTERNS[@]}"; do
    regex="${entry#*|}"
    COMBINED_REGEX="${COMBINED_REGEX:+$COMBINED_REGEX|}($regex)"
done

classify_kind() {
    local match="$1" entry kind regex
    for entry in "${PATTERNS[@]}"; do
        kind="${entry%%|*}"
        regex="${entry#*|}"
        if grep -qE "$regex" <<<"$match" 2>/dev/null; then
            printf '%s' "$kind"
            return
        fi
    done
    printf 'unknown'
}

# ── scan ──────────────────────────────────────────────────────────────────
findings=0

while IFS= read -r file; do
    [[ -z "$file" ]] && continue
    [[ ! -f "$file" ]] && continue
    is_self_skip "$file" && continue

    while IFS=: read -r lineno match; do
        [[ -z "$lineno" ]] && continue
        is_allowed "$match" && continue
        is_line_allowed "$(sed -n "${lineno}p" "$file")" && continue
        kind="$(classify_kind "$match")"
        echo "$file:$lineno: $kind match: $match" >&2
        findings=$((findings + 1))
    done < <(grep -nEoI "$COMBINED_REGEX" "$file" 2>/dev/null || true)
done < <(get_files)

if [[ $findings -gt 0 ]]; then
    echo "" >&2
    echo "check-no-pii: $findings finding(s) in $MODE mode" >&2
    echo "  See CONTRIBUTING.md § PII guardrails for the override protocol." >&2
    exit 1
fi

echo "check-no-pii: clean ($MODE mode)"
exit 0
