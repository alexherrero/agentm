package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"math/rand"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/enrich"
	"github.com/alexherrero/agentm/daemon/internal/meters"
)

// `agentmd completeness` — the sampling and the claim split, and no verdict.
//
// This half is deterministic and free. It reads the enrichment journal, draws a
// sample, splits each source into claims, and prints the pairs. The grading is a
// model call and lives in Python, because a scorecard number that costs money
// should be produced by the thing that also knows how to report it.
//
// The journal rather than the vault, because the vault holds only what a note
// says *now*. Completeness is a question about what it used to say, and the
// journal is the one place that records the body enrichment replaced.
//
// # The default sample size
//
// Thirty, and it is a flag. It matches the floor this pass was validated against,
// which is the only number here anybody has evidence for — below it the by-class
// report starts having cells with one note in them, and a class average over one
// note is not an average. Raising it costs one model call per note and nothing
// else; the flag is there so that trade is made by whoever is paying for it
// rather than by a constant.
const defaultCompletenessSample = 30

// completenessPair is one source and its rewrite, ready to be graded.
type completenessPair struct {
	Rel string `json:"rel"`
	// Class is what the by-class report groups on: the note's type after
	// enrichment, because that is the class the note is in now.
	Class string `json:"class"`
	At    string `json:"at"`
	// Claims are numbered by position, and the numbering is the contract with
	// the grader — it answers in claim indices.
	Claims  []string `json:"claims"`
	Rewrite string   `json:"rewrite"`
}

type completenessReport struct {
	Journal string `json:"journal"`
	// Available and Sampled are separate because they answer different
	// questions, and a report that prints only the second invites the reader to
	// assume it is the first.
	Available int `json:"available"`
	Sampled   int `json:"sampled"`
	// Skipped counts entries with nothing gradable in them, by reason, so a
	// small sample is legible rather than mysterious.
	Skipped map[string]int     `json:"skipped,omitempty"`
	Seed    int64              `json:"seed"`
	Pairs   []completenessPair `json:"pairs"`
	ByClass map[string]int     `json:"by_class"`
}

