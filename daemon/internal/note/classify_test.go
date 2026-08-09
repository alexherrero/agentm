package note

import (
	"sort"
	"strings"
	"testing"
	"time"
)

// Every case below is a hand-written document with a hand-written expected class
// set. None of them computes its expectation by calling the classifier a second
// way — a check that derives its expected value from the implementation's own
// logic verifies only that they agree with each other, and would have passed
// unchanged through the classifier behaviour change that shipped in session 1.
func TestClassify(t *testing.T) {
	cases := []struct {
		name string
		rel  string
		raw  string
		want []string
	}{
		{
			name: "miner lead-in, unfiled: the modal case, 3413 notes",
			rel:  "personal/_inbox/workflow-bash-382.md",
			raw: "---\ntype: workflow\nstatus: inbox\n---\n" +
				"User stated: the hooks resolve the wrong python.\n",
			want: []string{"fragment", "status"},
		},
		{
			name: "miner lead-in, filed: the status gate, 229 of 232 preferences",
			rel:  "personal/preferences/prefers-edit-over-write.md",
			raw: "---\ntype: preference\nstatus: active\n---\n" +
				"User stated: prefer Edit over Write for existing files.\n",
			want: []string{"fragment-promoted"},
		},
		{
			name: "mining_confidence is written by nothing but the miner: 5741 notes",
			rel:  "personal/fix/some-fix.md",
			raw:  "---\ntype: fix\nstatus: inbox\nmining_confidence: 0.31\n---\nA plain body.\n",
			want: []string{"fragment", "status"},
		},
		{
			name: "mining_confidence on a filed note is still gated by status",
			rel:  "personal/fix/some-fix.md",
			raw:  "---\ntype: fix\nstatus: active\nmining_confidence: 0.9\n---\nA plain body.\n",
			want: []string{"fragment-promoted"},
		},
		{
			name: "Fix observed: is the second lead-in",
			rel:  "personal/_inbox/fix-1.md",
			raw:  "---\nstatus: inbox\n---\nFix observed: the daemon held the port.\n",
			want: []string{"fragment", "status"},
		},
		{
			name: "User corrected the agent: is the third",
			rel:  "personal/_inbox/fix-2.md",
			raw:  "---\nstatus: unfiled\n---\nUser corrected the agent: it is AgentM, not Agent M.\n",
			want: []string{"fragment", "status"},
		},
		{
			name: "mid-word slug in a miner-filled directory: the remaining 32",
			rel:  "personal/idea/rver-s-vault-hardwiring-can-t-1.md",
			raw:  "---\nstatus: active\n---\nA body with no lead-in at all.\n",
			// Shaped by its slug, but filed — so gated, exactly like the others.
			want: []string{"fragment-promoted"},
		},
		{
			name: "the same mid-word slug, unfiled",
			rel:  "personal/fix/ps-truncated-thing.md",
			raw:  "---\nstatus: inbox\n---\nA body with no lead-in at all.\n",
			want: []string{"fragment", "status"},
		},
		{
			name: "a real short word may open a slug",
			rel:  "personal/idea/read-multi-agent-collective-memory-vault.md",
			raw:  "---\nstatus: active\n---\nA legitimate note about a paper.\n",
			want: nil,
		},
		{
			name: "a digit-led slug reads as a truncation",
			rel:  "personal/idea/1-something-clipped.md",
			raw:  "---\nstatus: unfiled\n---\nA body.\n",
			want: []string{"fragment", "status"},
		},
		{
			name: "the slug rule is scoped to the two miner directories",
			rel:  "personal/domains/mber-not-a-fragment.md",
			raw:  "---\nstatus: active\n---\nA body.\n",
			want: nil,
		},
		{
			name: "an ellipsis opener in a miner directory",
			rel:  "personal/idea/well-formed-slug-here.md",
			raw:  "---\nstatus: unfiled\n---\n... continued from something else.\n",
			want: []string{"fragment", "status"},
		},
		{
			name: "superseded is a penalized status without being a fragment",
			rel:  "personal/2026/07/old-convention.md",
			raw:  "---\ntype: convention\nstatus: superseded\n---\nThe old rule.\n",
			want: []string{"status"},
		},
		{
			name: "expired likewise",
			rel:  "personal/2026/07/gone.md",
			raw:  "---\nstatus: expired\n---\nNo longer true.\n",
			want: []string{"status"},
		},
		{
			name: "a dream-staging proposal by filename",
			rel:  "_dream-staging/2026-08-01-merge-two.proposal.md",
			raw:  "---\nstatus: active\n---\nQuotes both notes in full.\n",
			want: []string{"staging"},
		},
		{
			name: "a dream-staging proposal by heading",
			rel:  "_dream-staging/batch-7.md",
			raw:  "---\nstatus: active\n---\n# Proposal 12: merge these two notes\n",
			want: []string{"staging"},
		},
		{
			name: "staging compounds with the rest",
			rel:  "_dream-staging/batch-8.proposal.md",
			raw:  "---\nstatus: inbox\nmining_confidence: 0.4\n---\nUser stated: something.\n",
			want: []string{"fragment", "status", "staging"},
		},
		{
			name: "an ordinary filed note carries nothing",
			rel:  "personal/2026/08/vault-path-convention.md",
			raw: "---\ntype: convention\nstatus: active\n---\n" +
				"Vault paths are resolved at runtime, never cached as a literal.\n",
			want: nil,
		},
		{
			name: "a note with no frontmatter at all",
			rel:  "projects/notes.md",
			raw:  "Just prose, no frontmatter.\n",
			want: nil,
		},
		{
			name: "a lead-in must open the body, not merely appear in it",
			rel:  "personal/2026/08/about-the-miner.md",
			raw: "---\nstatus: active\n---\nThe miner writes bodies that open with " +
				"User stated: and that is how we detect them.\n",
			want: nil,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := Parse(tc.rel, tc.raw, time.Now()).Flags
			if !sameSet(got, tc.want) {
				t.Errorf("classify(%s)\n  got  %v\n  want %v", tc.rel, got, tc.want)
			}
		})
	}
}

