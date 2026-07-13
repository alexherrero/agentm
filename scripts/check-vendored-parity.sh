#!/usr/bin/env bash
# check-vendored-parity.sh — one parametrized gate over agentm's five
# vendored/checksum-manifest parity invariants (CONS-1 merge of what were
# five separate scripts: check-lib-parity.sh, check-vault-lock-parity.sh,
# check-storage-seam-vendor-parity.sh, check-hook-config-parity.sh,
# check-workflow-parity.sh).
#
# NOTE: check-parity.sh (the *adapter skill/command-set* parity gate — which
# canonical skills/commands each adapters/{claude-code,antigravity,gemini}
# dir ships) is a structurally different invariant and stays a separate
# script; it was inspected and deliberately NOT folded in here.
#
# Each mode below is byte-for-byte the same comparison the retired standalone
# script performed — this is a parametrization, not a weakening:
#
#   lib          — lib/install/ tree's recomputed SHA-256 manifest matches
#                  its committed .checksums.txt (directory-wide, LC_ALL=C
#                  sorted for cross-platform determinism).
#   vault-lock   — scripts/vault_lock.py is byte-identical to its vendored
#                  sibling harness/skills/memory/scripts/vault_lock.py
#                  (DC-9: the skill dir isn't on sys.path in a real install,
#                  so this security-critical write primitive is vendored,
#                  not imported cross-tree).
#   storage-seam — same DC-9 vendoring pattern, two pairs:
#                  storage_seam.py + storage_device_local.py.
#   wiki-publish-transform — scripts/wiki_publish_transform.py (agentm's own
#                  dogfooded copy, invoked by .github/workflows/wiki-sync.yml)
#                  is byte-identical to templates/scripts/wiki_publish_transform.py
#                  (the copy install.sh vendors into every installed project,
#                  alongside its templated wiki-sync.yml).
#   hook-config  — the four memory hooks' `_resolve_vault_path() { ... }`
#                  function bodies are byte-identical (extracted verbatim,
#                  diffed against the first hook's copy).
#   workflow     — every templated GitHub Actions workflow under
#                  templates/.github/workflows/ is active at
#                  .github/workflows/, byte-identical (the dogfood
#                  self-consumption invariant). Accepts --root DIR so the
#                  negative test can point at a fixture tree.
#
# Usage:
#   bash scripts/check-vendored-parity.sh                       # run all 5
#   bash scripts/check-vendored-parity.sh lib                   # run just one
#   bash scripts/check-vendored-parity.sh workflow --root DIR   # one mode, its own flags
#
# Exit: 0  every requested mode is clean
#       1  drift detected in at least one requested mode
#       2  setup error (missing file/tool/root) in at least one requested mode

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

MODES=(lib vault-lock wiki-publish-transform storage-seam hook-config workflow)

_sha_cmd() {
  # SHA-256 tool: prefer sha256sum (Linux/coreutils, Git Bash on Windows),
  # fall back to shasum -a 256 (macOS/BSD).
  if command -v sha256sum >/dev/null 2>&1; then
    echo "sha256sum"
  elif command -v shasum >/dev/null 2>&1; then
    echo "shasum -a 256"
  else
    echo ""
  fi
}

# ── lib: directory checksum-manifest ────────────────────────────────────────
mode_lib() {
  local LIB_DIR="$REPO_ROOT/lib/install"
  local CHECKSUMS="$LIB_DIR/.checksums.txt"

  if [[ ! -d "$LIB_DIR" ]]; then
    echo "check-vendored-parity[lib]: $LIB_DIR does not exist" >&2
    return 2
  fi
  if [[ ! -f "$CHECKSUMS" ]]; then
    echo "check-vendored-parity[lib]: $CHECKSUMS missing — run 'bash scripts/sync-lib.sh' to generate" >&2
    return 2
  fi

  local SHA_CMD; SHA_CMD="$(_sha_cmd)"
  if [[ -z "$SHA_CMD" ]]; then
    echo "check-vendored-parity[lib]: no SHA-256 tool found (need sha256sum or shasum)" >&2
    return 2
  fi

  # LC_ALL=C forces byte-order sort for deterministic line order across
  # platforms — macOS's default locale uses case-insensitive collation,
  # producing different ordering than CI Linux (C locale). Same fix in
  # sync-lib.sh.
  local RECOMPUTED COMMITTED
  RECOMPUTED=$(cd "$LIB_DIR" && find . -type f -not -name '.checksums.txt' -not -path './__pycache__/*' -not -path './python/__pycache__/*' -print0 \
      | LC_ALL=C sort -z \
      | xargs -0 $SHA_CMD \
      | sed 's| [ *]\./|  |')
  COMMITTED=$(cat "$CHECKSUMS")

  if [[ "$RECOMPUTED" == "$COMMITTED" ]]; then
    local file_count; file_count=$(echo "$RECOMPUTED" | wc -l | tr -d ' ')
    echo "check-vendored-parity[lib]: clean ($file_count file(s) under lib/install/ match .checksums.txt)"
    return 0
  fi

  echo "check-vendored-parity[lib]: DRIFT detected between lib/install/ and lib/install/.checksums.txt" >&2
  echo "" >&2
  echo "--- committed ---" >&2
  echo "$COMMITTED" >&2
  echo "" >&2
  echo "--- recomputed ---" >&2
  echo "$RECOMPUTED" >&2
  echo "" >&2
  echo "  Run 'bash scripts/sync-lib.sh' to regenerate checksums." >&2
  return 1
}

