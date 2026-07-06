"""Job manifest schema + loader for the AgentM runner (agentm-runner.md).

Loads `.harness/jobs/*.yaml` into `JobManifest` objects. Read-only — this
module never runs a job, it only describes one. A malformed manifest fails
loud (raises `ManifestError` naming the offending file) rather than silently
dropping a job from the cycle, matching the seam's own never-demote
discipline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is a repo-wide dependency already
    yaml = None

_DURATION_RE = re.compile(r"^(\d+)([smhdw])$")
_DURATION_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_NAMED_SCHEDULE_SECONDS = {"hourly": 3600, "daily": 86400, "weekly": 604800}

# T1 (the operator's personal space) is never a job target — that takes the
# separate, explicit, operator-authorized seam call a scheduled job never
# makes. Enforced at load time so a mistyped manifest fails at parse, not at
# the moment it would otherwise have written somewhere it shouldn't.
VALID_TIERS = ("T2", "T3")


class ManifestError(ValueError):
    """A job manifest failed to parse or violates the schema."""


def parse_duration(value: str) -> int:
    """"24h" / "7d" / "30m" -> seconds. Raises `ManifestError` if malformed."""
    m = _DURATION_RE.match(str(value).strip())
    if not m:
        raise ManifestError(f"malformed duration: {value!r} (expected e.g. '24h', '7d')")
    n, unit = m.groups()
    return int(n) * _DURATION_SECONDS[unit]


def schedule_interval_seconds(schedule: str) -> int:
    """A named cadence ("hourly"/"daily"/"weekly") or a raw duration -> seconds."""
    key = str(schedule).strip().lower()
    if key in _NAMED_SCHEDULE_SECONDS:
        return _NAMED_SCHEDULE_SECONDS[key]
    return parse_duration(schedule)


@dataclass(frozen=True)
class JobManifest:
    name: str
    schedule: str
    lookback: str
    command: str
    tier: str = "T3"
    gate: Optional[str] = None
    budget_tokens: Optional[int] = None
    dry_run: bool = True
    path: Optional[Path] = None

    @property
    def interval_seconds(self) -> int:
        return schedule_interval_seconds(self.schedule)

    @property
    def lookback_seconds(self) -> int:
        return parse_duration(self.lookback)


def _validate(name: str, data: dict, path: Path) -> JobManifest:
    for required in ("schedule", "lookback", "command"):
        if required not in data:
            raise ManifestError(f"{path}: missing required field {required!r}")

    tier = data.get("tier", "T3")
    if tier not in VALID_TIERS:
        raise ManifestError(
            f"{path}: tier {tier!r} is not a job-writable tier {VALID_TIERS} "
            "— T1 is never a job target"
        )

    # Fail loud at load time on a malformed schedule/lookback, not on first use.
    schedule_interval_seconds(str(data["schedule"]))
    parse_duration(str(data["lookback"]))

    budget = data.get("budget")
    budget_tokens = budget.get("tokens") if isinstance(budget, dict) else None

    return JobManifest(
        name=name,
        schedule=str(data["schedule"]),
        lookback=str(data["lookback"]),
        command=str(data["command"]),
        tier=str(tier),
        gate=data.get("gate"),
        budget_tokens=budget_tokens,
        dry_run=bool(data.get("dry_run", True)),
        path=path,
    )


def load_manifests(jobs_dir: Path) -> list[JobManifest]:
    """Read every `*.yaml`/`*.yml` in `jobs_dir` into a `JobManifest`.

    Returns `[]` if `jobs_dir` doesn't exist (a fresh install with no jobs
    configured yet — not an error). Raises `ManifestError` on the first
    malformed manifest found, naming the offending file.
    """
    if yaml is None:
        raise ManifestError("PyYAML not available — cannot parse job manifests")
    jobs_dir = Path(jobs_dir)
    if not jobs_dir.is_dir():
        return []
    out: list[JobManifest] = []
    for p in sorted(jobs_dir.iterdir()):
        if p.suffix not in (".yaml", ".yml"):
            continue
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise ManifestError(f"{p}: invalid YAML ({e})") from e
        if not isinstance(data, dict):
            raise ManifestError(f"{p}: manifest body must be a mapping")
        out.append(_validate(p.stem, data, p))
    return out
