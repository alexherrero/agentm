#!/usr/bin/env python3
"""corpus_gate — ask `agentmd gate corpus-write` before rewriting the corpus.

The contract, from `wiki/designs/agentm-rescope-topology.md`: **no corpus-wide
write job — migration, backfill, reclassification, dreaming's future drain —
may start unless the gate passes.** The job asks; nobody has to remember.

The gate itself lives in the daemon (`daemon/internal/gate`), which is what
knows whether the vault is a repository and whether its worktree is clean. This
module is only the call: run the binary, relay its own explanation, and stop the
job when it says no.

**Fails closed on every path.** A refusal stops the run; so does a gate that
could not answer, and so does a missing binary. "I could not check" is not "it
is fine" — what is being checked is whether an undo exists at all, which is why
there is deliberately no `--force`.

The message is the daemon's, verbatim, rather than reworded here. One gate, one
wording, one thing to keep true.

**This file is vendored.** `scripts/corpus_gate.py` is canonical and
`harness/skills/memory/scripts/corpus_gate.py` is a byte-identical copy, kept
that way by `check-vendored-parity.sh corpus-gate`. The copy exists because the
LC-8 bridge rule forbids kernel toolkit scripts importing back into `scripts/`
— the same reason `vault_lock.py` is vendored the same way. Edit the canonical
copy and re-run `bash scripts/check-vendored-parity.sh corpus-gate`.
"""

from __future__ import annotations

import subprocess
import sys

GATE_NAME = "corpus-write"

# The daemon's exit codes, which are the contract a caller branches on.
EXIT_PASS = 0
EXIT_REFUSED = 3


def require_corpus_write_gate(
    agentmd: str = "agentmd",
    vault: str | None = None,
    *,
    stderr=sys.stderr,
) -> None:
    """Stop the caller unless the corpus-write gate passes.

    Raises SystemExit on refusal, on an undecidable gate, and on a missing
    binary. Returns None — and prints the undo point — when the job may start.
    """
    cmd = [agentmd, "gate", GATE_NAME]
    if vault:
        cmd += ["--vault", str(vault)]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as exc:
        raise SystemExit(
            f"could not run `{' '.join(cmd)}` ({exc}).\n"
            "No corpus-wide write job starts without the gate. Install the daemon "
            "(see wiki/reference/Memory-Daemon.md) or point at it with --agentmd."
        ) from exc

    explanation = (proc.stdout or "").strip() or (proc.stderr or "").strip()

    if proc.returncode == EXIT_PASS:
        # The exit code is the verdict and this module does not re-derive it —
        # a second opinion here is a second dialect of the gate, and the two
        # would eventually disagree about whether an undo exists.
        #
        # What is checked is that we talked to the gate at all. `agentmd` is a
        # bare name resolved through PATH, so a collision with some unrelated
        # program that exits 0 would otherwise read as permission. Naming the
        # gate is something only the gate does.
        if GATE_NAME not in explanation:
            raise SystemExit(
                f"`{' '.join(cmd)}` exited 0 without naming the {GATE_NAME} gate, so "
                "this is not the gate answering:\n"
                f"{explanation or '(no output)'}\n\n"
                "Refusing rather than reading an unrecognized success as permission."
            )
        print(explanation, file=stderr)
        return

    detail = explanation or f"the gate exited {proc.returncode} and said nothing"
    if proc.returncode != EXIT_REFUSED:
        detail += (
            f"\n\nThe gate could not decide (exit {proc.returncode}), which is also a "
            "refusal — a gate that cannot answer must fail closed."
        )
    raise SystemExit(detail)
