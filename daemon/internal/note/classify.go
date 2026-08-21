package note

import (
	"strings"
)

// The rank-penalty classifier.
//
// 3,413 of this vault's 8,700 notes are miner fragments: a truncated sentence
// clipped out of a session transcript, wrapped in frontmatter, and written as its
// own file. They carry the operator's vocabulary because they are quotations of
// it, which is exactly why they rank — they compete on every query and win on
// some. Demoting them is worth +3.75 points of R@5 at p = 0.0195, measured over
// six replicates per arm against a frozen corpus snapshot.
//
// Demote, never exclude. A note that cannot be returned at all cannot be
// returned when it is the only answer, and a filter that silently removed a
// class of results is the mechanism that left recall dead for four months.
// Everything here produces a multiplier; nothing here drops a row.
const (
	ClassFragment = "fragment"
	// ClassFragmentPromoted is a fragment-shaped note that filing already
	// promoted. It is classified so the demotion decision stays visible, and
	// carries no weight so the shape rule does not fire on it — see Weights.
	ClassFragmentPromoted = "fragment-promoted"
	ClassStatus           = "status"
	ClassStaging          = "staging"
	// ClassSpace is a note in a space the operator has asked to be dampened on
	// ordinary questions — today that is `Personal/`.
	//
	// This replaces a directory boundary, and the replacement is the point. Recall
	// used to restrict itself to the memory root, which cured a real leak by
	// amputation: 13% of top-5 results across 20 prompts fell outside `Agent/`,
	// and "what should I work on next" returned two Church notes. But an
	// invisible space is how this vault lost 9,786 notes once already, and a note
	// that cannot be returned at all cannot be returned when it is the only
	// answer.
	//
	// Dampening cures the same leak without the amputation. A strong distinctive
	// match still clears the multiplier; a weak cosine neighbour does not.
	ClassSpace = "space"
	// ClassDurable marks a note that never ages. Like ClassFragmentPromoted it
	// carries no weight — it is not a penalty, it is the record of a decision,
	// and decay reads it where the weights do not.
	//
	// Computed here rather than at search time for the same reason every other
	// class is: the four routes into it read frontmatter and the path, and
	// re-deriving them per row per query would put a substring scan on the hot
	// path to answer a question the index already knows. The cost is the same one
	// spaces pay — changing what is durable does nothing until the affected notes
	// are reindexed.
	ClassDurable = "durable"
	// ClassArtifact is a note that records a moment rather than stating
	// something durable — session exhaust, a distilled meeting, a one-off
	// observation. It is dampened on a general question and undampened when the
	// question asks for that shape.
	//
	// Set only when the note says `altitude: artifact` in so many words. The
	// design makes `artifact` the *default* so that `canonical` has to be earned,
	// and that default belongs in enrichment, which assigns the field — not here,
	// as a fallback for its absence. The difference matters right now: no note in
	// this corpus carries `altitude` yet, and a ranker that treated every one of
	// them as an artifact would apply a multiplier to every row. That is not the
	// no-op it looks like, because the negative-IDF clamp below only fires on rows
	// whose score went negative, so a uniform multiplier reorders exactly the
	// rows it should leave alone.
	ClassArtifact = "artifact"
)

// Weights are applied multiplicatively to the BM25 score.
//
// These values are not taste, and they are deliberately not configurable. A
// 125-point sweep over [0.02, 1.0] per class produced exactly four distinct
// outcomes, and every setting at or below 0.6 ranked identically — weight 0.6 and
// weight 0.02 are the same ranking. Strength is not a parameter; only whether a
// class is penalized at all. A config surface here would be a surface with
// nothing behind it.
//
// ClassFragmentPromoted is absent on purpose, and its absence is the status gate:
// an unlisted class multiplies by 1.0, so a fragment-shaped note that filing
// already promoted keeps its score. That spares 1,288 notes — including 229 of
// the 232 in personal/preferences/, which the promotion pipeline promoted
// verbatim and which therefore look mined but are filed — for a measured cost of
// 0.000 hit@5 and 0.001 MRR. Filing is the signal that overrides the miner's
// fingerprint.
var Weights = map[string]float64{
	ClassFragment: 0.30,
	ClassStatus:   0.60,
	ClassStaging:  0.30,
	// Same 0.30 as the other demoted classes, and for the reason the comment
	// above gives rather than by analogy: the sweep found every setting at or
	// below 0.6 ranks identically, so this number is not a tuning knob and there
	// is nothing to gain by picking a different one.
	ClassSpace: 0.30,
	// Also 0.30, same reasoning. Note that this is the *dampened* case only — a
	// canonical note carries no flag and so multiplies by 1.0, which is how the
	// design's "canonical gets a flat lift" is expressed without any multiplier
	// above 1.0. Nothing here may exceed 1.0: the clamp below assumes a penalty
	// can only lower a score, and a genuine boost would make a negative-IDF row
	// rank higher for being penalized.
	ClassArtifact: 0.30,
}

