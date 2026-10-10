package enrich

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	notes "github.com/alexherrero/agentm/daemon/internal/note"
	"github.com/alexherrero/agentm/daemon/internal/people"
)

// agentm-vault plan 04, task 3: the prompt's guards, each tested on the bytes
// a judgment would write rather than asserted of the prompt.
//
//   - it never returns `why`, and the gate strips one if it appears;
//   - the Evidence block survives;
//   - a differing `importance` is never overwritten;
//   - body content is added only under `## Added by dreaming`, with everything
//     above it byte-identical;
//   - `related` names only the neighbours offered.

// card is a captured card as the session wrote it: frontmatter with a `why` and
// an importance, a body, and an Evidence block.
const card = "---\n" +
	"title: Keep git out of Google Drive\n" +
	"type: preference\n" +
	"status: unfiled\n" +
	"lifecycle: active\n" +
	"why: The vault moved into Drive and a repo inside it churned for a day.\n" +
	"importance: 7\n" +
	"importance_proposed: 7\n" +
	"source: conversation\n" +
	"created: 2026-09-06\n" +
	"---\n" +
	"\n" +
	"Never keep a git repository inside the Google Drive mount; Drive's\n" +
	"client rewrites `.git/index` mid-operation and the repo corrupts.\n" +
	"\n" +
	"## Evidence\n" +
	"\n" +
	"> user: the vault's .git broke again after Drive synced\n"

var offered = []Neighbour{
	{ID: "drive-upload-staging-churns", Rel: "agent/memory/procedural/drive-upload-staging-churns.md",
		Title: "Drive upload staging churns transient files", Summary: "Drive re-uploads files mid-write."},
	{ID: "vault-location", Rel: "agent/memory/semantic/vault-location.md",
		Title: "Where the vault lives", Summary: "The vault is a plain local path synced to Drive."},
}

func fullResponse() Response {
	return Response{
		Title:              "Keep git repositories out of the Google Drive mount",
		Type:               "preference",
		Summary:            "Drive's client corrupts a git repository it syncs.",
		Tags:               []string{"git", "google-drive"},
		Related:            []string{"drive-upload-staging-churns", "invented-note"},
		ImportanceProposed: 8,
		Body:               "This is the same churn the upload-staging note records for transient files.",
		Confidence:         0.9,
	}
}

func stampAt() Stamp {
	return Stamp{Version: PassVersion, RulesHash: "rh", ConfidenceFloor: 0.65,
		At: time.Date(2026, 9, 12, 2, 10, 0, 0, time.UTC)}
}

// bodyOf is everything after the frontmatter's closing fence.
func bodyOf(t *testing.T, note string) string {
	t.Helper()
	_, body := splitNote(note)
	return body
}

// The captured text survives byte for byte, and anything the pass adds is
// below it under the dated heading.
func TestTheCapturedTextIsByteIdenticalAboveTheAddedSection(t *testing.T) {
	next, _, err := Compose(card, fullResponse(), stampAt(), DepthDeep, offered)
	if err != nil {
		t.Fatal(err)
	}
	captured := bodyOf(t, card)
	body := bodyOf(t, next)
	if !strings.HasPrefix(body, captured) {
		t.Fatalf("the captured text changed:\nwas:\n%q\nnow:\n%q", captured, body)
	}
	added := body[len(captured):]
	if !strings.HasPrefix(strings.TrimLeft(added, "\n"), DreamingHeading+" (2026-09-12)\n") {
		t.Errorf("what follows the captured text is not the dated section:\n%q", added)
	}
	if !strings.Contains(added, "the same churn the upload-staging note records") {
		t.Errorf("the addition did not land under the heading:\n%q", added)
	}
	// Nothing the pass wrote sits above the heading: every line of the body
	// before it is a line of the card.
	if strings.Count(body, DreamingHeading) != 1 {
		t.Errorf("the heading appears %d times", strings.Count(body, DreamingHeading))
	}
}

