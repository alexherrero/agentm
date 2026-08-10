// Package notify is how a red status reaches the operator when he is not
// looking at a terminal.
//
// It rides the mail path he already configured — the same `plugins.autonomy.*`
// keys `scripts/health/session_email.py` reads — because there should be one
// place mail is configured and a second dialect of it would be a second thing to
// get wrong. Absent that configuration the channel is a silent no-op, and the
// status surface carries delivery on its own.
//
// Two contracts inherited from the Python channels, and one that is this
// package's own. It never blocks the daemon and never propagates a send failure
// into the loop it rides. It sends at most once per calendar day for the same
// set of red conditions. And — the one that is not inherited — a *different* set
// of conditions on the same day sends again, because the anti-fatigue rule
// exists to stop repetition, not to swallow news.
package notify

import (
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"mime"
	"net"
	"net/smtp"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
)

// ErrUnconfigured means no mail path is set up. It is a skip, not a failure —
// the operator has not asked for email, so not sending one is correct behaviour.
var ErrUnconfigured = errors.New("no email channel configured (plugins.autonomy.email_to + email_smtp_url)")

// ErrInsecureAuth is the refusal to hand credentials to a connection that never
// negotiated TLS. It is the STARTTLS-stripping downgrade, and the answer is not
// to send.
var ErrInsecureAuth = errors.New(
	"the relay offers no STARTTLS and the URL carries a password; refusing to send " +
		"credentials over an unencrypted connection")

// Mailer sends alerts and remembers what it already said.
type Mailer struct {
	cfg       config.EmailConfig
	statePath string
	// send is the transport, swappable so the anti-fatigue logic can be tested
	// without a relay. Production always uses sendSMTP.
	send func(cfg config.EmailConfig, subject, body string) error
}

// New builds a mailer writing its anti-fatigue record into stateDir.
func New(cfg config.EmailConfig, stateDir string) *Mailer {
	return &Mailer{
		cfg:       cfg,
		statePath: filepath.Join(stateDir, "alert-state.json"),
		send:      sendSMTP,
	}
}

// Configured reports whether mail can actually go out.
func (m *Mailer) Configured() bool { return m.cfg.Configured() }

// StatePath is where the anti-fatigue record lives, for reporting.
func (m *Mailer) StatePath() string { return m.statePath }

type state struct {
	Date string   `json:"date"`
	Sent []string `json:"sent"`
}

// Send delivers one alert unless it has already been delivered today.
//
// `fingerprint` identifies the set of conditions; the same fingerprint on the
// same calendar day is suppressed. Returns whether anything was sent.
func (m *Mailer) Send(now time.Time, fingerprint, subject, body string) (bool, error) {
	if !m.cfg.Configured() {
		return false, ErrUnconfigured
	}
	today := now.UTC().Format("2006-01-02")
	st := m.load()
	if st.Date == today {
		for _, seen := range st.Sent {
			if seen == fingerprint {
				return false, nil
			}
		}
	} else {
		st = state{Date: today}
	}

	if err := m.send(m.cfg, subject, body); err != nil {
		return false, err
	}

	st.Sent = append(st.Sent, fingerprint)
	// A day's distinct fingerprints are bounded by the alert vocabulary, but the
	// record is written by a long-lived process and a cap costs nothing.
	if len(st.Sent) > 64 {
		st.Sent = st.Sent[len(st.Sent)-64:]
	}
	m.save(st)
	return true, nil
}

func (m *Mailer) load() state {
	var st state
	blob, err := os.ReadFile(m.statePath)
	if err != nil {
		return st
	}
	if err := json.Unmarshal(blob, &st); err != nil {
		// A corrupt record means one duplicate alert, which is strictly better
		// than a swallowed one.
		return state{}
	}
	return st
}

func (m *Mailer) save(st state) {
	blob, err := json.Marshal(st)
	if err != nil {
		return
	}
	if err := os.MkdirAll(filepath.Dir(m.statePath), 0o755); err != nil {
		return
	}
	tmp := m.statePath + ".tmp"
	if err := os.WriteFile(tmp, append(blob, '\n'), 0o600); err != nil {
		return
	}
	_ = os.Rename(tmp, m.statePath)
}

// ---------------------------------------------------------------------------
// SMTP
// ---------------------------------------------------------------------------

const dialTimeout = 15 * time.Second

