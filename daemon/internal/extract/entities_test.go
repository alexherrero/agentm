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

// --- task 179: a note's context qualifies what it leaves bare ---------------

var known = map[string]string{"agentm": "alexherrero/agentm", "crickets": "alexherrero/crickets"}

// A note in a project that lists one repository means that repository.
func TestABareIssueTakesTheNotesRepository(t *testing.T) {
	got := EntitiesIn("Closes #466 and reopens #12.\n", Context{Repo: "alexherrero/agentm"})
	eq(t, got, []string{"issue:alexherrero/agentm#12", "issue:alexherrero/agentm#466"})
}

// A note of no project, or of a project listing two repositories, gets no
// Repo from the index, and its bare number stays bare.
func TestAnAmbiguousBareIssueStaysBare(t *testing.T) {
	got := EntitiesIn("Closes #466.\n", Context{Known: known})
	eq(t, got, []string{"issue:#466"})
}

// An issue's address names its repository, and the bare number written beside
// the link is that issue, whatever repository the note belongs to.
func TestAnIssueLinkIsQualifiedAndTakesItsBareNumber(t *testing.T) {
	got := EntitiesIn("Fixed in [#690](https://github.com/alexherrero/agentm/pull/690).\n",
		Context{Repo: "alexherrero/crickets", Known: known})
	eq(t, got, []string{"issue:alexherrero/agentm#690", "repo:alexherrero/agentm"})
	got = EntitiesIn("See https://github.com/alexherrero/crickets/issues/12 too.\n", Context{})
	eq(t, got, []string{"issue:alexherrero/crickets#12", "repo:alexherrero/crickets"})
}

// A version tag beside a repository's name, or its `owner/repo`, is that
// repository's release, whichever project the note is in.
func TestAReleaseBesideARepository(t *testing.T) {
	ctx := Context{Repo: "alexherrero/agentm", Known: known}
	eq(t, EntitiesIn("Paired with crickets v4.0.0 today.\n", ctx),
		[]string{"release:alexherrero/crickets@v4.0.0"})
	eq(t, EntitiesIn("Cut alexherrero/crickets v2.1.0.\n", ctx),
		[]string{"release:alexherrero/crickets@v2.1.0"})
	eq(t, EntitiesIn("Cut alexherrero/sherwood v2.1.0 from https://github.com/alexherrero/sherwood\n", ctx),
		[]string{"release:alexherrero/sherwood@v2.1.0", "repo:alexherrero/sherwood"})
	eq(t, EntitiesIn("Shipped **crickets [v5.0.0](https://example.com)**.\n", ctx),
		[]string{"release:alexherrero/crickets@v5.0.0"})
}

func TestAReleaseByItsAddress(t *testing.T) {
	got := EntitiesIn("Launched with [agentm v10.0.0](https://github.com/alexherrero/agentm/releases/tag/v10.0.0).\n",
		Context{Known: known})
	eq(t, got, []string{"release:alexherrero/agentm@v10.0.0", "repo:alexherrero/agentm"})
}

// With no word naming another thing, a version in a single-repo project's note
// is that project's release; a line naming one repository gives it that one.
func TestAReleaseFromTheNotesOrTheLinesRepository(t *testing.T) {
	ctx := Context{Repo: "alexherrero/agentm", Known: known}
	eq(t, EntitiesIn("Plan C shipped in v10.3.0.\n", ctx), []string{"release:alexherrero/agentm@v10.3.0"})
	eq(t, EntitiesIn("## v10.2.0\n", ctx), []string{"release:alexherrero/agentm@v10.2.0"})
	eq(t, EntitiesIn("Tagged v0.9.1 on https://github.com/alexherrero/nottingham\n", Context{}),
		[]string{"release:alexherrero/nottingham@v0.9.1", "repo:alexherrero/nottingham"})
}

// A stray version with no repository anywhere near it is no release.
func TestAStrayVersionIsNoRelease(t *testing.T) {
	if got := EntitiesIn("Upgraded to v1.2.3 yesterday.\n", Context{Known: known}); len(got) != 0 {
		t.Errorf("a stray version became %v", got)
	}
}

// A version after another thing's name belongs to that thing: a plugin, a tool.
// A pre-release, a four-part version, a two-part one and a version inside a
// path are not a release either.
func TestAVersionThatIsNotTheRepositorysIsNoRelease(t *testing.T) {
	ctx := Context{Repo: "alexherrero/agentm", Known: known}
	for _, line := range []string{
		"Installed development-lifecycle v0.44.1 from the cache.\n",
		"Ran it under agy v1.2.3 on the Mac.\n",
		"The candidate v10.4.0-rc1 is out.\n",
		"A four-part v1.2.3.4 build.\n",
		"Only v1.2 so far.\n",
		"Read plugins/cache/v0.49.0/scripts.\n",
		"See agentic-harness/CHANGELOG.md v1.4.0 and wiki/docs v2.0.0.\n",
		"Cut alexherrero/unlisted v2.1.0.\n",
		"Two repos: https://github.com/a/b and https://github.com/c/d at v1.0.0\n",
		"Paired with https://github.com/alexherrero/crickets today and shipped v10.0.0.\n",
	} {
		for _, g := range EntitiesIn(line, ctx) {
			if strings.HasPrefix(g, "release:") {
				t.Errorf("%q gave %s", line, g)
			}
		}
	}
}