// Nothing to add means nothing added: the body is exactly what it was.
func TestAnEmptyAdditionLeavesTheBodyExactlyAsItWas(t *testing.T) {
	r := fullResponse()
	r.Body = "   \n"
	next, _, err := Compose(card, r, stampAt(), DepthDeep, offered)
	if err != nil {
		t.Fatal(err)
	}
	if got, want := bodyOf(t, next), bodyOf(t, card); got != want {
		t.Errorf("an empty addition changed the body:\n%q\nwant:\n%q", got, want)
	}
}

// The Evidence block is the room's material; it survives verbatim.
func TestTheEvidenceBlockSurvives(t *testing.T) {
	next, _, err := Compose(card, fullResponse(), stampAt(), DepthDeep, offered)
	if err != nil {
		t.Fatal(err)
	}
	if got, want := evidenceBlock(next), evidenceBlock(card); got != want || want == "" {
		t.Errorf("the Evidence block changed:\n%q\nwant:\n%q", got, want)
	}
	// And it is still above the added section, where the session put it.
	if strings.Index(next, EvidenceHeading) > strings.Index(next, DreamingHeading) {
		t.Error("the Evidence block moved below the added section")
	}
}

// The pass never writes `why`. A model that offers one anyway has it stripped
// at the gate, and the card keeps the one the session wrote.
func TestAWhyTheModelReturnsIsStrippedAndNeverWritten(t *testing.T) {
	raw := map[string]any{}
	b, _ := json.Marshal(fullResponse())
	_ = json.Unmarshal(b, &raw)
	raw["why"] = "Because it seemed important."
	raw["Altitude"] = "canonical"
	withWhy, _ := json.Marshal(raw)

	// The gate accepts the response rather than failing it over a field that
	// was never going to land...
	g := DefaultSchema(func(string) bool { return true }, nil)
	if err := g.Check(nil, Request{}, string(withWhy)); err != nil {
		t.Fatalf("a response carrying why was rejected instead of stripped: %v", err)
	}
	r, stripped, err := decodeResponse(string(withWhy))
	if err != nil {
		t.Fatal(err)
	}
	if len(stripped) != 2 {
		t.Errorf("stripped %v, want why and altitude", stripped)
	}
	// ...and the note that lands carries the session's why, not the model's.
	next, _, err := Compose(card, r, stampAt(), DepthDeep, offered)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(next, "Because it seemed important") {
		t.Error("the model's why reached the note")
	}
	if got := frontmatterValue(next, "why"); got != frontmatterValue(card, "why") {
		t.Errorf("why = %q, want the session's %q", got, frontmatterValue(card, "why"))
	}
	if strings.Contains(strings.ToLower(next), "altitude") {
		t.Error("altitude reached the note")
	}

	// A card with no why stays without one.
	bare := strings.Replace(card, "why: The vault moved into Drive and a repo inside it churned for a day.\n", "", 1)
	next, _, err = Compose(bare, r, stampAt(), DepthDeep, offered)
	if err != nil {
		t.Fatal(err)
	}
	if frontmatterValue(next, "why") != "" {
		t.Error("the pass wrote a why onto a card that had none")
	}
}

// An importance the operator set — one that differs from what was proposed —
// is theirs, and the pass does not move it. The proposal still moves.
func TestAnImportanceThatDiffersFromTheProposalIsNeverOverwritten(t *testing.T) {
	edited := strings.Replace(card, "importance: 7\n", "importance: 3\n", 1)
	next, _, err := Compose(edited, fullResponse(), stampAt(), DepthDeep, offered)
	if err != nil {
		t.Fatal(err)
	}
	if got := frontmatterValue(next, "importance"); got != "3" {
		t.Errorf("importance = %q, want the operator's 3", got)
	}
	if got := frontmatterValue(next, "importance_proposed"); got != "8" {
		t.Errorf("importance_proposed = %q, want the pass's 8", got)
	}

	// Where the two agreed, the number was only the last proposal, and the new
	// one replaces both halves.
	next, _, err = Compose(card, fullResponse(), stampAt(), DepthDeep, offered)
	if err != nil {
		t.Fatal(err)
	}
	if frontmatterValue(next, "importance") != "8" || frontmatterValue(next, "importance_proposed") != "8" {
		t.Errorf("an unedited importance did not follow the proposal: %q / %q",
			frontmatterValue(next, "importance"), frontmatterValue(next, "importance_proposed"))
	}
}

