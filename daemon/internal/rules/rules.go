// Package rules reads the filing contract — the one file that decides where a
// memory goes and what shape it takes.
//
// `standards/storage-rules.md` is authoritative for filing, and it is read at
// runtime rather than compiled in. That inverts the usual arrangement: the
// contract stops being something a design specifies and code implements, and
// becomes something the operator writes and the daemon obeys. Changing where a
// type routes, retiring a value, or moving a threshold is an edit to a markdown
// file. No recompile, no release; the rules take effect on the next capture.
//
// This package is the *only* parser of that file. Everything that needs the
// taxonomy — capture's type validation, the MCP tool schema, and the Python
// batch layer via `agentmd rules --json` — reads it from here. A second parser
// would be a second thing to drift, and the design's whole claim is that a type
// added to the rules exists everywhere at once.
//
// # Absence falls through; corruption halts
//
// A rules file that is not there is not an error: resolution moves to the next
// source, ending at the copy embedded in this binary. A rules file that *is*
// there and will not parse returns an error and never falls back. Falling back
// is exactly the failure the arrangement exists to prevent — a model handed a
// malformed rule does not stop, it improvises, and the filing that results looks
// fine and is wrong. So filing halts: notes wait as `unfiled`, the digest names
// the parse failure, and nothing files anywhere until the file parses again.
package rules

import (
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"gopkg.in/yaml.v3"
)

//go:embed storage-rules.default.md
var packagedDefault string

// PackagedDefaultSource is what Rules.Source reads when the embedded copy won.
const PackagedDefaultSource = "<embedded default>"

// blockRe finds the fenced machine-readable block. The prose around it is what
// the enrichment prompt reads; this is what programs read. They are meant to
// agree, and when they disagree the block is what runs.
var blockRe = regexp.MustCompile("(?ms)^```storage-rules[ \t]*\r?\n(.*?)^```[ \t]*(?:\r?\n|\\z)")

var kebabRe = regexp.MustCompile(`^[a-z0-9-]+$`)

// vaultRelative is where the rules file sits inside a vault. Two probes, because
// the memory root and the vault root are not always the same directory: a split
// layout keeps memory under `<vault>/Agent/` and the rules under
// `<vault>/standards/`, so the sibling probe is what finds it from either.
var vaultRelative = []string{
	filepath.Join("standards", "storage-rules.md"),
	filepath.Join("..", "standards", "storage-rules.md"),
}

// Warrant is the evidence a new memory type carries.
//
// A type is added when a query class needs to rank by it, and not otherwise. The
// old taxonomy reached fifty-odd values because every addition was individually
// defensible and nothing ever asked whether the set still cohered. This is what
// asks: name the query class, name the nearest existing type, and say why that
// one does not fit.
type Warrant struct {
	QueryClass string `yaml:"query_class" json:"query_class"`
	Nearest    string `yaml:"nearest" json:"nearest"`
	WhyNot     string `yaml:"why_not" json:"why_not"`
}

// block is the parsed YAML, before validation.
type block struct {
	Classes      map[string]string `yaml:"classes" json:"classes"`
	MemoryTypes  []string          `yaml:"memory_types" json:"memory_types"`
	DefaultType  string            `yaml:"default_type" json:"default_type"`
	Routing      map[string]string `yaml:"routing" json:"routing"`
	RecordKinds  []string          `yaml:"record_kinds" json:"record_kinds"`
	Deprecations map[string]string `yaml:"deprecations" json:"deprecations"`
	// DampenedSpaces are top-level directories demoted on ordinary questions.
	// Optional: a contract that names none dampens nothing, which is the old
	// behaviour and a legitimate choice rather than a broken file.
	DampenedSpaces []string `yaml:"dampened_spaces" json:"dampened_spaces"`
	// ModelExemptSpaces are top-level directories no background model pass may
	// read. A privacy boundary rather than a ranking one, and deliberately a
	// separate list from DampenedSpaces: a space can rank low and still be safe
	// to summarize, and a space can rank normally and still be nobody's business
	// to send anywhere.
	ModelExemptSpaces []string `yaml:"model_exempt_spaces" json:"model_exempt_spaces"`
	// ContractExemptSpaces are top-level directories whose files are documents
	// rather than memories. A missing `type` there is the expected state, not a
	// finding.
	ContractExemptSpaces []string           `yaml:"contract_exempt_spaces" json:"contract_exempt_spaces"`
	Warrants             map[string]Warrant `yaml:"warrants" json:"warrants"`
	Thresholds           map[string]float64 `yaml:"thresholds" json:"thresholds"`
}