// TestMultiplierIsMonotone pins that a penalty can only ever lower a score. BM25
// hands back a negative score when a term's IDF goes negative, and a naive
// multiply turns the demotion into a promotion at exactly that point.
func TestMultiplierIsMonotone(t *testing.T) {
	for _, flags := range [][]string{
		{ClassFragment},
		{ClassStatus},
		{ClassStaging},
		{ClassFragment, ClassStatus},
		{ClassFragment, ClassStatus, ClassStaging},
	} {
		if m := Multiplier(flags); m <= 0 || m >= 1 {
			t.Errorf("Multiplier(%v) = %v; a penalty must be in (0,1) — never zero, "+
				"because a note that cannot be returned cannot be returned when it is "+
				"the only answer", flags, m)
		}
	}
	if m := Multiplier(nil); m != 1.0 {
		t.Errorf("Multiplier(nil) = %v, want 1.0", m)
	}
	if m := Multiplier([]string{"some-class-we-never-defined"}); m != 1.0 {
		t.Errorf("an unknown class multiplied by %v; unlisted classes must be inert, "+
			"which is the mechanism the status gate rides on", m)
	}
}

func TestParseTitleAndMeta(t *testing.T) {
	n := Parse("personal/2026/08/vault-path-convention.md",
		"---\ntitle: Resolve, don't recall\ntags: [vault, paths]\n"+
			"aliases:\n  - never cache an absolute path\n  - resolve at runtime\n---\nBody text.\n",
		time.Now())

	// The stem is spaced out so `vault-path-convention` also matches a query
	// phrased as three words.
	for _, want := range []string{"Resolve, don't recall", "vault path convention"} {
		if !strings.Contains(n.Title, want) {
			t.Errorf("title %q missing %q", n.Title, want)
		}
	}
	// Both the inline and the block list form reach the meta column.
	for _, want := range []string{"vault", "paths", "never cache an absolute path", "resolve at runtime"} {
		if !strings.Contains(n.Meta, want) {
			t.Errorf("meta %q missing %q", n.Meta, want)
		}
	}
	// The frontmatter stays in the body column: "what's my convention for X"
	// should hit `type: convention`.
	if !strings.Contains(n.Body, "title: Resolve") {
		t.Errorf("frontmatter dropped out of the body column: %q", n.Body)
	}
}

func TestParseCapturedPrecedence(t *testing.T) {
	mtime := time.Date(2020, 1, 1, 0, 0, 0, 0, time.UTC)

	n := Parse("a.md", "---\ncaptured: 2026-08-03T14:22:00Z\ndate: 2020-05-05\n---\nx\n", mtime)
	if got := n.Captured.Format(time.RFC3339); got != "2026-08-03T14:22:00Z" {
		t.Errorf("captured = %s, want the frontmatter captured field", got)
	}
	if n.CapturedSource != "frontmatter:captured" {
		t.Errorf("source = %s", n.CapturedSource)
	}

	n = Parse("a.md", "---\ndate: 2026-06-17\n---\nx\n", mtime)
	if got := n.Captured.Format("2006-01-02"); got != "2026-06-17" {
		t.Errorf("captured = %s, want the date field as the fallback", got)
	}

	// No claim in the file: the filesystem is the last resort, and which signal
	// won is recorded rather than smoothed over.
	n = Parse("a.md", "no frontmatter\n", mtime)
	if !n.Captured.Equal(mtime) {
		t.Errorf("captured = %s, want the mtime %s", n.Captured, mtime)
	}
	if n.CapturedSource != "mtime" {
		t.Errorf("source = %s, want mtime", n.CapturedSource)
	}
}

func sameSet(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	x, y := append([]string(nil), a...), append([]string(nil), b...)
	sort.Strings(x)
	sort.Strings(y)
	for i := range x {
		if x[i] != y[i] {
			return false
		}
	}
	return true
}