// sendSMTP delivers one message through the operator's own relay.
//
// The URL is `smtp://[user[:password]@]host[:port]`, and the credentials in it
// are his own, for a relay he holds — this never talks to anything the URL does
// not name. Port 465 (or an `smtps` scheme) is implicit TLS; anything else
// attempts STARTTLS and, if the URL carries a password, refuses to send when
// STARTTLS is unavailable rather than falling back to a plaintext login. That
// refusal is the whole point: a network attacker who strips STARTTLS from the
// EHLO response otherwise harvests the password from a daemon that thought it
// was being helpful.
func sendSMTP(cfg config.EmailConfig, subject, body string) error {
	// The URL carries the operator's relay password in its userinfo, so it never
	// appears in an error. `url.Parse` embeds the string it failed on, and a `%q`
	// of the URL would print the credential outright — either one puts a password
	// into a log file that a status surface, a support paste, or a bug report
	// then carries somewhere else.
	u, err := url.Parse(cfg.SMTPURL)
	if err != nil {
		return errors.New(
			"plugins.autonomy.email_smtp_url is not a URL I can parse " +
				"(expected smtp://[user[:password]@]host[:port]); the value is not " +
				"echoed here because it carries a password")
	}
	host := u.Hostname()
	if host == "" {
		return errors.New("plugins.autonomy.email_smtp_url names no host")
	}
	port := u.Port()
	if port == "" {
		port = "25"
		if u.Scheme == "smtps" {
			port = "465"
		}
	}
	username := u.User.Username()
	password, hasPassword := u.User.Password()

	sender := cfg.Sender()
	msg := message(sender, cfg.To, subject, body)

	addr := net.JoinHostPort(host, port)
	implicitTLS := port == "465" || u.Scheme == "smtps"

	var conn net.Conn
	if implicitTLS {
		conn, err = tls.DialWithDialer(
			&net.Dialer{Timeout: dialTimeout}, "tcp", addr, &tls.Config{ServerName: host})
	} else {
		conn, err = net.DialTimeout("tcp", addr, dialTimeout)
	}
	if err != nil {
		return fmt.Errorf("connecting to %s: %w", addr, err)
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(dialTimeout))

	c, err := smtp.NewClient(conn, host)
	if err != nil {
		return fmt.Errorf("smtp handshake with %s: %w", addr, err)
	}
	defer c.Close()

	secure := implicitTLS
	if !implicitTLS {
		if ok, _ := c.Extension("STARTTLS"); ok {
			if err := c.StartTLS(&tls.Config{ServerName: host}); err != nil {
				return fmt.Errorf("starttls: %w", err)
			}
			secure = true
		}
	}
	if hasPassword && password != "" {
		if !secure {
			return ErrInsecureAuth
		}
		if username == "" {
			username = sender
		}
		if err := c.Auth(smtp.PlainAuth("", username, password, host)); err != nil {
			return fmt.Errorf("smtp auth: %w", err)
		}
	}

	if err := c.Mail(sender); err != nil {
		return fmt.Errorf("MAIL FROM %s: %w", sender, err)
	}
	if err := c.Rcpt(cfg.To); err != nil {
		return fmt.Errorf("RCPT TO %s: %w", cfg.To, err)
	}
	w, err := c.Data()
	if err != nil {
		return fmt.Errorf("DATA: %w", err)
	}
	if _, err := w.Write([]byte(msg)); err != nil {
		w.Close()
		return fmt.Errorf("writing message: %w", err)
	}
	if err := w.Close(); err != nil {
		return fmt.Errorf("closing message: %w", err)
	}
	return c.Quit()
}

// message renders the wire form. Headers are encoded and the subject is
// scrubbed of line breaks, because an alert detail carrying a newline would
// otherwise inject headers into the operator's own mail.
func message(from, to, subject, body string) string {
	var b strings.Builder
	fmt.Fprintf(&b, "From: %s\r\n", scrubHeader(from))
	fmt.Fprintf(&b, "To: %s\r\n", scrubHeader(to))
	fmt.Fprintf(&b, "Subject: %s\r\n", mime.QEncoding.Encode("utf-8", scrubHeader(subject)))
	fmt.Fprintf(&b, "Date: %s\r\n", time.Now().Format(time.RFC1123Z))
	b.WriteString("MIME-Version: 1.0\r\n")
	b.WriteString("Content-Type: text/plain; charset=utf-8\r\n")
	b.WriteString("\r\n")
	// Dot-stuffing: a line that is a single dot would otherwise end the message.
	for _, line := range strings.Split(strings.ReplaceAll(body, "\r\n", "\n"), "\n") {
		if strings.HasPrefix(line, ".") {
			line = "." + line
		}
		b.WriteString(line)
		b.WriteString("\r\n")
	}
	return b.String()
}

func scrubHeader(s string) string {
	return strings.TrimSpace(strings.NewReplacer("\r", " ", "\n", " ").Replace(s))
}
