package capture

// An idea that already has a card adds to it (agentm-vault § Capture, amended
// 2026-09-28; the operator's ruling 6c).
//
// A captured idea is matched against the cards in `personal/ideas/` by slug,
// title and body. On a match it is appended to that card under a dated
// `## Added by capture` heading with its source, and no semantic note is
// written. A new idea is filed as before. The Python writers keep the same rule
// in `idea_cards.add_to_matching_card`; the stopwords, the word shape and the
// line are the same on both sides.

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

const (
	addedByCapture = "## Added by capture"
	ideasDirRel    = "personal/ideas"
	// ideaMatchLine is the score a card must reach: the largest of the slug test
	// (1 or 0), the title overlap and the body overlap, each a Jaccard over
	// meaningful words.
	ideaMatchLine = 0.5
)

var (
	ideaWordRe    = regexp.MustCompile(`[a-z0-9]+`)
	ideaStopwords = map[string]bool{}
)

func init() {
	for _, w := range strings.Fields("the a an and or of to in on for with is are was be it this that as at by " +
		"from we i you our my your can could should would will just not but so if then into about") {
		ideaStopwords[w] = true
	}
}

func ideaWords(text string) map[string]bool {
	out := map[string]bool{}
	for _, w := range ideaWordRe.FindAllString(strings.ToLower(text), -1) {
		if len(w) >= 3 && !ideaStopwords[w] {
			out[w] = true
		}
	}
	return out
}

func jaccard(a, b map[string]bool) float64 {
	if len(a) == 0 || len(b) == 0 {
		return 0
	}
	inter := 0
	for w := range a {
		if b[w] {
			inter++
		}
	}
	return float64(inter) / float64(len(a)+len(b)-inter)
}

// cardParts is a card's title and body, the body without what capture or the
// night added below it.
func cardParts(raw string) (title, body string) {
	raw = strings.ReplaceAll(raw, "\r\n", "\n")
	body = raw
	if strings.HasPrefix(raw, "---\n") {
		if end := strings.Index(raw[4:], "\n---\n"); end >= 0 {
			for _, line := range strings.Split(raw[4:4+end], "\n") {
				if v, ok := strings.CutPrefix(line, "title:"); ok {
					title = strings.Trim(strings.TrimSpace(v), `"'`)
				}
			}
			body = raw[4+end+5:]
		}
	}
	body = strings.TrimLeft(body, "\n")
	for _, heading := range []string{addedByCapture, "## Added by dreaming"} {
		if i := strings.Index(body, "\n"+heading); i >= 0 {
			body = body[:i]
		}
	}
	return title, body
}

// matchIdeaCard is the vault-relative path of the card a captured idea
// matches at or above the line, the best first; "" when none does.
func (c *Capturer) matchIdeaCard(slug, title, text string) string {
	dir := filepath.Join(c.cfg.VaultPath, filepath.FromSlash(ideasDirRel))
	entries, err := os.ReadDir(dir)
	if err != nil {
		return ""
	}
	names := make([]string, 0, len(entries))
	for _, e := range entries {
		if !e.IsDir() && strings.HasSuffix(e.Name(), ".md") && !strings.HasPrefix(e.Name(), ".") {
			names = append(names, e.Name())
		}
	}
	sort.Strings(names)
	best, bestScore := "", 0.0
	for _, name := range names {
		score := 0.0
		if slug != "" && strings.TrimSuffix(name, ".md") == slug {
			score = 1
		} else if raw, err := os.ReadFile(filepath.Join(dir, name)); err == nil {
			cardTitle, cardBody := cardParts(string(raw))
			score = jaccard(ideaWords(title), ideaWords(cardTitle))
			if b := jaccard(ideaWords(text), ideaWords(cardBody)); b > score {
				score = b
			}
		}
		if score > bestScore {
			best, bestScore = name, score
		}
	}
	if best == "" || bestScore < ideaMatchLine {
		return ""
	}
	return ideasDirRel + "/" + best
}

// appendToCard adds text to the card under `## Added by capture`, dated and
// sourced, keeping everything above the section and any section the night
// added after it. It reports false, writing nothing, when the card already
// holds those words.
func (c *Capturer) appendToCard(rel, text, source string, day time.Time) (bool, error) {
	abs := filepath.Join(c.cfg.VaultPath, filepath.FromSlash(rel))
	raw, err := os.ReadFile(abs)
	if err != nil {
		return false, err
	}
	current := string(raw)
	words := strings.Join(strings.Fields(text), " ")
	if words == "" || strings.Contains(strings.Join(strings.Fields(current), " "), words) {
		return false, nil
	}
	entry := "**" + day.Format("2006-01-02") + " · " + source + "**\n\n" + strings.TrimSpace(text) + "\n"
	var updated string
	at := strings.Index(current, "\n"+addedByCapture+"\n")
	if at < 0 {
		updated = strings.TrimRight(current, "\n") + "\n\n" + addedByCapture + "\n\n" + entry
	} else {
		end := len(current)
		if i := strings.Index(current[at+len(addedByCapture)+2:], "\n## "); i >= 0 {
			end = at + len(addedByCapture) + 2 + i
		}
		head, tail := strings.TrimRight(current[:end], "\n"), current[end:]
		updated = head + "\n\n" + entry
		if tail != "" {
			updated += "\n" + strings.TrimLeft(tail, "\n")
		}
	}
	return true, writeAtomic(abs, updated)
}