// Commits, changelists and bare numbers are indexed and never paged.
func TestOnlyRepositoriesQualifiedIssuesAndReleasesArePaged(t *testing.T) {
	for uri, want := range map[string]bool{
		"repo:alexherrero/agentm":            true,
		"issue:alexherrero/agentm#466":       true,
		"release:alexherrero/agentm@v10.0.0": true,
		"issue:#466":                         false,
		"commit:8296fc5":                     false,
		"cl:99":                              false,
		"nonsense":                           false,
	} {
		if got := Paged(uri); got != want {
			t.Errorf("Paged(%q) = %v, want %v", uri, got, want)
		}
	}
}

// GitHub's own pages are not repositories: a subdomain's page, a gist, a
// project board, an organisation's forum. A real repository beside them is.
func TestGitHubsOwnPagesAreNotRepositories(t *testing.T) {
	got := Entities("Docs at https://docs.github.com/en/rest and https://gist.github.com/karpathy/442a6bf5.\n" +
		"Board: https://github.com/users/alexherrero/projects/2, forum https://github.com/orgs/community/discussions/1.\n" +
		"Code: https://github.com/alexherrero/crickets/issues/12.\n")
	// The gist's hex id still reads as a commit, which is indexed and never paged.
	eq(t, got, []string{"commit:442a6bf5", "issue:alexherrero/crickets#12", "repo:alexherrero/crickets"})
}

// From the release review (2026-09-30): versions that belong to something
// else, and the forms a release is written in that the first rule missed.
func TestTheReviewsReleaseAndIssueCases(t *testing.T) {
	ctx := Context{Repo: "alexherrero/agentm", Known: map[string]string{
		"agentm": "alexherrero/agentm", "crickets": "alexherrero/crickets"}}
	for line, want := range map[string][]string{
		// A dependency bump names someone else's repository.
		"Bump actions/checkout from v3.5.2 to v4.0.0.\n": nil,
		// A word two back names the thing the version belongs to.
		"Upgraded node to v20.11.1 on the runner.\n": nil,
		// A pre-release by its address is not the final release.
		"See https://github.com/alexherrero/agentm/releases/tag/v10.0.0-rc.1 for it.\n": {"repo:alexherrero/agentm"},
		// A bullet's dash is no word, and a possessive is its owner's.
		"- v10.0.0 shipped the vault series.\n": {"release:alexherrero/agentm@v10.0.0"},
		"agentm's v10.0.0 closed it.\n":         {"release:alexherrero/agentm@v10.0.0"},
		"crickets’s v5.0.0 paired with it.\n":   {"release:alexherrero/crickets@v5.0.0"},
		// A repository's name right before a bare number is its repository.
		"Fixed in crickets #235.\n": {"issue:alexherrero/crickets#235"},
		// And the legitimate forms still read.
		"Plan C shipped in v10.3.0.\n": {"release:alexherrero/agentm@v10.3.0"},
	} {
		got := EntitiesIn(line, ctx)
		if len(want) == 0 {
			for _, g := range got {
				if strings.HasPrefix(g, "release:") {
					t.Errorf("%q gave %s", line, g)
				}
			}
			continue
		}
		eq(t, got, want)
	}
}

// Task 186: the vault growth audit of 2026-10-03 found the builder paging
// roadmap item numbers and colours as the note's repository's issues. A
// number after a roadmap word, or six digits long, stays bare — indexed for
// search, never paged — and a pull request reference keeps its repository.
func TestARoadmapNumberOrALongNumberIsNoIssueOfTheRepository(t *testing.T) {
	ctx := Context{Repo: "alexherrero/agentm"}
	for line, want := range map[string]string{
		"Gemini-CLI excluded per ROADMAP item #15.":        "issue:#15",
		"The registry lands in V4 #30 plan 1.":             "issue:#30",
		"Deferred to post-V4 #12.":                         "issue:#12",
		"Observed during the plan #15 close-out.":          "issue:#15",
		"Task #179 shipped the entity pages.":              "issue:#179",
		"Dark mode: `--bg #191614` (Cocoa).":               "issue:#191614",
		"Two MLS numbers were removed: #41138876 in July.": "issue:#41138876",
		"Shipped in PR #553.":                              "issue:alexherrero/agentm#553",
		"- #720: an operator's filing survives.":           "issue:alexherrero/agentm#720",
		"This release ships task 182 (#797).":              "issue:alexherrero/agentm#797",
		"Recorded in ROADMAP item (#43).":                  "issue:alexherrero/agentm#43",
	} {
		got := EntitiesIn(line, ctx)
		if len(got) != 1 || got[0] != want {
			t.Errorf("%q: got %v, want [%s]", line, got, want)
		}
	}
}
