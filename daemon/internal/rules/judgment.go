package rules

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"sort"
)

// The judgment hash: the part of the contract an enrichment judgment reads
// (#784).
//
// Hash covers the whole block, and that is right for what it answers: which
// contract a filing ran under. It was wrong as the key of an enrichment
// judgment. Every edit to the contract made every judged note owed again, and
// most edits were to fields the judgment never reads: over the nine nights to
// 2026-09-30, 84% of the re-judgments that changed nothing but the stamps
// followed a contract edit (task 180's measurement).
//
// What the enrichment path reads from the contract, enumerated from the code
// rather than from memory:
//
//   - memory_types: the prompt lists them (TypesSorted) and the schema gate
//     holds the answer's type to them. This is the judgment's input.
//   - default_type and deprecations ride with it as the rest of the type
//     vocabulary. Nothing in the enrichment path reads them today, and an edit
//     to either is an edit to what a type means, so a judgment made before it
//     is one made under different words.
//   - routing and classes decide which class directories the batch walks, so
//     they choose which notes are offered, not what a judgment says.
//   - recall_exempt_areas, model_exempt_spaces and record_kinds decide whether
//     a note may be offered at all. A note they newly refuse is skipped; one
//     they newly admit has never been judged and is owed a first judgment.
//   - thresholds supply the confidence floor, which the render applies to the
//     model's confidence. A changed floor applies at each note's next judgment
//     rather than buying every note a new one.
//
// The importance rubric is prose outside the block, kept out of Hash by the
// session-3 ruling (agentm-vault § Dreaming), and it stays out of this one for
// the same reason: the next deep pass proposes against the new words.
//
// The field list is pinned by TestEveryContractFieldIsClassified, which fails
// on a field added to the block and not classified here.
type judgment struct {
	MemoryTypes  []string          `json:"memory_types"`
	DefaultType  string            `json:"default_type"`
	Deprecations map[string]string `json:"deprecations"`
}

// judgmentFields names the block fields the judgment hash covers.
var judgmentFields = map[string]bool{
	"MemoryTypes": true, "DefaultType": true, "Deprecations": true,
}

// judgmentHash is over the type vocabulary, canonically serialized. The types
// are sorted first: the prompt lists them sorted, so reordering the list is not
// a change the model could see.
func (b block) judgmentHash() string {
	types := append([]string(nil), b.MemoryTypes...)
	sort.Strings(types)
	canonical, err := json.Marshal(judgment{
		MemoryTypes: types, DefaultType: b.DefaultType, Deprecations: b.Deprecations,
	})
	if err != nil {
		return "unhashable"
	}
	// Prefixed, so a judgment hash can never equal a whole-contract hash by
	// accident of two blocks serializing alike.
	sum := sha256.Sum256(append([]byte("judgment/1\n"), canonical...))
	return hex.EncodeToString(sum[:])[:16]
}

// ParseText parses a contract from its text, as Load would read the file. For
// readers of a contract's history, which have the text and no file.
func ParseText(text, source string) (*Rules, error) {
	return parse(text, source, false)
}
