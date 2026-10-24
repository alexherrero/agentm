package dreaming

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The diagnostics folder's map, `agent/diagnostics/moc-diagnostics.md`
// (task 193 step 2): every note, family by family, newest first, by path.

const diagMapRel = "diagnostics/moc-diagnostics.md"

func diagnosticsFixture(t *testing.T) (root, vault string) {
	t.Helper()
	root, vault = rootMapVault(t)
	d := "agent/diagnostics/"
	// the stub the 2a migration seeded: its `created` is the page's own
	writeAt(t, vault, d+"moc-diagnostics.md", "---\ntitle: moc-diagnostics\nkind: moc\nstatus: active\ncreated: 2026-09-02\n---\n\n# Diagnostics\n\nSeeded.\n")
	// dated reports at the top, one with a folder of working files
	writeAt(t, vault, d+"2026-10-22-unlinked-cards.md", "---\ntitle: Unlinked cards, 2026-10-22\ncreated: 2026-10-22\n---\n\nBody.\n")
	writeAt(t, vault, d+"2026-10-09-memory-junk-review.md", "---\ntitle: Memory junk review, 2026-10-09\ncreated: 2026-10-09\n---\n\nBody.\n")
	writeAt(t, vault, d+"2026-10-09-memory-junk-review/purge-list.md", "---\ntitle: Memory junk review, the list\n---\n\nBody.\n")
	writeAt(t, vault, d+"restamp-pass-version-2026-09-20.jsonl", "{}\n")
	// digests: one by `created`, one with no frontmatter dated by its compact name
	writeAt(t, vault, d+"digests/20261011-digest-monthly.md", "---\nkind: brief\ncreated: 2026-10-11\n---\n\n# Observability digest — monthly\n")
	writeAt(t, vault, d+"digests/20260711-digest-daily.md", "# Daily digest\n\nBody.\n")
	// morning notes, and the undated latest copy
	writeAt(t, vault, d+"morning/2026-10-22.md", "---\ntitle: Morning 2026-10-22\n---\n\nBody.\n")
	writeAt(t, vault, d+"morning/2026-10-23.md", "---\ntitle: Morning 2026-10-23\n---\n\nBody.\n")
	writeAt(t, vault, d+"morning/latest_morning_note.md", "---\ntitle: Morning 2026-10-23\n---\n\nBody.\n")
	// scorecards that share one title
	writeAt(t, vault, d+"health/2026-08-22-health-scorecard.md", "---\ntitle: Corpus health scorecard\n---\n\nBody.\n")
	writeAt(t, vault, d+"health/2026-09-03-health-scorecard.md", "---\ntitle: Corpus health scorecard\n---\n\nBody.\n")
	writeAt(t, vault, d+"health/latest_health_scorecard.md", "---\ntitle: Corpus health scorecard\n---\n\nBody.\n")
	// an empty family, and one holding only what a sync client leaves behind
	if err := os.MkdirAll(filepath.Join(vault, "agent", "diagnostics", "lint"), 0o755); err != nil {
		t.Fatal(err)
	}
	writeAt(t, vault, d+"dreaming/.DS_Store", "")
	writeAt(t, vault, d+"dreaming/latest_dreaming_scorecard.md", "---\ntitle: Dreaming scorecard\n---\n\nBody.\n")
	// migrations nest; a note dated by its folder, and one whose enrichment
	// stamp must not become its date
	writeAt(t, vault, d+"migrations/corpus-migration-3/20260904T073713-route/summary.md", "# Corpus migration — part 3 · 20260904T073713-route\n")
	writeAt(t, vault, d+"migrations/purge/retention-first-deletion.md", "---\ntitle: Retention's first deletion\ncreated: 2026-10-01\nupdated: 2026-10-20\n---\n\nBody.\n")
	// a family the page does not know, and a folder it does not walk
	writeAt(t, vault, d+"zz-extra/note.md", "---\ntitle: zz note\n---\n\nBody.\n")
	writeAt(t, vault, d+"_private/hidden.md", "---\ntitle: Hidden\ncreated: 2026-10-30\n---\n\nBody.\n")
	return root, vault
}