// Rules is one parsed filing contract, plus where it came from.
type Rules struct {
	block
	// Source is the path the rules were read from, or PackagedDefaultSource.
	Source string `json:"source"`
	// IsPackagedDefault says the embedded copy won — which means an edit to the
	// operator's own rules file is not taking effect, because there isn't one.
	IsPackagedDefault bool `json:"is_packaged_default"`
	// Hash identifies the contract a filing judgment was made under. It is the
	// `rules_hash` a memory carries in its frontmatter.
	Hash string `json:"hash"`
}

// ObservationalClasses are the three classes filing may write into. The other
// three are derived and rebuildable, and the passes that build them are the only
// things that write there.
var ObservationalClasses = []string{"semantic", "procedural", "episodic"}

// DerivedClasses are rebuildable from the observational three.
var DerivedClasses = []string{"entities", "crystallized", "mocs"}

// Load resolves the rules and parses them.
//
// Resolution order, first source that exists winning: $AGENTM_STORAGE_RULES,
// then the vault's own `standards/storage-rules.md` (both layouts probed), then
// the copy embedded in this binary.
func Load(vaultPath string) (*Rules, error) {
	var probed []string

	if explicit := strings.TrimSpace(os.Getenv("AGENTM_STORAGE_RULES")); explicit != "" {
		probed = append(probed, explicit)
		if text, err := os.ReadFile(explicit); err == nil {
			return parse(string(text), explicit, false)
		} else if !errors.Is(err, os.ErrNotExist) {
			return nil, fmt.Errorf("%s: cannot be read: %w", explicit, err)
		}
	}

	if vaultPath != "" {
		for _, rel := range vaultRelative {
			candidate := filepath.Clean(filepath.Join(vaultPath, rel))
			probed = append(probed, candidate)
			text, err := os.ReadFile(candidate)
			if err == nil {
				return parse(string(text), candidate, false)
			}
			if !errors.Is(err, os.ErrNotExist) {
				return nil, fmt.Errorf("%s: cannot be read: %w", candidate, err)
			}
		}
	}

	return parse(packagedDefault, PackagedDefaultSource, true)
}

// LoadFile parses one specific rules file. Used by the gate and by tests.
func LoadFile(path string) (*Rules, error) {
	text, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	return parse(string(text), path, false)
}

// Default returns the copy embedded in this binary — what `rules --init` seeds a
// vault from.
func Default() string { return packagedDefault }

func parse(text, source string, embedded bool) (*Rules, error) {
	m := blockRe.FindStringSubmatch(text)
	if m == nil {
		return nil, fmt.Errorf("%s: no ```storage-rules fenced block found — the "+
			"machine-readable core is what every consumer reads; prose alone is not a "+
			"rules file", source)
	}

	var b block
	if err := yaml.Unmarshal([]byte(m[1]), &b); err != nil {
		return nil, fmt.Errorf("%s: the rules block is not valid YAML: %w", source, err)
	}
	if err := b.validate(source); err != nil {
		return nil, err
	}

	r := &Rules{block: b, Source: source, IsPackagedDefault: embedded}
	r.Hash = b.contentHash()
	return r, nil
}

// contentHash is over the block's parsed content, canonically serialized — not
// over its raw text. Rewording the prose or reflowing the YAML must not
// invalidate every judgment in the corpus; changing what the block *says* must.
func (b block) contentHash() string {
	canonical, err := json.Marshal(b)
	if err != nil {
		// A struct of strings and maps cannot fail to marshal. If it somehow
		// does, a hash nothing matches is safer than a hash everything matches:
		// it marks every memory stale rather than every memory current.
		return "unhashable"
	}
	sum := sha256.Sum256(canonical)
	return hex.EncodeToString(sum[:])[:16]
}

