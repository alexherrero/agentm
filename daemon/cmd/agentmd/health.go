package main

import (
	"context"
	"fmt"
	"log/slog"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/health"
	"github.com/alexherrero/agentm/daemon/internal/notify"
	"github.com/alexherrero/agentm/daemon/internal/probe"
)

// thresholds derives what turns the daemon red from what it was configured
// with.
//
// Two of the four are derived rather than configured, because they are not
// independent judgements. Index staleness asks whether the reconcile loop is
// still turning, and only that loop's own period can say. Probe staleness asks
// whether the prover is still running, and one missed daily run is a blip while
// two is a stopped scheduler.
func thresholds(cfg *config.Config) health.Thresholds {
	return health.Thresholds{
		UnfiledAge:   cfg.UnfiledAgeRed,
		UnfiledCount: cfg.UnfiledCountRed,
		IndexStale:   3 * cfg.ReconcileEvery,
		ProbeStale:   2 * cfg.ProbeEvery,
		ProbeBudget:  cfg.ProbeBudget,
	}
}

// watchHealth is the loud half of the loud queue.
//
// On each tick it runs the self-probe if it is due, evaluates the thresholds,
// and — when the result is red — says so in the log and emails the operator.
// The first tick is one interval after startup rather than immediate: the probe
// writes a note and a daemon that probed on every restart would write one per
// restart, which is a strange thing for a health check to do to the vault it is
// checking.
func watchHealth(
	ctx context.Context, cfg *config.Config, log *slog.Logger,
	prober *probe.Runner, report func() health.Report,
) {
	mailer := notify.New(cfg.Email, cfg.StateDir)
	if !mailer.Configured() {
		// Absent-by-default, and said once at startup rather than discovered the
		// first time something goes red at 3am with nowhere to go.
		log.Info("alert email is not configured; red status will be logged only",
			"configure", "plugins.autonomy.email_to + plugins.autonomy.email_smtp_url")
	}

	ticker := time.NewTicker(cfg.HealthEvery)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}

		if prober.Due(time.Now()) {
			st, err := prober.Run(time.Now())
			if err != nil {
				log.Error("self-probe FAILED", "detail", st.Detail, "path", st.Path)
			} else {
				log.Info("self-probe ok", "elapsed", st.Elapsed, "path", st.Path)
			}
		}

		rep := report()
		if !rep.Red() {
			continue
		}
		for _, a := range rep.Alerts {
			log.Warn("RED", "code", a.Code, "detail", a.Detail)
		}
		if !mailer.Configured() {
			continue
		}
		sent, err := mailer.Send(time.Now(), rep.Fingerprint(), alertSubject(rep), renderReport(rep, cfg))
		switch {
		case err != nil:
			// A failed send is itself worth a line. The whole point of this
			// channel is reaching someone who is not watching the log, and a
			// silent failure to do that is the failure mode it exists to fix.
			log.Error("could not send the alert email", "err", err)
		case sent:
			log.Info("alert email sent", "to", cfg.Email.To, "conditions", rep.Fingerprint())
		}
	}
}

func alertSubject(rep health.Report) string {
	codes := make([]string, 0, len(rep.Alerts))
	for _, a := range rep.Alerts {
		codes = append(codes, a.Code)
	}
	return "[agentm] memory daemon is red: " + strings.Join(codes, ", ")
}

// renderReport is the status surface, for a person. It is what `agentmd status`
// prints and what the alert email carries, deliberately the same text: an alert
// that reads differently from the command you run to check on it makes the
// reader reconcile two descriptions of one machine.
func renderReport(rep health.Report, cfg *config.Config) string {
	var b strings.Builder

	fmt.Fprintf(&b, "%s\n", strings.ToUpper(rep.Level))
	fmt.Fprintf(&b, "  vault    %s\n", cfg.VaultPath)

	queue := fmt.Sprintf("%d unfiled", rep.Queue.Unfiled)
	if rep.Queue.Since > 0 && rep.Queue.OldestAge > 0 {
		queue += fmt.Sprintf(" · oldest %s old", rep.Queue.OldestAge)
	}
	fmt.Fprintf(&b, "  queue    %-44s (red past %s old, or %d items)\n",
		queue, health.Duration(rep.Thresholds.UnfiledAge), rep.Thresholds.UnfiledCount)
	// The inherited backlog, on its own line, every time. It is excluded from
	// what pages and never from what is reported — the previous system's sin was
	// hiding a pile, not declining to shout about one.
	if rep.Queue.Inherited > 0 {
		fmt.Fprintf(&b, "           of which %d inherited (captured before %s, oldest %s) — "+
			"reported, not paged about\n",
			rep.Queue.Inherited, shortDate(rep.Queue.Baseline), rep.Queue.InheritedOldestAge)
	}

	index := fmt.Sprintf("%d documents", rep.Index.Documents)
	if rep.Index.LastAt == "" {
		index += " · no reconcile pass yet"
	} else {
		index += fmt.Sprintf(" · last pass %s ago", rep.Index.Age)
	}
	if !rep.Index.Fresh {
		index += " · STALE"
	}
	if rep.Index.Errors > 0 {
		index += fmt.Sprintf(" · %d errors", rep.Index.Errors)
	}
	fmt.Fprintf(&b, "  index    %-44s (red past %s)\n",
		index, health.Duration(rep.Thresholds.IndexStale))

	fmt.Fprintf(&b, "  git      %s\n", rep.Git)
	if !rep.Git.Healthy() {
		b.WriteString("           no undo for a bad write, and `agentmd gate corpus-write` refuses\n")
	}

	fmt.Fprintf(&b, "  embedder %s\n", rep.Embedder)
	if rep.Embedder.State == "degraded" {
		// The same shape as the git line above: a degraded capability says what
		// stops working, because "degraded" on its own is a word a reader has to
		// go and interpret at exactly the moment they are least able to.
		b.WriteString("           searches still answer, from the lexical arm only — " +
			"paraphrases and vocabulary gaps will miss\n")
	}

	fmt.Fprintf(&b, "  probe    %s\n", describeProbe(rep))

	if len(rep.Alerts) > 0 {
		b.WriteString("\nwhat needs you:\n")
		for _, a := range rep.Alerts {
			fmt.Fprintf(&b, "  · %s — %s\n", a.Code, a.Detail)
		}
	}
	return b.String()
}

// shortDate trims an RFC3339 stamp to the day, which is the resolution anyone
// reads a baseline at.
func shortDate(s string) string {
	if len(s) >= 10 {
		return s[:10]
	}
	if s == "" {
		return "the daemon's first run"
	}
	return s
}

func describeProbe(rep health.Report) string {
	switch {
	case !rep.Probe.Recorded:
		return "never run"
	case !rep.Probe.OK:
		return fmt.Sprintf("FAILED at %s — %s", rep.Probe.At, rep.Probe.Detail)
	default:
		out := fmt.Sprintf("ok %s ago (round trip %s)", rep.Probe.Age, rep.Probe.Elapsed)
		if rep.Probe.Path != "" {
			out += " · " + rep.Probe.Path
		}
		return out
	}
}
