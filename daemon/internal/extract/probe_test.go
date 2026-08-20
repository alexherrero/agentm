package extract

import "testing"

// TestShowRealOutput prints what the extractor actually produces on realistic
// input. Not an assertion — a way to read the output rather than infer it from
// tests that all passed on the first run, which is exactly when a regex is most
// likely to be matching nothing and every `hasNot` is trivially satisfied.
//
// Run with: go test ./internal/extract/ -run ShowRealOutput -v
func TestShowRealOutput(t *testing.T) {
	cases := []struct{ name, title, body string }{
		{
			"design prose",
			"The filing contract",
			"`standards/storage-rules.md` decides where a memory goes. The daemon " +
				"reads it at runtime. The Open Knowledge Format (OKF) requires a type " +
				"field, and the index carries idx_timestamp_desc on the captured column. " +
				"A well-known failure is that noteType and StorageRules drift apart.",
		},
		{
			"a capture with no structure",
			"A preference",
			"Use Edit rather than Write for an existing file, because output is " +
				"billed at roughly five times input.",
		},
		{
			"an identifier-dense note",
			"Schema",
			"Tables: docmeta, embeddings. Columns: doc_id, chunk_idx, mtime_ns, " +
				"captured_src. Run `check-storage-rules` and `check-vault-frontmatter`.",
		},
	}
	for _, c := range cases {
		got := Aliases(c.title, c.body)
		t.Logf("%s -> %d aliases: %v", c.name, len(got), got)
	}
}

// A guard against the failure mode the printout above is watching for: a
// negative assertion is only meaningful when the positive case in the same
// scenario actually fires. If the extractor matched nothing at all, every
// `hasNot` in this package would pass and say nothing.
func TestTheExtractorIsNotSilentlyMatchingNothing(t *testing.T) {
	got := Aliases("", "The Open Knowledge Format (OKF) uses idx_timestamp_desc and noteType.")
	if len(got) < 6 {
		t.Fatalf("only %d aliases from input carrying an acronym, a snake_case "+
			"identifier and a camelCase one — the negative assertions elsewhere in "+
			"this package would be vacuous: %v", len(got), got)
	}
}
