package mcpsrv

import (
	"strings"
	"testing"

	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// captureProperties returns the memory_capture tool's advertised input
// properties, or fails if the tool is not there to advertise them.
func captureProperties(t *testing.T) map[string]any {
	t.Helper()
	specs := toolSpecs(nil)
	for _, spec := range specs {
		if spec["name"] != "memory_capture" {
			continue
		}
		schema, ok := spec["inputSchema"].(map[string]any)
		if !ok {
			t.Fatalf("memory_capture advertises no inputSchema")
		}
		props, ok := schema["properties"].(map[string]any)
		if !ok {
			t.Fatalf("memory_capture's inputSchema has no properties")
		}
		return props
	}
	t.Fatalf("memory_capture is not in the tool list")
	return nil
}

// The surface stays exactly two tools. Adding one is a decision, not a side
// effect of adding a field, and this is what makes the difference visible.
func TestTheSurfaceIsTwoTools(t *testing.T) {
	specs := toolSpecs(nil)
	if len(specs) != 2 {
		t.Fatalf("the daemon advertises %d tools, want 2", len(specs))
	}
	names := map[string]bool{}
	for _, spec := range specs {
		name, _ := spec["name"].(string)
		names[name] = true
	}
	for _, want := range []string{"memory_search", "memory_capture"} {
		if !names[want] {
			t.Errorf("%s is missing from the tool list", want)
		}
	}
}

// A caller can only pass what the tool says it takes. The provenance ruling
// of 2026-09-06 split one field into three, and a schema that still
// advertises the old single field leaves the other two undiscoverable —
// they would work if guessed, which is not the same as being offered.
func TestTheCaptureToolAdvertisesTheProvenanceSplit(t *testing.T) {
	props := captureProperties(t)
	for _, field := range []string{"source", "source_id", "source_url"} {
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

// `source:` sets the trust tier, so its description has to say it names the
// transport. It used to read "URL or message-id, for anything ingested from
// outside" — which is the field's old job, and describes what now belongs in
// `source_url:` and `source_id:`.
func TestTheTransportFieldDescribesATransport(t *testing.T) {
	props := captureProperties(t)
	spec, ok := props["source"].(map[string]any)
	if !ok {
		t.Fatalf("memory_capture does not advertise `source`")
	}
	description, _ := spec["description"].(string)
	loaded, err := rules.Load("")
	if err != nil {
		t.Fatalf("loading the packaged contract: %v", err)
	}
	for transport := range loaded.Sources {
		if !strings.Contains(description, transport) {
			t.Errorf("the `source` description does not name the transport %q: %q",
				transport, description)
		}
	}
	if strings.Contains(description, "message-id") || strings.Contains(description, "URL") {
		t.Errorf("the `source` description still describes the unit rather than "+
			"the transport: %q", description)
	}
}
