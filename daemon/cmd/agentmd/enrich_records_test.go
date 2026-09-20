package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"testing"

	"github.com/alexherrero/agentm/daemon/internal/enrich"
	"github.com/alexherrero/agentm/daemon/internal/index"
)

// agentm-vault plan 09, task 7: the night walks project records after the cards,
// inside the same line, and a card with `project:` sees its project's records
// first among its neighbours.

func openRecordIndex(t *testing.T, vault string) *index.Index {
	t.Helper()
	x, err := index.Open(filepath.Join(t.TempDir(), "index.db"), vault, "", false)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { x.Close() })
	return x
}

func putNote(t *testing.T, x *index.Index, vault, rel, body string) {
	t.Helper()
	abs := filepath.Join(vault, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(abs), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(abs, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := x.IndexFile(rel); err != nil {
		t.Fatalf("indexing %s: %v", rel, err)
	}
}

func TestTheRecordQueueHoldsRecordsAndNeverSessionFiles(t *testing.T) {
	vault := t.TempDir()
	x := openRecordIndex(t, vault)
	for _, rel := range []string{
		"agent/memory/semantic/a-card.md",
		"projects/agentm/_index.md", "projects/agentm/decisions/d.md", "projects/agentm/research/b/r.md",
		"projects/agentm/tracker.md", "projects/agentm/tasks/t/plan.md", "projects/agentm/tasks/t/progress.md",
		"projects/agentm/tasks/t/tracker.md", "projects/agentm/_harness/PLAN-x.md", "projects/agentm/desk/b.md",
	} {
		putNote(t, x, vault, rel, "---\ntitle: t\n---\n\nbody\n")
	}
	got, err := enrichRecordQueue(x)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"projects/agentm/_index.md", "projects/agentm/decisions/d.md", "projects/agentm/research/b/r.md"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("record queue %v, want %v", got, want)
	}
}

func TestTheListerPagesByPositionSoCardsComeFirst(t *testing.T) {
	// A memory root that sorts after the projects space, where path order would
	// have served the records first.
	queue := []string{"memory/semantic/a.md", "memory/semantic/b.md", "projects/agentm/decisions/d.md"}
	cases := []struct {
		cursor string
		limit  int
		want   []string
	}{
		{"", 2, queue[:2]},
		{"memory/semantic/b.md", 5, queue[2:]},
		{"projects/agentm/decisions/d.md", 5, []string{}},
	}
	for _, c := range cases {
		if got := queueAfter(queue, c.cursor, c.limit); !reflect.DeepEqual(got, c.want) {
			t.Errorf("queueAfter(%q, %d) = %v, want %v", c.cursor, c.limit, got, c.want)
		}
	}
	// A cursor the queue no longer holds restarts at the top.
	//
	// It used to resume at the first path that sorted after the missing one,
	// which meant something while the queue was in path order. Since
	// agentm-vault plan 16 the queue has tiers — the inbox, then the cards,
	// then the records — and each tier is served oldest first, so a path
	// comparison names an arbitrary position several tiers from where the run
	// actually was. Restarting costs a walk and no calls: the fingerprint gate
	// refuses an unchanged note before any call exists.
	if got := queueAfter([]string{"a", "c", "e"}, "b", 5); !reflect.DeepEqual(got, []string{"a", "c", "e"}) {
		t.Errorf("an unknown cursor resumed at %v, want the whole queue", got)
	}
}

func TestTheQueueServesTheInboxFirstAndEachTierOldestFirst(t *testing.T) {
	// The night's serving order, end to end over a real index: the drop folder
	// ahead of the class cards ahead of the project records, and inside each
	// tier the oldest note first rather than the alphabetically first one.
	vault := t.TempDir()
	cfg := configOverRules(t, vault, "reference")
	cfg.MemoryRoot = "agent"

	card := func(created string) string {
		return "---\ntype: reference\nstatus: unfiled\ncreated: " + created +
			"\n---\n\nA thought.\n"
	}
	notes := map[string]string{
		// Inbox: `zeta` is the oldest, so it leads despite sorting last.
		"agent/inbox/alpha.md": card("2026-09-18"),
		"agent/inbox/zeta.md":  card("2026-09-01"),
		// A dotfile in the drop folder is not a card, and neither is a
		// non-markdown file.
		"agent/inbox/.gitkeep":  "a marker\n",
		"agent/inbox/notes.txt": "loose\n",
		// Cards: `004-…` sorts first and is the newest, so it must not lead —
		// this is the case path order got wrong.
		"agent/memory/semantic/004-recent.md": card("2026-09-19"),
		"agent/memory/semantic/waited.md":     card("2026-09-10"),
		// A record, last whatever its age.
		"projects/agentm/decisions/old.md": "---\nkind: decision\ncreated: 2020-01-01\n---\n\nSettled.\n",
	}
	idxPath := filepath.Join(t.TempDir(), "index.db")
	x, err := index.Open(idxPath, vault, "agent", false)
	if err != nil {
		t.Fatal(err)
	}
	defer x.Close()
	for rel, body := range notes {
		putNote(t, x, vault, rel, body)
	}

	got, err := enrichServeOrder(cfg, x, true)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{
		"agent/inbox/zeta.md", "agent/inbox/alpha.md",
		"agent/memory/semantic/waited.md", "agent/memory/semantic/004-recent.md",
		"projects/agentm/decisions/old.md",
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("serve order\n got %v\nwant %v", got, want)
	}

	// The ledger's eligible population is the same order, less the records: a
	// card is eligible, a project record is the drain's and not this number's.
	cards, err := enrichServeOrder(cfg, x, false)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(cards, want[:4]) {
		t.Errorf("card population\n got %v\nwant %v", cards, want[:4])
	}
}

func TestAProjectCardsNeighboursLeadWithItsProjectsRecords(t *testing.T) {
	vault := t.TempDir()
	cfg := configOverRules(t, vault, "preference", "workflow")
	cfg.MemoryRoot = "agent"
	x := openRecordIndex(t, vault)
	card := "---\ntitle: Keep git out of Google Drive\ntags: [git, drive]\nproject: agentm\n---\n\nDrive corrupts git.\n"
	putNote(t, x, vault, "agent/memory/semantic/keep-git-out-of-drive.md", card)
	for i := 0; i < 8; i++ {
		putNote(t, x, vault, fmt.Sprintf("agent/memory/semantic/git-drive-%d.md", i),
			fmt.Sprintf("---\ntitle: Git and Drive note %d\nsummary: About git and drive.\n---\n\ngit drive %d\n", i, i))
	}
	putNote(t, x, vault, "projects/crickets/decisions/git-drive-elsewhere.md",
		"---\ntitle: Git drive elsewhere\nsummary: Another project's ruling.\n---\n\ngit drive\n")
	putNote(t, x, vault, "projects/agentm/decisions/git-drive-ruling.md",
		"---\ntitle: Git drive ruling\nsummary: The project's ruling.\n---\n\ngit drive\n")

	got := enrichNeighbours(cfg, x, func(string) bool { return true })(context.Background(),
		enrich.Request{Rel: "agent/memory/semantic/keep-git-out-of-drive.md", Raw: card})
	if len(got) == 0 || len(got) > enrich.MaxRelated {
		t.Fatalf("offered %d neighbours, want between 1 and %d", len(got), enrich.MaxRelated)
	}
	if got[0].Rel != "projects/agentm/decisions/git-drive-ruling.md" {
		t.Errorf("the project's record does not lead: %+v", got)
	}

	// Without `project:` the card gets the ranker's order, and nothing is lifted.
	plain := "---\ntitle: Keep git out of Google Drive\ntags: [git, drive]\n---\n\nDrive corrupts git.\n"
	unbound := enrichNeighbours(cfg, x, func(string) bool { return true })(context.Background(),
		enrich.Request{Rel: "agent/memory/semantic/keep-git-out-of-drive.md", Raw: plain})
	if len(unbound) == 0 {
		t.Fatal("an unbound card was offered no neighbours")
	}
}

// nightStubSource is the model a fixture night talks to: a compiled stand-in for
// `claude`, found on PATH the way the real one is. It answers the faithfulness
// judge with AGENTMD_STUB_VERDICT and every other prompt with AGENTMD_STUB_ANSWER,
// inside the envelope `--output-format json` asks for. Compiled rather than
// scripted, like the enrich package's stub, because a shell script is not a
// program on Windows.
const nightStubSource = `package main

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
)

func main() {
	prompt := ""
	for i, a := range os.Args {
		if a == "-p" && i+1 < len(os.Args) {
			prompt = os.Args[i+1]
		}
	}
	answer := os.Getenv("AGENTMD_STUB_ANSWER")
	if strings.HasPrefix(prompt, "You are checking what a pass proposes") {
		answer = os.Getenv("AGENTMD_STUB_VERDICT")
	}
	b, _ := json.Marshal(map[string]any{
		"type": "result", "subtype": "success", "is_error": false, "result": answer,
		"total_cost_usd": 0.001,
		"usage": map[string]any{"input_tokens": 1000, "output_tokens": 100,
			"cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
	})
	fmt.Print(string(b))
}
`

// The plan's fixture run (agentm-vault plan 09, task 7): one night through the
// command itself, against a model stub. The card is judged before the record
// although its memory root sorts after `Projects`; the decision gains a dated
// section and keeps every field it carried; the response that renamed the card
// leaves the record's name alone; and the tracker, plan and progress log beside
// it are byte for byte what they were.
func TestAFixtureNightMergesIntoARecordAfterTheCardsAndLeavesSessionFilesAlone(t *testing.T) {
	stub := t.TempDir()
	src := filepath.Join(stub, "main.go")
	if err := os.WriteFile(src, []byte(nightStubSource), 0o644); err != nil {
		t.Fatal(err)
	}
	bin := filepath.Join(stub, "claude")
	if runtime.GOOS == "windows" {
		bin += ".exe"
	}
	if out, err := exec.Command("go", "build", "-o", bin, src).CombinedOutput(); err != nil {
		t.Fatalf("building the model stub: %v\n%s", err, out)
	}
	t.Setenv("PATH", stub+string(os.PathListSeparator)+os.Getenv("PATH"))
	// Never the real model: a night that found `claude` anywhere else would spend.
	if found, err := exec.LookPath("claude"); err != nil || filepath.Dir(found) != stub {
		t.Fatalf("claude resolves to %q (%v), not to the stub", found, err)
	}
	t.Setenv("AGENTMD_STUB_ANSWER", `{"title": "Keep the wall", "slug": "renamed-by-the-pass", `+
		`"type": "reference", "summary": "The wall stays.", "tags": ["walls"], "confidence": 0.9, `+
		`"importance_proposed": 6, "body": "It follows from the lifecycle ruling."}`)
	t.Setenv("AGENTMD_STUB_VERDICT", `{"grounded": true}`)

	state := t.TempDir()
	t.Setenv("AGENTM_STATE_DIR", state)
	t.Setenv("AGENTM_STORAGE_RULES", "")
	os.Unsetenv("AGENTM_STORAGE_RULES")
	vault := t.TempDir()
	writeRules(t, vault, "reference")
	// A memory root that sorts after `Projects`, so a pager walking path order
	// would have served the record first; and an embedder nothing listens on, so
	// the dispersion measurement a two-note night takes reports itself unavailable
	// rather than reaching whatever serves on this machine. The kernel config's
	// keys are flat, dotted strings.
	kernel := filepath.Join(t.TempDir(), "agentm-config.json")
	if err := os.WriteFile(kernel, []byte(`{"plugins.obsidian-vault.memory_root": "agent", `+
		`"daemon.embedder_url": "http://127.0.0.1:1"}`), 0o644); err != nil {
		t.Fatal(err)
	}

	const (
		card     = "agent/memory/semantic/keep-git-out-of-drive.md"
		decision = "projects/agentm/decisions/keep-the-wall.md"
		tracker  = "projects/agentm/tracker.md"
		plan     = "projects/agentm/tasks/build-it/plan.md"
		progress = "projects/agentm/tasks/build-it/progress.md"
	)
	notes := map[string]string{
		card: "---\ntype: reference\nstatus: unfiled\nproject: agentm\n---\n\nDrive corrupts git.\n",
		decision: "---\ntype: reference\nstatus: active\ncreated: 2026-07-01\nupdated: 2026-07-02\n" +
			"tags: [agentm, retrieval]\ngroup: projects\nslug: keep-the-wall\nimportance: 8\n" +
			"source_url: https://example.com/wall\n---\n\n# Keep the wall\n\nArchived notes stay walled.\n",
		tracker:  "---\nkind: tracker\nstatus: active\n---\n\n## State\n\nBuilding.\n",
		plan:     "# Build it\n\n- [ ] the first step\n",
		progress: "# Progress\n\n- started\n",
	}
	idxPath := filepath.Join(t.TempDir(), "index.db")
	x, err := index.Open(idxPath, vault, "agent", false)
	if err != nil {
		t.Fatal(err)
	}
	for rel, body := range notes {
		putNote(t, x, vault, rel, body)
	}
	x.Close()

	// One note a page, so the record is served from a second page, after a cursor
	// naming the card: a pager that compared paths would find nothing after
	// `agent/…` and end the night with the record unjudged.
	if err := cmdEnrich([]string{"-config", kernel, "-vault", vault, "-index", idxPath,
		"-page-size", "1", "-yes"}); err != nil {
		t.Fatalf("the night: %v", err)
	}

	raw, err := os.ReadFile(filepath.Join(state, "enrich-runs.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	var run enrichRun
	if err := json.Unmarshal(raw, &run); err != nil {
		t.Fatal(err)
	}
	if run.Enriched != 2 || run.Failed != 0 {
		t.Fatalf("the night enriched %d and failed %d, want 2 and 0: %v", run.Enriched, run.Failed, run.Errors)
	}
	if run.Verdicts.Records != 1 {
		t.Errorf("records = %d, want 1: the decision, counted apart from the filing verdicts", run.Verdicts.Records)
	}
	if filed := run.Verdicts.Active + run.Verdicts.BelowFloor; filed != 1 {
		t.Errorf("%d filing verdicts, want 1: the card's, and none for the record", filed)
	}

	// The journal is written in the order the night wrote.
	journal, err := os.ReadFile(filepath.Join(filepath.Dir(idxPath), "enrichment-journal.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	var order []string
	for _, line := range strings.Split(strings.TrimSpace(string(journal)), "\n") {
		var e enrich.JournalEntry
		if err := json.Unmarshal([]byte(line), &e); err != nil {
			t.Fatal(err)
		}
		from := e.Rel
		if e.Renamed != "" {
			from = e.Renamed
		}
		order = append(order, from)
	}
	if want := []string{card, decision}; !reflect.DeepEqual(order, want) {
		t.Errorf("the night wrote %v, want the card before the record", order)
	}

	read := func(rel string) (string, error) {
		b, err := os.ReadFile(filepath.Join(vault, filepath.FromSlash(rel)))
		return string(b), err
	}
	if _, err := read("agent/memory/semantic/renamed-by-the-pass.md"); err != nil {
		t.Errorf("the response's slug did not rename the card, so the record keeping "+
			"its name shows nothing: %v", err)
	}
	if _, err := read("projects/agentm/decisions/renamed-by-the-pass.md"); err == nil {
		t.Error("the record was renamed")
	}
	got, err := read(decision)
	if err != nil {
		t.Fatalf("the record is gone from its path: %v", err)
	}
	for _, line := range []string{"type: reference", "status: active", "created: 2026-07-01",
		"group: projects", "slug: keep-the-wall", "importance: 8", "source_url: https://example.com/wall",
		"tags: [agentm, retrieval, walls]", "summary: The wall stays.", "importance_proposed: 6"} {
		if !strings.Contains(got, "\n"+line+"\n") {
			t.Errorf("the record lacks %q:\n%s", line, got)
		}
	}
	if strings.Contains(got, "\nupdated: 2026-07-02\n") || !strings.Contains(got, "\nupdated: ") ||
		!strings.Contains(got, "\nenriched_at: ") {
		t.Errorf("the record's stamps did not land:\n%s", got)
	}
	if strings.Contains(got, "\ntitle:") {
		t.Errorf("a card's title was rendered onto the record:\n%s", got)
	}
	if !strings.Contains(got, "\n# Keep the wall\n\nArchived notes stay walled.\n") {
		t.Errorf("the record's text did not survive:\n%s", got)
	}
	if !strings.Contains(got, enrich.DreamingHeading+" (") ||
		!strings.Contains(got, "\n\nIt follows from the lifecycle ruling.") {
		t.Errorf("no dated section on the record:\n%s", got)
	}
	for _, rel := range []string{tracker, plan, progress} {
		if now, err := read(rel); err != nil || now != notes[rel] {
			t.Errorf("%s changed (%v):\n%s", rel, err, now)
		}
	}
}