// validate checks shape and vocabulary, not just parseability.
//
// A block that is valid YAML but names a class that does not exist, or routes a
// type nowhere, is a malformed rule the model would otherwise be handed to
// interpret. Shape validation is as load-bearing as the parse.
func (b block) validate(source string) error {
	fail := func(format string, args ...any) error {
		return fmt.Errorf("%s: "+format, append([]any{source}, args...)...)
	}

	if len(b.Classes) == 0 {
		return fail("the rules block is missing required key `classes`")
	}
	if len(b.MemoryTypes) == 0 {
		return fail("the rules block is missing required key `memory_types`")
	}
	if b.Routing == nil {
		return fail("the rules block is missing required key `routing`")
	}
	if b.RecordKinds == nil {
		return fail("the rules block is missing required key `record_kinds`")
	}
	if b.Deprecations == nil {
		return fail("the rules block is missing required key `deprecations`")
	}
	if b.Thresholds == nil {
		return fail("the rules block is missing required key `thresholds`")
	}

	want := map[string]bool{}
	for _, c := range ObservationalClasses {
		want[c] = true
	}
	for _, c := range DerivedClasses {
		want[c] = true
	}
	var missing, unknown []string
	for c := range want {
		if _, ok := b.Classes[c]; !ok {
			missing = append(missing, c)
		}
	}
	for c := range b.Classes {
		if !want[c] {
			unknown = append(unknown, c)
		}
	}
	if len(missing) > 0 || len(unknown) > 0 {
		sort.Strings(missing)
		sort.Strings(unknown)
		var detail []string
		if len(missing) > 0 {
			detail = append(detail, "missing "+strings.Join(missing, ", "))
		}
		if len(unknown) > 0 {
			detail = append(detail, "unknown "+strings.Join(unknown, ", "))
		}
		return fail("`classes` must name exactly the six retrieval classes (%s). A "+
			"class is a directory, and a directory is close to permanent — adding one "+
			"is a design change, not a rules edit", strings.Join(detail, "; "))
	}

	types := map[string]bool{}
	for _, t := range b.MemoryTypes {
		if !kebabRe.MatchString(t) {
			return fail("`memory_types` entry %q is not kebab-case", t)
		}
		if types[t] {
			return fail("`memory_types` contains %q twice", t)
		}
		types[t] = true
	}
	kinds := map[string]bool{}
	for _, k := range b.RecordKinds {
		if !kebabRe.MatchString(k) {
			return fail("`record_kinds` entry %q is not kebab-case", k)
		}
		if kinds[k] {
			return fail("`record_kinds` contains %q twice", k)
		}
		if types[k] {
			return fail("%q appears in both `memory_types` and `record_kinds`. A value "+
				"is a memory type or a record kind, never both — the two registers are "+
				"what keep `type` and `kind` from meaning the same thing", k)
		}
		kinds[k] = true
	}

	if b.DefaultType == "" {
		return fail("the rules block is missing required key `default_type` — capture " +
			"is never blocked on a caller getting the taxonomy right, so an unlabelled " +
			"capture has to land somewhere")
	}
	if !types[b.DefaultType] {
		return fail("`default_type` is %q, which is not a memory type", b.DefaultType)
	}

	var unrouted []string
	for t := range types {
		if _, ok := b.Routing[t]; !ok {
			unrouted = append(unrouted, t)
		}
	}
	if len(unrouted) > 0 {
		sort.Strings(unrouted)
		return fail("memory type(s) %s have no `routing` entry. A type with nowhere to "+
			"go files nowhere", strings.Join(unrouted, ", "))
	}
	var stray []string
	for t := range b.Routing {
		if !types[t] {
			stray = append(stray, t)
		}
	}
	if len(stray) > 0 {
		sort.Strings(stray)
		return fail("`routing` names %s, which is not a memory type", strings.Join(stray, ", "))
	}
	derived := map[string]bool{}
	for _, c := range DerivedClasses {
		derived["memory/"+c] = true
	}
	for t, dest := range b.Routing {
		if derived[dest] {
			return fail("`routing` sends %q to %q, a derived class. Filing may only ever "+
				"write into the three observational classes; the derived three are "+
				"rebuilt from them", t, dest)
		}
	}

	for from, to := range b.Deprecations {
		if !types[to] && !kinds[to] {
			return fail("`deprecations` maps %q to %q, which no register carries. A "+
				"collapse map that points at an unknown value is not mechanical", from, to)
		}
		if types[from] || kinds[from] {
			return fail("%q is listed in `deprecations` and is also still registered. A "+
				"value is retired or current, not both", from)
		}
	}

	for name, w := range b.Warrants {
		if strings.TrimSpace(w.QueryClass) == "" {
			return fail("warrant for %q is missing `query_class`", name)
		}
		if strings.TrimSpace(w.Nearest) == "" {
			return fail("warrant for %q is missing `nearest`", name)
		}
		if strings.TrimSpace(w.WhyNot) == "" {
			return fail("warrant for %q is missing `why_not`", name)
		}
	}

	return nil
}

