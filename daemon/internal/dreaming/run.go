package dreaming

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/note"
	"github.com/alexherrero/agentm/daemon/internal/people"
	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// Options is one invocation's shape.
type Options struct {
	// Apply makes the writes. Off, the pass is report-only: it decides,
	// prints what it would do, journals the run, and touches no note. Off is
	// the default until the parity gate flips it (task 6).
	Apply bool
	// Force skips the dual gate — an operator asking for a pass now.
	Force bool
	// Every is the minimum interval between passes.
	Every time.Duration
	// Now is injectable; zero means the clock.
	Now time.Time
	// Pace sleeps between mutations. Tests use it to land a kill mid-pass;
	// production leaves it zero.
	Pace time.Duration
	// Cap bounds automatic demotions per pass.
	Cap int
	// RunID is injectable; empty means a fresh one.
	RunID string
	// LockWait is how long to wait for a live holder before refusing.
	LockWait time.Duration
	// Reclassify forces the sampled re-classification diff this pass.
	Reclassify bool
	// TaskCap bounds the closed task folders one pass moves, counted in
	// folders (0: the contract's `demotion_cap`).
	TaskCap int
}

// Report is the record of one invocation, printed by the command.
type Report struct {
	RunID    string        `json:"run_id,omitempty"`
	Mode     string        `json:"mode"`
	Decision Decision      `json:"decision"`
	Resumed  int           `json:"resumed"`
	Plan     LifecyclePlan `json:"plan"`
	Copies   CopiesPlan    `json:"copies"`
	Refile   RefilePlan    `json:"refile"`
	Promote  PromotePlan   `json:"promote"`
	Calendar CalendarPlan  `json:"calendar"`
	// The three free jobs the axis added, and the night's own record of itself.
	Retain    RetainPlan    `json:"retain"`
	Reconcile ReconcilePlan `json:"reconcile"`
	Projects  ProjectsPlan  `json:"projects"`
	// Tasks is the closed-task mover: what it moved, or would have moved while
	// its switch is off, and every folder it held back.
	Tasks TaskMovePlan `json:"tasks"`
	Facet FacetPlan    `json:"facet"`
	// SkippedByHandMove is every file an intent could not be applied to because
	// it had moved since the plan read it. Named in the dreaming facet rather
	// than swallowed: the reconcile step at the end of the same night is what
	// repairs them, and a repair nobody can see is indistinguishable from a loss.
	SkippedByHandMove []string `json:"skipped_by_hand_move,omitempty"`
	Mocs              MocsPlan `json:"mocs"`
	// Entities is the entity builder (task 179): the repository, issue,
	// release and people pages it wrote, and those it removed.
	Entities EntitiesPlan `json:"entities"`
	// Ideas is `Ideas.md`, rebuilt with the maps (agentm-vault part 13).
	Ideas IdeasPlan `json:"ideas"`
	Dates DatesPlan `json:"dates"`
	// The report-only checks: nothing below mutates a note.
	Vocabulary VocabularyReport `json:"vocabulary"`
	Trends     TrendReport      `json:"trends"`
	Reclassify ReclassifyReport `json:"reclassify"`
	Applied    int              `json:"applied"`
	Skipped    int              `json:"skipped"`
	Outcome    string           `json:"outcome"`
	// DeletionManifest is where the record of tonight's deletions was written,
	// before the files went. Empty when nothing was deleted; a pass that could
	// not write it deletes nothing, and says so by leaving this empty.
	DeletionManifest string `json:"deletion_manifest,omitempty"`
	Refused          string `json:"refused,omitempty"`
	Root             string `json:"root,omitempty"`
	seq              int
}

// ErrRefused is returned (with a Report) when the pass could not take its
// lock: another pass is live. The command maps it to exit 3.
var ErrRefused = errors.New("refused")

const (
	OutcomeNotDue   = "not-due"
	OutcomeReported = "reported"
	OutcomeApplied  = "applied"
)