// related names only the neighbours the prompt offered. An id the model
// invented is dropped, however it was spelled.
func TestRelatedIsChosenFromTheOfferOnly(t *testing.T) {
	r := fullResponse()
	r.Related = []string{"[[vault-location]]", "invented-note", "drive-upload-staging-churns.md",
		"vault-location"}
	next, _, err := Compose(card, r, stampAt(), DepthDeep, offered)
	if err != nil {
		t.Fatal(err)
	}
	got := rawFrontmatterValue(next, "related")
	if got != `["[[vault-location]]", "[[drive-upload-staging-churns]]"]` {
		t.Errorf("related = %s, want the two offered ids once each", got)
	}
	if strings.Contains(next, "invented-note") {
		t.Error("an invented link reached the note")
	}
}

// Every field the design lists lands, and neither forbidden one does.
func TestAFixturePassReturnsEveryFieldAndNoneOfTheForbiddenOnes(t *testing.T) {
	next, v, err := Compose(card, fullResponse(), stampAt(), DepthDeep, offered)
	if err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{"title", "type", "summary", "tags", "related",
		"importance_proposed", "confidence", "filing_confidence", "status",
		"enriched_by", "enriched_at", "rules_hash"} {
		if rawFrontmatterValue(next, k) == "" {
			t.Errorf("%s is missing from the note:\n%s", k, next)
		}
	}
	if v.Status != "active" || frontmatterValue(next, "filing_confidence") != "high" {
		t.Errorf("above the floor the card should land active at high, got %+v", v)
	}
	if got := frontmatterValue(next, "enriched_by"); got != PassVersion {
		t.Errorf("enriched_by = %q", got)
	}
}

// The prompt changed, so the pass version did: every card judged under plan
// 03's prompt is owed the deep pass again.
func TestThePassHashChangedFromPlan03s(t *testing.T) {
	const plan03 = "enrich/1+prompt/33dddba25c84"
	const augustBatch = "enrich/1+prompt/a73ff0f4f5dc"
	if PassVersion == plan03 || PassVersion == augustBatch {
		t.Fatalf("PassVersion is still %s; the two-shape prompt must re-owe the corpus", PassVersion)
	}
	old := strings.Replace(card, "status: unfiled\n",
		"status: unfiled\nenriched_by: "+plan03+"\nenriched_at: 2026-09-10T00:00:00Z\n", 1)
	if PassDepth(old) != DepthDeep {
		t.Error("a card stamped under plan 03's prompt is not owed the deep pass")
	}
}

// The verdict: floor → active/high, below → unfiled/low, and a second verdict
// below the floor sinks the card to dormant — except what never sinks.
func TestTheVerdictRules(t *testing.T) {
	low := fullResponse()
	low.Confidence = 0.4
	low.Type = "reference"

	next, v, err := Compose(card, low, stampAt(), DepthDeep, offered)
	if err != nil {
		t.Fatal(err)
	}
	// The card carries type: preference, which never sinks — but it has no
	// stamp yet, so this is a first verdict either way.
	if v.Status != "unfiled" || v.FilingConfidence != "low" || v.Sank {
		t.Errorf("a first verdict below the floor gave %+v", v)
	}
	if frontmatterValue(next, "lifecycle") != "active" {
		t.Errorf("a first sub-floor verdict moved the lifecycle: %q", frontmatterValue(next, "lifecycle"))
	}

	// Judged before, below the floor, and again below it: it sinks.
	judged := strings.Replace(card, "type: preference\n", "type: reference\n", 1)
	judged = strings.Replace(judged, "status: unfiled\n",
		"status: unfiled\nenriched_by: enrich/1+prompt/33dddba25c84\nenriched_at: 2026-09-10T00:00:00Z\n", 1)
	next, v, err = Compose(judged, low, stampAt(), DepthDeep, offered)
	if err != nil {
		t.Fatal(err)
	}
	if !v.Sank || frontmatterValue(next, "lifecycle") != "dormant" ||
		frontmatterValue(next, "lifecycle_since") != "2026-09-12" {
		t.Errorf("a second sub-floor verdict did not sink the card: %+v\n%s", v, next)
	}

	// A rule type never sinks, and neither does a pinned card.
	for name, note := range map[string]string{
		"preference": strings.Replace(judged, "type: reference\n", "type: preference\n", 1),
		"pinned":     strings.Replace(judged, "lifecycle: active\n", "lifecycle: pinned\n", 1),
	} {
		r := low
		if name == "preference" {
			r.Type = "preference"
		}
		_, v, err := Compose(note, r, stampAt(), DepthDeep, offered)
		if err != nil {
			t.Fatal(err)
		}
		if v.Sank {
			t.Errorf("a %s card sank", name)
		}
	}
}

