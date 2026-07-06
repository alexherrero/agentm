"""The AgentM runner — a standalone background-job executor (agentm-runner.md).

One idempotent cycle: read `.harness/jobs/*.yaml` manifests, decide which are
due (schedule + last-run + lookback), run them within budget, write through
the vault's V5-0 write floor as the third writer, and exit. No resident
daemon — a host scheduled task, OS cron, or an on-demand call supplies the
heartbeat; this package only supplies the cycle.
"""