// Overfetch is how deep to look before re-ranking. A penalty can only promote a
// note the first fetch actually saw, so the window has to be wide enough that a
// real note buried under a wall of fragments can climb into the top five. 200 is
// ~2.3% of the corpus and costs under a millisecond; re-ranking k alone would
// make the penalty a no-op, since re-sorting five rows cannot introduce a sixth.
const Overfetch = 200

// fragmentOpeners are the miner's stock lead-ins. Catches 3,413 notes.
var fragmentOpeners = []string{
	"User stated:",
	"Fix observed:",
	"User corrected the agent:",
}

// penalizedStatuses mean "not filed yet" or "no longer true". The rescope design
// names `unfiled`; the current corpus predates that vocabulary and spells the
// same condition `inbox`, so both are listed.
var penalizedStatuses = map[string]bool{
	"unfiled": true, "inbox": true, "superseded": true, "expired": true,
}

// promotedStatuses mean a human or dreaming looked at this note and kept it.
var promotedStatuses = map[string]bool{
	"active": true, "filed": true, "final": true, "promoted": true,
	"done": true, "ratified": true, "rendered": true,
}

// fragmentSlugDirs are where a mid-word slug is evidence of a fragment. Both
// directories were filled by the same miner, and a note whose filename starts
// partway into a word ("rver-s-vault-hardwiring-can-t-1") was cut out of a
// longer sentence. This signal contributes 32 files — a rounding error,
// implemented for completeness rather than for effect.
var fragmentSlugDirs = map[string]bool{
	"personal/idea": true, "personal/fix": true,
}

var ellipsisOpeners = []string{"...", "…"}

// realShortWords are short words that legitimately start a slug, so a real note
// is not read as a truncation just for beginning with one.
var realShortWords = func() map[string]bool {
	const list = `
a an and the of to in on at by for is it as or no not new old all one two six
add api cli fix log run see set use web why how who has had was are can may
day end few far git job key lab map max min net now off out own pdf per put
raw ref sdk sql ssh sum tag ten tip top ui url v1 v2 v3 v4 v5 v6 v7 v8 yes
also auto back base best both call case chat code copy core cost data date
docs done down draft drop each edit else even fail file find flag flow form
free from full gate give gold good half hand hard have head help here hold
hook host idea into join jump just keep kind know last left less line link
list live load lock long look loop main make many mark menu mode more most
move much must name need next node note only open over page part pass past
path plan play plus pull push read real repo rest role room root rule runs
safe same save scan seed seem self send sent ship show side sign site size
skip slow slug some sort spec stay step stop such sure sync take task team
tell term test text than that them then they this thus time tool turn type
unit upon used user very view wait walk want ways week well what when will
with word work wrap year your zero`
	m := map[string]bool{}
	for _, w := range strings.Fields(list) {
		m[w] = true
	}
	return m
}()

// looksTruncatedSlug reports whether a slug appears to start partway into a word.
//
// Deliberately conservative: it fires on a short leading segment that is not a
// real short word, which catches `mber-…`, `rver-…`, `ps-…` and leaves
// `read-multi-agent-collective-memory-vault` alone. It cannot catch every case
// without a dictionary and does not need to.
func looksTruncatedSlug(stem string) bool {
	first := stem
	if i := strings.Index(stem, "-"); i >= 0 {
		first = stem[:i]
	}
	first = strings.ToLower(first)
	if first == "" {
		return false
	}
	if isAllDigits(first) {
		return true
	}
	return len(first) <= 4 && !realShortWords[first]
}