// Run does one pass: lock, resume anything a crash left half-done, decide
// whether a new pass is due, plan, apply (or report), and record.
func Run(cfg *config.Config, opt Options) (Report, error) {
	now := opt.Now
	if now.IsZero() {
		now = time.Now().UTC()
	}
	if opt.Every <= 0 {
		opt.Every = 7 * 24 * time.Hour
	}
	if opt.LockWait <= 0 {
		opt.LockWait = 2 * time.Second
	}
	rep := Report{Mode: "report"}
	if opt.Apply {
		rep.Mode = "apply"
	}
	root := filepath.Join(cfg.VaultPath, filepath.FromSlash(cfg.MemoryRoot))
	rep.Root = root

	lock, err := Acquire(SingletonLockDir(cfg.EngineStateDir), 30*time.Second, opt.LockWait)
	if err != nil {
		var held *ErrHeld
		if errors.As(err, &held) {
			rep.Refused = held.Error()
			rep.Outcome = "refused"
			return rep, ErrRefused
		}
		return rep, err
	}
	defer lock.Release()

	st, err := LoadState(cfg.EngineStateDir)
	if err != nil {
		return rep, err
	}
	journal, err := OpenJournal(cfg.EngineStateDir)
	if err != nil {
		return rep, err
	}

	// Resume first, gate second: a crash's half-done work is finished
	// whether or not a new pass is due.
	entries, err := journal.Read()
	if err != nil {
		return rep, err
	}
	if runID, pending := Unfinished(entries); runID != "" {
		for _, e := range pending {
			if _, err := journal.Resolve(root, e, now); err != nil {
				return rep, err
			}
			rep.Resumed++
		}
		if err := journal.Append(Entry{Kind: KindRunDone, RunID: runID, TS: now,
			Outcome: fmt.Sprintf("resumed: %d intent(s) settled", len(pending))}); err != nil {
			return rep, err
		}
		st.LastDone, st.LastOutcome = now, "resumed"
		if err := SaveState(cfg.EngineStateDir, st); err != nil {
			return rep, err
		}
	}

	act := activity(cfg, st)
	rep.Decision = Due(st, now, opt.Every, act)
	if opt.Force {
		rep.Decision.Due, rep.Decision.Reason = true, "forced"
	}
	if !rep.Decision.Due {
		rep.Outcome = OutcomeNotDue
		return rep, nil
	}

	var contract *rules.Rules
	if cfg.Rules != nil {
		if r, err := cfg.Rules.Get(); err == nil {
			contract = r
		}
	}
	runID := opt.RunID
	if runID == "" {
		runID = newRunID(now)
	}
	rep.RunID = runID
	st.LastStarted, st.LastRunID, st.Runs = now, runID, st.Runs+1
	if err := SaveState(cfg.EngineStateDir, st); err != nil {
		return rep, err
	}
	if err := journal.Append(Entry{Kind: KindRunStart, RunID: runID, TS: now, Mode: rep.Mode}); err != nil {
		return rep, err
	}

	// The jobs, in the order they land: the lifecycle lane, the copy
	// collapse, the re-file, the promotion. Each plans against the corpus as
	// the previous left it in a report; when applying, each is planned and
	// applied before the next plans, so a note the copy job just superseded
	// is not re-filed under it.
	var intents []Intent
	plan, err := PlanLifecycle(root, cfg.EngineStateDir, contract, now, opt.Cap)
	if err != nil {
		return rep, err
	}
	rep.Plan = plan
	intents = append(intents, plan.Intents...)
	if opt.Apply {
		// The manifest, before the files go. A deletion with no record is what
		// the retired doctrine — "no policy outcome ever deletes a memory" —
		// was protecting against, and it is still refused: if the record cannot
		// be written, the deletions are dropped and the rest of the pass runs.
		if rows := DeletionRows(root, intents, plan.Deleted); len(rows) > 0 {
			manifest, mErr := WriteDeletionManifest(root, runID, rows, now)
			if mErr != nil || manifest == "" {
				intents = withoutDeletions(intents)
				rep.Plan.Deleted = nil
				rep.DeletionManifest = ""
			} else {
				rep.DeletionManifest = manifest
			}
		}
		if err := applyAll(journal, root, runID, intents, now, opt.Pace, &rep); err != nil {
			return rep, err
		}
		intents = nil
	}
	copies, err := PlanCopies(root, DefaultCopiesCap)
	if err != nil {
		return rep, err
	}
	rep.Copies = copies
	intents = append(intents, copies.Intents...)
	if opt.Apply {
		if err := applyAll(journal, root, runID, intents, now, opt.Pace, &rep); err != nil {
			return rep, err
		}
		intents = nil
	}
	refile, err := PlanRefile(root, contract)
	if err != nil {
		return rep, err
	}
	rep.Refile = refile
	intents = append(intents, refile.Intents...)
	if opt.Apply {
		if err := applyAll(journal, root, runID, intents, now, opt.Pace, &rep); err != nil {
			return rep, err
		}
		intents = nil
	}
	promote, err := PlanPromote(root, contract, now)
	if err != nil {
		return rep, err
	}
	rep.Promote = promote
	intents = append(intents, promote.Intents...)
	if opt.Apply {
		if err := applyAll(journal, root, runID, intents, now, opt.Pace, &rep); err != nil {
			return rep, err
		}
		intents = nil
	}
	// The entity pages (task 179): built from the index at no model cost, and
	// derived — nothing else writes them. A builder that cannot plan says so in
	// its own row and the night goes on.
	entities, err := planEntitiesFor(cfg, root, contract, now)
	if err != nil {
		entities = EntitiesPlan{Counts: map[string]int{}, Skipped: "the builder could not plan: " + err.Error()}
	}
	rep.Entities = entities
	intents = append(intents, entities.Intents...)
	if opt.Apply {
		if err := applyAll(journal, root, runID, intents, now, opt.Pace, &rep); err != nil {
			return rep, err
		}
		intents = nil
	}
	// Batch 2 (task 5): the maintenance jobs — the register's reviews, the
	// maps of content, the date glosses — then the report-only checks.
	calendar, err := PlanCalendar(root, contract, now, DefaultRollupWeeks)
	if err != nil {
		return rep, err
	}
	rep.Calendar = calendar
	intents = append(intents, calendar.Intents...)
	mocs, err := PlanMocs(root, contract, now)
	if err != nil {
		return rep, err
	}
	// The projects space's maps ride in the same job (plan 09): their pages join
	// the report's, and the root map counts the ones planned tonight.
	projectMaps, err := PlanProjectMaps(root, now)
	if err != nil {
		return rep, err
	}
	mocs.Pages = append(mocs.Pages, projectMaps.Pages...)
	mocs.Intents = append(mocs.Intents, projectMaps.Intents...)
	// Every project's tracker is generated in the same job from its task
	// trackers (task 176 step 6), so the checklist cannot disagree with them.
	// Tonight's activity reading is taken first and rendered in, and the
	// projects job below writes the same reading, so a tracker is written
	// once a night rather than rendered here and line-edited there (task 190).
	projActivity, projSkipped := ProjectActivity(root, note.NewAccessLog(cfg.EngineStateDir, root), now)
	activityBySlug := map[string]ActivityReading{}
	for _, a := range projActivity {
		activityBySlug[a.Slug] = a
	}
	projectTrackers, err := PlanProjectTrackers(root, now, activityBySlug)
	if err != nil {
		return rep, err
	}
	mocs.Pages = append(mocs.Pages, projectTrackers.Pages...)
	mocs.Intents = append(mocs.Intents, projectTrackers.Intents...)
	// The entity map lists the builder's four folders (task 179). A builder
	// that could not plan tonight leaves the map as it was, with its pages.
	if rep.Entities.Skipped == "" {
		entityMap := PlanEntityMap(root, rep.Entities.Pages, now)
		mocs.Pages = append(mocs.Pages, entityMap.Pages...)
		mocs.Removed = append(mocs.Removed, entityMap.Removed...)
		mocs.Intents = append(mocs.Intents, entityMap.Intents...)
	}
	// The two shared spaces of 2026-09-24 ride in it too, once they hold a note.
	spaceMaps, err := PlanSpaceMaps(root, now)
	if err != nil {
		return rep, err
	}
	mocs.Pages = append(mocs.Pages, spaceMaps.Pages...)
	mocs.Intents = append(mocs.Intents, spaceMaps.Intents...)
	planned := append([]string(nil), calendar.YearMaps...)
	for _, p := range mocs.Pages {
		planned = append(planned, p.Rel)
	}
	rootMap, err := PlanRootMap(root, planned, now)
	if err != nil {
		return rep, err
	}
	mocs.Pages = append(mocs.Pages, rootMap.Pages...)
	mocs.Intents = append(mocs.Intents, rootMap.Intents...)
	rep.Mocs = mocs
	intents = append(intents, mocs.Intents...)
	// `Ideas.md` rides with the maps: a list generated over the idea cards,
	// under the operator's own head, and only once they have adopted it.
	ideas, err := PlanIdeas(root, "")
	if err != nil {
		return rep, err
	}
	rep.Ideas = ideas
	intents = append(intents, ideas.Intents...)
	dates, err := PlanDates(root, contract, now)
	if err != nil {
		return rep, err
	}
	rep.Dates = dates
	intents = append(intents, dates.Intents...)
	if opt.Apply {
		if err := applyAll(journal, root, runID, intents, now, opt.Pace, &rep); err != nil {
			return rep, err
		}
		rep.Outcome = OutcomeApplied
	} else {
		rep.Outcome = OutcomeReported
	}
	if rep.Vocabulary, err = VocabularyAudit(root, contract); err != nil {
		return rep, err
	}
	if rep.Trends, err = Trends(root, contract, now, st.ClassPopulations); err != nil {
		return rep, err
	}
	version := PassVersion(contract)
	// The retention sweep. Last of the mutating jobs, and separate from the
	// lifecycle one: it deletes the night's own paper rather than a memory, on
	// the contract's `retention:` lines, and it takes the same manifest-first
	// rule every other deletion on the axis takes.
	retain, err := PlanRetain(root, contract, now, opt.Cap)
	if err != nil {
		return rep, err
	}
	rep.Retain = retain
	if opt.Apply && len(retain.Intents) > 0 {
		// The first deletion waits for the operator: until the gate note says
		// `approved: true`, the pass lists what it would remove and removes
		// nothing (RetentionApproved).
		approved, gate, gErr := RetentionApproved(root, retain.Removed, now)
		rows := RetentionRows(retain.Intents, retain.Removed)
		var manifest string
		var mErr error
		if gErr == nil && approved {
			manifest, mErr = WriteRetentionManifest(root, runID, rows, now)
		}
		if gErr != nil || !approved {
			rep.Retain.Held, rep.Retain.Gate = retain.Removed, gate
			rep.Retain.Removed = nil
		} else if mErr != nil || manifest == "" {
			rep.Retain.Removed = nil
		} else {
			if rep.DeletionManifest == "" {
				rep.DeletionManifest = manifest
			}
			if err := applyAll(journal, root, runID, retain.Intents, now, opt.Pace, &rep); err != nil {
				return rep, err
			}
		}
	}

	// Closed tasks, before the records that name them: two weeks after a
	// task's tracker closes, its whole directory moves to its project's
	// `completed/tasks/`. Planned every night; moved only once the operator has
	// switched the mover on.
	//
	// A mover that cannot plan says so in its own row and the night goes on: the
	// jobs below it — the projects pass, reconcile, the facet — owe the morning
	// their record whatever happened here.
	tasks, err := PlanTaskMoves(root, contract, now, TaskMoveCap(contract, opt.TaskCap))
	if err != nil {
		tasks.Skipped = "the mover could not plan: " + err.Error()
		tasks.Folders, tasks.folders, tasks.Intents = nil, nil, nil
	}
	rep.Tasks = tasks
	if opt.Apply && cfg.TaskMoverEnabled && err == nil {
		if err := ApplyTaskMoves(journal, root, cfg.EngineStateDir, runID, &rep.Tasks, contract, now, opt.Pace, &rep); err != nil {
			return rep, err
		}
	}

	// The projects space: each project's activity, and what has finished.
	//
	// Read before reconcile so a record this pass moved to `completed/` is one
	// reconcile can pair rather than one it reports as vanished.
	projects := PlanProjectsFrom(root, projActivity, projSkipped)
	rep.Projects = projects
	if len(projects.Activity) > 0 {
		// Written whether or not this is an apply pass: the reading is a
		// measurement, not a mutation, and the daemon's ranking should not wait
		// on a night that happened to be reporting.
		_ = WriteActivity(cfg.EngineStateDir, projects.Activity, now)
	}
	if completed, err := PlanCompleted(root, ClosedTasks(root), DoneProjects(root), now, opt.Cap); err == nil {
		rep.Projects.Moved = append(rep.Projects.Moved, completed.Moved...)
		projects.Intents = append(projects.Intents, completed.Intents...)
	}
	if opt.Apply && len(projects.Intents) > 0 {
		if err := applyAll(journal, root, runID, projects.Intents, now, opt.Pace, &rep); err != nil {
			return rep, err
		}
	}

	// Reconcile, last, so it sees every hand move including the ones this pass
	// tripped over. It repairs the engine's record of where a note is; it writes
	// no note.
	if known, err := KnownFingerprints(cfg.EngineStateDir, root); err == nil {
		if onDisk, err := FingerprintsOnDisk(root); err == nil {
			rep.Reconcile = PlanReconcile(known, onDisk, nil, now)
		}
	}

	// The night's own record of itself, written from what it actually did.
	facet := PlanDreamingFacet(root, contract, &rep, now)
	rep.Facet = facet
	if opt.Apply && len(facet.Intents) > 0 {
		if err := applyAll(journal, root, runID, facet.Intents, now, opt.Pace, &rep); err != nil {
			return rep, err
		}
	}

	if rep.Reclassify, err = Reclassify(root, contract, version, st.LastPassVersion, ReclassifySample(contract), 0, opt.Reclassify); err != nil {
		return rep, err
	}
	if err := journal.Append(Entry{Kind: KindRunDone, RunID: runID, TS: now, Outcome: rep.Outcome}); err != nil {
		return rep, err
	}
	st.LastOutcome = rep.Outcome
	if opt.Apply {
		// Only a pass that changed the corpus moves what the next pass is
		// measured from: the clock the gate reads, the populations the trend
		// compares against, and the pass version the re-classification diff
		// keys on. A report-only pass is a diagnostic — it records its own
		// stamp and leaves all three where the last applying pass put them, so
		// running one by hand never pushes the next real pass back.
		st.LastDone = now
		st.ClassPopulations = rep.Trends.Flat()
		st.LastPassVersion = version
	} else {
		st.LastReport = now
	}
	if err := SaveState(cfg.EngineStateDir, st); err != nil {
		return rep, err
	}
	if err := SaveLastReport(cfg.EngineStateDir, rep); err != nil {
		return rep, err
	}
	return rep, nil
}

