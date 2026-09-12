package enrich

import "testing"

// The card backfill (scripts/migrate/card_backfill.py) re-records a standing
// refusal under the key its rewritten card will have, and computes that key
// with a Python port of Key. This pins the key of one text that exercises every
// normalization Key makes — CRLF line endings, tabs, runs of spaces, blank
// lines, case, and a non-ASCII capital — to the same literal
// scripts/test_card_backfill.py pins for the port. When either side changes,
// one of the two tests fails, so a re-recorded refusal cannot silently stop
// matching the card it names.
func TestTheFingerprintKeyThePythonPortReproduces(t *testing.T) {
	fp := &Fingerprint{Version: "enrich/1+prompt/5d3a4cca1b02", RulesHash: "3c57fd89087c1a25"}
	text := "---\r\ntitle: Ünïcode Title\r\n\ttags: [a,  b]\n---\n\n  Body  WITH\ttabs  \n\n\nEnd.\n"
	const want = "7b214f29925d6dae9e94fb60eecde051619494d8c84b9d9ae1f5d672b8240dc9"
	if got := fp.Key(text); got != want {
		t.Fatalf("Key = %s, want %s: the Python port in card_backfill.py and Key have parted", got, want)
	}
}
