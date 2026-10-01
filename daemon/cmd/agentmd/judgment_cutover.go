package main

import (
	"bufio"
	"bytes"
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/enrich"
	"github.com/alexherrero/agentm/daemon/internal/ledger"
	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// The judgment-hash cutover (task 181 step 3, #784).
//
// A judgment used to be keyed to the whole filing contract's hash, and is now
// keyed to the judgment hash, the part of the contract a judgment reads. Every
// ledger row, refusal and stamp written before carries the old kind of hash,
// and so does every key built on it. Left alone, none of them would match
// again, and the night would re-judge the whole corpus: the mass re-judgment
// this task exists to prevent.
//
// So the first open of a ledger translates it in place. Each contract the
// vault has kept is read back out of its git history and parsed, which gives
// a map from every old contract hash to the judgment hash of the same text. A
// row under a mapped hash takes the judgment hash, and its key is rebuilt from
// the note as it stands now: a key that matched the note under the old hash
// matches it under the new one, and a key that did not match stays unmatched,
// so the cutover changes no decision except the ones the old hash got wrong.
// A row whose contract cannot be found — a contract edited by hand and never
// committed — keeps its hash and reads as stale, which costs a judgment and is
// never wrong. No note file is written.

// judgmentCutover is the ledger mark the cutover leaves.
const judgmentCutover = "judgment-hash-cutover"

// contractVersion is one text the contract has had, and its two hashes.
type contractVersion struct {
	Commit       string `json:"commit,omitempty"`
	Date         string `json:"date,omitempty"`
	Hash         string `json:"rules_hash"`
	JudgmentHash string `json:"judgment_hash"`
	// Source says where the text came from: the live file, the packaged
	// default, or a commit.
	Source string `json:"source"`
}

// contractHistory is every version of the contract this machine can still
// read: the one in force, the packaged default, and each committed text of the
// live file, newest first. A text today's parser refuses is counted, not
// listed: no judgment hash can be given for it.
func contractHistory(ctx context.Context, loaded *rules.Rules) ([]contractVersion, int, error) {
	var out []contractVersion
	out = append(out, contractVersion{Hash: loaded.Hash,
		JudgmentHash: loaded.JudgmentHash, Source: loaded.Source})
	if def, err := rules.ParseText(rules.Default(), rules.PackagedDefaultSource); err == nil {
		out = append(out, contractVersion{Hash: def.Hash,
			JudgmentHash: def.JudgmentHash, Source: rules.PackagedDefaultSource})
	}
	if loaded.IsPackagedDefault || loaded.Source == "" {
		return out, 0, nil
	}
	commits, err := gitFileHistory(ctx, loaded.Source)
	if err != nil {
		// No history is a smaller map, not a failure: the rows under the live
		// and default contracts still translate.
		return out, 0, err
	}
	refused := 0
	for _, c := range commits {
		r, err := rules.ParseText(c.text, c.commit)
		if err != nil {
			refused++
			continue
		}
		out = append(out, contractVersion{Commit: c.commit, Date: c.date,
			Hash: r.Hash, JudgmentHash: r.JudgmentHash, Source: c.path})
	}
	return out, refused, nil
}

type committedText struct{ commit, date, path, text string }

// gitFileHistory reads every committed text of one file, following renames.
func gitFileHistory(ctx context.Context, file string) ([]committedText, error) {
	ctx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	dir := filepath.Dir(file)
	log := exec.CommandContext(ctx, "git", "-C", dir, "log", "--follow",
		"--format=%H%x09%cs", "--name-only", "--", filepath.Base(file))
	raw, err := log.Output()
	if err != nil {
		return nil, fmt.Errorf("reading the contract's history: %w", err)
	}
	var out []committedText
	var cur *committedText
	sc := bufio.NewScanner(bytes.NewReader(raw))
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		switch {
		case line == "":
		case strings.Contains(line, "\t"):
			parts := strings.SplitN(line, "\t", 2)
			out = append(out, committedText{commit: parts[0], date: parts[1]})
			cur = &out[len(out)-1]
		case cur != nil && cur.path == "":
			cur.path = line
		}
	}
	kept := out[:0]
	for _, c := range out {
		if c.path == "" {
			continue
		}
		show := exec.CommandContext(ctx, "git", "-C", dir, "show", c.commit+":"+c.path)
		text, err := show.Output()
		if err != nil {
			// A rename's far side the follow could not name, or a deletion.
			continue
		}
		c.text = string(text)
		kept = append(kept, c)
	}
	return kept, nil
}

