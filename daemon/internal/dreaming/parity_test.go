package dreaming

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"
)

// Parity against the recorded Python outputs (filing v2 part 6, task 4).
// `scripts/fixtures/dreaming-parity/expected.json` was written by
// `scripts/health/record_dreaming_parity.py` from the Python producers with
// the clock pinned; nothing here recomputes an expectation. If this fails,
// either the port diverged or the recording was changed — and a rewritten
// recording during the port is a re-audit trigger by the plan's own rule.

const parityPin = "2026-09-05"

type parityRecording struct {
	Pin       string `json:"pin"`
	Lifecycle struct {
		Demoted    [][]any `json:"demoted"`
		Revived    [][]any `json:"revived"`
		Candidates [][]any `json:"archive_candidates"`
		Previews   [][]any `json:"previews"`
		Considered int     `json:"considered"`
	} `json:"lifecycle"`
	Copies []struct {
		Canonical string            `json:"canonical"`
		Copies    []string          `json:"copies"`
		Summary   string            `json:"summary"`
		After     map[string]string `json:"after"`
	} `json:"copies"`
	Promote map[string]struct {
		Sources []string `json:"sources"`
		Slug    string   `json:"slug"`
		Body    string   `json:"body"`
	} `json:"promote"`
}

func parityFixture(t *testing.T) (root string, rec parityRecording) {
	t.Helper()
	dir := filepath.Join("..", "..", "..", "scripts", "fixtures", "dreaming-parity")
	blob, err := os.ReadFile(filepath.Join(dir, "expected.json"))
	if err != nil {
		t.Fatalf("no recording: %v", err)
	}
	if err := json.Unmarshal(blob, &rec); err != nil {
		t.Fatal(err)
	}
	if rec.Pin != parityPin {
		t.Fatalf("the recording is pinned to %s; this test to %s", rec.Pin, parityPin)
	}
	root = t.TempDir()
	src := filepath.Join(dir, "vault")
	err = filepath.Walk(src, func(p string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(src, p)
		dst := filepath.Join(root, rel)
		if info.IsDir() {
			return os.MkdirAll(dst, 0o755)
		}
		b, err := os.ReadFile(p)
		if err != nil {
			return err
		}
		return os.WriteFile(dst, b, 0o644)
	})
	if err != nil {
		t.Fatal(err)
	}
	return root, rec
}

func moves(rows [][]any) []string {
	var out []string
	for _, r := range rows {
		out = append(out, r[0].(string))
	}
	sort.Strings(out)
	return out
}

func rels(ms []Move) []string {
	var out []string
	for _, m := range ms {
		out = append(out, m.Rel)
	}
	sort.Strings(out)
	return out
}

func TestParityWithTheRecordedPythonPass(t *testing.T) {
	root, rec := parityFixture(t)
	now := time.Date(2026, 9, 5, 9, 0, 0, 0, time.UTC)

	// The lifecycle lane: nil rules are the packaged thresholds, the same
	// the recorder's stub returned.
	life, err := PlanLifecycle(root, nil, now, 0)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(rels(life.Demoted), moves(rec.Lifecycle.Demoted)) {
		t.Errorf("demoted: go %v, python %v", rels(life.Demoted), moves(rec.Lifecycle.Demoted))
	}
	if !reflect.DeepEqual(rels(life.Revived), moves(rec.Lifecycle.Revived)) {
		t.Errorf("revived: go %v, python %v", rels(life.Revived), moves(rec.Lifecycle.Revived))
	}
	if !reflect.DeepEqual(rels(life.Candidates), moves(rec.Lifecycle.Candidates)) {
		t.Errorf("candidates: go %v, python %v", rels(life.Candidates), moves(rec.Lifecycle.Candidates))
	}
	if !reflect.DeepEqual(rels(life.Previews), moves(rec.Lifecycle.Previews)) {
		t.Errorf("previews: go %v, python %v", rels(life.Previews), moves(rec.Lifecycle.Previews))
	}
	if life.Considered != rec.Lifecycle.Considered {
		t.Errorf("considered: go %d, python %d", life.Considered, rec.Lifecycle.Considered)
	}
	for i, m := range life.Demoted {
		if py := rec.Lifecycle.Demoted[i][1].(float64); m.Days != py {
			t.Errorf("%s silent %v days in go, %v in python", m.Rel, m.Days, py)
		}
	}

	// The copy collapse: families, order, summary, and the copies' new text
	// byte for byte.
	copies, err := PlanCopies(root, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(copies.Families) != len(rec.Copies) {
		t.Fatalf("families: go %d, python %d", len(copies.Families), len(rec.Copies))
	}
	for i, f := range copies.Families {
		py := rec.Copies[i]
		if f.Canonical != py.Canonical || !reflect.DeepEqual(f.Copies, py.Copies) || f.Summary != py.Summary {
			t.Errorf("family %d: go %+v, python %+v", i, f, py)
		}
	}
	for _, in := range copies.Intents {
		var want string
		for _, py := range rec.Copies {
			if s, ok := py.After[in.Rel]; ok {
				want = s
			}
		}
		if string(in.After) != want {
			t.Errorf("%s after-text:\n go %q\n py %q", in.Rel, in.After, want)
		}
	}

	// The promotion: the recurring targets, their sources, the slug, and the
	// digest body byte for byte.
	recurring, _, err := RecurringTargets(root, 0)
	if err != nil {
		t.Fatal(err)
	}
	var gotTargets, pyTargets []string
	for k := range recurring {
		gotTargets = append(gotTargets, k)
	}
	for k := range rec.Promote {
		pyTargets = append(pyTargets, k)
	}
	sort.Strings(gotTargets)
	sort.Strings(pyTargets)
	if !reflect.DeepEqual(gotTargets, pyTargets) {
		t.Fatalf("recurring targets: go %v, python %v", gotTargets, pyTargets)
	}
	for target, py := range rec.Promote {
		if !reflect.DeepEqual(recurring[target], py.Sources) {
			t.Errorf("%s sources: go %v, python %v", target, recurring[target], py.Sources)
		}
		rel, content := RenderConsolidated(target, recurring[target], parityPin)
		if !strings.HasSuffix(rel, "/"+py.Slug+".md") {
			t.Errorf("%s slug: go %s, python %s", target, rel, py.Slug)
		}
		// The body follows the frontmatter block and a blank line.
		_, body := ParseFrontmatter(content)
		body = strings.TrimPrefix(body, "\n")
		if body != py.Body {
			t.Errorf("%s body:\n go %q\n py %q", target, body, py.Body)
		}
	}
}
