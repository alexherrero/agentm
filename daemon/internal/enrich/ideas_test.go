package enrich

import (
	"strings"
	"testing"
)

// The carve-out is one flat folder. A prefix match would have made every
// subfolder the operator makes under it eligible too, and a `personal/ideas.md`
// or `personal/Home/…` note must never read as an idea card.
func TestAnIdeaCardIsAMarkdownFileDirectlyInsideTheFolder(t *testing.T) {
	for rel, want := range map[string]bool{
		"personal/ideas/port-simcity-1989.md":    true,
		"personal/ideas/a.md":                    true,
		"personal/ideas/sub/deeper.md":           false,
		"personal/ideas/.hidden.md":              false,
		"personal/ideas/notes.txt":               false,
		"personal/ideas/":                        false,
		"personal/ideas.md":                      false,
		"personal/Home/recipes/stew.md":          false,
		"agent/memory/semantic/doom-llm-npcs.md": false,
		"Ideas.md":                               false,
		`personal\ideas\windows-spelling.md`:     true,
	} {
		if got := IsIdeaCard(rel); got != want {
			t.Errorf("IsIdeaCard(%q) = %v, want %v", rel, got, want)
		}
	}
}

// ideaCard is a card the operator filed into `personal/ideas/`: active, their
// group, a dismissal day, no lifecycle — and their own text under it.
const ideaCard = "---\ntitle: Port the 1989 SimCity to a modern target\ntype: idea\n" +
	"area: coding\nsummary: The operator's line.\nimportance: 4\nstatus: active\n" +
	"dismissed: 2026-05-24\nfiling_confidence: high\nsource: operator-direct\n" +
	"trust: trusted\ncreated: 2026-05-20\nupdated: 2026-05-20\ntags: [games]\n---\n\n" +
	"Rebuild the original in a modern engine, keeping the simulation rules.\n"

const ideaText = "Rebuild the original in a modern engine, keeping the simulation rules.\n"

// A model that tries to re-grade the idea: another kind, another title, and
// a confidence either side of the floor.
func regrading(confidence float64) Response {
	return Response{
		Title: "SimCity port feasibility", Type: "reference", Summary: "The night's summary.",
		Tags: []string{"games", "retro"}, Confidence: confidence, ImportanceProposed: 7,
		Body: "The 1989 source was released in 2008 as Micropolis, which makes the port a build rather than a reverse-engineering job.",
	}
}

// Ruling 6 of 2026-09-20, at the unit the night composes: whatever the score,
// an idea card keeps its standing, its name and its kind, never gains a
// lifecycle, and keeps the operator's group and dismissal — while the pass
// still does its work, a section under its own heading.
func TestAnIdeaCardIsThoughtThroughAndNeverRegraded(t *testing.T) {
	for _, tc := range []struct {
		name       string
		confidence float64
		depth      Depth
	}{
		{"deep, above the floor", 0.95, DepthDeep},
		{"deep, below the floor", 0.10, DepthDeep},
		{"light, above the floor", 0.95, DepthLight},
		{"light, below the floor", 0.10, DepthLight},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := Stamp{Version: "enrich/1", ConfidenceFloor: 0.65, At: stampAt().At, OperatorFiled: true}
			next, v, err := Compose(ideaCard, regrading(tc.confidence), s, tc.depth, nil)
			if err != nil {
				t.Fatal(err)
			}
			if v.Status != "active" || v.FilingConfidence != "high" || v.Sank {
				t.Errorf("the verdict re-graded the idea: %+v", v)
			}
			for _, want := range []string{
				"title: Port the 1989 SimCity to a modern target",
				"type: idea", "area: coding", "status: active", "dismissed: 2026-05-24",
				"filing_confidence: high", "source: operator-direct",
			} {
				if !strings.Contains(next, "\n"+want+"\n") {
					t.Errorf("%q is gone:\n%s", want, next)
				}
			}
			for _, never := range []string{"\nlifecycle:", "\nlifecycle_since:", "type: reference",
				"status: unfiled", "filing_confidence: low", "SimCity port feasibility"} {
				if strings.Contains(next, never) {
					t.Errorf("the card carries %q:\n%s", never, next)
				}
			}
			if !strings.Contains(bodyOf(t, next), ideaText) || !strings.HasPrefix(
				strings.TrimLeft(bodyOf(t, next), "\n"), ideaText) {
				t.Errorf("the operator's text is not first and whole:\n%s", next)
			}
			section := strings.Contains(next, DreamingHeading+" (")
			if tc.depth == DepthDeep && !section {
				t.Errorf("the deep pass added no section — the night did not think the idea through:\n%s", next)
			}
			if tc.depth == DepthLight && section {
				t.Errorf("the light pass wrote a body section:\n%s", next)
			}
		})
	}
}