// judgmentMap maps each contract hash to its judgment hash.
func judgmentMap(versions []contractVersion) map[string]string {
	m := map[string]string{}
	for _, v := range versions {
		m[v.Hash] = v.JudgmentHash
	}
	return m
}

// cutoverJudgmentHash translates a ledger, and the refusal record beside it,
// from contract hashes to judgment hashes, once per ledger file.
func cutoverJudgmentHash(ctx context.Context, cfg *config.Config, led *ledger.Ledger,
	log io.Writer) error {
	if _, done, err := led.Marked(ctx, judgmentCutover); err != nil || done {
		return err
	}
	loaded, err := cfg.Rules.Get()
	if err != nil {
		// The contract does not parse. Nothing can be translated, and nothing
		// will be judged until it parses either; the next open tries again.
		fmt.Fprintf(log, "ledger: judgment-hash cutover waits: the filing "+
			"contract does not parse: %v\n", err)
		return nil
	}
	versions, refused, herr := contractHistory(ctx, loaded)
	if herr != nil {
		fmt.Fprintf(log, "ledger: %v — translating under the live and default "+
			"contracts only\n", herr)
	}
	m := judgmentMap(versions)
	bodies := map[string]string{}
	body := func(rel string) (string, bool) {
		if b, ok := bodies[rel]; ok {
			return b, b != ""
		}
		raw, err := os.ReadFile(filepath.Join(cfg.VaultPath, filepath.FromSlash(rel)))
		bodies[rel] = string(raw)
		return string(raw), err == nil
	}
	// translate re-keys one key: the note's content under the old hash, if
	// that is what the key holds, becomes the same content under the new one.
	translate := func(key, rel, version, from, to string) string {
		if key == "" {
			return key
		}
		b, ok := body(rel)
		if !ok {
			return key
		}
		if key == (&enrich.Fingerprint{Version: version, RulesHash: from}).Key(b) {
			return (&enrich.Fingerprint{Version: version, RulesHash: to}).Key(b)
		}
		return key
	}

	var rekeyed, unmapped int
	n, err := led.Retag(ctx, ledger.StageEnrich, func(e ledger.Entry) (ledger.Entry, bool) {
		j, ok := m[e.RulesHash]
		if !ok {
			if e.RulesHash != "" {
				unmapped++
			}
			return e, false
		}
		if j == e.RulesHash {
			return e, false
		}
		in := translate(e.InputKey, e.Target, e.Version, e.RulesHash, j)
		out := translate(e.OutputKey, e.Target, e.Version, e.RulesHash, j)
		if in != e.InputKey || out != e.OutputKey {
			rekeyed++
		}
		e.InputKey, e.OutputKey, e.RulesHash = in, out, j
		return e, true
	})
	if err != nil {
		return err
	}

	refusals, rerr := enrich.NewRefusals(enrichStateDir(cfg))
	retagged := 0
	if rerr == nil {
		retagged = refusals.Retag(func(r enrich.Refusal) (enrich.Refusal, bool) {
			j, ok := m[r.RulesHash]
			if !ok || j == r.RulesHash {
				return r, false
			}
			r.Key = translate(r.Key, r.Rel, r.Version, r.RulesHash, j)
			r.RulesHash = j
			return r, true
		})
		if retagged > 0 {
			if err := refusals.Compact(); err != nil {
				return fmt.Errorf("ledger: writing the translated refusals: %w", err)
			}
		}
	}

	fmt.Fprintf(log, "ledger: judgment-hash cutover: %d row(s) translated, %d of them "+
		"re-keyed to the note as it stands; %d left stale under a contract no longer "+
		"on record; %d refusal(s) translated; %d contract version(s) read",
		n, rekeyed, unmapped, retagged, len(versions))
	if refused > 0 {
		fmt.Fprintf(log, " (%d committed text(s) today's parser refuses)", refused)
	}
	fmt.Fprintln(log)
	return led.Mark(ctx, judgmentCutover, time.Now().UTC().Format(time.RFC3339))
}
