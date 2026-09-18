package note

import "testing"

// A lesson outranks what taught it. Once the weekly crystallize phase writes a
// lesson, each source it consolidated is stamped `consolidated_into` and drops
// to x0.30 immediately — the same demotion `dormant` gives, and for the same
// reason: the card is still findable by a query that names the specific case,
// and it no longer stands in front of the lesson that generalised it.

func TestAStampedSourceRanksBehindTheLessonItTaught(t *testing.T) {
	head := "title: a\nstatus: active\nconsolidated_into: \"[[the-lesson]]\"\n"
	flags := classify("memory/semantic/a.md", head, "a body", "active", "", 0, false)

	if got := Multiplier(flags); got != 0.30 {
		t.Errorf("a consolidated source ranks %v, want 0.30 (flags %v)", got, flags)
	}
	// Demoted, never dropped: a query naming the case still reaches the card.
	if Multiplier(flags) <= 0 {
		t.Error("a consolidated source must stay rankable")
	}
}

func TestAnUnstampedSourceIsUntouched(t *testing.T) {
	head := "title: a\nstatus: active\n"
	flags := classify("memory/semantic/a.md", head, "a body", "active", "", 0, false)
	if got := Multiplier(flags); got != 1.0 {
		t.Errorf("an unstamped card ranks %v, want 1.0 (flags %v)", got, flags)
	}
}

// A writer mid-edit leaves the key with nothing after it. That is not a lesson,
// and dampening on it would sink a card because something started to write and
// stopped.
func TestAnEmptyStampIsNotAStamp(t *testing.T) {
	for _, head := range []string{
		"title: a\nconsolidated_into:\n",
		"title: a\nconsolidated_into: \n",
	} {
		flags := classify("memory/semantic/a.md", head, "a body", "active", "", 0, false)
		if got := Multiplier(flags); got != 1.0 {
			t.Errorf("an empty `consolidated_into` dampened the card to %v (head %q)",
				got, head)
		}
	}
}

// The lesson itself is never dampened by its own sources' field: it carries
// `consolidated_from`, which is the other direction and must not match.
func TestTheLessonsOwnFieldDoesNotDampenIt(t *testing.T) {
	head := "title: the lesson\nconsolidated_from:\n  - \"[[a]]\"\n  - \"[[b]]\"\n"
	flags := classify("memory/crystallized/the-lesson.md", head, "a lesson", "active", "", 0, false)
	if got := Multiplier(flags); got != 1.0 {
		t.Errorf("the lesson ranks %v, want 1.0 — `consolidated_from` names what "+
			"taught it, not a lesson above it (flags %v)", got, flags)
	}
}
