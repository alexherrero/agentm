package ledger

// Why a target is owed, in the order the night takes it (task 181 step 4,
// #784 option 2).
//
// Reason says what the ledger found; Cause says what that means for the
// budget. They part in one place: a stale row is owed for one of two reasons
// that call for different treatment. A row from an older pass is owed the deep
// pass, which a prompt change makes true of every note at once and the night
// works through over many nights. A row from an older judgment hash is owed
// because a contract field a judgment reads has changed; it is the cheapest
// re-judgment to defer, and it is taken least-recently-judged first, so a run
// of contract edits works across the corpus instead of re-judging the same
// notes at the head of the queue every night.

// Cause is one pending target's place in the night's order. Lower runs first.
type Cause int

const (
	// CauseNever: no row at all; the note has never been judged.
	CauseNever Cause = iota + 1
	// CauseChanged: judged at this pass and contract over content the note no
	// longer holds.
	CauseChanged
	// CauseRetry: the last attempt failed.
	CauseRetry
	// CausePass: judged under an older pass, so owed the deep pass.
	CausePass
	// CauseJudgment: judged at this pass under an older judgment hash.
	CauseJudgment
	// CauseSkipped: a gate declined it last time. It costs nothing to offer
	// again, and goes last for the same reason.
	CauseSkipped
)

var causeNames = map[Cause]string{
	CauseNever: "never", CauseChanged: "changed", CauseRetry: "retry",
	CausePass: "deep pass", CauseJudgment: "judgment", CauseSkipped: "skipped",
}

func (c Cause) String() string {
	if s, ok := causeNames[c]; ok {
		return s
	}
	return "unknown"
}

// Causes lists the causes in the order the night takes them.
func Causes() []Cause {
	return []Cause{CauseNever, CauseChanged, CauseRetry, CausePass, CauseJudgment, CauseSkipped}
}

// CauseOf says why one pending item is owed, against the version the report
// was taken at.
func (r Report) CauseOf(it Item) Cause {
	switch it.Reason {
	case ReasonNever:
		return CauseNever
	case ReasonChanged:
		return CauseChanged
	case ReasonRetry:
		return CauseRetry
	case ReasonSkipped:
		return CauseSkipped
	case ReasonStale:
		if r.Version != "" && it.Version != r.Version {
			return CausePass
		}
		return CauseJudgment
	}
	return CauseSkipped
}