func TestDiagnosticsMapListsEveryFamilyNewestFirst(t *testing.T) {
	root, _ := diagnosticsFixture(t)
	plan, err := PlanDiagnosticsMap(root, projectsNow)
	if err != nil {
		t.Fatal(err)
	}
	got := plannedText(plan, diagMapRel)
	want := "---\n" +
		"title: diagnostics — map of content\nkind: moc\nstatus: active\n" +
		"created: 2026-09-02\nupdated: 2026-10-23\ntags: [moc, diagnostics]\n" +
		"slug: moc-diagnostics\nmembers: 15\ngenerated_by: agentmdream\n" +
		"---\n\n# diagnostics\n\n[[moc-root]]\n\n" +
		"The system's health surfaces: the dated reports, then a section per family, every note newest first. " +
		"Digests, morning notes, scorecards, lint reports and dreaming notes are kept for the days the retention schedule names.\n\n" +
		"Generated nightly from the folder's notes; not edited by hand.\n\n" +
		"## Reports\n\n" +
		"- [[agent/diagnostics/2026-10-22-unlinked-cards|Unlinked cards, 2026-10-22]]\n" +
		"- [[agent/diagnostics/2026-10-09-memory-junk-review|Memory junk review, 2026-10-09]]\n" +
		"- [[agent/diagnostics/2026-10-09-memory-junk-review/purge-list|Memory junk review, the list]] · 2026-10-09\n\n" +
		"## digests\n\n" +
		"- [[agent/diagnostics/digests/20261011-digest-monthly|Observability digest — monthly]] · 2026-10-11\n" +
		"- [[agent/diagnostics/digests/20260711-digest-daily|Daily digest]] · 2026-07-11\n\n" +
		"## morning\n\n" +
		"- [[agent/diagnostics/morning/2026-10-23|Morning 2026-10-23]]\n" +
		"- [[agent/diagnostics/morning/2026-10-22|Morning 2026-10-22]]\n" +
		"- [[agent/diagnostics/morning/latest_morning_note|Morning 2026-10-23]]\n\n" +
		"## health\n\n" +
		"- [[agent/diagnostics/health/2026-09-03-health-scorecard|Corpus health scorecard]] · 2026-09-03\n" +
		"- [[agent/diagnostics/health/2026-08-22-health-scorecard|Corpus health scorecard]] · 2026-08-22\n" +
		"- [[agent/diagnostics/health/latest_health_scorecard|Corpus health scorecard]]\n\n" +
		"## dreaming\n\n" +
		"- [[agent/diagnostics/dreaming/latest_dreaming_scorecard|Dreaming scorecard]]\n\n" +
		"## migrations\n\n" +
		"- [[agent/diagnostics/migrations/purge/retention-first-deletion|Retention's first deletion]] · 2026-10-01\n" +
		"- [[agent/diagnostics/migrations/corpus-migration-3/20260904T073713-route/summary|Corpus migration — part 3 · 20260904T073713-route]] · 2026-09-04\n\n" +
		"## zz-extra\n\n" +
		"- [[agent/diagnostics/zz-extra/note|zz note]]\n"
	if got != want {
		t.Errorf("diagnostics map:\n%s\nwant:\n%s", got, want)
	}
	if len(plan.Intents) != 1 || len(plan.Pages) != 1 || plan.Pages[0].Members != 15 {
		t.Errorf("want one page of 15 members planned, got %+v", plan.Pages)
	}
}

// The stub is replaced in place: same file, same kind and slug, its own created.
func TestDiagnosticsMapReplacesTheSeededStub(t *testing.T) {
	root, _ := diagnosticsFixture(t)
	plan, _ := PlanDiagnosticsMap(root, projectsNow)
	if len(plan.Intents) != 1 {
		t.Fatalf("want one intent, got %d", len(plan.Intents))
	}
	in := plan.Intents[0]
	if in.Job != JobMocs || in.Rel != diagMapRel {
		t.Errorf("the intent is %q at %q, want the mocs job at %q", in.Job, in.Rel, diagMapRel)
	}
	if !strings.Contains(string(in.Before), "Seeded.") {
		t.Errorf("the intent does not carry the stub as its before:\n%s", in.Before)
	}
}

