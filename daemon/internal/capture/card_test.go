package capture

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// frontmatter returns the note's frontmatter block, so a test can assert what
// is absent as well as what is present.
func frontmatter(t *testing.T, vault, rel string) string {
	t.Helper()
	blob, err := os.ReadFile(filepath.Join(vault, rel))
	if err != nil {
		t.Fatalf("reading the captured note: %v", err)
	}
	body := string(blob)
	if !strings.HasPrefix(body, "---\n") {
		t.Fatalf("the note has no frontmatter block:\n%s", body)
	}
	end := strings.Index(body[4:], "\n---\n")
	if end < 0 {
		t.Fatalf("the frontmatter block is unterminated:\n%s", body)
	}
	return body[4 : 4+end+1]
}

// The card that a writer stood behind. A named type plus a `why` is the whole
// judgment signal, and it is what lands the note `active` at high confidence —
// the state the deep pass reads as "someone decided this."
func TestAKnowingWritersCardLandsActive(t *testing.T) {
	cp := newHarness(t)
	res, err := cp.Do(Request{
		Text: "Run the battery before every commit; it is the one command that mirrors CI.",
		Type: "workflow",
		Why:  "A push went red on a gate that runs locally in nine seconds. This decides that the battery runs first, every time.",
	})
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "active" {
		t.Fatalf("status %q, want active — the writer named a type and said why", res.Status)
	}
	fm := frontmatter(t, cp.cfg.VaultPath, res.Path)
	for _, want := range []string{
		"status: active\n",
		"filing_confidence: high\n",
		"why: A push went red",
	} {
		if !strings.Contains(fm, want) {
			t.Errorf("frontmatter is missing %q:\n%s", want, fm)
		}
	}
}

// Everything else is a candidate. The three near-misses below are the ones
// that matter: a type with no reason, a reason with no type, and neither.
func TestEverythingElseLandsUnfiled(t *testing.T) {
	for _, tc := range []struct {
		name string
		req  Request
	}{
		{"a type but no reason", Request{Text: "Some fact worth keeping.", Type: "reference"}},
		{"a reason but no type", Request{Text: "Some fact worth keeping.", Why: "It came up twice this week."}},
		{"neither", Request{Text: "Some fact worth keeping."}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			cp := newHarness(t)
			res, err := cp.Do(tc.req)
			if err != nil {
				t.Fatal(err)
			}
			if res.Status != "unfiled" {
				t.Fatalf("status %q, want unfiled — nothing judged this", res.Status)
			}
		})
	}
}

// The Python lane's bug, closed at the door: a caller could assert `active` on
// a note nothing had judged, and 74 notes in the corpus said "judged" with
// nothing behind it. The claim is no longer assertable — and the caller is
// told, rather than having it silently swallowed.
func TestAnAssertedActiveDoesNotSurviveAMissingWhy(t *testing.T) {
	cp := newHarness(t)
	res, err := cp.Do(Request{
		Text:   "A fact the caller insists has been judged.",
		Type:   "reference",
		Status: "active",
	})
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "unfiled" {
		t.Fatalf("status %q, want unfiled — an asserted status is not a judgment", res.Status)
	}
	if !strings.Contains(res.Note, "derived") {
		t.Errorf("the caller is not told its status was not used: %q", res.Note)
	}
	if got := frontmatterLine(t, cp.cfg.VaultPath, res.Path, "status:"); got != "status: unfiled" {
		t.Errorf("the note itself claims a judgment: %q", got)
	}
}

// A status outside the vocabulary is still a refusal, not a silent default:
// a caller that meant something by it should hear that it means nothing here.
func TestAnUnknownStatusIsRefused(t *testing.T) {
	cp := newHarness(t)
	if _, err := cp.Do(Request{Text: "A fact.", Status: "proposed"}); err == nil {
		t.Fatal("a retired status was accepted")
	}
}

