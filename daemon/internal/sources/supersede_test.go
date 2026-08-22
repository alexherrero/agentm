package sources

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

// vault is an in-memory stand-in for the notes a source produced.
type vault struct {
	files  map[string]string
	writes int
}

func newVault(files map[string]string) *vault {
	return &vault{files: files}
}

func (v *vault) find(bySource map[string][]string) Finder {
	return func(_ context.Context, source string) ([]string, error) {
		return bySource[source], nil
	}
}

func (v *vault) rewrite(_ context.Context, rel string, f func(string) string) error {
	body, ok := v.files[rel]
	if !ok {
		return errors.New("no such note: " + rel)
	}
	v.files[rel] = f(body)
	v.writes++
	return nil
}

func stampedAt() time.Time {
	return time.Date(2026, 8, 21, 12, 0, 0, 0, time.UTC)
}

// Re-ingest supersedes source-scoped rather than duplicating.
//
// The failure this prevents is quiet: two plausible distillations of one email
// sitting side by side in the corpus, with nothing saying which is current.
func TestSupersedeMarksEveryMemoryTheSourceProduced(t *testing.T) {
	id := mustID(t, "email:<abc@example.com>")
	v := newVault(map[string]string{
		"memory/semantic/a.md": "---\ntitle: A\nstatus: active\n---\n\nthe body of A\n",
		"memory/semantic/b.md": "---\ntitle: B\nstatus: active\n---\n\nthe body of B\n",
		"memory/semantic/c.md": "---\ntitle: C\nstatus: active\n---\n\nfrom somewhere else\n",
	})
	find := v.find(map[string][]string{
		id.String(): {"memory/semantic/a.md", "memory/semantic/b.md"},
	})

	rep, err := Supersede(context.Background(), id, "v2", stampedAt(), find, v.rewrite)
	if err != nil {
		t.Fatalf("Supersede: %v", err)
	}
	if len(rep.Superseded) != 2 {
		t.Fatalf("superseded %v, want the two memories this source produced",
			rep.Superseded)
	}

	for _, rel := range rep.Superseded {
		body := v.files[rel]
		if !strings.Contains(body, "status: superseded") {
			t.Errorf("%s is not marked superseded:\n%s", rel, body)
		}
		if !strings.Contains(body, id.String()) {
			t.Errorf("%s does not say what replaced it:\n%s", rel, body)
		}
		if !strings.Contains(body, "superseded_at") {
			t.Errorf("%s does not say when:\n%s", rel, body)
		}
		if strings.Contains(body, "status: active") {
			t.Errorf("%s still carries its old status:\n%s", rel, body)
		}
	}

	// And nothing else was touched. A supersession that reached a memory this
	// re-ingest never read is one the corpus has silently lost.
	if v.files["memory/semantic/c.md"] !=
		"---\ntitle: C\nstatus: active\n---\n\nfrom somewhere else\n" {
		t.Errorf("a memory from another source was superseded:\n%s",
			v.files["memory/semantic/c.md"])
	}
}

// The body survives exactly. What the system believed is the point of keeping a
// superseded memory, and a supersession that edited the text would leave nothing
// to compare the new distillation against.
func TestSupersedeLeavesTheBodyAlone(t *testing.T) {
	id := mustID(t, "email:<abc@example.com>")
	body := "the exact words, with **markup** and a [[link]]\nand a second line\n"
	v := newVault(map[string]string{
		"a.md": "---\ntitle: A\nstatus: active\n---\n\n" + body,
	})
	find := v.find(map[string][]string{id.String(): {"a.md"}})

	if _, err := Supersede(context.Background(), id, "v2", stampedAt(), find, v.rewrite); err != nil {
		t.Fatal(err)
	}
	if !strings.HasSuffix(v.files["a.md"], "\n\n"+body) {
		t.Errorf("the body changed:\n%s", v.files["a.md"])
	}
}

// A second supersession replaces the first rather than stacking duplicate keys
// that no YAML reader agrees about.
func TestSupersedingTwiceDoesNotStackKeys(t *testing.T) {
	first := mustID(t, "email:<abc@example.com>")
	v := newVault(map[string]string{
		"a.md": "---\ntitle: A\nstatus: active\n---\n\nbody\n",
	})
	find := v.find(map[string][]string{first.String(): {"a.md"}})

	for i := 0; i < 3; i++ {
		if _, err := Supersede(context.Background(), first, "v2", stampedAt(),
			find, v.rewrite); err != nil {
			t.Fatal(err)
		}
	}
	body := v.files["a.md"]
	for _, key := range []string{"status:", "superseded_by:", "superseded_at:"} {
		if n := strings.Count(body, key); n != 1 {
			t.Errorf("%q appears %d times after three supersessions:\n%s", key, n, body)
		}
	}
}

