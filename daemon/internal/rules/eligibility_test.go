package rules

import (
	"os"
	"path/filepath"
	"testing"
)

// The eligibility gate exists before the pass it gates, and that ordering is the
// point rather than an accident of scheduling.
//
// This repo has already shipped a promotion criterion whose reader never
// arrived — `merge_gate_passed`, computed and printed and consumed by nothing,
// which is why a stepped decay curve sat in shadow mode for weeks. A privacy
// boundary written *after* the pass that would violate it is the same bet with a
// much worse loss: the failure is not a stalled feature, it is the operator's
// private notes in a model call they did not make.

const exemptBlock = `classes:
  semantic: Facts.
  procedural: Recipes.
  episodic: Traces.
  entities: Referents.
  crystallized: Lessons.
  mocs: Maps.
memory_types: [preference, convention, reference, workflow, fix, idea]
default_type: preference
routing:
  preference: memory/semantic
  convention: memory/semantic
  reference: memory/semantic
  workflow: memory/procedural
  fix: memory/procedural
  idea: desk
record_kinds: [brief]
deprecations: {}
dampened_spaces: [Personal]
model_exempt_spaces: [Personal]
contract_exempt_spaces: [Personal]
warrants: {}
thresholds: {low_confidence: 0.65}
`

func loadExempt(t *testing.T, block string) *Rules {
	t.Helper()
	path := filepath.Join(t.TempDir(), "storage-rules.md")
	if err := os.WriteFile(path, []byte(rulesFile(block)), 0o644); err != nil {
		t.Fatal(err)
	}
	r, err := LoadFile(path)
	if err != nil {
		t.Fatalf("LoadFile: %v", err)
	}
	return r
}

func TestABackgroundPassMayNotReadAnExemptSpace(t *testing.T) {
	r := loadExempt(t, exemptBlock)
	for _, rel := range []string{
		"personal/Church/lesson.md",
		"personal/Home/Recipes/turkey.md",
		"personal/Tech/pages.md", // macOS treats the two spellings as one directory
	} {
		if r.MayReadWithModel(rel) {
			t.Errorf("a background model pass was allowed to read %q", rel)
		}
	}
}

func TestABackgroundPassMayReadEverythingElse(t *testing.T) {
	r := loadExempt(t, exemptBlock)
	for _, rel := range []string{
		"agent/memory/semantic/a-fact.md",
		"calendar/2026/2026-08-20_day.md",
		"standards/storage-rules.md",
		"projects/blog/post.md",
	} {
		if !r.MayReadWithModel(rel) {
			t.Errorf("a background model pass was refused %q, which is not exempt", rel)
		}
	}
}

// A folder named `personal` deep in the tree must not inherit a rule written
// about the operator's own space. The rule is about a space, not a word.
func TestANestedFolderNamedPersonalIsNotTheSpace(t *testing.T) {
	r := loadExempt(t, exemptBlock)
	if !r.MayReadWithModel("agent/desk/projects/x/personal/notes.md") {
		t.Error("a nested folder named `personal` was treated as the Personal space")
	}
}

// The two lists answer different questions and are deliberately separate: a
// space can rank low and still be safe to summarize, and a space can rank
// normally and still be nobody's business to send anywhere.
func TestTheTwoExemptionsAreIndependent(t *testing.T) {
	block := exemptBlock
	block = replaceOnce(block, "model_exempt_spaces: [Personal]", "model_exempt_spaces: [Calendar]")
	r := loadExempt(t, block)

	if r.MayReadWithModel("calendar/2026/day.md") {
		t.Error("Calendar is model-exempt in this contract and was allowed")
	}
	if !r.MayReadWithModel("personal/Church/lesson.md") {
		t.Error("Personal is not model-exempt in this contract and was refused — the " +
			"two lists are not the same list")
	}
	if !r.IsContractExempt("personal/Church/lesson.md") {
		t.Error("Personal is contract-exempt in this contract and was not treated so")
	}
}

// A contract naming no exemptions bars nothing. That is a legitimate choice
// rather than a broken file — and it is what every contract written before this
// field existed says.
func TestAContractWithNoExemptionsBarsNothing(t *testing.T) {
	block := exemptBlock
	block = replaceOnce(block, "model_exempt_spaces: [Personal]\n", "")
	block = replaceOnce(block, "contract_exempt_spaces: [Personal]\n", "")
	r := loadExempt(t, block)

	if !r.MayReadWithModel("personal/Church/lesson.md") {
		t.Error("a contract naming no model exemption barred something")
	}
	if r.IsContractExempt("personal/Church/lesson.md") {
		t.Error("a contract naming no contract exemption exempted something")
	}
}

// The shipped contract is what a fresh install runs on, and what it must not
// let out is the operator's certificates and recovery codes — not the whole of
// `personal/`.
//
// This test used to assert the opposite half of that sentence: that a
// background model pass could not read `personal/` at all. The operator
// reversed it in the axis-per-space landing, so the assertion is rewritten
// rather than deleted, and it still checks the same thing the old one did —
// that a fresh install protects what must be protected, by the boundary the
// contract now uses. Enrichment reads `personal/` and writes its frontmatter;
// `personal/Home/Important Docs` reaches no surface at all.
func TestTheShippedContractWallsImportantDocsAndOpensPersonal(t *testing.T) {
	clearEnv(t)
	r, err := Load("")
	if err != nil {
		t.Fatalf("the shipped contract does not parse: %v", err)
	}
	if !r.MayReadWithModel("personal/Church/lesson.md") {
		t.Error("the shipped contract bars a model pass from personal/; the ruling " +
			"opened it, writing frontmatter only")
	}
	if !r.IsContractExempt("personal/Church/lesson.md") {
		t.Error("the shipped contract holds personal/ to the memory contract; its " +
			"files are documents, not memories")
	}
	if !r.IsRecallExempt("personal/Home/Important Docs/Marriage License.md") {
		t.Error("the shipped contract serves Important Docs; it is the one area " +
			"walled from the corpus entirely")
	}
	if r.IsRecallExempt("personal/Home/Recipes/turkey.md") {
		t.Error("the shipped contract walls a recipe; the wall is one folder, not " +
			"the space around it")
	}
	// A near-miss the wall must not swallow, and must not be swallowed by.
	if r.IsRecallExempt("personal/Homework/algebra.md") {
		t.Error("the wall matched `personal/Homework` against `personal/Home`; an " +
			"area is matched segment by segment, never by string prefix")
	}
}

func replaceOnce(s, old, new string) string {
	i := indexOf(s, old)
	if i < 0 {
		panic("fixture substring not found: " + old)
	}
	return s[:i] + new + s[i+len(old):]
}

func indexOf(s, sub string) int {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return i
		}
	}
	return -1
}
