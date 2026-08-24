package meters

import (
	"errors"
	"math"
	"testing"
)

// Cluster detection. The properties that matter are about honesty rather than
// accuracy: the classifier's job is to say which of four things a cluster is,
// and two of the four are "cannot tell". Getting those two wrong means an
// automatic rewrite runs on a cluster nobody could classify.

// vec builds a unit-ish vector at a chosen angle in the first two dimensions, so
// a fixture can place notes at known similarities instead of hoping.
//
// Third dimension held at zero: cosine is then exactly cos(θa − θb), which makes
// every assertion in this file a number somebody chose rather than one the
// implementation produced.
func vec(deg float64) []float32 {
	r := deg * math.Pi / 180
	return []float32{float32(math.Cos(r)), float32(math.Sin(r)), 0}
}

func note(rel string, deg float64, prov ...string) Note {
	return Note{Rel: rel, Vec: vec(deg), Provenance: prov}
}

func TestTwoNotesAboveTheThresholdAreOneCluster(t *testing.T) {
	// 10 degrees apart: cos 10° = 0.985.
	got, err := Clusters([]Note{
		note("a.md", 0, "s1"), note("b.md", 10, "s1"),
	}, 0.95)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 {
		t.Fatalf("clusters = %d, want 1", len(got))
	}
	if len(got[0].Members) != 2 {
		t.Fatalf("members = %v, want both", got[0].Members)
	}
}

func TestTwoNotesBelowTheThresholdAreNotACluster(t *testing.T) {
	// 30 degrees apart: cos 30° = 0.866, under 0.95.
	got, err := Clusters([]Note{
		note("a.md", 0, "s1"), note("b.md", 30, "s1"),
	}, 0.95)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 0 {
		t.Fatalf("clusters = %v, want none — 0.866 is under the threshold", got)
	}
}

func TestTheThresholdIsTheThresholdAndNotAHardcodedNumber(t *testing.T) {
	// The same pair, two thresholds. Without this a constant compiled into
	// Clusters would pass every other test in the file.
	pair := []Note{note("a.md", 0, "s1"), note("b.md", 30, "s1")}
	loose, err := Clusters(pair, 0.80)
	if err != nil {
		t.Fatal(err)
	}
	tight, err := Clusters(pair, 0.95)
	if err != nil {
		t.Fatal(err)
	}
	if len(loose) != 1 || len(tight) != 0 {
		t.Fatalf("at 0.80 got %d clusters, at 0.95 got %d; want 1 and 0",
			len(loose), len(tight))
	}
}

func TestTheThresholdIsInclusive(t *testing.T) {
	// Exactly on the line. Two notes at the same angle have cosine exactly 1.0 —
	// `[1,0,0]` is exact in float32, its norm is exactly 1, and the dot product
	// of it with itself is exactly 1 — so a threshold of 1.0 is a real boundary
	// rather than one floating point rounds past.
	//
	// Without this, `s > threshold` and `s >= threshold` behave identically on
	// every other fixture in this file, and the difference is whether two
	// byte-identical notes are reported as duplicates at all.
	got, err := Clusters([]Note{
		note("a.md", 0, "s1"), note("b.md", 0, "s1"),
	}, 1.0)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 {
		t.Fatalf("clusters = %d, want 1 — two identical notes at a threshold of "+
			"1.0 are on the line, and the line is inclusive", len(got))
	}
}

func TestSharedProvenanceIsADuplicate(t *testing.T) {
	got := oneCluster(t, []Note{
		note("a.md", 0, "https://example.test/repo"),
		note("b.md", 5, "https://example.test/repo"),
	}, 0.95)
	if got.Kind != KindDuplicate {
		t.Fatalf("kind = %q, want %q — both came from one source", got.Kind, KindDuplicate)
	}
	if !got.Kind.Actionable() {
		t.Error("a duplicate is actionable; the design's first severity is merge")
	}
}

func TestDistinctProvenanceIsPatternCollapse(t *testing.T) {
	got := oneCluster(t, []Note{
		note("a.md", 0, "https://example.test/repo-one"),
		note("b.md", 5, "https://example.test/repo-two"),
	}, 0.95)
	if got.Kind != KindCollapsed {
		t.Fatalf("kind = %q, want %q — two sources, one voice", got.Kind, KindCollapsed)
	}
}