// applyAll commits intents through the journal, in order, counting the
// outcomes on the report. A lifecycle intent also lands in the governance
// journal so both layers keep one record — Commit writes that line itself,
// before the applied line, so a crash anywhere leaves a state Resolve can
// finish.
// withoutDeletions drops every deletion from a plan, leaving the rest of it to
// run. The one caller is the manifest failure above.
func withoutDeletions(intents []Intent) []Intent {
	out := intents[:0:0]
	for _, in := range intents {
		if in.Delete {
			continue
		}
		out = append(out, in)
	}
	return out
}

func applyAll(journal *Journal, root, runID string, intents []Intent, now time.Time, pace time.Duration, rep *Report) error {
	for _, in := range intents {
		rep.seq++
		id := fmt.Sprintf("%s-%04d", runID, rep.seq)
		kind, err := journal.Commit(root, runID, id, in, now)
		if err != nil {
			return err
		}
		if kind == KindSkipped {
			rep.Skipped++
			// The per-write re-check, and what it is for. The journal refuses an
			// intent whose target no longer hashes as the plan read it, which is
			// exactly a file the operator moved or edited between the plan and
			// the write. Named here so the facet can say so and the reconcile
			// step can repair it at the end of the same night.
			rep.SkippedByHandMove = append(rep.SkippedByHandMove, in.Rel)
		} else {
			rep.Applied++
		}
		if pace > 0 {
			time.Sleep(pace)
		}
	}
	return nil
}

