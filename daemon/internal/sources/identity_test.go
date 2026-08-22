package sources

import (
	"strings"
	"testing"
)

// The five namespaces the design names, each parsing as itself.
func TestEveryNamespaceParses(t *testing.T) {
	for _, tc := range []struct {
		raw  string
		want ID
	}{
		{"email:<CAF=abc123@example.com>",
			ID{Email, "<CAF=abc123@example.com>"}},
		{"claude-session:01JQ8", ID{ClaudeSession, "01JQ8"}},
		{"gemini-session:g-77", ID{GeminiSession, "g-77"}},
		{"antigravity-session:ag-9", ID{AntigravitySession, "ag-9"}},
		{"url:https://example.com/a", ID{URL, "https://example.com/a"}},
	} {
		got, err := ParseID(tc.raw)
		if err != nil {
			t.Errorf("ParseID(%q): %v", tc.raw, err)
			continue
		}
		if got != tc.want {
			t.Errorf("ParseID(%q) = %+v, want %+v", tc.raw, got, tc.want)
		}
		if got.String() != tc.raw {
			t.Errorf("round trip: %q -> %q", tc.raw, got.String())
		}
	}
}

// A bare URL is the shape 124 of the corpus's 138 sources actually have.
// Refusing them would make the registry blind to the population it covers.
func TestABareURLBecomesAURLIdentity(t *testing.T) {
	got, err := ParseID("https://example.com/article")
	if err != nil {
		t.Fatalf("ParseID: %v", err)
	}
	if got.Namespace != URL {
		t.Errorf("namespace %q, want %q", got.Namespace, URL)
	}
	if got.String() != "url:https://example.com/article" {
		t.Errorf("identity %q", got.String())
	}
}

// The closed set, and why it is closed. `source:` is free text, and a note whose
// source happens to contain a colon must not mint a namespace.
func TestAnUnknownNamespaceIsRefused(t *testing.T) {
	// Every one of these is a shape the live corpus actually contains.
	for _, raw := range []string{
		"idea-incubator:sherwood-automated-bug-fixing (research-pending)",
		"claude.ai conversation, exported manually",
		"notes: from a meeting",
		"",
		"   ",
	} {
		if got, err := ParseID(raw); err == nil {
			t.Errorf("ParseID(%q) minted %+v; the registry would fill with "+
				"identities that name nothing", raw, got)
		}
	}
}

// A namespace with nothing after it identifies nothing.
func TestANamespaceWithNoReferenceIsRefused(t *testing.T) {
	for _, raw := range []string{"email:", "url:", "claude-session:   "} {
		if _, err := ParseID(raw); err == nil {
			t.Errorf("ParseID(%q) was accepted", raw)
		}
	}
}

// The refusal says which namespaces would have worked. An error that only says
// "no" leaves the caller guessing at a closed vocabulary.
func TestTheRefusalNamesTheNamespacesThatWouldWork(t *testing.T) {
	_, err := ParseID("mastodon:12345")
	if err == nil {
		t.Fatal("an unknown namespace was accepted")
	}
	for _, want := range []string{"email", "claude-session", "url"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the refusal does not mention %q: %v", want, err)
		}
	}
}

// Quoting survives. Nine of the corpus's sources are quoted, because YAML
// quotes a value containing a colon and every URL contains one.
func TestAQuotedSourceParses(t *testing.T) {
	got, err := ParseID(`"https://example.com/a"`)
	if err != nil {
		t.Fatalf("ParseID: %v", err)
	}
	if got.Ref != "https://example.com/a" {
		t.Errorf("Ref = %q, quotes were not stripped", got.Ref)
	}
}

// An identity written in the `url:` namespace is canonicalized too.
//
// The idempotency test reaches canonicalization through the bare-URL branch,
// which does its own — so the `url:` branch could stop canonicalizing entirely
// and nothing would notice. These are the same page and must be one identity.
func TestAURLNamespaceIdentityIsCanonicalizedToo(t *testing.T) {
	messy, err := ParseID("url:HTTPS://Example.COM:443/a?utm_source=x&b=2&a=1#frag")
	if err != nil {
		t.Fatalf("ParseID: %v", err)
	}
	clean, err := ParseID("https://example.com/a?a=1&b=2")
	if err != nil {
		t.Fatalf("ParseID: %v", err)
	}
	if messy != clean {
		t.Errorf("%s and %s are different identities; the same page would be "+
			"fetched and distilled twice", messy, clean)
	}
}

