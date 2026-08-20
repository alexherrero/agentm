package extract

import (
	"strings"
	"testing"
)

func has(t *testing.T, got []string, want string) {
	t.Helper()
	for _, g := range got {
		if strings.EqualFold(g, want) {
			return
		}
	}
	t.Errorf("expected alias %q in %v", want, got)
}

func hasNot(t *testing.T, got []string, unwanted string) {
	t.Helper()
	for _, g := range got {
		if strings.EqualFold(g, unwanted) {
			t.Errorf("did not expect alias %q in %v", unwanted, got)
		}
	}
}

// Both parenthesised forms contribute both halves. The point is not that the
// strings become findable — they are already in the body — but that they move
// into the alias column, which ranks above body, and the acronym is usually the
// rarer of the two.
func TestAcronymBothDirections(t *testing.T) {
	forward := Aliases("", "The Open Knowledge Format (OKF) requires a type field.")
	has(t, forward, "OKF")
	has(t, forward, "Open Knowledge Format")

	reverse := Aliases("", "We follow OKF (Open Knowledge Format) for portability.")
	has(t, reverse, "OKF")
	has(t, reverse, "Open Knowledge Format")
}

// The initials check is what separates an acronym from an ordinary
// parenthetical. Without it, any `(WORD)` in the text drags in whatever
// happened to precede it, confidently and wrongly.
func TestParentheticalThatIsNotAnAcronymIsIgnored(t *testing.T) {
	got := Aliases("", "Filing halts and the digest names the parse failure (SEE BELOW).")
	hasNot(t, got, "SEE BELOW")
	hasNot(t, got, "the parse failure")
}

func TestSingleLetterParentheticalIsIgnored(t *testing.T) {
	got := Aliases("", "The first option (A) is the one we took.")
	hasNot(t, got, "A")
}

// An expansion may carry small words the acronym omits.
func TestExpansionMaySkipConnectingWords(t *testing.T) {
	got := Aliases("", "The Department of Motor Vehicles (DMV) is the canonical example.")
	has(t, got, "DMV")
	has(t, got, "Department of Motor Vehicles")
}

// The tightest phrase that spells the acronym wins, not the longest one that
// happens to contain it.
func TestShortestMatchingExpansionWins(t *testing.T) {
	got := Aliases("", "Yesterday I read about the Open Knowledge Format (OKF).")
	has(t, got, "Open Knowledge Format")
	hasNot(t, got, "Yesterday I read about the Open Knowledge Format")
}

// The design's own example.
func TestSnakeCaseDecomposes(t *testing.T) {
	got := Aliases("", "The index has idx_timestamp_desc on the captured column.")
	has(t, got, "idx_timestamp_desc")
	has(t, got, "idx")
	has(t, got, "timestamp")
	has(t, got, "desc")
}

func TestCamelCaseDecomposes(t *testing.T) {
	got := Aliases("", "The noteType field and the StorageRules struct.")
	has(t, got, "noteType")
	has(t, got, "note")
	has(t, got, "Type")
	has(t, got, "StorageRules")
	has(t, got, "Storage")
	has(t, got, "Rules")
}

// A hyphen is ordinary English punctuation, and decomposing every hyphenated
// word is exactly the noise the cap exists to prevent, produced on purpose.
func TestHyphenatedProseIsNotDecomposed(t *testing.T) {
	got := Aliases("", "This is a well-known and fail-closed arrangement.")
	hasNot(t, got, "well")
	hasNot(t, got, "known")
	hasNot(t, got, "fail")
}

// Inside a code span a hyphen is unambiguously part of a name.
func TestKebabInsideACodeSpanIsAnIdentifier(t *testing.T) {
	got := Aliases("", "Run `check-storage-rules` before committing.")
	has(t, got, "check-storage-rules")
	has(t, got, "check")
	has(t, got, "storage")
	has(t, got, "rules")
}

func TestSingleWordInACodeSpanIsNotDecomposed(t *testing.T) {
	got := Aliases("", "The `capture` transaction is synchronous.")
	hasNot(t, got, "captur")
}