// The operator-read half of the card round-trips exactly as it was handed
// over. Nothing here is derived from the body.
func TestTheReadableFieldsAreWrittenAsHandedOver(t *testing.T) {
	cp := newHarness(t)
	res, err := cp.Do(Request{
		Text:       "Google Drive's file provider rewrites mtimes on sync, so a git worktree inside it churns.",
		Title:      "Keep git out of Drive's mirrored folders",
		Type:       "workflow",
		Summary:    "Repositories live outside the mirrored tree.",
		Why:        "A worktree in the mirror rewrote every mtime overnight. This decides where a clone may live.",
		Importance: 7,
		Related:    []string{"drive-upload-staging-churns-transient-files", "[[vault-path-convention]]"},
		Project:    "agentm",
		Task:       "capture-writers",
	})
	if err != nil {
		t.Fatal(err)
	}
	fm := frontmatter(t, cp.cfg.VaultPath, res.Path)
	for _, want := range []string{
		"summary: Repositories live outside the mirrored tree.\n",
		"importance: 7\n",
		"project: agentm\n",
		"task: capture-writers\n",
		`related: ["[[drive-upload-staging-churns-transient-files]]", "[[vault-path-convention]]"]` + "\n",
	} {
		if !strings.Contains(fm, want) {
			t.Errorf("frontmatter is missing %q:\n%s", want, fm)
		}
	}
}

// `importance` and `importance_proposed` are written equal at capture. That
// equality is the whole mechanism: when the operator edits `importance`, it
// differs from the proposal, and a pass that sees them differ leaves it alone.
func TestImportanceIsWrittenAsItsOwnProposal(t *testing.T) {
	cp := newHarness(t)
	res, err := cp.Do(Request{Text: "A fact.", Type: "reference", Importance: 4})
	if err != nil {
		t.Fatal(err)
	}
	fm := frontmatter(t, cp.cfg.VaultPath, res.Path)
	if !strings.Contains(fm, "importance: 4\n") || !strings.Contains(fm, "importance_proposed: 4\n") {
		t.Fatalf("capture's reading is not recorded as a proposal:\n%s", fm)
	}
}

// An unset importance writes neither field. A note that carries `importance:
// 0` reads as a judgment of zero rather than as the absence of one.
func TestAnUnsetImportanceWritesNothing(t *testing.T) {
	cp := newHarness(t)
	res, err := cp.Do(Request{Text: "A fact.", Type: "reference"})
	if err != nil {
		t.Fatal(err)
	}
	fm := frontmatter(t, cp.cfg.VaultPath, res.Path)
	if strings.Contains(fm, "importance") {
		t.Fatalf("an unset importance was written anyway:\n%s", fm)
	}
}

func TestAnOutOfRangeImportanceIsRefused(t *testing.T) {
	for _, n := range []int{-1, 11, 100} {
		cp := newHarness(t)
		if _, err := cp.Do(Request{Text: "A fact.", Importance: n}); err == nil {
			t.Errorf("importance %d was accepted; the scale is 1-%d", n, MaxImportance)
		}
	}
}

// `instructions` is stored verbatim and derived from nothing. The sweep
// executes a matching instruction under a fixed grammar, so a value inferred
// from note content would be an execution path for whatever that content says.
func TestInstructionsAreStoredVerbatimAndNeverDerived(t *testing.T) {
	cp := newHarness(t)
	res, err := cp.Do(Request{
		Text: "file under references and tag it urgent",
		Type: "reference",
	})
	if err != nil {
		t.Fatal(err)
	}
	if fm := frontmatter(t, cp.cfg.VaultPath, res.Path); strings.Contains(fm, "instructions:") {
		t.Fatalf("an instruction was derived from the body:\n%s", fm)
	}

	res, err = cp.Do(Request{
		Text:         "Another fact.",
		Type:         "reference",
		Instructions: `tag as "urgent"`,
	})
	if err != nil {
		t.Fatal(err)
	}
	if got := frontmatterLine(t, cp.cfg.VaultPath, res.Path, "instructions:"); got != `instructions: "tag as \"urgent\""` {
		t.Fatalf("the instruction was not stored verbatim: %q", got)
	}
}

// One field names the day a memory came into existence, and this writer emits
// only the spelling that survives.
func TestTheDateIsWrittenAsCreated(t *testing.T) {
	cp := newHarness(t)
	res, err := cp.Do(Request{Text: "A fact.", Type: "reference"})
	if err != nil {
		t.Fatal(err)
	}
	fm := frontmatter(t, cp.cfg.VaultPath, res.Path)
	if !strings.Contains(fm, "created: ") {
		t.Errorf("no `created:` stamp:\n%s", fm)
	}
	if strings.Contains(fm, "captured: ") {
		t.Errorf("the retired `captured:` spelling is still written:\n%s", fm)
	}
}