// Task 187: a card whose lifecycle the operator set by hand is not sunk by a
// second verdict below the floor. It keeps the state they gave it.
func TestACardTheOperatorSetIsNotSunk(t *testing.T) {
	low := fullResponse()
	low.Confidence = 0.4
	low.Type = "reference"
	judged := strings.Replace(card, "type: preference\n", "type: reference\n", 1)
	judged = strings.Replace(judged, "status: unfiled\n",
		"status: unfiled\nenriched_by: enrich/1+prompt/33dddba25c84\nenriched_at: 2026-09-10T00:00:00Z\n", 1)
	s := stampAt()
	s.HandLifecycle = true
	next, v, err := Compose(judged, low, s, DepthDeep, offered)
	if err != nil {
		t.Fatal(err)
	}
	if v.Sank || frontmatterValue(next, "lifecycle") != "active" {
		t.Errorf("a card the operator set sank: %+v, lifecycle %q", v, frontmatterValue(next, "lifecycle"))
	}
	if v.Status != "unfiled" || v.FilingConfidence != "low" {
		t.Errorf("the verdict itself should stand: %+v", v)
	}
}

// The light pass moves what ranks nothing, keeps importance, and leaves the
// body — the added section included — exactly as it was.
func TestTheLightPassMovesOnlyWhatRanksNothing(t *testing.T) {
	deep, _, err := Compose(card, fullResponse(), stampAt(), DepthDeep, offered)
	if err != nil {
		t.Fatal(err)
	}
	moved := strings.Replace(deep, "the repo corrupts.", "the repo corrupts, twice now.", 1)

	light := fullResponse()
	light.Title = "A different title"
	light.Type = "reference"
	light.Summary = "A new summary."
	light.ImportanceProposed = 2
	light.Body = "prose the light pass must not add"
	light.Confidence = 0.5 // below the floor: title and type must hold

	next, _, err := Compose(moved, light, stampAt(), DepthLight, offered)
	if err != nil {
		t.Fatal(err)
	}
	if bodyOf(t, next) != bodyOf(t, moved) {
		t.Errorf("the light pass changed the body:\n%q\nwant:\n%q", bodyOf(t, next), bodyOf(t, moved))
	}
	if frontmatterValue(next, "summary") != "A new summary." {
		t.Error("the light pass did not move the summary")
	}
	if frontmatterValue(next, "title") != frontmatterValue(moved, "title") ||
		frontmatterValue(next, "type") != frontmatterValue(moved, "type") {
		t.Error("the light pass moved title or type below the floor")
	}
	if frontmatterValue(next, "importance_proposed") != "8" || frontmatterValue(next, "importance") != "8" {
		t.Errorf("the light pass moved importance: %q / %q",
			frontmatterValue(next, "importance"), frontmatterValue(next, "importance_proposed"))
	}

	// Above the floor, title and type may move.
	light.Confidence = 0.9
	next, _, err = Compose(moved, light, stampAt(), DepthLight, offered)
	if err != nil {
		t.Fatal(err)
	}
	if frontmatterValue(next, "title") != "A different title" {
		t.Error("the light pass could not move the title above the floor")
	}
}