func TestOneCharacterFragmentsAreDropped(t *testing.T) {
	got := Aliases("", "The idx_a_b index is small.")
	has(t, got, "idx")
	hasNot(t, got, "a")
	hasNot(t, got, "b")
}

func TestStopwordFragmentsAreDropped(t *testing.T) {
	got := Aliases("", "See the_quick_thing for details.")
	hasNot(t, got, "the")
	has(t, got, "quick")
}

func TestTitleIsScannedToo(t *testing.T) {
	got := Aliases("The Open Knowledge Format (OKF)", "Body without the term.")
	has(t, got, "OKF")
}

// A derived field that varied between runs would make every rebuild a diff.
func TestDeterministic(t *testing.T) {
	text := "The Open Knowledge Format (OKF) uses idx_timestamp_desc and noteType " +
		"alongside `check-storage-rules` and StorageRules."
	first := Aliases("t", text)
	for i := 0; i < 20; i++ {
		again := Aliases("t", text)
		if len(again) != len(first) {
			t.Fatalf("run %d produced %d aliases, first produced %d", i, len(again), len(first))
		}
		for j := range first {
			if again[j] != first[j] {
				t.Fatalf("run %d differs at %d: %q vs %q", i, j, again[j], first[j])
			}
		}
	}
}

func TestSortedAndDeduplicated(t *testing.T) {
	got := Aliases("", "idx_timestamp_desc and idx_timestamp_desc again, plus IDX_TIMESTAMP_DESC.")
	for i := 1; i < len(got); i++ {
		if strings.ToLower(got[i-1]) > strings.ToLower(got[i]) {
			t.Errorf("not sorted at %d: %q then %q", i, got[i-1], got[i])
		}
	}
	seen := map[string]int{}
	for _, g := range got {
		seen[strings.ToLower(g)]++
	}
	for k, n := range seen {
		if n > 1 {
			t.Errorf("%q appears %d times", k, n)
		}
	}
}

// The alias column ranks above body, so it is a scarce resource. A note that
// contributes forty aliases has diluted it for itself and everything it competes
// with.
func TestCapped(t *testing.T) {
	var b strings.Builder
	for i := 0; i < 200; i++ {
		b.WriteString("some_identifier_number_")
		b.WriteByte(byte('a' + i%26))
		b.WriteString(" ")
	}
	got := Aliases("", b.String())
	if len(got) > MaxAliases {
		t.Errorf("produced %d aliases, cap is %d", len(got), MaxAliases)
	}
}

// Which aliases survive the cap must be a property of the note, not of the order
// the regexes happened to run in.
func TestTheCapIsStableUnderInputOrder(t *testing.T) {
	acronyms := "The Open Knowledge Format (OKF) and the Department of Motor Vehicles (DMV)."
	idents := "Fields idx_timestamp_desc, noteType, StorageRules, user_account_id, http_request_count."

	a := Aliases("", acronyms+" "+idents)
	b := Aliases("", idents+" "+acronyms)
	if len(a) != len(b) {
		t.Fatalf("reordering the input changed the count: %d vs %d", len(a), len(b))
	}
	for i := range a {
		if a[i] != b[i] {
			t.Errorf("reordering changed alias %d: %q vs %q", i, a[i], b[i])
		}
	}
}

func TestEmptyInputProducesNothing(t *testing.T) {
	if got := Aliases("", ""); len(got) != 0 {
		t.Errorf("empty input produced %v", got)
	}
}

// Ordinary prose with no identifiers and no acronyms should contribute nothing.
// A note is not required to have aliases, and inventing some for one that has no
// structure to surface would be exactly the paraphrase channel this replaces.
func TestPlainProseProducesNothing(t *testing.T) {
	got := Aliases("A note about filing",
		"Filing is a frontmatter edit, so nothing moves and no link can break.")
	if len(got) != 0 {
		t.Errorf("plain prose produced %v", got)
	}
}
