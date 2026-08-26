#!/usr/bin/env python3
"""Mail the corpus health scorecard through the existing notify seam.

The vault file is the artifact. Delivery is a convenience on top of it, and
nothing here writes, moves or needs the scorecard to have been mailed — a run
with no mail configured has still done its whole job.

Placed beside `session_email` rather than next to the scorecard that produces it.
The scorecard lives under `harness/skills/memory/scripts/`, and a script there
importing from `scripts/` is a forbidden back-edge that `check-one-way-imports`
refuses — measured, when the scorecard itself tried to reach across for a vault
path.

# Three outcomes, not two

`session_email.run()` returns True iff it sent, and False for everything else:
not configured, no vault, already sent today, relay refused. That is the right
shape for its own caller, which only wants to know whether to record a send.

It is the wrong shape here, because two of those cases mean opposite things. Mail
that was never configured is a skip and the design says so explicitly —
"unconfigured mail is a skip, never a failure". Mail that *is* configured and did
not arrive is a channel the operator believes is working and is not, which is the
same invisible failure the rest of this part exists to prevent.

So delivery reports `sent`, `skipped` or `failed`, and the exit code follows:
0 for the first two, 1 for the third.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import session_email  # noqa: E402

SENT = "sent"
SKIPPED = "skipped"
FAILED = "failed"


@dataclass
class Delivery:
    """What delivery did, and why.

    `reason` is always filled, including on success — a log line saying only
    "sent" leaves the reader wondering where, and the answer is the one thing
    they would want to check.
    """

    outcome: str
    reason: str

    @property
    def ok(self) -> bool:
        """Whether this counts as the run having gone right.

        A skip is fine. The vault file is the artifact and it was written
        whatever mail did.
        """
        return self.outcome in (SENT, SKIPPED)

    def render(self) -> str:
        return f"scorecard-delivery: {self.outcome} — {self.reason}"


def deliver(scorecard: Path, *, install_prefix: Path = None,
            send=None) -> Delivery:
    """Mail one scorecard, and say plainly what happened.

    `send` is the SMTP call, injectable so the tests can exercise a refusing
    relay without one. It defaults to the seam's own sender, so the shipped path
    and the tested path differ only in that one function.
    """
    cfg = session_email.email_config(install_prefix)
    if cfg is None:
        return Delivery(SKIPPED, (
            "no mail configured — set plugins.autonomy.email_to and "
            "email_smtp_url to have the scorecard delivered. The vault copy is "
            "written either way"))

    to_addr, smtp_url, from_addr = cfg

    if not scorecard.is_file():
        # Configured mail with nothing to send is a failure of the step before
        # this one, and saying "skipped" would file it under the harmless case.
        return Delivery(FAILED, f"there is no scorecard at {scorecard}")
    try:
        body = scorecard.read_text(encoding="utf-8")
    except OSError as exc:
        return Delivery(FAILED, f"could not read {scorecard}: {exc}")

    subject = f"Corpus health — {scorecard.stem}"
    sender = send if send is not None else session_email._send_smtp

    try:
        accepted = sender(smtp_url, to_addr, subject, body, from_addr=from_addr)
    except Exception as exc:  # noqa: BLE001 - the reason matters, not the type
        # The seam's own sender catches its expected errors and returns False.
        # This catches whatever it did not, because a delivery step that raised
        # its way out of a nightly run would take the run down over the least
        # important thing it does.
        return Delivery(FAILED, f"the relay raised {type(exc).__name__}: {exc}")

    if not accepted:
        return Delivery(FAILED, (
            f"the relay at {_host(smtp_url)} did not accept the message. Mail is "
            f"configured, so this is a channel that looks like it works and does "
            f"not"))
    return Delivery(SENT, f"to {to_addr}")


def _host(smtp_url: str) -> str:
    """The relay's host, for a message a person reads.

    Host only. The configured URL can carry the operator's own credentials, and
    a log line is exactly the wrong place for them.
    """
    rest = smtp_url.split("://", 1)[-1]
    if "@" in rest:
        rest = rest.rsplit("@", 1)[1]
    return rest.split("/", 1)[0] or "the configured relay"


def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(
        description="Mail a corpus health scorecard through the notify seam.")
    parser.add_argument("scorecard", type=Path,
                        help="path to the scorecard markdown to send")
    parser.add_argument("--install-prefix", type=Path, default=None,
                        help="where to read .agentm-config.json from")
    args = parser.parse_args(argv)

    result = deliver(args.scorecard, install_prefix=args.install_prefix)
    stream = sys.stdout if result.ok else sys.stderr
    print(result.render(), file=stream)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