// A re-owed deep pass replaces the machine's own section and keeps anything
// the operator wrote below it.
func TestAReOwedDeepPassReplacesOnlyItsOwnSection(t *testing.T) {
	first, _, err := Compose(card, fullResponse(), stampAt(), DepthDeep, offered)
	if err != nil {
		t.Fatal(err)
	}
	withMine := strings.TrimRight(first, "\n") + "\n\n## My notes\n\nI checked this myself.\n"

	again := fullResponse()
	again.Body = "A newer connection."
	next, _, err := Compose(withMine, again, stampAt(), DepthDeep, offered)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Count(next, DreamingHeading) != 1 {
		t.Errorf("the section was appended instead of replaced:\n%s", next)
	}
	if strings.Contains(next, "the same churn the upload-staging note records") {
		t.Error("the old addition survived the re-owed pass")
	}
	if !strings.Contains(next, "A newer connection.") || !strings.Contains(next, "## My notes\n\nI checked this myself.") {
		t.Errorf("the new addition or the operator's section is missing:\n%s", next)
	}
	if !strings.HasPrefix(bodyOf(t, next), bodyOf(t, card)) {
		t.Error("the captured text changed on the second pass")
	}
}

// The section's boundary stays the only level-two heading in it, so the next
// pass finds where it ends.
func TestAHeadingInTheAdditionIsSteppedDown(t *testing.T) {
	r := fullResponse()
	r.Body = "# Context\n\nOne.\n\n## Decision\n\nTwo."
	next, _, err := Compose(card, r, stampAt(), DepthDeep, offered)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(next, "\n# Context") || strings.Contains(next, "\n## Decision") {
		t.Errorf("a top-level heading survived in the addition:\n%s", next)
	}
	if !strings.Contains(next, "### Context") || !strings.Contains(next, "### Decision") {
		t.Errorf("the headings were not stepped down:\n%s", next)
	}
}

// A card with no frontmatter of its own keeps its text too.
func TestACardWithNoFrontmatterKeepsItsText(t *testing.T) {
	raw := "Just a line the session wrote.\n"
	next, _, err := Compose(raw, fullResponse(), stampAt(), DepthDeep, offered)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(bodyOf(t, next), raw) {
		t.Errorf("the text was lost:\n%s", next)
	}
	if !strings.HasPrefix(next, "---\n") || frontmatterValue(next, "title") == "" {
		t.Errorf("no frontmatter was written:\n%s", next)
	}
}

// The night may enrich a card in the drop folder and may never file one
// (agentm-vault plan 16). `VerdictFor` promoted on confidence alone, with no
// path or trust check, so a card nobody had read could be stamped `active` by
// a score — which is the one thing that part exists to prevent. The folder was
// empty when this was found, so nothing had been mis-stamped; that was luck.
func TestTheNightNeverFilesACardInTheDropFolder(t *testing.T) {
	card := "---\ntitle: a thought typed on a phone\nstatus: unfiled\n" +
		"trust: untrusted\n---\n\nDrive is already the route.\n"
	r := Response{Title: "a thought typed on a phone", Type: "reference",
		Summary: "the night wrote this", Confidence: 0.99, ImportanceProposed: 8}

	// Same card, same sky-high confidence, two postures.
	filed := Stamp{Version: "enrich/1", ConfidenceFloor: 0.6}
	dropped := Stamp{Version: "enrich/1", ConfidenceFloor: 0.6, NeverFiles: true}

	inClass, cv, err := Compose(card, r, filed, DepthLight, nil)
	if err != nil {
		t.Fatal(err)
	}
	if cv.Status != "active" {
		t.Fatalf("a confident card in a class directory did not file: %q", cv.Status)
	}

	inbox, iv, err := Compose(card, r, dropped, DepthLight, nil)
	if err != nil {
		t.Fatal(err)
	}
	if iv.Status != "unfiled" {
		t.Errorf("the night filed an inbox card: status %q at confidence %.2f "+
			"against a floor of %.2f", iv.Status, r.Confidence, filed.ConfidenceFloor)
	}
	if iv.FilingConfidence != "low" {
		t.Errorf("filing_confidence %q, want low — the card is still a candidate",
			iv.FilingConfidence)
	}
	if iv.Sank {
		t.Error("an inbox card sank; it was never judged by a person, so there " +
			"is nothing for it to sink from")
	}
	for _, want := range []string{"status: unfiled", "filing_confidence: low"} {
		if !strings.Contains(inbox, want) {
			t.Errorf("the written card does not carry %q:\n%s", want, inbox)
		}
	}
	if strings.Contains(inbox, "status: active") {
		t.Errorf("the written card says active:\n%s", inbox)
	}

	// The enrichment itself still happened — that is why the night reaches the
	// folder at all. The guard bars the promotion, not the work.
	for _, want := range []string{"summary:", "the night wrote this"} {
		if !strings.Contains(inbox, want) {
			t.Errorf("the card was not enriched at all; %q is missing:\n%s", want, inbox)
		}
	}
	// And the operator's own text is untouched, in both postures.
	for name, got := range map[string]string{"class": inClass, "inbox": inbox} {
		if !strings.Contains(got, "Drive is already the route.") {
			t.Errorf("%s: the card's own text did not survive:\n%s", name, got)
		}
	}
}

