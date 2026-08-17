#!/usr/bin/env python3
"""Build gold-set-v3.json from gold-set-v2.json.

One-shot authoring script for _harness/PLAN.md task 2 (goldv3 rebaseline).
Applies exactly the nine operator-approved changes from
`_harness/goldv3-diagnosis.md` (per-question verdict tables, Groups B/C) plus
the two eval-side policy annotations (hook_reachable, layer). Kept in the repo
so the exact transform is auditable and re-runnable — not a throwaway.

v2 is read-only; this only ever writes gold-set-v3.json.
"""
import copy
import json
from pathlib import Path

HERE = Path(__file__).parent
V2_PATH = HERE / "gold-set-v2.json"
V3_PATH = HERE / "gold-set-v3.json"


def main():
    v2 = json.loads(V2_PATH.read_text())
    v3 = copy.deepcopy(v2)

    v3["$schema_note"] = (
        v2["$schema_note"]
        + " v3 (2026-08-17): post-decontamination relabel, see "
        "_harness/goldv3-diagnosis.md and NOTES.md's changeover entry. New "
        "optional entry fields: `expected_note_prefixes` (folder-level accept, "
        "alongside exact `expected_note_paths`), `hook_reachable` (false when "
        "every expected note lives in a hook-excluded subtree — excluded from "
        "the hook arm's denominator, reported separately), `layer` "
        "(`gate-only` on negatives — rejection is scored at the deliberate-path "
        "gate, not this layer; reported as a separate block, not inside R@5's "
        "sweep)."
    )

    by_id = {e["id"]: e for e in v3["entries"]}

    # --- Group B: label defects (expand or relabel the accept-set) ---

    by_id["dt07"]["expected_note_paths"] = [
        "Agent/desk/projects/_global/wiki-style/2026-06-09-llm-tell-vocabulary.md",
        "Agent/desk/projects/blog/writing-voice.md",
    ]
    by_id["dt07"]["v3_note"] = (
        "Relabeled: the codified alternates to 'load-bearing' live in the "
        "wiki-style lesson (retrieval's own #1), not the blog voice profile. "
        "writing-voice.md kept as secondary."
    )

    by_id["pp09"]["expected_note_prefixes"] = ["Agent/external/primos/"]
    by_id["pp09"]["v3_note"] = (
        "Granularity fix: the question asks where the primos notes are kept, "
        "not for two specific files. Any note under external/primos/ counts."
    )

    by_id["pp10"]["expected_note_paths"] = by_id["pp10"]["expected_note_paths"] + [
        "Agent/memory/preferences/i-want-this-context-vault-to.md",
        "Agent/memory/preferences/i-want-to-create-the-vault.md",
        "Agent/desk/projects/_archive/memoryvault/conversations/2026-05-10-vault-design.md",
    ]
    by_id["pp10"]["v3_note"] = (
        "Expanded: the operator's own first-person 'why I want this vault' "
        "preference notes and the founding design conversation are better "
        "answers to 'remind me why I chose a vault' than the formalized "
        "convention note alone."
    )

    by_id["ep08"]["expected_note_paths"] = by_id["ep08"]["expected_note_paths"] + [
        "Agent/desk/projects/agentm/research/file-watching/reference/google-drive-tmp-drivedownload-temp-files.md",
        "Agent/memory/2026/08/vault-git-directory-sits-outside-the-drive-sync-set.md",
    ]
    by_id["ep08"]["v3_note"] = (
        "Expanded: wanted note is pre-hoc concurrency research; the "
        "cause-and-fix record of Drive thrash lives in the file-watching "
        "reference note and the git-outside-sync note, both returned. "
        "'googel' typo kept in the question — the dense arm handles it and "
        "it is a realistic robustness case."
    )

    by_id["pp16"]["expected_note_paths"] = by_id["pp16"]["expected_note_paths"] + [
        "Agent/desk/projects/agentm/_harness/designs/roadmap-research-2026-06/R06-token-efficiency.md",
        "Agent/desk/projects/agentm/decisions/research-token-efficiency-novel.md",
    ]
    by_id["pp16"]["question"] = (
        "How am I optimizing model cost despite the limitations of Claude and "
        "Antigravity in changing models on the fly automatically?"
    )
    by_id["pp16"]["v3_note"] = (
        "Expanded: R06-token-efficiency.md and research-token-efficiency-novel.md "
        "(returned #1/#2) are defensible answers to 'how am I optimizing model "
        "cost'. Fixed the two typos ('limitaitons', 'automtically') — they were "
        "decoy-magnets shared verbatim with the purged pp17 decoy note."
    )

    by_id["ep07"]["question"] = "When did I decide to begin my first AgentM development arc?"
    by_id["ep07"]["v3_note"] = (
        "Rewritten to disambiguate: the bare question collided with the "
        "blog's 'Agent M arc' memoir series (a defensible but unintended "
        "reading, returned #1). 'development arc' specifies the intended "
        "V4-roadmap reading."
    )

    # --- Group C: contaminated (purge fixed the corpus; pp07 also needs its
    # question text restored) ---

    by_id["pp17"]["question"] = (
        "In my developer workflows, tell me what I do and don't do "
        "automatically and why"
    )
    by_id["pp17"]["v3_note"] = (
        "Typo fixed ('automaticaly' -> 'automatically') — it was the exact "
        "verbatim token the purged decoy note shared with this question, and "
        "the reason the decoy ranked #1 ahead of the real answer. Label "
        "(autonomy-doctrine.md) stands unchanged."
    )

    by_id["pp07"]["question"] = (
        "Why did AgentM never fully realize the vault vision that I had, "
        "forcing me to do a second re-write when we got to FRIDAY?"
    )
    by_id["pp07"]["v3_note"] = (
        "Truncated tail restored ('...forcing me to do a second re-write when "
        "we got to FRIDAY') — dropped at authoring time, along with it the "
        "distinctive token FRIDAY that points directly at the two expected "
        "designs/friday/ notes. Both $inbox decoys purged from the corpus "
        "(they carried this question's original, un-restored text). Expected "
        "notes and stratum/source unchanged from v2 -- distinct from the "
        "separately-scoped $deferred entry, which targets a repo file this "
        "corpus cannot reach."
    )

    # --- Eval-side policy annotations (Group A + negatives) ---

    for qid in ("dt01", "ep10", "ep12"):
        by_id[qid]["hook_reachable"] = False
        by_id[qid]["v3_note"] = (
            "Group A: retrieval hits (rank 1/2/1 on +question); the hook arm's "
            "recall path excludes the subtree every expected note lives in "
            "(_archive/ / _inbox / scratch). Excluded from the hook arm's "
            "denominator, not counted as a generic miss; reported separately."
        )

    for e in v3["entries"]:
        if e["stratum"] == "negative":
            e["layer"] = "gate-only"

    negatives_annotated = sum(1 for e in v3["entries"] if e.get("layer") == "gate-only")
    assert negatives_annotated == 20, f"expected 20 negatives annotated, got {negatives_annotated}"
    assert len(v3["entries"]) == len(v2["entries"]) == 84

    V3_PATH.write_text(json.dumps(v3, indent=2) + "\n")
    print(f"Wrote {V3_PATH} — {len(v3['entries'])} entries")


if __name__ == "__main__":
    main()
