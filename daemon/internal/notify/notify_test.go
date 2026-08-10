package notify

import (
	"bufio"
	"errors"
	"fmt"
	"net"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
)

var day = time.Date(2026, 8, 9, 12, 0, 0, 0, time.UTC)

func testMailer(t *testing.T) (*Mailer, *[]string) {
	t.Helper()
	sent := &[]string{}
	m := New(config.EmailConfig{To: "me@example.com", SMTPURL: "smtp://localhost:25"}, t.TempDir())
	m.send = func(_ config.EmailConfig, subject, _ string) error {
		*sent = append(*sent, subject)
		return nil
	}
	return m, sent
}

// TestTheSameProblemIsSentOnce is the anti-fatigue rule: a standing red
// condition emails once, not every fifteen minutes until the operator filters
// the sender.
func TestTheSameProblemIsSentOnce(t *testing.T) {
	m, sent := testMailer(t)
	for i := 0; i < 5; i++ {
		if _, err := m.Send(day.Add(time.Duration(i)*time.Hour), "queue-age", "red", "body"); err != nil {
			t.Fatal(err)
		}
	}
	if len(*sent) != 1 {
		t.Errorf("the same condition sent %d emails in one day, want 1", len(*sent))
	}
}

// TestANewProblemStillGetsThrough is the half of the rule that is this
// package's own. Suppressing repetition is the goal; suppressing news is the
// failure it would be easy to ship instead.
func TestANewProblemStillGetsThrough(t *testing.T) {
	m, sent := testMailer(t)
	if _, err := m.Send(day, "queue-age", "red: queue", "body"); err != nil {
		t.Fatal(err)
	}
	if _, err := m.Send(day.Add(2*time.Hour), "probe-failed+queue-age", "red: queue+probe", "body"); err != nil {
		t.Fatal(err)
	}
	if len(*sent) != 2 {
		t.Fatalf("a newly-red condition on the same day sent %d emails, want 2", len(*sent))
	}
	if !strings.Contains((*sent)[1], "probe") {
		t.Errorf("the second email was not the new condition: %q", (*sent)[1])
	}
}

func TestTheNextDayStartsOver(t *testing.T) {
	m, sent := testMailer(t)
	if _, err := m.Send(day, "queue-age", "red", "body"); err != nil {
		t.Fatal(err)
	}
	if _, err := m.Send(day.Add(24*time.Hour), "queue-age", "red", "body"); err != nil {
		t.Fatal(err)
	}
	if len(*sent) != 2 {
		t.Errorf("a condition still red the next day sent %d emails, want 2", len(*sent))
	}
}

// TestAFailedSendIsNotRecorded matters more than it looks: recording a send that
// never happened would suppress the retry and lose the alert entirely.
func TestAFailedSendIsNotRecorded(t *testing.T) {
	m, _ := testMailer(t)
	boom := errors.New("relay refused")
	m.send = func(config.EmailConfig, string, string) error { return boom }
	if _, err := m.Send(day, "queue-age", "red", "body"); !errors.Is(err, boom) {
		t.Fatalf("send error = %v, want %v", err, boom)
	}

	var delivered int
	m.send = func(config.EmailConfig, string, string) error { delivered++; return nil }
	if _, err := m.Send(day, "queue-age", "red", "body"); err != nil {
		t.Fatal(err)
	}
	if delivered != 1 {
		t.Error("the retry after a failed send was suppressed, so the alert was lost")
	}
}

func TestUnconfiguredIsASkipNotAFailure(t *testing.T) {
	m := New(config.EmailConfig{}, t.TempDir())
	if m.Configured() {
		t.Fatal("an empty config reported itself configured")
	}
	sent, err := m.Send(day, "queue-age", "red", "body")
	if sent {
		t.Error("an unconfigured mailer reported sending something")
	}
	if !errors.Is(err, ErrUnconfigured) {
		t.Errorf("err = %v, want ErrUnconfigured", err)
	}
}

func TestSenderFallsBackToTheRecipient(t *testing.T) {
	if got := (config.EmailConfig{To: "me@example.com"}).Sender(); got != "me@example.com" {
		t.Errorf("sender = %q, want the recipient", got)
	}
	cfg := config.EmailConfig{To: "me@example.com", From: "agentm@example.com"}
	if got := cfg.Sender(); got != "agentm@example.com" {
		t.Errorf("sender = %q, want the configured From", got)
	}
}

// TestHeadersCannotBeInjected: an alert detail is built from a note path and a
// threshold, and a path can contain anything. A newline reaching the Subject
// header would let the vault's own contents rewrite the operator's mail.
func TestHeadersCannotBeInjected(t *testing.T) {
	msg := message("a@x", "b@x", "red\r\nBcc: attacker@example.com", "body")
	head, _, _ := strings.Cut(msg, "\r\n\r\n")
	for _, line := range strings.Split(head, "\r\n") {
		if strings.HasPrefix(strings.ToLower(line), "bcc:") {
			t.Errorf("a newline in the subject injected a header:\n%s", head)
		}
	}
	if want := "Subject: red  Bcc: attacker@example.com"; !strings.Contains(head, want) {
		t.Errorf("the subject was not folded onto one line:\n%s", head)
	}
}

// TestALoneDotIsStuffed: SMTP ends a message at a line containing one dot, and a
// note body can contain one.
func TestALoneDotIsStuffed(t *testing.T) {
	msg := message("a@x", "b@x", "red", "first\n.\nsecond")
	_, body, _ := strings.Cut(msg, "\r\n\r\n")
	if !strings.Contains(body, "\r\n..\r\n") {
		t.Errorf("a lone dot was not stuffed, so the message would end early:\n%q", body)
	}
}