func isAllDigits(s string) bool {
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return len(s) > 0
}

// classify returns the rank-penalty classes a note falls into. Classes are
// independent and a note can carry several.
//
//	fragment  — a miner artifact, detected three ways because no single signal
//	            covers the population: a stock lead-in in the body (3,413), a
//	            `mining_confidence` key in the frontmatter which only the miner
//	            writes (5,741), or a mid-word slug in a miner-filled directory
//	            (the remaining 32).
//	status    — frontmatter status is unfiled/inbox/superseded/expired.
//	staging   — a dream-staging proposal. It earns a class because each proposal
//	            quotes the full text of the two notes it is about, so it is a
//	            fragment counted twice.
//
// `body` must already have leading whitespace trimmed.
func classify(rel, head, body, status string) []string {
	var flags []string

	if inDampenedSpace(rel) {
		flags = append(flags, ClassSpace)
	}

	if isDurable(rel, head) {
		flags = append(flags, ClassDurable)
	}

	if m := altitudeRe.FindStringSubmatch(head); altitudeDampening.Load() && m != nil {
		if strings.ToLower(strings.Trim(strings.TrimSpace(m[1]), `'"`)) == ClassArtifact {
			flags = append(flags, ClassArtifact)
		}
	}

	shaped := false
	for _, opener := range fragmentOpeners {
		if strings.HasPrefix(body, opener) {
			shaped = true
			break
		}
	}
	if !shaped && miningRe.MatchString(head) {
		shaped = true
	}
	if !shaped {
		dir, file := splitRel(rel)
		if fragmentSlugDirs[dir] {
			shaped = looksTruncatedSlug(strings.TrimSuffix(file, ".md"))
			if !shaped {
				for _, o := range ellipsisOpeners {
					if strings.HasPrefix(body, o) {
						shaped = true
						break
					}
				}
			}
		}
	}

	// The shape rule splits on whether filing has already passed judgement, and
	// both halves are recorded so a demotion is visible in the result rather
	// than inferred from a number moving.
	if shaped {
		if promotedStatuses[status] {
			flags = append(flags, ClassFragmentPromoted)
		} else {
			flags = append(flags, ClassFragment)
		}
	}

	if penalizedStatuses[status] {
		flags = append(flags, ClassStatus)
	}

	if strings.HasPrefix(rel, "_dream-staging/") &&
		(strings.HasSuffix(rel, ".proposal.md") || proposalRe.MatchString(body)) {
		flags = append(flags, ClassStaging)
	}

	return flags
}

// Signals reports which of the three fragment detectors fire for a note,
// independently of one another and of the status gate.
//
// It exists so the drift-check command can report per-signal counts against the
// figures in the measurement report — 3,413 lead-ins, 5,741 mining_confidence, 32
// slugs — without reimplementing the detection. The first version of that command
// counted with a substring search instead, and produced numbers that looked
// plausible and were wrong by thousands. Detection has exactly one implementation
// and this is how it is read.
type Signals struct {
	LeadIn           bool
	MiningConfidence bool
	TruncatedSlug    bool
}

// Any reports whether the note is fragment-shaped by any signal.
func (s Signals) Any() bool {
	return s.LeadIn || s.MiningConfidence || s.TruncatedSlug
}

// DetectSignals runs the three fragment detectors over a raw note.
func DetectSignals(rel, raw string) Signals {
	head, body := splitFrontmatter(raw)
	body = strings.TrimLeft(body, " \t\r\n")

	var s Signals
	for _, opener := range fragmentOpeners {
		if strings.HasPrefix(body, opener) {
			s.LeadIn = true
			break
		}
	}
	s.MiningConfidence = miningRe.MatchString(head)

	dir, file := splitRel(rel)
	if fragmentSlugDirs[dir] {
		if looksTruncatedSlug(strings.TrimSuffix(file, ".md")) {
			s.TruncatedSlug = true
		} else {
			for _, o := range ellipsisOpeners {
				if strings.HasPrefix(body, o) {
					s.TruncatedSlug = true
					break
				}
			}
		}
	}
	return s
}

