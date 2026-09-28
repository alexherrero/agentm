package linkrewrite

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"
)

// The fixtures are the census shapes the task mover meets: full-path
// wikilinks (1,237 of them in the vault on 2026-09-26), the one relative link
// in, the moved plans' own relative links out (61 plans), and the backticked
// and basename forms that stay as they are.

type fixtures struct {
	Moves  []Move    `json:"moves"`
	Exists []string  `json:"exists"`
	Cases  []fixture `json:"cases"`
}

type fixture struct {
	Name    string            `json:"name"`
	SrcOld  string            `json:"src_old"`
	SrcNew  string            `json:"src_new"`
	Text    string            `json:"text"`
	Want    *string           `json:"want"`
	Renamed map[string]string `json:"renamed"`
}

func load(t *testing.T) fixtures {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("testdata", "cases.json"))
	if err != nil {
		t.Fatal(err)
	}
	var f fixtures
	if err := json.Unmarshal(raw, &f); err != nil {
		t.Fatal(err)
	}
	return f
}

func (f fixtures) exists() func(string) bool {
	set := map[string]bool{}
	for _, e := range f.Exists {
		set[e] = true
	}
	for _, m := range f.Moves {
		set[m.To] = true
	}
	return func(rel string) bool { return set[rel] }
}

func rewrite(f fixtures, c fixture) (string, int) {
	return New(f.Moves, c.Renamed).Text(c.Text, c.SrcOld, c.SrcNew, f.exists())
}

func TestEachCensusShapeIsRewrittenAsWritten(t *testing.T) {
	f := load(t)
	for _, c := range f.Cases {
		if c.Want == nil {
			continue
		}
		t.Run(c.Name, func(t *testing.T) {
			got, n := rewrite(f, c)
			if got != *c.Want {
				t.Errorf("rewrote\n%q\nto\n%q\nwant\n%q", c.Text, got, *c.Want)
			}
			if (n == 0) != (got == c.Text) {
				t.Errorf("counted %d change(s) for a text that %s", n,
					map[bool]string{true: "did not change", false: "changed"}[got == c.Text])
			}
		})
	}
}

// The port answers what the Python it came from answers, on every fixture. The
// hand-run moves of task 176 went through the Python; the night's go through
// this, and the two must never disagree about a link.
func TestThePortAgreesWithThePythonOnEveryFixture(t *testing.T) {
	py, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("python3 is not on PATH")
	}
	_, here, _, _ := runtime.Caller(0)
	script := filepath.Join(filepath.Dir(here), "..", "..", "..", "scripts", "migrate", "agentkv_layout.py")
	cases, err := filepath.Abs(filepath.Join("testdata", "cases.json"))
	if err != nil {
		t.Fatal(err)
	}
	const driver = `
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("agentkv_layout", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
f = json.load(open(sys.argv[2], encoding="utf-8"))
moves = {m["from"]: m["to"] for m in f["moves"]}
present = set(f["exists"]) | set(moves.values())
out = []
for c in f["cases"]:
    text, n = mod.rewrite_text(c["text"], c["src_old"], c["src_new"], moves,
                               lambda rel: rel in present, c.get("renamed"))
    out.append({"text": text, "count": n})
# ASCII escapes, not raw UTF-8: Python writes stdout in the platform's code
# page (cp1252 on Windows), which turns a "·" into "\ufffd" on the way out.
json.dump(out, sys.stdout)
`
	raw, err := exec.Command(py, "-c", driver, script, cases).Output()
	if err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			t.Fatalf("the Python rewrite failed: %v\n%s", err, ee.Stderr)
		}
		t.Fatal(err)
	}
	var want []struct {
		Text  string `json:"text"`
		Count int    `json:"count"`
	}
	if err := json.Unmarshal(raw, &want); err != nil {
		t.Fatalf("the Python answered something that is not JSON: %v\n%s", err, raw)
	}
	f := load(t)
	if len(want) != len(f.Cases) {
		t.Fatalf("the Python answered %d case(s) for %d", len(want), len(f.Cases))
	}
	for i, c := range f.Cases {
		got, n := rewrite(f, c)
		if got != want[i].Text || n != want[i].Count {
			t.Errorf("%s:\n go     %q (%d)\n python %q (%d)", c.Name, got, n, want[i].Text, want[i].Count)
		}
	}
}

func TestATextWithNoLinkIntoAMovedFileIsReturnedUnchanged(t *testing.T) {
	r := New([]Move{{From: "projects/a/tasks/001-x/plan.md", To: "projects/a/completed/tasks/001-x/plan.md"}}, nil)
	in := "---\ntitle: x\n---\n\n[[projects/a/tasks/002-y/plan]] and [z](../z.md)\n"
	got, n := r.Text(in, "projects/a/charter.md", "projects/a/charter.md", func(string) bool { return true })
	if got != in || n != 0 {
		t.Errorf("changed %d link(s):\n%s", n, got)
	}
}
