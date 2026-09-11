package enrich

import "strings"

// Small text helpers the gates share.
//
// They lived in tokens.go with the token-preservation gate, which retired with
// the additive deep pass (agentm-vault plan 04): that gate held a rewrite to
// keep every identifier the source had, and a card's text is no longer
// rewritten — it is carried byte for byte, and Compose refuses a write that
// would change it.

// sourceBody strips frontmatter, so a gate compares prose to prose. A
// frontmatter key is not a claim the card makes.
func sourceBody(raw string) string {
	if !strings.HasPrefix(raw, "---") {
		return raw
	}
	if i := strings.Index(raw[3:], "\n---"); i >= 0 {
		rest := raw[3+i+4:]
		if j := strings.IndexByte(rest, '\n'); j >= 0 {
			return rest[j+1:]
		}
		return ""
	}
	return raw
}

func quoteAll(in []string) []string {
	out := make([]string, len(in))
	for i, s := range in {
		out[i] = "`" + s + "`"
	}
	return out
}
