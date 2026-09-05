package dreaming

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// Task 5: the maintenance jobs and the report-only checks. The calendar
// port is held to the recorded Python review texts; the rest are new
// capabilities pinned on fixtures where each threshold fires.

func TestCalendarReviewsMatchTheRecordedPythonText(t *testing.T) {
	root, _ := parityFixture(t)
	blob, err := os.ReadFile(filepath.Join("..", "..", "..", "scripts", "fixtures", "dreaming-parity", "expected.json"))
	if err != nil {
		t.Fatal(err)
	}
	var rec struct {
		Calendar struct {
			Week  map[string]string `json:"week"`
			Month map[string]string `json:"month"`
		} `json:"calendar"`
	}
	if err := json.Unmarshal(blob, &rec); err != nil {
		t.Fatal(err)
	}
	calendarRoot := CalendarRoot(root)
	if calendarRoot == "" {
		t.Fatal("the fixture carries a register; CalendarRoot found none")
	}
	if got := RenderWeek(calendarRoot, Facets(nil), 2026, 35); got != rec.Calendar.Week["2026-W35"] {
		t.Errorf("week review differs from the recording:\n got %q\n py  %q", got, rec.Calendar.Week["2026-W35"])
	}
	if got := RenderMonth(calendarRoot, Facets(nil), 2026, 8); got != rec.Calendar.Month["2026-08"] {
		t.Errorf("month review differs from the recording:\n got %q\n py  %q", got, rec.Calendar.Month["2026-08"])
	}
	// The takeover as intents: every closed week in the window and both
	// months are checked; only what differs from disk is written, and a
	// second plan over the written files writes nothing.
	today := time.Date(2026, 9, 5, 9, 0, 0, 0, time.UTC)
	plan, err := PlanCalendar(root, nil, today, 8)
	if err != nil {
		t.Fatal(err)
	}
	if plan.Refreshed != 10 || len(plan.Written) != 10 {
		t.Errorf("first plan: refreshed %d written %d, want 10 and 10 (eight closed weeks, two months)", plan.Refreshed, len(plan.Written))
	}
	j, _ := OpenJournal(t.TempDir())
	for i, in := range plan.Intents {
		if _, err := j.Commit(root, "cal", fmt.Sprintf("c-%d", i), in, today); err != nil {
			t.Fatal(err)
		}
	}
	again, _ := PlanCalendar(root, nil, today, 8)
	if len(again.Written) != 0 || again.Refreshed != 10 {
		t.Errorf("an unchanged register writes nothing on the next pass: %+v", again.Written)
	}
	// With the week review on disk, the month review links it.
	month, _ := os.ReadFile(filepath.Join(calendarRoot, "2026", "2026-08-review.md"))
	if !strings.Contains(string(month), "- [[2026-W35-review]] — 2 of 7 days with entries") {
		t.Errorf("the month should link the week review once it exists:\n%s", month)
	}
}

func TestISOWeekArithmeticMatchesPython(t *testing.T) {
	// date.fromisocalendar(2026, 1, 1) is 2025-12-29; 2020 is a 53-week year
	// and date.fromisocalendar(2020, 53, 7) is 2021-01-03.
	if d := isoWeekMonday(2026, 1); d.Format("2006-01-02") != "2025-12-29" {
		t.Errorf("2026-W01 Monday = %s", d.Format("2006-01-02"))
	}
	if d := WeekDays(2020, 53)[6]; d.Format("2006-01-02") != "2021-01-03" {
		t.Errorf("2020-W53 Sunday = %s", d.Format("2006-01-02"))
	}
	if n := len(MonthDays(2028, 2)); n != 29 {
		t.Errorf("Feb 2028 has %d days", n)
	}
}