func TestDiagnosticsMapRegenerationOverUnchangedNotesWritesNothing(t *testing.T) {
	root, _ := diagnosticsFixture(t)
	plan, _ := PlanDiagnosticsMap(root, projectsNow)
	for _, in := range plan.Intents {
		writeAt(t, root, in.Rel, string(in.After))
	}
	again, err := PlanDiagnosticsMap(root, projectsNow.AddDate(0, 0, 3))
	if err != nil {
		t.Fatal(err)
	}
	if len(again.Intents) != 0 {
		t.Errorf("an unchanged folder rewrote its map: %s", again.Intents[0].After)
	}
	if len(again.Pages) != 1 || again.Pages[0].Members != 15 || again.Pages[0].Changed {
		t.Errorf("the written map counted itself or changed: %+v", again.Pages)
	}
	// A new note does change it.
	writeAt(t, root, "diagnostics/lint/vault-lint-2026-10-24.md", "# MemoryVault lint audit — 2026-10-24\n")
	third, _ := PlanDiagnosticsMap(root, projectsNow.AddDate(0, 0, 3))
	text := plannedText(third, diagMapRel)
	if !strings.Contains(text, "## lint\n\n- [[agent/diagnostics/lint/vault-lint-2026-10-24|MemoryVault lint audit — 2026-10-24]]\n") ||
		!strings.Contains(text, "updated: 2026-10-24") || !strings.Contains(text, "members: 16") {
		t.Errorf("a new lint report did not join the map:\n%s", text)
	}
	if strings.Index(text, "## health") > strings.Index(text, "## lint") || strings.Index(text, "## lint") > strings.Index(text, "## dreaming") {
		t.Errorf("lint is out of the family order:\n%s", text)
	}
}

// No folder, no page; a folder of empty families and stray files, no page.
func TestDiagnosticsMapSkipsAnAbsentOrEmptyFolder(t *testing.T) {
	root, vault := rootMapVault(t)
	if err := os.RemoveAll(filepath.Join(vault, "agent", "diagnostics")); err != nil {
		t.Fatal(err)
	}
	if plan, err := PlanDiagnosticsMap(root, projectsNow); err != nil || len(plan.Pages) != 0 {
		t.Fatalf("no folder should plan nothing: %v %+v", err, plan.Pages)
	}
	if err := os.MkdirAll(filepath.Join(vault, "agent", "diagnostics", "lint"), 0o755); err != nil {
		t.Fatal(err)
	}
	writeAt(t, vault, "agent/diagnostics/health/.DS_Store", "")
	writeAt(t, vault, "agent/diagnostics/restamp.jsonl", "{}\n")
	if plan, _ := PlanDiagnosticsMap(root, projectsNow); len(plan.Pages) != 0 {
		t.Errorf("an empty folder got a map: %+v", plan.Pages)
	}
}

// A note's date is its `created`, else its name's, else its nearest folder's.
func TestDiagDate(t *testing.T) {
	for _, c := range []struct {
		fm   map[string]string
		rel  string
		want string
	}{
		{map[string]string{"created": "2026-10-11T08:00:00Z"}, "digests/20260101-digest-daily.md", "2026-10-11"},
		{nil, "digests/20260711-digest-daily.md", "2026-07-11"},
		{nil, "health/2026-08-22-health-scorecard.md", "2026-08-22"},
		{nil, "migrations/corpus-migration-3/20260904T073713-route/summary.md", "2026-09-04"},
		{nil, "2026-10-09-memory-junk-review/purge-list.md", "2026-10-09"},
		{nil, "health/latest_health_scorecard.md", ""},
		{nil, "lint/report-20261399.md", ""}, // not a date
	} {
		if got := diagDate(c.fm, c.rel); got != c.want {
			t.Errorf("diagDate(%v, %q) = %q, want %q", c.fm, c.rel, got, c.want)
		}
	}
}

// The root map lists the page the night it is first planned.
func TestRootMapListsThePlannedDiagnosticsMap(t *testing.T) {
	root, vault := diagnosticsFixture(t)
	if err := os.Remove(filepath.Join(vault, "agent", "diagnostics", "moc-diagnostics.md")); err != nil {
		t.Fatal(err)
	}
	plan, _ := PlanDiagnosticsMap(root, projectsNow)
	var planned []string
	for _, p := range plan.Pages {
		planned = append(planned, p.Rel)
	}
	rootPlan, err := PlanRootMap(root, planned, projectsNow)
	if err != nil {
		t.Fatal(err)
	}
	if text := rootMapText(rootPlan); !strings.Contains(text, "## Diagnostics\n\n- [[moc-diagnostics]]") {
		t.Errorf("root map lacks the diagnostics map:\n%s", text)
	}
	if !strings.Contains(plannedText(plan, diagMapRel), "created: 2026-10-23") {
		t.Errorf("a first page takes its newest note's date as created:\n%s", plannedText(plan, diagMapRel))
	}
}