func TestProvenanceIsComparedExactlyAndNotByPrefix(t *testing.T) {
	// The live corpus's only two clusters, verbatim. `DeepSeek-OCR` against
	// `DeepSeek-OCR-2` and `kimi-code` against `kimi-cli` are four upstream
	// projects, not two. Any prefix, substring or normalized comparison calls
	// each pair one source, which makes them duplicates, which stages a merge —
	// and merging them loses one of two real memories.
	for _, tc := range []struct{ a, b string }{
		{"https://github.com/deepseek-ai/DeepSeek-OCR",
			"https://github.com/deepseek-ai/DeepSeek-OCR-2"},
		{"https://github.com/MoonshotAI/kimi-code",
			"https://github.com/MoonshotAI/kimi-cli"},
	} {
		got := oneCluster(t, []Note{
			note("a.md", 0, tc.a), note("b.md", 5, tc.b),
		}, 0.95)
		if got.Kind != KindCollapsed {
			t.Errorf("%s vs %s: kind = %q, want %q — these are different projects",
				tc.a, tc.b, got.Kind, KindCollapsed)
		}
	}
}

func TestAMemberWithNoProvenanceMakesTheClusterUnclassifiable(t *testing.T) {
	got := oneCluster(t, []Note{
		note("a.md", 0, "https://example.test/repo"),
		note("b.md", 5),
	}, 0.95)
	if got.Kind != KindUnknown {
		t.Fatalf("kind = %q, want %q", got.Kind, KindUnknown)
	}
	if got.Kind.Actionable() {
		t.Fatal("an unclassifiable cluster must not license an automatic rewrite")
	}
}

func TestAPartlySharedClusterIsMixedAndNotActionable(t *testing.T) {
	// a and b share; c does not. Both severities in one group, and neither action
	// is right for all of it: re-distilling a and b from one source produces two
	// notes from one source, which the merge arm then finds again.
	got := oneCluster(t, []Note{
		note("a.md", 0, "s1"), note("b.md", 4, "s1"), note("c.md", 8, "s2"),
	}, 0.95)
	if got.Kind != KindMixed {
		t.Fatalf("kind = %q, want %q", got.Kind, KindMixed)
	}
	if got.Kind.Actionable() {
		t.Fatal("mixed must not license an automatic rewrite")
	}
}

func TestDerivedFromCountsAsProvenance(t *testing.T) {
	// A reference note carries `source` and no `derived_from`; a collapsed mining
	// note carries the reverse. A classifier reading only one field reports
	// `unknown` for half the corpus.
	got := oneCluster(t, []Note{
		note("a.md", 0, "_inbox/raw-1.md"),
		note("b.md", 5, "_inbox/raw-1.md"),
	}, 0.95)
	if got.Kind != KindDuplicate {
		t.Fatalf("kind = %q, want %q — both derive from one raw capture",
			got.Kind, KindDuplicate)
	}
}

func TestOneSharedUnitAmongSeveralIsEnough(t *testing.T) {
	// Two notes distilled from overlapping capture sets are the same material,
	// even where each also draws on something the other does not.
	got := oneCluster(t, []Note{
		note("a.md", 0, "_inbox/x.md", "_inbox/y.md"),
		note("b.md", 5, "_inbox/y.md", "_inbox/z.md"),
	}, 0.95)
	if got.Kind != KindDuplicate {
		t.Fatalf("kind = %q, want %q — both include _inbox/y.md",
			got.Kind, KindDuplicate)
	}
}

func TestAChainIsOneClusterAndSaysItIsAChain(t *testing.T) {
	// a~b and b~c at 12 degrees each; a~c at 24 degrees is 0.913, under the
	// threshold. Single linkage joins all three, which is the right report — one
	// pattern, three notes — but a caller reading membership as "interchangeable"
	// would be wrong, so the cluster says so.
	got := oneCluster(t, []Note{
		note("a.md", 0, "s1"), note("b.md", 12, "s2"), note("c.md", 24, "s3"),
	}, 0.95)
	if len(got.Members) != 3 {
		t.Fatalf("members = %v, want all three", got.Members)
	}
	if !got.Chained {
		t.Errorf("Chained = false; MinSim %.4f is under the 0.95 threshold", got.MinSim)
	}
	if got.MinSim > 0.95 {
		t.Errorf("MinSim = %.4f, want the loosest pair (~0.913)", got.MinSim)
	}
	if got.MaxSim < 0.97 {
		t.Errorf("MaxSim = %.4f, want the tightest pair (~0.978)", got.MaxSim)
	}
}