func writeTyped(t *testing.T, root, rel, typ, anchor, body string) {
	t.Helper()
	fm := "---\ntitle: " + strings.TrimSuffix(filepath.Base(rel), ".md") + "\ntype: " + typ + "\nstatus: active\n"
	if anchor != "" {
		fm += "captured: " + anchor + "T09:00:00+00:00\n"
	}
	writeRaw(t, root, rel, fm+"---\n\n"+body)
}

func TestMocsCreateAtTheFloorSplitPastTheLineAndFlagStale(t *testing.T) {
	root := t.TempDir()
	now := time.Date(2026, 9, 5, 9, 0, 0, 0, time.UTC)
	for i := 0; i < 4; i++ {
		writeTyped(t, root, fmt.Sprintf("memory/semantic/four-%d.md", i), "fact", "2026-08-01", "Four is not enough.\n")
	}
	for i := 0; i < 5; i++ {
		writeTyped(t, root, fmt.Sprintf("memory/procedural/five-%d.md", i), "workflow", fmt.Sprintf("2026-08-%02d", 10+i), fmt.Sprintf("# Heading\n\nStep %d of the procedure, the phrase the map shows.\n", i))
	}
	for i := 0; i < 41; i++ {
		writeTyped(t, root, fmt.Sprintf("memory/semantic/many-%02d.md", i), "preference", "2026-01-15", fmt.Sprintf("Preference number %d.\n", i))
	}
	writeTyped(t, root, "memory/semantic/gone.md", "workflow", "2026-08-20", "Superseded, not a member.\n")
	writeRaw(t, root, "memory/semantic/gone.md", "---\ntitle: gone\ntype: workflow\nstatus: superseded\ncaptured: 2026-08-20T09:00:00+00:00\n---\n\nSuperseded.\n")
	plan, err := PlanMocs(root, nil, now)
	if err != nil {
		t.Fatal(err)
	}
	if plan.BelowFloor["fact"] != 4 {
		t.Errorf("four members is below the floor of five: %v", plan.BelowFloor)
	}
	var byRel = map[string]MocPage{}
	for _, p := range plan.Pages {
		byRel[p.Rel] = p
	}
	wf, ok := byRel["memory/mocs/workflow.md"]
	if !ok || wf.Members != 5 || wf.Pages != 1 || wf.Stale || wf.Newest != "2026-08-14" {
		t.Errorf("workflow page = %+v", wf)
	}
	p1, ok1 := byRel["memory/mocs/preference.md"]
	p2, ok2 := byRel["memory/mocs/preference-2.md"]
	if !ok1 || !ok2 || p1.Members != 40 || p2.Members != 1 || p1.Pages != 2 {
		t.Errorf("41 members split into 40 + 1: %+v %+v", p1, p2)
	}
	if !p1.Stale || !p2.Stale {
		t.Errorf("the newest preference is from January, past the 90-day line: should be stale")
	}
	var wfText string
	for _, in := range plan.Intents {
		if in.Rel == "memory/mocs/workflow.md" {
			wfText = string(in.After)
		}
	}
	for _, want := range []string{
		"kind: moc\n", "updated: 2026-08-14\n", "slug: workflow\n", "type_of_members: workflow\n", "members: 5\n",
		"- [[five-4]] — five-4 · Step 4 of the procedure, the phrase the map shows.\n",
	} {
		if !strings.Contains(wfText, want) {
			t.Errorf("workflow page lacks %q:\n%s", want, wfText)
		}
	}
	if strings.Contains(wfText, "[[gone]]") || strings.Contains(wfText, "stale: true") {
		t.Errorf("a superseded note is not a member and a fresh map is not stale:\n%s", wfText)
	}
	if !strings.Contains(wfText, "- [[five-4]]") || strings.Index(wfText, "[[five-4]]") > strings.Index(wfText, "[[five-0]]") {
		t.Errorf("members are newest first")
	}
	// Apply, then plan again: byte-stable, and `created` survives.
	j, _ := OpenJournal(t.TempDir())
	for i, in := range plan.Intents {
		if _, err := j.Commit(root, "m", fmt.Sprintf("m-%d", i), in, now); err != nil {
			t.Fatal(err)
		}
	}
	again, _ := PlanMocs(root, nil, now.AddDate(0, 0, 30))
	if len(again.Intents) != 0 {
		t.Errorf("unchanged membership a month later regenerates nothing, got %d intents", len(again.Intents))
	}
	page, _ := os.ReadFile(filepath.Join(root, "memory/mocs/workflow.md"))
	if !strings.Contains(string(page), "created: 2026-08-14\n") {
		t.Errorf("created is the newest member's day on first render: %s", page)
	}
}

