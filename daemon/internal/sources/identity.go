// Package sources watermarks the material the world hands us, so it is mined
// once and not once per cycle.
//
// It is the coverage ledger's outward-facing sibling. The ledger tracks work
// over notes the vault owns; this tracks emails, chats, session transcripts and
// fetched pages — things whose content we did not write and cannot re-derive,
// and whose expensive read should happen exactly once per version.
//
// # Two shapes of source, two shapes of watermark
//
// An immutable unit — a sent email, a finished transcript, a fetched article —
// is recorded by identity plus content hash. An identity already in the registry
// at the current hash is skipped without a model call, which is the whole point.
//
// A growing unit — a live session log that appends over time — has no final
// content to hash, so it carries a cursor instead: the last offset or message
// consumed. Each sweep picks up exactly the new tail.
//
// Hashing a growing source would be the obvious mistake and a quiet one. The
// hash would change on every append, the unit would look entirely new every
// sweep, and a session appended to fifty times would be re-mined fifty times at
// full price — the exact cost this package exists to prevent, arrived at by
// treating a live log as if it were finished.
package sources

import (
	"fmt"
	"net/url"
	"sort"
	"strings"
)

// Namespace is the closed set of source kinds this registry knows.
//
// Closed on purpose. `source:` is a free-text frontmatter field and the corpus
// has 138 notes carrying one, in shapes ranging from a bare URL to the sentence
// "claude.ai conversation, exported manually". Parsing `<anything>:<rest>` as an
// identity would mint a namespace from any note whose source note happened to
// contain a colon, and the registry would fill with identities that name nothing.
//
// Adding a namespace is a line here and a line in the test. That is the right
// amount of friction for a vocabulary that decides what gets mined once.
type Namespace string

const (
	// Email is a message, identified by its Message-ID. Immutable: a sent
	// message does not change.
	Email Namespace = "email"
	// ClaudeSession, GeminiSession and AntigravitySession are agent session
	// transcripts. Growing while the session is live, immutable once it ends —
	// which is why Kind is a property of the registered unit rather than of its
	// namespace.
	ClaudeSession      Namespace = "claude-session"
	GeminiSession      Namespace = "gemini-session"
	AntigravitySession Namespace = "antigravity-session"
	// URL is a fetched page, identified by its canonical form.
	URL Namespace = "url"
)

// Namespaces is every namespace the registry accepts, in a stable order.
var Namespaces = []Namespace{Email, ClaudeSession, GeminiSession, AntigravitySession, URL}

func known(ns Namespace) bool {
	for _, n := range Namespaces {
		if n == ns {
			return true
		}
	}
	return false
}

// ID is a namespaced, stable identity for one unit of source material.
type ID struct {
	Namespace Namespace
	// Ref is the namespace-specific reference — a message-id, a session id, a
	// canonical URL.
	Ref string
}

// String is the wire form, and the form that goes in a note's `source` field.
func (id ID) String() string { return string(id.Namespace) + ":" + id.Ref }

// ParseID reads an identity out of whatever a `source:` field actually holds.
//
// Three shapes, and the second is why this is not a one-line split.
//
// A namespaced identity parses as itself. A bare URL is normalized into the
// `url:` namespace, because that is what 124 of the corpus's 138 sources look
// like and refusing them would make the registry blind to the population it is
// supposed to cover. Anything else is refused rather than guessed at: a note
// whose source says "claude.ai conversation, exported manually" is telling a
// human something, and turning that sentence into an identity would put a
// watermark on a source nothing can ever match again.
func ParseID(raw string) (ID, error) {
	s := strings.TrimSpace(strings.Trim(strings.TrimSpace(raw), `'"`))
	if s == "" {
		return ID{}, fmt.Errorf("sources: an empty source is not an identity")
	}

	// A bare URL first, because `https://example.com` splits on a colon into a
	// namespace named "https" and would otherwise be refused as an unknown one.
	//
	// Case-insensitively: a scheme is case-insensitive by specification, and a
	// literal prefix check let `HTTPS://…` fall through to the namespace split
	// and be rejected as a namespace called "HTTPS".
	if hasSchemePrefix(s, "http://") || hasSchemePrefix(s, "https://") {
		canon, err := CanonicalURL(s)
		if err != nil {
			return ID{}, err
		}
		return ID{Namespace: URL, Ref: canon}, nil
	}

	ns, ref, ok := strings.Cut(s, ":")
	if !ok {
		return ID{}, fmt.Errorf("sources: %q names no namespace; identities are "+
			"<namespace>:<ref> and the namespaces are %s", s, namespaceList())
	}
	space := Namespace(strings.ToLower(strings.TrimSpace(ns)))
	if !known(space) {
		return ID{}, fmt.Errorf("sources: %q is not a source namespace; the "+
			"registry knows %s, and minting one from any colon would fill it with "+
			"identities that name nothing", ns, namespaceList())
	}
	ref = strings.TrimSpace(ref)
	if ref == "" {
		return ID{}, fmt.Errorf("sources: %q has a namespace and no reference", s)
	}
	if space == URL {
		canon, err := CanonicalURL(ref)
		if err != nil {
			return ID{}, err
		}
		ref = canon
	}
	return ID{Namespace: space, Ref: ref}, nil
}