func TestATightClusterIsNotReportedAsChained(t *testing.T) {
	// The negative half. Without it, `Chained: true` unconditionally passes the
	// test above.
	got := oneCluster(t, []Note{
		note("a.md", 0, "s1"), note("b.md", 4, "s2"), note("c.md", 8, "s3"),
	}, 0.95)
	if got.Chained {
		t.Fatalf("Chained = true at MinSim %.4f, which is over the threshold", got.MinSim)
	}
}

func TestTwoSeparateClustersStaySeparate(t *testing.T) {
	got, err := Clusters([]Note{
		note("a.md", 0, "s1"), note("b.md", 5, "s1"),
		note("c.md", 90, "s2"), note("d.md", 95, "s2"),
	}, 0.95)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 {
		t.Fatalf("clusters = %d, want 2 — the pairs are 90 degrees apart", len(got))
	}
}

func TestALoneNoteIsNotACluster(t *testing.T) {
	got, err := Clusters([]Note{
		note("a.md", 0, "s1"), note("b.md", 5, "s1"), note("far.md", 90, "s2"),
	}, 0.95)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 {
		t.Fatalf("clusters = %d, want 1", len(got))
	}
	for _, m := range got[0].Members {
		if m == "far.md" {
			t.Fatal("far.md is 90 degrees from everything and is in a cluster")
		}
	}
}

func TestNoVectorsIsRefusedRatherThanReportedAsNoClusters(t *testing.T) {
	// "No duplication found" is the good outcome and "the dense arm is missing"
	// is a broken index. A nil slice reports both identically, which makes the
	// good news unfalsifiable.
	for _, tc := range []struct {
		name  string
		notes []Note
	}{
		{"nothing at all", nil},
		{"one note", []Note{note("a.md", 0, "s1")}},
		{"no vectors", []Note{{Rel: "a.md"}, {Rel: "b.md"}}},
		{"one vector between two notes", []Note{note("a.md", 0, "s1"), {Rel: "b.md"}}},
	} {
		_, err := Clusters(tc.notes, 0.95)
		if !errors.Is(err, ErrNoVectors) {
			t.Errorf("%s: err = %v, want ErrNoVectors", tc.name, err)
		}
	}
}

func TestTwoRunsOverTheSameCorpusAgree(t *testing.T) {
	// Given in one order, then reversed. Union-find's output otherwise depends on
	// input order, and every number downstream moves with it.
	// Three clusters, not one. With a single cluster the ordering of the result
	// is unobservable, and dropping the sort over roots — which leaves the order
	// to Go's randomized map iteration — stays green.
	notes := []Note{
		note("z.md", 0, "s1"), note("m.md", 5, "s2"), note("a.md", 9, "s3"),
		note("q.md", 60, "s5"), note("b.md", 65, "s6"),
		note("h.md", 120, "s7"), note("c.md", 125, "s8"),
		note("far.md", 250, "s4"),
	}
	rev := make([]Note, len(notes))
	for i := range notes {
		rev[i] = notes[len(notes)-1-i]
	}
	first, err := Clusters(notes, 0.95)
	if err != nil {
		t.Fatal(err)
	}
	second, err := Clusters(rev, 0.95)
	if err != nil {
		t.Fatal(err)
	}
	if len(first) != len(second) {
		t.Fatalf("%d clusters then %d", len(first), len(second))
	}
	if len(first) != 3 {
		t.Fatalf("clusters = %d, want 3 — one cluster cannot show an ordering bug",
			len(first))
	}
	for i := range first {
		if len(first[i].Members) != len(second[i].Members) {
			t.Fatalf("cluster %d: %v vs %v", i, first[i].Members, second[i].Members)
		}
		for j := range first[i].Members {
			if first[i].Members[j] != second[i].Members[j] {
				t.Fatalf("cluster %d member %d: %q vs %q", i, j,
					first[i].Members[j], second[i].Members[j])
			}
		}
		if first[i].MinSim != second[i].MinSim || first[i].MaxSim != second[i].MaxSim {
			t.Fatalf("cluster %d: sims %v/%v vs %v/%v", i,
				first[i].MinSim, first[i].MaxSim, second[i].MinSim, second[i].MaxSim)
		}
	}
}