func TestDatesGlossAgingNotesAndLeaveTheRestAlone(t *testing.T) {
	root := t.TempDir()
	now := time.Date(2026, 9, 5, 9, 0, 0, 0, time.UTC)
	writeRaw(t, root, "memory/semantic/aging.md", "---\ntitle: aging\ntype: fact\nstatus: active\ncaptured: 2026-06-10T09:00:00+00:00\n---\n\n"+
		"We decided this last week and shipped it yesterday; 3 days ago the build broke. Earlier today it passed. Next week we tag.\n\n"+
		"```\nlast week inside a fence stays\n```\n\nA code span `yesterday` stays too, and last week (the week of 2026-06-01) is already glossed. Recently it rained.\n")
	writeRaw(t, root, "memory/semantic/fresh.md", "---\ntitle: fresh\ntype: fact\nstatus: active\ncaptured: 2026-09-01T09:00:00+00:00\n---\n\nWe decided this last week.\n")
	writeRaw(t, root, "memory/semantic/undated.md", "---\ntitle: undated\ntype: fact\nstatus: active\n---\n\nLast week, without a date to anchor on.\n")
	plan, err := PlanDates(root, nil, now)
	if err != nil {
		t.Fatal(err)
	}
	if plan.Aging != 1 || len(plan.Intents) != 1 || plan.Intents[0].Rel != "memory/semantic/aging.md" {
		t.Fatalf("only the aging note is glossed: aging=%d intents=%d", plan.Aging, len(plan.Intents))
	}
	after := string(plan.Intents[0].After)
	for _, want := range []string{
		"last week (the week of 2026-06-01) and shipped it yesterday (2026-06-09); 3 days ago (2026-06-07) the build broke. Earlier today (2026-06-10) it passed. Next week (the week of 2026-06-15) we tag.",
		"```\nlast week inside a fence stays\n```",
		"A code span `yesterday` stays too, and last week (the week of 2026-06-01) is already glossed. Recently it rained.",
	} {
		if !strings.Contains(after, want) {
			t.Errorf("glossed text lacks %q:\n%s", want, after)
		}
	}
	if strings.Count(after, "(the week of 2026-06-01)") != 2 {
		t.Errorf("the already-glossed phrase must not be glossed again:\n%s", after)
	}
	if !strings.HasPrefix(after, "---\ntitle: aging\n") {
		t.Errorf("the frontmatter is untouched")
	}
	phrases := []string{}
	for _, g := range plan.Glossed {
		phrases = append(phrases, g.Phrase)
	}
	if strings.Join(phrases, ",") != "last week,yesterday,3 days ago,Earlier today,Next week" {
		t.Errorf("glossed phrases = %v", phrases)
	}
	// Idempotent: apply, then plan again — nothing left to gloss.
	j, _ := OpenJournal(t.TempDir())
	if _, err := j.Commit(root, "d", "d-1", plan.Intents[0], now); err != nil {
		t.Fatal(err)
	}
	again, _ := PlanDates(root, nil, now)
	if len(again.Intents) != 0 {
		t.Errorf("a glossed note is glossed once: %+v", again.Glossed)
	}
}