func cmdCompleteness(args []string) error {
	fs := newFlagSet("completeness")
	opts := bindCommon(fs)
	sample := fs.Int("sample", defaultCompletenessSample,
		"how many enriched notes to draw for grading")
	seed := fs.Int64("seed", 0, "seed for the draw (0 picks one and prints it)")
	journalPath := fs.String("journal", "", "override the enrichment journal path")
	asJSON := fs.Bool("json", false, "emit the report as JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}

	path := *journalPath
	if path == "" {
		cfg, err := config.Load(*opts)
		if err != nil {
			return err
		}
		path = enrich.NewFileJournal(filepath.Dir(cfg.IndexPath)).Path()
	}

	entries, err := readJournal(path)
	if err != nil {
		return err
	}

	rep := completenessReport{
		Journal: path, Available: len(entries), Seed: *seed,
		Skipped: map[string]int{}, ByClass: map[string]int{},
	}
	if rep.Seed == 0 {
		rep.Seed = int64(len(entries))*1_000_003 + 7
	}

	var gradable []completenessPair
	for _, e := range entries {
		if strings.TrimSpace(e.Previous) == "" {
			rep.Skipped["no source recorded"]++
			continue
		}
		claims := meters.Claims(bodyOf(e.Previous))
		if len(claims) == 0 {
			// A source with nothing to lose cannot be graded on losing it. Held
			// out rather than scored 1.0, which would be a free pass sitting in
			// the average pretending to be a measurement.
			rep.Skipped["source has no claims"]++
			continue
		}
		class := enrich.FrontmatterValue(e.Next, "type")
		if class == "" {
			class = enrich.FrontmatterValue(e.Previous, "type")
		}
		if class == "" {
			class = "untyped"
		}
		gradable = append(gradable, completenessPair{
			Rel: e.Rel, Class: class, At: e.At,
			Claims: claims, Rewrite: bodyOf(e.Next),
		})
	}

	// Newest first, then drawn at random from the whole set. The sort is so a
	// zero seed is reproducible across runs on the same journal rather than
	// depending on file order.
	sort.SliceStable(gradable, func(i, j int) bool {
		if gradable[i].At != gradable[j].At {
			return gradable[i].At > gradable[j].At
		}
		return gradable[i].Rel < gradable[j].Rel
	})
	rng := rand.New(rand.NewSource(rep.Seed))
	rng.Shuffle(len(gradable), func(i, j int) {
		gradable[i], gradable[j] = gradable[j], gradable[i]
	})
	if *sample > 0 && *sample < len(gradable) {
		gradable = gradable[:*sample]
	}
	rep.Pairs = gradable
	rep.Sampled = len(gradable)
	for _, p := range gradable {
		rep.ByClass[p.Class]++
	}

	if *asJSON {
		return json.NewEncoder(os.Stdout).Encode(rep)
	}
	fmt.Printf("journal   %s\n", rep.Journal)
	fmt.Printf("available %d enrichment write(s)\n", rep.Available)
	for reason, n := range rep.Skipped {
		fmt.Printf("  skipped %d — %s\n", n, reason)
	}
	fmt.Printf("sampled   %d (seed %d)\n", rep.Sampled, rep.Seed)
	classes := make([]string, 0, len(rep.ByClass))
	for c := range rep.ByClass {
		classes = append(classes, c)
	}
	sort.Strings(classes)
	for _, c := range classes {
		fmt.Printf("  %-14s %d\n", c, rep.ByClass[c])
	}
	total := 0
	for _, p := range rep.Pairs {
		total += len(p.Claims)
	}
	if rep.Sampled > 0 {
		fmt.Printf("claims    %d (%.1f per note)\n", total,
			float64(total)/float64(rep.Sampled))
	}
	fmt.Println("\nno grading here — pass --json to the Python grader, which " +
		"makes the model call.")
	return nil
}

// journalLine is the on-disk shape, read as written.
//
// `enrich.JournalEntry` types `at` as a `time.Time`, and this pass only ever
// sorts and prints it. Keeping it a string means a journal line with an
// unparseable timestamp costs its own ordering rather than the whole run.
type journalLine struct {
	At       string `json:"at"`
	Rel      string `json:"rel"`
	Trigger  string `json:"trigger"`
	Previous string `json:"previous"`
	Next     string `json:"next"`
}

// readJournal reads the append-only enrichment journal.
//
// A malformed line is skipped rather than fatal. The journal is appended to
// while other things run, and refusing to report because one line was half
// written would make the pass fail exactly when the corpus is busiest.
func readJournal(path string) ([]journalLine, error) {
	f, err := os.Open(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, fmt.Errorf("no enrichment journal at %s — nothing has "+
				"been enriched yet, so there is nothing to grade", path)
		}
		return nil, err
	}
	defer f.Close()

	var out []journalLine
	sc := bufio.NewScanner(f)
	// A note body is the payload here, twice over, so the default 64KB line cap
	// would drop exactly the longest notes.
	sc.Buffer(make([]byte, 0, 64*1024), 8*1024*1024)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		var e journalLine
		if json.Unmarshal([]byte(line), &e) != nil {
			continue
		}
		out = append(out, e)
	}
	return out, sc.Err()
}

// bodyOf drops the frontmatter block and returns the prose.
//
// Frontmatter is not the note for this purpose: tags, confidence and timestamps
// change by design on every write, and grading a rewrite for "losing" a
// timestamp would score every note in the corpus as incomplete.
func bodyOf(raw string) string {
	s := strings.TrimLeft(raw, "\ufeff")
	if !strings.HasPrefix(s, "---") {
		return strings.TrimSpace(s)
	}
	nl := strings.Index(s, "\n")
	if nl < 0 {
		return strings.TrimSpace(s)
	}
	if end := strings.Index(s[nl:], "\n---"); end >= 0 {
		rest := s[nl+end+len("\n---"):]
		if i := strings.Index(rest, "\n"); i >= 0 {
			return strings.TrimSpace(rest[i+1:])
		}
		return ""
	}
	return strings.TrimSpace(s)
}