func TestClustersComeBackInPathOrder(t *testing.T) {
	// Stated forwards, because run-to-run comparison cannot see this: sorting the
	// clusters descending is perfectly deterministic and two runs agree on it.
	//
	// It matters because the digest lists these for a person to scan, and the
	// alternative to a path order is a union-find root index — a number with no
	// meaning outside this function.
	got, err := Clusters([]Note{
		note("zebra-1.md", 0, "s1"), note("zebra-2.md", 5, "s2"),
		note("apple-1.md", 60, "s3"), note("apple-2.md", 65, "s4"),
		note("mango-1.md", 120, "s5"), note("mango-2.md", 125, "s6"),
	}, 0.95)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 3 {
		t.Fatalf("clusters = %d, want 3", len(got))
	}
	want := []string{"apple-1.md", "mango-1.md", "zebra-1.md"}
	for i, w := range want {
		if got[i].Members[0] != w {
			t.Fatalf("cluster %d leads with %q, want %q — clusters are ordered by "+
				"their first member's path", i, got[i].Members[0], w)
		}
	}
}

func TestMembersAreSorted(t *testing.T) {
	got := oneCluster(t, []Note{
		note("z.md", 0, "s1"), note("a.md", 5, "s1"), note("m.md", 9, "s1"),
	}, 0.95)
	want := []string{"a.md", "m.md", "z.md"}
	for i, w := range want {
		if got.Members[i] != w {
			t.Fatalf("members = %v, want %v", got.Members, want)
		}
	}
}

func TestEveryClusterCarriesItsProvenanceAndAReason(t *testing.T) {
	// The digest line and the action's audit trail both read these. A cluster
	// with a kind and no reason is a verdict nobody can check.
	got := oneCluster(t, []Note{
		note("a.md", 0, "src-one"), note("b.md", 5, "src-two"),
	}, 0.95)
	if got.Why == "" {
		t.Error("Why is empty")
	}
	for _, rel := range []string{"a.md", "b.md"} {
		if len(got.Provenance[rel]) == 0 {
			t.Errorf("Provenance[%q] is empty", rel)
		}
	}
}

func TestNotesWithoutVectorsAreDroppedRatherThanTreatedAsDistant(t *testing.T) {
	// A note the embedder has not reached is unmeasured, not far away. Treating a
	// nil vector as a point would put every un-embedded note at the same place —
	// so they would cluster with each other, perfectly, and be reported as the
	// corpus's tightest duplication.
	got, err := Clusters([]Note{
		note("a.md", 0, "s1"), note("b.md", 5, "s1"),
		{Rel: "cold-1.md", Provenance: []string{"s2"}},
		{Rel: "cold-2.md", Provenance: []string{"s3"}},
	}, 0.95)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 {
		t.Fatalf("clusters = %d, want 1 — the two un-embedded notes are unmeasured",
			len(got))
	}
	for _, m := range got[0].Members {
		if m == "cold-1.md" || m == "cold-2.md" {
			t.Fatalf("members = %v; an un-embedded note is in a cluster", got[0].Members)
		}
	}
}

func TestAnUnrecognisedKindIsNotActionable(t *testing.T) {
	// The default direction. A kind added later and left out of Actionable's list
	// must not license a rewrite by omission.
	if ClusterKind("something-new").Actionable() {
		t.Fatal("an unrecognised kind is actionable")
	}
	if ClusterKind("").Actionable() {
		t.Fatal("the zero kind is actionable")
	}
}

func oneCluster(t *testing.T, notes []Note, threshold float64) Cluster {
	t.Helper()
	got, err := Clusters(notes, threshold)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 {
		t.Fatalf("clusters = %d, want exactly 1", len(got))
	}
	return got[0]
}