// The second-verdict rule sinks a card judged below the floor twice. An idea
// card must not sink even when it reads, on disk, exactly like one that would:
// an earlier stamp and `unfiled`.
func TestAnIdeaCardNeverSinksEvenWhenItLooksJudgedBelow(t *testing.T) {
	card := strings.Replace(ideaCard, "status: active\n",
		"status: unfiled\nenriched_at: 2026-09-01T00:00:00Z\n", 1)
	s := Stamp{Version: "enrich/1", ConfidenceFloor: 0.65, At: stampAt().At, OperatorFiled: true}
	next, v, err := Compose(card, regrading(0.05), s, DepthDeep, nil)
	if err != nil {
		t.Fatal(err)
	}
	if v.Sank || strings.Contains(next, "\nlifecycle:") {
		t.Errorf("the idea card sank:\n%s", next)
	}
	if v.Status != "unfiled" {
		t.Errorf("status %q, want the card's own `unfiled` kept as it was", v.Status)
	}

	// The same bytes without the posture do sink — which is what makes the
	// case above a test of the guard rather than of the fixture.
	plain := Stamp{Version: "enrich/1", ConfidenceFloor: 0.65, At: stampAt().At}
	_, pv, err := Compose(card, regrading(0.05), plain, DepthDeep, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !pv.Sank {
		t.Fatalf("the fixture does not reach the sinking rule without the guard: %+v", pv)
	}
}

// A card filed with no standing written down is read as the ruling says it
// stands: active, at the operator's confidence.
func TestKeptFilingReadsAnUnstatedCardAsActive(t *testing.T) {
	v := KeptFiling("---\ntitle: t\ntype: idea\n---\n\nbody\n")
	if v != (FilingVerdict{Status: "active", FilingConfidence: "high"}) {
		t.Errorf("KeptFiling = %+v", v)
	}
}

// `area` and `dismissed` are carried for any card, not only under the posture:
// a rewrite that dropped the group would move an idea to the wrong heading, and
// one that dropped the day would put a retired idea back on the list.
func TestTheIdeaFieldsSurviveAnyRewrite(t *testing.T) {
	next, _, err := Compose(ideaCard, regrading(0.95), stampAt(), DepthDeep, nil)
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"\narea: coding\n", "\ndismissed: 2026-05-24\n"} {
		if !strings.Contains(next, want) {
			t.Errorf("%q was dropped by a rewrite:\n%s", want, next)
		}
	}
}

// The zero posture keeps the default that every other card relies on: a note
// with no lifecycle of its own starts `active`.
func TestTheDefaultCarryStillStartsTheAgingAxis(t *testing.T) {
	next, _, err := Compose("---\ntitle: x\nstatus: unfiled\n---\n\nbody\n",
		Response{Title: "x", Type: "reference", Summary: "s", Confidence: 0.9}, stampAt(), DepthLight, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(next, "\nlifecycle: active\n") {
		t.Errorf("a class card lost its default lifecycle:\n%s", next)
	}
}
