#!/usr/bin/env bash
# telemetry.sh — scan .harness/progress.md files and report signals that
# tell you whether the harness is pulling its weight.
#
# Usage:
#   telemetry.sh                   # single project: ./.harness/progress.md
#   telemetry.sh --all             # multi-project: ~/Antigravity, ~/Claude, ~/Projects
#   telemetry.sh <dir1> [<dir2>…]  # scan specific roots (recursively)
#
# Signals reported:
#   - /review rejection rate (NO ISSUES FOUND / total). Healthy band: 30–70%.
#   - cross-model availability (non-fallback cross-model runs).
#   - dependabot-fixer success rate (fixed vs aborted).
#   - compaction frequency per project per week.

set -uo pipefail

DEFAULT_ROOTS=("$HOME/Antigravity" "$HOME/Claude" "$HOME/Projects")  # root-casing: the operator's home, not the vault

# ── arg parsing ─────────────────────────────────────────────────────────────

roots=()
mode="single"
if [[ $# -eq 0 ]]; then
  mode="single"
elif [[ "$1" == "--all" ]]; then
  mode="multi"
  for r in "${DEFAULT_ROOTS[@]}"; do
    [[ -d "$r" ]] && roots+=("$r")
  done
elif [[ "$1" == "-h" || "$1" == "--help" ]]; then
  sed -n 's/^# \{0,1\}//p' "$0" | head -15
  exit 0
else
  mode="multi"
  for r in "$@"; do
    if [[ ! -d "$r" ]]; then
      echo "warning: $r does not exist — skipping" >&2
      continue
    fi
    roots+=("$r")
  done
fi

# ── discover progress.md files ──────────────────────────────────────────────

progress_files=()
if [[ "$mode" == "single" ]]; then
  if [[ ! -f .harness/progress.md ]]; then
    echo "error: no .harness/progress.md in current dir. Run from a harness project or use --all." >&2
    exit 1
  fi
  progress_files=(".harness/progress.md")
else
  if [[ ${#roots[@]} -eq 0 ]]; then
    echo "error: no valid roots to scan." >&2
    exit 1
  fi
  # find progress.md files inside .harness/ dirs, up to 4 levels deep
  while IFS= read -r -d '' f; do
    progress_files+=("$f")
  done < <(find "${roots[@]}" -maxdepth 4 -type f -path '*/.harness/progress.md' -print0 2>/dev/null)
fi

if [[ ${#progress_files[@]} -eq 0 ]]; then
  echo "no .harness/progress.md files found in the scanned roots."
  exit 0
fi

# ── counters ────────────────────────────────────────────────────────────────

total_reviews=0
reviews_nif=0
reviews_findings=0
xmodel_runs=0
xmodel_fallbacks=0
xmodel_nif=0
xmodel_findings=0
dbf_fixed=0
dbf_aborted=0
compactions=0

# Per-project breakdown rows: "name|reviews|nif|findings|dbf_fixed|dbf_aborted|compactions"
per_project=()

# Date range across all files (for per-week normalisation)
oldest_epoch=""
newest_epoch=""

ep() {
  # Convert YYYY-MM-DD to epoch seconds. BSD date (macOS) vs GNU date (linux).
  local d="$1"
  date -j -f "%Y-%m-%d" "$d" "+%s" 2>/dev/null || date -d "$d" "+%s" 2>/dev/null || echo ""
}

for f in "${progress_files[@]}"; do
  # Derive a short project label: the parent of .harness
  proj_dir="$(dirname "$(dirname "$f")")"
  proj_name="$(basename "$proj_dir")"

  # /review lines — cross-model variants counted separately
  pf_reviews=$(grep -cE '/review ' "$f" 2>/dev/null || echo 0)
  pf_reviews=$(echo "$pf_reviews" | tr -d '[:space:]')
  pf_nif=$(grep -cE '/review[^—]*— task [0-9]+:.*NO ISSUES FOUND' "$f" 2>/dev/null | tr -d '[:space:]')
  pf_findings=$(grep -cE '/review[^—]*— task [0-9]+:.*(defect found|failing test|findings)' "$f" 2>/dev/null | tr -d '[:space:]')
  pf_xmodel_ran=$(grep -cE '/review \(cross-model\) — task' "$f" 2>/dev/null | tr -d '[:space:]')
  pf_xmodel_fall=$(grep -cE '/review \(cross-model fallback\) — task' "$f" 2>/dev/null | tr -d '[:space:]')
  pf_xmodel_nif=$(grep -cE '/review \(cross-model\) — task [0-9]+:.*NO ISSUES FOUND' "$f" 2>/dev/null | tr -d '[:space:]')
  pf_xmodel_find=$(grep -cE '/review \(cross-model\) — task [0-9]+:.*(defect found|failing test|findings)' "$f" 2>/dev/null | tr -d '[:space:]')

  pf_dbf_fixed=$(grep -cE 'dependabot-fixer:.*fixed in [0-9]+ iteration' "$f" 2>/dev/null | tr -d '[:space:]')
  pf_dbf_aborted=$(grep -cE 'dependabot-fixer:.*ABORTED' "$f" 2>/dev/null | tr -d '[:space:]')
  pf_compactions=$(grep -cE '^## compaction event — ' "$f" 2>/dev/null | tr -d '[:space:]')

  total_reviews=$((total_reviews + pf_reviews))
  reviews_nif=$((reviews_nif + pf_nif))
  reviews_findings=$((reviews_findings + pf_findings))
  xmodel_runs=$((xmodel_runs + pf_xmodel_ran))
  xmodel_fallbacks=$((xmodel_fallbacks + pf_xmodel_fall))
  xmodel_nif=$((xmodel_nif + pf_xmodel_nif))
  xmodel_findings=$((xmodel_findings + pf_xmodel_find))
  dbf_fixed=$((dbf_fixed + pf_dbf_fixed))
  dbf_aborted=$((dbf_aborted + pf_dbf_aborted))
  compactions=$((compactions + pf_compactions))

  per_project+=("$proj_name|$pf_reviews|$pf_nif|$pf_findings|$pf_dbf_fixed|$pf_dbf_aborted|$pf_compactions")

  # Pull first and last dated entries from the file for the span calculation.
  first_date=$(grep -oE '^20[0-9]{2}-[0-9]{2}-[0-9]{2}' "$f" 2>/dev/null | head -1)
  last_date=$(grep -oE '^20[0-9]{2}-[0-9]{2}-[0-9]{2}' "$f" 2>/dev/null | tail -1)
  # Compaction markers also carry dates in header form — pick those up too.
  first_compact=$(grep -oE '## compaction event — 20[0-9]{2}-[0-9]{2}-[0-9]{2}' "$f" 2>/dev/null | head -1 | grep -oE '20[0-9]{2}-[0-9]{2}-[0-9]{2}')
  last_compact=$(grep -oE '## compaction event — 20[0-9]{2}-[0-9]{2}-[0-9]{2}' "$f" 2>/dev/null | tail -1 | grep -oE '20[0-9]{2}-[0-9]{2}-[0-9]{2}')

  for d in "$first_date" "$first_compact"; do
    [[ -z "$d" ]] && continue
    e=$(ep "$d"); [[ -z "$e" ]] && continue
    if [[ -z "$oldest_epoch" || $e -lt $oldest_epoch ]]; then oldest_epoch=$e; fi
  done
  for d in "$last_date" "$last_compact"; do
    [[ -z "$d" ]] && continue
    e=$(ep "$d"); [[ -z "$e" ]] && continue
    if [[ -z "$newest_epoch" || $e -gt $newest_epoch ]]; then newest_epoch=$e; fi
  done
done

# ── derive spans and percentages ────────────────────────────────────────────

days=""
if [[ -n "$oldest_epoch" && -n "$newest_epoch" ]]; then
  days=$(( (newest_epoch - oldest_epoch) / 86400 ))
  (( days < 1 )) && days=1
fi

pct() {
  local num=$1 den=$2
  if [[ $den -eq 0 ]]; then echo "—"; else echo "$(( num * 100 / den ))%"; fi
}

nif_pct=$(pct "$reviews_nif" "$total_reviews")
nif_num="${nif_pct%\%}"
nif_band="—"
if [[ "$nif_pct" != "—" ]]; then
  if   (( nif_num < 30 )); then nif_band="← below band [30–70] — reviewer may be too paranoid"
  elif (( nif_num > 70 )); then nif_band="← above band [30–70] — reviewer may be rubber-stamping"
  else nif_band="← within band [30–70]"; fi
fi

xmodel_total=$((xmodel_runs + xmodel_fallbacks))
xmodel_avail_pct=$(pct "$xmodel_runs" "$xmodel_total")

dbf_total=$((dbf_fixed + dbf_aborted))
dbf_fixed_pct=$(pct "$dbf_fixed" "$dbf_total")

compactions_per_week=""
projects=${#progress_files[@]}
if [[ -n "$days" && $projects -gt 0 ]]; then
  weeks=$(( days / 7 ))
  (( weeks < 1 )) && weeks=1
  # compactions / (projects * weeks), expressed to two decimals via awk
  compactions_per_week=$(awk -v c="$compactions" -v p="$projects" -v w="$weeks" 'BEGIN { if (p*w==0) print "—"; else printf "%.2f", c/(p*w) }')
fi

# ── report ──────────────────────────────────────────────────────────────────

echo "== Harness telemetry =="
echo "Scanned: $projects project(s)${days:+, $days day(s) span}"
echo ""
echo "/review"
printf "  Total runs:              %d\n" "$total_reviews"
printf "  No issues found:         %d (%s) %s\n" "$reviews_nif" "$nif_pct" "$nif_band"
printf "  Findings:                %d (%s)\n" "$reviews_findings" "$(pct "$reviews_findings" "$total_reviews")"
if [[ $xmodel_total -gt 0 ]]; then
  printf "  Cross-model availability: %d/%d (%s)\n" "$xmodel_runs" "$xmodel_total" "$xmodel_avail_pct"
  printf "  Cross-model findings:     %d / %d runs\n" "$xmodel_findings" "$xmodel_runs"
fi
echo ""

if [[ $dbf_total -gt 0 ]]; then
  echo "dependabot-fixer"
  printf "  Total invocations:       %d\n" "$dbf_total"
  printf "  Fixed:                   %d (%s)\n" "$dbf_fixed" "$dbf_fixed_pct"
  printf "  Aborted:                 %d (%s) ← honest-abort working as designed\n" "$dbf_aborted" "$(pct "$dbf_aborted" "$dbf_total")"
  echo ""
fi

if [[ $compactions -gt 0 ]]; then
  echo "compaction events"
  printf "  Total:                   %d\n" "$compactions"
  if [[ -n "$compactions_per_week" ]]; then
    printf "  Per project per week:    %s\n" "$compactions_per_week"
  fi
  echo ""
fi

# Warnings
warnings=()
if [[ "$nif_pct" != "—" ]]; then
  if   (( nif_num > 70 )); then warnings+=("reviewer NIF rate > 70% — likely rubber-stamping, audit adversarial-reviewer.md framing")
  elif (( nif_num < 30 )) && (( total_reviews >= 10 )); then
    warnings+=("reviewer NIF rate < 30% on 10+ reviews — likely too paranoid or verification gates are too loose")
  fi
fi
if [[ $xmodel_total -gt 0 ]]; then
  avail_num="${xmodel_avail_pct%\%}"
  if [[ "$xmodel_avail_pct" != "—" ]] && (( avail_num < 50 )); then
    warnings+=("cross-model fell back on > 50% of /review runs — check gemini CLI auth")
  fi
fi

if [[ ${#warnings[@]} -gt 0 ]]; then
  echo "== Warnings =="
  for w in "${warnings[@]}"; do echo "  ⚠  $w"; done
  echo ""
fi

# Per-project breakdown (only in multi mode)
if [[ "$mode" == "multi" && ${#per_project[@]} -gt 1 ]]; then
  echo "== Per-project breakdown =="
  printf "  %-24s %8s %8s %10s %10s\n" "project" "reviews" "%NIF" "dbf(fix/ab)" "compactions"
  for row in "${per_project[@]}"; do
    IFS='|' read -r n r nif fnd df da c <<< "$row"
    proj_nif_pct=$(pct "$nif" "$r")
    printf "  %-24s %8d %8s %10s %10d\n" "$n" "$r" "$proj_nif_pct" "$df/$da" "$c"
  done
fi
