package mcpsrv

import (
	"strings"
	"testing"
)

// The card's readable fields have to be offered, not merely accepted. A field
// that works when guessed is not a field any model will pass: the schema is
// the whole of what a caller can see.
func TestTheCaptureToolAdvertisesTheCardsReadableFields(t *testing.T) {
	props := captureProperties(t)
	for _, field := range []string{"summary", "why", "importance", "related", "project", "task"} {
		spec, ok := props[field].(map[string]any)
		if !ok {
			t.Errorf("memory_capture does not advertise %q", field)
			continue
		}
		if d, _ := spec["description"].(string); d == "" {
			t.Errorf("%q is advertised with no description", field)
		}
	}
}

// `why` is the field the whole capture contract turns on, so its description
// has to say what a `why` is — not just that the tool takes one.
func TestTheWhyFieldSaysWhatAWhyIs(t *testing.T) {
	props := captureProperties(t)
	spec, ok := props["why"].(map[string]any)
	if !ok {
		t.Fatalf("memory_capture does not advertise `why`")
	}
	description, _ := spec["description"].(string)
	for _, want := range []string{"what was happening", "decides"} {
		if !strings.Contains(strings.ToLower(description), want) {
			t.Errorf("the `why` description does not say %q: %q", want, description)
		}
	}
}

// `status` is derived from the type and the `why`. Advertising it would offer
// a model the one claim it must not be able to make about its own capture —
// that something judged this — and the Python lane's 74 mis-stamped notes are
// what that costs.
func TestTheCaptureToolDoesNotAdvertiseStatus(t *testing.T) {
	props := captureProperties(t)
	if _, ok := props["status"]; ok {
		t.Error("memory_capture advertises `status`, which is derived, not set")
	}
}

// `instructions` is the security-boundary field: the ingest sweep executes a
// matching value under a fixed grammar. The Go door accepts it from an
// operator-typed surface and never offers it to a model.
func TestTheCaptureToolDoesNotAdvertiseInstructions(t *testing.T) {
	props := captureProperties(t)
	if _, ok := props["instructions"]; ok {
		t.Error("memory_capture advertises `instructions`; a model that can see it will fill it")
	}
}