func TestVocabularyAuditNamesTheUnknownTheMalformedAndTheRetired(t *testing.T) {
	root := t.TempDir()
	r := testRules(t)
	memType := r.MemoryTypes[0]
	writeTyped(t, root, "memory/semantic/known.md", memType, "2026-09-01", "fine\n")
	writeTyped(t, root, "memory/semantic/unknown.md", "sonnet", "2026-09-01", "no such type\n")
	writeTyped(t, root, "memory/semantic/Bad_Case.md", "Bad_Case", "2026-09-01", "not kebab\n")
	writeRaw(t, root, "memory/mocs/record.md", "---\ntitle: r\nkind: moc\n---\n\nfine\n")
	writeRaw(t, root, "memory/mocs/odd.md", "---\ntitle: r\nkind: nobody-knows\n---\n\n?\n")
	rep, err := VocabularyAudit(root, r)
	if err != nil {
		t.Fatal(err)
	}
	if rep.Considered != 5 {
		t.Errorf("considered = %d", rep.Considered)
	}
	names := func(fs []VocabFinding) []string {
		var out []string
		for _, f := range fs {
			out = append(out, f.Field+"="+f.Value)
		}
		return out
	}
	// Findings come in walk order — the rels sorted, so the record under
	// memory/mocs/ precedes the memory under memory/semantic/.
	if strings.Join(names(rep.Unrecognized), ",") != "kind=nobody-knows,type=sonnet" {
		t.Errorf("unrecognized = %v", names(rep.Unrecognized))
	}
	if strings.Join(names(rep.Malformed), ",") != "type=Bad_Case" {
		t.Errorf("malformed = %v", names(rep.Malformed))
	}
	for retired := range r.Deprecations {
		writeTyped(t, root, "memory/semantic/old-vocab.md", retired, "2026-09-01", "an old word\n")
		rep, _ = VocabularyAudit(root, r)
		if len(rep.Retired) != 1 || rep.Retired[0].Value != retired {
			t.Errorf("a deprecated type is reported retired with its replacement: %+v", rep.Retired)
		}
		break
	}
}

func TestTrendsFlagDoublingTheCapAndClassGrowth(t *testing.T) {
	root := t.TempDir()
	now := time.Date(2026, 9, 5, 9, 0, 0, 0, time.UTC)
	// Previous week: 2 notes; this week: 5 flat + 1 in a lane — a +200% jump
	// (a note in a lane is a write like any other).
	for i, d := range []string{"2026-08-24", "2026-08-25", "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05"} {
		writeTyped(t, root, fmt.Sprintf("memory/semantic/n-%d.md", i), "fact", d, "x\n")
	}
	writeTyped(t, root, "memory/procedural/lane/inner.md", "workflow", "2026-09-05", "in a lane\n")
	r := &rules.Rules{}
	r.Thresholds = map[string]float64{"daily_write_cap": 1}
	rep, err := Trends(root, r, now, map[string]int{"semantic": 2, "procedural": 5})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Week != 6 || rep.PreviousWeek != 2 || rep.ChangePct == nil || *rep.ChangePct != 200 {
		t.Errorf("week %d previous %d change %v", rep.Week, rep.PreviousWeek, rep.ChangePct)
	}
	if rep.Populations["semantic"].Flat != 7 || rep.Populations["procedural"].Lanes != 1 || rep.Populations["procedural"].Flat != 0 {
		t.Errorf("populations = %+v", rep.Populations)
	}
	if len(rep.DaysAtCap) != 7 {
		t.Errorf("with a cap of one every writing day is at the cap: %v", rep.DaysAtCap)
	}
	if rep.Growth["semantic"] != 5 || rep.Growth["procedural"] != -5 {
		t.Errorf("growth = %v", rep.Growth)
	}
	joined := strings.Join(rep.Flags, "\n")
	if !strings.Contains(joined, "writes doubled week over week (6 vs 2, +200%)") || !strings.Contains(joined, "2026-09-05 wrote 2") {
		t.Errorf("flags = %v", rep.Flags)
	}
	if strings.Contains(joined, "semantic grew") {
		t.Errorf("a class under ten notes before is not flagged for growth: %v", rep.Flags)
	}
	quiet, _ := Trends(root, nil, now, nil)
	if quiet.ChangePct == nil || len(quiet.DaysAtCap) != 0 || len(quiet.Growth) != 0 {
		t.Errorf("no previous pass and the default cap: %+v", quiet)
	}
}

