package capture

import (
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// Four captures that want the same name. The second grows by the next word of
// its own text that the name does not hold, the third skips a bare number and
// the fourth a one-letter word, each taking the word after — never `-2`, never
// `-a` (agentm-vault § The card: the collision rule produces a meaningful word,
// not a counter).
func TestACollidingNameGrowsByItsOwnWordsNeverByACounter(t *testing.T) {
	cp := newHarness(t)
	var got []string
	for _, text := range []string{
		"Mirrored folders corrupt pack files.",
		"Mirrored folders corrupt the index.",
		"2 more reasons it hurts.",
		"A clone inside Drive breaks.",
	} {
		res, err := cp.Do(Request{Title: "Keep git out of Drive", Text: text})
		if err != nil {
			t.Fatal(err)
		}
		got = append(got, strings.TrimSuffix(filepath.Base(res.Path), ".md"))
	}
	want := []string{"keep-git-out-of-drive", "keep-git-out-of-drive-mirrored", "keep-git-out-of-drive-more",
		"keep-git-out-of-drive-clone"}
	counter := regexp.MustCompile(`-[0-9]+$`)
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("capture %d landed as %q, want %q", i+1, got[i], want[i])
		}
		if counter.MatchString(got[i]) {
			t.Errorf("capture %d took a counter name %q", i+1, got[i])
		}
	}
}
