package index

import (
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/note"
)

// Altitude separates a note that states something durable from one that records
// a moment. A convention and a distilled meeting are both `type: workflow`, and
// they should not rank alike on a general question.
//
// No note in this corpus carries the field yet — enrichment assigns it, and
// enrichment is the next part. So the property these tests have to pin hardest
// is not what dampening does to an artifact, but what it does to a corpus that
// has never heard of altitude: nothing at all.

// withAltitudeDampening turns the class on for a test and restores it after.
// The shipped default is off — see config.AltitudeEnabled for the transitional
// defect that keeps it there — so a test about what dampening does has to ask
// for it, and TestTheDampeningIsOffUnlessAskedFor keeps the default honest.
func withAltitudeDampening(t *testing.T) {
	t.Helper()
	before := note.AltitudeDampening()
	note.SetAltitudeDampening(true)
	t.Cleanup(func() { note.SetAltitudeDampening(before) })
}

func indexAltitude(t *testing.T, idx *Index, rel, title, altitude, body string) {
	t.Helper()
	raw := "---\ntitle: " + title + "\nstatus: active\n"
	if altitude != "" {
		raw += "altitude: " + altitude + "\n"
	}
	raw += "---\n\n" + body
	n := note.Parse(rel, raw, time.Now())
	if err := idx.Upsert(n, time.Now().UnixNano(), int64(len(raw))); err != nil {
		t.Fatalf("indexing %s: %v", rel, err)
	}
}