// The zero value is "may file", so every caller that predates the drop folder
// keeps the behaviour it had. This is what makes the additive field safe.
func TestTheDefaultPostureIsUnchanged(t *testing.T) {
	card := "---\ntitle: x\nstatus: unfiled\n---\n\nbody\n"
	r := Response{Title: "x", Type: "reference", Summary: "s", Confidence: 0.99}
	v := VerdictFor(card, r, 0.6)
	if v.Status != "active" {
		t.Errorf("VerdictFor no longer files a confident card: %q", v.Status)
	}
	if got := VerdictForNote(card, r, 0.6, false); got != v {
		t.Errorf("VerdictForNote(neverFiles=false) = %+v, want VerdictFor's %+v", got, v)
	}
}

// --- task 179: the people a card names -------------------------------------

// peopleCard names a colleague, cites a paper by its authors' surname only,
// and is written by the vault's owner.
const peopleCard = "---\n" +
	"title: Review the retrieval plan with Jane\n" +
	"type: reference\n" +
	"status: unfiled\n" +
	"source: conversation\n" +
	"created: 2026-09-28\n" +
	"---\n" +
	"\n" +
	"Pat walked Jane Doe through the retrieval plan. The ranker follows\n" +
	"Vaswani et al. (2017); Jane asked for the ablation first.\n"

func peopleTable(t *testing.T) people.Table {
	t.Helper()
	tb, err := people.Parse("```people\nyou: [Pat Owner, Pat]\ndeny: [Claude]\n```\n")
	if err != nil {
		t.Fatal(err)
	}
	return tb
}

// The model is asked to leave the owner and cited authors out; what it returns
// anyway is held to the card's own words and the operator's table before a
// byte lands. The colleague stays; the owner, a cited author the card names
// only by surname, and a name the card never states are dropped.
func TestOnlyTheColleagueTheCardNamesIsWrittenAsAPerson(t *testing.T) {
	r := fullResponse()
	r.People = []string{"Jane Doe", "Ashish Vaswani", "Pat Owner", "Pat", "Geoffrey Hinton", "jane doe"}
	s := stampAt()
	s.People = peopleTable(t)
	next, _, err := Compose(peopleCard, r, s, DepthDeep, offered)
	if err != nil {
		t.Fatal(err)
	}
	if got := frontmatterValue(next, "people"); got != "[Jane Doe]" {
		t.Errorf("people: %q, want [Jane Doe]\n%s", got, next)
	}
	// And a pass that names nobody writes no field at all.
	r.People = nil
	next, _, err = Compose(peopleCard, r, s, DepthDeep, offered)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(next, "\npeople:") {
		t.Errorf("a pass that named nobody wrote a people field:\n%s", next)
	}
}

