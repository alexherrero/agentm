package meters

import (
	"sort"
)

// Which notes are too similar to be independent memories, and what kind of
// too-similar they are.
//
// The meters say the corpus is converging. This says where, which is what an
// action needs — the design's correction step works "in order of severity", and
// severity is a property of a specific cluster rather than of the corpus-wide
// number.
//
// # The threshold, measured before it was chosen
//
// 0.95, and the number comes off the corrected filed corpus rather than from
// intuition. On the 494 filed live memories in the memory space:
//
//	pairwise median   0.4324
//	pairwise p90      0.6008
//	pairwise max      0.9557
//
// So 0.95 sits at the extreme tail: two pairs, four notes, 0.8% of the window.
// Intuition would have said 0.90, which on the population the meters were
// *previously* reading swept 451 of 500 notes into a cluster — the wrong
// threshold over the wrong corpus, twice wrong in the same direction.
//
// It is a parameter and not a constant for the reason the plan gives about sample
// sizes: a number that decides what gets rewritten belongs on the command line
// with a stated default, not buried where nobody can see it move.
//
// # Why single linkage, and why it says so
//
// Connected components: A and C land in one cluster when both are close to B even
// if they are not close to each other. That is right for reporting — five notes
// collapsed onto one pattern are one finding, not ten pair findings — and wrong
// if a caller reads membership as "these are interchangeable". So a chained
// cluster says so: `MinSim` is the loosest pair anywhere in it, and `Chained` is
// true when that falls under the threshold. A caller that wants only tight
// clusters can filter on it; one that ignores it gets a number that shows the
// chain.

// ClusterKind says what a cluster is. Two of the four say "cannot tell", which is
// the point: an action that deletes or rewrites needs to know the difference
// between distinct sources flattened together and provenance nobody recorded.
type ClusterKind string

const (
	// KindDuplicate — every member shares a provenance unit. The same material
	// filed more than once. The design's first severity: merge.
	KindDuplicate ClusterKind = "duplicate"
	// KindCollapsed — every member has provenance and no two share any. Distinct
	// sources flattened into near-identical prose, which is the pattern-collapse
	// signature. The design's second severity: re-distill from source.
	KindCollapsed ClusterKind = "collapsed"
	// KindMixed — some members share provenance, some do not. Both severities are
	// present in one cluster and neither action is right for all of it. Reported,
	// never acted on: re-distilling two notes from one source produces two notes
	// from one source, which the merge arm then finds again.
	KindMixed ClusterKind = "mixed"
	// KindUnknown — at least one member records no provenance at all. Not a
	// finding about the notes, a finding about the metadata. The whole corpus
	// currently looks like this outside the reference notes, so this is the
	// common case rather than the edge one.
	KindUnknown ClusterKind = "unknown"
)

// Actionable reports whether a kind licenses an automatic action.
//
// A method rather than a caller-side switch, so "mixed and unknown are
// review-only" is stated once. Adding a fifth kind that nobody classified then
// defaults to not-actionable, which is the safe direction.
func (k ClusterKind) Actionable() bool {
	return k == KindDuplicate || k == KindCollapsed
}

// Note is one member of the population, as the clusterer needs it.
type Note struct {
	Rel string
	// Vec is the whole-note embedding. A note without one cannot be clustered and
	// is not silently treated as distant — see Clusters.
	Vec []float32
	// Provenance is every unit this note came from: its `source`, plus each entry
	// of `derived_from`. Both, because the two fields carry provenance for
	// different classes of note — a reference note has `source` and no
	// `derived_from`, a collapsed mining note has the reverse — and a classifier
	// reading only one of them reports `unknown` for half the corpus.
	Provenance []string
}

// Cluster is one group of too-similar notes.
type Cluster struct {
	Kind    ClusterKind `json:"kind"`
	Members []string    `json:"members"`
	// MinSim is the loosest pair anywhere in the cluster and MaxSim the tightest.
	// Both, because they answer different questions: MaxSim says how bad the worst
	// duplication is, MinSim says whether the cluster is really one thing.
	MinSim float64 `json:"min_sim"`
	MaxSim float64 `json:"max_sim"`
	// Chained is true when MinSim fell below the threshold — single linkage joined
	// two notes that are not themselves similar, through a third that is close to
	// both.
	Chained bool `json:"chained"`
	// Provenance carries each member's units, so a caller can report why the kind
	// came out as it did without re-reading the notes.
	Provenance map[string][]string `json:"provenance,omitempty"`
	// Why states the classification in words, for a digest line.
	Why string `json:"why"`
}