func TestReclassifyRunsOnAVersionChangeAndFindsThePlantedMisfiling(t *testing.T) {
	root := t.TempDir()
	r := testRules(t)
	class, ok := r.ClassFor("workflow")
	if !ok || class == "semantic" {
		t.Skip("the fixture needs workflow to route away from semantic")
	}
	for i := 0; i < 12; i++ {
		writeTyped(t, root, fmt.Sprintf("memory/%s/w-%02d.md", class, i), "workflow", fmt.Sprintf("2026-08-%02d", 1+i), "right class\n")
	}
	writeTyped(t, root, "memory/semantic/misfiled.md", "workflow", "2026-09-01", "wrong class\n")
	current := PassVersion(r)
	same, _ := Reclassify(root, r, current, current, 30, 7, false)
	if same.Ran {
		t.Errorf("an unchanged version runs nothing: %+v", same)
	}
	changed, err := Reclassify(root, r, current, "enrich/0+rules/old", 30, 7, false)
	if err != nil {
		t.Fatal(err)
	}
	if !changed.Ran || changed.Available != 13 || changed.Sampled != 13 {
		t.Errorf("a version change samples everything when the corpus is small: %+v", changed)
	}
	if len(changed.Mismatches) != 1 || changed.Mismatches[0].Rel != "memory/semantic/misfiled.md" || changed.Mismatches[0].Routed != class {
		t.Errorf("mismatches = %+v", changed.Mismatches)
	}
	small, _ := Reclassify(root, r, current, "", 5, 7, false)
	if small.Sampled != 5 || small.Available != 13 || small.Seed != 7 {
		t.Errorf("the sample is capped and seeded: %+v", small)
	}
	again, _ := Reclassify(root, r, current, "", 5, 7, false)
	if fmt.Sprint(again.Mismatches) != fmt.Sprint(small.Mismatches) {
		t.Errorf("the same seed samples the same notes")
	}
	forced, _ := Reclassify(root, r, current, current, 30, 0, true)
	if !forced.Ran || forced.Reason != "forced" || forced.Seed == 0 {
		t.Errorf("forced: %+v", forced)
	}
}

func TestThePassCarriesTheChecksAndRemembersThem(t *testing.T) {
	cfg, root := scratchConfig(t)
	cfg.Rules = rules.NewHolder(root, time.Now())
	now := time.Date(2026, 9, 5, 9, 0, 0, 0, time.UTC)
	writeTyped(t, root, "memory/semantic/a.md", "fact", "2026-09-01", "a\n")
	rep, err := Run(cfg, Options{Force: true, Now: now})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Vocabulary.Considered != 1 || rep.Trends.Week != 1 || !rep.Reclassify.Ran || rep.Reclassify.Reason != "first pass under a recorded version" {
		t.Errorf("checks on the report: vocab=%+v trends week=%d reclassify=%+v", rep.Vocabulary, rep.Trends.Week, rep.Reclassify)
	}
	st, _ := LoadState(cfg.EngineStateDir)
	if st.ClassPopulations["semantic"] != 1 || st.LastPassVersion == "" {
		t.Errorf("state remembers the populations and the version: %+v", st)
	}
	second, _ := Run(cfg, Options{Force: true, Now: now.Add(time.Hour)})
	if second.Reclassify.Ran {
		t.Errorf("the same version does not re-run the diff: %+v", second.Reclassify)
	}
	forced, _ := Run(cfg, Options{Force: true, Now: now.Add(2 * time.Hour), Reclassify: true})
	if !forced.Reclassify.Ran {
		t.Errorf("-reclassify forces it")
	}
}