// activity reads what happened since the last completed pass: captures from
// the index, genuine recalls from the recall history. No index at all means
// the instrument is missing, which the gate treats as active.
func activity(cfg *config.Config, st State) Activity {
	act := Activity{Since: st.LastDone}
	if st.LastDone.IsZero() {
		act.Since = time.Time{}
	}
	if cfg.IndexPath != "" {
		if _, err := os.Stat(cfg.IndexPath); err == nil {
			idx, err := index.Open(cfg.IndexPath, cfg.VaultPath, cfg.MemoryRoot, false)
			if err == nil {
				prefix := ""
				if cfg.MemoryRoot != "" {
					prefix = cfg.MemoryRoot + "/"
				}
				if n, err := idx.CapturedSince(act.Since, prefix); err == nil {
					act.Captures = n
				} else {
					act.Unknown = true
				}
				idx.Close()
			} else {
				act.Unknown = true
			}
		} else {
			act.Unknown = true
		}
	} else {
		act.Unknown = true
	}
	if n, err := RecallsSince(RecallHistoryPath(), act.Since); err == nil {
		act.Recalls = n
	}
	return act
}

func newRunID(now time.Time) string {
	var b [4]byte
	_, _ = rand.Read(b[:])
	return now.UTC().Format("20060102-150405") + "-" + hex.EncodeToString(b[:])
}

