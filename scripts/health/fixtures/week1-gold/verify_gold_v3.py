#!/usr/bin/env python3
"""Task 2 verification: v3 differs from v2 in exactly the enumerated
entries/fields, entry count is unchanged, and every expected path/prefix in
v3 resolves against the post-purge live vault.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
V2_PATH = HERE / "gold-set-v2.json"
V3_PATH = HERE / "gold-set-v3.json"

_REPO_SCRIPTS = HERE.parent.parent.parent  # .../scripts/health/fixtures/week1-gold -> .../scripts
if str(_REPO_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_REPO_SCRIPTS))
import harness_memory  # noqa: E402  (never hardcode the vault path — see AGENTS.md)


def resolve_vault_root() -> Path:
    """`expected_note_paths`/`expected_note_prefixes` are vault-root-relative
    (`Agent/...`), so this needs `vault_path()`, not `memory_root()` — the
    latter already points *inside* `Agent/` and would double the prefix.
    """
    p = harness_memory.vault_path()
    if p is None or not Path(p).is_dir():
        raise SystemExit(
            "[verify_gold_v3] no reachable vault. Set "
            "plugins.obsidian-vault.vault_path via `agentm_config --vault-path` "
            "or export $MEMORY_VAULT_PATH to the vault root (not Agent/)."
        )
    return Path(p)


VAULT_ROOT = resolve_vault_root()

TOUCHED_IDS = {"dt07", "pp09", "pp10", "ep08", "pp16", "ep07", "pp07", "pp17"}
HOOK_ANNOTATED = {"dt01", "ep10", "ep12"}
ALLOWED_NEW_FIELDS = {"expected_note_prefixes", "hook_reachable", "layer", "v3_note"}


def main():
    v2 = json.loads(V2_PATH.read_text())
    v3 = json.loads(V3_PATH.read_text())

    errors = []

    if len(v2["entries"]) != 84 or len(v3["entries"]) != 84:
        errors.append(f"entry count changed: v2={len(v2['entries'])} v3={len(v3['entries'])}")

    v2_by_id = {e["id"]: e for e in v2["entries"]}
    v3_by_id = {e["id"]: e for e in v3["entries"]}

    if v2_by_id.keys() != v3_by_id.keys():
        errors.append(f"id set changed: {v2_by_id.keys() ^ v3_by_id.keys()}")

    changed_ids = set()
    for qid in v2_by_id.keys() & v3_by_id.keys():
        a, b = v2_by_id[qid], v3_by_id[qid]
        if a != b:
            changed_ids.add(qid)
            # every changed key on a non-touched id must be an eval-side
            # policy annotation only (Group A hook_reachable / negative layer)
            all_keys = set(a.keys()) | set(b.keys())
            diffkeys = {k for k in all_keys if a.get(k) != b.get(k)}
            if qid not in TOUCHED_IDS:
                unexpected = diffkeys - ALLOWED_NEW_FIELDS
                if unexpected:
                    errors.append(f"{qid}: unexpected field change(s) {unexpected} on an untouched id")
                if qid in HOOK_ANNOTATED:
                    if b.get("hook_reachable") is not False:
                        errors.append(f"{qid}: expected hook_reachable=false")
                elif b.get("stratum") == "negative":
                    if b.get("layer") != "gate-only":
                        errors.append(f"{qid}: expected layer=gate-only")
                else:
                    errors.append(f"{qid}: changed but not in TOUCHED_IDS, HOOK_ANNOTATED, or negative")

    expected_changed = TOUCHED_IDS | HOOK_ANNOTATED | {
        e["id"] for e in v2["entries"] if e["stratum"] == "negative"
    }
    if changed_ids != expected_changed:
        missing = expected_changed - changed_ids
        extra = changed_ids - expected_changed
        if missing:
            errors.append(f"expected changes missing for: {missing}")
        if extra:
            errors.append(f"unexpected changes for: {extra}")

    negatives = [e for e in v3["entries"] if e["stratum"] == "negative"]
    if len(negatives) != 20:
        errors.append(f"expected 20 negatives, found {len(negatives)}")
    if not all(e.get("layer") == "gate-only" for e in negatives):
        errors.append("not all negatives carry layer=gate-only")

    # Every expected path/prefix in v3 must resolve against the live vault.
    missing_paths = []
    for e in v3["entries"]:
        for p in e.get("expected_note_paths", []):
            if not (VAULT_ROOT / p).is_file():
                missing_paths.append((e["id"], p))
        for prefix in e.get("expected_note_prefixes", []):
            matches = list((VAULT_ROOT / prefix).parent.glob(f"{Path(prefix).name}*")) if prefix.endswith("/") is False else None
            target_dir = VAULT_ROOT / prefix
            if not target_dir.is_dir() or not any(target_dir.rglob("*.md")):
                missing_paths.append((e["id"], prefix + " (prefix, no notes found)"))

    if missing_paths:
        for qid, p in missing_paths:
            errors.append(f"{qid}: expected path/prefix does not resolve: {p}")

    if errors:
        print(f"VERIFY FAILED — {len(errors)} problem(s):")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    print(f"VERIFY OK — {len(changed_ids)} entries changed (8 relabeled/expanded + "
          f"3 hook_reachable + 20 gate-only, with pp17 overlapping the 8), "
          f"84 entries in both, all expected paths/prefixes resolve against the live vault.")


if __name__ == "__main__":
    main()