// A memory with no frontmatter is still a memory this source produced. Skipping
// it would leave a live duplicate, which is the thing being prevented.
func TestSupersedeGivesAnUnstructuredNoteFrontmatter(t *testing.T) {
	id := mustID(t, "email:<abc@example.com>")
	v := newVault(map[string]string{"a.md": "just prose, no frontmatter\n"})
	find := v.find(map[string][]string{id.String(): {"a.md"}})

	if _, err := Supersede(context.Background(), id, "v2", stampedAt(), find, v.rewrite); err != nil {
		t.Fatal(err)
	}
	body := v.files["a.md"]
	if !strings.HasPrefix(body, "---\n") {
		t.Errorf("no frontmatter was added:\n%s", body)
	}
	if !strings.Contains(body, "status: superseded") {
		t.Errorf("the note is not marked:\n%s", body)
	}
	if !strings.Contains(body, "just prose, no frontmatter") {
		t.Errorf("the prose was lost:\n%s", body)
	}
}

// The source id contains a colon by construction, which is a mapping in YAML.
// A value that stopped being a string would make the note unparseable.
func TestTheSupersessionStampIsValidYAMLScalar(t *testing.T) {
	id := mustID(t, "url:https://example.com/a?b=1")
	v := newVault(map[string]string{"a.md": "---\ntitle: A\n---\n\nbody\n"})
	find := v.find(map[string][]string{id.String(): {"a.md"}})

	if _, err := Supersede(context.Background(), id, "v2", stampedAt(), find, v.rewrite); err != nil {
		t.Fatal(err)
	}
	for _, line := range strings.Split(v.files["a.md"], "\n") {
		if !strings.HasPrefix(line, "superseded_by:") {
			continue
		}
		value := strings.TrimSpace(strings.TrimPrefix(line, "superseded_by:"))
		if !strings.HasPrefix(value, `"`) || !strings.HasSuffix(value, `"`) {
			t.Errorf("a value containing a colon was left unquoted: %s", line)
		}
	}
}

// A source being mined for the first time supersedes nothing, and that is an
// ordinary outcome rather than an error.
func TestSupersedingASourceWithNoMemoriesIsNotAnError(t *testing.T) {
	id := mustID(t, "email:<new@example.com>")
	v := newVault(map[string]string{})
	find := v.find(map[string][]string{})

	rep, err := Supersede(context.Background(), id, "v2", stampedAt(), find, v.rewrite)
	if err != nil {
		t.Fatalf("Supersede: %v", err)
	}
	if len(rep.Superseded) != 0 {
		t.Errorf("superseded %v from an empty corpus", rep.Superseded)
	}
	if v.writes != 0 {
		t.Errorf("%d writes for a source with no memories", v.writes)
	}
}

// A re-ingest with memories to supersede and no writer must refuse rather than
// proceed. Proceeding would duplicate every one of them.
func TestSupersedeRefusesToProceedWithNoWriter(t *testing.T) {
	id := mustID(t, "email:<abc@example.com>")
	v := newVault(map[string]string{"a.md": "---\n---\n\nbody\n"})
	find := v.find(map[string][]string{id.String(): {"a.md"}})

	if _, err := Supersede(context.Background(), id, "v2", stampedAt(), find, nil); err == nil {
		t.Error("a re-ingest with memories to supersede and no writer proceeded")
	}
	// With no memories, no writer is needed and nothing should be refused.
	empty := v.find(map[string][]string{})
	if _, err := Supersede(context.Background(), id, "v2", stampedAt(), empty, nil); err != nil {
		t.Errorf("a first ingest was refused for want of a writer it does not need: %v", err)
	}
}

// A finder that fails stops the re-ingest rather than reporting that nothing
// needed superseding. The two look identical from the report and are opposite.
func TestSupersedeReportsAFailedLookup(t *testing.T) {
	id := mustID(t, "email:<abc@example.com>")
	boom := errors.New("the index went away")
	_, err := Supersede(context.Background(), id, "v2", stampedAt(),
		func(context.Context, string) ([]string, error) { return nil, boom },
		func(context.Context, string, func(string) string) error { return nil })
	if !errors.Is(err, boom) {
		t.Errorf("Supersede error = %v, want the lookup's own", err)
	}
}

func TestSupersedeNeedsAFinder(t *testing.T) {
	if _, err := Supersede(context.Background(), mustID(t, "email:<a@example.com>"),
		"v2", stampedAt(), nil, nil); err == nil {
		t.Error("a re-ingest with no way to find what it produced proceeded")
	}
}