// Clusters groups the population at `threshold` and classifies each group.
//
// Returns ErrNoVectors rather than an empty slice when nothing can be measured,
// for the reason the two dense meters give: "no clusters found" and "could not
// look" are opposite findings, and a caller cannot tell them apart from a nil
// slice. A corpus with no duplication is the good outcome and a corpus with no
// vectors is a broken dense arm; reporting them identically means the good news
// is unfalsifiable.
func Clusters(notes []Note, threshold float64) ([]Cluster, error) {
	usable := make([]Note, 0, len(notes))
	for _, n := range notes {
		if len(n.Vec) > 0 {
			usable = append(usable, n)
		}
	}
	if len(usable) < 2 {
		return nil, ErrNoVectors
	}
	// Sorted before anything else. Union-find's output order otherwise depends on
	// input order, and two runs over the same corpus have to produce the same
	// clusters in the same order or every number downstream moves on its own.
	sort.Slice(usable, func(i, j int) bool { return usable[i].Rel < usable[j].Rel })

	vecs := make([][]float32, len(usable))
	for i, n := range usable {
		vecs[i] = n.Vec
	}
	unit := normalizeAll(vecs)

	sim := make([][]float64, len(unit))
	for i := range unit {
		sim[i] = make([]float64, len(unit))
	}
	parent := make([]int, len(unit))
	for i := range parent {
		parent[i] = i
	}
	var find func(int) int
	find = func(i int) int {
		for parent[i] != i {
			parent[i], i = parent[parent[i]], parent[i]
		}
		return i
	}
	for i := range unit {
		for j := i + 1; j < len(unit); j++ {
			s := dot(unit[i], unit[j])
			sim[i][j], sim[j][i] = s, s
			if s >= threshold {
				if a, b := find(i), find(j); a != b {
					parent[a] = b
				}
			}
		}
	}

	groups := map[int][]int{}
	for i := range unit {
		r := find(i)
		groups[r] = append(groups[r], i)
	}
	roots := make([]int, 0, len(groups))
	for r, members := range groups {
		if len(members) > 1 {
			roots = append(roots, r)
		}
	}
	// By first member's path, so the report is ordered by something a person can
	// find rather than by a union-find root index.
	sort.Slice(roots, func(a, b int) bool {
		return usable[groups[roots[a]][0]].Rel < usable[groups[roots[b]][0]].Rel
	})

	out := make([]Cluster, 0, len(roots))
	for _, r := range roots {
		// Already ascending: groups are appended in index order over a slice
		// sorted by path, so this is the sorted member list.
		idx := groups[r]
		c := Cluster{
			Members:    make([]string, 0, len(idx)),
			Provenance: map[string][]string{},
			MinSim:     1,
			MaxSim:     -1,
		}
		for _, i := range idx {
			c.Members = append(c.Members, usable[i].Rel)
			if p := usable[i].Provenance; len(p) > 0 {
				c.Provenance[usable[i].Rel] = p
			}
		}
		for a := 0; a < len(idx); a++ {
			for b := a + 1; b < len(idx); b++ {
				s := sim[idx[a]][idx[b]]
				if s < c.MinSim {
					c.MinSim = s
				}
				if s > c.MaxSim {
					c.MaxSim = s
				}
			}
		}
		c.Chained = c.MinSim < threshold
		c.Kind, c.Why = classify(idx, usable)
		out = append(out, c)
	}
	return out, nil
}

// classify decides what kind of too-similar a group is, from provenance alone.
//
// Deterministic and model-free. Whether two notes came from one source or two is
// a fact recorded in their frontmatter, and asking a model to guess at it would
// put a judgement call underneath an action that rewrites files.
func classify(idx []int, notes []Note) (ClusterKind, string) {
	sets := make([]map[string]bool, 0, len(idx))
	for _, i := range idx {
		if len(notes[i].Provenance) == 0 {
			return KindUnknown, "at least one member records no source or " +
				"derived_from, so which of the two this is cannot be told from the notes"
		}
		s := map[string]bool{}
		for _, p := range notes[i].Provenance {
			s[p] = true
		}
		sets = append(sets, s)
	}

	shared, disjoint := 0, 0
	for a := 0; a < len(sets); a++ {
		for b := a + 1; b < len(sets); b++ {
			if overlaps(sets[a], sets[b]) {
				shared++
			} else {
				disjoint++
			}
		}
	}
	switch {
	case disjoint == 0:
		return KindDuplicate, "every member shares a provenance unit — the same " +
			"material filed more than once"
	case shared == 0:
		return KindCollapsed, "every member has provenance and no two share any — " +
			"distinct sources in near-identical prose"
	default:
		return KindMixed, "some members share provenance and some do not, so no " +
			"one action is right for the whole cluster"
	}
}

// overlaps reports whether two provenance sets share a unit.
//
// Exact string equality, deliberately. The two real clusters in the live corpus
// are `.../DeepSeek-OCR` against `.../DeepSeek-OCR-2`, and `.../kimi-code`
// against `.../kimi-cli` — four distinct upstream projects, two pairs. A prefix
// or substring comparison calls each pair one source, which makes them
// duplicates, which stages a merge, which loses one of two real memories. The
// cheaper-looking comparison is the one that deletes things.
func overlaps(a, b map[string]bool) bool {
	for k := range a {
		if b[k] {
			return true
		}
	}
	return false
}
