package enrich

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
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
	{ID: "drive-upload-staging-churns", Rel: "Agent/memory/procedural/drive-upload-staging-churns.md",
		Title: "Drive upload staging churns transient files", Summary: "Drive re-uploads files mid-write."},
	{ID: "vault-location", Rel: "Agent/memory/semantic/vault-location.md",
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
