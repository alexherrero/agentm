package extract

import (
	"strings"
	"testing"
)

func TestQualifiedIssue(t *testing.T) {
	got := Entities("Fixed in alexherrero/agentm#466 last night.\n")
	eq(t, got, []string{"issue:alexherrero/agentm#466"})
}

func TestBareIssue(t *testing.T) {
	got := Entities("Closes #466 and reopens #12.\n")
	eq(t, got, []string{"issue:#12", "issue:#466"})
}

// A qualified reference must not also record a bare one. `owner/repo#123` is one
// fact; recording `#123` beside it would invent a second, local issue that does
// not exist.
func TestAQualifiedIssueDoesNotAlsoRecordABareOne(t *testing.T) {
	got := Entities("See alexherrero/agentm#466.\n")
	for _, g := range got {
		if g == "issue:#466" {
			t.Errorf("a qualified reference also recorded a bare one: %v", got)
		}
	}
}

// `#1` is as often a list marker or a heading fragment as a reference, and
// `#todo` is a tag.
func TestSingleDigitAndTagHashesAreIgnored(t *testing.T) {
	got := Entities("Item #1 in the list, tagged #todo and #wip.\n")
	if len(got) != 0 {
		t.Errorf("recorded %v", got)
	}
}

func TestRepositoryURL(t *testing.T) {
	got := Entities("Cloned from https://github.com/alexherrero/agentm.git today.\n")
	eq(t, got, []string{"repo:alexherrero/agentm"})
}

// `a/b` on its own is a path far more often than a repository, so the host is
// required.
func TestABarePathIsNotARepository(t *testing.T) {
	got := Entities("The file lives at memory/semantic and is fine.\n")
	if len(got) != 0 {
		t.Errorf("a bare path was recorded as a repo: %v", got)
	}
}

func TestCommitHash(t *testing.T) {
	got := Entities("Landed as 8296fc5 on main.\n")
	eq(t, got, []string{"commit:8296fc5"})
}

// All-digit runs are dates, counts and issue numbers far more often than
// commits.
func TestAllDigitRunsAreNotCommits(t *testing.T) {
	got := Entities("We captured 9473000 notes in 2026 across 15039 files.\n")
	for _, g := range got {
		if strings.HasPrefix(g, "commit:") {
			t.Errorf("a digit run was recorded as a commit: %v", got)
		}
	}
}

func TestShortHexIsNotACommit(t *testing.T) {
	got := Entities("The value was abc123 before.\n")
	for _, g := range got {
		if strings.HasPrefix(g, "commit:") {
			t.Errorf("a six-character token was recorded as a commit: %v", got)
		}
	}
}

func TestChangelist(t *testing.T) {
	got := Entities("Submitted as cl/123456789.\n")
	eq(t, got, []string{"cl:123456789"})
}

// A commit hash in a worked example is a sample, not a reference to something
// this note is about.
func TestFencedCodeIsSkipped(t *testing.T) {
	body := "Real: #466.\n\n```bash\ngit show 8296fc5   # not a reference\n```\n"
	got := Entities(body)
	eq(t, got, []string{"issue:#466"})
}

// Namespacing is what keeps two kinds of thing from colliding in one index.
func TestURIsAreNamespaced(t *testing.T) {
	got := Entities("Repo https://github.com/alexherrero/agentm, issue alexherrero/agentm#466.\n")
	eq(t, got, []string{"issue:alexherrero/agentm#466", "repo:alexherrero/agentm"})
}

func TestRepositoryCaseIsNormalised(t *testing.T) {
	a := Entities("https://github.com/AlexHerrero/AgentM\n")
	b := Entities("https://github.com/alexherrero/agentm\n")
	eq(t, a, b)
}

// A derived row set that varied between runs would make every rebuild a diff.
func TestSortedAndDeduped(t *testing.T) {
	got := Entities("#466 and #466 again, plus #12, plus #466 once more.\n")
	eq(t, got, []string{"issue:#12", "issue:#466"})
}

func TestNothingInPlainProse(t *testing.T) {
	got := Entities("Filing is a frontmatter edit, so nothing moves and no link breaks.\n")
	if len(got) != 0 {
		t.Errorf("plain prose produced %v", got)
	}
}

// The guard against the whole file being vacuous: if the regexes matched
// nothing, every negative assertion above would pass and say nothing.
func TestTheExtractorMatchesSomething(t *testing.T) {
	got := Entities("Fixed alexherrero/agentm#466 in 8296fc5, see https://github.com/alexherrero/agentm and cl/99.\n")
	if len(got) < 4 {
		t.Fatalf("only %d entities from input carrying one of each form: %v", len(got), got)
	}
}