// The whole point: same words, different altitude, different rank.
func TestAnArtifactRanksBelowACanonicalNoteWithTheSameWords(t *testing.T) {
	withAltitudeDampening(t)

	idx := openScratch(t)
	body := "The staging gate runs before the deployment finishes.\n"
	indexAltitude(t, idx, "Agent/memory/semantic/exhaust.md", "Gate", "artifact", body)
	indexAltitude(t, idx, "Agent/memory/semantic/rule.md", "Gate", "canonical", body)

	out, err := idx.Search(Query{Text: "staging gate deployment", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if len(out.Results) < 2 {
		t.Fatalf("expected both notes, got %v — the fixture cannot show an ordering",
			resultPaths(out.Results))
	}
	if !strings.HasSuffix(out.Results[0].Path, "rule.md") {
		t.Errorf("session exhaust outranked the convention it came from: %v",
			resultPaths(out.Results))
	}
	if !strings.HasSuffix(out.Results[1].Path, "exhaust.md") {
		t.Errorf("the artifact is not present at all; demote never means exclude: %v",
			resultPaths(out.Results))
	}
}

// The lift: a question that asks for the artifact shape stops paying for it.
//
// Asserted as the removal of a multiplier rather than as a rank inversion,
// because removal is what the mechanism promises. Whether the artifact then
// *beats* the canonical note is BM25's business, and a test that demanded it
// would be asserting something this change does not do.
func TestAQuestionAskingForTheShapeUndampensIt(t *testing.T) {
	withAltitudeDampening(t)

	idx := openScratch(t)
	body := "The staging gate runs before the deployment finishes in the meeting.\n"
	indexAltitude(t, idx, "Agent/memory/episodic/exhaust.md", "Gate", "artifact", body)

	general, err := idx.Search(Query{Text: "staging gate deployment", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if len(general.Results) != 1 {
		t.Fatalf("expected the artifact, got %v", resultPaths(general.Results))
	}
	g := general.Results[0]
	if g.Score >= g.RawScore {
		t.Fatalf("the artifact was not dampened on a general question at all "+
			"(score %v, raw %v), so this test cannot show a lift", g.Score, g.RawScore)
	}

	asked, err := idx.Search(Query{Text: "staging gate deployment meeting", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if len(asked.Results) != 1 {
		t.Fatalf("the artifact vanished from a question asking for it: %v",
			resultPaths(asked.Results))
	}
	a := asked.Results[0]
	if a.Score != a.RawScore {
		t.Errorf("asking for the shape left the dampening on: score %v, raw %v",
			a.Score, a.RawScore)
	}
	// And the row still says what it is. The lift changes rank, not identity.
	if !strings.Contains(a.Penalty, note.ClassArtifact) {
		t.Errorf("the lifted row stopped reporting its class: %q", a.Penalty)
	}
}

// The property that matters on today's corpus, where zero notes carry the field.
//
// The design makes `artifact` the default so that `canonical` has to be earned,
// and it would be easy to read that as "treat an absent field as artifact."
// Doing so would apply a multiplier to every row in the corpus — which is not
// the no-op it looks like, because the negative-IDF clamp only fires on rows
// whose score went negative. The default belongs to enrichment, which writes the
// field; the ranker only reads what is written.
func TestANoteWithNoAltitudeIsNotTreatedAsAnArtifact(t *testing.T) {
	idx := openScratch(t)
	body := "The staging gate runs before the deployment finishes.\n"
	indexAltitude(t, idx, "Agent/memory/semantic/silent.md", "Gate", "", body)
	indexAltitude(t, idx, "Agent/memory/semantic/stated.md", "Gate", "canonical", body)

	var flags string
	if err := idx.db.QueryRow(`SELECT flags FROM docmeta WHERE path = ?`,
		"Agent/memory/semantic/silent.md").Scan(&flags); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(flags, note.ClassArtifact) {
		t.Errorf("a note that says nothing about altitude was classed an artifact: %q",
			flags)
	}

	out, err := idx.Search(Query{Text: "staging gate deployment", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if len(out.Results) != 2 {
		t.Fatalf("expected both notes, got %v", resultPaths(out.Results))
	}
	// Not "the two scores match" — they carry different frontmatter, so they are
	// different documents and BM25 length-normalization separates them. The claim
	// is narrower and exactly right: neither row had a multiplier applied.
	for _, r := range out.Results {
		if r.Score != r.RawScore {
			t.Errorf("%s was multiplied (score %v, raw %v) with no altitude to "+
				"multiply it by", r.Path, r.Score, r.RawScore)
		}
	}
}

func TestQueryWantsArtifact(t *testing.T) {
	for _, tc := range []struct {
		text string
		want bool
	}{
		{"what did we decide in the meeting?", true},
		{"notes from that session", true},
		{"what was discussed about caching", true},
		{"what is my convention for vault paths", false},
		{"how does the staging gate work", false},
		{"", false},
		// Punctuation must not hide the word, and case must not either.
		{"Transcripts.", true},
		{"MEETING", true},
	} {
		if got := note.QueryWantsArtifact(tc.text); got != tc.want {
			t.Errorf("QueryWantsArtifact(%q) = %v, want %v", tc.text, got, tc.want)
		}
	}
}

// Off unless asked for, and the reason is a defect rather than caution.
//
// Capture writes `altitude: artifact` on every new note. The ranker dampens only
// a note that says so — the right call for the clamp reason in
// TestANoteWithNoAltitudeIsNotTreatedAsAnArtifact, but it means a labelled note
// is dampened while an unlabelled one is not. On this corpus 15,479 notes predate
// the field and every new capture carries it, so the live effect is to penalize
// having been captured recently. Enrichment is what labels the corpus; until it
// has, dampening the labelled minority is backwards.
func TestTheDampeningIsOffUnlessAskedFor(t *testing.T) {
	if note.AltitudeDampening() {
		t.Fatal("altitude dampening is on by default")
	}
	idx := openScratch(t)
	body := "The staging gate runs before the deployment finishes.\n"
	indexAltitude(t, idx, "Agent/memory/semantic/exhaust.md", "Gate", "artifact", body)

	var flags string
	if err := idx.db.QueryRow(`SELECT flags FROM docmeta WHERE path = ?`,
		"Agent/memory/semantic/exhaust.md").Scan(&flags); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(flags, note.ClassArtifact) {
		t.Errorf("a note was classed an artifact with dampening off: %q", flags)
	}

	out, err := idx.Search(Query{Text: "staging gate deployment", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if len(out.Results) != 1 {
		t.Fatalf("expected the note, got %v", resultPaths(out.Results))
	}
	if r := out.Results[0]; r.Score != r.RawScore {
		t.Errorf("the note was multiplied with dampening off: score %v, raw %v",
			r.Score, r.RawScore)
	}
}
