package enrich

import (
	"path"
	"strings"
)

// IdeasDir is the one folder in the operator's `personal/` space the night
// reaches (agentm-vault part 13): the idea cards, flat, at the vault root's
// `personal/ideas/<slug>.md`.
//
// Everything else under `personal/` is the operator's in a stricter sense. No
// queue walks it, and nothing here makes it eligible. The carve-out is this
// constant and the two readers of it — the queue that offers an idea card as a
// card, and the stamp that keeps the operator's filing on it — rather than a
// line in the filing contract, because the contract's block is hashed and an
// edit there would re-owe the whole corpus a pass for a rule about one folder.
const IdeasDir = "personal/ideas/"

// IsIdeaCard reports whether a vault-relative path is an idea card: a markdown
// file directly inside IdeasDir, not a dotfile, not in a subfolder.
//
// Direct children only, because the folder is flat by design and the generated
// `Ideas.md` lists exactly these. A subfolder the operator makes for their own
// reasons stays theirs — neither offered to the night nor listed — rather than
// becoming eligible because a prefix happened to match.
func IsIdeaCard(rel string) bool {
	rel = strings.ReplaceAll(rel, "\\", "/")
	if !strings.HasPrefix(rel, IdeasDir) || !strings.HasSuffix(rel, ".md") {
		return false
	}
	name := strings.TrimPrefix(rel, IdeasDir)
	return name != "" && !strings.Contains(name, "/") && !strings.HasPrefix(path.Base(name), ".")
}