func splitFrontmatter(raw string) (head, body string) {
	if m := frontmatterRe.FindStringSubmatchIndex(raw); m != nil {
		return raw[m[2]:m[3]], raw[m[1]:]
	}
	return "", raw
}

func splitRel(rel string) (dir, file string) {
	if i := strings.LastIndex(rel, "/"); i >= 0 {
		return rel[:i], rel[i+1:]
	}
	return "", rel
}

// Multiplier compounds the per-class weights a note's flags earn, and returns
// 1.0 when nothing applies.
//
// Multiplicative rather than additive so the classes compose without any one of
// them being able to zero a score out: a note that is both a fragment and
// unfiled is demoted twice and still ranked.
func Multiplier(flags []string) float64 {
	m := 1.0
	for _, f := range flags {
		if w, ok := Weights[f]; ok {
			m *= w
		}
	}
	return m
}

// durableKinds are the record kinds that never age.
//
// One value, because one is what the corpus actually distinguishes. The whole
// worth of a failure incident is being there on the one day, years later, when
// the same failure recurs — the case where a decay curve is exactly wrong.
var durableKinds = map[string]bool{"failure-incident": true}

// isDurable reports whether a note is exempt from ageing, by any of the four
// routes ported from `lifecycle.is_decay_exempt` and its caller.
//
// Two of them are proxies rather than direct statements, and deliberately so.
// There is no `kind: decision` in this corpus — the operator's ADRs retired into
// living-design amendment logs — so architecture decisions are recognised by the
// `decisions/` directory they have always lived in. And a space the filing
// contract does not govern is exempt wholesale: decay models a *memory* going
// cold because nobody has needed it, and those are documents. A 2016 lesson is
// not less true for being ten years old.
func isDurable(rel, head string) bool {
	if m := lifecycleTierRe.FindStringSubmatch(head); m != nil {
		if strings.ToLower(strings.Trim(strings.TrimSpace(m[1]), `'"`)) == "durable" {
			return true
		}
	}
	if m := kindRe.FindStringSubmatch(head); m != nil {
		if durableKinds[strings.ToLower(strings.Trim(strings.TrimSpace(m[1]), `'"`))] {
			return true
		}
	}
	// Any `decisions/` segment, not just the first: these sit at
	// `desk/projects/<name>/decisions/`, well below the top level that defines a
	// space.
	norm := strings.ReplaceAll(rel, "\\", "/")
	for _, seg := range strings.Split(norm, "/") {
		if strings.EqualFold(seg, "decisions") {
			return true
		}
	}
	return inDecayExemptSpace(rel)
}

// artifactAsks are the words that make a question a request for the artifact
// shape rather than a general one.
//
// An explicit list rather than a learned signal, and short on purpose. There is
// nothing to tune it against: no note in this corpus carries `altitude` yet, so
// every candidate list scores identically and a longer one would only be a
// longer guess. It grows when enrichment has populated the field and a measured
// run says which words actually separate the two intents.
var artifactAsks = map[string]bool{
	"meeting":      true,
	"meetings":     true,
	"session":      true,
	"sessions":     true,
	"standup":      true,
	"transcript":   true,
	"transcripts":  true,
	"conversation": true,
	"discussed":    true,
	"discussion":   true,
	"exhaust":      true,
}

// QueryWantsArtifact reports whether a question asks for the artifact shape, in
// which case the artifact dampening is suppressed for that query.
//
// Suppressed rather than reversed into a boost. Every multiplier in this ranker
// is at or below 1.0, and the negative-IDF clamp in penalizeRankAndDecay depends
// on it: a multiplier above 1.0 applied to a row whose score went negative would
// move that row *up* for being boosted and *down* for being ordinary. Removing
// the penalty gets the same ordering as adding a lift, without the trapdoor.
func QueryWantsArtifact(text string) bool {
	for _, w := range strings.Fields(strings.ToLower(text)) {
		w = strings.Trim(w, ".,;:!?\"'()[]")
		if artifactAsks[w] {
			return true
		}
	}
	return false
}
