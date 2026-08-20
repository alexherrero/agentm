package health

import (
	"strings"
	"testing"
	"time"
)

// baseInput is a daemon with nothing else wrong, so a test that goes red goes
// red for the reason it is about.
func baseInput() Input {
	now := time.Now()
	return Input{
		Now:           now,
		Uptime:        time.Hour,
		GitAvailable:  true,
		Documents:     100,
		LastReconcile: now.Add(-time.Minute),
		Baseline:      now.Add(-24 * time.Hour),
		Probe:         ProbeState{OK: true, Recorded: true},
		ProbeAt:       now.Add(-time.Hour),
		Thresholds: Thresholds{
			UnfiledAge:   48 * time.Hour,
			UnfiledCount: 500,
			IndexStale:   time.Hour,
			ProbeStale:   48 * time.Hour,
		},
		Contract: Contract{State: ContractHealthy, Source: "/v/standards/storage-rules.md",
			Hash: "abc123"},
	}
}

// The condition this whole component exists for: everything else looks fine.
// Search does not read the taxonomy and ambient capture supplies no type, so the
// two loudest surfaces stay green while filing has stopped. If health does not
// say so, nothing does.
func TestABrokenContractGoesRed(t *testing.T) {
	in := baseInput()
	in.Contract = Contract{
		State:  ContractBroken,
		Detail: "the rules block is not valid YAML: did not find expected ',' or ']'",
	}
	rep := Evaluate(in)

	if !rep.Red() {
		t.Fatalf("level = %q; a halted filing pipeline is not OK", rep.Level)
	}
	var found *Alert
	for i := range rep.Alerts {
		if rep.Alerts[i].Code == AlertContract {
			found = &rep.Alerts[i]
		}
	}
	if found == nil {
		t.Fatal("no filing-contract alert raised")
	}
	if !strings.Contains(found.Detail, "not valid YAML") {
		t.Errorf("the alert does not carry the parse error: %q", found.Detail)
	}
	if !strings.Contains(found.Detail, "nothing is being filed") {
		t.Errorf("the alert does not say what stopped: %q", found.Detail)
	}
}

// The quietest symptom, made countable. A client failing every write is worse
// than a stalled queue and looks like nothing at all.
func TestRefusedCapturesRideTheAlert(t *testing.T) {
	in := baseInput()
	in.Contract = Contract{State: ContractBroken, Detail: "unparseable", RefusedCaptures: 17}
	rep := Evaluate(in)

	for _, a := range rep.Alerts {
		if a.Code == AlertContract {
			if !strings.Contains(a.Detail, "17 capture(s)") {
				t.Errorf("the refused count is not in the alert: %q", a.Detail)
			}
			return
		}
	}
	t.Fatal("no filing-contract alert raised")
}

func TestAHealthyContractRaisesNothing(t *testing.T) {
	rep := Evaluate(baseInput())
	for _, a := range rep.Alerts {
		if a.Code == AlertContract {
			t.Fatalf("a healthy contract raised an alert: %q", a.Detail)
		}
	}
	if rep.Red() {
		t.Errorf("level = %q with nothing wrong; alerts: %v", rep.Level, rep.Alerts)
	}
}

// Running on the embedded default is a legitimate fresh install, not a fault.
// It is reported — an operator whose vault should have a rules file needs to know
// their edits are going nowhere — but it does not page.
func TestTheEmbeddedDefaultIsReportedAndNotPaged(t *testing.T) {
	in := baseInput()
	in.Contract = Contract{State: ContractDefault, Hash: "abc123"}
	rep := Evaluate(in)

	for _, a := range rep.Alerts {
		if a.Code == AlertContract {
			t.Fatalf("the embedded default paged: %q", a.Detail)
		}
	}
	if rep.Contract.State != ContractDefault {
		t.Errorf("the state was not reported: %q", rep.Contract.State)
	}
	if !strings.Contains(rep.Contract.String(), "will not take effect") {
		t.Errorf("the one-liner does not say what it costs: %q", rep.Contract.String())
	}
}

func TestFilingReportsWhetherAnythingCanBeFiled(t *testing.T) {
	for _, tc := range []struct {
		state string
		want  bool
	}{
		{ContractHealthy, true},
		{ContractDefault, true},
		{ContractBroken, false},
	} {
		if got := (Contract{State: tc.state}).Filing(); got != tc.want {
			t.Errorf("Filing() with state %q = %v, want %v", tc.state, got, tc.want)
		}
	}
}

// A standing problem must not be emailed twice while a genuinely new one still
// is, so the contract has to participate in the fingerprint like anything else.
func TestTheContractIsPartOfTheAlertFingerprint(t *testing.T) {
	clean := Evaluate(baseInput())

	in := baseInput()
	in.Contract = Contract{State: ContractBroken, Detail: "unparseable"}
	broken := Evaluate(in)

	if broken.Fingerprint() == clean.Fingerprint() {
		t.Error("a broken contract does not change the fingerprint, so it would " +
			"be suppressed behind whatever was already red")
	}
	if !strings.Contains(broken.Fingerprint(), AlertContract) {
		t.Errorf("fingerprint %q does not name the contract alert", broken.Fingerprint())
	}
}