// hasSchemePrefix is a case-insensitive prefix test, for the one place where
// the thing being matched is case-insensitive by specification.
func hasSchemePrefix(s, prefix string) bool {
	return len(s) >= len(prefix) && strings.EqualFold(s[:len(prefix)], prefix)
}

func namespaceList() string {
	out := make([]string, len(Namespaces))
	for i, n := range Namespaces {
		out[i] = string(n)
	}
	return strings.Join(out, ", ")
}

// trackingParams are query parameters that identify a referrer rather than a
// document. Two links to the same article that differ only in how somebody
// arrived at it are one source, and treating them as two means fetching and
// distilling the same page twice.
var trackingParams = map[string]bool{
	"utm_source": true, "utm_medium": true, "utm_campaign": true,
	"utm_term": true, "utm_content": true, "utm_id": true,
	"fbclid": true, "gclid": true, "mc_cid": true, "mc_eid": true,
}

// CanonicalURL reduces a URL to the form that identifies the document.
//
// Deliberately conservative, because the two failure directions are not
// symmetric. Canonicalizing too little costs a second fetch of a page already
// mined — annoying and visible. Canonicalizing too much merges two different
// documents into one identity, and the second one is then never mined at all,
// silently, forever.
//
// So this normalizes only what cannot change which document is meant:
//
//   - scheme and host are lower-cased, because they are case-insensitive by
//     specification
//   - a default port is dropped, for the same reason
//   - the fragment goes, because it addresses a position inside a document
//     rather than a document
//   - tracking parameters go, because they identify a referrer
//   - the remaining query is sorted, because parameter order is not meaningful
//     and two orderings of the same query are one page
//
// A trailing slash is deliberately left alone. `/a` and `/a/` are usually the
// same page and sometimes are not, and "usually" is not good enough for the
// direction of error that loses a document.
func CanonicalURL(raw string) (string, error) {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil {
		return "", fmt.Errorf("sources: %q will not parse as a URL: %w", raw, err)
	}
	if u.Scheme == "" || u.Host == "" {
		return "", fmt.Errorf("sources: %q is not an absolute URL, so it does not "+
			"identify anything on its own", raw)
	}
	// Only the host. net/url already lower-cases the scheme on parse and
	// deliberately leaves the host alone, so a second pass over the scheme is a
	// line no input can reach — checked, not assumed.
	u.Host = strings.ToLower(u.Host)
	if (u.Scheme == "http" && strings.HasSuffix(u.Host, ":80")) ||
		(u.Scheme == "https" && strings.HasSuffix(u.Host, ":443")) {
		u.Host = u.Host[:strings.LastIndex(u.Host, ":")]
	}
	u.Fragment = ""
	u.RawFragment = ""

	if q := u.Query(); len(q) > 0 {
		keys := make([]string, 0, len(q))
		for k := range q {
			if !trackingParams[strings.ToLower(k)] {
				keys = append(keys, k)
			}
		}
		sort.Strings(keys)
		var b strings.Builder
		for _, k := range keys {
			vs := q[k]
			sort.Strings(vs)
			for _, v := range vs {
				if b.Len() > 0 {
					b.WriteByte('&')
				}
				b.WriteString(url.QueryEscape(k))
				b.WriteByte('=')
				b.WriteString(url.QueryEscape(v))
			}
		}
		u.RawQuery = b.String()
	}
	return u.String(), nil
}
