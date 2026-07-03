# How to audit the MemoryVault for off-spec entries

> [!NOTE]
> **Goal:** Run the read-only vault lint, read the categorized report it writes under `_meta/`, and apply the suggested fixes by hand — the lint never edits the vault.
> **Prereqs:** agentm v4.9.0+ (ships V4 #33), `python3` on `PATH`, and a reachable vault (`MEMORY_VAULT_PATH` set, or pass `--vault PATH`). The lint reads only; it surfaces candidate fixes for you to review and apply.

## Steps

1. **Preview the findings (optional).** Run the lint and print findings to your terminal — `text` for skimming, `json` for piping:

   ```bash
   python3 harness/skills/memory/scripts/vault_lint.py --format text
   python3 harness/skills/memory/scripts/vault_lint.py --format json
   ```

   Narrow the corpus with `--scope` (`all` · `always-load` · `projects` · `personal`; default `all`). Point at a specific vault with `--vault PATH` if `MEMORY_VAULT_PATH` is unset.

2. **Write the audit report.** Add `--audit` to write a grouped operator-review report instead of printing:

   ```bash
   python3 harness/skills/memory/scripts/vault_lint.py --audit
   ```

   The report lands at `<vault>/_meta/vault-lint-<YYYY-MM-DD>.md` (override with `--out PATH`). The command prints a one-line summary (`N error · N warn · N info across N entries → <path>`). This file is the **only** thing the lint writes — it never touches an entry.

3. **Read the report.** Open `<vault>/_meta/vault-lint-<YYYY-MM-DD>.md`. Findings are grouped by severity (Errors → Warnings → Info), then by check, then collapsed by identical message so a repeated pattern (e.g. one unknown key across 30 entries) shows once with the affected entry list. Each finding names the entry path and a suggested fix.

4. **Apply fixes by hand.** Edit the flagged entries yourself. The lint applies nothing — every suggestion is advisory and operator-gated. Re-run step 1 to confirm a clean pass.

For what each finding's `check_id` and `severity` mean, see [Vault lint checks](Vault-Lint-Checks).

## Related

- [Vault lint checks reference](Vault-Lint-Checks) — the check catalog: id / severity / what each checks / suggested-fix shape.