# ── shared helper: whole-file sha256 pair compare ───────────────────────────
_pair_check() {
  local label="$1" canon="$2" vendored="$3"
  local SHA_CMD; SHA_CMD="$(_sha_cmd)"
  if [[ -z "$SHA_CMD" ]]; then
    echo "check-vendored-parity[$label]: no SHA-256 tool found (need sha256sum or shasum)" >&2
    return 2
  fi
  local f
  for f in "$canon" "$vendored"; do
    if [[ ! -f "$f" ]]; then
      echo "check-vendored-parity[$label]: missing $f" >&2
      return 2
    fi
  done
  local h_canon h_vendored
  h_canon="$($SHA_CMD < "$canon" | awk '{print $1}')"
  h_vendored="$($SHA_CMD < "$vendored" | awk '{print $1}')"
  if [[ "$h_canon" == "$h_vendored" ]]; then
    return 0
  fi
  echo "check-vendored-parity[$label]: DRIFT — $canon vs $vendored differ" >&2
  echo "  canonical $canon: $h_canon" >&2
  echo "  vendored  $vendored: $h_vendored" >&2
  echo "  Re-vendor with: cp $canon $vendored" >&2
  diff "$canon" "$vendored" >&2 || true
  return 1
}

# ── vault-lock: single canonical/vendored pair ──────────────────────────────
mode_vault_lock() {
  local CANON="$REPO_ROOT/scripts/vault_lock.py"
  local VENDORED="$REPO_ROOT/harness/skills/memory/scripts/vault_lock.py"
  _pair_check "vault-lock" "$CANON" "$VENDORED"
  local rc=$?
  if [[ "$rc" -eq 0 ]]; then
    echo "check-vendored-parity[vault-lock]: clean (both vault_lock.py copies are sha256-identical)"
  fi
  return "$rc"
}

# ── wiki-publish-transform: single canonical/vendored pair ──────────────────
mode_wiki_publish_transform() {
  local CANON="$REPO_ROOT/scripts/wiki_publish_transform.py"
  local VENDORED="$REPO_ROOT/templates/scripts/wiki_publish_transform.py"
  _pair_check "wiki-publish-transform" "$CANON" "$VENDORED"
  local rc=$?
  if [[ "$rc" -eq 0 ]]; then
    echo "check-vendored-parity[wiki-publish-transform]: clean (both wiki_publish_transform.py copies are sha256-identical)"
  fi
  return "$rc"
}

# ── storage-seam: two canonical/vendored pairs ──────────────────────────────
mode_storage_seam() {
  local pairs=(
    "$REPO_ROOT/scripts/storage_seam.py|$REPO_ROOT/harness/skills/memory/scripts/storage_seam.py"
    "$REPO_ROOT/scripts/storage_device_local.py|$REPO_ROOT/harness/skills/memory/scripts/storage_device_local.py"
  )
  local failed=0 pair canon vendored
  for pair in "${pairs[@]}"; do
    canon="${pair%%|*}"; vendored="${pair##*|}"
    _pair_check "storage-seam" "$canon" "$vendored" || failed=1
  done
  if [[ "$failed" -ne 0 ]]; then
    return 1
  fi
  echo "check-vendored-parity[storage-seam]: clean (both vendored pairs are sha256-identical)"
  return 0
}

