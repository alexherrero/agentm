package note

import (
	"sort"
	"strings"
	"testing"
	"time"
)

// The lifecycle axis read as classes (filing v2 part 6, task 1). Every expected
// set below is hand-written; none is derived by calling the classifier a second
// way.
func TestLifecycleClasses(t *testing.T) {
	cases := []struct {
		name   string
		raw    string
		want   []string
		exempt bool
	}{
		{
			name: "active is the default and carries nothing",
			raw:  "---\ntitle: T\nstatus: active\nlifecycle: active\n---\nA plain body.\n",
			want: nil,
		},
		{
			name: "no axis at all carries nothing either",
			raw:  "---\ntitle: T\nstatus: active\n---\nA plain body.\n",
			want: nil,
		},
		{
			name: "dormant ranks below its active twin",
			raw:  "---\ntitle: T\nstatus: active\nlifecycle: dormant\n---\nA plain body.\n",
			want: []string{ClassDormant},
		},
		{
			name: "archived is demoted here and walled in search",
			raw:  "---\ntitle: T\nstatus: active\nlifecycle: archived\n---\nA plain body.\n",
			want: []string{ClassArchived},
		},
		{
			name: "superseded never competes with its successor",
			raw:  "---\ntitle: T\nstatus: active\nlifecycle: superseded\nsuperseded_by: memory/semantic/next.md\n---\nA plain body.\n",
			want: []string{ClassSuperseded},
		},
		{
			name:   "pinned never ages: the durable class, no penalty",
			raw:    "---\ntitle: T\nstatus: active\nlifecycle: pinned\n---\nA plain body.\n",
			want:   []string{ClassDurable},
			exempt: true,
		},
		{
			name: "lifecycle_tier is the older marker and does not spell lifecycle",
			raw:  "---\ntitle: T\nstatus: active\nlifecycle_tier: volatile\n---\nA plain body.\n",
			want: nil,
		},
		{
			name: "a quoted, upper-cased value still reads",
			raw:  "---\ntitle: T\nstatus: active\nlifecycle: \"Dormant\"\n---\nA plain body.\n",
			want: []string{ClassDormant},
		},
		{
			name: "a value the contract does not name earns nothing",
			raw:  "---\ntitle: T\nstatus: active\nlifecycle: expired\n---\nA plain body.\n",
			want: nil,
		},
		{
			name: "the axis compounds with the shape rule instead of replacing it",
			raw:  "---\ntype: workflow\nstatus: inbox\nlifecycle: dormant\n---\nUser stated: the hooks resolve the wrong python.\n",
			want: []string{"fragment", "status", ClassDormant},
		},
	}
	for _, c := range cases {
		n := Parse("Agent/memory/semantic/n.md", c.raw, time.Now())
		got := append([]string(nil), n.Flags...)
		want := append([]string(nil), c.want...)
		sort.Strings(got)
		sort.Strings(want)
		if strings.Join(got, ",") != strings.Join(want, ",") {
			t.Errorf("%s: flags = %v, want %v", c.name, got, want)
		}
		if IsDecayExempt(n.Flags) != c.exempt {
			t.Errorf("%s: decay-exempt = %v, want %v", c.name, IsDecayExempt(n.Flags), c.exempt)
		}
	}
}

func TestLifecycleIsParsedLikeStatus(t *testing.T) {
	if got := Parse("a.md", "---\nlifecycle: \"Archived\"\n---\n", time.Now()).Lifecycle; got != "archived" {
		t.Errorf("quoted, cased value: got %q, want archived", got)
	}
	if got := Parse("b.md", "---\nlifecycle_tier: durable\n---\n", time.Now()).Lifecycle; got != "" {
		t.Errorf("lifecycle_tier read as lifecycle: %q", got)
	}
	if got := Parse("c.md", "no frontmatter at all\n", time.Now()).Lifecycle; got != "" {
		t.Errorf("no frontmatter: got %q", got)
	}
}

// The clamp in the ranker assumes every weight demotes. A lifecycle weight at
// or above 1.0 would turn the archive wall's demoted-when-included case into a
// promotion on a negative-IDF row.
func TestLifecycleWeightsDemote(t *testing.T) {
	for _, c := range []string{ClassDormant, ClassArchived, ClassSuperseded} {
		w, ok := Weights[c]
		if !ok {
			t.Errorf("%s carries no weight — the axis would be classified but never demoted", c)
			continue
		}
		if w <= 0 || w >= 1 {
			t.Errorf("%s weight %v is not a demotion", c, w)
		}
	}
	if _, ok := Weights[ClassDurable]; ok {
		t.Errorf("durable must carry no weight: pinned is an exemption, not a penalty")
	}
}