// planEntitiesFor opens the index and the operator's people table and plans
// the entity pages. No index is no plan, said rather than guessed around.
func planEntitiesFor(cfg *config.Config, root string, contract *rules.Rules, now time.Time) (EntitiesPlan, error) {
	if cfg.IndexPath == "" {
		return EntitiesPlan{Counts: map[string]int{}, Skipped: "no index is configured"}, nil
	}
	if _, err := os.Stat(cfg.IndexPath); err != nil {
		return EntitiesPlan{Counts: map[string]int{}, Skipped: "the index is missing: " + err.Error()}, nil
	}
	idx, err := index.Open(cfg.IndexPath, cfg.VaultPath, cfg.MemoryRoot, false)
	if err != nil {
		return EntitiesPlan{}, err
	}
	defer idx.Close()
	// The operator's people table. One that does not parse is a stop, not an
	// empty table: its deny lines are what keep a wrong name off a page, and
	// building without them would put back every page they took down.
	table, err := people.Load(cfg.VaultPath)
	if err != nil {
		return EntitiesPlan{}, err
	}
	// No email source ships (task 179 step 7): the switch is read so that
	// turning it on is the operator's act, and nothing reads mail until an
	// ingest supplies a source.
	return PlanEntities(root, cfg.VaultPath, idx, PeopleOptions{Table: table, EmailEnabled: cfg.PeopleEmailEvidenceEnabled},
		contract, now)
}