# ── hook-config: function-body extraction + diff across 4 hooks ────────────
mode_hook_config() {
  local hooks=(
    "harness/hooks/memory-recall-session-start/memory-recall-session-start.sh"
    "harness/hooks/memory-recall-prompt-submit/memory-recall-prompt-submit.sh"
    "harness/hooks/memory-reflect-stop/memory-reflect-stop.sh"
    "harness/hooks/memory-reflect-idle/memory-reflect-idle.sh"
  )

  _extract() {
    awk '/^_resolve_vault_path\(\) \{/{p=1} p{print} p && /^}$/{exit}' "$1"
  }

  local TMPDIR; TMPDIR="$(mktemp -d)"
  local FIRST="" FIRST_HOOK="" hook path out
  for hook in "${hooks[@]}"; do
    path="$REPO_ROOT/$hook"
    if [[ ! -f "$path" ]]; then
      echo "check-vendored-parity[hook-config]: missing $path" >&2
      rm -rf "$TMPDIR"
      return 2
    fi
    out="$TMPDIR/$(basename "$hook").func"
    _extract "$path" > "$out"
    if [[ ! -s "$out" ]]; then
      echo "check-vendored-parity[hook-config]: _resolve_vault_path not found in $path" >&2
      rm -rf "$TMPDIR"
      return 2
    fi
    if [[ -z "$FIRST" ]]; then
      FIRST="$out"
      FIRST_HOOK="$hook"
    fi
  done

  local drift=0
  for hook in "${hooks[@]}"; do
    out="$TMPDIR/$(basename "$hook").func"
    if ! diff -q "$FIRST" "$out" >/dev/null 2>&1; then
      echo "check-vendored-parity[hook-config]: DRIFT — $hook's _resolve_vault_path differs from $FIRST_HOOK" >&2
      diff -u "$FIRST" "$out" >&2 || true
      drift=1
    fi
  done
  rm -rf "$TMPDIR"

  if [[ "$drift" -ne 0 ]]; then
    return 1
  fi
  echo "check-vendored-parity[hook-config]: clean (all four _resolve_vault_path copies are byte-identical)"
  return 0
}

# ── workflow: templated dir vs active dir, wildcard byte-compare ───────────
mode_workflow() {
  local ROOT="$REPO_ROOT"
  while [ $# -gt 0 ]; do
    case "$1" in
      --root) ROOT="${2:?--root needs a value}"; shift 2 ;;
      --root=*) ROOT="${1#--root=}"; shift ;;
      *) echo "check-vendored-parity[workflow]: unknown arg: $1" >&2; return 2 ;;
    esac
  done

  [ -d "$ROOT" ] || { echo "check-vendored-parity[workflow]: not a directory: $ROOT" >&2; return 2; }

  local TEMPLATE_DIR="$ROOT/templates/.github/workflows"
  local ACTIVE_DIR="$ROOT/.github/workflows"

  local fail=0 checked=0 problems="" tmpl name active

  shopt -s nullglob
  for tmpl in "$TEMPLATE_DIR"/*.yml; do
    name="$(basename "$tmpl")"
    active="$ACTIVE_DIR/$name"
    checked=$((checked+1))
    if [ ! -f "$active" ]; then
      problems+="  missing: .github/workflows/$name — shipped as a template but not active at the repo root"$'\n'
      fail=1
      continue
    fi
    if ! diff -u "$tmpl" "$active" >/dev/null 2>&1; then
      problems+="  drifted: .github/workflows/$name differs from templates/.github/workflows/$name"$'\n'
      fail=1
    fi
  done
  shopt -u nullglob

  if [ "$checked" -eq 0 ]; then
    echo "check-vendored-parity[workflow]: no templated workflows found under templates/.github/workflows/ —" >&2
    echo "  wrong --root, or the template surface vanished? A parity gate that checks nothing is not green." >&2
    return 2
  fi

  if [ "$fail" -ne 0 ]; then
    echo "check-vendored-parity[workflow]: templated workflow(s) out of sync with the active copy —" >&2
    printf '%s' "$problems" >&2
    echo "" >&2
    echo "  Re-sync the twins so both carry the change, e.g.:" >&2
    echo "    cp .github/workflows/<name> templates/.github/workflows/<name>   # (or the reverse)" >&2
    return 1
  fi

  echo "check-vendored-parity[workflow]: clean — $checked templated workflow(s) active at root, byte-identical."
  return 0
}

# ── dispatcher ───────────────────────────────────────────────────────────────

_is_mode() {
  local m="$1" x
  for x in "${MODES[@]}"; do [[ "$x" == "$m" ]] && return 0; done
  return 1
}

main() {
  local requested=("${MODES[@]}")
  if [ $# -gt 0 ] && _is_mode "$1"; then
    requested=("$1")
    shift
  fi

  local worst=0 mode rc
  for mode in "${requested[@]}"; do
    case "$mode" in
      lib)          mode_lib ;;
      vault-lock)   mode_vault_lock ;;
      wiki-publish-transform) mode_wiki_publish_transform ;;
      storage-seam) mode_storage_seam ;;
      hook-config)  mode_hook_config ;;
      workflow)     mode_workflow "$@" ;;
    esac
    rc=$?
    if [ "$rc" -gt "$worst" ]; then worst=$rc; fi
  done

  return "$worst"
}

main "$@"
exit $?