// A name only an earlier pass ever wrote is not evidence for itself: the
// grounding reads the card's title and the session's text, never its old
// `people:` or the dreaming section.
func TestAPreviousPassesPeopleAreNotEvidenceForAName(t *testing.T) {
	previous := strings.Replace(peopleCard, "created: 2026-09-28\n",
		"created: 2026-09-28\npeople: [Mara Lin]\n", 1) +
		"\n" + DreamingHeading + " (2026-09-20)\n\nMara Lin reviewed it too.\n"
	r := fullResponse()
	r.People = []string{"Mara Lin", "Jane Doe"}
	next, _, err := Compose(previous, r, stampAt(), DepthDeep, offered)
	if err != nil {
		t.Fatal(err)
	}
	if got := frontmatterValue(next, "people"); got != "[Jane Doe]" {
		t.Errorf("people: %q, want [Jane Doe]", got)
	}
}

// The prompt asks for the field, and says whom to leave out.
func TestThePromptAsksForThePeopleAndWhomToLeaveOut(t *testing.T) {
	got := BuildPrompt(Request{Rel: "agent/memory/semantic/a.md", Raw: peopleCard, Depth: DepthDeep},
		[]string{"reference"}, "the rubric")
	for _, want := range []string{
		"people               OPTIONAL, at most 12",
		"Leave out the person the card is written\n                       by or for, the authors of any cited work",
		"public\n                       figures mentioned only in passing",
		"summary, tags, related, people and confidence may move",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("the prompt lacks %q", want)
		}
	}
	if _, err := ParseResponse(`{"title":"t","type":"reference","body":"","confidence":0.9,"people":["Jane Doe"]}`); err != nil {
		t.Errorf("a response carrying people was refused: %v", err)
	}
	g := DefaultSchema(nil, nil)
	if err := g.Validate(Response{Title: "t", People: make([]string, MaxPeople+1), Confidence: 0.5}); err == nil {
		t.Error("a response naming more people than the cap passed")
	}
}

// `updated` is the author's date and the age clock's input; enrichment's own
// date is `enriched_at` (task 190). The write-quality audit found 48 of 107
// semantic cards carrying 2026-09-30 or 10-01, the two nights enrichment
// restamped them, so every one of them read as freshly written.
func TestEnrichmentKeepsACardsUpdatedAndStampsItsOwnDate(t *testing.T) {
	dated := strings.Replace(card, "created: 2026-09-06\n", "created: 2026-09-06\nupdated: 2026-09-08\n", 1)
	next, _, err := Compose(dated, fullResponse(), stampAt(), DepthDeep, offered)
	if err != nil {
		t.Fatal(err)
	}
	if got := frontmatterValue(next, "updated"); got != "2026-09-08" {
		t.Errorf("updated = %q, want the card's own 2026-09-08:\n%s", got, next)
	}
	if got := frontmatterValue(next, "enriched_at"); !strings.HasPrefix(got, "2026-09-12") {
		t.Errorf("enriched_at = %q, want the pass's moment", got)
	}
	// A card is still the deep pass's to add to; only a project's record is not.
	if !strings.Contains(next, DreamingHeading+" (2026-09-12)") {
		t.Errorf("the deep pass no longer adds its section to a card:\n%s", next)
	}

	// A card that never carried `updated` keeps its author's date: `created`.
	next, _, err = Compose(card, fullResponse(), stampAt(), DepthDeep, offered)
	if err != nil {
		t.Fatal(err)
	}
	if got := frontmatterValue(next, "updated"); got != "2026-09-06" {
		t.Errorf("updated = %q on a card with none, want its created 2026-09-06", got)
	}
}

// And so enrichment no longer resets a card's age.
func TestEnrichmentDoesNotResetACardsAge(t *testing.T) {
	dated := strings.Replace(card, "created: 2026-09-06\n", "created: 2026-01-06\nupdated: 2026-02-01\n", 1)
	next, _, err := Compose(dated, fullResponse(), stampAt(), DepthDeep, offered)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Date(2026, 9, 12, 9, 0, 0, 0, time.UTC)
	age := func(text string) float64 {
		d, ok := notes.ElapsedDays(nil, "agent/memory/semantic/x.md", "x", frontmatterValue(text, "updated"),
			frontmatterValue(text, "created"), "", "", now)
		if !ok {
			t.Fatalf("no age for:\n%s", text)
		}
		return d
	}
	if before, after := age(dated), age(next); before != after {
		t.Errorf("enrichment moved the card's age from %.0f to %.0f days", before, after)
	}
}