// IsMemoryType reports whether v is one of the values a memory carries in `type`.
func (r *Rules) IsMemoryType(v string) bool {
	for _, t := range r.MemoryTypes {
		if t == v {
			return true
		}
	}
	return false
}

// IsRecordKind reports whether v is one of the shapes a record carries in `kind`.
func (r *Rules) IsRecordKind(v string) bool {
	for _, k := range r.RecordKinds {
		if k == v {
			return true
		}
	}
	return false
}

// Known reports whether either register carries v.
func (r *Rules) Known(v string) bool { return r.IsMemoryType(v) || r.IsRecordKind(v) }

// ReplacementFor returns the value that replaces a retired one, and whether it
// was retired at all.
func (r *Rules) ReplacementFor(v string) (string, bool) {
	to, ok := r.Deprecations[v]
	return to, ok
}

// TypesSorted is the enum a schema constrains against — sorted, because a JSON
// Schema enum is an ordered array and a stable order keeps a prompt's cache key
// stable.
func (r *Rules) TypesSorted() []string {
	out := append([]string(nil), r.MemoryTypes...)
	sort.Strings(out)
	return out
}

// InSpace reports whether a vault-relative path sits in one of `spaces`.
//
// Matched on the first path segment, case-insensitively. A space is a top-level
// directory: matching deeper would let a folder named `personal` anywhere in the
// tree inherit a rule written about the operator's own, and macOS treats
// `Personal/` and `personal/` as one directory, so a case-sensitive rule would
// be a hazard rather than a precision.
func InSpace(rel string, spaces []string) bool {
	if len(spaces) == 0 {
		return false
	}
	rel = strings.TrimPrefix(strings.ReplaceAll(rel, "\\", "/"), "./")
	first := rel
	if i := strings.IndexByte(rel, '/'); i >= 0 {
		first = rel[:i]
	}
	first = strings.ToLower(first)
	for _, s := range spaces {
		if first == strings.ToLower(strings.Trim(strings.TrimSpace(s), "/")) {
			return true
		}
	}
	return false
}

// MayReadWithModel is the eligibility gate's path rule.
//
// The design states this one in the strongest terms it uses anywhere: background
// model passes never read an exempt space. Enrichment skips it, dreaming never
// sends it to a model, no batch call includes it — "enforced as a path rule in
// the eligibility gate rather than as a convention."
//
// So it is a function that refuses, and it exists before the pass that would
// violate it. This repo has already shipped a criterion whose reader never
// arrived; a privacy boundary written after the thing it bounds is the same bet
// with a worse loss.
//
// Foreground recall is deliberately not covered here. The operator reading their
// own notes in their own session is the operator reading their own notes; what
// this bars is the machinery that runs unattended.
func (r *Rules) MayReadWithModel(rel string) bool {
	return !InSpace(rel, r.ModelExemptSpaces)
}

// ClassFor names the class a note of this type is filed into.
//
// Derived from `routing` rather than from the note's path, because the path does
// not say yet: filing is what creates the class folders and most of the corpus
// has not been filed. A note with no type, or a type the contract does not know,
// has no class — which is a fact about the corpus rather than a gap to fill in
// with a guess.
//
// The second return distinguishes "no class" from a class literally named the
// empty string, so a caller can label the difference instead of drawing both the
// same way.
func (r *Rules) ClassFor(noteType string) (string, bool) {
	dest, ok := r.Routing[strings.TrimSpace(noteType)]
	if !ok {
		return "", false
	}
	// Routing destinations are vault-relative paths — `memory/semantic` for the
	// observational classes, and other spaces such as `desk` for types that are
	// not memories at all. Only the first kind names a class.
	const prefix = "memory/"
	if !strings.HasPrefix(dest, prefix) {
		return "", false
	}
	class := strings.Trim(strings.TrimPrefix(dest, prefix), "/")
	if class == "" || strings.Contains(class, "/") {
		return "", false
	}
	return class, true
}

// IsContractExempt reports whether a path's files are documents rather than
// memories, so a missing `type` or `status` there is the expected state.
func (r *Rules) IsContractExempt(rel string) bool {
	return InSpace(rel, r.ContractExemptSpaces)
}