// ---------------------------------------------------------------------------
// The wire
// ---------------------------------------------------------------------------

// TestCredentialsAreNeverSentInTheClear is a security property, not a nicety.
// A relay that does not offer STARTTLS — or an attacker stripping it from the
// EHLO response, which is the classic downgrade — must not result in the
// operator's own relay password crossing the network in plaintext. Refusing to
// send is the correct answer; sending anyway is not.
func TestCredentialsAreNeverSentInTheClear(t *testing.T) {
	srv := startFakeSMTP(t, false)
	cfg := config.EmailConfig{
		To:      "me@example.com",
		SMTPURL: "smtp://user:hunter2@" + srv.addr,
	}
	err := sendSMTP(cfg, "red", "body")
	if !errors.Is(err, ErrInsecureAuth) {
		t.Fatalf("err = %v, want ErrInsecureAuth", err)
	}
	for _, line := range srv.received() {
		if strings.HasPrefix(strings.ToUpper(line), "AUTH") {
			t.Fatalf("the daemon sent an AUTH command over a plaintext connection: %q", line)
		}
	}
}

// TestAnUnauthenticatedRelayStillWorks: a local or on-device relay that needs no
// credential has none to protect, and refusing to talk to it would break the
// only mail path some machines have.
func TestAnUnauthenticatedRelayStillWorks(t *testing.T) {
	srv := startFakeSMTP(t, false)
	cfg := config.EmailConfig{To: "me@example.com", SMTPURL: "smtp://" + srv.addr}
	if err := sendSMTP(cfg, "queue is red", "the oldest unfiled item is 4d old"); err != nil {
		t.Fatalf("sending through an unauthenticated relay: %v", err)
	}
	transcript := strings.Join(srv.received(), "\n")
	for _, want := range []string{"MAIL FROM:<me@example.com>", "RCPT TO:<me@example.com>", "DATA"} {
		if !strings.Contains(transcript, want) {
			t.Errorf("the relay never saw %q:\n%s", want, transcript)
		}
	}
	if !strings.Contains(transcript, "the oldest unfiled item is 4d old") {
		t.Errorf("the message body never reached the relay:\n%s", transcript)
	}
}

func TestAMissingHostIsRefusedNotDialled(t *testing.T) {
	err := sendSMTP(config.EmailConfig{To: "me@x", SMTPURL: "smtp://"}, "s", "b")
	if err == nil {
		t.Fatal("an SMTP URL naming no host was accepted")
	}
}

// TestTheRelayPasswordNeverReachesAnErrorMessage.
//
// The SMTP URL carries the operator's relay credential in its userinfo. An
// error that echoes the URL puts that password wherever the error goes — a log
// file, a status paste, a bug report. `url.Parse` embeds the string it failed
// on, so the naive wrap leaks by default.
func TestTheRelayPasswordNeverReachesAnErrorMessage(t *testing.T) {
	const secret = "hunter2-the-actual-password"
	for _, url := range []string{
		"://" + secret + "@broken",           // unparseable
		"smtp://user:" + secret + "@",        // parses, names no host
		"smtp://user:" + secret + "@ho st:1", // invalid host
	} {
		err := sendSMTP(config.EmailConfig{To: "me@x", SMTPURL: url}, "s", "b")
		if err == nil {
			t.Errorf("%q was accepted", url)
			continue
		}
		if strings.Contains(err.Error(), secret) {
			t.Errorf("the relay password reached an error message: %v", err)
		}
	}
}

// fakeSMTP is the smallest server that lets a Go smtp.Client complete a
// transaction. It records every command line so a test can assert on what was
// and was not sent.
type fakeSMTP struct {
	addr string
	mu   sync.Mutex
	log  []string
}

func (f *fakeSMTP) received() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]string(nil), f.log...)
}

func (f *fakeSMTP) record(line string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.log = append(f.log, line)
}

func startFakeSMTP(t *testing.T, offerSTARTTLS bool) *fakeSMTP {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { ln.Close() })
	f := &fakeSMTP{addr: ln.Addr().String()}

	go func() {
		for {
			conn, err := ln.Accept()
			if err != nil {
				return
			}
			go f.serve(conn, offerSTARTTLS)
		}
	}()
	return f
}

func (f *fakeSMTP) serve(conn net.Conn, offerSTARTTLS bool) {
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(10 * time.Second))
	r := bufio.NewReader(conn)
	fmt.Fprint(conn, "220 fake ESMTP\r\n")

	inData := false
	for {
		line, err := r.ReadString('\n')
		if err != nil {
			return
		}
		line = strings.TrimRight(line, "\r\n")
		f.record(line)

		if inData {
			if line == "." {
				inData = false
				fmt.Fprint(conn, "250 OK\r\n")
			}
			continue
		}

		upper := strings.ToUpper(line)
		switch {
		case strings.HasPrefix(upper, "EHLO"), strings.HasPrefix(upper, "HELO"):
			if offerSTARTTLS {
				fmt.Fprint(conn, "250-fake\r\n250 STARTTLS\r\n")
			} else {
				fmt.Fprint(conn, "250-fake\r\n250 8BITMIME\r\n")
			}
		case strings.HasPrefix(upper, "DATA"):
			inData = true
			fmt.Fprint(conn, "354 go ahead\r\n")
		case strings.HasPrefix(upper, "QUIT"):
			fmt.Fprint(conn, "221 bye\r\n")
			return
		default:
			fmt.Fprint(conn, "250 OK\r\n")
		}
	}
}
