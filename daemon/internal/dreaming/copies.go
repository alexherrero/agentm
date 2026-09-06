package dreaming

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// Job "copies" — the port of dream.py's `_stage_suffix_backlog_drain`, the
// automatic lane that collapses content-identical copies. Every
// `status: active` memory (the curated `_always-load` notes excepted) is
// bucketed by its live body fingerprint; a bucket of two or more is a
// family; each family collapses into its canonical EARLIEST note (by
// `created`, path order for the legacy shape with no `created` at all):
// every copy is marked `lifecycle: superseded` + `superseded_by: <canonical rel>`,
// never deleted, and the survivor is left untouched. Families are ordered
// by their canonical rel and capped per pass, so a re-run against an
// unchanged corpus is idempotent — a collapsed family's copies drop out of
// the `active` grouping on their own.
//
// Two departures from the Python stage, both narrowing, both stated: the
// Python stage walks the whole vault minus its excluded dirs; this job walks
// the memory classes, which is the binary's authority. And the frontmatter
// patch is the byte-exact port, so the copy's new text matches what the
// Python stage would have written for the same note.

const (
	JobCopies          = "copies"
	DefaultCopiesCap   = 25 // _SUFFIX_BACKLOG_BATCH_CAP
	stageSuffixBacklog = "suffix_backlog_drain"
)

// Family is one collapse: the canonical survivor and its copies, in the
// order the Python stage lists them (canonical first, copies in walk order).
type Family struct {
	Canonical string   `json:"canonical"`
	Copies    []string `json:"copies"`
	Summary   string   `json:"summary"`
}

// CopiesPlan is what one pass would (or did) collapse.
type CopiesPlan struct {
	Intents  []Intent `json:"-"`
	Families []Family `json:"families"`
	// Considered is how many active memories were fingerprinted.
	Considered int `json:"considered"`
	// Deferred is how many families the cap left for the next pass.
	Deferred int `json:"deferred"`
}

type copyNote struct {
	rel     string
	raw     string
	created string
}

// PlanCopies reads the memory classes under `root` and decides the
// collapses. It writes nothing.
func PlanCopies(root string, cap int) (CopiesPlan, error) {
	var plan CopiesPlan
	if cap <= 0 {
		cap = DefaultCopiesCap
	}
	rels, err := MemoryNotes(root)
	if err != nil {
		return plan, err
	}
	byFingerprint := map[string][]copyNote{}
	var order []string
	for _, rel := range rels {
		if strings.Contains("/"+rel+"/", "/_always-load/") {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(rel)))
		if err != nil {
			continue
		}
		text := string(raw)
		fm, _ := ParseFrontmatter(text)
		if fm["status"] != "active" {
			continue
		}
		// The lifecycle axis has already settled these: a superseded or
		// archived note is out of everyday search, a pinned one is the
		// operator's word. None of them is a copy to collapse.
		switch strings.ToLower(strings.TrimSpace(fm["lifecycle"])) {
		case "superseded", "archived", "pinned":
			continue
		}
		plan.Considered++
		fp := LiveFingerprint(text)
		if _, seen := byFingerprint[fp]; !seen {
			order = append(order, fp)
		}
		byFingerprint[fp] = append(byFingerprint[fp], copyNote{rel: rel, raw: text, created: strings.TrimSpace(fm["created"])})
	}
	canonicalOf := func(family []copyNote) copyNote {
		sorted := make([]copyNote, len(family))
		copy(sorted, family)
		sort.SliceStable(sorted, func(i, j int) bool {
			a, b := sorted[i], sorted[j]
			// (0 if created else 1, created, path)
			ka, kb := 1, 1
			if a.created != "" {
				ka = 0
			}
			if b.created != "" {
				kb = 0
			}
			if ka != kb {
				return ka < kb
			}
			if a.created != b.created {
				return a.created < b.created
			}
			return a.rel < b.rel
		})
		return sorted[0]
	}
	var families [][]copyNote
	for _, fp := range order {
		if members := byFingerprint[fp]; len(members) >= 2 {
			families = append(families, members)
		}
	}
	sort.SliceStable(families, func(i, j int) bool {
		return canonicalOf(families[i]).rel < canonicalOf(families[j]).rel
	})
	if len(families) > cap {
		plan.Deferred = len(families) - cap
		families = families[:cap]
	}
	for _, family := range families {
		canonical := canonicalOf(family)
		var copies []copyNote
		for _, m := range family {
			if m.rel != canonical.rel {
				copies = append(copies, m)
			}
		}
		f := Family{Canonical: canonical.rel}
		noun := "copies"
		if len(copies) == 1 {
			noun = "copy"
		}
		f.Summary = fmt.Sprintf("%s + %d content-identical legacy %s — collapse into the earliest, mark the rest superseded",
			canonical.rel, len(copies), noun)
		for _, c := range copies {
			f.Copies = append(f.Copies, c.rel)
			// The contract's one shape for the relation (PLAN-superseded-vocabulary):
			// the loser carries `lifecycle: superseded` + `superseded_by:` naming
			// its successor; `status` is not touched, and `supersedes:` is only
			// ever the successor's back-link. The lifecycle journal records the
			// move like any other transition, through the intent's Meta.
			after := PatchFrontmatter(c.raw, []Update{{"lifecycle", "superseded"}, {"superseded_by", canonical.rel}})
			from := strings.TrimSpace(ParseFrontmatterValue(c.raw, "lifecycle"))
			if from == "" {
				from = lifecycleDefaultState
			}
			plan.Intents = append(plan.Intents, Intent{Job: JobCopies, Rel: c.rel, Before: []byte(c.raw), After: []byte(after),
				Summary: f.Summary, Meta: map[string]string{"from": from, "to": "superseded", "reason": "content-identical copy of " + canonical.rel}})
		}
		plan.Families = append(plan.Families, f)
	}
	return plan, nil
}