// --- canonicalization -------------------------------------------------------

// What must not change which document is meant.
func TestCanonicalURLNormalizesWhatCannotChangeTheDocument(t *testing.T) {
	for _, tc := range []struct{ raw, want string }{
		{"HTTPS://Example.COM/a", "https://example.com/a"},
		{"https://example.com:443/a", "https://example.com/a"},
		{"http://example.com:80/a", "http://example.com/a"},
		{"https://example.com/a#section-3", "https://example.com/a"},
		{"https://example.com/a?utm_source=x&id=7", "https://example.com/a?id=7"},
		{"https://example.com/a?fbclid=abc", "https://example.com/a"},
		{"https://example.com/a?b=2&a=1", "https://example.com/a?a=1&b=2"},
	} {
		got, err := CanonicalURL(tc.raw)
		if err != nil {
			t.Errorf("CanonicalURL(%q): %v", tc.raw, err)
			continue
		}
		if got != tc.want {
			t.Errorf("CanonicalURL(%q) = %q, want %q", tc.raw, got, tc.want)
		}
	}
}

// Two links to one article that differ only in how somebody arrived at it are
// one source. Treating them as two means fetching and distilling twice.
func TestTrackingParametersDoNotMakeASecondSource(t *testing.T) {
	plain, err := ParseID("https://example.com/article")
	if err != nil {
		t.Fatal(err)
	}
	tracked, err := ParseID(
		"https://example.com/article?utm_source=newsletter&utm_campaign=july")
	if err != nil {
		t.Fatal(err)
	}
	if plain != tracked {
		t.Errorf("%s and %s are different identities", plain, tracked)
	}
}

// And what must be left alone. The two directions of error are not symmetric:
// canonicalizing too little costs a second fetch, and too much merges two
// documents so the second is never mined at all.
func TestCanonicalURLLeavesMeaningAlone(t *testing.T) {
	for _, tc := range []struct{ a, b string }{
		// A trailing slash is usually the same page and sometimes is not.
		{"https://example.com/a", "https://example.com/a/"},
		// Case is meaningful past the host.
		{"https://example.com/A", "https://example.com/a"},
		// A non-tracking query decides which document is returned.
		{"https://example.com/a?id=7", "https://example.com/a?id=8"},
		{"https://example.com/a?id=7", "https://example.com/a"},
		// Different hosts are different documents even at the same path.
		{"https://example.com/a", "https://example.org/a"},
		// So are different schemes: one may redirect, and one may not exist.
		{"http://example.com/a", "https://example.com/a"},
	} {
		x, err := CanonicalURL(tc.a)
		if err != nil {
			t.Fatalf("CanonicalURL(%q): %v", tc.a, err)
		}
		y, err := CanonicalURL(tc.b)
		if err != nil {
			t.Fatalf("CanonicalURL(%q): %v", tc.b, err)
		}
		if x == y {
			t.Errorf("%q and %q collapsed to one identity (%q); the second "+
				"document would never be mined", tc.a, tc.b, x)
		}
	}
}

// Canonicalization is idempotent. A source read back out of a note and parsed
// again has to produce the identity it was stored under, or every re-ingest
// looks like a new source.
func TestCanonicalizationIsIdempotent(t *testing.T) {
	for _, raw := range []string{
		"HTTPS://Example.COM:443/a?b=2&a=1&utm_source=x#frag",
		"https://example.com/a",
		"http://example.com/path/with%20space",
	} {
		once, err := ParseID(raw)
		if err != nil {
			t.Fatalf("ParseID(%q): %v", raw, err)
		}
		twice, err := ParseID(once.String())
		if err != nil {
			t.Fatalf("re-parsing %q: %v", once.String(), err)
		}
		if once != twice {
			t.Errorf("%q parsed to %q and then to %q", raw, once, twice)
		}
	}
}

// A relative URL identifies nothing on its own.
func TestARelativeURLIsRefused(t *testing.T) {
	for _, raw := range []string{"url:/just/a/path", "url:example.com/a"} {
		if _, err := ParseID(raw); err == nil {
			t.Errorf("ParseID(%q) was accepted", raw)
		}
	}
}
