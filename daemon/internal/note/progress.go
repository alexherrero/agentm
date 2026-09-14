package note

import (
	"bytes"
	"path"
	"strings"
	"unicode/utf8"
)

// ProgressHeadBytes is how much of a progress log the index reads (agentm-vault
// § Projects and tasks: the Go arm reads a bounded head of a progress log, as the
// Python arm already does). The Python recall arm caps its read of an entry at a
// 20,000-token budget times four plus 8,192 bytes of slack (`read_cap_chars` in
// recall.py), and this is the same number, so the two arms read the same head.
//
// A progress log is append-only and grows for as long as its task runs. Read
// whole, a megabyte of log contains nearly every query's terms and ranks above
// the notes it mentions.
const ProgressHeadBytes = 20000*4 + 8192

// IsProgressLog reports whether `rel` names a progress log: `progress.md` (a
// singleton pair's, or a task's), or `progress-<slug>.md` beside a flat plan.
// Matched by name alone; a card that happens to carry such a name is far below
// the bound, so reading its head is reading all of it.
func IsProgressLog(rel string) bool {
	base := path.Base(rel)
	return base == "progress.md" ||
		(strings.HasPrefix(base, "progress-") && strings.HasSuffix(base, ".md"))
}

// ProgressHead returns a log's first ProgressHeadBytes, cut back to the last
// line break so no entry is split, or the whole of a log that fits.
func ProgressHead(raw []byte) []byte {
	if len(raw) <= ProgressHeadBytes {
		return raw
	}
	head := raw[:ProgressHeadBytes]
	if i := bytes.LastIndexByte(head, '\n'); i >= 0 {
		return head[:i+1]
	}
	// One enormous line: at least never cut a rune in half.
	for len(head) > 0 && !utf8.Valid(head) {
		head = head[:len(head)-1]
	}
	return head
}
