// Package health decides whether the daemon is fine or whether the operator
// needs to hear about it.
//
// This is the loud queue from `wiki/designs/agentm-rescope-topology.md`. The
// previous system's inbox reached 4,933 items without anything ever saying so,
// and the fix is not a smaller queue — ambient capture is staying on by the
// operator's own decision, so the queue is meant to be busy. The fix is that a
// queue which stops draining announces itself.
//
// Two properties of the thresholds are load-bearing. They are age-dominant: a
// pile of fresh unfiled items is a working day, and a three-day-old oldest item
// is a stalled pipeline, so the number that pages is an age. And Evaluate is a
// pure function of its input, so what turns a status red can be pinned to
// hand-written literals in a test rather than recomputed by the code under test.
package health

import (
	"fmt"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

// Levels a report can carry. There are two on purpose: something either needs
// the operator or it does not, and a middle rung is where conditions go to be
// ignored.
const (
	LevelOK  = "ok"
	LevelRed = "red"
)

// Alert codes. Stable strings — they key the anti-fatigue record, so renaming
// one re-sends every alert of that kind once.
const (
	AlertQueueAge    = "queue-age"
	AlertQueueSize   = "queue-size"
	AlertIndexStale  = "index-stale"
	AlertProbeFailed = "probe-failed"
	AlertProbeStale  = "probe-stale"
	AlertContract    = "filing-contract"
)

// Filing-contract states.
const (
	// ContractHealthy means the operator's own rules file resolved and parsed.
	ContractHealthy = "healthy"
	// ContractDefault means the binary's embedded copy is what runs, because no
	// vault instance was found. A working install, and a warning sign on a
	// machine that should have one.
	ContractDefault = "default"
	// ContractBroken means a rules file resolved and would not parse. Filing is
	// halted and typed captures are refused until it does.
	ContractBroken = "broken"
)

// Git states, in the vocabulary the status surface reports them in.
const (
	GitHealthy  = "healthy"
	GitDegraded = "degraded"
)

// Embedder is the vector arm's state, reported on the status surface.
//
// Reported loudly and never paged about, which is the same treatment git
// degradation gets and for the same reason: retrieval falling back to its lexical
// arm is a capability quietly going missing, so it has to be visible — but it is
// not the operator being woken up, because nothing is being lost and the daemon
// still answers every search.
type Embedder struct {
	// State is one of off / starting / warm / degraded. `off` is a working
	// install with no model, not a fault.
	State  string `json:"state"`
	Detail string `json:"detail,omitempty"`
	Model  string `json:"model,omitempty"`
	// Vectors and InScope make coverage a sentence rather than a bare count —
	// "7,391 of 9,473" says something a lone "7,391" does not.
	Vectors  int `json:"vectors"`
	InScope  int `json:"in_scope"`
	Stale    int `json:"stale"`
	Dim      int `json:"dim,omitempty"`
	Restarts int `json:"restarts"`
}

// Contract reports whether the filing contract is readable.
//
// This component exists because the failure it describes is otherwise invisible.
// When `standards/storage-rules.md` will not parse, the daemon keeps serving:
// search does not read the taxonomy, and an ambient capture supplies no type, so
// the two loudest surfaces both look fine. What has actually stopped is filing —
// and a capture that names a type is being refused, one caller at a time, with
// nothing counting it. A degradation that only shows up as "memories stopped
// getting better" is one that gets found in a month.
//
// Same reasoning the embedder's `degraded` state carries: it has to be visible on
// the status surface rather than inferred from bad results.
type Contract struct {
	// State is one of healthy / default / broken.
	State  string `json:"state"`
	Detail string `json:"detail,omitempty"`
	// Source is the file that won resolution, so a surprising contract is
	// diagnosable without re-deriving the resolution order by hand.
	Source string `json:"source,omitempty"`
	// Hash is the contract a filing judgment made right now would be stamped
	// with — the same value a memory carries as `rules_hash`.
	Hash string `json:"hash,omitempty"`
	// RefusedCaptures counts captures rejected since boot because the caller
	// named a type and there was no contract to validate it against. The
	// quietest symptom of a broken contract, and the one that means a client is
	// failing every write.
	RefusedCaptures int64 `json:"refused_captures"`
	// CheckedAt is when the contract was last re-read. A status is about now
	// only if this is.
	CheckedAt time.Time `json:"checked_at,omitempty"`
}

// Filing reports whether anything can be filed at all.
func (c Contract) Filing() bool { return c.State != ContractBroken }

// String is the status line's one-liner.
func (c Contract) String() string {
	switch c.State {
	case ContractBroken:
		s := "broken — filing is halted"
		if c.Detail != "" {
			s += ": " + c.Detail
		}
		if c.RefusedCaptures > 0 {
			s += fmt.Sprintf(" (%d typed capture(s) refused since boot)", c.RefusedCaptures)
		}
		return s
	case ContractDefault:
		return "the binary's embedded default — no vault instance, so edits to one " +
			"will not take effect"
	default:
		return fmt.Sprintf("%s (%s)", c.Source, c.Hash)
	}
}

// Hybrid reports whether the vector arm can actually serve a search: a warm
// model is necessary and vectors to compare against are too. A warm embedder over
// an empty table is a search that silently returns its lexical arm.
func (e Embedder) Hybrid() bool { return e.State == "warm" && e.Vectors > 0 }

// String is the status line's one-liner.
func (e Embedder) String() string {
	switch e.State {
	case "off":
		return "off — hybrid unavailable, lexical-only"
	case "warm":
		s := fmt.Sprintf("ok (warm) · %s", e.Model)
		if e.InScope > 0 {
			s += fmt.Sprintf(" · %d/%d embedded", e.Vectors, e.InScope)
		}
		if e.Stale > 0 {
			s += fmt.Sprintf(" · %d stale", e.Stale)
		}
		if e.Vectors == 0 {
			s += " — no vectors yet, run `agentmd embed`"
		}
		return s
	case "starting":
		return "starting — loading weights, hybrid off until warm"
	default:
		s := "DEGRADED — hybrid off, lexical-only"
		if e.Detail != "" {
			s += " (" + e.Detail + ")"
		}
		if e.Restarts > 0 {
			s += fmt.Sprintf(" · %d restarts", e.Restarts)
		}
		return s
	}
}

// Thresholds are what turns a number red.
type Thresholds struct {
	UnfiledAge   time.Duration `json:"unfiled_age"`
	UnfiledCount int           `json:"unfiled_count"`
	// IndexStale is how far behind the last reconcile pass may fall. Derived
	// from the reconcile interval rather than configured separately: the
	// question is whether the loop is still turning, and only the loop's own
	// period can answer it.
	IndexStale time.Duration `json:"index_stale"`
	// ProbeStale is how long the daemon may go without a completed self-probe.
	ProbeStale time.Duration `json:"probe_stale"`
	// ProbeBudget is how long one round trip may take.
	ProbeBudget time.Duration `json:"probe_budget"`
}

// Alert is one reason the daemon is red.
type Alert struct {
	Code   string `json:"code"`
	Detail string `json:"detail"`
}

// Queue is the filing queue's two numbers, which the design asks for by name:
// how many are waiting, and the age of the oldest.
//
// It is a query, not a folder. There is no inbox — an unfiled memory sits in
// `memory/` fully indexed and rank-penalized from the instant capture commits —
// so "how many are waiting" is a SELECT over frontmatter status.
type Queue struct {
	// Unfiled is everything waiting, inherited backlog included. It is the
	// number the design asks for and it is always reported.
	Unfiled int `json:"unfiled"`
	// Inherited is the part of it captured before the baseline. Reported, never
	// paged about — see baseline.go for why.
	Inherited int `json:"inherited"`
	// Since is the part this daemon is responsible for, and the number the
	// thresholds read.
	Since    int    `json:"since"`
	Baseline string `json:"baseline,omitempty"`
	// OldestAge is the age of the oldest item the thresholds apply to. Zero when
	// there is none; OldestAt says which.
	OldestAge Duration `json:"oldest_age"`
	OldestAt  string   `json:"oldest_at,omitempty"`
	// InheritedOldestAge is the age of the oldest inherited item, so the backlog
	// stays visible rather than becoming a number nobody sees again.
	InheritedOldestAge Duration `json:"inherited_oldest_age,omitempty"`
}

// Freshness is whether the index still reflects the vault.
type Freshness struct {
	Documents int      `json:"documents"`
	Age       Duration `json:"age"`
	LastAt    string   `json:"last_at,omitempty"`
	Errors    int      `json:"errors"`
	Fresh     bool     `json:"fresh"`
}

// Git is the state of the undo story, in the two words the topology document
// uses for it.
type Git struct {
	State string `json:"state"`
	// Detail carries the reason when the state is degraded, so `degraded` is
	// never a bare word the reader has to go and interpret.
	Detail string `json:"detail,omitempty"`
}

// Healthy reports whether commits are actually happening.
func (g Git) Healthy() bool { return g.State == GitHealthy }

// String renders the state the way the brief words it: "healthy", or
// "degraded: not a repository".
func (g Git) String() string {
	if g.Detail == "" {
		return g.State
	}
	return g.State + ": " + g.Detail
}

// ProbeState is the last recorded self-probe. It is persisted by the probe
// package and read here, so a probe that failed at 3am is still the reason the
// status is red at 9.
type ProbeState struct {
	At       string   `json:"at,omitempty"`
	OK       bool     `json:"ok"`
	Elapsed  Duration `json:"elapsed"`
	Detail   string   `json:"detail,omitempty"`
	Path     string   `json:"path,omitempty"`
	Age      Duration `json:"age"`
	Recorded bool     `json:"recorded"`
}

// Report is the daemon's account of whether it is doing its job.
type Report struct {
	Level      string     `json:"level"`
	Alerts     []Alert    `json:"alerts"`
	Queue      Queue      `json:"queue"`
	Index      Freshness  `json:"index"`
	Git        Git        `json:"git"`
	Embedder   Embedder   `json:"embedder"`
	Contract   Contract   `json:"contract"`
	Probe      ProbeState `json:"probe"`
	Thresholds Thresholds `json:"thresholds"`
}

// Red reports whether anything needs the operator.
func (r Report) Red() bool { return r.Level == LevelRed }

// Fingerprint identifies the set of conditions currently red, so the same
// standing problem is not emailed twice while a genuinely new one still is.
func (r Report) Fingerprint() string {
	if len(r.Alerts) == 0 {
		return ""
	}
	codes := make([]string, 0, len(r.Alerts))
	for _, a := range r.Alerts {
		codes = append(codes, a.Code)
	}
	sort.Strings(codes)
	return strings.Join(codes, "+")
}

// Input is everything Evaluate needs. Passing it in rather than reaching for it
// is what makes the thresholds testable against literals.
type Input struct {
	Now time.Time
	// Uptime keeps a freshly-started daemon from being red for not having run a
	// daily probe yet.
	Uptime time.Duration

	// Unfiled counts everything waiting; UnfiledSince counts only what was
	// captured after Baseline, which is what the thresholds read.
	Unfiled            int
	UnfiledSince       int
	OldestUnfiled      time.Time
	OldestUnfiledSince time.Time
	Baseline           time.Time
	Documents          int

	LastReconcile       time.Time
	LastReconcileErrors int

	GitAvailable bool
	GitReason    string

	// Embedder is passed through rather than derived: the supervisor owns the
	// child's state and the index owns the vector counts. Health reports both and
	// is a second source of truth about neither.
	Embedder Embedder

	// Contract is passed through rather than derived, for the same reason the
	// embedder is: the holder owns the resolution, and health is a second source
	// of truth about neither.
	Contract Contract

	Probe      ProbeState
	ProbeAt    time.Time
	Thresholds Thresholds
}

// Evaluate turns the daemon's numbers into a report.
//
// Git being degraded is deliberately not an alert. It is reported on every
// status surface and it blocks the corpus-write gate, but it is a known,
// deliberate, operator-owned condition today — the vault is not a repository
// until the git-transport migration runs, and paging daily about a migration
// that is scheduled for later is how an alert channel trains its reader to
// delete it unread. The gate is the mechanism that makes the degradation bite;
// the email is for things that changed.
func Evaluate(in Input) Report {
	r := Report{
		Level:      LevelOK,
		Alerts:     []Alert{},
		Thresholds: in.Thresholds,
		Queue:      Queue{Unfiled: in.Unfiled},
		Index: Freshness{
			Documents: in.Documents,
			Errors:    in.LastReconcileErrors,
		},
		Git:      Git{State: GitHealthy},
		Embedder: in.Embedder,
		Contract: in.Contract,
		Probe:    in.Probe,
	}

	if !in.GitAvailable {
		r.Git = Git{State: GitDegraded, Detail: gitDetail(in.GitReason)}
	}

	// --- the queue ----------------------------------------------------------
	//
	// Everything is counted; the thresholds read the part captured after the
	// baseline. The inherited backlog is reported on the same line and does not
	// page — see baseline.go.
	r.Queue.Since = in.UnfiledSince
	r.Queue.Inherited = in.Unfiled - in.UnfiledSince
	if r.Queue.Inherited < 0 {
		r.Queue.Inherited = 0
	}
	if !in.Baseline.IsZero() {
		r.Queue.Baseline = in.Baseline.UTC().Format(time.RFC3339)
	}
	if !in.OldestUnfiled.IsZero() && r.Queue.Inherited > 0 {
		r.Queue.InheritedOldestAge = Duration(since(in.Now, in.OldestUnfiled))
	}
	if !in.OldestUnfiledSince.IsZero() && in.UnfiledSince > 0 {
		age := since(in.Now, in.OldestUnfiledSince)
		r.Queue.OldestAge = Duration(age)
		r.Queue.OldestAt = in.OldestUnfiledSince.UTC().Format(time.RFC3339)
		if in.Thresholds.UnfiledAge > 0 && age > in.Thresholds.UnfiledAge {
			r.add(AlertQueueAge, fmt.Sprintf(
				"the oldest unfiled item is %s old, past the %s threshold — filing has "+
					"stalled, and a queue that stops draining is how the last inbox reached 4,933",
				short(age), short(in.Thresholds.UnfiledAge)))
		}
	}
	if in.Thresholds.UnfiledCount > 0 && in.UnfiledSince > in.Thresholds.UnfiledCount {
		r.add(AlertQueueSize, fmt.Sprintf(
			"%d items have gone unfiled since the baseline, past the %d backstop; size is "+
				"the weaker signal, so this one firing on its own means something wrote a "+
				"great many items at once",
			in.UnfiledSince, in.Thresholds.UnfiledCount))
	}

	// --- the index ----------------------------------------------------------
	if in.LastReconcile.IsZero() {
		r.Index.Fresh = false
		if in.Thresholds.IndexStale > 0 && in.Uptime > in.Thresholds.IndexStale {
			r.add(AlertIndexStale, fmt.Sprintf(
				"no reconcile pass has completed in %s of uptime; the index is not "+
					"tracking the vault", short(in.Uptime)))
		}
	} else {
		age := since(in.Now, in.LastReconcile)
		r.Index.Age = Duration(age)
		r.Index.LastAt = in.LastReconcile.UTC().Format(time.RFC3339)
		r.Index.Fresh = in.Thresholds.IndexStale <= 0 || age <= in.Thresholds.IndexStale
		if !r.Index.Fresh {
			r.add(AlertIndexStale, fmt.Sprintf(
				"the last reconcile pass finished %s ago, past the %s threshold — the "+
					"watch loop is not turning, so the index reflects whenever it stopped",
				short(age), short(in.Thresholds.IndexStale)))
		}
	}

	// --- the self-probe -----------------------------------------------------
	switch {
	case !in.Probe.Recorded:
		if in.Thresholds.ProbeStale > 0 && in.Uptime > in.Thresholds.ProbeStale {
			r.add(AlertProbeStale, fmt.Sprintf(
				"no self-probe has completed in %s of uptime; the round trip is unverified",
				short(in.Uptime)))
		}
	case !in.Probe.OK:
		r.add(AlertProbeFailed, fmt.Sprintf(
			"the last self-probe failed: %s", firstLine(in.Probe.Detail)))
	default:
		if !in.ProbeAt.IsZero() {
			age := since(in.Now, in.ProbeAt)
			r.Probe.Age = Duration(age)
			if in.Thresholds.ProbeStale > 0 && age > in.Thresholds.ProbeStale {
				r.add(AlertProbeStale, fmt.Sprintf(
					"the last self-probe passed but was %s ago, past the %s threshold — "+
						"the prover has stopped running, which is the failure that hides "+
						"every other one", short(age), short(in.Thresholds.ProbeStale)))
			}
		}
	}

	// --- the filing contract ------------------------------------------------
	//
	// Alerted, unlike git-degraded. Git being unavailable is a known, deliberate,
	// operator-owned condition scheduled for later, and paging daily about a
	// scheduled migration teaches a reader to delete the mail unread. A contract
	// that stopped parsing is the opposite: nobody chose it, it happened just
	// now, and it is silently holding up every filing decision in the system.
	// That is what this channel is for.
	if in.Contract.State == ContractBroken {
		detail := "the filing contract does not parse, so nothing is being filed"
		if in.Contract.Detail != "" {
			detail += ": " + in.Contract.Detail
		}
		if in.Contract.RefusedCaptures > 0 {
			detail += fmt.Sprintf(". %d capture(s) naming a type have been refused since boot",
				in.Contract.RefusedCaptures)
		}
		r.add(AlertContract, detail)
	}

	if len(r.Alerts) > 0 {
		r.Level = LevelRed
	}
	return r
}

func (r *Report) add(code, detail string) {
	r.Alerts = append(r.Alerts, Alert{Code: code, Detail: detail})
}

// since is now minus then, floored at zero. A clock that went backwards — a
// timezone change, an NTP correction, an mtime from the future on a synced
// mount — would otherwise produce a negative age that reads as fresh.
func since(now, then time.Time) time.Duration {
	d := now.Sub(then)
	if d < 0 {
		return 0
	}
	return d
}

// gitDetail reduces the repository's own reason to the short phrase the status
// surface reports, keeping the full sentence for the detail line beneath it.
func gitDetail(reason string) string {
	reason = strings.TrimSpace(reason)
	if reason == "" {
		return "not a repository"
	}
	if strings.Contains(reason, "not a git repository") {
		return "not a repository"
	}
	return firstLine(reason)
}

func firstLine(s string) string {
	if i := strings.IndexAny(s, "\n"); i >= 0 {
		return strings.TrimSpace(s[:i])
	}
	return strings.TrimSpace(s)
}

// Duration is a time.Duration that marshals as a readable string. The status
// surface is read by a person at least as often as by a machine, and three days
// rendered as a fifteen-digit nanosecond count is not something anyone can check
// against a threshold at a glance.
type Duration time.Duration

func (d Duration) MarshalJSON() ([]byte, error) {
	return []byte(`"` + short(time.Duration(d)) + `"`), nil
}

// UnmarshalJSON reads back what MarshalJSON wrote, including the day form.
//
// `time.ParseDuration` has no day unit, so a report whose oldest unfiled item
// was four days old marshalled as "4d" and then failed to parse — and the
// reader is `agentmd status`, which means the status surface broke precisely
// when the queue was stalled, the one condition it exists for. Found by running
// the daemon against the real vault, where uptime and queue ages routinely pass
// 48 hours; every test until then used a duration short enough to dodge it.
func (d *Duration) UnmarshalJSON(b []byte) error {
	s := strings.Trim(string(b), `"`)
	if s == "" || s == "null" {
		*d = 0
		return nil
	}
	parsed, err := parseShort(s)
	if err != nil {
		return err
	}
	*d = Duration(parsed)
	return nil
}

var dayFormRe = regexp.MustCompile(`^(\d+)d(?:(\d+)h)?$`)

func parseShort(s string) (time.Duration, error) {
	if m := dayFormRe.FindStringSubmatch(s); m != nil {
		days, err := strconv.Atoi(m[1])
		if err != nil {
			return 0, fmt.Errorf("duration %q: %w", s, err)
		}
		out := time.Duration(days) * 24 * time.Hour
		if m[2] != "" {
			hours, err := strconv.Atoi(m[2])
			if err != nil {
				return 0, fmt.Errorf("duration %q: %w", s, err)
			}
			out += time.Duration(hours) * time.Hour
		}
		return out, nil
	}
	parsed, err := time.ParseDuration(s)
	if err != nil {
		return 0, fmt.Errorf("duration %q: %w", s, err)
	}
	return parsed, nil
}

// String renders the duration the way the status surface prints it.
func (d Duration) String() string { return short(time.Duration(d)) }

// short renders a duration at the resolution a reader cares about: seconds for
// something recent, hours for something stale, days for something abandoned.
func short(d time.Duration) string {
	switch {
	case d <= 0:
		return "0s"
	case d < time.Minute:
		return d.Round(time.Millisecond).String()
	case d < time.Hour:
		return d.Round(time.Second).String()
	case d < 48*time.Hour:
		return d.Round(time.Minute).String()
	default:
		days := int(d.Hours() / 24)
		hours := int(d.Hours()) - days*24
		if hours == 0 {
			return fmt.Sprintf("%dd", days)
		}
		return fmt.Sprintf("%dd%dh", days, hours)
	}
}
